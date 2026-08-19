from __future__ import annotations

from collections import defaultdict

from gnssgo.data_networks import DataNetwork, default_data_network_registry
from gnssgo.gui.i18n import language_manager, tr
from gnssgo.gui.qt import require_qt
from gnssgo.provider_info import provider_info
from gnssgo.regional_sources import RegionalSource, default_regional_source_registry
from gnssgo.stations import StationCatalog

QtCore, _QtGui, QtWidgets = require_qt()

_ROLE_KIND = int(QtCore.Qt.UserRole) + 10
_ROLE_NETWORK = int(QtCore.Qt.UserRole) + 11
_ROLE_SOURCE = int(QtCore.Qt.UserRole) + 12
_ROLE_BASE_LABEL = int(QtCore.Qt.UserRole) + 13


def _check_value(state) -> int:
    value = getattr(state, "value", state)
    return int(value)

_CONTINENTS = (
    "Africa",
    "Antarctica",
    "Asia",
    "Europe",
    "Latin America",
    "North America",
    "Oceania",
)

# Sources that are intentionally catalog/link integrations rather than an
# in-app RINEX transport.  Everything else shown by this release has an
# automatic transport path (HTTP/FTP/SFTP/browser automation) inside GNSS Go.
_MANUAL_SOURCE_IDS = {
    "europe_apos",
    "sirgas_argentina",
    "sirgas_bolivia",
    "sirgas_colombia",
    "sirgas_ecuador",
    "sirgas_peru",
    "sirgas_costa_rica",
    "sirgas_panama",
    "china_cmonoc",
    "taiwan_gdms",
    "mongolia_monpos",
    "singapore_sirent",
    "southafrica_trignet",
}
_DIRECT_BADGE = "✅"
_MANUAL_BADGE = "🌐"


def _source_access_badge(source_id: str) -> tuple[str, str]:
    if source_id in _MANUAL_SOURCE_IDS:
        return _MANUAL_BADGE, tr("Open the official source to download")
    return _DIRECT_BADGE, tr("Direct download in the app")

_NETWORK_CONTINENT = {
    "australia": "Oceania",
    "new_zealand": "Oceania",
    "europe": "Europe",
    "france": "Europe",
    "spain": "Europe",
    "netherlands": "Europe",
    "portugal": "Europe",
    "austria": "Europe",
    "italy": "Europe",
    "poland": "Europe",
    "romania": "Europe",
    "united_kingdom": "Europe",
    "sweden": "Europe",
    "finland": "Europe",
    "switzerland": "Europe",
    "sirgas": "Latin America",
    "argentina": "Latin America",
    "brazil": "Latin America",
    "mexico": "Latin America",
    "canada": "North America",
    "united_states": "North America",
    "north_america": "North America",
    "japan": "Asia",
    "china": "Asia",
    "taiwan": "Asia",
    "hong_kong": "Asia",
    "mongolia": "Asia",
    "korea": "Asia",
    "singapore": "Asia",
    "south_africa": "Africa",
}


class DataNetworkFilter(QtWidgets.QWidget):
    """Unified Global / Regional hierarchical network filter.

    The GUI intentionally exposes only two top-level groups. Regional sources are
    arranged as continent -> country/region -> source (only when a country has
    multiple sources). Countries that have no integrated regional source are not
    shown merely because an IGS station happens to exist there.
    """

    changed = QtCore.Signal()

    def __init__(self, parent=None, *, catalog: StationCatalog | None = None) -> None:
        super().__init__(parent)
        self.registry = default_data_network_registry()
        self.source_registry = default_regional_source_registry()
        self.catalog = catalog or StationCatalog()
        self._syncing = False
        self._selection_memory: dict[str, bool] = {"network:igs": True}
        self._leaf_items: dict[str, QtWidgets.QTreeWidgetItem] = {}
        self._continent_items: dict[str, QtWidgets.QTreeWidgetItem] = {}

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        top = QtWidgets.QHBoxLayout()
        self.summary = QtWidgets.QLabel("IGS")
        self.summary.setObjectName("mapPanelSummary")
        self.summary.setProperty("_i18n_dynamic", True)
        self.select_all = QtWidgets.QPushButton(tr("Select All"))
        self.select_all.setObjectName("SecondaryButton")
        self.select_all.clicked.connect(self._toggle_all)
        top.addWidget(self.summary, 1)
        top.addWidget(self.select_all)
        layout.addLayout(top)

        tools = QtWidgets.QHBoxLayout()
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText(tr("Search..."))
        self.search.textChanged.connect(self._apply_search)
        self.expand_all = QtWidgets.QPushButton(tr("Expand All"))
        self.collapse_all = QtWidgets.QPushButton(tr("Collapse All"))
        self.expand_all.setObjectName("SecondaryButton")
        self.collapse_all.setObjectName("SecondaryButton")
        self.expand_all.clicked.connect(self.tree_expand_all)
        self.collapse_all.clicked.connect(self.tree_collapse_all)
        tools.addWidget(self.search, 1)
        tools.addWidget(self.expand_all)
        tools.addWidget(self.collapse_all)
        layout.addLayout(tools)

        self.access_legend = QtWidgets.QLabel(
            f"{_DIRECT_BADGE} {tr('Direct download in the app')}    "
            f"{_MANUAL_BADGE} {tr('Open the official source to download')}"
        )
        self.access_legend.setObjectName("mapHintLabel")
        self.access_legend.setWordWrap(True)
        layout.addWidget(self.access_legend)

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setAnimated(False)
        self.tree.setIndentation(18)
        self.tree.itemChanged.connect(self._item_changed)
        self.tree.setMinimumHeight(430)
        layout.addWidget(self.tree, 1)

        self.availability_frame = QtWidgets.QFrame()
        self.availability_frame.setObjectName("AvailabilityPanel")
        availability_layout = QtWidgets.QVBoxLayout(self.availability_frame)
        availability_layout.setContentsMargins(8, 8, 8, 8)
        availability_layout.setSpacing(4)
        self.availability_title = QtWidgets.QLabel(tr("Data availability"))
        self.availability_title.setObjectName("SectionTitle")
        self.availability_text = QtWidgets.QTextBrowser()
        self.availability_text.setOpenExternalLinks(True)
        self.availability_text.setReadOnly(True)
        self.availability_text.setObjectName("mapHintLabel")
        self.availability_text.setMinimumHeight(210)
        self.availability_text.setMaximumHeight(520)
        self.availability_text.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        availability_layout.addWidget(self.availability_title)
        availability_layout.addWidget(self.availability_text, 1)
        layout.addWidget(self.availability_frame, 0)

        self._rebuild()
        language_manager.changed.connect(lambda _language: self._rebuild(preserve=True))

    # ------------------------------------------------------------------ public
    def selected_ids(self) -> list[str]:
        networks: set[str] = set()
        for item in self._leaf_items.values():
            if item.checkState(0) != QtCore.Qt.Checked:
                continue
            network = str(item.data(0, _ROLE_NETWORK) or "")
            if network:
                networks.add(network)
        return sorted(networks)

    def selected_source_ids(self) -> list[str] | None:
        sources = sorted(
            str(item.data(0, _ROLE_SOURCE))
            for item in self._leaf_items.values()
            if item.checkState(0) == QtCore.Qt.Checked and item.data(0, _ROLE_SOURCE)
        )
        return sources or None

    def selected_continents(self) -> list[str]:
        leaves = list(self._leaf_items.values())
        if leaves and all(item.checkState(0) == QtCore.Qt.Checked for item in leaves):
            # Select All means literally every station: expose all continent tags
            # so global IGS stations are not spatially reduced to regional-source
            # countries only.
            return list(_CONTINENTS)
        return sorted(
            continent
            for continent, item in self._continent_items.items()
            if item.checkState(0) == QtCore.Qt.Checked
        )

    def refresh_catalog_metadata(self) -> None:
        # Rebuild so providers that were confirmed empty disappear from the
        # country tree immediately, while preserving the user's checked sources.
        self._rebuild(preserve=True)

    def tree_expand_all(self) -> None:
        self.tree.expandAll()

    def tree_collapse_all(self) -> None:
        self.tree.collapseAll()
        # Keep the two top-level groups visible/open enough to discover the tree.
        for index in range(self.tree.topLevelItemCount()):
            self.tree.topLevelItem(index).setExpanded(True)

    # ----------------------------------------------------------------- taxonomy
    def _source_continent(self, source: RegionalSource) -> str:
        return _NETWORK_CONTINENT.get(source.data_network, "")

    def _source_country(self, source: RegionalSource) -> str:
        if source.data_network == "europe":
            if source.id == "europe_epn":
                return "Europe-wide"
            return source.name.split(" · ", 1)[0]
        if source.data_network == "sirgas":
            return source.name.split(" · ", 1)[0]
        if source.data_network == "australia":
            return "Australia"
        if source.data_network == "canada":
            return "Canada"
        try:
            return self.registry.get(source.data_network).name
        except Exception:
            return source.data_network.replace("_", " ").title()

    def _taxonomy(self) -> dict[str, dict[str, list[RegionalSource]]]:
        result: dict[str, dict[str, list[RegionalSource]]] = {
            continent: defaultdict(list) for continent in _CONTINENTS
        }
        sources_all = list(self.source_registry.all())
        counts = self.catalog.regional_source_counts([source.id for source in sources_all])
        for source in sources_all:
            continent = self._source_continent(source)
            if not continent:
                continue
            count = counts.get(source.id, 0)
            record = self.catalog.metadata_record(source.provider)
            # Do not clutter the tree with placeholder countries that have no
            # regional station catalog.  A real station-metadata provider remains
            # visible before its first refresh so the user can trigger loading.
            provider_has_catalog = False
            try:
                provider_has_catalog = bool(self.registry.get(source.data_network).providers and source.provider in self.registry.get(source.data_network).providers)
            except Exception:
                pass
            status = str((record or {}).get("status") or "").lower()
            keep_new_zealand = source.id == "newzealand_geonet"
            keep_canada = source.id in {"cacs_ca", "chain_ca"}
            # Europe is intentionally compact: only countries/sources with actual
            # station rows in the local catalog are shown.  This removes long EPOS
            # placeholder lists that have no usable/map-visible data.
            if source.data_network == "europe" and count <= 0:
                continue
            if (
                count <= 0
                and not keep_new_zealand
                and not keep_canada
                and record is not None
                and status in {"success", "empty"}
            ):
                continue
            if count <= 0 and record is None and not keep_new_zealand and not keep_canada:
                # Keep known catalog-capable sources used by this release; hide
                # purely placeholder/manual countries that otherwise have no map
                # stations and would look like an empty standalone country.
                if source.provider not in {
                    "geonet_jp", "gdms_tw", "cmonoc_cn", "noaa_ncn", "kasi_kr", "ngii_kr", "ga",
                    "sirgas_rbmc_br", "sirgas_cl", "rgna_mx", "sirgas_uy", "cacs_ca", "chain_ca",
                    "epn", "rgp_fr", "gref_de", "redgae_es", "nsgi_nl", "apos_at", "renep_pt", "satref_hk",
                }:
                    continue
            country = self._source_country(source)
            result[continent][country].append(source)
        return result

    # ------------------------------------------------------------------- building
    def _snapshot_selection(self) -> None:
        for key, item in self._leaf_items.items():
            self._selection_memory[key] = item.checkState(0) == QtCore.Qt.Checked

    def _rebuild(self, *, preserve: bool = False) -> None:
        if preserve:
            self._snapshot_selection()
        self._syncing = True
        self.tree.blockSignals(True)
        self.tree.clear()
        self._leaf_items.clear()
        self._continent_items.clear()

        global_item = self._branch_item(tr("Global"), kind="group")
        self.tree.addTopLevelItem(global_item)
        igs = self._leaf_item(
            tr("IGS"),
            network="igs",
            source=None,
            key="network:igs",
            default_checked=True,
        )
        global_item.addChild(igs)
        global_item.setExpanded(True)

        regional_item = self._branch_item(tr("Regional"), kind="group")
        self.tree.addTopLevelItem(regional_item)
        taxonomy = self._taxonomy()
        for continent in _CONTINENTS:
            continent_item = self._branch_item(tr(continent), kind="continent")
            continent_item.setData(0, _ROLE_BASE_LABEL, continent)
            self._continent_items[continent] = continent_item
            regional_item.addChild(continent_item)
            countries = taxonomy.get(continent, {})
            for country in sorted(countries, key=str.casefold):
                sources = sorted(countries[country], key=lambda value: value.name.casefold())
                if len(sources) == 1:
                    source = sources[0]
                    item = self._leaf_item(
                        tr(country),
                        network=source.data_network,
                        source=source.id,
                        key=f"source:{source.id}",
                        default_checked=False,
                    )
                    item.setData(0, _ROLE_BASE_LABEL, country)
                    continent_item.addChild(item)
                else:
                    country_item = self._branch_item(tr(country), kind="country")
                    country_item.setData(0, _ROLE_BASE_LABEL, country)
                    continent_item.addChild(country_item)
                    for source in sources:
                        child = self._leaf_item(
                            tr(source.name),
                            network=source.data_network,
                            source=source.id,
                            key=f"source:{source.id}",
                            default_checked=False,
                        )
                        child.setData(0, _ROLE_BASE_LABEL, source.name)
                        country_item.addChild(child)
            if continent_item.childCount() == 0:
                # Africa/Antarctica intentionally stay as simple, non-expandable
                # continent rows.  Checking one acts as a spatial IGS filter.
                try:
                    continent_item.setChildIndicatorPolicy(
                        QtWidgets.QTreeWidgetItem.ChildIndicatorPolicy.DontShowIndicatorWhenChildless
                    )
                except AttributeError:
                    pass
                continent_item.setToolTip(
                    0,
                    tr("No integrated regional source yet; select this continent to filter IGS stations."),
                )

        regional_item.setExpanded(True)
        self.tree.blockSignals(False)
        self._syncing = False
        self._refresh_leaf_labels_and_tooltips()
        self._refresh_parent_states()
        self._apply_search(self.search.text())
        self._update_summary()

    def _branch_item(self, label: str, *, kind: str) -> QtWidgets.QTreeWidgetItem:
        item = QtWidgets.QTreeWidgetItem([label])
        item.setData(0, _ROLE_KIND, kind)
        item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
        item.setCheckState(0, QtCore.Qt.Unchecked)
        return item

    def _leaf_item(
        self,
        label: str,
        *,
        network: str,
        source: str | None,
        key: str,
        default_checked: bool,
    ) -> QtWidgets.QTreeWidgetItem:
        item = QtWidgets.QTreeWidgetItem([label])
        item.setData(0, _ROLE_KIND, "leaf")
        item.setData(0, _ROLE_NETWORK, network)
        item.setData(0, _ROLE_SOURCE, source or "")
        item.setData(0, _ROLE_BASE_LABEL, label)
        item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
        checked = self._selection_memory.get(key, default_checked)
        item.setCheckState(0, QtCore.Qt.Checked if checked else QtCore.Qt.Unchecked)
        self._leaf_items[key] = item
        return item

    # --------------------------------------------------------------- check states
    def _descendant_leaves(self, item: QtWidgets.QTreeWidgetItem):
        if item.data(0, _ROLE_KIND) == "leaf":
            yield item
            return
        for index in range(item.childCount()):
            yield from self._descendant_leaves(item.child(index))

    def _item_changed(self, item: QtWidgets.QTreeWidgetItem, _column: int) -> None:
        if self._syncing:
            return
        self._syncing = True
        self.tree.blockSignals(True)
        try:
            if item.data(0, _ROLE_KIND) != "leaf":
                checked = item.checkState(0) != QtCore.Qt.Unchecked
                for leaf in self._descendant_leaves(item):
                    leaf.setCheckState(0, QtCore.Qt.Checked if checked else QtCore.Qt.Unchecked)
                # A childless continent (currently Africa/Antarctica) has no
                # regional provider leaf to select.  Keep IGS enabled so the row
                # immediately shows the IGS stations geographically inside it.
                if item.data(0, _ROLE_KIND) == "continent" and item.childCount() == 0 and checked:
                    igs_item = self._leaf_items.get("network:igs")
                    if igs_item is not None:
                        igs_item.setCheckState(0, QtCore.Qt.Checked)
            self._refresh_parent_states()
            self._snapshot_selection()
        finally:
            self.tree.blockSignals(False)
            self._syncing = False
        self._update_summary()
        self.changed.emit()

    def _refresh_parent_states(self) -> None:
        def update(item: QtWidgets.QTreeWidgetItem) -> int:
            if item.data(0, _ROLE_KIND) == "leaf":
                return _check_value(item.checkState(0))
            states = [update(item.child(i)) for i in range(item.childCount())]
            states = [value for value in states if value >= 0]
            if not states:
                return -1
            if all(value == _check_value(QtCore.Qt.Checked) for value in states):
                state = QtCore.Qt.Checked
            elif all(value == _check_value(QtCore.Qt.Unchecked) for value in states):
                state = QtCore.Qt.Unchecked
            else:
                state = QtCore.Qt.PartiallyChecked
            item.setCheckState(0, state)
            return _check_value(state)

        for index in range(self.tree.topLevelItemCount()):
            update(self.tree.topLevelItem(index))

    def _toggle_all(self) -> None:
        selectable = list(self._leaf_items.values())
        all_checked = bool(selectable) and all(
            item.checkState(0) == QtCore.Qt.Checked for item in selectable
        )
        target = QtCore.Qt.Unchecked if all_checked else QtCore.Qt.Checked
        self._syncing = True
        self.tree.blockSignals(True)
        try:
            for item in selectable:
                item.setCheckState(0, target)
            self._refresh_parent_states()
            self._snapshot_selection()
        finally:
            self.tree.blockSignals(False)
            self._syncing = False
        self._update_summary()
        self.changed.emit()

    # --------------------------------------------------------------------- search
    def _apply_search(self, value: str) -> None:
        needle = value.strip().casefold()

        def apply(item: QtWidgets.QTreeWidgetItem) -> bool:
            own = needle in item.text(0).casefold() if needle else True
            child_match = False
            for index in range(item.childCount()):
                if apply(item.child(index)):
                    child_match = True
            visible = own or child_match
            item.setHidden(not visible)
            if needle and child_match:
                item.setExpanded(True)
            return visible

        for index in range(self.tree.topLevelItemCount()):
            apply(self.tree.topLevelItem(index))

    # --------------------------------------------------------------- labels/counts
    def _refresh_leaf_labels_and_tooltips(self) -> None:
        sources = [
            source_id
            for source_id in (str(item.data(0, _ROLE_SOURCE) or "") for item in self._leaf_items.values())
            if source_id
        ]
        counts = self.catalog.regional_source_counts(sources) if sources else {}
        for key, item in self._leaf_items.items():
            source_id = str(item.data(0, _ROLE_SOURCE) or "")
            if not source_id:
                network = self.registry.get(str(item.data(0, _ROLE_NETWORK)))
                base = str(item.data(0, _ROLE_BASE_LABEL) or network.name)
                count = self.catalog.data_network_count(network.id)
                label = tr(base)
                if count:
                    label = f"{label}   [{count}]"
                if network.id == "igs":
                    label = f"{label}   {_DIRECT_BADGE}"
                item.setText(0, label)
                item.setToolTip(0, self._network_tooltip(network))
                continue
            source = self.source_registry.get(source_id)
            count = counts.get(source_id, 0)
            base = str(item.data(0, _ROLE_BASE_LABEL) or source.name)
            label = tr(base)
            if count:
                label = f"{label}   [{count}]"
            badge, access_tip = _source_access_badge(source_id)
            label = f"{label}   {badge}"
            item.setText(0, label)
            record = self.catalog.metadata_record(source.provider)
            status = str((record or {}).get("status") or "not loaded")
            item.setToolTip(
                0,
                f"{tr('Source')}: {tr(source.name)}\n"
                f"{tr('Provider')}: {source.provider}\n"
                f"{tr('Stations')}: {count}\n"
                f"{tr('Access')}: {badge} {access_tip}\n"
                f"{tr('Status')}: {status}",
            )

    def _update_summary(self) -> None:
        networks = self.selected_ids()
        sources = self.selected_source_ids() or []
        if not networks:
            self.summary.setText(tr("No networks"))
        elif networks == ["igs"]:
            self.summary.setText("IGS")
        elif len(sources) == 1:
            self.summary.setText(tr(self.source_registry.get(sources[0]).name))
        else:
            self.summary.setText(
                tr("{count} sources selected").format(count=len(sources) + (1 if "igs" in networks else 0))
            )
        all_checked = bool(self._leaf_items) and all(
            item.checkState(0) == QtCore.Qt.Checked for item in self._leaf_items.values()
        )
        self.select_all.setText(tr("Select None") if all_checked else tr("Select All"))
        self._update_availability()

    def _update_availability(self) -> None:
        sources = self.selected_source_ids() or []
        networks = self.selected_ids()
        disclaimer = tr(
            "Third-party data remain subject to each provider's copyright, license, citation and access rules."
        )

        if not sources and networks == ["igs"]:
            self.availability_text.setHtml(
                f"<p><b>IGS</b> · {tr('Automatic download')}</p>"
                f"<p><a href='https://igs.org/data-access/'>https://igs.org/data-access/</a></p>"
                f"<hr><small>{disclaimer}</small>"
            )
            return
        if not sources:
            self.availability_text.setHtml(
                f"<p>{tr('Select a regional country/source to view data-access details.')}</p>"
                f"<hr><small>{disclaimer}</small>"
            )
            return

        special = {
            "japan_geonet": tr("Japan GEONET: Terras browser download; Auto uses GRJE / RINEX 3.02."),
            "korea_kasi": tr("Korea KASI/KVN: anonymous FTP automatic download."),
            "korea_national": tr("Korea National GNSS Data Center: automatic 30 s daily ZIP download through the public web session."),
            "taiwan_gdms": tr("Taiwan, China GDMS: GNSS stations are mapped; official download requires registration/login."),
            "china_cmonoc": tr("China CMONOC: station catalog and official source link are provided."),
            "sirgas_chile": tr("Chile CSN: 1 s daily RINEX through browser automation."),
            "sirgas_mexico": tr("Mexico RGNA: official INEGI SFTP; proxy may be needed for TCP 22."),
            "sirgas_uruguay": tr("Uruguay REGNA-ROU: current-year FTP; historical SFTP."),
        }
        blocks: list[str] = []
        if "igs" in networks:
            blocks.append(
                f"<p><b>IGS</b><br>{tr('Automatic download')}<br>"
                f"<a href='https://igs.org/data-access/'>{tr('Official source')}</a></p>"
            )
        for source_id in sources:
            source = self.source_registry.get(source_id)
            info = provider_info(source.provider)
            note = special.get(source_id)
            if note is None:
                try:
                    level = str(self.registry.get(source.data_network).automation_level)
                except Exception:
                    level = ""
                if "auth" in level:
                    note = tr("Registration/login required.")
                elif "browser" in level or "interactive" in level:
                    note = tr("Official web interaction/browser automation required.")
                elif "manual" in level:
                    note = tr("Station catalog available; no stable direct download endpoint is assumed.")
                else:
                    note = tr("Automatic download")
            official = info.url or ""
            if official:
                blocks.append(
                    f"<p><b>{tr(source.name)}</b><br>{note}<br>"
                    f"<a href='{official}'>{tr('Official source')}</a></p>"
                )
            else:
                blocks.append(f"<p><b>{tr(source.name)}</b><br>{note}</p>")
        blocks.append(f"<hr><small>{disclaimer}</small>")
        self.availability_text.setHtml("".join(blocks))

    def _network_tooltip(self, network: DataNetwork) -> str:
        merged = self.catalog.data_network_count(network.id)
        return "\n".join(
            [
                f"{tr('Status')}: {network.status}",
                f"{tr('Merged unique stations')}: {merged}",
            ]
        )
