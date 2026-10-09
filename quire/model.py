"""卷（Volume）数据模型与集合工具。

- Volume.id        卷标识（如 "preface" / "vol1" / "supplement"）
- Volume.title     主标题
- Volume.subtitle  副标题（默认为丛书名，用作页面品牌标识）
- Volume.files     源文件相对书根的路径列表
- Volume.label     侧栏 / 博客分组显示名（空则回退 subtitle）
- Volume.kind      front（序/前言）/ main（正卷）/ extra（附录/补充）
"""

from dataclasses import dataclass
from typing import Dict, List


@dataclass
class Volume:
    id: str
    title: str
    subtitle: str
    files: List[str]
    label: str = ""
    kind: str = "main"

    @property
    def is_standard(self) -> bool:
        """正卷（kind="main"），参与封面导航圆点计数。"""
        return self.kind == "main"

    @property
    def is_preface(self) -> bool:
        return self.kind == "front"


def standard_volume_count(volumes: List[Volume]) -> int:
    """正卷总数（用于封面导航圆点）。"""
    return sum(1 for volume in volumes if volume.is_standard)


def volume_index_map(volumes: List[Volume]) -> Dict[str, int]:
    """正卷 id → 0-based 序号（用于封面高亮与进度指示）。"""
    mapping: Dict[str, int] = {}
    index = 0
    for volume in volumes:
        if volume.is_standard:
            mapping[volume.id] = index
            index += 1
    return mapping


def all_volume_files(volumes: List[Volume]) -> List[str]:
    """扁平化所有卷的源文件路径（供 EPUB / 全局目录 / 缓存键使用）。"""
    collected: List[str] = []
    for volume in volumes:
        collected.extend(volume.files)
    return collected
