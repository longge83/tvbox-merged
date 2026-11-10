#!/usr/bin/env python3
"""
生成「首页优先」版配置：保留全部节点，但把能显示主界面推荐的节点排到最前面。

TVBox 首页推荐来自「当前选中的那个站点」，不是所有站点合并。默认往往落在
列表第一个站点，因此把已探活的首页节点前置，能直接改善首次打开就有推荐内容
的体验；同时不丢掉其余节点，搜索/切换源的能力完全保留。

输入（由 build.py / home_probe.py 生成）：
  tvbox.json        全量配置
  tvbox-lite.json   精简配置
  tvbox-home.json   只含首页可用节点，且已按可靠度排序
                    （ok 首页影片实测非空 -> likely 爬虫依赖有效 -> classes_only）

输出：
  tvbox-full-homefirst.json   全量 + 首页节点置顶
  tvbox-lite-homefirst.json   精简 + 首页节点置顶

仅依赖 Python 标准库。用法：python scripts/rank_home.py
"""
import json
import os
import sys

PAIRS = [
    ("tvbox.json", "tvbox-full-homefirst.json", "全量版"),
    ("tvbox-lite.json", "tvbox-lite-homefirst.json", "精简版"),
]


def identity(site):
    """站点身份签名。不用整个对象，避免上游字段微调就匹配不上；
    也不用单独 key，因为跨源合并后 key 有重名。"""
    return json.dumps(
        [site.get("key"), site.get("name"), site.get("api"),
         site.get("jar"), site.get("ext")],
        ensure_ascii=False, sort_keys=True,
    )


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main():
    home_path = "tvbox-home.json"
    if not os.path.exists(home_path):
        print("缺少 %s（首页探测未产出），跳过排序，不影响其它配置" % home_path)
        return 0

    home = load(home_path)
    # tvbox-home.json 的站点顺序本身就是可靠度顺序，用下标作为优先级
    priority = {}
    for i, s in enumerate(home.get("sites", [])):
        sig = identity(s)
        if sig not in priority:
            priority[sig] = i
    print("首页可用节点: %d（来自 %s）" % (len(priority), home_path))

    for src, dst, label in PAIRS:
        if not os.path.exists(src):
            print("缺少 %s，跳过 %s" % (src, label))
            continue
        cfg = load(src)
        sites = cfg["sites"]
        total = len(sites)

        # 稳定排序：首页节点按可靠度优先，其余节点保持原有顺序
        decorated = []
        for i, s in enumerate(sites):
            p = priority.get(identity(s))
            key = p if p is not None else 10 ** 6 + i
            decorated.append((key, i, s))
        decorated.sort(key=lambda t: (t[0], t[1]))
        ordered = [s for _, _, s in decorated]
        moved = sum(1 for s in ordered if identity(s) in priority)

        out = {k: v for k, v in cfg.items() if k not in ("sites", "_meta")}
        meta = dict(cfg.get("_meta", {}))
        meta["sites"] = total
        meta["home_first_count"] = moved
        meta["home_first_note"] = (
            "前 %d 个站点经首页推荐探测确认可用（ok/likely/classes_only 依次排序），"
            "其余节点保持原顺序，未删减" % moved
        )
        meta["note"] = "%s，首页可用节点已置顶" % label
        out["_meta"] = meta
        out["sites"] = ordered

        with open(dst, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        assert len(ordered) == total, "站点数发生变化，排序逻辑有误"
        print("  %-8s -> %-28s 站点 %d（置顶 %d）| %.1f KB" %
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
