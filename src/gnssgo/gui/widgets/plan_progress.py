from __future__ import annotations

from collections.abc import Callable

from gnssgo.gui.qt import require_qt
from gnssgo.plan import PlanEstimate, PlanProgress, PlanStage

QtCore, _QtGui, QtWidgets = require_qt()


class PlanProgressPanel(QtWidgets.QFrame):
    cancelRequested = QtCore.Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("PlanProgressPanel")
        self.setFrameShape(QtWidgets.QFrame.StyledPanel)
        self.setMinimumWidth(330)
        self.setMaximumWidth(430)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        top = QtWidgets.QHBoxLayout()
        self.title = QtWidgets.QLabel("Preparing download plan")
        self.title.setObjectName("SectionTitle")
        self.status = QtWidgets.QLabel("")
        self.status.setObjectName("StatusBadge")
        top.addWidget(self.title, 1)
        top.addWidget(self.status)
        layout.addLayout(top)

        self.summary = QtWidgets.QLabel("")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        self.steps = QtWidgets.QLabel("")
        self.steps.setWordWrap(True)
        layout.addWidget(self.steps)

        self.elapsed = QtWidgets.QLabel("Elapsed: 0 s")
        layout.addWidget(self.elapsed)

        row = QtWidgets.QHBoxLayout()
        row.addStretch(1)
        self.cancel = QtWidgets.QPushButton("Cancel")
        self.cancel.setObjectName("DangerButton")
        self.cancel.clicked.connect(self.cancelRequested.emit)
        row.addWidget(self.cancel)
        layout.addLayout(row)
        self._estimate: PlanEstimate | None = None
        self.update_estimate(None)

    def update_estimate(self, estimate: PlanEstimate | None) -> None:
        if estimate is None:
            if self._estimate is not None:
                estimate = self._estimate
            else:
                self.summary.setText("Estimating local task size...")
                return
        self._estimate = estimate
        if estimate is None:
            self.summary.setText("Estimating local task size...")
            return
        stations = _format_optional(estimate.station_count)
        days = _format_optional(estimate.day_count)
        candidates = _format_optional(estimate.candidate_file_count, prefix="~")
        warning = "\nLarge download plan. Planning may take longer." if estimate.large else ""
        self.summary.setText(
            f"Stations: {stations}\nDate range: {days} days\n"
            f"Estimated files: {candidates}{warning}"
        )

    def update_progress(self, progress: PlanProgress) -> None:
        if progress.estimate is not None:
            self.update_estimate(progress.estimate)
        self.title.setText(progress.message)
        if progress.completed is not None and progress.total is not None:
            self.status.setText(f"{progress.completed}/{progress.total}")
        else:
            self.status.setText(progress.stage.value)
        self.elapsed.setText(f"Elapsed: {round(progress.elapsed_seconds)} s")
        self.cancel.setEnabled(progress.cancelable)
        self.steps.setText(_stage_text(progress.stage, progress.completed, progress.total))


def compact_plan_status(progress: PlanProgress) -> str:
    label = progress.stage.value.replace("_", " ").title()
    if progress.completed is not None and progress.total:
        percent = round(progress.completed / progress.total * 100)
        return f"Planning | {label} | {percent}%"
    return f"Planning | {label}"


class PlanSummaryPopup(QtWidgets.QDialog):
    def __init__(
        self,
        *,
        title: str,
        plan,
        estimate: PlanEstimate | None,
        download_callback: Callable[[], None],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.plan = plan
        self._download_callback = download_callback
        self.setObjectName("PlanSummaryPopup")
        self.setWindowTitle("Download Plan")
        self.setModal(False)
        self.setWindowFlag(QtCore.Qt.Tool, True)
        self.resize(460, 420)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(9)

        header = QtWidgets.QHBoxLayout()
        heading = QtWidgets.QLabel("Download Plan")
        heading.setObjectName("SectionTitle")
        badge = QtWidgets.QLabel("Ready")
        badge.setObjectName("StatusBadge")
        header.addWidget(heading, 1)
        header.addWidget(badge)
        layout.addLayout(header)

        subtitle = QtWidgets.QLabel(title)
        subtitle.setObjectName("PageSubtitle")
        layout.addWidget(subtitle)

        summary = QtWidgets.QFormLayout()
        summary.setHorizontalSpacing(18)
        summary.setVerticalSpacing(7)
        station_count = (
            estimate.station_count
            if estimate and estimate.station_count is not None
            else len(getattr(plan, "matched_stations", []) or [])
        )
        day_count = estimate.day_count if estimate else None
        candidate_count = estimate.candidate_file_count if estimate else None
        rows = [
            ("Stations", _format_optional(station_count)),
            ("Date range", f"{day_count:,} days" if day_count is not None else "unknown"),
            ("Candidate files", _format_optional(candidate_count)),
            ("Available tasks", f"{len(plan.download_tasks):,}"),
            ("Unavailable", f"{len(plan.unavailable):,}"),
            ("Sources", _source_summary(plan)),
        ]
        for label, value in rows:
            summary.addRow(label, QtWidgets.QLabel(value))
        if plan.estimated_size is not None:
            summary.addRow("Estimated size", QtWidgets.QLabel(_format_size(plan.estimated_size)))
        layout.addLayout(summary)

        warnings = _warnings(plan)
        if warnings:
            warning_title = QtWidgets.QLabel("Warnings")
            warning_title.setObjectName("SectionTitle")
            layout.addWidget(warning_title)
            warning_box = QtWidgets.QPlainTextEdit("\n".join(warnings[:8]))
            warning_box.setReadOnly(True)
            warning_box.setMaximumHeight(90)
            layout.addWidget(warning_box)

        layout.addStretch(1)
        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch(1)
        close = QtWidgets.QPushButton("Close")
        close.setObjectName("SecondaryButton")
        close.clicked.connect(self.close)
        self.download = QtWidgets.QPushButton("Download")
        self.download.setObjectName("PrimaryButton")
        self.download.setEnabled(bool(plan.download_tasks))
        self.download.clicked.connect(self._download)
        buttons.addWidget(close)
        buttons.addWidget(self.download)
        layout.addLayout(buttons)

    def _download(self) -> None:
        self.close()
        self._download_callback()


def _stage_text(
    active_stage: PlanStage,
    completed: int | None,
    total: int | None,
) -> str:
    order = [
        PlanStage.RESOLVING_STATIONS,
        PlanStage.RESOLVING_SOURCES,
        PlanStage.CHECKING_AVAILABILITY,
        PlanStage.BUILDING_TASKS,
        PlanStage.FINALIZING,
    ]
    labels = {
        PlanStage.RESOLVING_STATIONS: "Resolving stations",
        PlanStage.RESOLVING_SOURCES: "Resolving data sources",
        PlanStage.CHECKING_AVAILABILITY: "Checking availability",
        PlanStage.BUILDING_TASKS: "Building download tasks",
        PlanStage.FINALIZING: "Finalizing",
    }
    active_index = order.index(active_stage) if active_stage in order else -1
    lines = []
    for index, stage in enumerate(order):
        marker = "[x]" if active_index > index else "[*]" if active_index == index else "[ ]"
        suffix = ""
        if stage == active_stage and completed is not None and total is not None:
            suffix = f" {completed} / {total}"
        lines.append(f"{marker} {labels[stage]}{suffix}")
    return "\n".join(lines)


def _format_optional(value: int | None, *, prefix: str = "") -> str:
    if value is None:
        return "unknown"
    return f"{prefix}{value:,}"


def _source_summary(plan) -> str:
    if getattr(plan, "provider_stats", None):
        return " / ".join(
            f"{provider}: {count:,}"
            for provider, count in sorted(plan.provider_stats.items())
        )
    provider = getattr(plan, "provider_used", None)
    return provider or getattr(plan, "provider_requested", "auto")


def _warnings(plan) -> list[str]:
    failed = [
        attempt
        for attempt in getattr(plan, "attempted_providers", [])
        if str(getattr(attempt, "status", "")).startswith("failed")
    ]
    warnings: list[str] = []
    if failed:
        warnings.append(f"{len(failed)} provider or directory checks failed.")
    unavailable = len(getattr(plan, "unavailable", []) or [])
    if unavailable:
        warnings.append(f"{unavailable:,} selected station/date combinations are unavailable.")
    return warnings


def _format_size(size: int) -> str:
    value = float(size)
    units = ["B", "KB", "MB", "GB", "TB"]
    unit = units[0]
    for unit in units:
        if value < 1024 or unit == units[-1]:
            break
        value /= 1024
    return f"{value:.1f} {unit}"
