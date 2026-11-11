#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""判定历史与质量指标：给「每天的判定」加上时间维度。

解决的问题：
  - 单日超时即剔除 / 次日恢复即纳回 → 上游一抖配置就变；
  - fail 里多少是误杀没有数；
  - 上游连续失败无人察觉；
  - 断言通过几天才敢把版本标为 stable，没有依据。

每天写一份快照 history/YYYY-MM-DD.json（保留 14 天），再汇总成 quality.json：
  site_stability     每个站点近 7 天可用天数 / ok 天数 / 连续失败天数
  probe_fail_streak  采集接口探活连续失败次数（build.py 据此决定要不要淘汰）
  source_fail_streak 上游连续失败天数
  assert_pass_streak 回归断言连续通过天数（stable.py 据此推进稳定版指针）
  trend              近 14 天站点数 / 首页可用 / 可播率 / 误杀率趋势

用法：python scripts/history.py [--days 14]
"""
import argparse
import datetime
import glob
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common

KEEP_DAYS = 14
CONFIG_FILES = [
    "tvbox.json", "tvbox-lite.json", "tvbox-network.json", "tvbox-home.json",
    "tvbox-full-homefirst.json", "tvbox-lite-homefirst.json",
    "tvbox-network-homefirst.json",
    "tvbox-quality.json", "tvbox-cn-fast.json", "tvbox-stable7.json",
]
GOOD_GRADES = ("ok", "likely", "classes_only")


def snapshot():
    st = common.load_json("_build_stats.json", {}) or {}
    rep = common.load_json("home_report.json", {}) or {}
    cfg = common.load_json("tvbox.json", {}) or {}
    sites = cfg.get("sites", [])
    recheck = common.load_json("recheck.json", {}) or {}
    play = common.load_json("play_summary.json", {}) or {}
    asserts = common.load_result("assertions", {}) or {}
    jh = common.load_json("_jar_health.json", {}) or {}
    bs = common.load_build_status()

    grades = {}
    for r in rep.get("sites", []):
        i = r.get("idx")
        if isinstance(i, int) and 0 <= i < len(sites):
            grades[common.sig(sites[i])] = r.get("grade", "unknown")

    files = {f: common.sha256_file(f) for f in CONFIG_FILES if os.path.exists(f)}
    return {
        "date": common.today(),
        "env": common.env_info(),
        "build": {
            "sites": st.get("sites", cfg.get("_meta", {}).get("sites", len(sites))),
            "lite": st.get("lite", 0),
            "network": st.get("network", 0),
            "sources_ok": st.get("sources_ok", 0),
            "sources_failed": st.get("sources_failed", []),
            "dead_sigs": st.get("dead_sigs", []),
            "dropped": st.get("dropped", 0),
            "observation": st.get("observation", 0),
            "parses": st.get("parses", 0),
            "parses_deduped": st.get("parses_deduped", 0),
            "lives": st.get("lives", 0),
            "blocked": st.get("blocked", {}),
            "sources": st.get("sources", []),
            # jar / ext 可达性盲区：今日探测到的死 jar / 死 ext（由 jar_health.py 产出）
            "jar_dead_today": (jh.get("jars") or {}).get("dead", []),
            "ext_dead_today": (jh.get("exts") or {}).get("dead", []),
        },
        "home": {
            "grades": rep.get("grades", {}),
            "inferred_count": rep.get("inferred_count", 0),
            "scanned": rep.get("scanned", 0),
            "sites": len((common.load_json("tvbox-home.json", {}) or {}).get("sites", [])),
        },
        "grades": grades,
        "play": {k: play.get(k) for k in
                 ("sampled", "list_rate", "detail_rate", "play_rate",
                  "search_rate", "playable", "parses_alive_rate")},
        "recheck": {k: recheck.get(k) for k in
                    ("checked", "revived", "false_kill_rate", "revived_sigs")},
        "assertions": {"passed": bool(asserts.get("passed")),
                       "failed": asserts.get("failed", []),
                       "checks": asserts.get("checks", {})},
        # 本轮探测有没有步骤失败。降级日的断言即便通过，也不该算作「连续
        # 健康」去推进稳定版 tag —— 见 summarize() 的 assert_streak。
        "build_status": {
            "degraded": bool(bs.get("degraded")),
            "failed_steps": bs.get("failed_steps", []),
            "config_steps_failed": bs.get("config_steps_failed", []),
        },
        "sample_rates": asserts.get("sample_rates", {}),
        "files": files,
    }


def summarize(snaps):
    """从按日期升序排列的快照里算出所有稳定性指标。"""
    recent7 = snaps[-7:]
    all_sigs = set()
    for s in recent7:
        all_sigs.update(s.get("grades", {}))

    site_stability = {}
    for sg in all_sigs:
        seq = [s.get("grades", {}).get(sg) for s in recent7]
        good_days = sum(1 for g in seq if g in GOOD_GRADES)
        ok_days = sum(1 for g in seq if g == "ok")
        streak = 0
        for g in reversed(seq):
            if g == "fail":
                streak += 1
            else:
                break
        site_stability[sg] = {
            "home_days_7": good_days,
            "ok_days_7": ok_days,
            "fail_streak": streak,
            "last_grade": next((g for g in reversed(seq) if g is not None), "unknown"),
        }

    # 采集接口探活失败：连续 N 天出现在 dead_sigs 里才淘汰；复核复活则清零
    probe_fail = {}
    for sg in {x for s in recent7 for x in s.get("build", {}).get("dead_sigs", [])}:
        streak = 0
        for s in reversed(recent7):
            if sg in s.get("build", {}).get("dead_sigs", []):
                streak += 1
            else:
                break
        probe_fail[sg] = streak
    revived = set((snaps[-1].get("recheck") or {}).get("revived_sigs") or [])
    for sg in revived:
        probe_fail.pop(sg, None)

    # jar / ext 连续失败：死 jar / 死远程 ext 都会让依赖它的站点整页空白，但单日
    # 探测有噪声，同样要走「连续 N 天失败才沉底」；复活机制暂不适用（无 UA 复核）。
    jar_fail = {}
    for u in {x for s in recent7 for x in s.get("build", {}).get("jar_dead_today", [])}:
        streak = 0
        for s in reversed(recent7):
            if u in s.get("build", {}).get("jar_dead_today", []):
                streak += 1
            else:
                break
        jar_fail[u] = streak

    ext_fail = {}
    for u in {x for s in recent7 for x in s.get("build", {}).get("ext_dead_today", [])}:
        streak = 0
        for s in reversed(recent7):
            if u in s.get("build", {}).get("ext_dead_today", []):
                streak += 1
            else:
                break
        ext_fail[u] = streak

    source_fail = {}
    for tag in {t for s in recent7 for t in s.get("build", {}).get("sources_failed", [])}:
        streak = 0
        for s in reversed(recent7):
            if tag in s.get("build", {}).get("sources_failed", []):
                streak += 1
            else:
                break
        source_fail[tag] = streak

    assert_streak = 0
    prev_date = None
    for s in reversed(snaps):
        # 断言失败/构建崩溃的当天没有快照，缺失日不参与循环也不 break，
        # 两段「通过」会被直接拼成连续 → stable tag 的 3 天连胜门槛可被绕过。
        # 必须校验相邻快照日期恰好差 1 天。
        if prev_date is not None:
            try:
                gap = (datetime.date.fromisoformat(s["date"]) -
                       prev_date).days
            except Exception:
                gap = 0
            if gap != -1:
                break
        # 探测有失败步骤的当天是「降级版」：配置仍可发布，但数据不完整，
        # 不能算作健康日去凑稳定版 tag 的 3 天连胜。旧快照没有这个字段，
        # 取 {} 即视为非降级，历史数据保持原样。
        if (s.get("build_status") or {}).get("degraded"):
            break
        if (s.get("assertions") or {}).get("passed"):
            assert_streak += 1
        else:
            break
        # 首条快照不进上面的 gap 分支，date 损坏会在这里直接抛异常，
        # 让整个 history.py 崩掉（进而阻断当天构建）。与 gap 分支同样保护。
        try:
            prev_date = datetime.date.fromisoformat(s["date"])
        except (KeyError, TypeError, ValueError):
            break

    trend = []
    for s in snaps:
        b, h = s.get("build", {}), s.get("home", {})
        trend.append({
            "date": s.get("date"),
            "sites": b.get("sites", 0),
            "lite": b.get("lite", 0),
            "network": b.get("network", 0),
            "home_sites": h.get("sites", 0),
            "home_ok": (h.get("grades") or {}).get("ok", 0),
            "play_rate": (s.get("play") or {}).get("play_rate"),
            "false_kill_rate": (s.get("recheck") or {}).get("false_kill_rate"),
            "sources_ok": b.get("sources_ok", 0),
            "assert_passed": bool((s.get("assertions") or {}).get("passed")),
            "degraded": bool((s.get("build_status") or {}).get("degraded")),
        })

    last = snaps[-1]
    return {
        "updated": last.get("date"),
        "env": common.env_info(),
        "days_recorded": len(snaps),
        "window_days": 7,
        "site_stability": site_stability,
        "probe_fail_streak": probe_fail,
        "jar_fail_streak": jar_fail,
        "ext_fail_streak": ext_fail,
        "source_fail_streak": source_fail,
        "assert_pass_streak": assert_streak,
        "false_kill_rate": (last.get("recheck") or {}).get("false_kill_rate"),
        "play_rate": (last.get("play") or {}).get("play_rate"),
        # 昨天的 jar/ext 抽样基线，供明天的断言做「骤降」检测
        "sample_rates": last.get("sample_rates", {}),
        "trend": trend,
        "latest": {
            "sites": last.get("build", {}).get("sites", 0),
            "home_sites": last.get("home", {}).get("sites", 0),
            "home_grades": last.get("home", {}).get("grades", {}),
            "sources_failed": last.get("build", {}).get("sources_failed", []),
            "assert_passed": bool((last.get("assertions") or {}).get("passed")),
            "files": last.get("files", {}),
        },
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=KEEP_DAYS)
    ap.add_argument("--dir", default="history")
    ap.add_argument("--no-write-snapshot", action="store_true",
                    help="只重算 quality.json，不写当天快照（本地调试用）")
    args = ap.parse_args()

    os.makedirs(args.dir, exist_ok=True)
    snap = snapshot()
    if not args.no_write_snapshot:
        common.write_json(os.path.join(args.dir, snap["date"] + ".json"),
                          snap, skip_unchanged=False)
        print("已写入快照 history/%s.json（站点判定 %d 条）" %
              (snap["date"], len(snap["grades"])))

    paths = sorted(glob.glob(os.path.join(args.dir, "20??-??-??.json")))
    snaps = []
    for p in paths[-(args.days):]:
        s = common.load_json(p)
        if s:
            snaps.append(s)
    # 清理过期快照，控制仓库体积
    for p in paths[:-args.days]:
        try:
            os.remove(p)
            print("清理过期快照", p)
        except OSError:
            pass
    if not snaps:
        print("没有历史快照", file=sys.stderr)
        return 1

    quality = summarize(snaps)
    common.write_json("quality.json", quality, skip_unchanged=False)
    print("已生成 quality.json：记录 %d 天 | 站点稳定性 %d 条 | "
          "断言连续通过 %d 天 | 上游连续失败 %s" %
          (len(snaps), len(quality["site_stability"]),
           quality["assert_pass_streak"], quality["source_fail_streak"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
