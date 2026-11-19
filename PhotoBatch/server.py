# -*- coding: utf-8 -*-
"""照片信息批量处理助手by清風 · 本地服务

仅监听 127.0.0.1，提供静态页面与 JSON API；完全离线，不访问外网。
正式入口：app.py（pywebview + WebView2 原生窗口）→ 打包为「照片信息批量处理助手by清風.exe」。
本模块单独运行仅供开发调试（浏览器模式）。
"""
from __future__ import annotations

import io
import json
import os
import socket
import subprocess
import sys
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import core
import dialogs

# 打包成 exe 后 index.html 位于 _MEIPASS 资源目录
RES_DIR = Path(getattr(sys, "_MEIPASS", str(Path(__file__).resolve().parent)))
INDEX = RES_DIR / "index.html"
HOST = "127.0.0.1"
START_PORT = 8765

STATE = {"current": 0, "total": 0, "message": "", "running": False}

# 「关于」面板允许打开的外链域名
LINK_HOSTS = ("github.com", "photo.qingfengstudio.cn", "qingfengstudio.cn")


def free_port(start: int) -> int:
    for p in range(start, start + 30):
        with socket.socket() as s:
            try:
                s.bind((HOST, p))
                return p
            except OSError:
                continue
    with socket.socket() as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


def make_thumbnail(path: str, size: int = 144) -> bytes:
    from PIL import Image
    with Image.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((size, size))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=76)
        return buf.getvalue()


class Handler(BaseHTTPRequestHandler):
    server_version = "PhotoInfoBatchByQingfeng/1.0"

    def log_message(self, *_args):
        pass

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        try:
            if u.path in ("/", "/index.html"):
                self._send(200, INDEX.read_bytes(), "text/html; charset=utf-8")
            elif u.path == "/api/caps":
                self._json(core.scan([])["capabilities"])
            elif u.path == "/api/journals":
                self._json({"items": core.list_journals()})
            elif u.path == "/api/progress":
                self._json(dict(STATE))
            elif u.path == "/api/thumbnail":
                p = (q.get("path") or [""])[0]
                if not p or not os.path.isfile(p) or Path(p).suffix.lower() not in core.DEFAULT_EXTS:
                    self._send(404, b"", "image/jpeg")
                    return
                self._send(200, make_thumbnail(p), "image/jpeg")
            else:
                self._send(404, b"Not found", "text/plain; charset=utf-8")
        except Exception as e:
            self._json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 500)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        body = self._body()
        try:
            if u.path == "/api/scan":
                self._json(core.scan(body.get("paths", []),
                                     recursive=bool(body.get("recursive", True)),
                                     exts=body.get("exts") or None))
            elif u.path == "/api/preview":
                self._json(core.build_changeset(body.get("items", []), body.get("rules")))
            elif u.path == "/api/execute":
                changes = body.get("changes", [])
                STATE.update({"running": True, "current": 0,
                              "total": len(changes), "message": "准备中"})

                def progress(i, total, msg):
                    STATE.update({"current": i, "total": total, "message": msg})

                result = core.execute(changes, progress_cb=progress)
                STATE.update({"running": False, "message": "完成",
                              "current": STATE["total"]})
                self._json(result)
            elif u.path == "/api/undo":
                self._json(core.undo(body.get("id", "")))
            elif u.path == "/api/pick":
                mode = "folder" if body.get("mode", "folder") == "folder" else "files"
                self._json({"ok": True, "paths": dialogs.pick(mode)})
            elif u.path == "/api/open":
                p = body.get("path", "")
                if p and os.path.exists(p):
                    if os.path.isdir(p):
                        os.startfile(p)
                    else:
                        subprocess.Popen(["explorer", "/select,", os.path.normpath(p)])
                self._json({"ok": True})
            elif u.path == "/api/link":
                # 仅允许「关于」面板里的官方地址，避免被当作任意 URL 打开器
                url = str(body.get("url", "")).strip()
                parsed = urllib.parse.urlparse(url)
                host = (parsed.hostname or "").lower()
                ok = parsed.scheme in ("http", "https") and any(
                    host == h or host.endswith("." + h) for h in LINK_HOSTS
                )
                if not ok:
                    self._json({"ok": False, "error": "链接不在白名单内"}, 400)
                    return
                webbrowser.open(url)
                self._json({"ok": True})
            else:
                self._send(404, b"Not found", "text/plain; charset=utf-8")
        except Exception as e:
            STATE["running"] = False
            self._json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 500)


def open_app_window(url: str) -> bool:
    edges = [
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    ]
    for exe in edges:
        if exe and os.path.isfile(exe):
            try:
                subprocess.Popen([exe, f"--app={url}", "--no-first-run",
                                  "--no-default-browser-check",
                                  "--window-size=1320,880"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True
            except OSError:
                continue
    return False


def main():
    port = free_port(START_PORT)
    url = f"http://{HOST}:{port}/"
    server = ThreadingHTTPServer((HOST, port), Handler)
    print("=" * 52)
    print("  照片信息批量处理助手by清風 · 已启动")
    print(f"  本地地址：{url}")
    print("  关闭本窗口即退出程序")
    print("=" * 52)

    args = sys.argv[1:]
    if "--no-open" in args:
        print("  已跳过自动打开窗口，可手动访问上面的地址")
    elif "--window" in args:
        try:
            import webview as wv
            threading.Thread(target=server.serve_forever, daemon=True).start()
            wv.create_window("照片信息批量处理助手by清風", url, width=1320, height=880,
                             min_size=(1000, 680))
            wv.start()
            return
        except Exception as e:
            print(f"  窗口模式不可用（{e}），已回退应用窗口")
            if not open_app_window(url):
                webbrowser.open(url)
    elif "--browser" in args:
        webbrowser.open(url)
    elif not open_app_window(url):
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出")


if __name__ == "__main__":
    main()
