#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cam-whip · 逐帧量镜头动作。

用法：
    python measure.py <video> --spec <spec.json>

逐个动作对账（stdout 一行 JSON）：
    per_move[]   每个拍点：cut_offset（最近切点相对拍点的帧差，应为 0）；
                 甩镜：out_dx / in_dx（出镜、入镜窗口里画面整体水平位移的中位数，px/帧@原画幅）与 dir_ok（方向与 spec 一致）；
                       上下甩看 out_dy / in_dy；
                 拉镜 / 平铺推拉：out_zoom / in_zoom（窗口里逐帧缩放比的几何均值，>1 推近、<1 拉远）与 zoom_ok；
                 blur（窗口里最快那帧的清晰度 / 同段平稳帧清晰度，<1 说明有运动模糊）
    extra_cuts          不在拍点、也不在动作窗口里的切点数（应为 0）
    black_edge_frames   画面四边出现整条纯黑带的帧数（镜像补边没兜住会露黑边，应为 0）
    summary      各项通过数与 ok

量法：解码成 960×540。位移用 480×270 灰度的相位相关；缩放在 240×135 上对前一帧按 0.70–1.45 的 61 档缩放搜索最小差；
切点看颜色直方图。依赖：ffmpeg、numpy、Pillow。
"""
import argparse
import io
import json
import math
import os
import subprocess
import sys

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
SW, SH = 960, 540


def decode(video):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width",
                        "-of", "default=nw=1:nk=1", video], capture_output=True, text=True, check=True)
    src_w = int(r.stdout.strip())
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-i", video, "-vf", "scale=%d:%d:flags=area" % (SW, SH),
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
    frames, n = [], SW * SH * 3
    while True:
        b = p.stdout.read(n)
        if len(b) < n:
            break
        frames.append(np.frombuffer(b, dtype=np.uint8).reshape(SH, SW, 3))
    p.wait()
    return frames, src_w


def phase_shift(a, b, win):
    """b 相对 a 的整体位移 (dx, dy)，px@480 宽。"""
    A = np.fft.fft2(a * win)
    B = np.fft.fft2(b * win)
    R = B * np.conj(A)
    R /= np.abs(R) + 1e-9
    r = np.real(np.fft.ifft2(R))
    y, x = np.unravel_index(np.argmax(r), r.shape)
    h, w = r.shape
    if y > h // 2:
        y -= h
    if x > w // 2:
        x -= w
    return float(x), float(y)


SCALES = np.exp(np.linspace(math.log(0.70), math.log(1.45), 61))


def zoom_ratio(prev, cur):
    """cur 相对 prev 的缩放比（以画面中心）：把 prev 放大 s 倍后与 cur 的中心区域最像的那个 s。"""
    h, w = cur.shape
    cy0, cy1, cx0, cx1 = h // 4, h * 3 // 4, w // 4, w * 3 // 4
    target = cur[cy0:cy1, cx0:cx1]
    im = Image.fromarray(prev.astype(np.uint8))
    best, arg = 1e18, 1.0
    for s in SCALES:
        ww, hh = int(round(w * s)), int(round(h * s))
        big = np.asarray(im.resize((ww, hh), Image.BILINEAR), dtype=np.float32)
        ox, oy = (ww - w) // 2, (hh - h) // 2
        if ox + cx0 < 0 or oy + cy0 < 0 or ox + cx1 > ww or oy + cy1 > hh:
            continue
        v = float(np.abs(big[oy + cy0:oy + cy1, ox + cx0:ox + cx1] - target).mean())
        if v < best:
            best, arg = v, float(s)
    return arg


def sharp(g):
    lap = g[1:-1, 1:-1] * 4 - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:]
    return float(lap.var())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--spec", required=True)
    a = ap.parse_args()
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8")
    sys.path.insert(0, HERE)
    import cam_whip as cw
    with io.open(a.spec, "r", encoding="utf-8") as f:
        spec = json.load(f)
    sdir = os.path.dirname(os.path.abspath(a.spec))
    fps = int(spec.get("fps", 60))
    total = int(round(float(spec["duration"]) * fps))
    hits = cw.resolve_hits(spec, sdir, fps, total)
    moves = cw.resolve_moves(spec, len(hits), fps)

    frames, src_w = decode(a.video)
    n = len(frames)
    scale = src_w / 480.0
    gray = [f.astype(np.float32).mean(axis=2) for f in frames]
    g480 = [np.asarray(Image.fromarray(g.astype(np.uint8)).resize((480, 270), Image.BILINEAR), dtype=np.float32) for g in gray]
    g240 = [np.asarray(Image.fromarray(g.astype(np.uint8)).resize((240, 135), Image.BILINEAR), dtype=np.float32) for g in gray]
    win = np.outer(np.hanning(270), np.hanning(480)).astype(np.float32)
    hists = []
    for f in frames:
        sub = f[::4, ::4]
        hists.append(np.concatenate([np.bincount(sub[:, :, c].ravel() >> 4, minlength=16) for c in range(3)]) / float(sub.shape[0] * sub.shape[1]))
    HD = np.zeros(n)
    for k in range(1, n):
        HD[k] = 0.5 * float(np.abs(hists[k] - hists[k - 1]).sum()) / 3.0
    thr = max(0.2, float(np.median(HD[1:])) * 4)
    cuts = [k for k in range(1, n) if HD[k] > thr and HD[k] >= HD[k - 1] and (k + 1 >= n or HD[k] >= HD[k + 1])]

    # 黑边：四条边上 3px 宽的带整条近乎纯黑（≥ 90% 像素 < 6）
    black = 0
    for f in frames:
        g = f.max(axis=2)
        bands = [g[:3, :], g[-3:, :], g[:, :3], g[:, -3:]]
        if any(float((b < 6).mean()) >= 0.9 for b in bands):
            black += 1

    cut_mode = spec.get("cut", "every")
    per, fails = [], []
    ok_dir = ok_zoom = n_dir = n_zoom = ok_cut = n_cut = 0
    for i, (h, m) in enumerate(zip(hits, moves)):
        row = {"hit": i, "frame": h, "move": m["type"]}
        if cut_mode == "every" and h > 0:
            n_cut += 1
            near = [c - h for c in cuts if abs(c - h) <= 3]
            row["cut_offset"] = min(near, key=abs) if near else None
            ok_cut += 1 if row["cut_offset"] == 0 else 0
        out_w = range(max(1, h - m["pre_f"] + 1), h)
        in_w = range(h + 1, min(n, h + m["post_f"]))
        if m["type"] == "whip":
            dx, dy = cw.DIRS[m["dir"]]
            o = [phase_shift(g480[k - 1], g480[k], win) for k in out_w]
            q = [phase_shift(g480[k - 1], g480[k], win) for k in in_w]
            ax = 0 if dx else 1
            om = float(np.median([v[ax] for v in o])) * scale if o else 0.0
            im_ = float(np.median([v[ax] for v in q])) * scale if q else 0.0
            want = dx if dx else dy
            row["out_d" + ("x" if dx else "y")] = round(om, 1)
            row["in_d" + ("x" if dx else "y")] = round(im_, 1)
            row["dir_ok"] = bool(om * want > 2 and im_ * want > 2)
            n_dir += 1
            ok_dir += 1 if row["dir_ok"] else 0
        else:
            oz = [zoom_ratio(g240[k - 1], g240[k]) for k in out_w]
            iz = [zoom_ratio(g240[k - 1], g240[k]) for k in in_w]
            ozg = float(np.exp(np.mean(np.log(oz)))) if oz else 1.0
            izg = float(np.exp(np.mean(np.log(iz)))) if iz else 1.0
            row["out_zoom"], row["in_zoom"] = round(ozg, 4), round(izg, 4)
            if m["type"] == "pull":
                row["zoom_ok"] = ozg > 1.005 and izg < 0.995
            else:
                row["zoom_ok"] = ozg < 0.995 and izg > 1.005
            n_zoom += 1
            ok_zoom += 1 if row["zoom_ok"] else 0
        # 运动模糊：窗口里最快那帧的清晰度 / 同段平稳帧（窗口外、同段）的清晰度
        fast = [k for k in list(out_w) + list(in_w)]
        nxt = min([x for x in hits if x > h] + [n])
        calm = [k for k in range(h + m["post_f"] + 2, min(n, nxt - 10))]
        if fast and calm:
            row["blur"] = round(min(sharp(gray[k]) for k in fast) / (float(np.mean([sharp(gray[k]) for k in calm])) + 1e-6), 3)
        per.append(row)
    expected = set(h for h in hits if h > 0) if cut_mode == "every" else set()
    wins = [(h - m["pre_f"], h + m["post_f"]) for h, m in zip(hits, moves)]
    extra = [c for c in cuts if all(abs(c - e) > 1 for e in expected) and not any(w0 <= c <= w1 for (w0, w1) in wins)]
    blurs = [r["blur"] for r in per if r.get("blur") is not None]
    summ = {"cut_hit": ok_cut, "cut_expected": n_cut, "whip_dir_ok": ok_dir, "whip_total": n_dir,
            "zoom_ok": ok_zoom, "zoom_total": n_zoom, "extra_cuts": len(extra), "black_edge_frames": black,
            "blur_median": round(float(np.median(blurs)), 3) if blurs else None}
    if ok_cut < n_cut:
        fails.append("%d 个切点没有落在拍点那一帧" % (n_cut - ok_cut))
    if ok_dir < n_dir:
        fails.append("%d 个甩镜方向不对或几乎没动" % (n_dir - ok_dir))
    if ok_zoom < n_zoom:
        fails.append("%d 个拉镜 / 平铺推拉的推拉方向不对" % (n_zoom - ok_zoom))
    if extra:
        fails.append("多出 %d 个不在拍点、也不在动作窗口里的切点：%s" % (len(extra), extra[:8]))
    if black:
        fails.append("%d 帧边缘露出整条黑边（镜像补边没兜住）" % black)
    if blurs and summ["blur_median"] > 0.85:
        fails.append("快动帧清晰度中位数是平稳帧的 %.2f 倍，几乎没有运动模糊" % summ["blur_median"])
    summ["ok"] = not fails
    summ["fail"] = fails
    sys.stdout.write(json.dumps({"frames": n, "cuts": cuts, "per_move": per, "summary": summ}, ensure_ascii=False) + "\n")
    sys.exit(0 if summ["ok"] else 1)


if __name__ == "__main__":
    main()
