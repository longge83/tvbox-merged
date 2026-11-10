#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""周级上游发现：把躺着的 harvest / scan_repos / eval_sources 编排成自动化。

流程：
  1. 挖导航仓库 README 与多仓聚合配置里的候选 URL（harvest.py）
  2. 扫已知配置仓库的 tree，找大 JSON（scan_repos.py）
  3. 去掉已在 sources.txt 里的、违规关键词的、拉不动的
  4. 相对**当前线上 tvbox.json** 计算「去重后的边际贡献」（eval_sources 的思路，
     但基线换成真实全量配置，而不是早期 9 源硬编码）
  5. 产出 reports/candidates.json + 可读 Markdown，由 Actions 每周自动提交

人工一键并入：python scripts/merge_candidates.py --take 5

用法：python scripts/discover.py [--max 80] [--workers 16]
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common
import build as build_mod

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT_DIR = "reports"
URL_RE = re.compile(r"https?://[^\s\)\]\}\"<>`\\，,；;）]+")
# 导航仓库里混着大量成人/违规线路，路径特征比站点名更早暴露；
# 周报是给人看的并入清单，绝不能把这类源列成「高分候选」。
SKIP_URL = ("18/xgg", "/xgg", "/r18", "/18/", "adult", "madou", "porn",
            "xgg.json", "jav", "hentai", "/av/")


def is_mirror(u):
    """gh-proxy / moeyy 之类只是把已收录的 GitHub raw 配置转了一层，
    没有新内容；而且相对路径基准不同会让边际贡献虚高，容易误导并入决策。"""
    try:
        from urllib.parse import urlsplit
        host = urlsplit(u).netloc
    except Exception:
        return False
    return ("raw.githubusercontent.com" in u and
            host not in ("raw.githubusercontent.com", ""))


def run_script(name, timeout=240):
    path = os.path.join(ROOT, "scripts", name)
    try:
        return subprocess.run([sys.executable, path], capture_output=True,
                              text=True, timeout=timeout,
                              cwd=ROOT).stdout
    except Exception as e:
        print("  %s 执行失败: %s" % (name, e), file=sys.stderr)
        return ""


def collect_urls():
    found = {}
    out = run_script("harvest.py")
    for line in out.splitlines():
        if "\t|" in line:
            u, src = line.split("\t|", 1)
            found.setdefault(u.strip(), set()).add(src.strip()[:30])
        elif line.startswith("http") and "|" not in line:
            found.setdefault(line.strip(), set()).add("harvest")
    out = run_script("scan_repos.py")
    repo = ""
    for line in out.splitlines():
        if line.startswith("## "):
            repo = line[3:].strip()
        m = re.search(r"(https?://\S+\.json)\s*$", line)
        if m:
            found.setdefault(m.group(1), set()).add(repo or "scan_repos")
    # 展开多仓聚合
    for agg in ("https://raw.githubusercontent.com/noimank/tvbox/main/tvboxmuti.json",):
        st, raw = common.fetch(agg, timeout=15, limit_bytes=500000)
        j = common.json_of(raw)
        if isinstance(j, dict):
            for item in j.get("urls", []):
                if isinstance(item, dict) and item.get("url"):
                    found.setdefault(item["url"].strip(), set()).add("多仓聚合")
    return found


def existing_urls():
    urls = set()
    p = os.path.join(ROOT, "sources.txt")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#"):
                cols = line.split("|")
                if len(cols) >= 2:
                    urls.add(cols[1].strip().rstrip("/"))
    return urls


def baseline():
    cfg = common.load_json(os.path.join(ROOT, "tvbox.json"), {}) or {}
    keys, apis = set(), set()
    for s in cfg.get("sites", []):
        api = str(s.get("api") or "")
        ext = s.get("ext")
        ek = ext if isinstance(ext, str) else json.dumps(ext, ensure_ascii=False, sort_keys=True)[:60]
        tail = (ek or api).rsplit("/", 1)[-1][:40]
        keys.add(build_mod.norm_name(s.get("name")) + "|" + tail)
        if api.startswith("http"):
            apis.add(api)
    return keys, apis


def evaluate(item, keys, apis, timeout=15):
    url, srcs = item
    st, raw = common.fetch(url, timeout=timeout, limit_bytes=3000000)
    cfg = common.json_of(raw)
    if not isinstance(cfg, dict) or not cfg.get("sites"):
        return {"url": url, "sources": sorted(srcs), "ok": False,
                "reason": "HTTP %s / 非 TVBox 配置" % st}
    # 上游大量使用 ./js/x.js 这类相对路径。不做绝对化会把「同一份配置的
    # 代理镜像」当成几百个新站点（实测 gh-proxy 的 gao 镜像曾虚报 +219）。
    # 这里与 build.py 用同一套规则，边际贡献才有可比性。
    base = url.rsplit("/", 1)[0] + "/"
    new = new_api = 0
    for s in cfg.get("sites", []):
        if not isinstance(s, dict):
            continue
        api = build_mod.absolutize(str(s.get("api") or ""), base)
        ext = s.get("ext")
        if isinstance(ext, str):
            ext = build_mod.absolutize(ext, base)
        ek = ext if isinstance(ext, str) else json.dumps(ext, ensure_ascii=False, sort_keys=True)[:60]
        tail = (ek or api).rsplit("/", 1)[-1][:40]
        k = build_mod.norm_name(s.get("name")) + "|" + tail
        if k in keys or (str(api).startswith("http") and api in apis):
            continue
        new += 1
        if str(api).startswith("http"):
            new_api += 1
    return {"url": url, "sources": sorted(srcs), "ok": True,
            "sites": len(cfg.get("sites", [])),
            "new_sites": new, "new_cms": new_api,
            "lives": len(cfg.get("lives", [])),
            "parses": len(cfg.get("parses", []))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=80)
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--min-new", type=int, default=3)
    args = ap.parse_args()

    keys, apis = baseline()
    print("基线：线上配置 %d 个站点键 / %d 个 http api" % (len(keys), len(apis)), flush=True)
    found = collect_urls()
    have = existing_urls()
    # 只查 URL 文本里带违规词的候选，避免把明显不合规源写进建议表
    cand = [(u, s) for u, s in found.items()
            if u.startswith("http") and u.rstrip("/") not in have
            and not build_mod.BLOCK_RX.search(u)
            and not any(x in u.lower() for x in SKIP_URL)
            and not is_mirror(u)]
    print("挖到候选 %d，去掉已收录后 %d，取前 %d 评估" %
          (len(found), len(cand), min(args.max, len(cand))), flush=True)
    cand.sort()
    cand = cand[:args.max]

    t0 = time.time()
    rows = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(evaluate, it, keys, apis): it for it in cand}
        for f in as_completed(futs):
            try:
                r = f.result()
            except Exception as e:
                u, s = futs[f]
                r = {"url": u, "sources": sorted(s), "ok": False, "reason": str(e)[:80]}
            rows.append(r)
    good = [r for r in rows if r.get("ok") and
            (r.get("new_sites", 0) >= args.min_new or r.get("lives") or r.get("parses"))]
    good.sort(key=lambda r: (-r.get("new_sites", 0), -r.get("lives", 0), r["url"]))
    failed = [r for r in rows if not r.get("ok")]

    os.makedirs(REPORT_DIR, exist_ok=True)
    report = {
        "date": common.today(),
        "env": common.env_info(),
        "baseline_sites": len(keys),
        "candidates_found": len(found),
        "candidates_evaluated": len(rows),
        "candidates_usable": len(good),
        "candidates_failed": len(failed),
        "min_new_sites": args.min_new,
        "elapsed_sec": round(time.time() - t0, 1),
        "candidates": good,
        "failed_sample": failed[:20],
    }
    common.write_json(os.path.join(REPORT_DIR, "candidates.json"), report,
                      skip_unchanged=False)
    md = [
        "# 上游候选发现报告 · %s" % common.today(), "",
        "- 挖到 URL：%d，评估：%d，有边际贡献：%d，拉取失败：%d" %
        (len(found), len(rows), len(good), len(failed)),
        "- 基线：当前线上 `tvbox.json` 去重后 %d 个站点键" % len(keys),
        "- 判定环境：%s（耗时 %.0fs）" % (common.env_info()["env"], time.time() - t0), "",
        "「新站点」= 相对线上配置去重后能新增的节点数，不是该源自身的站点总数。", "",
        "| 新站点 | 其中采集型 | 直播 | 解析 | 源内站点 | URL | 来源 |",
        "|---:|---:|---:|---:|---:|---|---|",
    ]
    for r in good:
        md.append("| %d | %d | %d | %d | %d | `%s` | %s |" %
                  (r["new_sites"], r["new_cms"], r["lives"], r["parses"],
                   r["sites"], r["url"], ", ".join(r["sources"])[:40]))
    md += ["", "## 一键并入", "",
           "```bash", "python scripts/merge_candidates.py --take 5", "```", "",
           "会按顺序把前 N 个 URL 追加到 `sources.txt`（自动跳过已收录项），提交后次日构建生效。", ""]
    with open(os.path.join(REPORT_DIR, "candidates-%s.md" % common.today()),
              "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print("候选可用 %d 个，报告 reports/candidates-%s.md（%.0fs）" %
          (len(good), common.today(), time.time() - t0))
    for r in good[:10]:
        print("  +%-4d 新站点 | 直播 %-3d 解析 %-3d | %s" %
              (r["new_sites"], r["lives"], r["parses"], r["url"][:100]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
