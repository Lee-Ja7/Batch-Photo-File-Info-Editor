# 照片信息批量处理助手by清風

批量修改照片信息的 Windows 桌面工具。完全离线、单文件 exe、原生独立窗口。

一次导入，批量处理四类信息；**先预览、后执行**，每一步都有日志，支持一键撤销。

## 它能改什么

| 类别 | 内容 | 说明 |
| --- | --- | --- |
| 文件名 | 模板命名、序号、查找替换（含正则）、前后缀、大小写 | 两阶段重命名，避免互相覆盖 |
| 文件日期 | 创建时间、修改时间、访问时间 | Windows 原生 API，含真正的「创建时间」 |
| 文件属性 | 只读 / 隐藏 / 存档 / 系统 | 三态：保持 / 置位 / 清除 |
| EXIF 元数据 | 拍摄日期、数字化日期、DateTime | 固定值 / 偏移 / 回填 / 清除 |

日期来源：固定值、原时间叠加偏移、**EXIF 拍摄日期回填**、文件名解析、原创建/修改时间。

## 快速开始

### 方式 A：直接运行（推荐）

1. 获取 `照片信息批量处理助手by清風.exe`
2. 双击启动（首次启动约 2–4 秒解压）
3. 点击「选择文件夹」或「选择文件」导入照片

> 无需安装 Python，无需联网。窗口基于系统自带的 **Edge WebView2 Runtime**（Win10/11 一般已预装）。

### 方式 B：从源码运行

要求：Windows 10/11、Python 3.11+（本项目在 3.13.14 验证）、Edge WebView2 Runtime。

```bash
pip install -r requirements.txt
python app.py
```

`启动.bat` 为备用入口（自动启动同目录 exe）。

## 从源码构建 exe

```bash
pip install -r requirements.txt
build.bat
```

产物在 `dist\PhotoStudio.exe`，重命名为 `照片信息批量处理助手by清風.exe` 即可发布。

构建要点（已固化在 `build.bat`）：

- `--onefile --noconsole`：单文件、无控制台
- `--exclude-module=numpy`：去掉 Pillow 未使用的 numpy（约省 8 MB）
- `--hidden-import=win32timezone`：`pywintypes.Time()` 的动态依赖，**不加会导致 exe 内时间戳写入失败**
- `--version-file version_info.txt`：写入产品名称等版本资源
- 可选 UPX：追加 `--upx-dir <upx目录>` 可再压约 4 MB（无损压缩 DLL，.NET 程序集会自动跳过）

体积参考：不压缩 29.8 MB → 排除 numpy 18.8 MB → UPX 14.7 MB。

## 项目结构

| 文件 | 作用 |
| --- | --- |
| `app.py` | 程序入口：后台本地服务 + pywebview 原生窗口 |
| `core.py` | 核心引擎：扫描、规则、预览、执行、撤销（含 `--selftest`） |
| `server.py` | 本地 JSON API（仅监听 `127.0.0.1`）与静态页面 |
| `dialogs.py` | ctypes 直调 Win32 原生文件 / 文件夹选择对话框 |
| `index.html` | 单文件界面（Material 2 风格、完整 CSS 变量与动效体系） |
| `version_info.txt` | exe 版本资源（产品名等） |
| `build.bat` / `启动.bat` | 构建 / 启动 |
| `requirements.txt` | 依赖 |
| `README.md` | 本文档 |

## 使用流程

1. **导入**：选择文件夹 / 多个文件，或直接粘贴路径（每行一个）；可设格式过滤与递归扫描
2. **配置规则**：四个页签分别配置文件名 / 日期 / 属性 / EXIF
3. **生成预览**：逐条 diff 对照表（改名前后、时间变化、属性与 EXIF 改动），冲突与警告高亮
4. **确认执行**：二次确认后事务式写入
5. **撤销**：在「操作日志」里一键回滚本次全部改动

### 命名模板变量

| 变量 | 含义 |
| --- | --- |
| `{date:yyyyMMdd}` | 目标日期（来自「日期」页规则） |
| `{exifdate:yyyy-MM-dd_HHmmss}` | EXIF 拍摄日期 |
| `{created:...}` / `{modified:...}` / `{accessed:...}` | 文件系统时间 |
| `{seq}` / `{seq:000}` | 序号（可指定补零宽度） |
| `{stem}` / `{ext}` / `{name}` | 原主名 / 扩展名 / 全名 |
| `{folder}` / `{parent}` | 所在文件夹名 / 完整路径 |
| `{width}` / `{height}` / `{size}` / `{sizekb}` | 尺寸与体积 |
| `{hash8}` | 文件头 MD5 前 8 位 |

扩展名自动补上，模板里无需书写。

## 设计要点

- **执行顺序固定**：`写 EXIF → 两阶段重命名 → 写时间戳 → 写属性`。写 EXIF 和改名都会刷新修改时间，所以时间戳必须最后写。
- **两阶段重命名**：先全部改成临时唯一名，再改成目标名，避免 `A→B, B→C` 链式覆盖。
- **写入限制预处理**：Windows 下隐藏 / 只读属性会让 `piexif` 写入报 `Permission denied`，执行与撤销前会临时清除这些属性，最后统一恢复。
- **事务日志**：每步成功后立即落盘到 `journals/`，崩溃可恢复；撤销按反序还原文件名、时间戳、属性与 EXIF。
- **本地离线**：服务只监听 `127.0.0.1`，不上传任何文件，不访问外网。

## 限制与注意

- EXIF 写入仅支持 **JPEG / TIFF / WebP**；HEIC / RAW / PNG 会跳过并在预览里提示（不影响改名、日期、属性）。
- 「创建时间」是 Windows 概念；其他平台无对应字段。
- 设为「隐藏」后文件可能从资源管理器消失，请谨慎；所有改动都可撤销。
- `journals/` 保存在 exe 同目录，请把 exe 放在**可写目录**（不要放 `C:\Program Files`）。
- 浏览器拖拽无法提供完整路径，因此改用系统原生选择对话框或直接粘贴路径。

## 常见问题

**Q：点「选择文件 / 选择文件夹」没反应？**
对话框用 ctypes 直调 Win32 API，不依赖 tkinter（精简版 Python 常缺 tcl/tk）。若仍无反应，可直接在路径框粘贴完整路径。

**Q：双击 exe 没窗口？**
确认系统装有 Edge WebView2 Runtime（Win10/11 通常自带）；可运行 `python app.py` 查看控制台报错。

**Q：如何自检？**
`python core.py --selftest`：自动构造测试照片，跑完扫描 → 预览 → 执行 → 撤销并断言结果。

## 上传到 GitHub

解压源码包后得到仓库目录（本包内为 `PhotoBatch/`）：

```bash
cd PhotoBatch
git init
git add .
git commit -m "Initial commit"
git remote add origin <你的仓库地址>
git push -u origin main
```

`.gitignore` 已排除 `*.exe`、`dist/`、`build/`、`__pycache__/`、`journals/*.json`。建议把 exe 放到 GitHub **Releases** 而不是仓库里。

## 许可证

未指定。若要公开发布，请自行添加 `LICENSE` 文件。
