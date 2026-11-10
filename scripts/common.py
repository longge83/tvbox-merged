#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TVBox 聚合项目的公共基础模块（仅 Python 标准库）。

统一三件容易出分歧的东西：
  1. 站点身份签名 —— 历史、排序、断言、可播率必须指向同一个站点；
  2. 抓取行为 —— UA、超时、代理、URL 编码、字节上限一致；
  3. 判定环境 —— 结果里必须写明是在云端还是本机跑的，避免本机网络误判被当结论。
"""
import hashlib
import json
import os
import re
import ssl
import threading
import time
import urllib.request
from urllib.parse import quote, urlsplit, urlunsplit, parse_qsl

IS_CI = os.environ.get("GITHUB_ACTIONS") == "true"
ENV = "github-actions" if IS_CI else "local"
PROXY = os.environ.get("HOME_PROXY", "")
GH_HOSTS = ("raw.githubusercontent.com", "github.com", "gist.githubusercontent.com",
            "gitlab.com", "jihulab.com", "agit.ai", "gitee.com")

# ── 配置托管地黑名单（唯一口径，build / variants / polish / assertions 共用）──
# 只检查 api / jar / ext 三条字段里的「域名」，判断这份配置住在哪台服务器上，
# 与片源内容、播放什么片子完全无关（netflix.com 之类的影视站一律不在此列）。
#
# 收进来的标准：有实测命中的「境外代码托管」与「GitHub 反代」两类。
# 没有任何站点命中的平台（vercel / netlify / pages.dev / herokuapp 等）不收 ——
# 它们跟影视无关、又容易误伤正常站点，等真出现再按实测加。
HOSTING_BASE = (
    "githubusercontent.com",   # 含 raw.githubusercontent.com / gist.
    "github.com",
    "github.io",
    "gitlab.com",
    "gitlab.io",
    "bitbucket.org",
    "sourceforge.net",
)
# GitHub 反代：域名换来换去但都带这些字样（gh-proxy.com / ghproxy.net / moeyy.xyz …）
HOSTING_KEYS = ("ghproxy", "gh-proxy", "gitmirror", "fastgit", "kgithub", "moeyy",
                "repl.co")  # repl.co 是目前唯一有实测命中的非 GitHub 平台

HOST_RX = re.compile(r"https?://([^/\s\"'<>:,#]+)")

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

UA_OKHTTP = {"User-Agent": "okhttp/4.9.0", "Accept": "*/*"}
# 真实 TVBox 客户端常见 UA，用于复核被判死的站点（很多源挡非客户端 UA）
UA_TVBOX = [
    {"User-Agent": "okhttp/3.15", "Accept": "*/*"},
    {"User-Agent": "Dalvik/2.1.0 (Linux; U; Android 11; Redmi Note 8 Build/RKQ1.200826.002)",
     "Accept": "*/*"},
    {"User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Version/4.0 Chrome/120 Mobile Safari/537.36",
     "Accept": "*/*"},
]

_DIRECT = urllib.request.build_opener(urllib.request.HTTPSHandler(context=CTX))
_VIA_PROXY = (urllib.request.build_opener(
    urllib.request.HTTPSHandler(context=CTX),
    urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}),
) if PROXY else _DIRECT)


def hosts_of(obj, fields=("api", "jar", "ext")):
    """把 api / jar / ext 里出现的所有域名抽出来（ext 可能是 dict）。"""
    out = set()
    for field in fields:
        v = obj.get(field)
        if isinstance(v, (dict, list)):
            v = json.dumps(v, ensure_ascii=False)
        for h in HOST_RX.findall(str(v or "")):
            out.add(h.lower())
    return out


def hosting_host(obj, deep=False):
    """配置托管地是否属于「国内不稳」的那类，命中返回域名，否则 None。

    deep=False 只认代码托管平台（GitHub/GitLab 等）；
    deep=True 连 GitHub 反代一起算。注意只看配置住在哪，不看播什么。
    """
    for h in hosts_of(obj):
        if any(h == d or h.endswith("." + d) for d in HOSTING_BASE):
            return h
        if deep and any(k in h for k in HOSTING_KEYS):
            return h
    return None


def env_info():
    """写进每份报告，避免「本机跑出来的结果」被当成线上结论。"""
    return {
        "env": ENV,
        "runner": os.environ.get("GITHUB_RUN_ID", ""),
        "proxy": bool(PROXY),
        "generated": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
    }


def today():
    return time.strftime("%Y-%m-%d", time.gmtime())


def safe_url(url):
    """GitHub raw 上的中文文件名必须 percent-encode，否则 urllib 直接抛错。"""
    try:
        p = urlsplit(url)
        return urlunsplit((p.scheme, p.netloc,
                           quote(p.path, safe="/%:@&=+$,~!*'()[]"),
                           p.query, p.fragment))
    except Exception:
        return url


_CACHE = {}
_LOCK = threading.Lock()


def fetch(url, timeout=8, limit_bytes=200000, headers=None, method="GET",
          use_cache=True, via_proxy=None):
    """返回 (status:int, body:bytes)。失败时 status 为 0 或 HTTP 错误码，body 是错误文本。

    use_cache 对同一 URL 只请求一次 —— 大量站点共享 jar/ext，重复请求会拖垮构建。
    """
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return 0, b"invalid url"
    # TVBox 的 jar / ext 常写成 "url;md5;hash"，客户端会剥离 md5 段再请求。
    # 探测时不剥离会把大量有效 jar 判成 404（实测抽样 4/4 全误杀）。
    if ";md5;" in url:
        url = url.split(";", 1)[0]
    url = safe_url(url)
    ck = (url, method, json.dumps(headers or {}, sort_keys=True))
    if use_cache:
        with _LOCK:
            if ck in _CACHE:
                return _CACHE[ck]
    if via_proxy is None:
        via_proxy = any(h in url for h in GH_HOSTS)
    opener = _VIA_PROXY if (via_proxy and PROXY) else _DIRECT
    hdrs = dict(headers or UA_OKHTTP)
    try:
        req = urllib.request.Request(url, headers=hdrs, method=method)
        with opener.open(req, timeout=timeout) as r:
            result = (r.status, r.read(limit_bytes))
    except Exception as e:
        result = (getattr(e, "code", 0) or 0, str(e).encode("utf-8", "ignore"))
    if use_cache:
        with _LOCK:
            _CACHE[ck] = result
    return result


def json_of(raw):
    try:
        return json.loads(raw.decode("utf-8", "ignore"))
    except Exception:
        return None


def canon_ext(v):
    """把 ext 归一成「同一份内容只有一种写法」。

    上游有的源把 ext 写成对象、有的写成 JSON 字符串，两种写法语义相同。
    签名里若不归一，polish 把对象改成字符串的那天，全站历史签名会一起断掉。
    归一后：对象 / JSON 字符串 → 同一份紧凑 JSON 文本；其余（URL、普通串）原样。
    """
    if isinstance(v, (dict, list)):
        try:
            return json.dumps(v, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":"))
        except Exception:
            return str(v)
    if isinstance(v, str) and v.strip()[:1] in ("{", "["):
        try:
            return json.dumps(json.loads(v), ensure_ascii=False, sort_keys=True,
                              separators=(",", ":"))
        except Exception:
            return v
    return v


# ── 内容指向归一（去重的唯一口径）────────────────────────────────────────
# 同一份配置，上游会用完全不同的链接发布：
#   raw.githubusercontent.com/o/r/main/x.json
#   https://gh-proxy.com/https://raw.githubusercontent.com/o/r/main/x.json
#   https://cdn.jsdelivr.net/gh/o/r@main/x.json
#   https://github.com/o/r/blob/main/x.json
# 再叠加 www. / http↔https / 默认端口 / 尾斜杠 / 参数顺序 / 缓存参数。
# 只按字面 URL 去重会把它们当成四个不同源，名称再一换皮就彻底认不出来。
# 这里统一还原成「内容指向」，与名称、与发布链接的写法都无关。
_GH_JSDELIVR = re.compile(r"^(?:cdn|fastly|gcore|testingcf|img)\.jsdelivr\.net$", re.I)
_GH_PROXY = re.compile(r"gh-?proxy|ghproxy|gitmirror|fastgit|kgithub|moeyy", re.I)
_CACHE_QS = {"_", "t", "ts", "timestamp", "rnd", "random", "nonce", "cachebust", "cb"}
_URL_IN_TEXT = re.compile(r"https?://[^\s\"'<>,\\]+")


def _gh_content(host, path):
    """GitHub 系链接 → gh:owner/repo/branch/path；不是则 None。"""
    parts = [x for x in path.split("/") if x]
    if host == "raw.githubusercontent.com" and len(parts) >= 4:
        owner, repo, branch, rest = parts[0], parts[1], parts[2], "/".join(parts[3:])
    elif host == "github.com" and len(parts) >= 5 and parts[2] in ("blob", "raw"):
        owner, repo, branch, rest = parts[0], parts[1], parts[3], "/".join(parts[4:])
    elif _GH_JSDELIVR.match(host) and len(parts) >= 4 and parts[0] == "gh":
        owner = parts[1]
        repo, sep, branch = parts[2].partition("@")
        rest = "/".join(parts[3:])
        # 没写版本号时无法确定分支，用 @latest 单独成键，不与显式分支混并
        if not sep:
            branch = "@latest"
    else:
        return None
    if not owner or not repo or not rest:
        return None
    return "gh:%s/%s/%s/%s" % (owner.lower(), repo.lower(), branch.lower(),
                               rest.rstrip("/").lower())


def content_url(v):
    """把一个 URL / ext 值归一成「指向同一份内容」的字符串键。

    非 URL 值（内嵌 JSON、加密串）走 canon_ext 紧凑化；内嵌 JSON 里出现的
    链接也一并归一，这样 {"json":"https://gh-proxy.org/https://raw.../a.json"}
    与 {"json":"https://raw.../a.json"} 会落到同一个键。
    """
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        v = canon_ext(v)
    s = str(v).strip()
    if not s:
        return ""
    # ;md5;hash 只在 URL 后出现，不能切到内嵌 JSON 中间的分号
    if s.lower().startswith(("http://", "https://")):
        s = s.split(";", 1)[0].strip()
    if not s.lower().startswith(("http://", "https://")):
        text = canon_ext(s).strip()
        # 内嵌 JSON 里的链接也按同一口径归一（不含分号/引号，正则已止于分隔符）
        return _URL_IN_TEXT.sub(lambda m: content_url(m.group(0)), text)
    p = urlsplit(s)
    host = (p.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    # GitHub 反代：路径里整条塞着真实 URL，先剥壳再还原
    if _GH_PROXY.search(host):
        m = re.match(r"^/(?:https?:)?/{0,2}(.+)$", p.path)
        if m:
            inner = m.group(1)
            if not inner.lower().startswith(("http://", "https://")):
                inner = "https://" + inner
            return content_url(inner)
    gh = _gh_content(host, p.path)
    if gh:
        return gh
    try:
        port = p.port
    except ValueError:
        port = None
    if port in (80, 443, None):
        port = ""
    # 必须保留空值参数：解析通道的 "?url=" / "?v=" 是 TVBox 拼接播放地址的
    # 占位符，丢掉会把两个完全不同的解析接口判成同一条。
    q = tuple(sorted((k, val) for k, val in
                     parse_qsl(p.query, keep_blank_values=True)
                     if k.lower() not in _CACHE_QS))
    return "url:%s" % json.dumps([host, port or "", p.path.rstrip("/"), q],
                                 ensure_ascii=False, sort_keys=True)


def content_key(site):
    """站点「内容指向」去重键 —— 与名称无关，只看实际拉哪份配置 / 哪个接口。

    csp_* 站点的内容完全由 ext（配置文件）+ jar（spider）+ api（解析器）决定，
    名称只是显示用的皮；把名称算进键，既会漏掉换皮重复，也会误杀同名不同配置。
    http 站点同样要带上 ext / jar：一批 drpy2 站点共用同一个 drpy2.min.js 当
    api，靠 ext 区分各自的配置；只看 api 会把它们全删到只剩一个。
    ext / jar 都为空的 guard 类站点没有配置内容，此时补 type / playerType，
    避免把两个行为不同的通道合并成一条。
    """
    api = str(site.get("api") or "").strip()
    ext = content_url(site.get("ext"))
    jar = content_url(site.get("jar"))
    if api.lower().startswith("csp_"):
        key = ["csp", api.lower(), ext, jar]
        if not ext and not jar:
            key += ["behavior", str(site.get("type") or 0),
                    str(site.get("playerType") or "")]
        return tuple(key)
    return ("api", content_url(api), ext, jar)


def identity(site):
    """站点身份签名。不用整个对象（上游字段微调会失配）。

    也**不用 key**：key 是客户端的唯一主键，重复时 polished 会给它加后缀去重，
    如果把 key 算进身份，那批站点每被修一次历史就断一次。同名的两条不同源，
    靠 api / jar / ext 已经能区分。
    """
    return json.dumps(
        [site.get("name"), site.get("api"),
         site.get("jar"), canon_ext(site.get("ext"))],
        ensure_ascii=False, sort_keys=True,
    )


def sig(site):
    """身份签名的短哈希，用于历史文件里做 key，避免每天存 2000 条长字符串。"""
    return hashlib.sha1(identity(site).encode("utf-8", "ignore")).hexdigest()[:16]


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _strip_meta(obj):
    """比较内容时忽略易变的时间戳/统计字段。"""
    a = dict(obj) if isinstance(obj, dict) else obj
    meta = dict(a.pop("_meta", {}) or {})
    for k in ("updated", "generated", "probe_env"):
        meta.pop(k, None)
    if meta:
        a["_meta"] = meta
    return json.dumps(a, ensure_ascii=False, sort_keys=True)


def write_json(path, obj, skip_unchanged=True):
    """原子写 + 内容未变不落盘（避免每天产生无意义提交）。返回是否写入。"""
    if skip_unchanged and os.path.exists(path):
        try:
            old = load_json(path)
            if old is not None and _strip_meta(old) == _strip_meta(obj):
                return False
        except Exception:
            pass
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return True


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def parse_vod_list(j):
    """从苹果 CMS 返回体里取出影片列表（兼容 list / list.list / list.data）。"""
    if not isinstance(j, dict):
        return []
    lst = j.get("list")
    if isinstance(lst, dict):
        lst = lst.get("list") or lst.get("data")
    if not isinstance(lst, list):
        return []
    return [x for x in lst if isinstance(x, dict) and
            (x.get("vod_name") or x.get("vod_id"))]


def extract_play_urls(item):
    """从详情条目里抽出播放地址（vod_play_url / vod_play_from 等多格式）。"""
    urls = []
    if not isinstance(item, dict):
        return urls
    for key in ("vod_play_url", "vod_url", "play_url", "vod_play_from"):
        v = item.get(key)
        if not isinstance(v, str) or not v:
            continue
        for seg in v.replace("$$$", "#").split("#"):
            parts = seg.split("$")
            cand = parts[-1] if parts else ""
            if cand.startswith(("http://", "https://")):
                urls.append(cand)
    return urls[:3]
