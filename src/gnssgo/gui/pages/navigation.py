from __future__ import annotations

from gnssgo.gui.i18n import tr
from gnssgo.gui.models.tasks import GuiTaskType
from gnssgo.gui.pages.base import CorePage
from gnssgo.gui.qt import require_qt
from gnssgo.gui.widgets.date_range import DateRangeWidget
from gnssgo.gui.widgets.provider_selector import ProviderSelector

_QtCore, _QtGui, QtWidgets = require_qt()


class NavigationPage(CorePage):
    def __init__(self, core, task_service, parent=None) -> None:
        super().__init__(core, task_service, parent)
        layout = QtWidgets.QVBoxLayout(self)
        title = QtWidgets.QLabel(tr("Download Navigation"))
        title.setObjectName("PageTitle")
        subtitle = QtWidgets.QLabel(tr("Review broadcast navigation files before download."))
        subtitle.setObjectName("PageSubtitle")
        layout.addWidget(title)
        layout.addWidget(subtitle)
        self.dates = DateRangeWidget()
        layout.addWidget(self.dates)
        form = QtWidgets.QFormLayout()
        self.nav_type = QtWidgets.QComboBox()
        self.nav_type.addItems(["mixed", "gps", "glonass", "galileo", "beidou"])
        self.provider = ProviderSelector(self.core.providers_for("navigation"))
        self.output = QtWidgets.QLineEdit()
        self.output.setPlaceholderText(tr("Default archive location"))
        output_row = QtWidgets.QWidget()
        output_layout = QtWidgets.QHBoxLayout(output_row)
        output_layout.setContentsMargins(0, 0, 0, 0)
        output_layout.setSpacing(8)
        output_layout.addWidget(self.output, 1)
        output_browse = QtWidgets.QPushButton(tr("Browse…"))
        output_browse.setObjectName("SecondaryButton")
        output_browse.setMinimumWidth(96)
        output_browse.clicked.connect(self._browse_output)
        output_layout.addWidget(output_browse)
        form.addRow("Navigation type", self.nav_type)
        form.addRow("Provider", self.provider)
        form.addRow("Output", output_row)
        layout.addLayout(form)
        dry = QtWidgets.QPushButton(tr("Review Plan"))
        dry.setObjectName("PrimaryButton")
        dry.clicked.connect(self.submit)
        layout.addWidget(dry)
        layout.addStretch(1)

    def _browse_output(self) -> None:
        directory = QtWidgets.QFileDialog.getExistingDirectory(self, tr("Output directory"))
        if directory:
            self.output.setText(directory)

    def submit(self) -> None:
        start, end = self.dates.values()
        request = {
            "start": start,
            "end": end,
            "nav_type": self.nav_type.currentText(),
            "provider": self.provider.value(),
            "output": self.output.text() or None,
        }
        self.run_plan(
            name="Navigation download",
            task_type=GuiTaskType.NAV,
            request=request,
            planner=lambda: self.core.plan_navigation(**request),
        )
