#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""归档 + 稳定版指针 + 一键回滚。

自动更新每天覆盖同一个文件，一旦某天生成了坏配置，用户手上没有任何
「退回昨天」的入口。这里做三层保险：
  1. archive/YYYY-MM-DD/   最近 7 天的轻量归档（manifest + gzip 配置），提交进仓库；
  2. 不可变 tag stable-YYYYMMDD  只有「回归断言连续通过 3 天」才推进，
     提供一个内容永不变化的固定直链；首次运行会自动 bootstrap 一个初始稳定版；
  3. stable.json             指针文件，记录当前推荐的 tag 与各版本完整链接，
     想回滚只要把 tag 换成上一个（或直接 python scripts/stable.py --rollback <tag>）。

注意调用顺序：必须在 workflow 的「第一次 git commit」之后运行，
这样 tag 会指向包含当天全部配置文件的那个提交。

用法：
  python scripts/stable.py                    # 归档 + 视情况推进指针
  python scripts/stable.py --rollback stable-20261109
  python scripts/stable.py --min-streak 3
"""
import argparse
import gzip
import json
import os
import re
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common

FILES = [
    "tvbox-full-homefirst.json", "tvbox-lite-homefirst.json",
    "tvbox-network-homefirst.json", "tvbox-home.json",
    "tvbox.json", "tvbox-lite.json", "tvbox-network.json",
]
# 场景版是后加的：旧 tag 里没有这些文件，不能凭文件名生成 404 链接。
# 只有 tag 对应提交里确实存在该文件时才写进 stable.json 的 files。
OPTIONAL_FILES = ["tvbox-quality.json", "tvbox-cn-fast.json", "tvbox-stable7.json"]
ARCHIVE_GZ = ["tvbox-lite.json", "tvbox-home.json", "tvbox-cn-fast.json"]
KEEP_DAYS = 7


def git(*args):
    try:
        return subprocess.run(["git"] + list(args), capture_output=True,
                              text=True, timeout=30).stdout.strip()
    except Exception:
        return ""


def repo_slug():
    r = os.environ.get("GITHUB_REPOSITORY", "")
    if r:
        return r
    remote = git("remote", "get-url", "origin")
    m = re.search(r"github\.com[:/]([^/]+/[^/.]+?)(?:\.git)?$", remote)
    return m.group(1) if m else "dimogu/tvbox-merged"


def tag_contains(tag, f):
    """tag 一旦创建就不可变，历史 tag 里没有后来新增的配置，链接不能凭空编。"""
    try:
        r = subprocess.run(["git", "cat-file", "-e", "%s:%s" % (tag, f)],
                           capture_output=True, timeout=15)
        return r.returncode == 0
    except Exception:
        return False


def tag_commit(tag):
    """tag 实际指向的 commit。

    同一天重复运行时 tag 会复用，此时它仍指向当天第一次构建的提交；
    stable.json 的 commit / history[].commit 必须写这个不可变 commit，
    否则指针里的 commit 与用户实际下载到的内容对不上，回滚排查会被误导。
    """
    try:
        r = subprocess.run(["git", "rev-list", "-n", "1", tag],
                           capture_output=True, text=True, timeout=20)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception:
        pass
    return ""


def links(repo, tag, f):
    return {
        "cdn": "https://cdn.jsdelivr.net/gh/%s@%s/%s" % (repo, tag, f),
        "raw": "https://raw.githubusercontent.com/%s/%s/%s" % (repo, tag, f),
        "fastly": "https://fastly.jsdelivr.net/gh/%s@%s/%s" % (repo, tag, f),
    }


def make_manifest():
    files = {}
    for f in FILES + OPTIONAL_FILES:
        if os.path.exists(f):
            cfg = common.load_json(f, {}) or {}
            files[f] = {
                "sha256": common.sha256_file(f),
                "bytes": os.path.getsize(f),
                "sites": len(cfg.get("sites", [])),
            }
    q = common.load_json("quality.json", {}) or {}
    a = common.load_result("assertions", {}) or {}
    p = common.load_json("play_summary.json", {}) or {}
    return {
        "date": common.today(),
        "commit": git("rev-parse", "HEAD"),
        "env": common.env_info(),
        "files": files,
        "assert_passed": bool(a.get("passed")),
        "assert_pass_streak": q.get("assert_pass_streak", 0),
        "play_rate": p.get("play_rate"),
        "false_kill_rate": q.get("false_kill_rate"),
        "sources_failed": (q.get("latest") or {}).get("sources_failed", []),
    }


def archive(manifest, keep_days=KEEP_DAYS):
    d = os.path.join("archive", manifest["date"])
    os.makedirs(d, exist_ok=True)
    common.write_json(os.path.join(d, "manifest.json"), manifest, skip_unchanged=False)
    for f in ARCHIVE_GZ:
        if not os.path.exists(f):
            continue
        with open(f, "rb") as src, gzip.open(os.path.join(d, f + ".gz"), "wb") as dst:
            shutil.copyfileobj(src, dst)
    # 只留最近 7 天
    try:
        days = sorted(x for x in os.listdir("archive")
                      if re.fullmatch(r"20\d\d-\d\d-\d\d", x))
        for old in days[:-keep_days]:
            shutil.rmtree(os.path.join("archive", old), ignore_errors=True)
            print("  清理过期归档", old)
    except OSError:
        pass
    print("  归档 %s（%d 份配置指纹，%d 份 gzip 备份）" %
          (d, len(manifest["files"]), len(ARCHIVE_GZ)))


def write_pointer(state, repo, tag, manifest, bootstrap=False):
    linked = tag_commit(tag) or manifest["commit"]
    history = [h for h in state.get("history", []) if h.get("tag") != tag]
    history.append({"date": common.today(), "tag": tag,
                    "commit": linked,
                    "sites": (manifest["files"].get("tvbox.json") or {}).get("sites", 0)})
    out = {
        "updated": common.today(),
        "tag": tag,
        "commit": linked,
        "last_check_commit": manifest["commit"],
        "repo": repo,
        "bootstrap": bootstrap,
        "assert_pass_streak": manifest["assert_pass_streak"],
        "min_streak_to_advance": 3,
        "policy": "回归断言连续通过 3 天才推进；坏版本可用 --rollback 退回历史 tag",
        "files": dict(
            {f: links(repo, tag, f) for f in FILES},
            **{f: links(repo, tag, f) for f in OPTIONAL_FILES if tag_contains(tag, f)}
        ),
        "history": history[-10:],
        "rollback_hint": "python scripts/stable.py --rollback <上一个 tag>",
        "readme": "把 files.<版本>.cdn 整行粘进 TVBox 配置地址即可；tag 固定，内容永不变化",
    }
    common.write_json("stable.json", out, skip_unchanged=False)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-streak", type=int, default=3)
    ap.add_argument("--keep-days", type=int, default=KEEP_DAYS)
    ap.add_argument("--rollback", default="", help="把指针退回指定 tag，不重新构建")
    args = ap.parse_args()

    repo = repo_slug()
    state = common.load_json("stable.json", {}) or {}

    if args.rollback:
        tags = git("tag", "-l", "stable-*").splitlines()
        if args.rollback not in tags:
            print("找不到 tag %s；现有：%s" % (args.rollback, ", ".join(tags[-8:])),
                  file=sys.stderr)
            return 1
        manifest = make_manifest()
        # manifest 的站点数取的是「当前工作区」tvbox.json，而回滚后的指针指向
        # 历史 tag。直接写进去会让 stable.json 的记录与实际下载内容对不上。
        # 优先从 tag 里的 tvbox.json 取真实条数，取不到再退回工作区值。
        try:
            hist = json.loads(git("show", "%s:tvbox.json" % args.rollback))
            site_count = len(hist.get("sites", []))
        except Exception:
            site_count = (manifest["files"].get("tvbox.json") or {}).get("sites", 0)
        manifest["files"].setdefault("tvbox.json", {})["sites"] = site_count
        out = write_pointer(dict(state, history=state.get("history", [])),
                            repo, args.rollback, manifest)
        print("已回滚稳定版指针 -> %s" % args.rollback)
        print("  主推链接：%s" % out["files"]["tvbox-full-homefirst.json"]["cdn"])
        return 0

    manifest = make_manifest()
    archive(manifest)
    if not manifest["assert_passed"]:
        print("  断言未通过，本次不推进稳定版指针（workflow 也不会提交）")
        return 0

    # 本轮有探测步骤失败 = 数据不完整的降级版。配置照常发布（线上可用性
    # 优先），但绝不能拿它去推进「连续 3 天健康」的稳定版 tag，否则用户
    # 以为拿到的是久经检验的版本，实际是缺探测数据的那一版。
    bs = common.load_build_status()
    # 状态文件缺失 / 过期同样不能当「健康」放行：以前只判 degraded，
    # bs={} 时会一路走到 bootstrap 分支，把降级日标成首个稳定版。
    if not bs or bs.get("degraded"):
        cur_tag = state.get("tag", "")
        if cur_tag:
            state["updated"] = common.today()
            state["last_check_commit"] = manifest["commit"]
            common.write_json("stable.json", state, skip_unchanged=False)
        reason = ("构建状态不可用" if not bs else
                  "不完整步骤 %s" % (", ".join(bs.get("incomplete_steps") or
                                              bs.get("failed_steps") or []) or "未知"))
        print("  %s，降级版不推进稳定版指针（仍指向 %s）" %
              (reason, cur_tag or "(无)"))
        return 0

    streak = int(manifest["assert_pass_streak"] or 0)
    tag = "stable-" + common.today().replace("-", "")
    existing_tags = git("tag", "-l", "stable-*").splitlines()
    current = state.get("tag", "")
    bootstrap = not current

    # bootstrap（stable.json 丢失 / 首次运行）以前直接跳过连胜门槛：只要当日
    # 断言通过就把这一版标成稳定版，哪怕 history 里连续健康天数是 0。门槛
    # 对首次运行同样适用 —— 稳定版必须是「久经检验」的那一版。
    if streak < args.min_streak:
        # 保持旧指针，只刷新统计字段，不重新指向新 commit
        if current:
            state["updated"] = common.today()
            state["assert_pass_streak"] = streak
            state["last_check_commit"] = manifest["commit"]
            common.write_json("stable.json", state, skip_unchanged=False)
        print("  断言连续通过 %d/%d 天，稳定版仍指向 %s" %
              (streak, args.min_streak, current or "(无)"))
        return 0

    if tag not in existing_tags:
        r = subprocess.run(["git", "tag", tag], capture_output=True, text=True)
        if r.returncode != 0:
            print("  创建 tag 失败：%s（非致命，指针仍会更新）" % r.stderr.strip()[:120],
                  file=sys.stderr)
        else:
            print("  创建不可变 tag %s -> %s" % (tag, manifest["commit"][:10]))
    else:
        print("  tag %s 已存在（当天重复运行），复用" % tag)

    out = write_pointer(state, repo, tag, manifest, bootstrap=bootstrap)
    print("  稳定版指针 -> %s%s" % (tag, "（首次 bootstrap）" if bootstrap else ""))
    print("  固定链接：%s" % out["files"]["tvbox-full-homefirst.json"]["cdn"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
