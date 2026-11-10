#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""误杀率复核：从被判 fail 的站点里抽样，用真实 TVBox 客户端 UA 重测。

单日探测把 jar 伪装、403 反爬、302 跳转、临时超时都算成 fail，
其中有多少是误杀其实没有数。这里按当天日期做固定随机抽样（可复现），
换 3 种真实客户端 UA 重试，算出「复活率 ≈ 误杀率」，并给 history.py
提供复活名单用于清零连续失败计数。

输出 recheck.json。用法：python scripts/recheck.py [--sample 50]
"""
import argparse
import json
import os
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common


def alive_with_real_ua(site):
    """按站点类型重测一次，任一真实 UA 拿到有效响应就算「复活」。"""
    api = str(site.get("api", ""))
    jar = str(site.get("jar") or "")
    for ua in common.UA_TVBOX:
        try:
            if api.startswith("csp_"):
                if not jar.startswith("http"):
                    return False, "无 jar 地址"
                st, raw = common.fetch(jar, timeout=8, limit_bytes=8,
                                       headers=ua, use_cache=False)
                if st == 200 and raw[:2] == b"PK":
                    return True, "jar(zip) HTTP200 UA=%s" % ua["User-Agent"][:18]
            elif api.startswith("http"):
                url = api + ("&" if "?" in api else "?") + "ac=list"
                st, raw = common.fetch(url, timeout=8, limit_bytes=120000,
                                       headers=ua, use_cache=False)
                j = common.json_of(raw)
                if st == 200 and (common.parse_vod_list(j) or
                                  (isinstance(j, dict) and j.get("class")) or
                                  (b"<class" in raw[:20000] and b"<list" in raw[:20000])):
                    return True, "ac=list 有效 UA=%s" % ua["User-Agent"][:18]
                if st == 200 and len(raw) > 100:
                    return True, "HTTP200 内容非空 UA=%s" % ua["User-Agent"][:18]
            else:
                return False, "未知 api"
        except Exception:
            continue
    return False, "真实 UA 重试仍失败"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="tvbox.json")
    ap.add_argument("--report", default="home_report.json")
    ap.add_argument("--sample", type=int, default=50)
    ap.add_argument("--workers", type=int, default=20)
    ap.add_argument("--output", default="recheck.json")
    args = ap.parse_args()

    cfg = common.load_json(args.input, {}) or {}
    rep = common.load_json(args.report, {}) or {}
    sites = cfg.get("sites", [])
    failed = [r for r in rep.get("sites", []) if r.get("grade") == "fail"]
    if not failed or not sites:
        common.write_json(args.output, {
            "date": common.today(), "env": common.env_info(),
            "checked": 0, "revived": 0, "false_kill_rate": None,
            "note": "无 fail 记录或缺少输入，跳过复核"}, skip_unchanged=False)
        print("无 fail 记录，跳过复核")
        return 0

    rng = random.Random("recheck-" + common.today())
    rng.shuffle(failed)
    picked = failed[:args.sample]
    targets = []
    for r in picked:
        i = r.get("idx")
        if isinstance(i, int) and 0 <= i < len(sites):
            targets.append((r, sites[i]))

    print("从 %d 个 fail 中抽样 %d 个，用真实 TVBox UA 复核 ..." %
          (len(failed), len(targets)), flush=True)
    t0 = time.time()
    rows = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(alive_with_real_ua, s): (r, s) for r, s in targets}
        for f in as_completed(futs):
            r, s = futs[f]
            try:
                ok, why = f.result()
            except Exception as e:
                ok, why = False, str(e)[:80]
            rows.append({
                "sig": common.sig(s),
                "name": str(s.get("name", ""))[:36],
                "original_grade": r.get("grade"),
                "original_reason": str(r.get("detail", {}))[:110],
                "revived": ok, "why": why,
            })

    revived = [r for r in rows if r["revived"]]
    out = {
        "date": common.today(),
        "env": common.env_info(),
        "failed_total": len(failed),
        "checked": len(rows),
        "revived": len(revived),
        "false_kill_rate": round(len(revived) / max(1, len(rows)), 4),
        "revived_sigs": [r["sig"] for r in revived],
        "note": "复活率≈误杀率下界；仅抽样，不代表全部 fail",
        "elapsed_sec": round(time.time() - t0, 1),
        "samples": rows,
    }
    common.write_json(args.output, out, skip_unchanged=False)
    print("复核结果: 复活 %d/%d，误杀率下界 %.1f%%（%.0fs）" %
          (len(revived), len(rows), 100 * out["false_kill_rate"], time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
