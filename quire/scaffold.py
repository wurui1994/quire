"""脚手架：初始化书籍项目 / 新建章节。

    quire init [PATH] --title 书名 --author 作者
    quire new vol1/新章 [标题…]

init 只做最小可构建骨架（quire.toml + 一卷一章）；发现机制基于文件
系统而非手工清单，因此 new 只需落一个 md 文件即可被下次构建收录。
"""

import json
import re
from pathlib import Path
from typing import Optional, Tuple

from .book import Book, CONFIG_NAME

_TOML_TEMPLATE = """\
[book]
title = {title}
author = {author}
# email = ""                 # 作者邮箱（EPUB 元数据）
# date = "2026-10"           # 缺省取构建当日（YYYY-MM）
# date_display = ""          # 封面日期显示，缺省由 date 生成「YYYY年M月」
# language = "zh-CN"
# content = "book"           # 内容目录（默认 book/）
# output = "dist"            # 产物目录（默认 dist/）
# size_limit = 1000000        # 非字节产物超限警告阈值

# [theme]                    # 模板 / CSS / JS 覆盖目录（缺文件回退内置）
# dir = "theme"

# [fonts]                     # 自带字体目录（PDF 排版优先使用）
# dir = "assets/fonts"

# [cover]                    # 自定义封面插件（见 quire.cover 文档）
# plugin = "scripts/cover.py"

# [layout]
# front_prefixes = ["导", "前言"]   # 章节排序时排在前面的文件名前缀

# [[volume]]                 # 显式卷：可收编自动发现跳过的目录 / 调整顺序
# id = "appendix"
# dir = "appendix"
# kind = "extra"
# label = "附录"
"""

_README_TEMPLATE = """\
# {title}

本仓库由 [quire](https://github.com/wurui1994/quire) 构建：Markdown → HTML / EPUB / PDF。

```sh
quire serve          # 本地预览 http://localhost:8000
quire build          # 构建全部卷到 dist/
quire list --words   # 查看卷 / 章节与字数
quire new vol1/新章   # 新建章节
```

目录约定详见 quire.toml 注释与 `python -m quire --help`。
"""

_GITIGNORE = """\
dist/
.DS_Store
__pycache__/
"""


def init_project(path: str = ".", *, title: Optional[str] = None,
                author: str = "") -> Path:
    """初始化书籍项目骨架，返回项目根目录。已有 quire.toml 时拒绝。"""
    root = Path(path).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if (root / CONFIG_NAME).is_file():
        raise ValueError(f"目录已是 quire 项目（存在 {CONFIG_NAME}）：{root}")

    project_title = title or root.name
    toml = _TOML_TEMPLATE.format(
        title=json.dumps(project_title, ensure_ascii=False),
        author=json.dumps(author, ensure_ascii=False))
    (root / CONFIG_NAME).write_text(toml, encoding="utf-8")

    readme = root / "README.md"
    if not readme.exists():
        readme.write_text(_README_TEMPLATE.format(title=project_title),
                          encoding="utf-8")
    gitignore = root / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(_GITIGNORE, encoding="utf-8")

    vol_dir = root / "book" / "vol1"
    (vol_dir / "chapters").mkdir(parents=True, exist_ok=True)
    (vol_dir / "目录.md").write_text(
        f"# 《{project_title}》（卷一）目录\n", encoding="utf-8")
    (vol_dir / "chapters" / "ch01-开篇.md").write_text(
        "# 开篇\n\n", encoding="utf-8")
    return root


def _chapters_dir(book: Book, volume) -> Path:
    """卷的章节落点：优先其 chapters/ 目录，否则首个文件所在目录。"""
    parents = {Path(book.root, relpath).parent for relpath in volume.files}
    named = [p for p in parents if p.name == "chapters"]
    if len(named) == 1:
        return named[0]
    if parents:
        return sorted(parents, key=lambda p: len(p.parts))[0]
    raise ValueError(f"卷 {volume.id} 无文件，无法确定章节目录")


def _next_chapter_number(directory: Path) -> int:
    numbers = [int(m.group(1)) for m in
               (re.match(r'ch(\d+)', p.stem) for p in directory.glob("*.md")) if m]
    return max(numbers, default=0) + 1


def new_chapter(book: Book, where: str, title: Optional[str] = None) -> Path:
    """在指定卷新建章节文件（如 where="vol1/新章名"），返回文件路径。

    文件名未带 chNN 前缀时自动补号（ch02-…）；已存在同名文件则拒绝。
    """
    if "/" not in where:
        raise ValueError('用法：quire new <卷id>/<文件名> [标题…]，如 quire new vol1/新章')
    volume_id, slug = where.split("/", 1)
    slug = slug.strip().strip("/")
    if not slug:
        raise ValueError(f"文件名为空：{where}")

    volume = book.volume(volume_id)
    if volume is None:
        available = ", ".join(v.id for v in book.volumes)
        raise ValueError(f"未知卷：{volume_id}（可用卷：{available}）")

    if re.match(r'ch\d+', slug):
        filename = f"{slug}.md"
    else:
        directory = _chapters_dir(book, volume)
        filename = f"ch{_next_chapter_number(directory):02d}-{slug}.md"

    target = _chapters_dir(book, volume) / filename
    if target.exists():
        raise ValueError(f"文件已存在：{target.relative_to(book.root)}")

    chapter_title = title or slug
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"# {chapter_title}\n\n", encoding="utf-8")
    return target
