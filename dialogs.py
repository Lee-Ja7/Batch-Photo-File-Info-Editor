# -*- coding: utf-8 -*-
"""Windows 原生文件 / 文件夹选择对话框。

纯 ctypes 调用 Win32 API，不依赖 tkinter（managed Python 未带 tcl/tk）、
不依赖 pywebview 的 UI 线程调度，可在任意后台线程安全弹出模态对话框。
"""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

BIF_RETURNONLYFSDIRS = 0x0001
BIF_EDITBOX = 0x0010
BIF_NEWDIALOGSTYLE = 0x0040

OFN_READONLY = 0x00000001
OFN_HIDEREADONLY = 0x00000004
OFN_FILEMUSTEXIST = 0x00001000
OFN_ALLOWMULTISELECT = 0x00000200
OFN_EXPLORER = 0x00080000

COINIT_APARTMENTTHREADED = 0x0002

IMAGE_FILTER = (
    "图片文件\0"
    "*.jpg;*.jpeg;*.png;*.tif;*.tiff;*.webp;*.bmp;*.gif;"
    "*.heic;*.heif;*.avif;*.cr2;*.nef;*.arw;*.dng;*.raw;*.orf;*.rw2\0"
    "所有文件\0*.*\0\0"
)


class BROWSEINFO(ctypes.Structure):
    _fields_ = [
        ("hwndOwner", wintypes.HWND),
        ("pidlRoot", ctypes.c_void_p),
        ("pszDisplayName", wintypes.LPWSTR),
        ("lpszTitle", wintypes.LPWSTR),
        ("ulFlags", wintypes.UINT),
        ("lpfn", ctypes.c_void_p),
        ("lParam", wintypes.LPARAM),
        ("iImage", ctypes.c_int),
    ]


class OPENFILENAME(ctypes.Structure):
    _fields_ = [
        ("lStructSize", wintypes.DWORD),
        ("hwndOwner", wintypes.HWND),
        ("hInstance", wintypes.HINSTANCE),
        ("lpstrFilter", wintypes.LPWSTR),
        ("lpstrCustomFilter", wintypes.LPWSTR),
        ("nMaxCustFilter", wintypes.DWORD),
        ("nFilterIndex", wintypes.DWORD),
        ("lpstrFile", wintypes.LPWSTR),
        ("nMaxFile", wintypes.DWORD),
        ("lpstrFileTitle", wintypes.LPWSTR),
        ("nMaxFileTitle", wintypes.DWORD),
        ("lpstrInitialDir", wintypes.LPWSTR),
        ("lpstrTitle", wintypes.LPWSTR),
        ("Flags", wintypes.DWORD),
        ("nFileOffset", wintypes.WORD),
        ("nFileExtension", wintypes.WORD),
        ("lpstrDefExt", wintypes.LPWSTR),
        ("lCustData", wintypes.LPARAM),
        ("lpfnHook", ctypes.c_void_p),
        ("lpTemplateName", wintypes.LPWSTR),
        ("pvReserved", ctypes.c_void_p),
        ("dwReserved", wintypes.DWORD),
        ("FlagsEx", wintypes.DWORD),
    ]


def _co_init():
    ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole32.CoInitializeEx.restype = ctypes.c_long
    hr = ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
    # S_OK = 0 表示本次初始化生效，需要配对释放
    return ole32, (hr == 0)


def pick_folder(title: str = "选择照片所在文件夹") -> list:
    """SHBrowseForFolderW：返回单个文件夹路径。"""
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.SHBrowseForFolderW.argtypes = [ctypes.POINTER(BROWSEINFO)]
    shell32.SHBrowseForFolderW.restype = ctypes.c_void_p
    shell32.SHGetPathFromIDListW.argtypes = [ctypes.c_void_p, wintypes.LPWSTR]
    shell32.SHGetPathFromIDListW.restype = wintypes.BOOL
    ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]

    display = ctypes.create_unicode_buffer(260)
    bi = BROWSEINFO()
    bi.hwndOwner = 0
    bi.pidlRoot = None
    bi.pszDisplayName = ctypes.cast(display, wintypes.LPWSTR)
    bi.lpszTitle = title
    bi.ulFlags = BIF_RETURNONLYFSDIRS | BIF_EDITBOX | BIF_NEWDIALOGSTYLE
    bi.lpfn = None
    bi.lParam = 0
    bi.iImage = 0

    pidl = shell32.SHBrowseForFolderW(ctypes.byref(bi))
    if not pidl:
        return []
    out = ctypes.create_unicode_buffer(1024)
    ok = shell32.SHGetPathFromIDListW(pidl, out)
    ole32.CoTaskMemFree(pidl)
    return [out.value] if ok and out.value else []


def pick_files(title: str = "选择照片文件") -> list:
    """GetOpenFileNameW（多选）：返回完整文件路径列表。"""
    ole32, need_uninit = _co_init()
    try:
        comdlg32 = ctypes.WinDLL("comdlg32", use_last_error=True)
        comdlg32.GetOpenFileNameW.argtypes = [ctypes.POINTER(OPENFILENAME)]
        comdlg32.GetOpenFileNameW.restype = wintypes.BOOL

        filter_buf = ctypes.create_unicode_buffer(IMAGE_FILTER)
        file_buf = ctypes.create_unicode_buffer(32768)
        title_buf = ctypes.create_unicode_buffer(title)

        of = OPENFILENAME()
        of.lStructSize = ctypes.sizeof(OPENFILENAME)
        of.hwndOwner = 0
        of.hInstance = None
        of.lpstrFilter = ctypes.cast(filter_buf, wintypes.LPWSTR)
        of.lpstrCustomFilter = None
        of.nMaxCustFilter = 0
        of.nFilterIndex = 1
        of.lpstrFile = ctypes.cast(file_buf, wintypes.LPWSTR)
        of.nMaxFile = len(file_buf)
        of.lpstrFileTitle = None
        of.nMaxFileTitle = 0
        of.lpstrInitialDir = None
        of.lpstrTitle = ctypes.cast(title_buf, wintypes.LPWSTR)
        of.Flags = OFN_ALLOWMULTISELECT | OFN_EXPLORER | OFN_FILEMUSTEXIST | OFN_HIDEREADONLY
        of.nFileOffset = 0
        of.nFileExtension = 0
        of.lpstrDefExt = None
        of.lCustData = 0
        of.lpfnHook = None
        of.lpTemplateName = None
        of.pvReserved = None
        of.dwReserved = 0
        of.FlagsEx = 0

        if not comdlg32.GetOpenFileNameW(ctypes.byref(of)):
            return []
        return _parse_multiselect(file_buf, of.nMaxFile)
    finally:
        if need_uninit:
            ole32.CoUninitialize()


def _parse_multiselect(buf, size: int) -> list:
    parts, cur = [], []
    for i in range(size):
        ch = buf[i]
        if ch == "\x00":
            if cur:
                parts.append("".join(cur))
                cur = []
                if i + 1 < size and buf[i + 1] == "\x00":
                    break
            else:
                break
        else:
            cur.append(ch)
    if not parts:
        return []
    if len(parts) == 1:
        return parts
    root = parts[0]
    return [os.path.join(root, name) for name in parts[1:]]


def pick(mode: str) -> list:
    return pick_folder() if mode == "folder" else pick_files()


if __name__ == "__main__":
    import sys
    print(pick(sys.argv[1] if len(sys.argv) > 1 else "folder"))
