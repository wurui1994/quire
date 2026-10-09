"""HTML 阅读页构建（单卷 / 全卷合本）。

- assemble_reader_page   用 reader 模板拼装一页（标题、侧栏目录、正文、脚本）
- build_volume_html      单卷 HTML
- render_all_reader      全卷合本 HTML 字符串（构建与预览共用）
- build_all_html         全卷合本文件
"""

from pathlib import Path
from typing import List

from . import config
from .config import fill_template
from .renderer import render_markdown
from .toc import (extract_headings, build_single_volume_toc_html,
                         build_all_volumes_toc_html)
from .cover import build_cover_html
from .model import Volume, standard_volume_count, volume_index_map

ALL_HTML_EXTRA_CSS = (
    ".toc-link-vol{padding:.2rem 0;font-weight:600}"
    ".toc-vol-divider{display:flex;gap:.4rem;align-items:baseline;margin:.35rem 0 .15rem}"
    ".toc-vol-divider .n{font-variant-numeric:tabular-nums;color:var(--accent)}"
    ".toc-vol-divider .t{font-size:.82rem;font-weight:600}"
    ".reader-main .cover{margin:0 0 2.2rem}"
)


def assemble_reader_page(*, title: str, brand: str, body: str, toc_html: str,
                         extra_css: str = "", indent: bool = False) -> str:
    """用 reader 模板拼装阅读页 HTML。"""
    body_class = "indent" if indent else ""
    return fill_template(config.READER_TEMPLATE,
        title=title, series=config.SERIES, author=config.AUTHOR, css=config.BOOK_CSS,
        extra_css=extra_css, body_cls=body_class, brand=brand,
        toc=toc_html, body=body, js=config.READER_JS)


def build_volume_html(volume: Volume, *, indent: bool = False,
                      vol_index: int = -1, total_vols: int = 0) -> Path:
    """构建单卷 HTML 阅读页 → dist/{id}.html。"""
    fragment = render_markdown(volume.files)
    cover = build_cover_html(volume.id, volume.title, volume.subtitle,
                             vol_index=vol_index, total_vols=total_vols,
                             is_preface=volume.is_preface)
    extra_css = f"<style>{config.COVER_CSS}</style>" if cover else ""
    headings = extract_headings(fragment)
    toc_html = build_single_volume_toc_html(headings)
    html = assemble_reader_page(title=volume.title, brand=volume.subtitle or config.SERIES,
                                body=cover + fragment, toc_html=toc_html,
                                extra_css=extra_css, indent=indent)
    out = config.DIST / f"{volume.id}.html"
    out.write_text(html, encoding="utf-8")
    return out


def render_all_reader(volumes: List[Volume], *, indent: bool = False) -> str:
    """全卷合本 HTML 字符串（构建与预览服务器共用）。"""
    total_vols = standard_volume_count(volumes)
    index_map = volume_index_map(volumes)
    all_fragments: List[str] = []
    volume_headings = []
    for volume in volumes:
        fragment = render_markdown(volume.files)
        cover = build_cover_html(volume.id, volume.title, volume.subtitle,
                                 vol_index=index_map.get(volume.id, -1),
                                 total_vols=total_vols, is_preface=volume.is_preface)
        all_fragments.append(cover)
        all_fragments.append(fragment)
        volume_headings.append((volume.id, volume.subtitle, volume.title, extract_headings(fragment)))
    toc_html = build_all_volumes_toc_html(volume_headings)
    extra_css = f"<style>{config.COVER_CSS}</style><style>{ALL_HTML_EXTRA_CSS}</style>"
    return assemble_reader_page(title=f"{config.SERIES}丛书 · 全卷", brand=f"{config.SERIES}丛书 · 全卷",
                                body="".join(all_fragments), toc_html=toc_html,
                                extra_css=extra_css, indent=indent)


def build_all_html(volumes: List[Volume], *, indent: bool = False) -> Path:
    """构建全卷合本 HTML 文件 → dist/all.html。"""
    html = render_all_reader(volumes, indent=indent)
    out = config.DIST / "all.html"
    out.write_text(html, encoding="utf-8")
    return out
