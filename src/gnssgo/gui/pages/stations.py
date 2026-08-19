from __future__ import annotations

from gnssgo.gui.i18n import tr
from gnssgo.gui.models.station_filter import StationFilterState
from gnssgo.gui.qt import require_qt
from gnssgo.gui.services.map_service import MapService, station_to_json
from gnssgo.gui.widgets.data_network_filter import DataNetworkFilter
from gnssgo.gui.widgets.map_view import MapView
from gnssgo.gui.widgets.station_table import StationTable

_QtCore, _QtGui, QtWidgets = require_qt()


class StationsPage(QtWidgets.QWidget):
    def __init__(self, core, _task_service, parent=None) -> None:
        super().__init__(parent)
        self.core = core
        self.map_service = MapService(catalog=self.core.client._station_catalog())
        self.filter_state = StationFilterState(data_networks=["igs"])
        self.stations = []
        self.selected: set[str] = set()
        self._visible_ids: set[str] = set()
        self._refresh_timer = _QtCore.QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(150)
        self._refresh_timer.timeout.connect(self.refresh_from_filter_state)

        root = QtWidgets.QHBoxLayout(self)
        main = QtWidgets.QVBoxLayout()
        root.addLayout(main, 1)
        root.addWidget(self._build_filter_panel())

        title = QtWidgets.QLabel(tr("Browse Stations"))
        title.setObjectName("PageTitle")
        subtitle = QtWidgets.QLabel(
            "Find, filter, and select stations without losing hidden selections."
        )
        subtitle.setObjectName("PageSubtitle")
        main.addWidget(title)
        main.addWidget(subtitle)

        self.map = MapView(self.map_service)
        self.map.set_title("Station Catalog Map")
        self.map.setMinimumHeight(360)
        self.map.bridge.stationToggled.connect(self._map_station_toggled)
        self.map.bridge.selectionCleared.connect(self._map_selection_cleared)
        self.map.bridge.bboxSelected.connect(self._bbox_from_map)
        self.map.bridge.radiusSelected.connect(self._radius_from_map)
        main.addWidget(self.map)

        search_card = QtWidgets.QFrame()
        search_card.setObjectName("CardWidget")
        filters = QtWidgets.QHBoxLayout(search_card)
        self.query = QtWidgets.QLineEdit()
        self.query.setPlaceholderText(tr("Search station"))
        self.country = QtWidgets.QLineEdit()
        self.country.setPlaceholderText(tr("Country"))
        self.network = QtWidgets.QLineEdit()
        self.network.setPlaceholderText(tr("Network"))
        self.provider = QtWidgets.QLineEdit()
        self.provider.setPlaceholderText(tr("Provider"))
        search = QtWidgets.QPushButton(tr("Search"))
        search.setObjectName("SecondaryButton")
        search.clicked.connect(self.refresh)
        for widget in (self.query, self.country, self.network, self.provider, search):
            filters.addWidget(widget)
        main.addWidget(search_card)

        spatial_card = QtWidgets.QFrame()
        spatial_card.setObjectName("CardWidget")
        spatial = QtWidgets.QHBoxLayout(spatial_card)
        self.west = QtWidgets.QDoubleSpinBox()
        self.south = QtWidgets.QDoubleSpinBox()
        self.east = QtWidgets.QDoubleSpinBox()
        self.north = QtWidgets.QDoubleSpinBox()
        for widget in (self.west, self.east):
            widget.setRange(-180, 180)
        for widget in (self.south, self.north):
            widget.setRange(-90, 90)
        self.west.setValue(-180)
        self.south.setValue(-90)
        self.east.setValue(180)
        self.north.setValue(90)
        self.radius_lat = QtWidgets.QDoubleSpinBox()
        self.radius_lat.setRange(-90, 90)
        self.radius_lon = QtWidgets.QDoubleSpinBox()
        self.radius_lon.setRange(-180, 180)
        self.radius_km = QtWidgets.QDoubleSpinBox()
        self.radius_km.setRange(0, 20000)
        for label, widget in [
            ("W", self.west),
            ("S", self.south),
            ("E", self.east),
            ("N", self.north),
            ("Lat", self.radius_lat),
            ("Lon", self.radius_lon),
            ("Km", self.radius_km),
        ]:
            spatial.addWidget(QtWidgets.QLabel(label))
            spatial.addWidget(widget)
        bbox = QtWidgets.QPushButton(tr("BBox"))
        bbox.setObjectName("SecondaryButton")
        bbox.clicked.connect(self.refresh_bbox)
        radius = QtWidgets.QPushButton(tr("Radius"))
        radius.setObjectName("SecondaryButton")
        radius.clicked.connect(self.refresh_radius)
        clear_spatial = QtWidgets.QPushButton(tr("Clear Spatial"))
        clear_spatial.setObjectName("SecondaryButton")
        clear_spatial.clicked.connect(self.clear_spatial_filter)
        spatial.addWidget(bbox)
        spatial.addWidget(radius)
        spatial.addWidget(clear_spatial)
        main.addWidget(spatial_card)

        self.table = StationTable()
        main.addWidget(self.table, 1)
        self.refresh()

    def _build_filter_panel(self) -> QtWidgets.QScrollArea:
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(270)
        scroll.setMaximumWidth(350)
        body = QtWidgets.QFrame()
        body.setObjectName("CardWidget")
        layout = QtWidgets.QVBoxLayout(body)
        layout.setContentsMargins(10, 10, 10, 10)
        title = QtWidgets.QLabel(tr("Data Network Filter"))
        title.setObjectName("SectionTitle")
        layout.addWidget(title)
        self.filter_status = QtWidgets.QLabel(tr("Visible: 0   Selected: 0"))
        self.filter_status.setObjectName("StatusBadge")
        layout.addWidget(self.filter_status)
        active_catalog = self.core.client._station_catalog()
        self.data_network_filter = DataNetworkFilter(catalog=active_catalog)
        self.data_network_filter.changed.connect(self.schedule_refresh_from_filters)
        layout.addWidget(self.data_network_filter)
        layout.addStretch(1)
        scroll.setWidget(body)
        return scroll

    def refresh(self) -> None:
        self.filter_state.query = self.query.text() or None
        self.filter_state.country = self.country.text() or None
        self.filter_state.network = [self.network.text()] if self.network.text() else None
        self.filter_state.provider = self.provider.text() or None
        self.refresh_from_filter_state()

    def schedule_refresh_from_filters(self) -> None:
        self.filter_state.data_networks = self.data_network_filter.selected_ids()
        self.filter_state.regional_sources = self.selected_regional_sources()
        self._refresh_timer.start()

    def refresh_from_filter_state(self) -> None:
        state = self.filter_state
        if state.radius:
            lat, lon, radius_km = state.radius
            stations = self.core.search_stations(
                query=state.query,
                country=state.country,
                network=state.network,
                data_networks=state.data_networks,
                regional_sources=state.regional_sources,
                provider=state.provider,
                center=(lat, lon),
                radius=radius_km,
            )
        else:
            stations = self.core.search_stations(
                query=state.query,
                country=state.country,
                network=state.network,
                data_networks=state.data_networks,
                regional_sources=state.regional_sources,
                provider=state.provider,
                bbox=state.bbox,
            )
        self._set_stations(stations)

    def _set_stations(self, stations) -> None:
        self.stations = stations
        self._visible_ids = {station.id for station in stations}
        model = self.table.set_stations(stations, self.selected)
        model.dataChanged.connect(self._table_selection_changed)
        self.map.set_stations(
            [
                station_to_json(station, self.filter_state.data_networks)
                for station in stations
            ]
        )
        self.map.set_selected(self.selected)
        self._update_filter_status()

    def _map_station_toggled(self, station_id: str, selected: bool) -> None:
        if selected:
            self.selected.add(station_id)
        else:
            self.selected.discard(station_id)
        model = self.table.model()
        if model is not None:
            model.selected = self.selected
            if model.rowCount():
                top_left = model.index(0, 0)
                bottom_right = model.index(model.rowCount() - 1, 0)
                model.dataChanged.emit(top_left, bottom_right, [_QtCore.Qt.CheckStateRole])
        self.map.set_selected(self.selected)
        self._update_filter_status()

    def _table_selection_changed(self, *_args) -> None:
        model = self.table.model()
        if model is not None:
            self.selected = set(model.selected)
            self.map.set_selected(self.selected)
            self._update_filter_status()

    def _map_selection_cleared(self) -> None:
        self.selected.clear()
        model = self.table.model()
        if model is not None:
            model.selected = self.selected
            if model.rowCount():
                top_left = model.index(0, 0)
                bottom_right = model.index(model.rowCount() - 1, 0)
                model.dataChanged.emit(top_left, bottom_right, [_QtCore.Qt.CheckStateRole])
        self._update_filter_status()

    def _bbox_from_map(self, west: float, south: float, east: float, north: float) -> None:
        self.west.setValue(west)
        self.south.setValue(south)
        self.east.setValue(east)
        self.north.setValue(north)
        self.filter_state.bbox = (west, south, east, north)
        self.filter_state.radius = None
        self.refresh_from_filter_state()

    def _radius_from_map(self, lat: float, lon: float, radius_km: float) -> None:
        self.radius_lat.setValue(lat)
        self.radius_lon.setValue(lon)
        self.radius_km.setValue(radius_km)
        self.filter_state.radius = (lat, lon, radius_km)
        self.filter_state.bbox = None
        self.refresh_from_filter_state()

    def refresh_bbox(self) -> None:
        self.filter_state.bbox = (
            self.west.value(),
            self.south.value(),
            self.east.value(),
            self.north.value(),
        )
        self.filter_state.radius = None
        self.refresh_from_filter_state()

    def refresh_radius(self) -> None:
        self.filter_state.radius = (
            self.radius_lat.value(),
            self.radius_lon.value(),
            self.radius_km.value(),
        )
        self.filter_state.bbox = None
        self.refresh_from_filter_state()

    def clear_spatial_filter(self) -> None:
        self.filter_state.bbox = None
        self.filter_state.radius = None
        self.refresh_from_filter_state()

    def selected_regional_sources(self) -> list[str] | None:
        return self.data_network_filter.selected_source_ids()

    def _update_filter_status(self) -> None:
        hidden = len(self.selected.difference(self._visible_ids))
        self.filter_status.setText(
            tr("Visible: {visible}   Selected: {selected}   Hidden selected: {hidden}").format(
                visible=len(self._visible_ids), selected=len(self.selected), hidden=hidden
            )
        )
