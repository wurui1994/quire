"""运行时配置：当前书籍上下文 + 元数据 + 主题资产加载。

与绑定单一丛书的旧脚本不同，quire 把「书籍」做成一等对象：

    book = quire.load_book("/path/to/repo")   # 解析 quire.toml + 自动发现卷
    quire.build(book, html=True, pdf=True)   # 构建期间 book 处于激活状态

激活后，本模块成为全包的运行时配置入口（属性按访问时解析，支持多书切换）：

- 元数据：config.SERIES / AUTHOR / PUB_DATE / PUB_DATE_DISPLAY / SIZE_LIMIT …
  转发当前 Book 的对应字段（小写）。
- 路径：config.ROOT（书根）/ config.DIST（产物目录）。
- 资产：config.READER_TEMPLATE / BOOK_CSS / READER_JS … 经模块级 __getattr__
  懒加载，先查书籍主题目录（[theme] dir），回退包内置 assets/——书籍可以用
  同名文件整体换肤，不必改动工具。
- 特例：config.ASSETS（pandoc --data-dir 根）、config.EPUB_CSS_PATH（EPUB 样式
  路径）、config.LANGUAGE。
"""

from pathlib import Path
from typing import Optional

_PACKAGE_DIR = Path(__file__).resolve().parent
BUNDLED_ASSETS = _PACKAGE_DIR / "assets"

# 文本资产：模块属性名 → assets 下相对路径（懒加载，主题覆盖优先）。
_TEXT_ASSETS = {
    "READER_TEMPLATE": "templates/reader.html",
    "INDEX_TEMPLATE": "templates/index.html",
    "COVER_TEMPLATE": "templates/cover.html",
    "BLOG_TEMPLATE": "templates/blog.html",
    "BLOG_INDEX_TEMPLATE": "templates/blog_index.html",
    "BOOK_CSS": "css/book.css",
    "COVER_CSS": "css/cover.css",
    "PRINT_CSS": "css/print.css",
    "BLOG_CSS": "css/blog.css",
    "READER_JS": "js/reader.js",
    "BLOG_JS": "js/blog.js",
}

# 当前激活的 Book（类型见 book.py；此处不导入以避免循环）。
_active: Optional[object] = None


def activate(book) -> None:
    """激活一本书：此后全包经 config.* 读到的元数据 / 路径都属于它。"""
    global _active
    _active = book


def current():
    """返回当前激活的 Book；未激活时给出可操作的错误。"""
    if _active is None:
        raise RuntimeError(
            "尚无激活的书籍：先 quire.load_book(...)（CLI 会自动完成）。")
    return _active


def asset_path(relpath: str) -> Path:
    """解析资产路径：书籍主题目录优先，回退包内置 assets/。"""
    if _active is not None and getattr(_active, "theme_dir", None) is not None:
        candidate = _active.theme_dir / relpath
        if candidate.exists():
            return candidate
    return BUNDLED_ASSETS / relpath


def load_asset(relpath: str) -> str:
    """读取资产文本内容（模板 / 样式 / 脚本），主题覆盖优先。"""
    return asset_path(relpath).read_text(encoding="utf-8")


def fonts_dir() -> Path:
    """字体目录：书籍自带（[fonts] dir）优先，回退包内置 assets/fonts。"""
    if _active is not None and getattr(_active, "fonts_dir", None) is not None:
        return _active.fonts_dir
    return BUNDLED_ASSETS / "fonts"


def fill_template(template: str, **fields) -> str:
    """简单 {{key}} 占位符替换。"""
    for key, value in fields.items():
        template = template.replace("{{" + key + "}}", value)
    return template


def __getattr__(name: str):
    """模块级懒属性：文本资产走主题覆盖；元数据 / 路径转发当前 Book。"""
    if name in _TEXT_ASSETS:
        return load_asset(_TEXT_ASSETS[name])
    if _active is None:
        raise AttributeError(f"模块 quire.config 没有属性 {name!r}（尚无激活书籍）")
    if name == "ASSETS":
        # pandoc --data-dir：主题目录带 translations/ 才可用，否则用内置。
        if (_active.theme_dir is not None
                and (_active.theme_dir / "translations").is_dir()):
            return _active.theme_dir
        return BUNDLED_ASSETS
    if name == "EPUB_CSS_PATH":
        return asset_path("css/epub.css")
    attr = name.lower()
    if hasattr(_active, attr):
        return getattr(_active, attr)
    raise AttributeError(f"模块 quire.config 没有属性 {name!r}")
