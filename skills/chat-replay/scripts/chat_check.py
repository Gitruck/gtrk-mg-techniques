#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chat_check · chat-replay 颗粒的本地验收：在真浏览器里 seek，量出每条消息「出现后是否完整落在视口里」、
气泡实际宽高与生成期排版是否对得上、断行估宽的误差，并拼接触表；可选逐帧出一段本地预览 mp4。

用法：
    python chat_check.py <particle.html> --out <sheet.png> [--video preview.mp4 --fps 30] [--port 7818]

读数（stdout 一行 JSON）：
    messages[]      每条带 t 的消息在 t+0.6s 时的可见比例（1.0 = 完整落在视口内）
    typing[]        每个「打字中」气泡在出现 0.35s 后的可见比例
    bubble_w_over   气泡实际宽 − 生成期上限（px，>1 即断行估窄了、气泡被字撑破上限）
    bubble_h_err    气泡实际高 − 生成期排版高（px，≠0 即上下会叠）
    est_ratio       每行真实字宽 / 生成期估宽 的 min / max / mean（> 1 说明估窄了）
    frames          抽样帧 sha1；相邻两帧相同 = 冻结
    ok              以上全部通过

任一不过退出码 1。

⚠️ 这是本地预检，不替代真渲染引擎抽帧：本地 chromium 用的是本机字体，渲染服务端字体表不同时字宽会有出入，
真验收仍是 `gtrk mg render` / `gtrk render` 出片抽帧。

依赖：playwright（pip install playwright && playwright install chromium）、Pillow；--video 另需 ffmpeg。
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
import subprocess
import sys
import tempfile
import threading

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.join(HERE, "..", "assets", "harness.html")

PROBE_JS = r"""
(function(){
  var root=document.querySelector('[data-composition-id]');
  var vp=root.querySelector('.vp').getBoundingClientRect();
  function vis(el){
    var r=el.getBoundingClientRect(); var a=r.width*r.height; if(a<=0) return 0;
    var w=Math.max(0,Math.min(r.right,vp.right)-Math.max(r.left,vp.left));
    var h=Math.max(0,Math.min(r.bottom,vp.bottom)-Math.max(r.top,vp.top));
    return (w*h)/a;
  }
  var out={msgs:{},typing:{}};
  root.querySelectorAll('.m').forEach(function(m){
    var b=m.querySelector('.bb')||m.querySelector('.sp');
    out.msgs[m.getAttribute('data-i')]={vis:vis(b),op:parseFloat(getComputedStyle(m).opacity)};
  });
  root.querySelectorAll('.ty').forEach(function(t){
    out.typing[t.className.match(/ty(\d+)/)[1]]={vis:vis(t.querySelector('.bb')),op:parseFloat(getComputedStyle(t).opacity)};
  });
  return out;
})()
"""

STATIC_JS = r"""
(function(){
  var root=document.querySelector('[data-composition-id]');
  var bubbles=[], lines=[];
  root.querySelectorAll('.m .bb').forEach(function(b){
    var m=b.closest('.m');
    bubbles.push({i:+m.getAttribute('data-i'), w:b.offsetWidth, h:b.offsetHeight,
                  maxw:parseFloat(b.getAttribute('data-maxw')), plan_h:parseFloat(b.getAttribute('data-h'))});
  });
  root.querySelectorAll('.m .ln').forEach(function(l){
    var ew=parseFloat(l.getAttribute('data-ew'));
    if(ew>0) lines.push({i:+l.closest('.m').getAttribute('data-i'), w:l.offsetWidth, ew:ew, text:l.textContent});
  });
  var msgs=[].map.call(root.querySelectorAll('.m'),function(m){return {i:+m.getAttribute('data-i'), t:m.getAttribute('data-t')};});
  var typing=[].map.call(root.querySelectorAll('.ty'),function(t){return {i:+t.className.match(/ty(\d+)/)[1], t0:parseFloat(t.getAttribute('data-t0'))};});
  return {bubbles:bubbles, lines:lines, msgs:msgs, typing:typing};
})()
"""


def serve(tmp, port):
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args, **kw):
            pass

    class Q(socketserver.TCPServer):
        allow_reuse_address = True

    srv = Q(("127.0.0.1", port), functools.partial(Quiet, directory=tmp))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("particle")
    ap.add_argument("--out", required=True, help="接触表 png")
    ap.add_argument("--video", default=None, help="可选：逐帧 seek 出本地预览 mp4（不是验收，只给人看节奏）")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--port", type=int, default=7818)
    ap.add_argument("--tile", type=int, default=480)
    a = ap.parse_args()
    for st in (sys.stdout, sys.stderr):
        if hasattr(st, "reconfigure"):
            st.reconfigure(encoding="utf-8")
    tmp = tempfile.mkdtemp(prefix="chat-replay-check-")
    try:
        shutil.copy(HARNESS, os.path.join(tmp, "harness.html"))
        shutil.copy(a.particle, os.path.join(tmp, "particle.html"))
        srv = serve(tmp, a.port)
        from playwright.sync_api import sync_playwright

        res = {"ok": True, "fail": []}
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page(viewport={"width": 1920, "height": 1120})
            pg.goto("http://127.0.0.1:%d/harness.html?hud=0&p=particle.html&t=0" % a.port)
            pg.wait_for_function("window.__tl && document.fonts.status==='loaded'", timeout=30000)
            pg.evaluate("document.getElementById('stage').style.transform='translateY(40px) scale(1)'")
            dur = pg.evaluate("window.__tl.duration()")
            res["tl_duration"] = round(dur, 3)

            def seek(t):
                pg.evaluate("window.__seek(%s)" % min(max(t, 0), dur - 0.001))

            # —— 静态量：气泡宽高、逐行估宽误差（在终态量，所有消息都已排好）——
            seek(dur - 0.01)
            st = pg.evaluate(STATIC_JS)
            over = max([bb["w"] - bb["maxw"] for bb in st["bubbles"]] or [0])
            herr = max([abs(bb["h"] - bb["plan_h"]) for bb in st["bubbles"]] or [0])
            ratios = [ln["w"] / ln["ew"] for ln in st["lines"]]
            res["bubble_w_over"] = round(over, 1)
            res["bubble_h_err"] = round(herr, 1)
            res["est_ratio"] = {"min": round(min(ratios), 3), "max": round(max(ratios), 3),
                                "mean": round(sum(ratios) / len(ratios), 3)} if ratios else None
            if over > 1:
                worst = max(st["bubbles"], key=lambda x: x["w"] - x["maxw"])
                res["fail"].append("消息 %d 的气泡实际宽 %dpx 超过上限 %dpx（断行估窄了）" % (worst["i"], worst["w"], worst["maxw"]))
            if herr > 1:
                res["fail"].append("有气泡实际高与排版高差 %.1fpx（上下会叠）" % herr)

            # —— 动态量：每条消息出现后是否完整落在视口里 ——
            samples = []
            res["messages"], res["typing"] = [], []
            for m in st["msgs"]:
                if m["t"] == "pre":
                    continue
                t = float(m["t"]) + 0.6
                seek(t)
                pr = pg.evaluate(PROBE_JS)
                v = pr["msgs"][str(m["i"])]
                res["messages"].append({"i": m["i"], "t": round(t, 2), "vis": round(v["vis"], 3), "op": round(v["op"], 2)})
                if v["vis"] < 0.995:
                    res["fail"].append("消息 %d 在 %.2fs 只露出 %.0f%%（滚动没跟上或被裁）" % (m["i"], t, v["vis"] * 100))
                if v["op"] < 0.95:
                    res["fail"].append("消息 %d 在 %.2fs 不透明度只有 %.2f" % (m["i"], t, v["op"]))
                samples.append(t)
            for ty in st["typing"]:
                t = ty["t0"] + 0.35
                seek(t)
                pr = pg.evaluate(PROBE_JS)
                v = pr["typing"][str(ty["i"])]
                res["typing"].append({"i": ty["i"], "t": round(t, 2), "vis": round(v["vis"], 3), "op": round(v["op"], 2)})
                if v["vis"] < 0.995 or v["op"] < 0.9:
                    res["fail"].append("消息 %d 的打字中气泡在 %.2fs 没有完整露出" % (ty["i"], t))
                samples.append(t)

            # —— 接触表 ——
            sheet_times = sorted(set([0.05] + [round(x, 2) for x in samples] + [round(dur - 0.06, 2)]))
            W, Hh = a.tile, int(a.tile * 9 / 16)
            cols = 4
            rows = (len(sheet_times) + cols - 1) // cols
            sheet = Image.new("RGB", (cols * (W + 6) + 6, rows * (Hh + 26) + 6), (30, 30, 30))
            d = ImageDraw.Draw(sheet)
            try:
                font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 15)
            except Exception:
                font = ImageFont.load_default()
            frames = []
            for k, t in enumerate(sheet_times):
                seek(t)
                png = pg.screenshot(clip={"x": 0, "y": 40, "width": 1920, "height": 1080})
                sha = hashlib.sha1(png).hexdigest()[:12]
                frames.append({"t": t, "sha1": sha})
                im = Image.open(io.BytesIO(png)).convert("RGB").resize((W, Hh), Image.LANCZOS)
                x, y = 6 + (k % cols) * (W + 6), 6 + (k // cols) * (Hh + 26)
                sheet.paste(im, (x, y))
                d.text((x + 4, y + Hh + 4), "t=%.2fs  %s" % (t, sha), fill=(255, 220, 120), font=font)
            os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
            sheet.save(a.out)
            res["frames"] = frames
            res["sheet"] = os.path.abspath(a.out)
            for f0, f1 in zip(frames, frames[1:]):
                if f0["sha1"] == f1["sha1"]:
                    res["fail"].append("%.2fs 与 %.2fs 两帧完全相同（疑似冻结）" % (f0["t"], f1["t"]))

            # —— 可选：本地预览视频 ——
            if a.video:
                n = int(round(dur * a.fps))
                ff = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "image2pipe", "-framerate", str(a.fps), "-i", "-",
                                       "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-preset", "veryfast", a.video],
                                      stdin=subprocess.PIPE)
                for k in range(n):
                    seek(k / float(a.fps))
                    ff.stdin.write(pg.screenshot(type="jpeg", quality=92, clip={"x": 0, "y": 40, "width": 1920, "height": 1080}))
                ff.stdin.close()
                if ff.wait() != 0:
                    res["fail"].append("ffmpeg 编码预览失败")
                res["video"] = {"path": os.path.abspath(a.video), "frames": n, "fps": a.fps}
            b.close()
        srv.shutdown()
        res["ok"] = not res["fail"]
        sys.stdout.write(json.dumps(res, ensure_ascii=False) + "\n")
        sys.exit(0 if res["ok"] else 1)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
