#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""beat-pulse · 逐帧量冲击。

两个用途：
  ① **验自己的**：`--spec` 给出生成用的 spec，逐拍对账——切点是不是正好落在拍点那一帧、
     闪白 / 色散是不是出现在拍点、冲击后几帧的运动能量是不是明显高于平时。
  ② **量别人的**：不给 spec，按帧间差与亮度跳变检出切点和闪白，`--emit-hits` 吐出可直接粘进 spec 的 hits。

用法：
    python measure.py <video> [--spec <spec.json>] [--emit-hits] [--k 4.0]

读数（stdout 一行 JSON）：
    cuts        检出的切点帧（相邻帧颜色直方图距离 > max(0.2, 中位数 × k) 且是局部峰）
    per_hit[]   每个拍点：cut_offset（最近切点相对拍点的帧差，应为 0）、
                flash_lift（拍点帧比**同段冲击衰减完的参照帧**亮多少，0–255）、
                rgb_px（拍点帧与参照帧的 R/B 水平错位之差，px@原画幅）、impact（效果窗口内最动的连续 4 帧的帧间差均值 / 同段衰减完之后的帧间差均值）
    summary     cut_hit / cut_expected、多出来的切点数、各效果在应出现的拍上的中位读数，ok

量法：解码成 960×540。切点看颜色直方图（换片段时颜色分布突变；画面自身运动、震屏几乎不改变它）。
闪白与色散都和**同一段里衰减完的那一帧**比（同内容），扣掉素材本身的亮度与色边——彩条这类画面 R/B 边缘
天然不对齐，直接读绝对值会有基线。注意成品是 yuv420p：色度减半采样，色散在成片里本来就比设定值弱，读数反映的是观众看到的量。
依赖：ffmpeg、numpy。
"""
import argparse
import io
import json
import os
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SW, SH = 960, 540


def decode(video):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=r_frame_rate,width,height",
                        "-of", "json", video], capture_output=True, text=True, check=True)
    st = json.loads(r.stdout)["streams"][0]
    num, den = st["r_frame_rate"].split("/")
    fps = float(num) / float(den)
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-i", video, "-vf", "scale=%d:%d:flags=area" % (SW, SH),
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
    frames = []
    n = SW * SH * 3
    while True:
        b = p.stdout.read(n)
        if len(b) < n:
            break
        frames.append(np.frombuffer(b, dtype=np.uint8).reshape(SH, SW, 3))
    p.wait()
    return frames, fps, int(st["width"])


def rgb_offset(f):
    """R 与 B 两通道水平梯度的互相关峰位置（像素@960 宽）。正常画面 ≈ 0。"""
    c = f[SH // 4: SH * 3 // 4].astype(np.float32)
    gr = np.diff(c[:, :, 0], axis=1)
    gb = np.diff(c[:, :, 2], axis=1)
    gr -= gr.mean()
    gb -= gb.mean()
    best, arg = -1e18, 0
    for s in range(-30, 31):
        if s >= 0:
            a, b = gr[:, s:], gb[:, :gb.shape[1] - s]
        else:
            a, b = gr[:, :s], gb[:, -s:]
        v = float((a * b).sum()) / (np.sqrt(float((a * a).sum()) * float((b * b).sum())) + 1e-6)
        if v > best:
            best, arg = v, s
    return arg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--spec", default=None)
    ap.add_argument("--emit-hits", action="store_true")
    ap.add_argument("--k", type=float, default=4.0, help="切点阈值：帧间差 > 中位数 × k")
    a = ap.parse_args()
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8")
    frames, fps, src_w = decode(a.video)
    n = len(frames)
    gray = [f.astype(np.float32).mean(axis=2) for f in frames]
    Y = np.array([g.mean() for g in gray])
    D = np.zeros(n)
    for k in range(1, n):
        D[k] = float(np.abs(gray[k] - gray[k - 1]).mean())
    hists = []
    for f in frames:
        sub = f[::4, ::4]
        hists.append(np.concatenate([np.bincount(sub[:, :, c].ravel() >> 4, minlength=16) for c in range(3)]).astype(np.float64) / (sub.shape[0] * sub.shape[1]))
    HD = np.zeros(n)
    for k in range(1, n):
        HD[k] = 0.5 * float(np.abs(hists[k] - hists[k - 1]).sum()) / 3.0
    med = float(np.median(HD[1:])) + 1e-6
    thr = max(0.2, med * a.k)
    cuts = [k for k in range(1, n) if HD[k] > thr and HD[k] >= HD[k - 1] and (k + 1 >= n or HD[k] >= HD[k + 1])]
    res = {"frames": n, "fps": round(fps, 3), "hist_median": round(med, 4), "cut_threshold": round(thr, 3), "cuts": cuts}

    if a.spec:
        sys.path.insert(0, HERE)
        import beat_pulse as bp
        with io.open(a.spec, "r", encoding="utf-8") as f:
            spec = json.load(f)
        sdir = os.path.dirname(os.path.abspath(a.spec))
        total = int(round(float(spec["duration"]) * int(spec.get("fps", 60))))
        hits = bp.resolve_hits(spec, sdir, int(spec.get("fps", 60)), total)
        cut_mode = spec.get("cut", "every")
        scale = src_w / float(SW)
        # 平稳帧：离任何拍点 ≥ 12 帧
        hf = [h for (h, _) in hits]
        calm = [k for k in range(1, n) if all(abs(k - h) >= 12 for h in hf) and k not in cuts]
        base = float(np.mean(D[calm])) if calm else med
        per, exp_cuts, got_cuts = [], 0, 0
        fx_read = {"flash": [], "rgb": [], "impact": []}
        for i, (h, acc) in enumerate(hits):
            if h >= n:
                continue
            rec = bp.recipe(spec, acc)
            fx = [e["type"] for e in rec]
            # 参照帧：同一段里所有效果都衰减完的那一帧（不越过下一个切点 / 拍点）
            nxt = min([c for c in cuts if c > h] + [x for x in hf if x > h] + [n])
            ref = min(h + max(int(e["len"]) for e in rec) + 1, nxt - 1)
            want_cut = h > 0 and (cut_mode == "every" or (cut_mode == "accent" and acc))
            near = [c - h for c in cuts if abs(c - h) <= 3]
            off = min(near, key=abs) if near else None
            if want_cut:
                exp_cuts += 1
                if off == 0:
                    got_cuts += 1
            ok_ref = ref > h
            lift = float(Y[h] - Y[ref]) if ok_ref else None
            rgbp = abs(rgb_offset(frames[h]) - rgb_offset(frames[ref])) * scale / 2.0 if ok_ref else None
            # 效果窗口内滑动取「最动的连续 4 帧」：推 / 震的峰在拍后，呼吸式胀缩的峰在拍前后的两翼
            pre_w = max([int(e["len"]) // 2 + 1 for e in rec if e.get("shape") == "breathe"] or [0])
            w = [k for k in range(max(1, h - pre_w), min(n, h + max(int(e["len"]) for e in rec) + 1)) if k not in cuts and k != h]
            runs = [float(np.mean([D[x] for x in w[j:j + 4]])) for j in range(0, max(1, len(w) - 3)) if len(w[j:j + 4]) == 4]
            calm_seg = [D[k] for k in range(ref + 1, min(nxt, ref + 9))]  # 同段、冲击衰减完之后
            cb = float(np.mean(calm_seg)) if len(calm_seg) >= 3 else base
            imp = (max(runs) / cb) if runs and cb > 0 else None
            row = {"hit": i, "frame": h, "ref": ref, "accent": acc, "fx": fx, "cut_offset": off,
                   "flash_lift": round(lift, 1) if lift is not None else None,
                   "rgb_px": round(rgbp, 1) if rgbp is not None else None, "impact": round(imp, 2) if imp is not None else None}
            per.append(row)
            if "flash" in fx and lift is not None:
                fx_read["flash"].append(lift)
            if "rgb" in fx and rgbp is not None:
                fx_read["rgb"].append(rgbp)
            if imp is not None and ("shake" in fx or "zoom" in fx or "glitch" in fx):
                fx_read["impact"].append(imp)
        expected = set(h for (h, acc) in hits if h > 0 and (cut_mode == "every" or (cut_mode == "accent" and acc)))
        # 效果的起落（如色散铺满细碎图案后突然复原）也会让直方图跳一下——拍点的效果窗口内不算「多余切点」
        wins = []
        for (h, acc) in hits:
            rec = bp.recipe(spec, acc)
            wins.append((h - max([int(e["len"]) // 2 + 1 for e in rec if e.get("shape") == "breathe"] or [0]),
                         h + max(int(e["len"]) for e in rec) + 1))
        extra = [c for c in cuts if all(abs(c - e) > 1 for e in expected) and not any(w0 <= c <= w1 for (w0, w1) in wins)]
        summ = {"cut_hit": got_cuts, "cut_expected": exp_cuts, "extra_cuts": len(extra),
                "calm_diff": round(base, 3),
                "flash_lift_median": round(float(np.median(fx_read["flash"])), 1) if fx_read["flash"] else None,
                "rgb_px_median": round(float(np.median(fx_read["rgb"])), 1) if fx_read["rgb"] else None,
                "impact_median": round(float(np.median(fx_read["impact"])), 2) if fx_read["impact"] else None}
        fails = []
        if got_cuts < exp_cuts:
            fails.append("%d 个该切的拍没有切在拍点那一帧" % (exp_cuts - got_cuts))
        if extra:
            fails.append("多出 %d 个不在拍点上的切点：%s" % (len(extra), extra[:8]))
        if fx_read["flash"] and summ["flash_lift_median"] < 8:
            fails.append("闪白拍的亮度抬升中位数只有 %.1f" % summ["flash_lift_median"])
        if fx_read["rgb"] and summ["rgb_px_median"] < 2:
            fails.append("色散拍的 R/B 错位中位数只有 %.1fpx" % summ["rgb_px_median"])
        if fx_read["impact"] and summ["impact_median"] < 1.3:
            fails.append("冲击后运动能量只有平时的 %.2f 倍" % summ["impact_median"])
        summ["ok"] = not fails
        summ["fail"] = fails
        res["per_hit"] = per
        res["summary"] = summ
    if a.emit_hits:
        flashes = [k for k in range(1, n) if Y[k] - Y[k - 1] > 25]
        ev = sorted(set(cuts) | set(flashes))
        res["emit"] = {"hits": [round(k / fps, 3) for k in ev], "fps": round(fps, 3)}
    sys.stdout.write(json.dumps(res, ensure_ascii=False) + "\n")
    if a.spec:
        sys.exit(0 if res["summary"]["ok"] else 1)


if __name__ == "__main__":
    main()
