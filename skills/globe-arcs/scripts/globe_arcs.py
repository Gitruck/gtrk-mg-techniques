#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""globe-arcs · 点阵地球飞线颗粒生成器（gsap-emit v1 合规）

用法：
    python globe_arcs.py <spec.json> --out <particle.html>

spec 字段见 ../references/spec.md。脚本只做机械活：读陆地点阵位图、把「几点几分让哪座城市转到正面」换算成
旋转补间、算飞线大圆弧的高度、在生成期按同一套投影复算每个关键时刻（落点在不在正面、标签有没有出画 / 互叠），
再发射时间线。去哪、从哪飞到哪、什么时候飞、标签写什么由调用方决定。

画面：四层 canvas——底层（大气辉光、海洋圆盘，只随半径重画）、经纬网层、陆地层（点阵）、特效层（飞线、落点光圈、地点标签）。
时间线只补间一个状态对象（中心经纬度、缩放、自转、每条飞线的进度、每个光圈的相位、每个地点的不透明度），
时间线 onUpdate 按状态整帧重画——seek 到哪一帧就画哪一帧，不累加、不追加（gsap-emit「幂等回调」）。

陆地点阵：assets/land-mask.json，由 build_land_mask.py 从 Natural Earth 1:110m Land（公有领域）栅格化而来。
确定性：无随机、无时钟；同 spec 逐字节一致。
依赖：仅标准库。
"""
import argparse
import html
import io
import json
import math
import os
import re
import sys

GSAP_CDN = "https://lib.baomitu.com/gsap/3.13.0/gsap.min.js"
FONT_SANS = "'Source Han Sans CN','Source Han Sans SC','Noto Sans SC','思源黑体',sans-serif"
HERE = os.path.dirname(os.path.abspath(__file__))
MASK = os.path.join(HERE, "..", "assets", "land-mask.json")

ASCII_EM = [
    0.225, 0.343, 0.518, 0.57, 0.57, 0.939, 0.705, 0.298, 0.355, 0.355, 0.484, 0.57, 0.298, 0.357, 0.298, 0.39,
    0.57, 0.57, 0.57, 0.57, 0.57, 0.57, 0.57, 0.57, 0.57, 0.57, 0.298, 0.298, 0.57, 0.57, 0.57, 0.491,
    0.972, 0.622, 0.667, 0.646, 0.699, 0.6, 0.566, 0.701, 0.74, 0.309, 0.55, 0.663, 0.557, 0.83, 0.734, 0.754,
    0.647, 0.754, 0.655, 0.608, 0.61, 0.733, 0.594, 0.894, 0.596, 0.553, 0.607, 0.355, 0.39, 0.355, 0.57, 0.562,
    0.614, 0.575, 0.629, 0.517, 0.63, 0.565, 0.345, 0.578, 0.621, 0.287, 0.288, 0.575, 0.298, 0.942, 0.624, 0.614,
    0.63, 0.63, 0.409, 0.48, 0.396, 0.62, 0.545, 0.828, 0.526, 0.544, 0.49, 0.355, 0.281, 0.355, 0.57,
]
EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF\U0001F1E6-\U0001F1FF✀-➿️‍]")

PRESETS = {
    "dark": {
        "canvas": "#05070C", "ocean": "#0A1322", "ocean_hi": "#16294A", "atmosphere": "rgba(70,140,255,0.32)",
        "land": "#8DB6FF", "graticule": "rgba(140,180,255,0.10)", "arc": "#FFC24D", "head": "#FFF4D6",
        "ring": "#FFC24D", "marker": "#FFFFFF", "ink": "#EEF2F8", "ink_sub": "#8B95A7", "chip": "rgba(8,12,20,0.80)",
    },
    "light": {
        "canvas": "#EDF0F4", "ocean": "#D5DEEA", "ocean_hi": "#EEF3FA", "atmosphere": "rgba(90,120,170,0.20)",
        "land": "#4E5F78", "graticule": "rgba(40,60,90,0.10)", "arc": "#E0572A", "head": "#FFFFFF",
        "ring": "#E0572A", "marker": "#1B2433", "ink": "#1B2433", "ink_sub": "#5C6778", "chip": "rgba(255,255,255,0.90)",
    },
}
PRESETS["transparent"] = dict(PRESETS["dark"], canvas=None)

LABEL_FS = 26


def die(msg):
    sys.stderr.write("globe-arcs: " + msg + "\n")
    sys.exit(2)


def num(v, nd=2):
    s = ("%." + str(nd) + "f") % float(v)
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def esc(s):
    return html.escape(str(s), quote=True)


def text_px(s, fs):
    w = 0.0
    for ch in s:
        o = ord(ch)
        w += ASCII_EM[o - 0x20] if 0x20 <= o <= 0x7E else (0.62 if o < 0x2E80 else 1.0)
    return w * fs * 1.04


def ease_inout_cubic(p):
    """GSAP power2.inOut（= cubic in-out），生成期复算视角用，必须与颗粒里的缓动一致。"""
    p = min(1.0, max(0.0, p))
    return 4 * p ** 3 if p < 0.5 else 1 - (-2 * p + 2) ** 3 / 2


def vec(lat, lon):
    la, lo = math.radians(lat), math.radians(lon)
    return (math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la))


def project(v, lon0, lat0):
    """正射投影：返回 (x, y, z)，x 向右、y 向上、z 朝观众（z > 0 在正面）。与颗粒 JS 同一公式。"""
    ex, ey, ez = v
    l0, b0 = math.radians(lon0), math.radians(lat0)
    x = ey * math.cos(l0) - ex * math.sin(l0)
    c = ex * math.cos(l0) + ey * math.sin(l0)
    y = math.cos(b0) * ez - math.sin(b0) * c
    z = math.sin(b0) * ez + math.cos(b0) * c
    return x, y, z


def build(spec, spec_dir, max_chars):
    cid = spec.get("id") or die("spec.id 必填（= data-composition-id）")
    W = int(spec.get("width", 1920))
    H = int(spec.get("height", 1080))
    D = float(spec.get("duration") or 0) or die("spec.duration 必填（秒，≥ 坑位包络 + 0.3）")
    safe_bottom = float(spec.get("safe_bottom", 0.85)) * H

    bg = spec.get("bg", "dark")
    if bg in PRESETS:
        P = dict(PRESETS[bg])
    elif isinstance(bg, str) and bg.startswith("#"):
        P = dict(PRESETS["dark"])
        P["canvas"] = bg
    else:
        die("bg 只收 dark / light / transparent / #RRGGBB")
    for k, v in (spec.get("palette") or {}).items():
        if k not in P:
            die("palette 不认识的键：%s（可用：%s）" % (k, " / ".join(sorted(P))))
        P[k] = v
    font = (spec.get("fonts") or {}).get("sans", FONT_SANS)

    with io.open(MASK, "r", encoding="utf-8") as f:
        mask = json.load(f)

    gl = spec.get("globe") or {}
    gx = float(gl.get("x", W * 0.5))
    gy = float(gl.get("y", H * 0.47))
    R0 = float(gl.get("radius", min(W, H) * 0.36))
    dot = float(gl.get("dot", max(1.2, R0 * 0.0042)))
    graticule = bool(gl.get("graticule", True))
    spin = float(spec.get("spin", 3.0))

    # —— 地点 ——
    places = []
    pid = {}
    for k, p in enumerate(spec.get("places") or []):
        if not p.get("id"):
            die("places[%d] 要有 id" % k)
        if p["id"] in pid:
            die("places 里 id 重复：%s" % p["id"])
        lat, lon = float(p.get("lat", 999)), float(p.get("lon", 999))
        if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            die("places[%d]（%s）经纬度越界：lat %s lon %s（纬度 −90..90，经度 −180..180，东经为正）" % (k, p["id"], num(lat), num(lon)))
        name = p.get("name", "")
        if name and EMOJI_RE.search(name):
            die("places[%d].name 含 emoji（渲染端字体表没有）" % k)
        side = p.get("label", "right")
        if side not in ("right", "left", "above", "below", "none"):
            die("places[%d].label 只收 right / left / above / below / none" % k)
        pid[p["id"]] = len(places)
        places.append({"id": p["id"], "name": name, "lat": lat, "lon": lon, "side": side,
                       "t": float(p["t"]) if p.get("t") is not None else None, "v": vec(lat, lon)})

    # —— 视角关键帧：spec 里写「t+dur 时刻哪里在正中」，脚本扣掉自转换算成补间目标，并走最短的经度方向 ——
    v0 = spec.get("view") or {}
    lon_s, lat_s, zoom_s = float(v0.get("lon", 0)), float(v0.get("lat", 20)), float(v0.get("zoom", 1))
    keys = []  # (t0, t1, lon_from, lon_to, lat_from, lat_to, z_from, z_to)
    cur = (lon_s, lat_s, zoom_s)
    last_end = 0.0
    for k, vw in enumerate(sorted(spec.get("views") or [], key=lambda x: float(x["t"]))):
        t = float(vw["t"])
        dur = float(vw.get("dur", 1.6))
        if t < last_end - 1e-6:
            die("views[%d] 在 %ss 开始，上一段视角 %ss 才转完——视角转动不能重叠" % (k, num(t), num(last_end)))
        if t + dur > D:
            die("views[%d] 转到 %ss 才结束，超出颗粒时长" % (k, num(t + dur)))
        if "at" in vw:
            if vw["at"] not in pid:
                die("views[%d].at=%s 不在 places 里" % (k, vw["at"]))
            pl = places[pid[vw["at"]]]
            want_lon, want_lat = pl["lon"], pl["lat"]
        else:
            want_lon, want_lat = float(vw.get("lon", cur[0] + spin * (t + dur))), float(vw.get("lat", cur[1]))
        want_lat = max(-80.0, min(80.0, want_lat + float(vw.get("lat_offset", 0))))
        target = want_lon - spin * (t + dur)
        target += 360.0 * round((cur[0] - target) / 360.0)  # 最短路径
        z = float(vw.get("zoom", cur[2]))
        keys.append((t, t + dur, cur[0], target, cur[1], want_lat, cur[2], z))
        cur = (target, want_lat, z)
        last_end = t + dur

    def view_at(t):
        lon, lat, z = lon_s, lat_s, zoom_s
        for (t0, t1, a0, a1, b0, b1, z0, z1) in keys:
            if t >= t1:
                lon, lat, z = a1, b1, z1
            elif t > t0:
                e = ease_inout_cubic((t - t0) / (t1 - t0))
                lon, lat, z = a0 + (a1 - a0) * e, b0 + (b1 - b0) * e, z0 + (z1 - z0) * e
                break
            else:
                break
        return lon + spin * t, lat, z

    def screen(v, t, r=1.0):
        lon0, lat0, z = view_at(t)
        x, y, zz = project(v, lon0, lat0)
        Rz = R0 * z
        return gx + Rz * r * x, gy - Rz * r * y, zz

    # —— 飞线 ——
    arcs = []
    for k, a in enumerate(spec.get("arcs") or []):
        def endpoint(ref, what):
            if isinstance(ref, str):
                if ref not in pid:
                    die("arcs[%d].%s=%s 不在 places 里" % (k, what, ref))
                return pid[ref]
            die("arcs[%d].%s 要写 places 里的 id" % (k, what))
        ia, ib = endpoint(a.get("from"), "from"), endpoint(a.get("to"), "to")
        A, B = places[ia]["v"], places[ib]["v"]
        dotp = max(-1.0, min(1.0, sum(p * q for p, q in zip(A, B))))
        ang = math.acos(dotp)
        if ang < math.radians(0.8):
            die("arcs[%d] 起点终点几乎重合" % k)
        if ang > math.radians(178):
            die("arcs[%d] 起点终点几乎对跖，大圆路径不唯一——中间加一个中转地点拆成两段" % k)
        t = float(a.get("t", -1))
        dur = float(a.get("dur", 1.4))
        if t < 0:
            die("arcs[%d] 要有 t" % k)
        style = a.get("style", "line")
        if style not in ("line", "trail"):
            die("arcs[%d].style 只收 line / trail" % k)
        height = float(a.get("height", 0.06 + 0.30 * ang / math.pi))
        n_rings = int(a.get("rings", 2)) if a.get("ring", True) else 0
        t_arr = t + dur
        end = t_arr + (dur * 0.25 if style == "trail" else 0.0) + n_rings * 0.9
        if end > D - 0.3:
            die("arcs[%d] 连同落点光圈到 %ss 才结束，离颗粒末尾不到 0.3s——没有驻留" % (k, num(end)))
        # 起飞与落地时两端都要在正面
        for (idx, tt, what) in ((ia, t, "起点"), (ib, t_arr, "落点")):
            _, _, zz = screen(places[idx]["v"], tt)
            if zz < 0.25:
                die("arcs[%d] 的%s %s 在 %ss 转到了地球%s（z=%s）——加一个 views 把它转到正面，或改时刻"
                    % (k, what, places[idx]["id"], num(tt), "背面" if zz < 0 else "边缘", num(zz)))
        arcs.append({"k": k, "a": ia, "b": ib, "t": t, "dur": dur, "t_arr": t_arr, "h": height, "style": style,
                     "rings": n_rings, "color": a.get("color", P["arc"]), "ang": ang})

    # 地点出现时刻：没写就取「第一次用到它」——作起点在起飞前 0.3s 出现，作终点在落地时出现
    for k, pl in enumerate(places):
        if pl["t"] is None:
            uses = [a["t"] - 0.3 for a in arcs if a["a"] == k] + [a["t_arr"] for a in arcs if a["b"] == k]
            pl["t"] = max(0.0, min(uses)) if uses else 0.0

    # 标签几何复算：出现时与终态，不出画、不进字幕带、正面的标签互不相交
    def label_box(pl, t):
        x, y, zz = screen(pl["v"], t)
        if pl["side"] == "none" or not pl["name"]:
            return None, zz
        w = text_px(pl["name"], LABEL_FS) + 24
        h = LABEL_FS * 1.3 + 12
        if pl["side"] == "right":
            bx, by = x + 14, y - h / 2
        elif pl["side"] == "left":
            bx, by = x - 14 - w, y - h / 2
        elif pl["side"] == "above":
            bx, by = x - w / 2, y - 14 - h
        else:
            bx, by = x - w / 2, y + 14
        return (bx, by, bx + w, by + h), zz

    ttl = spec.get("title") or {}
    title_box = None
    if ttl.get("title") or ttl.get("sub"):
        tx, ty = float(ttl.get("x", 96)), float(ttl.get("y", 72))
        title_box = (tx, ty, tx + max(text_px(ttl.get("title", ""), 46), text_px(ttl.get("sub", ""), 24)), ty + 94)
    check_times = sorted(set([pl["t"] + 0.3 for pl in places] + [D - 0.05]))
    for tt in check_times:
        boxes = [("标题", title_box)] if title_box else []
        for pl in places:
            if pl["t"] > tt + 1e-6:
                continue
            box, zz = label_box(pl, tt)
            if box is None or zz < 0.12:
                continue
            if box[0] < 0 or box[2] > W or box[1] < 0 or box[3] > safe_bottom:
                die("地点 %s 的标签在 %ss 出画或落进字幕带（%s）——换 label 方向或调视角" % (pl["id"], num(tt), ", ".join(num(b) for b in box)))
            for (oid, ob) in boxes:
                if box[0] < ob[2] and ob[0] < box[2] and box[1] < ob[3] and ob[1] < box[3]:
                    die("地点 %s 的标签在 %ss 压到了%s——换 label 方向（left / above / below）、挪标题或调视角"
                        % (pl["id"], num(tt), "标题" if oid == "标题" else ("地点 %s 的标签" % oid)))
            boxes.append((pl["id"], box))

    # —— 标题 ——
    for key in ("title", "sub"):
        if ttl.get(key) and EMOJI_RE.search(ttl[key]):
            die("title.%s 含 emoji" % key)

    # ———————————————————————— HTML ————————————————————————
    R = '[data-composition-id="%s"]' % cid
    css = ["%s canvas{position:absolute;left:0;top:0;width:%dpx;height:%dpx;}" % (R, W, H)]
    title_html = ""
    if ttl.get("title") or ttl.get("sub"):
        tx, ty = float(ttl.get("x", 96)), float(ttl.get("y", 72))
        title_html = ('<div class="tt" style="position:absolute;left:%spx;top:%spx;white-space:pre;">'
                      '<div style="font-size:46px;line-height:60px;font-weight:700;color:%s;">%s</div>'
                      '<div style="font-size:24px;line-height:34px;color:%s;">%s</div></div>'
                      % (num(tx), num(ty), P["ink"], esc(ttl.get("title", "")), P["ink_sub"], esc(ttl.get("sub", ""))))

    def_places = [{"id": p["id"], "name": p["name"], "lat": p["lat"], "lon": p["lon"], "side": p["side"]} for p in places]
    def_arcs = [{"a": a["a"], "b": a["b"], "h": round(a["h"], 4), "style": a["style"], "color": a["color"], "rings": a["rings"],
                 "t": round(a["t"], 4), "t_arr": round(a["t_arr"], 4)} for a in arcs]

    js = []
    js.append("var ID=%s;" % json.dumps(cid, ensure_ascii=False))
    js.append("var ROOT='[data-composition-id=\"'+ID+'\"]';")
    js.append("var q=function(s){return document.querySelector(ROOT+' '+s);};")
    js.append("var W=%d,H=%d,GX=%s,GY=%s,R0=%s,DOT=%s,GRAT=%s,SAFE=%s;" % (W, H, num(gx), num(gy), num(R0), num(dot, 3), "true" if graticule else "false", num(safe_bottom)))
    js.append("var C=%s;" % json.dumps({k: P[k] for k in ("ocean", "ocean_hi", "atmosphere", "land", "graticule", "head", "ring", "marker", "ink", "chip")}))
    js.append("var FONT=%s;" % json.dumps("600 %dpx %s" % (LABEL_FS, font)))
    js.append("var PL=%s, AR=%s;" % (json.dumps(def_places, ensure_ascii=False), json.dumps(def_arcs)))
    js.append("var MASK={step:%s,bits:%s};" % (num(mask["step_deg"], 4), json.dumps(mask["bits"])))
    js.append(GLOBE_JS)
    # 状态与时间线
    js.append("var S={lon:%s,lat:%s,zoom:%s,spin:0};" % (num(lon_s, 4), num(lat_s, 4), num(zoom_s, 4)))
    js.append("var AU=AR.map(function(){return {u:0};}), RP=AR.map(function(){return {p:0};}), PA=PL.map(function(){return {a:0};});")
    js.append("var TT=q('.tt');")
    if title_html:
        js.append("gsap.set(TT,{opacity:0,x:-30});")
    js.append("var tl=gsap.timeline({paused:true,onUpdate:draw});")
    if spin:
        js.append("tl.to(S,{spin:%s,duration:%s,ease:'none'},0);" % (num(spin * D, 4), num(D, 4)))
    for (t0, t1, a0, a1, b0, b1, z0, z1) in keys:
        js.append("tl.to(S,{lon:%s,lat:%s,zoom:%s,duration:%s,ease:'power2.inOut'},%s);" % (num(a1, 4), num(b1, 4), num(z1, 4), num(t1 - t0, 4), num(t0, 4)))
    if title_html:
        js.append("tl.to(TT,{opacity:1,x:0,duration:0.7,ease:'power3.out'},0.2);")
    for k, pl in enumerate(places):
        js.append("tl.to(PA[%d],{a:1,duration:0.4,ease:'none'},%s);" % (k, num(pl["t"], 4)))
    for a in arcs:
        if a["style"] == "line":
            js.append("tl.to(AU[%d],{u:1,duration:%s,ease:'power1.inOut'},%s);" % (a["k"], num(a["dur"], 4), num(a["t"], 4)))
        else:
            js.append("tl.to(AU[%d],{u:1.25,duration:%s,ease:'none'},%s);" % (a["k"], num(a["dur"] * 1.25, 4), num(a["t"], 4)))
        if a["rings"]:
            js.append("tl.to(RP[%d],{p:%d,duration:%s,ease:'none'},%s);" % (a["k"], a["rings"], num(a["rings"] * 0.9, 4), num(a["t_arr"], 4)))
    js.append("tl.to({},{duration:0.05},%s);" % num(D, 4))
    js.append("window.__timelines=window.__timelines||{};")
    js.append("window.__timelines[ID]=tl;")
    js.append("draw();")

    out = []
    out.append('<template id="p">')
    out.append('<div data-composition-id="%s" data-width="%d" data-height="%d" style="position:absolute;inset:0;overflow:hidden;background:transparent;font-family:%s;">'
               % (esc(cid), W, H, font))
    if P["canvas"]:
        out.append('<div class="bgfill" style="position:absolute;inset:0;background:%s;"></div>' % P["canvas"])
    out.append("<style>%s</style>" % "".join(css))
    out.append('<canvas class="ga" width="%d" height="%d"></canvas><canvas class="gb" width="%d" height="%d"></canvas>'
               '<canvas class="gl" width="%d" height="%d"></canvas><canvas class="gf" width="%d" height="%d"></canvas>'
               % (W, H, W, H, W, H, W, H))
    out.append(title_html)
    out.append('<script src="%s"></script>' % GSAP_CDN)
    out.append("<script>(function(){\n%s\n})();</script>" % "\n".join(js))
    out.append("</div>")
    out.append("</template>")
    out_html = "\n".join(out)
    if len(out_html) > max_chars:
        die("产物 %d 字符超过预算 %d" % (len(out_html), max_chars))

    summary = {
        "id": cid, "chars": len(out_html), "duration": D,
        "globe": {"x": gx, "y": gy, "radius": R0, "dot": round(dot, 3), "land_points": mask["land_points"]},
        "spin": spin,
        "views": [{"t0": round(k[0], 3), "t1": round(k[1], 3), "center_at_t1": [round(k[5], 3), round((k[3] + spin * k[1] + 180) % 360 - 180, 3)],
                   "zoom": k[7]} for k in keys],
        "places": [{"id": p["id"], "t": round(p["t"], 3), "z_at_end": round(screen(p["v"], D - 0.05)[2], 3)} for p in places],
        "arcs": [{"k": a["k"], "from": places[a["a"]]["id"], "to": places[a["b"]]["id"], "t": a["t"], "arrive": round(a["t_arr"], 3),
                  "deg": round(math.degrees(a["ang"]), 2), "height": round(a["h"], 3),
                  "z_from": round(screen(places[a["a"]]["v"], a["t"])[2], 3), "z_to": round(screen(places[a["b"]]["v"], a["t_arr"])[2], 3)} for a in arcs],
    }
    return out_html, summary


# 颗粒里的绘制代码。draw() 只读状态 S / AU / RP / PA，整帧重画——seek 到哪画到哪。
GLOBE_JS = r"""
var ga=q('.ga'), gb=q('.gb'), glc=q('.gl'), gf=q('.gf');
var ca=ga.getContext('2d'), cb=gb.getContext('2d'), cl=glc.getContext('2d'), cf=gf.getContext('2d');
var lastR=-1;
var D2R=Math.PI/180;
// 陆地点阵：按 build_land_mask.py 的布局规则还原单位向量
var LX=[],LY=[],LZ=[];
(function(){
  var raw=atob(MASK.bits), K=Math.round(180/MASK.step), i=0;
  for(var k=0;k<K;k++){
    var lat=-90+MASK.step*(k+0.5), n=Math.max(1,Math.round(360*Math.cos(lat*D2R)/MASK.step));
    for(var j=0;j<n;j++){
      if((raw.charCodeAt(i>>3)>>(7-(i&7)))&1){
        var lon=-180+360*(j+0.5)/n, cla=Math.cos(lat*D2R);
        LX.push(cla*Math.cos(lon*D2R)); LY.push(cla*Math.sin(lon*D2R)); LZ.push(Math.sin(lat*D2R));
      }
      i++;
    }
  }
})();
function V(lat,lon){var a=lat*D2R,o=lon*D2R;return [Math.cos(a)*Math.cos(o),Math.cos(a)*Math.sin(o),Math.sin(a)];}
PL.forEach(function(p){p.v=V(p.lat,p.lon);});
// 大圆弧采样点（单位球上），抬高按 sin(pi*s)
AR.forEach(function(a){
  var A=PL[a.a].v,B=PL[a.b].v,d=Math.acos(Math.max(-1,Math.min(1,A[0]*B[0]+A[1]*B[1]+A[2]*B[2]))),s=Math.sin(d),N=72;
  a.pts=[];
  for(var k=0;k<=N;k++){
    var f=k/N, w1=Math.sin((1-f)*d)/s, w2=Math.sin(f*d)/s, r=1+a.h*Math.sin(Math.PI*f);
    a.pts.push([(w1*A[0]+w2*B[0])*r,(w1*A[1]+w2*B[1])*r,(w1*A[2]+w2*B[2])*r]);
  }
});
function rot(){var l=(S.lon+S.spin)*D2R,b=S.lat*D2R;return {cl:Math.cos(l),sl:Math.sin(l),cb:Math.cos(b),sb:Math.sin(b),R:R0*S.zoom};}
function P3(v,o){var x=v[1]*o.cl-v[0]*o.sl, c=v[0]*o.cl+v[1]*o.sl; return [x, o.cb*v[2]-o.sb*c, o.sb*v[2]+o.cb*c];}
function hidden(p){return p[2]<0 && (p[0]*p[0]+p[1]*p[1])<1;}
function draw(){
  var o=rot(), Rz=o.R, dbg={S:{lon:S.lon+S.spin,lat:S.lat,zoom:S.zoom},R:Rz,cx:GX,cy:GY,marks:[],heads:[],labels:[]};
  // 底层：大气辉光 + 海洋圆盘。只随半径变——半径没变就不重画（软件光栅化下大面积径向渐变很贵）；
  // 缓存键是状态本身（Rz），seek 到哪一帧都画出同一张，仍是幂等的
  if(Math.abs(Rz-lastR)>1e-6){
    ca.clearRect(0,0,W,H);
    var g=ca.createRadialGradient(GX,GY,Rz*0.98,GX,GY,Rz*1.2); g.addColorStop(0,C.atmosphere); g.addColorStop(1,'rgba(0,0,0,0)');
    ca.fillStyle=g; ca.beginPath(); ca.arc(GX,GY,Rz*1.2,0,Math.PI*2); ca.fill();
    var g2=ca.createRadialGradient(GX-Rz*0.35,GY-Rz*0.4,Rz*0.05,GX,GY,Rz); g2.addColorStop(0,C.ocean_hi); g2.addColorStop(1,C.ocean);
    ca.fillStyle=g2; ca.beginPath(); ca.arc(GX,GY,Rz,0,Math.PI*2); ca.fill();
    lastR=Rz;
  }
  // 经纬网（随旋转变，每帧画）
  cb.clearRect(0,0,W,H);
  if(GRAT){
    cb.strokeStyle=C.graticule; cb.lineWidth=1; cb.beginPath();
    for(var lo=-180;lo<180;lo+=30){var up=false;for(var la=-90;la<=90;la+=3){var p=P3(V(la,lo),o);if(p[2]>0){var X=GX+Rz*p[0],Y=GY-Rz*p[1];if(up)cb.lineTo(X,Y);else cb.moveTo(X,Y);up=true;}else up=false;}}
    for(var la2=-60;la2<=60;la2+=30){var up2=false;for(var lo2=-180;lo2<=180;lo2+=3){var p2=P3(V(la2,lo2),o);if(p2[2]>0){var X2=GX+Rz*p2[0],Y2=GY-Rz*p2[1];if(up2)cb.lineTo(X2,Y2);else cb.moveTo(X2,Y2);up2=true;}else up2=false;}}
    cb.stroke();
  }
  // 陆地层：点阵（按 z 分 5 档明暗，边缘暗、正面亮）
  cl.clearRect(0,0,W,H); cl.fillStyle=C.land;
  var bins=[[],[],[],[],[]];
  for(var i=0;i<LX.length;i++){
    var x=LY[i]*o.cl-LX[i]*o.sl, c=LX[i]*o.cl+LY[i]*o.sl, z=o.sb*LZ[i]+o.cb*c;
    if(z<=0.02) continue;
    var y=o.cb*LZ[i]-o.sb*c;
    bins[Math.min(4,Math.floor(z*5))].push(GX+Rz*x, GY-Rz*y);
  }
  // 点画成等面积的小方块而不是圆：几千个 arc 在软件光栅化下是整颗颗粒最贵的一步（引擎实测 3 秒片段 68s → 28s），
  // 半径 1–2px 的圆与方块肉眼分不出
  var hs=DOT*Math.sqrt(S.zoom)*0.886;
  for(var b=0;b<5;b++){
    cl.globalAlpha=0.28+0.72*(b+0.5)/5; cl.beginPath(); var arr=bins[b];
    for(var m=0;m<arr.length;m+=2){cl.rect(arr[m]-hs,arr[m+1]-hs,2*hs,2*hs);}
    cl.fill();
  }
  cl.globalAlpha=1;
  // 特效层：飞线 → 光圈 → 地点
  cf.clearRect(0,0,W,H); cf.lineCap='round'; cf.lineJoin='round';
  AR.forEach(function(a,k){
    var u=AU[k].u; if(u<=0) return;
    var N=a.pts.length-1, scr=a.pts.map(function(v){var p=P3(v,o);return [GX+Rz*p[0],GY-Rz*p[1],hidden(p)];});
    function seg(f0,f1,alpha,lw){
      f0=Math.max(0,f0); f1=Math.min(1,f1); if(f1<=f0) return;
      cf.globalAlpha=alpha; cf.lineWidth=lw; cf.strokeStyle=a.color; cf.beginPath();
      var i0=Math.floor(f0*N), i1=Math.ceil(f1*N), up=false;
      for(var j=i0;j<=i1;j++){var s=scr[j]; if(s[2]){up=false;continue;} if(up) cf.lineTo(s[0],s[1]); else cf.moveTo(s[0],s[1]); up=true;}
      cf.stroke();
    }
    if(a.style==='line'){ seg(0,u,0.7,2.6); seg(u-0.12,u,1,3.4); }
    else { seg(u-0.25,u,1,3.4); }
    if(u<1){
      var f=u*N, j=Math.min(N-1,Math.floor(f)), r=f-j, h=[scr[j][0]+(scr[j+1][0]-scr[j][0])*r, scr[j][1]+(scr[j+1][1]-scr[j][1])*r];
      if(!scr[j][2]){
        var hg=cf.createRadialGradient(h[0],h[1],0,h[0],h[1],12); hg.addColorStop(0,C.head); hg.addColorStop(1,'rgba(255,255,255,0)');
        cf.globalAlpha=1; cf.fillStyle=hg; cf.beginPath(); cf.arc(h[0],h[1],12,0,Math.PI*2); cf.fill();
      }
      dbg.heads.push({k:k,u:u,x:h[0],y:h[1]});
    } else {
      var e=scr[N]; dbg.heads.push({k:k,u:u,x:e[0],y:e[1]});
    }
  });
  // 落点光圈：地表上的小圆，按投影画成透视椭圆
  AR.forEach(function(a,k){
    var p=RP[k].p; if(p<=0||p>=a.rings) return;
    var ph=p-Math.floor(p), B=PL[a.b].v, rho=(0.6+3.4*ph)*D2R;
    var e1=[-B[1],B[0],0], n1=Math.sqrt(e1[0]*e1[0]+e1[1]*e1[1])||1; e1=[e1[0]/n1,e1[1]/n1,0];
    var e2=[B[1]*e1[2]-B[2]*e1[1], B[2]*e1[0]-B[0]*e1[2], B[0]*e1[1]-B[1]*e1[0]];
    cf.globalAlpha=1-ph; cf.strokeStyle=C.ring; cf.lineWidth=2.4; cf.beginPath(); var up=false;
    for(var s=0;s<=48;s++){
      var th=s/48*Math.PI*2, cr=Math.cos(rho), sr=Math.sin(rho);
      var v=[cr*B[0]+sr*(Math.cos(th)*e1[0]+Math.sin(th)*e2[0]), cr*B[1]+sr*(Math.cos(th)*e1[1]+Math.sin(th)*e2[1]), cr*B[2]+sr*(Math.cos(th)*e1[2]+Math.sin(th)*e2[2])];
      var pp=P3(v,o); if(pp[2]<=0){up=false;continue;}
      var X=GX+Rz*pp[0],Y=GY-Rz*pp[1]; if(up) cf.lineTo(X,Y); else cf.moveTo(X,Y); up=true;
    }
    cf.stroke();
  });
  cf.font=FONT; cf.textBaseline='middle';
  PL.forEach(function(pl,k){
    var a=PA[k].a, p=P3(pl.v,o), X=GX+Rz*p[0], Y=GY-Rz*p[1];
    var vis=Math.max(0,Math.min(1,(p[2]-0.05)/0.2))*a;
    dbg.marks.push({id:pl.id,x:X,y:Y,z:p[2],a:vis});
    if(vis<=0.001) return;
    cf.globalAlpha=vis; cf.fillStyle=C.marker; cf.strokeStyle=C.ring; cf.lineWidth=2.5;
    cf.beginPath(); cf.arc(X,Y,5.5,0,Math.PI*2); cf.fill(); cf.stroke();
    if(pl.side==='none'||!pl.name) return;
    var w=cf.measureText(pl.name).width+24, h=%LH%, bx, by;
    if(pl.side==='right'){bx=X+14;by=Y-h/2;} else if(pl.side==='left'){bx=X-14-w;by=Y-h/2;}
    else if(pl.side==='above'){bx=X-w/2;by=Y-14-h;} else {bx=X-w/2;by=Y+14;}
    cf.fillStyle=C.chip; cf.beginPath(); if(cf.roundRect) cf.roundRect(bx,by,w,h,8); else cf.rect(bx,by,w,h); cf.fill();
    cf.fillStyle=C.ink; cf.fillText(pl.name,bx+12,by+h/2+1);
    dbg.labels.push({id:pl.id,x:bx,y:by,w:w,h:h,a:vis});
  });
  cf.globalAlpha=1;
  gf.__dbg=dbg;
}
gf.__def={places:PL.map(function(p){return {id:p.id,lat:p.lat,lon:p.lon};}), arcs:AR.map(function(a){return {a:a.a,b:a.b,t:a.t,t_arr:a.t_arr,rings:a.rings};}),
  GX:GX, GY:GY, R0:R0, W:W, H:H, SAFE:SAFE, step:MASK.step};
""".replace("%LH%", str(round(LABEL_FS * 1.3 + 12)))


def main():
    ap = argparse.ArgumentParser(description="globe-arcs 颗粒生成器")
    ap.add_argument("spec")
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-chars", type=int, default=1900000)
    a = ap.parse_args()
    for st in (sys.stdout, sys.stderr):
        if hasattr(st, "reconfigure"):
            st.reconfigure(encoding="utf-8")
    with io.open(a.spec, "r", encoding="utf-8") as f:
        spec = json.load(f)
    spec_dir = os.path.dirname(os.path.abspath(a.spec))
    out_html, summary = build(spec, spec_dir, a.max_chars)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
    with io.open(a.out, "w", encoding="utf-8", newline="\n") as f:
        f.write(out_html)
    summary["out"] = os.path.abspath(a.out)
    sys.stdout.write(json.dumps(summary, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
