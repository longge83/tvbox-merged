#!/usr/bin/env python3
"""
检测 TVBox 配置里哪些站点能显示「主界面推荐（首页）」。

原理：TVBox 首页推荐 ≠ 搜索，两者走不同接口。
  - CMS 采集源 (type=0/1)：首页来自 ?ac=videolist / ?ac=detail / ?ac=list
    返回的分类(class)与首页影片列表(list)。列表为空 -> 首页空白。
  - csp_* 爬虫源 (type=3)：首页由 jar 里的 homeContent() 生成，而分类/首页
    规则常放在 ext 指向的 JSON 文件里。jar 或 ext 失效 -> 首页空白但搜索可能仍可用。
  - 纯 http js 源：取决于 js 脚本本身，只做可达性检查，标 unknown。

输出：
  home_report.json   每个站点的判定明细
  tvbox-home.json    只保留「确认能出首页」的站点，按可靠度排序

用法：python scripts/home_probe.py [--limit N] [--input tvbox.json]
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

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

# 本机直连 GitHub raw 常超时，这类域名走本地代理；国内采集源直连更快
PROXY = os.environ.get("HOME_PROXY", "http://127.0.0.1:7890")
GH_HOSTS = ("raw.githubusercontent.com", "github.com", "gist.githubusercontent.com",
            "gitlab.com", "jihulab.com", "agit.ai", "gitee.com")
DIRECT = urllib.request.build_opener(urllib.request.HTTPSHandler(context=CTX))
VIA_PROXY = urllib.request.build_opener(
    urllib.request.HTTPSHandler(context=CTX),
    urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}),
)
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

    # jar 是爬虫本体，失效则整个站点（含首页）不可用
    jar_alive = True
    if jar.startswith("http"):
        st, raw = fetch(jar, limit_bytes=64)
        jar_alive = st == 200
        detail["jar"] = "HTTP %s%s" % (st, " (zip)" if raw[:2] == b"PK" else "")

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


def probe(site):
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
        "key": site.get("key"),
        "name": site.get("name"),
        "kind": kind,
        "grade": grade,
        "api": str(site.get("api", ""))[:120],
        "detail": detail,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="tvbox.json")
    ap.add_argument("--limit", type=int, default=0, help="只测前 N 个站点（调试用）")
    ap.add_argument("--workers", type=int, default=40)
    ap.add_argument("--report", default="home_report.json")
    ap.add_argument("--output", default="tvbox-home.json")
    args = ap.parse_args()

    cfg = json.load(open(args.input, encoding="utf-8"))
    sites = cfg["sites"][: args.limit] if args.limit else cfg["sites"]
    t0 = time.time()
    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(probe, s): s for s in sites}
        for i, f in enumerate(as_completed(futs), 1):
            try:
                results.append(f.result())
            except Exception as e:
                s = futs[f]
                results.append({"key": s.get("key"), "name": s.get("name"),
                                "grade": "fail", "detail": {"error": str(e)[:80]}})
            if i % 200 == 0:
                print("  已检测 %d/%d ... %.0fs" % (i, len(sites), time.time() - t0), flush=True)

    by_grade, by_kind = {}, {}
    for r in results:
        by_grade[r["grade"]] = by_grade.get(r["grade"], 0) + 1
        k = r["kind"]
        by_kind.setdefault(k, {})
        by_kind[k][r["grade"]] = by_kind[k].get(r["grade"], 0) + 1

    print("\n=== 判定统计（共 %d，耗时 %.0fs）===" % (len(results), time.time() - t0))
    for g in ("ok", "likely", "classes_only", "unknown", "fail"):
        if g in by_grade:
            print("  %-13s %d" % (g, by_grade[g]))
    print("  按类型:")
    for k, v in sorted(by_kind.items()):
        print("    %-8s %s" % (k, dict(v)))

    with open(args.report, "w", encoding="utf-8") as f:
        json.dump({"scanned": len(results), "grades": by_grade, "sites": results},
                  f, ensure_ascii=False, indent=2)

    if not args.limit:
        # 只保留确认有首页数据的站点；ok 排最前，其次 likely
        rank = {"ok": 0, "likely": 1, "classes_only": 2}
        keys = {r["key"]: rank[r["grade"]] for r in results if r["grade"] in rank}
        kept = [s for s in cfg["sites"] if s.get("key") in keys]
        kept.sort(key=lambda s: keys.get(s.get("key"), 99))
        out = {k: v for k, v in cfg.items() if k != "sites"}
        meta = dict(cfg.get("_meta", {}))
        meta["sites"] = len(kept)
        meta["note"] = "仅保留经首页数据探测确认可显示主界面推荐的站点"
        meta["home_grades"] = by_grade
        out["_meta"] = meta
        out["sites"] = kept
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print("\n已生成 %s：%d 个站点（%.1f KB）" %
              (args.output, len(kept), os.path.getsize(args.output) / 1024))
    print("明细：%s" % args.report)


if __name__ == "__main__":
    main()
