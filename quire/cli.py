"""命令行入口：quire init / new / list / build / serve。

    quire init [PATH] --title 书名 --author 作者   # 初始化项目
    quire new vol1/新章 [标题…]                    # 新建章节
    quire list [--words]                          # 卷 / 章节清单（--words 附字数）
    quire build [卷id…] [--html|--epub|--pdf] …   # 构建（默认全格式）
    quire serve [--port 8000]                     # 本地预览（改动即生效）

全局 -d/--dir 可指定项目目录；默认自当前位置向上找 quire.toml。
"""

import argparse
import sys
from pathlib import Path

from . import __version__
from .api import build
from .book import load_book, file_words, volume_words
from .scaffold import init_project, new_chapter


def _cmd_init(args) -> None:
    root = init_project(args.path, title=args.title, author=args.author)
    print(f"已初始化 quire 项目：{root}")
    print("  下一步：")
    print("    quire serve                # 本地预览（改动即生效）")
    print("    quire build                # 构建 HTML / EPUB / PDF 到 dist/")
    print("    quire new vol1/新章 标题    # 新建章节")


def _cmd_new(args) -> None:
    book = load_book(args.dir)
    title = " ".join(args.title) or None
    try:
        target = new_chapter(book, args.where, title)
    except ValueError as exc:
        sys.exit(str(exc))
    print(f"已创建：{target.relative_to(book.root)}")
    print("    quire build / serve 即可收录")


def _cmd_list(args) -> None:
    book = load_book(args.dir)
    words = args.words
    grand = 0
    print(f"《{book.series}》 · {len(book.volumes)} 卷 · "
          f"{sum(len(v.files) for v in book.volumes)} 篇 · {book.root}")
    for volume in book.volumes:
        tag = volume.label or volume.kind
        head = f"· {volume.id}  《{volume.title}》"
        if volume.subtitle:
            head += f" {volume.subtitle}"
        head += f"  [{tag}]"
        if words:
            count = volume_words(book, volume)
            grand += count
            head += f"  {count:,} 字"
        print(head)
        for relpath in volume.files:
            line = f"    {relpath}"
            if words:
                line += f"  （{file_words(book.root, relpath):,} 字）"
            print(line)
    if words:
        print(f"合计：{grand:,} 字")


def _font_tools(args, book) -> bool:
    """--list-fonts / --pdf-fonts / --cache-font：处理后返回 True（CLI 即退出）。"""
    if args.list_fonts:
        from . import fonts as _fonts
        rows = _fonts.list_cjk_fonts()
        if not rows:
            print("未检测到系统中文字体。可用 --font 指定，或安装 Noto CJK / 微软雅黑 / 宋体 等。")
        else:
            origin_tag = {"bundled": "项目自带/缓存", "env": "环境变量", "system": "系统"}
            print("可用中文字体（--font/--font-bold 写法，按来源分组）：")
            for display, family, path, origin in rows:
                print(f"  [{origin_tag.get(origin, origin)}] {display}")
                faces = _fonts.face_styles(path)
                if faces:
                    print(f"      族名（默认字面）：--font \"{family}\"")
                    for s in faces:
                        print(f"      字面 {s:<10}：--font \"{family} {s}\"")
                else:
                    print(f"      --font \"{family}\"")
                print(f"      {path}")
        return True

    if args.pdf_fonts:
        from .pdf import format_pdf_fonts
        pdf_path = Path(args.pdf_fonts)
        if not pdf_path.exists():
            sys.exit(f"PDF 文件不存在：{args.pdf_fonts}")
        print(format_pdf_fonts(pdf_path))
        return True

    if args.cache_font:
        from . import fonts as _fonts
        if book.fonts_dir is None:
            sys.exit("未配置 [fonts] dir（quire.toml），无缓存目标目录。\n"
                     "  示例：\n    [fonts]\n    dir = \"assets/fonts\"")
        cached = _fonts.cache_font(args.font, args.font_bold)
        if not cached:
            print("未解析到可缓存的字体（请检查 --font/--font-bold 取值，或系统中文字体）。")
        for fam, style, src, dst, status in cached:
            tag = fam + (f" {style}" if style else "")
            if status == "skipped":
                print(f"· 已存在（跳过）：{dst.parent.name}/{dst.name} "
                      f"（{dst.stat().st_size / 1024 / 1024:.1f} MB）")
            else:
                verb = "已缓存" if status == "cached" else "已更新"
                print(f"· {verb} {tag}：{src.name} → {dst.parent.name}/{dst.name} "
                      f"（{dst.stat().st_size / 1024 / 1024:.1f} MB）")
        if cached:
            print(f"完成。后续构建将优先使用 {book.fonts_dir} 下的字体（随仓库走 / 离线可用）。")
        return True

    return False


def _cmd_build(args) -> None:
    book = load_book(args.dir)
    if _font_tools(args, book):
        return

    explicit = args.html or args.epub or args.pdf
    with_html = args.html or not explicit
    with_epub = (args.epub or not explicit) and not args.no_epub
    with_pdf = (args.pdf or not explicit) and not args.no_pdf

    if args.output:
        book.output_dir = args.output

    try:
        build(book, volumes=args.volumes,
              html=with_html, epub=with_epub, pdf=with_pdf,
              all_one=args.all_one, indent=args.indent,
              pdf_engine=args.pdf_engine, epub_engine=args.epub_engine,
              font=args.font, font_bold=args.font_bold,
              mode=args.mode)
    except ValueError as exc:
        sys.exit(str(exc))


def _cmd_serve(args) -> None:
    book = load_book(args.dir)
    from .serve import serve
    serve(port=args.port, indent=args.indent, mode=args.mode)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="quire", description="quire：Markdown 书籍构建（HTML / EPUB / PDF / 博客 / 预览）")
    parser.add_argument("--version", action="version", version=f"quire {__version__}")
    parser.add_argument("-d", "--dir", default=None, metavar="PATH",
                        help="书籍项目目录（默认自当前位置向上查找 quire.toml）")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="初始化书籍项目骨架")
    p_init.add_argument("path", nargs="?", default=".")
    p_init.add_argument("--title", default=None, help="书名 / 丛书名（缺省取目录名）")
    p_init.add_argument("--author", default="", help="作者")
    p_init.set_defaults(func=_cmd_init)

    p_new = sub.add_parser("new", help="新建章节：<卷id>/<文件名> [标题…]")
    p_new.add_argument("where", metavar="卷id/文件名",
                       help="如 vol1/新章（未带 chNN 前缀时自动补号）")
    p_new.add_argument("title", nargs="*", default=[], help="章节标题（缺省用文件名）")
    p_new.set_defaults(func=_cmd_new)

    p_list = sub.add_parser("list", help="列出卷与章节")
    p_list.add_argument("--words", action="store_true", help="统计 CJK 字数")
    p_list.set_defaults(func=_cmd_list)

    p_build = sub.add_parser("build", help="构建产物（默认 HTML + EPUB + PDF）")
    p_build.add_argument("volumes", nargs="*", default=[], metavar="卷id",
                         help="要构建的卷（默认全部）")
    p_build.add_argument("--output", default=None, metavar="DIR",
                         help="产物目录（覆盖 quire.toml [book] output）")
    p_build.add_argument("--indent", action="store_true", help="段落首行缩进 2 字")
    p_build.add_argument("--no-epub", action="store_true", help="跳过 EPUB")
    p_build.add_argument("--no-pdf", action="store_true", help="跳过 PDF")
    p_build.add_argument("--pdf-engine", choices=["fitz", "browser"], default="fitz",
                         help="PDF 引擎（默认 fitz，无需浏览器）")
    p_build.add_argument("--epub-engine", choices=["epublib", "pandoc"], default="epublib",
                         help="EPUB 引擎（默认 epublib，无需 pandoc）")
    p_build.add_argument("--mode", choices=["book", "blog"], default="book",
                         help="book=每卷一页阅读本；blog=每篇一页 + 博客首页")
    p_build.add_argument("--all-one", action="store_true",
                         help="构建全部卷时仅生成 all.* 合本，跳过分卷")
    p_build.add_argument("--html", action="store_true", help="仅 HTML")
    p_build.add_argument("--epub", action="store_true", help="仅 EPUB")
    p_build.add_argument("--pdf", action="store_true", help="仅 PDF")
    p_build.add_argument("--font", default=None,
                          help="fitz PDF 常规中文字体：文件路径，或族名（如 \"PingFang SC\"），"
                               "或「族名 字面」（如 \"PingFang SC Light\"）")
    p_build.add_argument("--font-bold", default=None,
                          help="粗体中文字体，写法同 --font；缺省与 --font 同文件自动选粗体面")
    p_build.add_argument("--list-fonts", action="store_true",
                         help="列出可用中文字体后退出")
    p_build.add_argument("--pdf-fonts", metavar="PDF", default=None,
                         help="检查给定 PDF 使用的字体后退出")
    p_build.add_argument("--cache-font", action="store_true",
                         help="把解析到的中文字体缓存到 [fonts] dir（离线/可复现构建）后退出")
    p_build.set_defaults(func=_cmd_build)

    p_serve = sub.add_parser("serve", help="本地预览服务器（源文件改动即生效）")
    p_serve.add_argument("--port", type=int, default=8000, help="端口（默认 8000）")
    p_serve.add_argument("--indent", action="store_true", help="段落首行缩进 2 字")
    p_serve.add_argument("--mode", choices=["book", "blog"], default="book",
                         help="book=阅读本模式；blog=博客模式")
    p_serve.set_defaults(func=_cmd_serve)
    return parser


def main(argv=None) -> None:
    args = _build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
