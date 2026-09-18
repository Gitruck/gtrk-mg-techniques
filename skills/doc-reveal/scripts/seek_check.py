#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""seek_check · 本地 seek 预检：用 playwright 把颗粒挂进 harness，逐时刻 seek 抽帧拼接触表，并判「帧是否随时间变化」。

用法：
    python seek_check.py <particle.html> --times 0.3,1.2,2.5,4,6,8,10 --out <sheet.png> [--bg checker|0] [--port 7817]

stdout 一行 JSON：{tl_duration, frames:[{t, sha1}], distinct, ok}。ok=false 表示至少两帧完全相同（疑似冻结）
或时间线未注册。

⚠️ 这是预检，不替代真渲染引擎（html_animate_render）抽帧验收——契约正文写明本地无头模拟测不出 var()-不解析、
CDN 内联失败、StaticGuard 这类只在真引擎暴露的问题。预检的用途是在交付前先把「时间线没注册 / 画面冻结 / 排版越界」
这类低级错误挡在本地。

依赖：playwright（pip install playwright && playwright install chromium）、Pillow。
产物：只写 --out 指定的接触表；临时 http 目录用完即删。
"""
import argparse
import functools
import hashlib
import http.server
import io
import json
import os
import shutil
import socketserver
import sys
import tempfile
import threading

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.join(HERE, "..", "assets", "harness.html")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("particle")
    ap.add_argument("--times", default="0.3,1,2,3.5,5,7,9")
    ap.add_argument("--out", required=True)
    ap.add_argument("--bg", default="checker", help="checker（棋盘格，看透明）或 0（透明背景截图）")
    ap.add_argument("--port", type=int, default=7817)
    ap.add_argument("--tile", type=int, default=480)
    a = ap.parse_args()
    times = [float(x) for x in a.times.split(",") if x.strip()]
    tmp = tempfile.mkdtemp(prefix="doc-reveal-check-")
    try:
        shutil.copy(HARNESS, os.path.join(tmp, "harness.html"))
        shutil.copy(a.particle, os.path.join(tmp, "particle.html"))
        class Quiet(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *args, **kw):
                pass

        Handler = functools.partial(Quiet, directory=tmp)

        class Q(socketserver.TCPServer):
            allow_reuse_address = True

        srv = Q(("127.0.0.1", a.port), Handler)
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        from playwright.sync_api import sync_playwright

        W, Hh = a.tile, int(a.tile * 9 / 16)
        cols = min(4, len(times))
        rows = (len(times) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * (W + 6) + 6, rows * (Hh + 26) + 6), (30, 30, 30))
        d = ImageDraw.Draw(sheet)
        try:
            font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 15)
        except Exception:
            font = ImageFont.load_default()
        frames = []
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page(viewport={"width": 1920, "height": 1120})
            bgq = "0" if a.bg == "0" else "1"
            pg.goto("http://127.0.0.1:%d/harness.html?bg=%s&hud=0&p=particle.html&t=0" % (a.port, bgq))
            pg.wait_for_function("window.__tl && document.fonts.status==='loaded'", timeout=30000)
            pg.evaluate("document.getElementById('stage').style.transform='translateY(40px) scale(1)'")
            tl_dur = pg.evaluate("window.__tl.duration()")
            for k, t in enumerate(times):
                pg.evaluate("window.__seek(%s)" % min(t, tl_dur - 0.01))
                pg.wait_for_timeout(150)
                png = pg.screenshot(omit_background=(a.bg == "0"), clip={"x": 0, "y": 40, "width": 1920, "height": 1080})
                sha = hashlib.sha1(png).hexdigest()[:12]
                frames.append({"t": t, "sha1": sha})
                im = Image.open(io.BytesIO(png)).convert("RGB").resize((W, Hh), Image.LANCZOS)
                x = 6 + (k % cols) * (W + 6)
                y = 6 + (k // cols) * (Hh + 26)
                sheet.paste(im, (x, y))
                d.text((x + 4, y + Hh + 4), "t=%.2fs  %s" % (t, sha), fill=(255, 220, 120), font=font)
            b.close()
        srv.shutdown()
        os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
        sheet.save(a.out)
        distinct = len(set(f["sha1"] for f in frames))
        res = {"tl_duration": round(tl_dur, 3), "frames": frames, "distinct": distinct, "ok": distinct == len(frames), "sheet": os.path.abspath(a.out)}
        sys.stdout.write(json.dumps(res, ensure_ascii=False) + "\n")
        sys.exit(0 if res["ok"] else 1)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
