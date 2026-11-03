#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_land_mask · 把陆地多边形栅格化成点阵位图（globe-arcs 的 assets/land-mask.json 就是它产的）。

用法：
    python build_land_mask.py <ne_110m_land.geojson> --step 1.2 --out ../assets/land-mask.json

点阵布局（生成器与颗粒里的 JS 按同一规则还原，改了这里两边都要改）：
    纬度环 k = 0..K-1，K = round(180 / step)，环心纬度 lat_k = -90 + step × (k + 0.5)
    每环点数 n_k = max(1, round(360 × cos(lat_k) / step))，点经度 lon_j = -180 + 360 × (j + 0.5) / n_k
    逐环逐点一位：1 = 陆地。位按大端打包后 base64。

数据：Natural Earth 1:110m Land（ne_110m_land，公有领域，https://www.naturalearthdata.com/）。
平时用不到本脚本——仓里已带构建好的位图；只有要换分辨率或换数据源时才重跑。
依赖：numpy。
"""
import argparse
import base64
import hashlib
import io
import json
import math
import sys

import numpy as np


def rings(step):
    K = int(round(180.0 / step))
    out = []
    for k in range(K):
        lat = -90.0 + step * (k + 0.5)
        n = max(1, int(round(360.0 * math.cos(math.radians(lat)) / step)))
        out.append((lat, n))
    return out


def polygons(gj):
    for f in gj["features"]:
        g = f["geometry"]
        polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
        for poly in polys:
            yield [np.asarray(r, dtype=np.float64) for r in poly]


def inside(px, py, ring):
    """射线法（向量化）：点集是否在一个环内。"""
    x0, y0 = ring[:-1, 0], ring[:-1, 1]
    x1, y1 = ring[1:, 0], ring[1:, 1]
    res = np.zeros(px.shape, dtype=bool)
    for a, b, c, d in zip(x0, y0, x1, y1):
        cond = ((b > py) != (d > py))
        if not cond.any():
            continue
        xin = a + (py - b) * (c - a) / ((d - b) if d != b else 1e-12)
        res ^= cond & (px < xin)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("geojson")
    ap.add_argument("--step", type=float, default=1.2)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    with io.open(a.geojson, "r", encoding="utf-8") as f:
        raw = f.read()
    gj = json.loads(raw)
    R = rings(a.step)
    lons, lats = [], []
    for lat, n in R:
        for j in range(n):
            lons.append(-180.0 + 360.0 * (j + 0.5) / n)
            lats.append(lat)
    px = np.asarray(lons)
    py = np.asarray(lats)
    land = np.zeros(px.shape, dtype=bool)
    for poly in polygons(gj):
        outer = poly[0]
        bx0, by0 = outer[:, 0].min(), outer[:, 1].min()
        bx1, by1 = outer[:, 0].max(), outer[:, 1].max()
        sel = (px >= bx0) & (px <= bx1) & (py >= by0) & (py <= by1)
        if not sel.any():
            continue
        idx = np.nonzero(sel)[0]
        hit = inside(px[idx], py[idx], outer)
        for hole in poly[1:]:
            hit &= ~inside(px[idx], py[idx], hole)
        land[idx[hit]] = True
    bits = np.packbits(land.astype(np.uint8), bitorder="big")
    out = {
        "source": "Natural Earth 1:110m Land (ne_110m_land.geojson), public domain, https://www.naturalearthdata.com/",
        "source_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "step_deg": a.step,
        "rings": len(R),
        "points": int(px.size),
        "land_points": int(land.sum()),
        "layout": "lat_k=-90+step*(k+0.5); n_k=max(1,round(360*cos(lat_k)/step)); lon_j=-180+360*(j+0.5)/n_k; bits big-endian, ring by ring",
        "bits": base64.b64encode(bits.tobytes()).decode("ascii"),
    }
    with io.open(a.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
        f.write("\n")
    sys.stdout.write(json.dumps({k: v for k, v in out.items() if k != "bits"}, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
