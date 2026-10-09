"""根目录索引页 dist/index.html。"""

from typing import List

from . import config
from .config import fill_template
from .model import Volume


def build_index(volumes: List[Volume]) -> None:
    """为每卷生成 HTML/EPUB/PDF 下载入口，并附全卷合本链接。"""
    rows = "\n".join(
        f'<li><span class="t">{volume.subtitle}　<b>{volume.title}</b></span>'
        f'<span class="l">'
        f'<a href="{volume.id}.html">HTML</a>'
        f'<a href="{volume.id}.epub">EPUB</a>'
        f'<a href="{volume.id}.pdf">PDF</a>'
        f'</span></li>'
        for volume in volumes)
    all_links = ('<a href="all.html">HTML</a>'
                 '<a href="all.epub">EPUB</a>'
                 '<a href="all.pdf">PDF</a>')
    html = fill_template(config.INDEX_TEMPLATE, series=config.SERIES, items=rows, all_links=all_links)
    (config.DIST / "index.html").write_text(html, encoding="utf-8")
