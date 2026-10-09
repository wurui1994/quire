"""quire：Markdown 书籍构建工具链（HTML / EPUB / PDF / 博客 / 预览）。

用法（CLI）::

    quire init mybook --title 书名
    quire new vol1/新章
    quire build          # HTML + EPUB + PDF → dist/
    quire serve          # 预览 http://localhost:8000

用法（SDK）::

    from quire import load_book, build
    book = load_book("/path/to/book")
    outputs = build(book, html=True, epub=False, pdf=False)

本包刻意保持轻量：ebooklib / fitz / markdown-it 均在构建时按需导入。
"""

__version__ = "0.1.0"

from .api import build
from .book import Book, load_book, find_root
from .model import Volume
from .scaffold import init_project, new_chapter

__all__ = ["__version__", "Book", "Volume", "load_book", "find_root", "build",
           "init_project", "new_chapter"]
