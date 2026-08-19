from __future__ import annotations

import re
from pathlib import Path

from gnssgo.gui.i18n import language_manager, tr
from gnssgo.gui.models.tasks import GuiTaskType
from gnssgo.gui.pages.base import CorePage
from gnssgo.gui.qt import require_qt
from gnssgo.gui.services.map_service import MapService, station_to_json
from gnssgo.gui.widgets.data_network_filter import DataNetworkFilter
from gnssgo.gui.widgets.date_range import DateRangeWidget
from gnssgo.gui.widgets.map_view import MapView
from gnssgo.gui.widgets.provider_selector import ProviderSelector
from gnssgo.gui.widgets.station_table import StationTable
from gnssgo.gui.workers.base import FunctionWorker
from gnssgo.regional_sources import default_regional_source_registry
from gnssgo.stations.catalog import seed_stations

_QtCore, _QtGui, QtWidgets = require_qt()


class ObservationsPage(CorePage):
    """Unified station browser and observation download workflow."""

    def __init__(self, core, task_service, parent=None) -> None:
        super().__init__(core, task_service, parent)
        self.selected_from_map: set[str] = set()
        self._visible_ids: set[str] = set()
        self._visible_stations: list = []
        self._station_table_model = None
        self._catalog_refreshing: set[str] = set()
        self._catalog_workers: dict[str, object] = {}
        self._catalog_quiet: set[str] = set()
        # Catalog refresh is background maintenance, not part of Review Plan.
        # Keep it off both the global download pool and the dedicated plan pool.
        self._catalog_thread_pool = _QtCore.QThreadPool(self)
        self._catalog_thread_pool.setMaxThreadCount(2)
        self._last_map_payload: list[dict] = []
        # Network/source filters define the default station scope.  When Brazil ·
        # RBMC is the sole active SIRGAS source, no manual station selection means
        # "all files available in the official IBGE day directory".
        self._manual_station_selection = False

        self._refresh_timer = _QtCore.QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(150)
        self._refresh_timer.timeout.connect(self.refresh_map_networks)

        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)
        main = QtWidgets.QVBoxLayout()
        main.setSpacing(10)
        root.addLayout(main, 1)

        title = QtWidgets.QLabel(tr("GNSS Data Download"))
        title.setObjectName("PageTitle")
        subtitle = QtWidgets.QLabel(tr("Browse stations on one map, select targets, set date and sampling, then review the download plan."))
        subtitle.setObjectName("PageSubtitle")
        main.addWidget(title)
        main.addWidget(subtitle)

        # Station discovery tools replace the old Map / IDs / File / BBox / Radius tabs.
        station_tools = QtWidgets.QFrame()
        station_tools.setObjectName("CardWidget")
        station_tools_layout = QtWidgets.QHBoxLayout(station_tools)
        station_tools_layout.setContentsMargins(10, 8, 10, 8)
        self.station_search = QtWidgets.QLineEdit()
        self.station_search.setPlaceholderText(tr("Search station ID, country, network or source…"))
        self.station_search.setClearButtonEnabled(True)
        self.station_search.textChanged.connect(self.schedule_map_refresh)
        station_tools_layout.addWidget(self.station_search, 1)

        self.paste_ids_button = QtWidgets.QPushButton(tr("Paste Stations"))
        self.paste_ids_button.setObjectName("SecondaryButton")
        self.paste_ids_button.clicked.connect(self._paste_station_ids)
        station_tools_layout.addWidget(self.paste_ids_button)

        self.import_button = QtWidgets.QPushButton(tr("Import File"))
        self.import_button.setObjectName("SecondaryButton")
        self.import_button.clicked.connect(self._import_station_file)
        station_tools_layout.addWidget(self.import_button)

        self.station_list_button = QtWidgets.QToolButton()
        self.station_list_button.setText(tr("Station list"))
        self.station_list_button.setCheckable(True)
        self.station_list_button.setChecked(False)
        self.station_list_button.toggled.connect(self._toggle_station_table)
        station_tools_layout.addWidget(self.station_list_button)
        main.addWidget(station_tools)

        map_frame = QtWidgets.QFrame()
        map_frame.setObjectName("mapCanvasPanel")
        map_frame_layout = QtWidgets.QVBoxLayout(map_frame)
        map_frame_layout.setContentsMargins(0, 0, 0, 0)
        self.map = MapView(MapService(catalog=self.core.client._station_catalog()))
        self.map.set_title(tr("Station map"))
        self.map.setMinimumHeight(360)
        self.map.bridge.stationToggled.connect(self._map_station_toggled)
        self.map.bridge.selectionCleared.connect(self._map_selection_cleared)
        map_frame_layout.addWidget(self.map)
        main.addWidget(map_frame, 1)

        # The former Stations page is now an optional table directly under the map.
        self.station_table_frame = QtWidgets.QFrame()
        self.station_table_frame.setObjectName("CardWidget")
        table_layout = QtWidgets.QVBoxLayout(self.station_table_frame)
        table_layout.setContentsMargins(8, 8, 8, 8)
        table_header = QtWidgets.QHBoxLayout()
        table_title = QtWidgets.QLabel(tr("Visible stations"))
        table_title.setObjectName("SectionTitle")
        self.table_summary = QtWidgets.QLabel("")
        self.table_summary.setObjectName("mapHintLabel")
        self.table_summary.setProperty("_i18n_dynamic", True)
        table_header.addWidget(table_title)
        table_header.addStretch(1)
        table_header.addWidget(self.table_summary)
        table_layout.addLayout(table_header)
        self.station_table = StationTable()
        self.station_table.setMinimumHeight(160)
        table_layout.addWidget(self.station_table)
        self.station_table_frame.setVisible(False)
        main.addWidget(self.station_table_frame)

        # Compact control column on the right, following the new three-column
        # layout: network tree | map + availability | current selection.
        config = QtWidgets.QFrame()
        config.setObjectName("RightControlPanel")
        config.setMinimumWidth(300)
        config.setMaximumWidth(340)
        config_layout = QtWidgets.QVBoxLayout(config)
        config_layout.setContentsMargins(14, 14, 14, 14)
        config_layout.setSpacing(9)

        current_title = QtWidgets.QLabel(tr("Current Selection"))
        current_title.setObjectName("SectionTitle")
        config_layout.addWidget(current_title)

        self.dates = DateRangeWidget()
        self.dates.set_compact_mode(True)
        self.provider = ProviderSelector(self.core.providers_for("observations"))
        self.provider.currentTextChanged.connect(self.refresh_sampling_options)
        self.rinex = QtWidgets.QComboBox()
        self.rinex.addItems(["auto", "2", "3", "4"])
        self.sampling = QtWidgets.QComboBox()
        self.output = QtWidgets.QLineEdit()
        self.output.setPlaceholderText(tr("Default archive location"))
        output_browse = QtWidgets.QPushButton(tr("Browse…"))
        output_browse.setObjectName("SecondaryButton")
        output_browse.clicked.connect(self._browse_output)

        def add_field(label_text, widget):
            label = QtWidgets.QLabel(label_text)
            label.setObjectName("ControlLabel")
            config_layout.addWidget(label)
            config_layout.addWidget(widget)

        add_field(tr("Date"), self.dates)
        add_field(tr("Provider"), self.provider)
        add_field(tr("Sampling"), self.sampling)
        add_field(tr("RINEX"), self.rinex)
        config_layout.addWidget(QtWidgets.QLabel(tr("Output")))
        output_row = QtWidgets.QHBoxLayout()
        output_row.addWidget(self.output, 1)
        output_row.addWidget(output_browse)
        config_layout.addLayout(output_row)

        review = QtWidgets.QPushButton(tr("Plan (PLAN)"))
        review.setObjectName("PrimaryButton")
        review.clicked.connect(self.submit)
        config_layout.addSpacing(4)
        config_layout.addWidget(review)
        config_layout.addStretch(1)

        # Persistent visibility filters on the left.
        filter_scroll = QtWidgets.QScrollArea()
        self.filter_scroll = filter_scroll
        filter_scroll.setWidgetResizable(True)
        filter_scroll.setMinimumWidth(275)
        filter_scroll.setMaximumWidth(330)
        filter_scroll.setHorizontalScrollBarPolicy(_QtCore.Qt.ScrollBarAlwaysOff)
        filter_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        filter_body = QtWidgets.QFrame()
        filter_body.setObjectName("CardWidget")
        filter_layout = QtWidgets.QVBoxLayout(filter_body)
        filter_layout.setContentsMargins(12, 10, 10, 10)
        filter_title = QtWidgets.QLabel(tr("Region / Data Source"))
        filter_title.setObjectName("SectionTitle")
        filter_layout.addWidget(filter_title)
        filter_hint = QtWidgets.QLabel(tr("Select global IGS or regional sources by continent/country."))
        filter_hint.setWordWrap(True)
        filter_hint.setObjectName("mapHintLabel")
        filter_layout.addWidget(filter_hint)
        self.filter_status = QtWidgets.QLabel(tr("Visible: 0   Selected: 0"))
        self.filter_status.setObjectName("StatusBadge")
        self.filter_status.setProperty("_i18n_dynamic", True)
        filter_layout.addWidget(self.filter_status)

        active_catalog = self.core.client._station_catalog()
        # IGS is checked by default.  Ensure there is always a small built-in IGS
        # map immediately at startup instead of showing a blank map while the live
        # BKG catalog refresh runs in the background.  The live catalog then
        # replaces/augments these seed stations automatically.
        igs_rows = active_catalog.search(data_networks=["igs"])
        if not any(
            station.latitude is not None and station.longitude is not None
            for station in igs_rows
        ):
            active_catalog.upsert_many(seed_stations(), provider="builtin", source="builtin")
        self.data_network_filter = DataNetworkFilter(catalog=active_catalog)
        self.data_network_filter.changed.connect(self._station_scope_changed)
        filter_layout.addWidget(self.data_network_filter, 1)
        filter_scroll.setWidget(filter_body)

        # The availability panel belongs visually below the map in the new GUI.
        availability = self.data_network_filter.availability_frame
        self.data_network_filter.layout().removeWidget(availability)
        availability.setParent(self)
        map_index = main.indexOf(map_frame)
        main.insertWidget(map_index + 1, availability)

        # root originally contains the central column. Insert the source panel
        # before it and append the control panel after it.
        root.insertWidget(0, filter_scroll)
        root.addWidget(config)

        language_manager.changed.connect(self._language_changed)
        self.refresh_sampling_options()
        self.refresh_map_networks()
        # Taiwan GDMS publishes a downloadable station list behind its map page.
        # Check for a fresh official catalog once per desktop session without
        # blocking startup; the bundled CSV remains available offline.
        _QtCore.QTimer.singleShot(350, self._refresh_session_catalogs)

    def _language_changed(self, _language: str) -> None:
        # Static labels are retranslated by MainWindow; these counters/tooltips
        # are dynamic and must be recomputed from the current station state.
        self._update_filter_status()
        if self.station_table_frame.isVisible():
            self.table_summary.setText(
                tr("{count} visible").format(count=len(self._visible_stations))
            )

    def _refresh_session_catalogs(self) -> None:
        # Refresh a few lightweight catalogs once per desktop session.  IGS is
        # selected by default, so load the live BKG/IGS station map in the
        # background while the bundled seed is already visible.  GeoNet's GNSS
        # station endpoint restores New Zealand coordinates/counts, and Taiwan
        # keeps its existing official GDMS refresh.
        jobs = [
            ("startup:igs:bkg", "IGS · BKG station catalog", "igs", "bkg"),
            ("startup:geonet_jp", "Japan · GEONET", "japan", "geonet_jp"),
            ("startup:geonet_nz", "New Zealand · GeoNet", "new_zealand", "geonet_nz"),
            ("startup:gdms_tw", "Taiwan, China · GDMS", "taiwan", "gdms_tw"),
            ("startup:satref_hk", "Hong Kong, China · SatRef", "hong_kong", "satref_hk"),
            ("startup:epn", "Europe · EPN", "europe", "epn"),
        ]
        for key, label, network_id, provider_name in jobs:
            if key in self._catalog_refreshing:
                continue
            self._start_catalog_worker(
                key,
                label,
                self.core.force_update_station_provider,
                network_id,
                provider_name,
                quiet=True,
            )

    def submit(self) -> None:
        discover_available = self._uses_provider_directory_scope()
        if not self.selected_from_map and not discover_available:
            QtWidgets.QMessageBox.information(
                self,
                "Select stations",
                (
                    "Select at least one station on the map, in the station list, "
                    "or by pasting/importing station IDs."
                ),
            )
            return
        start, end = self.dates.values()
        selected_sources = self.selected_regional_sources()
        selected_networks = self.data_network_filter.selected_ids()
        effective_provider = self.provider.value()
        # A single national/regional source already identifies its owning provider.
        # Route to it directly instead of entering the generic auto-provider graph.
        # This makes national archive planning deterministic and avoids unrelated
        # provider discovery during Review Plan.
        if effective_provider == "auto" and len(selected_sources or []) == 1:
            try:
                source = default_regional_source_registry().get(selected_sources[0])
            except Exception:
                source = None
            if source is not None and source.data_network in selected_networks:
                effective_provider = source.provider

        request = {
            # In RBMC/CSN all-available mode the provider discovers stations from
            # its authoritative YYYY/DOY listing. A manual map/list selection turns
            # this back into the normal explicit-station workflow.
            "stations": [] if discover_available else sorted(self.selected_from_map),
            "station_file": None,
            "bbox": None,
            "center": None,
            "radius": None,
            "start": start,
            "end": end,
            "provider": effective_provider,
            "data_networks": selected_networks,
            "regional_sources": selected_sources,
            "discover_available": discover_available,
            "sampling": self.sampling.currentData(),
            "rinex": self.rinex.currentText(),
            "output": self.output.text() or None,
        }
        self.run_plan(
            name="Observation download",
            task_type=GuiTaskType.OBS,
            request=request,
            planner=lambda: self.core.plan_observations(**request),
        )

    def station_ids(self) -> list[str]:
        return sorted(self.selected_from_map)

    def _map_station_toggled(self, station_id: str, selected: bool) -> None:
        self._manual_station_selection = True
        if selected:
            self.selected_from_map.add(station_id)
        else:
            self.selected_from_map.discard(station_id)
        self.map.set_selected(self.selected_from_map)
        if self.station_table.model() is not None:
            self.station_table.viewport().update()
        self._update_filter_status()

    def _map_selection_cleared(self) -> None:
        self.selected_from_map.clear()
        self._manual_station_selection = False
        if self.station_table.model() is not None:
            self.station_table.viewport().update()
        self._update_filter_status()

    def _station_scope_changed(self) -> None:
        """Reset stale map selection when the data/source scope changes.

        Previously, IGS selections stayed selected after switching to Brazil ·
        RBMC.  The resulting request therefore contained only that small IGS
        subset even though the IBGE directory exposed many more RBMC files.
        """
        self.selected_from_map.clear()
        self._manual_station_selection = False
        if hasattr(self, "map"):
            self.map.set_selected(self.selected_from_map)
        self.refresh_sampling_options()
        self.schedule_map_refresh()

    def _uses_provider_directory_scope(self) -> bool:
        if self._manual_station_selection:
            return False
        networks = set(self.data_network_filter.selected_ids())
        sources = set(self.selected_regional_sources() or [])
        # Brazil RBMC and Chile CSN both expose an authoritative YYYY/DOY day
        # directory.  With no manual station selection, let that directory define
        # the downloadable set instead of issuing one request per map marker.
        return networks == {"sirgas"} and sources in (
            {"sirgas_brazil"},
            {"sirgas_chile"},
        )

    def schedule_map_refresh(self) -> None:
        self.refresh_sampling_options()
        self._ensure_selected_network_catalogs()
        self._refresh_timer.start()

    def _ensure_selected_network_catalogs(self) -> None:
        selected_networks = self.data_network_filter.selected_ids()
        selected_sources = self.selected_regional_sources() or []

        # When exactly one regional source is selected, refresh only its provider.
        # The previous implementation refreshed every SIRGAS country whenever the
        # user selected Chile/Mexico/Uruguay, which could leave the global Qt pool
        # occupied by unrelated slow portals.
        if len(selected_sources) == 1:
            try:
                source = default_regional_source_registry().get(selected_sources[0])
            except Exception:
                source = None
            if source is not None and source.data_network in selected_networks:
                self._ensure_provider_catalog(source.data_network, source.provider, source.name)
                return

        for network_id in selected_networks:
            if network_id == "igs":
                continue
            key = f"network:{network_id}"
            if key in self._catalog_refreshing:
                continue
            try:
                needs_refresh = self.core.network_catalog_needs_refresh(network_id)
            except Exception:
                continue
            if not needs_refresh:
                continue
            network_name = self.data_network_filter.registry.get(network_id).name
            self._start_catalog_worker(
                key, network_name, self.core.update_station_network, network_id
            )

    def _ensure_provider_catalog(
        self, network_id: str, provider_name: str, label: str
    ) -> None:
        key = f"provider:{network_id}:{provider_name}"
        if key in self._catalog_refreshing:
            return
        try:
            needs_refresh = self.core.provider_catalog_needs_refresh(
                network_id, provider_name
            )
        except Exception:
            return
        if not needs_refresh:
            return
        self._start_catalog_worker(
            key, label, self.core.update_station_provider, network_id, provider_name
        )

    def _start_catalog_worker(self, key: str, label: str, fn, *args, quiet: bool = False) -> None:
        self._catalog_refreshing.add(key)
        if quiet:
            self._catalog_quiet.add(key)
        else:
            self.filter_status.setText(tr("Refreshing {label} station catalog…").format(label=tr(label)))
        worker = FunctionWorker(fn, *args)
        self._catalog_workers[key] = worker
        worker.signals.result.connect(
            lambda result, k=key: self._catalog_refresh_finished(k, result)
        )
        worker.signals.error.connect(
            lambda message, k=key, text=label: self._catalog_refresh_failed(k, text, message)
        )
        worker.signals.finished.connect(
            lambda k=key: self._catalog_workers.pop(k, None)
        )
        self._catalog_thread_pool.start(worker)

    def _catalog_refresh_finished(self, key: str, _result: object) -> None:
        self._catalog_refreshing.discard(key)
        self._catalog_quiet.discard(key)
        self.data_network_filter.refresh_catalog_metadata()
        self.refresh_map_networks()

    def _catalog_refresh_failed(self, key: str, label: str, message: str) -> None:
        self._catalog_refreshing.discard(key)
        quiet = key in self._catalog_quiet
        self._catalog_quiet.discard(key)
        if not quiet:
            self.filter_status.setText(tr("{label} catalog refresh failed: {message}").format(label=tr(label), message=message))

    def refresh_map_networks(self) -> None:
        stations = self.core.search_stations(
            query=self.station_search.text().strip() or None,
            data_networks=self.data_network_filter.selected_ids(),
            regional_sources=self.selected_regional_sources(),
            continents=self.data_network_filter.selected_continents(),
        )
        self._visible_stations = list(stations)
        self._visible_ids = {station.id for station in stations}
        active_networks = self.data_network_filter.selected_ids()
        payload = [station_to_json(station, active_networks) for station in stations]
        if payload != self._last_map_payload:
            self._last_map_payload = payload
            self.map.set_stations(payload)
        self.map.set_selected(self.selected_from_map)
        if self.station_table_frame.isVisible():
            self._refresh_station_table()
        else:
            self.table_summary.setText(tr("{count} visible").format(count=len(self._visible_stations)))
        self._update_filter_status()

    def selected_regional_sources(self) -> list[str] | None:
        return self.data_network_filter.selected_source_ids()

    def refresh_sampling_options(self) -> None:
        if not hasattr(self, "sampling"):
            return
        current = self.sampling.currentData()
        options = self.core.observation_sampling_options(
            provider=self.provider.value() if hasattr(self, "provider") else "auto",
            data_networks=self.data_network_filter.selected_ids()
            if hasattr(self, "data_network_filter")
            else None,
            regional_sources=self.selected_regional_sources()
            if hasattr(self, "data_network_filter")
            else None,
        )
        self.sampling.blockSignals(True)
        self.sampling.clear()
        self.sampling.addItem(tr("Auto"), None)
        for label, value in options:
            self.sampling.addItem(label, value)
        index = self.sampling.findData(current)
        self.sampling.setCurrentIndex(index if index >= 0 else 0)
        self.sampling.blockSignals(False)

    def _refresh_station_table(self) -> None:
        model = self.station_table.set_stations(self._visible_stations, self.selected_from_map)
        self._station_table_model = model
        model.dataChanged.connect(self._table_selection_changed)
        self.table_summary.setText(tr("{count} visible").format(count=len(self._visible_stations)))
        header = self.station_table.horizontalHeader()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(6, QtWidgets.QHeaderView.Stretch)

    def _table_selection_changed(self, *_args) -> None:
        self._manual_station_selection = True
        self.map.set_selected(self.selected_from_map)
        self._update_filter_status()

    def _toggle_station_table(self, visible: bool) -> None:
        self.station_table_frame.setVisible(visible)
        self.station_list_button.setText(tr("Hide list") if visible else tr("Station list"))
        if visible:
            self._refresh_station_table()

    def _paste_station_ids(self) -> None:
        text, ok = QtWidgets.QInputDialog.getMultiLineText(
            self,
            tr("Paste station IDs"),
            tr("Station IDs separated by commas, spaces, or new lines:"),
            "",
        )
        if ok and text.strip():
            self._select_station_ids(_parse_station_ids(text))

    def _import_station_file(self) -> None:
        path, _filter = QtWidgets.QFileDialog.getOpenFileName(
            self,
            tr("Import station IDs"),
            "",
            tr("Station list (*.txt *.csv);;All files (*)"),
        )
        if not path:
            return
        try:
            text = Path(path).read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            QtWidgets.QMessageBox.warning(self, tr("Import failed"), str(exc))
            return
        self._select_station_ids(_parse_station_ids(text))

    def _select_station_ids(self, station_ids: list[str]) -> None:
        self._manual_station_selection = True
        available = {station.id.upper(): station.id for station in self.core.search_stations()}
        added = 0
        missing: list[str] = []
        for candidate in station_ids:
            station_id = available.get(candidate.upper())
            if station_id is None:
                missing.append(candidate)
                continue
            if station_id not in self.selected_from_map:
                self.selected_from_map.add(station_id)
                added += 1
        self.map.set_selected(self.selected_from_map)
        if self.station_table.model() is not None:
            self.station_table.viewport().update()
        self._update_filter_status()
        if missing:
            preview = ", ".join(missing[:8])
            suffix = "…" if len(missing) > 8 else ""
            self.filter_status.setToolTip(f"Not found: {preview}{suffix}")
        else:
            self.filter_status.setToolTip("")
        if added == 0 and missing:
            QtWidgets.QMessageBox.information(
                self,
                tr("No stations added"),
                tr("None of the supplied station IDs were found in the current station catalog."),
            )

    def _browse_output(self) -> None:
        directory = QtWidgets.QFileDialog.getExistingDirectory(self, tr("Output directory"))
        if directory:
            self.output.setText(directory)

    def _update_filter_status(self) -> None:
        hidden = len(self.selected_from_map.difference(self._visible_ids))
        if self._uses_provider_directory_scope():
            sources = set(self.selected_regional_sources() or [])
            source_label = (
                tr("Brazil · RBMC") if sources == {"sirgas_brazil"} else tr("Chile · CSN")
            )
            self.filter_status.setText(
                tr("Visible: {visible}   Download: all available files for date").format(
                    visible=len(self._visible_ids)
                )
            )
            self.table_summary.setText(
                tr("{count} visible · availability discovered at Plan time").format(
                    count=len(self._visible_ids)
                )
            )
            self.filter_status.setToolTip(
                tr("{source} uses the official daily directory. Click a station to switch to an explicit station selection.").format(
                    source=source_label
                )
            )
            return
        self.filter_status.setText(
            tr("Visible: {visible}   Selected: {selected}   Hidden: {hidden}").format(
                visible=len(self._visible_ids), selected=len(self.selected_from_map), hidden=hidden
            )
        )
        self.table_summary.setText(
            tr("{visible} visible · {selected} selected").format(
                visible=len(self._visible_ids), selected=len(self.selected_from_map)
            )
        )



def _parse_station_ids(text: str) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in re.split(r"[\s,;]+", text):
        value = item.strip().upper()
        if not value or value.startswith("#") or value in seen:
            continue
        # Station IDs are commonly 4 or 9 characters; keep the parser permissive
        # so local aliases can also be resolved by the catalog.
        seen.add(value)
        result.append(value)
    return result
