"""构建进度显示（基于 rich）。

为每种“处理”显示一条独立进度条——各自按自己的数量推进，不做任何时间权重换算：
- 渲染 Markdown：逐 .md 源文件渲染的次数
- 生成 HTML / 生成 EPUB / 排版 PDF：各自生成的产物份数
  （PDF 合本会按卷细分，每卷排版计一步）

既有 print(· 卷名 / 产物行 / 书签数) 改为通过 note() 收集，在进度条收起后统一输出，
避免与 live 进度条相互穿插、把产物行顶到进度条上方。
rich 不可用时整体退化为无操作，不影响构建。

计时用自维护列（_ElapsedColumn）：
- 未完成的任务按墙钟实时走动，精确到 0.1s；
- 一旦 completed>=total（真正完成）即冻结在那一刻，不再走动。
"""

import time
from contextlib import contextmanager
from typing import Dict, Optional

try:
    from rich.progress import (Progress, ProgressColumn, TextColumn, BarColumn,
                               TaskProgressColumn, MofNCompleteColumn,
                               SpinnerColumn)
    from rich.text import Text
    _RICH = True
except Exception:  # rich 未安装
    _RICH = False


def _fmt_elapsed(sec: float) -> str:
    """格式化为 M:SS.s（<1h）/ H:MM:SS.s（>=1h），精确到 0.1s。"""
    sec = max(0.0, sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h >= 1:
        return f"{int(h)}:{int(m):02d}:{s:04.1f}"
    return f"{int(m):02d}:{s:04.1f}"


if _RICH:

    class _CurrentColumn(ProgressColumn):
        """显示当前正在处理的条目（文件名 / 卷 id 等）。"""

        def render(self, task) -> Text:
            cur = task.fields.get("current")
            if not cur:
                return Text("")
            return Text(f"· {cur}", style="progress.remaining")

    class _ElapsedColumn(ProgressColumn):
        """自维护耗时：持续推进时按墙钟实时走；停止推进超过阈值即视为完成并冻结
        在最后一次推进时刻（避免动态校正造成的“假完成”误冻结，也满足“已结束不走动”）。"""

        def render(self, task) -> Text:
            start = _task_start.get(task.description)
            if start is None:
                return Text("00:00.0", style="progress.elapsed")
            last = _task_last.get(task.description, start)
            # 距最后一次推进已超过阈值（默认 0.5s）→ 认为该任务已完成，冻结耗时。
            if time.time() - last > _FINISH_IDLE:
                elapsed = last - start
            else:
                elapsed = time.time() - start
            return Text(_fmt_elapsed(elapsed), style="progress.elapsed")

    class _Progress:
        def __init__(self, totals: Dict[str, int]) -> None:
            self._progress = Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                MofNCompleteColumn(),
                _CurrentColumn(),
                _ElapsedColumn(),
            )
            self._tasks = {}
            for name, total in totals.items():
                if total > 0:
                    self._tasks[name] = self._progress.add_task(
                        name, total=total, current="")
            self._progress.start()

        def advance(self, name: str, n: int = 1, current: str = None) -> None:
            tid = self._tasks.get(name)
            if tid is None:
                return
            # 首次推进时记录该任务起点（墙钟）。
            if name not in _task_start:
                _task_start[name] = time.time()
            # 每次推进都更新“最后推进时刻”，供 _ElapsedColumn 判定是否完成。
            _task_last[name] = time.time()
            if current is not None:
                self._progress.update(tid, advance=n, current=current)
            else:
                self._progress.update(tid, advance=n)
            # 兜底：若预估总数偏小（如合本末卷）导致 completed 超过 total，
            # 把 total 拉平到 completed，避免显示溢出（如 1408/1356）。
            task = self._progress.tasks[tid]
            if task.completed > task.total:
                self._progress.update(tid, total=task.completed)

        def set_total(self, name: str, total: int) -> None:
            tid = self._tasks.get(name)
            if tid is not None:
                self._progress.update(tid, total=max(total, 1))

        def stop(self) -> None:
            self._progress.stop()

else:  # 无 rich：零开销占位

    class _Progress:
        def __init__(self, totals: Dict[str, int]) -> None:
            pass

        def advance(self, name: str, n: int = 1, current: str = None) -> None:
            pass

        def set_total(self, name: str, total: int) -> None:
            pass

        def stop(self) -> None:
            pass


_manager: Optional[_Progress] = None
_section_time: Dict[str, float] = {}  # 各处理累计耗时（秒），与进度显示解耦
_counts: Dict[str, int] = {}  # 各处理已完成数（自维护，确保与进度条一致）
_task_start: Dict[str, float] = {}  # 各处理首次推进的墙钟时刻（自维护计时用）
_task_last: Dict[str, float] = {}   # 各处理最后一次推进的墙钟时刻（判定完成用）
_build_notes: list = []              # 进度条收起后统一输出的产物行 / 书签数等
_FINISH_IDLE: float = 0.5           # 停止推进超过该秒数即视为完成并冻结耗时


def note(msg: str) -> None:
    """收集一行“产物汇总”文本，待进度条收起后（build_progress 退出时）统一打印。

    直接 print 会与 rich 的 live 进度条互相穿插，把产物行顶到进度条上方，
    造成“进度条跑到最下面”的观感；改为收集后统一输出则顺序干净。
    """
    _build_notes.append(msg)


def get_notes() -> list:
    return _build_notes


@contextmanager
def build_progress(totals: Dict[str, int]):
    """totals: {处理名: 总数}；rich 不可用时退化为无操作。"""
    global _manager, _section_time, _counts, _task_start, _task_last
    global _build_notes
    _section_time = {}
    _counts = {}
    _task_start = {}
    _task_last = {}
    _task_finished = {}
    _build_notes = []
    if not _RICH:
        _manager = None
        yield None
        return
    ctrl = _Progress(totals)
    _manager = ctrl
    try:
        yield ctrl
    finally:
        ctrl.stop()
        _manager = None


def report(task: str, n: int = 1, current: str = None) -> None:
    """推进某条处理进度；未启用进度时零开销。"""
    _counts[task] = _counts.get(task, 0) + max(n, 0)
    if _manager is not None:
        _manager.advance(task, n, current)


def report_file(relpath: str) -> None:
    """render_markdown 逐文件渲染处调用。

    relpath 仅用于内部计数，不显示在进度条上——文件路径碎片对用户无意义。
    """
    report("渲染 Markdown", 1)


def add_time(task: str, seconds: float) -> None:
    """累计某处理的总耗时（与是否显示进度条无关）。"""
    global _section_time
    _section_time[task] = _section_time.get(task, 0.0) + seconds


def set_total(task: str, total: int) -> None:
    """运行中校正某处理的进度总数（如 PDF 由卷数改为真实页数）。"""
    if _manager is not None:
        _manager.set_total(task, total)


def get_completed(task: str) -> int:
    """返回某处理当前已完成数（自维护计数，与进度条显示一致）。"""
    return _counts.get(task, 0)


def get_times() -> Dict[str, float]:
    """返回各处理累计耗时（秒）的副本。"""
    return dict(_section_time)
