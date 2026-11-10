#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""三份基础配置的后处理：复查 → 修复 → 排序。

build.py 只负责「把上游合起来」，它做的是结构性去重，不管成品的卫生状况。
实测下来三份成品各自都带着会让用户直接踩坑的瑕疵：

  全量版   8 个 `--- 1 ---` 分隔符被当成站点（api 为 null）、15 个非法 api、
           21 组同名节点（AList×3、推送×3…）、1 个 ext 是整数的畸形站点、
           516 个「csp 爬虫但没 ext」的节点（点进去必然空白）；
           而且整份按名称拼音排序 —— 昨日实测失败的 860 个节点排在最前面。
  精简版   号称「不依赖 jar」，却仍带 spider 全局 jar 与 6 个 jar 站点。
  网络优化版  同一个 spider 指向 raw.githubusercontent.com，等于所有爬虫节点
           都要去境外拉 jar；另有 repl.co 这类反代域名漏网。

本脚本按「每份版本自己的目标」做后处理，文件名与线上链接保持不变：

  1. 卫生：丢弃空 / 非法 api、整型 ext、`--- 1 ---` 占位符；parses 的 url 必须
     真的以 http 开头（上游里混进了 url 叫 "Demo" 的占位项）
  2. 去重：http 接口按「域名 + 路径 + 参数排序」归一后去重；csp 按 api + 名称
  3. 消歧：同名节点追加域名后缀，避免列表里出现三条一模一样的「AList」
  4. 排序：可用 → 未知 → 已判定失败 / 首页必空白。只沉底不删除，误杀可在
     次日复核复活；根因是「按拼音排」会让用户第一屏全是死节点
  5. 精简 / 网络版：真正去掉 spider 与所有 jar 依赖。注意 type=3 里 api 是
     .js/.py 的属于 JS / Python 爬虫，不需要 jar，保留
  6. 网络版：境外托管黑名单加严（ghproxy / gitmirror / vercel / netlify /
     pages.dev / repl.co 等反代与托管平台）

安全：非 Actions 环境只处理 _local-* 临时文件；任何一份被削掉超过一半时
直接放弃改动，避免一次误判把线上配置清空。

用法：python scripts/polish.py [--dry-run]
"""
import argparse
import glob
import json
import os
import re
import sys
from collections import Counter
from urllib.parse import parse_qsl, urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common

TARGETS = ["tvbox.json", "tvbox-lite.json", "tvbox-network.json"]

# 每一份版本自己的目标。改这里的口径要同步改 README 的版本表。
POLICIES = {
    "tvbox.json": {
        "label": "全量版",
        "jar_free": False,          # 保留 spider 与所有 jar 站点
        "strict_offshore": False,   # 不管代码托管在哪，功能优先
        "parse_types": None,        # 解析通道全留（含 fork 私有类型）
        "parse_cap": None,
    },
    "tvbox-lite.json": {
        "label": "精简版",
        "jar_free": True,           # 真·不加载 jar，低性能盒子才不会卡首页
        "strict_offshore": False,
        "parse_types": (0, 1),      # 只留通用 webview / json 解析
        "parse_cap": 80,
    },
    "tvbox-network.json": {
        "label": "网络优化版",
        "jar_free": True,
        "strict_offshore": True,    # 国内直连最稳
        "parse_types": (0, 1),
        "parse_cap": 60,
    },
}

# 代码托管类：命中即视为需要「翻出去」才能拉到 jar / ext
OFFSHORE_BASE = (
    "raw.githubusercontent.com", "githubusercontent.com", "github.com",
    "gitlab.com", "gitee.com", "jsdelivr.net", "github.io", "gitlab.io",
    "bitbucket.org", "sourceforge.net", "raw.gitmirror.com",
)
# 反代 / 免费托管平台：域名本身不在上面，但同样是国内不稳的境外设施
OFFSHORE_KEYS = (
    "ghproxy", "gh-proxy", "gitmirror", "fastgit", "kgithub", "moeyy",
    "vercel.app", "netlify.app", "pages.dev", "workers.dev", "herokuapp",
    "glitch.me", "repl.co", "onrender.com", "fly.dev", "surge.sh",
    "azurewebsites.net", "appspot.com", "deno.dev", "val.town",
    "ngrok", "serveo.net", "localtunnel",
)

API_RX = re.compile(r"^(?:https?://\S+|csp_\S+)$", re.I)
PLACEHOLDER_RX = re.compile(r"^(?:[-—=_*·\s]{2,}\d*[-—=_*·\s]*|\d+)$")
HOST_RX = re.compile(r"https?://([^/\s\"'<>:,#]+)")


def hosts_of(site):
    """把 api / jar / ext 里出现的所有域名抽出来（ext 可能是 dict）。"""
    out = set()
    for field in ("api", "jar", "ext"):
        v = site.get(field)
        if isinstance(v, (dict, list)):
            v = json.dumps(v, ensure_ascii=False)
        v = str(v or "")
        for h in HOST_RX.findall(v):
            out.add(h.lower())
    return out


def offshore_host(site, deep=False):
    """命中境外托管返回域名，否则 None。deep=True 时连反代 / 免费托管一起算。"""
    for h in hosts_of(site):
        if any(h == d or h.endswith("." + d) for d in OFFSHORE_BASE):
            return h
        if deep and any(k in h for k in OFFSHORE_KEYS):
            return h
    return None


def clean_site(site):
    """返回 (清洗后的站点, 丢弃原因)。"""
    s = dict(site)
    api = s.get("api")
    if not isinstance(api, str) or not api.strip():
        return None, "empty_api"
    api = api.strip()
    if not API_RX.match(api) or re.search(r"\s", api):
        return None, "illegal_api"
    s["api"] = api

    name = str(s.get("name") or "").strip()
    if not name or PLACEHOLDER_RX.match(name):
        host = urlsplit(api).netloc
        if not host:
            return None, "no_name"
        name = host
    s["name"] = name

    try:
        s["type"] = int(s.get("type") or 0)
    except (TypeError, ValueError):
        s["type"] = 0

    ext = s.get("ext")
    if isinstance(ext, bool) or isinstance(ext, (int, float)):
        s.pop("ext", None)                      # 上游把 ext 写成 1，客户端会崩
    elif isinstance(ext, str) and not ext.strip():
        s.pop("ext", None)
    return s, None


def site_key(site):
    """去重键。参数顺序不同但指向同一接口的，视为同一个站点。"""
    api = site["api"]
    if api.lower().startswith("csp_"):
        return ("csp", api.lower(), norm(site["name"]))
    p = urlsplit(api)
    q = tuple(sorted(parse_qsl(p.query, keep_blank_values=True)))
    return ("api", p.netloc.lower(), p.path.rstrip("/").lower(), q)


def norm(name):
    return re.sub(r"[\s\[\]（）()【】┃｜|·•・\-—_*]+", "", str(name or ""))


def policy_drop(site, pol):
    """按版本目标剔除。返回原因或 None。"""
    api = site["api"]
    if pol["jar_free"]:
        if str(site.get("jar") or "").strip():
            return "jar_dep"
        if api.lower().startswith("csp_"):
            return "jar_dep"
    if pol["strict_offshore"] and offshore_host(site, deep=True):
        return "offshore"
    return None


def tier_of(site, stab, revived=()):
    """0 可用 / 1 未知 / 2 已判定失败或首页必空白。"""
    sg = common.sig(site)
    st = stab.get(sg) or {}
    try:
        streak = int(st.get("fail_streak") or 0)
    except (TypeError, ValueError):
        streak = 0
    grade = str(st.get("last_grade") or "")
    if str(site.get("api", "")).lower().startswith("csp_") and not site.get("ext"):
        return 2, "csp_no_ext"
    if streak >= 2:
        return 2, "fail_streak"
    if grade == "fail":
        # 昨日已用真实 TVBox UA 复核复活的，不要因为一次误杀就沉底
        return (1, "revived") if sg in revived else (2, "grade_fail")
    if grade in ("ok", "likely"):
        return 0, grade
    return 1, grade or "no_history"


def load_revived():
    """读最近一份历史快照里的「误杀复活」名单。"""
    files = sorted(glob.glob(os.path.join("history", "*.json")))
    if not files:
        return set()
    d = common.load_json(files[-1], {}) or {}
    return set((d.get("recheck") or {}).get("revived_sigs") or [])


def unique_name(base, tag, taken):
    """给同名节点加标识后缀；标识仍冲突再补序号。返回可用的新名字。

    标识优先用域名；csp_ 爬虫没有域名（api 就是 csp_XBPQ），退而用 ext 的域名，
    再不行用爬虫类名 —— 否则会退化成一串没有信息量的「xxx (2)(3)」。
    """
    cand = "%s · %s" % (base, tag) if tag else base
    k = 2
    while cand in taken:
        cand = "%s · %s (%d)" % (base, tag or "src", k)
        k += 1
    taken.add(cand)
    return cand


def name_tag(site):
    api = str(site.get("api") or "")
    if api.lower().startswith("csp_"):
        ext = site.get("ext")
        if isinstance(ext, str):
            m = HOST_RX.search(ext)
            if m:
                return re.sub(r"^www\.", "", m.group(1))
        return api[4:] or "csp"
    return re.sub(r"^www\.", "", urlsplit(api).netloc or "")


def fix_lives(lives, dup_names):
    out, seen, dropped, type_fixed = [], set(), 0, 0
    for g in lives:
        if not isinstance(g, dict):
            dropped += 1
            continue
        u = str(g.get("url") or "").strip()
        if not re.match(r"^https?://", u, re.I) or u in seen:
            dropped += 1
            continue
        seen.add(u)
        ng = dict(g)
        ng["url"] = u
        ng["name"] = str(ng.get("name") or "").strip() or (urlsplit(u).netloc or "直播源")
        t = ng.get("type")
        if not isinstance(t, int):
            try:
                ng["type"] = int(str(t).strip())
            except (TypeError, ValueError):
                ng["type"] = 0
            type_fixed += 1
        out.append(ng)

    renamed = 0
    taken = set(ng["name"] for ng in out if ng["name"] not in dup_names)
    for ng in out:
        if ng["name"] in dup_names:
            host = re.sub(r"^www\.", "", urlsplit(ng["url"]).netloc or "")
            if host.split(":")[0] in ("127.0.0.1", "localhost", "0.0.0.0"):
                host = "内置"          # 本机代理类直播源，写 127.0.0.1 没有信息量
            ng["name"] = unique_name(ng["name"], host, taken)
            renamed += 1
    return out, {"dropped": dropped, "type_fixed": type_fixed, "renamed": renamed}


def fix_parses(parses, types, cap):
    out, seen, dropped, fixed = [], set(), 0, 0
    for p in parses:
        if not isinstance(p, dict):
            dropped += 1
            continue
        u = str(p.get("url") or "").strip()
        if not re.match(r"^https?://", u, re.I):
            dropped += 1                      # url 写的是 "Demo" 这类占位
            continue
        np_ = dict(p)
        np_["url"] = u
        t = np_.get("type")
        if not isinstance(t, int):
            try:
                np_["type"] = int(str(t).strip())
            except (TypeError, ValueError):
                np_["type"] = 0
            fixed += 1
        if types is not None and np_["type"] not in types:
            dropped += 1
            continue
        k = u.split(";")[0].rstrip("/").lower()
        if k in seen:
            dropped += 1
            continue
        seen.add(k)
        out.append(np_)
    capped = 0
    if cap and len(out) > cap:
        capped = len(out) - cap
        out = out[:cap]
    return out, {"dropped": dropped, "type_fixed": fixed, "capped": capped}


def target_paths():
    """非 Actions 环境只碰 _local-* 临时文件，绝不改线上配置。"""
    if common.IS_CI or os.environ.get("ALLOW_LOCAL_WRITE") == "1":
        return list(TARGETS)
    local = [("_local-" + f) for f in TARGETS if os.path.exists("_local-" + f)]
    if local:
        print("本地试跑：只处理 _local-* 临时文件")
        return local
    print("本地试跑保护：没有 _local-* 文件，跳过（ALLOW_LOCAL_WRITE=1 可强制）")
    return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    quality = common.load_json("quality.json", {}) or {}
    stab = quality.get("site_stability", {}) or {}
    revived = load_revived()

    paths = target_paths()
    if not paths:
        return 0

    # 同名清单以全量版为准：三份版本共用同一套后缀，避免某个版本因筛掉
    # 一个重名节点导致另一个节点名字来回变（那会让历史签名天天断链）。
    dup_names, live_dup = set(), set()
    full = common.load_json(paths[0]) if paths else None
    if isinstance(full, dict):
        cnt = Counter(str(s.get("name") or "").strip()
                      for s in full.get("sites", []) if isinstance(s, dict))
        dup_names = set(k for k, v in cnt.items() if v > 1)
        lcnt = Counter(str(g.get("name") or "").strip()
                       for g in full.get("lives", []) if isinstance(g, dict))
        live_dup = set(k for k, v in lcnt.items() if v > 1)

    summary = {}
    print("=== 三份基础配置后处理 ===", flush=True)
    for path in paths:
        base = os.path.basename(path).replace("_local-", "")
        pol = POLICIES.get(base)
        if not pol:
            continue
        cfg = common.load_json(path)
        if not isinstance(cfg, dict) or "sites" not in cfg:
            print("  %-24s 读不到，跳过" % base)
            continue

        raw = [s for s in cfg.get("sites", []) if isinstance(s, dict)]
        reasons, kept = Counter(), []
        for s in raw:
            ns, why = clean_site(s)
            if why:
                reasons[why] += 1
                continue
            why = policy_drop(ns, pol)
            if why:
                reasons[why] += 1
                continue
            kept.append(ns)

        seen, uniq = set(), []
        for s in kept:
            k = site_key(s)
            if k in seen:
                reasons["dup_api"] += 1
                continue
            seen.add(k)
            uniq.append(s)

        # 兜底：一次误判把配置削掉一半以上，宁可不动
        if raw and len(uniq) < len(raw) * 0.5:
            print("  %-24s 过滤后仅剩 %d/%d，放弃改动（疑似口径写错）"
                  % (base, len(uniq), len(raw)))
            summary[base] = {"aborted": True, "sites": len(raw)}
            continue

        tiers, tags = {}, Counter()
        for s in uniq:
            t, why = tier_of(s, stab, revived)
            tiers[id(s)] = t
            if t == 2:
                tags[why] += 1
        uniq.sort(key=lambda s: (tiers[id(s)], str(s.get("name")), str(s.get("api"))))

        # 同名消歧：全量版的重名清单 + 本版本自身的重名
        local_dup = set(k for k, v in Counter(s["name"] for s in uniq).items() if v > 1)
        taken, renamed = set(), 0
        for s in uniq:
            if s["name"] in dup_names or s["name"] in local_dup:
                s["name"] = unique_name(s["name"], name_tag(s), taken)
                renamed += 1
            else:
                taken.add(s["name"])

        lives, live_stat = fix_lives(cfg.get("lives", []) or [], live_dup)
        parses, parse_stat = fix_parses(cfg.get("parses", []) or [],
                                        pol["parse_types"], pol["parse_cap"])

        spider = cfg.get("spider")
        spider_removed = bool(spider) and pol["jar_free"]
        out = {
            "_meta": dict(cfg.get("_meta") or {}),
            "sites": uniq,
        }
        if spider and not spider_removed:
            out["spider"] = spider
        for key in ("wallpaper",):
            if cfg.get(key):
                out[key] = cfg[key]
        if parses:
            out["parses"] = parses
        if lives:
            out["lives"] = lives

        out["_meta"]["sites"] = len(uniq)
        out["_meta"]["polish"] = {
            "version": 1,
            "label": pol["label"],
            "removed": {k: v for k, v in sorted(reasons.items())},
            "renamed": renamed,
            "order": {"usable": sum(1 for v in tiers.values() if v == 0),
                      "unknown": sum(1 for v in tiers.values() if v == 1),
                      "degraded": sum(1 for v in tiers.values() if v == 2)},
            "degraded_reason": dict(tags.most_common(4)),
            "jar_free": pol["jar_free"],
            "spider_removed": spider_removed,
            "lives": live_stat,
            "parses": parse_stat,
            "history_days": quality.get("days_recorded"),
        }
        summary[base] = out["_meta"]["polish"]

        if args.dry_run:
            print("  %-24s sites=%d→%d %s" % (base, len(raw), len(uniq),
                                               out["_meta"]["polish"]["removed"]))
            continue
        common.write_json(path, out)

        print("  %-24s 站点 %4d → %4d | 剔除 %s | 改名 %d | 可用 %d / 未知 %d / 降级 %d"
              % (base, len(raw), len(uniq),
                 json.dumps(out["_meta"]["polish"]["removed"], ensure_ascii=False),
                 renamed,
                 out["_meta"]["polish"]["order"]["usable"],
                 out["_meta"]["polish"]["order"]["unknown"],
                 out["_meta"]["polish"]["order"]["degraded"]))
        print("  %-24s 直播 %d（修 type %d / 改名 %d）| 解析 %d（剔 %d 修 %d 截 %d）| 去 spider %s"
              % ("", len(lives), live_stat["type_fixed"], live_stat["renamed"],
                 len(parses), parse_stat["dropped"], parse_stat["type_fixed"],
                 parse_stat["capped"], spider_removed))

    return 0


if __name__ == "__main__":
    sys.exit(main())
