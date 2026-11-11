#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TVBox 多源聚合 + 存活复检（每日 Actions 主构建）

- 拉取 sources.txt 里的多个上游配置
- 相对路径绝对化（./js/x.js → 绝对 URL）、spider jar 下沉到站点级 jar 字段
- 跨源按「内容指向」去重（api + ext + jar，或与名称无关的归一化接口）
- 关键词外置（blocklist.txt），站点 / 直播 / 解析全量过滤
- parses 规范化去重
- CMS 采集源 ?ac=list 探活；连续失败 N 次才淘汰（读 quality.json，避免上游抖动）
- 输出全量 / 精简 / 网络优化三份配置，并把判定环境写进 _meta

安全约束：非 Actions 环境默认只写 `_local-*.json` 临时文件，
绝不覆盖线上配置（曾因本地试跑把 lite 版覆盖成单源版）。
需要显式写正式文件时设置 ALLOW_LOCAL_WRITE=1。

仅用 Python 标准库，无第三方依赖。
"""
import json, os, re, ssl, sys, time, socket
import urllib.request, urllib.error
from urllib.parse import urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common

socket.setdefaulttimeout(12)
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
UA = "okhttp/4.9.0"

# 默认上游（sources.txt 不存在时使用）。每行 名称|URL[|相对路径基准]，# 为注释。
SOURCES = [
    ("qist/js",  "https://raw.githubusercontent.com/qist/tvbox/master/js.json", ""),
    ("qist/jsm", "https://raw.githubusercontent.com/qist/tvbox/master/jsm.json", ""),
    ("qist/ds",  "https://raw.githubusercontent.com/qist/tvbox/master/dianshi.json", ""),
    ("cyao/ok",  "https://raw.githubusercontent.com/cyao2q/files/master/ok.json", ""),
    ("cyao/xs",  "https://raw.githubusercontent.com/cyao2q/files/master/xs.json", ""),
    ("cyao/m",   "https://raw.githubusercontent.com/cyao2q/files/master/m.json", ""),
    ("noimank",  "https://gitlab.com/noimank/tvbox/-/raw/main/tvbox1.json", ""),
    ("gao/js",   "https://raw.githubusercontent.com/gaotianliuyun/gao/master/js.json", ""),
    ("clun",     "https://clun.top/box.json", ""),
]

FALLBACK_BLOCK = [r"成人", r"伦理", r"无码", r"番号", r"色情", r"情色",
                  r"porn", r"hentai", r"91porn", r"4kav"]

# 网络优化版要排除「配置住在境外代码托管 / GitHub 反代」上的节点，
# 名单只此一份（common.HOSTING_*），只看 api/jar/ext 的域名，与片源内容无关。


def load_sources():
    path = os.environ.get("SOURCES_FILE", "sources.txt")
    if not os.path.exists(path):
        return SOURCES
    out = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        cols = [c.strip() for c in line.split("|")]
        if len(cols) >= 2:
            out.append((cols[0], cols[1], cols[2] if len(cols) > 2 else ""))
        else:
            out.append((line.rsplit("/", 1)[-1][:12], line, ""))
    return out or SOURCES


def load_blocklist():
    path = os.environ.get("BLOCKLIST_FILE", "blocklist.txt")
    pats = []
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#"):
                pats.append(line)
    else:
        pats = FALLBACK_BLOCK
    try:
        rx = re.compile("|".join("(?:%s)" % p for p in pats), re.I)
    except re.error as e:
        print("blocklist 正则有误(%s)，回退内置关键词" % e, file=sys.stderr)
        rx = re.compile("|".join(FALLBACK_BLOCK), re.I)
    return rx, len(pats)


BLOCK_RX, BLOCK_COUNT = load_blocklist()


def blocked(obj, fields=("name", "api", "ext", "jar", "url", "urls", "group")):
    # ext 可能内嵌很长的 JSON，截 200 字符会让出现在尾部的关键词漏过；
    # 断言 content_filter_clean 用同一函数，口径必须一致。
    text = " ".join(str(obj.get(f, ""))[:2000] for f in fields)
    return bool(BLOCK_RX.search(text))


def url_alive(url, timeout=6):
    """只取少量字节判断 URL 是否存在（用于校验 spider jar 的基准路径）"""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Range": "bytes=0-63"})
        with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
            r.read(64)
            return r.status in (200, 206)
    except Exception:
        return False


def choose_base(url, cfg, hint=""):
    """确定 ./ 相对路径的基准目录。

    多数上游把 ./x.js 写作「相对配置文件所在目录」；也有上游（yw88075）写成
    「相对仓库根目录」。拼错会得到 XBPQ/yw530.jar 这类 404，整批爬虫节点首页空白。
    这里用 spider jar 探测，自动挑出能命中的基准。
    """
    dir_base = url.rsplit("/", 1)[0] + "/"
    if hint:
        return hint.rstrip("/") + "/"
    m = re.match(r"https://raw\.githubusercontent\.com/([^/]+)/([^/]+)/([^/]+)/(.+)", url)
    if not m:
        return dir_base
    root_base = "https://raw.githubusercontent.com/%s/%s/%s/" % m.group(1, 2, 3)
    if root_base == dir_base or "/" not in m.group(4):
        return dir_base
    sp = str(cfg.get("spider") or "").split(";")[0]
    if not re.match(r"^\.{0,2}/", sp):
        return dir_base
    if url_alive(urljoin(dir_base, sp)):
        return dir_base
    if url_alive(urljoin(root_base, sp)):
        return root_base
    return dir_base


def fetch(url, tries=3, timeout=25):
    last = None
    # 中文/空格文件名的 URL 必须先 percent-encode，否则 urlopen 抛
    # UnicodeEncodeError，被当成「拉取失败」静默跳过（common.fetch 已做，这里对齐）
    url = common.safe_url(url)
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


def parse_key(p):
    """解析通道去重键：按 URL 的内容指向归一（是否生效只取决于 url，与名字无关）。

    同一份解析脚本挂在 raw / gh-proxy / jsdelivr 上，或带 www / 尾斜杠差异，
    content_url 会把它们还原成同一个键。ext 是 header / flag 配置，一并入键，
    避免 url 相同但配置不同的两条通道被合并。
    """
    return (common.content_url(p.get("url", "")),
            common.content_url(p.get("ext")))


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


def output_paths():
    """本地试跑只写 _local-*.json；Actions 才写正式文件名。"""
    if common.IS_CI or os.environ.get("ALLOW_LOCAL_WRITE") == "1":
        return (os.environ.get("OUT_FILE", "tvbox.json"),
                os.environ.get("LITE_FILE", "tvbox-lite.json"),
                os.environ.get("NETWORK_FILE", "tvbox-network.json"))
    print("!" * 66, flush=True)
    print("! 本地试跑保护：输出重定向到 _local-*.json，不会覆盖线上配置。", flush=True)
    print("! 确实要写正式文件时，设置 ALLOW_LOCAL_WRITE=1 再运行。", flush=True)
    print("!" * 66, flush=True)
    return ("_local-tvbox.json", "_local-tvbox-lite.json", "_local-tvbox-network.json")


def build():
    out_path, lite_path, network_path = output_paths()
    merged, seen_content = [], set()
    flat_lives, seen_live, parses, seen_parse = [], set(), [], set()
    wallpaper, global_spider, stats = None, None, []
    blocked_n, blocked_live_n, blocked_parse_n = 0, 0, 0
    parse_dup = 0

    for tag, url, hint in load_sources():
        try:
            cfg = json.loads(fetch(url))
        except Exception as e:
            stats.append((tag, 0, 0, f"拉取失败:{type(e).__name__}"))
            continue
        # TVBox 圈的「多仓列表」接口返回 JSON 数组/字符串很常见；cfg.get 会抛
        # AttributeError 且冒泡到 __main__ 兜底 → 整天构建失败（断言在 build 之后
        # 拦不住）。单源坏数据只能跳过该源，不能拖垮全天产出。
        if not isinstance(cfg, dict):
            stats.append((tag, 0, 0,
                          f"格式异常:顶层是{type(cfg).__name__}而非对象,已跳过"))
            continue
        base = choose_base(url, cfg, hint)
        if base != url.rsplit("/", 1)[0] + "/":
            print("  %s: 相对路径基准改用仓库根 %s" % (tag, base), flush=True)
        spider = absolutize(cfg.get("spider"), base)
        if spider and not global_spider:
            global_spider = spider
        # 同源 jar 兜底：有些上游没写全局 spider，只在少数站点写了 jar。
        # 用同源里第一个有效 jar 补齐该源其它 csp 站点，避免整批首页空白。
        src_jar = spider
        if not src_jar:
            for _s in cfg.get("sites", []):
                if isinstance(_s, dict) and str(_s.get("jar") or "").startswith("http"):
                    src_jar = absolutize(_s["jar"], base)
                    break
        added = dup = 0
        for s in cfg.get("sites", []):
            if not isinstance(s, dict):
                continue
            if blocked(s):
                blocked_n += 1
                continue
            api = absolutize(s.get("api"), base)
            ext = s.get("ext")
            if isinstance(ext, str):
                ext = absolutize(ext, base)
            # 同源 jar 兜底必须在判重之前补：两条内容相同的 csp 站点常常一条
            # 自己写了 jar、另一条靠全局 spider 补，补完才是同一个内容指向。
            jar = s.get("jar")
            if not jar and src_jar and str(api).startswith("csp_"):
                jar = src_jar
            if jar:
                jar = absolutize(jar, base)
            # 名称不参与判重：它是显示用的皮（❤野荷塘 / 😘野荷塘），
            # 真正决定内容的是 api + ext（配置文件）+ jar（spider）。
            ck = common.content_key({"api": api, "ext": ext, "jar": jar,
                                     "type": s.get("type"),
                                     "playerType": s.get("playerType")})
            if ck in seen_content:
                dup += 1
                continue
            seen_content.add(ck)
            ns = dict(s)
            ns["api"] = api
            if ext is not None:
                ns["ext"] = ext
            if jar:
                ns["jar"] = jar
            merged.append(ns)
            added += 1

        for g in cfg.get("lives", []):
            if not isinstance(g, dict):
                continue
            if blocked(g):
                blocked_live_n += 1
                continue
            if "channels" in g:                       # 旧版嵌套
                for ch in g.get("channels", []):
                    if not isinstance(ch, dict) or blocked(ch):
                        if isinstance(ch, dict) and blocked(ch):
                            blocked_live_n += 1
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
                ck = (common.content_url(u), common.content_url(g.get("ext")))
                if not u or ck in seen_live:
                    continue
                seen_live.add(ck)
                ng = dict(g)
                ng["url"] = u
                flat_lives.append(ng)

        for p in cfg.get("parses", []):
            if not isinstance(p, dict):
                continue
            if blocked(p):
                blocked_parse_n += 1
                continue
            np_ = dict(p)
            np_["url"] = absolutize(np_.get("url"), base)
            pk = parse_key(np_)
            # 原来的 `if not pk` 恒为假（parse_key 返回二元组），空 url 解析通道
            # 拦不住；显式按 url 判定
            if not str(np_.get("url") or "").strip() or pk in seen_parse:
                parse_dup += 1
                continue
            seen_parse.add(pk)
            parses.append(np_)

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

    # 多日判定：连续失败达到阈值才真正淘汰，避免上游一次抖动就改动线上配置。
    quality = common.load_json("quality.json", {}) or {}
    fail_streaks = quality.get("probe_fail_streak", {})
    has_history = bool(quality.get("updated"))
    dead_after = int(os.environ.get("DEAD_AFTER_FAILS", "3"))
    dead_sigs, kept, observed = [], [], 0
    observed_ids = set()
    for s in merged:
        if id(s) not in alive or alive[id(s)]:
            kept.append(s)
            continue
        sg = common.sig(s)
        streak = int(fail_streaks.get(sg, 0)) + 1
        dead_sigs.append(sg)
        if has_history and streak < dead_after:
            observed += 1                        # 观察期：暂时保留，不写额外字段
            observed_ids.add(id(s))
            kept.append(s)
    dropped_n = len(dead_sigs) - observed

    # 稳定排序，便于 diff；观察期（连败但未达阈值）的站点沉到末尾，
    # 避免客户端默认落在刚探活失败的源上。
    kept.sort(key=lambda x: (1 if id(x) in observed_ids else 0,
                             str(x.get("name", "")), str(x.get("api", ""))))

    # jar 分级：统计依赖，供健康页与网络优化版使用
    jar_groups = {}
    for s in kept:
        j = str(s.get("jar") or "")
        if j:
            jar_groups[j] = jar_groups.get(j, 0) + 1

    meta = {
        "updated": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "sites": len(kept),
        "sites_dropped_dead": dropped_n,
        "sites_in_observation": observed,
        "dead_policy": ("连续失败 %d 次才淘汰" % dead_after) if has_history else "首轮构建：单日失败即淘汰",
        "sites_blocked": blocked_n,
        "lives_blocked": blocked_live_n,
        "parses_blocked": blocked_parse_n,
        "parses_deduped": parse_dup,
        "blocklist_rules": BLOCK_COUNT,
        "lives": len(flat_lives),
        "parses": len(parses),
        "jar_count": len(jar_groups),
        "jar_top": sorted(jar_groups.items(), key=lambda x: -x[1])[:5],
        "sources_ok": sum(1 for t, a, d, n in stats if n == "ok"),
        "sources_failed": [t for t, a, d, n in stats if n != "ok"],
        "probe_env": common.env_info(),
    }

    def make(sites, note=None):
        out = {"_meta": dict(meta, sites=len(sites)), "sites": sites}
        if global_spider:
            out["spider"] = global_spider
        if wallpaper:
            out["wallpaper"] = wallpaper
        if parses:
            out["parses"] = parses
        if flat_lives:
            out["lives"] = flat_lives
        if note:
            out["_meta"]["note"] = note
        return out

    # 精简版：仅保留不依赖 spider jar 的 http 站点
    lite_sites = [s for s in kept if str(s.get("api", "")).startswith("http")]
    # 网络优化版：连 jar/ext 也不落在境外代码托管上，国内直连最稳、体积最小
    def network_ok(s):
        return (str(s.get("api", "")).startswith("http")
                and not common.hosting_host(s, deep=True))

    network_sites = [s for s in lite_sites if network_ok(s)]

    written = {
        out_path: common.write_json(out_path, make(kept, "全量版")),
        lite_path: common.write_json(lite_path, make(lite_sites, "仅 http 站点，不依赖 spider jar")),
        network_path: common.write_json(network_path, make(network_sites, "国内直连优化：不依赖境外代码托管的 jar/ext")),
    }

    # 供 history.py / health.py 消费的中间数据（下划线前缀，不入库）
    common.write_json("_build_stats.json", {
        "date": common.today(),
        "env": common.env_info(),
        "sources": [{"tag": t, "added": a, "dup": d, "note": n} for t, a, d, n in stats],
        "sources_failed": meta["sources_failed"],
        "sources_ok": meta["sources_ok"],
        "sites": len(kept),
        "lite": len(lite_sites),
        "network": len(network_sites),
        "probed": len(probeable),
        "dead_sigs": dead_sigs,
        "dropped": dropped_n,
        "observation": observed,
        "parses": len(parses),
        "parses_deduped": parse_dup,
        "lives": len(flat_lives),
        "jar_count": len(jar_groups),
        "blocked": {"sites": blocked_n, "lives": blocked_live_n, "parses": blocked_parse_n},
        "files_changed": [k for k, v in written.items() if v],
    }, skip_unchanged=False)

    for tag, added, dup, note in stats:
        print(f"  {tag:10} 采纳 {added:>4}  去重 {dup:>4}  {note}")
    print(f"\n结果: 站点 {len(kept)}（淘汰失效 {dropped_n}，观察期保留 {observed}）"
          f"| 直播 {len(flat_lives)} | 解析 {len(parses)}（去重 {parse_dup}）")
    print(f"探活站点: {len(probeable)} | 精简版: {len(lite_sites)} | 网络优化版: {len(network_sites)}")
    print(f"jar 种类: {len(jar_groups)} | 过滤: 站点 {blocked_n} 直播 {blocked_live_n} 解析 {blocked_parse_n}")
    print("CHANGED=%s" % ("true" if any(written.values()) else "false"))
    return any(written.values())


if __name__ == "__main__":
    try:
        build()
    except Exception as e:
        print("构建失败:", e, file=sys.stderr)
        sys.exit(1)
