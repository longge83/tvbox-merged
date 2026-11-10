# -*- coding: utf-8 -*-
"""从大型导航仓库 README / 聚合配置中挖出候选 TVBox 接口 URL"""
import json, re, ssl, urllib.request, time
from concurrent.futures import ThreadPoolExecutor

CTX = ssl.create_default_context(); CTX.check_hostname=False; CTX.verify_mode=ssl.CERT_NONE
UA = "okhttp/4.9.0"

def get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
        return r.read().decode("utf-8", "ignore")

README_REPOS = [
    "youhunwl/TVAPP", "laoma528/awesome-zhuiju-free", "Zhou-Li-Bin/Tvbox-QingNing",
    "adminouyang/231006", "kimwang1978/collect-txt", "noimank/tvbox",
    "apaodh/apaodh", "qist/tvbox", "jinenge/tvbox", "xianyuyimu/TVBOX-",
    "qirenzhidao/tvbox18", "Dong-learn9/TVBox-zyjk", "scovis/TVBox",
    "laoma528/awesome-zhuiju-free", "HerbertHe/iptv-sources", "joevess/IPTV",
]

# 已知多仓配置（展开子线路）
AGGREGATORS = [
    "https://raw.githubusercontent.com/noimank/tvbox/main/tvboxmuti.json",
]

URL_RE = re.compile(r"https?://[^\s\)\]\}\"<>`\\，,；;）]+")
SKIP = ("github.com", "raw.githubusercontent", "user-images", "img.shields",
        "badge", "avatars", "jsdelivr.net/npm", "t.me", "t.me/", "youtube.com",
        "bilibili.com/video", "pan.quark", "pan.baidu", "docs.qq.com",
        "workbuddy", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico",
        ".apk", ".zip", ".m3u8", ".md", ".svg", "twitter.com", "t.me")

def looks_config(u):
    low = u.lower()
    if any(s in low for s in SKIP): return False
    return (".json" in low or low.rstrip("/").endswith(("/tv", "/ok", "/dc", "/m", "/xs", "/box", "/api", "/ysc", "/tvbox"))
            or re.search(r"/(tv|api|box|json|ysc|dc|ok|m3u)[^\s]*$", low))

def main():
    found = {}
    def scan(repo):
        out = []
        for br in ("main", "master"):
            for name in ("README.md", "readme.md", "README.MD"):
                try:
                    txt = get(f"https://raw.githubusercontent.com/{repo}/{br}/{name}")
                    out.append((repo, br, txt)); break
                except Exception: pass
            if out: break
        return out
    with ThreadPoolExecutor(max_workers=8) as ex:
        for res in ex.map(scan, README_REPOS):
            for repo, br, txt in res:
                for u in URL_RE.findall(txt):
                    u = u.rstrip(".,;:、，。")
                    if looks_config(u):
                        found.setdefault(u, set()).add(repo)
    # 展开多仓
    for agg in AGGREGATORS:
        try:
            d = json.loads(get(agg))
            for item in d.get("urls", []):
                if isinstance(item, dict) and item.get("url"):
                    u = item["url"].strip()
                    if looks_config(u): found.setdefault(u, set()).add("noimank多仓")
        except Exception as e: print("多仓展开失败", e)
    for u in sorted(found):
        print(f"{u}\t|{','.join(sorted(found[u]))[:40]}")
    print(f"\n候选总数: {len(found)}")

if __name__ == "__main__": main()
