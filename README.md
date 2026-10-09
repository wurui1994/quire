# quire

**quire**（书帖——装订时把数页折成一叠的基本单位）是一个 Markdown 书籍构建工具链：一份章法清晰的目录约定，构建出阅读本 HTML、EPUB3、排版 PDF 与博客站点，并带改动即生效的本地预览。定位类似 mdbook / sphinx，但面向中文书籍排版做了整条管线优化（CJK 字体处理、EPUB 元数据、PDF 分卷排版）。

```
Markdown 源文件 ──► quire ──► dist/
                              ├── vol1.html / all.html     阅读本（侧栏目录 / 封面 / 上下卷导航）
                              ├── vol1.epub / all.epub     EPUB3（无障碍元数据 / MathML）
                              ├── vol1.pdf / all.pdf       排版 PDF（页脚 / 书签 / 字体内嵌）
                              ├── index.html               门户页
                              └── blog/                    博客模式：每篇一页 + 索引
```

## 安装

```sh
pip install -e .            # 本仓库
# 可选：pip install -e ".[browser,macos]"
```

依赖：Python ≥ 3.10。核心管线（HTML/EPUB/PDF）无需任何外部二进制；`pandoc`、`playwright` 仅为可选引擎/对照。

## 快速开始

```sh
quire init mybook --title 《书名》 --author 作者
cd mybook
quire serve                 # → http://localhost:8000，改动即生效
quire new vol1/新章 章节标题  # 新建章节（自动编号 ch02-新章.md）
quire build                 # 全部卷 → HTML + EPUB + PDF 到 dist/
quire list --words          # 卷 / 章节 / 字数清单
```

## 目录约定（自动发现）

内容目录默认 `book/`（quire.toml `[book] content` 可改）：

```
book/
├── 前言.md               # 散置前置文件 → 前置卷（front）
├── 正文目录.md           # 其余散置 md → 附录卷（extra）
└── vol1/                 # 子目录含 目录.md 或 chapters/ → 正卷（main）
    ├── 目录.md           #   # 《主标题》（卷一·副题）目录
    └── chapters/
        ├── ch01-xx.md
        └── ch02-xx.md    #   导*/前言* 在前，其余按自然序（ch2 < ch10）
```

- 单卷书：把 `chapters/` 直接放在 `book/` 下即可。
- 既无 `目录.md` 又无 `chapters/` 的目录视为草稿区，跳过；可用 quire.toml `[[volume]]` 显式认领。
- 发现基于文件系统而非手工清单——**落盘即收录，无需维护 SUMMARY**。

`目录.md` 首行决定卷的标题/副题，支持：

```markdown
# 《主标题》（卷一·分篇名）目录     # 推荐
# 《主标题》目录
# 卷一　主标题
# 主标题
```

## quire.toml（全部可选）

```toml
[book]
title = "丛书名"          # 缺省取根目录名
author = "作者"
email = ""
date = "2026-07"          # 缺省取构建当日
date_display = "2026年7月"
language = "zh-CN"        # EPUB 元数据 / XHTML lang
content = "book"          # 内容目录
output = "dist"           # 产物目录
size_limit = 1000000      # 非字节产物超限警告阈值

[theme]
dir = "theme"             # 模板/CSS/JS 覆盖目录，缺文件回退包内置

[fonts]
dir = "assets/fonts"      # 自带字体目录（PDF 优先使用；--cache-font 的目标）

[cover]
plugin = "scripts/cover.py"   # 自定义封面插件（默认为排版式封面）

[layout]
front_prefixes = ["导", "前言"] # 章节排序的前置前缀

[[volume]]                # 显式卷：按 id 覆盖自动卷，否则追加
id = "supplement"
dir = "supplement"        # 或 file = "path/to.md"，或 files = ["*.md"]
extra = ["bibliography.md"] # 附加在自动收集之后的文件
kind = "extra"            # front / main / extra（main 计入封面进度刻度）
label = "补充卷"           # 博客分组 / list 显示名
```

## CLI

```
quire init [PATH] --title --author     初始化项目骨架
quire new <卷id>/<文件名> [标题…]       新建章节（自动补 chNN 前缀）
quire list [--words]                   卷 / 章节清单（--words 附 CJK 字数）
quire build [卷id…]                    构建（默认 HTML+EPUB+PDF；可只选部分卷）
    --html / --epub / --pdf            三选一：仅构建该格式
    --no-epub / --no-pdf               跳过某格式
    --all-one                          全量构建时仅产 all.* 合本
    --indent                           段落首行缩进 2 字
    --mode {book,blog}                 阅读本 / 博客（每篇一页）
    --pdf-engine {fitz,browser}        默认 fitz（无需浏览器）
    --epub-engine {epublib,pandoc}     默认 epublib（无需 pandoc）
    --font/--font-bold                 指定 PDF 中文字体（文件路径 / 族名 / 「族名 字面」）
    --list-fonts / --pdf-fonts PDF / --cache-font   字体排查与离线缓存
quire serve [--port 8000] [--mode blog]  预览服务器（源文件改动即生效）
```

全局 `-d/--dir` 指定项目目录；未指定时自当前位置向上查找 `quire.toml`。

## SDK

```python
from quire import load_book, build

book = load_book("/path/to/project")     # Book 对象（已激活为当前上下文）
for volume in book.volumes:
    print(volume.id, volume.title, volume.kind)

outputs = build(book, volumes=["vol1"], html=True, epub=False, pdf=True,
                pdf_engine="fitz", font="PingFang SC")
# outputs: List[Path]，控制台进度 / 统计与 CLI 一致（show_progress=False 可关）
```

封面插件（`[cover] plugin` 指向的 Python 文件）需实现：

```python
def build_cover_html(cover_id, title, subtitle, *,
                     vol_index, total_vols, is_preface, config) -> str: ...
# config 即 quire.config：可用 config.SERIES / config.AUTHOR /
# config.PUB_DATE_DISPLAY / config.COVER_TEMPLATE / config.fill_template
```

## 架构说明

- **`book.py`** Book / quire.toml 解析 / 卷自动发现 / 显式 `[[volume]]` 合并 / 字数统计
- **`config.py`** 活动书籍上下文：`config.activate(book)` 后，各构建模块以 `config.SERIES` 这类属性访问取值（PEP 562 `__getattr__` 转发到活动 Book），使单进程多书成为可能
- **`renderer.py`** markdown-it 渲染：脚注 / 定义列表 / 标题锚点 / 数学（HTML 与 MathML 两态）/ 中文引号归一
- **`html_builder.py` / `cover.py`** 阅读本页面与封面（默认排版式封面 + 项目插件钩子）
- **`epub.py`** ebooklib 引擎（EPUB3、无障碍元数据、ibooks display-options）+ pandoc 后备引擎
- **`pdf.py`** fitz Story 排版引擎（按文件分片排版避免单文档二次方开销；页脚 / 书签 / `garbage=4` 去重）+ Playwright 打印引擎
- **`fonts.py`** 跨平台 CJK 字体解析（macOS CoreText / Linux fontconfig / Windows 注册表），字重选面、子集缓存
- **`serve.py`** Flask 预览：按片段粒度做 mtime 缓存，改动即生效
- **`api.py` / `cli.py`** 构建编排（CLI 只是参数翻译层，`api.build` 即 SDK 入口）

内置模板 / CSS / JS 在 `quire/assets/`，项目 `[theme] dir` 同名文件可覆盖任意一个。

## 已知取舍

- `目录.md` / `toc.md` 只在卷目录第一个文件位置被识别为目录页（与历史排版管线对齐）。
- PDF 引擎 fitz 走 MuPDF Story：极速、纯 Python 依赖；浏览器引擎用于视觉对照。
- EPUB 语言由 `[book] language` 决定，默认 `zh-CN`。
