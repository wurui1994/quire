"""跨平台中文字体发现与选择（供 fitz / PyMuPDF 排版 PDF 嵌入使用）。

各平台用其**原生字体子系统**枚举，而不是瞎扫目录 / 只靠 fc-list：
  · macOS   : CoreText (pyobjc) —— fc-list 在 macOS 上发现不了放进
              /System/Library/AssetsV2 的按需字体（PingFang SC 就在那儿）。
  · Linux   : fontconfig（fc-list）—— Linux 的权威字体数据库。
  · Windows : 注册表 Fonts 键 + %WINDIR%\\Fonts。

枚举得到 {family 名: 字体文件路径}，选定后交由 pdf._extract_face 用 fontTools
按 family + 字重（usWeightClass）挑出正确字面（.ttc 常把 SC/TC/多字重混在一起）。

选择优先级（find_cjk_font）：
  1. --font/--font-bold 显式指定（文件路径或 family 名）  → origin='specified'
  2. 仓库自带 / 本地缓存（assets/fonts 下的字体文件）     → origin='bundled'
  3. 环境变量 SONGTI_TTC                                   → origin='env'
  4. 平台系统默认（按 _PREFERENCE 顺序）                   → origin='system'
  5. 都找不到 → 回退 PyMuPDF 默认（不嵌入中文字体）        → origin='none'
结果含 detail 字段（族名 · 来源类别 · 具体路径），供构建时展示所用字体。
"""

import os
import platform
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from . import config

# 各平台默认中文字体优先级（family 名）。书籍正文偏宋体，故 Songti/宋体系在前。
_PREFERENCE = {
    "Darwin": ["Songti SC", "STSong", "PingFang SC", "Heiti SC", "Hiragino Sans GB"],
    "Windows": ["SimSun", "Microsoft YaHei", "SimHei", "NSimSun"],
    "Linux": ["Noto Serif CJK SC", "Noto Sans CJK SC", "Source Han Serif SC",
              "Source Han Sans SC", "WenQuanYi Zen Hei", "WenQuanYi Micro Hei"],
}


# {英文族名: 中文本地化名}，枚举时填充（如 "Songti SC" -> "宋体-简"），仅用于展示。
# macOS 经 CoreText 采集；Linux/Windows 从字体文件 name 表读（_zh_family_from_file）。
# family 的 key 仍用英文名，供 --font 指定与 fontTools 按族名提取字面。
_ZH_NAMES: dict = {}


def _fonts_dir() -> Path:
    return config.fonts_dir()


def _display(family) -> str:
    """展示用名字：有中文本地化名则「中文（英文）」，否则原样返回。"""
    if not family:
        return ""
    zh = _ZH_NAMES.get(family)
    return f"{zh}（{family}）" if zh else family


# ---------------------------------------------------------------- 平台枚举 ----

def _enum_macos() -> dict:
    """CoreText 枚举可用中文字体族 → {family: path}。缺 pyobjc 时返回空。"""
    try:
        import CoreText
    except Exception:
        return {}
    coll = CoreText.CTFontCollectionCreateFromAvailableFonts(None)
    descs = CoreText.CTFontCollectionCreateMatchingFontDescriptors(coll) or []
    out: dict = {}
    for d in descs:
        fam = CoreText.CTFontDescriptorCopyAttribute(d, CoreText.kCTFontFamilyNameAttribute)
        url = CoreText.CTFontDescriptorCopyAttribute(d, CoreText.kCTFontURLAttribute)
        langs = CoreText.CTFontDescriptorCopyAttribute(d, CoreText.kCTFontLanguagesAttribute)
        if not fam or not url:
            continue
        fam = str(fam)
        if fam.startswith("."):          # 系统内部/隐藏族（.PingFang UI 等）
            continue
        path = url.path()
        if not path:
            continue
        is_cjk = langs and any(str(lang).split("-")[0] in ("zh", "ja", "ko") for lang in langs)
        if is_cjk:
            if out.setdefault(fam, str(path)) == str(path) and fam not in _ZH_NAMES:
                # 顺带取中文本地化族名（宋体-简 / 苹方-简 …），仅用于展示
                try:
                    font = CoreText.CTFontCreateWithFontDescriptor(d, 0.0, None)
                    zh, _ = CoreText.CTFontCopyLocalizedName(
                        font, CoreText.kCTFontFamilyNameKey, None)
                    if zh and str(zh) != fam:
                        _ZH_NAMES[fam] = str(zh)
                except Exception:
                    pass
    return out


def _enum_linux() -> dict:
    """fontconfig 枚举中文字体族 → {family: path}。

    fc-list 对同一字体会同时返回英文族名与本地化（中文）族名（如 "Source Han Sans CN"
    与 "思源黑体 CN"）。只保留 ASCII（英文）族名作为 --font 的键：中文族名交给
    _zh_family_from_file 按 name 表精确配对后用于展示，从而同一字体不重复列出、且
    --font 取值恒为英文。某字体若全无英文族名（极少见），退用本地化族名以免漏掉。
    """
    if not shutil.which("fc-list"):
        return {}
    out: dict = {}
    try:
        res = subprocess.run(["fc-list", ":lang=zh", "file", "family"],
                             capture_output=True, text=True)
        for line in res.stdout.splitlines():
            if ":" not in line:
                continue
            path, _, fams = line.partition(":")
            path = path.strip()
            all_fams = [f.strip() for f in fams.split(",") if f.strip()]
            ascii_fams = [f for f in all_fams if f.isascii()]
            for fam in (ascii_fams or all_fams):
                if path:
                    out.setdefault(fam, path)
    except Exception:
        pass
    return out


def _cjk_family(path: Path):
    """用 fontTools 读族名，并按 cmap 是否含常用汉字判定是否为中文字体。

    用 lazy=True 打开：只需读 cmap / name 表判定，无需把整个字体（含 glyf 等大表）
    一次性载入。对仓库自带的 .ttc（Songti/PingFang 各 ~0.8s eager）从 ~1.6s 降到
    ~0.02s，每次构建省约 1s（字体探测在 configure_pdf_font 阶段、进度条之前发生）。
    """
    from fontTools.ttLib import TTFont, TTCollection
    try:
        try:
            faces = list(TTCollection(str(path), lazy=True).fonts)
        except Exception:
            faces = [TTFont(str(path), lazy=True)]
        for face in faces:
            cmap = face.getBestCmap() if face.get("cmap") else None
            if cmap and 0x4E2D in cmap:          # “中” U+4E2D
                return face["name"].getDebugName(1)
    except Exception:
        pass
    return None


def _zh_family_from_file(path, family=None):
    """从字体文件 name 表读中文本地化族名（优先简体 zh-CN），读不到/无翻译返回 None。

    fontconfig 不提供本地化族名，macOS 之外的平台靠此补齐 _ZH_NAMES。
    family 给定时，在 .ttc 内优先取英文名（nameID 1）匹配 family 的字面，避免
    多区域字面串台（如 NotoSansCJK-Thin.ttc 含 SC/TC/HK 多面，否则会读到 SC 面的名）。
    若所选字面的中文记录与其英文名一致（Noto CJK 这类无中文译名，仅以英文挂 zh 语言），
    视作无翻译，返回 None，避免展示成「Noto Sans CJK SC（Noto Sans CJK HK）」。
    """
    from fontTools.ttLib import TTFont, TTCollection
    # Windows 平台中文 langID → 优先级（简体在前）：0x0804=zh-CN, 0x1004=zh-SG,
    # 0x0c04=zh-HK, 0x0404=zh-TW；Mac 平台：19=简体, 2=繁体
    win_prio = {0x0804: 0, 0x1004: 1, 0x0c04: 2, 0x0404: 3}
    mac_prio = {19: 0, 2: 3}
    try:
        try:
            faces = list(TTCollection(str(path), lazy=True).fonts)
        except Exception:
            faces = [TTFont(str(path), lazy=True)]
        # 指定 family 时，把英文名匹配的字面排到最前
        if family:
            low = family.lower()

            def _face_key(f):
                nt = f.get("name")
                en = (nt.getDebugName(1) or "").lower() if nt else ""
                return 0 if en == low else 1
            faces = sorted(faces, key=_face_key)
        for face in faces:
            nt = face.get("name")
            if not nt:
                continue
            en = (nt.getDebugName(1) or "").strip()
            cands = []  # [(优先级, 中文名), ...]
            for rec in nt.names:
                if rec.nameID not in (1, 16):
                    continue
                if rec.platformID == 3:
                    prio = win_prio.get(rec.langID)
                elif rec.platformID == 1:
                    prio = mac_prio.get(rec.langID)
                else:
                    continue
                if prio is None:
                    continue
                try:
                    s = rec.toUnicode().strip()
                except Exception:
                    continue
                if s:
                    cands.append((prio, s))
            if not cands:
                continue
            cands.sort(key=lambda x: x[0])
            zh = cands[0][1]
            # 无真实翻译（中文记录与英文名一致，如 Noto CJK）→ 视作无中文名
            if en and zh.lower() == en.lower():
                return None
            return zh
    except Exception:
        pass
    return None


def _enum_windows() -> dict:
    """注册表 Fonts 键（回退 %WINDIR%\\Fonts）枚举，fontTools 过滤中文 → {family: path}。"""
    fonts_dir = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    paths: list = []
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts")
        i = 0
        while True:
            try:
                _, val, _ = winreg.EnumValue(key, i)
            except OSError:
                break
            i += 1
            p = Path(val)
            if not p.is_absolute():
                p = fonts_dir / val
            if p.exists():
                paths.append(p)
    except Exception:
        if fonts_dir.exists():
            paths = [p for p in fonts_dir.glob("*") if p.suffix.lower() in (".ttc", ".ttf", ".otf")]
    out: dict = {}
    for p in paths:
        fam = _cjk_family(p)
        if fam:
            out.setdefault(fam, str(p))
    return out


@lru_cache(maxsize=1)
def _enum_system() -> dict:
    """按当前平台的原生字体子系统枚举系统中文字体 → {family: path}。"""
    system = platform.system()
    if system == "Darwin":
        return _enum_macos()
    if system == "Windows":
        return _enum_windows()
    return _enum_linux()


def _bundled_fonts() -> list:
    """仓库自带 / 本地缓存的中文字体文件（assets/fonts 下的 .ttc/.ttf/.otf）。

    他人 clone 后即便无系统中文字体也能构建；此目录优先于系统默认。
    返回 [(family, path), ...]，family 用 fontTools 读取，读不到则用文件名兜底。
    """
    out = []
    d = _fonts_dir()
    if not d.exists():
        return out
    for p in sorted(d.glob("*")):
        if p.suffix.lower() not in (".ttc", ".ttf", ".otf"):
            continue
        fam = _cjk_family(p) or p.stem
        out.append((fam, str(p)))
    return out


def _env_font():
    """环境变量 SONGTI_TTC 指定的字体文件（若存在）。返回 (family, path) 或 None。"""
    env = os.environ.get("QUIRE_FONT") or os.environ.get("SONGTI_TTC")
    if env and Path(env).exists():
        p = Path(env)
        return (_cjk_family(p) or p.stem, str(p))
    return None


@lru_cache(maxsize=1)
def _enum() -> dict:
    """汇总所有来源的中文字体 {family: path}（供 --list-fonts 展示 / 按名查找）。

    合并顺序体现优先级：仓库自带 → 环境变量 → 系统字体（先者不被后者覆盖）。
    """
    out: dict = {}
    for fam, path in _bundled_fonts():
        out.setdefault(fam, path)
    env = _env_font()
    if env:
        out.setdefault(env[0], env[1])
    for fam, path in _enum_system().items():
        out.setdefault(fam, path)
    return out


# ---------------------------------------------------------------- 对外接口 ----

def list_cjk_fonts() -> list:
    """枚举所有可用中文字体，返回 [(display, family, Path, origin), ...]。

    display 为展示名「中文（英文）」（macOS 上取自 CoreText 本地化名），family 为
    传给 --font 的英文族名。origin ∈ {bundled, env, system}，按来源分组：
    仓库自带 → 环境变量 → 系统，组内按 family 名排序。
    """
    bundled = {fam: path for fam, path in _bundled_fonts()}
    system = _enum_system()          # 触发中文名采集（填充 _ZH_NAMES）
    env = _env_font()
    env_map = {env[0]: env[1]} if env else {}

    rows = []
    seen = set()
    for group, mapping in (("bundled", bundled), ("env", env_map), ("system", system)):
        for fam in sorted(mapping):
            if fam in seen:
                continue
            seen.add(fam)
            # 补齐中文本地化族名：macOS 经 CoreText 已采；其余从字体文件 name 表读
            if fam not in _ZH_NAMES:
                zh = _zh_family_from_file(mapping[fam], fam)
                if zh:
                    _ZH_NAMES[fam] = zh
            rows.append((_display(fam), fam, Path(mapping[fam]), group))
    return rows


def _lookup(name: str):
    """按 family 名在枚举结果中查（大小写不敏感），命中返回 (family, path)。"""
    table = _enum()
    if name in table:
        return name, table[name]
    low = name.lower()
    for fam, path in table.items():
        if fam.lower() == low:
            return fam, path
    return None


_ORIGIN_LABEL = {
    "specified": "指定",
    "bundled": "仓库自带/缓存",
    "env": "环境变量 QUIRE_FONT/SONGTI_TTC",
    "system": "平台系统默认",
    "none": "PyMuPDF 默认（未嵌入中文字体）",
}


def _face_style(f):
    """字面（字重风格）名（nameID 2, subfamily）。如 Light/Regular/Medium/Semibold。"""
    try:
        return (f["name"].getDebugName(2) or "").strip()
    except Exception:
        return ""


def _default_regular_weight(family):
    """未显式指定字面时，常规字重的默认目标字重（usWeightClass）。

    PingFang SC 默认 Regular(400) 偏细，默认调粗到 Medium(500)；
    其余字体保持 Regular(400)。粗体一律 Semibold/Bold(700)。
    """
    if family and "pingfang" in family.lower():
        return 500
    return 400


def _parse_face(value):
    """解析 --font/--font-bold 取值为 (family, style, path)。

    支持三类输入：
      · 字体文件路径（/path/to/x.ttc）        → (读出的 family, None, Path)
      · 族名（"PingFang SC"）                 → (族名, None, Path)
      · 「族名 字面」("PingFang SC Light" /
        "PingFang SC Semibold")               → (族名, "Light", Path)，字面取最后一段
    无法解析返回 (None, None, None)。
    """
    if not value:
        return None, None, None
    p = Path(value)
    if p.exists():                       # 直接给了文件路径
        return _cjk_family(p) or p.stem, None, p
    hit = _lookup(value)                 # 当作完整族名查
    if hit:
        return hit[0], None, Path(hit[1])
    # 「族名 字面」：最后一段作字面，其余作族名再查
    parts = value.rsplit(" ", 1)
    if len(parts) == 2:
        fam, style = parts[0], parts[1]
        hit = _lookup(fam)
        if hit:
            return hit[0], style, Path(hit[1])
    return None, None, None


def _result(family, path, origin: str, regular_style=None, bold_style=None,
            regular_weight=400, bold_weight=700, bold_family=None) -> dict:
    """构造统一的字体选择结果，含来源类别、具体路径与字面（style/weight）。

    regular_style / bold_style：字面（字重风格）名，如 "Light"/"Semibold"，
    None 表示按字重（regular_weight/bold_weight）自动挑最近的字面。
    供 pdf._extract_face 在 .ttc 内精确选面
    （如 "PingFang SC Light" 而不是默认 Regular）。
    """
    p = Path(path) if path else None
    # 补齐中文本地化族名：system 来源触发系统枚举（macOS 即 CoreText），
    # 其余来源直接从字体文件 name 表读，跨平台一致且不依赖 CoreText。
    if family and family not in _ZH_NAMES:
        if origin == "system":
            _enum_system()
        if family not in _ZH_NAMES and p and p.exists():
            zh = _zh_family_from_file(p, family)
            if zh:
                _ZH_NAMES[family] = zh
    disp = _display(family) if family else (p.name if p else "默认")
    style_tag = ""
    if regular_style or bold_style:
        style_tag = f"〔{regular_style or '默认'}/{bold_style or '默认'}〕"
    return {
        "family": family,
        "bold_family": bold_family or family,
        "regular": p,
        "bold": p,
        "regular_style": regular_style,
        "bold_style": bold_style,
        "regular_weight": regular_weight,
        "bold_weight": bold_weight,
        "origin": origin,                     # specified/bundled/env/system/none
        "source": origin,                     # 兼容旧字段
        "path": str(p) if p else None,
        # 展示行：中文（英文）名 · 来源类别 · 字面 · 具体路径
        "label": disp,
        "detail": (f"{disp}（{_ORIGIN_LABEL.get(origin, origin)}）{style_tag}"
                   + (f"：{p}" if p else "")),
    }


def find_cjk_font(regular=None, bold=None) -> dict:
    """选择一款可用 CJK 字体，返回带来源类别与具体路径的结果。

    regular/bold 可为字体**文件路径**、**族名**（如 "PingFang SC"），或
    「族名 字面」字面规格（如 "PingFang SC Light" /
    "PingFang SC Semibold"）。字面规格让 fitz 在 .ttc 内精确选面，
    而不只是按字重挑最近的。
    选择优先级：
      1. 显式指定（--font/--font-bold）解析成功 → origin='specified'
      2. 仓库自带 / 本地缓存（assets/fonts）        → origin='bundled'
      3. 环境变量 SONGTI_TTC                        → origin='env'
      4. 平台系统默认（按 _PREFERENCE 顺序）        → origin='system'
      5. 都找不到 → regular=None, origin='none'（调用方回退 PyMuPDF 默认）
    返回 dict 关键字段：family / bold_family / regular / bold / regular_style /
    bold_style / regular_weight / bold_weight / path / origin / label / detail。
    family 供 pdf._extract_face 在 .ttc 内按族名+字面/字重挑面。
    """
    # 1. 显式指定
    if regular or bold:
        r = _parse_face(regular)
        if r[0]:
            fam, style, reg = r
            res = _result(fam, reg, "specified",
                          regular_style=style,
                          regular_weight=400 if style else _default_regular_weight(fam))
            b = _parse_face(bold) if bold else None
            if b and b[0]:
                res["bold"] = b[2]
                res["bold_family"] = b[0]
                res["bold_style"] = b[1]
                res["bold_weight"] = 700
            else:
                res["bold_style"] = None
                res["bold_weight"] = 700
            return res
        # 指定但没解析成功 → 落到自动探测

    # 2. 仓库自带 / 本地缓存（第一优先级）
    bundled = _bundled_fonts()
    if bundled:
        # 若默认族名优先列表命中自带字体，用它；否则取第一个
        pref = _PREFERENCE.get(platform.system(), [])
        table = {fam: path for fam, path in bundled}
        for fam in pref:
            for bfam, bpath in bundled:
                if bfam.lower() == fam.lower():
                    return _result(bfam, bpath, "bundled")
        fam, path = bundled[0]
        return _result(fam, path, "bundled")

    # 3. 环境变量
    env = _env_font()
    if env:
        return _result(env[0], env[1], "env")

    # 4. 平台系统默认
    sysfonts = _enum_system()
    for fam in _PREFERENCE.get(platform.system(), []):
        low = fam.lower()
        for f, path in sysfonts.items():
            if f.lower() == low:
                return _result(f, path, "system")
    if sysfonts:                              # 优先级未命中：取枚举第一个
        fam, path = sorted(sysfonts.items())[0]
        return _result(fam, path, "system")

    # 5. 全落空
    return _result(None, None, "none")


def face_styles(path) -> list:
    """返回字体文件内所有字面（subfamily）名列表，供 --list-fonts 展示可用「族名 字面」。"""
    from fontTools.ttLib import TTFont, TTCollection
    out: list = []
    try:
        try:
            faces = list(TTCollection(str(path), lazy=True).fonts)
        except Exception:
            faces = [TTFont(str(path))]
        for f in faces:
            s = _face_style(f)
            if s and s not in out:
                out.append(s)
    except Exception:
        pass
    return out


def cache_font(regular=None, bold=None) -> list:
    """把解析到的中文字体**完整文件**缓存到 assets/fonts，便于离线/可复现构建。

    解析规则同 find_cjk_font：regular/bold 可为字体文件路径、族名（"PingFang SC"），
    或「族名 字面」（"PingFang SC Light"）。不传则缓存当前平台系统默认中文字体。
    返回 [(family, style, src_path, dst_path, status), ...]，status ∈
    {"cached" 新缓存, "recopied" 大小不符已覆盖, "skipped" 已存在且完整}。
    """
    targets = []
    seen_src = set()

    def add(value):
        fam, style, path = _parse_face(value) if value else (None, None, None)
        if path and path not in seen_src:
            seen_src.add(path)
            targets.append((fam, style, Path(path)))

    add(regular)
    add(bold)
    if not targets:                          # 没显式指定 → 用系统默认
        info = find_cjk_font()
        if info["regular"]:
            targets.append((info["family"], None, Path(info["regular"])))
        if info["bold"] and info["bold"] != info["regular"]:
            targets.append((info.get("bold_family"), None, Path(info["bold"])))

    if not targets:
        return []

    dst_dir = _fonts_dir()
    dst_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for fam, style, src in targets:
        if not src.exists():
            continue
        if src.resolve().parent == dst_dir.resolve():
            # 源本身就在缓存目录（如默认字体恰好已 bundled）→ 视为已缓存
            results.append((fam, style, src, src, "skipped"))
            continue
        dst = dst_dir / src.name
        # 已存在且大小一致 → 视为完整缓存，跳过（不覆盖）
        if dst.exists() and dst.stat().st_size == src.stat().st_size:
            results.append((fam, style, src, dst, "skipped"))
            continue
        existed = dst.exists()
        try:
            # 仅复制内容（copyfile），避免 macOS 对系统字体 copy2 设置
            # chflags/扩展属性时触发 Operation not permitted。
            shutil.copyfile(src, dst)
        except Exception as e:
            print(f"⚠️ 缓存失败 {src.name}：{e}")
            continue
        status = "recopied" if existed else "cached"
        results.append((fam, style, src, dst, status))
    return results
