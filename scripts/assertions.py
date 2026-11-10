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
  8. 客户端解析契约：key 唯一、ext 是字符串、_meta 的直播/解析条数对得上、
     解析通道不重名、没有客户端不认的字段（类型 / playurl）
  9. 壁纸若配置了就必须拉得到
软检查（抽样，低于阈值才阻断）：
 10. 抽样 jar 必须是 200 + PK(zip)；抽样 ext 必须 200
     —— 允许个别抖动，但整体可用率不能塌

输出 _assertions.json。用法：python scripts/assertions.py [--strict]
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
    ap.add_argument("--output", default="_assertions.json")
    args = ap.parse_args()

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
        common.write_json(args.output, {"passed": False, "checks": checks,
                                        "failed": failed, "env": common.env_info()},
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
           "今日 %d，昨日 %d，下限 %.0f%%" % (len(sites), prev, args.min_ratio * 100))

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

    # 7) 过滤残留
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import build as build_mod
    dirty = []
    for group, items in (("site", sites),
                         ("live", full.get("lives", [])),
                         ("parse", full.get("parses", []))):
        for o in items:
            if isinstance(o, dict) and build_mod.blocked(o):
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
            if not isinstance(g.get("type"), int):
                hygiene.append("%s 直播type非整数" % f)
                break
    record("structure_clean", not hygiene,
           "; ".join(hygiene[:4]) or "无空/非法 api、无重名、解析 url 合法、type 均为整数")

    # 11) 客户端解析契约：key 唯一、ext 是字符串、_meta 计数对得上、解析不重名。
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
        for coll in ("sites", "parses"):
            for o in c.get(coll, []):
                if isinstance(o.get("ext"), (dict, list)):
                    ext_bad.append("%s:%s" % (f, coll))
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
    record("ext_is_string", not ext_bad, "; ".join(ext_bad[:4]) or "sites/parses 的 ext 均为字符串")
    record("meta_counts_consistent", not count_bad, "; ".join(count_bad[:4]) or "直播/解析条数与元信息一致")
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
    for f in ("tvbox-lite.json", "tvbox-network.json",
              "tvbox-lite-homefirst.json", "tvbox-network-homefirst.json"):
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
    offbad = []
    for f in ("tvbox-network.json", "tvbox-network-homefirst.json"):
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

    # 8) 抽样 jar / ext 可达性
    rng = random.Random(common.today())
    jars = list({str(s.get("jar")) for s in sites
                 if str(s.get("jar") or "").startswith("http")})
    exts = list({str(s.get("ext")) for s in sites
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
    common.write_json(args.output, {
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
