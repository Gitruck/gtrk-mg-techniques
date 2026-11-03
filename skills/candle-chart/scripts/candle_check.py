#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""candle_check · candle-chart 颗粒的本地验收：在真浏览器里 seek，从画面量出客观读数。

用法：
    python candle_check.py <particle.html> --out <sheet.png> [--video preview.mp4 --fps 30] [--port 7819]

读数（stdout 一行 JSON）：
    price_err_px    从**纵轴刻度文字**（观众读到的数）拟合「价格 → 像素」，反推每根蜡烛的高 / 低 / 开 / 收，
                    与数据对账的最大像素误差。> 0.6 即图画歪了（刻度与蜡烛对不上）
    ma_err_px       按收盘价**另算一遍**均线，与画出来的线逐点对账的最大像素误差
    sweep_bad       生长期抽样时，「该长完却没长完 / 不该出现却已出现」的蜡烛根数（应为 0）
    ma_lag_px       均线笔尖（裁切右沿）与最新长完那根蜡烛中心的最大偏差（应 ≤ 一根的宽度）
    labels          每个标注 / 价位线 / 区间在落下 0.8s 后的不透明度与是否出画、进字幕带
    label_overlap   终态标注标签两两相交的对数（应为 0）
    frames          生长期抽样帧 sha1；相邻相同 = 冻结（终态驻留本来就静止，不算）
    ok              以上全部通过

任一不过退出码 1。

⚠️ 本地预检，不替代真渲染引擎抽帧；真验收仍是 `gtrk mg render` / `gtrk render` 出片抽帧。
依赖：playwright、Pillow；--video 另需 ffmpeg。
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

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.join(HERE, "..", "assets", "harness.html")

STATIC_JS = r"""
(function(){
  var root=document.querySelector('[data-composition-id]');
  var svg=root.querySelector('svg.cc');
  var plot=svg.getAttribute('data-plot').split(',').map(parseFloat);
  var ticks=[].map.call(svg.querySelectorAll('.yt'),function(t){return {v:parseFloat(t.textContent), y:parseFloat(t.getAttribute('y'))};});
  var cs=[].map.call(svg.querySelectorAll('.c'),function(g){
    var ln=g.querySelector('line'), r=g.querySelector('rect');
    return {i:+g.getAttribute('data-i'), t:parseFloat(g.getAttribute('data-t')),
      ohlc:g.getAttribute('data-ohlc').split(',').map(parseFloat),
      x:parseFloat(ln.getAttribute('x1')), y1:parseFloat(ln.getAttribute('y1')), y2:parseFloat(ln.getAttribute('y2')),
      rt:parseFloat(r.getAttribute('y')), rh:parseFloat(r.getAttribute('height')), rw:parseFloat(r.getAttribute('width'))};
  });
  var mas=[].map.call(svg.querySelectorAll('.ma'),function(p){return {p:+p.getAttribute('class').match(/ma(\d+)/)[1], d:p.getAttribute('d')};});
  var gd=parseFloat(svg.querySelector('.cs').getAttribute('data-gd'));
  var labels=[].map.call(svg.querySelectorAll('.mk,.lv,.rg'),function(g){return {cls:g.getAttribute('class'), t:parseFloat(g.getAttribute('data-t'))};});
  return {plot:plot, ticks:ticks, cs:cs, mas:mas, gd:gd, labels:labels, safe:parseFloat(svg.getAttribute('data-safe')),
          ma_mode:svg.getAttribute('data-ma')};
})()
"""

DYN_JS = r"""
(function(){
  var root=document.querySelector('[data-composition-id]');
  var svg=root.querySelector('svg.cc');
  var sc=[].map.call(svg.querySelectorAll('.c'),function(g){return gsap.getProperty(g,'scaleY');});
  var mac=svg.querySelector('.mac'); var w=mac?parseFloat(mac.getAttribute('width')):null;
  return {sc:sc, mac:w};
})()
"""

LABEL_JS = r"""
(function(cls){
  var root=document.querySelector('[data-composition-id]');
  var g=root.querySelector('svg.cc .'+cls.split(' ').join('.'));
  var box=g.querySelector('.mkc,.lvt,rect')||g; var r=box.getBoundingClientRect();
  var op=1, el=box; while(el && el!==root){op*=parseFloat(getComputedStyle(el).opacity); el=el.parentElement;}
  return {op:op, l:r.left, t:r.top-40, r:r.right, b:r.bottom-40};
})
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


def fit(ticks):
    """最小二乘拟合 y = a·price + b（刻度文字是观众读到的数，用它当真值）。"""
    n = len(ticks)
    sx = sum(t["v"] for t in ticks)
    sy = sum(t["y"] for t in ticks)
    sxx = sum(t["v"] ** 2 for t in ticks)
    sxy = sum(t["v"] * t["y"] for t in ticks)
    a = (n * sxy - sx * sy) / (n * sxx - sx * sx)
    return a, (sy - a * sx) / n


def parse_path(d):
    return [(float(x), float(y)) for x, y in re.findall(r"[ML]\s*(-?[\d.]+)\s+(-?[\d.]+)", d)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("particle")
    ap.add_argument("--out", required=True)
    ap.add_argument("--video", default=None)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--port", type=int, default=7819)
    ap.add_argument("--tile", type=int, default=480)
    a = ap.parse_args()
    for st in (sys.stdout, sys.stderr):
        if hasattr(st, "reconfigure"):
            st.reconfigure(encoding="utf-8")
    tmp = tempfile.mkdtemp(prefix="candle-check-")
    try:
        shutil.copy(HARNESS, os.path.join(tmp, "harness.html"))
        shutil.copy(a.particle, os.path.join(tmp, "particle.html"))
        with io.open(a.particle, "r", encoding="utf-8") as f:
            head = f.read(4000)
        CW = int(re.search(r'data-width="(\d+)"', head).group(1))
        CH = int(re.search(r'data-height="(\d+)"', head).group(1))
        srv = serve(tmp, a.port)
        from playwright.sync_api import sync_playwright

        res = {"fail": []}
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

            st = pg.evaluate(STATIC_JS)
            if len(st["ticks"]) < 2:
                res["fail"].append("纵轴刻度少于 2 个，无法拟合价格映射")
                raise SystemExit
            ka, kb = fit(st["ticks"])
            y_of = lambda price: ka * price + kb
            tick_res = max(abs(t["y"] - y_of(t["v"])) for t in st["ticks"])
            # —— 价格对账 ——
            err = 0.0
            worst = None
            for c in st["cs"]:
                o, h, l, cl = c["ohlc"]
                es = [abs(c["y1"] - y_of(h)), abs(c["y2"] - y_of(l)), abs(c["rt"] - y_of(max(o, cl)))]
                if abs(y_of(o) - y_of(cl)) >= 1.0:
                    es.append(abs(c["rt"] + c["rh"] - y_of(min(o, cl))))
                e = max(es)
                if e > err:
                    err, worst = e, c["i"]
            res["price_err_px"] = round(err, 3)
            res["tick_fit_residual_px"] = round(tick_res, 3)
            if err > 0.6:
                res["fail"].append("第 %d 根蜡烛与纵轴刻度对不上，偏 %.2fpx" % (worst, err))
            xs = [c["x"] for c in st["cs"]]
            if any(x1 <= x0 for x0, x1 in zip(xs, xs[1:])):
                res["fail"].append("蜡烛 x 不是严格递增")
            px, py, pw, ph = st["plot"]
            if xs[0] < px or xs[-1] > px + pw:
                res["fail"].append("蜡烛出了价格区")
            # —— 均线对账（自己按收盘价重算）——
            closes = [c["ohlc"][3] for c in st["cs"]]
            ma_err = 0.0
            for m in st["mas"]:
                pts = parse_path(m["d"])
                per = m["p"]
                exp = []
                for i in range(per - 1, len(closes)):
                    exp.append((xs[i], y_of(sum(closes[i - per + 1:i + 1]) / per)))
                if len(exp) != len(pts):
                    res["fail"].append("MA%d 点数 %d，应为 %d" % (per, len(pts), len(exp)))
                    continue
                for (x0, y0), (x1, y1) in zip(pts, exp):
                    ma_err = max(ma_err, abs(x0 - x1), abs(y0 - y1))
            res["ma_err_px"] = round(ma_err, 3)
            if ma_err > 0.6:
                res["fail"].append("均线与按收盘价重算的值偏 %.2fpx" % ma_err)
            # —— 生长进度与均线笔尖 ——
            gd = st["gd"]
            t_first, t_last = st["cs"][0]["t"], st["cs"][-1]["t"] + gd
            grow_times = [t_first + (t_last - t_first) * k / 9.0 for k in range(1, 9)]
            bad, lag = 0, 0.0
            follow = st["ma_mode"] == "follow"
            first_p = min([m["p"] for m in st["mas"]] or [0]) if follow else 0
            slot = (xs[-1] - xs[0]) / max(1, len(xs) - 1)
            for t in grow_times:
                seek(t)
                dy = pg.evaluate(DYN_JS)
                for c, s in zip(st["cs"], dy["sc"]):
                    if t >= c["t"] + gd + 0.01 and s < 0.999:
                        bad += 1
                    if t <= c["t"] - 0.01 and s > 0.001:
                        bad += 1
                if first_p and dy["mac"] is not None:
                    grown = [c for c in st["cs"] if t >= c["t"] + gd]
                    if len(grown) >= first_p:
                        lag = max(lag, abs((grown[-1]["x"] - px) - dy["mac"]))
            res["sweep_bad"] = bad
            res["ma_lag_px"] = round(lag, 2) if first_p else None
            if st["mas"] and not follow:
                # 单独描出：起笔前裁切宽 0，描完铺满价格区
                _, at, mdur = st["ma_mode"].split(",")
                at, mdur = float(at), float(mdur)
                seek(at - 0.05)
                w_before = pg.evaluate(DYN_JS)["mac"]
                seek(at + mdur + 0.05)
                w_after = pg.evaluate(DYN_JS)["mac"]
                res["ma_draw"] = {"at": at, "width_before": w_before, "width_after": w_after, "plot_w": pw}
                if w_before > 0.5 or w_after < pw - 0.5:
                    res["fail"].append("均线单独描出不完整：起笔前宽 %.1f、描完宽 %.1f（价格区宽 %.1f）" % (w_before, w_after, pw))
            if bad:
                res["fail"].append("生长期有 %d 根次的蜡烛长得不对时" % bad)
            if first_p and lag > slot + 0.5:
                res["fail"].append("均线笔尖偏离最新蜡烛 %.1fpx（> 一根宽 %.1fpx）" % (lag, slot))
            # —— 标注 ——
            res["labels"] = []
            for lb in st["labels"]:
                t = lb["t"] + 0.8
                seek(t)
                r = pg.evaluate("(" + LABEL_JS.strip() + ")(%s)" % json.dumps(lb["cls"]))
                item = {"cls": lb["cls"], "t": round(t, 2), "op": round(r["op"], 3)}
                res["labels"].append(item)
                if r["op"] < 0.95:
                    res["fail"].append("%s 在 %.2fs 不透明度只有 %.2f" % (lb["cls"], t, r["op"]))
                if r["l"] < 0 or r["r"] > CW or r["t"] < 0 or r["b"] > st["safe"] + 0.5:
                    res["fail"].append("%s 出画或进字幕带" % lb["cls"])
            seek(dur - 0.01)
            boxes = pg.evaluate(r"""[].map.call(document.querySelectorAll('[data-composition-id] svg.cc .mkc, [data-composition-id] svg.cc .lvt'),function(e){var r=e.getBoundingClientRect();return [r.left,r.top,r.right,r.bottom];})""")
            ov = 0
            for i in range(len(boxes)):
                for j in range(i + 1, len(boxes)):
                    A, B = boxes[i], boxes[j]
                    if A[0] < B[2] and B[0] < A[2] and A[1] < B[3] and B[1] < A[3]:
                        ov += 1
            res["label_overlap"] = ov
            if ov:
                res["fail"].append("终态有 %d 对标注标签相交" % ov)
            # —— 接触表与防冻结 ——
            label_times = sorted(round(l["t"] + 0.8, 2) for l in st["labels"])
            sheet_times = [round(t, 2) for t in grow_times[::2]] + label_times + [round(dur - 0.06, 2)]
            W, Hh = a.tile, int(a.tile * CH / CW)
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
                png = pg.screenshot(clip={"x": 0, "y": 40, "width": CW, "height": CH})
                sha = hashlib.sha1(png).hexdigest()[:12]
                frames.append({"t": t, "sha1": sha})
                im = Image.open(io.BytesIO(png)).convert("RGB").resize((W, Hh), Image.LANCZOS)
                x, y = 6 + (k % cols) * (W + 6), 6 + (k // cols) * (Hh + 26)
                sheet.paste(im, (x, y))
                d.text((x + 4, y + Hh + 4), "t=%.2fs  %s" % (t, sha), fill=(255, 220, 120), font=font)
            sheet.save(a.out)
            res["frames"] = frames
            res["sheet"] = os.path.abspath(a.out)
            grow_frames = frames[:len(grow_times[::2])]
            for f0, f1 in zip(grow_frames, grow_frames[1:]):
                if f0["sha1"] == f1["sha1"]:
                    res["fail"].append("生长期 %.2fs 与 %.2fs 两帧相同（疑似冻结）" % (f0["t"], f1["t"]))
            if a.video:
                n = int(round(dur * a.fps))
                ff = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "image2pipe", "-framerate", str(a.fps), "-i", "-",
                                       "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-preset", "veryfast", a.video],
                                      stdin=subprocess.PIPE)
                for k in range(n):
                    seek(k / float(a.fps))
                    ff.stdin.write(pg.screenshot(type="jpeg", quality=92, clip={"x": 0, "y": 40, "width": CW, "height": CH}))
                ff.stdin.close()
                if ff.wait() != 0:
                    res["fail"].append("ffmpeg 编码预览失败")
                res["video"] = {"path": os.path.abspath(a.video), "frames": n, "fps": a.fps}
            b.close()
        srv.shutdown()
    except SystemExit:
        pass
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    res["ok"] = not res["fail"]
    sys.stdout.write(json.dumps(res, ensure_ascii=False) + "\n")
    sys.exit(0 if res["ok"] else 1)


if __name__ == "__main__":
    main()
