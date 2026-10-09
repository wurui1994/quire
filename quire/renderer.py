"""Markdown 渲染管线。

负责把仓库中的 .md 源文件渲染为 HTML 片段，包含：
- markdown-it-py 实例构建（含脚注 / 定义列表 / 锚点 / 行内数学插件）
- 渲染前预处理：中文弯引号、Pandoc 风格上标
- 渲染中修复：AI 中文紧贴加粗（core rule，见 _repair_failed_strong）
- 渲染后处理：dollarmath 占位 span/div → MathML（默认，浏览器/EPUB 用）
  或 HTML 上/下标（fitz PDF 用，因其 Story 引擎不支持 MathML）
- 标题 slug 生成
"""

import re
import unicodedata
from typing import List

from . import config
from .progress import report_file

_MDIT = None  # markdown-it 实例懒加载缓存


def get_markdown_renderer():
    """懒加载并缓存 markdown-it-py 渲染器（仅在首次使用时导入第三方库）。"""
    global _MDIT
    if _MDIT is not None:
        return _MDIT

    from markdown_it import MarkdownIt
    from mdit_py_plugins.footnote import footnote_plugin
    from mdit_py_plugins.deflist import deflist_plugin
    from mdit_py_plugins.anchors import anchors_plugin
    from mdit_py_plugins.dollarmath import dollarmath_plugin

    md = (MarkdownIt("commonmark", {"html": True})
          .enable("table")
          .enable("strikethrough"))
    footnote_plugin(md)
    deflist_plugin(md)
    anchors_plugin(md, min_level=1, max_level=3)
    dollarmath_plugin(md)
    md.core.ruler.after("inline", "repair_failed_strong", _repair_failed_strong)
    _MDIT = md
    return _MDIT


# --------------------------------------------------------------- AI 紧贴加粗修复

_TIGHT_STRONG = re.compile(r"\*\*([^\s*](?:[^*]*[^\s*])?)\*\*")
_PUNCT_CACHE: dict = {}


def _is_punct(char: str) -> bool:
    """Unicode 标点判定（P* 类别），逐字符缓存。"""
    known = _PUNCT_CACHE.get(char)
    if known is None:
        known = unicodedata.category(char).startswith("P")
        _PUNCT_CACHE[char] = known
    return known


def _fix_failed_strong_text(content: str):
    """把 text 令牌里渲染失败的紧贴加粗对改写为 strong 令牌序列。

    CommonMark 侧翼规则下，AI 中文常见的紧贴加粗会失败留成字面星号：
    闭合 ** 前是标点且后紧跟文字（**……。**判决），或开启 ** 后是标点
    且前是文字（他**“话”**完）。失败的整对留在同一 text 令牌里，这里
    按「紧贴（首尾无空隙）且内侧至少一端贴标点」识别；空隙装饰写法
    （a ** b ** c）、行内码与代码块（独立令牌）不受影响，转义星号
    （\\*\\*字面\\*\\*，内侧贴字母）也不会误伤。
    """
    from markdown_it.token import Token

    pieces = []
    pos = 0
    repaired = False
    for match in _TIGHT_STRONG.finditer(content):
        inner = match.group(1)
        if not (_is_punct(inner[0]) or _is_punct(inner[-1])):
            continue
        repaired = True
        if match.start() > pos:
            pieces.append(Token("text", "", 0, content=content[pos:match.start()]))
        pieces.append(Token("strong_open", "strong", 1))
        pieces.append(Token("text", "", 0, content=inner))
        pieces.append(Token("strong_close", "strong", -1))
        pos = match.end()
    if not repaired:
        return None
    if pos < len(content):
        pieces.append(Token("text", "", 0, content=content[pos:]))
    return pieces


def _repair_failed_strong(state) -> None:
    """markdown-it core rule：在 inline 解析后修复失败的紧贴加粗对。"""
    for block in state.tokens:
        children = getattr(block, "children", None)
        if not children:
            continue
        new_children = []
        touched = False
        for child in children:
            pieces = None
            if child.type == "text" and "**" in child.content:
                pieces = _fix_failed_strong_text(child.content)
            if pieces is not None:
                new_children.extend(pieces)
                touched = True
            else:
                new_children.append(child)
        if touched:
            block.children = new_children


def normalize_cjk_quotes(source: str) -> str:
    """把正文的 ASCII 双引号交替转成中文弯引号 “ ”（开合交替）。

    必须在 markdown-it 渲染前调用，否则 " 会被转义成 &quot;。
    """
    out: List[str] = []
    open_quote = True
    for char in source:
        if char == '"':
            out.append('“' if open_quote else '”')
            open_quote = not open_quote
        else:
            out.append(char)
    return "".join(out)


def slugify(text: str) -> str:
    """标题 → URL-safe slug（保留中文，空格变 -，去除其余标点）。"""
    slug = re.sub(r'[^\w\u4e00-\u9fff\u3400-\u4dbf_-]+', '-', text).strip('-')
    slug = re.sub(r'-{2,}', '-', slug)
    return slug or 'section'


def preprocess_superscript(source: str) -> str:
    """Pandoc 风格上标 N^exp^ → N<sup>exp</sup>（数学公式内部不处理）。"""
    result: List[str] = []
    index = 0
    in_math = False
    while index < len(source):
        if source[index] == '$' and (index == 0 or source[index - 1] != '\\'):
            # 连续 $ 数量不影响切换逻辑（原实现两个分支效果一致）
            while index < len(source) and source[index] == '$':
                result.append(source[index])
                index += 1
            in_math = not in_math
            continue
        if not in_math:
            match = re.match(r'\^([A-Za-z0-9+\-*/.()]{1,15})\^', source[index:])
            if match and index > 0 and re.match(r'[\w\u4e00-\u9fff)]', source[index - 1:index]):
                exponent = match.group(1)
                base_end = len(result)
                base_start = base_end
                while base_start > 0 and re.match(r'[A-Za-z0-9.()]', result[base_start - 1]):
                    base_start -= 1
                base = "".join(result[base_start:base_end])
                result[base_start:] = [base + '<sup>' + exponent + '</sup>']
                index += match.end()
                continue
        result.append(source[index])
        index += 1
    return "".join(result)


_LATEX_SYMBOLS = {
    r"\sim": "~", r"\approx": "≈", r"\simeq": "≃", r"\equiv": "≡",
    r"\times": "×", r"\cdot": "·", r"\div": "÷",
    r"\leq": "≤", r"\geq": "≥", r"\ne": "≠", r"\neq": "≠",
    r"\pm": "±", r"\mp": "∓",
    r"\to": "→", r"\rightarrow": "→", r"\leftarrow": "←", r"\Rightarrow": "⇒",
    r"\infty": "∞", r"\partial": "∂", r"\nabla": "∇",
    r"\alpha": "α", r"\beta": "β", r"\gamma": "γ", r"\delta": "δ",
    r"\theta": "θ", r"\lambda": "λ", r"\mu": "μ", r"\pi": "π",
    r"\sigma": "σ", r"\phi": "φ", r"\psi": "ψ", r"\omega": "ω",
    r"\Gamma": "Γ", r"\Delta": "Δ", r"\Theta": "Θ", r"\Lambda": "Λ",
    r"\Sigma": "Σ", r"\Phi": "Φ", r"\Psi": "Ψ", r"\Omega": "Ω",
}


def _latex_to_html(latex: str) -> str:
    """把 LaTeX 数学表达式转为 HTML（<sup>/<sub>）。

    PyMuPDF 的 Story 引擎不支持 MathML 的 <msup>/<msub>，会把它压平成
    普通文本（如 10^5 ~ 10^6 变成 “105~106”）；而 HTML 的 <sup>/<sub>
    在 fitz 与浏览器中都能正确渲染为上/下标。本书公式均为简单上标、下标、
    \\sim、\\infty、\\sqrt，故用轻量转换即可同时兼容两条 PDF 路径。
    """
    i = 0
    n = len(latex)

    def parse(until_close: bool) -> str:
        nonlocal i
        out: list = []
        while i < n:
            c = latex[i]
            if c == "}" and until_close:
                i += 1
                return "".join(out)
            if c == "{":
                i += 1
                out.append(parse(True))
                continue
            if c == "^":
                i += 1
                out.append("<sup>" + _script() + "</sup>")
                continue
            if c == "_":
                i += 1
                out.append("<sub>" + _script() + "</sub>")
                continue
            if c == "\\":
                m = re.match(r"\\[a-zA-Z]+", latex[i:])
                if m:
                    cmd = m.group(0)
                    i += len(cmd)
                    out.append(_cmd(cmd))
                    continue
                i += 1  # 转义字符，如 \{ \} \( \)
                if i < n:
                    out.append(latex[i])
                    i += 1
                continue
            out.append(c)
            i += 1
        return "".join(out)

    def _script() -> str:
        nonlocal i
        if i < n and latex[i] == "{":
            i += 1
            return parse(True)
        if i < n:
            ch = latex[i]
            i += 1
            return ch
        return ""

    def _cmd(cmd: str) -> str:
        if cmd == r"\sqrt":
            return "√" + _script()
        if cmd == r"\frac":
            return "<sup>" + _script() + "</sup>⁄<sub>" + _script() + "</sub>"
        return _LATEX_SYMBOLS.get(cmd, "")

    return parse(False)


def _convert_latex_to_html(html: str) -> str:
    """将 dollarmath 插件输出的占位 span/div 转为 HTML 上/下标（<sup>/<sub>）。

    供 PyMuPDF（fitz）PDF 路径使用：其 Story 引擎不支持 MathML 的
    <msup>/<msub>，会把它压平成普通文本；HTML 上/下标在 fitz 与浏览器
    中都能正确渲染。
    """
    def replace(match):
        return '<span class="math">' + _latex_to_html(match.group(2)) + "</span>"

    return re.sub(
        r'<(span|div) class="math (?:inline|display)">(.*?)</\1>',
        replace, html, flags=re.DOTALL)


def _convert_latex_to_mathml(html: str) -> str:
    """将 dollarmath 插件输出的占位 span/div 转为真实 MathML。

    供浏览器 HTML / EPUB 等支持 MathML 的环境使用，显示更规范。
    latex2mathml 不可用时回退为原始占位（不阻断构建）。
    """
    try:
        from latex2mathml.converter import convert
    except ImportError:
        html = _convert_latex_to_html(html)
        return html

    def replace(match):
        latex = match.group(2)
        try:
            return convert(latex)
        except Exception:
            return match.group(0)

    return re.sub(
        r'<(span|div) class="math (?:inline|display)">(.*?)</\1>',
        replace, html, flags=re.DOTALL)


def render_markdown(files: List[str], *, math_format: str = "html") -> str:
    """将多个 md 文件（相对 config.ROOT 的路径）分别渲染后拼接为 HTML 片段。

    各文件独立渲染，保证每个文件的 H1 都能生成锚点 id。
    """
    # print("math_format", math_format)
    md = get_markdown_renderer()
    fragments: List[str] = []
    for relpath in files:
        source = (config.ROOT / relpath).read_text(encoding="utf-8")
        source = normalize_cjk_quotes(source)
        source = preprocess_superscript(source)
        fragments.append(md.render(source))
        report_file(relpath)
    joined = "".join(fragments)
    if math_format == "html":
        joined = _convert_latex_to_html(joined)
    else:
        joined = _convert_latex_to_mathml(joined)
    return joined
