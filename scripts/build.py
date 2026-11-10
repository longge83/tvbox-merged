#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TVBox 多源聚合 + 存活复检
- 拉取多个上游 TVBox 配置
- 相对路径绝对化（./js/x.js → 绝对 URL）、spider jar 下沉到站点级 jar 字段
- 跨源去重（归一化名称+脚本标识 / 相同 http api）
- 对 http 采集源做 ?ac=list 探活，连续失败的剔除
- 输出 tvbox.json（内容无变化时不写文件，避免无意义提交）
仅用 Python 标准库，无第三方依赖。
"""
import json, os, re, ssl, sys, time, socket
import urllib.request, urllib.error
from urllib.parse import urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed

socket.setdefaulttimeout(12)
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
UA = "okhttp/4.9.0"
OUT = os.environ.get("OUT_FILE", "tvbox.json")

SOURCES = [
    ("qist/js",  "https://raw.githubusercontent.com/qist/tvbox/master/js.json"),
    ("qist/jsm", "https://raw.githubusercontent.com/qist/tvbox/master/jsm.json"),
    ("qist/ds",  "https://raw.githubusercontent.com/qist/tvbox/master/dianshi.json"),
    ("cyao/ok",  "https://raw.githubusercontent.com/cyao2q/files/master/ok.json"),
    ("cyao/xs",  "https://raw.githubusercontent.com/cyao2q/files/master/xs.json"),
    ("cyao/m",   "https://raw.githubusercontent.com/cyao2q/files/master/m.json"),
    ("noimank",  "https://gitlab.com/noimank/tvbox/-/raw/main/tvbox1.json"),
    ("gao/js",   "https://raw.githubusercontent.com/gaotianliuyun/gao/master/js.json"),
    ("clun",     "https://clun.top/box.json"),
]


def fetch(url, tries=3, timeout=25):
    last = None
    for _ in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception as e:
            last = e
            time.sleep(1)
    raise last


def absolutize(val, base):
    """'./x' '../x' '/x' → 绝对 URL；保留 ';md5;xxx' 后缀"""
    if not isinstance(val, str) or not val:
        return val
    parts = val.split(";")
    p = parts[0]
    if p.startswith(("http://", "https://")) or not re.search(r"^\.{0,2}/", p):
        return val
    parts[0] = urljoin(base, p)
    return ";".join(parts)


def norm_name(name):
    return re.sub(r"[\s\[\]（）()【】┃｜|·•・\-—_*]+", "", str(name or ""))


def probe(site):
    """探活一个 http 采集源：?ac=list 是否返回 class/list 结构（兼容 XML 接口）"""
    api = str(site.get("api", ""))
    url = api + ("&" if "?" in api else "?") + "ac=list"
    for _ in range(2):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=10, context=CTX) as r:
                body = r.read(300000)
            txt = body.decode("utf-8", "ignore").strip()
            if "<list" in txt and "<class" in txt:          # XML 采集接口
                return True
            j = json.loads(txt)
            if isinstance(j, dict) and ("class" in j or "list" in j):
                return True
            return False
        except Exception:
            time.sleep(1)
    return False


def build():
    merged, seen_key, seen_api = [], set(), set()
    flat_lives, seen_live, parses, seen_parse = [], set(), [], set()
    wallpaper, global_spider, stats = None, None, []

    for tag, url in SOURCES:
        try:
            cfg = json.loads(fetch(url))
        except Exception as e:
            stats.append((tag, 0, 0, f"拉取失败:{type(e).__name__}"))
            continue
        base = url.rsplit("/", 1)[0] + "/"
        spider = absolutize(cfg.get("spider"), base)
        if spider and not global_spider:
            global_spider = spider
        added = dup = 0
        for s in cfg.get("sites", []):
            if not isinstance(s, dict):
                continue
            api = absolutize(s.get("api"), base)
            ext = s.get("ext")
            if isinstance(ext, str):
                ext = absolutize(ext, base)
            ext_key = ext if isinstance(ext, str) else json.dumps(ext, ensure_ascii=False, sort_keys=True)[:60]
            tail = (ext_key or api or "").rsplit("/", 1)[-1][:40]
            k1 = norm_name(s.get("name")) + "|" + tail
            k2 = api if str(api).startswith("http") else None
            if k1 in seen_key or (k2 and k2 in seen_api):
                dup += 1
                continue
            seen_key.add(k1)
            if k2:
                seen_api.add(k2)
            ns = dict(s)
            ns["api"] = api
            if ext is not None:
                ns["ext"] = ext
            if spider and str(api).startswith("csp_") and not ns.get("jar"):
                ns["jar"] = spider
            if ns.get("jar"):
                ns["jar"] = absolutize(ns["jar"], base)
            merged.append(ns)
            added += 1

        for g in cfg.get("lives", []):
            if not isinstance(g, dict):
                continue
            if "channels" in g:                       # 旧版嵌套
                for ch in g.get("channels", []):
                    if not isinstance(ch, dict):
                        continue
                    u = (ch.get("urls") or [""])[0]
                    ck = norm_name(ch.get("name")) + "|" + str(u)[:80]
                    if ck in seen_live:
                        continue
                    seen_live.add(ck)
                    flat_lives.append({"group": g.get("group") or g.get("name") or "直播",
                                       "name": ch.get("name"), "url": u})
            else:                                     # 新版扁平
                u = absolutize(g.get("url"), base)
                if not u or str(u) in seen_live:
                    continue
                seen_live.add(str(u))
                ng = dict(g)
                ng["url"] = u
                flat_lives.append(ng)

        for p in cfg.get("parses", []):
            if not isinstance(p, dict):
                continue
            pk = str(p.get("url", ""))[:100] + "|" + str(p.get("name", ""))
            if pk in seen_parse:
                continue
            seen_parse.add(pk)
            parses.append(p)

        if not wallpaper and cfg.get("wallpaper"):
            wallpaper = cfg["wallpaper"]
        stats.append((tag, added, dup, "ok"))

    # ---- 存活复检：只探测 CMS 采集接口（js/csp 类源无法用 http 探活，必须保留）----
    def is_probeable(api):
        a = str(api).lower()
        return "api.php" in a or "/provide/vod" in a or "/api/vod" in a

    probeable = [s for s in merged if is_probeable(s.get("api"))]
    alive = {}
    if probeable:
        with ThreadPoolExecutor(max_workers=24) as ex:
            futs = {ex.submit(probe, s): s for s in probeable}
            for f in as_completed(futs):
                alive[id(futs[f])] = f.result()
    dropped = [s for s in merged if id(s) in alive and not alive[id(s)]]
    kept = [s for s in merged if id(s) not in alive or alive[id(s)]]

    # 稳定排序，便于 diff
    kept.sort(key=lambda x: (str(x.get("name", "")), str(x.get("api", ""))))
    out = {
        "_meta": {
            "updated": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
            "sites": len(kept),
            "sites_dropped_dead": len(dropped),
            "lives": len(flat_lives),
            "parses": len(parses),
        },
        "sites": kept,
    }
    if global_spider:
        out["spider"] = global_spider
    if wallpaper:
        out["wallpaper"] = wallpaper
    if parses:
        out["parses"] = parses
    if flat_lives:
        out["lives"] = flat_lives

    # 内容未变（忽略 updated 时间戳）则不写文件
    changed = True
    if os.path.exists(OUT):
        try:
            old = json.load(open(OUT, encoding="utf-8"))
            a, b = dict(old), dict(out)
            a.pop("_meta", None); b.pop("_meta", None)
            changed = json.dumps(a, ensure_ascii=False, sort_keys=True) != \
                      json.dumps(b, ensure_ascii=False, sort_keys=True)
        except Exception:
            changed = True

    if changed:
        with open(OUT, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)

    for tag, added, dup, note in stats:
        print(f"  {tag:10} 采纳 {added:>4}  去重 {dup:>4}  {note}")
    print(f"\n结果: 站点 {len(kept)}（剔除失效 {len(dropped)}）| 直播 {len(flat_lives)} | 解析 {len(parses)}")
    print(f"探活站点: {len(probeable)}")
    print(f"CHANGED={'true' if changed else 'false'}")
    return changed


if __name__ == "__main__":
    try:
        build()
    except Exception as e:
        print("构建失败:", e, file=sys.stderr)
        sys.exit(1)
