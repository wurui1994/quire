"""博客模式：每篇 md 独立成页 + 博客首页索引。"""

import re
from pathlib import Path
from typing import Dict, List

from . import config
from .config import fill_template
from .renderer import render_markdown
from .toc import extract_headings
from .model import Volume


def strip_first_h1(html: str):
    """去掉渲染结果中的首个 H1（标题已用于页面 <title>/品牌）。"""
    match = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL)
    if not match:
        return "未命名", html
    title = re.sub(r'<[^>]+>', '', match.group(1)).strip()
    body = html[:match.start()] + html[match.end():]
    return title, body


def extract_tags(body_html: str, limit: int = 6) -> List[str]:
    """提取正文前 3 段中文关键词作为标签（简单词频，去标点）。"""
    paragraphs = re.findall(r'<p>(.*?)</p>', body_html, re.DOTALL)[:3]
    text = " ".join(re.sub(r'<[^>]+>', '', p) for p in paragraphs)
    text = re.sub(r'[^\u4e00-\u9fff]', ' ', text)
    words = [w for w in re.findall(r'[\u4e00-\u9fff]{2,}', text)]
    seen = set()
    tags: List[str] = []
    for word in words:
        if word not in seen:
            seen.add(word)
            tags.append(word)
        if len(tags) >= limit:
            break
    return tags


def estimate_reading_time(source: str) -> int:
    """按中文字数估算阅读时长（分钟），至少 1 分钟。"""
    char_count = len(re.sub(r'\s+', '', source))
    return max(1, char_count // 350)


def chapter_number(slug: str) -> str:
    """从文件名推测章号（如 ch03 / 12）。"""
    match = re.match(r'.*[-_ ]?ch(\d+)|.*[-_ ]?(\d+)$', slug, re.I)
    return (match.group(1) or match.group(2)) if match else ""


def blog_volume_label(volume: "Volume") -> str:
    """博客侧栏 / 索引中显示的卷分组名：label → subtitle → 丛书名。"""
    return volume.label or volume.subtitle or config.SERIES


def build_onpage_toc(headings) -> str:
    """正文右侧的「本文目录」悬浮按钮 + 弹出列表。"""
    items = []
    for heading_id, level, text in headings:
        if level < 2:
            continue
        item_class = f"h{level}" if level >= 2 else ""
        items.append(f'<li class="{item_class}"><a href="#{heading_id}">{text}</a></li>')
    if not items:
        return ""
    return ('<div class="toc-box collapsed">'
            '<button class="toc-toggle" type="button" aria-label="本文目录" aria-expanded="false"></button>'
            '<div class="toc-pop"><ul class="toc-list">' + "".join(items) + '</ul></div>'
            '</div>')


def build_global_toc(posts: List[Dict], prefix: str) -> str:
    """全局目录 + 搜索面板（⌘K 唤起）。"""
    groups: Dict[str, Dict] = {}
    order: List[str] = []
    for post in posts:
        key = post["volume_id"]
        if key not in groups:
            groups[key] = {"label": post["vol_label"], "items": []}
            order.append(key)
        groups[key]["items"].append(post)
    body = []
    for key in order:
        group = groups[key]
        items = "".join(
            f'<li><a href="{prefix}{post["url"]}"><span class="ft">{post["title"]}</span></a></li>'
            for post in group["items"])
        body.append(f'<section class="finder-group"><h4>{group["label"]}</h4>'
                    f'<ul>{items}</ul></section>')
    return ('<div class="finder" id="finder" aria-hidden="true">'
            '<div class="finder-panel" role="dialog" aria-label="全局目录与搜索">'
            '<input type="search" class="finder-search" placeholder="搜索全部文章…（⌘K）" '
            'aria-label="搜索文章" autocomplete="off">'
            '<div class="finder-body">' + "".join(body) + '</div>'
            '<div class="finder-empty" id="finder-empty" hidden>无匹配结果</div>'
            '</div></div>')


def build_blog(volumes: List[Volume]) -> None:
    """每篇 md 独立成页 → dist/blog/{volume_id}--{slug}.html，并生成博客首页。"""
    posts: List[Dict] = []
    for volume in volumes:
        for relpath in volume.files:
            source = (config.ROOT / relpath).read_text(encoding="utf-8")
            fragment = render_markdown([relpath])
            title, body = strip_first_h1(fragment)
            headings = extract_headings(body)
            tags = extract_tags(body)
            reading = estimate_reading_time(source)
            slug = Path(relpath).stem
            url = f"{volume.id}--{slug}.html"
            posts.append({
                "volume_id": volume.id, "title": title, "body": body,
                "tags": tags, "reading": reading, "url": url,
                "vol_label": blog_volume_label(volume),
                "toc_box": build_onpage_toc(headings),
            })

    blog_dir = config.DIST / "blog"
    blog_dir.mkdir(parents=True, exist_ok=True)
    global_toc = build_global_toc(posts, "")

    for position, post in enumerate(posts):
        previous_post = posts[position - 1] if position > 0 else None
        next_post = posts[position + 1] if position + 1 < len(posts) else None
        related = [other for other in posts
                   if other["volume_id"] == post["volume_id"] and other["url"] != post["url"]][:4]

        if previous_post:
            prev_html = (f'<a class="nav-link prev" href="{previous_post["url"]}">'
                         f'<span class="dir">← 上一篇</span>'
                         f'<span class="ttl">{previous_post["title"]}</span></a>')
        else:
            prev_html = '<span class="nav-link prev disabled"></span>'
        if next_post:
            next_html = (f'<a class="nav-link next" href="{next_post["url"]}">'
                         f'<span class="dir">下一篇 →</span>'
                         f'<span class="ttl">{next_post["title"]}</span></a>')
        else:
            next_html = '<span class="nav-link next disabled"></span>'
        related_html = "".join(
            f'<li><a href="{other["url"]}">{other["title"]}</a></li>' for other in related)
        tags_html = "".join(f'<span class="tag">{tag}</span>' for tag in post["tags"])

        html = fill_template(config.BLOG_TEMPLATE,
            title=post["title"], series=config.SERIES, author=config.AUTHOR,
            css=config.BLOG_CSS, extra_css="", body_cls="",
            brand=f"{config.SERIES}丛书 · 博客",
            vol_label=post["vol_label"],
            pub_date=config.PUB_DATE_DISPLAY, reading_time=str(post["reading"]),
            tags=tags_html, toc_box=post["toc_box"], body=post["body"],
            global_toc=global_toc, home_url="index.html",
            prev=prev_html, next=next_html, related=related_html,
            js=config.BLOG_JS)
        (blog_dir / post["url"]).write_text(html, encoding="utf-8")

    build_blog_index(posts)
    print(f"    blog 文章页：{len(posts)} 篇")


def build_blog_index(posts: List[Dict]) -> None:
    """博客首页：按卷分组列出全部文章。"""
    groups: Dict[str, Dict] = {}
    order: List[str] = []
    for post in posts:
        key = post["volume_id"]
        if key not in groups:
            groups[key] = {"label": post["vol_label"], "items": []}
            order.append(key)
        groups[key]["items"].append(post)

    vol_nav = "".join(f'<a href="#vol-{key}">{groups[key]["label"]}</a>' for key in order)
    group_html = []
    for key in order:
        group = groups[key]
        items = "".join(
            f'<li>'
            f'<span class="gnum">{chapter_number(Path(post["url"]).stem) or "·"}</span>'
            f'<a href="{post["url"]}" class="gt">{post["title"]}</a>'
            f'<span class="gm">约 {post["reading"]} 分钟</span></li>'
            for post in group["items"])
        group_html.append(
            f'<section class="blog-group" id="vol-{key}">'
            f'<h2 class="group-title">{group["label"]}'
            f'<span class="gcount">{len(group["items"])} 篇</span></h2>'
            f'<ul class="group-list">{items}</ul></section>')

    html = fill_template(config.BLOG_INDEX_TEMPLATE,
        series=config.SERIES, brand=f"{config.SERIES}丛书 · 博客",
        count=str(len(posts)), vol_nav=vol_nav,
        groups="".join(group_html), pub_date=config.PUB_DATE_DISPLAY,
        js=config.BLOG_JS, css=config.BLOG_CSS)
    (config.DIST / "blog" / "index.html").write_text(html, encoding="utf-8")
