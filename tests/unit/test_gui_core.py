from gnssgo import GNSSGo
from gnssgo.config import load_settings
from gnssgo.gui.main_window import MainWindow
from gnssgo.gui.pages.observations import ObservationsPage
from gnssgo.gui.pages.settings import SettingsPage
from gnssgo.gui.pages.stations import StationsPage
from gnssgo.gui.qt import require_qt
from gnssgo.gui.services.core_service import CoreService
from gnssgo.gui.services.task_service import TaskService
from gnssgo.gui.styles.tokens import app_qss
from gnssgo.gui.widgets.australia_source_filter import AustraliaSourceFilter
from gnssgo.gui.widgets.data_network_filter import DataNetworkFilter
from gnssgo.gui.widgets.map_view import MapView, NativeMapCanvas
from gnssgo.gui.widgets.provider_selector import ProviderSelector
from gnssgo.models import Station
from gnssgo.providers.base import GNSSProvider, ProviderCapabilities
from gnssgo.providers.registry import ProviderRegistry

QtCore, _QtGui, _QtWidgets = require_qt()


class CapabilityProvider(GNSSProvider):
    def __init__(self, name: str, caps: ProviderCapabilities) -> None:
        self.name = name
        self._caps = caps

    def capabilities(self) -> ProviderCapabilities:
        return self._caps

    async def search_observations(self, request):
        return []

    async def search_navigation(self, request):
        return []

    async def search_products(self, request):
        return []


def test_provider_selector_preserves_dynamic_items(qtbot) -> None:
    selector = ProviderSelector(["auto", "whu", "bkg"])
    qtbot.addWidget(selector)

    assert [selector.itemText(i) for i in range(selector.count())] == ["auto", "whu", "bkg"]


def test_core_service_filters_providers_by_capability() -> None:
    registry = ProviderRegistry()
    registry.register(CapabilityProvider("obs", ProviderCapabilities(observations=True)))
    registry.register(CapabilityProvider("nav", ProviderCapabilities(navigation=True)))
    registry.register(CapabilityProvider("prod", ProviderCapabilities(products=["orbit"])))
    settings = load_settings({"provider": {"priority": ["obs", "nav", "prod"]}})
    core = CoreService(GNSSGo(settings=settings, registry=registry))

    assert core.providers_for("observations") == ["auto", "obs"]
    assert core.providers_for("navigation") == ["auto", "nav"]
    assert core.providers_for("products") == ["auto", "prod"]


def test_settings_page_save_applies_core_settings(qtbot, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "gnssgo.gui.pages.settings.save_user_settings",
        lambda settings: tmp_path / "settings.json",
    )
    core = CoreService(GNSSGo(settings=load_settings()))
    page = SettingsPage(core, TaskService(core))
    qtbot.addWidget(page)

    page.data_root.setText(str(tmp_path / "data"))
    page.catalog_path.setText(str(tmp_path / "stations.sqlite"))
    page.workers.setValue(7)
    page.per_provider_workers.setValue(2)
    page.retries.setValue(3)
    page.auto_extract.setChecked(True)
    page.keep_compressed.setChecked(True)
    while page.provider_priority.list.count() > 0:
        page.provider_priority.list.takeItem(0)
    page.provider_priority.list.addItems(["whu", "bkg"])
    while page.product_provider_priority.list.count() > 0:
        page.product_provider_priority.list.takeItem(0)
    page.product_provider_priority.list.addItems(["esa", "ign"])
    page.proxy.setText("http://proxy.example:8080")
    page.save()

    settings = core.client.settings
    assert settings.archive.root == tmp_path / "data"
    assert settings.stations.catalog_path == tmp_path / "stations.sqlite"
    assert settings.download.workers == 7
    assert settings.download.per_provider_workers == 2
    assert settings.download.retries == 3
    assert settings.archive.auto_extract is True
    assert settings.archive.keep_compressed is True
    assert settings.provider.priority == ["whu", "bkg"]
    assert settings.products.provider_priority == ["esa", "ign"]
    assert settings.network.proxy == "http://proxy.example:8080"


def test_main_window_instantiates(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)

    assert window.windowTitle() == "GNSS Go"
    assert window.stack.count() == 6


def test_station_page_map_table_selection_sync(qtbot, tmp_path) -> None:
    catalog_path = tmp_path / "stations.sqlite"
    from gnssgo.stations import StationCatalog

    StationCatalog(catalog_path, seed_if_empty=False).upsert_many(
        [
            Station(id="AAAA00AAA", latitude=1, longitude=2, country="AAA", network=["igs"]),
            Station(id="BBBB00BBB", latitude=3, longitude=4, country="BBB", network=["igs"]),
        ],
        provider="unit",
        source="unit",
    )
    settings = load_settings(
        {"stations": {"catalog_path": catalog_path, "auto_seed": False}}
    )
    core = CoreService(GNSSGo(settings=settings))
    page = StationsPage(core, TaskService(core))
    qtbot.addWidget(page)

    page._map_station_toggled("AAAA00AAA", True)
    assert "AAAA00AAA" in page.table.model().selected

    model = page.table.model()
    model.setData(model.index(1, 0), QtCore.Qt.Checked)
    page._table_selection_changed()
    assert "BBBB00BBB" in page.selected


def test_leaflet_map_view_has_webengine_or_native_fallback(qtbot) -> None:
    class EmptyMapService:
        def stations_json(self):
            return []

    widget = MapView(EmptyMapService())
    qtbot.addWidget(widget)

    assert widget.web_view is not None or widget.native is not None
    assert widget.bridge is not None


def test_native_map_loads_offline_land_geometry() -> None:
    assert NativeMapCanvas._land_geometry()


def test_native_map_rectangle_selects_stations(qtbot) -> None:
    canvas = NativeMapCanvas()
    qtbot.addWidget(canvas)
    canvas.resize(720, 360)
    canvas.set_stations(
        [
            {"id": "ZERO00AAA", "lat": 0.0, "lon": 0.0},
            {"id": "FAR000BBB", "lat": 70.0, "lon": 150.0},
        ]
    )

    rect = canvas.rect().adjusted(8, 8, -8, -8)
    x, y = canvas._project(0.0, 0.0, rect)
    selected = canvas._stations_in_screen_rect(
        QtCore.QRect(round(x - 20), round(y - 20), 40, 40)
    )

    assert selected == ["ZERO00AAA"]


def test_native_map_zoom_and_pan_projection(qtbot) -> None:
    canvas = NativeMapCanvas()
    qtbot.addWidget(canvas)
    canvas.resize(720, 360)
    rect = canvas.rect().adjusted(8, 8, -8, -8)

    x_before, _y_before = canvas._project(0.0, 30.0, rect)
    canvas.zoom = 2.0
    x_after, _y_after = canvas._project(0.0, 30.0, rect)

    assert x_after > x_before


def test_data_network_filter_defaults_to_igs(qtbot) -> None:
    widget = DataNetworkFilter()
    qtbot.addWidget(widget)

    assert widget.selected_ids() == ["igs"]


def test_empty_data_network_filter_returns_no_stations(tmp_path) -> None:
    catalog_path = tmp_path / "stations.sqlite"
    from gnssgo.stations import StationCatalog

    StationCatalog(catalog_path, seed_if_empty=False).upsert_many(
        [Station(id="AAAA00AAA", latitude=1, longitude=2, data_networks=["igs"])],
        provider="unit",
        source="unit",
    )
    settings = load_settings(
        {"stations": {"catalog_path": catalog_path, "auto_seed": False}}
    )
    core = CoreService(GNSSGo(settings=settings))

    assert core.search_stations(data_networks=[]) == []


def test_selection_is_preserved_when_filtered_hidden(qtbot, tmp_path) -> None:
    catalog_path = tmp_path / "stations.sqlite"
    from gnssgo.stations import StationCatalog

    StationCatalog(catalog_path, seed_if_empty=False).upsert_many(
        [
            Station(id="AAAA00AAA", latitude=1, longitude=2, data_networks=["igs"]),
            Station(
                id="ALBY00AUS",
                latitude=-35,
                longitude=117,
                country="AUS",
                data_networks=["australia"],
                regional_sources=["auscope"],
            ),
        ],
        provider="unit",
        source="unit",
    )
    settings = load_settings(
        {"stations": {"catalog_path": catalog_path, "auto_seed": False}}
    )
    core = CoreService(GNSSGo(settings=settings))
    page = StationsPage(core, TaskService(core))
    qtbot.addWidget(page)

    page._map_station_toggled("ALBY00AUS", True)
    page.filter_state.data_networks = ["igs"]
    page.filter_state.regional_sources = None
    page.refresh_from_filter_state()

    assert "ALBY00AUS" in page.selected
    assert "Hidden selected: 1" in page.filter_status.text()


def test_station_bbox_and_radius_ranges(qtbot) -> None:
    page = StationsPage(CoreService(GNSSGo(settings=load_settings())), TaskService())
    qtbot.addWidget(page)

    assert page.west.minimum() == -180
    assert page.east.maximum() == 180
    assert page.south.minimum() == -90
    assert page.north.maximum() == 90
    assert page.radius_lon.minimum() == -180
    assert page.radius_lat.maximum() == 90


def test_observations_sampling_and_review_plan(qtbot) -> None:
    core = CoreService(GNSSGo(settings=load_settings()))
    page = ObservationsPage(core, TaskService(core))
    qtbot.addWidget(page)

    assert page.sampling.itemText(0) == "Auto"
    assert any(page.sampling.itemText(i) == "30 s" for i in range(page.sampling.count()))
    buttons = page.findChildren(_QtWidgets.QPushButton)
    assert any(button.text() == "Review Plan" for button in buttons)


def test_australia_source_title_summary_and_tristate(qtbot) -> None:
    widget = AustraliaSourceFilter()
    qtbot.addWidget(widget)
    first = next(iter(widget.checks.values()))
    first.setChecked(False)

    labels = [label.text() for label in widget.findChildren(_QtWidgets.QLabel)]
    assert "Australia Sources" in labels
    assert widget.select_all.checkState() == QtCore.Qt.PartiallyChecked


def test_settings_theme_switch_and_priority_lists(qtbot, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "gnssgo.gui.pages.settings.save_user_settings",
        lambda settings: tmp_path / "settings.json",
    )
    core = CoreService(GNSSGo(settings=load_settings()))
    page = SettingsPage(core, TaskService(core))
    qtbot.addWidget(page)

    page.theme.setCurrentText("dark")
    page.provider_priority.list.setCurrentRow(1)
    before = page.provider_priority.values()
    page.provider_priority._move(-1)
    page.save()

    assert core.client.settings.appearance.theme == "dark"
    assert page.provider_priority.values()[0] == before[1]
    assert "QWidget" in app_qss(core.client.settings.appearance.theme)


def test_data_network_tree_is_global_regional_continent_country_source(qtbot) -> None:
    widget = DataNetworkFilter()
    qtbot.addWidget(widget)

    assert widget.tree.topLevelItemCount() == 2
    assert widget.tree.topLevelItem(0).text(0) == "Global"
    regional = widget.tree.topLevelItem(1)
    assert regional.text(0) == "Regional"
    continents = [regional.child(i).text(0) for i in range(regional.childCount())]
    assert continents == [
        "Africa", "Antarctica", "Asia", "Europe", "Latin America", "North America", "Oceania"
    ]

    asia = next(regional.child(i) for i in range(regional.childCount()) if regional.child(i).text(0) == "Asia")
    korea = next(asia.child(i) for i in range(asia.childCount()) if asia.child(i).text(0) == "Korea")
    assert korea.childCount() == 2
    assert {korea.child(i).text(0) for i in range(korea.childCount())} == {
        "KASI KASINet / KVN FTP", "National GNSS Data Center"
    }


def test_data_network_select_all_button_toggles_select_none(qtbot) -> None:
    widget = DataNetworkFilter()
    qtbot.addWidget(widget)

    widget.select_all.click()
    assert widget.select_all.text() == "Select None"
    assert all(item.checkState(0) == QtCore.Qt.Checked for item in widget._leaf_items.values())

    widget.select_all.click()
    assert widget.select_all.text() == "Select All"
    assert widget.selected_ids() == []
