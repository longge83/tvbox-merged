#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自托管入口（合规 / 内网备用）：在 NAS、软路由或家里一台常开的机器上跑，
局域网设备填 http://<这台机器>:8080/tvbox-full-homefirst.json 即可。

作用：
  - 不依赖公开 CDN，避免公开仓库/镜像被污染或被投诉下架后全家设备同时失效；
  - 定期从上游拉最新版缓存到本地，客户端只访问内网，速度快且稳定；
  - 上游拉不到时继续用上一次成功缓存，绝不返回空配置；
  - 提供 / 状态页、/health JSON、ETag 与 CORS。

仅依赖 Python 标准库。用法：
  python scripts/serve.py --port 8080 --refresh 21600
  python scripts/serve.py --upstream https://cdn.jsdelivr.net/gh/dimogu/tvbox-merged
"""
import argparse
import hashlib
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common

FILES = [
    "tvbox-full-homefirst.json", "tvbox-lite-homefirst.json",
    "tvbox-network-homefirst.json", "tvbox-home.json",
    "tvbox.json", "tvbox-lite.json", "tvbox-network.json",
    "stable.json", "health.json",
]
STATE = {"last_refresh": None, "ok": 0, "failed": 0, "etags": {}, "upstream": ""}
CACHE_DIR = "serve-cache"


def refresh(base, timeout=20):
    os.makedirs(CACHE_DIR, exist_ok=True)
    ok = fail = 0
    for f in FILES:
        url = base.rstrip("/") + "/" + f
        st, raw = common.fetch(url, timeout=timeout, limit_bytes=3000000,
                               use_cache=False)
        if st == 200 and len(raw) > 20:
            # JSON 合法性校验：上游偶尔返回 HTML 错误页，不能把坏内容写给客户端
            if f.endswith(".json") and common.json_of(raw) is None:
                fail += 1
                continue
            with open(os.path.join(CACHE_DIR, f), "wb") as out:
                out.write(raw)
            STATE["etags"][f] = hashlib.sha1(raw).hexdigest()[:16]
            ok += 1
        else:
            fail += 1
    STATE.update(last_refresh=time.strftime("%Y-%m-%d %H:%M:%S"),
                 ok=ok, failed=fail, upstream=base)
    print("[%s] 刷新完成：成功 %d / 失败 %d（失败项继续用旧缓存）" %
          (STATE["last_refresh"], ok, fail), flush=True)


def refresher(base, interval):
    while True:
        try:
            refresh(base)
        except Exception as e:
            print("刷新异常:", e, file=sys.stderr)
        time.sleep(interval)


def make_handler():
    class Handler(BaseHTTPRequestHandler):
        server_version = "TVBoxSelfHost/1.0"

        def log_message(self, fmt, *a):
            pass

        def _send(self, code, body, ctype="application/json; charset=utf-8", etag=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "public, max-age=300")
            if etag:
                self.send_header("ETag", '"%s"' % etag)
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_HEAD(self):
            self.do_GET(head=True)

        def do_GET(self, head=False):
            path = self.path.split("?", 1)[0].lstrip("/")
            if path in ("", "index.html"):
                rows = "".join("<li><a href='/%s'>%s</a> %s</li>" %
                               (f, f, "✓" if os.path.exists(os.path.join(CACHE_DIR, f))
                                else "（待拉取）") for f in FILES)
                html = ("<!DOCTYPE html><html lang='zh-CN'><meta charset='utf-8'>"
                        "<title>TVBox 自托管入口</title><body style='font:15px/1.7 sans-serif;"
                        "max-width:760px;margin:40px auto;padding:0 16px'>"
                        "<h1>TVBox 自托管入口</h1>"
                        "<p>把下面任意一条完整地址填进 TVBox「配置地址」：</p><ul>" + rows + "</ul>"
                        "<p>上游：<code>" + STATE["upstream"] + "</code> · 上次刷新："
                        + str(STATE["last_refresh"]) + " · 成功 " + str(STATE["ok"])
                        + " / 失败 " + str(STATE["failed"]) + "</p>"
                        "<p>局域网示例：<code>http://192.168.1.10:"
                        + str(self.server.server_port) + "/tvbox-lite-homefirst.json</code></p>"
                        "</body></html>").encode("utf-8")
                return self._send(200, html if not head else b"", "text/html; charset=utf-8")
            if path == "health":
                return self._send(200, json.dumps(STATE, ensure_ascii=False,
                                                  indent=2).encode("utf-8"))
            if path not in FILES:
                return self._send(404, b'{"error":"not found"}')
            fp = os.path.join(CACHE_DIR, path)
            if not os.path.exists(fp):
                return self._send(503, b'{"error":"cache not ready"}')
            etag = STATE["etags"].get(path)
            if self.headers.get("If-None-Match") == ('"%s"' % etag if etag else None):
                self.send_response(304)
                self.end_headers()
                return
            with open(fp, "rb") as f:
                body = f.read()
            self._send(200, body if not head else b"", etag=etag)
    return Handler


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--refresh", type=int, default=21600, help="刷新间隔（秒），默认 6 小时")
    ap.add_argument("--upstream", default="https://cdn.jsdelivr.net/gh/dimogu/tvbox-merged")
    args = ap.parse_args()
    STATE["upstream"] = args.upstream
    refresh(args.upstream)
    if STATE["ok"] == 0:
        print("上游一份都没拉到，仍启动服务（可手动把配置放进 %s/）" % CACHE_DIR,
              file=sys.stderr)
    threading.Thread(target=refresher, args=(args.upstream, args.refresh),
                     daemon=True).start()
    srv = ThreadingHTTPServer((args.host, args.port), make_handler())
    print("自托管入口已启动： http://%s:%d/  （Ctrl+C 停止）" % (args.host, args.port),
          flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    return 0


if __name__ == "__main__":
    sys.exit(main())
