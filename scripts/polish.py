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
           都要去拉代码托管上的 jar；另有 repl.co 这类反代域名漏网。

本脚本按「每份版本自己的目标」做后处理，文件名与线上链接保持不变：

  1. 卫生：丢弃空 / 非法 api、整型 ext、`--- 1 ---` 占位符；parses 的 url 必须
     真的以 http 开头（上游里混进了 url 叫 "Demo" 的占位项）
  2. 归一：ext 统一成字符串（382 个站点 + 104 个解析通道原本是 JSON 对象，
     严格解析的客户端会因此判定整份配置非法）；`playurl` → `playUrl`；丢掉
     上游抄来的 `类型` 这类客户端不认的字段
  3. 主键：`key` 是全客户端唯一主键，174 组重名会让后一个站点顶掉前一个，
     保留第一个、其余加稳定后缀
  4. 去重：http 接口按「域名 + 路径 + 参数排序」归一后去重；csp 按 api + 名称
  5. 消歧：同名节点追加域名后缀，避免列表里出现三条一模一样的「AList」
  6. 排序：可用 → 未知 → 已判定失败 / 首页必空白。只沉底不删除，误杀可在
     次日复核复活；根因是「按拼音排」会让用户第一屏全是死节点
  7. 精简 / 网络版：真正去掉 spider 与所有 jar 依赖。注意 type=3 里 api 是
     .js/.py 的属于 JS / Python 爬虫，不需要 jar，保留
  8. 网络版：按「配置托管地」过滤 —— 只留 api/jar/ext 都不落在境外代码托管
     （GitHub / GitLab 等）与 GitHub 反代上的节点。判断的是「配置住在哪」，
     与片源内容无关，影视站本身（含 Netflix 类）不在过滤范围
  9. 元信息：`_meta.sites/lives/parses/jar_count` 按本版本实际内容重算 —— 之前
     直接沿用全量版数字，精简版写着 108 条直播、实际只有 101 条
 10. 壁纸：地址拉不到就整段去掉，客户端退回内置背景，比开屏卡十秒好
 11. 直播：列表顺序就是客户端的显示顺序，没有排序功能。走 GitHub / 反代的
     40 条国内必然空频道，稳定沉到末尾（不删）
 12. 解析截断：按「type=1 且带 ext」优先排序后再截，别一刀切掉最好用的通道

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
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common

TARGETS = ["tvbox.json", "tvbox-lite.json", "tvbox-network.json"]

# 每一份版本自己的目标。改这里的口径要同步改 README 的版本表。
POLICIES = {
    "tvbox.json": {
        "label": "全量版",
        "jar_free": False,          # 保留 spider 与所有 jar 站点
        "hosting_strict": False,    # 不管配置托管在哪，功能优先
        "parse_types": None,        # 解析通道全留（含 fork 私有类型）
        "parse_cap": None,
    },
    "tvbox-lite.json": {
        "label": "精简版",
        "jar_free": True,           # 真·不加载 jar，低性能盒子才不会卡首页
        "hosting_strict": False,
        "parse_types": (0, 1),      # 只留通用 webview / json 解析
        "parse_cap": 80,
    },
    "tvbox-network.json": {
        "label": "网络优化版",
        "jar_free": True,
        "hosting_strict": True,     # 国内直连最稳：配置不落在代码托管/反代上
        "parse_types": (0, 1),
        "parse_cap": 60,
    },
}

# 配置托管地黑名单已收敛到 common.py（唯一口径，build / variants / 本脚本共用）。
# 这里只检查 api / jar / ext 的域名，与片源内容无关。
hosts_of = common.hosts_of
HOST_RX = common.HOST_RX

API_RX = re.compile(r"^(?:https?://\S+|csp_\S+)$", re.I)
PLACEHOLDER_RX = re.compile(r"^(?:[-—=_*·\s]{2,}\d*[-—=_*·\s]*|\d+)$")

# 上游从别的客户端抄过来的非标准字段：TVBox / FongMi 都不认，留着只会让
# 站点对象变形（个别 fork 用 Gson 严格解析，多出来的中文键会一起报错）。
JUNK_FIELDS = ("类型",)
FIELD_ALIAS = {"playurl": "playUrl"}      # 只认驼峰，小写那份等于没写
EMPTY_EXT = ("", "0", "null", "none", "{}", "[]", '""')

# 媒体 / 图片类扩展名：这些永远不可能是 TVBox 的 spider jar。上游却把 .jpg/.png
# 之类塞进 jar 槽（实测 352 个站点），客户端照拉必崩。合法 jar 常以 .jar/.js/.txt/
# .php/无扩展/动态端点出现，一律保留 —— 只看路径后缀，且忽略 ? 查询与 # 片段，
# 避免 "getFile?id=xxx#.png" 这种动态端点被误判成图片。
JAR_MEDIA_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg",
                 ".ico", ".mp4", ".css", ".html")


def norm_jar(v):
    """清洗 jar 值：去掉前后空白；媒体类（图片）当作「没有 jar」返回空串。"""
    if not isinstance(v, str):
        return ""
    v = v.strip()
    if not v:
        return ""
    base = v.split(";", 1)[0]               # 去掉可能的 ;md5;hash 后缀
    path = urlsplit(base).path.lower()       # 只取路径，忽略 ? 与 #
    if any(path.endswith(e) for e in JAR_MEDIA_EXT):
        return ""
    return v


def clean_site(site):
    """返回 (清洗后的站点, 丢弃原因)。"""
    s = {k: v for k, v in site.items() if k not in JUNK_FIELDS}
    for bad, good in FIELD_ALIAS.items():
        if bad in s:
            if not s.get(good):
                s[good] = s.pop(bad)
            else:
                s.pop(bad)
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

    s["key"] = str(s.get("key") or "").strip()

    # jar 同样要去掉前后空白；并且「图片 / 媒体文件」不可能是 TVBox 的 spider
    # jar，上游却把 .jpg/.png 之类塞进 jar 槽（实测 352 个站点），客户端照拉必崩。
    # 这里把媒体类 jar 直接当「没有 jar」处理（保留合法 .jar/.js/.txt/.php 等）。
    s["jar"] = norm_jar(s.get("jar") or "")

    # ext 必须是一段字符串。上游有 382 个站点把 ext 写成 JSON 对象，
    # Gson 严格解析的客户端（TVBox 原版）会直接判定整份配置非法。
    ext = s.get("ext")
    if isinstance(ext, bool) or isinstance(ext, (int, float)):
        s.pop("ext", None)                      # 上游把 ext 写成 1，客户端会崩
    elif isinstance(ext, (dict, list)):
        v = common.canon_ext(ext)               # 对象 → 紧凑 JSON 文本
        if v.strip() in EMPTY_EXT:
            s.pop("ext", None)
        else:
            s["ext"] = v
    elif isinstance(ext, str):
        v = ext.strip()
        if v.lower() in EMPTY_EXT:
            s.pop("ext", None)
        else:
            s["ext"] = v
    elif ext is not None:
        s.pop("ext", None)
    return s, None


def fix_keys(sites):
    """key 是客户端的主键，重复会让后一个顶掉前一个（列表少站点、收藏串号）。

    保留第一个（历史与收藏不变），其余按 api 稳定排序后追加 -2/-3 后缀，
    这样明天同一批重复项还会拿到同一个后缀，签名不会天天跳。
    """
    groups = {}
    for s in sites:
        k = s["key"] or re.sub(r"[^\w\-]+", "_", s["name"])[:16] or "site"
        s["key"] = k
        groups.setdefault(k, []).append(s)
    renamed = 0
    for k, lst in groups.items():
        if len(lst) == 1:
            continue
        lst.sort(key=lambda x: (str(x.get("api")), str(x.get("name"))))
        for i, s in enumerate(lst[1:], 2):
            cand, n = "%s-%d" % (k, i), 2
            while cand in groups:
                cand = "%s-%d-%d" % (k, i, n)
                n += 1
            s["key"] = cand
            renamed += 1
    return renamed



def site_key(site):
    """内容指向去重键，转交 common.content_key 统一口径。

    旧口径对 csp 站点用「api + 归一化名称」：名称只是显示用的皮，内容其实
    由 ext（配置文件）+ jar（spider）决定。结果就是换皮名（❤野荷塘 / 😘野荷塘）
    漏删，同名不同配置反而可能被误杀。现在名称完全不参与判重，且把
    raw / jsdelivr / gh-proxy / github blob 等链接别名还原成同一份内容。
    """
    return common.content_key(site)


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
    if pol["hosting_strict"] and common.hosting_host(site, deep=True):
        return "hosting"
    return None


def tier_of(site, stab, revived=(), jar_fail=None, ext_fail=None):
    """0 可用 / 1 未知 / 2 已判定失败或首页必空白。

    jar_fail / ext_fail 是 quality.json 里的「连续失败天数」映射（按 jar / ext
    URL 计）。csp 站点靠 jar 跑 spider、靠远程 ext 出首页配置：jar 或远程 ext
    任一连败 ≥2 天直接沉底，与 api 探活的口径一致（单日探测有噪声，连败才可信）。
    """
    jar_fail = jar_fail or {}
    ext_fail = ext_fail or {}
    sg = common.sig(site)
    st = stab.get(sg) or {}
    try:
        streak = int(st.get("fail_streak") or 0)
    except (TypeError, ValueError):
        streak = 0
    grade = str(st.get("last_grade") or "")
    api = str(site.get("api", "")).lower()
    ext = str(site.get("ext") or "")
    if api.startswith("csp_"):
        if not ext:
            return 2, "csp_no_ext"
        jurl = str(site.get("jar") or "").split(";", 1)[0]
        if jurl and jar_fail.get(jurl, 0) >= 2:
            return 2, "jar_dead"
    # 远程 ext 配置（json / spider 首页配置）连败 ≥2 天：站点首页 / 解析依赖它，
    # 死了等于整页空白。仅当 ext 是 http(s) 远程地址时才算（本地 JSON 配置串不算）。
    if ext.lower().startswith("http"):
        eurl = ext.split(";", 1)[0]
        if eurl and ext_fail.get(eurl, 0) >= 2:
            return 2, "ext_dead"
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
    # 直播源最容易被上游用不同链接发同一份文件：南风直播源 / Yoursmile 分别是
    # gh-proxy.com 与 gh-proxy.org 的同一个 raw live.txt。按字面 URL 去重认不出，
    # 统一走内容指向归一。
    out, seen, dropped, type_fixed, dup_content = [], set(), 0, 0, 0
    for g in lives:
        if not isinstance(g, dict):
            dropped += 1
            continue
        u = str(g.get("url") or "").strip()
        if not re.match(r"^https?://", u, re.I):
            dropped += 1
            continue
        ck = (common.content_url(u), common.content_url(g.get("ext")))
        if ck in seen:
            dropped += 1
            dup_content += 1
            continue
        seen.add(ck)
        ng = dict(g)
        ng["url"] = u
        ng["name"] = str(ng.get("name") or "").strip() or (urlsplit(u).netloc or "直播源")
        # 影视仓 / catvod 系的 Live.type 声明为 String，Gson 把整数当字符串会抛
        # IllegalStateException；FongMi/TV 的 Live 根本没有 type 字段（忽略它），
        # 所以统一写成字符串对两类客户端都安全。
        t = ng.get("type")
        if not isinstance(t, str):
            ng["type"] = "0" if t is None else str(t).strip()
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

    # 直播列表在客户端里就是按配置顺序排的，没有「排序」概念。101 条里 40 条
    # 走 raw.githubusercontent / GitHub 反代 —— 国内打开必然是空频道，却混在
    # 最前面。按「境外托管 → 沉底」稳定排序，不删任何一条。
    before = [ng["url"] for ng in out]
    out.sort(key=lambda ng: 1 if common.hosting_host({"api": ng["url"]}, deep=True) else 0)
    pushed = sum(1 for ng in out if common.hosting_host({"api": ng["url"]}, deep=True))
    return out, {"dropped": dropped, "type_fixed": type_fixed, "renamed": renamed,
                 "dup_content": dup_content, "offshore_moved": pushed}


def fix_parses(parses, types, cap):
    out, seen, dropped, fixed, ext_fixed, dup_content = [], set(), 0, 0, 0, 0
    for p in parses:
        if not isinstance(p, dict):
            dropped += 1
            continue
        u = str(p.get("url") or "").strip()
        if not re.match(r"^https?://", u, re.I):
            dropped += 1                      # url 写的是 "Demo" 这类占位
            continue
        np_ = {k: v for k, v in p.items() if k not in JUNK_FIELDS}
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
        # 解析通道的 ext 同样是字符串字段（flag 列表 / header），对象要归一
        e = np_.get("ext")
        if isinstance(e, (dict, list)):
            np_["ext"] = common.canon_ext(e)
            ext_fixed += 1
        elif isinstance(e, str) and e.strip().lower() in EMPTY_EXT:
            np_.pop("ext", None)
        # 同一 url 的 www / 协议 / 尾斜杠 / 参数序差异与 GitHub 反代别名，
        # 都按内容指向归一后去重；但 ext 是 header / flag 配置，url 相同而
        # ext 不同的两条通道行为不一样，必须一并纳入键。
        k = (common.content_url(u), common.content_url(np_.get("ext")))
        if k in seen:
            dropped += 1
            dup_content += 1
            continue
        seen.add(k)
        out.append(np_)

    # 同名解析在客户端是两条一模一样的「飞云」，用户根本分不出该选哪个
    taken = set()
    renamed = 0
    for np_ in out:
        base = str(np_.get("name") or "").strip() or urlsplit(np_["url"]).netloc
        np_["name"] = base
        if base in taken:
            host = re.sub(r"^www\.", "", urlsplit(np_["url"]).netloc or "src")
            np_["name"] = unique_name(base, host, taken)
            renamed += 1
        else:
            taken.add(base)

    capped, cap_kept = 0, {}
    if cap and len(out) > cap:
        # 截断不能按文件顺序截：上游把最好用的那一类通道排在后面，一刀切会先
        # 砍掉它们；也不能全给 json —— webview 嗅探是采集站的主力。按配额分。
        total = len(out)
        out = cap_parses(out, cap)
        capped = total - len(out)
        cap_kept = dict(Counter(p.get("type") for p in out))
    return out, {"dropped": dropped, "type_fixed": fixed, "ext_fixed": ext_fixed,
                 "dup_content": dup_content, "renamed": renamed,
                 "capped": capped, "cap_kept_types": cap_kept}


def cap_parses(items, cap):
    """按类型配额截断：json 聚合（type=1）拿 60% 名额，webview 嗅探（type=0）
    拿剩下的，两边都不许把对方挤干净；还有空位再按原顺序补。"""
    json_q = [p for p in items if parse_rank(p) <= 1]
    web_q = [p for p in items if parse_rank(p) >= 2]
    n_json = min(len(json_q), max(1, int(cap * 0.6)))
    n_web = min(len(web_q), cap - n_json)
    keep = json_q[:n_json] + web_q[:n_web]
    if len(keep) < cap:
        chosen = {id(p) for p in keep}
        keep += [p for p in items if id(p) not in chosen][:cap - len(keep)]
    return keep


def parse_rank(p):
    """解析通道的可用性排序：0 最好。type=1 是 json 聚合接口，带 ext 的能直接
    拿到播放地址；type=0 是 webview 嗅探，依赖客户端把网页跑起来；其余少用。"""
    t = p.get("type")
    if t == 1:
        return 0 if p.get("ext") else 1
    if t == 0:
        return 2
    return 3


def wallpaper_alive(url):
    """壁纸地址死在开屏上最刺眼：客户端会先卡几秒再退回默认。

    两道关：主机属于国内常见打不开的静态托管（实测本机连不上），或者真的
    拉不到。只在云端判定可达性 —— 本机试跑网络位置不稳定，避免离线误删。
    """
    if not isinstance(url, str) or not url.lower().startswith(("http://", "https://")):
        return False
    host = (urlsplit(url).hostname or "").lower()
    if any(host == d or host.endswith("." + d) for d in ("pages.dev", "vercel.app",
                                                         "netlify.app")):
        return False
    if not common.IS_CI:
        return True
    st, raw = common.fetch(url, timeout=10, limit_bytes=2048)
    return st == 200 and len(raw) > 0



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
    jar_fail = quality.get("jar_fail_streak") or {}
    ext_fail = quality.get("ext_fail_streak") or {}
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
        ext_norm = sum(1 for s in raw if isinstance(s.get("ext"), (dict, list)))
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

        # 内容指向去重：名称不参与判重。保留首次出现的那条 —— 身份签名是
        # name+api+jar+ext，不改名就不会让历史链天天断。
        seen, uniq = set(), []
        for s in kept:
            k = site_key(s)
            if k in seen:
                reasons["dup_content"] += 1
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
            t, why = tier_of(s, stab, revived, jar_fail, ext_fail)
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

        raw_lives = cfg.get("lives", []) or []
        raw_parses = cfg.get("parses", []) or []
        lives, live_stat = fix_lives(raw_lives, live_dup)
        parses, parse_stat = fix_parses(raw_parses, pol["parse_types"], pol["parse_cap"])
        # 只在被「清空」时兜底。注意不能套用 sites 的半量阈值：fix_parses 的
        # parse_cap 本来就会把全量版 285 条解析按配额截到 80 / 60 条，用半量
        # 判断会把设计内截断当成异常、回退到未清洗的原始列表（含占位 url、
        # 对象型 ext、重名解析），反而写出坏配置 —— 首次上线就被断言抓到。
        # 真正的症状是 0 条：旧代码会让 out 里干脆不写这个键、_meta 记 0，
        # 断言用 0 == 0 放行，坏配置静默上线。现在还有 collections_not_empty
        # 硬断言兜底，这里回退只是不让它变成空配置。
        collections_aborted = ((bool(raw_lives) and not lives) or
                               (bool(raw_parses) and not parses))
        if collections_aborted:
            print("  %-24s 直播/解析被清空（%d/%d, %d/%d），回退为原始列表（疑似口径写错）"
                  % (base, len(lives), len(raw_lives), len(parses), len(raw_parses)))
            lives, parses = list(raw_lives), list(raw_parses)
        keys_renamed = fix_keys(uniq)

        spider = cfg.get("spider")
        spider_removed = bool(spider) and pol["jar_free"]
        out = {
            "_meta": dict(cfg.get("_meta") or {}),
            "sites": uniq,
        }
        if spider and not spider_removed:
            out["spider"] = spider
        wall = cfg.get("wallpaper")
        if wall and wallpaper_alive(wall):
            out["wallpaper"] = wall
        if parses:
            out["parses"] = parses
        if lives:
            out["lives"] = lives

        # 最后一道：客户端字段兼容。TVBox 原版 / 影视仓把 style、header
        # 声明成 String，上游却常写成 JSON 对象，Gson 会抛
        # IllegalStateException 让整份配置导不进来；统一降级成各分支都能读
        # 的形式。数字字段里的字符串（如 gridview "0-0-4.1"）同样处理。
        # 另外把播放期会被裸 .trim() 的 String 字段兜底成非 null，避免老分支
        # 点播时报 NullPointerException: String.trim() on a null object reference。
        compat_fixed = 0
        for coll, kind in (("sites", "site"), ("parses", "parse"), ("lives", "live")):
            for o in out.get(coll, []):
                compat_fixed += common.client_compat(o, kind)

        # 元信息必须跟着这一份的实际内容走：之前 lives / parses 直接沿用全量版的
        # 数字（108/192），精简版实际只有 101/80，客户端里对不上。
        jars = Counter(str(s["jar"]).split(";")[0] for s in uniq
                       if str(s.get("jar") or "").startswith("http"))
        out["_meta"].update({
            "sites": len(uniq),
            "lives": len(lives),
            "parses": len(parses),
            "jar_count": len(jars),
            "jar_top": dict(jars.most_common(3)),
        })
        out["_meta"]["polish"] = {
            "version": 3,
            "collections_aborted": collections_aborted,
            "label": pol["label"],
            "removed": {k: v for k, v in sorted(reasons.items())},
            "renamed": renamed,
            "keys_renamed": keys_renamed,
            "ext_normalized": ext_norm,
            "client_normalized": compat_fixed,
            "wallpaper_removed": bool(wall) and "wallpaper" not in out,
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

        print("  %-24s 站点 %4d → %4d | 剔除 %s | 改名 %d | 主键去重 %d | 可用 %d / 未知 %d / 降级 %d"
              % (base, len(raw), len(uniq),
                 json.dumps(out["_meta"]["polish"]["removed"], ensure_ascii=False),
                 renamed, keys_renamed,
                 out["_meta"]["polish"]["order"]["usable"],
                 out["_meta"]["polish"]["order"]["unknown"],
                 out["_meta"]["polish"]["order"]["degraded"]))
        print("  %-24s 直播 %d（修 type %d / 改名 %d / 境外沉底 %d）| 解析 %d（剔 %d 修 %d ext归一 %d 改名 %d 截 %d）| 去 spider %s | 去壁纸 %s"
              % ("", len(lives), live_stat["type_fixed"], live_stat["renamed"],
                 live_stat["offshore_moved"],
                 len(parses), parse_stat["dropped"], parse_stat["type_fixed"],
                 parse_stat["ext_fixed"], parse_stat["renamed"],
                 parse_stat["capped"], spider_removed,
                 out["_meta"]["polish"]["wallpaper_removed"]))

    return 0


if __name__ == "__main__":
    sys.exit(main())
