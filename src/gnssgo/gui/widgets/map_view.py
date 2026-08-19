from __future__ import annotations

import json
import math
from pathlib import Path

from gnssgo.gui.i18n import language_manager, tr
from gnssgo.gui.qt import require_qt, require_webengine

QtCore, QtGui, QtWidgets = require_qt()
QtWebChannel, QtWebEngineCore, QtWebEngineWidgets = require_webengine()


class MapBridge(QtCore.QObject):
    bboxSelected = QtCore.Signal(float, float, float, float)
    radiusSelected = QtCore.Signal(float, float, float)
    stationToggled = QtCore.Signal(str, bool)
    selectionCleared = QtCore.Signal()
    mapReady = QtCore.Signal()

    @QtCore.Slot(float, float, float, float)
    def send_bbox(self, west: float, south: float, east: float, north: float) -> None:
        self.bboxSelected.emit(west, south, east, north)

    @QtCore.Slot(float, float, float)
    def send_radius(self, lat: float, lon: float, radius_km: float) -> None:
        self.radiusSelected.emit(lat, lon, radius_km)

    @QtCore.Slot(str, bool)
    def toggle_station(self, station_id: str, selected: bool) -> None:
        self.stationToggled.emit(station_id, selected)

    @QtCore.Slot()
    def clear_selection(self) -> None:
        self.selectionCleared.emit()

    @QtCore.Slot()
    def map_ready(self) -> None:
        self.mapReady.emit()


class NativeMapCanvas(QtWidgets.QWidget):
    _land_paths: list[list[list[tuple[float, float]]]] | None = None

    stationToggled = QtCore.Signal(str, bool)
    bboxSelected = QtCore.Signal(float, float, float, float)
    radiusSelected = QtCore.Signal(float, float, float)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.stations: list[dict] = []
        self.selected: set[str] = set()
        self._press_pos: QtCore.QPoint | None = None
        self._drag_pos: QtCore.QPoint | None = None
        self._pan_start: QtCore.QPoint | None = None
        self._pan_center: tuple[float, float] | None = None
        self._selecting = False
        self._interaction_mode = "select"
        self._radius_km = 500.0
        self._theme = "light"
        self._language = "en"
        self.center_lat = 0.0
        self.center_lon = 0.0
        self.zoom = 1.0
        self.setMinimumHeight(220)
        self.setMouseTracking(True)

    def set_stations(self, stations: list[dict]) -> None:
        self.stations = [
            _normalize_station_payload(station)
            for station in stations
            if station.get("lat") is not None and station.get("lon") is not None
        ]
        self.update()

    def set_selected(self, station_ids: set[str]) -> None:
        self.selected = set(station_ids)
        self.update()

    def clear_selection(self) -> None:
        self.selected.clear()
        self.update()

    def set_interaction_mode(self, mode: str) -> None:
        self._interaction_mode = mode
        if mode == "pan":
            cursor = QtCore.Qt.OpenHandCursor
        elif mode in {"rectangle", "radius"}:
            cursor = QtCore.Qt.CrossCursor
        else:
            cursor = QtCore.Qt.ArrowCursor
        self.setCursor(cursor)

    def set_radius_km(self, radius_km: float) -> None:
        self._radius_km = max(1.0, float(radius_km))

    def set_theme(self, theme: str) -> None:
        self._theme = "dark" if theme == "dark" else "light"
        self.update()

    def set_language(self, language: str) -> None:
        self._language = "zh" if str(language).lower().startswith("zh") else "en"
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        rect = self.rect().adjusted(8, 8, -8, -8)
        colors = self._colors()
        painter.fillRect(self.rect(), QtGui.QColor(colors["water"]))
        painter.setPen(QtGui.QPen(QtGui.QColor(colors["border"]), 1))
        painter.setBrush(QtGui.QColor(colors["water"]))
        painter.drawRoundedRect(rect, 6, 6)
        painter.save()
        painter.setClipRect(rect)
        self._draw_land(painter, rect)
        self._draw_grid(painter, rect)
        self._draw_stations(painter, rect)
        self._draw_selection_box(painter)
        painter.restore()
        self._draw_legend(painter, rect)
        painter.setPen(QtGui.QColor(colors["text"]))
        painter.drawText(
            rect.adjusted(10, 8, -10, -8),
            QtCore.Qt.AlignTop | QtCore.Qt.AlignLeft,
            f"{len(self.stations)} stations / {len(self.selected)} selected",
        )

    def mousePressEvent(self, event) -> None:
        if event.button() == QtCore.Qt.LeftButton and self._interaction_mode == "radius":
            point = event.position().toPoint()
            lat, lon = self._screen_to_geo(point)
            for station in self.stations:
                distance = _haversine_km(
                    lat,
                    lon,
                    float(station["lat"]),
                    float(station["lon"]),
                )
                if distance <= self._radius_km and station["id"] not in self.selected:
                    self.selected.add(station["id"])
                    self.stationToggled.emit(station["id"], True)
            self.radiusSelected.emit(lat, lon, self._radius_km)
            self.update()
            return
        if event.button() == QtCore.Qt.RightButton or (
            event.button() == QtCore.Qt.LeftButton and self._interaction_mode == "pan"
        ):
            self._pan_start = event.position().toPoint()
            self._pan_center = (self.center_lat, self.center_lon)
            self.setCursor(QtCore.Qt.ClosedHandCursor)
            return
        if event.button() != QtCore.Qt.LeftButton:
            return
        self._press_pos = event.position().toPoint()
        self._drag_pos = self._press_pos
        self._selecting = False

    def mouseMoveEvent(self, event) -> None:
        if self._pan_start is not None and self._pan_center is not None:
            point = event.position().toPoint()
            rect = self._map_rect()
            x_scale, y_scale = self._scales(rect)
            dx = point.x() - self._pan_start.x()
            dy = point.y() - self._pan_start.y()
            start_lat, start_lon = self._pan_center
            self.center_lon = _wrap_lon(start_lon - dx / x_scale)
            self.center_lat = _clamp(start_lat + dy / y_scale, -85.0, 85.0)
            self.update()
            return
        if self._press_pos is None:
            self._update_station_tooltip(event.position().toPoint())
            return
        self._drag_pos = event.position().toPoint()
        if (
            self._interaction_mode == "rectangle"
            and (self._drag_pos - self._press_pos).manhattanLength() > 8
        ):
            self._selecting = True
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() in {QtCore.Qt.RightButton, QtCore.Qt.LeftButton} and self._pan_start:
            self._pan_start = None
            self._pan_center = None
            self.setCursor(
                QtCore.Qt.OpenHandCursor
                if self._interaction_mode == "pan"
                else QtCore.Qt.ArrowCursor
            )
            return
        if event.button() != QtCore.Qt.LeftButton or self._press_pos is None:
            return
        release_pos = event.position().toPoint()
        if self._selecting and self._drag_pos is not None:
            selection_rect = QtCore.QRect(self._press_pos, release_pos).normalized()
            selected_ids = self._stations_in_screen_rect(selection_rect)
            for station_id in selected_ids:
                if station_id not in self.selected:
                    self.selected.add(station_id)
                    self.stationToggled.emit(station_id, True)
            west_lat, west_lon = self._screen_to_geo(selection_rect.bottomLeft())
            east_lat, east_lon = self._screen_to_geo(selection_rect.topRight())
            self.bboxSelected.emit(
                min(west_lon, east_lon),
                min(west_lat, east_lat),
                max(west_lon, east_lon),
                max(west_lat, east_lat),
            )
            self._press_pos = None
            self._drag_pos = None
            self._selecting = False
            self.update()
            return

        station = self._nearest_station(release_pos)
        self._press_pos = None
        self._drag_pos = None
        self._selecting = False
        if not station:
            self.update()
            return
        station_id = station["id"]
        selected = station_id not in self.selected
        if selected:
            self.selected.add(station_id)
        else:
            self.selected.discard(station_id)
        self.stationToggled.emit(station_id, selected)
        self.update()

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y()
        if delta == 0:
            return
        before = self._screen_to_geo(event.position().toPoint())
        factor = 1.25 if delta > 0 else 0.8
        self.zoom = _clamp(self.zoom * factor, 1.0, 24.0)
        after = self._screen_to_geo(event.position().toPoint())
        self.center_lat = _clamp(self.center_lat + before[0] - after[0], -85.0, 85.0)
        self.center_lon = _wrap_lon(self.center_lon + before[1] - after[1])
        self.update()

    def mouseDoubleClickEvent(self, _event) -> None:
        self.fit_world()

    def fit_world(self) -> None:
        self.center_lat = 0.0
        self.center_lon = 0.0
        self.zoom = 1.0
        self.update()

    def fit_stations(self) -> None:
        if not self.stations:
            self.fit_world()
            return
        lats = [float(station["lat"]) for station in self.stations]
        lons = [float(station["lon"]) for station in self.stations]
        self.center_lat = _clamp(sum(lats) / len(lats), -85.0, 85.0)
        self.center_lon = _wrap_lon(sum(lons) / len(lons))
        lat_span = max(lats) - min(lats)
        lon_span = max(lons) - min(lons)
        if len(self.stations) == 1:
            self.zoom = 8.0
        elif lon_span > 300:
            self.zoom = 1.0
        else:
            lon_zoom = 288.0 / max(lon_span, 1.0)
            lat_zoom = 144.0 / max(lat_span, 1.0)
            self.zoom = _clamp(min(lon_zoom, lat_zoom), 1.0, 24.0)
        self.update()

    def _draw_grid(self, painter: QtGui.QPainter, rect: QtCore.QRect) -> None:
        painter.setPen(QtGui.QPen(QtGui.QColor(self._colors()["grid"]), 1))
        for lon in range(-120, 181, 60):
            x, _y = self._project(0, lon, rect)
            painter.drawLine(x, rect.top(), x, rect.bottom())
        for lat in range(-60, 61, 30):
            _x, y = self._project(lat, 0, rect)
            painter.drawLine(rect.left(), y, rect.right(), y)

    def _draw_stations(self, painter: QtGui.QPainter, rect: QtCore.QRect) -> None:
        colors = self._colors()
        for station in self.stations:
            point = QtCore.QPointF(
                *self._project(float(station["lat"]), float(station["lon"]), rect)
            )
            station_id = station["id"]
            selected = station_id in self.selected
            marker_class = str(station.get("marker_class") or "other")
            fill = colors.get(marker_class, colors["other"])
            radius = 5 if selected else 3
            if selected:
                painter.setPen(QtGui.QPen(QtGui.QColor(colors["selected_ring"]), 2))
            else:
                painter.setPen(QtGui.QPen(QtGui.QColor(colors["marker_outline"]), 1))
            painter.setBrush(QtGui.QColor(fill))
            painter.drawEllipse(point, radius, radius)

    def _draw_legend(self, painter: QtGui.QPainter, rect: QtCore.QRect) -> None:
        colors = self._colors()
        labels = (
            ("igs_only", "IGS 全球站" if self._language == "zh" else "IGS global"),
            ("regional_only", "区域 CORS" if self._language == "zh" else "Regional CORS"),
        )
        metrics = painter.fontMetrics()
        text_width = max(metrics.horizontalAdvance(label) for _key, label in labels)
        selected_label = "已选择" if self._language == "zh" else "Selected"
        text_width = max(text_width, metrics.horizontalAdvance(selected_label))
        width = text_width + 42
        height = 3 * 22 + 10
        box = QtCore.QRect(rect.left() + 10, rect.bottom() - height - 10, width, height)
        painter.setPen(QtGui.QPen(QtGui.QColor(colors["border"]), 1))
        painter.setBrush(QtGui.QColor(colors["legend_bg"]))
        painter.drawRoundedRect(box, 6, 6)
        y = box.top() + 16
        for key, label in labels:
            center = QtCore.QPointF(box.left() + 15, y - 4)
            painter.setPen(QtGui.QPen(QtGui.QColor(colors["marker_outline"]), 1))
            painter.setBrush(QtGui.QColor(colors[key]))
            painter.drawEllipse(center, 4, 4)
            painter.setPen(QtGui.QColor(colors["text"]))
            painter.drawText(box.left() + 28, y, label)
            y += 22
        center = QtCore.QPointF(box.left() + 15, y - 4)
        painter.setPen(QtGui.QPen(QtGui.QColor(colors["selected_ring"]), 2))
        painter.setBrush(QtGui.QColor(colors["legend_selected_fill"]))
        painter.drawEllipse(center, 5, 5)
        painter.setPen(QtGui.QColor(colors["text"]))
        painter.drawText(box.left() + 28, y, selected_label)

    def _draw_selection_box(self, painter: QtGui.QPainter) -> None:
        if not self._selecting or self._press_pos is None or self._drag_pos is None:
            return
        selection_rect = QtCore.QRect(self._press_pos, self._drag_pos).normalized()
        painter.setPen(
            QtGui.QPen(QtGui.QColor(self._colors()["accent"]), 1, QtCore.Qt.DashLine)
        )
        painter.setBrush(QtGui.QColor(27, 111, 147, 36))
        painter.drawRect(selection_rect)

    def _draw_land(self, painter: QtGui.QPainter, rect: QtCore.QRect) -> None:
        """Draw the offline vector land without dateline fill artefacts.

        The native fallback wraps longitudes around the current map centre.  If
        every polygon vertex is wrapped independently, a ring crossing the
        antimeridian jumps from one side of the widget to the other and QPainter
        fills the huge chord between them.  That is the large green band that was
        visible around Australia.  Unwrap each ring continuously, draw adjacent
        world copies, and let the viewport clip the result.
        """
        colors = self._colors()
        painter.setPen(QtGui.QPen(QtGui.QColor(colors["land_border"]), 1))
        painter.setBrush(QtGui.QColor(colors["land"]))
        world_width = self._scales(rect)[0] * 360.0

        for polygon in self._land_geometry():
            for world_shift in (-world_width, 0.0, world_width):
                path = QtGui.QPainterPath()
                path.setFillRule(QtCore.Qt.OddEvenFill)
                started = False
                for ring in polygon:
                    # Polar-cap rings (notably Antarctica) legitimately span the
                    # whole -180..180 range.  They must keep that world span so
                    # their closing segment runs along the pole, not across the
                    # coastline near ~64°S.
                    lons = [float(point[0]) for point in ring] if ring else []
                    lats = [float(point[1]) for point in ring] if ring else []
                    preserve_world_span = bool(
                        lons
                        and lats
                        and min(lats) <= -89.0
                        and max(lons) - min(lons) >= 350.0
                    )
                    points = self._project_ring(
                        ring,
                        rect,
                        world_shift,
                        preserve_world_span=preserve_world_span,
                    )
                    if len(points) < 3:
                        continue
                    path.moveTo(points[0])
                    for point in points[1:]:
                        path.lineTo(point)
                    path.closeSubpath()
                    started = True
                if started:
                    painter.drawPath(path)

    def _project_ring(
        self,
        ring: list[list[float]] | list[tuple[float, float]],
        rect: QtCore.QRect,
        world_shift: float = 0.0,
        *,
        preserve_world_span: bool = False,
    ) -> list[QtCore.QPointF]:
        if not ring:
            return []
        x_scale, y_scale = self._scales(rect)
        points: list[QtCore.QPointF] = []
        previous_delta: float | None = None
        for lon, lat in ring:
            if preserve_world_span:
                # Keep -180 and +180 on opposite map edges.  This is required
                # for a pole-enclosing polygon such as Antarctica.
                delta = float(lon) - self.center_lon
            else:
                delta = _short_lon_delta(float(lon), self.center_lon)
                if previous_delta is not None:
                    while delta - previous_delta > 180.0:
                        delta -= 360.0
                    while delta - previous_delta < -180.0:
                        delta += 360.0
            previous_delta = delta
            x = rect.center().x() + delta * x_scale + world_shift
            y = rect.center().y() - (float(lat) - self.center_lat) * y_scale
            points.append(QtCore.QPointF(x, y))
        return points

    def _colors(self) -> dict[str, str]:
        if self._theme == "dark":
            return {
                "water": "#17222d",
                "land": "#2b3b45",
                "land_border": "#5c7181",
                "grid": "#344957",
                "border": "#405365",
                "text": "#d7e2ec",
                "igs_only": "#4FA3FF",
                "regional_only": "#FF9F43",
                "igs_regional": "#4FA3FF",
                "other": "#E67E22",
                "selected_ring": "#FF5C5C",
                "marker_outline": "#F4F7FA",
                "legend_bg": "#E61B2530",
                "legend_selected_fill": "#24313D",
                "accent": "#78bddb",
            }
        return {
            "water": "#e7f1f7",
            "land": "#d7e2d7",
            "land_border": "#96aab8",
            "grid": "#c8d7e2",
            "border": "#bfd0dc",
            "text": "#4f6272",
            "igs_only": "#2563EB",
            "regional_only": "#E67E22",
            "igs_regional": "#2563EB",
            "other": "#E67E22",
            "selected_ring": "#DC2626",
            "marker_outline": "#FFFFFF",
            "legend_bg": "#F2FFFFFF",
            "legend_selected_fill": "#FFFFFF",
            "accent": "#1b6f93",
        }

    def _nearest_station(self, point: QtCore.QPoint) -> dict | None:
        rect = self._map_rect()
        nearest = None
        nearest_distance = 14.0
        for station in self.stations:
            x, y = self._project(float(station["lat"]), float(station["lon"]), rect)
            distance = math.hypot(point.x() - x, point.y() - y)
            if distance < nearest_distance:
                nearest = station
                nearest_distance = distance
        return nearest

    def _update_station_tooltip(self, point: QtCore.QPoint) -> None:
        station = self._nearest_station(point)
        if not station:
            self.setToolTip("")
            return
        data_networks = ", ".join(station.get("data_networks") or [])
        regional_sources = ", ".join(station.get("regional_sources") or [])
        providers = ", ".join(station.get("providers") or [])
        lines = [
            str(station.get("id", "")),
            f"Lat/Lon: {station.get('lat')}, {station.get('lon')}",
        ]
        if data_networks:
            lines.append(f"Data Network: {data_networks}")
        if regional_sources:
            lines.append(f"Regional Source: {regional_sources}")
        if providers:
            lines.append(f"Available Sources: {providers}")
        self.setToolTip("\n".join(lines))

    def _stations_in_screen_rect(self, selection_rect: QtCore.QRect) -> list[str]:
        map_rect = self._map_rect()
        selected: list[str] = []
        for station in self.stations:
            x, y = self._project(float(station["lat"]), float(station["lon"]), map_rect)
            if selection_rect.contains(QtCore.QPoint(round(x), round(y))):
                selected.append(station["id"])
        return selected

    def _project(self, lat: float, lon: float, rect: QtCore.QRect) -> tuple[float, float]:
        x_scale, y_scale = self._scales(rect)
        lon_delta = _short_lon_delta(lon, self.center_lon)
        x = rect.center().x() + lon_delta * x_scale
        y = rect.center().y() - (lat - self.center_lat) * y_scale
        return x, y

    def _screen_to_geo(self, point: QtCore.QPoint) -> tuple[float, float]:
        rect = self._map_rect()
        x_scale, y_scale = self._scales(rect)
        lon = _wrap_lon(self.center_lon + (point.x() - rect.center().x()) / x_scale)
        lat = _clamp(self.center_lat - (point.y() - rect.center().y()) / y_scale, -90.0, 90.0)
        return lat, lon

    def _map_rect(self) -> QtCore.QRect:
        return self.rect().adjusted(8, 8, -8, -8)

    def _scales(self, rect: QtCore.QRect) -> tuple[float, float]:
        return rect.width() / 360.0 * self.zoom, rect.height() / 180.0 * self.zoom

    @classmethod
    def _land_geometry(cls) -> list[list[list[tuple[float, float]]]]:
        if cls._land_paths is not None:
            return cls._land_paths
        path = Path(__file__).resolve().parents[1] / "resources" / "map" / "world_land_110m.geojson"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except OSError:
            cls._land_paths = []
            return cls._land_paths
        polygons: list[list[list[tuple[float, float]]]] = []
        for feature in payload.get("features", []):
            geometry = feature.get("geometry", {})
            coordinates = geometry.get("coordinates", [])
            if geometry.get("type") == "Polygon":
                polygons.append(coordinates)
            elif geometry.get("type") == "MultiPolygon":
                polygons.extend(coordinates)
        cls._land_paths = polygons
        return cls._land_paths


class MapView(QtWidgets.QWidget):
    """Station map with a fail-safe native backend and an optional Leaflet backend.

    The native vector map is displayed immediately.  Leaflet is only promoted to
    the visible backend after its local assets and JavaScript map object have been
    validated.  This avoids the previous blank-map failure mode when QtWebEngine is
    installed but Chromium/local-resource startup fails on a particular Windows PC.
    """

    def __init__(self, map_service, parent=None) -> None:
        super().__init__(parent)
        self.bridge = MapBridge()
        self.map_service = map_service
        self._pending_stations: list[dict] = []
        self._pending_selected: set[str] = set()
        self._visible_count = 0
        self._web_ready = False
        self._web_validated = False
        self._web_error = ""
        self._theme = "light"
        self._language = language_manager.language

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        toolbar = QtWidgets.QFrame()
        toolbar.setObjectName("mapToolbar")
        toolbar.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        toolbar_layout = QtWidgets.QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(10, 8, 10, 8)
        toolbar_layout.setSpacing(6)
        self.title = QtWidgets.QLabel(tr("Stations Map"))
        self.title.setObjectName("mapToolbarTitle")
        toolbar_layout.addWidget(self.title)
        toolbar_layout.addStretch(1)

        self.mode_group = QtWidgets.QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.select_button = self._make_mode_button("Select", "select", checked=True)
        self.rectangle_button = self._make_mode_button("Rectangle", "rectangle")
        self.radius_button = self._make_mode_button("Radius", "radius")
        for button in (self.select_button, self.rectangle_button, self.radius_button):
            toolbar_layout.addWidget(button)

        self.radius_value = QtWidgets.QDoubleSpinBox()
        self.radius_value.setObjectName("mapRadiusValue")
        self.radius_value.setRange(1.0, 20000.0)
        self.radius_value.setDecimals(0)
        self.radius_value.setValue(500.0)
        self.radius_value.setSuffix(" km")
        # The native spin buttons consume a surprisingly large amount of width on
        # Windows/high-DPI displays and can clip values such as ``500 km``.  The
        # radius is primarily typed or changed with the mouse wheel, so keep this
        # toolbar field compact and button-free.
        self.radius_value.setButtonSymbols(
            QtWidgets.QAbstractSpinBox.ButtonSymbols.NoButtons
        )
        self.radius_value.setAlignment(QtCore.Qt.AlignCenter)
        self.radius_value.setFixedWidth(118)
        self.radius_value.setToolTip("Radius used by the Radius map tool")
        self.radius_value.valueChanged.connect(self._radius_changed)
        self.radius_value.setVisible(False)
        toolbar_layout.addWidget(self.radius_value)

        self.station_mode = QtWidgets.QComboBox()
        self.station_mode.addItem("Individual stations", "individual")
        self.station_mode.addItem("Clusters", "cluster")
        self.station_mode.setMinimumWidth(150)
        self.station_mode.setToolTip(
            "Show every station individually, or aggregate markers into clusters"
        )
        self.station_mode.currentIndexChanged.connect(self._station_mode_changed)
        toolbar_layout.addWidget(self.station_mode)

        self.basemap = QtWidgets.QComboBox()
        # OpenStreetMap is the online default.  Startup probes the real OSM tile
        # endpoint first; only an unavailable/blocked network falls back to the
        # bundled offline basemap.
        self.basemap.addItem("OpenStreetMap", "osm")
        self.basemap.addItem("Offline", "offline")
        self.basemap.setMinimumWidth(132)
        self.basemap.setToolTip(tr("OpenStreetMap is used by default when reachable; Offline is the automatic fallback"))
        app = QtWidgets.QApplication.instance()
        prefer_osm = bool(app.property("osmAvailable")) if app is not None else False
        desired = "osm" if prefer_osm else "offline"
        startup_index = self.basemap.findData(desired)
        if startup_index >= 0:
            # Set the startup choice before connecting the change signal.  The
            # Leaflet backend is created immediately afterwards and reads this
            # exact value on its first render.
            self.basemap.setCurrentIndex(startup_index)
        self.basemap.currentIndexChanged.connect(self._basemap_changed)
        toolbar_layout.addWidget(self.basemap)

        self.fit_button = QtWidgets.QToolButton()
        self.fit_button.setText(tr("Fit"))
        self.fit_button.setToolTip("Fit all visible stations")
        self.fit_button.clicked.connect(self.fit_stations)
        toolbar_layout.addWidget(self.fit_button)
        layout.addWidget(toolbar)

        self.map_stack = QtWidgets.QStackedWidget()
        self.map_stack.setObjectName("mapStack")
        self.map_stack.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Expanding,
        )
        self.native = NativeMapCanvas()
        self.native.set_language(self._language)
        self.native.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self.native.stationToggled.connect(self.bridge.stationToggled.emit)
        self.native.bboxSelected.connect(self.bridge.bboxSelected.emit)
        self.native.radiusSelected.connect(self.bridge.radiusSelected.emit)
        self.map_stack.addWidget(self.native)
        self.map_stack.setCurrentWidget(self.native)
        layout.addWidget(self.map_stack, 1)

        self.web_view = None
        self.web_channel = None
        self._setup_web_backend()

        status = QtWidgets.QFrame()
        status.setObjectName("mapStatusStrip")
        status.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        status_layout = QtWidgets.QHBoxLayout(status)
        status_layout.setContentsMargins(10, 6, 10, 6)
        self.count_label = QtWidgets.QLabel(tr("Visible: 0   Selected: 0   Hidden selected: 0"))
        status_layout.addWidget(self.count_label)
        self.clear_button = QtWidgets.QToolButton()
        self.clear_button.setText(tr("Clear selection"))
        self.clear_button.setToolTip("Clear selected stations and selection shapes")
        self.clear_button.clicked.connect(self._clear_selection_requested)
        status_layout.addWidget(self.clear_button)
        backend_text = "Map: Native offline"
        if self._web_error:
            backend_text += f" ({self._web_error})"
        self.backend_label = QtWidgets.QLabel(backend_text)
        self.backend_label.setObjectName("mapHintLabel")
        status_layout.addStretch(1)
        status_layout.addWidget(self.backend_label)
        self.hint_label = QtWidgets.QLabel(
            "Click stations to select. Rectangle and Radius are explicit tools."
        )
        self.hint_label.setObjectName("mapHintLabel")
        status_layout.addWidget(self.hint_label)
        layout.addWidget(status)

        self.bridge.mapReady.connect(self._bridge_map_ready)
        language_manager.changed.connect(self._language_changed)
        self.set_theme(self._application_theme())
        self._mode_changed(True)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        width = self.width()
        compact = width < 900
        very_compact = width < 760

        # Keep map controls usable instead of allowing long labels/status text to
        # cover neighbouring widgets when the main window is narrowed.
        self.title.setVisible(not compact)
        self.station_mode.setMinimumWidth(118 if compact else 150)
        self.basemap.setMinimumWidth(108 if compact else 132)
        self.hint_label.setVisible(not compact)
        self.backend_label.setVisible(not very_compact)

    def _make_mode_button(self, text: str, mode: str, *, checked: bool = False):
        button = QtWidgets.QToolButton()
        button.setText(text)
        button.setCheckable(True)
        button.setChecked(checked)
        button.setProperty("mapMode", mode)
        button.toggled.connect(self._mode_changed)
        self.mode_group.addButton(button)
        return button

    def _setup_web_backend(self) -> None:
        if QtWebChannel is None or QtWebEngineWidgets is None:
            self._web_error = "QtWebEngine is unavailable"
            return
        index_path = _map_resource_path("index.html")
        required = [
            index_path,
            _map_resource_path("map.js"),
            _map_resource_path("map.css"),
            _map_resource_path("vendor/leaflet.js"),
            _map_resource_path("vendor/leaflet.css"),
            _map_resource_path("vendor/leaflet.markercluster.js"),
        ]
        missing = [path.name for path in required if not path.exists()]
        if missing:
            self._web_error = f"Missing map resources: {', '.join(missing)}"
            return

        try:
            self.web_view = QtWebEngineWidgets.QWebEngineView()
            self.web_view.setContextMenuPolicy(QtCore.Qt.NoContextMenu)
            if QtWebEngineCore is not None:
                web_settings = self.web_view.settings()
                attrs = QtWebEngineCore.QWebEngineSettings.WebAttribute
                web_settings.setAttribute(attrs.JavascriptEnabled, True)
                web_settings.setAttribute(attrs.LocalContentCanAccessFileUrls, True)
                web_settings.setAttribute(attrs.LocalContentCanAccessRemoteUrls, True)
            self.web_channel = QtWebChannel.QWebChannel(self.web_view.page())
            self.web_channel.registerObject("bridge", self.bridge)
            self.web_view.page().setWebChannel(self.web_channel)
            self.web_view.loadFinished.connect(self._leaflet_load_finished)
            self.map_stack.addWidget(self.web_view)
            self.web_view.load(QtCore.QUrl.fromLocalFile(str(index_path.resolve())))
            # Native remains visible until Leaflet is positively validated.
            QtCore.QTimer.singleShot(6500, self._fallback_if_leaflet_not_ready)
        except Exception as exc:  # pragma: no cover - machine-specific WebEngine failures.
            self._web_error = str(exc)
            self._activate_native_fallback()

    def set_stations(self, stations: list[dict] | None = None) -> None:
        raw = self.map_service.stations_json() if stations is None else stations
        normalized = [
            _normalize_station_payload(station)
            for station in (raw or [])
            if station.get("lat") is not None and station.get("lon") is not None
        ]
        if normalized == self._pending_stations:
            return
        self._pending_stations = normalized
        self._visible_count = len(self._pending_stations)
        # QWebEngine/Leaflet is the active backend on normal desktops.  Repainting
        # the hidden native map as well doubles the work during large catalog
        # refreshes, so update only the backend that is currently visible.
        if not self._using_leaflet():
            self.native.set_stations(self._pending_stations)
        self._run_leaflet(
            "window.GNSSGoMap && window.GNSSGoMap.setStations("
            f"{json.dumps(self._pending_stations, separators=(',', ':'))});"
        )
        self._update_counts()

    def set_selected(self, station_ids: set[str]) -> None:
        selected = set(station_ids)
        if selected == self._pending_selected:
            return
        self._pending_selected = selected
        if not self._using_leaflet():
            self.native.set_selected(self._pending_selected)
        self._run_leaflet(
            "window.GNSSGoMap && window.GNSSGoMap.setSelected("
            f"{json.dumps(sorted(self._pending_selected), separators=(',', ':'))});"
        )
        self._update_counts()

    def fit_stations(self) -> None:
        if self._using_leaflet():
            self._run_leaflet("window.GNSSGoMap && window.GNSSGoMap.fitVisible();")
        else:
            self.native.fit_stations()

    def clear_selection(self) -> None:
        self._pending_selected.clear()
        self.native.clear_selection()
        self._run_leaflet("window.GNSSGoMap && window.GNSSGoMap.clearSelection(false);")
        self._update_counts()

    def set_title(self, title: str) -> None:
        self.title.setText(title)

    def set_theme(self, theme: str) -> None:
        self._theme = "dark" if theme == "dark" else "light"
        self.native.set_theme(self._theme)
        self._run_leaflet(
            "window.GNSSGoMap && window.GNSSGoMap.setTheme("
            f"{json.dumps(self._theme)});"
        )

    def _language_changed(self, language: str) -> None:
        self._language = "zh" if str(language).lower().startswith("zh") else "en"
        self.native.set_language(self._language)
        self._run_leaflet(
            "window.GNSSGoMap && window.GNSSGoMap.setLanguage("
            f"{json.dumps(self._language)});"
        )
        self._update_counts()
        self._mode_changed(True)
        self._basemap_changed()

    def _application_theme(self) -> str:
        app = QtWidgets.QApplication.instance()
        if app is not None:
            value = app.property("gnssgo_effective_theme")
            if value in {"light", "dark"}:
                return str(value)
        return "light"

    def _clear_selection_requested(self) -> None:
        self.clear_selection()
        self.bridge.selectionCleared.emit()

    def _mode_changed(self, checked: bool) -> None:
        if not checked:
            return
        button = self.mode_group.checkedButton()
        mode = str(button.property("mapMode")) if button is not None else "select"
        self.native.set_interaction_mode(mode)
        self._run_leaflet(
            "window.GNSSGoMap && window.GNSSGoMap.setTool("
            f"{json.dumps(mode)});"
        )
        self.radius_value.setVisible(mode == "radius")
        hints = {
            "select": tr(
                "Drag to pan and click stations to select; Native fallback uses right-drag to pan."
            ),
            "rectangle": tr("Drag a rectangle to add all stations inside it to the selection."),
            "radius": tr("Choose a radius, then click the map center to add stations inside it."),
        }
        self.hint_label.setText(hints.get(mode, hints["select"]))

    def _radius_changed(self, value: float) -> None:
        self.native.set_radius_km(value)
        self._run_leaflet(
            "window.GNSSGoMap && window.GNSSGoMap.setRadiusKm("
            f"{float(value)});"
        )

    def _station_mode_changed(self) -> None:
        value = self.station_mode.currentData() or "individual"
        self._run_leaflet(
            "window.GNSSGoMap && window.GNSSGoMap.setMarkerMode("
            f"{json.dumps(value)});"
        )

    def _basemap_changed(self) -> None:
        value = self.basemap.currentData() or "offline"
        self._run_leaflet(
            "window.GNSSGoMap && window.GNSSGoMap.setBasemap("
            f"{json.dumps(value)});"
        )
        if self._using_leaflet():
            label = tr("OpenStreetMap") if value == "osm" else tr("Offline")
            self.backend_label.setText(tr("Map: Leaflet / {label}").format(label=label))
            return

        # If WebEngine exists but an earlier startup race left us on the native
        # fallback, choosing the online basemap is also an explicit retry request.
        if value == "osm" and self.web_view is not None:
            self._web_error = ""
            self._web_ready = False
            self._web_validated = False
            self.backend_label.setText(tr("Map: retrying Leaflet / OpenStreetMap..."))
            self.web_view.reload()
            QtCore.QTimer.singleShot(6500, self._fallback_if_leaflet_not_ready)
            return

        detail = f" ({self._web_error})" if self._web_error else ""
        self.backend_label.setText(tr("Map: Native offline{detail}").format(detail=detail))

    def _update_counts(self) -> None:
        visible_ids = {station.get("id") for station in self._pending_stations}
        hidden = len(self._pending_selected.difference(visible_ids))
        self.count_label.setText(
            tr("Visible: {visible}   Selected: {selected}   Hidden selected: {hidden}").format(
                visible=self._visible_count, selected=len(self._pending_selected), hidden=hidden
            )
        )

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.set_theme(self._application_theme())
        self.refresh_leaflet_view()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.refresh_leaflet_view()

    def refresh_leaflet_view(self) -> None:
        if self.web_view is None or not self._web_ready:
            return
        QtCore.QTimer.singleShot(
            80,
            lambda: self._run_leaflet("window.GNSSGoMap && window.GNSSGoMap.refreshSize();"),
        )

    def _leaflet_load_finished(self, ok: bool) -> None:
        if not ok or self.web_view is None:
            self._web_error = "Map HTML failed to load"
            self._activate_native_fallback()
            return
        self.web_view.page().runJavaScript(
            "JSON.stringify(window.GNSSGoMap ? window.GNSSGoMap.diagnostics() : null)",
            self._leaflet_diagnostics_checked,
        )

    def _bridge_map_ready(self) -> None:
        if self.web_view is None:
            return
        self.web_view.page().runJavaScript(
            "JSON.stringify(window.GNSSGoMap ? window.GNSSGoMap.diagnostics() : null)",
            self._leaflet_diagnostics_checked,
        )

    def _leaflet_diagnostics_checked(self, payload) -> None:
        if self.web_view is None or self._web_validated or self._web_ready:
            return
        try:
            diagnostics = json.loads(payload) if isinstance(payload, str) else (payload or {})
        except (TypeError, json.JSONDecodeError):
            diagnostics = {}
        ready = bool(
            diagnostics.get("leaflet")
            and diagnostics.get("map")
            and diagnostics.get("cluster")
            and diagnostics.get("individualLayer")
        )
        if not ready:
            # A resize shortly after page load often resolves the temporary 0x0 WebEngine viewport.
            QtCore.QTimer.singleShot(250, self._retry_leaflet_validation)
            return
        # The previous implementation tried to pixel-grab the QWebEngineView while
        # it was still the hidden page of a QStackedWidget.  On Windows that often
        # returns a blank image even though Leaflet is fully initialized, causing a
        # false fallback to the native map.  JS diagnostics are the reliable health
        # check here; once the Leaflet objects exist, promote the web view first and
        # then invalidate its size.
        self._web_ready = True
        self._web_validated = True
        self._web_error = ""
        try:
            land = _map_resource_path("world_land_110m.geojson").read_text(encoding="utf-8")
        except OSError:
            land = ""
        if land:
            self._run_leaflet(f"window.GNSSGoMap && window.GNSSGoMap.setLand({land});")
        self._run_leaflet(
            "window.GNSSGoMap && window.GNSSGoMap.setTheme("
            f"{json.dumps(self._theme)});"
        )
        self._run_leaflet(
            "window.GNSSGoMap && window.GNSSGoMap.setBasemap("
            f"{json.dumps(self.basemap.currentData() or 'offline')});"
        )
        self._run_leaflet(
            "window.GNSSGoMap && window.GNSSGoMap.setMarkerMode("
            f"{json.dumps(self.station_mode.currentData() or 'individual')});"
        )
        self._run_leaflet(
            "window.GNSSGoMap && window.GNSSGoMap.setLanguage("
            f"{json.dumps(self._language)});"
        )
        self._radius_changed(self.radius_value.value())
        self.set_stations(self._pending_stations)
        self.set_selected(self._pending_selected)
        self.map_stack.setCurrentWidget(self.web_view)
        self.refresh_leaflet_view()
        self._run_leaflet("window.GNSSGoMap && window.GNSSGoMap.fitWorld();")
        label = "OpenStreetMap" if self.basemap.currentData() == "osm" else "Offline"
        self.backend_label.setText(tr("Map: Leaflet / {label}").format(label=label))

    def _verify_leaflet_render(self) -> None:
        if self.web_view is None or not self._web_ready or self._web_validated or self._web_error:
            return
        pixmap = self.web_view.grab()
        image = pixmap.toImage()
        if image.isNull() or image.width() < 40 or image.height() < 40:
            self._web_error = "Leaflet render surface is unavailable"
            self._activate_native_fallback()
            return
        colors: set[int] = set()
        x_steps = range(0, image.width(), max(1, image.width() // 9))
        y_steps = range(0, image.height(), max(1, image.height() // 6))
        for x in x_steps:
            for y in y_steps:
                colors.add(image.pixel(min(x, image.width() - 1), min(y, image.height() - 1)))
                if len(colors) >= 3:
                    break
            if len(colors) >= 3:
                break
        if len(colors) < 3:
            self._web_error = "Leaflet rendered a blank surface"
            self._activate_native_fallback()
            return
        self._web_validated = True
        label = "OpenStreetMap" if self.basemap.currentData() == "osm" else "Offline"
        self.backend_label.setText(tr("Map: Leaflet / {label}").format(label=label))

    def _retry_leaflet_validation(self) -> None:
        if self.web_view is None or self._web_validated or self._web_error:
            return
        self.web_view.page().runJavaScript(
            "window.GNSSGoMap && window.GNSSGoMap.refreshSize();"
        )
        self.web_view.page().runJavaScript(
            "JSON.stringify(window.GNSSGoMap ? window.GNSSGoMap.diagnostics() : null)",
            self._leaflet_diagnostics_checked,
        )

    def _activate_native_fallback(self) -> None:
        self._web_ready = False
        self.native.set_stations(self._pending_stations)
        self.native.set_selected(self._pending_selected)
        self.map_stack.setCurrentWidget(self.native)
        detail = f" ({self._web_error})" if self._web_error else ""
        self.backend_label.setText(tr("Map: Native offline{detail}").format(detail=detail))
        self.native.show()

    def _fallback_if_leaflet_not_ready(self) -> None:
        if not self._web_validated:
            if not self._web_error:
                self._web_error = "Leaflet startup timeout"
            self._activate_native_fallback()

    def _using_leaflet(self) -> bool:
        return (
            self.web_view is not None
            and self._web_ready
            and self.map_stack.currentWidget() is self.web_view
        )

    def _run_leaflet(self, script: str) -> None:
        if self.web_view is not None and self._web_ready:
            self.web_view.page().runJavaScript(script)


def _map_resource_path(filename: str) -> Path:
    return Path(__file__).resolve().parents[1] / "resources" / "map" / filename


def _normalize_station_payload(station: dict) -> dict:
    """Ensure every map payload carries a valid marker class.

    If the caller already supplied a context-aware class (for example an IGS-only
    view where an overlap station must still be blue), preserve it.  Only derive
    the intrinsic class as a fallback for older callers that omit ``marker_class``.
    """
    payload = dict(station)
    existing = str(payload.get("marker_class") or "").strip().lower()
    if existing in {"igs_only", "regional_only", "igs_regional", "other"}:
        payload["marker_class"] = "igs_only" if existing == "igs_regional" else existing
        return payload

    data_networks = {
        str(value).strip().lower()
        for value in (payload.get("data_networks") or [])
        if str(value).strip()
    }
    regional_sources = {
        str(value).strip().lower()
        for value in (payload.get("regional_sources") or [])
        if str(value).strip()
    }
    is_igs = "igs" in data_networks
    is_regional = bool(regional_sources) or any(
        value != "igs" for value in data_networks
    )
    if is_igs:
        marker_class = "igs_only"
    elif is_regional:
        marker_class = "regional_only"
    else:
        marker_class = "other"
    payload["marker_class"] = marker_class
    return payload


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _wrap_lon(value: float) -> float:
    return ((value + 180.0) % 360.0) - 180.0


def _short_lon_delta(lon: float, center_lon: float) -> float:
    return _wrap_lon(lon - center_lon)


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_km = 6371.0088
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(_short_lon_delta(lon2, lon1))
    a = (
        math.sin(dphi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    )
    return radius_km * 2.0 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))
