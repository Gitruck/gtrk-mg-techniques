#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""band-strobe · 横带频闪蒙太奇生成器。

把画幅横切成 N 条带，**每条带各自跑一条独立的快切序列**，
三条带的切点相位错开 ⇒ 任意时刻各带内容都不同、且不同步跳变。
叠 RGB 错位 + 洗白调色 + 拖影。产物是 mp4（不是 html-particle）。

脚本只做机械活：排段、切片、拼带、叠带、调色、编码，以及**四道自检**。
「用哪些镜、快到什么程度、几条带」由调用方在 spec 里决定。

依赖：ffmpeg / ffprobe 在 PATH 上。
"""
import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile

# 调色预设。key 是 spec 里的 grade.preset。
# 🩸 别用 `blend=screen` 拿模糊副本抬黑位做泛光——它会把阴影推成品红。
#    参考片是「暖高光 + 青阴影」，所以走 curves 抬黑位 + colorbalance 定色向。
GRADES = {
    # 暖高光 + 青阴影，洗白。参考片就是这个调子
    "warm-cyan": (
        "curves=all='0/0.10 0.5/0.60 1/1',"
        "colorbalance=rs=-0.14:gs=0.04:bs=0.16:rh=0.12:bh=-0.08,"
        "eq=saturation=0.60:contrast=1.25"
    ),
    # 只洗白不定色向，留原素材的色温
    "wash": "curves=all='0/0.08 0.5/0.58 1/1',eq=saturation=0.70:contrast=1.18",
    # 不调色，只保留频闪结构与 RGB 错位
    "none": None,
}


def run(cmd, **kw):
    kw.setdefault("check", True)
    kw.setdefault("timeout", 600)
    return subprocess.run(cmd, **kw)


def probe_duration(path):
    r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", path], capture_output=True, text=True)
    return float(r.stdout.strip())


def probe_frames(path):
    r = run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
             "-show_entries", "stream=nb_read_frames", "-of", "default=nw=1:nk=1", path],
            capture_output=True, text=True)
    return int(r.stdout.strip())


def schedule(spec, band_i, total_frames, rng):
    """排出该带的段长序列（帧）。

    两种节奏：
      uniform  等间隔快闪。全程同一段长，靠 `phase` 让各带错开。
                🔴 等间隔下**不要**再让段长随机——随机只会把"不匀速"带回来。
      rhythm   慢一段（hold_pool）→ 急闪一串（burst_pool × burst_run）交替。
                这是参考片的原节奏，看起来会有"切换时停一下"的顿挫。
    """
    mode = spec.get("rhythm", {}).get("mode", "uniform")
    segs = []
    if mode == "uniform":
        seg = int(spec.get("rhythm", {}).get("seg", 6))
        phases = spec.get("rhythm", {}).get("phase") or [
            (i * max(seg // max(len(spec["bands"]), 1), 1)) % seg for i in range(len(spec["bands"]))
        ]
        first = seg - (phases[band_i] % seg)
        if first > 0:
            segs.append(first)
        acc = sum(segs)
        while acc < total_frames:
            n = min(seg, total_frames - acc)
            segs.append(n)
            acc += n
    else:
        r = spec.get("rhythm", {})
        hold = r.get("hold_pool", [16, 17, 22, 26, 26, 27, 27, 28, 39, 39, 39, 43, 43])
        burst = r.get("burst_pool", [3, 4, 5, 5, 5, 5, 5, 6, 7, 7])
        run_lo, run_hi = r.get("burst_run", [2, 3])
        acc, slow = 0, True
        while acc < total_frames:
            if slow:
                n = rng.choice(hold)
                segs.append(n)
                acc += n
            else:
                for _ in range(rng.randint(run_lo, run_hi)):
                    n = rng.choice(burst)
                    segs.append(n)
                    acc += n
                    if acc >= total_frames:
                        break
            slow = not slow
        if acc > total_frames:
            segs[-1] -= acc - total_frames
    return [s for s in segs if s > 0]


def build_band(spec, band_i, segs, clips, durs, taken, tmp, fps, W, H, rng):
    """把该带的段逐个切出来再拼成一条全幅视频（之后再裁成带）。"""
    src_cfg = spec.get("source", {})
    lead = float(src_cfg.get("lead", 0.5))        # 跳过片头静止帧
    stride = float(src_cfg.get("stride", 1.7))    # 同一素材每次复用往后挪多少

    parts, picks, cursor = [], [], 0
    for si, n in enumerate(segs):
        # 选素材：避开①此刻其他带正在播的 ②本带上一段
        # 🩸 ② 不能漏：同一素材连播两段就看不出切换，等间隔也会读成"停一下"。
        busy = set()
        for f in range(cursor, cursor + n):
            busy |= taken.get(f, set())
        prev = picks[-1] if picks else None
        pool = [c for c in clips if c not in busy and c != prev] \
            or [c for c in clips if c != prev] or list(clips)
        name = rng.choice(pool)
        for f in range(cursor, cursor + n):
            taken.setdefault(f, set()).add(name)

        # 🩸 取材偏移 MUST 夹在素材长度内。偏移越界时 `ffmpeg -ss` 一帧都不出
        #    **且退出码为 0**——整条带会静默变短，到成片才发现时长不对。
        #    用取模在 [lead, dur - seg - 0.1] 内循环。
        span = max(durs[name] - lead - n / fps - 0.1, 0.1)
        off = lead + ((picks.count(name) * stride + band_i * 0.4) % span)

        dst = os.path.join(tmp, f"b{band_i}_{si:04d}.mp4")
        run(["ffmpeg", "-y", "-v", "error", "-ss", f"{off:.3f}", "-i", name,
             # 🩸 用 `-frames:v N` 而不是 `-t 秒数`：6 帧 = 0.1s，按时长截取一取整就
             #    可能变 5 帧，几十段累计下来整条带短一大截。
             "-frames:v", str(n), "-an",
             "-vf", f"fps={fps},scale={W}:{H}:flags=bicubic,setsar=1",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
             # 全 I 帧：小段用 -c copy 拼接时不会在时间戳接缝丢帧
             "-g", "1", "-pix_fmt", "yuv420p", dst])

        # 自检①：切出来必须正好 n 帧，不是就当场炸（禁静默兜底）
        got = probe_frames(dst)
        if got != n:
            sys.exit(f"✗ 带{band_i} 第{si}段 {os.path.basename(name)} @{off:.2f}s "
                     f"要 {n} 帧、实得 {got} 帧")

        parts.append(dst)
        picks.append(name)
        cursor += n

    lst = os.path.join(tmp, f"_b{band_i}.txt")
    with open(lst, "w", encoding="utf-8", newline="") as f:
        for p in parts:
            f.write(f"file '{os.path.basename(p)}'\n")
    band = os.path.join(tmp, f"_band{band_i}.mp4")
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst,
         "-c", "copy", band], cwd=tmp)

    # 自检②：整条带的帧数必须等于段长之和
    want = sum(segs)
    got = probe_frames(band)
    if got != want:
        sys.exit(f"✗ 带{band_i} 拼接后 {got} 帧，应为 {want} 帧")
    return band, picks


def main():
    ap = argparse.ArgumentParser(description="band-strobe · 横带频闪蒙太奇生成器")
    ap.add_argument("spec")
    ap.add_argument("--out", required=True, help="产物 mp4 路径（直接写最终落点）")
    ap.add_argument("--keep-tmp", action="store_true", help="保留中间带文件，便于排查")
    args = ap.parse_args()

    for exe in ("ffmpeg", "ffprobe"):
        if shutil.which(exe) is None:
            sys.exit(f"✗ 找不到 {exe}，请先装 ffmpeg 或加进 PATH")

    spec_path = os.path.abspath(args.spec)
    spec = json.load(open(spec_path, encoding="utf-8"))
    base = os.path.dirname(spec_path)

    W = int(spec.get("width", 1920))
    H = int(spec.get("height", 1080))
    fps = int(spec.get("fps", 60))
    dur = float(spec["duration"])
    total = round(dur * fps)

    bands = spec.get("bands")
    if isinstance(bands, int):
        # 等分。余数摊给最后一条，保证合计 == H
        h = H // bands
        bands = [h] * (bands - 1) + [H - h * (bands - 1)]
        spec["bands"] = bands
    if sum(bands) != H:
        sys.exit(f"✗ bands 高度合计 {sum(bands)} ≠ height {H}")

    clips = [c if os.path.isabs(c) else os.path.join(base, c) for c in spec["clips"]]
    for c in clips:
        if not os.path.exists(c):
            sys.exit(f"✗ 素材不存在：{c}")
    if len(clips) < len(bands) + 1:
        sys.exit(f"✗ 素材只有 {len(clips)} 个，至少要 {len(bands)+1} 个"
                 f"（否则同一时刻各带避不开撞图）")
    durs = {c: probe_duration(c) for c in clips}

    seed = int(spec.get("seed", 0))
    tmp = tempfile.mkdtemp(prefix="band-strobe-")
    try:
        taken, band_files = {}, []
        print(f"画幅 {W}×{H} @ {fps}fps · {dur}s = {total} 帧 · {len(bands)} 条带 {bands}")
        for bi in range(len(bands)):
            rng = random.Random(seed + bi * 977)      # 每带独立种子 ⇒ 选材互不相干
            segs = schedule(spec, bi, total, rng)
            bf, picks = build_band(spec, bi, segs, clips, durs, taken, tmp, fps, W, H, rng)
            band_files.append(bf)
            uniq = len(set(picks))
            print(f"  带{bi+1}（高 {bands[bi]}px）：{len(segs)} 段 · 段长 "
                  f"{min(segs)}–{max(segs)} 帧 · 用到 {uniq}/{len(clips)} 个素材")

        # 各带裁自己那一段高度再 vstack。
        # 🔴 **不加任何带内左右位移**——参考片逐帧量过，27 个切点全是换图、零横移；
        #    那个"撕裂"观感纯粹来自各带内容不同，带缝本身是硬边。
        parts, y = [], 0
        for bi, bh in enumerate(bands):
            parts.append(f"[{bi}:v]crop={W}:{bh}:0:{y}[b{bi}];")
            y += bh
        fc = "".join(parts)
        cur = "[b0]"
        for bi in range(1, len(bands)):
            nxt = f"[v{bi}]"
            fc += f"{cur}[b{bi}]vstack=inputs=2{nxt};"
            cur = nxt
        chain = []
        g = spec.get("grade", {})
        gp = GRADES.get(g.get("preset", "warm-cyan"), GRADES["warm-cyan"])
        if gp:
            chain.append(gp)
        shift = g.get("rgb_shift", 9)
        if shift:
            v = int(shift)
            chain.append(f"rgbashift=rh=-{v}:rv=3:bh={v}:bv=-3")
        smear = int(g.get("smear", 2))
        if smear > 1:
            # 拖影帧数要跟段长匹配：一张才 6 帧时 tmix=3 会把相邻两张糊成一团
            chain.append(f"tmix=frames={smear}:weights='{' '.join(['1']*smear)}'")
        fc += f"{cur}{','.join(chain)}[out]" if chain else f"{cur}null[out]"

        out = os.path.abspath(args.out)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        cmd = ["ffmpeg", "-y", "-v", "error"]
        for bf in band_files:
            cmd += ["-i", bf]
        cmd += ["-filter_complex", fc, "-map", "[out]", "-t", f"{dur:.3f}", "-an",
                "-r", str(fps), "-c:v", "libx264", "-preset", "medium",
                "-crf", str(spec.get("crf", 16)), "-pix_fmt", "yuv420p", out]
        run(cmd, timeout=1800)

        # 自检③：成品帧数（tmix 会吃掉 smear-1 帧，容差放到那个量级）
        got = probe_frames(out)
        if abs(got - total) > max(smear, 1):
            sys.exit(f"✗ 成品 {got} 帧，应为 {total} 帧")
        # 自检④：时长
        gd = probe_duration(out)
        if abs(gd - dur) > 0.05:
            sys.exit(f"✗ 成品 {gd:.3f}s，应为 {dur:.3f}s")
        print(f"\n✅ {out}  {got} 帧 / {gd:.3f}s / {os.path.getsize(out)/1e6:.1f} MB")
    finally:
        if args.keep_tmp:
            print(f"   中间文件保留在 {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
