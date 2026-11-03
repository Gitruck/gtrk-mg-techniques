#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""candle-chart · K 线图动画颗粒生成器（gsap-emit v1 合规）

用法：
    python candle_chart.py <spec.json> --out <particle.html>

spec 字段见 ../references/spec.md。脚本只做机械活：读数据、校验 OHLC、按需合并周期 / 截取区间、算均线、
定纵轴范围与刻度、排蜡烛 / 成交量 / 均线 / 标注的几何、给标注留头顶空间并查互相避让、发射 GSAP 时间线。
画哪段行情、强调哪根、价位线画在哪、什么时候长出来由调用方决定。

动作：蜡烛按时间逐根生长（从开盘价向收盘价与影线两端展开），成交量同步升起；均线用一块裁切矩形
按蜡烛的 x 线性推进，笔尖始终跟在最新长出的那根上；标注（点位、水平价位线、区间高亮）按 spec 的时刻落下。

确定性：无随机、无时钟；同 spec 逐字节一致。
依赖：仅标准库。
"""
import argparse
import csv
import html
import io
import json
import math
import os
import re
import sys

GSAP_CDN = "https://lib.baomitu.com/gsap/3.13.0/gsap.min.js"
FONT_SANS = "'Source Han Sans CN','Source Han Sans SC','Noto Sans SC','思源黑体',sans-serif"

# 思源黑体 CN Medium 的 ASCII 0x20..0x7E 步进宽（em），估标注标签宽用
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
        "canvas": "#0E1014", "grid": "rgba(255,255,255,0.07)", "axis": "#8C93A0", "ink": "#EEF0F4", "ink_sub": "#8C93A0",
        "up": "#F0524F", "down": "#20B27A", "accent": "#F2C14E", "band": "rgba(242,193,78,0.13)",
        "chip": "rgba(22,25,32,0.94)", "chip_line": "rgba(255,255,255,0.14)", "level": "#9AA3B2",
        "ma": ["#F2C14E", "#5AA9F0", "#C77DDB", "#EF8A4C"],
    },
    "light": {
        "canvas": "#F6F6F4", "grid": "rgba(16,20,28,0.07)", "axis": "#6B7280", "ink": "#14171C", "ink_sub": "#6B7280",
        "up": "#D63A3A", "down": "#14955C", "accent": "#C98A12", "band": "rgba(201,138,18,0.12)",
        "chip": "rgba(255,255,255,0.96)", "chip_line": "rgba(16,20,28,0.14)", "level": "#5B6472",
        "ma": ["#D89A1F", "#2F7FD1", "#9B59B6", "#E07B39"],
    },
}
PRESETS["transparent"] = dict(PRESETS["dark"], canvas=None)

COLS = {
    "date": ("date", "d", "time", "日期", "时间"),
    "open": ("open", "o", "开盘", "开盘价"),
    "high": ("high", "h", "最高", "最高价"),
    "low": ("low", "l", "最低", "最低价"),
    "close": ("close", "c", "收盘", "收盘价"),
    "volume": ("volume", "v", "vol", "成交量"),
}


def die(msg):
    sys.stderr.write("candle-chart: " + msg + "\n")
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


# ———————————————————————— 数据 ————————————————————————

def load_rows(spec, spec_dir):
    rows = []
    if spec.get("csv"):
        path = spec["csv"] if os.path.isabs(spec["csv"]) else os.path.join(spec_dir, spec["csv"])
        if not os.path.exists(path):
            die("找不到 csv：" + path)
        with io.open(path, "r", encoding="utf-8-sig", newline="") as f:
            rd = csv.reader(f)
            head = [h.strip().lower() for h in next(rd)]
            idx = {}
            for key, names in COLS.items():
                for k, h in enumerate(head):
                    if h in names:
                        idx[key] = k
                        break
            for key in ("date", "open", "high", "low", "close"):
                if key not in idx:
                    die("csv 缺列：%s（表头可写 %s）" % (key, " / ".join(COLS[key])))
            for ln, r in enumerate(rd, start=2):
                if not r or not "".join(r).strip():
                    continue
                try:
                    rows.append({"d": r[idx["date"]].strip(), "o": float(r[idx["open"]]), "h": float(r[idx["high"]]),
                                 "l": float(r[idx["low"]]), "c": float(r[idx["close"]]),
                                 "v": float(r[idx["volume"]]) if "volume" in idx and r[idx["volume"]].strip() else None,
                                 "_src": "csv 第 %d 行" % ln, "_raw": r})
                except (ValueError, IndexError):
                    die("csv 第 %d 行读不成数：%r" % (ln, r))
    elif spec.get("data"):
        for k, r in enumerate(spec["data"]):
            if isinstance(r, dict):
                rows.append({"d": str(r.get("d", k)), "o": float(r["o"]), "h": float(r["h"]), "l": float(r["l"]),
                             "c": float(r["c"]), "v": float(r["v"]) if r.get("v") is not None else None, "_src": "data[%d]" % k})
            else:
                if len(r) < 5:
                    die("data[%d] 至少 [日期, 开, 高, 低, 收]" % k)
                rows.append({"d": str(r[0]), "o": float(r[1]), "h": float(r[2]), "l": float(r[3]), "c": float(r[4]),
                             "v": float(r[5]) if len(r) > 5 and r[5] is not None else None, "_src": "data[%d]" % k})
    else:
        die("data 或 csv 必给其一")
    for r in rows:
        for key in ("o", "h", "l", "c"):
            if not math.isfinite(r[key]):
                die("%s 的 %s 不是有限数" % (r["_src"], key))
        if r["h"] < max(r["o"], r["c"]) - 1e-9 or r["l"] > min(r["o"], r["c"]) + 1e-9:
            die("%s 不是合法 K 线：高 %s 必须 ≥ 开 / 收，低 %s 必须 ≤ 开 / 收（开 %s 收 %s）"
                % (r["_src"], num(r["h"], 4), num(r["l"], 4), num(r["o"], 4), num(r["c"], 4)))
        if r["v"] is not None and r["v"] < 0:
            die("%s 成交量为负" % r["_src"])
    return rows


def find_index(rows, at, what):
    if isinstance(at, int):
        if not (0 <= at < len(rows)):
            die("%s 下标 %d 越界（共 %d 根）" % (what, at, len(rows)))
        return at
    for k, r in enumerate(rows):
        if r["d"] == str(at):
            return k
    die("%s 找不到日期 %r（逐字匹配数据里的日期列；合并周期后日期是每组第一根的日期）" % (what, at))


def nice_step(span, target):
    """取 1 / 2 / 2.5 / 5 × 10^k 里离理想步长最近的（按比例），刻度数落在 target 附近。"""
    raw = span / max(1, target)
    mag = 10 ** math.floor(math.log10(raw))
    return min((m * mag for m in (1, 2, 2.5, 5, 10)), key=lambda c: abs(math.log(c / raw)))


# ———————————————————————— 主体 ————————————————————————

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
    conv = spec.get("convention", "cn")
    if conv not in ("cn", "us"):
        die("convention 只收 cn（红涨绿跌）/ us（绿涨红跌）")
    if conv == "us":
        P["up"], P["down"] = P["down"], P["up"]
    for k, v in (spec.get("palette") or {}).items():
        if k not in P:
            die("palette 不认识的键：%s（可用：%s）" % (k, " / ".join(sorted(P))))
        P[k] = v
    font = (spec.get("fonts") or {}).get("sans", FONT_SANS)

    # —— 数据：读 → 截取 → 合并周期 ——
    rows = load_rows(spec, spec_dir)
    sl = spec.get("slice")
    if sl:
        a = find_index(rows, sl[0], "slice[0]")
        b = find_index(rows, sl[1], "slice[1]")
        if b < a:
            die("slice 起点在终点之后")
        rows = rows[a:b + 1]
    agg = int(spec.get("aggregate", 1))
    if agg > 1:
        merged = []
        for k in range(0, len(rows), agg):
            g = rows[k:k + agg]
            vs = [r["v"] for r in g if r["v"] is not None]
            merged.append({"d": g[0]["d"], "o": g[0]["o"], "h": max(r["h"] for r in g), "l": min(r["l"] for r in g),
                           "c": g[-1]["c"], "v": sum(vs) if vs else None, "_src": g[0]["_src"]})
        rows = merged
    n = len(rows)
    if n < 2:
        die("至少 2 根 K 线")

    # —— 版面 ——
    lay = spec.get("layout") or {}
    show_vol = bool(spec.get("volume", True)) and all(r["v"] is not None for r in rows)
    if spec.get("volume") and not show_vol:
        die("volume=true 但数据缺成交量列")
    px = float(lay.get("x", 120))
    pw = float(lay.get("w", W - 120 - 240))
    py = float(lay.get("y", 150))
    total_h = float(lay.get("h", safe_bottom - 38 - py))
    gap_v = 20.0
    vh = float(lay.get("volume_h", round(total_h * 0.2))) if show_vol else 0.0
    ph = total_h - (vh + gap_v if show_vol else 0.0)
    vy = py + ph + gap_v
    date_y = py + total_h + 26
    if px < 0 or py < 60 or px + pw + 120 > W + 0.5 or ph < 120:
        die("版面越界或价格区太矮：x=%s y=%s w=%s 价格区高=%s" % (num(px), num(py), num(pw), num(ph)))
    if date_y + 8 > safe_bottom + 0.5:
        die("日期标签底沿 %s 落进字幕带（> %s）——调小 layout.h 或上移" % (num(date_y + 8), num(safe_bottom)))
    slot = pw / n
    body_w = max(1.0, slot * float(spec.get("body", 0.62)))
    if slot < 2.5:
        die("%d 根挤在 %spx 里，每根只有 %spx——看不清。用 aggregate 合并周期（如 5 = 周线）或 slice 截取" % (n, num(pw), num(slot)))
    wick_w = max(1.0, min(2.0, slot * 0.14))
    cx = lambda i: px + slot * (i + 0.5)

    # —— 小数位 ——
    dec = spec.get("decimals")
    if dec is None:
        dec = 0
        for r in rows:
            for key in ("o", "h", "l", "c"):
                s = repr(round(r[key], 4))
                if "." in s:
                    dec = max(dec, len(s.split(".")[1].rstrip("0")))
        dec = min(dec, 4)
    fmt = lambda p: ("%." + str(int(dec)) + "f") % p

    # —— 时间 ——
    grow = spec.get("grow") or {}
    g_start = float(grow.get("start", 0.6))
    g_end = float(grow.get("end", min(D * 0.55, g_start + 6.0)))
    if g_end <= g_start:
        die("grow.end 必须晚于 grow.start")
    if g_end > D - 0.5:
        die("生长结束 %ss 离颗粒末尾 %ss 不到 0.5s——没有驻留" % (num(g_end), num(D)))
    gd = max(0.12, min(0.45, (g_end - g_start) / n * 4))
    step = (g_end - g_start - gd) / max(1, n - 1)
    if step <= 0:
        die("生长窗口太短：%d 根要在 %ss 里长完" % (n, num(g_end - g_start)))
    t_of = lambda i: g_start + step * i          # 第 i 根开始长
    done_of = lambda i: t_of(i) + gd             # 第 i 根长完

    # —— 标注预处理（要先知道它们，才能给纵轴留头顶 / 脚下空间）——
    mk_fs = int(spec.get("label_size", 22))
    chip_h = round(mk_fs * 1.3 + 16)
    leader = 30.0
    marks = []
    for k, m in enumerate(spec.get("marks") or []):
        i = find_index(rows, m.get("at"), "marks[%d].at" % k)
        kind = m.get("kind", "high")
        if kind not in ("high", "low", "close", "price"):
            die("marks[%d].kind 只收 high / low / close / price" % k)
        if kind == "price" and m.get("price") is None:
            die("marks[%d] kind=price 要给 price" % k)
        price = {"high": rows[i]["h"], "low": rows[i]["l"], "close": rows[i]["c"]}.get(kind, m.get("price"))
        price = float(price)
        txt = m.get("text") or fmt(price)
        if EMOJI_RE.search(txt):
            die("marks[%d].text 含 emoji（渲染端字体表没有）" % k)
        # 标签里写了与价位同精度的小数，就必须是这根的实际价位——凭印象写数是最常见的错
        nums = [x for x in re.findall(r"\d+\.\d+", txt) if dec and len(x.split(".")[1]) == int(dec)]
        if nums and fmt(price) not in nums:
            die("marks[%d].text 写的是 %s，但 %s 这根的%s实际是 %s——数从数据里取（看 stdout 的 extremes）"
                % (k, " / ".join(nums), rows[i]["d"], {"high": "最高价", "low": "最低价", "close": "收盘价"}.get(kind, "标注价位"), fmt(price)))
        t = float(m.get("t", done_of(i) + 0.2))
        if t < done_of(i) + 0.05:
            die("marks[%d] 在 %ss 落下，但第 %d 根（%s）%ss 才长完——还没画出来的蜡烛没法标" % (k, num(t), i, rows[i]["d"], num(done_of(i))))
        below = m.get("side", "below" if kind == "low" else "above") == "below"
        # 标签放在这根蜡烛的影线之外（收盘 / 指定价位的点在实体里时，标签不压实体）
        ext = min(rows[i]["l"], price) if below else max(rows[i]["h"], price)
        marks.append({"k": k, "i": i, "kind": kind, "price": price, "ext": ext, "text": txt, "t": t, "below": below,
                      "w": text_px(txt, mk_fs) + 26})

    # —— 纵轴范围：数据 ±8%，再给标注留出空间 ——
    lo = min(r["l"] for r in rows)
    hi = max(r["h"] for r in rows)
    levels_in = spec.get("levels") or []
    for lv in levels_in:
        if lv.get("price") is None:
            die("levels[] 每项要有 price")
    yr = spec.get("y_range")
    if yr:
        lo, hi = float(yr[0]), float(yr[1])
        if hi <= lo:
            die("y_range 上限要大于下限")
    else:
        span = max(hi - lo, abs(hi) * 1e-3, 1e-9)
        lo, hi = lo - span * 0.08, hi + span * 0.08
        need = leader + chip_h + 8
        for _ in range(3):
            r_ = need / ph
            for m in marks:
                if not m["below"]:
                    hi = max(hi, (m["ext"] - r_ * lo) / (1 - r_))
                else:
                    lo = min(lo, (m["ext"] - r_ * hi) / (1 - r_))
    yv = lambda p: py + ph * (hi - p) / (hi - lo)
    for r in rows:
        if r["h"] > hi + 1e-9 or r["l"] < lo - 1e-9:
            die("%s 超出 y_range [%s, %s]" % (r["_src"], fmt(lo), fmt(hi)))

    # —— 刻度 ——
    stp = nice_step(hi - lo, int(spec.get("ticks", 5)))
    ticks = []
    v = math.ceil(lo / stp - 1e-9) * stp
    while v <= hi + 1e-9:
        ticks.append(round(v, 10))
        v += stp
    st_s = ("%.6f" % stp).rstrip("0").rstrip(".")
    tick_dec = len(st_s.split(".")[1]) if "." in st_s else 0
    tfmt = lambda p: ("%." + str(tick_dec) + "f") % p

    # —— 均线 ——
    ma_cfg = spec.get("ma", [5, 20])
    if isinstance(ma_cfg, dict):
        periods = ma_cfg.get("periods", [5, 20])
        ma_at = ma_cfg.get("at")
        ma_dur = float(ma_cfg.get("dur", 1.6))
    else:
        periods, ma_at, ma_dur = ma_cfg, None, 1.6
    ma_paths = []
    for k, p in enumerate(periods):
        p = int(p)
        if p < 2 or p >= n:
            die("均线周期 %d 不合法（2 ≤ 周期 < 根数 %d）" % (p, n))
        pts = []
        acc = 0.0
        for i, r in enumerate(rows):
            acc += r["c"]
            if i >= p:
                acc -= rows[i - p]["c"]
            if i >= p - 1:
                pts.append((cx(i), yv(acc / p)))
        d = "M" + " L".join("%s %s" % (num(x, 1), num(y, 1)) for x, y in pts)
        ma_paths.append((p, P["ma"][k % len(P["ma"])], d))

    # —— 标注几何与避让 ——
    for m in marks:
        x0 = cx(m["i"])
        y0 = yv(m["price"])
        cw = m["w"]
        cxl = min(max(x0 - cw / 2, px), px + pw - cw)
        ye = yv(m["ext"])
        if m["below"]:
            ctop = ye + leader
        else:
            ctop = ye - leader - chip_h
        # 标签盖住了几根蜡烛（影线范围）：不算错，但报出来，交给调用方决定换 side 还是接受
        covers = 0
        for j, r in enumerate(rows):
            if cxl < cx(j) + body_w / 2 and cx(j) - body_w / 2 < cxl + cw and ctop < yv(r["l"]) and yv(r["h"]) < ctop + chip_h:
                covers += 1
        m.update(x0=x0, y0=y0, cxl=cxl, ctop=ctop, covers=covers)
        if ctop < py - 2 or ctop + chip_h > py + ph + 2:
            die("marks[%d] 的标签出了价格区（%s）——给了 y_range 时头顶空间不够，放宽 y_range 或换 side" % (m["k"], "上沿" if ctop < py else "下沿"))
    for a in marks:
        for b in marks:
            if a["k"] < b["k"] and a["cxl"] < b["cxl"] + b["w"] + 6 and b["cxl"] < a["cxl"] + a["w"] + 6 \
                    and a["ctop"] < b["ctop"] + chip_h + 4 and b["ctop"] < a["ctop"] + chip_h + 4:
                die("marks[%d] 与 marks[%d] 的标签叠在一起——换 side、删一个，或拉开它们指的蜡烛" % (a["k"], b["k"]))

    levels = []
    for k, lv in enumerate(levels_in):
        p = float(lv["price"])
        if not (lo <= p <= hi):
            die("levels[%d].price=%s 不在纵轴范围 [%s, %s] 内" % (k, fmt(p), fmt(lo), fmt(hi)))
        t = float(lv.get("t", g_end + 0.3))
        txt = lv.get("text")
        if txt and EMOJI_RE.search(txt):
            die("levels[%d].text 含 emoji" % k)
        levels.append({"k": k, "p": p, "y": yv(p), "t": t, "text": txt, "dash": lv.get("style", "dashed") == "dashed"})

    ranges = []
    for k, rg in enumerate(spec.get("ranges") or []):
        a = find_index(rows, rg.get("from"), "ranges[%d].from" % k)
        b = find_index(rows, rg.get("to"), "ranges[%d].to" % k)
        if b < a:
            die("ranges[%d] from 在 to 之后" % k)
        t = float(rg.get("t", done_of(b) + 0.2))
        if t < done_of(b) + 0.05:
            die("ranges[%d] 在 %ss 亮起，但区间末根 %ss 才长完" % (k, num(t), num(done_of(b))))
        txt = rg.get("text")
        if txt and EMOJI_RE.search(txt):
            die("ranges[%d].text 含 emoji" % k)
        ranges.append({"k": k, "a": a, "b": b, "t": t, "text": txt,
                       "x": cx(a) - slot / 2, "w": slot * (b - a + 1)})

    # —— 标题 ——
    hd = spec.get("header") or {}
    for key in ("title", "sub"):
        if hd.get(key) and EMOJI_RE.search(hd[key]):
            die("header.%s 含 emoji" % key)

    # ———————————————————————— SVG ————————————————————————
    R = '[data-composition-id="%s"]' % cid
    sid = re.sub(r"[^A-Za-z0-9_-]", "_", cid)
    svg = []
    svg.append('<svg class="cc" viewBox="0 0 %d %d" width="%d" height="%d" data-plot="%s,%s,%s,%s" '
               'data-safe="%s" data-ma="%s" style="position:absolute;left:0;top:0;" font-family="%s">'
               % (W, H, W, H, num(px), num(py), num(pw), num(ph), num(safe_bottom),
                  "follow" if ma_at is None else "after,%s,%s" % (num(ma_at, 4), num(ma_dur, 4)), esc(font)))
    svg.append('<defs><clipPath id="ccma-%s"><rect class="mac" x="%s" y="%s" width="0" height="%s"/></clipPath></defs>'
               % (sid, num(px), num(py - 6), num(ph + 12)))
    # 网格与坐标轴（一条 path，铁律 8）
    gd_path = []
    for v in ticks:
        y = yv(v)
        gd_path.append("M%s %sH%s" % (num(px), num(y, 1), num(px + pw)))
    if show_vol:
        gd_path.append("M%s %sH%s" % (num(px), num(vy + vh, 1), num(px + pw)))
    svg.append('<g class="ax">')
    svg.append('<path d="%s" stroke="%s" stroke-width="1" fill="none"/>' % ("".join(gd_path), P["grid"]))
    tag_ys = [lv["y"] for lv in levels] + ([yv(rows[-1]["c"])] if spec.get("last_tag", True) else [])
    shown_ticks = [v for v in ticks if not any(abs(yv(v) - ty) < 26 for ty in tag_ys)]  # 让位给价位标签，免得露半个数字
    if len(shown_ticks) < 2:
        die("纵轴只剩 %d 个刻度没被价位标签挡住——观众读不出比例。加大 ticks 或减少价位线" % len(shown_ticks))
    for v in shown_ticks:
        svg.append('<text class="yt" x="%s" y="%s" fill="%s" font-size="20" dominant-baseline="central">%s</text>'
                   % (num(px + pw + 16), num(yv(v), 1), P["axis"], tfmt(v)))
    n_dates = int(spec.get("date_labels", 6))
    every = max(1, int(math.ceil(n / float(max(1, n_dates)))))
    for i in range(0, n, every):
        svg.append('<text class="xt" x="%s" y="%s" fill="%s" font-size="19" text-anchor="middle">%s</text>'
                   % (num(cx(i), 1), num(date_y), P["axis"], esc(rows[i]["d"])))
    svg.append("</g>")
    # 区间高亮（在蜡烛之下）
    for rg in ranges:
        svg.append('<g class="rg rg%d" data-t="%s"><rect x="%s" y="%s" width="%s" height="%s" fill="%s"/>'
                   % (rg["k"], num(rg["t"], 3), num(rg["x"], 1), num(py), num(rg["w"], 1), num(ph + (gap_v + vh if show_vol else 0)), P["band"]))
        if rg["text"]:
            svg.append('<text x="%s" y="%s" fill="%s" font-size="%d" font-weight="600" text-anchor="middle">%s</text>'
                       % (num(rg["x"] + rg["w"] / 2, 1), num(py + 30), P["accent"], mk_fs, esc(rg["text"])))
        svg.append("</g>")
    # 成交量
    if show_vol:
        vmax = max(r["v"] for r in rows) or 1.0
        svg.append('<g class="vol" opacity="0.55">')
        for i, r in enumerate(rows):
            h = vh * 0.94 * r["v"] / vmax
            svg.append('<rect class="v" x="%s" y="%s" width="%s" height="%s" fill="%s"/>'
                       % (num(cx(i) - body_w / 2, 2), num(vy + vh - h, 2), num(body_w, 2), num(max(h, 0.5), 2),
                          P["up"] if r["c"] >= r["o"] else P["down"]))
        svg.append("</g>")
    # 蜡烛
    svg.append('<g class="cs" data-gd="%s">' % num(gd, 4))
    for i, r in enumerate(rows):
        up = r["c"] >= r["o"]
        yo, yc = yv(r["o"]), yv(r["c"])
        top, bh = min(yo, yc), max(abs(yo - yc), 1.0)
        svg.append('<g class="c %s" data-i="%d" data-t="%s" data-oy="%s" data-ohlc="%s,%s,%s,%s">'
                   '<line x1="%s" y1="%s" x2="%s" y2="%s" stroke-width="%s"/><rect x="%s" y="%s" width="%s" height="%s"/></g>'
                   % ("u" if up else "d", i, num(t_of(i), 4), num(yo, 2), repr(r["o"]), repr(r["h"]), repr(r["l"]), repr(r["c"]),
                      num(cx(i), 2), num(yv(r["h"]), 2), num(cx(i), 2), num(yv(r["l"]), 2), num(wick_w),
                      num(cx(i) - body_w / 2, 2), num(top, 2), num(body_w, 2), num(bh, 2)))
    svg.append("</g>")
    # 均线（裁切推进）
    svg.append('<g class="mas" clip-path="url(#ccma-%s)" fill="none" stroke-width="2.4" stroke-linejoin="round" stroke-linecap="round">' % sid)
    for (p, col, d) in ma_paths:
        svg.append('<path class="ma ma%d" d="%s" stroke="%s"/>' % (p, d, col))
    svg.append("</g>")
    # 均线图例
    if ma_paths:
        lx = px
        leg = []
        for (p, col, d) in ma_paths:
            leg.append('<rect x="%s" y="%s" width="18" height="3" fill="%s"/><text x="%s" y="%s" fill="%s" font-size="19" dominant-baseline="central">MA%d</text>'
                       % (num(lx), num(py - 16), col, num(lx + 24), num(py - 14.5), P["ink_sub"], p))
            lx += 24 + text_px("MA%d" % p, 19) + 22
        svg.append('<g class="leg">%s</g>' % "".join(leg))
    # 水平价位线
    for lv in levels:
        tag = fmt(lv["p"])
        tw = text_px(tag, 20) + 16
        svg.append('<g class="lv lv%d" data-t="%s"><path class="lvl" d="M%s %sH%s" stroke="%s" stroke-width="1.6"%s fill="none"/>'
                   '<g class="lvt"><rect x="%s" y="%s" width="%s" height="30" rx="4" fill="%s"/>'
                   '<text x="%s" y="%s" fill="%s" font-size="20" dominant-baseline="central">%s</text></g>'
                   % (lv["k"], num(lv["t"], 3), num(px), num(lv["y"], 1), num(px + pw), P["level"], ' stroke-dasharray="9 7"' if lv["dash"] else "",
                      num(px + pw + 8), num(lv["y"] - 15, 1), num(tw), P["level"], num(px + pw + 16), num(lv["y"], 1),
                      P["canvas"] or "#0E1014", tag))
        if lv["text"]:
            svg.append('<text class="lvx" x="%s" y="%s" fill="%s" font-size="%d" font-weight="600">%s</text>'
                       % (num(px + 8), num(lv["y"] - 10, 1), P["level"], mk_fs - 2, esc(lv["text"])))
        svg.append("</g>")
    # 点位标注
    for m in marks:
        ly0 = m["y0"] + (6 if m["below"] else -6)
        ly1 = m["ctop"] if m["below"] else m["ctop"] + chip_h
        svg.append('<g class="mk mk%d" data-t="%s"><line class="mkl" x1="%s" y1="%s" x2="%s" y2="%s" stroke="%s" stroke-width="1.6"/>'
                   '<circle class="mkd" cx="%s" cy="%s" r="5" fill="%s" stroke="%s" stroke-width="2"/>'
                   '<g class="mkc"><rect x="%s" y="%s" width="%s" height="%d" rx="6" fill="%s" stroke="%s" stroke-width="1"/>'
                   '<text x="%s" y="%s" fill="%s" font-size="%d" font-weight="600" dominant-baseline="central">%s</text></g></g>'
                   % (m["k"], num(m["t"], 3), num(m["x0"], 1), num(ly0, 1), num(m["x0"], 1), num(ly1, 1), P["accent"],
                      num(m["x0"], 1), num(m["y0"], 1), P["accent"], P["canvas"] or "#0E1014",
                      num(m["cxl"], 1), num(m["ctop"], 1), num(m["w"], 1), chip_h, P["chip"], P["chip_line"],
                      num(m["cxl"] + 13, 1), num(m["ctop"] + chip_h / 2, 1), P["ink"], mk_fs, esc(m["text"])))
    # 最新价标签
    last = rows[-1]
    last_on = bool(spec.get("last_tag", True))
    if last_on:
        ly = yv(last["c"])
        tag = fmt(last["c"])
        col = P["up"] if last["c"] >= last["o"] else P["down"]
        svg.append('<g class="lt"><path d="M%s %sH%s" stroke="%s" stroke-width="1.2" stroke-dasharray="3 4" fill="none"/>'
                   '<rect x="%s" y="%s" width="%s" height="30" rx="4" fill="%s"/>'
                   '<text x="%s" y="%s" fill="#FFFFFF" font-size="20" font-weight="600" dominant-baseline="central">%s</text></g>'
                   % (num(cx(n - 1) + body_w / 2 + 2, 1), num(ly, 1), num(px + pw + 6), col,
                      num(px + pw + 8), num(ly - 15, 1), num(text_px(tag, 20) + 16), col, num(px + pw + 16), num(ly, 1), tag))
    # 标题
    if hd.get("title") or hd.get("sub"):
        svg.append('<g class="hd"><text x="%s" y="%s" fill="%s" font-size="40" font-weight="700">%s</text>'
                   '<text x="%s" y="%s" fill="%s" font-size="22">%s</text></g>'
                   % (num(px), num(py - 62), P["ink"], esc(hd.get("title", "")),
                      num(px + text_px(hd.get("title", ""), 40) + 18), num(py - 64), P["ink_sub"], esc(hd.get("sub", ""))))
    svg.append("</svg>")

    # ———————————————————————— 时间线 ————————————————————————
    js = []
    js.append("var ID=%s;" % json.dumps(cid, ensure_ascii=False))
    js.append("var ROOT='[data-composition-id=\"'+ID+'\"]';")
    js.append("var q=function(s){return document.querySelector(ROOT+' '+s);};")
    js.append("var qa=function(s){return document.querySelectorAll(ROOT+' '+s);};")
    intro = float(spec.get("intro", 0.1))
    js.append("gsap.set([q('.ax'),q('.leg')].filter(Boolean),{opacity:0});")
    if hd.get("title") or hd.get("sub"):
        js.append("gsap.set(q('.hd'),{opacity:0,x:-30});")
    js.append("gsap.set(qa('.c'),{scaleY:0,opacity:0,svgOrigin:function(i,el){return '0 '+el.getAttribute('data-oy');}});")
    if show_vol:
        js.append("gsap.set(qa('.v'),{scaleY:0,svgOrigin:'0 %s'});" % num(vy + vh))
    for rg in ranges:
        js.append("gsap.set(q('.rg%d'),{opacity:0});" % rg["k"])
    for lv in levels:
        js.append("gsap.set(q('.lv%d .lvl'),{scaleX:0,svgOrigin:'%s %s'});" % (lv["k"], num(px), num(lv["y"], 1)))
        js.append("gsap.set(q('.lv%d .lvt'),{opacity:0});" % lv["k"])
        if lv["text"]:
            js.append("gsap.set(q('.lv%d .lvx'),{opacity:0});" % lv["k"])
    for m in marks:
        js.append("gsap.set(q('.mk%d .mkd'),{scale:0,svgOrigin:'%s %s'});" % (m["k"], num(m["x0"], 1), num(m["y0"], 1)))
        js.append("gsap.set(q('.mk%d .mkl'),{scaleY:0,svgOrigin:'%s %s'});" % (m["k"], num(m["x0"], 1), num(m["y0"], 1)))
        js.append("gsap.set(q('.mk%d .mkc'),{opacity:0,y:%s});" % (m["k"], -10 if m["below"] else 10))
    if last_on:
        js.append("gsap.set(q('.lt'),{opacity:0});")
    js.append("var tl=gsap.timeline({paused:true});")
    js.append("tl.to([q('.ax'),q('.leg')].filter(Boolean),{opacity:1,duration:0.5,ease:'none'},%s);" % num(intro))
    if hd.get("title") or hd.get("sub"):
        js.append("tl.to(q('.hd'),{opacity:1,x:0,duration:0.7,ease:'power3.out'},%s);" % num(intro))
    js.append("tl.to(qa('.c'),{scaleY:1,opacity:1,duration:%s,ease:'power2.out',stagger:%s},%s);" % (num(gd, 4), num(step, 5), num(g_start, 4)))
    if show_vol:
        js.append("tl.to(qa('.v'),{scaleY:1,duration:%s,ease:'power2.out',stagger:%s},%s);" % (num(gd, 4), num(step, 5), num(g_start, 4)))
    # 均线笔尖：第 p-1 根长完时起笔，之后与蜡烛同速推进；或在 ma.at 单独描出
    if ma_paths:
        first_p = min(p for (p, c, d) in ma_paths)
        w_full = pw
        if ma_at is None:
            t0 = done_of(first_p - 1)
            w0 = cx(first_p - 1) - px
            js.append("tl.set(q('.mac'),{attr:{width:%s}},%s);" % (num(w0, 1), num(t0, 4)))
            js.append("tl.to(q('.mac'),{attr:{width:%s},duration:%s,ease:'none'},%s);"
                      % (num(cx(n - 1) - px, 1), num(done_of(n - 1) - t0, 4), num(t0, 4)))
            js.append("tl.set(q('.mac'),{attr:{width:%s}},%s);" % (num(w_full, 1), num(done_of(n - 1), 4)))
            ma_done = done_of(n - 1)
        else:
            ma_at = float(ma_at)
            if ma_at < g_end:
                die("ma.at=%s 早于蜡烛长完（%ss）——要单独描出就放在生长之后；要跟随生长就别给 at" % (num(ma_at), num(g_end)))
            js.append("tl.to(q('.mac'),{attr:{width:%s},duration:%s,ease:'power1.inOut'},%s);" % (num(w_full, 1), num(ma_dur), num(ma_at)))
            ma_done = ma_at + ma_dur
    else:
        ma_done = g_end
    if last_on:
        js.append("tl.to(q('.lt'),{opacity:1,duration:0.4,ease:'none'},%s);" % num(done_of(n - 1) + 0.1))
    for rg in ranges:
        js.append("tl.to(q('.rg%d'),{opacity:1,duration:0.5,ease:'power2.out'},%s);" % (rg["k"], num(rg["t"])))
    for lv in levels:
        js.append("tl.to(q('.lv%d .lvl'),{scaleX:1,duration:0.7,ease:'power2.inOut'},%s);" % (lv["k"], num(lv["t"])))
        js.append("tl.to(q('.lv%d .lvt'),{opacity:1,duration:0.3,ease:'none'},%s);" % (lv["k"], num(lv["t"] + 0.5)))
        if lv["text"]:
            js.append("tl.to(q('.lv%d .lvx'),{opacity:1,duration:0.4,ease:'none'},%s);" % (lv["k"], num(lv["t"] + 0.3)))
    for m in marks:
        js.append("tl.to(q('.mk%d .mkd'),{scale:1,duration:0.35,ease:'back.out(2)'},%s);" % (m["k"], num(m["t"])))
        js.append("tl.to(q('.mk%d .mkl'),{scaleY:1,duration:0.3,ease:'power2.out'},%s);" % (m["k"], num(m["t"] + 0.12)))
        js.append("tl.to(q('.mk%d .mkc'),{opacity:1,y:0,duration:0.45,ease:'power3.out'},%s);" % (m["k"], num(m["t"] + 0.3)))
    events_end = max([ma_done, done_of(n - 1) + 0.5] + [m["t"] + 0.75 for m in marks] + [lv["t"] + 0.8 for lv in levels]
                     + [rg["t"] + 0.5 for rg in ranges])
    if events_end > D - 0.3:
        die("最后一个动作 %ss 才结束，离颗粒末尾 %ss 不到 0.3s——没有驻留" % (num(events_end), num(D)))
    js.append("tl.to({},{duration:0.05},%s);" % num(D))
    js.append("window.__timelines=window.__timelines||{};")
    js.append("window.__timelines[ID]=tl;")

    out = []
    out.append('<template id="p">')
    out.append('<div data-composition-id="%s" data-width="%d" data-height="%d" style="position:absolute;inset:0;overflow:hidden;background:transparent;font-family:%s;">'
               % (esc(cid), W, H, font))
    if P["canvas"]:
        out.append('<div class="bgfill" style="position:absolute;inset:0;background:%s;"></div>' % P["canvas"])
    out.append("<style>%s .c.u line{stroke:%s;}%s .c.u rect{fill:%s;}%s .c.d line{stroke:%s;}%s .c.d rect{fill:%s;}</style>"
               % (R, P["up"], R, P["up"], R, P["down"], R, P["down"]))
    out.append("".join(svg))
    out.append('<script src="%s"></script>' % GSAP_CDN)
    out.append("<script>(function(){\n%s\n})();</script>" % "\n".join(js))
    out.append("</div>")
    out.append("</template>")
    out_html = "\n".join(out)
    if len(out_html) > max_chars:
        die("产物 %d 字符超过预算 %d（用 aggregate 合并周期或 slice 截取）" % (len(out_html), max_chars))
    summary = {
        "id": cid, "chars": len(out_html), "duration": D, "candles": n,
        "first": rows[0]["d"], "last": rows[-1]["d"],
        "plot": {"x": px, "y": py, "w": pw, "h": round(ph, 1), "volume_h": vh},
        "y_range": [round(lo, 6), round(hi, 6)], "ticks": [tfmt(v) for v in ticks],
        "grow": {"start": g_start, "step": round(step, 5), "candle_dur": round(gd, 3), "end": round(done_of(n - 1), 3)},
        "ma": [p for (p, c, d) in ma_paths], "ma_done": round(ma_done, 3),
        "marks": [{"k": m["k"], "at": rows[m["i"]]["d"], "kind": m["kind"], "price": m["price"], "t": m["t"],
                   "covers": m["covers"]} for m in marks],
        "levels": [{"k": lv["k"], "price": lv["p"], "t": lv["t"]} for lv in levels],
        "ranges": [{"k": rg["k"], "from": rows[rg["a"]]["d"], "to": rows[rg["b"]]["d"], "t": rg["t"]} for rg in ranges],
        "hold_from": round(events_end, 3),
        "extremes": {
            "max_high": [rows[max(range(n), key=lambda j: rows[j]["h"])]["d"], max(r["h"] for r in rows)],
            "min_low": [rows[min(range(n), key=lambda j: rows[j]["l"])]["d"], min(r["l"] for r in rows)],
            "max_close": [rows[max(range(n), key=lambda j: rows[j]["c"])]["d"], max(r["c"] for r in rows)],
            "min_close": [rows[min(range(n), key=lambda j: rows[j]["c"])]["d"], min(r["c"] for r in rows)],
            "first_open": [rows[0]["d"], rows[0]["o"]], "last_close": [rows[-1]["d"], rows[-1]["c"]],
        },
    }
    return out_html, summary


def main():
    ap = argparse.ArgumentParser(description="candle-chart 颗粒生成器")
    ap.add_argument("spec")
    ap.add_argument("--out", required=True, help="产物 html 路径（直接写最终落点）")
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
