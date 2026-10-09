"""目录（TOC）提取与渲染。

- extract_headings      从渲染后的 HTML 片段提取 (id, level, text) 列表
- build_toc_tree        扁平项 → 嵌套树（用于收缩/展开）
- render_toc_tree       嵌套树 → 侧栏 HTML
- build_single_volume_toc_html   单卷目录
- build_all_volumes_toc_html     全卷合本目录（每卷为一级节点）
"""

import re
from typing import List, Tuple

# (id, level, text)
Heading = Tuple[str, int, str]
# (id, level, text, cls)
TocItem = Tuple[str, int, str, str]

_CHEVRON_SVG = (
    '<svg class="chev" viewBox="0 0 16 16" aria-hidden="true">'
    '<path d="M6 4l4 4-4 4" fill="none" stroke="currentColor" '
    'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>'
)


def extract_headings(html: str) -> List[Heading]:
    """从渲染后的 HTML 片段提取所有 h1~h3 的 (id, 层级, 文本)。"""
    headings: List[Heading] = []
    for match in re.finditer(r'<(h[1-3])\s+id="([^"]*)">(.*?)</\1>', html):
        tag, heading_id, inner = match.group(1), match.group(2), match.group(3)
        text = re.sub(r'<[^>]+>', '', inner).strip()
        level = int(tag[1])
        headings.append((heading_id, level, text))
    return headings


def build_toc_tree(items: List[TocItem]):
    """扁平 (id, level, text, cls) 列表 → 嵌套树。

    返回根层级的子节点列表（哨兵根层级记为 -1）。
    """
    root: list = []
    stack = [(-1, None, None, "", root)]
    for heading_id, level, text, cls in items:
        node = [level, heading_id, text, cls, []]
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack[-1][4].append(node)
        stack.append(node)
    return root


def render_toc_tree(nodes, *, collapse_from: int = 2, article_level: int = 1) -> str:
    """嵌套树 → 侧栏目录 HTML（支持收缩与文章标记）。"""
    out: List[str] = []

    def recurse(children, depth: int) -> None:
        for level, heading_id, text, cls, grandchildren in children:
            has_children = bool(grandchildren)
            list_class = f"h{level}" if level >= 2 else ""
            if cls:
                list_class = (list_class + " " + cls).strip()
            if level == article_level:
                list_class = (list_class + " md").strip()
            if has_children:
                list_class = (list_class + " node").strip()
                collapsed = depth >= collapse_from
                if collapsed:
                    list_class = (list_class + " collapsed").strip()
                aria_expanded = "false" if collapsed else "true"
                toggle = (
                    '<button class="tw" type="button" aria-label="展开/收起" '
                    f'aria-expanded="{aria_expanded}">{_CHEVRON_SVG}</button>'
                )
            else:
                toggle = ""
            out.append(
                f'<li class="{list_class}"><div class="row">{toggle}'
                f'<a href="#{heading_id}">{text}</a></div>'
            )
            if has_children:
                out.append('<ul class="sub">')
                recurse(grandchildren, depth + 1)
                out.append("</ul>")
            out.append("</li>")

    recurse(nodes, 1)
    return "\n".join(out)


def build_single_volume_toc_html(headings: List[Heading]) -> str:
    """单卷：章节作为一级条目。"""
    items: List[TocItem] = [(heading_id, level, text, "") for heading_id, level, text in headings]
    return render_toc_tree(build_toc_tree(items), collapse_from=1, article_level=1)


def build_all_volumes_toc_html(volume_headings) -> str:
    """全卷合本：每卷为一级节点，标题层级整体下沉一级。

    volume_headings: list of (volume_id, subtitle, title, headings)
    """
    items: List[TocItem] = []
    for volume_id, subtitle, title, headings in volume_headings:
        items.append((f"vol-{volume_id}", 1, f"{subtitle}　{title}", "vol-link"))
        for heading_id, level, text in headings:
            items.append((heading_id, min(level + 1, 3), text, ""))
    return render_toc_tree(build_toc_tree(items), collapse_from=1, article_level=2)
