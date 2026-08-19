from __future__ import annotations

from pathlib import Path

from gnssgo.gui.i18n import language_manager, tr
from gnssgo.gui.models.tasks import GuiTaskState
from gnssgo.gui.qt import require_qt
from gnssgo.gui.widgets.log_panel import LogPanel
from gnssgo.gui.widgets.task_table import TaskTable

QtCore, QtGui, QtWidgets = require_qt()


class TasksPage(QtWidgets.QWidget):
    """Compact download manager.

    The queue itself is the primary progress display. Technical activity is
    hidden by default and only records state/file transitions, not every byte
    progress event.
    """

    def __init__(self, core, task_service, parent=None) -> None:
        super().__init__(parent)
        self.core = core
        self.task_service = task_service
        self._last_terminal_state: dict[str, str] = {}
        self._activity_markers: dict[str, tuple] = {}

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)

        title = QtWidgets.QLabel(tr("Downloads"))
        title.setObjectName("PageTitle")
        subtitle = QtWidgets.QLabel(
            "Each queue row shows whole-plan progress; the current file is shown separately."
        )
        subtitle.setObjectName("PageSubtitle")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        self.notice = QtWidgets.QLabel("")
        self.notice.setObjectName("StatusBadge")
        self.notice.setProperty("_i18n_dynamic", True)
        self.notice.setWordWrap(True)
        self.notice.setVisible(False)
        layout.addWidget(self.notice)

        summary = QtWidgets.QHBoxLayout()
        self.active_summary = _summary_card("Active", "0")
        self.completed_summary = _summary_card("Completed", "0")
        self.failed_summary = _summary_card("Failed / Partial", "0")
        summary.addWidget(self.active_summary[0])
        summary.addWidget(self.completed_summary[0])
        summary.addWidget(self.failed_summary[0])
        layout.addLayout(summary)

        queue = QtWidgets.QFrame()
        queue.setObjectName("CardWidget")
        queue_layout = QtWidgets.QVBoxLayout(queue)
        queue_layout.setContentsMargins(10, 10, 10, 10)
        queue_layout.setSpacing(8)

        queue_title_row = QtWidgets.QHBoxLayout()
        queue_title = QtWidgets.QLabel(tr("Download queue"))
        queue_title.setObjectName("SectionTitle")
        queue_title_row.addWidget(queue_title)
        queue_title_row.addStretch(1)
        self.details_toggle = QtWidgets.QToolButton()
        self.details_toggle.setText(tr("Details"))
        self.details_toggle.setCheckable(True)
        self.details_toggle.setArrowType(QtCore.Qt.RightArrow)
        self.details_toggle.toggled.connect(self._toggle_details)
        queue_title_row.addWidget(self.details_toggle)
        queue_layout.addLayout(queue_title_row)

        self.table = TaskTable(task_service)
        queue_layout.addWidget(self.table, 1)

        action_row = QtWidgets.QHBoxLayout()
        self.selected_summary = QtWidgets.QLabel(tr("No download selected"))
        self.selected_summary.setObjectName("PageSubtitle")
        self.selected_summary.setProperty("_i18n_dynamic", True)
        self.selected_summary.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.selected_summary.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Preferred,
        )
        action_row.addWidget(self.selected_summary, 1)

        self.cancel = QtWidgets.QPushButton(tr("Cancel"))
        self.cancel.setObjectName("DangerButton")
        self.retry = QtWidgets.QPushButton(tr("Retry Failed"))
        self.retry.setObjectName("PrimaryButton")
        self.open_folder = QtWidgets.QPushButton(tr("Open folder"))
        self.open_folder.setObjectName("SecondaryButton")
        self.cancel.clicked.connect(self._cancel_selected)
        self.retry.clicked.connect(self._retry_selected)
        self.open_folder.clicked.connect(self._open_selected_folder)
        action_row.addWidget(self.cancel)
        action_row.addWidget(self.retry)
        action_row.addWidget(self.open_folder)
        queue_layout.addLayout(action_row)
        layout.addWidget(queue, 1)

        self.details_frame = QtWidgets.QFrame()
        self.details_frame.setObjectName("CardWidget")
        details_layout = QtWidgets.QVBoxLayout(self.details_frame)
        details_layout.setContentsMargins(10, 8, 10, 8)
        activity_title = QtWidgets.QLabel(tr("Activity log"))
        activity_title.setObjectName("SectionTitle")
        self.log = LogPanel()
        self.log.setMaximumHeight(150)
        details_layout.addWidget(activity_title)
        details_layout.addWidget(self.log)
        self.details_frame.setVisible(False)
        layout.addWidget(self.details_frame)

        self.table.selectionModel().currentRowChanged.connect(
            lambda *_args: self._refresh_selected_summary()
        )
        task_service.subscribe(self._task_updated)
        language_manager.changed.connect(lambda _language: self._language_changed())
        self._refresh_summary()
        self._refresh_selected_summary()

    def _selected_task(self):
        index = self.table.currentIndex()
        if not index.isValid():
            return None
        row = index.row()
        if row >= len(self.task_service.tasks):
            return None
        return self.task_service.tasks[row]

    def _cancel_selected(self) -> None:
        task = self._selected_task()
        if task:
            self.task_service.cancel(task)

    def _retry_selected(self) -> None:
        task = self._selected_task()
        if task:
            self.task_service.retry_failed(task)

    def _open_selected_folder(self) -> None:
        task = self._selected_task()
        if not task:
            return
        path = _task_output_directory(task, self.core)
        if path:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(path)))

    def _toggle_details(self, checked: bool) -> None:
        self.details_frame.setVisible(checked)
        self.details_toggle.setArrowType(QtCore.Qt.DownArrow if checked else QtCore.Qt.RightArrow)

    def _language_changed(self) -> None:
        self._refresh_selected_summary()
        self.details_toggle.setText(tr("Details"))
        if self.notice.isVisible():
            terminal = next((
                task for task in reversed(self.task_service.tasks)
                if task.state in {
                    GuiTaskState.COMPLETED, GuiTaskState.PARTIAL,
                    GuiTaskState.FAILED, GuiTaskState.CANCELLED,
                }
            ), None)
            if terminal is not None:
                self.notice.setText(_terminal_notice(terminal))

    def _task_updated(self, task) -> None:
        if self._should_log_activity(task):
            self.log.append_message(_activity_message(task))
        self._refresh_summary()
        self._refresh_selected_summary()
        if task.state in {
            GuiTaskState.COMPLETED,
            GuiTaskState.PARTIAL,
            GuiTaskState.FAILED,
            GuiTaskState.CANCELLED,
        }:
            marker = task.state.value
            if self._last_terminal_state.get(task.id) != marker:
                self._last_terminal_state[task.id] = marker
                self.notice.setText(_terminal_notice(task))
                self.notice.setVisible(True)
                window = self.window()
                if window is not None:
                    QtWidgets.QApplication.alert(window, 2500)

    def _should_log_activity(self, task) -> bool:
        """Log only meaningful transitions, not every progress percentage."""
        marker = (
            task.state.value,
            task.current_file,
            task.completed_files,
            task.failed_files,
        )
        previous = self._activity_markers.get(task.id)
        self._activity_markers[task.id] = marker
        if previous is None:
            return True
        previous_state, previous_file, previous_completed, previous_failed = previous
        if task.state.value != previous_state:
            return True
        if task.current_file != previous_file:
            return True
        if task.completed_files != previous_completed or task.failed_files != previous_failed:
            return True
        return False

    def _refresh_summary(self) -> None:
        active_states = {
            GuiTaskState.PENDING,
            GuiTaskState.PLANNING,
            GuiTaskState.READY,
            GuiTaskState.DOWNLOADING,
            GuiTaskState.PROCESSING,
        }
        active = sum(task.state in active_states for task in self.task_service.tasks)
        completed = sum(task.state == GuiTaskState.COMPLETED for task in self.task_service.tasks)
        failed = sum(
            task.state in {GuiTaskState.FAILED, GuiTaskState.PARTIAL}
            for task in self.task_service.tasks
        )
        self.active_summary[1].setText(str(active))
        self.completed_summary[1].setText(str(completed))
        self.failed_summary[1].setText(str(failed))

    def _refresh_selected_summary(self) -> None:
        task = self._selected_task()
        if not task:
            self.selected_summary.setText(tr("No download selected"))
            self.cancel.setEnabled(False)
            self.retry.setEnabled(False)
            self.open_folder.setEnabled(False)
            return

        progress = _task_progress_text(task)
        state = _state_label(task.state.value)
        output_dir = _task_output_directory(task, self.core)
        output_text = str(output_dir) if output_dir else "—"
        self.selected_summary.setText(
            f"{tr(task.name)} · {progress} · {state} · {tr('Output')}: {output_text}"
        )
        self.selected_summary.setToolTip(task.message or task.current_file or output_text)
        terminal = task.state in {
            GuiTaskState.COMPLETED,
            GuiTaskState.FAILED,
            GuiTaskState.CANCELLED,
        }
        self.cancel.setEnabled(not terminal)
        self.retry.setEnabled(
            task.state
            in {GuiTaskState.FAILED, GuiTaskState.PARTIAL, GuiTaskState.CANCELLED}
        )
        self.open_folder.setEnabled(bool(output_dir))


def _summary_card(title: str, value: str):
    frame = QtWidgets.QFrame()
    frame.setObjectName("CardWidget")
    layout = QtWidgets.QVBoxLayout(frame)
    label = QtWidgets.QLabel(title)
    label.setObjectName("SectionTitle")
    number = QtWidgets.QLabel(value)
    number.setObjectName("StatusBadge")
    layout.addWidget(label)
    layout.addWidget(number)
    return frame, number


def _activity_message(task) -> str:
    return f"{tr(task.name)}: {task.message or task.state.value}"


def _terminal_notice(task) -> str:
    name = tr(task.name)
    if language_manager.language == "zh":
        if task.state == GuiTaskState.COMPLETED:
            return f"✓ {name}完成 — {task.completed_files} 个文件已就绪。"
        if task.state == GuiTaskState.PARTIAL:
            return (
                f"⚠ {name}部分完成 — {task.completed_files} 成功，"
                f"{task.failed_files} 失败。"
            )
        if task.state == GuiTaskState.FAILED:
            return f"✕ {name}失败 — {task.message or '请展开详情查看。'}"
        return f"{name}已取消。"
    if task.state == GuiTaskState.COMPLETED:
        return f"✓ {name} completed — {task.completed_files} file(s) ready."
    if task.state == GuiTaskState.PARTIAL:
        return (
            f"⚠ {name} finished with errors — {task.completed_files} succeeded, "
            f"{task.failed_files} failed."
        )
    if task.state == GuiTaskState.FAILED:
        return f"✕ {name} failed — {task.message or 'expand Details for more information.'}"
    return f"{name} cancelled."


def _task_output_directory(task, core) -> Path | None:
    if task.output_paths:
        path = Path(task.output_paths[0])
        return path if path.is_dir() else path.parent
    request = task.request or {}
    output = request.get("output") or request.get("output_dir")
    if output:
        return Path(str(output))
    settings = getattr(core, "settings", None)
    data_root = getattr(settings, "data_root", None)
    return Path(str(data_root)) if data_root else None


def _task_progress_text(task) -> str:
    if task.total_files:
        terminal_keys = set(task.completed_keys) | set(task.failed_keys)
        units = float(task.completed_files + task.failed_files)
        for key, (current, total) in task.file_progress.items():
            if key in terminal_keys:
                continue
            if total and total > 0:
                units += min(1.0, max(0.0, current / total))
        percent = min(100, max(0, round(units / max(task.total_files, 1) * 100)))
        done = task.completed_files + task.failed_files
        return f"{percent}% · {done}/{task.total_files} {tr('Files')}"
    if task.state == GuiTaskState.COMPLETED:
        return "100%"
    return tr("Waiting")


def _state_label(value: str) -> str:
    label = {
        "pending": "Pending",
        "planning": "Planning",
        "ready": "Ready",
        "downloading": "Downloading",
        "processing": "Processing",
        "paused": "Paused",
        "completed": "Completed",
        "partial": "Partial",
        "failed": "Failed",
        "cancelled": "Cancelled",
    }.get(value, value)
    return tr(label)


def _human_bytes(value: int | None) -> str:
    if value is None:
        return "—"
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"
