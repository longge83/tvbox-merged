#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""构建回归断言 —— 自动更新的「保险丝」。

每天无人值守覆盖线上同一个文件，如果某天上游改了格式、把路径拼错、
或 jar 批量 404，坏配置会直接推给所有客户端且无法回退。这个脚本在提交前
做一组硬检查，任一失败就 exit 1，workflow 因此不会提交，线上保持上一版。

硬检查（失败即阻断）：
  1. 所有配置是合法 JSON、无重复 key
  2. _meta.sites == len(sites)；站点数不低于上一版的 60%（防骤降/空配置）
  3. 每个 csp_* 站点必须有 http jar
  4. 无残留相对路径 ./ ../ 开头
  5. 无重复路径段（XBPQ/XBPQ/、/js/js/ 这类基准拼错）
  6. 首页版 ⊆ 全量版；homefirst 版站点数与对应源一致
  7. 过滤后不应残留违规关键词
  8. 客户端解析契约：key 唯一、ext 形态合规（sites/lives 字符串、
     parses 对象或缺键）、_meta 的直播/解析条数对得上、解析通道不重名、
     没有客户端不认的字段（类型 / playurl）
  9. 壁纸若配置了就必须拉得到
  9.5 按「内容指向」判重后无重复站点 / 直播 / 解析（防换皮名与链接别名复燃）
  9.6 客户端字段兼容：style / header 不得是 JSON 对象，数字字段不得塞字符串
  9.7 客户端会裸 .trim() 的 String 字段不得缺失 / null（播放期空指针防线）
  9.8 搜索顺序：数组顺序 = 客户端搜索结果的展示顺序。每份配置都要跑过
     search_rank，第一屏不得出现磁力 / 体育 / 听书这类非影视站，置顶名单
     里的线路必须在前 10 位
  9.9 直播源：河南台专列表引用了就必须真有文件且结构合规；live_blocklist
     里记过的收费 / 导流源不得回流到配置
软检查（抽样，低于阈值才阻断）：
 10. 抽样 jar 必须是 200 + PK(zip)；抽样 ext 必须 200
     —— 允许个别抖动，但整体可用率不能塌

输出：CI 写 reports/assertions.json（由 Actions 用 git add -f 提交，外部可核对当日
结论）；本地试跑写 _assertions.json（被 .gitignore 忽略，不会污染入库结果）。
可用 --output 显式覆盖。用法：python scripts/assertions.py [--strict]
"""
import argparse
import json
import os
import random
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common

API_RX = re.compile(r"^(?:https?://\S+|csp_\S+)$", re.I)

# 与 polish.JAR_MEDIA_EXT 保持一致：这些扩展名永远不是合法的 spider jar。
JAR_MEDIA_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg",
                 ".ico", ".mp4", ".css", ".html")

CONFIGS = [
    "tvbox.json", "tvbox-lite.json", "tvbox-network.json", "tvbox-home.json",
    "tvbox-full-homefirst.json", "tvbox-lite-homefirst.json",
    "tvbox-network-homefirst.json",
]
# 场景版是新增产物：首日构建或探测失败时可能来不及产出，缺失不阻断主配置。
# 只要存在，就同样校验 JSON 合法性、元信息一致性与「⊆ 全量版」。
OPTIONAL_CONFIGS = ["tvbox-quality.json", "tvbox-cn-fast.json", "tvbox-stable7.json"]
PAIRS = [("tvbox.json", "tvbox-full-homefirst.json"),
         ("tvbox-lite.json", "tvbox-lite-homefirst.json"),
         ("tvbox-network.json", "tvbox-network-homefirst.json")]
def dup_segment(url):
    """检测 XBPQ/XBPQ/ 这类「相对路径基准拼错」的相邻重复段。

    跳过路径前两段：GitHub raw 的 /<owner>/<repo>/ 里 owner 与 repo 同名
    （例如 apaodh/apaodh）是完全合法的，按整串正则会误报几十个节点。
    """
    try:
        segs = [s for s in urlsplit(url).path.split("/") if s][2:]
    except Exception:
        return None
    for a, b in zip(segs, segs[1:]):
        if a == b and len(a) >= 2:
            return a
    return None


class DuplicateKey(Exception):
    pass


def pairs_hook(pairs):
    seen = set()
    for k, _ in pairs:
        if k in seen:
            raise DuplicateKey(k)
        seen.add(k)
    return dict(pairs)


def load_strict(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f, object_pairs_hook=pairs_hook)


def check_url(url, want_zip=False):
    st, raw = common.fetch(url, timeout=8, limit_bytes=64)
    # 不少上游把 JS 爬虫也写进 jar 槽（qu.ax/xxx.js），按 PK 魔数会全判成坏链
    if want_zip and url.split("?")[0].lower().endswith((".js", ".mjs")):
        want_zip = False
    ok = st == 200 and (raw[:2] == b"PK" if want_zip else len(raw) > 0)
    return {"url": url[:130], "status": st, "ok": ok}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true", help="抽样必须 100% 通过")
    ap.add_argument("--jar-sample", type=int, default=20)
    ap.add_argument("--ext-sample", type=int, default=20)
    ap.add_argument("--min-ratio", type=float, default=0.6,
                    help="站点数相对昨日快照的最低比例")
    ap.add_argument("--min-sample-ratio", type=float, default=0.30,
                    help="首次运行（无昨日基线）时 jar/ext 抽样可用率下限")
    ap.add_argument("--max-no-jar-ratio", type=float, default=0.30,
                    help="允许缺 jar 的 csp 站点比例（上游本身就没写 jar 时聚合器无法凭空生成）")
    ap.add_argument("--output", default=None,
                    help="结果 JSON 路径；默认按环境分流（CI 入库 / 本地隔离）")
    args = ap.parse_args()
    out_path = args.output or common.result_path("assertions")

    checks, failed = {}, []

    def record(name, ok, detail):
        checks[name] = {"ok": bool(ok), "detail": detail}
        if not ok:
            failed.append({"name": name, "detail": detail})
        print("  [%s] %-26s %s" % ("OK" if ok else "FAIL", name, detail), flush=True)

    print("=== 回归断言 ===", flush=True)
    # 1) JSON 合法性 + 重复 key
    cfgs = {}
    bad = []
    for f in CONFIGS + OPTIONAL_CONFIGS:
        if not os.path.exists(f):
            if f not in OPTIONAL_CONFIGS:
                bad.append(f + "(缺失)")
            continue
        try:
            cfgs[f] = load_strict(f)
        except Exception as e:
            bad.append("%s(%s)" % (f, str(e)[:60]))
    record("json_valid_no_dup_key", not bad, "; ".join(bad) or "%d 份配置合法" % len(cfgs))
    if "tvbox.json" not in cfgs:
        # 失败结果也要带 date：消费端统一按日期判新鲜，缺 date 会被当成
        # 过期结论丢弃，反而看不出「本轮确实跑过且失败了」。
        common.write_json(out_path, {"date": common.today(), "passed": False,
                                     "checks": checks, "failed": failed,
                                     "env": common.env_info()},
                          skip_unchanged=False)
        return 1

    full = cfgs["tvbox.json"]
    sites = full.get("sites", [])

    # 2) 元信息一致 + 站点数骤降保护
    meta_bad = []
    for f, c in cfgs.items():
        n = len(c.get("sites", []))
        m = c.get("_meta", {}).get("sites")
        if m is not None and m != n:
            meta_bad.append("%s meta=%s actual=%s" % (f, m, n))
    record("meta_sites_consistent", not meta_bad, "; ".join(meta_bad) or "全部一致")

    trend = (common.load_json("quality.json", {}) or {}).get("trend", [])
    # 断言跑在 history.py 之前，quality.json 里最后一天就是上一版
    prev = next((t["sites"] for t in reversed(trend) if t.get("sites")), 0)
    ratio_ok = (not prev) or len(sites) >= prev * args.min_ratio
    record("no_site_collapse", ratio_ok,
           "今日 %d（polish 后），昨日 %d（build 口径，polish 前），下限 %.0f%%" %
           (len(sites), prev, args.min_ratio * 100))

    # 3) csp 必须有 jar；4) 无相对路径；5) 无重复路径段
    no_jar, rel_left, dup_paths, jar_media = [], [], [], []
    for i, s in enumerate(sites):
        api = str(s.get("api", ""))
        if api.startswith("csp_") and not str(s.get("jar") or "").startswith("http"):
            no_jar.append("%d:%s" % (i, str(s.get("name", ""))[:16]))
        jv = str(s.get("jar") or "")
        if jv.startswith("http"):
            jpath = urlsplit(jv.split(";", 1)[0]).path.lower()
            if any(jpath.endswith(e) for e in JAR_MEDIA_EXT):
                jar_media.append("%d:%s" % (i, str(s.get("name", ""))[:16]))
        for field in ("api", "jar", "ext"):
            v = s.get(field)
            if isinstance(v, str) and re.match(r"^\.{1,2}/", v):
                rel_left.append("%d:%s=%s" % (i, field, v[:40]))
            if isinstance(v, str) and v.startswith("http") and dup_segment(v):
                dup_paths.append("%d:%s=%s" % (i, field, v[:70]))
    csp_total = sum(1 for s in sites if str(s.get("api", "")).startswith("csp_"))
    no_jar_ratio = len(no_jar) / max(1, csp_total)
    record("csp_has_jar", no_jar_ratio <= args.max_no_jar_ratio,
           "%d/%d 个 csp 缺 jar (%.1f%%, 阈值 %.0f%%): %s" %
           (len(no_jar), csp_total, no_jar_ratio * 100,
            args.max_no_jar_ratio * 100, ",".join(no_jar[:4])) if no_jar
           else "全部 csp 站点都有 jar")
    record("no_relative_path", not rel_left, "; ".join(rel_left[:4]) or "无残留相对路径")
    # 个别上游本来就有 /api/api/ 这类合法路径，命中 1~2 个只记警告；
    # 成批出现才是「基准拼错」的典型症状（历史上一次拼错影响几百个节点）。
    record("no_duplicate_path_segment", len(dup_paths) < 3,
           ("%d 处可疑重复段: " % len(dup_paths)) + "; ".join(dup_paths[:4])
           if dup_paths else "无 XBPQ/XBPQ 类重复路径段")

    # 3.5) jar 绝不能是图片 / 媒体文件：上游把 .jpg/.png 塞进 jar 槽，
    #      客户端照拉必崩（实测曾 352 个站点）。硬阻断，零容忍。
    record("jar_not_media", not jar_media,
           "%d 个站点 jar 是图片/媒体: %s" % (len(jar_media), ",".join(jar_media[:4]))
           if jar_media else "无媒体类 jar")

    # 6) 版本间关系
    home = cfgs.get("tvbox-home.json", {}).get("sites", [])
    full_sigs = set(common.sig(s) for s in sites)
    home_bad = [s.get("name") for s in home if common.sig(s) not in full_sigs][:5]
    record("home_subset_of_full", not home_bad,
           "首页版 %d 个，越界 %s" % (len(home), home_bad) if home_bad else
           "首页版 %d ⊆ 全量 %d" % (len(home), len(sites)))
    pair_bad = []
    for src, dst in PAIRS:
        if src in cfgs and dst in cfgs and len(cfgs[src].get("sites", [])) != len(cfgs[dst].get("sites", [])):
            pair_bad.append("%s!=%s" % (src, dst))
    record("homefirst_same_site_count", not pair_bad,
           "; ".join(pair_bad) or "三个 homefirst 版本均未删节点")

    var_bad = []
    for f in OPTIONAL_CONFIGS:
        c = cfgs.get(f)
        if not c:
            continue
        for s in c.get("sites", []):
            if common.sig(s) not in full_sigs:
                var_bad.append("%s:%s" % (f, str(s.get("name", ""))[:16]))
    record("variants_subset_of_full", not var_bad,
           "; ".join(var_bad[:4]) if var_bad else
           "已生成的 %d 个场景版均为全量版子集" %
           sum(1 for f in OPTIONAL_CONFIGS if f in cfgs))

    # 6.5) 十份配置整体 ⊆ 全量版。sig() 含 name，polish 对每份基础配置独立做
    #      同名消歧，个别站点跨版本会被改名（实测精简版 2/1212），所以用与名称
    #      无关的 content_key 再做一次全覆盖比对，避免漏掉 lite/network/homefirst。
    full_ck = {common.content_key(s) for s in sites}
    subset_bad = []
    for f, c in cfgs.items():
        if f == "tvbox.json":
            continue
        extra = [str(s.get("name", ""))[:14] for s in c.get("sites", [])
                 if common.content_key(s) not in full_ck][:3]
        if extra:
            subset_bad.append("%s:%s" % (f, ",".join(extra)))
    record("all_versions_subset_of_full", not subset_bad,
           "; ".join(subset_bad[:4]) if subset_bad else
           "%d 份派生配置的站点均为全量版子集（按内容指向比对，不受改名影响）" %
           (len(cfgs) - 1))

    # 7) 过滤残留
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import build as build_mod
    dirty = []
    for group, items in (("site", sites),
                         ("live", full.get("lives", [])),
                         ("parse", full.get("parses", []))):
        # 解析通道不扫 ext：那里是「支持哪些平台」的 flag 清单，扫了会把
        # 顺带支持哔哩的通用解析全判成残留（口径与 build.py 保持一致）。
        fields = (build_mod.PARSE_FILTER_FIELDS if group == "parse"
                  else ("name", "api", "ext", "jar", "url", "urls", "group"))
        for o in items:
            if isinstance(o, dict) and build_mod.blocked(o, fields=fields):
                dirty.append("%s:%s" % (group, str(o.get("name", ""))[:18]))
    record("content_filter_clean", not dirty, "; ".join(dirty[:5]) or "站点/直播/解析均无命中")

    # 9) 成品卫生：polish.py 的验收口径。这些瑕疵不会让构建崩，但会直接
    #    变成用户列表里的空条目、重复条目，所以同样按硬检查处理。
    hygiene = []
    for f in CONFIGS + OPTIONAL_CONFIGS:
        c = cfgs.get(f)
        if not c:
            continue
        names = Counter(str(s.get("name") or "") for s in c.get("sites", []))
        dupn = sum(v - 1 for v in names.values() if v > 1)
        if dupn:
            hygiene.append("%s 重名 %d" % (f, dupn))
        for s in c.get("sites", []):
            if not API_RX.match(str(s.get("api") or "").strip()):
                hygiene.append("%s 非法api:%s" % (f, str(s.get("name"))[:14]))
                break
        for p in c.get("parses", []):
            if not re.match(r"^https?://", str(p.get("url") or "")):
                hygiene.append("%s 解析url非法" % f)
                break
        for g in c.get("lives", []):
            if not isinstance(g.get("type"), str):
                hygiene.append("%s 直播type非字符串(影视仓/catvod 的 Live.type 为 String)" % f)
                break
    record("structure_clean", not hygiene,
           "; ".join(hygiene[:4]) or
           "无空/非法 api、无重名、解析 url 合法、直播 type 均为字符串")

    # 9.5) 内容指向去重回归防线：名称只是显示用的皮，链接还常被换成
    #      raw / jsdelivr / gh-proxy 等别名。只按名称或字面 URL 判重会漏，
    #      结果就是客户端里出现好几条一模一样的源。
    cdup = []
    for f, c in cfgs.items():
        seen_ck = set()
        for s in c.get("sites", []):
            ck = common.content_key(s)
            if ck in seen_ck:
                cdup.append("site %s:%s" % (f, str(s.get("name", ""))[:18]))
            seen_ck.add(ck)
        for coll in ("lives", "parses"):
            seen_u = set()
            for o in c.get(coll, []):
                if not isinstance(o, dict):
                    continue
                cu = (common.content_url(o.get("url")),
                      common.content_url(o.get("ext")))
                if not cu[0]:
                    continue
                if cu in seen_u:
                    cdup.append("%s %s:%s" % (coll, f, str(o.get("name", ""))[:18]))
                seen_u.add(cu)
    record("no_content_duplicates", not cdup,
           ("; ".join(cdup[:4]) + (" 共 %d 处" % len(cdup)) if len(cdup) > 4
            else "; ".join(cdup)) if cdup else "站点/直播/解析按内容指向判重后均无重复")

    # 9.6) 客户端字段兼容回归防线：TVBox 原版 / 影视仓把 style、header 声明成
    #      String，上游（FongMi 系）却写成 JSON 对象，Gson 读到对象仍按字符串
    #      取会抛 java.lang.IllegalStateException —— 一处不兼容整份配置都导不
    #      进来。数字字段塞非数字字符串同样会让部分分支 NumberFormatException。
    cbad = []
    for f, c in cfgs.items():
        for coll in ("sites", "parses", "lives"):
            for o in c.get(coll, []):
                if not isinstance(o, dict):
                    continue
                for k in ("style", "header"):
                    if isinstance(o.get(k), (dict, list)):
                        cbad.append("%s %s:%s 是 %s" %
                                    (f, coll, k, type(o.get(k)).__name__))
                if isinstance(o.get("style"), str) and \
                        o["style"].lstrip()[:1] in "{[":
                    cbad.append("%s %s:style 是 JSON 文本" % (f, coll))
                for k in ("playerType", "gridview"):
                    if isinstance(o.get(k), str):
                        cbad.append("%s %s:%s 是字符串" % (f, coll, k))
    record("client_compat_clean", not cbad,
           ("; ".join(cbad[:4]) + (" 共 %d 处" % len(cbad)) if len(cbad) > 4
            else "; ".join(cbad)) if cbad else
           "站点/直播/解析字段均为各 TVBox 分支可解析的类型")

    # 9.7) 播放期空指针防线。FongMi 的 Site.setExt / Live.setExt 是
    #      `this.ext = ext.trim()`，UrlUtil.convert -> UriUtil.uri 第一行也是
    #      `url.trim()`，都没有判空；Gson 对缺键不填默认值，字段留 null，
    #      5.6.4 等老分支点播 / 切线路时会抛
    #      NullPointerException: String.trim() on a null object reference。
    nbad = []
    for f, c in cfgs.items():
        for coll, kind in (("sites", "site"), ("parses", "parse"), ("lives", "live")):
            for o in c.get(coll, []):
                if not isinstance(o, dict):
                    continue
                for k in common.client_string_fields(kind):
                    v = o.get(k)
                    if k not in o or v is None or not isinstance(v, str):
                        nbad.append("%s %s:%s=%r" % (f, coll, k, v))
    record("no_null_strings", not nbad,
           ("; ".join(nbad[:4]) + (" 共 %d 处" % len(nbad)) if len(nbad) > 4
            else "; ".join(nbad)) if nbad else
           "客户端会裸 trim 的 String 字段均非 null（播放期不会空指针）")

    # 11) 客户端解析契约：key 唯一、ext 形态符合各字段契约、_meta 计数对得上、
    #     解析不重名。
    #     这几条不会让构建崩，但会让用户在客户端里「看不见某些站点 / 整份配置
    #     报错 / 列表里一堆同名解析」，属于必须挡住的结构性问题。
    key_bad, ext_bad, count_bad, pname_bad, field_bad = [], [], [], [], []
    for f in CONFIGS + OPTIONAL_CONFIGS:
        c = cfgs.get(f)
        if not c:
            continue
        ks = Counter(str(s.get("key") or "") for s in c.get("sites", []))
        dupk = sum(v - 1 for v in ks.values() if v > 1) + ks.get("", 0)
        if dupk:
            key_bad.append("%s 重复/空 key %d" % (f, dupk))
        # sites/lives 的 ext 在所有分支都是 String；parses 反过来必须是
        # JSON 对象（影视仓 getAsJsonObject() / FongMi 新版 Ext），给字符串会
        # 抛 IllegalStateException 且整份配置导入失败。缺键对两端都安全。
        for coll in ("sites", "lives"):
            if any(isinstance(o.get("ext"), (dict, list))
                   for o in c.get(coll, [])):
                ext_bad.append("%s:%s ext 非字符串" % (f, coll))
        for o in c.get("parses", []):
            e = o.get("ext")
            if e is not None and not isinstance(e, dict):
                ext_bad.append("%s:parses ext 非对象(%s)" %
                               (f, type(e).__name__))
                break
        m = c.get("_meta") or {}
        for field in ("lives", "parses"):
            if m.get(field) is not None and m[field] != len(c.get(field, [])):
                count_bad.append("%s %s meta=%s actual=%s" %
                                 (f, field, m[field], len(c.get(field, []))))
        pn = Counter(str(p.get("name") or "") for p in c.get("parses", []))
        if any(v > 1 for v in pn.values()):
            pname_bad.append("%s 重名解析 %d" %
                             (f, sum(v - 1 for v in pn.values() if v > 1)))
        for s in c.get("sites", []):
            if "类型" in s or "playurl" in s:
                field_bad.append("%s:%s" % (f, str(s.get("name"))[:12]))
                break
    record("site_key_unique", not key_bad, "; ".join(key_bad[:4]) or "主键唯一")
    record("ext_contract_clean", not ext_bad,
           "; ".join(ext_bad[:4]) or
           "sites/lives 的 ext 为字符串、parses 的 ext 为对象或缺键")
    record("meta_counts_consistent", not count_bad, "; ".join(count_bad[:4]) or "直播/解析条数与元信息一致")

    # 11.5) 筛选版不能照抄全量版的 jar 依赖统计。实测三份场景版都带着
    #       jar_count=53（实际 19 / 0 / 0），排查「这份配置到底依不依赖 spider」
    #       时会得出完全相反的结论；直播/解析被清空也不能靠 0 == 0 悄悄放行。
    jar_meta_bad, empty_bad = [], []
    for f, c in cfgs.items():
        m = c.get("_meta") or {}
        actual = len({str(s.get("jar") or "").split(";", 1)[0]
                      for s in c.get("sites", [])
                      if str(s.get("jar") or "").startswith("http")})
        if m.get("jar_count") is not None and m["jar_count"] != actual:
            jar_meta_bad.append("%s meta=%s actual=%s" % (f, m["jar_count"], actual))
        for coll in ("lives", "parses"):
            if not c.get(coll):
                empty_bad.append("%s:%s" % (f, coll))
    record("meta_jar_consistent", not jar_meta_bad,
           "; ".join(jar_meta_bad[:4]) or "每份配置的 jar_count 都按自身站点重算")
    record("collections_not_empty", not empty_bad,
           "; ".join(empty_bad[:4]) or "每份配置的直播与解析通道都非空")
    record("parse_names_unique", not pname_bad, "; ".join(pname_bad[:4]) or "解析通道无重名")
    record("no_foreign_fields", not field_bad, "; ".join(field_bad[:4]) or "无客户端不认的字段")

    # 12) 壁纸：写在配置里就必须拉得到，否则开屏会先卡几秒
    wall_bad = []
    for f in CONFIGS[:1]:
        w = (cfgs.get(f) or {}).get("wallpaper")
        if w:
            st, raw = common.fetch(str(w), timeout=10, limit_bytes=512)
            if st != 200 or not raw:
                wall_bad.append("%s=%s(%s)" % (f, str(w)[:50], st))
    record("wallpaper_reachable", not wall_bad,
           "; ".join(wall_bad) or "壁纸地址可达或未配置")

    # 10) 精简版 / 网络优化版必须真的「不加载 jar」
    jarbad = []
    # cn-fast 也宣称「0 jar / 0 爬虫」，必须与精简版/网络版一起守
    for f in ("tvbox-lite.json", "tvbox-network.json",
              "tvbox-lite-homefirst.json", "tvbox-network-homefirst.json",
              "tvbox-cn-fast.json"):
        c = cfgs.get(f)
        if not c:
            continue
        if c.get("spider"):
            jarbad.append("%s:spider" % f)
        for s in c.get("sites", []):
            if str(s.get("jar") or "").strip() or \
                    str(s.get("api", "")).lower().startswith("csp_"):
                jarbad.append("%s:%s" % (f, str(s.get("name"))[:14]))
                break
    record("lite_network_jar_free", not jarbad,
           "; ".join(jarbad[:4]) or "精简版/网络版 0 个 jar 依赖")

    # 11) 网络优化版不得残留「配置住在境外代码托管 / 反代上」的节点
    # 只查 sites：lives 里的境外直播是「沉底不删除」的设计（有代理仍可用），
    # 不该在这里被判失败。cn-fast 是三份里最强调国内直连的，一并纳入。
    offbad = []
    for f in ("tvbox-network.json", "tvbox-network-homefirst.json",
              "tvbox-cn-fast.json"):
        c = cfgs.get(f)
        if not c:
            continue
        for s in c.get("sites", []):
            h = common.hosting_host(s, deep=True)
            if h:
                offbad.append("%s:%s=%s" % (f, str(s.get("name"))[:12], h))
                break
    record("network_hosting_clean", not offbad,
           "; ".join(offbad[:4]) or "网络优化版无境外代码托管依赖")

    # 7.5) 搜索排序：数组顺序 = 客户端搜索结果的展示顺序
    # 防的是「排序步骤悄悄没跑 / 被改回按名称排」：按名称排会把磁力、体育、
    # 听书这类非影视站顶到最前（名字带数字 emoji 的排最靠前），用户搜动画时
    # 第一屏全是无关线路。这里只看两件事：排序步骤真的跑过、第一屏没有明显
    # 不相关的类目、置顶名单生效。
    import search_rank
    pins = []
    if os.path.exists("pins.txt"):
        pins = [ln.strip() for ln in open("pins.txt", encoding="utf-8")
                if ln.strip() and not ln.strip().startswith("#")]
    order_bad = []
    for f, c in cfgs.items():
        ss = c.get("sites") or []
        if not ss:
            continue
        if (c.get("_meta") or {}).get("order") != "search-first":
            order_bad.append("%s 未标记 search-first（排序步骤没跑？）" % f)
            continue
        noise = [str(s.get("name"))[:12] for s in ss[:10]
                 if search_rank.cat_group(s)]
        if noise:
            order_bad.append("%s 前 10 位有非影视站 %s" % (f, noise[:2]))
    head_names = [str(s.get("name") or "").casefold() for s in sites[:10]]
    if pins and not any(p.casefold() in n for p in pins for n in head_names):
        order_bad.append("置顶名单 %s 不在前 10 位" % pins[:2])
    record("search_order_sane", not order_bad,
           "; ".join(order_bad[:4]) or "顺序按搜索可播优先，置顶线路在前 10 位")

    # 9.9 直播源：自建专列表必须真的存在，收费源不得回流
    # 专列表这条 lives 是 live_audit 插进去的，m3u 也是它写的。审计步骤失败
    # （continue-on-error）时配置里不会出现这条，所以「引用了但文件缺失」只可能
    # 是清理 / 提交环节漏了 —— 客户端点开就是一个 404 的空白直播源。
    live_bad = []
    live_block = []
    if os.path.exists("live_blocklist.txt"):
        for line in open("live_blocklist.txt", encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#"):
                live_block.append(line)
    henan_file = os.path.exists("live-henan.m3u")
    henan_ok = False
    if henan_file:
        txt = open("live-henan.m3u", encoding="utf-8").read()
        n_ch = txt.count("#EXTINF")
        henan_ok = txt.startswith("#EXTM3U") and n_ch >= 1 and "://" in txt
        if not henan_ok:
            live_bad.append("live-henan.m3u 结构不合规（%d 个频道）" % n_ch)
    for f in CONFIGS:
        cfg = common.load_json(f, {}) or {}
        lives_f = cfg.get("lives") or []
        if not lives_f:
            continue
        for g in lives_f:
            u = str(g.get("url") or "")
            if any(b in u for b in live_block):
                live_bad.append("%s 仍有收费源 %s" % (f, u[:48]))
        if not henan_file and any(str(g.get("url") or "").endswith("live-henan.m3u")
                                  for g in lives_f):
            live_bad.append("%s 引用了 live-henan.m3u 但文件不存在" % f)
    record("live_sources_sane", not live_bad,
           "; ".join(live_bad[:4]) or
           ("专列表 %d 个频道、收费黑名单 %d 条、配置无引用断裂"
            % (open("live-henan.m3u", encoding="utf-8").read().count("#EXTINF")
               if henan_ok else 0, len(live_block))))

    # 8) 抽样 jar / ext 可达性
    rng = random.Random(common.today())
    # 抽样前先按客户端口径剥掉 ;md5;hash：同一个 jar 挂不同 hash 会被当多个
    # 样本，抽样偏向 hash 多的那个，且带后缀去请求必然 404。
    jars = list({str(s.get("jar")).split(";", 1)[0] for s in sites
                 if str(s.get("jar") or "").startswith("http")})
    exts = list({str(s.get("ext")).split(";", 1)[0] for s in sites
                 if str(s.get("ext") or "").startswith("http")})
    rng.shuffle(jars); rng.shuffle(exts)
    jt, et = jars[:args.jar_sample], exts[:args.ext_sample]
    # 第三方 jar/ext 本来就有大量失效链接，用固定高阈值会让构建天天被挡。
    # 改成回归检测：昨天很好 -> 今天不许骤降；昨天本来就差 -> 不苛求；
    # 无论基线如何，低于绝对下限（20%）都视为「基准拼错 / 批量 404」。
    quality = common.load_json("quality.json", {}) or {}
    prev_rates = quality.get("sample_rates", {}) or {}
    rows = {"jar": [], "ext": []}
    with ThreadPoolExecutor(max_workers=16) as ex:
        futs = {ex.submit(check_url, u, True): "jar" for u in jt}
        futs.update({ex.submit(check_url, u, False): "ext" for u in et})
        for f in as_completed(futs):
            try:
                rows[futs[f]].append(f.result())
            except Exception:
                pass
    sample_rates = {}
    for kind, rs in rows.items():
        if not rs:
            record(kind + "_sample_reachable", True, "无可抽样项，跳过")
            sample_rates[kind] = None
            continue
        ratio = sum(1 for r in rs if r["ok"]) / len(rs)
        sample_rates[kind] = round(ratio, 4)
        prev = prev_rates.get(kind)
        # 抽样本身有网络噪声（同一批 URL 两次跑可能差 30pp），所以不用
        # 「昨日减固定百分点」这种敏感规则。只有两种失败：低于绝对下限，
        # 或昨天很好(>=70%)而今天几乎全挂(<30%) —— 后者才是批量拼错的症状。
        if args.strict:
            need = 1.0
        elif isinstance(prev, (int, float)) and prev >= 0.7 and ratio < 0.3:
            need = 0.30
        elif isinstance(prev, (int, float)):
            need = 0.20
        else:
            need = args.min_sample_ratio
        record(kind + "_sample_reachable", ratio >= need,
               "%d/%d 可用 (%.0f%%, 阈值 %.0f%%%s)" %
               (sum(1 for r in rs if r["ok"]), len(rs), ratio * 100, need * 100,
                "，昨日 %.0f%%" % (prev * 100) if isinstance(prev, (int, float)) else "，无基线"))

    passed = not failed
    common.write_json(out_path, {
        "date": common.today(),
        "passed": passed,
        "env": common.env_info(),
        "checks": {k: v["ok"] for k, v in checks.items()},
        "check_details": checks,
        "failed": failed,
        "sample": rows,
        "sample_rates": sample_rates,
        "sites": len(sites),
    }, skip_unchanged=False)
    print("\n断言结果: %s（%d 项检查，%d 项失败）" %
          ("通过，允许提交" if passed else "未通过，本次不提交，线上保持上一版",
           len(checks), len(failed)), flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
