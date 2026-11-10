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
        # 客户端解析契约：这几项出问题，用户看到的是「站点变少 / 配置打不开」
        "dup_key": sum(v - 1 for v in Counter(
            str(s.get("key") or "") for s in sites).values() if v > 1)
        + sum(1 for s in sites if not str(s.get("key") or "")),
        "ext_obj": sum(1 for o in sites + parses if isinstance(o.get("ext"), (dict, list))),
        "parse_dup_names": sum(v - 1 for v in Counter(
            str(p.get("name") or "") for p in parses).values() if v > 1),
        "junk_fields": sum(1 for s in sites if "类型" in s or "playurl" in s),
        "meta_mismatch": sum(1 for field, coll in (("sites", sites), ("lives", lives),
                                                   ("parses", parses))
                            if meta.get(field) is not None and meta[field] != len(coll)),
        "wallpaper": cfg.get("wallpaper") or "",
    }
    hosts = set()
    for s in sites:
        h = common.hosting_host(s, deep=True)
        if h:
            hosts.add(h)
    r["hosting_hosts"] = sorted(hosts)
    r["hosting_n"] = sum(1 for s in sites if common.hosting_host(s, deep=True))
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
             "依赖 jar | 配置托管在境外 | 空或非法 api | 重名 |")
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
            ("%d 台主机" % len(r["hosting_hosts"])) if r["hosting_hosts"] else "无",
            r["empty_api"] + r["illegal_api"], r["dup_names"]))

    L.append("\n## 本轮修掉的问题")
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
            "dup_api": "同一接口的不同写法（参数顺序/大小写差异）重复收录（历史口径）",
            "dup_content": "名称不同、但内容指向同一处：emoji/装饰符换皮名，"
                           "raw / jsdelivr / gh-proxy / github blob 链接别名，"
                           "www / 协议 / 尾斜杠 / 参数顺序差异",
            "jar_dep": "依赖 jar 的站点，与「不加载 jar」的版本目标冲突",
            "hosting": "api / jar / ext 落在境外代码托管或 GitHub 反代（与片源内容无关）",
        }
        for k, v in removed.items():
            L.append("- 剔除 **%d** 个：%s" % (v, meaning.get(k, k)))
        if p.get("renamed"):
            L.append("- 同名消歧 **%d** 个：追加域名后缀，列表里不再出现多条一样的「AList」"
                     % p["renamed"])
        if p.get("keys_renamed"):
            L.append("- 主键去重 **%d** 个：`key` 是客户端唯一主键，重复会让后一个站点"
                     "顶掉前一个（列表里少站点、收藏串号），保留第一个、其余加稳定后缀"
                     % p["keys_renamed"])
        lv, ps = p.get("lives") or {}, p.get("parses") or {}
        if lv.get("dup_content") or ps.get("dup_content"):
            L.append("- 直播 / 解析按内容指向去重 **%d / %d** 个：同一份 `live.txt` / 解析脚本"
                     "挂在 raw 与 gh-proxy 上，或名称被换成「南风直播源」「Yoursmile」，"
                     "归一后只保留一条" % (lv.get("dup_content", 0),
                                          ps.get("dup_content", 0)))
        if ps.get("ext_fixed") or p.get("ext_normalized"):
            L.append("- `ext` 归一 **%d** 个站点 / **%d** 个解析通道：上游把 ext 写成 JSON "
                     "对象，严格解析的客户端会判定整份配置非法，统一改成字符串"
                     % (p.get("ext_normalized", 0), ps.get("ext_fixed", 0)))
        if p.get("wallpaper_removed"):
            L.append("- 移除 `wallpaper`：上游壁纸地址在国内打不开（实测超时），"
                     "留着只会让开屏先卡几秒再退回内置背景")
        sp = p.get("spider_removed")
        if sp:
            L.append("- 移除全局 `spider` 字段（原指向 `raw.githubusercontent.com`，"
                     "国内盒子拉不到会让所有爬虫节点首页空白）")
        if lv.get("type_fixed") or lv.get("renamed") or lv.get("offshore_moved"):
            L.append("- 直播：修正 `type` 非整数 %d 个、重名消歧 %d 个、"
                     "境外托管沉底 %d 条"
                     % (lv.get("type_fixed", 0), lv.get("renamed", 0),
                        lv.get("offshore_moved", 0)))
        if ps.get("dropped") or ps.get("type_fixed") or ps.get("capped"):
            kept = ps.get("cap_kept_types") or {}
            L.append("- 解析：剔除 url 非法/重复 %d 个、`type` 归一 %d 个、"
                     "按配额截断 %d 个%s"
                     % (ps.get("dropped", 0), ps.get("type_fixed", 0), ps.get("capped", 0),
                        ("（保留 json %d / webview %d）" %
                         (kept.get("1", 0), kept.get("0", 0))) if kept else ""))
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
        ("重复/空 key", lambda r: r["dup_key"]),
        ("ext 仍是对象", lambda r: r["ext_obj"]),
        ("重名解析通道", lambda r: r["parse_dup_names"]),
        ("客户端不认的字段", lambda r: r["junk_fields"]),
        ("元信息条数不符", lambda r: r["meta_mismatch"]),
        ("解析 url 非法", lambda r: r["bad_parse_url"]),
        ("解析 type 非整数", lambda r: r["non_int_parse_type"]),
        ("直播 type 非整数", lambda r: r["non_int_live_type"]),
        ("直播 url 重复", lambda r: r["live_dup"]),
    ]
    for name, fn in checks:
        L.append("| %s | %s |" % (name, " | ".join(
            "0" if fn(r) == 0 else "**%d**" % fn(r) for r in rows)))
    L.append("| 壁纸 | %s |" % " | ".join(
        "有" if r["wallpaper"] else "未配置" for r in rows))
    L.append("| 全局 spider | %s |" % " | ".join(
        "有" if r["spider"] else "无" for r in rows))
    L.append("| 带 jar 站点 | %s |" % " | ".join(str(r["jar_sites"]) for r in rows))
    L.append("| csp 缺 ext（首页多为空） | %s |" % " | ".join(
        "%d（%.0f%%）" % (r["csp_no_ext"], r["csp_no_ext_ratio"] * 100) for r in rows))
    L.append("| 配置托管在境外的主机 | %s |" % " | ".join(
        (str(len(r["hosting_hosts"]))) if r["hosting_hosts"] else "0" for r in rows))

    L.append("\n## 怎么读这份报告\n")
    L.append("- **可用 / 未知 / 降级** 是排序分层，不是删除：降级层只是排到后面，"
             "站点仍在配置里，客户端手动滑到底还能选到。")
    L.append("- **降级**包含「昨日实测首页失败」和「csp 爬虫但没有 ext」。"
             "前者有 36%~52% 的误杀率，所以只沉底、不剔除。")
    L.append("- **精简版 / 网络优化版的依赖 jar 应为 0**：这两版删掉了全局 spider 与"
             "所有 jar 站点；`type=3` 里 api 是 `.js` / `.py` 的属于 JS/Python 爬虫，"
             "不需要 jar，予以保留。")
    L.append("- **改名站点**的历史签名会变，当天回到「未知」层，次日自动接回。")
    L.append("- **身份签名 = 名称 + api + jar + ext（不含 key）**：所以主键去重、ext 归一"
             "都不会让整批站点历史断链；只有改名会（它本来就是另一个名字了）。")
    L.append("- **`ext` 必须是字符串**：TVBox 原版用 Gson 严格解析，对象型 ext 会让整份"
             "配置报「格式错误」，不是单个站点打不开。")

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")

    print("=== 复查摘要 ===")
    for r in rows:
        p = r["polish"]
        print("  %-6s %-24s 站点 %4d | 剔除 %2d | 改名 %2d | jar %s | 托管境外 %d | 重名 %d"
              % (r["label"], r["file"], r["sites"],
                 sum((p.get("removed") or {}).values()), p.get("renamed", 0),
                 "有" if (r["spider"] or r["jar_sites"]) else "无",
                 len(r["hosting_hosts"]), r["dup_names"]))
    print("报告已写入 %s" % args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
