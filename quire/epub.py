"""EPUB 构建。

提供两套引擎：
- Python（ebooklib，默认）：用现有 markdown-it 渲染管线产出 HTML，再交
  ebooklib 打包成 EPUB3；结构严格对齐 pandoc 产物（按章分 xhtml、
  text/styles 子目录、titlepage、landmarks、ibooks display-options 等）。
- pandoc（可选，--epub-engine pandoc）：保留历史实现，作为后备/对照。

【KB-003】历史告警及修法（详见 scripts/assets/translations/zh.yaml 头注释）：
1. 数学公式被渲染成原始 TeX：pandoc 对 epub3 默认不转换数学，需显式 --mathml。
   Python 路径由渲染器直接产出 MathML，天然无此问题。
2. 中文翻译缺失告警：pandoc 3.10 未随附中文翻译数据；Python 路径不经过
   pandoc，也无此告警。

【KB-004】XHTML 良构：EPUB3 内容文档必须是 XML 良构的 XHTML。经全库扫描
（74 万字 HTML），markdown-it 产物已相当干净——无裸 &、MathML 均带 xmlns，
唯一未自闭合的 void 元素是 <hr>（全库仅 5 处）。故 Python 路径仅做极简的
void 自闭合归一（_selfclose_void），不引入 html5lib/lxml 之类重型转换。
"""

import re
import hashlib
from typing import List, Tuple
from pathlib import Path
from xml.sax.saxutils import escape as _xml_escape

from ebooklib import epub

from . import config
from .model import Volume, all_volume_files
from .renderer import render_markdown
from .toc import extract_headings


_VOID_TAGS = ("area", "base", "br", "col", "embed", "hr", "img", "input",
              "link", "meta", "param", "source", "track", "wbr")
_VOID_RE = re.compile(r"<(" + "|".join(_VOID_TAGS) + r")((?:\s+[^>]*)?)(?<!/)>")


def _selfclose_void(html: str) -> str:
    return _VOID_RE.sub(lambda m: f"<{m.group(1)}{m.group(2)}/>", html)


class _RawXhtml(epub.EpubHtml):
    """EpubHtml 子类，绕过 ebooklib 的模板重建，直接输出预构建的完整 XHTML。

    ebooklib 的 EpubHtml.get_content() 会用模板重建 XHTML（拆解 body 子元素、
    重建 head），导致 epub:type、自定义 <style> 块、CSS <link> 等属性丢失。
    此子类覆盖 get_content()，直接返回 _raw_content 字节。
    """

    def __init__(self, *, uid, file_name, title, raw_content: bytes):
        super().__init__(uid=uid, file_name=file_name, title=title,
                         content=raw_content)
        self._raw_content = raw_content

    def get_content(self, default=None):
        return self._raw_content


_CSS_HREF = "../styles/stylesheet1.css"


def _xhtml_doc(title: str, body_attr: str, body_inner: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops" '
        f'lang="{config.LANGUAGE}" xml:lang="{config.LANGUAGE}">\n'
        '<head>\n'
        '  <meta charset="utf-8" />\n'
        f'  <title>{_xml_escape(title)}</title>\n'
        '  <style>\n  </style>\n'
        f'  <link rel="stylesheet" type="text/css" href="{_CSS_HREF}" />\n'
        '</head>\n'
        f'<body {body_attr}>\n'
        f'{body_inner}\n'
        '</body>\n</html>'
    )


def _title_page_xhtml(title: str, author: str, date: str) -> str:
    body = (
        '<section epub:type="titlepage" class="titlepage">\n'
        f'  <h1 class="title">{_xml_escape(title)}</h1>\n'
        f'  <p class="author">{_xml_escape(author)}</p>\n'
        f'  <p class="date">{_xml_escape(date)}</p>\n'
        '</section>'
    )
    return _xhtml_doc(title, 'epub:type="frontmatter"', body)


def _chapter_xhtml(title: str, body: str) -> str:
    body = _selfclose_void(body)
    return _xhtml_doc(title, 'epub:type="bodymatter"', body)


def _render_volume_chapters(volume: Volume,
                            link_map: dict = None) -> List[Tuple[str, str, List]]:
    """逐文件渲染为章节 XHTML。link_map 把目录页 .md 链接改写为
    同级文档 href（如 ch001.xhtml；EPUB 内所有章节文档平铺于 text/）。"""
    results = []
    for relpath in volume.files:
        md = render_markdown([relpath], math_format="html", link_map=link_map)
        headings = extract_headings(md)
        title = headings[0][2] if headings else Path(relpath).stem
        xhtml = _chapter_xhtml(title, md)
        results.append((title, xhtml, headings))
    return results


def _build_toc(headings: List, ch_href: str, *, skip_first: bool = False) -> list:
    """扁平 headings → ebooklib 嵌套 TOC。

    skip_first: 当章节已由外层 epub.Section 代表时，跳过第一个 h1
    （否则 Section 与其下第一个子节点指向同一锚点，产生重复目录项）。
    """
    effective = headings[1:] if skip_first and headings else headings
    root: list = []
    stack = [(-1, root)]
    for hid, level, text in effective:
        href = ch_href + "#" + hid
        while stack and stack[-1][0] >= level:
            stack.pop()
        children: list = []
        stack[-1][1].append((epub.Section(text, href), children))
        stack.append((level, children))

    counter = [0]

    def freeze(lst):
        out = []
        for sec, ch in lst:
            if ch:
                out.append((sec, freeze(ch)))
            else:
                counter[0] += 1
                out.append(epub.Link(sec.href, sec.title, uid=f"toc-{counter[0]}"))
        return tuple(out)

    return freeze(root)


def _ibooks_display_options() -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<display_options>\n'
        '  <platform name="*">\n'
        '    <option name="specified-fonts">true</option>\n'
        '  </platform>\n'
        '</display_options>\n'
    ).encode("utf-8")


def _assemble_book(identifier: str, title: str, author: str,
                    title_page: _RawXhtml,
                    chapters: List[_RawXhtml],
                    toc) -> epub.EpubBook:
    book = epub.EpubBook()
    book.set_identifier(identifier)
    book.set_title(title)
    book.set_language(config.LANGUAGE)
    book.add_author(author)

    book.add_metadata("http://purl.org/dc/elements/1.1/", "date", config.PUB_DATE,
                      {"id": "epub-date"})
    creator_id = None
    for ns_name, values in book.metadata.items():
        for name, vals in values.items():
            for v in vals:
                if name == "creator" and v[1] and v[1].get("id"):
                    creator_id = v[1]["id"]
    if creator_id:
        book.add_metadata("http://www.idpf.org/2007/opf", "meta", "",
                          {"refines": f"#{creator_id}", "property": "role",
                           "scheme": "marc:relators", "content": "aut"})

    for prop in ("textual",):
        book.add_metadata("http://www.idpf.org/2007/opf", "meta", prop,
                          {"property": "schema:accessMode"})
        book.add_metadata("http://www.idpf.org/2007/opf", "meta", prop,
                          {"property": "schema:accessModeSufficient"})
    for feat in ("alternativeText", "readingOrder",
                 "structuralNavigation", "tableOfContents"):
        book.add_metadata("http://www.idpf.org/2007/opf", "meta", feat,
                          {"property": "schema:accessibilityFeature"})
    book.add_metadata("http://www.idpf.org/2007/opf", "meta", "none",
                      {"property": "schema:accessibilityHazard"})

    book.add_prefix("ibooks", "http://vocabulary.itunes.apple.com/rdf/ibooks/vocabulary-extensions-1.0/")

    css = epub.EpubItem(uid="stylesheet1", file_name="styles/stylesheet1.css",
                        media_type="text/css",
                        content=config.EPUB_CSS_PATH.read_text(encoding="utf-8").encode("utf-8"))
    book.add_item(css)

    ibooks_opts = epub.EpubItem(
        uid="ibooks_display_options",
        file_name="META-INF/com.apple.ibooks.display-options.xml",
        media_type="application/xml",
        content=_ibooks_display_options(),
        manifest=False)
    book.add_item(ibooks_opts)

    book.add_item(title_page)
    for ch in chapters:
        book.add_item(ch)

    book.toc = toc
    book.add_item(epub.EpubNcx())
    nav = epub.EpubNav(uid="nav", file_name="nav.xhtml", title=title)
    book.add_item(nav)

    book.guide.append({"type": "toc", "title": title,
                        "href": "nav.xhtml"})
    book.guide.append({"type": "titlepage", "title": "Title Page",
                        "item": title_page})

    book.spine = ["nav", title_page] + chapters
    return book


def _write_epub(book: epub.EpubBook, out_path: Path) -> None:
    epub.write_epub(str(out_path), book,
                    options={"epub3_landmark": True, "epub2_guide": True})


def build_epub_py(volume: Volume) -> Path:
    title = f"{volume.title}（{volume.subtitle}）" if volume.subtitle else volume.title
    identifier = "urn:uuid:" + hashlib.md5(volume.id.encode()).hexdigest()

    title_page = _RawXhtml(
        uid="title_page_xhtml", file_name="text/title_page.xhtml",
        title=title,
        raw_content=_title_page_xhtml(title, config.AUTHOR, config.PUB_DATE).encode("utf-8"))

    chapters_data = _render_volume_chapters(
        volume, {relpath: f"ch{i:03d}.xhtml"
                 for i, relpath in enumerate(volume.files, 1)})
    chapters: List[_RawXhtml] = []
    for i, (ch_title, ch_xhtml, _) in enumerate(chapters_data, 1):
        ch = _RawXhtml(
            uid=f"ch{i:03d}_xhtml", file_name=f"text/ch{i:03d}.xhtml",
            title=ch_title,
            raw_content=ch_xhtml.encode("utf-8"))
        chapters.append(ch)

    if len(chapters_data) == 1:
        toc = list(_build_toc(chapters_data[0][2], "text/ch001.xhtml"))
    else:
        toc = []
        for i, (ch_title, _, headings) in enumerate(chapters_data, 1):
            ch_fname = f"text/ch{i:03d}.xhtml"
            ch_toc = _build_toc(headings, ch_fname, skip_first=True)
            if headings:
                sec_href = ch_fname + "#" + headings[0][0]
                if ch_toc:
                    toc.append((epub.Section(ch_title, sec_href), ch_toc))
                else:
                    toc.append(epub.Link(sec_href, ch_title,
                                         uid=f"toc-ch{i:03d}"))
            elif ch_toc:
                toc.extend(ch_toc)

    book = _assemble_book(identifier, title, config.AUTHOR, title_page, chapters, toc)
    out = config.DIST / f"{volume.id}.epub"
    _write_epub(book, out)
    return out


def build_all_epub_py(volumes: List[Volume]) -> Path:
    title = f"{config.SERIES}丛书（全卷）"
    identifier = "urn:uuid:" + hashlib.md5(b"all").hexdigest()

    title_page = _RawXhtml(
        uid="title_page_xhtml", file_name="text/title_page.xhtml",
        title=title,
        raw_content=_title_page_xhtml(title, config.AUTHOR, config.PUB_DATE).encode("utf-8"))

    chapters: List[_RawXhtml] = []
    toc = []
    ch_counter = 0
    last_vol_headings = []
    # 全卷 EPUB 文档跨卷连续编号，故映射一次算好（与下方 ch_counter 同序）
    doc_map = {relpath: f"ch{index:03d}.xhtml"
               for index, relpath in enumerate(all_volume_files(volumes), 1)}

    for volume in volumes:
        vol_chapters = _render_volume_chapters(volume, doc_map)
        vol_toc_children = []
        for ch_title, ch_xhtml, headings in vol_chapters:
            ch_counter += 1
            ch = _RawXhtml(
                uid=f"ch{ch_counter:03d}_xhtml",
                file_name=f"text/ch{ch_counter:03d}.xhtml",
                title=ch_title,
                raw_content=ch_xhtml.encode("utf-8"))
            chapters.append(ch)
            ch_toc = _build_toc(headings, f"text/ch{ch_counter:03d}.xhtml", skip_first=True)
            if headings:
                sec_href = f"text/ch{ch_counter:03d}.xhtml#{headings[0][0]}"
                if ch_toc:
                    vol_toc_children.append(
                        (epub.Section(ch_title, sec_href), ch_toc))
                else:
                    vol_toc_children.append(
                        epub.Link(sec_href, ch_title,
                                  uid=f"toc-ch{ch_counter:03d}"))
            elif ch_toc:
                vol_toc_children.extend(ch_toc)
            last_vol_headings = headings

        if last_vol_headings and vol_toc_children:
            first_ch_num = ch_counter - len(vol_chapters) + 1
            first_href = f"text/ch{first_ch_num:03d}.xhtml#{last_vol_headings[0][0]}"
            toc.append((epub.Section(
                volume.heading, first_href),
                tuple(vol_toc_children)))
        elif vol_toc_children:
            toc.extend(vol_toc_children)

    book = _assemble_book(identifier, title, config.AUTHOR, title_page, chapters, toc)
    out = config.DIST / "all.epub"
    _write_epub(book, out)
    return out


# --------------------------------------------------------------------------
# pandoc 引擎（可选，--epub-engine pandoc）：保留历史实现作为对照/后备。
# --------------------------------------------------------------------------

def _pandoc_epub(out, files: List[str], title: str) -> None:
    import subprocess
    subprocess.run(["pandoc", "-f", "markdown-smart+superscript", "-t", "epub3",
                   "--css", str(config.EPUB_CSS_PATH),
                   "--mathml",
                   "--data-dir", str(config.ASSETS),
                   "-M", f"title={title}",
                   "-M", f"author={config.AUTHOR}",
                   "-M", f"date={config.PUB_DATE}",
                   "-M", f"lang={config.LANGUAGE}",
                   "-o", str(out), *files],
                  cwd=config.ROOT, check=True)


def build_epub_pandoc(volume: Volume) -> Path:
    out = config.DIST / f"{volume.id}.epub"
    _pandoc_epub(out, volume.files,
                 f"{volume.title}（{volume.subtitle}）" if volume.subtitle else volume.title)
    return out


def build_all_epub_pandoc(volumes: List[Volume]) -> Path:
    out = config.DIST / "all.epub"
    _pandoc_epub(out, all_volume_files(volumes), f"{config.SERIES}丛书（全卷）")
    return out


build_epub = build_epub_py
build_all_epub = build_all_epub_py
