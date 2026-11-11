#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""直播源审计 —— 把「列表地址」拆成频道，实测能不能播，剔掉收费/导流源。

为什么需要单独一轮：
  lives 里存的只是「列表 URL」，聚合阶段看不到里面有什么。实测 389 条河南台
  地址只有 22 条真出 TS 码流（5.7%），其余分四类：组播内网地址（公网必黑）、
  签名过期 403、死链，以及大量频道共同指向同一个「VIP 提示视频」的收费壳。
  用户点开河南卫视看到的黑屏 / 广告，就是这四类造成的。

判据（每条都必须可解释）：
  1. 死链    列表拉不到、分片 404、返回网页/JSON
  2. 组播    /rtp/ /udp/ 239.x，只在当地运营商内网有效
  3. IPv6    分片是 [2408:...] 这类字面量，无 v6 出口的环境必然失败
  4. 收费壳  一半以上频道是促销/导流，或某个提示视频被大量频道复用

产出：
  live-henan.m3u        自己校验过的河南台专列表（可播的在前，按线路编号）
  reports/live-audit.json  每源频道数 / 可播率 / 广告占比 / 河南可播数

用法：
  python scripts/live_audit.py                  # 只出报告
  python scripts/live_audit.py --apply          # 同时改配置：剔广告壳、河南优先、插专列表
  python scripts/live_audit.py --extra live_sources.txt   # 把候选新源一起评估
"""
import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common

# 与 assertions.py 的 CONFIGS 保持一致：审计结果要能落到每一份可导入的配置上。
CONFIGS = [
    "tvbox.json", "tvbox-lite.json", "tvbox-network.json", "tvbox-home.json",
    "tvbox-full-homefirst.json", "tvbox-lite-homefirst.json",
    "tvbox-network-homefirst.json", "tvbox-quality.json", "tvbox-cn-fast.json",
    "tvbox-stable7.json",
]

PAGES_BASE = "https://dimogu.github.io/tvbox-merged/"
HENAN_LIST = "live-henan.m3u"
HENAN_LIST_URL = PAGES_BASE + HENAN_LIST
HENAN_ENTRY = "河南台 · 自建校验"

# 河南台识别：只认「河南 + 台名特征」。裸「河南」会把《中国有个河南》这类
# 歌曲、以及「河南郑东新区千玺广场」这类景区慢直播一起收进来。
HENAN_RX = re.compile(
    r"河南\s*(?:卫视|卫星|都市|民生|新闻|乡村|法制|法治|公共|影视|电视剧|"
    r"梨园|国际|交通|农村|大象|航空|武术|戏曲|睛彩|都市|[\d一二三四五六七八九]套)"
    r"|henan\s*(?:sat|tv|weishi|tv\s*\d)", re.I)
HENAN_MAIN_RX = re.compile(r"河南\s*(?:卫视|卫星)|henan\s*(?:sat|weishi)", re.I)

# 收费 / 导流特征。只收「明确要钱或明确导流」的词：
# 「公告/通知/备用/测试/免责」这类正常列表也常见，收进来会造成大面积误伤。
AD_NAME_RX = re.compile(
    r"收费|付费|会员|加群|客服|扫码|二维码|推广|广告|福利|试看|秒杀|购买|下单|"
    r"关注公众号|公众号|下载APP|充值|解锁|激活码|加盟|代理", re.I)
AD_URL_RX = re.compile(
    r"vip_notice|/notice|guanggao|/ads?/|/adv/|advert|pay\.|/buy|jiameng|"
    r"tuiguang|/gg/|qrcode|shikan|/trial", re.I)

MCAST_RX = re.compile(r"/(?:rtp|udp|rtsp)/", re.I)

# 名实校验：上游把频道名写错的不少（实测 dxtx.hntv.tv/live/lypd 被标成
# 「河南卫视」，lypd 其实是梨园频道）。用频道号/路径特征纠正，避免用户点
# 「河南卫视」看到戏曲台 —— 这正是「搜到了但不是我要的」那类抱怨。
NAME_FIX = [
    (re.compile(r"/hls/49/"), "河南卫视高清"),
    (re.compile(r"/hls/33/"), "河南卫视"),
    (re.compile(r"/tsfile/live/0139_"), "河南卫视"),
    (re.compile(r"lypd", re.I), "河南梨园频道"),
    (re.compile(r"hnws|id=hnws|haws", re.I), "河南卫视"),
    (re.compile(r"/play/44\.m3u8|/live6/henan", re.I), "河南卫视"),
]


def decode(b):
    for enc in ("utf-8", "utf-8-sig", "gb18030", "big5"):
        try:
            return b.decode(enc)
        except Exception:
            continue
    return b.decode("utf-8", "ignore")


def parse_playlist(text):
    """M3U 与 TXT 两种格式都认，返回 [(name, url, group)]。"""
    out = []
    if "#EXTM3U" in text[:3000] or "#EXTINF" in text[:8000]:
        group, name = "", ""
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("#EXTINF"):
                name = line.rsplit(",", 1)[-1].strip() if "," in line else ""
                m = re.search(r'group-title="([^"]*)"', line)
                group = m.group(1) if m else ""
            elif line.startswith("#"):
                continue
            elif line.startswith(("http://", "https://", "rtmp://", "rtsp://")):
                if name:
                    out.append((name, line, group))
                name = ""
    else:
        group = ""
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ",#genre#" in line:
                group = line.split(",")[0]
                continue
            if "," in line:
                n, _, u = line.partition(",")
                u = u.strip()
                if u.startswith(("http://", "https://", "rtmp://", "rtsp://")):
                    out.append((n.strip(), u, group))
    return out


def is_v6(url):
    try:
        host = urlsplit(url).hostname or ""
    except Exception:
        return False
    return ":" in host


def fetch_text(url, timeout=25, limit=3 * 1024 * 1024):
    st, body = common.fetch(url, timeout=timeout, limit_bytes=limit)
    return st, decode(body)


def probe(url, timeout=8, depth=0):
    """可播 = 列表能拉到 + 首个分片真能下到字节。

    只验证到「列表 200」是不够的：大量签名过期的地址照样返回 200 空列表，
    必须再往下一层。返回 (ok, kind, detail)，kind 供报告分类。"""
    u = url.split("|")[0].strip()
    if not u.startswith(("http://", "https://")):
        return False, "nonhttp", "非 http"
    if MCAST_RX.search(u):
        return False, "multicast", "组播内网"
    if is_v6(u):
        return False, "v6", "IPv6 地址"
    st, body = common.fetch(u, timeout=timeout, limit_bytes=200000)
    if st not in (200, 206):
        return False, "dead", "HTTP%d" % st
    head = body[:3000].lower()
    if b"#extm3u" not in head and b"#extinf" not in head:
        if body[:1] == b"\x47":
            return True, "ts", "TS 直链"
        if b"<html" in head or b"<!doctype" in head:
            return False, "web", "返回网页"
        if b"{" in body[:80]:
            return False, "json", "返回 JSON"
        return False, "nonhls", "非 HLS"
    text = decode(body)
    if "#EXT-X-STREAM-INF" in text:
        if depth >= 2:
            return False, "master", "master 无可用子列表"
        nxt = ""
        for line in text.splitlines():
            if line.strip().startswith(("http://", "https://")):
                nxt = line.strip()
                break
        if not nxt:
            return False, "master", "master 无子地址"
        sub = urljoin(u, nxt)
        if is_v6(sub):
            return False, "v6", "子列表仅 IPv6"
        return probe(sub, timeout, depth + 1)
    segs = [l.strip() for l in text.splitlines()
            if l.strip() and not l.strip().startswith("#")]
    if not segs:
        return False, "empty", "列表空"
    seg = urljoin(u, segs[0])
    if is_v6(seg):
        return False, "v6", "分片仅 IPv6"
    st2, b2 = common.fetch(seg, timeout=timeout, limit_bytes=4096)
    if st2 in (200, 206) and b2:
        # 首字节 0x47 是 TS 同步字节；不对说明下到的是错误页而不是码流。
        if b2[:1] == b"\x47" or b2[:3] == b"\x00\x00\x01":
            return True, "hls", "分片真码流"
        return False, "garbage", "分片非码流"
    return False, "dead", "分片 HTTP%d" % st2


def fix_name(name, url):
    for rx, real in NAME_FIX:
        if rx.search(url):
            return real
    return name


def audit_source(name, url, deep=False, sample=0):
    """一个直播列表 → 频道统计 + 河南条目。deep=True 时对抽样频道实测可播。"""
    t0 = time.time()
    st, text = fetch_text(url)
    if st != 200:
        return {"name": name, "url": url, "status": st, "chans": 0, "sec": round(time.time() - t0, 1),
                "ad": 0, "ad_ratio": 0.0, "reuse": 0, "henan": [], "playable": None,
                "err": "拉取失败"}
    chans = parse_playlist(text)
    ad = sum(1 for n, u, g in chans if AD_NAME_RX.search(n) or AD_URL_RX.search(u))
    # 复用度：同一个地址被多少条频道共用。正常列表即便有重复，也远低于 10%；
    # 收费壳会把几百个频道指向同一个「VIP 提示视频」，这是最硬的证据。
    reuse = {}
    for n, u, g in chans:
        reuse[u] = reuse.get(u, 0) + 1
    reuse_max = max(reuse.values()) if reuse else 0
    top_url = max(reuse, key=reuse.get) if reuse else ""
    henan = [(fix_name(n, u), u, g) for n, u, g in chans if HENAN_RX.search(n)]

    playable = None
    if deep and chans:
        picks = chans if not sample else chans[::max(1, len(chans) // sample)][:sample]
        with ThreadPoolExecutor(max_workers=12) as ex:
            res = list(ex.map(lambda c: probe(c[1], timeout=6), picks))
        playable = sum(1 for ok, _, _ in res if ok)

    return {"name": name, "url": url, "status": st, "chans": len(chans),
            "sec": round(time.time() - t0, 1), "ad": ad,
            "ad_ratio": round(ad / len(chans), 3) if chans else 0.0,
            "reuse": reuse_max, "reuse_url": top_url[:120],
            "henan": henan, "playable": playable, "err": ""}


def shell_score(row):
    """收费壳判定分：0 正常，>=2 删。两个独立证据各计一分。"""
    score = 0
    if row["ad_ratio"] >= 0.5 and row["chans"] >= 20:
        score += 1
    # 同一个地址覆盖 >=30% 频道，且该地址本身像提示视频 —— 正常源不会这样。
    if row["chans"] >= 20 and row["reuse"] >= row["chans"] * 0.3 \
            and AD_URL_RX.search(row["reuse_url"] or ""):
        score += 1
    return score


def source_family(url):
    """同一运营方的列表族：host + 路径前两段（124.71.19.220/live/1）。

    广告壳很少只做一个列表 —— 实测那个站的 /live/1/0、/live/1/1 是广告，
    /live/1/2 直接拉不到。只按单条 URL 判会永远剩一条尾巴，所以按族扩散。

    两类 host 必须排除，否则扩散会横扫整个配置：
      · 本机反代 127.0.0.1 —— 几十条「内置」列表共用这一个 host；
      · GitHub 反代 —— gh-proxy.org 下挂的是不同作者的不同文件。
    """
    try:
        p = urlsplit(url)
    except Exception:
        return None
    host = (p.netloc or "").lower()
    if host.split(":")[0] in ("127.0.0.1", "localhost", "0.0.0.0"):
        return None
    if any(k in host for k in ("ghproxy", "gh-proxy", "gitmirror", "ghfast",
                               "moeyy", "github", "jsdelivr", "gitlab")):
        return None
    segs = [s for s in p.path.split("/") if s][:2]
    return host + "/" + "/".join(segs)


def load_live_blocklist():
    """已判定的收费源族（一行一个）。删除判定必须认这份名单：

    同一站的列表常有 3-4 条，其中一条拉不到时本轮探测给不出证据。只靠当轮
    探测，漏掉的那条会一直留在配置里（断言 live_sources_sane 就是这么抓到的）。
    """
    path = "live_blocklist.txt"
    out = []
    if not os.path.exists(path):
        return out
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def shell_urls(rows):
    """要剔除的收费源：自身是壳，或与某个壳同族且自身没有「正常」证据。"""
    bad = set()
    fams = {}
    for r in rows:
        f = source_family(r["url"])
        if f:
            fams.setdefault(f, []).append(r)
        if r["shell"] >= 2:
            bad.add(r["url"])
    for f, rs in fams.items():
        if not any(r["shell"] >= 2 for r in rs):
            continue
        for r in rs:
            # 同族里能正常拉到、而且广告占比低的，是运营方另外维护的干净列表，
            # 放过；其余（拉不到的、广告占比高的）一起清掉。
            if r["chans"] >= 20 and r["ad_ratio"] < 0.3 and r["shell"] < 2:
                continue
            bad.add(r["url"])
    return bad


def henan_rank(row, playable_map):
    """河南线路排序：先看实测，再看地址形态（越像本地 EPG 单播越稳）。"""
    u = row["url"]
    if playable_map.get(u) is True:
        tier = 0
    elif playable_map.get(u) is None:          # 未验（v6 / 组播）不进专列表
        return None
    else:
        tier = 9                                # 实测不通的排最后，但保留
    shape = 5
    if re.search(r"/hls/(?:33|49)/", u):
        shape = 0                               # 河南移动 EPG 频道号，实测最稳
    elif "tsfile/live/" in u:
        shape = 1                               # 各省移动单播，0139 = 河南卫视
    elif re.search(r"hndt|hntv", u):
        shape = 2                               # 河南台官方 CDN（签名有时效）
    elif re.search(r"\.m3u8", u):
        shape = 3
    return (tier, shape, -row["hits"])


def build_henan_m3u(rows, per_ch=6, total=40):
    """河南台专列表：同名多条线路，客户端里可以逐条切换。

    不做「只留一条最优」：单条线路今天能播明天可能 403，多条同名才是真冗余。
    """
    best = {}
    for r in rows:
        if r["ok"] is not True:
            continue
        nm = r["ch"] or "河南台"
        d = best.setdefault(nm, [])
        if r["url"] not in [x["url"] for x in d]:
            d.append(r)
    lines = ["#EXTM3U"]
    picked = 0
    order = ["河南卫视", "河南卫视高清"]
    names = order + [k for k in best if k not in order]
    for nm in names:
        items = sorted(best.get(nm, []), key=lambda x: x.get("rk") or (9, 9, 0))
        for i, it in enumerate(items[:per_ch]):
            if picked >= total:
                break
            label = nm if i == 0 else "%s·%d" % (nm, i + 1)
            lines.append('#EXTINF:-1 tvg-name="%s" group-title="河南",%s' % (nm, label))
            lines.append(it["url"])
            picked += 1
    return "\n".join(lines) + "\n", picked


def dedup_henan(rows):
    """跨源按地址归一，同一条线路只留一次，记录它在几个源里出现。

    srcs 必须记全：一条可播线路常常同时出现在三四个源里，只记第一个会把
    「这个源有河南卫视可播」的功劳全算给排序靠前的那一个，源排序随之失真。
    """
    agg = {}
    for row in rows:
        for n, u, g in row["henan"]:
            key = u.split("#")[0]
            item = agg.get(key)
            if item:
                item["hits"] += 1
                if row["name"] not in item["srcs"]:
                    item["srcs"].append(row["name"])
                continue
            agg[key] = {"ch": n, "url": u, "group": g, "hits": 1,
                        "srcs": [row["name"]], "ok": None, "kind": "", "detail": ""}
    return list(agg.values())


def write_live_blocklist(urls):
    """把收费源以「host/前两段路径」的形式追加进 live_blocklist.txt。

    存族而不是整条 URL：同一站的列表会带不同的 ?v=、?token=，整条存等于每天
    都要重存一遍；存族一次挡住整个运营方的列表。
    """
    path = "live_blocklist.txt"
    old = []
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#"):
                old.append(line)
    new = list(old)
    for u in urls:
        f = source_family(u) or urlsplit(u).netloc
        if f and f not in new:
            new.append(f)
    if len(new) == len(old):
        return
    head = ("# 收费 / 导流直播源（由 scripts/live_audit.py 自动写入，一行一个）。\n"
            "# 匹配规则：源 URL 里包含该串即跳过。粒度是「host/前两段路径」，\n"
            "# 一次挡住同一运营方的整组列表，不误伤同 host 上别的作者。\n")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(head + "\n".join(new) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="回写配置：剔广告壳 / 河南优先 / 插专列表")
    ap.add_argument("--extra", default="", help="额外候选源清单（名称|URL），一并评估")
    ap.add_argument("--sample", type=int, default=0, help="每源抽样多少条频道测可播率，0=不抽")
    ap.add_argument("--only-extra", action="store_true",
                    help="只审 --extra 里的候选源（评估新源可播率时用，省掉 100 多个老源）")
    ap.add_argument("--probe-henan", type=int, default=600, help="河南线路最多实测多少条")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--output", default=None)
    args = ap.parse_args()

    out_path = args.output or common.result_path("live_audit")

    # 源清单 = 现有配置里的 lives ∪ 额外候选。配置里的才是「用户真能点到」的，
    # 候选只用于评估要不要采纳，未采纳的不进报告结论。
    base = common.load_json("tvbox.json", {}) or {}
    seen, sources = set(), []
    if not args.only_extra:
        for g in base.get("lives", []):
            u = str(g.get("url") or "").strip()
            if u.startswith(("http://", "https://")) and u not in seen:
                seen.add(u)
                sources.append((str(g.get("name") or u)[:40], u, True))
    extras = 0
    if args.extra and os.path.exists(args.extra):
        for line in open(args.extra, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#") or "|" not in line:
                continue
            n, _, u = line.partition("|")
            u = u.strip()
            if u.startswith(("http://", "https://")) and u not in seen:
                seen.add(u)
                sources.append((n.strip()[:40], u, False))
                extras += 1

    print("待审计直播源 %d（其中新候选 %d）" % (len(sources), extras), flush=True)
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        rows = list(ex.map(lambda s: audit_source(s[0], s[1], deep=bool(args.sample),
                                                  sample=args.sample), sources))
    for r, (_, _, is_cur) in zip(rows, sources):
        r["current"] = is_cur
        r["shell"] = shell_score(r)

    ok_src = [r for r in rows if r["chans"]]
    print("拉到频道 %d/%d，共 %d 个频道条目，用时 %.0fs" % (
        len(ok_src), len(rows), sum(r["chans"] for r in ok_src), time.time() - t0), flush=True)

    # ---- 河南线路实测 ----
    hn = dedup_henan([r for r in rows if r["henan"]])
    hn.sort(key=lambda x: (-x["hits"], x["ch"]))
    todo = hn[:args.probe_henan]
    print("河南台唯一条目 %d，实测前 %d 条…" % (len(hn), len(todo)), flush=True)
    with ThreadPoolExecutor(max_workers=16) as ex:
        res = list(ex.map(lambda x: probe(x["url"], timeout=8), todo))
    for item, (ok, kind, detail) in zip(todo, res):
        item["ok"], item["kind"], item["detail"] = ok, kind, detail
        item["rk"] = henan_rank(item, {item["url"]: ok})
    playable = [x for x in todo if x["ok"]]
    main_ok = [x for x in playable if HENAN_MAIN_RX.search(x["ch"])]
    print("河南台可播 %d/%d（其中卫视 %d）" % (len(playable), len(todo), len(main_ok)), flush=True)

    m3u, picked = build_henan_m3u(playable)
    live_msg = ""
    if picked:
        # 只有真出码流才覆盖：探测全挂（比如跑在境外节点）时保留上一版，
        # 绝不用一份空列表覆盖掉用户还能用的线路。
        with open(HENAN_LIST, "w", encoding="utf-8") as f:
            f.write(m3u)
        live_msg = "河南专列表 %d 条线路" % picked
    else:
        live_msg = "本轮没有实测可播的河南线路，保留上一版 %s" % HENAN_LIST

    # ---- 每源结论 ----
    hn_ok_by_src = {}
    for x in playable:
        for s in x["srcs"]:
            hn_ok_by_src[s] = hn_ok_by_src.get(s, 0) + 1
    for r in rows:
        r["henan_total"] = len(r["henan"])
        r["henan_ok"] = hn_ok_by_src.get(r["name"], 0)

    # 收费源判定放在这里算一次：报告要标红，--apply 要按同一份结果删，
    # 两边口径必须完全一致，否则会出现「报告说删了、配置里还留着」。
    shells = shell_urls(rows)
    for r in rows:
        r["verdict"] = ("收费广告源" if r["url"] in shells else
                        "拉取失败" if not r["chans"] else
                        "河南可播" if r["henan_ok"] else "正常")

    report = {
        "date": common.today(),
        "env": common.env_info(),
        "sources": [{k: v for k, v in r.items() if k != "henan"} for r in rows],
        "henan": [x for x in hn],
        "henan_playable": len(playable),
        "henan_main_playable": len(main_ok),
        "henan_list_lines": picked,
        "elapsed": round(time.time() - t0, 1),
    }
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    common.write_json(out_path, report, skip_unchanged=False)

    # ---- 回写配置 ----
    dropped, moved = [], 0
    if args.apply:
        adv = {r["url"]: r["henan_ok"] for r in rows}
        live_block = load_live_blocklist()
        for cfg in CONFIGS:
            if not os.path.exists(cfg):
                continue
            d = common.load_json(cfg, {}) or {}
            lives = d.get("lives") or []
            if not lives:
                continue
            out, seen_u = [], set()
            for g in lives:
                u = str(g.get("url") or "")
                if u in shells or any(b in u for b in live_block):
                    if u not in dropped:
                        dropped.append(u)
                    continue
                if u in seen_u:
                    continue
                seen_u.add(u)
                out.append(g)
            # 三档：自建专列表恒第一，其次是能播河南台的源，其余保持原序。
            # 专列表必须单独占一档：它也是 lives 里的一条，adv 查不到它，
            # 和「河南可播=0」的源同档就会被 sort 甩到中间去。
            out.sort(key=lambda g: (
                0 if str(g.get("url")) == HENAN_LIST_URL
                else 1 if adv.get(str(g.get("url") or ""), 0) else 2))
            if picked and not any(str(g.get("url")) == HENAN_LIST_URL for g in out):
                entry = {"name": HENAN_ENTRY, "type": "0", "url": HENAN_LIST_URL}
                # 客户端播放时会裸 .trim() 这些 String 字段，缺键就是空指针。
                # 其它 lives 条目都被 polish 补过，新插的这条必须走同一口径。
                common.client_compat(entry, "live")
                out.insert(0, entry)
            moved += sum(1 for a, b in zip(lives, out) if a is not b)
            d["lives"] = out
            # 删了收费源、加了专列表，_meta 里的计数必须同步：断言
            # json_valid_no_dup_key / _meta 计数一致 是硬检查，不同步就整轮阻断。
            if isinstance(d.get("_meta"), dict):
                d["_meta"]["lives"] = len(out)
            common.write_json(cfg, d, skip_unchanged=True)

        # 判定结果落成静态黑名单：删掉的源下一轮还会从上游重新长出来，而探测
        # 本身可能失败（境外节点、上游抖动）。写进文件后，build.py 在聚合阶段
        # 就能拦住，不依赖当次探测有没有跑通。
        if shells:
            write_live_blocklist(shells)

    print("\n%-28s %6s %5s %5s %4s  %s" % ("源", "频道", "广告", "河南", "可播", "结论"))
    for r in sorted(rows, key=lambda x: (-x["henan_ok"], -x["chans"]))[:40]:
        print("%-28s %6d %5d %5d %4d  %s" % (
            (r["name"] or r["url"])[:28], r["chans"], r["ad"], r["henan_total"],
            r["henan_ok"], r["verdict"]))
    print("\n%s" % live_msg)
    if dropped:
        print("剔除收费广告源 %d 个：%s" % (len(dropped), ", ".join(x[:60] for x in dropped[:5])))
    if args.apply:
        print("配置已回写 %d 份（调整顺序 %d 处）" % (len(CONFIGS), moved))
    print("报告：%s" % out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
