from __future__ import annotations

from gnssgo.gui.i18n import language_manager, tr
from gnssgo.gui.qt import require_qt

_QtCore, _QtGui, QtWidgets = require_qt()


class DownloadPlanDialog(QtWidgets.QDialog):
    def __init__(self, plan, parent=None) -> None:
        super().__init__(parent)
        self.plan = plan
        self.setWindowTitle(tr("Download Plan"))
        self.resize(760, 480)
        layout = QtWidgets.QVBoxLayout(self)
        summary = QtWidgets.QFormLayout()
        summary.addRow(tr("Remote files"), QtWidgets.QLabel(str(len(plan.remote_files))))
        summary.addRow(tr("Existing"), QtWidgets.QLabel(str(len(plan.existing_files))))
        summary.addRow(tr("To download"), QtWidgets.QLabel(str(len(plan.download_tasks))))
        summary.addRow(tr("Unavailable"), QtWidgets.QLabel(str(len(plan.unavailable))))
        if plan.estimated_size is not None:
            summary.addRow(
                tr("Estimated size"),
                QtWidgets.QLabel(f"{plan.estimated_size / 1024 / 1024:.1f} MB"),
            )
        layout.addLayout(summary)

        self.table = QtWidgets.QTableWidget(min(len(plan.remote_files), 200), 4)
        self.table.setHorizontalHeaderLabels([tr("Provider"), tr("Type"), tr("Date"), tr("Filename")])
        self.table.horizontalHeader().setStretchLastSection(True)
        for row, remote in enumerate(plan.remote_files[:200]):
            self.table.setItem(row, 0, QtWidgets.QTableWidgetItem(remote.provider))
            self.table.setItem(row, 1, QtWidgets.QTableWidgetItem(remote.data_type))
            self.table.setItem(
                row,
                2,
                QtWidgets.QTableWidgetItem(remote.date.isoformat() if remote.date else ""),
            )
            self.table.setItem(row, 3, QtWidgets.QTableWidgetItem(remote.filename))
        layout.addWidget(self.table)

        if plan.unavailable:
            missing_text = (
                f"无数据的站点/日期组合（{len(plan.unavailable)}）："
                if language_manager.language == "zh"
                else f"Unavailable station/date combinations ({len(plan.unavailable)}):"
            )
            missing_label = QtWidgets.QLabel(missing_text)
            missing_label.setWordWrap(True)
            layout.addWidget(missing_label)
            missing_box = QtWidgets.QPlainTextEdit()
            missing_box.setReadOnly(True)
            missing_box.setMaximumHeight(120)
            visible = list(plan.unavailable[:100])
            text = "\n".join(visible)
            if len(plan.unavailable) > len(visible):
                remaining = len(plan.unavailable) - len(visible)
                text += (
                    f"\n… 另有 {remaining} 项"
                    if language_manager.language == "zh"
                    else f"\n… and {remaining} more"
                )
            missing_box.setPlainText(text)
            layout.addWidget(missing_box)

        buttons = QtWidgets.QDialogButtonBox()
        self.download_button = buttons.addButton(tr("Download"), QtWidgets.QDialogButtonBox.AcceptRole)
        self.download_button.setEnabled(bool(plan.download_tasks))
        if not plan.download_tasks:
            self.download_button.setToolTip(
                "没有需要下载的新文件。"
                if language_manager.language == "zh"
                else "No new files need to be downloaded."
            )
        buttons.addButton(tr("Close"), QtWidgets.QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
