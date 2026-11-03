#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""beat-pulse · 卡点冲击链生成器。

在拍点上给画面一记「冲击」：震屏、胀缩（推一下再回弹 / 呼吸）、曝光闪白、RGB 色散、负片闪、
放射模糊、坏电视条带错位——按配方叠在一起，随帧衰减；可选在拍点上切到下一个片段。产物是 mp4。

脚本只做机械活：解析拍点、排段选材、逐帧算包络与变换、编码，以及自检。
「用哪些片段、在哪些拍上打、打多重、重拍换什么配方」由调用方在 spec 里决定。

管线：ffmpeg 把每段解成**正好 n 帧**原始 RGB → Python 逐帧加效果 → 管道回 ffmpeg 编码。
帧数由本脚本的循环决定；先写临时文件，自检全过才挪到落点，任何一道不过都不留产物。

确定性：所有「随机感」（震动方向、条带位置）都由 random.Random(整数种子) 派生；同 spec 同 seed ⇒ 逐帧一致。
依赖：ffmpeg / ffprobe 在 PATH 上；numpy、Pillow。
"""
import argparse
import io
import json
import math
import os
import random
import shutil
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image

# 配方预设：normal = 普通拍，accent = 重拍。每项一个效果，参数见 references/spec.md。
PRESETS = {
    "punch": {
        "normal": [{"type": "zoom", "amp": 0.07, "len": 10}, {"type": "shake", "amp": 16, "len": 8}],
        "accent": [{"type": "zoom", "amp": 0.13, "len": 12}, {"type": "shake", "amp": 30, "len": 10},
                   {"type": "flash", "amp": 0.55, "len": 6}, {"type": "rgb", "amp": 10, "len": 8}],
    },
    "glitch": {
        "normal": [{"type": "rgb", "amp": 14, "len": 6}, {"type": "glitch", "amp": 40, "len": 5}],
        "accent": [{"type": "invert", "len": 2}, {"type": "rgb", "amp": 22, "len": 8},
                   {"type": "glitch", "amp": 90, "len": 8}, {"type": "shake", "amp": 20, "len": 6}],
    },
    "breathe": {
        "normal": [{"type": "zoom", "amp": 0.05, "len": 16, "shape": "breathe"}, {"type": "blur", "amp": 0.025, "len": 8}],
        "accent": [{"type": "zoom", "amp": 0.09, "len": 20, "shape": "breathe"}, {"type": "flash", "amp": 0.3, "len": 8},
                   {"type": "blur", "amp": 0.05, "len": 10}],
    },
}

DEFAULTS = {
    "shake": {"amp": 20, "len": 8, "rot": 1.2},
    "zoom": {"amp": 0.08, "len": 10, "shape": "punch"},
    "flash": {"amp": 0.5, "len": 6, "color": "#FFFFFF"},
    "rgb": {"amp": 12, "len": 8},
    "invert": {"len": 2},
    "blur": {"amp": 0.04, "len": 8},
    "glitch": {"amp": 60, "len": 6, "bands": 12},
}


def die(msg):
    sys.stderr.write("beat-pulse: " + msg + "\n")
    sys.exit(2)


def run(cmd, **kw):
    kw.setdefault("check", True)
    kw.setdefault("timeout", 900)
    return subprocess.run(cmd, **kw)


def probe_duration(path):
    r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", path],
            capture_output=True, text=True)
    return float(r.stdout.strip())


def probe_frames(path):
    r = run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
             "-show_entries", "stream=nb_read_frames", "-of", "default=nw=1:nk=1", path], capture_output=True, text=True)
    return int(r.stdout.strip())


def hex_rgb(h):
    h = h.lstrip("#")
    return np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)], dtype=np.float32)


# ———————————————————————— 拍点 ————————————————————————

def resolve_hits(spec, spec_dir, fps, total):
    """返回 [(frame, accent)]，按帧升序。拍点来源三选一：hits / beats / bpm。"""
    dur = total / float(fps)
    down = set()
    if spec.get("hits") is not None:
        times = [float(t) for t in spec["hits"]]
    elif spec.get("beats") is not None:
        b = spec["beats"]
        if isinstance(b, list):
            src = {"beats": b}
        else:
            if b.get("file"):
                p = b["file"] if os.path.isabs(b["file"]) else os.path.join(spec_dir, b["file"])
                if not os.path.exists(p):
                    die("找不到拍点文件：" + p)
                with io.open(p, "r", encoding="utf-8") as f:
                    src = json.load(f)
                src = src.get("output_result", src)
            else:
                src = {"beats": b.get("list") or []}
        beats = [float(t) for t in src.get("beats") or []]
        if not beats:
            die("beats 为空——拍点文件里要有 beats 数组（秒）")
        down = {round(float(t) * fps) for t in src.get("downbeats") or []}
        bb = spec["beats"] if isinstance(spec["beats"], dict) else {}
        every, phase = int(bb.get("every", 1)), int(bb.get("phase", 0))
        off = float(bb.get("offset", 0))
        times = [t - off for k, t in enumerate(beats) if k >= phase and (k - phase) % every == 0]
    elif spec.get("bpm") is not None:
        g = spec["bpm"] if isinstance(spec["bpm"], dict) else {"bpm": spec["bpm"]}
        period = 60.0 / float(g["bpm"]) * float(g.get("every", 1))
        t, times = float(g.get("offset", 0)), []
        while t < dur:
            times.append(t)
            t += period
    else:
        die("拍点来源三选一：hits（秒列表）/ beats（列表或 {file}）/ bpm（{bpm, offset, every}）")
    frames = []
    for t in times:
        if t < 0 or t >= dur:
            continue
        frames.append(int(round(t * fps)))
    if not frames:
        die("时长内一个拍点都没有")
    min_gap = int(spec.get("min_gap", 3))
    for a, b in zip(frames, frames[1:]):
        if b - a < min_gap:
            die("相邻两拍只隔 %d 帧（< %d）：第 %d 帧与第 %d 帧——拍点太密，用 every 隔拍取或删掉重复拍" % (b - a, min_gap, a, b))
    ac = spec.get("accent")
    acc = []
    for k, f in enumerate(frames):
        if isinstance(ac, dict):
            hit = k >= int(ac.get("phase", 0)) and (k - int(ac.get("phase", 0))) % int(ac.get("every", 4)) == 0
        elif isinstance(ac, list):
            hit = k in set(int(x) for x in ac)
        elif ac == "downbeats":
            hit = any(abs(f - d) <= 1 for d in down)
            if not down:
                die("accent=downbeats 但拍点来源里没有 downbeats")
        else:
            hit = False
        acc.append(bool(hit))
    return list(zip(frames, acc))


def recipe(spec, accent):
    fx = spec.get("fx", "punch")
    if isinstance(fx, str):
        if fx not in PRESETS:
            die("fx 预设只收 %s" % " / ".join(PRESETS))
        fx = PRESETS[fx]
    lst = fx.get("accent" if accent else "normal") or ([] if accent else None)
    if lst is None:
        die("fx 要有 normal 配方")
    if accent and not lst:
        lst = fx["normal"]
    out = []
    for e in lst:
        t = e.get("type")
        if t not in DEFAULTS:
            die("不认识的效果 %r（可用：%s）" % (t, " / ".join(DEFAULTS)))
        d = dict(DEFAULTS[t])
        d.update(e)
        if int(d["len"]) < 1:
            die("%s 的 len 至少 1 帧" % t)
        out.append(d)
    return out


# ———————————————————————— 逐帧效果 ————————————————————————

def env(k, length):
    """衰减包络：命中那一帧 = 1，len 帧内按 (1 - k/len)^2.2 落到 0。"""
    if k < 0 or k >= length:
        return 0.0
    return (1.0 - k / float(length)) ** 2.2


def affine(img, s, rot, tx, ty):
    """以画面中心缩放 s、旋转 rot（度）、平移 (tx, ty)；越界处镜像补边（MAD 里的 tile 镜像）。"""
    H, W = img.shape[:2]
    if abs(s - 1) < 1e-4 and abs(rot) < 1e-3 and abs(tx) < 0.05 and abs(ty) < 0.05:
        return img
    th = math.radians(rot)
    c, sn = math.cos(th), math.sin(th)
    cx, cy = W / 2.0, H / 2.0
    a, b = c / s, sn / s
    d, e = -sn / s, c / s
    # 输出 (X, Y) → 源 (a(X−cx−tx) + b(Y−cy−ty) + cx, d(X−cx−tx) + e(Y−cy−ty) + cy)
    over = 0.0
    for X, Y in ((0, 0), (W, 0), (0, H), (W, H)):
        u, v = X - cx - tx, Y - cy - ty
        sx, sy = a * u + b * v + cx, d * u + e * v + cy
        over = max(over, -sx, sx - W, -sy, sy - H)
    m = int(min(math.ceil(over) + 2, min(W, H) // 2 - 1)) if over > 0 else 0
    src = np.pad(img, ((m, m), (m, m), (0, 0)), mode="reflect") if m > 0 else img
    coef = (a, b, -a * (cx + tx) - b * (cy + ty) + cx + m, d, e, -d * (cx + tx) - e * (cy + ty) + cy + m)
    return np.asarray(Image.fromarray(src).transform((W, H), Image.AFFINE, coef, resample=Image.BICUBIC))


def zoom_blur(img, a):
    """放射（缩放）模糊：同中心的 4 个放大版本取平均。视频产物不受颗粒的零卷积约束。"""
    H, W = img.shape[:2]
    acc = img.astype(np.float32)
    im = Image.fromarray(img)
    for j in (1, 2, 3):
        s = 1 + a * j / 3.0
        w, h = int(round(W * s)), int(round(H * s))
        big = np.asarray(im.resize((w, h), Image.BILINEAR))
        x0, y0 = (w - W) // 2, (h - H) // 2
        acc += big[y0:y0 + H, x0:x0 + W].astype(np.float32)
    return (acc / 4.0).astype(np.uint8)


def rgb_split(img, d):
    d = int(round(d))
    if d == 0:
        return img
    out = img.copy()
    out[:, :, 0] = np.roll(img[:, :, 0], -d, axis=1)
    out[:, :, 2] = np.roll(img[:, :, 2], d, axis=1)
    return out


def glitch(img, amp, bands, rng):
    out = img.copy()
    H = img.shape[0]
    for _ in range(bands):
        y0 = rng.randrange(0, H)
        h = rng.randrange(6, max(7, H // 14))
        off = int(round((rng.random() * 2 - 1) * amp))
        out[y0:y0 + h] = np.roll(img[y0:y0 + h], off, axis=1)
    return out


def render_frame(img, active, seed):
    """active：[(hit_index, k_rel, effect)]。按「变换 → 模糊 → 条带 → 色散 → 负片 → 闪」顺序叠。"""
    s, rot, tx, ty = 1.0, 0.0, 0.0, 0.0
    blur = rgb = 0.0
    glitches, inv, flashes = [], False, []
    for (hi, kr, e) in active:
        t = e["type"]
        if t == "zoom":
            if e.get("shape") == "breathe":
                L = int(e["len"])
                ph = (kr + L / 2.0) / L
                v = math.sin(math.pi * ph) if 0 <= ph <= 1 else 0.0
            else:
                v = env(kr, int(e["len"]))
            s += float(e["amp"]) * v
        elif t == "shake":
            v = env(kr, int(e["len"]))
            if v > 0:
                r = random.Random(seed * 1000003 + hi * 7919 + kr)
                ang = r.random() * 2 * math.pi
                tx += math.cos(ang) * float(e["amp"]) * v
                ty += math.sin(ang) * float(e["amp"]) * v
                rot += (r.random() * 2 - 1) * float(e.get("rot", 1.2)) * v
        elif t == "blur":
            blur += float(e["amp"]) * env(kr, int(e["len"]))
        elif t == "rgb":
            rgb += float(e["amp"]) * env(kr, int(e["len"]))
        elif t == "glitch":
            v = env(kr, int(e["len"]))
            if v > 0:
                glitches.append((float(e["amp"]) * v, int(e.get("bands", 12)), random.Random(seed * 2000003 + hi * 104729 + kr)))
        elif t == "invert":
            if 0 <= kr < int(e["len"]):
                inv = not inv
        elif t == "flash":
            v = float(e["amp"]) * env(kr, int(e["len"]))
            if v > 0:
                flashes.append((v, hex_rgb(e.get("color", "#FFFFFF"))))
    out = affine(img, s, rot, tx, ty)
    if blur > 1e-4:
        out = zoom_blur(out, blur)
    for (a, nb, r) in glitches:
        out = glitch(out, a, nb, r)
    if rgb >= 0.5:
        out = rgb_split(out, rgb)
    if inv:
        out = 255 - out
    if flashes:
        f = out.astype(np.float32)
        for (v, col) in flashes:
            f += (col - f) * min(1.0, v)
        out = np.clip(f, 0, 255).astype(np.uint8)
    return out


# ———————————————————————— 主流程 ————————————————————————

def main():
    ap = argparse.ArgumentParser(description="beat-pulse · 卡点冲击链生成器")
    ap.add_argument("spec")
    ap.add_argument("--out", required=True, help="产物 mp4（直接写最终落点）")
    a = ap.parse_args()
    for st in (sys.stdout, sys.stderr):
        if hasattr(st, "reconfigure"):
            st.reconfigure(encoding="utf-8")
    for exe in ("ffmpeg", "ffprobe"):
        if shutil.which(exe) is None:
            die("找不到 %s，请先装 ffmpeg 或加进 PATH" % exe)

    spec_path = os.path.abspath(a.spec)
    with io.open(spec_path, "r", encoding="utf-8") as f:
        spec = json.load(f)
    base = os.path.dirname(spec_path)
    W, H = int(spec.get("width", 1920)), int(spec.get("height", 1080))
    fps = int(spec.get("fps", 60))
    dur = float(spec.get("duration") or 0) or die("duration 必填（秒）")
    total = int(round(dur * fps))
    seed = int(spec.get("seed", 0))

    clips = [c if os.path.isabs(c) else os.path.join(base, c) for c in spec.get("clips") or []]
    if not clips:
        die("clips 至少一个")
    for c in clips:
        if not os.path.exists(c):
            die("素材不存在：" + c)
    cut = spec.get("cut", "every")
    if cut not in ("every", "accent", "none"):
        die("cut 只收 every（每拍切）/ accent（重拍才切）/ none（不切，一条素材到底）")
    if cut != "none" and len(clips) < 2:
        die("要在拍点上切，至少给 2 个素材")

    hits = resolve_hits(spec, base, fps, total)
    recipes = [recipe(spec, acc) for (_, acc) in hits]

    # —— 排段：切点 = 选中的拍点帧 ——
    cuts = [0]
    if cut != "none":
        for (fr, acc) in hits:
            if fr > 0 and (cut == "every" or acc):
                cuts.append(fr)
    cuts.append(total)
    segs = [(cuts[i], cuts[i + 1] - cuts[i]) for i in range(len(cuts) - 1) if cuts[i + 1] > cuts[i]]
    src_cfg = spec.get("source") or {}
    lead, stride = float(src_cfg.get("lead", 0.3)), float(src_cfg.get("stride", 1.3))
    durs = {c: probe_duration(c) for c in clips}
    rng = random.Random(seed)
    order = list(clips)
    plan, prev, used = [], None, {}
    for (st, n) in segs:
        if cut == "none":
            name = clips[0]
        else:
            pool = [c for c in order if c != prev] or order
            name = pool[rng.randrange(len(pool))]
        span = durs[name] - lead - n / float(fps) - 0.1
        if span <= 0:
            die("素材 %s 只有 %.2fs，放不下 %d 帧的一段（+lead %.2fs）" % (os.path.basename(name), durs[name], n, lead))
        k = used.get(name, 0)
        off = lead + ((k * stride) % span)
        used[name] = k + 1
        plan.append({"clip": name, "start": st, "frames": n, "offset": round(off, 3)})
        prev = name

    # —— 逐帧 ——
    out = os.path.abspath(a.out)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    part = out + ".part.mp4"
    enc = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "%dx%d" % (W, H),
                            "-framerate", str(fps), "-i", "-", "-an", "-c:v", "libx264", "-preset", "medium",
                            "-crf", str(int(spec.get("crf", 16))), "-pix_fmt", "yuv420p", part],
                           stdin=subprocess.PIPE)
    hit_frames = [fr for (fr, _) in hits]
    pre = max([int(e["len"]) // 2 + 1 for r in recipes for e in r if e.get("shape") == "breathe"] or [0])
    post = max(int(e["len"]) for r in recipes for e in r) + 1
    fsz = W * H * 3
    k_global = 0
    try:
        for si, sg in enumerate(plan):
            dec = subprocess.Popen(["ffmpeg", "-v", "error", "-ss", "%.3f" % sg["offset"], "-i", sg["clip"],
                                    "-frames:v", str(sg["frames"]), "-an",
                                    "-vf", "fps=%d,scale=%d:%d:force_original_aspect_ratio=increase:flags=bicubic,crop=%d:%d,setsar=1"
                                    % (fps, W, H, W, H),
                                    "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
            got = 0
            while True:
                buf = dec.stdout.read(fsz)
                if len(buf) < fsz:
                    break
                img = np.frombuffer(buf, dtype=np.uint8).reshape(H, W, 3)
                k = k_global
                active = []
                for hi, hf in enumerate(hit_frames):
                    if hf - pre <= k < hf + post:
                        for e in recipes[hi]:
                            active.append((hi, k - hf, e))
                frame = render_frame(img, active, seed) if active else img
                enc.stdin.write(np.ascontiguousarray(frame).tobytes())
                got += 1
                k_global += 1
                if got == sg["frames"]:
                    break
            dec.stdout.close()
            dec.wait()
            # 自检①：每段必须正好 n 帧（-ss 越界时 ffmpeg 一帧不出且退出码为 0）
            if got != sg["frames"]:
                raise RuntimeError("第 %d 段 %s @%.2fs 要 %d 帧、实得 %d 帧" % (si, os.path.basename(sg["clip"]), sg["offset"], sg["frames"], got))
        enc.stdin.close()
        if enc.wait() != 0:
            raise RuntimeError("编码失败")
        # 自检②：送进编码器的帧数
        if k_global != total:
            raise RuntimeError("送进编码器 %d 帧，应为 %d 帧" % (k_global, total))
        # 自检③④：成品帧数与时长
        got = probe_frames(part)
        if got != total:
            raise RuntimeError("成品 %d 帧，应为 %d 帧" % (got, total))
        gd = probe_duration(part)
        if abs(gd - dur) > 1.0 / fps + 1e-3:
            raise RuntimeError("成品 %.3fs，应为 %.3fs" % (gd, dur))
        os.replace(part, out)
    except Exception as e:
        try:
            enc.stdin.close()
        except Exception:
            pass
        enc.wait()
        if os.path.exists(part):
            os.remove(part)
        die("✗ " + str(e) + "（未产文件）")

    summary = {
        "out": out, "frames": total, "fps": fps, "duration": dur, "size_mb": round(os.path.getsize(out) / 1e6, 2),
        "hits": [{"frame": fr, "t": round(fr / float(fps), 3), "accent": acc, "fx": [e["type"] for e in recipes[i]]}
                 for i, (fr, acc) in enumerate(hits)],
        "segments": [{"clip": os.path.basename(p["clip"]), "start": p["start"], "frames": p["frames"], "offset": p["offset"]} for p in plan],
    }
    sys.stdout.write(json.dumps(summary, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
