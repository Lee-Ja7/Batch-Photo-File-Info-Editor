# -*- coding: utf-8 -*-
"""照片信息批量处理助手by清風 · 独立窗口程序入口（PyInstaller 打包目标）

双击 exe 即启动：后台线程跑本地服务（仅 127.0.0.1），
pywebview + Edge WebView2 承载原生独立窗口。无浏览器、无地址栏、无需 Python。
"""
from __future__ import annotations

import sys
import time
import threading
from pathlib import Path

TITLE = "照片信息批量处理助手by清風"
ICON_NAME = "app.ico"


def _icon_path() -> Path:
    """frozen 时从 _MEIPASS/assets 取图标，源码运行时从源码目录取。"""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "assets" / ICON_NAME
    return Path(__file__).resolve().parent / "assets" / ICON_NAME


def _apply_window_icon(icon: Path, timeout: float = 15.0) -> None:
    """pywebview(WinForms) 标题栏/任务栏默认不是 exe 图标，用 WM_SETICON 覆写。"""
    import ctypes

    user32 = ctypes.windll.user32
    IMAGE_ICON, LR_LOADFROMFILE = 1, 0x10
    WM_SETICON, ICON_SMALL, ICON_BIG = 0x0080, 0, 1
    sm_cxsm = user32.GetSystemMetrics(49)  # SM_CXSMICON
    sm_cx = user32.GetSystemMetrics(11)    # SM_CXICON
    hi_s = user32.LoadImageW(None, str(icon), IMAGE_ICON, sm_cxsm, sm_cxsm, LR_LOADFROMFILE)
    hi_b = user32.LoadImageW(None, str(icon), IMAGE_ICON, sm_cx, sm_cx, LR_LOADFROMFILE)
    if not hi_b:
        return
    deadline = time.time() + timeout
    while time.time() < deadline:
        hwnd = user32.FindWindowW(None, TITLE)
        if hwnd:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hi_s)
            user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hi_b)
            return
        time.sleep(0.2)


def main():
    import server
    import webview

    port = server.free_port(server.START_PORT)
    url = f"http://{server.HOST}:{port}/"
    httpd = server.ThreadingHTTPServer((server.HOST, port), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    icon = _icon_path()
    if icon.is_file():
        threading.Thread(target=_apply_window_icon, args=(icon,), daemon=True).start()

    webview.create_window(
        TITLE,
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
