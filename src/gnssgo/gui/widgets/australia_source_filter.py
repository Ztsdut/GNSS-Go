from __future__ import annotations

from gnssgo.gui.i18n import tr

from gnssgo.gui.qt import require_qt
from gnssgo.regional_sources import default_regional_source_registry
from gnssgo.stations import StationCatalog

QtCore, _QtGui, QtWidgets = require_qt()


class AustraliaSourceFilter(QtWidgets.QWidget):
    changed = QtCore.Signal()

    def __init__(self, parent=None, *, catalog: StationCatalog | None = None) -> None:
        super().__init__(parent)
        self.registry = default_regional_source_registry()
        self.catalog = catalog or StationCatalog()
        self.checks: dict[str, QtWidgets.QCheckBox] = {}
        self._syncing_select_all = False
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        title = QtWidgets.QLabel(tr("Australia Sources"))
        title.setObjectName("SectionTitle")
        layout.addWidget(title)
        top = QtWidgets.QHBoxLayout()
        self.summary = QtWidgets.QLabel("")
        self.summary.setObjectName("mapPanelSummary")
        self.select_all = QtWidgets.QCheckBox(tr("Select All"))
        self.select_all.setTristate(True)
        self.select_all.setChecked(True)
        self.select_all.stateChanged.connect(self._select_all_changed)
        top.addWidget(self.summary)
        top.addStretch(1)
        top.addWidget(self.select_all)
        layout.addLayout(top)
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText(tr("Search..."))
        self.search.textChanged.connect(self._apply_search)
        layout.addWidget(self.search)
        for source in self.registry.all("australia"):
            count = len(self.catalog.search(regional_sources=[source.id]))
            check = QtWidgets.QCheckBox(f"{source.name}    {count}")
            check.setChecked(True)
            check.stateChanged.connect(self._selection_changed)
            self.checks[source.id] = check
            layout.addWidget(check)
        layout.addStretch(1)
        self._update_summary()

    def selected_ids(self) -> list[str]:
        return [source_id for source_id, check in self.checks.items() if check.isChecked()]

    def set_australia_enabled(self, enabled: bool) -> None:
        self.setVisible(enabled)

    def _select_all_changed(self, state: int) -> None:
        if self._syncing_select_all:
            return
        checked = state != QtCore.Qt.Unchecked
        for check in self.checks.values():
            check.blockSignals(True)
            check.setChecked(checked)
            check.blockSignals(False)
        self._selection_changed()

    def _selection_changed(self) -> None:
        self._update_summary()
        self.changed.emit()

    def _apply_search(self, value: str) -> None:
        needle = value.strip().lower()
        for source_id, check in self.checks.items():
            source = self.registry.get(source_id)
            check.setVisible(needle in source.name.lower() or needle in source.id)

    def _update_summary(self) -> None:
        sources = [self.registry.get(source_id).name for source_id in self.selected_ids()]
        if not sources:
            self.summary.setText(tr("No sources"))
        elif len(sources) == 1:
            self.summary.setText(sources[0])
        else:
            self.summary.setText(f"{sources[0]} (+{len(sources) - 1} others)")
        checked_count = sum(1 for check in self.checks.values() if check.isChecked())
        if checked_count == 0:
            state = QtCore.Qt.Unchecked
        elif checked_count == len(self.checks):
            state = QtCore.Qt.Checked
        else:
            state = QtCore.Qt.PartiallyChecked
        self._syncing_select_all = True
        self.select_all.setCheckState(state)
        self._syncing_select_all = False
