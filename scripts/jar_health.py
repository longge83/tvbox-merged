#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""jar / ext 可达性探测：把「每日只探 api 接口」的盲区补上。

旧流程（build.py 的 probe）只检查采集站 api 接口（?ac=list），从不检查 spider
jar 和远程 ext 是否还活着。实测 jar 抽样可用率常年 ~25%，意味着大量 jar 已经失效，
却一直留在配置里 —— 用户点开就是空。

本步**只记录数据、不改动任何配置文件**，且非阻断（失败不影响构建）。产出
_jar_health.json（含今日死 jar / 死 ext 列表），由 history.py 并入 quality.json
的多日「连续失败」计数；polish 据此把「连败 ≥2 天的死 jar 站点」沉到列表末尾。

仅依赖标准库。用法：python scripts/jar_health.py
"""
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common

HTTP_RX = re.compile(r"^https?://", re.I)


def collect():
    """从全量配置里收出所有唯一 jar / ext URL（去掉 ;md5;hash 后缀）。"""
    cfg = common.load_json("tvbox.json", {}) or {}
    jars, exts = set(), set()
    for s in cfg.get("sites", []):
        j = str(s.get("jar") or "")
        if HTTP_RX.match(j):
            jars.add(j.split(";", 1)[0])
        e = str(s.get("ext") or "")
        if HTTP_RX.match(e):
            exts.add(e.split(";", 1)[0])
    return jars, exts


def reachable(url):
    st, raw = common.fetch(url, timeout=8, limit_bytes=64)
    return st == 200 and len(raw) > 0


def probe_all(pool):
    dead = []
    if not pool:
        return dead
    with ThreadPoolExecutor(max_workers=24) as ex:
        futs = {ex.submit(reachable, u): u for u in sorted(pool)}
        for f in as_completed(futs):
            u = futs[f]
            try:
                if not f.result():
                    dead.append(u)
            except Exception:
                dead.append(u)
    return dead


def main():
    jars, exts = collect()
    out = {"date": common.today(), "env": common.env_info(),
           "probed": common.IS_CI, "jars": {}, "exts": {}}

    if not common.IS_CI:
        # 本地试跑不做网络探测：避免本机网络位置误判污染历史。只保留统计。
        out["jars"] = {"unique": len(jars), "dead_count": None, "dead": []}
        out["exts"] = {"unique": len(exts), "dead_count": None, "dead": []}
        common.write_json("_jar_health.json", out, skip_unchanged=False)
        print("本地试跑：跳过 jar/ext 网络探测（唯一 jar %d · 唯一 ext %d）"
              % (len(jars), len(exts)))
        return 0

    jdead = probe_all(jars)
    edead = probe_all(exts)
    out["jars"] = {"unique": len(jars), "dead_count": len(jdead), "dead": jdead}
    out["exts"] = {"unique": len(exts), "dead_count": len(edead), "dead": edead}
    common.write_json("_jar_health.json", out, skip_unchanged=False)
    print("jar/ext 探测：jar 唯一 %d / 死 %d；ext 唯一 %d / 死 %d"
          % (len(jars), len(jdead), len(exts), len(edead)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
