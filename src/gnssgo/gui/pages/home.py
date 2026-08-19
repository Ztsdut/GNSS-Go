from __future__ import annotations

from gnssgo.gui.i18n import language_manager, tr

from pathlib import Path

from gnssgo.data_networks import default_data_network_registry
from gnssgo.gui.qt import require_qt
from gnssgo.version import __version__

QtCore, _QtGui, QtWidgets = require_qt()


class HomePage(QtWidgets.QWidget):
    openPage = QtCore.Signal(str)

    def __init__(self, core, task_service, parent=None) -> None:
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        title = QtWidgets.QLabel("GNSS Go")
        title.setObjectName("PageTitle")
        self.subtitle = QtWidgets.QLabel()
        self.subtitle.setObjectName("PageSubtitle")
        self.subtitle.setProperty("_i18n_dynamic", True)
        layout.addWidget(title)
        layout.addWidget(self.subtitle)

        summary = QtWidgets.QGridLayout()
        summary.setHorizontalSpacing(12)
        summary.setVerticalSpacing(12)
        cards = [
            (tr("Global Stations"), str(core.station_catalog_count())),
            (tr("Data Networks"), str(len(default_data_network_registry().all()))),
            (tr("Download Tasks"), str(len(task_service.tasks))),
            (tr("Storage"), str(Path(core.client.settings.archive.root))),
        ]
        for index, (label, value) in enumerate(cards):
            card = QtWidgets.QFrame()
            card.setObjectName("CardWidget")
            card_layout = QtWidgets.QVBoxLayout(card)
            heading = QtWidgets.QLabel(label)
            heading.setObjectName("SectionTitle")
            content = QtWidgets.QLabel(value)
            content.setObjectName("StatusBadge")
            card_layout.addWidget(heading)
            card_layout.addWidget(content)
            summary.addWidget(card, index // 2, index % 2)
        layout.addLayout(summary)

        quick = QtWidgets.QLabel(tr("Quick actions"))
        quick.setObjectName("SectionTitle")
        layout.addWidget(quick)
        actions = QtWidgets.QHBoxLayout()
        for label, page in [
            (tr("Open Observation Download"), "Observations"),
            (tr("Navigation download"), "Navigation"),
            (tr("Product download"), "Products"),
        ]:
            button = QtWidgets.QPushButton(label)
            button.setObjectName("PrimaryButton" if page == "Observations" else "SecondaryButton")
            button.clicked.connect(lambda _checked=False, target=page: self.openPage.emit(target))
            actions.addWidget(button)
        actions.addStretch(1)
        layout.addLayout(actions)
        layout.addStretch(1)

        language_manager.changed.connect(self._language_changed)
        self._language_changed(language_manager.language)

    def _language_changed(self, _language: str) -> None:
        self.subtitle.setText(
            f"Version {__version__} · {tr('One-stop GNSS data access and management')}"
        )
