#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""端到端「能不能播」探测（把「配置活着」升级为「片子能播」）。

单站链路：
  首页列表 ?ac=videolist/detail&pg=1
    → 取第一部片子的 vod_id
    → 详情 ?ac=detail&ids=<id>（兼容 videolist&ids=）
    → 从 vod_play_url 抽出真实播放地址
    → HEAD/Range GET 播放地址，校验状态与内容（m3u8/mp4/媒体流）
  另外并行探一次搜索 ?wd=，以及抽样验证 parses 通道是否真的 200。

输出：
  _play_report.json   每站明细
  play_summary.json   汇总（可播率 / 搜索可用率 / 解析可用率 + 站点级得分）
     ↑ 被 rank_home.py 用作排序权重，被 health.py 画趋势，被 history.py 存档

用法：python scripts/play_probe.py [--sample 30] [--parse-sample 20] [--workers 24]
"""
import argparse
import json
import os
import random
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common


def is_cms(site):
    api = str(site.get("api", ""))
    return (api.startswith("http") and
            str(site.get("type", "")) in ("0", "1") and
            ".js" not in api[-8:])


def with_param(api, params):
    sep = "&" if "?" in api else "?"
    return api.rstrip("/") + sep + params


def get_json(url, timeout=6):
    st, raw = common.fetch(url, timeout=timeout, limit_bytes=400000)
    return st, common.json_of(raw)


def check_play_url(url, timeout=6):
    """播放地址连通性。直接用 Range GET 只取前几百字节 —— 大量源站禁用 HEAD，
    先 HEAD 再 GET 会让每个失效地址白等一个完整超时，30 站就要多花几分钟。"""
    st, body = common.fetch(url, timeout=timeout, limit_bytes=4096,
                            headers=dict(common.UA_OKHTTP, Range="bytes=0-511"),
                            use_cache=False)
    if st in (200, 206):
        txt = body[:512].decode("utf-8", "ignore")
        # 原来的 `or len(body) > 0` 让任何非空 200 都算媒体（防盗链白名单页、
        # CDN 错误页、运营商劫持页全是 HTML 但字节数 > 0），可播率系统性虚高，
        # 精播版 tier 0 与排序权重都被污染。改为魔数白名单 + HTML 黑名单。
        low = txt.lower()
        looks_media = ("#EXTM3U" in txt or b"ftyp" in body[:64] or
                       b"\x47" == body[:1] or b"FLV" == body[:3] or
                       (len(body) > 0 and "<html" not in low and
                        "<!doctype" not in low and "<head" not in low))
        return {"reachable": True, "status": st, "media_hint": bool(looks_media)}
    if st in (301, 302):
        return {"reachable": True, "status": st, "media_hint": False}
    if st == 403:
        return {"reachable": False, "status": st, "media_hint": False,
                "reason": "防盗链/403"}
    return {"reachable": False, "status": st, "media_hint": False, "reason": "连不上"}


def probe_one(site):
    api = str(site.get("api", ""))
    r = {
        "sig": common.sig(site),
        "name": str(site.get("name", ""))[:40],
        "api": api[:120],
        "list": False, "detail": False, "play": False, "search": False,
        "vod_id": "", "play_url": "", "detail_note": "", "play_check": {},
        "score": 0,
    }
    # 1) 首页列表
    vod = None
    for ac in ("ac=videolist&pg=1", "ac=detail&pg=1", "ac=list"):
        st, j = get_json(with_param(api, ac))
        items = common.parse_vod_list(j)
        if items:
            vod = items[0]
            r["list"] = True
            r["vod_id"] = str(vod.get("vod_id") or "")[:30]
            break
    if not r["list"]:
        return r
    # 2) 搜索（与首页是两套数据，单独记）
    st, j = get_json(with_param(api, "wd=" + quote("电影")))
    r["search"] = bool(common.parse_vod_list(j))
    # 3) 详情 → play_url
    if r["vod_id"]:
        for ac in ("ac=detail&ids=" + r["vod_id"], "ac=videolist&ids=" + r["vod_id"]):
            st, j = get_json(with_param(api, ac))
            items = common.parse_vod_list(j)
            if items and common.extract_play_urls(items[0]):
                r["detail"] = True
                r["play_url"] = common.extract_play_urls(items[0])[0][:160]
                break
            if items and not r["detail_note"]:
                r["detail_note"] = "详情无播放地址"
    # 详情接口没给地址时，部分 CMS 在列表里就带了 vod_play_url
    if not r["play_url"]:
        urls = common.extract_play_urls(vod)
        if urls:
            r["play_url"] = urls[0][:160]
            r["detail"] = True
            r["detail_note"] = "列表自带播放地址"
    # 4) 播放地址连通性
    if r["play_url"]:
        chk = check_play_url(r["play_url"])
        r["play_check"] = chk
        r["play"] = bool(chk.get("reachable") and chk.get("media_hint"))
    # 得分：可播权重最高，其次详情/列表/搜索
    r["score"] = (5 if r["play"] else 0) + (2 if r["detail"] else 0) + \
                 (1 if r["list"] else 0) + (1 if r["search"] else 0)
    return r


def probe_parse(p):
    u = str(p.get("url", ""))
    # 带占位符的解析模板无法直接请求，跳过但如实计数
    if not u.startswith("http") or any(x in u for x in ("{", "%%", "$")):
        return {"alive": None, "status": 0, "url": u[:120]}
    st, raw = common.fetch(u, timeout=7, limit_bytes=4096)
    return {"alive": st == 200 and len(raw) > 10, "status": st,
            "bytes": len(raw), "url": u[:120]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="tvbox.json")
    ap.add_argument("--home", default="tvbox-home.json")
    ap.add_argument("--sample", type=int, default=30)
    ap.add_argument("--parse-sample", type=int, default=20)
    ap.add_argument("--workers", type=int, default=24)
    ap.add_argument("--report", default="_play_report.json")
    ap.add_argument("--summary", default="play_summary.json")
    args = ap.parse_args()

    cfg = common.load_json(args.input)
    if not cfg:
        print("缺少 %s，跳过可播率探测" % args.input, file=sys.stderr)
        return 1
    sites = [s for s in cfg.get("sites", []) if is_cms(s)]
    home = common.load_json(args.home, {}) or {}
    home_sigs = {common.sig(s) for s in home.get("sites", [])}

    # 抽样优先覆盖「已判定能出首页」的节点：它们正是会被置顶、最值得验证的源
    rng = random.Random(common.today())
    preferred = [s for s in sites if common.sig(s) in home_sigs]
    others = [s for s in sites if common.sig(s) not in home_sigs]
    rng.shuffle(preferred)
    rng.shuffle(others)
    n = args.sample or len(sites)
    picked = (preferred + others)[:n]
    print("CMS 站点 %d，抽样 %d（首页可用 %d 优先）" %
          (len(sites), len(picked), len(preferred)), flush=True)

    t0 = time.time()
    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(probe_one, s): s for s in picked}
        for f in as_completed(futs):
            try:
                results.append(f.result())
            except Exception as e:
                s = futs[f]
                results.append({"sig": common.sig(s), "name": s.get("name"),
                                "error": str(e)[:100], "score": 0,
                                "list": False, "detail": False, "play": False, "search": False})

    all_parses = list(cfg.get("parses", []))
    rng.shuffle(all_parses)
    picked_parses = all_parses[:args.parse_sample]
    parse_results = []
    with ThreadPoolExecutor(max_workers=12) as ex:
        futs = {ex.submit(probe_parse, p): p for p in picked_parses}
        for f in as_completed(futs):
            try:
                parse_results.append(f.result())
            except Exception:
                parse_results.append({"alive": None, "status": 0})

    def rate(key):
        return round(sum(1 for r in results if r.get(key)) / max(1, len(results)), 4)

    checked_parses = [p for p in parse_results if p.get("alive") is not None]
    summary = {
        "date": common.today(),
        "env": common.env_info(),
        "sampled": len(results),
        "cms_total": len(sites),
        "list_rate": rate("list"),
        "detail_rate": rate("detail"),
        "play_rate": rate("play"),
        "search_rate": rate("search"),
        "playable": sum(1 for r in results if r.get("play")),
        "parses_total": len(all_parses),
        "parses_sampled": len(checked_parses),
        "parses_alive_rate": round(
            sum(1 for p in checked_parses if p.get("alive")) / max(1, len(checked_parses)), 4),
        "scores": {r["sig"]: r.get("score", 0) for r in results},
        "playable_sigs": [r["sig"] for r in results if r.get("play")],
        "elapsed_sec": round(time.time() - t0, 1),
    }
    common.write_json(args.report, dict(summary, sites=results, parses=parse_results),
                      skip_unchanged=False)
    common.write_json(args.summary, summary, skip_unchanged=False)

    print("\n=== 端到端可播率（%d 站，%.0fs）===" % (len(results), time.time() - t0))
    print("  列表可用 %-6s 详情有地址 %-6s 播放可连 %-6s 搜索可用 %-6s" %
          (summary["list_rate"], summary["detail_rate"],
           summary["play_rate"], summary["search_rate"]))
    print("  可播站点 %d/%d | parses 抽样存活 %s (%d/%d)" %
          (summary["playable"], len(results), summary["parses_alive_rate"],
           sum(1 for p in checked_parses if p.get("alive")), len(checked_parses)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
