#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全历史问题回归复核：把九轮迭代踩过的坑变成一份可复跑的清单。

`assertions.py` 是每日提交前的「保险丝」，只管当天产物能不能上线；本脚本管的是
**整个项目**：产物契约、生成链路、工作流、文档口径、线上线路，逐条对照历史上真实
出现过的故障复核，确认修复仍在、防线没被绕过、没有新引入的同类问题。

默认完全离线（只读仓库文件），适合每次改完代码立刻跑；`--online` 追加线上
Pages / jsDelivr 三种线路与仓库 HEAD 的 sha256 比对（需网络，本机可加 --proxy）。

输出（CI / 本地自动分流，避免本地试跑覆盖入库的当日结论）：
  CI    reports/regression-latest.md + reports/regression.json（Actions 用 git add -f 提交）
  本地  _regression-local.md + _regression.json（均被 .gitignore 忽略）
可用 --output / --json-output 显式覆盖。
用法：python scripts/regression.py [--online] [--proxy http://127.0.0.1:7890]
"""
import argparse
import hashlib
import json
import os
import py_compile
import re
import subprocess
import sys
import urllib.request
from collections import Counter
from urllib.parse import urlsplit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import common  # noqa: E402

CONFIGS = [
    "tvbox.json", "tvbox-lite.json", "tvbox-network.json", "tvbox-home.json",
    "tvbox-full-homefirst.json", "tvbox-lite-homefirst.json",
    "tvbox-network-homefirst.json",
    "tvbox-quality.json", "tvbox-cn-fast.json", "tvbox-stable7.json",
]
JAR_FREE = ("tvbox-lite.json", "tvbox-network.json",
            "tvbox-lite-homefirst.json", "tvbox-network-homefirst.json",
            "tvbox-cn-fast.json")
HOSTING_CLEAN = ("tvbox-network.json", "tvbox-network-homefirst.json",
                 "tvbox-cn-fast.json")
JAR_MEDIA_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg",
                 ".ico", ".mp4", ".css", ".html")
REQUIRED_ASSERTIONS = (
    "client_compat_clean", "no_null_strings", "no_content_duplicates",
    "jar_not_media", "csp_has_jar", "no_relative_path",
    "lite_network_jar_free", "network_hosting_clean",
    "meta_counts_consistent", "meta_jar_consistent",
    "collections_not_empty", "ext_contract_clean", "site_key_unique",
)


def path(f):
    return os.path.join(ROOT, f)


class DuplicateKey(Exception):
    pass


def _pairs(pairs):
    seen = set()
    for k, _ in pairs:
        if k in seen:
            raise DuplicateKey(k)
        seen.add(k)
    return dict(pairs)


def load(f, default=None):
    try:
        with open(path(f), encoding="utf-8") as fh:
            return json.load(fh, object_pairs_hook=_pairs)
    except Exception:
        return default


def text(f):
    try:
        with open(path(f), encoding="utf-8") as fh:
            return fh.read()
    except Exception:
        return ""


def all_configs():
    cfgs, bad = {}, []
    for f in CONFIGS:
        c = load(f)
        if c is None:
            bad.append(f)
        else:
            cfgs[f] = c
    return cfgs, bad


def dup_segment(url):
    try:
        segs = [s for s in urlsplit(url).path.split("/") if s][2:]
    except Exception:
        return None
    for a, b in zip(segs, segs[1:]):
        if a == b and len(a) >= 2:
            return a
    return None


def jar_key(v):
    return str(v or "").split(";", 1)[0]


RESULTS = []


def check(cid, group, origin, title, fn, critical=True):
    """fn() -> (ok, detail)。ok=True/False/None(跳过)。单条异常不会中断整份复核。"""
    try:
        r = fn()
        # 允许检查函数只返回 bool；统一成 (ok, detail)，避免手写检查时漏元组。
        ok, detail = r if isinstance(r, tuple) and len(r) == 2 else (r, "")
    except Exception as e:
        ok, detail = False, "复核脚本异常：%s: %s" % (type(e).__name__, str(e)[:120])
    RESULTS.append({"id": cid, "group": group, "origin": origin,
                    "title": title, "ok": ok, "detail": str(detail)[:300],
                    "critical": critical})


def scan_all(cfgs):
    """把 10 份配置摊平成待查对象，供多条检查复用。"""
    rows = []
    for f, c in cfgs.items():
        for coll, kind in (("sites", "site"), ("parses", "parse"), ("lives", "live")):
            for o in c.get(coll, []):
                if isinstance(o, dict):
                    rows.append((f, coll, kind, o))
    return rows


def build_checks(cfgs, bad, online, proxy):
    A, B, C = "产物契约", "构建链路", "线上线路"

    # ---------- A. 产物契约：历史上让用户「导不进 / 一点就崩 / 首页空白」的问题 ----------
    check("A01", A, "第九轮 / 全程", "10 份配置齐全、合法 JSON 且无重复键",
          lambda: (not bad and len(cfgs) == 10,
                   ("缺失或损坏：%s" % ", ".join(bad)) if bad else
                   "%d 份全部合法、无重复 key" % len(cfgs)))

    rows = scan_all(cfgs)

    def c_style():
        hits = ["%s %s:%s" % (f, coll, k) for f, coll, kind, o in rows
                for k in ("style", "header") if isinstance(o.get(k), (dict, list))]
        return not hits, ("%d 处对象型 style/header：%s" % (len(hits), ", ".join(hits[:3]))
                          if hits else "0 处对象型 style/header（不会抛 IllegalStateException）")

    check("A02", A, "第九轮：导入报 IllegalStateException", "style / header 不得是 JSON 对象", c_style)

    def c_numeric():
        # type 的客户端契约按集合相反：sites / parses 走 Gson getAsInt() 必须是
        # 整数；lives 在影视仓(catvod) 的 Live.type 声明为 String，必须是字符串。
        # 旧口径把三者一锅端地要求「不得是字符串」，会把 lives.type 的正确修复
        # 判成回归（实测 980 处命中，健康页长期挂 A03 阻断）。
        hits = ["%s %s:%s=%r" % (f, coll, k, o.get(k)) for f, coll, kind, o in rows
                for k in ("playerType", "gridview") if isinstance(o.get(k), str)]
        for f, coll, kind, o in rows:
            t = o.get("type")
            if coll == "lives":
                if not isinstance(t, str):
                    hits.append("%s lives:type=%r 非字符串" % (f, t))
            elif not isinstance(t, int) or isinstance(t, bool):
                hits.append("%s %s:type=%r 非整数" % (f, coll, t))
        return not hits, ("%d 处类型契约违规：%s" % (len(hits), ", ".join(hits[:3]))
                          if hits else "playerType/gridview 为整数，sites/parses.type 为整数，lives.type 为字符串")

    check("A03", A, "第九轮 / 第三轮 / 类型契约修复",
          "类型契约：sites/parses.type 整数、lives.type 字符串、playerType/gridview 非字符串", c_numeric)

    def c_null():
        hits = []
        for f, coll, kind, o in rows:
            for k in common.client_string_fields(kind):
                if k not in o or o.get(k) is None or not isinstance(o.get(k), str):
                    hits.append("%s %s:%s" % (f, coll, k))
        return not hits, ("%d 处可空 String：%s" % (len(hits), ", ".join(hits[:3]))
                          if hits else "客户端会裸 trim() 的字段全部非 null（不会 NPE 闪退）")

    check("A04", A, "第九轮：播放期 NullPointerException(String.trim())",
          "裸 trim 的 String 字段不得缺失 / null / 非字符串", c_null)

    def c_media_jar():
        hits = []
        for f, c in cfgs.items():
            for s in c.get("sites", []):
                j = jar_key(s.get("jar"))
                if j.startswith("http") and \
                        urlsplit(j.split("?", 1)[0]).path.lower().endswith(JAR_MEDIA_EXT):
                    hits.append("%s:%s" % (f, str(s.get("name", ""))[:12]))
        return not hits, ("%d 个媒体类 jar：%s" % (len(hits), ", ".join(hits[:3]))
                          if hits else "0 个图片/媒体被塞进 jar 槽")

    check("A05", A, "第六轮：图片被当 spider jar（352 站）", "jar 不得是图片 / 媒体 URL", c_media_jar)

    def c_jar_space():
        hits = ["%s:%s" % (f, str(s.get("name", ""))[:12]) for f, coll, kind, o in rows
                if coll == "sites" and str(o.get("jar") or "") != str(o.get("jar") or "").strip()]
        return not hits, ("%d 个 jar 带前后空格：%s" % (len(hits), ", ".join(hits[:3]))
                          if hits else "jar 值无前后空白（不会被误判缺 jar / 拉 404）")

    check("A06", A, "第五轮：jar 字段带前后空格（26 站）", "jar 值必须已 strip", c_jar_space)

    def c_csp():
        hits, total = [], 0
        for s in cfgs.get("tvbox.json", {}).get("sites", []):
            if str(s.get("api", "")).startswith("csp_"):
                total += 1
                if not jar_key(s.get("jar")).startswith("http"):
                    hits.append(str(s.get("name", ""))[:12])
        return len(hits) / max(1, total) <= 0.3, \
            "%d/%d 个 csp 站点缺 jar（上游本身可能没写，阈值 30%%）" % (len(hits), total)

    check("A07", A, "第一轮：csp 爬虫节点没有 jar", "csp 站点必须有 http jar", c_csp)

    def c_relative():
        hits = ["%s:%s" % (f, k) for f, coll, kind, o in rows
                for k in ("api", "jar", "ext", "url")
                if isinstance(o.get(k), str) and re.match(r"^\.{1,2}/", o[k])]
        return not hits, ("%d 处残留相对路径：%s" % (len(hits), ", ".join(hits[:3]))
                          if hits else "0 处 ./ ../ 相对路径")

    check("A08", A, "合并坑：上游相对路径未绝对化", "api/jar/ext/url 不得是相对路径", c_relative)

    def c_dupseg():
        hits = ["%s:%s" % (f, dup_segment(o[k])) for f, coll, kind, o in rows
                for k in ("api", "jar", "ext")
                if isinstance(o.get(k), str) and o[k].startswith("http") and dup_segment(o[k])]
        return len(hits) < 3, ("%d 处相邻重复路径段：%s" % (len(hits), ", ".join(hits[:3]))
                               if hits else "0 处 XBPQ/XBPQ 类基准拼错")

    check("A09", A, "第九轮：yw88075 相对仓库根拼错（138 站首页空白）",
          "无相邻重复路径段（跳过 raw 的 owner/repo 同名）", c_dupseg)

    def c_content_dup():
        hits = []
        for f, c in cfgs.items():
            seen = set()
            for s in c.get("sites", []):
                k = common.content_key(s)
                if k in seen:
                    hits.append(f)
                seen.add(k)
            for coll in ("lives", "parses"):
                seen2 = set()
                for o in c.get(coll, []):
                    if not isinstance(o, dict):
                        continue
                    k = (common.content_url(o.get("url")), common.content_url(o.get("ext")))
                    if k[0] and k in seen2:
                        hits.append("%s:%s" % (f, coll))
                    seen2.add(k)
        return not hits, ("%d 处内容重复：%s" % (len(hits), ", ".join(hits[:3]))
                          if hits else "10 份配置按内容指向判重后均无重复")

    check("A10", A, "第八轮：换皮名 / raw-jsdelivr-gh-proxy 别名重复",
          "站点 / 直播 / 解析按内容指向去重", c_content_dup)

    def c_meta():
        bad_meta = []
        for f, c in cfgs.items():
            m = c.get("_meta") or {}
            for k, coll in (("sites", "sites"), ("lives", "lives"), ("parses", "parses")):
                if m.get(k) is not None and m[k] != len(c.get(coll, [])):
                    bad_meta.append("%s.%s %s≠%s" % (f, k, m[k], len(c.get(coll, []))))
            actual = len({jar_key(s.get("jar")) for s in c.get("sites", [])
                          if jar_key(s.get("jar")).startswith("http")})
            if m.get("jar_count") is not None and m["jar_count"] != actual:
                bad_meta.append("%s.jar_count %s≠%s" % (f, m["jar_count"], actual))
        return not bad_meta, ("; ".join(bad_meta[:4]) + (" 共 %d 处" % len(bad_meta) if len(bad_meta) > 4 else "")
                              if bad_meta else "sites/lives/parses/jar_count 全部按各自内容重算")

    check("A11", A, "第三轮 / 本轮：筛选版照抄全量版 meta", "_meta 计数与实际内容一致", c_meta)

    def c_jarfree():
        hits = []
        for f in JAR_FREE:
            c = cfgs.get(f)
            if not c:
                continue
            if c.get("spider"):
                hits.append(f + ":spider")
            n = sum(1 for s in c.get("sites", [])
                    if str(s.get("jar") or "").strip() or
                    str(s.get("api", "")).lower().startswith("csp_"))
            if n:
                hits.append("%s:%d" % (f, n))
        return not hits, ("; ".join(hits[:4]) if hits else
                          "%d 份「无 jar 依赖」版本实测 0 jar / 0 csp / 无全局 spider" % len(JAR_FREE))

    check("A12", A, "第三轮：精简版号称不依赖 jar 却带 spider + 6 个 jar 站",
          "精简 / 网络 / 极速版真的不加载 jar", c_jarfree)

    def c_hosting():
        hits = []
        for f in HOSTING_CLEAN:
            c = cfgs.get(f)
            if not c:
                continue
            for s in c.get("sites", []):
                h = common.hosting_host(s, deep=True)
                if h:
                    hits.append("%s:%s" % (f, h))
                    break
        return not hits, ("; ".join(hits[:3]) if hits else
                          "网络优化版 / 国内极速版的 sites 均无境外配置托管地（lives 沉底不删属设计）")

    check("A13", A, "第四轮：境外托管黑名单口径（gitee 误伤）",
          "网络 / 极速版 sites 无境外配置托管地", c_hosting)

    def c_subset():
        full = cfgs.get("tvbox.json", {}).get("sites", [])
        keys = {common.content_key(s) for s in full}
        hits = []
        for f, c in cfgs.items():
            if f == "tvbox.json":
                continue
            extra = sum(1 for s in c.get("sites", []) if common.content_key(s) not in keys)
            if extra:
                hits.append("%s:%d" % (f, extra))
        return not hits, ("; ".join(hits[:4]) if hits else
                          "9 份派生配置均 ⊆ 全量版（按内容指向，改名不影响）")

    check("A14", A, "场景版 / 首页版混入非全量版节点", "派生版本站点集合 ⊆ 全量版", c_subset)

    def c_hygiene():
        hits = []
        for f, c in cfgs.items():
            names = Counter(str(s.get("name") or "") for s in c.get("sites", []))
            if any(v > 1 for v in names.values()):
                hits.append(f + ":重名")
            ks = Counter(str(s.get("key") or "") for s in c.get("sites", []))
            if any(v > 1 for v in ks.values()):
                hits.append(f + ":重复key")
            for s in c.get("sites", []):
                if not re.match(r"^(?:https?://\S+|csp_\S+)$", str(s.get("api") or "").strip()):
                    hits.append(f + ":非法api")
                    break
            for p in c.get("parses", []):
                if not re.match(r"^https?://", str(p.get("url") or "")):
                    hits.append(f + ":非法解析url")
                    break
            for s in c.get("sites", []):
                if "类型" in s or "playurl" in s:
                    hits.append(f + ":外来字段")
                    break
        return not hits, ("; ".join(hits[:4]) if hits else "无空/非法 api、重名、重复 key、非法解析 url、外来字段")

    check("A15", A, "第三轮：8 个分隔符被当站点 / AList×3 重名 / 客户端不认字段",
          "成品卫生：api、名称、主键、解析 url、外来字段", c_hygiene)

    def c_compact():
        loose = []
        for f, c in cfgs.items():
            raw = os.path.getsize(path(f))
            tight = len(json.dumps(c, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            if tight and raw > tight * 1.05:
                loose.append("%s %d>%d" % (f, raw, tight))
        return not loose, ("; ".join(loose[:3]) if loose else
                           "10 份均为紧凑 JSON（慢网络拉取超时风险已降）")

    check("A16", A, "第九轮：1.14 MB 大配置在 22 KB/s 下超时",
          "配置为紧凑 JSON（体积不超过紧凑序列化 105%）", c_compact)

    def c_collections():
        hits = ["%s:%s" % (f, coll) for f, c in cfgs.items()
                for coll in ("lives", "parses") if not c.get(coll)]
        return not hits, ("; ".join(hits[:4]) if hits else "10 份配置的 lives / parses 均非空")

    check("A17", A, "本轮：lives/parses 被清空时 meta 记 0、断言 0==0 放行",
          "直播 / 解析集合不得为空", c_collections)

    # ---------- B. 构建链路：顺序、静默失败、缓存、口径 ----------
    yml = text(".github/workflows/update.yml")

    check("B01", B, "第三轮：polish 必须跑在 home_probe 之前（下标回填）",
          "workflow 顺序 polish → home_probe",
          lambda: (("python scripts/polish.py" in yml and "python scripts/home_probe.py" in yml
                    and yml.index("python scripts/polish.py") < yml.index("python scripts/home_probe.py")),
                   "polish 在探测之前" if yml else "未读到 update.yml"))

    def c_gitadd():
        # 只看真正的命令行：注释里也会提到旧写法（含 "|| true"），不能拿注释误判
        code = "\n".join(l for l in yml.splitlines()
                         if not l.strip().startswith("#"))
        silent = (re.search(r"git add -f[^\n]*\|\|\s*true", code) is not None
                  or "2>/dev/null || true" in code)
        loops = "for f in" in code and '[ -e "$f" ]' in code
        guarded = "缺少必需产物" in code
        return (not silent and loops and guarded,
                "逐文件 git add=%s / 无静默吞失败=%s / 必需产物显式检查=%s" %
                (loops, not silent, guarded))

    check("B02", B, "本轮：git add -f 整条 + || true 会静默吞掉当天全部提交",
          "workflow 提交步骤逐文件判断且不吞错误", c_gitadd)

    check("B03", B, "第十轮：bash -e 末轮 `[ ... ] && sleep` 返回 1 导致 purge 跳过",
          "workflow 无 && sleep 末表达式",
          lambda: ("&& sleep 8" not in yml,
                   "已改为 if/exit 0 结构" if yml else "未读到 update.yml"))

    check("B04", B, "第九轮：jsDelivr @main 顽固缓存 / purge 需多轮",
          "purge 同时清两种路径且 workflow 至少 2 轮",
          lambda: (('repo + "/" + path' in text("scripts/purge_cdn.py")
                    and 'repo + "@main/" + path' in text("scripts/purge_cdn.py")
                    and re.search(r"--attempts\s+[2-9]", yml) is not None),
                   "无版本号 + @main 双路径 purge；--attempts 3"))

    check("B05", B, "第八轮：stable tag 里没有新增文件却造出 404 链接 / 连续 3 天才推进",
          "stable.py 用 git cat-file 判文件、min-streak 3",
          lambda: (("cat-file" in text("scripts/stable.py")
                    and '"-e"' in text("scripts/stable.py")
                    and "tag_contains" in text("scripts/stable.py")
                    and "--min-streak 3" in yml),
                   "tag_contains 用 git cat-file -e 判文件存在 + 3 天连胜门槛均在位"))

    check("B06", B, "第十一轮：同一天多次运行 history 覆盖快照",
          "history.py 按日期存快照、连胜按天计",
          lambda: (('["date"]' in text("scripts/history.py")
                    and "assert_pass_streak" in text("scripts/history.py")),
                   "快照按 date 命名，streak 按天数"))

    def c_sources():
        lines = [l.strip() for l in text("sources.txt").splitlines()
                 if l.strip() and not l.strip().startswith("#")]
        bad_lines = [l for l in lines if not re.match(r"^[^|]+\|https?://\S+(\|https?://\S+)?$", l)]
        return (not bad_lines and len(lines) >= 20,
                "%d 个上游源，格式异常 %d 条" % (len(lines), len(bad_lines)))

    check("B07", B, "第九轮：choose_base 第三列修相对仓库根的路径",
          "sources.txt 格式合法且源数量未塌", c_sources)

    check("B08", B, "第十轮：关键词外置 / 生成物本地只读",
          "blocklist 非空、.gitignore 覆盖、local_readonly 在位",
          lambda: (len([l for l in text("blocklist.txt").splitlines()
                        if l.strip() and not l.strip().startswith("#")]) >= 10
                   and all(x in text(".gitignore") for x in
                           ("/tvbox*.json", "/site/", "/reports/", "/history/", "/archive/"))
                   and os.path.exists(path("scripts/local_readonly.py")),
                   "blocklist / .gitignore / local_readonly.py 三项齐全"))

    def c_assert_cov():
        src = text("scripts/assertions.py")
        missing = [k for k in REQUIRED_ASSERTIONS if ('record("%s"' % k) not in src]
        return not missing, ("缺少断言：%s" % ", ".join(missing) if missing else
                             "%d 条关键硬断言全部在位" % len(REQUIRED_ASSERTIONS))

    check("B09", B, "各轮修复必须有硬断言守着，不能只靠文档",
          "assertions.py 覆盖全部关键回归防线", c_assert_cov)

    def c_assert_result():
        # 断言结果按环境分流：CI 读 reports/assertions.json（入库），本地读
        # _assertions.json。硬编码下划线文件会让 CI 里 B10 恒失败。
        a = common.load_result("assertions", {}) or {}
        return (a.get("passed") is True,
                "passed=%s，%d 项检查，失败 %s" %
                (a.get("passed"), len(a.get("checks") or {}),
                 [x.get("name") for x in (a.get("failed") or [])][:3]))

    check("B10", B, "每日保险丝结果", "当日回归断言已通过", c_assert_result)

    def c_quality():
        q = load("quality.json", {}) or {}
        need = ("trend", "days_recorded", "assert_pass_streak", "sample_rates",
                "jar_fail_streak", "ext_fail_streak")
        miss = [k for k in need if k not in q]
        return not miss, ("缺少字段：%s" % ", ".join(miss) if miss else
                          "多日历史 / 连败 / 抽样基线字段齐全（%d 天）" % (q.get("days_recorded") or 0))

    check("B11", B, "第十轮：误杀率 52% 证明单日判定不可信",
          "quality.json 保留多日历史与抽样基线", c_quality)

    def c_b12():
        # README 后来按用户要求精简成「链接 + 含义 + 免责声明」，不再罗列
        # jsDelivr 备用链接，原检查里「不要用不带 @main 的链接」那句已随章节
        # 一起移除 —— 继续硬找这句话会恒失败。改查它真正想守的风险：README
        # 主推 Pages、不出现无版本号的 jsDelivr 直链、健康页直链都带 @main。
        readme = text("README.md")
        hp = text("scripts/health.py")
        unversioned = [u for u in re.findall(
            r"https?://(?:cdn|fastly|gcore|testingcf)\.jsdelivr\.net/gh/[^\s`)\]]+",
            readme) if "@" not in u.split("/gh/", 1)[-1].split("/", 1)[0]]
        ok = ("dimogu.github.io/tvbox-merged/" in readme
              and "@main/%s" in hp
              and not unversioned)
        return ok, ("README 主推 Pages、无无版本号 CDN 直链；健康页直链均带 @main"
                    if ok else "README/健康页口径不一致：Pages=%s, @main=%s, 无版本CDN=%s"
                    % ("dimogu.github.io/tvbox-merged/" in readme,
                       "@main/%s" in hp, unversioned[:2]))

    check("B12", B, "第九轮：README 要求 Pages 主线路；健康页给无版本 CDN 链接",
          "README 主线路 Pages / health.py 直链带 @main", c_b12)

    def c_html():
        h = text(os.path.join("site", "index.html"))
        if h:
            op, cl = h.count("<table>"), h.count("</table>")
            return op == cl, ("生成页 %d 个 <table> / %d 个 </table>%s" %
                              (op, cl, "（闭合平衡）" if op == cl else "（结构破损）"))
        # CI 里本步骤跑在 health.py 之前，页面还没生成。改做源码静态检查：
        # 每个 p.append("</table>") 往上找最近的、缩进更小的块头，若是 for
        # 就说明闭合写进了循环体（本轮那个 bug 的形态），if 块内则合法。
        lines = text("scripts/health.py").splitlines()
        bad, closes = [], 0
        for i, line in enumerate(lines):
            if 'p.append("</table>")' not in line:
                continue
            closes += 1
            indent = len(line) - len(line.lstrip())
            # 往上找最近的、缩进更小的 for 块头；再看这个块到闭合行之间
            # 有没有缩进 <= for 的代码行（有 = 块已结束）。纯注释行要跳过，
            # 否则紧挨闭合标签的注释会被误当成父块，把 bug 漏过去。
            for j in range(i - 1, -1, -1):
                prev = lines[j]
                stripped = prev.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                pi = len(prev) - len(prev.lstrip())
                if pi < indent and re.match(r"\s*for\b", prev):
                    ended = any(
                        ln.strip() and not ln.lstrip().startswith("#")
                        and (len(ln) - len(ln.lstrip())) <= pi
                        for ln in lines[j + 1:i]
                    )
                    if not ended:
                        bad.append("health.py:%d 的 </table> 落在 L%d 的 for 循环体内"
                                   % (i + 1, j + 1))
                    break
        return (not bad and closes > 0,
                ("源码静态检查：%d 个表格闭合均在循环外" % closes) if not bad else
                "; ".join(bad))

    check("B13", B, "本轮：健康页 </table> 写在 for 循环体内（线上 33 闭合 / 5 开标签）",
          "生成的健康页 HTML 表格标签平衡", c_html, critical=False)

    def c_compile():
        scripts = sorted(f for f in os.listdir(path("scripts")) if f.endswith(".py"))
        errs = []
        for f in scripts:
            try:
                py_compile.compile(path(os.path.join("scripts", f)), doraise=True)
            except Exception as e:
                errs.append("%s:%s" % (f, str(e)[:60]))
        return not errs, ("%d 个脚本全部编译通过" % len(scripts) if not errs else
                          "; ".join(errs[:3]))

    check("B14", B, "每轮交付前的基础检查", "全部 scripts/*.py 可编译", c_compile)

    def c_pointer():
        s = load("stable.json", {}) or {}
        tag, commit = s.get("tag", ""), s.get("commit", "")
        if not tag:
            return None, "尚无稳定版指针"
        try:
            r = subprocess.run(["git", "rev-list", "-n", "1", tag], cwd=ROOT,
                               capture_output=True, text=True, timeout=15)
            actual = (r.stdout or "").strip()
        except Exception:
            actual = ""
        return (bool(actual) and (not commit or actual == commit),
                "tag=%s / 指针 commit=%s / tag 实际=%s" %
                (tag, (commit or "—")[:10], (actual or "—")[:10]))

    check("B15", B, "本轮：tag 复用时指针 commit 写成当前 HEAD、与不可变 tag 不一致",
          "stable.json 的 commit 指向 tag 实际提交", c_pointer)

    def c_polish_abort():
        src = text("scripts/polish.py")
        i = src.find("collections_aborted =")
        seg = src[i:i + 300] if i >= 0 else ""
        return (i >= 0 and "* 0.5" not in seg and "not parses" in seg,
                ("兜底只判「清空」，不会把 parse_cap 的配额截断当异常回退"
                 if i >= 0 and "* 0.5" not in seg else
                 "兜底仍在用半量阈值，会把 285→80 的正常截断误判成异常"))

    check("B16", B, "本轮：lives/parses 半量兜底误伤设计内解析截断（已被断言拦下）",
          "polish 的集合兜底只在被清空时触发", c_polish_abort)

    # ---------- B17-B21：第十一轮复核修复的源码级防线 ----------

    def c_upstream_guard():
        b = text("scripts/build.py")
        return ("isinstance(cfg, dict)" in b and "common.safe_url(url)" in b,
                "非 dict JSON 跳过该源 + fetch 先 percent-encode，单源坏数据不再拖垮整天构建")

    check("B17", B, "本轮：上游改版返回 JSON 数组让整个每日构建崩溃",
          "build.py 对非对象 JSON 与非法 URL 有兜底", c_upstream_guard)

    def c_media_check():
        pp = text("scripts/play_probe.py")
        i = pp.find("looks_media =")
        seg = pp[i:i + 400] if i >= 0 else ""
        return (i >= 0 and "<html" in seg and "<!doctype" in seg and
                "or len(body) > 0)" not in seg,
                "魔数白名单 + HTML 黑名单，不再「任何非空 200 都算媒体」")

    check("B18", B, "本轮：`or len(body) > 0` 让可播率判定完全失效（虚高）",
          "play_probe 媒体判定排除 HTML", c_media_check)

    def c_revive_guard():
        rc = text("scripts/recheck.py")
        ok = ("内容非空(非HTML)" in rc and '"<html" not in head' in rc)
        return ok, ("复活兜底排除 HTML 错误页，死源能攒够连败被淘汰" if ok
                    else "recheck 的 HTTP200 兜底未排除 HTML，回退到旧行为")

    check("B19", B, "本轮：反爬源 200+HTML 被当成「复活」，死源永不淘汰",
          "recheck 复活判定剔掉 HTML 页面", c_revive_guard)

    def c_serve_guard():
        sv = text("scripts/serve.py")
        return ("if etag and self.headers.get" in sv
                and "tvbox-merged@main\"" in sv
                and all(x in sv for x in ("tvbox-quality.json", "tvbox-cn-fast.json",
                                          "tvbox-stable7.json")),
                "304 判定有 etag 前置 / 默认上游带 @main / FILES 含三个场景版")

    check("B20", B, "本轮：serve.py 无 etag 时普通 GET 也回 304、默认上游无 @main、缺三个场景版",
          "自托管入口三处修复在位", c_serve_guard)

    def c_streak_dates():
        hp = text("scripts/history.py")
        yml = text(".github/workflows/update.yml")
        return ("fromisoformat" in hp and "git pull --rebase origin main" in yml,
                "连胜按日历日连续累计；CI push 前先 rebase，两个工作流不再互拒")

    check("B21", B, "本轮：断言失败的缺快照日被跳过、stable 3 天门槛可被绕过；两工作流 push 互撞",
          "history 连胜日期连续性 + workflow push 前 rebase", c_streak_dates)

    def c_mirror_watch():
        pc = text("scripts/purge_cdn.py")
        h = text("scripts/health.py")
        return ("MIRROR_HOSTS" in pc and "gcore.jsdelivr.net/gh/%s@main" in h,
                "purge 记录 fastly/gcore 滞后；健康页自检覆盖 gcore 线路")

    check("B22", B, "本轮：README 推荐的 gcore / 健康页推荐的 fastly 是 12h 缓存盲区",
          "推荐镜像滞后可被发现", c_mirror_watch)

    def c_results_committed():
        yml = text(".github/workflows/update.yml")
        cm = text("scripts/common.py")
        h = text("scripts/health.py")
        need = ("reports/assertions.json", "reports/regression.json",
                "reports/build-status.json")
        # health.py 用 os.path.join("reports", name)，只有文件名没有目录前缀
        short = ("assertions.json", "regression.json", "build-status.json")
        ok = (all(x in yml for x in need)
              and "def load_result" in cm and "def result_path" in cm
              and "--json-output" in text("scripts/regression.py")
              and "_regression-local.md" in text(".gitignore")
              and all(x in h for x in short))
        return ok, ("当日断言 / 回归 / 构建状态三份结论入库并进 Pages；"
                    "本地试跑走 _*.json 自动隔离" if ok else "结果文件入库链路有缺口")

    check("B23", B, "本轮：_assertions.json / _regression.json 被忽略，外部无法核对当日结论；"
          "本地试跑还会覆盖出过时快照",
          "每日结论入库 + 本地试跑隔离", c_results_committed)

    def c_no_stale_products():
        yml = text(".github/workflows/update.yml")
        bs = text("scripts/build_status.py")
        hp = text("scripts/history.py")
        sp = text("scripts/stable.py")
        h = text("scripts/health.py")
        ok = ("清除上一版派生配置" in yml
              and "rm -f tvbox-home.json" in yml
              and all(("id: %s" % s) in yml for s in
                      ("home_probe", "recheck", "play_probe", "rank_home",
                       "variants", "jar_health", "regression"))
              and "def collect" in bs and "degraded" in bs
              and "load_build_status" in hp and "degraded" in hp
              and "load_build_status" in sp and "降级版不推进" in sp
              and "本轮是降级版" in h)
        return ok, ("派生配置构建前清除（生成失败硬阻断）；失败步骤入库可见，"
                    "降级日不推进稳定版且健康页告警" if ok else "探测失败防线有缺口")

    check("B24", B, "本轮：探测失败不阻断 → 上一版派生配置被当作今日成果发布成混搭；"
          "workflow 全绿但健康页缺数据",
          "探测失败不沿用旧产物 + 降级可见", c_no_stale_products)

    # ---------- C. 线上线路（可选，需网络） ----------
    if online:
        opener = urllib.request.build_opener()
        if proxy:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler(
                {"http": proxy, "https": proxy}))
        opener.addheaders = [("User-Agent", "tvbox-regression/1.0")]

        def net(f, url):
            def run():
                try:
                    with opener.open(url, timeout=25) as r:
                        body = r.read()
                    local = open(path(f), "rb").read()
                    same = hashlib.sha256(body).digest() == hashlib.sha256(local).digest()
                    n = len(json.loads(body).get("sites", []))
                    return same, "%s：%s / %d 站点" % ("一致" if same else "滞后",
                                                     hashlib.sha256(body).hexdigest()[:8], n)
                except Exception as e:
                    return False, "取回失败：%s" % str(e)[:80]
            return run

        for i, (label, base) in enumerate((
                ("GitHub Pages 主线路", "https://dimogu.github.io/tvbox-merged/"),
                ("jsDelivr @main", "https://cdn.jsdelivr.net/gh/dimogu/tvbox-merged@main/"),
                ("jsDelivr 无版本号", "https://cdn.jsdelivr.net/gh/dimogu/tvbox-merged/"),
                # README 推荐 gcore、健康页推荐 fastly，此前两者都不在校验范围，
                # 12h 边缘缓存滞后的旧版（含对象型 style）会直接导入失败
                ("fastly @main（健康页推荐镜像）", "https://fastly.jsdelivr.net/gh/dimogu/tvbox-merged@main/"),
                ("gcore @main（README 推荐镜像）", "https://gcore.jsdelivr.net/gh/dimogu/tvbox-merged@main/")), 1):
            check("C%02d" % i, C, "第九轮：CDN 同时挂 4 个年代的旧版，导入直接报错",
                  "%s 与仓库内容一致（全量首页版）" % label,
                  net("tvbox-full-homefirst.json", base + "tvbox-full-homefirst.json"),
                  critical=(i == 1))

        def c_health_page():
            def run():
                try:
                    with opener.open("https://dimogu.github.io/tvbox-merged/", timeout=25) as r:
                        h = r.read().decode("utf-8", "replace")
                    return h.count("<table>") == h.count("</table>"), \
                        "HTTP 200 / %d 个表格且闭合平衡 / %d 字节" % (h.count("<table>"), len(h))
                except Exception as e:
                    return False, str(e)[:80]
            return run

        check("C04", C, "本轮：健康页表格结构破损", "线上健康页可访问且标签平衡",
              c_health_page(), critical=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--online", action="store_true", help="追加线上 Pages/CDN 一致性检查")
    ap.add_argument("--proxy", default="", help="本机网络需代理时填 http://127.0.0.1:7890")
    ap.add_argument("--output", default=None,
                    help="Markdown 报告路径；默认 CI 入库 / 本地隔离")
    ap.add_argument("--json-output", default=None,
                    help="结构化结果路径；默认 CI 入库 / 本地隔离")
    args = ap.parse_args()
    if args.output is None:
        args.output = ("reports/regression-latest.md" if common.IS_CI
                       else "_regression-local.md")
    if args.json_output is None:
        args.json_output = common.result_path("regression")

    cfgs, bad = all_configs()
    build_checks(cfgs, bad, args.online, args.proxy)

    ok = sum(1 for r in RESULTS if r["ok"] is True)
    fail = [r for r in RESULTS if r["ok"] is False]
    skip = [r for r in RESULTS if r["ok"] is None]
    hard_fail = [r for r in fail if r["critical"]]
    passed = not hard_fail

    out = {
        "date": common.today(), "env": common.env_info(),
        "passed": passed, "total": len(RESULTS), "ok": ok,
        "failed": len(fail), "skipped": len(skip),
        "hard_failed": [r["id"] for r in hard_fail],
        "checks": RESULTS,
    }
    common.write_json(args.json_output, out, skip_unchanged=False)

    lines = ["# 全历史问题回归复核 · %s" % common.today(), "",
             "把九轮迭代里真实出现过的故障逐条复核，确认修复仍在、防线没被绕过。",
             "默认离线，`--online` 追加线上线路比对；判定环境：%s。" %
             common.env_info()["env"], "",
             "**结果：%d 项检查，%d 项通过，%d 项未通过（其中阻断 %d 项），%d 项跳过 —— %s**" %
             (len(RESULTS), ok, len(fail), len(hard_fail), len(skip),
              "全部通过" if passed else "存在需处理项"), ""]
    for group in ("产物契约", "构建链路", "线上线路"):
        rows = [r for r in RESULTS if r["group"] == group]
        if not rows:
            continue
        lines += ["## %s" % group, "",
                  "| # | 历史出处 | 复核项 | 结果 | 证据 |", "|---|---|---|---|---|"]
        for r in rows:
            mark = "✅ 通过" if r["ok"] is True else ("❌ 未通过" if r["ok"] is False else "⏭ 跳过")
            lines.append("| %s | %s | %s | %s | %s |" %
                         (r["id"], r["origin"].replace("|", "\\|"),
                          r["title"].replace("|", "\\|"), mark,
                          r["detail"].replace("|", "\\|").replace("\n", " ")))
        lines.append("")
    if fail:
        lines += ["## 未通过明细", ""]
        for r in fail:
            lines += ["- **%s %s**（%s）：%s" % (r["id"], r["title"],
                                              "阻断" if r["critical"] else "提示", r["detail"])]
        lines.append("")
    lines += ["---", "",
              "本报告由 `scripts/regression.py` 生成，与 `assertions.py` 的每日保险丝互补："
              "断言只决定「今天能不能提交」，本报告回答「历史修过的东西有没有悄悄坏掉」。", ""]

    dest = path(args.output)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print("=== 全历史问题回归复核 ===")
    for r in RESULTS:
        print("  [%s] %-4s %s" % ("OK" if r["ok"] is True else
                                  ("FAIL" if r["ok"] is False else "SKIP"),
                                  r["id"], r["title"]))
    print("\n%d 项检查 / %d 通过 / %d 未通过（阻断 %d） -> %s" %
          (len(RESULTS), ok, len(fail), len(hard_fail),
           "全部通过" if passed else "存在需处理项"))
    print("报告：%s" % args.output)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
