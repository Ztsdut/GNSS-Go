from __future__ import annotations

from gnssgo.gui.i18n import language_manager, tr
from gnssgo.gui.qt import require_qt

QtCore, _QtGui, QtWidgets = require_qt()


class StationTableModel(QtCore.QAbstractTableModel):
    headers = [
        "Selected",
        "Station",
        "Latitude",
        "Longitude",
        "Country",
        "Data Network",
        "Regional Source",
        "Network",
        "Providers",
    ]

    def __init__(self, stations: list | None = None, selected: set[str] | None = None) -> None:
        super().__init__()
        self.stations = stations or []
        self.selected = selected if selected is not None else set()
        language_manager.changed.connect(self._language_changed)

    def rowCount(self, parent=QtCore.QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.stations)

    def columnCount(self, parent=QtCore.QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.headers)

    def data(self, index, role=QtCore.Qt.DisplayRole):
        if not index.isValid() or role not in {QtCore.Qt.DisplayRole, QtCore.Qt.CheckStateRole}:
            return None
        station = self.stations[index.row()]
        if index.column() == 0:
            if role == QtCore.Qt.CheckStateRole:
                return QtCore.Qt.Checked if station.id in self.selected else QtCore.Qt.Unchecked
            return ""
        if role != QtCore.Qt.DisplayRole:
            return None
        values = [
            "",
            station.id,
            f"{station.latitude:.4f}" if station.latitude is not None else "",
            f"{station.longitude:.4f}" if station.longitude is not None else "",
            station.country or "",
            ",".join(station.data_networks),
            ",".join(station.regional_sources),
            ",".join(station.network),
            ",".join(station.providers),
        ]
        return values[index.column()]

    def headerData(self, section, orientation, role=QtCore.Qt.DisplayRole):
        if orientation == QtCore.Qt.Horizontal and role == QtCore.Qt.DisplayRole:
            return tr(self.headers[section])
        return None

    def _language_changed(self, _language: str) -> None:
        if self.headers:
            self.headerDataChanged.emit(QtCore.Qt.Horizontal, 0, len(self.headers) - 1)

    def flags(self, index):
        flags = super().flags(index)
        if index.isValid() and index.column() == 0:
            flags |= QtCore.Qt.ItemIsUserCheckable
        return flags

    def setData(self, index, value, role=QtCore.Qt.CheckStateRole):
        if index.isValid() and index.column() == 0 and role == QtCore.Qt.CheckStateRole:
            station = self.stations[index.row()]
            if value == QtCore.Qt.Checked:
                self.selected.add(station.id)
            else:
                self.selected.discard(station.id)
            self.dataChanged.emit(index, index, [role])
            return True
        return False


class StationTable(QtWidgets.QTableView):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setSortingEnabled(True)
        self.horizontalHeader().setStretchLastSection(True)

    def set_stations(self, stations: list, selected: set[str] | None = None) -> StationTableModel:
        model = StationTableModel(stations, selected)
        self.setModel(model)
        return model
