from __future__ import annotations

from collections.abc import Callable

from gnssgo.download.events import DownloadEvent, DownloadEventType
from gnssgo.gui.i18n import language_manager
from gnssgo.gui.models.tasks import GuiTask, GuiTaskState, GuiTaskType
from gnssgo.gui.services.core_service import CoreService
from gnssgo.models import DownloadPlan

EventSink = Callable[[GuiTask], None]


class TaskService:
    def __init__(self, core: CoreService | None = None) -> None:
        self.core = core or CoreService()
        self.tasks: list[GuiTask] = []
        self._subscribers: list[EventSink] = []

    def subscribe(self, callback: EventSink) -> None:
        self._subscribers.append(callback)

    def create_task(
        self,
        *,
        name: str,
        task_type: GuiTaskType,
        request: dict,
    ) -> GuiTask:
        task = GuiTask(name=name, type=task_type, request=request)
        self.tasks.append(task)
        self._emit(task)
        return task

    def attach_plan(self, task: GuiTask, plan: DownloadPlan) -> None:
        task.plan = plan
        task.total_files = len(plan.download_tasks) or len(plan.remote_files)
        task.total_bytes = plan.estimated_size
        task.state = GuiTaskState.READY
        unavailable = len(plan.unavailable)
        if unavailable:
            task.message = _msg(
                f"Ready · {task.total_files} file(s) · {unavailable} unavailable",
                f"就绪 · {task.total_files} 个文件 · {unavailable} 项无数据",
            )
        else:
            task.message = _msg(
                f"Ready · {task.total_files} file(s)",
                f"就绪 · {task.total_files} 个文件",
            )
        self._emit(task)

    def mark_no_data(self, task: GuiTask, plan: DownloadPlan) -> None:
        task.plan = plan
        task.total_files = 0
        task.total_bytes = None
        task.state = GuiTaskState.COMPLETED
        count = len(plan.unavailable or plan.missing)
        task.message = _msg(
            f"No data found · {count} station-date request(s)",
            f"未找到数据 · {count} 个站点-日期请求",
        )
        self._emit(task)

    def mark_downloading(self, task: GuiTask) -> None:
        task.state = GuiTaskState.DOWNLOADING
        task.message = _msg("Starting download…", "正在开始下载…")
        self._emit(task)

    def handle_download_event(self, task: GuiTask, event: DownloadEvent) -> None:
        """Fold core download events into a user-facing task progress model."""
        remote = event.remote_file
        key = remote.filename if remote is not None else (event.message or "")
        event_type = event.type

        if event_type == DownloadEventType.FILE_STARTED:
            task.state = GuiTaskState.DOWNLOADING
            task.current_file = key or task.current_file
            task.message = (
                _msg(
                    f"Downloading {task.current_file}",
                    f"正在下载 {task.current_file}",
                )
                if task.current_file
                else _msg("Downloading…", "正在下载…")
            )
        elif event_type == DownloadEventType.FILE_PROGRESS:
            if key:
                current = int(event.downloaded_bytes or 0)
                total = int(event.total_bytes) if event.total_bytes else None
                task.file_progress[key] = (current, total)
                task.current_file = key
            task.downloaded_bytes = sum(item[0] for item in task.file_progress.values())
            known_totals = [item[1] for item in task.file_progress.values() if item[1]]
            if known_totals:
                task.total_bytes = max(task.total_bytes or 0, sum(known_totals))
            task.message = _progress_message(task)
        elif event_type == DownloadEventType.FILE_POSTPROCESS_STARTED:
            task.state = GuiTaskState.PROCESSING
            task.current_file = key or task.current_file
            task.message = (
                _msg(
                    f"Processing {task.current_file}",
                    f"正在处理 {task.current_file}",
                )
                if task.current_file
                else _msg("Processing…", "正在处理…")
            )
        elif event_type in {
            DownloadEventType.FILE_VALIDATED,
            DownloadEventType.FILE_SKIPPED,
        }:
            if key:
                task.completed_keys.add(key)
                task.failed_keys.discard(key)
            task.completed_files = len(task.completed_keys)
            task.failed_files = len(task.failed_keys)
            task.message = _file_count_message(task)
        elif event_type == DownloadEventType.FILE_FAILED:
            if key:
                task.failed_keys.add(key)
            task.failed_files = len(task.failed_keys)
            task.message = event.message or (
                _msg(f"Failed: {key}", f"失败：{key}")
                if key
                else _msg("A file failed", "文件下载失败")
            )
        elif event_type == DownloadEventType.TASK_COMPLETED:
            task.state = GuiTaskState.COMPLETED
            task.completed_files = event.completed_files or task.completed_files
            task.current_file = None
            task.message = _msg(
                f"Completed · {task.completed_files}/{task.total_files} file(s)",
                f"已完成 · {task.completed_files}/{task.total_files} 个文件",
            )
        elif event_type == DownloadEventType.TASK_PARTIAL:
            task.state = GuiTaskState.PARTIAL
            task.completed_files = event.completed_files or task.completed_files
            task.current_file = None
            task.message = _msg(
                f"Completed with errors · {task.completed_files} succeeded, "
                f"{task.failed_files} failed",
                f"完成但有错误 · {task.completed_files} 成功，{task.failed_files} 失败",
            )
        elif event_type == DownloadEventType.TASK_FAILED:
            task.state = GuiTaskState.FAILED
            task.current_file = None
            task.message = event.message or _msg("Download failed", "下载失败")
        elif event_type == DownloadEventType.TASK_CANCELLED:
            task.state = GuiTaskState.CANCELLED
            task.current_file = None
            task.message = _msg("Download cancelled", "下载已取消")

        self._emit(task)

    def complete_from_results(self, task: GuiTask, results: list) -> None:
        if task.state == GuiTaskState.CANCELLED:
            return
        task.completed_files = sum(
            1 for item in results if item.status not in {"failed", "cancelled"}
        )
        task.failed_files = sum(1 for item in results if item.status == "failed")
        task.output_paths = [
            str(item.local_file.path)
            for item in results
            if getattr(item, "local_file", None) is not None
        ]
        if any(item.status == "cancelled" for item in results):
            task.state = GuiTaskState.CANCELLED
            task.message = _msg("Download cancelled", "下载已取消")
        else:
            task.state = GuiTaskState.PARTIAL if task.failed_files else GuiTaskState.COMPLETED
            task.message = (
                _msg(
                    f"Completed · {task.completed_files}/{task.total_files} file(s)",
                    f"已完成 · {task.completed_files}/{task.total_files} 个文件",
                )
                if not task.failed_files
                else _failure_summary(task, results)
            )
        if task.total_bytes is not None and task.state == GuiTaskState.COMPLETED:
            task.downloaded_bytes = task.total_bytes
        self._emit(task)

    def fail(self, task: GuiTask, message: str) -> None:
        task.state = GuiTaskState.FAILED
        task.message = message
        self._emit(task)

    def cancel(self, task: GuiTask) -> None:
        token = task.cancellation_token
        cancel = getattr(token, "cancel", None)
        if callable(cancel):
            cancel()
        was_planning = task.state == GuiTaskState.PLANNING
        task.state = GuiTaskState.CANCELLED
        if was_planning:
            task.message = _msg(
                "Planning cancelled; any in-flight network discovery result will be discarded.",
                "已取消下载计划；正在进行的网络探测返回后会被直接丢弃。",
            )
        else:
            task.message = _msg(
                "Cancellation requested; active transfers will stop at the next safe check.",
                "已请求取消；当前传输将在下一个安全检查点停止。",
            )
        self._emit(task)

    def pause(self, task: GuiTask) -> None:
        if task.state in {GuiTaskState.COMPLETED, GuiTaskState.FAILED, GuiTaskState.CANCELLED}:
            return
        task.message = _msg(
            "Pause is not supported by the download core yet; use Cancel to stop safely.",
            "下载内核暂不支持暂停；请使用取消安全停止任务。",
        )
        self._emit(task)

    def retry_failed(self, task: GuiTask) -> None:
        if task.state not in {GuiTaskState.FAILED, GuiTaskState.PARTIAL, GuiTaskState.CANCELLED}:
            return
        task.failed_files = 0
        task.completed_files = 0
        task.failed_keys.clear()
        task.completed_keys.clear()
        task.file_progress.clear()
        task.downloaded_bytes = 0
        task.state = GuiTaskState.READY if task.plan else GuiTaskState.PENDING
        task.message = _msg("Ready to retry.", "已准备重试。")
        self._emit(task)

    def _emit(self, task: GuiTask) -> None:
        for callback in self._subscribers:
            callback(task)


def _file_count_message(task: GuiTask) -> str:
    return _msg(
        f"{task.completed_files}/{task.total_files} file(s) completed",
        f"已完成 {task.completed_files}/{task.total_files} 个文件",
    )


def _progress_message(task: GuiTask) -> str:
    file_text = task.current_file or "current file"
    if task.downloaded_bytes is None:
        return _msg(f"Downloading {file_text}", f"正在下载 {file_text}")
    if task.total_bytes:
        percent = min(100, round(task.downloaded_bytes / max(task.total_bytes, 1) * 100))
        return _msg(
            f"{percent}% · {_human_bytes(task.downloaded_bytes)} / "
            f"{_human_bytes(task.total_bytes)} · {file_text}",
            f"{percent}% · {_human_bytes(task.downloaded_bytes)} / "
            f"{_human_bytes(task.total_bytes)} · {file_text}",
        )
    return _msg(
        f"{_human_bytes(task.downloaded_bytes)} downloaded · {file_text}",
        f"已下载 {_human_bytes(task.downloaded_bytes)} · {file_text}",
    )



def _failure_summary(task: GuiTask, results: list) -> str:
    errors = [str(getattr(item, "error", "") or "").strip() for item in results]
    errors = [item for item in errors if item]
    base = _msg(
        f"Partial · {task.completed_files} succeeded, {task.failed_files} failed",
        f"部分完成 · {task.completed_files} 成功，{task.failed_files} 失败",
    )
    if not errors:
        return base
    first = errors[0].replace("\n", " ")
    if len(first) > 260:
        first = first[:257] + "..."
    return f"{base} · {first}"

def _msg(english: str, chinese: str) -> str:
    return chinese if language_manager.language == "zh" else english


def _human_bytes(value: int | None) -> str:
    if value is None:
        return "—"
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"
