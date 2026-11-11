# -*- coding: utf-8 -*-
"""扫描一批 GitHub 仓库，列出可作为 TVBox 接口的 json 文件 raw 地址"""
import json, os, ssl, urllib.request, time
from concurrent.futures import ThreadPoolExecutor

CTX = ssl.create_default_context(); CTX.check_hostname=False; CTX.verify_mode=ssl.CERT_NONE

def api(path, tries=3):
    # 36 个仓库 × (repos + trees) ≈ 72 次调用，超过未认证限额 60 次/小时，
    # 尾部仓库会静默漏扫。CI 里注入 GITHUB_TOKEN 即可提升到 5000 次/小时。
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    headers = {"User-Agent": "tvbox-harvest", "Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    for _ in range(tries):
        try:
            req = urllib.request.Request("https://api.github.com" + path,
                headers=headers)
            with urllib.request.urlopen(req, timeout=20, context=CTX) as r:
                return json.load(r)
        except Exception:
            time.sleep(1)
    return {}

REPOS = [
 "guot55/yg","loopool/fan","qirenzhidao/tvbox18","chitue/dongliTV","xiongjian83/TvBox",
 "liu673cn/box","cnnbgo/tvbox","yw88075/tvbox","maoystv/6","which0113/freefish-api",
 "jyoketsu/tv","davidlee6628/ssr","lubin776/tvbox-api-backup","Lightconer/tvbox-ysc-config",
 "HowKongFish/tvbox-json","hebijunge/tvbox-config","adminouyang/231006","jiusanzhou/tvbox",
 "zhuzhuyule/juhe-subhub","JinRudy/tvbox-more","apaodh/apaodh","xianyuyimu/TVBOX-",
 "jinenge/tvbox","kimwang1978/collect-txt","scovis/TVBox","Dong-learn9/TVBox-zyjk",
 "Zhou-Li-Bin/Tvbox-QingNing","youhunwl/TVAPP","laoma528/awesome-zhuiju-free",
 "hubeizgd1979/tvbox-config","khw934/TV","royzhu36/TVBoxConfig","fengzhiku123/ysc-api",
 "huangweiping6666/tvbox-top100","xiaochwn/tvbox-sources",
]
REPOS = sorted(set(REPOS))  # 曾有重复项，重复扫描白耗 API 限额

def scan(repo):
    info = api("/repos/" + repo)
    br = info.get("default_branch")
    if not br: return []
    tree = api(f"/repos/{repo}/git/trees/{br}?recursive=1").get("tree", [])
    out = []
    for t in tree:
        p = t.get("path", "")
        if t.get("type") == "blob" and p.lower().endswith((".json", ".txt")) and (t.get("size") or 0) > 4000:
            out.append((t["size"], p, f"https://raw.githubusercontent.com/{repo}/{br}/{p}"))
    return sorted(out, reverse=True)[:8]

if __name__ == "__main__":
    with ThreadPoolExecutor(max_workers=8) as ex:
        for repo, files in zip(REPOS, ex.map(scan, REPOS)):
            if files:
                print(f"## {repo}")
                for size, path, url in files:
                    print(f"  {size:>8} {url}")
