#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把生成产物标记为本地只读（git skip-worktree），避免本地误改 / 误提交。

设计取舍：这些配置文件必须留在 git 历史里 —— 一旦从索引移除，某天回归断言
失败、Actions 不提交时，线上直链会直接 404，连「回退到上一版」都没得退。
因此不删跟踪，而是：
  - workflow 用 git add -f 独家提交生成物；
  - 本地用 skip-worktree 让 git 忽略工作区里这些文件的变化；
  - build.py 等脚本在非 Actions 环境只写 _local-*.json，从源头避免覆盖。

用法：
  python scripts/local_readonly.py            # 设置只读（默认）
  python scripts/local_readonly.py --status   # 查看当前状态
  python scripts/local_readonly.py --undo     # 取消只读（需要本地查看/修改时）
"""
import argparse
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE_FILES = [
    "tvbox.json", "tvbox-lite.json", "tvbox-network.json", "tvbox-home.json",
    "tvbox-full-homefirst.json", "tvbox-lite-homefirst.json",
    "tvbox-network-homefirst.json", "home-sites.txt", "quality.json",
    "health.json", "stable.json", "reports/audit-latest.md",
]
# 自动纳入后加的场景版，避免每新增一个版本就要回来改这个清单
DISCOVERED = [f for f in os.listdir(ROOT)
              if re.fullmatch(r"tvbox.*\.json", f)]
FILES = sorted(set(BASE_FILES) | set(DISCOVERED))


def git(*args):
    return subprocess.run(["git"] + list(args), cwd=ROOT,
                          capture_output=True, text=True).stdout.strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--undo", action="store_true")
    args = ap.parse_args()

    tracked = set(git("ls-files", *FILES).splitlines())
    if not tracked:
        print("没有已跟踪的生成产物（新仓库？），无需设置。")
        return 0

    if args.status:
        # git ls-files -v 的行首：S=skip-worktree(只读)，H=普通跟踪
        marks = {}
        for line in git("ls-files", "-v", *FILES).splitlines():
            if len(line) > 2:
                marks[line[2:].strip()] = line[0]
        for f in sorted(tracked):
            print("  %-7s %s" % ("只读" if marks.get(f) == "S" else "可写", f))
        return 0

    flag = "--no-skip-worktree" if args.undo else "--skip-worktree"
    r = subprocess.run(["git", "update-index", flag, "--"] + sorted(tracked),
                       cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        print("设置失败:", r.stderr.strip()[:200], file=sys.stderr)
        return 1
    print("已对 %d 个生成产物%s：" % (len(tracked), "取消只读" if args.undo else "设置本地只读"))
    for f in sorted(tracked):
        print("  ", f)
    print("\n本地 git status 不会再显示这些文件的变化；生成与提交只由 GitHub Actions 负责。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
