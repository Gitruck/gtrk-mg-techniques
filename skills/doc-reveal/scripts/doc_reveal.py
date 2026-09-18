#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""doc-reveal · 文献 / 文档页「叠放 · 发牌 · 局部强调」颗粒生成器（gsap-emit v1 合规）

用法：
    python doc_reveal.py <spec.json> --out <particle.html>

spec 字段见 ../references/spec.md。所有随机感一律由确定性公式产生（无 Math.random / Date）。
产物：单文件 html-particle（<template> 包裹、GSAP paused 时间线注册 window.__timelines[id]、
图片以 data URI 内嵌、总字符数受 --max-chars 预算约束）。

依赖：Pillow（图片重编码）。
"""
import argparse
import base64
import html
import io
import json
import math
import os
import sys

from PIL import Image

GSAP_CDN = "https://lib.baomitu.com/gsap/3.13.0/gsap.min.js"

FONT_SANS = "'Source Han Sans CN','Source Han Sans SC','Noto Sans SC','思源黑体',sans-serif"
FONT_SERIF = "'Source Han Serif CN','Source Han Serif SC','Noto Serif SC','思源宋体',serif"
FONT_LATIN_SERIF = "'Times New Roman','Georgia',serif"

# 底色预设：dark = 参考片那种黑底烟雾 + 尘点；paper = 奶白档案桌面；transparent = 叠加在底轨上。
PRESETS = {
    "dark": {
        "bg": "#0A0A0C",
        "ink": "#FFFFFF",
        "ink_sub": "#B8B8B8",
        "accent": "#E8A33D",
        "card": "rgba(12,12,16,0.74)",
        "rule": "rgba(255,255,255,0.20)",
        "page_bg": "#FFFFFF",
        "shadow": "0 28px 60px rgba(0,0,0,0.58), 0 2px 8px rgba(0,0,0,0.55)",
        "smoke": True,
        "dust": True,
        "vignette": "radial-gradient(ellipse at 50% 45%, rgba(0,0,0,0) 40%, rgba(0,0,0,0.62) 100%)",
    },
    "paper": {
        "bg": "#F4F3F1",
        "ink": "#121212",
        "ink_sub": "#4A4E57",
        "accent": "#BD2B35",
        "card": "rgba(244,243,241,0.88)",
        "rule": "rgba(18,18,18,0.18)",
        "page_bg": "#FFFFFF",
        "shadow": "0 34px 70px rgba(30,24,18,0.32), 0 6px 14px rgba(0,0,0,0.16), 0 0 0 1px rgba(0,0,0,0.05)",
        "smoke": False,
        "dust": False,
        "vignette": "radial-gradient(ellipse at 50% 50%, rgba(60,50,40,0) 52%, rgba(60,50,40,0.16) 100%)",
    },
    "transparent": {
        "bg": None,
        "ink": "#FFFFFF",
        "ink_sub": "#D8D8D8",
        "accent": "#E8A33D",
        "card": "rgba(12,12,16,0.62)",
        "rule": "rgba(255,255,255,0.22)",
        "page_bg": "#FFFFFF",
        "shadow": "0 28px 60px rgba(0,0,0,0.55), 0 2px 8px rgba(0,0,0,0.5)",
        "smoke": False,
        "dust": False,
        "vignette": None,
    },
}

DEFAULT_FAN = [-2.0, 5.0, -7.0, 9.0, -11.0, 13.0]


def die(msg):
    sys.stderr.write("doc-reveal: " + msg + "\n")
    sys.exit(2)


def num(v, nd=2):
    """确定性数字字面量（统一小数位，避免 1e-7 之类科学计数法进 JS）。"""
    s = ("%." + str(nd) + "f") % float(v)
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def esc(s):
    return html.escape(str(s), quote=True)


def cycles(span, cycle):
    """yoyo 循环的 repeat 次数：总长 (repeat+1)*cycle ≥ span，且不多绕整圈（铁律 7：有限次、按坑位算死）。"""
    return max(0, int(math.ceil(span / cycle)) - 1)


def encode_image(path, max_w, quality):
    im = Image.open(path)
    im = im.convert("RGB")
    ow, oh = im.size
    if im.width > max_w:
        im = im.resize((int(max_w), int(round(im.height * max_w / im.width))), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "WEBP", quality=int(quality), method=6)
    data = buf.getvalue()
    return "data:image/webp;base64," + base64.b64encode(data).decode("ascii"), ow, oh, len(data)


def build(spec, spec_dir, max_chars):
    cid = spec.get("id") or die("spec.id 必填（= data-composition-id）")
    W = int(spec.get("width", 1920))
    H = int(spec.get("height", 1080))
    D = float(spec.get("duration") or 0) or die("spec.duration 必填（秒，≥ 坑位包络 + 0.3）")

    bg = spec.get("bg", "dark")
    if bg in PRESETS:
        P = dict(PRESETS[bg])
    elif isinstance(bg, str) and bg.startswith("#"):
        P = dict(PRESETS["dark"])
        P["bg"] = bg
    else:
        die("bg 只收 dark / paper / transparent / #RRGGBB")
    for k, v in (spec.get("palette") or {}).items():
        P[k] = v
    fonts = spec.get("fonts") or {}
    f_sans = fonts.get("sans", FONT_SANS)
    f_serif = fonts.get("serif", FONT_SERIF)
    f_latin = fonts.get("latin_serif", FONT_LATIN_SERIF)

    pages = spec.get("pages") or die("pages 至少一页（第一页是顶页，会被推近）")
    if isinstance(pages[0], str):
        pages = [{"src": p} for p in pages]
    page_w = float(spec.get("page_width", 620))
    top_max = int(spec.get("top_max_px", 1700))
    back_max = int(spec.get("back_max_px", 900))
    quality = int(spec.get("quality", 82))

    stack = spec.get("stack") or {}
    sx = float(stack.get("x", W * 0.62))
    sy = float(stack.get("y", H * 0.52))
    fan = list(stack.get("fan", DEFAULT_FAN))
    off = stack.get("offset", [16, 10])
    while len(fan) < len(pages):
        fan.append(fan[-1] * -1 + (2 if fan[-1] < 0 else -2))

    ent = spec.get("entrance") or {}
    ent_mode = ent.get("mode", "deal")
    if ent_mode not in ("deal", "stack"):
        die("entrance.mode 只收 deal（发牌）/ stack（叠放）")
    ent_start = float(ent.get("start", 0.2))
    ent_gap = float(ent.get("gap", 0.32))
    ent_dur = float(ent.get("dur", 0.72))
    ent_from = ent.get("from", "bottom-right")

    focus = spec.get("focus") or []
    captions = spec.get("captions") or []
    dust_on = bool(spec.get("dust", P["dust"]))
    smoke_on = bool(spec.get("smoke", P["smoke"]))
    hold_drift = bool(spec.get("hold_drift", True))

    # —— 图片重编码（顶页高清，背页低清；总量受预算约束，超预算逐级降质）——
    imgs = []
    q = quality
    tm, bm = top_max, back_max
    for attempt in range(6):
        imgs = []
        for i, pg in enumerate(pages):
            src = pg["src"] if os.path.isabs(pg["src"]) else os.path.join(spec_dir, pg["src"])
            if not os.path.exists(src):
                die("找不到页面图片：" + src)
            uri, ow, oh, nbytes = encode_image(src, tm if i == 0 else bm, q)
            imgs.append({"uri": uri, "w": ow, "h": oh, "bytes": nbytes, "src": src})
        total = sum(len(x["uri"]) for x in imgs)
        if total < max_chars * 0.86:
            break
        q = max(40, q - 10)
        tm = int(tm * 0.85)
        bm = int(bm * 0.85)

    # —— 页面几何（cam 空间 = 画布像素，scale 1）——
    geo = []
    for i, im in enumerate(imgs):
        ph = page_w * im["h"] / im["w"]
        left = sx - page_w / 2 + float(off[0]) * i
        top = sy - ph / 2 + float(off[1]) * i
        geo.append({"left": left, "top": top, "w": page_w, "h": ph, "rot": float(fan[i]), "z": len(imgs) - i})
    g0 = geo[0]

    # —— 顶页上的标记与镜头目标 ——
    def rect_px(r):
        x0, y0, x1, y1 = [float(v) for v in r]
        return (x0 * g0["w"], y0 * g0["h"], (x1 - x0) * g0["w"], (y1 - y0) * g0["h"])

    cam_keys = []  # (t, dur, tx, ty, s, ease)
    marks = []     # html for marks on page 0
    mark_anims = []
    for k, f in enumerate(focus):
        t = float(f["t"])
        dur = float(f.get("dur", 1.1))
        zoom = float(f.get("zoom", 2.2))
        anchor = f.get("anchor", [0.36, 0.5])
        rx, ry, rw, rh = rect_px(f["rect"])
        # 目标点：矩形中心 → 屏幕锚点
        px = g0["left"] + rx + rw / 2
        py = g0["top"] + ry + rh / 2
        tx = float(anchor[0]) * W - zoom * px
        ty = float(anchor[1]) * H - zoom * py
        cam_keys.append((t, dur, tx, ty, zoom, f.get("ease", "flow")))
        mk = f.get("mark", "marker")
        if mk and mk != "none":
            pad_x = float(f.get("pad", [8, 4])[0])
            pad_y = float(f.get("pad", [8, 4])[1])
            mid = "mk%d" % k
            if mk == "marker":
                style = ("left:%spx;top:%spx;width:%spx;height:%spx;background:%s;opacity:0.34;"
                         "mix-blend-mode:multiply;transform-origin:0%% 50%%;border-radius:2px;"
                         % (num(rx - pad_x), num(ry - pad_y), num(rw + 2 * pad_x), num(rh + 2 * pad_y), P["accent"]))
            elif mk == "box":
                style = ("left:%spx;top:%spx;width:%spx;height:%spx;border:2px solid %s;box-sizing:border-box;border-radius:3px;"
                         % (num(rx - pad_x), num(ry - pad_y), num(rw + 2 * pad_x), num(rh + 2 * pad_y), P["accent"]))
            elif mk == "underline":
                style = ("left:%spx;top:%spx;width:%spx;height:3px;background:%s;transform-origin:0%% 50%%;"
                         % (num(rx - 2), num(ry + rh + 2), num(rw + 4), P["accent"]))
            else:
                die("focus.mark 只收 marker / box / underline / none")
            marks.append('<div class="mk %s" style="position:absolute;%s"></div>' % (mid, style))
            mat = float(f.get("mark_at", t + dur * 0.55))
            mdur = float(f.get("mark_dur", 0.55))
            mark_anims.append((mid, mk, mat, mdur, k))

    # —— 文案卡 ——
    cap_html = []
    cap_anims = []
    for k, c in enumerate(captions):
        variant = c.get("variant", "card")
        pos = c.get("pos", "left")
        cw = float(c.get("w", 700 if variant == "card" else 560))
        if "x" in c and "y" in c:
            cx, cy = float(c["x"]), float(c["y"])
        elif pos == "right":
            cx, cy = W - 96 - cw, float(c.get("y", 300))
        elif pos == "bottom":
            cx, cy = 96, float(c.get("y", 690))
        else:
            cx, cy = 96, float(c.get("y", 300))
        cid_k = "cap%d" % k
        inner = []
        if c.get("kicker"):
            inner.append('<div class="ck">%s</div>' % esc(c["kicker"]))
        if variant == "card":
            inner.append('<div class="cr"></div>')
        if c.get("title"):
            inner.append('<div class="ct">%s</div>' % esc(c["title"]))
        if c.get("en"):
            inner.append('<div class="ce">%s</div>' % esc(c["en"]))
        if c.get("meta"):
            inner.append('<div class="cm">%s</div>' % esc(c["meta"]))
        cap_html.append('<div class="cap %s %s" style="left:%spx;top:%spx;width:%spx;">%s</div>'
                        % (cid_k, "chip" if variant == "chip" else "card", num(cx), num(cy), num(cw), "".join(inner)))
        cap_anims.append((cid_k, float(c["t"]), c.get("from", "left" if pos != "right" else "right"), float(c.get("dur", 0.7)),
                          (float(c["until"]) if c.get("until") is not None else None)))

    # —— 尘点（dark 预设）：黄金角撒点，逐点相位不同的漂移 + 明暗呼吸，有限次循环按时长算死 ——
    dust_svg = ""
    dust_anims = []
    if dust_on:
        n_dust = int(spec.get("dust_count", 22))
        circles = []
        for i in range(n_dust):
            x = ((i * 0.6180339887) % 1.0) * W
            y = ((i * 0.3819660113 + 0.13) % 1.0) * H
            r = 1.4 + (i % 4) * 0.55
            op = 0.16 + ((i * 7) % 5) * 0.07
            circles.append('<circle cx="%s" cy="%s" r="%s" fill="%s" opacity="%s"/>' % (num(x), num(y), num(r), P["ink"], num(op)))
            ddur = 5.2 + (i % 4) * 0.9
            rep = cycles(D, ddur)
            dy = -(48 + (i % 5) * 16)
            dx = (12 if i % 2 == 0 else -12) + (i % 3) * 4
            fdur = 1.3 + (i % 3) * 0.45
            frep = cycles(D, fdur)
            dust_anims.append((i, dx, dy, ddur, rep, num(min(0.9, op + 0.35)), fdur, frep))
        dust_svg = ('<svg class="dust" viewBox="0 0 %d %d" preserveAspectRatio="none" '
                    'style="position:absolute;inset:0;width:100%%;height:100%%;pointer-events:none;">%s</svg>' % (W, H, "".join(circles)))

    # —— 时间线（全部字面量）——
    js = []
    js.append('var ID=%s;' % json.dumps(cid, ensure_ascii=False))
    js.append("var ROOT='[data-composition-id=\"'+ID+'\"]';")
    js.append("var q=function(s){return document.querySelector(ROOT+' '+s);};")
    js.append("var qa=function(s){return document.querySelectorAll(ROOT+' '+s);};")
    js.append("var EASE={flow:'power2.inOut',void:'power4.out',snap:'power4.in',breathe:'sine.inOut',linear:'none'};")
    js.append("var cam=q('.cam'), pgs=qa('.pg');")
    js.append("gsap.set(cam,{x:0,y:0,scale:1,transformOrigin:'0px 0px'});")
    # 初始态
    for i, g in enumerate(geo):
        if ent_mode == "deal":
            fx, fy = {"bottom-right": (1100, 820), "bottom-left": (-1100, 820), "top-right": (1100, -820), "top-left": (-1100, -820)}.get(ent_from, (1100, 820))
            js.append("gsap.set(pgs[%d],{x:%s,y:%s,rotation:%s,opacity:0,transformOrigin:'50%% 50%%'});" % (i, num(fx), num(fy), num(g["rot"] + (22 if fx > 0 else -22))))
        else:
            js.append("gsap.set(pgs[%d],{x:0,y:-260,scale:1.14,rotation:%s,opacity:0,transformOrigin:'50%% 50%%'});" % (i, num(g["rot"] - 9)))
    for mid, mk, mat, mdur, k in mark_anims:
        if mk in ("marker", "underline"):
            js.append("gsap.set(q('.%s'),{scaleX:0});" % mid)
        else:
            js.append("gsap.set(q('.%s'),{opacity:0,scale:1.08,transformOrigin:'50%% 50%%'});" % mid)
    for cid_k, t, frm, cdur, until in cap_anims:
        js.append("gsap.set(q('.%s'),{opacity:0,x:%s});" % (cid_k, num(-60 if frm == "left" else (60 if frm == "right" else 0)), ) if frm in ("left", "right") else "gsap.set(q('.%s'),{opacity:0,y:40});" % cid_k)
    js.append("var tl=gsap.timeline({paused:true});")
    # 入场
    n = len(geo)
    for i, g in enumerate(geo):
        t0 = ent_start + ent_gap * (n - 1 - i)  # 底页先落，顶页最后
        if ent_mode == "deal":
            js.append("tl.to(pgs[%d],{opacity:1,duration:0.22,ease:'none'},%s);" % (i, num(t0)))
            js.append("tl.to(pgs[%d],{x:0,y:0,rotation:%s,duration:%s,ease:EASE.flow},%s);" % (i, num(g["rot"]), num(ent_dur), num(t0)))
        else:
            js.append("tl.to(pgs[%d],{opacity:1,duration:0.2,ease:'none'},%s);" % (i, num(t0)))
            js.append("tl.to(pgs[%d],{y:0,scale:1,rotation:%s,duration:%s,ease:'power3.out'},%s);" % (i, num(g["rot"]), num(ent_dur), num(t0)))
    ent_end = ent_start + ent_gap * (n - 1) + ent_dur
    # 镜头推近（顶页同时转正）
    last_cam = None
    for (t, dur, tx, ty, s, ease) in cam_keys:
        e = {"flow": "EASE.flow", "void": "EASE.void", "breathe": "EASE.breathe"}.get(ease, "EASE.flow")
        js.append("tl.to(pgs[0],{rotation:0,duration:%s,ease:%s},%s);" % (num(dur), e, num(t)))
        js.append("tl.to(cam,{x:%s,y:%s,scale:%s,duration:%s,ease:%s},%s);" % (num(tx), num(ty), num(s, 4), num(dur), e, num(t)))
        last_cam = (t + dur, tx, ty, s)
    # 标记：新标记画入时，旧标记黯淡让位（不清空）
    prev_mid = None
    for mid, mk, mat, mdur, k in mark_anims:
        if mk in ("marker", "underline"):
            js.append("tl.to(q('.%s'),{scaleX:1,duration:%s,ease:'power2.out'},%s);" % (mid, num(mdur), num(mat)))
        else:
            js.append("tl.to(q('.%s'),{opacity:1,scale:1,duration:%s,ease:EASE.void},%s);" % (mid, num(mdur), num(mat)))
        if prev_mid:
            js.append("tl.to(q('.%s'),{opacity:0.4,duration:0.6,ease:'none'},%s);" % (prev_mid, num(mat)))
        prev_mid = mid
    # 文案卡（`until` = 过渡性标签的退场时刻；终态主体不要给 until）
    for cid_k, t, frm, cdur, until in cap_anims:
        if frm in ("left", "right"):
            js.append("tl.to(q('.%s'),{opacity:1,x:0,duration:%s,ease:EASE.void},%s);" % (cid_k, num(cdur), num(t)))
        else:
            js.append("tl.to(q('.%s'),{opacity:1,y:0,duration:%s,ease:EASE.void},%s);" % (cid_k, num(cdur), num(t)))
        if until is not None and until > t + cdur:
            ex = num(-40 if frm == "left" else (40 if frm == "right" else 0))
            js.append("tl.to(q('.%s'),{opacity:0,x:%s,duration:0.45,ease:EASE.flow},%s);" % (cid_k, ex, num(until)))
    # 烟雾层缓漂（有限次）
    if smoke_on:
        srep = cycles(D, 9.0)
        js.append("tl.to(q('.smoke'),{x:60,y:-34,duration:9,ease:'sine.inOut',yoyo:true,repeat:%d},0);" % srep)
    # 尘点
    if dust_anims:
        js.append("var dots=qa('.dust circle');")
        for (i, dx, dy, ddur, rep, ophi, fdur, frep) in dust_anims:
            js.append("tl.to(dots[%d],{x:%s,y:%s,duration:%s,ease:'sine.inOut',yoyo:true,repeat:%d},0);" % (i, num(dx), num(dy), num(ddur), rep))
            js.append("tl.to(dots[%d],{opacity:%s,duration:%s,ease:'sine.inOut',yoyo:true,repeat:%d},0);" % (i, ophi, num(fdur), frep))
    # 终态驻留：镜头微漂（绝对值，避开相对补间），最后一枚标记轻呼吸；画面不归零
    hold_from = last_cam[0] if last_cam else ent_end
    if hold_drift and hold_from < D:
        if last_cam:
            _, tx, ty, s = last_cam
            hx, hy = tx + 7, ty - 5
        else:
            hx, hy = 7, -5
        hd = 3.4
        hrep = cycles(D - hold_from, hd)
        js.append("tl.to(cam,{x:%s,y:%s,duration:%s,ease:'sine.inOut',yoyo:true,repeat:%d},%s);" % (num(hx), num(hy), num(hd), hrep, num(hold_from)))
    if prev_mid and mark_anims:
        mid, mk, mat, mdur, k = mark_anims[-1]
        m_from = mat + mdur + 0.2
        if m_from < D:
            mrep = cycles(D - m_from, 1.4)
            js.append("tl.to(q('.%s'),{opacity:%s,duration:1.4,ease:'sine.inOut',yoyo:true,repeat:%d},%s);"
                      % (mid, "0.22" if mk == "marker" else "0.72", mrep, num(m_from)))
    js.append("tl.to({},{duration:0.05},%s);" % num(D))
    js.append("window.__timelines=window.__timelines||{};")
    js.append("window.__timelines[ID]=tl;")

    # —— HTML ——
    R = '[data-composition-id="%s"]' % cid
    css = []
    css.append("%s .cam{position:absolute;left:0;top:0;width:%dpx;height:%dpx;}" % (R, W, H))
    css.append("%s .pg{position:absolute;background:%s;box-shadow:%s;overflow:visible;}" % (R, P["page_bg"], P["shadow"]))
    css.append("%s .pg img{display:block;width:100%%;height:100%%;}" % R)
    css.append("%s .mk{pointer-events:none;}" % R)
    css.append("%s .cap{position:absolute;box-sizing:border-box;}" % R)
    css.append("%s .cap.card{padding:28px 34px 30px;background:%s;border-radius:6px;}" % (R, P["card"]))
    css.append("%s .cap.chip{padding:12px 20px 12px 22px;background:%s;border-left:4px solid %s;border-radius:4px;}" % (R, P["card"], P["accent"]))
    css.append("%s .ck{font-family:%s;font-weight:500;font-size:22px;letter-spacing:0.18em;color:%s;margin-bottom:10px;}" % (R, f_sans, P["ink_sub"]))
    css.append("%s .cr{width:52px;height:3px;background:%s;margin:0 0 18px;}" % (R, P["accent"]))
    css.append("%s .ct{font-family:%s;font-weight:900;font-size:48px;line-height:1.28;letter-spacing:0.04em;color:%s;}" % (R, f_serif, P["ink"]))
    css.append("%s .chip .ct{font-family:%s;font-weight:700;font-size:30px;letter-spacing:0.04em;}" % (R, f_sans))
    css.append("%s .ce{font-family:%s;font-style:italic;font-size:24px;line-height:1.35;color:%s;margin-top:12px;}" % (R, f_latin, P["ink_sub"]))
    css.append("%s .cm{font-family:%s;font-weight:500;font-size:24px;line-height:1.4;letter-spacing:0.06em;color:%s;margin-top:14px;}" % (R, f_sans, P["ink_sub"]))
    css.append("%s .chip .cm{margin-top:4px;font-size:22px;}" % R)

    parts = []
    parts.append('<template id="p">')
    parts.append('<div data-composition-id="%s" data-width="%d" data-height="%d" style="position:absolute;inset:0;overflow:hidden;background:transparent;font-family:%s;">'
                 % (esc(cid), W, H, f_sans))
    if P["bg"]:
        # 铁律 4①：实心底 MUST 是根下第一个全幅子层
        parts.append('<div class="bgfill" style="position:absolute;inset:0;background:%s;"></div>' % P["bg"])
    parts.append("<style>%s</style>" % "\n".join(css))
    if smoke_on:
        parts.append('<div class="smoke" style="position:absolute;left:-140px;top:-140px;right:-140px;bottom:-140px;'
                     'background:radial-gradient(ellipse 46%% 40%% at 30%% 38%%, rgba(255,255,255,0.075), rgba(255,255,255,0) 100%),'
                     'radial-gradient(ellipse 52%% 44%% at 72%% 64%%, rgba(255,255,255,0.055), rgba(255,255,255,0) 100%);"></div>')
    if P.get("vignette"):
        parts.append('<div class="vig" style="position:absolute;inset:0;background:%s;"></div>' % P["vignette"])
    parts.append('<div class="cam">')
    for i, (g, im) in enumerate(zip(geo, imgs)):
        inner = '<img src="%s" alt="">' % im["uri"]
        if i == 0:
            inner += "".join(marks)
        parts.append('<div class="pg pg%d" style="left:%spx;top:%spx;width:%spx;height:%spx;z-index:%d;">%s</div>'
                     % (i, num(g["left"]), num(g["top"]), num(g["w"]), num(g["h"]), g["z"], inner))
    parts.append("</div>")
    if dust_svg:
        parts.append(dust_svg)
    parts.extend(cap_html)
    parts.append('<script src="%s"></script>' % GSAP_CDN)
    parts.append("<script>(function(){\n%s\n})();</script>" % "\n".join(js))
    parts.append("</div>")
    parts.append("</template>")
    out_html = "\n".join(parts)
    if len(out_html) > max_chars:
        die("产物 %d 字符超过预算 %d（减页数 / 降 top_max_px / 降 quality）" % (len(out_html), max_chars))
    summary = {
        "id": cid,
        "chars": len(out_html),
        "duration": D,
        "pages": [{"src": os.path.basename(x["src"]), "orig": [x["w"], x["h"]], "bytes": x["bytes"]} for x in imgs],
        "quality": q,
        "entrance_end": round(ent_end, 3),
        "focus": [{"t": c[0], "end": round(c[0] + c[1], 3), "zoom": c[4]} for c in cam_keys],
        "captions": [{"id": c[0], "t": c[1], "until": c[4]} for c in cap_anims],
    }
    return out_html, summary


def main():
    ap = argparse.ArgumentParser(description="doc-reveal 颗粒生成器")
    ap.add_argument("spec")
    ap.add_argument("--out", required=True, help="产物 html 路径（直接写最终落点）")
    ap.add_argument("--max-chars", type=int, default=1900000, help="产物字符预算（客户端 2,000,000 硬上限之下）")
    a = ap.parse_args()
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
