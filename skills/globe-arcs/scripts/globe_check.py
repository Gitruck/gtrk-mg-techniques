#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""globe_check · globe-arcs 颗粒的本地验收：在真浏览器里 seek，用**本工具自己的投影**对账画面。

用法：
    python globe_check.py <particle.html> --out <sheet.png> [--video preview.mp4 --fps 30] [--port 7820]

读数（stdout 一行 JSON）：
    land            地理朝向：一组远离海岸的已知陆地点与大洋点，在正面时按本工具的投影算出屏幕位置，
                    去**陆地层 canvas** 读像素——陆地点下要有点阵（hit），大洋点下不能有（false）。
                    地图镜像、经度符号反、旋转方向错，这一项都会挂
    proj_err_px     本工具算的地点屏幕位置 vs 颗粒实际画的位置，最大偏差
    arc_end_err_px  每条飞线到达时刻，线的末端 vs 落点投影，最大偏差
    arrive_vis      每条飞线落地 0.45s 后，落点标记的可见度（应 ≥ 0.95）
    labels          终态正面标签（按实测字宽）是否出画、进字幕带、互相叠
    frames          抽样帧 sha1；动作期相邻相同 = 冻结
    ok              以上全部通过

任一不过退出码 1。

⚠️ 本地预检，不替代真渲染引擎抽帧。本技法靠时间线 onUpdate 回调整帧重画 canvas——gsap-emit「回调与 seek 语义」
规定定帧时回调可达是引擎侧义务，但首次上线 MUST 先用 `gtrk mg render` 真渲一颗抽帧确认。
依赖：playwright、Pillow；--video 另需 ffmpeg。
"""
import argparse
import functools
import hashlib
import http.server
import io
import json
import math
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

# 远离海岸线（≥ 3°）的已知点，只用来验地理朝向；与陆地数据源无关，手工按地理常识取
LAND_PROBES = [
    ("撒哈拉", 22, 12), ("中非", 5, 22), ("西伯利亚", 62, 100), ("哈萨克斯坦", 48, 68), ("青藏", 33, 88),
    ("阿拉伯半岛", 23, 46), ("澳洲腹地", -25, 133), ("美国中部", 40, -98), ("加拿大", 58, -105),
    ("巴西", -10, -55), ("阿根廷北部", -28, -63), ("格陵兰", 74, -40), ("南极", -82, 40), ("东欧", 52, 30),
]
OCEAN_PROBES = [
    ("赤道太平洋", 0, -150), ("南太平洋", -40, -120), ("北太平洋", 40, -160), ("西太平洋", 15, 155),
    ("北大西洋", 32, -42), ("南大西洋", -30, -15), ("赤道大西洋", 0, -25), ("印度洋", -25, 80),
    ("阿拉伯海", 12, 64), ("南大洋", -58, 150), ("塔斯曼海", -40, 160), ("墨西哥湾以东", 26, -65),
]

DBG_JS = "(function(){var gf=document.querySelector('[data-composition-id] canvas.gf');return {dbg:gf.__dbg, def:gf.__def};})()"

PIX_JS = r"""
(function(pts){
  var c=document.querySelector('[data-composition-id] canvas.gl'), x=c.getContext('2d'), out=[];
  pts.forEach(function(p){
    var r=p[2], d=x.getImageData(Math.round(p[0]-r),Math.round(p[1]-r),2*r+1,2*r+1).data, m=0;
    for(var i=3;i<d.length;i+=4){ if(d[i]>m) m=d[i]; }
    out.push(m);
  });
  return out;
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


def proj(lat, lon, lon0, lat0, R, cx, cy, r=1.0):
    """正射投影（独立实现：先绕 z 轴转经度，再绕 x 轴转纬度）。"""
    la, lo = math.radians(lat), math.radians(lon - lon0)
    b0 = math.radians(lat0)
    x = math.cos(la) * math.sin(lo)
    y = math.cos(b0) * math.sin(la) - math.sin(b0) * math.cos(la) * math.cos(lo)
    z = math.sin(b0) * math.sin(la) + math.cos(b0) * math.cos(la) * math.cos(lo)
    return cx + R * r * x, cy - R * r * y, z


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("particle")
    ap.add_argument("--out", required=True)
    ap.add_argument("--video", default=None)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--port", type=int, default=7820)
    ap.add_argument("--tile", type=int, default=480)
    a = ap.parse_args()
    for st in (sys.stdout, sys.stderr):
        if hasattr(st, "reconfigure"):
            st.reconfigure(encoding="utf-8")
    tmp = tempfile.mkdtemp(prefix="globe-check-")
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
                return pg.evaluate(DBG_JS)

            d0 = seek(0.0)
            df = d0["def"]
            places = df["places"]
            # —— 地理朝向 + 投影一致：每秒抽一帧 ——
            times = [0.05] + [float(k) for k in range(1, int(dur))] + [dur - 0.06]
            hit = tot_l = fp = tot_o = 0
            perr = 0.0
            misses = []
            for t in times:
                st = seek(t)
                dbg = st["dbg"]
                lon0, lat0, R = dbg["S"]["lon"], dbg["S"]["lat"], dbg["R"]
                cx, cy = dbg["cx"], dbg["cy"]
                spacing = R * math.radians(df["step"])
                win = max(2, int(math.ceil(spacing * 0.75)))
                pts, kinds = [], []
                for (nm, la, lo) in LAND_PROBES:
                    x, y, z = proj(la, lo, lon0, lat0, R, cx, cy)
                    if z > 0.35 and 0 <= x < CW and 0 <= y < CH:
                        pts.append([x, y, win]); kinds.append(("L", nm))
                for (nm, la, lo) in OCEAN_PROBES:
                    x, y, z = proj(la, lo, lon0, lat0, R, cx, cy)
                    if z > 0.35 and 0 <= x < CW and 0 <= y < CH:
                        pts.append([x, y, max(2, win - 2)]); kinds.append(("O", nm))
                if pts:
                    alphas = pg.evaluate("(" + PIX_JS.strip() + ")(%s)" % json.dumps(pts))
                    for (kd, nm), al in zip(kinds, alphas):
                        if kd == "L":
                            tot_l += 1
                            if al > 40:
                                hit += 1
                            else:
                                misses.append("%s@%.1fs" % (nm, t))
                        else:
                            tot_o += 1
                            if al > 40:
                                fp += 1
                                misses.append("%s(海上有点)@%.1fs" % (nm, t))
                for mk, pl in zip(dbg["marks"], places):
                    x, y, z = proj(pl["lat"], pl["lon"], lon0, lat0, R, cx, cy)
                    if z > 0:
                        perr = max(perr, abs(x - mk["x"]), abs(y - mk["y"]))
            res["land"] = {"land_probes": tot_l, "land_hit": hit, "ocean_probes": tot_o, "ocean_false": fp, "misses": misses[:8]}
            res["proj_err_px"] = round(perr, 3)
            if tot_l < 3 or tot_o < 3:
                res["fail"].append("正面可验的陆地 / 大洋探针太少（%d / %d），地理朝向没验到" % (tot_l, tot_o))
            if tot_l and hit < tot_l:
                res["fail"].append("有 %d 个陆地探针下没有点阵（地图可能镜像或转反了）" % (tot_l - hit))
            if fp:
                res["fail"].append("有 %d 个大洋探针下出现点阵" % fp)
            if perr > 0.75:
                res["fail"].append("地点画的位置与投影偏 %.2fpx" % perr)
            # —— 飞线落点 ——
            aerr, avis = 0.0, []
            for k, arc in enumerate(df["arcs"]):
                st = seek(arc["t_arr"] + 0.02)
                dbg = st["dbg"]
                hd = [h for h in dbg["heads"] if h["k"] == k]
                B = places[arc["b"]]
                x, y, z = proj(B["lat"], B["lon"], dbg["S"]["lon"], dbg["S"]["lat"], dbg["R"], dbg["cx"], dbg["cy"])
                if not hd:
                    res["fail"].append("飞线 %d 到达时没有画出来" % k)
                    continue
                aerr = max(aerr, math.hypot(hd[0]["x"] - x, hd[0]["y"] - y))
                st2 = seek(arc["t_arr"] + 0.45)
                mk = [m for m in st2["dbg"]["marks"] if m["id"] == B["id"]][0]
                avis.append({"arc": k, "to": B["id"], "vis": round(mk["a"], 3), "z": round(mk["z"], 3)})
                if mk["a"] < 0.95:
                    res["fail"].append("飞线 %d 落地后落点 %s 的标记只有 %.2f 可见" % (k, B["id"], mk["a"]))
            res["arc_end_err_px"] = round(aerr, 3)
            res["arrive_vis"] = avis
            if aerr > 1.0:
                res["fail"].append("飞线末端与落点投影偏 %.2fpx" % aerr)
            # —— 终态标签（实测字宽）——
            st = seek(dur - 0.06)
            labs = [l for l in st["dbg"]["labels"] if l["a"] > 0.5]
            bad = []
            for l in labs:
                if l["x"] < 0 or l["x"] + l["w"] > CW or l["y"] < 0 or l["y"] + l["h"] > df["SAFE"] + 0.5:
                    bad.append("%s 出画或进字幕带" % l["id"])
            for i in range(len(labs)):
                for j in range(i + 1, len(labs)):
                    A, B = labs[i], labs[j]
                    if A["x"] < B["x"] + B["w"] and B["x"] < A["x"] + A["w"] and A["y"] < B["y"] + B["h"] and B["y"] < A["y"] + A["h"]:
                        bad.append("%s 与 %s 叠" % (A["id"], B["id"]))
            res["labels"] = {"visible": len(labs), "bad": bad}
            res["fail"].extend("终态标签：" + x for x in bad)
            # —— 接触表与防冻结 ——
            ev = sorted(set([0.3] + [round(x["t_arr"] + 0.3, 2) for x in df["arcs"]] + [round(x["t"] + 0.5 * (x["t_arr"] - x["t"]), 2) for x in df["arcs"]] + [round(dur - 0.06, 2)]))
            W, Hh = a.tile, int(a.tile * CH / CW)
            cols = 4
            rows = (len(ev) + cols - 1) // cols
            sheet = Image.new("RGB", (cols * (W + 6) + 6, rows * (Hh + 26) + 6), (30, 30, 30))
            dr = ImageDraw.Draw(sheet)
            try:
                font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 15)
            except Exception:
                font = ImageFont.load_default()
            frames = []
            for k, t in enumerate(ev):
                seek(t)
                png = pg.screenshot(clip={"x": 0, "y": 40, "width": CW, "height": CH})
                sha = hashlib.sha1(png).hexdigest()[:12]
                frames.append({"t": t, "sha1": sha})
                im = Image.open(io.BytesIO(png)).convert("RGB").resize((W, Hh), Image.LANCZOS)
                x, y = 6 + (k % cols) * (W + 6), 6 + (k // cols) * (Hh + 26)
                sheet.paste(im, (x, y))
                dr.text((x + 4, y + Hh + 4), "t=%.2fs  %s" % (t, sha), fill=(255, 220, 120), font=font)
            sheet.save(a.out)
            res["frames"] = frames
            res["sheet"] = os.path.abspath(a.out)
            last_act = max([x["t_arr"] + x["rings"] * 0.9 for x in df["arcs"]] or [0])
            act = [f for f in frames if f["t"] <= last_act]
            for f0, f1 in zip(act, act[1:]):
                if f0["sha1"] == f1["sha1"]:
                    res["fail"].append("动作期 %.2fs 与 %.2fs 两帧相同（疑似冻结）" % (f0["t"], f1["t"]))
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
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    res["ok"] = not res["fail"]
    sys.stdout.write(json.dumps(res, ensure_ascii=False) + "\n")
    sys.exit(0 if res["ok"] else 1)


if __name__ == "__main__":
    main()
