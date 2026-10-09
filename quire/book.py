"""书籍（Book）：quire.toml 解析 + 卷自动发现 + 字数统计。

目录结构约定（自动发现；没有 quire.toml 也能用）
==================================================

内容目录默认 book/（[book] content 可改），其下：

- 散置单文件：
    preface.md / 前言.md / 序.md / intro.md / readme.md → 前置卷（kind="front"）
    其余 *.md（如 正文目录.md）                       → 附录卷（kind="extra"）
- 子目录：
    含 目录.md / toc.md / _toc.md / summary.md，或含 chapters/
      → 正卷（kind="main"，id=目录名）
      文件顺序：前言.md → 目录.md → chapters/*.md（导*/前言* 在前，自然排序）
    既无目录又无 chapters/、只有裸 md 的目录
      → 跳过（草稿区，可由 [[volume]] 显式认领）
    直接叫 chapters/ 的目录
      → 单卷书的正文（id="main"）

quire.toml
==========

    [book]
    title = "…"          # 丛书名（缺省取根目录名）
    author = "…"
    email = "…"
    date = "2026-07"     # 缺省今天
    date_display = "2026年7月"
    language = "zh-CN"
    content = "book"     # 内容目录
    output = "dist"      # 产物目录
    size_limit = 1000000 # 非字节产物超限警告（PDF 除外）

    [theme]
    dir = "theme"        # 模板/CSS/JS 覆盖目录（缺文件回退内置）

    [fonts]
    dir = "assets/fonts" # 自带字体目录（优先于包内置与系统）

    [cover]
    plugin = "scripts/cover.py"  # 封面插件（见 cover.py）

    [layout]
    front_prefixes = ["导", "前言"]  # 章节排序时排在前面的文件名前缀

    [[volume]]           # 显式卷：覆盖同 id 的自动卷，或追加新卷
    id = "preface"
    file = "book/preface.md"
    title = "序"
    kind = "front"

    [[volume]]
    id = "supplement"    # dir 卷：目录.md + chapters/，附加文件用 extra
    dir = "supplement"
    extra = ["bibliography.md"]
    kind = "extra"
    label = "补充卷"

    [[volume]]
    id = "questions"     # files 卷：有序清单（支持 glob），相对 dir 解析
    dir = "questions"
    files = ["README.md", "index.md", "q*.md"]
    kind = "extra"
    label = "问题池"
"""

import re
from dataclasses import dataclass, field
from datetime import date as _date
from pathlib import Path
from typing import List, Optional, Tuple

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

from . import config
from .model import Volume

CONFIG_NAME = "quire.toml"

_CN_NUMS = r'[〇一二三四五六七八九十百\d]+'
_VOL_SEP = r'[·\s\u3000]+'

_TOC_NAMES = ("目录.md", "toc.md", "_toc.md", "summary.md")
_FRONT_STEMS = ("preface", "foreword", "intro", "introduction", "readme",
                "前言", "序", "序言", "导言")
_DEFAULT_FRONT_PREFIXES = ("导", "前言")


# ---------------------------------------------------------------- 排序 key

def _natural_key(text: str) -> list:
    """自然排序 key：ch2 < ch10（ASCII 序会把 ch10 排到 ch2 前）。"""
    return [(0, int(part)) if part.isdigit() else (1, part)
            for part in re.split(r'(\d+)', text)]


def _volume_sort_key(path: Path) -> tuple:
    """卷目录排序：volN/卷N 自然序在前，其余目录按名序在后。"""
    name = path.name
    for pattern in (r'^vol(\d+)$', r'^卷(\d+)$'):
        match = re.match(pattern, name)
        if match:
            return (0, int(match.group(1)), name)
    return (1, 0, name)


def _chapter_sort_key(path: Path, front_prefixes: tuple) -> tuple:
    """章节排序：front_prefixes 命中者在前（按前缀序），其余按自然名序。

    字符串排序下 ASCII 'c' < CJK '导'，会把 ch* 排到 导* 前面；
    前缀分桶解决之，桶内自然排序解决 ch2/ch10 问题。
    """
    name = path.name
    for bucket, prefix in enumerate(front_prefixes):
        if name.startswith(prefix):
            return (bucket, [(1, "")], _natural_key(name))
    return (len(front_prefixes), [(0, 0)], _natural_key(name))


# ---------------------------------------------------------------- 标题解析

def parse_volume_title(first_line: str, series: str) -> Tuple[str, str]:
    """从目录/首页首行解析 (主标题, 副标题)。支持四种书写格式。"""
    line = first_line.strip()

    # 格式A: # 《...》（...）目录
    match = re.match(r'^#\s*《(.+?)》（(.+?)）(?:\s*目录)?$', line)
    if match:
        title_raw, subtitle_raw = match.group(1), match.group(2)
        if ('：' in title_raw or ':' in title_raw) and '卷' in title_raw.split('：')[0].split(':')[0]:
            parts = re.split(r'[：:]', title_raw, maxsplit=1)
            title = parts[-1]
            subtitle = f"{parts[0]} · {subtitle_raw}"
        else:
            title = title_raw
            subtitle = subtitle_raw
        return title, subtitle

    # 格式B: # 《...》目录
    match = re.match(r'^#\s*《(.+?)》(?:\s*目录)?$', line)
    if match:
        title_raw = match.group(1)
        if ('：' in title_raw or ':' in title_raw) and '卷' in title_raw.split('：')[0].split(':')[0]:
            parts = re.split(r'[：:]', title_raw, maxsplit=1)
            title = parts[-1]
            subtitle = parts[0]
        else:
            title = title_raw
            subtitle = series
        return title, subtitle

    # 格式C: # 卷X　标题
    match = re.match(r'^#\s*(卷' + _CN_NUMS + r')' + _VOL_SEP + r'(.+?)$', line)
    if match:
        return match.group(2), match.group(1)

    # 格式D: # 纯标题
    match = re.match(r'^#\s*(.+?)$', line)
    if match:
        return match.group(1), series

    return line.lstrip('#').strip(), series


def _first_line(path: Path) -> str:
    return path.read_text(encoding="utf-8").split("\n", 1)[0]


def _first_h1(path: Path) -> Optional[str]:
    """读文件首个 H1 文本（无 H1 返回 None）。"""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            return line.lstrip("#").strip()
    return None


def _find_toc(volume_dir: Path) -> Optional[Path]:
    for name in _TOC_NAMES:
        candidate = volume_dir / name
        if candidate.exists():
            return candidate
    return None


def _is_front_stem(stem: str) -> bool:
    return stem in _FRONT_STEMS or stem.lower() in _FRONT_STEMS


# ---------------------------------------------------------------- 文件收集

def collect_volume_files(volume_dir: Path, root: Path, front_prefixes: tuple) -> List[str]:
    """收集一卷的 md 文件：前言 → 目录 → chapters/*.md（按序）。

    无 chapters/ 时，卷目录下的其余 *.md 即章节（前言 / 目录不重复计入）。
    """
    files: List[str] = []
    taken = {volume_dir / name for name in _TOC_NAMES}

    for front in ("前言.md", "preface.md", "序.md"):
        candidate = volume_dir / front
        if candidate.exists():
            files.append(str(candidate.relative_to(root)))
            taken.add(candidate)
            break

    toc = _find_toc(volume_dir)
    if toc is not None:
        files.append(str(toc.relative_to(root)))

    chapters_dir = volume_dir / "chapters"
    if chapters_dir.is_dir():
        members = list(chapters_dir.glob("*.md"))
    else:
        members = [p for p in volume_dir.glob("*.md") if p not in taken]
    files.extend(str(p.relative_to(root))
                 for p in sorted(members, key=lambda p: _chapter_sort_key(p, front_prefixes)))
    return files


def _expand_pattern(root: Path, base: Path, pattern: str) -> List[str]:
    """在 base 下展开 pattern（支持 glob），返回相对 root 的路径。"""
    if any(ch in pattern for ch in "*?["):
        return [str(m.relative_to(root)) for m in
                sorted(base.glob(pattern), key=lambda p: _natural_key(str(p.relative_to(root))))]
    candidate = base / pattern
    return [str(candidate.relative_to(root))] if candidate.exists() else []


# ---------------------------------------------------------------- 自动发现

def _auto_discover(book: "Book") -> List[Volume]:
    root: Path = book.root
    content = root / book.content_dir
    if not content.is_dir():
        # 无内容目录：根下 chapters/ 或根下 md 也可构成一本书
        if (root / "chapters").is_dir() or any(root.glob("*.md")):
            content = root
        else:
            return []

    volumes: List[Volume] = []

    # 1) 散置单文件：前置类在前，其余按自然序
    loose = sorted(content.glob("*.md"),
                   key=lambda p: (0 if _is_front_stem(p.stem) else 1, _natural_key(p.stem)))
    for path in loose:
        kind = "front" if _is_front_stem(path.stem) else "extra"
        title = _first_h1(path) or path.stem
        volumes.append(Volume(path.stem, title, book.series,
                               [str(path.relative_to(root))], kind=kind))

    # 2) 子目录 → 卷
    for directory in sorted((p for p in content.iterdir() if p.is_dir()),
                            key=_volume_sort_key):
        toc = _find_toc(directory)
        has_chapters = (directory / "chapters").is_dir()
        if toc is None and not has_chapters:
            if directory.name == "chapters" and any(directory.glob("*.md")):
                # 内容目录直属 chapters/ → 单卷书正文
                files = [str(p.relative_to(root)) for p in
                         sorted(directory.glob("*.md"),
                                key=lambda p: _chapter_sort_key(p, book.front_prefixes))]
                volumes.append(Volume("main", book.series, book.series, files))
            continue  # 其余无目录无章节的目录：草稿区，跳过

        if toc is not None:
            title, subtitle = parse_volume_title(_first_line(toc), book.series)
        else:
            title, subtitle = directory.name, book.series
        files = collect_volume_files(directory, root, book.front_prefixes)
        if files:
            volumes.append(Volume(directory.name, title, subtitle, files))

    return volumes


# ---------------------------------------------------------------- 显式卷

def _volume_from_spec(book: "Book", spec: dict) -> Volume:
    vid = spec.get("id")
    if not vid:
        raise ValueError(f"[[volume]] 缺少 id：{spec}")
    kind = spec.get("kind", "main")
    label = spec.get("label", "")
    base = book.root / spec["dir"] if spec.get("dir") else book.root

    files: List[str] = []
    if "file" in spec:
        files = _expand_pattern(book.root, book.root, spec["file"])
    elif "files" in spec:
        for pattern in spec["files"]:
            files.extend(_expand_pattern(book.root, base, pattern))
    else:
        files = collect_volume_files(base, book.root, book.front_prefixes)
    for extra in spec.get("extra", []):
        files.extend(_expand_pattern(book.root, book.root, extra))
    if not files:
        raise ValueError(f"卷 {vid} 未发现任何源文件（dir/file/files 配置有误？）")

    # 标题回退链：显式 > 目录首行 > 首文件首 H1 > 目录名 > 卷 id
    toc = _find_toc(base) if base != book.root else None
    if toc is not None:
        parsed_title, parsed_subtitle = parse_volume_title(_first_line(toc), book.series)
    else:
        parsed_title, parsed_subtitle = "", ""
    first_file_title = (_first_h1(book.root / files[0]) or "") if files else ""
    title = (spec.get("title") or parsed_title or first_file_title
             or (Path(spec["dir"]).name if spec.get("dir") else "")
             or (Path(files[0]).stem if "file" in spec else vid))
    subtitle = spec.get("subtitle") or parsed_subtitle or book.series

    return Volume(vid, title, subtitle, files, label=label, kind=kind)


def _discover(book: "Book") -> List[Volume]:
    """自动发现 + 显式 [[volume]] 合并（显式按 id 覆盖，否则追加）。"""
    volumes = _auto_discover(book)
    for spec in book.volume_specs:
        volume = _volume_from_spec(book, spec)
        for index, existing in enumerate(volumes):
            if existing.id == volume.id:
                volumes[index] = volume
                break
        else:
            volumes.append(volume)
    return volumes


# ---------------------------------------------------------------- Book

@dataclass
class Book:
    root: Path
    series: str = ""
    author: str = ""
    author_email: str = ""
    pub_date: str = ""
    pub_date_display: str = ""
    language: str = "zh-CN"
    content_dir: str = "book"
    output_dir: str = "dist"
    size_limit: int = 1_000_000
    theme_dir: Optional[Path] = None
    fonts_dir: Optional[Path] = None
    cover_plugin: Optional[Path] = None
    front_prefixes: tuple = _DEFAULT_FRONT_PREFIXES
    volume_specs: list = field(default_factory=list)
    config_path: Optional[Path] = None
    volumes: List[Volume] = field(default_factory=list)

    @property
    def dist(self) -> Path:
        return self.root / self.output_dir

    @property
    def epub_css_path(self) -> Path:
        return config.asset_path("css/epub.css")

    def rescan(self) -> List[Volume]:
        """重新扫描卷集（预览服务器每次请求都会调用）。"""
        self.volumes = _discover(self)
        return self.volumes

    def volume(self, volume_id: str) -> Optional[Volume]:
        return next((v for v in self.volumes if v.id == volume_id), None)


# ---------------------------------------------------------------- 载入

def find_root(start: Optional[str] = None) -> Path:
    """自 start（默认 CWD）向上查找 quire.toml；未找到则原样返回。"""
    current = Path(start).resolve() if start else Path.cwd().resolve()
    if current.is_file():
        current = current.parent
    for directory in (current, *current.parents):
        if (directory / CONFIG_NAME).is_file():
            return directory
    return current


def _format_date_display(pub_date: str) -> str:
    match = re.match(r'^(\d{4})-(\d{1,2})', pub_date)
    if not match:
        return pub_date
    return f"{match.group(1)}年{int(match.group(2))}月"


def load_book(root: Optional[str] = None, *, activate: bool = True) -> Book:
    """载入一本书：解析 quire.toml（可选）+ 发现卷集。

    root 未给时，自当前位置向上找 quire.toml 定位书根；找不到则以
    当前目录按约定扫描（无配置也能构建）。
    """
    root_path = Path(root).resolve() if root else find_root()
    config_path = root_path / CONFIG_NAME
    data = {}
    if config_path.is_file():
        with open(config_path, "rb") as handle:
            data = tomllib.load(handle)

    meta = data.get("book", {})
    theme = data.get("theme", {}).get("dir")
    fonts = data.get("fonts", {}).get("dir")
    cover = data.get("cover", {}).get("plugin")
    layout = data.get("layout", {})
    pub_date = meta.get("date") or _date.today().strftime("%Y-%m")

    book = Book(
        root=root_path,
        series=meta.get("title") or meta.get("series") or root_path.name,
        author=meta.get("author", ""),
        author_email=meta.get("email", ""),
        pub_date=pub_date,
        pub_date_display=meta.get("date_display") or _format_date_display(pub_date),
        language=meta.get("language", "zh-CN"),
        content_dir=meta.get("content", "book"),
        output_dir=meta.get("output", "dist"),
        size_limit=int(meta.get("size_limit", 1_000_000)),
        theme_dir=root_path / theme if theme else None,
        fonts_dir=root_path / fonts if fonts else None,
        cover_plugin=root_path / cover if cover else None,
        front_prefixes=tuple(layout.get("front_prefixes", _DEFAULT_FRONT_PREFIXES)),
        volume_specs=list(data.get("volume", [])),
        config_path=config_path if config_path.is_file() else None,
    )
    book.rescan()
    if activate:
        config.activate(book)
    return book


# ---------------------------------------------------------------- 统计

_CJK_RE = re.compile(r'[\u3400-\u4dbf\u4e00-\u9fff]')


def count_words(text: str) -> int:
    """统计 CJK 字符数（书籍字数的通行口径）。"""
    return len(_CJK_RE.findall(text))


def file_words(root: Path, relpath: str) -> int:
    return count_words((root / relpath).read_text(encoding="utf-8"))


def volume_words(book: Book, volume: Volume) -> int:
    return sum(file_words(book.root, relpath) for relpath in volume.files)
