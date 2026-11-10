#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""刷新 jsDelivr 缓存，并且**校验刷新真的生效**。

jsDelivr 对 GitHub 路径有 12h 边缘缓存且不感知 commit 变化，两种写法
（`/gh/user/repo/file` 与 `/gh/user/repo@main/file`）各有独立缓存，都可能滞留旧版。
所以「总是最新」入口应优先用 GitHub Pages（缓存几分钟、每次自动部署），
jsDelivr 用 `@main` 别名。

这里的做法：purge（无版本号 + @main 都发）→ 取回 `@main` 路径的字节算 sha256
和仓库 HEAD 比对 → 不一致就再 purge（最多 3 轮）。无版本号路径只做比对并提示
「可能滞后」，不作为成功/失败判据（它清不掉也不影响 @main / Pages / raw）。
非致命：刷新失败不影响已提交的仓库内容。

用法：python scripts/purge_cdn.py [--files a.json b.json] [--attempts 3]
"""
import argparse
import hashlib
import os
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common

DEFAULT_FILES = [
    "tvbox.json", "tvbox-lite.json", "tvbox-network.json", "tvbox-home.json",
    "tvbox-full-homefirst.json", "tvbox-lite-homefirst.json",
    "tvbox-network-homefirst.json",
    "tvbox-quality.json", "tvbox-cn-fast.json", "tvbox-stable7.json",
    "home-sites.txt", "health.json", "stable.json",
]
PURGE = "https://purge.jsdelivr.net/gh/%s"
CDN = "https://cdn.jsdelivr.net/gh/%s/%s"
CDN_MAIN = "https://cdn.jsdelivr.net/gh/%s@main/%s"


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def expected_sha(path):
    """以仓库 HEAD 里的内容为准（CDN 服务的就是已提交的版本）。

    用工作区文件比对会误判：本地跑过脚本后工作区可能比 HEAD 新/旧，
    而跳过的 git 状态看起来又是干净的。
    """
    try:
        blob = subprocess.run(["git", "show", "HEAD:" + path],
                              capture_output=True, timeout=30)
        if blob.returncode == 0 and blob.stdout:
            return hashlib.sha256(blob.stdout).hexdigest()
    except Exception:
        pass
    return sha256_file(path)


def purge(repo, path):
    """发一次 purge（两种写法都发：无版本别名与 @main）。失败不算错。"""
    for target in (repo + "/" + path, repo + "@main/" + path):
        url = PURGE % target
        try:
            with urllib.request.urlopen(url, timeout=25) as r:
                r.read(400)
        except Exception as e:
            print("    purge 失败（不致命）%s: %s" % (target, str(e)[:60]))


def fetch_sha(repo, path, main=False):
    """取回 CDN 上的内容，返回 (sha256, 字节数)。带 no-cache 头，尽量绕过边缘缓存。

    main=True 时取 `@main` 别名路径（实测能稳定拿到最新版）；
    默认取无版本号路径（边缘缓存最顽固，可能滞后 ≤12h）。
    """
    url = (CDN_MAIN if main else CDN) % (repo, path)
    req = urllib.request.Request(url, headers={
        "Cache-Control": "no-cache", "Pragma": "no-cache",
        "User-Agent": "tvbox-merged-purge-check/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            h = hashlib.sha256()
            n = 0
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                h.update(chunk)
                n += len(chunk)
            return h.hexdigest(), n
    except Exception as e:
        return None, str(e)[:60]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    ap.add_argument("--files", nargs="*", default=None)
    ap.add_argument("--attempts", type=int, default=3)
    args = ap.parse_args()

    repo = args.repo or "dimogu/tvbox-merged"
    files = [f for f in (args.files or DEFAULT_FILES) if os.path.exists(f)]
    if not files:
        print("没有可刷新的文件")
        return 0

    # 先给仓库一点时间让 GitHub 侧稳定，再开始刷
    print("等待 20s 后开始刷新（%d 个文件，最多 %d 轮）" % (len(files), args.attempts),
          flush=True)
    time.sleep(20)

    pending = list(files)
    done, want_sha, less_sha = {}, {}, {}
    for attempt in range(1, args.attempts + 1):
        if not pending:
            break
        print("=== 第 %d 轮：%d 个待刷新 ===" % (attempt, len(pending)), flush=True)
        for f in pending:
            purge(repo, f)
        # 大文件给 CDN 更久一点回源，否则会把「还没回源」误判成「没刷新」
        time.sleep(12 if attempt == 1 else 8)
        still = []
        for f in pending:
            want = expected_sha(f)
            got_main, n_main = fetch_sha(repo, f, main=True)
            got_less, n_less = fetch_sha(repo, f, main=False)
            want_sha[f] = want
            if got_main == want:
                done[f] = attempt
                less_sha[f] = got_less
                note = "" if got_less == got_main else \
                    "（⚠ 无版本号路径仍滞后，建议用户改用 @main 或 Pages 链接）"
                print("  [OK]   %-32s %6d 字节（@main 第 %d 轮生效）%s" %
                      (f, os.path.getsize(f), attempt, note))
            else:
                still.append(f)
                print("  [STALE] %-31s @main 期望 %s… 实际 %s…" %
                      (f, want[:8], (got_main or "取回失败")[:8]))
        pending = still

    ok = len(done)
    print("\nCDN(@main) 刷新结果：%d/%d 已生效" % (ok, len(files)), flush=True)
    if pending:
        print("仍未刷新（非致命，raw / Pages 两条线路不受影响）：%s" % ", ".join(pending))
    # 复用本轮已经取回的 sha：旧实现在这里对每个文件再发 4 次请求，而且
    # 任一侧取回失败返回 None 时两边相等会漏报 / 误报。
    lag = [f for f in done
           if less_sha.get(f) is not None and less_sha[f] != want_sha.get(f)]
    if lag:
        print("无版本号路径仍滞后（≤12h 自愈，不影响 @main / Pages）：%s" % ", ".join(lag))
    common.write_json("_cdn_purge.json", {
        "date": common.today(), "env": common.env_info(),
        "files": len(files), "refreshed": sorted(done), "stale": pending,
        "noversion_stale": lag,
        "attempts": args.attempts,
    }, skip_unchanged=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
