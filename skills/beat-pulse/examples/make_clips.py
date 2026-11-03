#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""make_clips · 生成示例用的合成素材（ffmpeg 内置的程序化信号源，不含任何外部画面）。

用法：
    python make_clips.py [--out clips] [--seconds 6] [--fps 60] [--bpm 120]

产出 <out>/c01.mp4 … c06.mp4（1920×1080，各 6 秒）与 <out>/beat120.wav（120 BPM 合成鼓点，只给预览配声用）。
这些只是示例素材：真用时换成你自己片子里的镜头。
"""
import argparse
import os
import subprocess

SOURCES = [
    "mandelbrot=s=1920x1080:r={fps}:maxiter=600",
    "testsrc2=s=1920x1080:r={fps}",
    "life=s=480x270:r={fps}:mold=12:ratio=0.08:life_color=#38c8ff:death_color=#14182a:mold_color=#ff6a3d,scale=1920:1080:flags=neighbor",
    "cellauto=s=480x270:r={fps}:rule=30:scroll=1,scale=1920:1080:flags=neighbor,format=rgb24,lutrgb=r='val*0.35+30':g='val*0.75+20':b='val*0.55+60'",
    "sierpinski=s=1920x1080:r={fps}:type=carpet:jump=60,lutrgb=r='val':g='val*0.8':b='val*0.35'",
    "gradients=s=1920x1080:r={fps}:speed=0.03:n=4:c0=#2b5876:c1=#e96443:c2=#f6d365:c3=#4e4376,drawgrid=w=120:h=120:t=3:c=white@0.28",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "clips"))
    ap.add_argument("--seconds", type=float, default=6)
    ap.add_argument("--fps", type=int, default=60)
    ap.add_argument("--bpm", type=float, default=120)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    for i, src in enumerate(SOURCES, start=1):
        dst = os.path.join(a.out, "c%02d.mp4" % i)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", src.format(fps=a.fps), "-t", str(a.seconds),
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", dst], check=True)
        print(dst)
    # 合成鼓点：每拍一下低频「咚」，每小节第一拍更重；只给预览配声
    beat = 60.0 / a.bpm
    kick = "0.9*sin(2*PI*(55+90*exp(-mod(t\\,{b})*30))*mod(t\\,{b}))*exp(-mod(t\\,{b})*9)*(1+0.6*lt(mod(t\\,{bar})\\,{b}))/1.6".format(b=beat, bar=beat * 4)
    hat = "0.12*sin(2*PI*7000*t)*exp(-mod(t+{h}\\,{b})*60)".format(b=beat, h=beat / 2)
    wav = os.path.join(a.out, "beat%d.wav" % int(a.bpm))
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "aevalsrc=%s+%s:s=48000:d=%s" % (kick, hat, a.seconds * 6),
                    "-c:a", "pcm_s16le", wav], check=True)
    print(wav)


if __name__ == "__main__":
    main()
