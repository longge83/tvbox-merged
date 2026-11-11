#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""汇总本轮构建的探测步骤结果，产出可核对的「今日是不是完整版」结论。

背景：update.yml 里 9 个探测/生成步骤设了 continue-on-error，脚本失败时
不阻断，workflow 照样显示全绿。以前只能翻 Actions 日志才知道哪步挂了，
健康页却把缺数据的产物当作「今日新配置」展示。这个脚本把每个步骤的
outcome 落成 reports/build-status.json，由 Actions 提交入库，做到：

  1. 外部可核对：任何人打开 build-status.json 就知道今天哪步失败；
  2. 健康页顶部醒目提示降级，不再静默；
  3. history.py 不把降级日算进「连续健康」，stable.py 不推进稳定版 tag；
  4. 与「清除上一版派生配置」配合：会产出入库配置的步骤一旦失败，
     文件缺失会先被断言 / 必需产物检查硬阻断，绝不会发布混搭配置。

仅依赖 Python 标准库。用法：python scripts/build_status.py [--output PATH]
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common

# 步骤 id -> workflow 传入的环境变量名
STEP_VARS = {
    "audit": "STEP_AUDIT",
    "home_probe": "STEP_HOME_PROBE",
    "recheck": "STEP_RECHECK",
    "play_probe": "STEP_PLAY_PROBE",
    "rank_home": "STEP_RANK_HOME",
    "variants": "STEP_VARIANTS",
    "search_rank": "STEP_SEARCH_RANK",
    "live_audit": "STEP_LIVE_AUDIT",
    "jar_health": "STEP_JAR_HEALTH",
    "regression": "STEP_REGRESSION",
}

# 这三步产出 7 份入库派生配置。它们失败 → 产物缺失 → 前置的回归断言 /
# 必需产物检查会硬阻断（线上保持上一版），理论上传不到本脚本；单列出来
# 便于事后核对「当天到底为什么没发版」。
CONFIG_STEPS = ("home_probe", "rank_home", "variants", "search_rank")

# 决定今日探测数据是否完整。任一失败 → 配置仍发布，但标记为降级版。
DATA_STEPS = ("home_probe", "recheck", "play_probe",
              "rank_home", "variants", "search_rank", "live_audit", "jar_health")

VALID = ("success", "failure", "cancelled", "skipped")


def collect():
    steps = {}
    for name, var in STEP_VARS.items():
        raw = os.environ.get(var, "")
        steps[name] = raw if raw in VALID else "unknown"
    failed = [n for n, v in steps.items() if v == "failure"]
    # 以前只看 failure：env 漏传（unknown）、步骤被取消（cancelled）、被跳过
    # （skipped）都会静默写成 degraded=false，history 照算连胜、stable 照推进
    # tag、健康页也不告警 —— 正是这个脚本要消灭的「静默失败」。凡是没拿到
    # success 的步骤一律视为本轮不完整。
    incomplete = [n for n, v in steps.items() if v != "success"]
    return {
        "date": common.today(),
        "env": common.env_info(),
        "source": "github-actions" if common.IS_CI else "local",
        "run_id": os.environ.get("GITHUB_RUN_ID", ""),
        "commit": os.environ.get("COMMIT_SHA", ""),
        "steps": steps,
        "failed_steps": failed,
        "incomplete_steps": incomplete,
        "status_incomplete": bool(incomplete),
        "config_steps_failed": [n for n in CONFIG_STEPS if steps.get(n) != "success"],
        "data_steps_failed": [n for n in DATA_STEPS if steps.get(n) != "success"],
        # 报告类步骤（audit / regression）失败不改变配置内容，但同样意味着
        # 「今天这一轮不完整」，所以任何非 success 都标降级。
        "degraded": bool(incomplete),
        "policy": ("探测失败不沿用旧产物：派生配置与每日结论在构建前清除，"
                   "生成失败即硬阻断；数据类失败照常发布但标记降级，"
                   "且不推进稳定版 tag"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default=None,
                    help="默认 CI 写 reports/build-status.json（入库），本地写隔离临时文件")
    args = ap.parse_args()
    dest = args.output or common.result_path("build_status")
    out = collect()
    common.write_json(dest, out, skip_unchanged=False)
    print("本轮构建状态 -> %s" % dest)
    print("  步骤: %s" % ", ".join("%s=%s" % (k, v) for k, v in out["steps"].items()))
    print("  结论: %s%s" % ("降级版" if out["degraded"] else "完整版",
                          "（不完整: %s）" % ", ".join(out["incomplete_steps"])
                          if out["incomplete_steps"] else ""))
    # 全部 STEP_* 缺失只可能是 workflow 的 env 段写错 / 被删。这时写出的
    # 状态毫无意义，必须硬阻断，不能让一份「看似完整」的报告进 Pages。
    if (common.IS_CI and len(out["incomplete_steps"]) == len(STEP_VARS)
            and all(v == "unknown" for v in out["steps"].values())):
        print("::error::%d 个 STEP_* 环境变量全部缺失，无法判定本轮是否降级；"
              "请检查 update.yml 的 build_status 步骤 env"
              % len(STEP_VARS), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
