#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""三个「场景版」配置：把探测数据变成可以直接选来用的成品。

前面 7 份配置回答的是「有哪些源」，这三份回答的是「我该用哪一份」：

  tvbox-quality.json   精播版   只留「端到端实测能播」或「首页影片列表实测非空」的节点，
                                并去掉明确探活失败的解析通道。宁可少，不要点开是空的。
  tvbox-cn-fast.json   极速版   在国内直连优化版上再收一层：无 spider jar、无境外依赖、
                                不依赖爬虫源，按质量取前 N 个，老盒子开屏最快。
  tvbox-stable7.json   稳定版   按判定历史取「近 7 天多次可用且当前无连败」的节点，
                                上游抖一天不会被换掉；历史不足 3 天时自动降级为今日实测，
                                并在 _meta.history_fallback 里如实标注，随天数累积自动收紧。

输入（全部由同一天的 workflow 产出，缺失时逐级降级，绝不产出空配置）：
  tvbox.json / tvbox-full-homefirst.json / tvbox-network-homefirst.json / tvbox-home.json
  home_report.json（首页判定）  play_summary.json + _play_report.json（可播率 / 解析存活）
  quality.json（多日稳定性）    recheck.json（误杀复核复活名单）

输出三份配置，并写 _variants_stats.json 供本地排查。
仅依赖 Python 标准库。用法：python scripts/variants.py
"""
import argparse
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common

# 「配置托管地」名单只此一份（common.HOSTING_*），只看 api/jar/ext 的域名

GOOD_GRADES = ("ok", "likely")
BAND = {"ok": 0, "likely": 1, "classes_only": 2, "unknown": 3, "fail": 4}


def load(path, default=None):
    return common.load_json(path, default) or default


def offshore(site):
    """配置是否住在境外代码托管 / GitHub 反代上（与片源内容无关）。"""
    return bool(common.hosting_host(site, deep=True))


class Context:
    """把分散在 5 份报告里的「同一站点」证据按 sig 对齐到一处。"""

    def __init__(self):
        self.full = load("tvbox.json", {})
        self.full_sites = self.full.get("sites", [])
        self.rep = load("home_report.json", {})
        self.play = load("play_summary.json", {})
        self.play_report = load("_play_report.json", {})
        self.quality = load("quality.json", {})
        self.recheck = load("recheck.json", {})
        self.home_cfg = load("tvbox-home.json", {})

        self.playable = set(self.play.get("playable_sigs") or [])
        self.scores = self.play.get("scores") or {}
        self.stab = self.quality.get("site_stability") or {}
        self.probe_fail = self.quality.get("probe_fail_streak") or {}
        self.revived = set(self.recheck.get("revived_sigs") or [])
        self.days = int(self.quality.get("days_recorded") or 0)
        self.play_rate = self.play.get("play_rate")
        self.play_sampled = self.play.get("sampled", 0)

        # 解析通道：明确 200 的优先保留，明确连不上的剔除，模板类（alive=None）保留
        self.alive_parses, self.dead_parses = set(), set()
        for p in self.play_report.get("parses", []):
            u = str(p.get("url") or "")
            if not u:
                continue
            if p.get("alive") is True:
                self.alive_parses.add(u)
            elif p.get("alive") is False:
                self.dead_parses.add(u)

        # home_report 的 idx 对应 tvbox.json（原序），换算成 sig 才能匹配重排后的配置
        self.grade_by_sig, self.inferred = {}, set()
        # sig 含 name，而 polish 对每份基础配置独立做同名消歧：同一站点在
        # 精简 / 网络版可能被改名，导致探测证据静默丢失、场景版里退化成
        # unknown 被排除。补一份与名称无关的 content_key 索引兜底（与
        # rank_home.build_priority 同一处理），不动历史签名。
        self.grade_by_content, self.inferred_content = {}, set()
        for r in self.rep.get("sites", []):
            i = r.get("idx")
            if isinstance(i, int) and 0 <= i < len(self.full_sites) and r.get("grade"):
                s = self.full_sites[i]
                sg, ck = common.sig(s), common.content_key(s)
                self.grade_by_sig[sg] = r["grade"]
                self.grade_by_content.setdefault(ck, r["grade"])
                if r.get("inferred"):
                    self.inferred.add(sg)
                    self.inferred_content.add(ck)
        # 探测报告缺失时，退化为「首页版里的节点视为 likely」
        self.home_fallback = not self.grade_by_sig
        if self.home_fallback:
            for s in self.home_cfg.get("sites", []):
                sg, ck = common.sig(s), common.content_key(s)
                self.grade_by_sig.setdefault(sg, "likely")
                self.grade_by_content.setdefault(ck, "likely")

    def info(self, site):
        sg = common.sig(site)
        ck = common.content_key(site)
        st = self.stab.get(sg) or {}
        return {
            "sig": sg,
            "grade": self.grade_by_sig.get(sg) or self.grade_by_content.get(ck, "unknown"),
            "inferred": sg in self.inferred or ck in self.inferred_content,
            "playable": sg in self.playable,
            "score": int(self.scores.get(sg, 0) or 0),
            "home_days": int(st.get("home_days_7", 0) or 0),
            "ok_days": int(st.get("ok_days_7", 0) or 0),
            "fail_streak": int(st.get("fail_streak", 0) or 0),
            # 连续 2 天以上探活失败才排除；误杀复核复活的站点清零
            "dead": int(self.probe_fail.get(sg, 0) or 0) >= 2 and sg not in self.revived,
            "revived": sg in self.revived,
        }

    def evidence(self):
        return {
            "home_report": not self.home_fallback,
            "home_graded": len(self.grade_by_sig),
            "home_inferred": len(self.inferred),
            "play_sampled": self.play_sampled,
            "play_rate": self.play_rate,
            "playable_sites": len(self.playable),
            "history_days": self.days,
            "stability_records": len(self.stab),
            "parses_alive": len(self.alive_parses),
            "parses_dead": len(self.dead_parses),
            "revived": len(self.revived),
        }


# 这些字段描述的是「build.py 怎么把 29 个上游合成全量版」，筛选版照抄会撒谎：
# 实测三份场景版曾都带着 jar_count=53 / jar_top=yw530.jar×136，而各自实际只有
# 19 / 0 / 0 个唯一 jar；还有 sites_blocked、sources_ok、polish 等全量版口径。
BUILD_ONLY_META = (
    "sites_blocked", "lives_blocked", "parses_blocked", "parses_deduped",
    "blocklist_rules", "sources_ok", "sources_failed", "dead_policy",
    "sites_dropped_dead", "dropped", "observation", "polish",
    "home_first_count", "home_first_note", "home_grades", "home_inferred",
    "ranking", "note", "history_days",
)


def pack(cfg, sites, meta, parses=None, lives=None):
    out = {k: v for k, v in cfg.items() if k not in ("sites", "_meta")}
    m = dict(cfg.get("_meta") or {})
    for k in BUILD_ONLY_META:
        m.pop(k, None)
    m["sites"] = len(sites)
    m.update(meta)
    m["updated"] = common.env_info()["generated"]
    m["probe_env"] = common.env_info()
    # 按这一份自己的站点重算 jar 统计；照抄全量版的 53 会让「有几个 jar 依赖」看不出来
    jars = Counter(str(s.get("jar") or "").split(";", 1)[0] for s in sites
                   if str(s.get("jar") or "").startswith("http"))
    m["jar_count"] = len(jars)
    m["jar_top"] = dict(jars.most_common(5))
    out["_meta"] = m
    out["sites"] = sites
    if parses is not None:
        out["parses"] = parses
    if lives is not None:
        out["lives"] = lives
    # 元信息必须写这一份自己的实际条数：源配置是 187 条解析，精播版剔掉探活
    # 失败后剩 170、极速版截断后剩 40，继续沿用 187 就是骗客户端
    m["parses"] = len(out.get("parses", []))
    m["lives"] = len(out.get("lives", []))
    # 整份配置里已经没有站点需要 jar 了，就别留全局 spider：客户端会照拉不误，
    # 2.9 MB 的 jar 在国内拉不到会让开屏多等十几秒（稳定版 90 个站全是采集源）
    if out.get("spider") and not any(
            str(s.get("jar") or "").strip() or
            str(s.get("api", "")).lower().startswith("csp_") for s in sites):
        out.pop("spider", None)
        m["spider_dropped"] = "无 jar 依赖"
    return out


def keeps_parses(cfg, ctx, mode, cap=0):
    ps = list(cfg.get("parses", []))
    if mode == "alive":
        kept = [p for p in ps if str(p.get("url") or "") not in ctx.dead_parses]
    elif mode == "top":
        kept = sorted(ps, key=lambda p: (
            0 if str(p.get("url") or "") in ctx.alive_parses else
            (2 if str(p.get("url") or "") in ctx.dead_parses else 1)))
        kept = [p for p in kept if str(p.get("url") or "") not in ctx.dead_parses]
        kept = kept[:cap] if cap else kept
    else:
        kept = ps
    return kept, len(ps) - len(kept)


def quality_version(ctx, pool, cfg, floor):
    """精播版：端到端可播 > 首页影片实测非空 > jar/ext 实测有效 > 复核复活。"""
    picked = []
    for i, s in enumerate(pool):
        x = ctx.info(s)
        if x["dead"]:
            continue
        if x["playable"]:
            tier = 0
        elif x["grade"] == "ok" and not x["inferred"]:
            tier = 1
        elif x["grade"] == "likely" and not x["inferred"]:
            tier = 2
        elif x["revived"] or x["score"] >= 3:
            tier = 3
        else:
            continue
        picked.append((tier, -x["score"], -x["home_days"],
                       BAND.get(x["grade"], 9), i, s))
    picked.sort(key=lambda t: t[:5])
    sites = [t[5] for t in picked]

    fallback = len(sites) < floor
    if fallback:
        sites = list(pool[:max(floor, 200)])
    parses, removed = keeps_parses(cfg, ctx, "alive")
    meta = {
        "variant": "quality",
        "variant_label": "精播版",
        "variant_note": "只留实测可播 / 首页实测有影片 / jar 实测有效的节点，剔除死解析通道",
        "source_config": "tvbox-full-homefirst.json",
        "filter": {"end_to_end_playable": len(ctx.playable),
                   "dead_parses_removed": removed,
                   "history_fallback": fallback,
                   "home_fallback": ctx.home_fallback},
        "play_rate": ctx.play_rate,
        "play_sampled": ctx.play_sampled,
        "stability_days": ctx.days,
        "ranking": "playable > home_ok > home_likely > play_score > home_days_7",
        "note": "精播版：宁缺毋滥，抽样可播率不代表全部节点，详见 _meta.evidence",
        "evidence": ctx.evidence(),
    }
    return pack(cfg, sites, meta, parses=parses)


def fast_version(ctx, pool, cfg, limit, floor):
    """国内极速版：无 jar、无境外依赖、只留 http 采集源，取质量最高的前 N 个。"""
    picked = []
    for i, s in enumerate(pool):
        api = str(s.get("api") or "")
        if not api.startswith("http") or str(s.get("type") or "") == "3" or offshore(s):
            continue
        x = ctx.info(s)
        if x["dead"]:
            continue
        if not (x["playable"] or (x["grade"] in GOOD_GRADES and not x["inferred"])
                or x["score"] >= 3 or x["revived"]):
            continue
        tier = 0 if x["playable"] else (1 if x["grade"] == "ok" else 2)
        picked.append((tier, -x["score"], -x["home_days"],
                       BAND.get(x["grade"], 9), i, s))
    picked.sort(key=lambda t: t[:5])
    sites = [t[5] for t in picked[:limit]]

    fallback = len(sites) < floor
    if fallback:
        # 降级：保底给出前 limit 个国内直连 http 站点，避免空配置
        sites = [s for s in pool
                 if str(s.get("api") or "").startswith("http")
                 and str(s.get("type") or "") != "3"
                 and not offshore(s)][:limit]

    parses, removed = keeps_parses(cfg, ctx, "top", cap=40)
    out = pack(cfg, sites, {
        "variant": "cn-fast",
        "variant_label": "国内极速版",
        "variant_note": "国内直连 + 无 spider jar + 无爬虫源，节点按质量取前 %d 个" % limit,
        "source_config": "tvbox-network-homefirst.json",
        "filter": {"max_sites": limit, "parses_cap": 40,
                   "parses_removed": removed, "offshore_excluded": True,
                   "spider_removed": False, "history_fallback": fallback,
                   "home_fallback": ctx.home_fallback},
        "play_rate": ctx.play_rate,
        "play_sampled": ctx.play_sampled,
        "stability_days": ctx.days,
        "ranking": "playable > home_ok > play_score > home_days_7",
        "note": "极速版：老盒子 / 国内直连首选，牺牲覆盖面换开屏速度",
        "evidence": ctx.evidence(),
    }, parses=parses)
    # 全局 spider 常指向境外 jar，客户端仍会去拉一次；这里整份不依赖 jar，直接去掉
    sp = str(out.get("spider") or "")
    if sp and common.hosting_host({"api": sp}, deep=True):
        out.pop("spider", None)
        out["_meta"]["filter"]["spider_removed"] = True
    return out


def stable_version(ctx, pool, cfg, floor):
    """7 天稳定版：历史累积够 3 天后按「近 7 天可用天数」筛选，之前用今日实测。"""
    strict = ctx.days >= 3
    need = min(3, max(1, ctx.days - 1)) if strict else 1
    picked = []
    for i, s in enumerate(pool):
        x = ctx.info(s)
        if x["dead"]:
            continue
        if strict:
            # 同 jar 推断项在历史里也会被记成「连续可用」，若放进来，
            # 历史满 3 天那天稳定版会从百来个实测节点暴涨到上千个推断节点。
            # 稳定版始终只认实测，降级/严格两种口径保持一致。
            ok = (x["home_days"] >= need and x["fail_streak"] == 0
                  and not x["inferred"])
        else:
            # 历史不足时也不能照单全收：只认「首页影片实测非空」这个最强信号，
            # 以及端到端实测可播的站点。这样稳定版从第一天就明显比精播版保守，
            # 不会和「去掉已知坏节点」的精播版退化成同一份配置。
            ok = (x["grade"] == "ok" and not x["inferred"]
                  and x["fail_streak"] == 0) or x["playable"]
        if not ok:
            continue
        picked.append((-x["home_days"], -x["ok_days"], -x["score"],
                       BAND.get(x["grade"], 9), i, s))
    picked.sort(key=lambda t: t[:5])
    sites = [t[5] for t in picked]

    fallback = len(sites) < floor
    if fallback:
        # 保底也用「全量首页优先版」的前 N 个，与排序口径一致，
        # 不突然换成另一份未经筛选的数据
        sites = list(pool[:max(floor, 200)])
        strict = False
    return pack(cfg, sites, {
        "variant": "stable7",
        "variant_label": "7 天稳定版",
        "variant_note": "按近 7 天判定历史筛掉抖动源，历史不足时退化为今日实测并自动收紧",
        "source_config": "tvbox-full-homefirst.json",
        "filter": {"policy": "home_days_7 >= %d 且 fail_streak == 0" % need if strict
                             else "今日首页实测可用或端到端可播（历史不足）",
                   "required_home_days": need if strict else 0,
                   "history_days": ctx.days,
                   "history_fallback": not strict,
                   "home_fallback": ctx.home_fallback,
                   "excluded_inferred": True,
                   "fallback_due_to_small_result": fallback,
                   "fallback_source": "full-homefirst 前 200 个"},
        "play_rate": ctx.play_rate,
        "play_sampled": ctx.play_sampled,
        "stability_days": ctx.days,
        "ranking": "home_days_7 > ok_days_7 > play_score > home_grade",
        "note": "稳定版：不追新源，适合长期不折腾的设备；随历史天数自动变严格",
        "evidence": ctx.evidence(),
    })


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-sites", type=int, default=120, help="极速版站点上限")
    ap.add_argument("--min-sites", type=int, default=60, help="结果低于此数触发降级保底")
    ap.add_argument("--stats", default="_variants_stats.json")
    args = ap.parse_args()

    local = not (common.IS_CI or os.environ.get("ALLOW_LOCAL_WRITE") == "1")
    if local:
        print("本地试跑：输出重定向到 _local-*.json，不覆盖线上配置", flush=True)

    ctx = Context()
    if not ctx.full_sites:
        print("缺少 tvbox.json，跳过场景版生成", file=sys.stderr)
        return 1

    full_hf = load("tvbox-full-homefirst.json", {}) or ctx.full
    net_hf = load("tvbox-network-homefirst.json", {}) or full_hf
    q_pool = full_hf.get("sites", [])
    f_pool = net_hf.get("sites", [])

    jobs = [
        ("tvbox-quality.json", quality_version(ctx, q_pool, full_hf, args.min_sites)),
        ("tvbox-cn-fast.json", fast_version(ctx, f_pool, net_hf,
                                            args.max_sites, args.min_sites)),
        ("tvbox-stable7.json", stable_version(ctx, q_pool, full_hf, args.min_sites)),
    ]

    stats = {"date": common.today(), "env": common.env_info(),
             "evidence": ctx.evidence(), "versions": []}
    for name, out in jobs:
        dst = ("_local-" + name) if local else name
        common.write_json(dst, out, skip_unchanged=True)
        n = len(out["sites"])
        assert n > 0, "%s 生成了空配置" % name
        assert out["_meta"]["sites"] == n, "%s 元信息不一致" % name
        stats["versions"].append({
            "file": name, "sites": n, "parses": len(out.get("parses", [])),
            "bytes": os.path.getsize(dst),
            "variant": out["_meta"]["variant"],
            "history_fallback": bool(out["_meta"]["filter"].get("history_fallback")),
        })
        print("  %-24s 站点 %-5d 解析 %-4d | %.0f KB | %s" %
              (name, n, len(out.get("parses", [])), os.path.getsize(dst) / 1024,
               out["_meta"]["variant_label"]))
        print("     前 5 个: %s" %
              ", ".join(str(s.get("name", "")).strip()[:12] for s in out["sites"][:5]))

    common.write_json(args.stats if not local else "_local-" + args.stats,
                      stats, skip_unchanged=False)
    print("证据: %s" % json.dumps(ctx.evidence(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print("场景版生成失败: %s" % e, file=sys.stderr)
        sys.exit(1)
