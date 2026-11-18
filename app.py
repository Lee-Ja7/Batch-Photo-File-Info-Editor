# -*- coding: utf-8 -*-
"""照片信息批量处理助手by清風 · 独立窗口程序入口（PyInstaller 打包目标）

双击 exe 即启动：后台线程跑本地服务（仅 127.0.0.1），
pywebview + Edge WebView2 承载原生独立窗口。无浏览器、无地址栏、无需 Python。
"""
from __future__ import annotations

import threading


def main():
    import server
    import webview

    port = server.free_port(server.START_PORT)
    url = f"http://{server.HOST}:{port}/"
    httpd = server.ThreadingHTTPServer((server.HOST, port), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    webview.create_window(
        "照片信息批量处理助手by清風",
        url,
        width=1320,
        height=880,
        min_size=(1000, 680),
        background_color="#F2F5FA",
    )
    webview.start(gui="edgechromium")
    try:
        httpd.shutdown()
    except Exception:
        pass


if __name__ == "__main__":
    main()
