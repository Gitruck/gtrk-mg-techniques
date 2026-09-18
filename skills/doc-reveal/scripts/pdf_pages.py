#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pdf_pages · 把 PDF 指定页栅格化成 PNG，并可按文字查找返回归一化矩形（喂给 spec.focus[].rect）。

用法：
    python pdf_pages.py <file.pdf> --pages 1-3 --dpi 200 --out <dir> [--prefix name]
                        [--find "Some title" --find "Author Name" ...] [--find-page 1]

输出：写 <out>/<prefix>-p<N>.png，stdout 一行 JSON：{pages:[...], finds:{text:[[x0,y0,x1,y1],...]}}。
矩形是页面归一化坐标（0..1），与 doc_reveal.py 的 focus.rect 同口径。

依赖：pymupdf（pip install pymupdf）。
"""
import argparse
import io
import json
import os
import sys


def parse_pages(s):
    out = []
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--pages", default="1", help="如 1-3 或 1,2,5（1 起）")
    ap.add_argument("--dpi", type=int, default=200)
    ap.add_argument("--out", required=True)
    ap.add_argument("--prefix", default=None)
    ap.add_argument("--find", action="append", default=[])
    ap.add_argument("--find-page", type=int, default=1)
    a = ap.parse_args()
    try:
        import pymupdf
    except ImportError:
        sys.stderr.write("需要 pymupdf：pip install pymupdf\n")
        sys.exit(2)
    doc = pymupdf.open(a.pdf)
    prefix = a.prefix or os.path.splitext(os.path.basename(a.pdf))[0]
    os.makedirs(a.out, exist_ok=True)
    pages = []
    for n in parse_pages(a.pages):
        if n < 1 or n > len(doc):
            sys.stderr.write("页码越界：%d（共 %d 页）\n" % (n, len(doc)))
            sys.exit(2)
        pg = doc[n - 1]
        pix = pg.get_pixmap(dpi=a.dpi)
        path = os.path.join(a.out, "%s-p%d.png" % (prefix, n))
        pix.save(path)
        pages.append({"page": n, "path": path, "w": pix.width, "h": pix.height})
    finds = {}
    if a.find:
        pg = doc[a.find_page - 1]
        R = pg.rect
        for q in a.find:
            rs = pg.search_for(q)
            finds[q] = [[round(r.x0 / R.width, 4), round(r.y0 / R.height, 4), round(r.x1 / R.width, 4), round(r.y1 / R.height, 4)] for r in rs]
    sys.stdout.write(json.dumps({"pdf": os.path.abspath(a.pdf), "page_count": len(doc), "pages": pages, "find_page": a.find_page, "finds": finds}, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
