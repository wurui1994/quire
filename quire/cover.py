"""封面：默认排版式封面 + 项目自定义封面插件钩子。

默认封面是 800×1000 的点阵网格底纹 + 排版框架（丛书名 / 卷号水印 /
标题 / 作者 / 日期 / 进度刻度）。需要独特视觉设计的书（如 freedom 的
「约束场」七层环）通过 quire.toml 配置封面插件：

    [cover]
    plugin = "scripts/cover.py"

插件为普通 Python 文件，需实现：

    def build_cover_html(cover_id, title, subtitle, *,
                         vol_index, total_vols, is_preface, config) -> str

config 即 quire.config 模块（可用 config.SERIES / config.AUTHOR /
config.PUB_DATE_DISPLAY / config.COVER_TEMPLATE / config.fill_template 等）。
返回值是完整封面 HTML 片段，将插入阅读页正文顶部。
"""

import re
from pathlib import Path

from . import config

_CN_NUM = r'[〇一二三四五六七八九十百\d]+'

# 插件模块缓存：{路径: (mtime, module)}，serve 模式下改动即重载。
_PLUGIN_CACHE = {}


# ---------------------------------------------------------------- 默认几何

def build_cover_geometry(cover_id: str) -> str:
    """默认封面几何：点阵网格底纹（样式由 cover.css 的 .grid-dot 驱动）。"""
    defs = (
        f'<defs>'
        f'<pattern id="grid-{cover_id}" width="46" height="46" '
        f'patternUnits="userSpaceOnUse">'
        f'<circle cx="1.5" cy="1.5" r="1.2" class="grid-dot"/>'
        f'</pattern>'
        f'</defs>'
    )
    grid = (f'<rect x="0" y="0" width="800" height="1000" '
            f'fill="url(#grid-{cover_id})"/>')
    return (
        f'<svg class="cover-svg" viewBox="0 0 800 1000" '
        f'preserveAspectRatio="xMidYMid slice" '
        f'xmlns="http://www.w3.org/2000/svg">'
        f'{defs}{grid}'
        f'</svg>'
    )


def _parse_vol_tag(subtitle: str, series: str) -> tuple:
    """subtitle 形如 "卷X·分篇 · {丛书名}" → (卷号标签, 卷内副题)。"""
    suffix = f" · {series}"
    base = subtitle[:-len(suffix)] if subtitle.endswith(suffix) else subtitle
    if base == series:
        base = ""
    parts = [part for part in base.split("·") if part] if base else []
    vol_tag = parts[0] if parts else ""
    cover_sub = parts[1] if len(parts) > 1 else ""
    return vol_tag, cover_sub


def _default_cover(cover_id: str, title: str, subtitle: str, *,
                   vol_index: int = -1, total_vols: int = 0,
                   is_preface: bool = False) -> str:
    if is_preface:
        title = config.SERIES
        subtitle = ""

    vol_tag, cover_sub = _parse_vol_tag(subtitle, config.SERIES)
    if is_preface:
        vol_glyph, vol_tag, cover_sub = "序", "总序", "丛书前言"
    else:
        match = re.search(r'卷(' + _CN_NUM + r')', vol_tag)
        vol_glyph = match.group(1) if match else ""

    geometry = build_cover_geometry(f"vol-{cover_id}")

    # 底部进度刻度：仅标准卷（有序号）显示
    if vol_index >= 0 and total_vols > 0:
        ticks = "".join(
            f'<i class="tick{" active" if tick_index == vol_index else ""}"></i>'
            for tick_index in range(total_vols))
        layer_indicator = f'<div class="cover-progress">{ticks}</div>'
    else:
        layer_indicator = ""

    return config.fill_template(config.COVER_TEMPLATE,
        cover_id=f"vol-{cover_id}", series=config.SERIES, title=title,
        subtitle=cover_sub, vol_tag=vol_tag, vol_glyph=vol_glyph,
        author=config.AUTHOR, pub_date=config.PUB_DATE_DISPLAY,
        geometry=geometry, layer_indicator=layer_indicator)


# ---------------------------------------------------------------- 插件钩子

def _load_plugin(path: Path):
    """加载封面插件模块（按 mtime 缓存；serve 模式下编辑插件后刷新即生效）。"""
    mtime = path.stat().st_mtime
    cached = _PLUGIN_CACHE.get(str(path))
    if cached is not None and cached[0] == mtime:
        return cached[1]

    import importlib.util
    module_name = f"quire_cover_plugin_{re.sub(r'\\W', '_', path.stem)}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    if not hasattr(module, "build_cover_html"):
        raise ValueError(f"封面插件缺少 build_cover_html：{path}")
    _PLUGIN_CACHE[str(path)] = (mtime, module)
    return module


def build_cover_html(cover_id: str, title: str, subtitle: str, *,
                     vol_index: int = -1, total_vols: int = 0,
                     is_preface: bool = False) -> str:
    """生成卷封面 HTML：优先项目封面插件，否则默认排版式封面。"""
    book = config.current()
    if book.cover_plugin is not None:
        plugin_path = Path(book.cover_plugin)
        if plugin_path.is_file():
            plugin = _load_plugin(plugin_path)
            return plugin.build_cover_html(cover_id, title, subtitle,
                                           vol_index=vol_index,
                                           total_vols=total_vols,
                                           is_preface=is_preface,
                                           config=config)
    return _default_cover(cover_id, title, subtitle, vol_index=vol_index,
                          total_vols=total_vols, is_preface=is_preface)
