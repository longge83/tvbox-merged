#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""三份基础配置的复查报告。

polish.py 改完后由这里独立复验一遍（不复用它的内部状态，读文件重新算），
写成 reports/audit-latest.md 并打印摘要。任何一项残留都会标红，
但**不阻断构建** —— 阻断交给 assertions.py，这里只负责把话说清楚。

用法：python scripts/audit.py [--output reports/audit-latest.md]
"""
import argparse
import json
import os
import re
import sys
from collections import Counter
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common
import polish as polish_mod

TARGETS = ["tvbox.json", "tvbox-lite.json", "tvbox-network.json"]
API_RX = re.compile(r"^(?:https?://\S+|csp_\S+)$", re.I)


def audit_file(path):
    cfg = common.load_json(path)
    if not isinstance(cfg, dict):
        return None
    meta = cfg.get("_meta") or {}
    sites = [s for s in cfg.get("sites", []) if isinstance(s, dict)]
    lives = [g for g in cfg.get("lives", []) if isinstance(g, dict)]
    parses = [p for p in cfg.get("parses", []) if isinstance(p, dict)]

    names = Counter(str(s.get("name") or "") for s in sites)
    r = {
        "file": os.path.basename(path),
        "label": (meta.get("polish") or {}).get("label") or meta.get("note") or "",
        "sites": len(sites),
        "lives": len(lives),
        "parses": len(parses),
        "kb": round(os.path.getsize(path) / 1024),
        "empty_api": sum(1 for s in sites if not str(s.get("api") or "").strip()),
        "illegal_api": sum(1 for s in sites
                           if not API_RX.match(str(s.get("api") or "").strip())),
        "dup_names": sum(v - 1 for v in names.values() if v > 1),
        "bad_parse_url": sum(1 for p in parses
                             if not re.match(r"^https?://", str(p.get("url") or ""))),
        "non_int_parse_type": sum(1 for p in parses if not isinstance(p.get("type"), int)),
        "non_int_live_type": sum(1 for p in lives if not isinstance(p.get("type"), int)),
        "live_dup": len(lives) - len({str(g.get("url")) for g in lives}),
        "spider": bool(cfg.get("spider")),
        "jar_sites": sum(1 for s in sites if str(s.get("jar") or "").strip()),
        "csp_sites": sum(1 for s in sites
                         if str(s.get("api") or "").lower().startswith("csp_")),
        "csp_no_ext": sum(1 for s in sites
                          if str(s.get("api") or "").lower().startswith("csp_")
                          and not s.get("ext")),
        "polish": meta.get("polish") or {},
    }
    hosts = set()
    for s in sites:
        h = polish_mod.offshore_host(s, deep=True)
        if h:
            hosts.add(h)
    r["offshore_hosts"] = sorted(hosts)
    r["offshore_n"] = sum(1 for s in sites if polish_mod.offshore_host(s, deep=True))
    # 首页必空白的节点占比（全量版里最影响观感的一类）
    r["csp_no_ext_ratio"] = (r["csp_no_ext"] / r["sites"]) if r["sites"] else 0
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default="reports/audit-latest.md")
    args = ap.parse_args()

    rows = [a for a in (audit_file(f) for f in TARGETS) if a]
    if not rows:
        print("没有可复查的配置")
        return 0

    L = []
    L.append("# 三份基础配置复查报告\n")
    env = common.env_info()
    L.append("- 生成时间：%s（环境 `%s`）" % (env["generated"], env["env"]))
    L.append("- 复查对象：`tvbox.json`（全量）/ `tvbox-lite.json`（精简）/ "
             "`tvbox-network.json`（网络优化）")
    L.append("- 复查方式：读生成后的文件重新统计，不复用生成时的内部状态\n")

    L.append("## 一目了然\n")
    L.append("| 版本 | 站点 | 体积 | 剔除 | 改名 | 可用 / 未知 / 降级 | "
             "依赖 jar | 境外托管 | 空或非法 api | 重名 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        p = r["polish"]
        removed = sum((p.get("removed") or {}).values())
        order = p.get("order") or {}
        L.append("| %s `%s` | %d | %d KB | %d | %d | %s / %s / %s | %s | %s | %d | %d |" % (
            r["label"], r["file"], r["sites"], r["kb"], removed, p.get("renamed", 0),
            order.get("usable", "-"), order.get("unknown", "-"),
            order.get("degraded", "-"),
            "是" if (r["spider"] or r["jar_sites"]) else "否",
            ("%d 台主机" % len(r["offshore_hosts"])) if r["offshore_hosts"] else "无",
            r["empty_api"] + r["illegal_api"], r["dup_names"]))

    L.append("\n## 本轮修掉的问题\n")
    for r in rows:
        p = r["polish"]
        removed = p.get("removed") or {}
        L.append("")
        L.append("### %s `%s`" % (r["label"], r["file"]))
        L.append("")
        if not removed and not p.get("renamed"):
            L.append("- 无需修复，本轮未改动\n")
            continue
        meaning = {
            "empty_api": "`--- 1 ---` 这类分隔符被上游当成站点写进 sites，api 为空",
            "illegal_api": "api 不是 http(s):// 或 csp_ 开头，客户端无法加载",
            "no_name": "名称为空且 api 里取不到域名",
            "dup_api": "同一接口的不同写法（参数顺序/大小写差异）重复收录",
            "jar_dep": "依赖 jar 的站点，与「不加载 jar」的版本目标冲突",
            "offshore": "api / jar / ext 落在境外代码托管或反代平台",
        }
        for k, v in removed.items():
            L.append("- 剔除 **%d** 个：%s" % (v, meaning.get(k, k)))
        if p.get("renamed"):
            L.append("- 同名消歧 **%d** 个：追加域名后缀，列表里不再出现多条一样的「AList」"
                     % p["renamed"])
        sp = p.get("spider_removed")
        if sp:
            L.append("- 移除全局 `spider` 字段（原指向 `raw.githubusercontent.com`，"
                     "国内盒子拉不到会让所有爬虫节点首页空白）")
        lv, ps = p.get("lives") or {}, p.get("parses") or {}
        if lv.get("type_fixed") or lv.get("renamed"):
            L.append("- 直播：修正 `type` 非整数 %d 个、重名消歧 %d 个"
                     % (lv.get("type_fixed", 0), lv.get("renamed", 0)))
        if ps.get("dropped") or ps.get("type_fixed") or ps.get("capped"):
            L.append("- 解析：剔除 url 非法/重复 %d 个、`type` 归一 %d 个、"
                     "截断到上限 %d 个"
                     % (ps.get("dropped", 0), ps.get("type_fixed", 0), ps.get("capped", 0)))
        dr = p.get("degraded_reason") or {}
        if dr:
            L.append("- 排序：`%s` 这类节点沉到末尾但**不删除**，误杀可在次日复核复活"
                     % " / ".join("%s×%d" % (k, v) for k, v in list(dr.items())[:3]))

    L.append("\n## 残留检查\n")
    L.append("| 检查项 | " + " | ".join(r["label"] for r in rows) + " |")
    L.append("|---" * (len(rows) + 1) + "|")
    checks = [
        ("空 api", lambda r: r["empty_api"]),
        ("非法 api", lambda r: r["illegal_api"]),
        ("重名节点", lambda r: r["dup_names"]),
        ("解析 url 非法", lambda r: r["bad_parse_url"]),
        ("解析 type 非整数", lambda r: r["non_int_parse_type"]),
        ("直播 type 非整数", lambda r: r["non_int_live_type"]),
        ("直播 url 重复", lambda r: r["live_dup"]),
    ]
    for name, fn in checks:
        L.append("| %s | %s |" % (name, " | ".join(
            "0" if fn(r) == 0 else "**%d**" % fn(r) for r in rows)))
    L.append("| 全局 spider | %s |" % " | ".join(
        "有" if r["spider"] else "无" for r in rows))
    L.append("| 带 jar 站点 | %s |" % " | ".join(str(r["jar_sites"]) for r in rows))
    L.append("| csp 缺 ext（首页多为空） | %s |" % " | ".join(
        "%d（%.0f%%）" % (r["csp_no_ext"], r["csp_no_ext_ratio"] * 100) for r in rows))
    L.append("| 境外托管主机 | %s |" % " | ".join(
        (str(len(r["offshore_hosts"]))) if r["offshore_hosts"] else "0" for r in rows))

    L.append("\n## 怎么读这份报告\n")
    L.append("- **可用 / 未知 / 降级** 是排序分层，不是删除：降级层只是排到后面，"
             "站点仍在配置里，客户端手动滑到底还能选到。")
    L.append("- **降级**包含「昨日实测首页失败」和「csp 爬虫但没有 ext」。"
             "前者有 36%~52% 的误杀率，所以只沉底、不剔除。")
    L.append("- **精简版 / 网络优化版的依赖 jar 应为 0**：这两版删掉了全局 spider 与"
             "所有 jar 站点；`type=3` 里 api 是 `.js` / `.py` 的属于 JS/Python 爬虫，"
             "不需要 jar，予以保留。")
    L.append("- **改名站点**的历史签名会变，当天回到「未知」层，次日自动接回。")

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")

    print("=== 复查摘要 ===")
    for r in rows:
        p = r["polish"]
        print("  %-6s %-24s 站点 %4d | 剔除 %2d | 改名 %2d | jar %s | 境外 %d | 重名 %d"
              % (r["label"], r["file"], r["sites"],
                 sum((p.get("removed") or {}).values()), p.get("renamed", 0),
                 "有" if (r["spider"] or r["jar_sites"]) else "无",
                 len(r["offshore_hosts"]), r["dup_names"]))
    print("报告已写入 %s" % args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
