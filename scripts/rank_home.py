#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成「首页优先」版配置：保留全部节点，把最可能出首页推荐的节点排到最前面。

TVBox 首页推荐来自「当前选中的那个站点」，默认往往落在列表第一个站点，
因此把可用节点前置能直接改善首次打开就有推荐内容的体验；同时不删任何节点，
搜索与切换源的能力完全保留。

v2 排序依据（可靠度从高到低）：
  1. 今日首页判定等级        ok > likely(含同 jar 推断) > classes_only
  2. 近 7 天可用天数          quality.json / site_stability，抑制单日抖动导致的排序跳动
  3. 端到端可播得分          play_summary.json，实测「列表→详情→播放地址」的站点加权
  4. 探测顺序 / 原始顺序     其余节点严格保持原顺序

输入：tvbox.json / tvbox-lite.json / tvbox-network.json（build.py）
      tvbox-home.json + home_report.json（home_probe.py）
      quality.json（history.py，昨日）、play_summary.json（play_probe.py，今日）
输出：tvbox-full-homefirst.json / tvbox-lite-homefirst.json / tvbox-network-homefirst.json

仅依赖 Python 标准库。用法：python scripts/rank_home.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common

PAIRS = [
    ("tvbox.json", "tvbox-full-homefirst.json", "全量版"),
    ("tvbox-lite.json", "tvbox-lite-homefirst.json", "精简版"),
    ("tvbox-network.json", "tvbox-network-homefirst.json", "网络优化版"),
]
BAND = {"ok": 0, "likely": 1, "classes_only": 2}


def load(path, default=None):
    return common.load_json(path, default)


def build_priority():
    """返回 sig -> (band, home_days_7, play_score, order)。"""
    home = load("tvbox-home.json", {}) or {}
    full = load("tvbox.json", {}) or {}
    rep = load("home_report.json", {}) or {}
    quality = load("quality.json", {}) or {}
    play = load("play_summary.json", {}) or {}

    stab = quality.get("site_stability", {})
    scores = play.get("scores", {})
    full_sites = full.get("sites", [])

    # home_report 的 idx 是「全量配置」的下标，用它换算出 sig -> 等级
    grade_by_sig = {}
    for r in rep.get("sites", []):
        i = r.get("idx")
        if isinstance(i, int) and 0 <= i < len(full_sites) and r.get("grade") in BAND:
            grade_by_sig[common.sig(full_sites[i])] = r["grade"]

    # 若没有 home_report（例如本地只拿到 home 配置），退化为按 home 文件顺序
    fallback_order = {}
    for i, s in enumerate(home.get("sites", [])):
        fallback_order.setdefault(common.sig(s), i)

    priority = {}
    for i, s in enumerate(home.get("sites", [])):
        sg = common.sig(s)
        if sg in priority:
            continue
        grade = grade_by_sig.get(sg)
        band = BAND.get(grade, 2 if sg in fallback_order else 9)
        priority[sg] = (
            band,
            int(stab.get(sg, {}).get("home_days_7", 0)),
            int(scores.get(sg, 0)),
            fallback_order.get(sg, i),
        )
    return priority, {
        "home_sites": len(priority),
        "stability_days": quality.get("days_recorded", 0),
        "play_rate": play.get("play_rate"),
        "play_sampled": play.get("sampled", 0),
        "graded": len(grade_by_sig),
    }


def main():
    local = not (common.IS_CI or os.environ.get("ALLOW_LOCAL_WRITE") == "1")
    if local:
        print("本地试跑：输出重定向到 _local-*-homefirst.json", flush=True)

    priority, info = build_priority()
    if not priority:
        print("首页版无节点（探测未产出），跳过排序，不影响其它配置")
        return 0
    print("首页可用节点 %d | 历史 %d 天 | 今日可播率 %s（抽样 %d）" %
          (info["home_sites"], info["stability_days"],
           info["play_rate"], info["play_sampled"]), flush=True)

    for src, dst, label in PAIRS:
        if not os.path.exists(src):
            print("缺少 %s，跳过 %s" % (src, label))
            continue
        if local:
            dst = "_local-" + dst
        cfg = load(src)
        sites = cfg["sites"]
        total = len(sites)

        decorated = []
        for i, s in enumerate(sites):
            p = priority.get(common.sig(s))
            if p is None:
                key = (1, 0, 0, 0, i)                    # 其余节点保持原顺序
            else:
                band, days, score, order = p
                key = (0, band, -days, -score, order)    # 等级 → 稳定天数 → 可播得分
            decorated.append((key, i, s))
        decorated.sort(key=lambda t: (t[0], t[1]))
        ordered = [s for _, _, s in decorated]
        moved = sum(1 for s in ordered if common.sig(s) in priority)

        out = {k: v for k, v in cfg.items() if k not in ("sites", "_meta")}
        meta = dict(cfg.get("_meta", {}))
        meta["sites"] = total
        meta["home_first_count"] = moved
        meta["home_first_note"] = (
            "前 %d 个站点按「今日首页等级 > 近 7 天可用天数 > 端到端可播得分」排序，"
            "其余节点保持原顺序，未删减" % moved)
        meta["ranking"] = "grade > home_days_7 > play_score > probe_order"
        meta["play_rate"] = info["play_rate"]
        meta["play_sampled"] = info["play_sampled"]
        meta["stability_days"] = info["stability_days"]
        meta["note"] = "%s，首页可用节点已置顶" % label
        out["_meta"] = meta
        out["sites"] = ordered

        common.write_json(dst, out, skip_unchanged=True)
        assert len(ordered) == total, "站点数发生变化，排序逻辑有误"
        print("  %-10s -> %-32s 站点 %d（置顶 %d）| %.1f KB" %
              (label, dst, total, moved, os.path.getsize(dst) / 1024))
        print("     前 5 个: %s" %
              ", ".join(str(s.get("name", "")).strip()[:12] for s in ordered[:5]))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print("排序失败: %s" % e, file=sys.stderr)
        sys.exit(1)
