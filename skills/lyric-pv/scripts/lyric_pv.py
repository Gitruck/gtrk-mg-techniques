#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""lyric-pv · 文字 PV（歌词排版）颗粒生成器（gsap-emit v1 合规）

用法：
    python lyric_pv.py <spec.json> --out <particle.html>

每一句歌词是一个「镜头」：布局 × 登场 × 停留 × 退场 × 装饰 × 背景 × 镜头运动，句与句之间可加转场。
没指定的部件按 seed 确定性抽选（同 seed 同结果），并守几条构成规则：相邻两句不用同一布局、
同一段（part）共用一套底色、重复出现的歌词（副歌）回到第一次的样子、强调词放大换色。

脚本只做机械活：读歌词与时码（JSON 或 LRC）、估字宽断行定字号、排每个字的位置、抽选部件、发射 GSAP 时间线、自检。
唱什么、每句什么时候出、哪句是重点、整体什么调子，由调用方决定。

思路借鉴 JIZURA（文字 PV 自动构成，MIT）把表现拆成「布局 / 登场 / 保持 / 退场 / 装饰 / 背景 / 镜头 / 转场」
几类部件再组合的做法；本脚本的部件全部自写，未复制其代码。
确定性：抽选用 random.Random(seed)，结果以字面量写进时间线；颗粒里无随机、无时钟。
依赖：仅标准库。
"""
import argparse
import html
import io
import json
import math
import os
import random
import re
import sys

GSAP_CDN = "https://lib.baomitu.com/gsap/3.13.0/gsap.min.js"
FONT_SANS = "'Source Han Sans CN','Source Han Sans SC','Noto Sans SC','思源黑体',sans-serif"
FONT_SERIF = "'Source Han Serif CN','Source Han Serif SC','Noto Serif SC','思源宋体',serif"

ASCII_EM = [
    0.225, 0.343, 0.518, 0.57, 0.57, 0.939, 0.705, 0.298, 0.355, 0.355, 0.484, 0.57, 0.298, 0.357, 0.298, 0.39,
    0.57, 0.57, 0.57, 0.57, 0.57, 0.57, 0.57, 0.57, 0.57, 0.57, 0.298, 0.298, 0.57, 0.57, 0.57, 0.491,
    0.972, 0.622, 0.667, 0.646, 0.699, 0.6, 0.566, 0.701, 0.74, 0.309, 0.55, 0.663, 0.557, 0.83, 0.734, 0.754,
    0.647, 0.754, 0.655, 0.608, 0.61, 0.733, 0.594, 0.894, 0.596, 0.553, 0.607, 0.355, 0.39, 0.355, 0.57, 0.562,
    0.614, 0.575, 0.629, 0.517, 0.63, 0.565, 0.345, 0.578, 0.621, 0.287, 0.288, 0.575, 0.298, 0.942, 0.624, 0.614,
    0.63, 0.63, 0.409, 0.48, 0.396, 0.62, 0.545, 0.828, 0.526, 0.544, 0.49, 0.355, 0.281, 0.355, 0.57,
]
EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF\U0001F1E6-\U0001F1FF✀-➿️‍]")
NO_ROW_START = set("，。、；：？！）》」』”’…—,.;:?!)]}")

STYLES = {
    "ink": {"bg": "#0F0F11", "bg_alt": "#1A1716", "ink": "#F3EFE7", "accent": "#E5483A", "sub": "#8C8780",
            "band_ink": "#0F0F11", "pattern": "stripes", "serif": False},
    "paper": {"bg": "#EEE8DC", "bg_alt": "#E3DACA", "ink": "#1B1A18", "accent": "#C7372C", "sub": "#7D7468",
              "band_ink": "#EEE8DC", "pattern": "grid", "serif": True},
    "mono": {"bg": "#FAFAFA", "bg_alt": "#ECECEC", "ink": "#111111", "accent": "#2E5BFF", "sub": "#8A8A8A",
             "band_ink": "#FAFAFA", "pattern": "none", "serif": False},
    "transparent": {"bg": None, "bg_alt": None, "ink": "#FFFFFF", "accent": "#FFD23F", "sub": "#E6E6E6",
                    "band_ink": "#111111", "pattern": "none", "serif": False},
}

LAYOUTS = ["center", "stack", "vertical", "split", "band", "scatter", "frame", "ticker"]
ENTERS = ["rise", "pop", "wipe", "type", "scramble", "drop", "zoom", "part"]
EXITS = ["fade", "cut", "wipe", "scatter", "sink", "stretch"]
HOLDS = ["drift", "still", "float", "tilt"]
DECORS = ["marks", "rule", "circle", "slash", "dots", "bigno", "index"]
TRANSITIONS = ["none", "flash", "wipe"]
ENTER_DUR = {"rise": 0.5, "pop": 0.45, "wipe": 0.4, "type": None, "scramble": 0.55, "drop": 0.55, "zoom": 0.35, "part": 0.45}
EXIT_DUR = {"fade": 0.3, "cut": 0.0, "wipe": 0.3, "scatter": 0.4, "sink": 0.35, "stretch": 0.3}
DECOY_POOL = "#%&*+=?/<>[]{}01ABXYZ"


def die(msg):
    sys.stderr.write("lyric-pv: " + msg + "\n")
    sys.exit(2)


def num(v, nd=2):
    s = ("%." + str(nd) + "f") % float(v)
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def esc(s):
    return html.escape(str(s), quote=True)


def cem(ch):
    o = ord(ch)
    if 0x20 <= o <= 0x7E:
        return ASCII_EM[o - 0x20]
    return 0.62 if (o < 0x2E80 and ch not in "—…“”‘’") else 1.0


def em_len(s):
    return sum(cem(c) for c in s) * 1.04


def parse_lrc(path):
    out = []
    with io.open(path, "r", encoding="utf-8-sig") as f:
        for ln in f:
            tags = re.findall(r"\[(\d+):(\d+(?:\.\d+)?)\]", ln)
            text = re.sub(r"\[[^\]]*\]", "", ln).strip()
            for mm, ss in tags:
                out.append({"t": int(mm) * 60 + float(ss), "text": text})
    out.sort(key=lambda x: x["t"])
    return out


def split_rows(text, n):
    """把一句拆成 n 行：优先在空格 / 标点后断，否则按字数均分；标点不落行首。"""
    if n <= 1 or len(text) < 2:
        return [text]
    cuts = []
    L = len(text)
    for k in range(1, n):
        target = L * k / n
        best, arg = 1e9, None
        for j in range(1, L):
            if text[j] in NO_ROW_START:
                continue
            bonus = -1.5 if (text[j - 1] in " ，、,。" or text[j] == " ") else 0
            d = abs(j - target) + bonus
            if d < best and (not cuts or j > cuts[-1]):
                best, arg = d, j
        if arg:
            cuts.append(arg)
    rows, prev = [], 0
    for c in cuts + [L]:
        rows.append(text[prev:c].strip())
        prev = c
    return [r for r in rows if r]


# ———————————————————————— 主体 ————————————————————————

def build(spec, spec_dir, max_chars):
    cid = spec.get("id") or die("spec.id 必填（= data-composition-id）")
    W, H = int(spec.get("width", 1920)), int(spec.get("height", 1080))
    D = float(spec.get("duration") or 0) or die("spec.duration 必填（秒）")
    u = min(W / 1920.0, H / 1080.0) if W >= H else W / 1080.0
    seed = int(spec.get("seed", 0))
    rng = random.Random(seed)
    sty_name = spec.get("style", "ink")
    if sty_name not in STYLES:
        die("style 只收 %s" % " / ".join(STYLES))
    P = dict(STYLES[sty_name])
    for k, v in (spec.get("palette") or {}).items():
        if k not in P or k in ("pattern", "serif"):
            die("palette 不认识的键：%s（可用：bg / bg_alt / ink / accent / sub / band_ink）" % k)
        P[k] = v
    pattern = spec.get("pattern", P["pattern"])
    if pattern not in ("stripes", "grid", "none"):
        die("pattern 只收 stripes / grid / none")
    fonts = spec.get("fonts") or {}
    font = fonts.get("serif", FONT_SERIF) if spec.get("serif", P["serif"]) else fonts.get("sans", FONT_SANS)
    koma = int(spec.get("koma", 2))
    if koma not in (1, 2, 3):
        die("koma 只收 1（逐帧）/ 2（一拍二，12 张/秒）/ 3（一拍三，8 张/秒）")
    numbers = bool(spec.get("decor_numbers", True))
    margin = float(spec.get("margin", 0.06))

    # —— 歌词 ——
    if spec.get("lrc"):
        p = spec["lrc"] if os.path.isabs(spec["lrc"]) else os.path.join(spec_dir, spec["lrc"])
        if not os.path.exists(p):
            die("找不到 LRC：" + p)
        raw = parse_lrc(p)
        overrides = {int(k): v for k, v in (spec.get("line_overrides") or {}).items()}
        lines = [dict(x, **overrides.get(i, {})) for i, x in enumerate(raw) if x["text"]]
    else:
        lines = [dict(x) for x in spec.get("lines") or []]
    if not lines:
        die("lines（或 lrc）至少一句")
    for i, ln in enumerate(lines):
        if not ln.get("text"):
            die("lines[%d] 没有 text" % i)
        # 「|」= 调用方指定的断行处（脚本不做中文分词，按字数均分会拆开词）；显示时去掉
        if "|" in ln["text"]:
            ln["rows_hint"] = [r for r in ln["text"].split("|") if r]
            ln["text"] = ln["text"].replace("|", "")
        if EMOJI_RE.search(ln["text"]):
            die("lines[%d] 含 emoji：%r——渲染端字体表没有彩色 emoji" % (i, ln["text"]))
        ln["t"] = float(ln.get("t", -1))
        if ln["t"] < 0 or ln["t"] >= D:
            die("lines[%d].t=%s 越出 [0, duration)" % (i, num(ln["t"])))
    for i in range(1, len(lines)):
        if lines[i]["t"] <= lines[i - 1]["t"]:
            die("lines[%d] 的 t 不比上一句晚——歌词时码要严格递增" % i)
    for i, ln in enumerate(lines):
        nxt = lines[i + 1]["t"] if i + 1 < len(lines) else D
        ln["end"] = min(float(ln.get("end", nxt)), nxt)
        if ln["end"] - ln["t"] < 0.3:
            die("lines[%d]「%s」只在屏上 %ss（< 0.3s）——太快读不了，并到上一句或拉开时码" % (i, ln["text"], num(ln["end"] - ln["t"])))
        cps = len(re.sub(r"\s", "", ln["text"])) / max(1e-6, ln["end"] - ln["t"])
        if cps > float(spec.get("max_cps", 20)):
            die("lines[%d]「%s」%d 个字只在屏上 %ss（%.0f 字/秒 > %s）——读不完，拆成几句"
                % (i, ln["text"], len(ln["text"]), num(ln["end"] - ln["t"]), cps, num(spec.get("max_cps", 20))))
        if ln.get("emph") and ln["emph"] not in ln["text"]:
            die("lines[%d].emph=%r 在这句里找不到（逐字匹配）" % (i, ln["emph"]))

    # —— 抽选部件（确定性） ——
    choose = lambda pool, avoid=None: rng.choice([x for x in pool if x != avoid] or pool)
    allowed_layouts = spec.get("layouts") or LAYOUTS
    allowed_enters = spec.get("enters") or ENTERS
    allowed_exits = spec.get("exits") or EXITS
    allowed_decors = spec.get("decors") or DECORS
    for lst, pool, nm in ((allowed_layouts, LAYOUTS, "layouts"), (allowed_enters, ENTERS, "enters"),
                          (allowed_exits, EXITS, "exits"), (allowed_decors, DECORS, "decors")):
        for x in lst:
            if x not in pool:
                die("%s 里不认识的部件 %r（可用：%s）" % (nm, x, " / ".join(pool)))
    seen = {}
    prev_layout = None
    parts = []
    for i, ln in enumerate(lines):
        key = ln["text"]
        if key in seen and not any(k in ln for k in ("layout", "enter", "exit", "decor", "hold")):
            src = seen[key]
            for k in ("layout", "enter", "exit", "hold", "decor", "camera_seed"):
                ln.setdefault(k, src[k])
            if src.get("emph") and not ln.get("emph"):
                ln["emph"] = src["emph"]  # 副歌回到第一次的样子，强调词也一样
            ln["repeat_of"] = src["i"]
        else:
            for k, pool in (("layout", LAYOUTS), ("enter", ENTERS), ("exit", EXITS), ("hold", HOLDS)):
                if k in ln and ln[k] not in pool:
                    die("lines[%d].%s=%r 不认识（可用：%s）" % (i, k, ln[k], " / ".join(pool)))
            pool_l = [x for x in allowed_layouts if x not in ("band", "ticker")] if ln.get("emph") else allowed_layouts
            if ln.get("emph") and ln.get("layout") in ("band", "ticker"):
                die("lines[%d] 有强调词，但布局 %s 不支持强调（band / ticker 整行同字号）" % (i, ln["layout"]))
            ln.setdefault("layout", choose(pool_l or allowed_layouts, prev_layout))
            ln.setdefault("enter", choose(allowed_enters))
            ln.setdefault("exit", choose(allowed_exits))
            ln.setdefault("hold", choose(HOLDS))
            if "decor" not in ln:
                k = rng.choice([0, 1, 1, 2])
                ln["decor"] = rng.sample(list(allowed_decors), min(k, len(allowed_decors)))
            for d_ in ln["decor"]:
                if d_ not in DECORS:
                    die("lines[%d].decor 里不认识的 %r（可用：%s）" % (i, d_, " / ".join(DECORS)))
            ln["camera_seed"] = rng.randrange(1 << 30)
            seen[key] = dict(ln, i=i)
        if not numbers:
            ln["decor"] = [d_ for d_ in ln["decor"] if d_ not in ("bigno", "index")]
        if ln["layout"] == "ticker":  # 跑马灯整行横贯画面，只留不压字的装饰
            ln["decor"] = [d_ for d_ in ln["decor"] if d_ in ("marks", "bigno", "index")]
        prev_layout = ln["layout"]
        pt = ln.get("part", parts[-1] if parts else 0)
        parts.append(pt)
        ln["part"] = pt
        ln["transition"] = ln.get("transition", spec.get("transition", "none") if i else "none")
        if ln["transition"] not in TRANSITIONS:
            die("lines[%d].transition 只收 none / flash / wipe" % i)

    # —— 版面：字号、断行、每个字的位置 ——
    x0, x1 = W * margin, W * (1 - margin)
    y0, y1 = H * margin, H * (1 - margin)
    avail_w = x1 - x0
    base_fs = float(spec.get("font_size", 112)) * u
    min_fs = float(spec.get("min_font_size", 46)) * u
    css_rows = []
    for i, ln in enumerate(lines):
        text, lay = ln["text"], ln["layout"]
        emph = ln.get("emph")
        er = (text.find(emph), text.find(emph) + len(emph)) if emph else None
        chars = []  # (ch, x, y, fs, rot, is_emph)

        def place_rows(rows, fs, align, top, left=None, right=None, row_gap=1.28, indent=0.0):
            out, y, idx = [], top, 0
            for r_i, row in enumerate(rows):
                rw = 0.0  # 计入强调字放大后的行宽
                for c_i, ch in enumerate(row):
                    gpos = idx + c_i
                    big = er and er[0] <= gpos_map[gpos] < er[1]
                    rw += cem(ch) * 1.04 * fs * (1.55 if big else 1.0)
                if align == "center":
                    x = W / 2.0 - rw / 2.0
                elif align == "left":
                    x = left + indent * r_i * W
                else:
                    x = right - rw
                row_fs = max(fs * (1.55 if (er and any(er[0] <= gpos_map[idx + c] < er[1] for c in range(len(row)))) else 1.0), fs)
                for c_i, ch in enumerate(row):
                    gpos = idx + c_i
                    big = bool(er and er[0] <= gpos_map[gpos] < er[1])
                    cfs = fs * (1.55 if big else 1.0)
                    out.append((ch, x, y + (row_fs - cfs) * 0.85, cfs, 0.0, big))
                    x += cem(ch) * 1.04 * cfs
                y += row_fs * row_gap
                idx += len(row)
            return out, y - top

        # gpos_map：行内字序 → 原文下标（断行时去掉的空格不占位）
        def rows_and_map(rows):
            m, p = [], 0
            for row in rows:
                for ch in row:
                    p = text.find(ch, p)
                    m.append(p)
                    p += 1
            return m

        if lay in ("center", "frame", "split", "stack"):
            fs = base_fs * (0.82 if lay == "frame" else 1.0)
            nrows = 1
            limit = avail_w * (0.62 if lay == "split" else (0.7 if lay == "frame" else 0.92))
            hint = ln.get("rows_hint")
            if hint:
                longest = max(em_len(r) for r in hint)
                while longest * fs * (1.25 if emph else 1.0) > limit and fs * 0.9 >= min_fs:
                    fs *= 0.9
                if longest * fs * (1.25 if emph else 1.0) > limit:
                    die("lines[%d] 用「|」指定的一行「%s」太长，最小字号也放不下" % (i, max(hint, key=em_len)))
                nrows = len(hint)
            while not hint and em_len(text) / nrows * fs * (1.25 if emph else 1.0) > limit:
                if fs * 0.9 >= min_fs:
                    fs *= 0.9
                elif nrows < 3:
                    nrows += 1
                    fs = base_fs
                else:
                    die("lines[%d]「%s」太长：缩到最小字号 %spx、拆成 3 行仍放不下——拆成两句" % (i, text, num(min_fs)))
            if lay == "stack" and not hint:
                nrows = max(nrows, 2 if len(text) >= 4 else 1)
            if lay == "split" and not hint:
                nrows = max(nrows, 2 if len(text) >= 2 else 1)
            rows = hint if hint else split_rows(text, nrows)
            gpos_map = rows_and_map(rows)
            block_h = sum(fs * 1.28 * (1.55 if emph else 1) for _ in rows)
            if lay in ("center", "frame"):
                chars, bh = place_rows(rows, fs, "center", H / 2.0 - block_h / 2.0)
            elif lay == "stack":
                chars, bh = place_rows(rows, fs, "left", H * 0.5 - block_h / 2.0, left=x0 + W * 0.04, indent=0.06)
            else:
                half = len(rows) // 2 or 1
                a_rows, b_rows = rows[:half], rows[half:]
                gpos_map = rows_and_map(rows)
                ca, _ = place_rows(a_rows, fs, "left", H * 0.24, left=x0 + W * 0.03)
                m_save = gpos_map
                gpos_map = m_save[len("".join(a_rows)):]
                cb, _ = place_rows(b_rows, fs, "right", H * 0.56, right=x1 - W * 0.03)
                gpos_map = m_save
                chars = ca + cb
        elif lay == "vertical":
            fs = base_fs * 0.9
            ncol = 1
            while len(text) / ncol * fs * 1.06 > (y1 - y0) * 0.9:
                if fs * 0.9 >= min_fs:
                    fs *= 0.9
                elif ncol < 3:
                    ncol += 1
                    fs = base_fs * 0.9
                else:
                    die("lines[%d]「%s」太长，竖排三列仍放不下——拆成两句" % (i, text))
            cols = ln["rows_hint"] if ln.get("rows_hint") else split_rows(text, ncol)
            gpos_map = rows_and_map(cols)
            xr = W * 0.66
            k = 0
            for c_i, col in enumerate(cols):
                x = xr - c_i * fs * 1.45
                total = sum(fs * 1.06 * (1.4 if (er and er[0] <= gpos_map[k + j] < er[1]) else 1) for j in range(len(col)))
                y = H / 2.0 - total / 2.0
                for j, ch in enumerate(col):
                    big = bool(er and er[0] <= gpos_map[k + j] < er[1])
                    cfs = fs * (1.4 if big else 1.0)
                    rot = 90.0 if (ch in "ー—…（）()「」-~" or ord(ch) < 0x2E80) else 0.0
                    chars.append((ch, x - (cfs - fs) / 2.0, y, cfs, rot, big))
                    y += cfs * 1.06
                k += len(col)
        elif lay == "band":
            fs = base_fs * 0.86
            while em_len(text) * fs > W * 0.84:
                fs *= 0.92
                if fs < min_fs:
                    die("lines[%d]「%s」太长，放不进一条带——拆成两句或换布局" % (i, text))
            gpos_map = list(range(len(text)))
            chars, _ = place_rows([text], fs, "center", H * 0.52 - fs * 0.62)
            ln["band"] = (H * 0.52, fs * 1.9)
        elif lay == "scatter":
            # 散排：字号各自抖一点、强调字放大，横向按每个字的实际宽度累加（不按字序均分，免得放大的字互相压住）
            fs = base_fs * 0.95
            r2 = random.Random(ln["camera_seed"])
            sizes = []
            for j, ch in enumerate(text):
                big = bool(er and er[0] <= j < er[1])
                sizes.append((ch, r2.uniform(0.82, 1.18) + (0.45 if big else 0), big, r2.random(), r2.uniform(-9, 9)))
            gap = 0.28
            while True:
                span = sum(cem(ch) * 1.04 * fs * k for (ch, k, b, ph, rot) in sizes) + gap * fs * (len(sizes) - 1)
                if span <= avail_w * 0.88:
                    break
                fs *= 0.92
                if fs < min_fs:
                    die("lines[%d]「%s」太长，散排放不下——拆成两句或换布局" % (i, text))
            x = W / 2.0 - span / 2.0
            for j, (ch, k, big, ph, rot) in enumerate(sizes):
                cfs = fs * k
                y = H / 2.0 - cfs / 2.0 + math.sin(j * 1.3 + ph * 2) * H * 0.09
                chars.append((ch, x, y, cfs, rot, big))
                x += cem(ch) * 1.04 * cfs + gap * fs
        else:  # ticker
            fs = base_fs * 1.05
            seq = (text + "　·　") * 4
            gpos_map = [j % (len(text) + 3) for j in range(len(seq))]
            x = x0
            for j, ch in enumerate(seq):
                real = (j % (len(text) + 3)) < len(text)
                chars.append((ch, x, H / 2.0 - fs * 0.62, fs, 0.0, False) if real else (ch, x, H / 2.0 - fs * 0.62, fs, 0.0, None))
                x += cem(ch) * 1.04 * fs
            ln["ticker_w"] = x - x0
            ln["ticker_unit"] = em_len(text + "　·　") / 1.04 * 1.04 * fs
        # 自检：字不出安全框（跑马灯本来就出画，竖排看列）
        if lay != "ticker":
            for (ch, x, y, fs_, rot, big) in chars:
                w = cem(ch) * 1.04 * fs_
                if x < x0 - 2 or x + w > x1 + 2 or y < y0 - 2 or y + fs_ * 1.2 > y1 + 2:
                    die("lines[%d]「%s」的「%s」排出了安全框（x %s–%s，y %s–%s）——字号太大或句子太长"
                        % (i, text, ch, num(x), num(x + w), num(y), num(y + fs_ * 1.2)))
        ln["chars"] = chars
        ln["fs"] = fs

    # —— 时间 ——
    for i, ln in enumerate(lines):
        n_real = sum(1 for c in ln["chars"] if c[5] is not None)
        ed = ENTER_DUR[ln["enter"]]
        if ln["enter"] == "type":
            ed = min(0.06 * n_real, 0.9)
        avail = ln["end"] - ln["t"]
        xd = EXIT_DUR[ln["exit"]] if i + 1 < len(lines) else 0.0
        ed = min(ed, max(0.12, avail * 0.45))
        xd = min(xd, max(0.0, avail * 0.3))
        ln["enter_dur"], ln["exit_dur"] = ed, xd
        ln["exit_at"] = ln["end"] - xd
    if lines[-1]["end"] > D:
        die("最后一句到 %ss 才结束，超出 duration" % num(lines[-1]["end"]))

    # ———————————————————————— HTML ————————————————————————
    R = '[data-composition-id="%s"]' % cid
    css = [
        "%s .L{position:absolute;left:0;top:0;width:%dpx;height:%dpx;opacity:0;}" % (R, W, H),
        # .tx 必须铺满画幅：里面的字全是绝对定位，容器若是 0 高，clip-path 会把整句裁没、缩放原点也会跑到顶边
        "%s .tx{position:absolute;left:0;top:0;width:%dpx;height:%dpx;}" % (R, W, H),
        "%s .c,%s .d{position:absolute;white-space:pre;line-height:1;color:%s;font-weight:900;}" % (R, R, P["ink"]),
        "%s .c.e{color:%s;}" % (R, P["accent"]),
        "%s .d{opacity:0;color:%s;}" % (R, P["accent"]),
        "%s .band{position:absolute;left:%spx;width:%spx;background:%s;}" % (R, num(-W * 0.1), num(W * 1.2), P["accent"]),
        "%s .band .c{color:%s;}" % (R, P["band_ink"]),
        "%s .lbl{position:absolute;font-weight:500;letter-spacing:0.12em;color:%s;white-space:pre;}" % (R, P["sub"]),
        "%s .bigno{position:absolute;font-weight:900;line-height:1;color:%s;opacity:0.08;white-space:pre;}" % (R, P["ink"]),
    ]
    if sty_name == "transparent":
        css.append("%s .c{-webkit-text-stroke:%spx rgba(0,0,0,0.55);paint-order:stroke fill;}" % (R, num(6 * u)))

    body = []
    for i, ln in enumerate(lines):
        parts_html = []
        if ln["layout"] == "band":
            by, bh = ln["band"]
            parts_html.append('<div class="band" style="top:%spx;height:%spx;transform:rotate(-5deg);transform-origin:50%% 50%%;">' % (num(by - bh / 2), num(bh)))
        decoys = []
        r3 = random.Random(ln["camera_seed"] + 99)
        for j, (ch, x, y, fs_, rot, big) in enumerate(ln["chars"]):
            yy = (y - (ln["band"][0] - ln["band"][1] / 2)) if ln["layout"] == "band" else y
            st = "left:%spx;top:%spx;font-size:%spx;" % (num(x), num(yy), num(fs_))
            if rot:
                st += "transform:rotate(%sdeg);" % num(rot, 1)
            cls = "c" + (" e" if big else "") + (" f" if big is None else "")
            parts_html.append('<span class="%s ch%d" style="%s">%s</span>' % (cls, j, st, esc(ch)))
            if ln["enter"] == "scramble" and big is not None and ch.strip():
                for k in range(3):
                    dc = DECOY_POOL[r3.randrange(len(DECOY_POOL))] if r3.random() < 0.6 else ln["text"][r3.randrange(len(ln["text"]))]
                    decoys.append('<span class="d dc%d_%d" style="%s">%s</span>' % (j, k, st, esc(dc)))
        parts_html.extend(decoys)
        if ln["layout"] == "band":
            parts_html.append("</div>")
        # 装饰
        xs = [c[1] for c in ln["chars"] if c[5] is not None] or [W / 2]
        ys = [c[2] for c in ln["chars"] if c[5] is not None] or [H / 2]
        fe = max([c[3] for c in ln["chars"]] or [ln["fs"]])
        bx0, bx1 = min(xs), max(c[1] + cem(c[0]) * 1.04 * c[3] for c in ln["chars"] if c[5] is not None) if ln["layout"] != "ticker" else W * 0.9
        by0, by1 = min(ys), max(ys) + fe * 1.15
        if ln["layout"] == "ticker":
            bx0 = W * 0.1
        dec = []
        for dname in ln["decor"]:
            if dname == "marks":
                m = W * margin * 0.6
                L_ = 40 * u
                d_ = "M%s %sv-%sh%s M%s %sh%sv%s M%s %sv%sh-%s M%s %sh-%sv-%s" % (
                    num(m), num(m + L_), num(0), num(L_), num(W - m - L_), num(m), num(L_), num(L_),
                    num(W - m), num(H - m - L_), num(L_), num(L_), num(m + L_), num(H - m), num(L_), num(L_))
                d_ = "M%s %sV%sH%s M%s %sH%sV%s M%s %sV%sH%s M%s %sH%sV%s" % (
                    num(m), num(m + L_), num(m), num(m + L_), num(W - m - L_), num(m), num(W - m), num(m + L_),
                    num(W - m), num(H - m - L_), num(H - m), num(W - m - L_), num(m + L_), num(H - m), num(m), num(H - m - L_))
                dec.append('<svg class="dec dm" viewBox="0 0 %d %d" style="position:absolute;inset:0;width:100%%;height:100%%;"><path d="%s" fill="none" stroke="%s" stroke-width="%s"/></svg>' % (W, H, d_, P["sub"], num(3 * u)))
            elif dname == "rule":
                dec.append('<div class="dec dr dr1" style="position:absolute;left:%spx;top:%spx;width:%spx;height:%spx;background:%s;"></div>'
                           % (num(bx0), num(by0 - 26 * u), num(bx1 - bx0), num(3 * u), P["accent"]))
                dec.append('<div class="dec dr dr2" style="position:absolute;left:%spx;top:%spx;width:%spx;height:%spx;background:%s;"></div>'
                           % (num(bx0), num(by1 + 18 * u), num((bx1 - bx0) * 0.4), num(3 * u), P["sub"]))
            elif dname == "circle":
                rr = max(bx1 - bx0, by1 - by0) * 0.62
                cx_, cy_ = (bx0 + bx1) / 2, (by0 + by1) / 2
                circ = 2 * math.pi * rr
                dec.append('<svg class="dec dcirc" viewBox="0 0 %d %d" style="position:absolute;inset:0;width:100%%;height:100%%;"><circle cx="%s" cy="%s" r="%s" fill="none" stroke="%s" stroke-width="%s" stroke-dasharray="%s" stroke-dashoffset="%s" transform="rotate(-90 %s %s)"/></svg>'
                           % (W, H, num(cx_), num(cy_), num(rr), P["accent"], num(2.5 * u), num(circ), num(circ), num(cx_), num(cy_)))
            elif dname == "slash":
                sx, sy = bx1 + 24 * u, by0
                d_ = " ".join("M%s %sL%s %s" % (num(sx + k * 22 * u), num(sy + 60 * u), num(sx + k * 22 * u + 34 * u), num(sy)) for k in range(3))
                dec.append('<svg class="dec ds" viewBox="0 0 %d %d" style="position:absolute;inset:0;width:100%%;height:100%%;"><path d="%s" stroke="%s" stroke-width="%s" stroke-linecap="round"/></svg>' % (W, H, d_, P["accent"], num(6 * u)))
            elif dname == "dots":
                rr_ = 5 * u
                d_ = "".join("M%s %sa%s %s 0 1 0 %s 0a%s %s 0 1 0 -%s 0" % (num(bx0 + k * 26 * u), num(by1 + 40 * u), num(rr_), num(rr_), num(2 * rr_), num(rr_), num(rr_), num(2 * rr_)) for k in range(12))
                dec.append('<svg class="dec dd" viewBox="0 0 %d %d" style="position:absolute;inset:0;width:100%%;height:100%%;"><path d="%s" fill="%s"/></svg>' % (W, H, d_, P["sub"]))
            elif dname == "bigno":
                dec.append('<div class="dec bigno" style="right:%spx;bottom:%spx;font-size:%spx;">%02d</div>' % (num(W * margin), num(H * margin * 0.4), num(360 * u), i + 1))
            elif dname == "index":
                mm, ss = divmod(ln["t"], 60)
                dec.append('<div class="dec lbl" style="left:%spx;top:%spx;font-size:%spx;">No.%02d　%02d:%05.2f</div>' % (num(W * margin), num(H * (1 - margin) - 30 * u), num(22 * u), i + 1, int(mm), ss))
        if ln["layout"] == "frame":
            pad_x, pad_y = 56 * u, 30 * u
            bx, by, bw, bh = bx0 - pad_x, by0 - pad_y, (bx1 - bx0) + 2 * pad_x, (by1 - by0) + 2 * pad_y
            dec.append('<div class="dec dfr" style="position:absolute;left:%spx;top:%spx;width:%spx;height:%spx;border:%spx solid %s;box-sizing:border-box;"></div>' % (num(bx), num(by), num(bw), num(bh), num(2 * u), P["ink"]))
            if numbers:
                dec.append('<div class="dec lbl dfl" style="left:%spx;top:%spx;font-size:%spx;">%02d</div>' % (num(bx), num(by - 34 * u), num(22 * u), i + 1))
        body.append('<div class="L L%d" data-i="%d" data-t="%s" data-end="%s" data-enter="%s" data-exit="%s" data-layout="%s" data-ed="%s" data-text="%s">%s<div class="tx">%s</div></div>'
                    % (i, i, num(ln["t"], 3), num(ln["end"], 3), ln["enter"], ln["exit"], ln["layout"], num(ln["enter_dur"], 3), esc(ln["text"]), "".join(dec), "".join(parts_html)))

    # ———————————————————————— 时间线 ————————————————————————
    js = []
    js.append("var ID=%s;" % json.dumps(cid, ensure_ascii=False))
    js.append("var ROOT='[data-composition-id=\"'+ID+'\"]';")
    js.append("var q=function(s){return document.querySelector(ROOT+' '+s);};")
    js.append("var qa=function(s){return document.querySelectorAll(ROOT+' '+s);};")
    js.append("var KOMA=%d;" % koma)
    # 一拍二 / 一拍三：把缓动的进度量化到 24/KOMA 张每秒，得到文字 PV 那种「一顿一顿」的动作
    js.append("function Q(n,d){var e=gsap.parseEase(n);if(KOMA<=1)return e;var k=Math.max(1,Math.round(d*24/KOMA));return function(p){return e(Math.floor(p*k+1e-6)/k);};}")
    js.append("var tl=gsap.timeline({paused:true});")
    if P["bg"]:
        js.append("gsap.set(q('.bg'),{backgroundColor:%s});" % json.dumps(P["bg"]))
    cur_bg = P["bg"]
    for i, ln in enumerate(lines):
        L = "q('.L%d')" % i
        chars = "qa('.L%d .c:not(.f)')" % i
        t, ed, xd, ex = ln["t"], ln["enter_dur"], ln["exit_dur"], ln["exit_at"]
        n_real = sum(1 for c in ln["chars"] if c[5] is not None)
        stg = min(0.05, ed * 0.6 / max(1, n_real))
        js.append("tl.set(%s,{opacity:1},%s);" % (L, num(t, 3)))
        # 背景：段落切换时换底色
        if P["bg"]:
            want = P["bg"] if (ln["part"] % 2 == 0) else P["bg_alt"]
            if want != cur_bg:
                js.append("tl.to(q('.bg'),{backgroundColor:%s,duration:0.2,ease:'none'},%s);" % (json.dumps(want), num(t, 3)))
                cur_bg = want
        # 转场
        if ln["transition"] == "flash":
            js.append("tl.fromTo(q('.flash'),{opacity:0.85},{opacity:0,duration:0.18,ease:'power2.out',immediateRender:false},%s);" % num(t, 3))
        elif ln["transition"] == "wipe":
            js.append("tl.fromTo(q('.wipe'),{x:%s},{x:%s,duration:0.36,ease:Q('power3.inOut',0.36),immediateRender:false},%s);" % (num(-W * 1.1), num(W * 1.1), num(t - 0.18, 3)))
        # 登场
        e = ln["enter"]
        if e == "rise":
            js.append("gsap.set(%s,{y:%s,opacity:0});" % (chars, num(60 * u)))
            js.append("tl.to(%s,{y:0,opacity:1,duration:%s,ease:Q('power3.out',%s),stagger:%s},%s);" % (chars, num(ed, 3), num(ed, 3), num(stg, 4), num(t, 3)))
        elif e == "pop":
            js.append("gsap.set(%s,{scale:0,opacity:0,transformOrigin:'50%% 60%%'});" % chars)
            js.append("tl.to(%s,{scale:1,opacity:1,duration:%s,ease:Q('back.out(2.4)',%s),stagger:%s},%s);" % (chars, num(ed, 3), num(ed, 3), num(stg, 4), num(t, 3)))
        elif e == "drop":
            js.append("gsap.set(%s,{y:%s,opacity:0});" % (chars, num(-140 * u)))
            js.append("tl.to(%s,{y:0,opacity:1,duration:%s,ease:Q('bounce.out',%s),stagger:%s},%s);" % (chars, num(ed, 3), num(ed, 3), num(stg, 4), num(t, 3)))
        elif e == "zoom":
            js.append("gsap.set(q('.L%d .tx'),{scale:2.4,opacity:0,transformOrigin:'50%% 50%%'});" % i)
            js.append("tl.to(q('.L%d .tx'),{scale:1,opacity:1,duration:%s,ease:Q('expo.out',%s)},%s);" % (i, num(ed, 3), num(ed, 3), num(t, 3)))
        elif e == "wipe":
            js.append("gsap.set(q('.L%d .tx'),{clipPath:'inset(0%% 100%% 0%% 0%%)'});" % i)
            js.append("tl.to(q('.L%d .tx'),{clipPath:'inset(0%% 0%% 0%% 0%%)',duration:%s,ease:Q('power2.inOut',%s)},%s);" % (i, num(ed, 3), num(ed, 3), num(t, 3)))
        elif e == "part":
            js.append("gsap.set(%s,{x:function(k,el,a){return (k<a.length/2?-1:1)*%s;},opacity:0});" % (chars, num(160 * u)))
            js.append("tl.to(%s,{x:0,opacity:1,duration:%s,ease:Q('power4.out',%s)},%s);" % (chars, num(ed, 3), num(ed, 3), num(t, 3)))
        elif e == "type":
            js.append("gsap.set(%s,{opacity:0});" % chars)
            js.append("tl.to(%s,{opacity:1,duration:0.01,ease:'none',stagger:%s},%s);" % (chars, num(ed / max(1, n_real), 4), num(t, 3)))
        elif e == "scramble":
            js.append("gsap.set(%s,{opacity:0});" % chars)
            fr = 1 / 15.0
            for j, c in enumerate(ln["chars"]):
                if c[5] is None or not c[0].strip():
                    continue
                st = t + min(j * stg, ed - 4 * fr)
                for k in range(3):
                    js.append("tl.set(q('.L%d .dc%d_%d'),{opacity:1},%s);tl.set(q('.L%d .dc%d_%d'),{opacity:0},%s);" % (i, j, k, num(st + k * fr, 3), i, j, k, num(st + (k + 1) * fr, 3)))
                js.append("tl.set(q('.L%d .ch%d'),{opacity:1},%s);" % (i, j, num(st + 3 * fr, 3)))
        # 跑马灯：停留期间整行向左流
        if ln["layout"] == "ticker":
            dist = ln["ticker_unit"]
            js.append("tl.to(q('.L%d .tx'),{x:%s,duration:%s,ease:'none'},%s);" % (i, num(-dist), num(max(0.1, ex - t), 3), num(t, 3)))
        # 停留：镜头
        hold_from, hold_len = t + ed, max(0.05, ex - (t + ed))
        hd = ln["hold"]
        cam = "q('.L%d')" % i
        if hd == "drift":
            js.append("tl.fromTo(%s,{scale:1},{scale:1.05,duration:%s,ease:'none',transformOrigin:'50%% 50%%',immediateRender:false},%s);" % (cam, num(ex - t, 3), num(t, 3)))
        elif hd == "tilt":
            js.append("tl.fromTo(%s,{rotation:0},{rotation:%s,duration:%s,ease:'sine.inOut',transformOrigin:'50%% 50%%',immediateRender:false},%s);"
                      % (cam, "-1.6" if ln["camera_seed"] % 2 else "1.6", num(ex - t, 3), num(t, 3)))
        elif hd == "float":
            rep = max(0, int(math.ceil(hold_len / 0.9)) - 1)
            js.append("tl.to(%s,{y:%s,duration:0.9,ease:'sine.inOut',yoyo:true,repeat:%d},%s);" % (cam, num(-8 * u), rep, num(hold_from, 3)))
        # 装饰入场
        if ln["decor"] or ln["layout"] == "frame":
            js.append("gsap.set(qa('.L%d .dec'),{opacity:0});" % i)
            js.append("tl.to(qa('.L%d .dec'),{opacity:1,duration:0.25,ease:Q('power2.out',0.25),stagger:0.06},%s);" % (i, num(t + 0.05, 3)))
            if "rule" in ln["decor"]:
                js.append("gsap.set(qa('.L%d .dr'),{scaleX:0,transformOrigin:'0%% 50%%'});" % i)
                js.append("tl.to(qa('.L%d .dr'),{scaleX:1,duration:%s,ease:Q('power3.out',%s),stagger:0.08},%s);" % (i, num(ed + 0.2, 3), num(ed + 0.2, 3), num(t, 3)))
            if "circle" in ln["decor"]:
                js.append("tl.to(q('.L%d .dcirc circle'),{attr:{'stroke-dashoffset':0},duration:%s,ease:Q('power2.inOut',%s)},%s);" % (i, num(ed + 0.3, 3), num(ed + 0.3, 3), num(t, 3)))
        # 退场（最后一句不退场：终态驻留）
        if i + 1 < len(lines):
            x_ = ln["exit"]
            if x_ == "cut" or xd <= 0:
                js.append("tl.set(%s,{opacity:0},%s);" % (L, num(ln["end"], 3)))
            else:
                if x_ == "fade":
                    js.append("tl.to(q('.L%d .tx'),{y:%s,opacity:0,duration:%s,ease:Q('power2.in',%s)},%s);" % (i, num(-40 * u), num(xd, 3), num(xd, 3), num(ex, 3)))
                elif x_ == "wipe":
                    js.append("tl.to(q('.L%d .tx'),{clipPath:'inset(0%% 0%% 0%% 100%%)',duration:%s,ease:Q('power2.in',%s)},%s);" % (i, num(xd, 3), num(xd, 3), num(ex, 3)))
                elif x_ == "scatter":
                    js.append("tl.to(%s,{x:function(k){return Math.cos(k*2.39996)*%s;},y:function(k){return Math.sin(k*2.39996)*%s;},rotation:function(k){return ((k*37)%%60)-30;},opacity:0,duration:%s,ease:Q('power3.in',%s)},%s);"
                              % (chars, num(420 * u), num(300 * u), num(xd, 3), num(xd, 3), num(ex, 3)))
                elif x_ == "sink":
                    js.append("tl.to(%s,{y:%s,opacity:0,duration:%s,ease:Q('power2.in',%s),stagger:%s},%s);" % (chars, num(120 * u), num(xd * 0.7, 3), num(xd * 0.7, 3), num(xd * 0.3 / max(1, n_real), 4), num(ex, 3)))
                elif x_ == "stretch":
                    js.append("tl.to(q('.L%d .tx'),{scaleX:2.6,scaleY:0.3,opacity:0,transformOrigin:'50%% 50%%',duration:%s,ease:Q('expo.in',%s)},%s);" % (i, num(xd, 3), num(xd, 3), num(ex, 3)))
                if ln["decor"] or ln["layout"] == "frame":
                    js.append("tl.to(qa('.L%d .dec'),{opacity:0,duration:%s,ease:'none'},%s);" % (i, num(max(0.08, xd), 3), num(ex, 3)))
                js.append("tl.set(%s,{opacity:0},%s);" % (L, num(ln["end"], 3)))
    if pattern != "none" and P["bg"]:
        js.append("tl.fromTo(q('.pat'),{x:0},{x:%s,duration:%s,ease:'none',immediateRender:false},0);" % (num(-80 * u), num(D, 3)))
    js.append("tl.to({},{duration:0.05},%s);" % num(D, 3))
    js.append("window.__timelines=window.__timelines||{};")
    js.append("window.__timelines[ID]=tl;")

    out = []
    out.append('<template id="p">')
    out.append('<div data-composition-id="%s" data-width="%d" data-height="%d" style="position:absolute;inset:0;overflow:hidden;background:transparent;font-family:%s;">' % (esc(cid), W, H, font))
    if P["bg"]:
        out.append('<div class="bgfill bg" style="position:absolute;inset:0;background:%s;"></div>' % P["bg"])
        if pattern == "stripes":
            out.append('<div class="pat" style="position:absolute;left:0;top:0;width:%spx;height:%spx;opacity:0.06;background:repeating-linear-gradient(-45deg,%s 0px,%s %spx,rgba(0,0,0,0) %spx,rgba(0,0,0,0) %spx);"></div>'
                       % (num(W + 200), num(H), P["ink"], P["ink"], num(2 * u), num(2 * u), num(28 * u)))
        elif pattern == "grid":
            out.append('<div class="pat" style="position:absolute;left:0;top:0;width:%spx;height:%spx;opacity:0.08;background-image:linear-gradient(%s 1px,rgba(0,0,0,0) 1px),linear-gradient(90deg,%s 1px,rgba(0,0,0,0) 1px);background-size:%spx %spx;"></div>'
                       % (num(W + 200), num(H), P["ink"], P["ink"], num(80 * u), num(80 * u)))
    out.append("<style>%s</style>" % "\n".join(css))
    out.append("".join(body))
    out.append('<div class="wipe" style="position:absolute;left:0;top:0;width:%dpx;height:%dpx;background:%s;transform:translateX(%spx);"></div>' % (W, H, P["accent"], num(-W * 1.2)))
    out.append('<div class="flash" style="position:absolute;inset:0;background:#FFFFFF;opacity:0;"></div>')
    out.append('<script src="%s"></script>' % GSAP_CDN)
    out.append("<script>(function(){\n%s\n})();</script>" % "\n".join(js))
    out.append("</div>")
    out.append("</template>")
    out_html = "\n".join(out)
    if len(out_html) > max_chars:
        die("产物 %d 字符超过预算 %d" % (len(out_html), max_chars))
    summary = {
        "id": cid, "chars": len(out_html), "duration": D, "style": sty_name, "koma": koma, "seed": seed,
        "lines": [{"i": i, "t": ln["t"], "end": round(ln["end"], 3), "text": ln["text"], "layout": ln["layout"], "enter": ln["enter"],
                   "hold": ln["hold"], "exit": ln["exit"] if i + 1 < len(lines) else "(驻留)", "decor": ln["decor"],
                   "transition": ln["transition"], "part": ln["part"], "font_px": round(ln["fs"], 1),
                   "repeat_of": ln.get("repeat_of")} for i, ln in enumerate(lines)],
    }
    return out_html, summary


def main():
    ap = argparse.ArgumentParser(description="lyric-pv 颗粒生成器")
    ap.add_argument("spec")
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-chars", type=int, default=1900000)
    a = ap.parse_args()
    for st in (sys.stdout, sys.stderr):
        if hasattr(st, "reconfigure"):
            st.reconfigure(encoding="utf-8")
    with io.open(a.spec, "r", encoding="utf-8") as f:
        spec = json.load(f)
    out_html, summary = build(spec, os.path.dirname(os.path.abspath(a.spec)), a.max_chars)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
    with io.open(a.out, "w", encoding="utf-8", newline="\n") as f:
        f.write(out_html)
    summary["out"] = os.path.abspath(a.out)
    sys.stdout.write(json.dumps(summary, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
