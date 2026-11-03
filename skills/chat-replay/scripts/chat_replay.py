#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chat-replay · 聊天记录回放颗粒生成器（gsap-emit v1 合规）

用法：
    python chat_replay.py <spec.json> --out <particle.html>

spec 字段见 ../references/spec.md。脚本只做机械活：按内置字宽表断行、算每条消息的绝对位置与滚动量、
头像 / 图片重编码进预算、按 spec 的时刻发射 GSAP 时间线。说什么、谁说、什么时候说、强调哪句由调用方决定。

排版不交给浏览器：每行文字在生成期就断好（white-space:pre），每条消息绝对定位——
所以气泡高度、滚动量、镜头推近目标在生成期就是确定数，同 spec 逐帧一致。
断行用的字宽是估算（思源黑体 Medium 的 ASCII 步进 + CJK 全角），真实宽度由 chat_check.py 在浏览器里量出来对账。

依赖：Pillow（只在有头像 / 图片消息时用）。
"""
import argparse
import base64
import html
import io
import json
import math
import os
import re
import sys

GSAP_CDN = "https://lib.baomitu.com/gsap/3.13.0/gsap.min.js"

FONT_SANS = "'Source Han Sans CN','Source Han Sans SC','Noto Sans SC','思源黑体',sans-serif"
FONT_SERIF = "'Source Han Serif CN','Source Han Serif SC','Noto Serif SC','思源宋体',serif"

# 思源黑体 CN Medium 的 ASCII 0x20..0x7E 步进宽（em）。比 Regular 略宽，断行偏保守。
ASCII_EM = [
    0.225, 0.343, 0.518, 0.57, 0.57, 0.939, 0.705, 0.298, 0.355, 0.355, 0.484, 0.57, 0.298, 0.357, 0.298, 0.39,
    0.57, 0.57, 0.57, 0.57, 0.57, 0.57, 0.57, 0.57, 0.57, 0.57, 0.298, 0.298, 0.57, 0.57, 0.57, 0.491,
    0.972, 0.622, 0.667, 0.646, 0.699, 0.6, 0.566, 0.701, 0.74, 0.309, 0.55, 0.663, 0.557, 0.83, 0.734, 0.754,
    0.647, 0.754, 0.655, 0.608, 0.61, 0.733, 0.594, 0.894, 0.596, 0.553, 0.607, 0.355, 0.39, 0.355, 0.57, 0.562,
    0.614, 0.575, 0.629, 0.517, 0.63, 0.565, 0.345, 0.578, 0.621, 0.287, 0.288, 0.575, 0.298, 0.942, 0.624, 0.614,
    0.63, 0.63, 0.409, 0.48, 0.396, 0.62, 0.545, 0.828, 0.526, 0.544, 0.49, 0.355, 0.281, 0.355, 0.57,
]
WRAP_SAFETY = 1.04  # 估算宽度再放大 4%：宁可早一个字换行，也别让真实字形撑破气泡上限

NO_LINE_START = set("，。、；：？！）》」』”’…—,.;:?!)]}%")
NO_LINE_END = set("（《「『“‘([{")

# 渲染服务端字体表没有彩色 emoji：出现即豆腐块。自检拦下，确认过渲染端能出字再用 allow_emoji 放行。
EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF\U0001F1E6-\U0001F1FF✀-➿️‍☀-☄☇-☿♁♃-⛿]")

PRESETS = {
    "light": {
        "canvas": "#E9EBEF", "screen": "#F4F5F7", "header": "#FFFFFF", "hd_rule": "rgba(20,24,32,0.08)",
        "ink": "#15181E", "ink_sub": "#7B818C",
        "bubble_in": "#FFFFFF", "ink_in": "#15181E", "bubble_out": "#3A6FF0", "ink_out": "#FFFFFF",
        "accent": "#E8962A", "mark": "rgba(255,196,0,0.45)",
        "bezel": "#1A1C21", "field": "#EEF0F3", "send": "#3A6FF0", "sys_bg": "rgba(20,24,32,0.06)",
        "card": "rgba(255,255,255,0.88)", "rule": "rgba(20,24,32,0.14)",
        "bubble_shadow": "0 1px 2px rgba(20,24,32,0.10)",
        "frame_shadow": "0 40px 80px rgba(20,24,40,0.22), 0 8px 20px rgba(20,24,40,0.12)",
    },
    "dark": {
        "canvas": "#0C0D10", "screen": "#14161B", "header": "#1B1E25", "hd_rule": "rgba(255,255,255,0.06)",
        "ink": "#ECEEF2", "ink_sub": "#8B919C",
        "bubble_in": "#262A33", "ink_in": "#ECEEF2", "bubble_out": "#3E6BE6", "ink_out": "#FFFFFF",
        "accent": "#F2B03A", "mark": "rgba(255,190,40,0.38)",
        "bezel": "#2A2D34", "field": "#232730", "send": "#3E6BE6", "sys_bg": "rgba(255,255,255,0.07)",
        "card": "rgba(20,22,28,0.80)", "rule": "rgba(255,255,255,0.16)",
        "bubble_shadow": "0 1px 2px rgba(0,0,0,0.30)",
        "frame_shadow": "0 40px 90px rgba(0,0,0,0.55), 0 6px 18px rgba(0,0,0,0.40)",
    },
}
PRESETS["transparent"] = dict(PRESETS["dark"], canvas=None)

# 首字头像的中性底色（按参与者出场序取，确定性）。栏目要统一色就在 participants[].color 里给。
AVATAR_COLORS = ["#6F86B5", "#B5846F", "#6FA58A", "#9479B0", "#B09A6A", "#6AA2AE", "#B0707F"]


def die(msg):
    sys.stderr.write("chat-replay: " + msg + "\n")
    sys.exit(2)


def num(v, nd=2):
    s = ("%." + str(nd) + "f") % float(v)
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def esc(s):
    return html.escape(str(s), quote=True)


def cycles(span, cycle):
    """yoyo 循环的 repeat 次数：总长 (repeat+1)*cycle ≥ span（铁律 7：有限次、按坑位算死）。"""
    return max(0, int(math.ceil(span / cycle)) - 1)


# ———————————————————————— 字宽估算与断行 ————————————————————————

def char_em(ch):
    o = ord(ch)
    if 0x20 <= o <= 0x7E:
        return ASCII_EM[o - 0x20]
    if o < 0x2E80 and ch not in "—…“”‘’":
        return 0.62  # 拉丁扩展 / 希腊 / 西里尔：取中位
    return 1.0       # CJK、全角标点、弯引号


def text_em(s):
    return sum(char_em(c) for c in s)


def is_word(ch):
    o = ord(ch)
    return (0x30 <= o <= 0x39) or (0x41 <= o <= 0x5A) or (0x61 <= o <= 0x7A) or ch in "'_-.@/#%+&:"


def wrap(text, fs, max_px):
    """断行。返回 [(start, end, est_px)]，下标指向原文（换行符不进任何一行）。"""
    toks = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "\n":
            toks.append((i, i + 1, "nl"))
            i += 1
        elif is_word(ch):
            j = i
            while j < n and is_word(text[j]):
                j += 1
            toks.append((i, j, "w"))
            i = j
        else:
            toks.append((i, i + 1, "sp" if ch == " " else "c"))
            i += 1
    px = lambda a, b: text_em(text[a:b]) * fs * WRAP_SAFETY
    lines = []
    cur = []  # 当前行的 token

    def flush():
        while cur and cur[-1][2] == "sp":
            cur.pop()
        if cur:
            lines.append((cur[0][0], cur[-1][1]))
        else:
            lines.append(None)  # 空行（显式换行产生）
        del cur[:]

    def cur_px():
        return px(cur[0][0], cur[-1][1]) if cur else 0.0

    for tk in toks:
        a, b, kind = tk
        if kind == "nl":
            flush()
            continue
        if kind == "sp" and not cur:
            continue
        if (not cur and px(a, b) <= max_px) or (cur and px(cur[0][0], b) <= max_px):
            cur.append(tk)
            continue
        # 放不下
        if kind == "w" and px(a, b) > max_px:
            # 超长单词：逐字符拆
            for k in range(a, b):
                sub = (k, k + 1, "c")
                if cur and px(cur[0][0], k + 1) > max_px:
                    flush()
                cur.append(sub)
            continue
        if text[a] in NO_LINE_START and len(cur) >= 2:
            # 避头：把上一行末尾的 token 拉下来陪它
            prev = cur.pop()
            flush()
            cur.extend([prev, tk])
            continue
        if cur and text[cur[-1][0]] in NO_LINE_END and len(cur) >= 2:
            # 避尾：开括号不留在行末
            prev = cur.pop()
            flush()
            cur.extend([prev] + ([] if kind == "sp" else [tk]))
            continue
        flush()
        if kind != "sp":
            cur.append(tk)
    flush()
    # 去掉尾部空行，保留中间的空行
    while lines and lines[-1] is None:
        lines.pop()
    # 孤字：段末一行只剩一个字，从上一行挪一个字下来（不拆拉丁单词、不把避头标点挪到行首）
    for k in range(1, len(lines)):
        cur_ln, prev_ln = lines[k], lines[k - 1]
        last_of_para = (k + 1 == len(lines)) or lines[k + 1] is None
        if not last_of_para or cur_ln is None or prev_ln is None or cur_ln[1] - cur_ln[0] != 1:
            continue
        ps, pe = prev_ln
        mv = pe - 1
        if pe - ps < 4 or text[mv] == " " or text[mv] in NO_LINE_START or (is_word(text[mv]) and is_word(text[mv - 1])):
            continue
        ne = mv
        while ne > ps and text[ne - 1] == " ":
            ne -= 1
        if text[ne - 1] in NO_LINE_END:
            continue
        lines[k - 1] = (ps, ne)
        lines[k] = (mv, cur_ln[1])
    out = []
    for ln in lines:
        if ln is None:
            out.append((0, 0, 0.0))
        else:
            out.append((ln[0], ln[1], px(ln[0], ln[1])))
    return out


# ———————————————————————— 图片 ————————————————————————

def encode_image(path, box_w, box_h, quality, cover):
    try:
        from PIL import Image
    except ImportError:
        die("有头像 / 图片消息时需要 Pillow：pip install pillow")
    im = Image.open(path).convert("RGB")
    ow, oh = im.size
    if cover:
        # 头像：居中裁方
        side = min(ow, oh)
        im = im.crop(((ow - side) // 2, (oh - side) // 2, (ow - side) // 2 + side, (oh - side) // 2 + side))
        im = im.resize((int(box_w), int(box_h)), Image.LANCZOS)
    else:
        # box 是显示尺寸（1x）：显示宽高取整数（半像素会让气泡高与排版高差 0.5px），按 2x 编码保清晰
        s = min(box_w / ow, box_h / oh, 2.0)
        dw, dh = max(1, int(round(ow * s))), max(1, int(round(oh * s)))
        if ow > dw * 2:
            im = im.resize((dw * 2, max(1, int(round(oh * dw * 2 / ow)))), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "WEBP", quality=int(quality), method=6)
        return "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode("ascii"), (dw, dh), (ow, oh)
    buf = io.BytesIO()
    im.save(buf, "WEBP", quality=int(quality), method=6)
    return "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode("ascii"), im.size, (ow, oh)


# ———————————————————————— 主体 ————————————————————————

def build(spec, spec_dir, max_chars):
    cid = spec.get("id") or die("spec.id 必填（= data-composition-id）")
    W = int(spec.get("width", 1920))
    H = int(spec.get("height", 1080))
    D = float(spec.get("duration") or 0) or die("spec.duration 必填（秒，≥ 坑位包络 + 0.3）")
    if W <= 0 or H <= 0:
        die("width / height 必须是正整数")

    bg = spec.get("bg", "light")
    if bg in PRESETS:
        P = dict(PRESETS[bg])
    elif isinstance(bg, str) and bg.startswith("#"):
        P = dict(PRESETS["light"])
        P["canvas"] = bg
    else:
        die("bg 只收 light / dark / transparent / #RRGGBB")
    for k, v in (spec.get("palette") or {}).items():
        if k not in P:
            die("palette 不认识的键：%s（可用：%s）" % (k, " / ".join(sorted(x for x in P))))
        P[k] = v
    fonts = spec.get("fonts") or {}
    f_sans = fonts.get("sans", FONT_SANS)
    f_serif = fonts.get("serif", FONT_SERIF)
    safe_bottom = float(spec.get("safe_bottom", 0.85)) * H
    allow_emoji = bool(spec.get("allow_emoji", False))
    quality = int(spec.get("quality", 84))

    # —— 画框几何 ——
    fr = spec.get("frame") or {}
    kind = fr.get("kind", "phone")
    if kind not in ("phone", "panel", "none"):
        die("frame.kind 只收 phone / panel / none")
    if kind == "phone":
        fh = float(fr.get("h", round(H * 0.81)))
        fw = float(fr.get("w", round(fh * 0.52)))
        fx = float(fr.get("x", round(W * 0.64 - fw / 2)))
        fy = float(fr.get("y", round(H * 0.025)))
        bez, rad, hd_h, ib_h = 12.0, 58.0, 92.0, 78.0
    elif kind == "panel":
        fw = float(fr.get("w", round(W * 0.42)))
        fh = float(fr.get("h", round(H * 0.80)))
        fx = float(fr.get("x", W - 96 - fw))
        fy = float(fr.get("y", round((H * 0.85 - fh) / 2)))
        bez, rad, hd_h, ib_h = 0.0, 22.0, 76.0, 76.0
    else:
        fw = float(fr.get("w", 720))
        fh = float(fr.get("h", round(H * 0.78)))
        fx = float(fr.get("x", round((W - fw) / 2)))
        fy = float(fr.get("y", round(H * 0.04)))
        bez, rad, hd_h, ib_h = 0.0, 0.0, 0.0, 0.0
    if kind != "none" and fr.get("input") is False:
        ib_h = 0.0
    if kind != "none" and fr.get("header") is False:
        hd_h = 0.0
    if fx < 0 or fy < 0 or fx + fw > W + 0.5 or fy + fh > H + 0.5:
        die("画框越出画幅：x=%s y=%s w=%s h=%s（画幅 %dx%d）" % (num(fx), num(fy), num(fw), num(fh), W, H))
    sw, sh = fw - 2 * bez, fh - 2 * bez          # 屏幕
    VW, VH = sw, sh - hd_h - ib_h                # 消息视口
    vp_bottom_abs = fy + bez + hd_h + VH
    if vp_bottom_abs > safe_bottom + 0.5:
        die("消息视口底沿 %s 落进字幕带（> safe_bottom %s）——最新一条消息会被字幕压住。"
            "把画框上移 / 调矮，或确认无字幕后设 safe_bottom: 1" % (num(vp_bottom_abs), num(safe_bottom)))

    # —— 尺度 ——
    fs = float(spec.get("font_size", 26))
    LH = round(fs * 1.42)
    pad_y, pad_x = round(fs * 0.5), round(fs * 0.7)
    rad_b, rad_s = round(fs * 0.8), 6
    show_av = bool(spec.get("avatars", True))
    AV = round(fs * 2.0) if show_av else 0
    side = 18
    off = side + AV + (12 if show_av else 0)     # 气泡离视口边的距离（含头像）
    max_bw = round(min(VW * 0.72, VW - off - 44))
    max_tw = max_bw - 2 * pad_x                  # 气泡内文字最大宽
    if max_tw < fs * 4:
        die("视口太窄：气泡里一行放不下 4 个字（视口宽 %s，font_size %s）" % (num(VW), num(fs)))
    name_fs, name_h = round(fs * 0.66), round(fs * 1.0)
    sys_fs = round(fs * 0.68)
    sys_h = round(sys_fs * 1.4) + 12
    ty_w, ty_h = round(fs * 3.4), LH + 2 * pad_y
    dot = max(6, round(fs * 0.3))
    gap, gap_run = round(fs * 0.75), round(fs * 0.4)
    top_pad, bot_pad = round(fs * 0.8), round(fs * 0.6)

    # —— 参与者 ——
    parts = spec.get("participants") or die("participants 必填：{key: {name, side: left|right}}")
    pk_list = list(parts.keys())
    avatars_css = []
    for k_i, k in enumerate(pk_list):
        pp = parts[k]
        if pp.get("side", "left") not in ("left", "right"):
            die("participants.%s.side 只收 left / right" % k)
        pp.setdefault("name", k)
        pp.setdefault("color", AVATAR_COLORS[k_i % len(AVATAR_COLORS)])
        if show_av and pp.get("avatar"):
            src = pp["avatar"] if os.path.isabs(pp["avatar"]) else os.path.join(spec_dir, pp["avatar"])
            if not os.path.exists(src):
                die("找不到头像：" + src)
            uri, _, _ = encode_image(src, AV * 2, AV * 2, quality, True)
            avatars_css.append(("av%d" % k_i, uri))
        pp["_cls"] = "av%d" % k_i
    show_names = spec.get("names")
    if show_names is None:
        show_names = sum(1 for k in pk_list if parts[k].get("side", "left") == "left") >= 2

    # —— 消息排版 ——
    msgs = spec.get("messages") or die("messages 至少一条")
    layout = []
    y = float(top_pad)
    prev_from = None
    seen_timed = False
    for i, m in enumerate(msgs):
        mk = m.get("kind", "text")
        if mk not in ("text", "image", "system"):
            die("messages[%d].kind 只收 text / image / system" % i)
        t = m.get("t")
        if t is None:
            if seen_timed:
                die("messages[%d] 没有 t，但前面已有带 t 的消息——不带 t 的「开场已在屏上」消息只能排在最前面" % i)
        else:
            seen_timed = True
            t = float(t)
        L = {"i": i, "kind": mk, "t": t}
        if mk == "system":
            txt = m.get("text") or die("messages[%d] system 消息要有 text" % i)
            if not allow_emoji and EMOJI_RE.search(txt):
                die("messages[%d] 含 emoji：%r——渲染端字体表没有彩色 emoji，会出豆腐块。改成文字或图片消息" % (i, txt))
            if y > top_pad:
                y += gap - gap_run
            L.update(top=y, h=sys_h, text=txt, from_=None)
            y += sys_h + gap_run
            prev_from = None
        else:
            who = m.get("from") or die("messages[%d] 要有 from" % i)
            if who not in parts:
                die("messages[%d].from=%s 不在 participants 里" % (i, who))
            pp = parts[who]
            sd = pp.get("side", "left")
            run = (prev_from == who)
            if y > top_pad and not run:
                y += gap - gap_run
            nm = bool(show_names and sd == "left" and not run)
            nh = name_h if nm else 0
            if mk == "text":
                txt = m.get("text")
                if not txt:
                    die("messages[%d] text 消息要有 text" % i)
                if not allow_emoji and EMOJI_RE.search(txt):
                    die("messages[%d] 含 emoji：%r——渲染端字体表没有彩色 emoji，会出豆腐块。改成文字或图片消息" % (i, txt))
                lines = wrap(txt, fs, max_tw)
                bw = max(ln[2] for ln in lines) + 2 * pad_x
                bh = len(lines) * LH + 2 * pad_y
                L.update(text=txt, lines=lines)
            else:
                src = m.get("src") or die("messages[%d] image 消息要有 src" % i)
                src = src if os.path.isabs(src) else os.path.join(spec_dir, src)
                if not os.path.exists(src):
                    die("找不到图片：" + src)
                box_w = min(max_bw, round(VW * 0.6))
                box_h = round(VH * 0.45)
                uri, (dw, dh), orig = encode_image(src, box_w, box_h, quality, False)
                bw, bh = dw, dh
                L.update(uri=uri, iw=dw, ih=dh)
            if bh > VH - top_pad - bot_pad:
                die("messages[%d] 气泡高 %spx，超过视口 %spx——一条消息放不进屏，拆成几条" % (i, num(bh), num(VH)))
            L.update(top=y, from_=who, side=sd, name=nm, nh=nh, bw=bw, bh=bh,
                     h=nh + max(bh, AV))
            y += L["h"] + gap_run
            prev_from = who
        L["bottom"] = L["top"] + L["h"]
        layout.append(L)

    # —— 事件：打字中 / 我方输入 / 出现 ——
    has_input = kind != "none" and ib_h > 0
    events = []   # (time, bottom, msg_i, what)
    last_t = None
    for L, m in zip(layout, msgs):
        if L["t"] is None:
            continue
        t = L["t"]
        if t < 0 or t > D:
            die("messages[%d].t=%s 越出 [0, duration]" % (L["i"], num(t)))
        ty = float(m.get("typing", 0) or 0)
        cp = float(m.get("compose", 0) or 0)
        if ty and cp:
            die("messages[%d] 同时给了 typing 与 compose——对方打字用 typing，我方在输入框里打字用 compose" % L["i"])
        if cp and not has_input:
            die("messages[%d] 用了 compose，但画框没有输入栏（frame.kind=none 或 frame.input=false）" % L["i"])
        if (ty or cp) and L["kind"] == "system":
            die("messages[%d] 系统提示不能带 typing / compose" % L["i"])
        start = t - (ty or cp)
        if last_t is not None and start < last_t + 0.2:
            die("messages[%d] 的 %s 窗口从 %ss 开始，与上一条（%ss 出现）挤在一起（至少隔 0.2s）——"
                "缩短 typing/compose 或把 t 往后挪" % (L["i"], "typing" if ty else ("compose" if cp else "出现"), num(start), num(last_t)))
        if start < 0:
            die("messages[%d] 的 typing/compose 窗口从负时刻开始（%ss）" % (L["i"], num(start)))
        L["typing"], L["compose"], L["start"] = ty, cp, start
        if ty:
            events.append((start, L["top"] + L["nh"] + max(ty_h, AV), L["i"], "typing"))
        events.append((t, L["bottom"], L["i"], "show"))
        last_t = t
    if last_t is not None and last_t > D - 0.5:
        die("最后一条消息在 %ss 出现，离颗粒末尾（%ss）不到 0.5s——没有驻留，观众看不清。加长 duration 或提前" % (num(last_t), num(D)))

    # 滚动量：事件底沿进视口所需的位移，单调不减。
    # stack=top：像真实 App，消息从视口顶往下排，排满才开始上推；
    # stack=bottom：最新一条始终贴着视口底，每来一条整列上推一格（视线固定在底部）。
    stack = spec.get("stack", "top")
    if stack not in ("top", "bottom"):
        die("stack 只收 top / bottom")
    floor_s = 0.0 if stack == "top" else -1e9
    ev_sorted = sorted(events, key=lambda e: (e[0], e[2]))
    pre = [L["bottom"] for L in layout if L["t"] is None]
    if pre:
        S = max(floor_s, max(pre) + bot_pad - VH)
    elif stack == "bottom" and ev_sorted:
        S = ev_sorted[0][1] + bot_pad - VH  # 没有开场消息：第一条直接落在底部，不从空白处滑上来
    else:
        S = 0.0
    S0 = S
    scroll_keys = []  # (t, S)
    for (te, b, i, what) in ev_sorted:
        need = max(floor_s, b + bot_pad - VH)
        if need > S + 0.5:
            S = need
            scroll_keys.append((te, S))
    scroll_at = lambda tt: max([S0] + [s for (k, s) in scroll_keys if k <= tt + 1e-6])

    # —— 强调 ——
    focus = spec.get("focus") or []
    fl = []
    for k, f in enumerate(focus):
        mi = f.get("msg")
        if not isinstance(mi, int) or not (0 <= mi < len(layout)):
            die("focus[%d].msg 要是 messages 的下标（0 起）" % k)
        L = layout[mi]
        if L["kind"] == "system":
            die("focus[%d] 指向系统提示——强调只给对话气泡" % k)
        ft = float(f.get("t", -1))
        shown = L["t"] if L["t"] is not None else 0.0
        if ft < shown + 0.2:
            die("focus[%d].t=%s 早于消息 %d 出现（%ss）+0.2——还没出现的消息没法强调" % (k, num(ft), mi, num(shown)))
        until = f.get("until")
        until = float(until) if until is not None else None
        if until is not None and until < ft + 0.6:
            die("focus[%d].until 至少比 t 晚 0.6s" % k)
        mark = f.get("mark")
        mark_rng = None
        if mark:
            if L["kind"] != "text":
                die("focus[%d].mark 只用于文字消息" % k)
            pos = L["text"].find(mark)
            if pos < 0:
                die("focus[%d].mark=%r 在消息 %d 的原文里找不到（逐字匹配）" % (k, mark, mi))
            mark_rng = (pos, pos + len(mark))
        zoom = float(f.get("zoom", 1.0))
        fl.append({"k": k, "msg": mi, "t": ft, "until": until, "mark": mark_rng, "zoom": zoom,
                   "anchor": f.get("anchor", [0.5, 0.46]), "ring": bool(f.get("ring", True)), "dim": float(f.get("dim", 0.32))})
    fl.sort(key=lambda x: x["t"])
    for a, b in zip(fl, fl[1:]):
        if a["until"] is None or a["until"] > b["t"]:
            a["until_auto"] = b["t"]  # 下一处强调开始时，上一处自动让位
    for L in layout:
        L["marks"] = [(f["k"], f["mark"]) for f in fl if f["msg"] == L["i"] and f["mark"]]

    # —— 文案卡 ——
    captions = spec.get("captions") or []
    cap_html, cap_anims, cap_rects = [], [], {}
    for k, c in enumerate(captions):
        variant = c.get("variant", "card")
        if variant not in ("card", "chip"):
            die("captions[%d].variant 只收 card / chip" % k)
        cw = float(c.get("w", 600 if variant == "card" else 520))
        pos = c.get("pos", "left")
        if "x" in c:
            cx = float(c["x"])
        elif pos == "right":
            cx = W - 96 - cw
        else:
            cx = 96.0
        cy = float(c.get("y", 300))
        # 估高（用同一张字宽表），查字幕带与画框避让
        inner_w = cw - (68 if variant == "card" else 42)
        est_h = 58.0 if variant == "card" else 24.0
        if c.get("kicker"):
            est_h += 22 * 1.4 + 10
        if variant == "card":
            est_h += 21
        if c.get("title"):
            tfs = 48 if variant == "card" else 30
            est_h += len(wrap(c["title"], tfs, inner_w)) * tfs * 1.28
        if c.get("meta"):
            est_h += len(wrap(c["meta"], 24, inner_w)) * 24 * 1.4 + 14
        cap_rects[k] = (cx, cy, cw, est_h)
        if cy + est_h > safe_bottom + 0.5:
            die("captions[%d] 估算底沿 %s 落进字幕带（> %s）——上移或删字" % (k, num(cy + est_h), num(safe_bottom)))
        if cx < fx + fw and cx + cw > fx and cy < fy + fh and cy + est_h > fy:
            die("captions[%d]（x %s–%s）压到画框（x %s–%s）上——换栏或把画框挪开" % (k, num(cx), num(cx + cw), num(fx), num(fx + fw)))
        for key in ("kicker", "title", "meta"):
            if c.get(key) and not allow_emoji and EMOJI_RE.search(c[key]):
                die("captions[%d].%s 含 emoji" % (k, key))
        inner = []
        if c.get("kicker"):
            inner.append('<div class="ck">%s</div>' % esc(c["kicker"]))
        if variant == "card":
            inner.append('<div class="cr"></div>')
        if c.get("title"):
            inner.append('<div class="ct">%s</div>' % esc(c["title"]))
        if c.get("meta"):
            inner.append('<div class="cm">%s</div>' % esc(c["meta"]))
        cap_html.append('<div class="cap cap%d %s" style="left:%spx;top:%spx;width:%spx;">%s</div>'
                        % (k, variant, num(cx), num(cy), num(cw), "".join(inner)))
        ct = float(c.get("t", 0))
        cu = c.get("until")
        cap_anims.append((k, ct, "left" if cx + cw / 2 < W / 2 else "right", float(c.get("dur", 0.7)),
                          float(cu) if cu is not None else None))

    # ———————————————————————— HTML ————————————————————————
    R = '[data-composition-id="%s"]' % cid
    css = []
    css.append("%s .cam{position:absolute;left:0;top:0;width:%dpx;height:%dpx;}" % (R, W, H))
    css.append("%s .fr{position:absolute;box-sizing:border-box;}" % R)
    css.append("%s .scr{position:absolute;overflow:hidden;}" % R)
    css.append("%s .hd{position:absolute;left:0;top:0;right:0;box-sizing:border-box;}" % R)
    css.append("%s .vp{position:absolute;left:0;overflow:hidden;}" % R)
    css.append("%s .ls{position:absolute;left:0;top:0;width:100%%;}" % R)
    css.append("%s .m,%s .ty{position:absolute;left:0;width:100%%;}" % (R, R))
    css.append("%s .av{position:absolute;width:%dpx;height:%dpx;border-radius:50%%;overflow:hidden;background-size:cover;"
               "background-position:50%% 50%%;color:#FFFFFF;font-weight:600;font-size:%dpx;line-height:%dpx;text-align:center;}"
               % (R, AV, AV, round(AV * 0.42), AV))
    for cls, uri in avatars_css:
        css.append("%s .%s{background-image:url(%s);}" % (R, cls, uri))
    css.append("%s .nm{position:absolute;font-size:%dpx;line-height:%dpx;color:%s;white-space:pre;}" % (R, name_fs, name_h - 4, P["ink_sub"]))
    css.append("%s .bb{position:absolute;box-sizing:border-box;padding:%dpx %dpx;font-size:%spx;line-height:%dpx;box-shadow:%s;}"
               % (R, pad_y, pad_x, num(fs), LH, P["bubble_shadow"]))
    css.append("%s .in .bb{background:%s;color:%s;border-radius:%dpx %dpx %dpx %dpx;}" % (R, P["bubble_in"], P["ink_in"], rad_s, rad_b, rad_b, rad_b))
    css.append("%s .out .bb{background:%s;color:%s;border-radius:%dpx %dpx %dpx %dpx;}" % (R, P["bubble_out"], P["ink_out"], rad_b, rad_s, rad_b, rad_b))
    css.append("%s .bb.im{padding:0;overflow:hidden;background:transparent;}" % R)
    css.append("%s .bb.im img{display:block;width:100%%;height:100%%;}" % R)
    css.append("%s .ln{white-space:pre;width:max-content;height:%dpx;}" % (R, LH))
    css.append("%s .hl{background-image:linear-gradient(%s,%s);background-repeat:no-repeat;background-position:0 60%%;background-size:0%% 78%%;}"
               % (R, P["mark"], P["mark"]))
    css.append("%s .rg{position:absolute;left:-5px;top:-5px;right:-5px;bottom:-5px;border:3px solid %s;border-radius:%dpx;opacity:0;}"
               % (R, P["accent"], rad_b + 5))
    css.append("%s .sy .sp{position:absolute;left:50%%;top:0;transform:translateX(-50%%);white-space:pre;font-size:%dpx;line-height:%dpx;"
               "padding:6px 14px;border-radius:%dpx;background:%s;color:%s;}" % (R, sys_fs, sys_h - 12, round(sys_h / 2), P["sys_bg"], P["ink_sub"]))
    css.append("%s .ty .bb{width:%dpx;height:%dpx;padding:0;}" % (R, ty_w, ty_h))
    css.append("%s .d{position:absolute;top:%spx;width:%dpx;height:%dpx;border-radius:50%%;background:%s;opacity:0.55;}"
               % (R, num((ty_h - dot) / 2), dot, dot, P["ink_sub"]))
    css.append("%s .cap{position:absolute;box-sizing:border-box;}" % R)
    css.append("%s .cap.card{padding:28px 34px 30px;background:%s;border-radius:6px;}" % (R, P["card"]))
    css.append("%s .cap.chip{padding:12px 20px 12px 22px;background:%s;border-left:4px solid %s;border-radius:4px;}" % (R, P["card"], P["accent"]))
    css.append("%s .ck{font-weight:500;font-size:22px;letter-spacing:0.18em;color:%s;margin-bottom:10px;}" % (R, P["ink_sub"]))
    css.append("%s .cr{width:52px;height:3px;background:%s;margin:0 0 18px;}" % (R, P["accent"]))
    css.append("%s .card .ct{font-family:%s;font-weight:900;font-size:48px;line-height:1.28;letter-spacing:0.04em;color:%s;}" % (R, f_serif, P["ink"]))
    css.append("%s .chip .ct{font-weight:700;font-size:30px;line-height:1.28;letter-spacing:0.04em;color:%s;}" % (R, P["ink"]))
    css.append("%s .cm{font-weight:500;font-size:24px;line-height:1.4;letter-spacing:0.06em;color:%s;margin-top:14px;}" % (R, P["ink_sub"]))
    css.append("%s .chip .cm{margin-top:4px;font-size:22px;}" % R)

    def av_html(pk, left_px, top_px, right=False):
        if not show_av:
            return ""
        pp = parts[pk]
        side_css = ("right:%spx;" % num(left_px)) if right else ("left:%spx;" % num(left_px))
        if pp.get("avatar"):
            return '<div class="av %s" style="%stop:%spx;"></div>' % (pp["_cls"], side_css, num(top_px))
        initial = (pp.get("initial") or pp["name"])[:1]
        return '<div class="av" style="%stop:%spx;background:%s;">%s</div>' % (side_css, num(top_px), pp["color"], esc(initial))

    def lines_html(L):
        out = []
        txt = L["text"]
        for (a, b, est) in L["lines"]:
            segs = []  # 按强调区间切段
            cuts = {a, b}
            for (fk, (ma, mb)) in L["marks"]:
                if ma < b and mb > a:
                    cuts.add(max(a, ma))
                    cuts.add(min(b, mb))
            cuts = sorted(cuts)
            for s0, s1 in zip(cuts, cuts[1:]):
                hit = [fk for (fk, (ma, mb)) in L["marks"] if ma <= s0 and mb >= s1]
                piece = esc(txt[s0:s1])
                if hit:
                    piece = '<span class="hl hl%d">%s</span>' % (hit[0], piece)
                segs.append(piece)
            out.append('<div class="ln" data-ew="%s">%s</div>' % (num(est, 1), "".join(segs)))
        return "".join(out)

    items = []
    for L in layout:
        i = L["i"]
        tattr = num(L["t"], 3) if L["t"] is not None else "pre"
        if L["kind"] == "system":
            items.append('<div class="m m%d sy" data-i="%d" data-t="%s" style="top:%spx;height:%spx;"><div class="sp">%s</div></div>'
                         % (i, i, tattr, num(L["top"]), num(L["h"]), esc(L["text"])))
            continue
        right = L["side"] == "right"
        cls = "out" if right else "in"
        inner = []
        inner.append(av_html(L["from_"], side, L["nh"], right))
        if L["name"]:
            inner.append('<div class="nm" style="left:%spx;top:0;">%s</div>' % (num(off), esc(parts[L["from_"]]["name"])))
        pos_css = ("right:%spx;" % num(off)) if right else ("left:%spx;" % num(off))
        if L["kind"] == "text":
            inner.append('<div class="bb" data-h="%s" data-maxw="%s" style="%stop:%spx;">%s<div class="rg"></div></div>'
                         % (num(L["bh"]), num(max_bw), pos_css, num(L["nh"]), lines_html(L)))
        else:
            inner.append('<div class="bb im" data-h="%s" data-maxw="%s" style="%stop:%spx;width:%spx;height:%spx;border-radius:%dpx;">'
                         '<img src="%s" alt=""><div class="rg"></div></div>'
                         % (num(L["bh"]), num(max_bw), pos_css, num(L["nh"]), num(L["iw"]), num(L["ih"]), rad_b, L["uri"]))
        items.append('<div class="m m%d %s" data-i="%d" data-t="%s" style="top:%spx;height:%spx;">%s</div>'
                     % (i, cls, i, tattr, num(L["top"]), num(L["h"]), "".join(inner)))
        if L.get("typing"):
            dots = "".join('<div class="d d%d" style="left:%spx;"></div>' % (k, num((ty_w - 3 * dot - 2 * round(dot * 0.9)) / 2 + k * (dot + round(dot * 0.9)))) for k in range(3))
            tin = [av_html(L["from_"], side, L["nh"], right)]
            if L["name"]:
                tin.append('<div class="nm" style="left:%spx;top:0;">%s</div>' % (num(off), esc(parts[L["from_"]]["name"])))
            tin.append('<div class="bb" style="%stop:%spx;">%s</div>' % (pos_css, num(L["nh"]), dots))
            items.append('<div class="ty ty%d %s" data-t0="%s" data-t1="%s" style="top:%spx;height:%spx;">%s</div>'
                         % (i, cls, num(L["t"] - L["typing"], 3), num(L["t"], 3), num(L["top"]), num(L["nh"] + max(ty_h, AV)), "".join(tin)))

    # 输入栏：我方 compose 的文字逐字出现在输入框里，发送瞬间清空
    ib_html = ""
    compose_items = []
    field_h = round(fs * 1.85)
    if has_input:
        btn = field_h
        field_l, field_r = 18, 18 + btn + 12
        field_w = sw - field_l - field_r
        txt_w = field_w - 2 * round(fs * 0.6)
        cps = []
        for L in layout:
            if L.get("compose"):
                flat = L["text"].replace("\n", " ") if L["kind"] == "text" else "[图片]"
                # 一字一个 <i>，逐个显形：不靠估宽裁切，就不会切出半个字
                chars = "".join("<i>%s</i>" % esc(ch) for ch in flat)
                cps.append('<div class="cpw cpw%d" style="position:absolute;left:%dpx;top:0;height:%dpx;width:%spx;overflow:hidden;">'
                           '<div class="cpt cpt%d" style="white-space:pre;font-size:%spx;line-height:%dpx;color:%s;">%s</div></div>'
                           % (L["i"], round(fs * 0.6), field_h, num(txt_w), L["i"], num(fs * 0.9), field_h, P["ink"], chars))
                # 超出输入框宽时整行左推：从第一个越界字开始，推到末字贴右沿（估宽不乘安全系数，CJK 逐字精确）
                right = 0.0
                k0, total = None, 0.0
                for ci, ch in enumerate(flat):
                    right += char_em(ch) * fs * 0.9
                    if k0 is None and right > txt_w:
                        k0 = ci
                total = right
                compose_items.append((L, len(flat), k0, max(0.0, total - txt_w + 4)))
        if compose_items:
            css.append("%s .cpt i{font-style:normal;opacity:0;}" % R)
        ib_html = ('<div class="ib" style="position:absolute;left:0;right:0;bottom:0;height:%spx;background:%s;border-top:1px solid %s;">'
                   '<div class="fd" style="position:absolute;left:%dpx;top:%spx;width:%spx;height:%dpx;border-radius:%dpx;background:%s;overflow:hidden;">%s</div>'
                   '<div class="sd" style="position:absolute;right:18px;top:%spx;width:%dpx;height:%dpx;border-radius:50%%;background:%s;">'
                   '<svg viewBox="0 0 24 24" style="position:absolute;left:25%%;top:25%%;width:50%%;height:50%%;"><path d="M12 19V6M6.5 11.5L12 6l5.5 5.5" fill="none" stroke="#FFFFFF" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/></svg></div>'
                   '</div>'
                   % (num(ib_h), P["header"], P["hd_rule"], field_l, num((ib_h - field_h) / 2), num(field_w), field_h, round(field_h / 2),
                      P["field"], "".join(cps), num((ib_h - field_h) / 2), btn, btn, P["send"]))

    hd_html = ""
    if kind != "none" and hd_h > 0:
        title = fr.get("title", "")
        sub = fr.get("subtitle", "")
        stamp = fr.get("stamp", "")
        for key, val in (("title", title), ("subtitle", sub), ("stamp", stamp)):
            if val and not allow_emoji and EMOJI_RE.search(val):
                die("frame.%s 含 emoji" % key)
        t_fs, s_fs = round(fs * 1.0), round(fs * 0.66)
        if kind == "phone":
            chev = ('<svg viewBox="0 0 14 24" style="position:absolute;left:22px;top:%spx;width:14px;height:24px;">'
                    '<path d="M11.5 2.5L3 12l8.5 9.5" fill="none" stroke="%s" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/></svg>'
                    % (num((hd_h - 24) / 2), P["ink"]))
            ttl = ('<div style="position:absolute;left:60px;right:60px;top:%spx;text-align:center;">'
                   '<div style="font-size:%dpx;line-height:%dpx;font-weight:600;color:%s;white-space:pre;">%s</div>%s</div>'
                   % (num(hd_h / 2 - (t_fs * 1.25 + (s_fs * 1.3 if sub else 0)) / 2), t_fs, round(t_fs * 1.25), P["ink"], esc(title),
                      ('<div style="font-size:%dpx;line-height:%dpx;color:%s;white-space:pre;">%s</div>' % (s_fs, round(s_fs * 1.3), P["ink_sub"], esc(sub))) if sub else ""))
        else:
            chev = ""
            ttl = ('<div style="position:absolute;left:28px;top:%spx;white-space:pre;">'
                   '<span style="font-size:%dpx;line-height:%dpx;font-weight:600;color:%s;">%s</span>'
                   '<span style="font-size:%dpx;color:%s;margin-left:14px;">%s</span></div>'
                   % (num((hd_h - t_fs * 1.25) / 2), t_fs, round(t_fs * 1.25), P["ink"], esc(title), s_fs, P["ink_sub"], esc(sub)))
        stp = ""
        if stamp:
            stp = ('<div style="position:absolute;right:%dpx;top:%spx;font-size:%dpx;line-height:%dpx;padding:0 10px;border-radius:%dpx;'
                   'border:1.5px solid %s;color:%s;white-space:pre;">%s</div>'
                   % (16 if kind == "phone" else 24, num((hd_h - s_fs * 1.6) / 2), s_fs, round(s_fs * 1.6) - 3, round(s_fs * 0.8), P["accent"], P["accent"], esc(stamp)))
        hd_html = ('<div class="hd" style="height:%spx;background:%s;border-bottom:1px solid %s;">%s%s%s</div>'
                   % (num(hd_h), P["header"], P["hd_rule"], chev, ttl, stp))

    if kind == "phone":
        fr_style = ("left:%spx;top:%spx;width:%spx;height:%spx;background:%s;border-radius:%spx;box-shadow:%s;"
                    % (num(fx), num(fy), num(fw), num(fh), P["bezel"], num(rad), P["frame_shadow"]))
        scr_style = ("left:%spx;top:%spx;width:%spx;height:%spx;border-radius:%spx;background:%s;"
                     % (num(bez), num(bez), num(sw), num(sh), num(rad - bez), P["screen"]))
    elif kind == "panel":
        fr_style = ("left:%spx;top:%spx;width:%spx;height:%spx;border-radius:%spx;box-shadow:%s;"
                    % (num(fx), num(fy), num(fw), num(fh), num(rad), P["frame_shadow"]))
        scr_style = ("left:0;top:0;width:%spx;height:%spx;border-radius:%spx;background:%s;"
                     % (num(sw), num(sh), num(rad), P["screen"]))
    else:
        fr_style = "left:%spx;top:%spx;width:%spx;height:%spx;" % (num(fx), num(fy), num(fw), num(fh))
        scr_style = "left:0;top:0;width:%spx;height:%spx;" % (num(sw), num(sh))

    # ———————————————————————— 时间线 ————————————————————————
    js = []
    js.append("var ID=%s;" % json.dumps(cid, ensure_ascii=False))
    js.append("var ROOT='[data-composition-id=\"'+ID+'\"]';")
    js.append("var q=function(s){return document.querySelector(ROOT+' '+s);};")
    js.append("var qa=function(s){return document.querySelectorAll(s.split(',').map(function(x){return ROOT+' '+x;}).join(','));};")
    js.append("var cam=q('.cam'),fr=q('.fr'),ls=q('.ls');")
    js.append("gsap.set(cam,{x:0,y:0,scale:1,transformOrigin:'0px 0px'});")
    js.append("gsap.set(ls,{y:%s});" % num(-S0))

    ent = spec.get("entrance") or {}
    ent_mode = ent.get("mode", "rise")
    if ent_mode not in ("rise", "fade", "none"):
        die("entrance.mode 只收 rise / fade / none")
    ent_start = float(ent.get("start", 0.1))
    ent_dur = float(ent.get("dur", 0.7))
    if ent_mode == "rise":
        js.append("gsap.set(fr,{opacity:0,y:70,scale:0.97,transformOrigin:'50% 100%'});")
    elif ent_mode == "fade":
        js.append("gsap.set(fr,{opacity:0});")
    for L in layout:
        if L["t"] is not None:
            i = L["i"]
            if L["kind"] == "system":
                js.append("gsap.set(q('.m%d'),{opacity:0,y:8});" % i)
            else:
                ox = (VW - off) if L["side"] == "right" else off
                oy = L["nh"] + L["bh"]
                js.append("gsap.set(q('.m%d'),{opacity:0,y:14,scale:0.92,transformOrigin:'%spx %spx'});" % (i, num(ox), num(oy)))
            if L.get("typing"):
                js.append("gsap.set(q('.ty%d'),{opacity:0,scale:0.9,transformOrigin:'%spx %spx'});"
                          % (i, num((VW - off) if L["side"] == "right" else off), num(L["nh"] + ty_h)))
    for f in fl:
        if f["ring"]:
            js.append("gsap.set(q('.m%d .rg'),{opacity:0,scale:1.08,transformOrigin:'50%% 50%%'});" % f["msg"])
    if compose_items:
        js.append("gsap.set(q('.sd'),{transformOrigin:'50% 50%'});")
    for (k, ct, frm, cdur, cu) in cap_anims:
        js.append("gsap.set(q('.cap%d'),{opacity:0,x:%s});" % (k, -60 if frm == "left" else 60))

    js.append("var tl=gsap.timeline({paused:true});")
    ent_end = 0.0
    if ent_mode == "rise":
        js.append("tl.to(fr,{opacity:1,duration:0.25,ease:'none'},%s);" % num(ent_start))
        js.append("tl.to(fr,{y:0,scale:1,duration:%s,ease:'power3.out'},%s);" % (num(ent_dur), num(ent_start)))
        ent_end = ent_start + ent_dur
    elif ent_mode == "fade":
        js.append("tl.to(fr,{opacity:1,duration:%s,ease:'none'},%s);" % (num(ent_dur), num(ent_start)))
        ent_end = ent_start + ent_dur
    first_start = min([L["start"] for L in layout if L["t"] is not None] or [D])
    if ent_mode != "none" and first_start < ent_end - 0.15:
        die("第一条消息（或它的 typing/compose）在 %ss 开始，画框入场 %ss 才落定——先入场再说话" % (num(first_start), num(ent_end)))

    # 打字中
    for L in layout:
        if not L.get("typing"):
            continue
        i, t1 = L["i"], L["t"]
        t0 = t1 - L["typing"]
        js.append("tl.to(q('.ty%d'),{opacity:1,scale:1,duration:0.24,ease:'power2.out'},%s);" % (i, num(t0)))
        for k in range(3):
            st = t0 + 0.18 + 0.14 * k
            win = t1 - st
            r = int(win / 0.3) - 1
            if r % 2 == 0:
                r -= 1  # 奇数次 repeat ⇒ 回到起点，打字点落回原位
            if r >= 1:
                js.append("tl.to(q('.ty%d .d%d'),{y:%s,opacity:1,duration:0.3,ease:'sine.inOut',yoyo:true,repeat:%d},%s);"
                          % (i, k, num(-dot * 0.8), r, num(st)))
        js.append("tl.to(q('.ty%d'),{opacity:0,duration:0.08,ease:'none'},%s);" % (i, num(t1)))
    # 我方输入：逐字显形，超出输入框宽就把整行往左推，发送瞬间清空
    for (L, nch, k0, shift) in compose_items:
        i, t1 = L["i"], L["t"]
        t0 = t1 - L["compose"]
        typ = max(0.2, L["compose"] - 0.25)
        per = typ / nch
        js.append("tl.to(qa('.cpt%d i'),{opacity:1,duration:0.01,ease:'none',stagger:%s},%s);" % (i, num(per, 4), num(t0)))
        if k0 is not None and shift > 0:
            js.append("tl.to(q('.cpt%d'),{x:%s,duration:%s,ease:'steps(%d)'},%s);"
                      % (i, num(-shift), num(per * (nch - k0), 3), nch - k0, num(t0 + per * k0)))
        js.append("tl.to(q('.cpw%d'),{opacity:0,duration:0.06,ease:'none'},%s);" % (i, num(t1)))
        js.append("tl.to(q('.sd'),{scale:0.86,duration:0.08,ease:'power2.out'},%s);" % num(t1 - 0.08))
        js.append("tl.to(q('.sd'),{scale:1,duration:0.22,ease:'back.out(2)'},%s);" % num(t1))
    # 出现
    for L in layout:
        if L["t"] is None:
            continue
        i, t = L["i"], L["t"]
        if L["kind"] == "system":
            js.append("tl.to(q('.m%d'),{opacity:1,y:0,duration:0.35,ease:'power2.out'},%s);" % (i, num(t)))
        else:
            js.append("tl.to(q('.m%d'),{opacity:1,duration:0.16,ease:'none'},%s);" % (i, num(t)))
            js.append("tl.to(q('.m%d'),{y:0,scale:1,duration:0.38,ease:'back.out(1.6)'},%s);" % (i, num(t)))
    # 滚动
    for (te, s) in scroll_keys:
        js.append("tl.to(ls,{y:%s,duration:0.42,ease:'power3.out'},%s);" % (num(-s), num(te)))
    # 强调：其余消息变暗、目标描边、荧光笔划过、可选镜头推近；让位时全部复原
    fx_scr, fy_scr = fx + bez, fy + bez + hd_h
    focus_out = []
    for f in fl:
        L = layout[f["msg"]]
        t = f["t"]
        end = f.get("until_auto", f["until"])
        others = ",".join(".m%d" % x["i"] for x in layout if x["i"] != f["msg"] and (x["t"] is None or x["t"] <= t))
        if others and f["dim"] < 1:
            js.append("tl.to(qa(%s),{opacity:%s,duration:0.4,ease:'power2.out'},%s);" % (json.dumps(others), num(f["dim"]), num(t)))
        if f["ring"]:
            js.append("tl.to(q('.m%d .rg'),{opacity:1,scale:1,duration:0.45,ease:'power3.out'},%s);" % (f["msg"], num(t + 0.1)))
        if f["mark"]:
            js.append("tl.to(qa('.hl%d'),{backgroundSize:'100%% 78%%',duration:0.5,ease:'power2.inOut',stagger:0.42},%s);" % (f["k"], num(t + 0.35)))
        cam = None
        if abs(f["zoom"] - 1.0) > 1e-3:
            z = f["zoom"]
            s_now = scroll_at(t)
            bx = (VW - off - L["bw"] / 2) if L["side"] == "right" else (off + L["bw"] / 2)
            sx = fx_scr + bx
            sy = fy_scr + L["top"] + L["nh"] + L["bh"] / 2 - s_now
            ax, ay = float(f["anchor"][0]), float(f["anchor"][1])
            cam = (ax * W - z * sx, ay * H - z * sy, z)
            # 推近后的自检：目标气泡不能出画；画框不能钻到当时在屏的文案卡底下
            bl, br = cam[0] + z * (sx - L["bw"] / 2), cam[0] + z * (sx + L["bw"] / 2)
            bt, bb_ = cam[1] + z * (sy - L["bh"] / 2), cam[1] + z * (sy + L["bh"] / 2)
            if bl < 0 or br > W or bt < 0 or bb_ > safe_bottom:
                die("focus[%d] 推近后目标气泡出画或落进字幕带（x %s–%s，y %s–%s）——调 anchor 或减小 zoom"
                    % (f["k"], num(bl), num(br), num(bt), num(bb_)))
            zl, zr = cam[0] + z * fx, cam[0] + z * (fx + fw)
            zt, zb = cam[1] + z * fy, cam[1] + z * (fy + fh)
            for (ck, ct, frm, cdur, cu) in cap_anims:
                ccx, ccy, ccw, cch = cap_rects[ck]
                on_screen = ct < (end if end is not None else D) and (cu is None or cu > t)
                if on_screen and zl < ccx + ccw and zr > ccx and zt < ccy + cch and zb > ccy:
                    need = (ccx + ccw + 16 + z * (sx - fx)) / W if ccx < W / 2 else None
                    die("focus[%d] 推近后画框（x %s–%s）钻到 captions[%d]（x %s–%s）底下。%s"
                        % (f["k"], num(zl), num(zr), ck, num(ccx), num(ccx + ccw),
                           ("anchor[0] 至少给到 %s，或减小 zoom" % num(need, 3)) if need else "调 anchor 或减小 zoom"))
            js.append("tl.to(cam,{x:%s,y:%s,scale:%s,duration:0.9,ease:'power2.inOut'},%s);" % (num(cam[0]), num(cam[1]), num(z, 4), num(t)))
        if end is not None:
            if others and f["dim"] < 1:
                js.append("tl.to(qa(%s),{opacity:1,duration:0.35,ease:'power2.out'},%s);" % (json.dumps(others), num(end)))
            if f["ring"]:
                js.append("tl.to(q('.m%d .rg'),{opacity:0,duration:0.3,ease:'none'},%s);" % (f["msg"], num(end)))
            if cam:
                js.append("tl.to(cam,{x:0,y:0,scale:1,duration:0.8,ease:'power2.inOut'},%s);" % num(end))
        focus_out.append({"k": f["k"], "msg": f["msg"], "t": t, "until": end, "zoom": f["zoom"],
                          "mark": L["text"][f["mark"][0]:f["mark"][1]] if f["mark"] else None})
    # 文案卡
    for (k, ct, frm, cdur, cu) in cap_anims:
        js.append("tl.to(q('.cap%d'),{opacity:1,x:0,duration:%s,ease:'power4.out'},%s);" % (k, num(cdur), num(ct)))
        if cu is not None and cu > ct + cdur:
            js.append("tl.to(q('.cap%d'),{opacity:0,x:%s,duration:0.45,ease:'power2.inOut'},%s);" % (k, -40 if frm == "left" else 40, num(cu)))
    # 终态驻留：画框轻浮（有限次、按坑位算死）
    hold_from = max([ent_end] + [L["t"] + 0.45 for L in layout if L["t"] is not None] + [f["t"] + 1.0 for f in fl])
    if spec.get("hold_drift", True) and hold_from < D - 0.3:
        hd_dur = 2.8
        js.append("tl.to(fr,{y:-4,duration:%s,ease:'sine.inOut',yoyo:true,repeat:%d},%s);" % (num(hd_dur), cycles(D - hold_from, hd_dur), num(hold_from)))
    js.append("tl.to({},{duration:0.05},%s);" % num(D))
    js.append("window.__timelines=window.__timelines||{};")
    js.append("window.__timelines[ID]=tl;")
    js_src = "\n".join(js)

    out = []
    out.append('<template id="p">')
    out.append('<div data-composition-id="%s" data-width="%d" data-height="%d" style="position:absolute;inset:0;overflow:hidden;background:transparent;font-family:%s;">'
               % (esc(cid), W, H, f_sans))
    if P["canvas"]:
        out.append('<div class="bgfill" style="position:absolute;inset:0;background:%s;"></div>' % P["canvas"])
    out.append("<style>%s</style>" % "\n".join(css))
    out.append('<div class="cam"><div class="fr" style="%s"><div class="scr" style="%s">' % (fr_style, scr_style))
    out.append(hd_html)
    out.append('<div class="vp" style="top:%spx;width:%spx;height:%spx;"><div class="ls">%s</div></div>' % (num(hd_h), num(VW), num(VH), "".join(items)))
    out.append(ib_html)
    out.append("</div></div></div>")
    out.extend(cap_html)
    out.append('<script src="%s"></script>' % GSAP_CDN)
    out.append("<script>(function(){\n%s\n})();</script>" % js_src)
    out.append("</div>")
    out.append("</template>")
    out_html = "\n".join(out)
    if len(out_html) > max_chars:
        die("产物 %d 字符超过预算 %d（减图片消息 / 降 quality）" % (len(out_html), max_chars))

    summary = {
        "id": cid,
        "chars": len(out_html),
        "duration": D,
        "frame": {"kind": kind, "x": fx, "y": fy, "w": fw, "h": fh},
        "viewport": {"x": fx_scr, "y": fy_scr, "w": VW, "h": VH, "max_bubble_w": max_bw},
        "entrance_end": round(ent_end, 3),
        "messages": [{"i": L["i"], "kind": L["kind"], "from": L.get("from_"), "t": L["t"],
                      "lines": len(L["lines"]) if L.get("lines") else None, "top": round(L["top"], 1),
                      "h": round(L["h"], 1)} for L in layout],
        "scroll": [{"t": round(t, 3), "y": round(-s, 1)} for (t, s) in scroll_keys],
        "focus": focus_out,
        "hold_from": round(hold_from, 3),
    }
    return out_html, summary


def main():
    ap = argparse.ArgumentParser(description="chat-replay 颗粒生成器")
    ap.add_argument("spec")
    ap.add_argument("--out", required=True, help="产物 html 路径（直接写最终落点）")
    ap.add_argument("--max-chars", type=int, default=1900000, help="产物字符预算（客户端 2,000,000 硬上限之下）")
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
