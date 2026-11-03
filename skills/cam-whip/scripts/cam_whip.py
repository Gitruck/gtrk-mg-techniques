#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cam-whip · 拉镜 / 甩镜卡点生成器。

在拍点（切点）两侧做一次「镜头动作」，把两个镜头接起来：
  whip  甩镜：出镜段加速甩出（带转角），入镜段从反方向甩入、减速落定；
  pull  拉镜：出镜段急推到 Z 倍（带转角、可偏锚点），入镜段从 Z 倍急拉回 1；
  tile  平铺推拉：出镜段缩到 M 倍露出镜像平铺，入镜段从 M 倍弹回 1。
段内可叠缓推（drift）与手持微震（handheld）。越界处一律镜像补边；快动的帧做真运动模糊
（快门时间内取多个子时刻的变换叠平均）。产物是 mp4。

脚本只做机械活：解析拍点、排段选材、逐帧算镜头、采样、编码，以及自检。
「哪些片段、哪几拍、哪种动作、甩多远推多深」由调用方在 spec 里决定。

确定性：手持微震的相位由 random.Random(seed) 派生；同 spec 同 seed ⇒ 逐帧一致。
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

import numpy as np
from PIL import Image

MOVE_DEFAULTS = {
    "whip": {"dir": "left", "dist": 0.62, "rot": 6.0, "pre": 0.09, "post": 0.17},
    "pull": {"zoom": 1.9, "rot": 14.0, "anchor": [0.5, 0.5], "pre": 0.12, "post": 0.24},
    "tile": {"min": 0.5, "rot": 0.0, "pre": 0.14, "post": 0.22},
}
DIRS = {"left": (-1, 0), "right": (1, 0), "up": (0, -1), "down": (0, 1)}


def die(msg):
    sys.stderr.write("cam-whip: " + msg + "\n")
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


# ———————————————————————— 拍点（与 beat-pulse 同口径） ————————————————————————

def resolve_hits(spec, spec_dir, fps, total):
    dur = total / float(fps)
    if spec.get("hits") is not None:
        times = [float(t) for t in spec["hits"]]
    elif spec.get("beats") is not None:
        b = spec["beats"]
        if isinstance(b, list):
            src = {"beats": b}
        elif b.get("file"):
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
        bb = spec["beats"] if isinstance(spec["beats"], dict) else {}
        every, phase, off = int(bb.get("every", 1)), int(bb.get("phase", 0)), float(bb.get("offset", 0))
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
    frames = [int(round(t * fps)) for t in times if 0 <= t < dur]
    if not frames:
        die("时长内一个拍点都没有")
    return frames


def resolve_moves(spec, n_hits, fps):
    pat = spec.get("moves") or ["whip"]
    out = []
    for i in range(n_hits):
        m = pat[i % len(pat)]
        if isinstance(m, str):
            parts = m.split("-")
            m = {"type": parts[0]}
            if len(parts) > 1:
                if m["type"] == "whip":
                    m["dir"] = parts[1]
                elif m["type"] == "pull":
                    m["style"] = parts[1]
        t = m.get("type")
        if t not in MOVE_DEFAULTS:
            die("moves 里不认识的动作 %r（可用：whip[-left/right/up/down] / pull / tile）" % t)
        d = dict(MOVE_DEFAULTS[t])
        d.update(m)
        if t == "whip" and d["dir"] not in DIRS:
            die("whip.dir 只收 left / right / up / down")
        if t == "tile" and not (0.34 <= float(d["min"]) < 1):
            die("tile.min 要在 0.34–1 之间（再小镜像平铺要铺三圈以上，补边算不过来）")
        if t == "pull" and not (1.05 <= float(d["zoom"]) <= 4):
            die("pull.zoom 要在 1.05–4 之间")
        d["pre_f"] = max(1, int(round(float(d["pre"]) * fps)))
        d["post_f"] = max(1, int(round(float(d["post"]) * fps)))
        out.append(d)
    # 拉镜转角左右交替，连招才有来回感
    sign = 1
    for d in out:
        if d["type"] == "pull" and "rot_sign" not in d:
            d["rot_sign"] = sign
            sign = -sign
    return out


# ———————————————————————— 镜头 ————————————————————————

def ease_in(p):
    return p ** 3


def ease_out(p):
    return 1 - (1 - p) ** 3


def move_cam(m, t, h, W, H):
    """第 h 帧切点上的动作在（小数）帧时刻 t 的镜头：(scale, rot, tx, ty, ax, ay)。t < h 属出镜段，t ≥ h 属入镜段。"""
    pre, post = m["pre_f"], m["post_f"]
    ax, ay = 0.5, 0.5
    if t < h:
        p = (t - (h - pre)) / float(pre)
        if p < 0:
            return None
        e = ease_in(min(1.0, p))
        if m["type"] == "whip":
            dx, dy = DIRS[m["dir"]]
            D = float(m["dist"]) * (W if dx else H)
            return (1.0, float(m["rot"]) * e * (dx or dy), dx * D * e, dy * D * e, ax, ay)
        if m["type"] == "pull":
            ax, ay = m["anchor"]
            return (1 + (float(m["zoom"]) - 1) * e, float(m["rot"]) * m["rot_sign"] * e, 0.0, 0.0, ax, ay)
        return (1 - (1 - float(m["min"])) * e, float(m["rot"]) * e, 0.0, 0.0, ax, ay)
    p = (t - h) / float(post)
    if p >= 1:
        return None
    e = ease_out(max(0.0, p))
    if m["type"] == "whip":
        dx, dy = DIRS[m["dir"]]
        D = float(m["dist"]) * (W if dx else H)
        return (1.0, -float(m["rot"]) * (1 - e) * (dx or dy), -dx * D * (1 - e), -dy * D * (1 - e), ax, ay)
    if m["type"] == "pull":
        ax, ay = m["anchor"]
        return (float(m["zoom"]) + (1 - float(m["zoom"])) * e, float(m["rot"]) * m["rot_sign"] * (1 - e), 0.0, 0.0, ax, ay)
    return (float(m["min"]) + (1 - float(m["min"])) * e, -float(m["rot"]) * (1 - e), 0.0, 0.0, ax, ay)


def _coef(W, H, s, rot, tx, ty, ax, ay):
    """输出 (X, Y) → 源坐标的仿射系数，及横纵两向越界多少像素。"""
    th = math.radians(rot)
    c, sn = math.cos(th), math.sin(th)
    cx, cy = ax * W, ay * H
    a, b, d, e = c / s, sn / s, -sn / s, c / s
    ox = oy = 0.0
    for X, Y in ((0, 0), (W, 0), (0, H), (W, H)):
        u, v = X - cx - tx, Y - cy - ty
        sx, sy = a * u + b * v + cx, d * u + e * v + cy
        ox = max(ox, -sx, sx - W)
        oy = max(oy, -sy, sy - H)
    return (a, b, -a * (cx + tx) - b * (cy + ty) + cx, d, e, -d * (cx + tx) - e * (cy + ty) + cy), ox, oy


def affine_multi(img, cams):
    """按一组镜头采样并取平均（1 组 = 普通帧，多组 = 运动模糊）。越界处镜像补边：每帧只补一次，所有子采样共用。"""
    H, W = img.shape[:2]
    if len(cams) == 1:
        s, rot, tx, ty = cams[0][:4]
        if abs(s - 1) < 1e-4 and abs(rot) < 1e-3 and abs(tx) < 0.05 and abs(ty) < 0.05:
            return img
    coefs, mx, my = [], 0, 0
    for c in cams:
        cf, ox, oy = _coef(W, H, *c)
        coefs.append(cf)
        mx = max(mx, int(math.ceil(ox) + 2) if ox > 0 else 0)
        my = max(my, int(math.ceil(oy) + 2) if oy > 0 else 0)
    if mx >= W or my >= H:
        raise RuntimeError("镜头越界 %d×%dpx，超过一圈镜像补边能覆盖的范围" % (mx, my))
    src = Image.fromarray(np.pad(img, ((my, my), (mx, mx), (0, 0)), mode="reflect") if (mx or my) else img)
    rs = Image.BICUBIC if len(cams) == 1 else Image.BILINEAR
    if len(cams) == 1:
        a, b, c, d, e, f = coefs[0]
        return np.asarray(src.transform((W, H), Image.AFFINE, (a, b, c + mx, d, e, f + my), resample=rs))
    acc = np.zeros(img.shape, dtype=np.float32)
    for (a, b, c, d, e, f) in coefs:
        acc += np.asarray(src.transform((W, H), Image.AFFINE, (a, b, c + mx, d, e, f + my), resample=rs), dtype=np.float32)
    return (acc / len(coefs) + 0.5).astype(np.uint8)


class Camera:
    def __init__(self, spec, hits, moves, segs, fps, W, H, seed):
        self.hits, self.moves, self.segs, self.fps, self.W, self.H = hits, moves, segs, fps, W, H
        dr = spec.get("drift") or {}
        self.drift = float(dr.get("amp", 0.0)) if isinstance(dr, dict) else float(dr)
        hh = spec.get("handheld") or {}
        self.hh_amp = float(hh.get("amp", 0.0))
        self.hh_rot = float(hh.get("rot", 0.0))
        self.hh_f = float(hh.get("freq", 0.6))
        r = random.Random(seed * 7907 + 11)
        self.ph = [r.random() * 2 * math.pi for _ in range(6)]

    def at(self, t, k):
        """t：小数帧时刻；k：这一帧所在的整数帧（决定属于哪个切点的哪一侧）。"""
        s, rot, tx, ty, ax, ay = 1.0, 0.0, 0.0, 0.0, 0.5, 0.5
        for h, m in zip(self.hits, self.moves):
            if k < h - m["pre_f"] or k >= h + m["post_f"]:
                continue
            tt = min(t, h - 1e-6) if k < h else max(t, float(h))
            c = move_cam(m, tt, h, self.W, self.H)
            if c:
                s *= c[0]
                rot += c[1]
                tx += c[2]
                ty += c[3]
                if abs(c[4] - 0.5) > 1e-6 or abs(c[5] - 0.5) > 1e-6:
                    ax, ay = c[4], c[5]
        if self.drift:
            for (st, n) in self.segs:
                if st <= k < st + n:
                    s *= 1 + self.drift * (t - st) / float(max(1, n))
                    break
        if self.hh_amp or self.hh_rot:
            sec = t / float(self.fps)
            w = 2 * math.pi * self.hh_f
            tx += self.hh_amp * (0.6 * math.sin(w * sec + self.ph[0]) + 0.4 * math.sin(2.3 * w * sec + self.ph[1]))
            ty += self.hh_amp * (0.6 * math.sin(1.3 * w * sec + self.ph[2]) + 0.4 * math.sin(2.9 * w * sec + self.ph[3]))
            rot += self.hh_rot * math.sin(0.7 * w * sec + self.ph[4])
        return (s, rot, tx, ty, ax, ay)


def render(img, cam, k, blur):
    """运动模糊：这一帧镜头在快门时间内动了多少像素，就按每 10px 一个子时刻采样（2 ≤ n ≤ samples）叠平均；
    几乎不动的帧只采一次。子采样少于位移 / 10 会看出离散重影。"""
    smax, shutter = int(blur.get("samples", 12)), float(blur.get("shutter", 0.75))
    c0, c1 = cam.at(k - shutter / 2, k), cam.at(k + shutter / 2, k)
    move = math.hypot(c1[2] - c0[2], c1[3] - c0[3]) + abs(c1[0] - c0[0]) * cam.W / 2 + abs(c1[1] - c0[1]) * math.pi / 180 * cam.W / 2
    if smax <= 1 or move < 2.0:
        return affine_multi(img, [cam.at(k, k)]), 1
    n = max(2, min(smax, int(math.ceil(move / 10.0))))
    cams = [cam.at(k + (j / float(n - 1) - 0.5) * shutter, k) for j in range(n)]
    return affine_multi(img, cams), n


# ———————————————————————— 主流程 ————————————————————————

def main():
    ap = argparse.ArgumentParser(description="cam-whip · 拉镜 / 甩镜卡点生成器")
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
    blur = spec.get("blur") or {}

    clips = [c if os.path.isabs(c) else os.path.join(base, c) for c in spec.get("clips") or []]
    if not clips:
        die("clips 至少一个")
    for c in clips:
        if not os.path.exists(c):
            die("素材不存在：" + c)
    cut = spec.get("cut", "every")
    if cut not in ("every", "none"):
        die("cut 只收 every（每个动作都换镜头）/ none（一条素材到底，只做镜头动作）")
    if cut == "every" and len(clips) < 2:
        die("每个动作都换镜头，至少给 2 个素材")

    hits = resolve_hits(spec, base, fps, total)
    moves = resolve_moves(spec, len(hits), fps)
    # 动作窗口不能互相重叠、不能越出首尾
    for i, (h, m) in enumerate(zip(hits, moves)):
        if h - m["pre_f"] < 0 or h + m["post_f"] > total:
            die("第 %d 拍（第 %d 帧）的 %s 动作窗口 [%d, %d) 越出了 [0, %d)——挪开首尾的拍或缩短 pre / post"
                % (i, h, m["type"], h - m["pre_f"], h + m["post_f"], total))
        if i:
            ph, pm = hits[i - 1], moves[i - 1]
            if ph + pm["post_f"] > h - m["pre_f"]:
                die("第 %d 拍的入镜（到第 %d 帧）与第 %d 拍的出镜（从第 %d 帧）重叠——拍太密，用 every 隔拍取或缩短 pre / post"
                    % (i - 1, ph + pm["post_f"], i, h - m["pre_f"]))

    cuts = [0] + ([h for h in hits if h > 0] if cut == "every" else []) + [total]
    segs = [(cuts[i], cuts[i + 1] - cuts[i]) for i in range(len(cuts) - 1) if cuts[i + 1] > cuts[i]]
    src_cfg = spec.get("source") or {}
    lead, stride = float(src_cfg.get("lead", 0.3)), float(src_cfg.get("stride", 1.3))
    durs = {c: probe_duration(c) for c in clips}
    rng = random.Random(seed)
    plan, prev, used = [], None, {}
    for (st, n) in segs:
        if cut == "none":
            name = clips[0]
        else:
            pool = [c for c in clips if c != prev] or clips
            name = pool[rng.randrange(len(pool))]
        span = durs[name] - lead - n / float(fps) - 0.1
        if span <= 0:
            die("素材 %s 只有 %.2fs，放不下 %d 帧的一段（+lead %.2fs）" % (os.path.basename(name), durs[name], n, lead))
        k = used.get(name, 0)
        plan.append({"clip": name, "start": st, "frames": n, "offset": round(lead + (k * stride) % span, 3)})
        used[name] = k + 1
        prev = name

    cam = Camera(spec, hits, moves, segs, fps, W, H, seed)
    out = os.path.abspath(a.out)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    part = out + ".part.mp4"
    enc = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "%dx%d" % (W, H),
                            "-framerate", str(fps), "-i", "-", "-an", "-c:v", "libx264", "-preset", "medium",
                            "-crf", str(int(spec.get("crf", 16))), "-pix_fmt", "yuv420p", part], stdin=subprocess.PIPE)
    fsz = W * H * 3
    k_global, blurred, blur_max = 0, 0, 1
    try:
        for si, sg in enumerate(plan):
            dec = subprocess.Popen(["ffmpeg", "-v", "error", "-ss", "%.3f" % sg["offset"], "-i", sg["clip"],
                                    "-frames:v", str(sg["frames"]), "-an",
                                    "-vf", "fps=%d,scale=%d:%d:force_original_aspect_ratio=increase:flags=bicubic,crop=%d:%d,setsar=1"
                                    % (fps, W, H, W, H), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
            got = 0
            while got < sg["frames"]:
                buf = dec.stdout.read(fsz)
                if len(buf) < fsz:
                    break
                img = np.frombuffer(buf, dtype=np.uint8).reshape(H, W, 3)
                frame, ns = render(img, cam, k_global, blur)
                blurred += 1 if ns > 1 else 0
                blur_max = max(blur_max, ns)
                enc.stdin.write(np.ascontiguousarray(frame).tobytes())
                got += 1
                k_global += 1
            dec.stdout.close()
            dec.wait()
            if got != sg["frames"]:
                raise RuntimeError("第 %d 段 %s @%.2fs 要 %d 帧、实得 %d 帧" % (si, os.path.basename(sg["clip"]), sg["offset"], sg["frames"], got))
        enc.stdin.close()
        if enc.wait() != 0:
            raise RuntimeError("编码失败")
        if k_global != total:
            raise RuntimeError("送进编码器 %d 帧，应为 %d 帧" % (k_global, total))
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
        "motion_blurred_frames": blurred, "blur_samples_max": blur_max,
        "hits": [{"frame": h, "t": round(h / float(fps), 3), "move": m["type"],
                  "dir": m.get("dir") if m["type"] == "whip" else None, "window": [h - m["pre_f"], h + m["post_f"]]}
                 for h, m in zip(hits, moves)],
        "segments": [{"clip": os.path.basename(p["clip"]), "start": p["start"], "frames": p["frames"], "offset": p["offset"]} for p in plan],
    }
    sys.stdout.write(json.dumps(summary, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
