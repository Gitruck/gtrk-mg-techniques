#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""lyric_check · lyric-pv 颗粒的本地验收：在真浏览器里 seek、截图，**看像素**而不只读 DOM。

用法：
    python lyric_check.py <particle.html> --out <sheet.png> [--video preview.mp4 --fps 30] [--port 7821]

逐句读数（stdout 一行 JSON）：
    ink_min / ink_median  停留中段截图里，这句每个字的框内「接近该字颜色」的像素占比的最小值 / 中位数（逐字 ≥ 0.008、中位 ≥ 0.04；
                 单笔画的字如「一」框里本来就没多少墨，所以逐字只要求「有墨」）。
                 字被裁掉、被盖住、透明，这个数都会掉到 0——只读 DOM 看不出这类问题（实测过：容器 0 高时
                 clip-path 把整句裁没，DOM 里每个字的位置、不透明度都正常）
    overlap_max  字框两两相交面积 / 较小字框面积 的最大值（散排、强调放大时最容易压字）
    out_of_frame 排出画幅的字数（跑马灯不算）
    text_ok      显示出来的字（去掉干扰字）连起来是否等于歌词原文
    before / done / after  这句在起点前 0.04s 的不透明度（应为 0）、登场结束时 ink 达标的字比例（应为 1）、
                 终点后 0.03s 的不透明度（应为 0；最后一句驻留，不查）
    ok           全部通过

⚠️ 本地预检，不替代真渲染引擎抽帧。依赖：playwright、Pillow、numpy；--video 另需 ffmpeg。
"""
import argparse
import functools
import hashlib
import http.server
import io
import json
import os
import re
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading

import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.join(HERE, "..", "assets", "harness.html")

LINES_JS = r"""[].map.call(document.querySelectorAll('[data-composition-id] .L'),function(L){
  return {i:+L.getAttribute('data-i'), t:+L.getAttribute('data-t'), end:+L.getAttribute('data-end'), ed:+L.getAttribute('data-ed'),
          layout:L.getAttribute('data-layout'), text:L.getAttribute('data-text')};})"""

CHARS_JS = r"""(function(i){
  var root=document.querySelector('[data-composition-id]'), L=root.querySelector('.L'+i);
  function op(el){var o=1;while(el&&el!==root){o*=parseFloat(getComputedStyle(el).opacity);el=el.parentElement;}return o;}
  return {Lop:parseFloat(getComputedStyle(L).opacity), chars:[].map.call(L.querySelectorAll('.c:not(.f)'),function(c){
    var r=c.getBoundingClientRect(); return {ch:c.textContent, x:r.left, y:r.top-40, w:r.width, h:r.height, op:op(c), color:getComputedStyle(c).color};})};
})"""


def serve(tmp, port):
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a, **k):
            pass

    class Q(socketserver.TCPServer):
        allow_reuse_address = True

    srv = Q(("127.0.0.1", port), functools.partial(Quiet, directory=tmp))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def rgb(css):
    m = re.findall(r"[\d.]+", css)
    return np.array([float(m[0]), float(m[1]), float(m[2])], dtype=np.float32)


def ink(img, c):
    x0, y0 = max(0, int(c["x"])), max(0, int(c["y"]))
    x1, y1 = min(img.shape[1], int(c["x"] + c["w"])), min(img.shape[0], int(c["y"] + c["h"]))
    if x1 - x0 < 2 or y1 - y0 < 2:
        return 0.0
    reg = img[y0:y1, x0:x1].astype(np.float32)
    d = np.sqrt(((reg - rgb(c["color"])) ** 2).sum(axis=2))
    return float((d < 70).mean())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("particle")
    ap.add_argument("--out", required=True)
    ap.add_argument("--video", default=None)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--port", type=int, default=7821)
    ap.add_argument("--tile", type=int, default=480)
    a = ap.parse_args()
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8")
    tmp = tempfile.mkdtemp(prefix="lyric-check-")
    res = {"fail": []}
    try:
        shutil.copy(HARNESS, os.path.join(tmp, "harness.html"))
        shutil.copy(a.particle, os.path.join(tmp, "particle.html"))
        with io.open(a.particle, "r", encoding="utf-8") as f:
            head = f.read(4000)
        CW = int(re.search(r'data-width="(\d+)"', head).group(1))
        CH = int(re.search(r'data-height="(\d+)"', head).group(1))
        srv = serve(tmp, a.port)
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page(viewport={"width": CW, "height": CH + 40})
            pg.goto("http://127.0.0.1:%d/harness.html?hud=0&p=particle.html&t=0" % a.port)
            pg.wait_for_function("window.__tl && document.fonts.status==='loaded'", timeout=30000)
            pg.evaluate("document.getElementById('stage').style.transform='translateY(40px) scale(1)'")
            dur = pg.evaluate("window.__tl.duration()")
            res["tl_duration"] = round(dur, 3)

            def seek(t):
                pg.evaluate("window.__seek(%s)" % min(max(t, 0), dur - 0.001))

            def shot():
                return np.asarray(Image.open(io.BytesIO(pg.screenshot(clip={"x": 0, "y": 40, "width": CW, "height": CH}))).convert("RGB"))

            lines = pg.evaluate(LINES_JS)
            n = len(lines)
            per, mids = [], []
            for ln in lines:
                i = ln["i"]
                nxt_t = lines[i + 1]["t"] if i + 1 < n else dur
                t_mid = ln["t"] + ln["ed"] + 0.4 * max(0.0, (ln["end"] - ln["t"] - ln["ed"]))
                t_mid = min(t_mid, nxt_t - 0.12) if i + 1 < n else min(t_mid, dur - 0.05)
                seek(t_mid)
                st = pg.evaluate("(" + CHARS_JS.strip() + ")(%d)" % i)
                img = shot()
                mids.append((t_mid, img))
                inside = lambda c: c["x"] >= 0 and c["y"] >= 0 and c["x"] + c["w"] <= CW and c["y"] + c["h"] <= CH
                # 跑马灯本来就有一部分字流在画外：只量完整落在画内的字
                chs = [c for c in st["chars"] if c["ch"].strip() and (ln["layout"] != "ticker" or inside(c))]
                inks = [ink(img, c) for c in chs]
                ov = 0.0
                for x in range(len(chs)):
                    for y in range(x + 1, len(chs)):
                        A, B = chs[x], chs[y]
                        iw = min(A["x"] + A["w"], B["x"] + B["w"]) - max(A["x"], B["x"])
                        ih = min(A["y"] + A["h"], B["y"] + B["h"]) - max(A["y"], B["y"])
                        if iw > 0 and ih > 0:
                            ov = max(ov, iw * ih / max(1.0, min(A["w"] * A["h"], B["w"] * B["h"])))
                oof = 0 if ln["layout"] == "ticker" else sum(1 for c in chs if c["x"] < -1 or c["y"] < -1 or c["x"] + c["w"] > CW + 1 or c["y"] + c["h"] > CH + 1)
                shown = "".join(c["ch"] for c in st["chars"])
                want = ln["text"]
                text_ok = (re.sub(r"\s", "", shown).startswith(re.sub(r"\s", "", want)) if ln["layout"] == "ticker"
                           else re.sub(r"\s", "", shown) == re.sub(r"\s", "", want))
                # 时序
                seek(ln["t"] - 0.04)
                before = pg.evaluate("(" + CHARS_JS.strip() + ")(%d)" % i)["Lop"] if ln["t"] >= 0.04 else 0.0
                seek(ln["t"] + ln["ed"] + 0.06)
                st2 = pg.evaluate("(" + CHARS_JS.strip() + ")(%d)" % i)
                img2 = shot()
                chs2 = [c for c in st2["chars"] if c["ch"].strip() and (ln["layout"] != "ticker" or inside(c))]
                done = (sum(1 for c in chs2 if ink(img2, c) >= 0.008) / float(len(chs2))) if chs2 else 0.0
                after = None
                if i + 1 < n:
                    seek(ln["end"] + 0.03)
                    after = pg.evaluate("(" + CHARS_JS.strip() + ")(%d)" % i)["Lop"]
                row = {"i": i, "text": want, "layout": ln["layout"], "t_mid": round(t_mid, 2),
                       "ink_min": round(min(inks), 3) if inks else 0.0, "ink_median": round(float(np.median(inks)), 3) if inks else 0.0, "overlap_max": round(ov, 3), "out_of_frame": oof,
                       "text_ok": text_ok, "before": round(before, 2), "done": round(done, 2), "after": None if after is None else round(after, 2)}
                per.append(row)
                # 单笔画的字（如「一」）框里本来就没多少墨：逐字只要求「有墨」，整句再看中位数
                if row["ink_min"] < 0.008 or row["ink_median"] < 0.04:
                    res["fail"].append("第 %d 句「%s」在 %.2fs 有字没显出来（ink 最小 %.3f、中位 %.3f）" % (i, want, t_mid, row["ink_min"], row["ink_median"]))
                if ov > 0.25:
                    res["fail"].append("第 %d 句「%s」有两个字互相压住（重叠 %.0f%%）" % (i, want, ov * 100))
                if oof:
                    res["fail"].append("第 %d 句「%s」有 %d 个字排出画幅" % (i, want, oof))
                if not text_ok:
                    res["fail"].append("第 %d 句显示的是「%s」，应为「%s」" % (i, shown, want))
                if before > 0.01:
                    res["fail"].append("第 %d 句在起点前就可见（%.2f）" % (i, before))
                if done < 0.999:
                    res["fail"].append("第 %d 句登场结束时只有 %.0f%% 的字显出来" % (i, done * 100))
                if after is not None and after > 0.01:
                    res["fail"].append("第 %d 句过了终点还没消失（%.2f）" % (i, after))
            res["lines"] = per
            # 接触表：每句停留中段一帧 + 末帧
            seek(dur - 0.06)
            mids.append((dur - 0.06, shot()))
            W_, H_ = a.tile, int(a.tile * CH / CW)
            cols = 4
            rows = (len(mids) + cols - 1) // cols
            sheet = Image.new("RGB", (cols * (W_ + 6) + 6, rows * (H_ + 26) + 6), (30, 30, 30))
            dr = ImageDraw.Draw(sheet)
            try:
                font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 15)
            except Exception:
                font = ImageFont.load_default()
            frames = []
            for k, (t, img) in enumerate(mids):
                im = Image.fromarray(img)
                sha = hashlib.sha1(im.tobytes()).hexdigest()[:12]
                frames.append({"t": round(t, 2), "sha1": sha})
                x, y = 6 + (k % cols) * (W_ + 6), 6 + (k // cols) * (H_ + 26)
                sheet.paste(im.resize((W_, H_), Image.LANCZOS), (x, y))
                dr.text((x + 4, y + H_ + 4), "t=%.2fs  %s" % (t, sha), fill=(255, 220, 120), font=font)
            sheet.save(a.out)
            res["frames"] = frames
            res["sheet"] = os.path.abspath(a.out)
            if a.video:
                nfr = int(round(dur * a.fps))
                ff = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "image2pipe", "-framerate", str(a.fps), "-i", "-",
                                       "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-preset", "veryfast", a.video], stdin=subprocess.PIPE)
                for k in range(nfr):
                    seek(k / float(a.fps))
                    ff.stdin.write(pg.screenshot(type="jpeg", quality=92, clip={"x": 0, "y": 40, "width": CW, "height": CH}))
                ff.stdin.close()
                if ff.wait() != 0:
                    res["fail"].append("ffmpeg 编码预览失败")
                res["video"] = {"path": os.path.abspath(a.video), "frames": nfr, "fps": a.fps}
            b.close()
        srv.shutdown()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    res["ok"] = not res["fail"]
    sys.stdout.write(json.dumps(res, ensure_ascii=False) + "\n")
    sys.exit(0 if res["ok"] else 1)


if __name__ == "__main__":
    main()
