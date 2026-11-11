#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""汇总本轮构建的探测步骤结果，产出可核对的「今日是不是完整版」结论。

背景：update.yml 里 8 个探测/生成步骤设了 continue-on-error，脚本失败时
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
    "jar_health": "STEP_JAR_HEALTH",
    "regression": "STEP_REGRESSION",
}

# 这三步产出 7 份入库派生配置。它们失败 → 产物缺失 → 前置的回归断言 /
# 必需产物检查会硬阻断（线上保持上一版），理论上传不到本脚本；单列出来
# 便于事后核对「当天到底为什么没发版」。
CONFIG_STEPS = ("home_probe", "rank_home", "variants")

# 决定今日探测数据是否完整。任一失败 → 配置仍发布，但标记为降级版。
DATA_STEPS = ("home_probe", "recheck", "play_probe",
              "rank_home", "variants", "jar_health")

VALID = ("success", "failure", "cancelled", "skipped")


def collect():
    steps = {}
    for name, var in STEP_VARS.items():
        raw = os.environ.get(var, "")
        steps[name] = raw if raw in VALID else "unknown"
    failed = [n for n, v in steps.items() if v == "failure"]
    return {
        "date": common.today(),
        "env": common.env_info(),
        "source": "github-actions" if common.IS_CI else "local",
        "run_id": os.environ.get("GITHUB_RUN_ID", ""),
        "commit": os.environ.get("COMMIT_SHA", ""),
        "steps": steps,
        "failed_steps": failed,
        "config_steps_failed": [n for n in CONFIG_STEPS if steps.get(n) == "failure"],
        "data_steps_failed": [n for n in DATA_STEPS if steps.get(n) == "failure"],
        # 报告类步骤（audit / regression）失败不改变配置内容，但同样意味着
        # 「今天这一轮不完整」，所以任何失败都标降级。
        "degraded": bool(failed),
        "policy": ("探测失败不沿用旧产物：派生配置在构建前清除，生成失败即硬阻断；"
                   "数据类失败照常发布但标记降级，且不推进稳定版 tag"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default=None,
                    help="默认 CI 写 reports/build-status.json（入库），本地写隔离临时文件")
    args = ap.parse_args()
    dest = args.output or (common.BUILD_STATUS_PATH if common.IS_CI
                           else "_build_status_local.json")
    out = collect()
    common.write_json(dest, out, skip_unchanged=False)
    print("本轮构建状态 -> %s" % dest)
    print("  步骤: %s" % ", ".join("%s=%s" % (k, v) for k, v in out["steps"].items()))
    print("  结论: %s%s" % ("降级版" if out["degraded"] else "完整版",
                          "（失败: %s）" % ", ".join(out["failed_steps"])
                          if out["failed_steps"] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
