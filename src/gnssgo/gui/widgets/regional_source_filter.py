from __future__ import annotations

from gnssgo.data_networks import default_data_network_registry
from gnssgo.gui.i18n import language_manager, tr
from gnssgo.gui.qt import require_qt
from gnssgo.regional_sources import default_regional_source_registry
from gnssgo.stations import StationCatalog

QtCore, _QtGui, QtWidgets = require_qt()


def _compact_source_label(source_id: str, label: str) -> str:
    """Short UI labels for long Europe network names; backend identity is unchanged."""
    compact = {
        "europe_italy": "Italy · RING / EPOS",
        "europe_poland": "Poland · ASG-EUPOS",
        "europe_romania": "Romania · EPOS",
        "europe_uk": "UK · OS Net",
        "europe_sweden": "Sweden · SWEPOS",
        "europe_finland": "Finland · FinnRef / FINPOS",
        "europe_switzerland": "Switzerland · AGNES",
        "europe_bosnia": "Bosnia & Herzegovina · EPOS",
        "europe_north_macedonia": "North Macedonia · EPOS",
        "sirgas_argentina": "Argentina · RAMSAC",
        "sirgas_brazil": "Brazil · RBMC",
        "sirgas_chile": "Chile · CSN",
        "sirgas_mexico": "Mexico · INEGI RGNA",
        "sirgas_bolivia": "Bolivia · IGM",
        "sirgas_colombia": "Colombia · IGAC",
        "sirgas_ecuador": "Ecuador · IGM",
        "sirgas_peru": "Peru · IGN",
        "sirgas_uruguay": "Uruguay · IGM REGNA-ROU",
        "sirgas_costa_rica": "Costa Rica · IGN",
        "sirgas_panama": "Panama · IGNTG",
    }
    return compact.get(source_id, label)


_SIRGAS_SOURCE_ACCESS = {
    "sirgas_argentina": "PORTAL",
    "sirgas_brazil": "LIVE",
    "sirgas_chile": "LIVE",
    "sirgas_mexico": "SFTP/WEB",
    "sirgas_bolivia": "PORTAL",
    "sirgas_colombia": "PORTAL",
    "sirgas_ecuador": "PORTAL",
    "sirgas_peru": "PORTAL",
    "sirgas_uruguay": "LIVE",
    "sirgas_costa_rica": "PORTAL",
    "sirgas_panama": "PORTAL",
}


class RegionalSourceFilter(QtWidgets.QWidget):
    """Second-level source filter for regional networks that expose sub-sources.

    Australia exposes multiple GA source networks, Europe exposes logical
    station networks (EPN/RGP/GREF/redGAE), and Canada exposes NRCan CACS and
    UNB CHAIN.  Physical EPN file servers are intentionally not shown here.
    The widget remains
    hidden for regions that do not yet define source-level choices.
    """

    changed = QtCore.Signal()

    def __init__(self, parent=None, *, catalog: StationCatalog | None = None) -> None:
        super().__init__(parent)
        self.registry = default_regional_source_registry()
        self.network_registry = default_data_network_registry()
        self.catalog = catalog or StationCatalog()
        self.checks: dict[str, QtWidgets.QCheckBox] = {}
        self._selected_memory: dict[str, bool] = {}
        self._active_networks: list[str] = []
        self._syncing_select_all = False

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self.title = QtWidgets.QLabel(tr("Regional Sources"))
        self.title.setObjectName("SectionTitle")
        layout.addWidget(self.title)

        top = QtWidgets.QHBoxLayout()
        self.summary = QtWidgets.QLabel("")
        self.summary.setObjectName("mapPanelSummary")
        self.select_all = QtWidgets.QCheckBox(tr("Select All"))
        self.select_all.setTristate(True)
        self.select_all.stateChanged.connect(self._select_all_changed)
        top.addWidget(self.summary, 1)
        top.addWidget(self.select_all)
        layout.addLayout(top)

        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText(tr("Search..."))
        self.search.textChanged.connect(self._apply_search)
        layout.addWidget(self.search)

        self.items_widget = QtWidgets.QWidget()
        self.items_layout = QtWidgets.QVBoxLayout(self.items_widget)
        self.items_layout.setContentsMargins(0, 0, 0, 0)
        self.items_layout.setSpacing(3)
        layout.addWidget(self.items_widget)
        layout.addStretch(1)
        self.setVisible(False)
        language_manager.changed.connect(lambda _language: self._rebuild())

    def set_networks(self, network_ids: list[str]) -> None:
        regional = [
            network_id
            for network_id in network_ids
            if network_id != "igs" and self.registry.all(network_id)
        ]
        if regional == self._active_networks:
            self._refresh_counts()
            return
        for source_id, check in self.checks.items():
            self._selected_memory[source_id] = check.isChecked()
        self._active_networks = regional
        self._rebuild()

    def selected_ids(self) -> list[str] | None:
        if not self._active_networks:
            return None
        return [source_id for source_id, check in self.checks.items() if check.isChecked()]

    def _clear_items(self) -> None:
        while self.items_layout.count():
            item = self.items_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.checks.clear()

    def _rebuild(self) -> None:
        self._clear_items()
        sources = []
        for network_id in self._active_networks:
            sources.extend(self.registry.all(network_id))

        if not sources:
            self.setVisible(False)
            return
        self.setVisible(True)

        if len(self._active_networks) == 1:
            network = self.network_registry.get(self._active_networks[0])
            if network.id == "europe":
                self.title.setText(tr("European Networks"))
            elif network.id == "sirgas":
                self.title.setText(tr("SIRGAS / Latin America Networks"))
            else:
                self.title.setText(tr(f"{network.name} Sources"))
        else:
            self.title.setText(tr("Regional Sources"))

        multiple_networks = len(self._active_networks) > 1
        source_ids = [source.id for source in sources]
        counts = self.catalog.regional_source_counts(source_ids)
        mapped_counts = self.catalog.regional_source_mappable_counts(source_ids)
        for source in sources:
            label = _compact_source_label(source.id, source.name)
            if multiple_networks:
                label = f"{self.network_registry.get(source.data_network).name} · {source.name}"
            count_text, tooltip = self._source_count_state(source.id, count=counts.get(source.id, 0))
            if source.data_network == "sirgas":
                mapped = mapped_counts.get(source.id, 0)
                access = _SIRGAS_SOURCE_ACCESS.get(source.id, "PORTAL")
                count_text = f"{count_text} · MAP {mapped} · {access}"
                tooltip = f"{tooltip}  Mappable stations: {mapped}. Access: {access}."
            check = QtWidgets.QCheckBox(f"{label}    {count_text}")
            check.setProperty("sourceName", label)
            check.setToolTip(tooltip)
            check.setChecked(self._selected_memory.get(source.id, True))
            check.stateChanged.connect(self._selection_changed)
            self.checks[source.id] = check
            self.items_layout.addWidget(check)
        self._apply_search(self.search.text())
        self._update_summary()

    def _refresh_counts(self) -> None:
        if not self.checks:
            return
        multiple_networks = len(self._active_networks) > 1
        source_ids = list(self.checks)
        counts = self.catalog.regional_source_counts(source_ids)
        mapped_counts = self.catalog.regional_source_mappable_counts(source_ids)
        for source_id, check in self.checks.items():
            source = self.registry.get(source_id)
            count_text, tooltip = self._source_count_state(source.id, count=counts.get(source.id, 0))
            if source.data_network == "sirgas":
                mapped = mapped_counts.get(source.id, 0)
                access = _SIRGAS_SOURCE_ACCESS.get(source.id, "PORTAL")
                count_text = f"{count_text} · MAP {mapped} · {access}"
                tooltip = f"{tooltip}  Mappable stations: {mapped}. Access: {access}."
            label = _compact_source_label(source.id, source.name)
            if multiple_networks:
                label = f"{self.network_registry.get(source.data_network).name} · {source.name}"
            check.setProperty("sourceName", label)
            check.setToolTip(tooltip)
            check.setText(f"{label}    {count_text}")

    def _source_count_state(self, source_id: str, *, count: int | None = None) -> tuple[str, str]:
        source = self.registry.get(source_id)
        if count is None:
            count = self.catalog.regional_source_counts([source.id]).get(source.id, 0)
        record = self.catalog.metadata_record(source.provider)
        if record is None:
            return (str(count) if count else "…", tr("Station catalog is loading or has not been loaded yet."))
        status = str(record.get("status") or "success").lower()
        error = str(record.get("error") or "").strip()
        if status == "success":
            return str(count), tr("Station catalog loaded.")
        if count:
            # Keep showing last-good cached stations while flagging refresh failure.
            text = f"{count} ⚠"
        else:
            text = "⚠"
        tooltip = error or tr("Station catalog refresh failed; retry will occur automatically.")
        return text, tooltip

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
        for source_id, check in self.checks.items():
            self._selected_memory[source_id] = check.isChecked()
        self._update_summary()
        self.changed.emit()

    def _apply_search(self, value: str) -> None:
        needle = value.strip().lower()
        for source_id, check in self.checks.items():
            source = self.registry.get(source_id)
            label = str(check.property("sourceName") or source.name)
            check.setVisible(needle in label.lower() or needle in source.id.lower())

    def _update_summary(self) -> None:
        selected = [
            self.registry.get(source_id).name
            for source_id, check in self.checks.items()
            if check.isChecked()
        ]
        if not selected:
            self.summary.setText(tr("No sources"))
        elif len(selected) == 1:
            self.summary.setText(selected[0])
        else:
            self.summary.setText(f"{selected[0]} (+{len(selected) - 1} others)")

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
