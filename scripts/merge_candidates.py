#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把周级候选报告里的高分源一键并入 sources.txt。

用法：
  python scripts/merge_candidates.py --take 5 --dry-run   # 只看会加什么
  python scripts/merge_candidates.py --take 5             # 真正追加并提交即可
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_URL = ("18/xgg", "/xgg", "/r18", "/18/", "adult", "madou", "porn",
            "xgg.json", "jav", "hentai", "/av/")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--take", type=int, default=5)
    ap.add_argument("--min-new", type=int, default=3)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report", default="reports/candidates.json")
    args = ap.parse_args()

    rep = common.load_json(os.path.join(ROOT, args.report), {}) or {}
    cands = [c for c in rep.get("candidates", [])
             if c.get("new_sites", 0) >= args.min_new
             and not any(x in c.get("url", "").lower() for x in SKIP_URL)]
    if not cands:
        print("报告里没有满足 min-new=%d 的候选，无需并入" % args.min_new)
        return 0

    spath = os.path.join(ROOT, "sources.txt")
    text = open(spath, encoding="utf-8").read()
    existing = set(re.findall(r"^[^#\n|]+\|(\S+)", text, re.M))
    picked = [c for c in cands if c["url"].rstrip("/") not in
              {u.rstrip("/") for u in existing}][:args.take]
    if not picked:
        print("候选都已在 sources.txt 里，无需并入")
        return 0

    lines = ["", "# ---- 周级候选自动并入 %s（来自 reports/candidates.json）----" % common.today()]
    for c in picked:
        tag = c["url"].rsplit("/", 1)[-1].replace(".json", "")[:12] or "new"
        tag = re.sub(r"[^\w.-]", "", tag)
        lines.append("%s|%s" % (tag, c["url"]))
    add = "\n".join(lines) + "\n"
    print("准备并入 %d 个源（新站点合计 %d）：" %
          (len(picked), sum(c["new_sites"] for c in picked)))
    for c in picked:
        print("  +%-4d 新站点 | %s" % (c["new_sites"], c["url"]))
    if args.dry_run:
        print("\n[dry-run] 未修改 sources.txt")
        return 0
    with open(spath, "a", encoding="utf-8") as f:
        f.write(add)
    print("\n已追加到 sources.txt，提交后次日构建生效。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
