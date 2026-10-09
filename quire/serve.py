"""本地预览服务器（Flask）。

每次请求自动重新扫描卷集；源文件变动后刷新即生效，无需重启。
为保证增量渲染，按「卷片段 / 单卷页 / 文章页 / 全卷页 / 全局目录 / 博客首页」
分别做基于文件 mtime 的缓存。
"""

from pathlib import Path
from typing import Dict, List, Tuple

from . import config
from .config import fill_template
from .renderer import render_markdown
from .toc import (extract_headings, build_single_volume_toc_html,
                         build_all_volumes_toc_html)
from .cover import build_cover_html
from .model import (Volume, all_volume_files, standard_volume_count,
                            volume_index_map)
from .blog import (strip_first_h1, extract_tags, estimate_reading_time,
                           chapter_number, blog_volume_label,
                           build_onpage_toc, build_global_toc)
from .html_builder import assemble_reader_page, render_all_reader


class PreviewServer:
    def __init__(self, *, indent: bool = False, mode: str = "book"):
        self.indent = indent
        self.mode = mode
        self.fragment_cache: Dict[str, Tuple[str, list, dict]] = {}
        self.page_cache: Dict[str, Tuple[str, dict]] = {}
        self.global_toc_cache: Dict[str, object] = {"html": None, "mtime": None}

    # ------------------------------------------------------------ 基础设施
    def discover(self) -> Tuple[List[Volume], int, Dict[str, int]]:
        volumes = config.current().rescan()
        total = standard_volume_count(volumes)
        index_map = volume_index_map(volumes)
        return volumes, total, index_map

    def _file_mtimes(self, files) -> dict:
        return {file: (config.ROOT / file).stat().st_mtime for file in files}

    def _find_volume(self, volume_id: str, volumes: List[Volume]) -> Volume:
        return next((volume for volume in volumes if volume.id == volume_id), None)

    # ------------------------------------------------------------ 片段 / 单卷页
    def _render_fragment(self, volume_id: str, volumes: List[Volume]):
        volume = self._find_volume(volume_id, volumes)
        if volume is None:
            return "", []
        current = self._file_mtimes(volume.files)
        if volume_id in self.fragment_cache and self.fragment_cache[volume_id][2] == current:
            return self.fragment_cache[volume_id][0], self.fragment_cache[volume_id][1]
        fragment = render_markdown(volume.files)
        headings = extract_headings(fragment)
        self.fragment_cache[volume_id] = (fragment, headings, current)
        return fragment, headings

    def _render_volume_page(self, volume_id, volumes, total, index_map) -> str:
        volume = self._find_volume(volume_id, volumes)
        if volume is None:
            return ""
        current = self._file_mtimes(volume.files)
        if volume_id in self.page_cache and self.page_cache[volume_id][1] == current:
            return self.page_cache[volume_id][0]
        fragment, headings = self._render_fragment(volume_id, volumes)
        cover = build_cover_html(volume.id, volume.title, volume.subtitle,
                                 vol_index=index_map.get(volume.id, -1), total_vols=total,
                                 is_preface=volume.is_preface)
        cover_css = f"<style>{config.COVER_CSS}</style>" if cover else ""
        toc_html = build_single_volume_toc_html(headings)
        html = assemble_reader_page(title=volume.title, brand=volume.subtitle,
                                    body=cover + fragment, toc_html=toc_html,
                                    extra_css=cover_css, indent=self.indent)
        self.page_cache[volume_id] = (html, current)
        return html

    # ------------------------------------------------------------ 博客文章页
    def _render_post(self, volume_id, chapter, volumes, total, index_map) -> str:
        volume = self._find_volume(volume_id, volumes)
        if volume is None:
            return ""
        target = next((file for file in volume.files if Path(file).stem == chapter), None)
        if target is None:
            return ""
        flat = [(volume.id, file) for volume in volumes for file in volume.files]
        position = next((index for index, (vid, file) in enumerate(flat)
                         if vid == volume_id and Path(file).stem == chapter), -1)
        if position < 0:
            return ""
        current = self._file_mtimes([target])
        cache_key = f"post:{volume_id}--{chapter}"
        if cache_key in self.page_cache and self.page_cache[cache_key][1] == current:
            return self.page_cache[cache_key][0]

        source = (config.ROOT / target).read_text(encoding="utf-8")
        fragment = render_markdown([target])
        title, body = strip_first_h1(fragment)
        headings = extract_headings(body)
        tags = extract_tags(body)
        reading = estimate_reading_time(source)
        previous = next(((vid, Path(file).stem) for index, (vid, file) in enumerate(flat)
                         if index == position - 1), None)
        following = next(((vid, Path(file).stem) for index, (vid, file) in enumerate(flat)
                          if index == position + 1), None)

        def post_link(item):
            vid, slug = item
            return f"{vid}--{slug}.html"

        if previous:
            prev_html = (f'<a class="nav-link prev" href="/p/{post_link(previous)}">'
                         f'<span class="dir">← 上一篇</span>'
                         f'<span class="ttl">{blog_volume_label(self._find_volume(previous[0], volumes))}</span></a>')
        else:
            prev_html = '<span class="nav-link prev disabled"></span>'
        if following:
            next_html = (f'<a class="nav-link next" href="/p/{post_link(following)}">'
                         f'<span class="dir">下一篇 →</span>'
                         f'<span class="ttl">{blog_volume_label(self._find_volume(following[0], volumes))}</span></a>')
        else:
            next_html = '<span class="nav-link next disabled"></span>'

        related = [(vid, Path(file).stem) for vid, file in flat
                   if vid == volume_id and Path(file).stem != chapter][:4]
        related_html = "".join(f'<li><a href="/p/{vid}--{slug}.html">{slug}</a></li>'
                               for vid, slug in related)
        tags_html = "".join(f'<span class="tag">{tag}</span>' for tag in tags)
        toc_box = build_onpage_toc(headings)
        html = fill_template(config.BLOG_TEMPLATE,
            title=title, series=config.SERIES, author=config.AUTHOR,
            css=config.BLOG_CSS, extra_css="", body_cls="",
            brand=f"{config.SERIES}丛书 · 博客",
            vol_label=blog_volume_label(volume),
            pub_date=config.PUB_DATE_DISPLAY, reading_time=str(reading),
            tags=tags_html, toc_box=toc_box, body=body,
            global_toc=self._global_toc(volumes), home_url="/",
            prev=prev_html, next=next_html, related=related_html,
            js=config.BLOG_JS)
        self.page_cache[cache_key] = (html, current)
        return html

    def _global_toc(self, volumes) -> str:
        files = all_volume_files(volumes)
        current = self._file_mtimes(files)
        if self.global_toc_cache["html"] is not None and self.global_toc_cache["mtime"] == current:
            return self.global_toc_cache["html"]
        posts = []
        for volume in volumes:
            for file in volume.files:
                title, _ = strip_first_h1(render_markdown([file]))
                posts.append({"volume_id": volume.id,
                              "url": f"{volume.id}--{Path(file).stem}.html",
                              "vol_label": blog_volume_label(volume),
                              "title": title})
        html = build_global_toc(posts, "/p/")
        self.global_toc_cache["html"] = html
        self.global_toc_cache["mtime"] = current
        return html

    def _render_blog_index(self, volumes) -> str:
        files = all_volume_files(volumes)
        current = self._file_mtimes(files)
        if "blog_index" in self.page_cache and self.page_cache["blog_index"][1] == current:
            return self.page_cache["blog_index"][0]
        posts = []
        for volume in volumes:
            for file in volume.files:
                source = (config.ROOT / file).read_text(encoding="utf-8")
                fragment = render_markdown([file])
                title, _ = strip_first_h1(fragment)
                reading = estimate_reading_time(source)
                posts.append({"volume_id": volume.id, "title": title,
                              "url": f"{volume.id}--{Path(file).stem}.html",
                              "vol_label": blog_volume_label(volume),
                              "reading": reading})
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
                f'<a href="/p/{post["url"]}" class="gt">{post["title"]}</a>'
                f'<span class="gm">约 {post["reading"]} 分钟</span></li>'
                for post in group["items"])
            group_html.append(
                f'<section class="blog-group" id="vol-{key}">'
                f'<h2 class="group-title">{group["label"]}'
                f'<span class="gcount">{len(group["items"])} 篇</span></h2>'
                f'<ul class="group-list">{items}</ul></section>')
        html = fill_template(config.BLOG_INDEX_TEMPLATE, series=config.SERIES, brand=f"{config.SERIES}丛书 · 博客",
                             count=str(len(posts)), vol_nav=vol_nav,
                             groups="".join(group_html), pub_date=config.PUB_DATE_DISPLAY,
                             js=config.BLOG_JS, css=config.BLOG_CSS)
        self.page_cache["blog_index"] = (html, current)
        return html

    # ------------------------------------------------------------ 全卷页 / 首页
    def _render_all_page(self, volumes, total, index_map) -> str:
        files = all_volume_files(volumes)
        current = self._file_mtimes(files)
        if "all" in self.page_cache and self.page_cache["all"][1] == current:
            return self.page_cache["all"][0]
        html = render_all_reader(volumes, indent=self.indent)
        self.page_cache["all"] = (html, current)
        return html

    def _render_home(self, volumes) -> str:
        rows = "\n".join(
            f'<li><span class="t">{volume.subtitle}　<b>{volume.title}</b></span>'
            f'<span class="l"><a href="/{volume.id}">阅读</a></span></li>'
            for volume in volumes)
        all_link = '<a href="/all">全卷</a>'
        return fill_template(config.INDEX_TEMPLATE, series=config.SERIES, items=rows, all_links=all_link)

    # ------------------------------------------------------------ 启动
    def run(self, *, port: int = 80) -> None:
        from flask import Flask, Response

        app = Flask(__name__, static_folder=None)
        server = self

        if self.mode == "blog":
            @app.route("/")
            def blog_home():
                volumes, _, _ = server.discover()
                return Response(server._render_blog_index(volumes),
                                content_type="text/html; charset=utf-8")

            @app.route("/p/<path:slug>")
            def blog_post(slug):
                if slug.endswith(".html"):
                    slug = slug[:-5]
                if "--" not in slug:
                    return Response("无效文章路径", status=404)
                volume_id, chapter = slug.split("--", 1)
                volumes, total, index_map = server.discover()
                html = server._render_post(volume_id, chapter, volumes, total, index_map)
                if not html:
                    return Response(f"未知文章：{slug}", status=404)
                return Response(html, content_type="text/html; charset=utf-8")
        else:
            @app.route("/")
            def home():
                volumes, _, _ = server.discover()
                return Response(server._render_home(volumes),
                                content_type="text/html; charset=utf-8")

            @app.route("/<volume_id>")
            def volume_page(volume_id):
                volumes, total, index_map = server.discover()
                html = server._render_volume_page(volume_id, volumes, total, index_map)
                if not html:
                    return Response(f"未知卷：{volume_id}", status=404)
                return Response(html, content_type="text/html; charset=utf-8")

            @app.route("/all")
            def all_volumes_page():
                volumes, total, index_map = server.discover()
                return Response(server._render_all_page(volumes, total, index_map),
                                content_type="text/html; charset=utf-8")

        volumes, _, _ = server.discover()
        print(f"  本地预览 → http://localhost:{port}")
        print(f"  可用卷：{', '.join(v.id for v in volumes)}")
        print("  每次请求自动重新扫描卷集；源文件变动后刷新即生效，无需重启。")
        app.run(host="0.0.0.0", port=port, debug=False)


def serve(*, port: int = 80, indent: bool = False, mode: str = "book") -> None:
    PreviewServer(indent=indent, mode=mode).run(port=port)
