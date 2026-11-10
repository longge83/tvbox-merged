#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""静态健康页 + 健康数据 + GitHub Pages 配置镜像。

把「失败静默」变成看得见：各版本站点数、上游健康（连续 3 天失败自动标注）、
首页可用趋势、端到端可播率、误杀率、断言状态、稳定版指针，一页全展示。

同时把 10 份配置复制到 site/ 交给 GitHub Pages 托管：
Pages 的缓存 TTL 远短于 jsDelivr 的 12 小时，可作为第二个、更新更快的直链，
也顺带解决 CDN 单点。

输出：
  health.json               结构化数据（提交进仓库，可被外部读取）
  site/index.html           健康页（纯静态，无外部依赖）
  site/health.json
  site/<10 份配置>          Pages 备用直链

用法：python scripts/health.py
"""
import html
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common

FILES = [
    ("tvbox-quality.json", "精播版", "只留实测可播 / 首页实测有影片的节点，宁缺毋滥"),
    ("tvbox-cn-fast.json", "国内极速版", "国内直连、无 spider jar、按质量取前 120 个，老盒子开屏最快"),
    ("tvbox-stable7.json", "7 天稳定版", "近 7 天多次可用且当前无连败，不追新源、不随上游抖动"),
    ("tvbox-full-homefirst.json", "全量 + 首页优先", "功能最全，推荐桌面/高性能盒子"),
    ("tvbox-lite-homefirst.json", "精简 + 首页优先", "不依赖 spider jar，低性能盒子首选"),
    ("tvbox-network-homefirst.json", "网络优化 + 首页优先", "不依赖境外代码托管，国内直连最稳"),
    ("tvbox-home.json", "首页版", "只保留能出主界面推荐的节点"),
    ("tvbox.json", "全量（原序）", "全部节点，原始顺序"),
    ("tvbox-lite.json", "精简（原序）", "仅 http 站点"),
    ("tvbox-network.json", "网络优化（原序）", "仅国内直连友好站点"),
]
REPO = os.environ.get("GITHUB_REPOSITORY", "dimogu/tvbox-merged")


def esc(v):
    return html.escape(str(v if v is not None else ""))


def pct(v):
    return "—" if v is None else "%.1f%%" % (float(v) * 100)


def bar(value, maximum, color):
    w = 0 if not maximum else max(2, min(100, round(100 * value / maximum)))
    return '<span class="bar"><i style="width:%d%%;background:%s"></i></span>' % (w, color)


def trend_chart(trend):
    """近 N 天「端到端可播率 / 误杀率」双折线 SVG（纯静态，无外部依赖）。"""
    if not trend:
        return ""
    W, H = 1000, 260
    pad_l, pad_r, pad_t, pad_b = 50, 18, 18, 30
    plot_w = W - pad_l - pad_r
    plot_h = H - pad_t - pad_b
    n = len(trend)

    def x(i):
        return pad_l + (plot_w * i / (n - 1)) if n > 1 else pad_l + plot_w / 2

    def y(v):
        v = 0.0 if v is None else float(v)
        return pad_t + plot_h * (1 - max(0.0, min(1.0, v)))

    grid = []
    for g in (0, 0.25, 0.5, 0.75, 1.0):
        yy = y(g)
        grid.append("<line x1='%g' y1='%g' x2='%g' y2='%g' stroke='#e3e9f2' stroke-width='1'/>"
                    % (pad_l, yy, W - pad_r, yy))
        grid.append("<text x='%g' y='%g' fill='#5c6b82' font-size='11' text-anchor='end'>%d%%</text>"
                    % (pad_l - 6, yy + 4, round(g * 100)))
    xlabels = []
    for i in (0, n // 2, n - 1):
        if 0 <= i < n:
            d = str(trend[i].get("date") or "")[5:]
            xlabels.append("<text x='%g' y='%g' fill='#5c6b82' font-size='11' text-anchor='middle'>%s</text>"
                           % (x(i), H - 8, esc(d)))

    def segments(key):
        runs, cur = [], []
        for i, t in enumerate(trend):
            v = t.get(key)
            if v is None:
                if cur:
                    runs.append(cur)
                    cur = []
            else:
                cur.append((i, v))
        if cur:
            runs.append(cur)
        return runs

    def polylines(runs, color):
        out = []
        for run in runs:
            pts = " ".join("%g,%g" % (x(i), y(v)) for i, v in run)
            out.append("<polyline points='%s' fill='none' stroke='%s' stroke-width='2.5' "
                       "stroke-linejoin='round' stroke-linecap='round'/>" % (pts, color))
            for i, v in run:
                out.append("<circle cx='%g' cy='%g' r='2.5' fill='%s'/>" % (x(i), y(v), color))
        return "".join(out)

    play = polylines(segments("play_rate"), "#0d9f6e")
    kill = polylines(segments("false_kill_rate"), "#e08a00")
    chart = ("<svg viewBox='0 0 %d %d' role='img' aria-label='可播率与误杀率趋势'>"
             "%s%s%s%s</svg>" % (W, H, "".join(grid), "".join(xlabels), kill, play))
    legend = ("<div class='legend'>"
              "<span><i style='background:#0d9f6e'></i>端到端可播率</span>"
              "<span><i style='background:#e08a00'></i>误杀率下界</span></div>")
    return chart + legend


def main():
    q = common.load_json("quality.json", {}) or {}
    st = common.load_json("_build_stats.json", {}) or {}
    hp = common.load_json("home_report.json", {}) or {}
    play = common.load_json("play_summary.json", {}) or {}
    rc = common.load_json("recheck.json", {}) or {}
    asserts = common.load_json("_assertions.json", {}) or {}
    stable = common.load_json("stable.json", {}) or {}
    trend = q.get("trend", [])

    versions = []
    for f, label, note in FILES:
        cfg = common.load_json(f, {}) or {}
        versions.append({
            "file": f, "label": label, "note": note,
            "sites": len(cfg.get("sites", [])),
            "home_first": (cfg.get("_meta") or {}).get("home_first_count"),
            "bytes": os.path.getsize(f) if os.path.exists(f) else 0,
            "cdn": "https://cdn.jsdelivr.net/gh/%s/%s" % (REPO, f),
            "mirror": "https://fastly.jsdelivr.net/gh/%s/%s" % (REPO, f),
            "pages": "https://%s.github.io/%s/%s" %
                     (REPO.split("/")[0], REPO.split("/")[1], f),
        })

    src_status = {s["tag"]: s for s in st.get("sources", [])}
    fail_streak = q.get("source_fail_streak", {})
    sources = []
    for item in src_status.values():
        tag = item["tag"]
        streak = int(fail_streak.get(tag, 0))
        sources.append({
            "tag": tag, "added": item.get("added", 0),
            "dup": item.get("dup", 0), "note": item.get("note", ""),
            "failed": item.get("note") != "ok", "fail_streak": streak,
            "alert": streak >= 3,
        })
    sources.sort(key=lambda x: (-x["alert"], -x["fail_streak"], x["tag"]))

    health = {
        "date": common.today(),
        "env": common.env_info(),
        "versions": versions,
        "sources": sources,
        "sources_ok": st.get("sources_ok", 0),
        "sources_failed": st.get("sources_failed", []),
        "home_grades": hp.get("grades", {}),
        "home_inferred": hp.get("inferred_count", 0),
        "play": {k: play.get(k) for k in
                 ("sampled", "list_rate", "detail_rate", "play_rate",
                  "search_rate", "playable", "parses_alive_rate", "parses_total")},
        "false_kill_rate": rc.get("false_kill_rate"),
        "assert_passed": bool(asserts.get("passed")),
        "assert_pass_streak": q.get("assert_pass_streak", 0),
        "stable": {"tag": stable.get("tag", ""), "commit": stable.get("commit", ""),
                   "cdn": ((stable.get("files") or {}).get("tvbox-full-homefirst.json") or {}).get("cdn", "")},
        "trend": trend,
        "dedupe": {"parses_deduped": st.get("parses_deduped", 0),
                   "blocked": st.get("blocked", {})},
    }
    # 今日体检：把「修了什么」变成可核对的数字，别只写在 commit message 里
    hygiene = []
    for f, label in (("tvbox.json", "全量版"), ("tvbox-lite.json", "精简版"),
                     ("tvbox-network.json", "网络优化版")):
        cfg = common.load_json(f, {}) or {}
        pol = (cfg.get("_meta") or {}).get("polish") or {}
        if not pol:
            continue
        lv, ps = pol.get("lives") or {}, pol.get("parses") or {}
        removed = pol.get("removed") or {}
        hygiene.append({
            "label": label,
            "file": f,
            "keys": pol.get("keys_renamed", 0),
            "content_dup": (removed.get("dup_content", 0) +
                            lv.get("dup_content", 0) + ps.get("dup_content", 0)),
            "client_norm": pol.get("client_normalized", 0),
            "ext": pol.get("ext_normalized", 0),
            "names": pol.get("renamed", 0),
            "parse_renamed": ps.get("renamed", 0),
            "parse_capped": ps.get("capped", 0),
            "offshore_live": lv.get("offshore_moved", 0),
            "wallpaper": bool(pol.get("wallpaper_removed")),
        })
    health["hygiene"] = hygiene

    # jar / ext 健康：每日探活的盲区（旧流程只查 api 接口），现在逐日探测并展示
    jh = common.load_json("_jar_health.json", {}) or {}
    jd, ed = jh.get("jars") or {}, jh.get("exts") or {}
    jar_total, jar_dead = jd.get("unique") or 0, jd.get("dead_count")
    jar_alive_rate = None if (jar_dead is None or not jar_total) else (jar_total - jar_dead) / jar_total
    health["jar_health"] = {
        "probed": bool(jh.get("probed")),
        "jar_total": jar_total, "jar_dead": jar_dead, "jar_alive_rate": jar_alive_rate,
        "ext_total": ed.get("unique") or 0, "ext_dead": ed.get("dead_count"),
        "jar_fail_streak": len(q.get("jar_fail_streak") or {}),
    }
    common.write_json("health.json", health, skip_unchanged=False)

    # ---- GitHub Pages 目录：健康页 + 配置镜像（短 TTL 备用直链）----
    os.makedirs("site", exist_ok=True)
    shutil.copyfile("health.json", os.path.join("site", "health.json"))
    for f, _, _ in FILES:
        if os.path.exists(f):
            shutil.copyfile(f, os.path.join("site", f))
    # 复查报告也放进 Pages：jsDelivr 对 .md 只做 301 转 raw，不如 Pages 直接可读
    audit = os.path.join("reports", "audit-latest.md")
    if os.path.exists(audit):
        shutil.copyfile(audit, os.path.join("site", "audit-latest.md"))

    css = """
    :root{--bg:#f6f8fb;--card:#fff;--ink:#152238;--sub:#5c6b82;--line:#e3e9f2;
          --blue:#2563eb;--green:#0d9f6e;--amber:#e08a00;--red:#dc2626}
    *{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
      font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
    .wrap{max-width:1080px;margin:0 auto;padding:32px 20px 64px}
    h1{font-size:26px;margin:0 0 6px}h2{font-size:18px;margin:34px 0 12px}
    .sub{color:var(--sub);font-size:13px;margin-bottom:24px}
    .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}
    .card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px}
    .card .k{font-size:12px;color:var(--sub)}.card .v{font-size:24px;font-weight:700;margin-top:4px}
    .card .n{font-size:11px;color:var(--sub);margin-top:4px}
    .ok{color:var(--green)}.warn{color:var(--amber)}.bad{color:var(--red)}
    table{width:100%;border-collapse:collapse;background:var(--card);
          border:1px solid var(--line);border-radius:12px;overflow:hidden;font-size:13px}
    th,td{padding:9px 12px;text-align:left;border-bottom:1px solid var(--line)}
    th{background:#eef3fa;color:var(--sub);font-weight:600}tr:last-child td{border-bottom:0}
    code{background:#eef3fa;padding:2px 6px;border-radius:5px;font-size:12px;word-break:break-all}
    .pill{display:inline-block;padding:1px 8px;border-radius:20px;font-size:11px;
          background:#e7f6ef;color:var(--green)}
    .pill.bad{background:#fdecec;color:var(--red)}.pill.warn{background:#fff3dd;color:var(--amber)}
    .bar{display:inline-block;width:110px;height:8px;background:#e9eef6;border-radius:6px;
         vertical-align:middle;margin-right:6px;overflow:hidden}
    .bar i{display:block;height:100%;border-radius:6px}
    .links a{color:var(--blue);text-decoration:none}.links a:hover{text-decoration:underline}
    .note{background:#fff8e8;border:1px solid #f4dfae;border-radius:10px;padding:12px 16px;
          font-size:13px;color:#7a5b12;margin:16px 0}
    footer{margin-top:40px;color:var(--sub);font-size:12px}
    svg{max-width:100%;height:auto;background:#fff;border:1px solid var(--line);border-radius:12px}
    .legend{font-size:12px;color:var(--sub);margin:8px 0 0}
    .legend span{display:inline-block;margin-right:16px}
    .legend i{display:inline-block;width:12px;height:12px;border-radius:3px;margin-right:5px;vertical-align:middle}
    @media(max-width:640px){.wrap{padding:20px 12px}table{font-size:12px}th,td{padding:7px 8px}}
    """
    p = []
    p.append("<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>")
    p.append("<meta name='viewport' content='width=device-width,initial-scale=1'>")
    p.append("<title>TVBox 聚合配置 · 健康状态</title><style>" + css + "</style></head><body><div class='wrap'>")
    p.append("<h1>TVBox 聚合配置 · 健康状态</h1>")
    p.append("<div class='sub'>生成时间 %s · 判定环境 %s · 每日北京时间 04:00 自动构建 · "
             "<a href='health.json'>原始健康数据 JSON</a></div>" %
             (esc(common.env_info()["generated"]), esc(common.env_info()["env"])))

    status_cls = "ok" if health["assert_passed"] else "bad"
    status_txt = "通过" if health["assert_passed"] else "未通过（本次未提交）"
    # 版本清单会随迭代增加，卡片按文件名取数，不依赖固定下标
    by_file = {v["file"]: v for v in versions}

    def n(f):
        return (by_file.get(f) or {}).get("sites", 0)

    p.append("<div class='cards'>")
    cards = [
        ("全量版站点", n("tvbox.json"), "精简 %d · 网络优化 %d · 精播 %d" %
         (n("tvbox-lite.json"), n("tvbox-network.json"), n("tvbox-quality.json")), ""),
        ("首页可用节点", n("tvbox-home.json"), "同 jar 推断召回 %d" % health["home_inferred"], ""),
        ("端到端可播率", pct(health["play"]["play_rate"]),
         "抽样 %d 站 · 列表→详情→播放地址" % (health["play"]["sampled"] or 0), ""),
        ("误杀率下界", pct(health["false_kill_rate"]),
         "fail 抽样用真实 TVBox UA 复核", ""),
        ("上游健康", "%d/%d" % (health["sources_ok"], health["sources_ok"] + len(health["sources_failed"])),
         "连续失败 ≥3 天自动标红", "ok" if not health["sources_failed"] else "warn"),
        ("回归断言", status_txt, "连续通过 %d 天" % health["assert_pass_streak"], status_cls),
        ("jar 健康", (pct(health["jar_health"]["jar_alive_rate"])
                      if health["jar_health"]["probed"] else "未探测"),
         "死 %s / 唯一 %s · 连败 %d" %
         (health["jar_health"]["jar_dead"] if health["jar_health"]["jar_dead"] is not None else "—",
          health["jar_health"]["jar_total"], health["jar_health"]["jar_fail_streak"]),
         "ok" if (not health["jar_health"]["probed"] or
                  (health["jar_health"]["jar_alive_rate"] or 0) >= 0.5) else "warn"),
    ]
    for k, v, n, cls in cards:
        p.append("<div class='card'><div class='k'>%s</div><div class='v %s'>%s</div>"
                 "<div class='n'>%s</div></div>" % (esc(k), cls, esc(v), esc(n)))
    p.append("</div>")

    if health["stable"]["tag"]:
        p.append("<div class='note'><b>稳定版指针</b> <code>%s</code>（断言连续通过 3 天才推进，"
                 "tag 内容不可变，可随时回滚）<br>固定直链：<a href='%s'>%s</a></div>" %
                 (esc(health["stable"]["tag"]), esc(health["stable"]["cdn"]),
                  esc(health["stable"]["cdn"])))
    if not health["assert_passed"]:
        p.append("<div class='note'>本次回归断言未通过，workflow 不会提交，线上仍是上一版配置。"
                 "查看 <a href='https://github.com/%s/actions'>Actions 日志</a>定位。</div>" % esc(REPO))

    # 版本与直链
    p.append("<h2>配置版本与完整直链</h2><table><tr><th>版本</th><th>站点数</th>"
             "<th>置顶</th><th>用途</th><th>大小</th><th>国内直链（jsDelivr）</th>"
             "<th>fastly 镜像</th><th>Pages 备用</th></tr>")
    for v in versions:
        p.append("<tr><td><b>%s</b><br><code>%s</code></td><td>%d</td><td>%s</td>"
                 "<td>%s</td><td>%.0f KB</td><td class='links'><a href='%s'>%s</a></td>"
                 "<td class='links'><a href='%s'>fastly</a></td>"
                 "<td class='links'><a href='%s'>Pages</a></td></tr>" %
                 (esc(v["label"]), esc(v["file"]), v["sites"],
                  esc(v["home_first"] if v["home_first"] is not None else "—"),
                  esc(v["note"]), v["bytes"] / 1024, esc(v["cdn"]), esc(v["file"]),
                  esc(v["mirror"]), esc(v["pages"])))
    p.append("</table>")
    p.append("<div class='sub'>今日三份基础配置的复查明细（删了什么、改了什么名、为什么这样排序）："
             "<a href='audit-latest.md'>reports/audit-latest.md</a></div>")

    # 今日体检：结构性问题（客户端看不见站点 / 整份配置解析失败）的可核对数字
    if health.get("hygiene"):
        p.append("<h2>今日配置体检</h2>")
        p.append("<table><tr><th>版本</th><th>主键去重</th><th>内容去重</th><th>兼容归一</th>"
                 "<th>ext 归一</th><th>同名消歧</th><th>解析重名</th><th>解析截断</th>"
                 "<th>境外直播沉底</th><th>壁纸</th></tr>")
        for h in health["hygiene"]:
            p.append("<tr><td><b>%s</b><br><code>%s</code></td><td>%d</td><td>%d</td>"
                     "<td>%d</td><td>%d</td><td>%d</td><td>%d</td><td>%d</td>"
                     "<td>%d</td><td>%s</td></tr>" %
                     (esc(h["label"]), esc(h["file"]), h["keys"],
                      h.get("content_dup", 0), h.get("client_norm", 0), h["ext"],
                      h["names"], h["parse_renamed"], h["parse_capped"],
                      h["offshore_live"], "已移除" if h["wallpaper"] else "保留"))
        p.append("</table>")
        p.append("<div class='sub'>主键去重：`key` 是客户端唯一主键，重复会让后一个站点被顶掉；"
                 "内容去重：名称只是显示用的皮，按 <code>api + ext + jar</code> 的内容指向判重，"
                 "并把 raw / jsdelivr / gh-proxy / github blob 等链接别名还原成同一份，"
                 "删掉换皮重复；"
                 "兼容归一：`style` / `header` 写成 JSON 对象会让 TVBox 原版、影视仓"
                 "抛 `IllegalStateException` 导致整份配置导不进来，统一降级；"
                 "ext 归一：对象型 `ext` 会让严格解析的客户端判定整份配置非法；"
                 "境外直播沉底：走 GitHub / 反代的直播源在国内必然是空频道，排到列表末尾。</div>")

    # 上游健康
    p.append("<h2>上游源健康</h2><table><tr><th>源</th><th>今日采纳</th><th>去重</th>"
             "<th>状态</th><th>连续失败</th></tr>")
    for s in sources:
        if s["failed"]:
            badge = "<span class='pill %s'>失败 %s</span>" % (
                "bad" if s["alert"] else "warn", esc(s["note"]))
        else:
            badge = "<span class='pill'>正常</span>"
        p.append("<tr><td><code>%s</code></td><td>%d</td><td>%d</td><td>%s</td>"
                 "<td>%s</td></tr>" %
                 (esc(s["tag"]), s["added"], s["dup"], badge,
                  ("<b class='bad'>%d 天（建议更换）</b>" % s["fail_streak"]) if s["alert"]
                  else ("%d 天" % s["fail_streak"])))
        p.append("</table>")

    # jar / ext 健康（每日探活盲区）
    jh2 = health["jar_health"]
    jh2["ext_fail_streak"] = len(q.get("ext_fail_streak") or {})
    if jh2["probed"]:
        p.append("<h2>jar / ext 健康</h2>")
        p.append("<div class='note'>spider jar 与远程 ext 是旧流程的探活盲区（每日只查 api 接口），"
                 "失效的 jar / ext 会一直留在配置里、用户点开就空。现已接入逐日探测：今日 死 jar "
                 "<b>%s</b> / 唯一 %s（可用率 %s）；死 ext <b>%s</b> / 唯一 %s；"
                 "多日连败（≥2 天）的死 jar 共 <b>%d</b> 个、死 ext 共 <b>%d</b> 个，"
                 "其依赖的站点已被自动沉底。"
                 "</div>" % (jh2["jar_dead"] if jh2["jar_dead"] is not None else "—",
                             jh2["jar_total"], pct(jh2["jar_alive_rate"]),
                             jh2["ext_dead"] if jh2["ext_dead"] is not None else "—",
                             jh2["ext_total"], jh2["jar_fail_streak"],
                             jh2["ext_fail_streak"]))
    else:
        p.append("<h2>jar / ext 健康</h2><div class='sub'>本次未执行网络探测（本地试跑或探测步骤跳过）。</div>")

    # 首页判定分布
    p.append("<h2>首页推荐判定分布</h2><table><tr><th>等级</th><th>数量</th><th>占比</th><th>含义</th></tr>")
    meanings = {"ok": "首页影片列表实测非空", "likely": "jar 与首页配置有效（含同 jar 推断）",
                "classes_only": "只返回分类，首页可能为空", "unknown": "依赖 jar 内置首页，需客户端实测",
                "fail": "jar/ext 失效或接口连不上"}
    total_grade = sum(health["home_grades"].values()) or 1
    for g in ("ok", "likely", "classes_only", "unknown", "fail"):
        n = health["home_grades"].get(g, 0)
        p.append("<tr><td><code>%s</code></td><td>%d</td><td>%s %.0f%%</td><td>%s</td></tr>" %
                 (g, n, bar(n, total_grade, "#2563eb" if g in ("ok", "likely") else "#94a3b8"),
                  100 * n / total_grade, esc(meanings[g])))
    p.append("</table>")

    # 趋势
    p.append("<h2>近 %d 天趋势</h2>" % len(trend))
    if trend:
        p.append(trend_chart(trend))
        maxsites = max([t.get("sites") or 0 for t in trend] + [1])
        p.append("<table><tr><th>日期</th><th>全量站点</th><th>首页可用</th>"
                 "<th>可播率</th><th>误杀率</th><th>上游 OK</th><th>断言</th></tr>")
        for t in trend:
            p.append("<tr><td>%s</td><td>%s %d</td><td>%s %d</td><td>%s</td><td>%s</td>"
                     "<td>%d</td><td>%s</td></tr>" %
                     (esc(t.get("date")), bar(t.get("sites") or 0, maxsites, "#2563eb"),
                      t.get("sites") or 0, bar(t.get("home_sites") or 0, maxsites, "#0d9f6e"),
                      t.get("home_sites") or 0, pct(t.get("play_rate")),
                      pct(t.get("false_kill_rate")), t.get("sources_ok") or 0,
                      "<span class='pill'>通过</span>" if t.get("assert_passed")
                      else "<span class='pill bad'>未过</span>"))
        p.append("</table>")
    else:
        p.append("<div class='sub'>暂无历史数据，次日构建后开始积累。</div>")

    p.append("<h2>口径说明</h2><div class='sub'>"
             "「可播率」= 抽样站点中「首页列表 → 详情 vod_play_url → 播放地址可连通」全部成立的比例；"
             "「误杀率下界」= 被判 fail 的站点抽样后用真实 TVBox UA 复核复活的比例；"
             "所有判定都在 GitHub Actions 云端网络执行，本机结果只作调试。 "
             "站点排序依据：今日首页等级 &gt; 近 7 天可用天数 &gt; 端到端可播得分。</div>")
    p.append("<footer>本项目仅聚合公开配置文件，不托管任何视频内容。健康页与配置均由 Actions 自动生成。</footer>")
    p.append("</div></body></html>")

    with open(os.path.join("site", "index.html"), "w", encoding="utf-8") as f:
        f.write("".join(p))
    print("已生成 health.json 与 site/index.html（%d 个版本，%d 个上游，%d 天趋势）" %
          (len(versions), len(sources), len(trend)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
