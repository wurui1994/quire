"""构建编排（SDK 入口）。

api.build(book, ...) 完成一次完整构建并返回产物路径列表；CLI 只是
把参数翻译后调它。与旧 buildlib.cli.main 的差异：

- 卷集来自 Book 对象（config.activate 已在其中），不再模块级全局；
- ebooklib / fitz 等重依赖按需导入，`import quire` 保持轻量；
- 未知卷以 ValueError 上抛（CLI 捕获后 sys.exit）。
"""

import contextlib
import shutil
import sys
import time
from pathlib import Path
from typing import List, Optional

from . import config
from .book import Book
from .model import standard_volume_count, volume_index_map
from .progress import (build_progress, report, add_time, note, get_notes,
                       get_times, get_completed)


def _progress_totals(selected, all_volumes, *, all_one, with_html,
                     with_epub, with_pdf, mode, epub_engine,
                     pdf_engine) -> dict:
    """返回每种处理的总数量，作为各自进度条的上限（纯计数，无时间权重）。

    - 渲染 Markdown：每种“在 Python 内渲染 md”的格式（HTML、ebooklib 引擎
      EPUB、PDF 不论引擎）对每个卷各渲染一次文件 → 累计文件数。
      合本构建（--all-one 且全选）对全卷文件各渲染一次。
    - 生成 HTML / 生成 EPUB：产物份数 = 逐卷数 +（合本 1 份）。
    - 排版 PDF：逐卷每卷计一步；合本在 fitz 引擎下按卷细分（每卷一步），
      browser 引擎下整本一次打印计一步。
    """
    building_everything = (len(selected) == len(all_volumes))
    per_volume_vols = [] if all_one else selected
    combined = all_one and building_everything and mode != "blog"

    files_per = sum(len(v.files) for v in per_volume_vols)
    files_all = sum(len(v.files) for v in all_volumes)

    md_passes = 0
    if with_html and mode != "blog":
        md_passes += 1
    if with_epub and epub_engine != "pandoc":
        md_passes += 1
    if with_pdf:
        md_passes += 1
    md_total = md_passes * files_per + (md_passes * files_all if combined else 0)

    totals: dict = {}
    if md_total:
        totals["渲染 Markdown"] = md_total
    if with_html and mode != "blog":
        totals["生成 HTML"] = len(per_volume_vols) + (1 if combined else 0)
    if with_epub:
        totals["生成 EPUB"] = len(per_volume_vols) + (1 if combined else 0)
    if with_pdf:
        if combined and pdf_engine == "fitz":
            # 合本 PDF 按页细粒度上报：先给一个基于文件数的页数预估值，
            # 真正的总数在 build_all_pdf_fitz 内逐页校正（set_total）。
            est_pages = sum(max(round(1.5 * len(v.files)), 1) for v in all_volumes)
            totals["排版 PDF"] = max(est_pages, 1)
        else:
            totals["排版 PDF"] = len(per_volume_vols) + (1 if combined else 0)
        # 生成 PDF 文件（finalize：插页脚 / 写书签 / 保存）独立一条进度，
        # 与“排版 PDF”分开；总数在 finalize_pdf 内按真实页数累加校正。
        totals["生成 PDF 文件"] = 1
    return totals


def _timed(builder, *args, **kwargs):
    """运行构建函数并返回 (产物路径, 耗时秒)。"""
    start = time.time()
    output = builder(*args, **kwargs)
    return output, time.time() - start


def build(book: Book, *, volumes: Optional[List[str]] = None,
          html: bool = True, epub: bool = True, pdf: bool = True,
          all_one: bool = False, indent: bool = False,
          pdf_engine: str = "fitz", epub_engine: str = "epublib",
          font: Optional[str] = None, font_bold: Optional[str] = None,
          mode: str = "book", show_progress: bool = True) -> List[Path]:
    """构建一本书（或其中的指定卷），返回全部产物路径。

    volumes: 卷 id 列表（空 = 全部）；html/epub/pdf: 各产物开关；
    mode: book=每卷一页阅读本，blog=每篇一页博客；
    all_one: 构建全部卷时额外生成 all.html/all.epub/all.pdf 合本（跳过分卷）。
    """
    config.activate(book)
    book.rescan()

    # EPUB：默认 epublib 引擎不依赖 pandoc；仅当显式选 pandoc 且缺失时回退。
    if epub_engine == "pandoc" and shutil.which("pandoc") is None:
        print("⚠️ 未找到 pandoc，EPUB 回退到 epublib 引擎")
        epub_engine = "epublib"

    all_volumes = book.volumes
    requested = list(volumes or [])
    selected = [v for v in all_volumes if not requested or v.id in requested]
    if not selected:
        raise ValueError(
            f"未知卷：{requested}（可用卷：{[v.id for v in all_volumes]}）")

    with_html = bool(html) and mode != "blog"
    with_epub = bool(epub)
    with_pdf = bool(pdf)

    # 重依赖按需导入（ebooklib / fitz / markdown-it 均不在 import quire 时加载）
    if with_html or (with_pdf and pdf_engine == "browser"):
        from .html_builder import build_volume_html, build_all_html
    if with_pdf:
        from .pdf import (build_pdf_fitz, build_pdf_browser,
                          build_all_pdf_fitz, build_all_pdf_browser)
        if pdf_engine == "fitz":
            from .pdf import configure_pdf_font
    if with_epub:
        from . import epub as _epub
        build_epub = _epub.build_epub_py if epub_engine == "epublib" else _epub.build_epub_pandoc
        build_all_epub = _epub.build_all_epub_py if epub_engine == "epublib" else _epub.build_all_epub_pandoc

    totals = _progress_totals(
        selected, all_volumes,
        all_one=all_one, with_html=with_html, with_epub=with_epub,
        with_pdf=with_pdf, mode=mode, epub_engine=epub_engine,
        pdf_engine=pdf_engine,
    )
    outputs: List[Path] = []
    progress_ctx = build_progress(totals) if show_progress else contextlib.nullcontext()
    with progress_ctx:
        config.DIST.mkdir(parents=True, exist_ok=True)
        if with_pdf and pdf_engine == "fitz":
            info = configure_pdf_font(font, font_bold)
            note(f"· 字体：{info['detail']}")
        warnings = []
        total_vols = standard_volume_count(all_volumes)
        index_map = volume_index_map(all_volumes)

        if all_one:
            note("· --all-one：仅生成合本（all.*），跳过各分卷构建")
        for volume in ([] if all_one else selected):
            note(f"· {volume.id} 《{volume.title}》")
            volume_outputs = []
            timing_parts = []
            vol_index = index_map.get(volume.id, -1)
            if with_html:
                report("生成 HTML", 0, current=volume.id)  # 先启动计时，再构建
                html_path, elapsed = _timed(build_volume_html, volume,
                                            indent=indent,
                                            vol_index=vol_index, total_vols=total_vols)
                report("生成 HTML", 1, current=volume.id)
                add_time("生成 HTML", elapsed)
                volume_outputs.append(html_path)
                timing_parts.append(f"HTML {elapsed:.1f}s")
            if with_epub:
                report("生成 EPUB", 0, current=volume.id)
                epub_path, elapsed = _timed(build_epub, volume)
                report("生成 EPUB", 1, current=volume.id)
                add_time("生成 EPUB", elapsed)
                volume_outputs.append(epub_path)
                timing_parts.append(f"EPUB {elapsed:.1f}s")
            if with_pdf:
                if pdf_engine == "browser":
                    if not (config.DIST / f"{volume.id}.html").exists():
                        build_volume_html(volume, indent=indent,
                                          vol_index=vol_index, total_vols=total_vols)
                    report("排版 PDF", 0, current=volume.id)
                    pdf_path, elapsed = _timed(build_pdf_browser, volume)
                    report("排版 PDF", 1, current=volume.id)
                else:
                    pdf_path, elapsed = _timed(build_pdf_fitz, volume)
                    # 排版 / 生成 PDF 的进度与计时已在 build_pdf_fitz 内部分别上报
                volume_outputs.append(pdf_path)
                timing_parts.append(f"PDF {elapsed:.1f}s")
            for output in volume_outputs:
                size = output.stat().st_size
                flag = ""
                if output.suffix != ".pdf" and size > config.SIZE_LIMIT:
                    flag = f"  ⚠️ 超过 {config.SIZE_LIMIT/1000/1000:g}MB！"
                    warnings.append(output.name)
                note(f"    {output.name:22s} {size/1000:>8.0f} KB{flag}")
            if timing_parts:
                note(f"    ⏱ {' | '.join(timing_parts)}")
            outputs.extend(volume_outputs)

        building_everything = (len(selected) == len(all_volumes))
        if all_one and building_everything and mode != "blog":
            if with_html:
                report("生成 HTML", 0, current="all")
                all_html_path, elapsed = _timed(build_all_html, all_volumes, indent=indent)
                report("生成 HTML", 1, current="all")
                add_time("生成 HTML", elapsed)
                outputs.append(all_html_path)
                note(f"  all.html              {all_html_path.stat().st_size/1000:>8.0f} KB  ⏱ {elapsed:.1f}s")
            if with_epub:
                report("生成 EPUB", 0, current="all")
                all_epub_path, elapsed = _timed(build_all_epub, all_volumes)
                report("生成 EPUB", 1, current="all")
                add_time("生成 EPUB", elapsed)
                outputs.append(all_epub_path)
                note(f"  all.epub              {all_epub_path.stat().st_size/1000:>8.0f} KB  ⏱ {elapsed:.1f}s")
            if with_pdf:
                if pdf_engine == "browser":
                    if not (config.DIST / "all.html").exists():
                        build_all_html(all_volumes, indent=False)
                    report("排版 PDF", 0, current="all")
                    all_pdf_path, elapsed = _timed(build_all_pdf_browser, all_volumes)
                    report("排版 PDF", 1, current="all")
                else:
                    all_pdf_path, elapsed = _timed(build_all_pdf_fitz, all_volumes)
                    # 排版 / 生成 PDF 的进度与计时已在 build_all_pdf_fitz 内部分别上报
                outputs.append(all_pdf_path)
                note(f"  all.pdf               {all_pdf_path.stat().st_size/1000:>8.0f} KB  ⏱ {elapsed:.1f}s")

        if with_html and mode == "blog":
            from .blog import build_blog
            build_blog(selected)
            note("· 博客模式：dist/blog/ 已生成")
        elif with_html and building_everything and not all_one:
            from .index import build_index
            build_index(selected)
            note("· index.html 已生成")
        if warnings:
            note("超体积文件：" + ", ".join(warnings))

    for line in get_notes():
        print(line)
    print_summary(totals)
    return outputs


def print_summary(totals: dict) -> None:
    """构建结束后输出各处理部分的耗时与数量统计（与进度条是否显示无关）。"""
    times = get_times()
    if not totals:
        return
    width = max(len(name) for name in totals)
    print("· 构建统计")
    total_t = 0.0
    for name, count in totals.items():
        # “渲染 Markdown”是各产物（HTML/EPUB/PDF）内部子步骤，会被多次调用，
        # 其耗时已包含在下述产物计时的 _timed 中，故不单列、也不计入合计。
        if name == "渲染 Markdown":
            continue
        t = times.get(name, 0.0)
        total_t += t
        if name == "生成 PDF 文件":
            # 该任务按真实页数推进（finalize 内 set_total 累加），显示实际页数。
            count = get_completed(name) or count
            unit = " 页"
        else:
            unit = " 项"
        print(f"    {name:<{width}}  {count:>4d}{unit}   {t:7.1f}s")
    print(f"    {'合计':<{width}}            {total_t:7.1f}s")
