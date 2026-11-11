#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""搜索优先排序：把「真能播」的线路排到搜索栏最前面。

背景：TVBox / 影视仓的搜索是**按 sites 数组顺序逐站展示**的 —— 数组顺序就是
用户在搜索栏里看到的先后，客户端不提供手动排序。而此前的顺序有两处与「能不
能用」无关：

  * polish 的分层内按**名称**排序，于是数字 / emoji / 拉丁字母开头的站点
    （234|弹幕、360|弹幕、CHNNETFILX、☺FREEOK、⚽瓜子┃看球 …）整批挤在
    最前面，只因名字排序靠前；
  * 首页优先版按「能否出首页推荐」重排，把大量首页有数据、但搜索内容不相关
    或播放地址已死的采集站顶到最前，实测能播的 APP 类站被挤到六七百位。

两者都会让「前面几条都不能用」，而能用的线路要翻很久才能看到。

本脚本在所有配置生成之后跑最后一遍，按下面的顺序重排 sites（只改先后，不删站）：

  1. 置顶名单 pins.txt   用户指定的线路排最前，文件里的顺序就是优先级
  2. 端到端实测          今日 play_probe 的「搜索有结果 + 播放地址可连」
  3. 类目                磁力 / 体育 / 听书 / 短视频 / 直播 / 工具等非影视站降权
  4. 首页等级            仅首页类配置：ok/likely 同级内优先，保住「首页有内容」
  5. 探活分层            沿用 polish 的 tier（可用 / 未知 / 已判定失败）
  6. 每屏去连号          同一 spider / 同一域名的站点上游里常常连号，按每 36
                         条一屏打散，一屏内尽量是不同来源
  7. 原顺序              其余保持各版本已有顺序

输入：10 份配置、_play_report.json 或 play_summary.json（今日探测）、
      quality.json（探活分层）、home_report.json（首页等级）、pins.txt
输出：原地重排上述配置的 sites；本地试跑只写 _local-<文件名>

用法：python scripts/search_rank.py [--dry-run]
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common
import polish

FILES = [
    "tvbox.json", "tvbox-lite.json", "tvbox-network.json",
    "tvbox-home.json",
    "tvbox-full-homefirst.json", "tvbox-lite-homefirst.json",
    "tvbox-network-homefirst.json",
    "tvbox-quality.json", "tvbox-cn-fast.json", "tvbox-stable7.json",
]
# 这些配置存在的理由就是「首页推荐能出内容」，排序里保留首页等级作为次级因素
HOME_FILES = ("tvbox-home.json", "tvbox-full-homefirst.json",
              "tvbox-lite-homefirst.json", "tvbox-network-homefirst.json")
PIN_FILE = "pins.txt"

# 非影视类目：搜一部动画 / 剧，这些站要么没有、要么返回完全无关的东西，
# 却因为名字靠前常年占据第一屏。只降权，不删除。
NON_VIDEO_RX = re.compile(
    "磁力|磁链|磁搜|种子|迅雷|体育|足球|篮球|看球|球赛|赛事|NBA|英超|中超|"
    "网球|台球|听书|有声|评书|相声|小品|音乐|歌曲|电台|FM|"
    "短视频|抖音|快手|直播|卫视|电视台|"
    "写真|福利|美女|壁纸|头像|"
    "工具|配置中心|小说|漫画|课程|题库|游戏|手游",
    re.I)

# 组号越小越靠前，只用于排序，不改变任何站点是否保留
G_PLAY_BOTH, G_PLAY_ONLY, G_UNKNOWN, G_MEASURED_BAD = 0, 1, 2, 3

# 每屏去连号的窗口大小。手机搜索栏一屏大约 8~10 条，取 24 让相邻两屏都覆盖
# 到不同来源；窗口越小翻动越频繁，越大则越接近原始顺序。
WINDOW = 36


def load_pins():
    path = os.environ.get("PINS_FILE", PIN_FILE)
    pins = []
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            s = line.strip()
            if s and not s.startswith("#"):
                pins.append(s)
    return pins


def pin_rank(site, pins):
    """未置顶一律排到置顶项之后；置顶项按文件里的书写顺序。"""
    name = str(site.get("name") or "").casefold()
    for i, p in enumerate(pins):
        if p.casefold() in name:
            return i
    return len(pins) + 1000


def load_evidence():
    """今日端到端探测结果：sig -> 明细。优先用逐站报告，只有汇总时降级。"""
    rep = common.load_json("_play_report.json", {}) or {}
    ev = {}
    for r in rep.get("sites") or []:
        sg = r.get("sig")
        if sg:
            ev[sg] = r
    if ev:
        return ev, "detail"

    summ = common.load_json("play_summary.json", {}) or {}
    ok = set(summ.get("playable_sigs") or [])
    for sg in summ.get("scores") or {}:
        ev[sg] = {"play": sg in ok, "search": None,
                  "list": True, "detail": sg in ok}
    return ev, ("summary" if ev else "none")


def play_group(r):
    """0 搜得到且放得出 / 1 其一成立 / 2 没证据 / 3 实测不可用。

    没证据不能当失败：2473 个站点里每天只能实测一小部分（csp 爬虫站必须
    跑 jar 才能搜，服务端跑不了），把「没测过」判死会让绝大多数正常站沉底。
    """
    if not r:
        return G_UNKNOWN
    play = bool(r.get("play"))
    search = r.get("search")
    if play and search:
        return G_PLAY_BOTH
    if play or (search and r.get("detail")):
        return G_PLAY_ONLY
    if r.get("list") or search:
        return G_UNKNOWN
    return G_MEASURED_BAD


def cat_group(site):
    return 1 if NON_VIDEO_RX.search(str(site.get("name") or "")) else 0


def family(site):
    """同源家族：同一个 spider（csp_*）或同一个域名算一族。

    一批同名同源的站点（例如几十个 csp_XBPQ）在数组里连号，一旦这套 spider
    或这个上游挂了，第一屏就是整片死站。按家族交错后，第一屏覆盖多个不同
    来源，其中有一个能用就不会白翻。

    代码托管域名（raw.githubusercontent.com 等）上挂着 600 多个彼此无关的
    js 配置，按域名归成一家等于把它们整批沉到队尾 —— 改成按完整 api 区分。
    """
    api = str(site.get("api") or "")
    if api.startswith("csp_"):
        return "csp:" + api
    if api.startswith("http"):
        m = re.match(r"https?://([^/?#]+)([^?#]*)", api, re.I)
        host = (m.group(1) if m else api).lower()
        if any(host == h or host.endswith("." + h) for h in common.GH_HOSTS):
            return "api:" + (host + m.group(2)) if m else "api:" + api
        return "http:" + host
    return "other:" + api


def load_tier():
    """沿用 polish 的分层口径，避免两处判断标准打架。"""
    q = common.load_json("quality.json", {}) or {}
    stab = q.get("site_stability") or {}
    jar_fail = q.get("jar_fail_streak") or {}
    ext_fail = q.get("ext_fail_streak") or {}
    try:
        revived = polish.load_revived()
    except Exception:
        revived = set()

    def tier(site):
        try:
            return polish.tier_of(site, stab, revived, jar_fail, ext_fail)[0]
        except Exception:
            return 1
    return tier


def load_home_grades(base_sites):
    """home_report 的 idx 指向基础配置下标，换回与名称无关的身份签名。"""
    rep = common.load_json("home_report.json", {}) or {}
    grades = {}
    for r in rep.get("sites") or []:
        i = r.get("idx")
        if isinstance(i, int) and 0 <= i < len(base_sites):
            g = r.get("grade")
            if g:
                grades[common.sig(base_sites[i])] = g
    return grades


def order(sites, pins, ev, tier, grades, use_home):
    """返回重排后的列表：先按质量分组，再在每屏内打散同源连号。"""
    keyed = []
    for i, s in enumerate(sites):
        sg = common.sig(s)
        key = (pin_rank(s, pins), play_group(ev.get(sg)), cat_group(s),
               0 if (not use_home or grades.get(sg) in ("ok", "likely")) else 1,
               tier(s), i)
        keyed.append((key, i))
    keyed.sort()
    order_idx = [i for _, i in keyed]

    # 同一 spider / 同一域名的站点在上游里往往连号：按每屏（WINDOW）去连号，
    # 让一屏里尽量是不同的来源。只在窗口内调整，不改变整体质量分组。
    fam_of = [family(sites[i]) for i in range(len(sites))]
    out = []
    for start in range(0, len(order_idx), WINDOW):
        block = order_idx[start:start + WINDOW]
        seen, head, tail = set(), [], []
        for k in block:
            f = fam_of[k]
            if f in seen:
                tail.append(k)
            else:
                seen.add(f)
                head.append(k)
        out.extend(head + tail)
    assert len(out) == len(sites), "排序丢站：%d != %d" % (len(out), len(sites))
    return [sites[i] for i in out]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    local = not (common.IS_CI or os.environ.get("ALLOW_LOCAL_WRITE") == "1")
    if local and not args.dry_run:
        print("本地试跑：输出重定向到 _local-*，不覆盖线上配置", flush=True)

    base = common.load_json("tvbox.json", {}) or {}
    base_sites = [s for s in base.get("sites") or [] if isinstance(s, dict)]
    if not base_sites:
        print("缺少 tvbox.json，跳过搜索排序")
        return 0

    pins = load_pins()
    ev, ev_src = load_evidence()
    tier = load_tier()
    grades = load_home_grades(base_sites)
    print("置顶名单 %d 条 | 可播证据 %s（%d 站）| 首页等级 %d 条" %
          (len(pins), ev_src, len(ev), len(grades)), flush=True)

    total_moved = 0
    for name in FILES:
        if not os.path.exists(name):
            continue
        cfg = common.load_json(name)
        if not isinstance(cfg, dict) or not isinstance(cfg.get("sites"), list):
            print("  %-30s 读不到，跳过" % name)
            continue
        sites = [s for s in cfg["sites"] if isinstance(s, dict)]
        if len(sites) != len(cfg["sites"]):
            print("  %-30s 含非对象站点，跳过以免破坏结构" % name)
            continue

        ordered = order(sites, pins, ev, tier, grades,
                        use_home=name in HOME_FILES)
        moved = sum(1 for a, b in zip(sites, ordered) if a is not b)
        total_moved += moved

        meta = dict(cfg.get("_meta") or {})
        meta["sites"] = len(ordered)
        meta["order"] = "search-first"
        meta["order_note"] = (
            "站点顺序即客户端搜索结果的展示顺序：置顶名单 > 端到端实测可播 > "
            "影视类目 > 探活分层 > 首页等级 > 同源家族交错；不删站，只改先后")
        meta["pinned"] = [p for p in pins
                          if any(p.casefold() in str(s.get("name") or "").casefold()
                                 for s in ordered)][:20]
        meta["play_verified"] = sum(
            1 for s in ordered if play_group(ev.get(common.sig(s))) <= G_PLAY_ONLY)
        if name in HOME_FILES and "home_first_note" in meta:
            meta["home_first_note"] += "（随后按搜索可播优先重排，首页等级改为同级内部次序）"

        cfg["_meta"] = meta
        cfg["sites"] = ordered

        dst = ("_local-" + name) if (local and not args.dry_run) else name
        if args.dry_run:
            print("  %-30s 站点 %5d | 会移动 %5d | 前 5: %s" %
                  (name, len(ordered), moved,
                   "、".join(str(s.get("name", ""))[:14] for s in ordered[:5])))
            continue
        common.write_json(dst, cfg, skip_unchanged=True)
        print("  %-30s -> %-34s 站点 %5d | 移动 %5d | 前 5: %s" %
              (name, dst, len(ordered), moved,
               "、".join(str(s.get("name", ""))[:14] for s in ordered[:5])))

    print("搜索优先排序完成：10 份配置共移动 %d 条（只改先后，未删除任何站点）" %
          total_moved)
    return 0


if __name__ == "__main__":
    sys.exit(main())
