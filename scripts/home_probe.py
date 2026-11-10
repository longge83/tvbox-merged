#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检测 TVBox 配置里哪些站点能显示「主界面推荐（首页）」。

原理：TVBox 首页推荐 ≠ 搜索，两者走不同接口。
  - CMS 采集源 (type=0/1)：首页来自 ?ac=videolist / ?ac=detail / ?ac=list
    返回的分类(class)与首页影片列表(list)。列表为空 -> 首页空白。
  - csp_* 爬虫源 (type=3)：首页由 jar 里的 homeContent() 生成，而分类/首页
    规则常放在 ext 指向的 JSON 文件里。jar 或 ext 失效 -> 首页空白但搜索可能仍可用。
  - 纯 http js 源：取决于 js 脚本本身，只做可达性检查，标 unknown。

v2 新增：
  - 结果写入判定环境（云端 / 本机 / 是否走代理），本机结果不会被误当作线上结论；
  - 同 jar 推断：一个 jar 下已有站点判 ok/likely，则同 jar 的 unknown 大概率
    也能出首页（依赖 jar 内置 homeContent），升级为 likely 并单独标记；
  - 本地试跑保护：非 Actions 环境只写 _local-* 文件。

输出：
  home_report.json   每个站点的判定明细（含 env）
  tvbox-home.json    只保留「确认能出首页」的站点，按可靠度排序
  home-sites.txt     人可读清单

用法：python scripts/home_probe.py [--limit N] [--input tvbox.json] [--no-infer]
"""
import argparse
import json
import os
import re
import ssl
import sys
import threading
import time
import urllib.request
from urllib.parse import quote, urlsplit, urlunsplit
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

# GitHub Actions 云端可直连所有域名；本机直连 raw 常超时，设 HOME_PROXY 走本地代理
PROXY = os.environ.get("HOME_PROXY", "")
GH_HOSTS = ("raw.githubusercontent.com", "github.com", "gist.githubusercontent.com",
            "gitlab.com", "jihulab.com", "agit.ai", "gitee.com")
DIRECT = urllib.request.build_opener(urllib.request.HTTPSHandler(context=CTX))
VIA_PROXY = (urllib.request.build_opener(
    urllib.request.HTTPSHandler(context=CTX),
    urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}),
) if PROXY else DIRECT)
UA = {"User-Agent": "okhttp/4.9.0", "Accept": "*/*"}


def safe_url(url):
    """GitHub raw 上的中文文件名必须 percent-encode，否则 urllib 直接抛错"""
    p = urlsplit(url)
    path = quote(p.path, safe="/%:@&=+$,~!*'()[]")
    return urlunsplit((p.scheme, p.netloc, path, p.query, p.fragment))


_CACHE = {}
_LOCK = threading.Lock()


def fetch(url, timeout=7, limit_bytes=60000, use_cache=True):
    """返回 (status, bytes)。同一 URL 只请求一次（大量站点共享 jar/ext）。"""
    # TVBox jar/ext 的 "url;md5;hash" 后缀必须剥离后再请求，否则全是 404。
    # 与 common.fetch / content_url 统一按分号切，别只认字面 ";md5;"。
    if ";" in url:
        url = url.split(";", 1)[0]
    url = safe_url(url)
    if use_cache:
        with _LOCK:
            if url in _CACHE:
                return _CACHE[url]
    opener = VIA_PROXY if any(h in url for h in GH_HOSTS) else DIRECT
    try:
        req = urllib.request.Request(url, headers=UA)
        with opener.open(req, timeout=timeout) as r:
            result = (r.status, r.read(limit_bytes))
    except Exception as e:
        result = (getattr(e, "code", 0) or 0, str(e).encode())
    if use_cache:
        with _LOCK:
            _CACHE[url] = result
    return result


def json_or_none(raw):
    try:
        return json.loads(raw.decode("utf-8", "ignore"))
    except Exception:
        return None


def video_count(j):
    """从苹果CMS 返回体里数出首页影片条数"""
    if not isinstance(j, dict):
        return 0
    lst = j.get("list")
    if isinstance(lst, dict):
        lst = lst.get("list") or lst.get("data")
    if not isinstance(lst, list):
        return 0
    n = 0
    for it in lst:
        if isinstance(it, dict) and (it.get("vod_name") or it.get("vod_id")):
            n += 1
    return n


def class_count(j):
    if not isinstance(j, dict):
        return 0
    c = j.get("class") or j.get("classes") or j.get("type_list")
    return len(c) if isinstance(c, list) else 0


def probe_cms(site):
    api = str(site.get("api", "")).rstrip("/")
    sep = "&" if "?" in api else "?"

    result = {"videos": 0, "classes": 0, "via": "", "status": 0}
    # 新版 maccms 用 ac=detail，老版用 ac=videolist，另有 ac=list 只给分类
    for ac in ("ac=videolist&pg=1", "ac=detail&pg=1", "ac=list"):
        st, raw = fetch(api + sep + ac)
        j = json_or_none(raw)
        v, c = video_count(j), class_count(j)
        if v or c:
            result["status"] = st
            if v:
                result.update(videos=v, classes=c, via=ac)
                break
            if not result["via"]:
                result.update(classes=c, via=ac + "(仅分类)")
    if result["videos"] > 0:
        return "ok", result
    if result["classes"] > 0:
        return "classes_only", result
    return "fail", result


def ext_ok(raw, status):
    """ext 首页配置文件是否有效"""
    if status != 200:
        return False, "HTTP %s" % status
    j = json_or_none(raw)
    if isinstance(j, dict):
        hit = [k for k in ("home", "首页", "class", "分类", "list", "categories",
                           "cate", "type", "url", "urls", "site", "sites") if k in j]
        return (True, "json keys:" + ",".join(hit[:5])) if hit else (False, "json 无首页字段")
    if isinstance(j, list):
        return (True, "json list %d" % len(j)) if j else (False, "json 空列表")
    txt = raw.decode("utf-8", "ignore")
    # 非 JSON 但含分类/首页 URL 规则的文本配置也算有效
    if len(txt.strip()) > 20:
        return True, "text %d chars" % len(txt)
    return False, "空内容"


def probe_csp(site):
    jar = str(site.get("jar") or "")
    ext = site.get("ext")
    detail = {"jar": "", "ext": "", "status": 0}

    # jar 是爬虫本体，失效则整个站点（含首页）不可用。
    # 一律按内容魔数判断：不少作者把 jar 伪装成 .jpg/.png 上传图床，
    # 按后缀判会误杀几百个站点；必须是 200 且以 PK(zip) 开头。
    jar_alive = True
    if jar.startswith("http"):
        st, raw = fetch(jar, limit_bytes=64)
        jar_alive = st == 200 and raw[:2] == b"PK"
        detail["jar"] = "HTTP %s%s" % (st, " (zip)" if raw[:2] == b"PK" else " (非zip)")

    # ext 常是首页分类配置；内嵌或为空则依赖 jar 内置 homeContent
    if isinstance(ext, str) and ext.startswith("http"):
        st, raw = fetch(ext, limit_bytes=60000)
        ok, why = ext_ok(raw, st)
        detail["ext"] = why
        detail["status"] = st
        if not jar_alive:
            return "fail", detail
        return ("likely" if ok else "fail"), detail
    if isinstance(ext, (dict, list)) or (isinstance(ext, str) and ext.strip().startswith("{")):
        detail["ext"] = "内嵌配置"
        return ("likely" if jar_alive else "fail"), detail
    detail["ext"] = "无 ext（依赖 jar 内置首页）"
    return ("unknown" if jar_alive else "fail"), detail


def probe_http_js(site):
    api = str(site.get("api", ""))
    st, raw = fetch(api, timeout=9, limit_bytes=4096)
    alive = st == 200 and len(raw) > 20
    return ("unknown" if alive else "fail"), {"status": st, "bytes": len(raw)}


def classify(site):
    api = str(site.get("api", ""))
    if api.startswith("csp_"):
        return "csp"
    if str(site.get("type", "")) in ("0", "1") and api.startswith("http") and ".js" not in api[-8:]:
        return "cms"
    if api.startswith("http"):
        return "http_js"
    return "other"


def probe(site, idx=0):
    kind = classify(site)
    if kind == "cms":
        grade, detail = probe_cms(site)
    elif kind == "csp":
        grade, detail = probe_csp(site)
    elif kind == "http_js":
        grade, detail = probe_http_js(site)
    else:
        grade, detail = "unknown", {"reason": "未知类型"}
    return {
        "idx": idx,
        "key": site.get("key"),
        "name": site.get("name"),
        "kind": kind,
        "grade": grade,
        "api": str(site.get("api", ""))[:120],
        "detail": detail,
    }


def infer_by_jar(results, sites):
    """同一个 jar 由相同的 homeContent() 提供首页；若已有站点判可用，
    其余「无 ext」的 unknown 站点大概率也能出首页，低成本召回。"""
    good = set()
    for r in results:
        if r["grade"] in ("ok", "likely"):
            i = r.get("idx")
            j = str(sites[i].get("jar") or "") if isinstance(i, int) and 0 <= i < len(sites) else ""
            if j.startswith("http"):
                good.add(j)
    n = 0
    for r in results:
        if r["grade"] != "unknown":
            continue
        i = r.get("idx")
        j = str(sites[i].get("jar") or "") if isinstance(i, int) and i < len(sites) else ""
        if j in good:
            r["grade"] = "likely"
            r["inferred"] = True
            r.setdefault("detail", {})["ext"] = "同 jar 已有可用站点，推断内置首页可用"
            n += 1
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="tvbox.json")
    ap.add_argument("--limit", type=int, default=0, help="只测前 N 个站点（调试用）")
    ap.add_argument("--workers", type=int, default=40)
    ap.add_argument("--report", default="home_report.json")
    ap.add_argument("--output", default="tvbox-home.json")
    ap.add_argument("--list-file", default="home-sites.txt")
    ap.add_argument("--no-infer", action="store_true", help="关闭同 jar 推断")
    args = ap.parse_args()

    # 本地试跑保护：绝不覆盖线上配置
    local = not (common.IS_CI or os.environ.get("ALLOW_LOCAL_WRITE") == "1")
    if local:
        args.report = "_local-home_report.json"
        args.output = "_local-home.json"
        args.list_file = "_local-home-sites.txt"
        print("本地试跑：输出重定向到 _local-home*，不覆盖线上文件", flush=True)

    cfg = json.load(open(args.input, encoding="utf-8"))
    sites = cfg["sites"][: args.limit] if args.limit else cfg["sites"]
    t0 = time.time()
    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(probe, s, i): (s, i) for i, s in enumerate(sites)}
        for i, f in enumerate(as_completed(futs), 1):
            try:
                results.append(f.result())
            except Exception as e:
                s, idx = futs[f]
                results.append({"idx": idx, "key": s.get("key"), "name": s.get("name"),
                                "grade": "fail", "detail": {"error": str(e)[:80]}})
            if i % 200 == 0:
                print("  已检测 %d/%d ... %.0fs" % (i, len(sites), time.time() - t0), flush=True)

    inferred = 0 if args.no_infer else infer_by_jar(results, sites)

    by_grade, by_kind = {}, {}
    for r in results:
        by_grade[r["grade"]] = by_grade.get(r["grade"], 0) + 1
        k = r.get("kind", "other")
        by_kind.setdefault(k, {})
        by_kind[k][r["grade"]] = by_kind[k].get(r["grade"], 0) + 1

    print("\n=== 判定统计（共 %d，耗时 %.0fs，同 jar 推断召回 %d）===" %
          (len(results), time.time() - t0, inferred))
    for g in ("ok", "likely", "classes_only", "unknown", "fail"):
        if g in by_grade:
            print("  %-13s %d" % (g, by_grade[g]))
    print("  按类型:")
    for k, v in sorted(by_kind.items()):
        print("    %-8s %s" % (k, dict(v)))

    common.write_json(args.report, {
        "env": common.env_info(),
        "scanned": len(results),
        "grades": by_grade,
        "inferred_count": inferred,
        "elapsed_sec": round(time.time() - t0, 1),
        "sites": results,
    }, skip_unchanged=False)

    if not args.limit:
        # 只保留确认有首页数据的站点；ok 排最前，其次 likely
        rank = {"ok": 0, "likely": 1, "classes_only": 2}
        # 按站点下标匹配，避免不同源复用同一个 key 时把无关站点一起带进来
        grades = {r["idx"]: rank[r["grade"]] for r in results if r["grade"] in rank}
        # 同 jar 推断出来的 likely 没有实测依据，排在同级实测节点之后
        inferred_idx = {r["idx"] for r in results if r.get("inferred")}
        pairs = sorted([(grades[i], i, s) for i, s in enumerate(cfg["sites"]) if i in grades],
                       key=lambda t: (t[0], 1 if t[1] in inferred_idx else 0, t[1]))
        kept = [s for _, _, s in pairs]
        out = {k: v for k, v in cfg.items() if k != "sites"}
        meta = dict(cfg.get("_meta", {}))
        meta["sites"] = len(kept)
        meta["note"] = "仅保留经首页数据探测确认可显示主界面推荐的站点"
        meta["home_grades"] = by_grade
        meta["home_inferred"] = inferred
        meta["probe_env"] = common.env_info()
        # 首页版是全量的子集，jar 统计必须按这一份重算（曾出现实际 19 个、
        # meta 仍写 53 个的情况，客户端 / 排查时会误判依赖面）。
        jars = Counter(str(s.get("jar") or "").split(";", 1)[0] for s in kept
                       if str(s.get("jar") or "").startswith("http"))
        meta["jar_count"] = len(jars)
        meta["jar_top"] = dict(jars.most_common(5))
        out["_meta"] = meta
        out["sites"] = kept
        common.write_json(args.output, out, skip_unchanged=False)
        print("\n已生成 %s：%d 个站点（%.1f KB）" %
              (args.output, len(kept), os.path.getsize(args.output) / 1024))

        # 人可读清单：等级 | 站点名 | 判定依据
        by_key = {r["idx"]: r for r in results}
        lines = ["# 能显示主界面推荐（首页）的节点",
                 "# 等级：ok=首页影片列表实测非空 / likely=爬虫jar与首页配置均有效(含同jar推断) /",
                 "#       classes_only=只返回分类（首页可能空）",
                 "# 判定环境：%s | 同 jar 推断召回：%d" %
                 (common.env_info()["env"], inferred),
                 "# 生成时间：%s" % time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), ""]
        for _, idx, s in pairs:
            r = by_key.get(idx, {})
            d = r.get("detail", {})
            why = ("首页影片 %s 条 via %s" % (d.get("videos"), d.get("via"))
                   if r.get("grade") == "ok" else
                   "jar:%s ext:%s%s" % (d.get("jar", "-"), d.get("ext", "-"),
                                        " [推断]" if r.get("inferred") else "")
                   if r.get("grade") == "likely" else "仅分类 %s 组" % d.get("classes"))
            lines.append("%-13s | %s | %s" % (r.get("grade", "?"),
                                             str(s.get("name", "")).strip()[:28], why[:70]))
        with open(args.list_file, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        print("已生成 %s（可读清单）" % args.list_file)
    print("明细：%s" % args.report)


if __name__ == "__main__":
    main()
