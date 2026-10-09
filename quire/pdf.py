"""PDF 构建。

提供两套引擎：
- PyMuPDF(fitz) 直接排版   → build_*_fitz
- 浏览器（Playwright）打印 → build_*_browser（需先有对应 HTML）

两套都通过注入隐形锚点 span（@@卷@@ / @@卷@@标题@@）来定位 PDF 书签。
"""

import re
import sys
import hashlib
import time
from pathlib import Path
from typing import List, Tuple, Optional

from . import config
from .renderer import render_markdown
from .toc import extract_headings
from .model import Volume, all_volume_files
from .progress import report, set_total, get_completed, add_time, note


def extract_pdf_headings(html) -> List[Tuple[int, str]]:
    """提取 (层级, 文本)，用于 PDF 书签。"""
    out: List[Tuple[int, str]] = []
    for match in re.finditer(r'<(h[1-3])\s+id="([^"]*)">(.*?)</\1>', html, re.DOTALL):
        tag, heading_id, inner = match.group(1), match.group(2), match.group(3)
        text = re.sub(r'<[^>]+>', '', inner).strip()
        level = int(tag[1])
        out.append((level, text if text else heading_id))
    return out


def extract_pdf_heading_ids(html) -> List[Tuple[int, str, str]]:
    """提取 (层级, id, 文本)，供 fitz 原生书签使用。

    fitz(Story) 的目录是天然的：排版每页 place 之后调用
    Story.element_positions，标题（h1-h6 且带 id）落在哪页当场回报，
    无需注入 token、更无需事后扫描页面文字——那是 browser 路径
    （Chromium 打印无排版回调）才不得不用的办法。
    """
    out: List[Tuple[int, str, str]] = []
    for match in re.finditer(r'<(h[1-3])\s+id="([^"]*)">(.*?)</\1>', html, re.DOTALL):
        tag, heading_id, inner = match.group(1), match.group(2), match.group(3)
        text = re.sub(r'<[^>]+>', '', inner).strip()
        out.append((int(tag[1]), heading_id, text if text else heading_id))
    return out


def locate_bookmarks(doc, items) -> List[list]:
    """【仅 browser 路径】根据注入的 token 在页面文本中定位书签的页码。

    Chromium 打印拿不到排版回调，只能事后按 token 匹配页面文字。
    fitz 路径禁止走这里——用 extract_pdf_heading_ids + element_positions。

    一次性预提取每页文本，避免对每个书签重复调用 get_text()（每次都会
    重新解析整页），否则书签越多越慢（vol1 342 书签×数百页曾达 ~19s）。
    """
    page_texts = [doc[pi].get_text().replace("\n", "") for pi in range(len(doc))]
    results: List[list] = []
    for level, token, label in items:
        for page_index in range(len(doc)):
            if token in page_texts[page_index]:
                results.append([level, label, page_index + 1])
                break
    if results:
        first_level = results[0][0]
        for row in results:
            row[0] = row[0] - first_level + 1
    return results


def build_pdf_bookmarks(volumes):
    """构造 PDF 书签项，并返回把 token 注入 HTML 的闭包。"""
    items: List[Tuple[int, str, str]] = []
    for volume in volumes:
        fragment = render_markdown(volume.files)
        headings = extract_pdf_headings(fragment)
        items.append((1, f"@@{volume.id}@@", f"{volume.subtitle}　{volume.title}"))
        for level, text in headings:
            items.append((min(level + 1, 3), f"@@{volume.id}@@{text}@@", text))

    def inject(html: str) -> str:
        for _, token, text in items:
            html = html.replace(token, f'<span id="{text}"></span>')
        return html

    return items, inject


def _clamp_toc_levels(toc: List[list]) -> List[list]:
    """set_toc 要求首条层级为 1 且不得跳级（level ≤ 前一条+1），钳制修正。"""
    prev = 0
    for row in toc:
        if row[0] > prev + 1:
            row[0] = prev + 1
        prev = row[0]
    return toc


def _write_pdf_robust(path: Path, data: bytes) -> None:
    """把 PDF 字节写回 path。

    Windows 上文档刚 close，句柄未必即时释放，直接覆写可能报
    WinError 32（文件被占用）。重试几次；通常首次即可成功。
    """
    last = None
    for _i in range(20):
        try:
            path.write_bytes(data)
            return
        except (PermissionError, OSError) as e:
            last = e
            time.sleep(0.05)
    if last:
        raise last


def finalize_pdf(path: Path, footer_left: str, items=None, toc=None) -> None:
    """就地给 PDF 写入页脚页码 + 书签并回存（不产生任何临时文件）。

    书签来源二选一：
    - toc：fitz(Story) 路径——排版时由 element_positions 天然记录，直接写入；
    - items：browser 路径——事后按 token 扫描页面文字（locate_bookmarks）。

    本阶段独立显示为“生成 PDF 文件”进度条（与“排版 PDF”分开），
    逐页 / 逐步上报，避免 100% 后 10+ 秒无反馈、且与排版混淆。

    实现：先打开 path 立即做 garbage=4 去重（原始输出含逐 Story 重复内嵌
    字体，臃肿文档上逐页 insert_text 极慢），在瘦身后的文档上插页脚/书签，
    再 tobytes(garbage=4, deflate=True) 生成完整字节，关闭文档后回写原路径
    ——完全不依赖 .tmp.pdf 临时文件，也就绕开 Windows 上“删 .tmp.pdf
    被占用(WinError 32)”的问题。
    """
    import fitz

    start = time.time()
    doc = fitz.open(str(path))
    npages = len(doc)

    # 【性能】先做 garbage=4 全量对象去重（合本各卷 Story 逐份内嵌同一子集
    # 字体，去重后 10.5MB→5.8MB），再重开轻量文档做页脚/书签。
    # 逐页 insert_text 的耗时与文档臃肿程度强相关：在原始输出上 1408 页
    # 需 ~11s，去重后同样的循环只要 ~1.5s，净省约 9s。
    data = doc.tobytes(garbage=4, deflate=True)
    doc.close()
    doc = fitz.open(stream=data, filetype="pdf")

    # 进度条预留 = 页脚(每页1步) + 书签/保存(1步)。先校正总数（累加式，
    # 跨多本产物累计到同一条“生成 PDF 文件”）。
    set_total("生成 PDF 文件", get_completed("生成 PDF 文件") + npages + 1)

    # 1) 逐页插页脚页码
    report("生成 PDF 文件", 0, current="写入页脚页码")
    for page_index, page in enumerate(doc):
        y = page.rect.height - 30
        page.insert_text((50, y), footer_left, fontsize=8, fontname="china-s")
        page_number = str(page_index + 1)
        width = fitz.get_text_length(page_number, fontname="helv", fontsize=8)
        page.insert_text((page.rect.width - 50 - width, y), page_number,
                         fontname="helv", color=(0.45, 0.43, 0.38))
        report("生成 PDF 文件", 1, current=f"写入页脚页码 {page_index + 1}/{npages}")

    # 2) 书签 + 回存
    if toc is None:
        toc = locate_bookmarks(doc, items or [])
    report("生成 PDF 文件", 0, current="写入书签 / 保存")
    if toc:
        doc.set_toc(_clamp_toc_levels(toc))
        note(f"    PDF 书签：{len(toc)} 条")
    else:
        note("    ⚠️ PDF 书签：未找到任何标题")
    # 仍用 garbage=4：页脚左侧文本每页生成一条完全相同的内容流
    # （1408 页 ≈ 0.5MB），只有流级去重能合并；在已瘦身的文档上此步只
    # 需 ~1s（原先在臃肿文档上是与页脚循环叠加共 12s+）。
    data = doc.tobytes(garbage=4, deflate=True)
    doc.close()
    _write_pdf_robust(path, data)
    report("生成 PDF 文件", 1, current="写入书签 / 保存")
    add_time("生成 PDF 文件", time.time() - start)


def print_html_to_pdf(html_path: Path, out_path: Path,
                      page_format: str = "a5",
                      margin: dict = None) -> None:
    """用 Playwright 启动 Chromium，把 HTML 打印为 PDF。"""
    from playwright.sync_api import sync_playwright

    margin = margin or {"top": "12mm", "bottom": "14mm", "left": "14mm", "right": "14mm"}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page()
        page.goto(f"file://{html_path}")
        page.pdf(path=str(out_path), format=page_format, print_background=True,
                 margin=margin, prefer_css_page_size=True)
        browser.close()


def _fonts_dir() -> Path:
    return config.fonts_dir()


def _subset_cache_dir() -> Path:
    """字体子集缓存目录：项目 [fonts] dir 下 .subset；未配置时退到用户缓存目录。

    未配置 [fonts] dir 时 fonts_dir 解析为包内置目录（site-packages 等），
    向其写缓存既越权又可能在只读安装下失败，故落到各平台的用户缓存区。
    """
    import os
    if config.current().fonts_dir is not None:
        return _fonts_dir() / ".subset"
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    elif os.name == "nt":
        base = Path.home() / "AppData" / "Local" / "Cache"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
    return base / "quire" / "subsets"


# 当前选定的 PDF 中文字体；由 configure_pdf_font 设置，None 表示尚未选择。
_PDF_FONT: Optional[dict] = None


def _selected_font() -> dict:
    """返回当前选定字体信息 dict；未选择时按平台自动探测一次。"""
    global _PDF_FONT
    if _PDF_FONT is None:
        from . import fonts
        _PDF_FONT = fonts.find_cjk_font()
    return _PDF_FONT


def configure_pdf_font(regular: str | None = None,
                      bold: str | None = None) -> dict:
    """显式指定 fitz(PyMuPDF) 排版用中文字体（常规/粗体文件路径）。

    返回字体信息 dict：{label, regular, bold, source}。
    regular/bold 为空则按平台自动探测；都找不到时 regular=None（回退 PyMuPDF 默认）。
    """
    global _PDF_FONT
    from . import fonts
    _PDF_FONT = fonts.find_cjk_font(regular, bold)
    return _PDF_FONT


# PDF 字体子集化前缀，如 "ABCDEE+PingFangHK" → "PingFangHK"
_SUBSET_PREFIX = re.compile(r"^[A-Z]{6}\+")


def _strip_subset(name: str) -> str:
    """去掉 PDF 子集化前缀（6 个大写字母 + '+'）。"""
    return _SUBSET_PREFIX.sub("", name, count=1)


def _font_is_embedded(doc, xref) -> bool:
    """用 extract_font 取字体程序流，非空即内嵌（对 Type0/CID 也适用）。"""
    try:
        res = doc.extract_font(xref)
        content = res[3] if len(res) >= 4 else b""
        return bool(content)
    except Exception:
        return False


def _compact_pages(pages: list) -> str:
    """把页码列表压成紧凑文本：连续区间用 a-b，零散且少用逗号，多则「a-b 等 N 页」。"""
    n = len(pages)
    if n == 1:
        return f"{pages[0]} 页"
    if pages == list(range(pages[0], pages[0] + n)):   # 连续
        return f"{pages[0]}-{pages[-1]} 页"
    if n <= 10:
        return ",".join(str(x) for x in pages) + " 页"
    return f"{pages[0]}-{pages[-1]} 等 {n} 页"


def inspect_pdf_fonts(path) -> dict:
    """读取 PDF 使用的字体 → {page_count, fonts: [{name, type, embedded, pages}]}。

    用 PyMuPDF 遍历每页 get_page_fonts，按 xref 去重，记录字体名（去掉子集前缀）、
    类型、是否内嵌、出现页码（1-based）。供 --pdf-fonts 展示某 PDF 实际嵌入/引用
    了哪些字体。
    """
    import fitz
    doc = fitz.open(str(path))
    page_count = doc.page_count
    table: dict = {}          # xref -> entry
    for pno in range(page_count):
        for f in doc.get_page_fonts(pno, full=False):
            xref = f[0]
            ftype = f[2] or ""
            basefont = f[3] or f[4] or f"(xref {xref})"
            entry = table.get(xref)
            if entry is None:
                entry = {
                    "name": _strip_subset(basefont),
                    "type": ftype,
                    "embedded": _font_is_embedded(doc, xref),
                    "pages": [],
                }
                table[xref] = entry
            entry["pages"].append(pno + 1)
    doc.close()
    fonts = sorted(table.values(), key=lambda e: e["name"].lower())
    return {"page_count": page_count, "fonts": fonts}


def format_pdf_fonts(path) -> str:
    """检查 PDF 使用的字体，返回可打印的清单文本（供 --pdf-fonts）。"""
    info = inspect_pdf_fonts(path)
    fonts_list = info["fonts"]
    lines = [f"PDF 字体清单：{path}（{info['page_count']} 页，{len(fonts_list)} 款字体）"]
    if not fonts_list:
        lines.append("  （未检测到字体）")
    else:
        name_w = max(len(e["name"]) for e in fonts_list)
        for i, e in enumerate(fonts_list, 1):
            emb = "内嵌" if e["embedded"] else "未内嵌"
            lines.append(f"  {i}. {e['name'].ljust(name_w)}  "
                         f"[{e['type']}, {emb}]  {_compact_pages(e['pages'])}")
    return "\n".join(lines)


def _is_cff(src: Path) -> bool:
    """判断字体源是否 CFF/CID 矢量轮廓（而非 TrueType glyf）。

    CFF 字体（如 PingFang.ttc）用 fontTools 从 .ttc 抽取单字面并重存为
    .ttf 时，会破坏 cmap↔glyph 映射（CID-keyed 重打包缺陷），导致渲染
    乱码（如「什」→U+FBDD）。此类字体必须整包子集化、绝不能拆字面。
    TrueType（glyf）字体（如 Songti.ttc）则无此问题，可安全拆字面缩小体积。
    """
    from fontTools.ttLib import TTFont, TTCollection
    try:
        try:
            faces = list(TTCollection(str(src)).fonts)
        except Exception:
            faces = [TTFont(str(src))]
        return any("CFF " in f or "CFF2" in f for f in faces)
    except Exception:
        return False


def _extract_face(src: Path, weight: int, family: Optional[str] = None,
                  style: Optional[str] = None):
    """从字体源（.ttc/.otf/.ttf，可能含多字重/多族）取最接近 weight 的字面。

    weight: 400=Regular, 700=Bold。style: 字面（字重风格）名，如 "Light"/
    "Semibold"，给定时优先按字面名精确选面（如 "PingFang SC Light"）。
    单字面字体直接返回；多字面时先按 family 名过滤（.ttc 常把
    Songti SC/TC、PingFang SC/HK/TC 等混在一起），再按需按 style 或
    usWeightClass 选最接近的，无该字段时按名称（含 bold 判定为粗体）兜底。
    """
    from fontTools.ttLib import TTFont, TTCollection
    try:
        coll = TTCollection(str(src))
        faces = list(coll.fonts)
    except Exception:
        faces = [TTFont(str(src))]
    if family and len(faces) > 1:
        low = family.lower()
        matched = []
        for f in faces:
            try:
                names = (f["name"].getDebugName(16) or "",
                         f["name"].getDebugName(1) or "")
            except Exception:
                names = ("",)
            if any(n.lower() == low for n in names):
                matched.append(f)
        if matched:
            faces = matched

    # 字面风格优先：精确匹配 subfamily(nameID 2) 或 full name(nameID 4) 含该字面
    if style and len(faces) > 1:
        s = style.lower()
        styled = []
        for f in faces:
            try:
                sub = (f["name"].getDebugName(2) or "").strip().lower()
                full = (f["name"].getDebugName(4) or "").strip().lower()
            except Exception:
                sub = full = ""
            if sub == s or full == s or full.endswith(" " + s):
                styled.append(f)
        if styled:
            faces = styled

    if len(faces) == 1:
        return faces[0]
    best, best_diff = None, None
    for f in faces:
        w = None
        try:
            w = f["OS/2"].usWeightClass
        except Exception:
            w = None
        if w is None:
            nm = ""
            try:
                nm = (f["name"].getDebugName(4) or "") + " " + (f["name"].getDebugName(1) or "")
            except Exception:
                pass
            w = 700 if "bold" in nm.lower() else 400
        diff = abs(w - weight)
        if best_diff is None or diff < best_diff:
            best_diff, best = diff, f
    return best


def _otf_to_ttf(font, chars=None, max_err=1.0):
    """把 CFF/CID 轮廓字体转换成 TrueType(glyf) 轮廓。

    PyMuPDF 对 CID-keyed CFF（PingFang.ttc 等）的多字面 .ttc 处理有缺陷：
    按 @font-face 选面会错选（如选成 HK）且 CID↔glyph 映射错乱，渲染乱码。
    改用业界标准的 cu2qu 把轮廓转成 glyf，fitz 对 TrueType 单字面的嵌入/
    子集化是成熟的（Songti.ttc 同路径一直正常）。
    """
    from fontTools.ttLib import newTable
    from fontTools.pens.cu2quPen import Cu2QuPen
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    if chars:
        import logging
        from fontTools.subset import Subsetter, Options
        logging.getLogger("fontTools.subset").setLevel(logging.ERROR)
        _opts = Options(); _opts.desubroutinize = True
        _s = Subsetter(options=_opts)
        _s.populate(text="".join(sorted(chars)))
        _s.subset(font)
    glyphOrder = font.getGlyphOrder()
    glyf = newTable("glyf")
    glyf.glyphOrder = glyphOrder
    glyf.glyphs = {}
    font["glyf"] = glyf
    glyphSet = font.getGlyphSet()
    for name in glyphOrder:
        ttPen = TTGlyphPen(glyphSet)
        cu2quPen = Cu2QuPen(ttPen, max_err, reverse_direction=True)
        glyphSet[name].draw(cu2quPen)
        glyf[name] = ttPen.glyph()
    for t in ("CFF ", "CFF2", "VORG"):
        if t in font:
            del font[t]
    font["loca"] = newTable("loca")
    for g in glyf.glyphs.values():
        g.recalcBounds(glyf)
    m = font["maxp"]
    m.tableVersion = 0x00010000
    m.maxZones = 1
    m.maxTwilightPoints = 0
    m.maxStorage = 0
    m.maxFunctionDefs = 0
    m.maxInstructionDefs = 0
    m.maxStackElements = 0
    m.maxSizeOfInstructions = 0
    m.recalc(font)
    font.sfntVersion = "\x00\x01\x00\x00"
    return font


def _load_face_ttf(src: Path, weight: int, family: Optional[str] = None,
                    chars: set = None, style: Optional[str] = None):
    """取最接近 weight / 指定 style 的字面；若是 CFF/CID 轮廓则先转成 TTF 再返回。"""
    face = _extract_face(src, weight, family, style)
    if face is None:
        return None
    if chars:
        import logging
        from fontTools.subset import Subsetter, Options
        logging.getLogger("fontTools.subset").setLevel(logging.ERROR)
        _opts = Options(); _opts.desubroutinize = True
        _s = Subsetter(options=_opts)
        _s.populate(text="".join(sorted(chars)))
        _s.subset(face)
    if "CFF " in face or "CFF2" in face:
        return _otf_to_ttf(face)
    return face


def _ensure_fonts(chars: set) -> Tuple[Optional[Path], Optional[Path]]:
    """确保两档字重 ttf 存在于 assets/fonts/；缺失则从选定字体源自动提取。

    提取按 usWeightClass（Regular≈400 / Bold≈700）选字面，跨平台通用。
    PyMuPDF 默认（未找到任何中文字体）时返回 (None, None)。
    """
    info = _selected_font()
    if info["regular"] is None:
        return None, None
    reg_style = info.get("regular_style")
    bold_style = info.get("bold_style")
    reg_weight = info.get("regular_weight") or 400
    bold_weight = info.get("bold_weight") or 700
    fonts_dir = _subset_cache_dir()
    # 缓存名带上字体身份（族名+源文件+字面+字重），切换字体/字面不会复用到旧字面。
    tag = hashlib.md5(
        f"{info.get('family')}|{info['regular']}|{info['bold']}"
        f"|{reg_style}|{bold_style}|{reg_weight}|{bold_weight}"
        f"|{''.join(sorted(chars))}".encode()).hexdigest()[:10]
    reg = fonts_dir / f"full.{tag}.reg.ttf"
    bold = fonts_dir / f"full.{tag}.bold.ttf"
    if reg.exists() and bold.exists():
        return reg, bold
    fonts_dir.mkdir(parents=True, exist_ok=True)
    reg_src, bold_src = info["regular"], info["bold"]
    family = info.get("family")
    bold_family = info.get("bold_family") or family
    label = family or reg_src.name
    style_note = ""
    if reg_style or bold_style:
        style_note = f"〔{reg_style or '默认'}/{bold_style or '默认'}〕"
    print(f"    ⏳ 从 {reg_src.name} 提取 {label}{style_note} 的 Regular/Bold 字重 …")
    rf = _load_face_ttf(reg_src, reg_weight, family, chars, style=reg_style)
    bf = _load_face_ttf(bold_src, bold_weight, bold_family, chars, style=bold_style)
    if rf is None or bf is None:
        return None, None
    rf.save(str(reg))
    bf.save(str(bold))
    return reg, bold


def _fitz_css(regular: Path = None, bold: Path = None) -> str:
    """把字体占位符替换为绝对路径（PyMuPDF 不能解析相对 url / var()）。

    regular / bold 可传入子集化后的字体文件，仅嵌入实际用到的字形，
    把单卷 PDF 从 ~28MB 降到数百 KB。若两者均为 None（未找到任何中文字体），
    则回退 PyMuPDF 默认：移除 @font-face、字体名改为通用 serif，由 PyMuPDF 内置处理。
    """
    if regular is None or bold is None:
        css = config.PRINT_CSS
        # 仅匹配真正的 @font-face {…} 块（注释里的 @font-face 后面不是 {，不会被误删）。
        css = re.sub(r'@font-face\s*\{[^}]*__REGULAR_FONT__[^}]*\}', "", css, flags=re.S)
        css = re.sub(r'@font-face\s*\{[^}]*__BOLD_FONT__[^}]*\}', "", css, flags=re.S)
        css = css.replace('"SongtiSC"', "serif")
        return css
    # Windows 路径含反斜杠，直接塞进 CSS url("...") 会被 CSS 当作转义
    # （\f→换页符、\W→W …）而损坏，导致 MuPDF 找不到字体。统一转成正斜杠，
    # fitz 在 Windows 上同样接受 / 分隔的绝对路径。
    reg_url = str(regular).replace("\\", "/")
    bold_url = str(bold).replace("\\", "/")
    return (config.PRINT_CSS
            .replace("__REGULAR_FONT__", reg_url)
            .replace("__BOLD_FONT__", bold_url))


# 安全增补字符：PyMuPDF 会把 HTML 实体解码（如 &nbsp;→U+00A0），
# 而源文本里只有实体字面量、不含真实码位，故此处兜底，避免缺字回退到 Droid。
_SAFETY_CHARS = set(
    "".join(chr(c) for c in range(0x20, 0x7F))          # ASCII 可打印 + 控制
    + "　、。，「」『』！？：；（）…—·《》〈〉“”‘’【】〔〕〖〗〔〕〔〕・"
    + "　\xa0\u3000"                                       # 不换行空格 / 全角空格
    + "◆"
)


def _collect_chars(*texts: str) -> set:
    """收集所有实际会出现在 PDF 中的字符。"""
    chars: set = set()
    for t in texts:
        chars.update(t)
    chars.update(_SAFETY_CHARS)
    return chars


def _subset_fonts(chars: set) -> Tuple[Optional[Path], Optional[Path]]:
    """把选定字体的两档字重子集化到 chars，缓存到临时目录并返回路径。

    失败（如字体工具不可用）时回退为完整字体，保证构建不中断。
    未找到任何中文字体（PyMuPDF 默认回退）时返回 (None, None)。
    """
    cache_dir = _subset_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    info = _selected_font()
    reg_full, bold_full = _ensure_fonts(chars)
    if reg_full is None or bold_full is None:
        return None, None   # 未找到任何中文字体 → 回退 PyMuPDF 默认

    # CFF/CID 字体（PingFang 等）不能拆字面（见 _is_cff）：直接对整包 .ttc
    # 子集化，regular/bold 共用同一份（fitz 按 @font-face 的 font-weight 选面）。
    if False:  # CFF 分支已移除：CFF/CID 字体在 _ensure_fonts 阶段已用 cu2qu 转 TrueType
        ident = f"cff|{info.get('family')}|{info['regular']}|{''.join(sorted(chars))}"
        key = hashlib.md5(ident.encode()).hexdigest()[:12]
        out = cache_dir / f"cjk-CFF.{key}.ttc"
        if out.exists():
            return out, out
        try:
            import logging
            from fontTools.subset import Subsetter, Options
            from fontTools.ttLib import TTCollection
            logging.getLogger("fontTools.subset").setLevel(logging.ERROR)
            opts = Options()
            opts.glyph_names = False
            opts.notdef_outline = True
            opts.recalc_bounds = True
            opts.drop_tables += ["cidg", "meta", "trak"]  # 非必要表，跳过告警
            text = "".join(sorted(chars))
            s = Subsetter(options=opts)
            s.populate(text=text)
            f = TTCollection(str(info["regular"]))
            for face in f.fonts:
                s.subset(face)
            f.save(str(out))
            return out, out
        except Exception as e:  # 兜底：用完整字体
            print(f"    (unused) 字体子集化失败，回退完整字体：{e}")
            return Path(info["regular"]), Path(info["regular"])

    # TrueType 轮廓：沿用拆字面子集（体积更小）。
    ident = f"{info.get('family')}|{info['regular']}|{''.join(sorted(chars))}"
    key = hashlib.md5(ident.encode()).hexdigest()[:12]
    reg = cache_dir / f"cjk-Regular.{key}.ttf"
    bold = cache_dir / f"cjk-Bold.{key}.ttf"
    if reg.exists() and bold.exists():
        return reg, bold
    try:
        import logging
        from fontTools.subset import Subsetter, Options
        from fontTools.ttLib import TTFont
        logging.getLogger("fontTools.subset").setLevel(logging.ERROR)
        opts = Options()
        opts.glyph_names = False
        opts.notdef_outline = True
        opts.recalc_bounds = True
        text = "".join(sorted(chars))
        for src, dst in ((reg_full, reg), (bold_full, bold)):
            s = Subsetter(options=opts)
            s.populate(text=text)
            f = TTFont(str(src))
            s.subset(f)
            f.save(str(dst))
        return reg, bold
    except Exception as e:  # 兜底：用完整字体
        print(f"    ⚠️ 字体子集化失败，回退完整字体：{e}")
        return reg_full, bold_full


def _is_toc_file(relpath: str) -> bool:
    """判断是否目录文件（目录.md / toc.md / _toc.md / summary.md）。"""
    name = Path(relpath).name.lower()
    return "目录" in name or name.startswith("toc") or name == "summary.md"


def _split_toc(files):
    """把目录文件（若有）单独拆出，便于在 fitz 中包进 .toc 容器单独排版。"""
    if files and _is_toc_file(files[0]):
        return files[0], files[1:]
    return None, files


def _strip_ids(html: str) -> str:
    """去掉 id 属性。用于目录页片段：element_positions 只回报带 id 的标题，
    去掉 id 即可让目录页/封面的 h1/h2 不混入书签（封面本身就没有 id）。"""
    return re.sub(r'\s+id="[^"]*"', "", html)


def _heading_recorder(id_entries, toc_out: List[list], base_level: int = 0):
    """构造 element_positions 回调：fitz 天然目录。

    排版每页 place 之后，标题落在哪页由 fitz 当场回报（el.page 来自 args），
    按 id 对回 (层级, 文本)。同 id 重复时按文档顺序逐个消费。
    """
    pending: dict = {}
    for level, hid, text in id_entries:
        pending.setdefault(hid, []).append((level, text))

    def recorder(el):
        if el.open_close != 1 or not el.heading or not el.id:
            return
        queue = pending.get(el.id)
        if queue:
            level, text = queue.pop(0)
            toc_out.append([min(level + base_level, 3), text, el.page])
    return recorder


def _run_story(writer, mediabox, where, story, recorder, page_offset: int = 0) -> int:
    """把一个 Story 逐页排入 writer，每页取一次标题位置。返回新的页偏移。

    逐页上报进度（report），使几百页的卷也能细粒度推进，不会长时间不动。
    """
    pages = page_offset
    more = True
    while more:
        device = writer.begin_page(mediabox)
        more, _ = story.place(where)
        story.element_positions(recorder, {"page": pages + 1})
        story.draw(device)
        writer.end_page()
        pages += 1
        report("排版 PDF", 1, current=f"第 {pages - page_offset} 页")
    return pages


def _lay_volume(writer, mediabox, where, css, cover, toc_html, file_htmls,
                id_entries, toc_out, base_level: int, page_offset: int) -> int:
    """按文件拆分 Story 排版单卷，返回新的页偏移。

    把一卷拆成多个 Story 顺序写入同一 DocumentWriter：
      · 封面 + 目录 + 首篇 → 一个 Story（保留封面到首篇的连续流，与单 Story 等价）；
      · 其后每篇文件各成一个 Story。
    这样每个 Story 的 place() 只需在本篇内重排，避免单卷巨型 Story 的平方级
    place 代价（实测 vol9：557 页单 Story place ≈ 4.5s，按文件拆后该卷 ≈ 0.4s；
    全卷 place 5.2s→0.7s，页数仅 +6/1408）。书签由同一个 recorder 跨 Story
    累积——element_positions 按文档顺序回报标题，recorder 按 id 逐个消费，
    与单 Story 路径结果一致。
    """
    import fitz
    recorder = _heading_recorder(id_entries, toc_out, base_level=base_level)
    if file_htmls:
        # 首 Story：封面 + 目录 + 首篇（连续流，复刻单 Story 的封面区排版）
        first = cover
        if toc_html:
            first += f'<div class="toc">{toc_html}</div>'
        pieces = [first + file_htmls[0], *file_htmls[1:]]
    else:
        head = cover
        if toc_html:
            head += f'<div class="toc">{toc_html}</div>'
        pieces = [head]
    for piece_html in pieces:
        story = fitz.Story(html=piece_html, user_css=css)
        page_offset = _run_story(writer, mediabox, where, story, recorder, page_offset)
    return page_offset


def build_pdf_fitz(volume: Volume) -> Path:
    """PyMuPDF 直接排版单卷 PDF。书签由 element_positions 天然记录。"""
    import fitz

    toc_file, rest = _split_toc(volume.files)
    # 逐文件渲染（与 render_markdown(rest) 等价），便于按文件拆分 Story 排版。
    file_htmls = [render_markdown([f], math_format="html") for f in rest]
    fragment = "".join(file_htmls)
    id_entries = extract_pdf_heading_ids(fragment)
    toc_html = (_strip_ids(render_markdown([toc_file], math_format="html"))
                if toc_file else None)
    cover = (f'<h1 class="cover-title">{volume.title}</h1>'
             f'<h2 class="cover-sub">{volume.subtitle}</h2>'
             f'<p class="cover-series">{config.SERIES}丛书</p>'
             f'<p class="cover-date">{config.PUB_DATE_DISPLAY}</p>')
    css = _fitz_css(*_subset_fonts(_collect_chars(cover, fragment, toc_html or "")))
    out = config.DIST / f"{volume.id}.pdf"
    writer = fitz.DocumentWriter(str(out))
    mediabox = fitz.paper_rect("a5")
    # 版心边距逼近 browser 默认打印边距（14mm≈39.7pt / 12mm≈34pt）；
    # 底部留 42pt 以容下 finalize_pdf 的页脚（y=height-30）。
    where = mediabox + (39, 35, -39, -42)
    toc: List[list] = []
    # 进度：先按文件数高估总页数（保证不超 100%）；逐页进度在 _run_story 内上报。
    # finalize（插页脚/书签/保存）由 finalize_pdf 单独显示在“生成 PDF 文件”条。
    set_total("排版 PDF", max(round(7 * len(rest)), 1))
    layout_start = time.time()
    _lay_volume(writer, mediabox, where, css, cover, toc_html, file_htmls,
                id_entries, toc, base_level=0, page_offset=0)
    writer.close()
    add_time("排版 PDF", time.time() - layout_start)
    finalize_pdf(out, volume.title, toc=toc)
    return out


def build_pdf_browser(volume: Volume, html_path: Path = None) -> Path:
    """浏览器打印单卷 PDF（基于 dist/{id}.html）。"""
    if html_path is None:
        html_path = config.DIST / f"{volume.id}.html"
    out = config.DIST / f"{volume.id}.pdf"
    items, inject = build_pdf_bookmarks([volume])
    html = html_path.read_text(encoding="utf-8")
    html = inject(html)
    marked = config.DIST / f".{volume.id}.marked.html"
    marked.write_text(html, encoding="utf-8")
    try:
        layout_start = time.time()
        print_html_to_pdf(marked, out)
        add_time("排版 PDF", time.time() - layout_start)
    finally:
        marked.unlink(missing_ok=True)
    finalize_pdf(out, volume.title, items)
    return out


def build_all_pdf_fitz(volumes: List[Volume]) -> Path:
    """PyMuPDF 直接排版全卷合本 PDF。

    【KB-002】禁止把全部卷拼成一个巨型 Story：Story 每页 place 都要从根
    重走一遍 restartable layout（跳过已排内容再续排），代价 ≈ 页数×盒子数，
    文档越大越接近平方级——各卷单独构建合计约 40s，巨型 Story 合本却
    超过 2 分钟仍未排完（曾被迫 Ctrl-C）。故改为每卷一个 Story、顺序写入
    同一个 DocumentWriter，总耗时回到线性（≈各卷之和）。

    【KB-003】进一步把每卷按文件拆成多个 Story（见 _lay_volume）：单卷大 Story
    的 place 仍是平方级，vol9（557 页/100 篇）单卷 place 即占全卷 88%（≈4.5s）。
    按文件拆后每个 Story 只在本篇内重排，全卷 place 5.2s→0.7s；封面+目录+首篇
    仍合成一个 Story，保留封面到首篇的连续流，页数仅 +6/1408（绝大多数篇本就
    起于新页）。字体对全书字符集只子集化一次、各 Story 共用同一份文件，
    finalize_pdf 里 garbage=4 去重，避免逐 Story 重复内嵌。
    书签同样走 element_positions 天然记录：卷名为 1 级，卷内标题降 1 级。
    """
    import fitz

    prepared = []
    for volume in volumes:
        toc_file, rest = _split_toc(volume.files)
        # 逐文件渲染（与 render_markdown(rest) 等价），便于按文件拆分 Story 排版。
        file_htmls = [render_markdown([f], math_format="html") for f in rest]
        fragment = "".join(file_htmls)
        id_entries = extract_pdf_heading_ids(fragment)
        toc_html = (_strip_ids(render_markdown([toc_file], math_format="html"))
                    if toc_file else None)
        cover = (f'<h1 class="cover-title">{volume.title}</h1>'
                 f'<h2 class="cover-sub">{volume.subtitle}</h2>'
                 f'<p class="cover-series">{config.SERIES}丛书</p>'
                 f'<p class="cover-date">{config.PUB_DATE_DISPLAY}</p>')
        prepared.append((volume, cover, toc_html, file_htmls, id_entries))

    all_text = []
    for _vol, cover, toc_html, file_htmls, _ids in prepared:
        all_text.append(cover)
        if toc_html:
            all_text.append(toc_html)
        all_text.append("".join(file_htmls))
    css = _fitz_css(*_subset_fonts(_collect_chars(*all_text)))
    out = config.DIST / "all.pdf"
    writer = fitz.DocumentWriter(str(out))
    mediabox = fitz.paper_rect("a5")
    where = mediabox + (39, 35, -39, -42)
    toc: List[list] = []
    page_offset = 0
    files_laid = 0
    ratio = 7.0  # 文件数→页数 默认倍率；首个卷排版后按真实已完成数自校正
    n = len(prepared)
    layout_start = time.time()
    for i, (volume, cover, toc_html, file_htmls, id_entries) in enumerate(prepared):
        # 总数 = 真实已完成页数 + 剩余文件的预估页数（倍率随真实数据校正），
        # 保证进度条在排到任一页时都不超过 100%。
        remaining_files = sum(len(prepared[j][0].files) for j in range(i + 1, n))
        est_remaining = round(remaining_files * ratio)
        set_total("排版 PDF", get_completed("排版 PDF") + max(est_remaining, 1))
        toc.append([1, f"{volume.subtitle}　{volume.title}", page_offset + 1])
        page_offset = _lay_volume(writer, mediabox, where, css, cover, toc_html,
                                  file_htmls, id_entries, toc, base_level=1,
                                  page_offset=page_offset)
        files_laid += len(volume.files)
        if files_laid:
            ratio = page_offset / files_laid
    # 排版结束：finalize（插页脚/书签/保存）由 finalize_pdf 单独显示在“生成 PDF 文件”条，
    # 逐页上报、不再与排版混在一起。
    layout_end = time.time()
    writer.close()
    add_time("排版 PDF", layout_end - layout_start)
    finalize_pdf(out, f"{config.SERIES}丛书", toc=toc)
    return out


def build_all_pdf_browser(volumes: List[Volume], html_path: Path = None) -> Path:
    """浏览器打印全卷合本 PDF（基于 dist/all.html）。"""
    if html_path is None:
        html_path = config.DIST / "all.html"
    out = config.DIST / "all.pdf"
    items, inject = build_pdf_bookmarks(volumes)
    html = html_path.read_text(encoding="utf-8")
    html = inject(html)
    marked = config.DIST / ".all.marked.html"
    marked.write_text(html, encoding="utf-8")
    try:
        layout_start = time.time()
        print_html_to_pdf(marked, out)
        add_time("排版 PDF", time.time() - layout_start)
    finally:
        marked.unlink(missing_ok=True)
    finalize_pdf(out, f"{config.SERIES}丛书", items)
    return out
