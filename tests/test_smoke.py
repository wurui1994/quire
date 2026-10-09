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
        book.rescan()
        names = [Path(f).name for f in book.volumes[0].files]
        assert names == ["目录.md", "ch01-开篇.md", "ch02-第二章.md"], names

        # 4) 构建（HTML + EPUB；PDF 依赖系统字体，冒烟不覆盖）
        outputs = build(book, html=True, epub=True, pdf=False, show_progress=False)
        names = sorted(p.name for p in outputs)
        assert names == ["vol1.epub", "vol1.html"], names

        html = (book.dist / "vol1.html").read_text(encoding="utf-8")
        assert "测试书" in html
        assert "第二章标题" in html
        assert 'id="vol-vol1"' in html          # 默认封面已嵌入
        assert (book.dist / "vol1.epub").stat().st_size > 1000

        # 5) CLI：list（子进程，验证 python -m quire 入口与 -d 定位）
        proc = subprocess.run(
            [sys.executable, "-m", "quire", "-d", str(root), "list", "--words"],
            capture_output=True, text=True, check=True)
        assert "vol1" in proc.stdout and "字" in proc.stdout, proc.stdout

    print("smoke OK")


if __name__ == "__main__":
    main()
