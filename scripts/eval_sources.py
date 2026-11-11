# -*- coding: utf-8 -*-
"""【已废弃】评估候选源在去重后的边际贡献（相对当前 9 源基线）。

此脚本为第一轮的硬编码基线版本，已由 scripts/discover.py 取代（动态读取
tvbox.json 基线 + content_key 判重口径），未接入任何 workflow。保留仅供
历史参考，请勿依据其结论做并源决策。
"""
import json, re, ssl, sys, time, urllib.request
from urllib.parse import urljoin
from concurrent.futures import ThreadPoolExecutor

CTX = ssl.create_default_context(); CTX.check_hostname=False; CTX.verify_mode=ssl.CERT_NONE
UA = "okhttp/4.9.0"

def get(url, tries=3):
    for _ in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=25, context=CTX) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception as e:
            last = e; time.sleep(1)
    raise last

def absu(v, base):
    if not isinstance(v, str) or not v: return v
    parts = v.split(";"); p = parts[0]
    if p.startswith(("http://","https://")) or not re.search(r"^\.{0,2}/", p): return v
    parts[0] = urljoin(base, p)
    return ";".join(parts)

def norm(n): return re.sub(r"[\s\[\]（）()【】┃｜|·•・\-—_*]+","",str(n or ""))

def keys(cfg, base):
    out = []
    for s in cfg.get("sites", []):
        if not isinstance(s, dict): continue
        api = absu(s.get("api"), base)
        ext = s.get("ext")
        if isinstance(ext, str): ext = absu(ext, base)
        ek = ext if isinstance(ext, str) else json.dumps(ext, ensure_ascii=False, sort_keys=True)[:60]
        tail = (ek or api or "").rsplit("/",1)[-1][:40]
        out.append((norm(s.get("name"))+"|"+tail, api if str(api).startswith("http") else None))
    return out

def load(item):
    tag, url = item
    try:
        cfg = json.loads(get(url))
        return tag, url, keys(cfg, url.rsplit("/",1)[0]+"/"), cfg
    except Exception as e:
        return tag, url, [], {}

BASE = [
 ("qist/js","https://raw.githubusercontent.com/qist/tvbox/master/js.json"),
 ("qist/jsm","https://raw.githubusercontent.com/qist/tvbox/master/jsm.json"),
 ("qist/ds","https://raw.githubusercontent.com/qist/tvbox/master/dianshi.json"),
 ("cyao/ok","https://raw.githubusercontent.com/cyao2q/files/master/ok.json"),
 ("cyao/xs","https://raw.githubusercontent.com/cyao2q/files/master/xs.json"),
 ("cyao/m","https://raw.githubusercontent.com/cyao2q/files/master/m.json"),
 ("noimank","https://gitlab.com/noimank/tvbox/-/raw/main/tvbox1.json"),
 ("gao/js","https://raw.githubusercontent.com/gaotianliuyun/gao/master/js.json"),
 ("clun","https://clun.top/box.json"),
]
EXCLUDE = ("r18", "18/xgg", "adult", "madou")

if __name__ == "__main__":
    cand_file = sys.argv[1]
    cand = []
    for l in open(cand_file, encoding="utf-8"):
        u = l.strip()
        if not u or u.startswith("#"): continue
        if any(x in u.lower() for x in EXCLUDE): continue
        cand.append(("new", u))
    with ThreadPoolExecutor(max_workers=10) as ex:
        base_res = list(ex.map(load, BASE))
        cand_res = list(ex.map(load, cand))
    seen_key, seen_api = set(), set()
    for tag,url,ks,_ in base_res:
        for k,a in ks:
            seen_key.add(k)
            if a: seen_api.add(a)
    print(f"基线去重后: {len(seen_key)} 站点键\n")
    rows = []
    for tag,url,ks,cfg in cand_res:
        new, apis = 0, 0
        for k,a in ks:
            if k in seen_key or (a and a in seen_api): continue
            new += 1
            if a: apis += 1
        lives = len(cfg.get("lives",[])); parses = len(cfg.get("parses",[]))
        if new or lives or parses:
            rows.append((new, apis, lives, parses, url))
    rows.sort(reverse=True)
    for new, apis, lives, parses, url in rows:
        print(f"  +{new:>4} 新站点 (采集型{apis:>3}) 直播{lives:>3} 解析{parses:>3} | {url}")
    print(f"\n候选可用且有贡献: {len(rows)}")
