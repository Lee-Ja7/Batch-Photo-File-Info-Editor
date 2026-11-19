# -*- coding: utf-8 -*-
"""照片信息批量处理助手by清風 · 核心引擎

纯 Python，无 GUI 依赖。负责：扫描 / 读取 / 规则编译 / 预览 / 事务执行 / 撤销。
可被 server.py 调用，也可命令行独立运行（python core.py --selftest）。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import uuid
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path

# 打包成 exe 后：资源在 _MEIPASS，日志要写到 exe 同目录（可持久化）
if getattr(sys, "frozen", False):
    RES_DIR = Path(sys._MEIPASS)
    DATA_DIR = Path(sys.executable).resolve().parent
else:
    RES_DIR = Path(__file__).resolve().parent
    DATA_DIR = RES_DIR
APP_DIR = RES_DIR
JOURNAL_DIR = DATA_DIR / "journals"
JOURNAL_DIR.mkdir(parents=True, exist_ok=True)

try:
    import pywintypes
    import win32con
    import win32file
    import win32timezone  # pywintypes.Time() 动态依赖，打包时必须显式收集
    HAS_WIN32 = True
except Exception:
    HAS_WIN32 = False

try:
    import piexif
    HAS_PIEXIF = True
except Exception:
    HAS_PIEXIF = False

try:
    from PIL import Image
    HAS_PIL = True
except Exception:
    HAS_PIL = False

IS_WIN = os.name == "nt"

DEFAULT_EXTS = {
    ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp", ".gif",
    ".heic", ".heif", ".avif", ".cr2", ".nef", ".arw", ".dng", ".raw",
    ".orf", ".rw2", ".pef", ".srw",
}
EXIF_WRITABLE = {".jpg", ".jpeg", ".tif", ".tiff", ".webp"}

EXIF_KEYS = {
    "datetime_original": (36867, "Exif"),
    "datetime_digitized": (36868, "Exif"),
    "datetime": (306, "0th"),
}

ATTR_BITS = {
    "readonly": 0x0001,
    "hidden": 0x0002,
    "system": 0x0004,
    "archive": 0x0020,
}
ATTR_NAMES = ("readonly", "hidden", "archive", "system")

ILLEGAL_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]')
RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3",
                  "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
                  "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6",
                  "LPT7", "LPT8", "LPT9"}
VAR_RE = re.compile(r"\{([^{}]+)\}")
DATE_TOKENS = {"yyyy": "%Y", "yy": "%y", "MM": "%m", "dd": "%d",
               "HH": "%H", "mm": "%M", "ss": "%S"}
DATE_TOKEN_RE = re.compile(r"yyyy|yy|MM|dd|HH|mm|ss")


# ---------------------------------------------------------------- 时间工具
def iso(ts: float | None) -> str:
    if ts is None:
        return ""
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def parse_dt(value) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value)
        except OSError:
            return None
    s = str(value).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d",
                "%Y/%m/%d %H:%M:%S", "%Y/%m/%d",
                "%Y:%m:%d %H:%M:%S", "%Y%m%d%H%M%S", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def fmt_date(dt: datetime | None, pattern: str) -> str:
    if dt is None:
        return ""
    if not pattern:
        pattern = "yyyyMMdd"
    fmt = DATE_TOKEN_RE.sub(lambda m: DATE_TOKENS[m.group()], pattern)
    try:
        return dt.strftime(fmt)
    except (ValueError, TypeError):
        return dt.strftime("%Y%m%d")


def exif_dt(dt: datetime) -> str:
    return dt.strftime("%Y:%m:%d %H:%M:%S")


def dt_from_name(name: str) -> datetime | None:
    m = re.search(r"(20\d{2})[-_.]?(\d{2})[-_.]?(\d{2})(?:[-_.T ]?(\d{2})(\d{2})(\d{2}))?", name)
    if not m:
        return None
    try:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hh = int(m.group(4) or 12)
        mi = int(m.group(5) or 0)
        ss = int(m.group(6) or 0)
        return datetime(y, mo, d, hh, mi, ss)
    except ValueError:
        return None


# ---------------------------------------------------------------- 文件读取
def read_times(path: str) -> dict:
    st = os.stat(path)
    birth = getattr(st, "st_birthtime", st.st_ctime) if IS_WIN else st.st_ctime
    return {"created": birth, "modified": st.st_mtime, "accessed": st.st_atime}


def read_attrs(path: str) -> dict:
    raw = getattr(os.stat(path), "st_file_attributes", 0)
    if raw:
        return {k: bool(raw & bit) for k, bit in ATTR_BITS.items()}
    return {"readonly": not os.access(path, os.W_OK),
            "hidden": False, "system": False, "archive": False}


def set_attr_state(path: str, state: dict) -> None:
    if not HAS_WIN32:
        if IS_WIN:
            return
        # POSIX 仅能改变只读位
        mode = os.stat(path).st_mode
        os.chmod(path, mode & ~0o222 if state.get("readonly") else mode | 0o200)
        return
    cur = win32file.GetFileAttributesW(path)
    for key, bit in ATTR_BITS.items():
        if key not in state:
            continue
        cur = cur | bit if state[key] else cur & ~bit
    win32file.SetFileAttributesW(path, cur)


def set_times(path: str, times: dict) -> None:
    """times = {created, modified, accessed} 均为 Unix 时间戳。"""
    if HAS_WIN32:
        handle = win32file.CreateFileW(
            path, win32file.GENERIC_WRITE,
            win32file.FILE_SHARE_READ | win32file.FILE_SHARE_WRITE,
            None, win32file.OPEN_EXISTING, 0, None)
        try:
            cur = read_times(path)
            c = pywintypes.Time(datetime.fromtimestamp(times.get("created", cur["created"])))
            a = pywintypes.Time(datetime.fromtimestamp(times.get("accessed", cur["accessed"])))
            w = pywintypes.Time(datetime.fromtimestamp(times.get("modified", cur["modified"])))
            win32file.SetFileTime(handle, c, a, w)
        finally:
            handle.Close()
    else:
        os.utime(path, (times["accessed"], times["modified"]))


def read_exif(path: str) -> dict:
    out = {k: "" for k in EXIF_KEYS}
    if not HAS_PIEXIF or Path(path).suffix.lower() not in EXIF_WRITABLE:
        return out
    try:
        data = piexif.load(path)
    except Exception:
        return out

    def dec(v):
        if isinstance(v, bytes):
            return v.decode("ascii", "ignore").strip("\x00 ").strip()
        return str(v).strip() if v is not None else ""

    zeroth, exif = data.get("0th") or {}, data.get("Exif") or {}
    out["datetime"] = dec(zeroth.get(306))
    out["datetime_original"] = dec(exif.get(36867))
    out["datetime_digitized"] = dec(exif.get(36868))
    return out


def write_exif(path: str, updates: dict) -> None:
    if not HAS_PIEXIF:
        raise RuntimeError("piexif 未安装，无法写入 EXIF")
    if Path(path).suffix.lower() not in EXIF_WRITABLE:
        raise RuntimeError("该格式不支持写入 EXIF（仅 JPEG / TIFF / WebP）")
    try:
        data = piexif.load(path)
    except Exception:
        data = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}
    zeroth = data.get("0th") or {}
    exif = data.get("Exif") or {}
    for key, value in updates.items():
        if key not in EXIF_KEYS:
            continue
        tag, section = EXIF_KEYS[key]
        target = zeroth if section == "0th" else exif
        dt = parse_dt(value) if value not in (None, "") else None
        if dt is None:
            target.pop(tag, None)
        else:
            target[tag] = exif_dt(dt).encode("ascii")
    data["0th"], data["Exif"] = zeroth, exif
    piexif.insert(piexif.dump(data), path)


@lru_cache(maxsize=512)
def hash8(path: str) -> str:
    try:
        h = hashlib.md5()
        with open(path, "rb") as f:
            h.update(f.read(65536))
        return h.hexdigest()[:8]
    except OSError:
        return "00000000"


def image_size(path: str) -> tuple:
    if not HAS_PIL:
        return (0, 0)
    try:
        with Image.open(path) as im:
            return im.size
    except Exception:
        return (0, 0)


def read_item(path: str) -> dict:
    p = Path(path)
    st = os.stat(path)
    times = read_times(path)
    width, height = image_size(path)
    ext = p.suffix.lower()
    return {
        "path": str(p.resolve()),
        "name": p.name,
        "stem": p.stem,
        "ext": ext.lstrip("."),
        "folder": p.parent.name,
        "parent": str(p.parent.resolve()),
        "size": st.st_size,
        "width": width,
        "height": height,
        "created": times["created"],
        "modified": times["modified"],
        "accessed": times["accessed"],
        "created_iso": iso(times["created"]),
        "modified_iso": iso(times["modified"]),
        "accessed_iso": iso(times["accessed"]),
        "attrs": read_attrs(path),
        "exif": read_exif(path),
        "exif_writable": ext in EXIF_WRITABLE and HAS_PIEXIF,
    }


def scan(paths, recursive=True, exts=None) -> dict:
    allowed = set(exts) if exts else set(DEFAULT_EXTS)
    allowed = {e if e.startswith(".") else "." + e for e in allowed}
    allowed = {e.lower() for e in allowed}
    found, errors = [], []
    for raw in paths:
        p = Path(str(raw).strip().strip('"'))
        if not p.exists():
            errors.append(f"路径不存在：{p}")
            continue
        candidates = []
        if p.is_file():
            candidates = [p]
        else:
            it = p.rglob("*") if recursive else p.glob("*")
            candidates = [x for x in it if x.is_file()]
        for f in candidates:
            if f.name.startswith("__qf_tmp_"):
                continue
            if f.suffix.lower() not in allowed:
                continue
            found.append(str(f.resolve()))
    seen, items = set(), []
    for path in sorted(set(found), key=lambda s: (os.path.normcase(os.path.dirname(s)),
                                                  os.path.normcase(os.path.basename(s)).lower())):
        key = os.path.normcase(path)
        if key in seen:
            continue
        seen.add(key)
        try:
            items.append(read_item(path))
        except Exception as e:
            errors.append(f"{path}：{e}")
    return {"items": items, "errors": errors,
            "capabilities": {"win32": HAS_WIN32, "piexif": HAS_PIEXIF,
                             "pillow": HAS_PIL, "windows": IS_WIN}}


# ---------------------------------------------------------------- 规则引擎
def default_rules() -> dict:
    return {
        "rename": {"enabled": False, "template": "{date:yyyyMMdd}_{seq:000}_{stem}",
                   "seq_start": 1, "find": "", "replace": "", "regex": False,
                   "prefix": "", "suffix": "", "case": "keep", "ext_case": "keep",
                   "conflict": "suffix"},
        "date": {"enabled": False, "source": "fixed", "fixed": "",
                 "base": "modified", "offset_days": 0, "offset_hours": 0, "offset_minutes": 0,
                 "targets": {"created": True, "modified": True, "accessed": False},
                 "sync_exif": False},
        "attributes": {"enabled": False, "readonly": "keep", "hidden": "keep",
                       "archive": "keep", "system": "keep"},
        "exif": {"enabled": False, "mode": "keep", "fixed": "", "base": "modified",
                 "offset_days": 0, "offset_hours": 0},
    }


def resolve_date(item: dict, cfg: dict, source_key: str = "source") -> tuple:
    """返回 (目标 datetime, 说明文字)。"""
    src = (cfg.get(source_key) or "fixed")
    if src == "fixed":
        dt = parse_dt(cfg.get("fixed"))
        if dt is None:
            return None, "固定日期为空或格式错误"
        return dt, "固定值"
    base_key = cfg.get("base", "modified")
    base_map = {"created": item["created"], "modified": item["modified"],
                "accessed": item["accessed"]}
    if src == "exif":
        dt = parse_dt(item["exif"].get("datetime_original"))
        if dt is None:
            return None, "无 EXIF 拍摄日期"
        return dt, "EXIF 拍摄日期"
    if src == "filename":
        dt = dt_from_name(item["name"])
        if dt is None:
            return None, "文件名中未找到日期"
        return dt, "文件名解析"
    if src == "modified":
        return datetime.fromtimestamp(base_map["modified"]), "原修改时间"
    if src == "created":
        return datetime.fromtimestamp(base_map["created"]), "原创建时间"
    # offset：以 base 为基准叠加偏移
    base = datetime.fromtimestamp(base_map.get(base_key, item["modified"]))
    delta = timedelta(days=float(cfg.get("offset_days") or 0),
                      hours=float(cfg.get("offset_hours") or 0),
                      minutes=float(cfg.get("offset_minutes") or 0))
    return base + delta, f"{base_key} + 偏移"


def sanitize_stem(stem: str) -> str:
    stem = ILLEGAL_CHARS.sub("_", stem)
    stem = re.sub(r"\s+", " ", stem).strip().strip(" .")
    if not stem:
        stem = "photo"
    if stem.upper() in RESERVED_NAMES:
        stem = "_" + stem
    return stem[:180]


def render_template(tpl: str, item: dict, seq: int, date_dt: datetime | None) -> str:
    dates = {
        "date": date_dt,
        "exifdate": parse_dt(item["exif"].get("datetime_original")),
        "created": datetime.fromtimestamp(item["created"]),
        "modified": datetime.fromtimestamp(item["modified"]),
        "accessed": datetime.fromtimestamp(item["accessed"]),
    }

    def repl(m):
        raw = m.group(1).strip()
        key, _, arg = raw.partition(":")
        key = key.lower().strip()
        arg = arg.strip()
        if key == "seq":
            # {seq:000} -> 补零到 3 位；{seq:4} -> 补零到 4 位；纯 0 串按长度算宽度
            if arg.isdigit():
                width = len(arg) if set(arg) == {"0"} else int(arg)
            else:
                width = 0
            return str(seq).zfill(width) if width else str(seq)
        if key in dates:
            return fmt_date(dates[key], arg)
        simple = {
            "stem": item["stem"], "name": item["name"], "ext": item["ext"],
            "folder": item["folder"], "parent": item["parent"],
            "size": str(item["size"]), "sizekb": str(round(item["size"] / 1024)),
            "width": str(item["width"]), "height": str(item["height"]),
            "hash8": hash8(item["path"]),
        }
        if key in simple:
            return simple[key]
        return m.group(0)

    return VAR_RE.sub(repl, tpl)


def tri_state(current: bool, action: str) -> bool:
    if action == "set":
        return True
    if action == "clear":
        return False
    return current


def build_changeset(items: list, rules: dict | None = None) -> dict:
    r = default_rules()
    if rules:
        for section, values in rules.items():
            if section in r and isinstance(values, dict):
                r[section].update(values)
    rn, dt_cfg, at, ex = r["rename"], r["date"], r["attributes"], r["exif"]

    # 先为每个条目解出"主日期"，供命名模板使用
    date_cache = []
    for item in items:
        dt, note = resolve_date(item, dt_cfg) if dt_cfg["enabled"] else (None, "未启用")
        if dt is None and rn["enabled"] and "{date" in rn["template"]:
            dt = datetime.fromtimestamp(item["modified"])
        date_cache.append(dt)

    changes, warnings = [], []
    for idx, item in enumerate(items):
        c = {
            "id": idx, "path": item["path"], "name": item["name"],
            "ext": item["ext"], "new_name": item["name"], "new_path": item["path"],
            "rename": False, "enabled": True, "conflicts": [], "warnings": [],
            "times_before": {"created": item["created"], "modified": item["modified"],
                             "accessed": item["accessed"]},
            "times_after": {"created": item["created"], "modified": item["modified"],
                            "accessed": item["accessed"]},
            "attrs_before": dict(item["attrs"]), "attrs_after": dict(item["attrs"]),
            "exif_before": None, "exif_after": None,
            "diff": {"name": [], "date": [], "attr": [], "exif": []},
        }

        # --- 文件名 ---
        if rn["enabled"]:
            stem = item["stem"]
            tpl = rn.get("template", "").strip()
            if tpl:
                stem = render_template(tpl, item, int(rn.get("seq_start", 1)) + idx,
                                       date_cache[idx])
            if rn.get("find"):
                if rn.get("regex"):
                    try:
                        stem = re.sub(rn["find"], rn.get("replace", ""), stem)
                    except re.error as e:
                        c["warnings"].append(f"正则错误：{e}")
                else:
                    stem = stem.replace(rn["find"], rn.get("replace", ""))
            stem = (rn.get("prefix") or "") + stem + (rn.get("suffix") or "")
            case = rn.get("case", "keep")
            stem = stem.lower() if case == "lower" else stem.upper() if case == "upper" else stem
            ext = item["ext"]
            ec = rn.get("ext_case", "keep")
            ext = ext.lower() if ec == "lower" else ext.upper() if ec == "upper" else ext
            stem = sanitize_stem(stem)
            new_name = f"{stem}.{ext}" if ext else stem
            c["new_name"] = new_name
            c["new_path"] = os.path.join(item["parent"], new_name)
            c["rename"] = os.path.normcase(c["new_path"]) != os.path.normcase(item["path"])
            if c["rename"]:
                c["diff"]["name"] = [item["name"], new_name]

        # --- 日期 / 时间戳 ---
        new_dt, note = (resolve_date(item, dt_cfg) if dt_cfg["enabled"] else (None, ""))
        if dt_cfg["enabled"]:
            if new_dt is None:
                c["warnings"].append(f"日期来源无效：{note}")
            else:
                ts = new_dt.timestamp()
                for key in ("created", "modified", "accessed"):
                    if dt_cfg["targets"].get(key):
                        c["times_after"][key] = ts
                        c["diff"]["date"].append(
                            f"{key}: {iso(c['times_before'][key])} → {iso(ts)}")

        # --- EXIF ---
        exif_dt_target = None
        if ex["enabled"]:
            mode = ex.get("mode", "keep")
            if mode == "clear":
                exif_dt_target = "clear"
            elif mode in ("fixed", "offset", "from_fs"):
                cfg = dict(ex)
                if mode == "fixed":
                    exif_dt_target = parse_dt(ex.get("fixed"))
                elif mode == "from_fs":
                    exif_dt_target = datetime.fromtimestamp(item[ex.get("base", "modified")])
                else:
                    exif_dt_target, _ = resolve_date(item, cfg, source_key="mode")
            elif mode == "from_date" and dt_cfg["enabled"] and new_dt:
                exif_dt_target = new_dt
        if dt_cfg["enabled"] and dt_cfg.get("sync_exif") and new_dt:
            exif_dt_target = new_dt

        if exif_dt_target is not None:
            if not item["exif_writable"]:
                c["warnings"].append("该格式不支持写入 EXIF，已跳过")
            else:
                c["exif_before"] = {k: item["exif"].get(k, "") for k in EXIF_KEYS}
                if exif_dt_target == "clear":
                    c["exif_after"] = {}
                    c["diff"]["exif"].append("清除拍摄日期")
                else:
                    c["exif_after"] = {"datetime_original": exif_dt(exif_dt_target),
                                       "datetime_digitized": exif_dt(exif_dt_target),
                                       "datetime": exif_dt(exif_dt_target)}
                    c["diff"]["exif"].append(
                        f"拍摄日期 → {exif_dt_target.strftime('%Y-%m-%d %H:%M:%S')}")

        # --- 属性 ---
        if at["enabled"]:
            for key in ATTR_NAMES:
                action = at.get(key, "keep")
                c["attrs_after"][key] = tri_state(c["attrs_before"][key], action)
                if c["attrs_after"][key] != c["attrs_before"][key]:
                    c["diff"]["attr"].append(
                        f"{key}: {'开' if c['attrs_after'][key] else '关'}")

        changes.append(c)

    # --- 冲突检测 ---
    mode = rn.get("conflict", "suffix")
    src_set = {os.path.normcase(c["path"]) for c in changes}
    used = {}
    for c in changes:
        if not c["rename"]:
            continue
        base = c["new_path"]
        stem, ext = os.path.splitext(base)
        target, n = base, 2
        while True:
            key = os.path.normcase(target)
            on_disk = os.path.exists(target) and key not in src_set
            if key not in used and not on_disk:
                break
            if mode == "skip":
                c["enabled"] = False
                c["warnings"].append("目标已存在，已跳过")
                target = None
                break
            if mode == "error":
                c["enabled"] = False
                c["conflicts"].append("目标文件已存在")
                target = None
                break
            target = f"{stem}_{n}{ext}"
            n += 1
        if target is None:
            continue
        used[os.path.normcase(target)] = c["id"]
        c["new_path"] = target
        c["new_name"] = os.path.basename(target)
        if c["diff"]["name"]:
            c["diff"]["name"][1] = c["new_name"]

    summary = {
        "total": len(changes),
        "rename": sum(1 for c in changes if c["enabled"] and c["rename"]),
        "date": sum(1 for c in changes if c["enabled"] and c["diff"]["date"]),
        "attr": sum(1 for c in changes if c["enabled"] and c["diff"]["attr"]),
        "exif": sum(1 for c in changes if c["enabled"] and c["exif_after"] is not None),
        "skipped": sum(1 for c in changes if not c["enabled"]),
        "warnings": sum(len(c["warnings"]) for c in changes),
    }
    return {"changes": changes, "summary": summary,
            "capabilities": {"win32": HAS_WIN32, "piexif": HAS_PIEXIF,
                             "pillow": HAS_PIL, "windows": IS_WIN}}


# ---------------------------------------------------------------- 执行 / 撤销
def _flush(jpath: Path, journal: dict) -> None:
    jpath.write_text(json.dumps(journal, ensure_ascii=False, indent=1), encoding="utf-8")


def execute(changes: list, progress_cb=None) -> dict:
    active = [c for c in changes if c.get("enabled", True)]
    if not active:
        return {"ok": False, "error": "没有可执行的条目", "journal_id": None}

    jid = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:4]
    journal = {"id": jid, "created_at": datetime.now().isoformat(timespec="seconds"),
               "status": "running", "records": [], "errors": []}
    jpath = JOURNAL_DIR / f"{jid}.json"
    for c in active:
        journal["records"].append({
            "src": c["path"], "dst": c["new_path"], "cur": c["path"],
            "rename": c["rename"],
            "exif_before": c["exif_before"], "exif_after": c["exif_after"],
            "times_before": c["times_before"], "times_after": c["times_after"],
            "attrs_before": c["attrs_before"], "attrs_after": c["attrs_after"],
            "done": [],
        })
    _flush(jpath, journal)
    recs = journal["records"]
    total = len(recs)

    def step(i, msg):
        _flush(jpath, journal)
        if progress_cb:
            progress_cb(i, total, msg)

    # 0) 临时清除会阻碍写入的属性（只读 / 隐藏 / 系统），最后一步统一恢复
    #    Windows 下对隐藏文件以写模式打开会被拒绝，piexif 写入尤其受影响
    for rec in recs:
        before = rec["attrs_before"]
        if any(before.get(k) for k in ("readonly", "hidden", "system")):
            try:
                state = dict(before)
                for k in ("readonly", "hidden", "system"):
                    state[k] = False
                set_attr_state(rec["cur"], state)
            except Exception as e:
                journal["errors"].append(f"清除写入限制失败 {rec['src']}：{e}")

    # 1) 写 EXIF（会刷新 mtime，必须最先）
    for i, rec in enumerate(recs):
        if rec["exif_after"] is None:
            continue
        try:
            write_exif(rec["cur"], rec["exif_after"])
            rec["done"].append("exif")
        except Exception as e:
            journal["errors"].append(f"EXIF 写入失败 {rec['src']}：{e}")
        step(i + 1, "写入 EXIF 元数据")

    # 2) 两阶段重命名（避免链式覆盖）
    tmp_map = []
    for i, rec in enumerate(recs):
        if not rec["rename"]:
            continue
        tmp = os.path.join(os.path.dirname(rec["src"]),
                           f"__qf_tmp_{jid}_{i}{os.path.splitext(rec['src'])[1]}")
        try:
            os.rename(rec["cur"], tmp)
            rec["cur"] = tmp
            tmp_map.append((rec, tmp))
        except Exception as e:
            journal["errors"].append(f"重命名暂存失败 {rec['src']}：{e}")
    for rec, tmp in tmp_map:
        try:
            os.rename(tmp, rec["dst"])
            rec["cur"] = rec["dst"]
            rec["done"].append("rename")
        except Exception as e:
            journal["errors"].append(f"重命名失败 {tmp} → {rec['dst']}：{e}")
            try:
                os.rename(tmp, rec["src"])
                rec["cur"] = rec["src"]
            except Exception:
                pass
    step(total, "完成重命名")

    # 3) 设置时间戳（最后写，避免被 EXIF / 重命名覆盖）
    for rec in recs:
        if rec["times_after"] == rec["times_before"]:
            continue
        try:
            set_times(rec["cur"], rec["times_after"])
            rec["done"].append("times")
        except Exception as e:
            journal["errors"].append(f"时间戳写入失败 {rec['cur']}：{e}")

    # 4) 设置属性（只读放最后；步骤 0 若临时清过属性，此处必须恢复）
    for rec in recs:
        try:
            set_attr_state(rec["cur"], rec["attrs_after"])
            rec["done"].append("attrs")
        except Exception as e:
            journal["errors"].append(f"属性写入失败 {rec['cur']}：{e}")

    journal["status"] = "done" if not journal["errors"] else "done_with_errors"
    for rec in recs:
        rec["final"] = rec["cur"]
    _flush(jpath, journal)
    return {"ok": not journal["errors"], "journal_id": jid,
            "count": total, "errors": journal["errors"],
            "status": journal["status"]}


def list_journals() -> list:
    out = []
    for f in sorted(JOURNAL_DIR.glob("*.json"), reverse=True):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            out.append({"id": d.get("id"), "created_at": d.get("created_at"),
                        "status": d.get("status"), "count": len(d.get("records", []))})
        except Exception:
            continue
    return out


def undo(jid: str) -> dict:
    jpath = JOURNAL_DIR / f"{jid}.json"
    if not jpath.exists():
        return {"ok": False, "error": "日志不存在"}
    journal = json.loads(jpath.read_text(encoding="utf-8"))
    if journal.get("status") == "undone":
        return {"ok": False, "error": "该操作已撤销"}
    recs = journal["records"]
    errors = []

    def cur(rec):
        return rec.get("final") or rec.get("cur") or rec["dst"]

    # 0) 清掉执行后可能残留的写入限制（只读 / 隐藏 / 系统）
    for rec in recs:
        try:
            state = dict(rec["attrs_after"])
            for k in ("readonly", "hidden", "system"):
                state[k] = False
            set_attr_state(cur(rec), state)
        except Exception as e:
            errors.append(f"清除写入限制失败：{e}")

    # 1) 恢复 EXIF
    for rec in recs:
        if rec.get("exif_before") is None:
            continue
        try:
            write_exif(cur(rec), rec["exif_before"])
        except Exception as e:
            errors.append(f"EXIF 恢复失败 {cur(rec)}：{e}")

    # 2) 两阶段反向重命名
    tmp_map = []
    for i, rec in enumerate(recs):
        if not rec.get("rename"):
            continue
        src, final = rec["src"], cur(rec)
        if os.path.normcase(src) == os.path.normcase(final):
            continue
        tmp = os.path.join(os.path.dirname(final), f"__qf_undo_{jid}_{i}.tmp")
        try:
            os.rename(final, tmp)
            tmp_map.append((rec, tmp))
        except Exception as e:
            errors.append(f"反向暂存失败 {final}：{e}")
    for rec, tmp in tmp_map:
        try:
            os.rename(tmp, rec["src"])
            rec["cur"] = rec["src"]
        except Exception as e:
            errors.append(f"反向重命名失败 {tmp} → {rec['src']}：{e}")

    # 3) 恢复时间戳（在 EXIF 恢复之后）
    for rec in recs:
        try:
            set_times(rec.get("cur", cur(rec)), rec["times_before"])
        except Exception as e:
            errors.append(f"时间戳恢复失败：{e}")

    # 4) 恢复属性
    for rec in recs:
        try:
            set_attr_state(rec.get("cur", cur(rec)), rec["attrs_before"])
        except Exception as e:
            errors.append(f"属性恢复失败：{e}")

    journal["status"] = "undone" if not errors else "undo_with_errors"
    journal["errors"] = journal.get("errors", []) + errors
    _flush(jpath, journal)
    return {"ok": not errors, "journal_id": jid, "errors": errors}


# ---------------------------------------------------------------- 自检
def selftest() -> dict:
    import tempfile
    root = Path(tempfile.mkdtemp(prefix="qf_selftest_"))
    files = []
    jid = None
    try:
        if not HAS_PIL:
            return {"ok": False, "error": "Pillow 未安装，无法自检"}
        for i in range(3):
            p = root / f"IMG_2024010{i + 1}_{i}.jpg"
            Image.new("RGB", (32, 24), (i * 60, 100, 160)).save(p, "JPEG")
            if HAS_PIEXIF:
                zeroth = {"0th": {306: b"2020:01:01 10:00:00"},
                          "Exif": {36867: f"2020:01:0{i + 1} 1{i}:00:00".encode()},
                          "GPS": {}, "1st": {}, "thumbnail": None}
                piexif.insert(piexif.dump(zeroth), str(p))
            files.append(str(p))
        scanned = scan([str(root)])
        assert len(scanned["items"]) == 3, f"扫描数量错误：{len(scanned['items'])}"
        assert scanned["capabilities"]["win32"], "pywin32 未加载"
        assert scanned["items"][0]["exif"]["datetime_original"].startswith("2020"), "EXIF 读取失败"

        rules = {
            "rename": {"enabled": True, "template": "旅行_{date:yyyyMMdd}_{seq:000}",
                       "seq_start": 1, "conflict": "suffix"},
            "date": {"enabled": True, "source": "exif",
                     "targets": {"created": True, "modified": True, "accessed": False},
                     "sync_exif": False},
            "attributes": {"enabled": True, "archive": "set", "readonly": "clear"},
            "exif": {"enabled": True, "mode": "fixed", "fixed": "2023-06-01 08:30:00"},
        }
        preview = build_changeset(scanned["items"], rules)
        assert preview["summary"]["rename"] == 3, "重命名预览错误"
        assert preview["summary"]["exif"] == 3, "EXIF 预览错误"
        names = [c["new_name"] for c in preview["changes"]]
        assert len(set(names)) == 3, f"命名冲突未解决：{names}"

        result = execute(preview["changes"])
        jid = result.get("journal_id")
        assert result["ok"], f"执行失败：{result['errors']}"
        after = scan([str(root)])
        assert len(after["items"]) == 3, "执行后文件数量变化"
        assert all(x["name"].startswith("旅行_") for x in after["items"]), "重命名未生效"
        assert all(x["created_iso"].startswith("2020-01-0") for x in after["items"]), "创建时间未生效"
        assert all(x["exif"]["datetime_original"].startswith("2023:06:01") for x in after["items"]), "EXIF 未生效"

        undone = undo(result["journal_id"])
        assert undone["ok"], f"撤销失败：{undone['errors']}"
        restored = scan([str(root)])
        assert all(x["name"].startswith("IMG_") for x in restored["items"]), "撤销后文件名未恢复"
        assert all(x["exif"]["datetime_original"].startswith("2020") for x in restored["items"]), "撤销后 EXIF 未恢复"
        return {"ok": True, "dir": str(root), "preview": preview["summary"],
                "execute": result, "undo": undone}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "dir": str(root)}
    finally:
        # 自检不污染用户的操作日志
        if jid:
            try:
                (JOURNAL_DIR / f"{jid}.json").unlink()
            except OSError:
                pass
        # 自检目录位于系统临时区，仅清理本次创建的文件
        for f in root.glob("*"):
            try:
                if f.is_file():
                    f.unlink()
            except OSError:
                pass
        try:
            root.rmdir()
        except OSError:
            pass


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        print(json.dumps(selftest(), ensure_ascii=False, indent=2))
    else:
        caps = scan([])["capabilities"]
        print("照片信息批量处理助手by清風 · 核心引擎")
        print(f"能力：win32={caps['win32']} piexif={caps['piexif']} pillow={caps['pillow']}")
        print("用法：python core.py --selftest")
