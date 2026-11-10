#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""刷新 jsDelivr 缓存，并且**校验刷新真的生效**。

原来的做法是「提交后 sleep 30，对每个文件发一次 purge」，发完就当成了。
实测：purge 接口返回 200 / status=finished，cdn.jsdelivr.net 仍然继续下发
上一版配置，`tvbox.json` 就这样旧了十几分钟 —— 用户复制的是主线链接，
改了拿不到等于没改。

现在的做法：purge → 把 CDN 上的字节取回来算 sha256 → 和本地文件比对，
不一致就再 purge（最多 3 轮），并在结尾如实报告有几个文件没刷新成功。
非致命：刷新失败不影响已提交的仓库内容，raw / Pages 两条线路仍然是新的。

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


def fetch_sha(repo, path):
    """取回 CDN 上的内容，返回 (sha256, 字节数)。带 no-cache 头，尽量绕过边缘缓存。"""
    url = CDN % (repo, path)
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
    done = {}
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
            got, n = fetch_sha(repo, f)
            if got == want:
                done[f] = attempt
                print("  [OK]   %-32s %6d 字节（第 %d 轮生效）" % (f, os.path.getsize(f), attempt))
            else:
                still.append(f)
                print("  [STALE] %-31s 期望 %s… 实际 %s… %s" %
                      (f, want[:8], (got or "取回失败")[:8],
                       "（%s）" % n if isinstance(n, str) else ""))
        pending = still

    ok = len(done)
    print("\nCDN 刷新结果：%d/%d 已生效" % (ok, len(files)), flush=True)
    if pending:
        print("仍未刷新（非致命，raw / Pages 两条线路不受影响）：%s" % ", ".join(pending))
    common.write_json("_cdn_purge.json", {
        "date": common.today(), "env": common.env_info(),
        "files": len(files), "refreshed": sorted(done), "stale": pending,
        "attempts": args.attempts,
    }, skip_unchanged=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
