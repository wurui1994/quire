"""冒烟测试：不依赖 pytest，直接运行。

    python tests/test_smoke.py

覆盖：脚手架 init → 卷自动发现 → new 章节 → HTML/EPUB 构建 → CLI list。
"""

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from quire import Book, Volume, build, init_project, load_book, new_chapter


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "mybook"

        # 1) 脚手架
        init_project(str(root), title="测试书", author="冒烟")
        assert (root / "quire.toml").is_file()
        assert (root / "book" / "vol1" / "chapters" / "ch01-开篇.md").is_file()

        # 2) 自动发现：一卷 = vol1（目录.md + 前言? + chapters/ch01）
        book = load_book(str(root))
        assert isinstance(book, Book)
        assert [v.id for v in book.volumes] == ["vol1"], book.volumes
        vol = book.volumes[0]
        assert isinstance(vol, Volume)
        assert vol.kind == "main"
        assert vol.title == "测试书" and vol.subtitle == "卷一"
        assert [Path(f).name for f in vol.files] == ["目录.md", "ch01-开篇.md"]

        # 3) 新建章节（自动补号）
        new_chapter(book, "vol1/第二章", "第二章标题")
        ch2 = root / "book" / "vol1" / "chapters" / "ch02-第二章.md"
        ch2.write_text(
            "# 第二章标题\n\n"
            "**第六样，自我执行。**判决生效之后，它自己找证据。\n"
            "字面 \\*\\*不是加粗\\*\\* 不动；`**代码**` 不动；a ** b ** c 不动。\n",
            encoding="utf-8")
        book.rescan()
        names = [Path(f).name for f in book.volumes[0].files]
        assert names == ["目录.md", "ch01-开篇.md", "ch02-第二章.md"], names

        # 目录页补 ch02 链接（标准写法：相对 chapters/ 的 .md 链接）
        (root / "book" / "vol1" / "目录.md").write_text(
            "# 《测试书》（卷一）目录\n\n"
            "- [开篇](chapters/ch01-开篇.md)\n"
            "- [第二章标题](chapters/ch02-第二章.md)\n", encoding="utf-8")

        # 4) 构建（HTML + EPUB；PDF 依赖系统字体，冒烟不覆盖）
        outputs = build(book, html=True, epub=True, pdf=False, show_progress=False)
        names = sorted(p.name for p in outputs)
        assert names == ["vol1.epub", "vol1.html"], names

        html = (book.dist / "vol1.html").read_text(encoding="utf-8")
        assert "测试书" in html
        assert "第二章标题" in html
        assert 'id="vol-vol1"' in html          # 默认封面已嵌入
        # AI 中文紧贴加粗修复：失败的 **……。**判决 → strong；
        # 转义星号 / 行内码 / 空隙写法保持字面
        assert "<strong>第六样，自我执行。</strong>判决生效" in html
        assert "**不是加粗**" in html
        assert "<code>**代码**</code>" in html
        assert "a ** b ** c" in html
        # 站内 .md 相对链接 → 同页锚点（与侧栏目录同一套标题 id）
        assert '<a href="#开篇">开篇</a>' in html
        assert '<a href="#第二章标题">第二章标题</a>' in html
        assert "chapters/ch01-开篇.md" not in html
        assert (book.dist / "vol1.epub").stat().st_size > 1000
        # EPUB：目录文档（ch001）里的链接 → 同级章节文档
        import zipfile
        with zipfile.ZipFile(book.dist / "vol1.epub") as zf:
            toc_doc = zf.read("EPUB/text/ch001.xhtml").decode("utf-8")
        assert 'href="ch002.xhtml"' in toc_doc      # ch01-开篇.md → ch002.xhtml
        assert 'href="ch003.xhtml"' in toc_doc      # ch02-第二章.md → ch003.xhtml
        assert "chapters/ch01-开篇.md" not in toc_doc

        # 5) CLI：list（子进程，验证 python -m quire 入口与 -d 定位）
        proc = subprocess.run(
            [sys.executable, "-m", "quire", "-d", str(root), "list", "--words"],
            capture_output=True, text=True, check=True)
        assert "vol1" in proc.stdout and "字" in proc.stdout, proc.stdout

    print("smoke OK")


if __name__ == "__main__":
    main()
