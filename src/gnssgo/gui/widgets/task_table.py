from __future__ import annotations

from gnssgo.gui.i18n import language_manager, tr
from gnssgo.gui.qt import require_qt

QtCore, QtGui, QtWidgets = require_qt()


class TaskTableModel(QtCore.QAbstractTableModel):
    """User-facing download queue.

    The progress column represents the *whole download plan*, not merely the
    currently transferring file.  The current-file column can still show the
    active file's own percentage when it is known.
    """

    headers = ["Name", "Plan progress", "Status", "Current file / message"]

    def __init__(self, tasks: list | None = None) -> None:
        super().__init__()
        # Keep the TaskService list by reference even when it is initially empty.
        # ``tasks or []`` silently broke the queue because an empty source list is
        # falsy and a new list was created instead.
        self.tasks = tasks if tasks is not None else []
        self._last_row_count = len(self.tasks)

    def rowCount(self, parent=QtCore.QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.tasks)

    def columnCount(self, parent=QtCore.QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.headers)

    def data(self, index, role=QtCore.Qt.DisplayRole):
        if not index.isValid():
            return None
        task = self.tasks[index.row()]
        if role == QtCore.Qt.DisplayRole:
            values = [
                tr(task.name),
                _plan_progress_text(task),
                _state_label(task.state.value),
                _current_text(task),
            ]
            return values[index.column()]
        if role == QtCore.Qt.TextAlignmentRole and index.column() == 2:
            return int(QtCore.Qt.AlignCenter)
        if role == QtCore.Qt.ToolTipRole:
            return _tooltip_text(task)
        if role == QtCore.Qt.UserRole and index.column() == 1:
            return _plan_progress_percent(task)
        return None

    def headerData(self, section, orientation, role=QtCore.Qt.DisplayRole):
        if orientation == QtCore.Qt.Horizontal and role == QtCore.Qt.DisplayRole:
            return tr(self.headers[section])
        return None

    def refresh(self) -> None:
        row_count = len(self.tasks)
        if row_count != self._last_row_count:
            # A layout change is enough here and preserves the shared task list.
            self._last_row_count = row_count
            self.layoutChanged.emit()
            return
        if not self.tasks:
            return
        top_left = self.index(0, 0)
        bottom_right = self.index(row_count - 1, len(self.headers) - 1)
        self.dataChanged.emit(top_left, bottom_right)


class ProgressBarDelegate(QtWidgets.QStyledItemDelegate):
    """Draw a plan-level progress bar directly in the queue."""

    def paint(self, painter, option, index) -> None:
        percent = index.data(QtCore.Qt.UserRole)
        if percent is None:
            super().paint(painter, option, index)
            return

        style_option = QtWidgets.QStyleOptionProgressBar()
        style_option.rect = option.rect.adjusted(8, 7, -8, -7)
        style_option.minimum = 0
        style_option.maximum = 100
        style_option.progress = int(percent)
        style_option.text = str(index.data(QtCore.Qt.DisplayRole) or "")
        style_option.textVisible = True
        style_option.textAlignment = QtCore.Qt.AlignCenter
        style = option.widget.style() if option.widget is not None else QtWidgets.QApplication.style()
        style.drawControl(QtWidgets.QStyle.CE_ProgressBar, style_option, painter, option.widget)

    def sizeHint(self, option, index):
        hint = super().sizeHint(option, index)
        hint.setHeight(max(hint.height(), 46))
        return hint


class TaskTable(QtWidgets.QTableView):
    def __init__(self, task_service, parent=None) -> None:
        super().__init__(parent)
        self.model_obj = TaskTableModel(task_service.tasks)
        self.setModel(self.model_obj)
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.setShowGrid(False)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(50)
        self.setItemDelegateForColumn(1, ProgressBarDelegate(self))

        header = self.horizontalHeader()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        # Give the plan progress and current-file columns the available room.
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QtWidgets.QHeaderView.Stretch)
        header.setMinimumSectionSize(100)

        task_service.subscribe(lambda _task: self.model_obj.refresh())
        language_manager.changed.connect(lambda _language: self.model_obj.refresh())


def _plan_progress_percent(task) -> int | None:
    """Return whole-plan progress in file-equivalent units.

    A completed/failed file contributes one full unit.  An in-flight file with a
    known byte total contributes its fractional unit.  This keeps the main bar
    intuitive for multi-file plans even when individual file sizes differ or the
    server did not publish a complete plan byte total.
    """
    if task.total_files:
        terminal_keys = set(task.completed_keys) | set(task.failed_keys)
        units = float(task.completed_files + task.failed_files)
        for key, (current, total) in task.file_progress.items():
            if key in terminal_keys:
                continue
            if total and total > 0:
                units += min(1.0, max(0.0, current / total))
        return min(100, max(0, round(units / max(task.total_files, 1) * 100)))
    if task.state.value == "completed":
        return 100
    if task.state.value in {"pending", "planning", "ready", "downloading", "processing"}:
        return 0
    return None


def _plan_progress_text(task) -> str:
    percent = _plan_progress_percent(task)
    if task.total_files:
        done = task.completed_files + task.failed_files
        if percent is not None:
            return f"{percent}% · {done}/{task.total_files} {tr('Files')}"
        return f"{done}/{task.total_files} {tr('Files')}"
    if percent is not None:
        return f"{percent}%"
    return tr("Waiting")


def _current_text(task) -> str:
    if task.current_file:
        current, total = task.file_progress.get(task.current_file, (0, None))
        if total:
            percent = min(100, round(current / max(total, 1) * 100))
            return f"{task.current_file} · {percent}%"
        return task.current_file
    return task.message or ""


def _tooltip_text(task) -> str:
    parts = [tr(task.name), _state_label(task.state.value), _plan_progress_text(task)]
    if task.total_bytes and task.downloaded_bytes is not None:
        parts.append(
            f"{_human_bytes(task.downloaded_bytes)} / {_human_bytes(task.total_bytes)}"
        )
    if task.current_file:
        parts.append(_current_text(task))
    if task.message and task.message != task.current_file:
        parts.append(task.message)
    return "\n".join(part for part in parts if part)


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
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"
