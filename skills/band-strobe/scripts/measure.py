#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""band-strobe · 逐帧量节奏。

两个用途：
  ① **抄别人的**：把一条参考片量成 spec 参数（带边界 / 段长分布 / 切点密度 / 错开率），
     `--emit-spec` 直接吐出可用的 rhythm 段。
  ② **验自己的**：跑完 band_strobe.py 拿产物再量一遍，和参考片对读数。

🩸 这个工具是踩出来的：光靠抽几帧目测会把效果看错。
   第一次看参考片，把「三条带各播一张不同的图」误读成「同一张图被横向撕开」，
   做出来完全是另一个东西。逐帧量 + 横移相关性检验才看清。

依赖：ffmpeg、numpy、Pillow。
"""
import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile

try:
    import numpy as np
    from PIL import Image
except ImportError:
    sys.exit("✗ 需要 numpy 与 Pillow：pip install numpy pillow")


def extract(video, tmp, width=480):
    # 🩸 别按位置解包 ffprobe 的输出：它按**流里的自然字段序**返回，
    #    不按 -show_entries 的书写序。走 json 按名取才稳。
    run = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=r_frame_rate,width,height",
         "-of", "json", video],
        capture_output=True, text=True, timeout=60)
    st = json.loads(run.stdout)["streams"][0]
    W, H = int(st["width"]), int(st["height"])
    num, den = st["r_frame_rate"].split("/")
    fps = float(num) / float(den)
    h = round(H * width / W / 2) * 2
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", video,
                    "-vf", f"scale={width}:{h}", os.path.join(tmp, "f%05d.png")],
                   check=True, timeout=900)
    fs = sorted(glob.glob(os.path.join(tmp, "f*.png")))
    A = np.stack([np.asarray(Image.open(f).convert("L"), dtype=np.float32) for f in fs])
    return A, fps, W, H


def find_bands(A, H, want=0):
    """带边界 = 「逐行帧间差」在行方向上的突变处。

    各带内容不同、切换时刻也不同 ⇒ 带内各行的帧间差曲线相似、跨带则不同，
    所以二阶差分的峰值就是边界。

    🩸 聚簇窗口别开太窄：第一版用 3 行，把真边界旁边的次级峰当成了独立边界，
       2 条真边界被检成 7 条。窗口放宽到 8 行、再按**峰强**筛，才稳。
       拿不准就显式给 `--bands`——自动检出只是省事，不是权威。
    """
    d = np.abs(np.diff(A, axis=0)).mean(axis=(0, 2))      # 每一行的平均帧间差
    g = np.abs(np.diff(d))
    rows = A.shape[1]
    cand = sorted(int(x) for x in np.argsort(g)[::-1][:24])
    groups, cur = [], [cand[0]]
    for r in cand[1:]:
        if r - cur[-1] <= 8:
            cur.append(r)
        else:
            groups.append(cur); cur = [r]
    groups.append(cur)

    scored = []
    for grp in groups:
        pos = int(max(grp, key=lambda r: g[r]))           # 取簇内最强的那一行
        if not (0.06 * rows < pos < 0.94 * rows):         # 贴边的不是真边界
            continue
        scored.append((float(g[pos]), pos))
    if not scored:
        return []
    scored.sort(reverse=True)
    if want:
        scored = scored[: want - 1]
    else:
        top = scored[0][0]
        scored = [x for x in scored if x[0] >= 0.4 * top]
    return [(pos, round(pos / rows * H)) for _, pos in sorted(scored, key=lambda x: x[1])]


def cuts_of(A, y0, y1, k=1.0, min_gap=3):
    d = np.abs(np.diff(A[:, y0:y1, :], axis=0)).mean(axis=(1, 2))
    thr = d.mean() + k * d.std()
    out = []
    for i, v in enumerate(d):
        if v > thr and (not out or i + 1 - out[-1] >= min_gap):
            out.append(i + 1)
    return out


def shift_test(A, y0, y1, cuts, max_shift=18):
    """切点是「换图」还是「同图横移」？

    横移的话，把后一帧横推回去残差会明显下降；换图不会。
    这一步决定要不要在脚本里做带内位移——实测参考片 27 个切点全是换图。
    """
    n_move = 0
    for c in cuts:
        if c < 1 or c >= A.shape[0]:
            continue
        p, q = A[c - 1, y0:y1], A[c, y0:y1]
        raw = np.abs(p - q).mean()
        best = raw
        for s in range(-max_shift, max_shift + 1):
            if s == 0:
                continue
            if s > 0:
                a, b = p[:, s:], q[:, : q.shape[1] - s]
            else:
                a, b = p[:, : p.shape[1] + s], q[:, -s:]
            best = min(best, np.abs(a - b).mean())
        if best < raw * 0.45:
            n_move += 1
    return n_move


def main():
    ap = argparse.ArgumentParser(description="band-strobe · 逐帧量节奏")
    ap.add_argument("video")
    ap.add_argument("--bands", type=int, default=0,
                    help="期望带数（0 = 按峰强自动定）。自动检出只是省事，拿不准就显式给")
    ap.add_argument("--k", type=float, default=1.0, help="切点检测阈值 μ+kσ；漏切就调小到 0.5")
    ap.add_argument("--emit-spec", action="store_true", help="吐出可直接用的 rhythm 段")
    args = ap.parse_args()
    for st in (sys.stdout, sys.stderr):  # Windows 控制台缺省 GBK，打印 ✅ 等字符会崩
        if hasattr(st, "reconfigure"):
            st.reconfigure(encoding="utf-8")

    for exe in ("ffmpeg", "ffprobe"):
        if shutil.which(exe) is None:
            sys.exit(f"✗ 找不到 {exe}")

    tmp = tempfile.mkdtemp(prefix="band-measure-")
    try:
        A, fps, W, H = extract(args.video, tmp)
        n = A.shape[0]
        rows = A.shape[1]
        print(f"{os.path.basename(args.video)}  {W}×{H} @ {fps:.3g}fps · {n} 帧 / {n/fps:.2f}s")

        edges = find_bands(A, H, args.bands)
        print(f"带边界（缩略行 / 原画 y）：{edges or '未检出 —— 可能是单幅无分带'}")

        bounds = [0] + [e[0] for e in edges] + [rows]
        heights = [round((bounds[i + 1] - bounds[i]) / rows * H) for i in range(len(bounds) - 1)]
        print(f"⇒ {len(heights)} 条带，高度 {heights}（合计 {sum(heights)}，画幅 {H}）\n")

        all_segs, all_cuts, moves = [], [], 0
        for i in range(len(bounds) - 1):
            y0, y1 = bounds[i], bounds[i + 1]
            c = cuts_of(A, y0, y1, args.k)
            segs = ([c[0]] + [c[j + 1] - c[j] for j in range(len(c) - 1)] + [n - c[-1]]) if c else [n]
            moves += shift_test(A, y0, y1, c)
            all_segs += segs
            all_cuts.append(c)
            print(f"  带{i+1}: {len(c)} 切点 · 段长(帧) {segs}")

        tot = sum(len(c) for c in all_cuts)
        ov = sum(1 for i in range(len(all_cuts)) for j in range(i + 1, len(all_cuts))
                 for x in all_cuts[i] for y in all_cuts[j] if abs(x - y) <= 1)
        s = sorted(all_segs)
        fast = [x for x in s if x <= 8]
        slow = [x for x in s if x > 8]
        print(f"\n每秒切点 {tot/(n/fps):.1f} · 错开率 {(1-ov/max(tot,1))*100:.0f}%"
              f" · 段长 最短{s[0]} 中位{s[len(s)//2]} 最长{s[-1]} 帧")
        if fast and slow:
            print(f"快档 {len(fast)} 段(中位 {fast[len(fast)//2]}) · 慢档 {len(slow)} 段(中位 {slow[len(slow)//2]})"
                  f"  ⇒ 有顿挫，对应 rhythm 档")
        else:
            print(f"只有单一档 ⇒ 等间隔，对应 uniform 档（seg ≈ {s[len(s)//2]} 帧）")
        print(f"切点里「同图横移」{moves} 个 / 共 {tot} 个"
              f"{'  ⇒ 全是换图，不要做带内位移' if moves == 0 else '  ⇒ 存在位移，需另行处理'}")

        if args.emit_spec:
            if fast and slow:
                r = {"mode": "rhythm", "hold_pool": slow, "burst_pool": fast, "burst_run": [2, 3]}
            else:
                seg = s[len(s) // 2]
                nb = len(heights)
                r = {"mode": "uniform", "seg": seg,
                     "phase": [(i * max(seg // nb, 1)) % seg for i in range(nb)]}
            print("\n--- 可直接粘进 spec ---")
            print(json.dumps({"fps": round(fps), "bands": heights, "rhythm": r},
                             ensure_ascii=False, indent=1))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
