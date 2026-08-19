from __future__ import annotations

import platform
from pathlib import Path

from gnssgo.config import NetworkSettings, save_user_settings
from gnssgo.data_networks import AutomationLevel, default_data_network_registry
from gnssgo.gui.i18n import language_manager, set_language, tr
from gnssgo.gui.qt import require_qt
from gnssgo.gui.services.settings_service import SettingsService
from gnssgo.gui.workers.base import FunctionWorker
from gnssgo.network import ProxyConfig, test_network_settings
from gnssgo.gui.styles.tokens import apply_app_theme
from gnssgo.provider_info import provider_info
from gnssgo.version import __version__

_QtCore, _QtGui, QtWidgets = require_qt()


class SettingsPage(QtWidgets.QWidget):
    def __init__(self, core, _task_service, parent=None) -> None:
        super().__init__(parent)
        self.core = core
        self.service = SettingsService(core.client.settings)
        settings = core.client.settings
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)

        title = QtWidgets.QLabel(tr("Settings"))
        title.setObjectName("PageTitle")
        subtitle = QtWidgets.QLabel(tr("Adjust persistent settings, data sources, and appearance."))
        subtitle.setObjectName("PageSubtitle")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        tabs = QtWidgets.QTabWidget()
        general = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(general)
        form.addRow("GNSS Go", QtWidgets.QLabel(__version__))
        form.addRow("Python", QtWidgets.QLabel(platform.python_version()))
        self.data_root = QtWidgets.QLineEdit(str(Path(settings.archive.root)))
        self.catalog_path = QtWidgets.QLineEdit(str(settings.stations.catalog_path or ""))
        self.theme = QtWidgets.QComboBox()
        self.theme.addItem("system", "system")
        self.theme.addItem("light", "light")
        self.theme.addItem("dark", "dark")
        theme_index = self.theme.findData(settings.appearance.theme)
        self.theme.setCurrentIndex(theme_index if theme_index >= 0 else 0)
        self.language = QtWidgets.QComboBox()
        self.language.addItem("English", "en")
        self.language.addItem("Chinese", "zh")
        language_index = self.language.findData(settings.appearance.language)
        self.language.setCurrentIndex(language_index if language_index >= 0 else 0)
        form.addRow("Data root", self.data_root)
        form.addRow("Station catalog", self.catalog_path)
        form.addRow("Theme", self.theme)
        form.addRow("Language", self.language)
        tabs.addTab(general, "General")

        download = QtWidgets.QWidget()
        dform = QtWidgets.QFormLayout(download)
        self.workers = QtWidgets.QSpinBox()
        self.workers.setRange(1, 64)
        self.workers.setValue(settings.download.workers)
        self.per_provider_workers = QtWidgets.QSpinBox()
        self.per_provider_workers.setRange(1, 32)
        self.per_provider_workers.setValue(settings.download.per_provider_workers)
        self.retries = QtWidgets.QSpinBox()
        self.retries.setRange(0, 20)
        self.retries.setValue(settings.download.retries)
        self.resume = QtWidgets.QCheckBox()
        self.resume.setChecked(settings.download.resume)
        self.auto_extract = QtWidgets.QCheckBox()
        self.auto_extract.setChecked(settings.archive.auto_extract)
        self.auto_extract.setToolTip(
            "Off: keep the downloaded .gz/.Z archive as the final file. "
            "On: automatically decompress/restore it after download."
        )
        self.keep_compressed = QtWidgets.QCheckBox()
        self.keep_compressed.setChecked(settings.archive.keep_compressed)
        self.keep_compressed.setToolTip(
            "When automatic decompression is enabled, also keep the original archive."
        )
        self.keep_compressed.setEnabled(self.auto_extract.isChecked())
        self.auto_extract.toggled.connect(self.keep_compressed.setEnabled)
        dform.addRow("Workers", self.workers)
        dform.addRow("Per-provider workers", self.per_provider_workers)
        dform.addRow("Retries", self.retries)
        dform.addRow("Resume", self.resume)
        dform.addRow("Automatically decompress", self.auto_extract)
        dform.addRow("Keep compressed", self.keep_compressed)
        tabs.addTab(download, "Download")

        providers = QtWidgets.QWidget()
        provider_layout = QtWidgets.QVBoxLayout(providers)
        provider_layout.setContentsMargins(8, 8, 8, 8)
        provider_layout.setSpacing(10)
        provider_note = QtWidgets.QLabel(tr(
            "Provider priority controls automatic global fallback. "
            "Regional networks use their own source. Select a provider to reveal "
            "its official source link."
        ))
        provider_note.setWordWrap(True)
        provider_note.setObjectName("PageSubtitle")
        provider_layout.addWidget(provider_note)

        self.provider_split = QtWidgets.QSplitter(_QtCore.Qt.Horizontal)
        self.provider_split.setChildrenCollapsible(False)
        self.provider_priority = PriorityListWidget(self.service.provider_priority())
        self.product_provider_priority = PriorityListWidget(
            self.service.product_provider_priority()
        )
        self.provider_priority.set_title("Global OBS / NAV mirrors")
        self.product_provider_priority.set_title("Product mirrors")
        self.provider_split.addWidget(self.provider_priority)
        self.provider_split.addWidget(self.product_provider_priority)
        self.provider_split.setSizes([500, 500])
        provider_layout.addWidget(self.provider_split, 1)

        regional_title = QtWidgets.QLabel(tr("Regional data sources"))
        regional_title.setObjectName("SectionTitle")
        provider_layout.addWidget(regional_title)
        regional_hint = QtWidgets.QLabel(tr(
            "Regional sources use a compact one-row view. Europe and South America are shown as multi-country networks; hover Coverage to see the full country list."
        ))
        regional_hint.setWordWrap(True)
        regional_hint.setObjectName("mapHintLabel")
        provider_layout.addWidget(regional_hint)
        self.regional_sources = RegionalSourcesTable()
        provider_layout.addWidget(self.regional_sources, 1)
        source_disclaimer = QtWidgets.QLabel(tr(
            "GNSS Go is an independent data-access client. It does not claim ownership of third-party data or websites. "
            "Copyright, licenses, terms of use, citation requirements and access restrictions remain with the original providers; "
            "please follow each official source's rules."
        ))
        source_disclaimer.setWordWrap(True)
        source_disclaimer.setObjectName("PageSubtitle")
        provider_layout.addWidget(source_disclaimer)
        tabs.addTab(providers, tr("Providers"))

        network = QtWidgets.QWidget()
        nlayout = QtWidgets.QVBoxLayout(network)
        nlayout.setContentsMargins(8, 8, 8, 8)
        nlayout.setSpacing(10)

        note = QtWidgets.QLabel(tr(
            "Configure one connection route for GNSS downloads. HTTP and SOCKS5 "
            "can be used for HTTP/HTTPS; SFTP can tunnel through HTTP CONNECT or "
            "SOCKS5. In System mode, HTTP/HTTPS uses the OS/environment proxy; "
            "when an HTTP system proxy is discoverable, SFTP can also use it as "
            "an HTTP CONNECT tunnel."
        ))
        note.setWordWrap(True)
        note.setObjectName("PageSubtitle")
        nlayout.addWidget(note)

        proxy_frame = QtWidgets.QFrame()
        proxy_frame.setObjectName("CardWidget")
        nform = QtWidgets.QFormLayout(proxy_frame)
        current_proxy = ProxyConfig.from_settings(settings.network)

        self.proxy_mode = QtWidgets.QComboBox()
        self.proxy_mode.addItem("Direct (no proxy)", "direct")
        self.proxy_mode.addItem("System proxy", "system")
        self.proxy_mode.addItem("HTTP proxy", "http")
        self.proxy_mode.addItem("SOCKS5 proxy", "socks5")
        mode_index = self.proxy_mode.findData(current_proxy.mode)
        self.proxy_mode.setCurrentIndex(mode_index if mode_index >= 0 else 1)

        self.proxy_host = QtWidgets.QLineEdit(current_proxy.host)
        self.proxy_host.setPlaceholderText("127.0.0.1")
        self.proxy_port = QtWidgets.QSpinBox()
        self.proxy_port.setRange(0, 65535)
        self.proxy_port.setSpecialValueText("Auto")
        self.proxy_port.setValue(current_proxy.port)
        self.proxy_username = QtWidgets.QLineEdit(current_proxy.username)
        self.proxy_username.setPlaceholderText(tr("Optional"))
        self.proxy_password = QtWidgets.QLineEdit(current_proxy.password)
        self.proxy_password.setEchoMode(QtWidgets.QLineEdit.Password)
        self.proxy_password.setPlaceholderText(tr("Optional"))
        self.proxy_password.setToolTip(
            tr("Proxy credentials are stored in the local GNSS Go settings.json file.")
        )

        self.proxy_http = QtWidgets.QCheckBox(tr("HTTP / HTTPS"))
        self.proxy_http.setChecked(current_proxy.use_for_http)
        self.proxy_sftp = QtWidgets.QCheckBox(tr("SFTP"))
        self.proxy_sftp.setChecked(current_proxy.use_for_sftp)
        self.proxy_ftp = QtWidgets.QCheckBox(tr("FTP"))
        self.proxy_ftp.setChecked(current_proxy.use_for_ftp)
        protocol_row = QtWidgets.QWidget()
        protocol_layout = QtWidgets.QHBoxLayout(protocol_row)
        protocol_layout.setContentsMargins(0, 0, 0, 0)
        protocol_layout.addWidget(self.proxy_http)
        protocol_layout.addWidget(self.proxy_sftp)
        protocol_layout.addWidget(self.proxy_ftp)
        protocol_layout.addStretch(1)

        nform.addRow("Mode", self.proxy_mode)
        nform.addRow("Host", self.proxy_host)
        nform.addRow("Port", self.proxy_port)
        nform.addRow("Username", self.proxy_username)
        nform.addRow("Password", self.proxy_password)
        nform.addRow("Use for", protocol_row)

        self.chromedriver_path = QtWidgets.QLineEdit(settings.network.chromedriver_path)
        self.chromedriver_path.setPlaceholderText(tr("Auto-detect chromedriver.exe"))
        self.chromedriver_path.setToolTip(tr(
            "Optional. Use a standalone ChromeDriver matching the installed Google Chrome. "
            "Leave blank to search GNSSGO_CHROMEDRIVER, PATH, tools/, and drivers/."
        ))
        driver_row = QtWidgets.QWidget()
        driver_layout = QtWidgets.QHBoxLayout(driver_row)
        driver_layout.setContentsMargins(0, 0, 0, 0)
        driver_layout.addWidget(self.chromedriver_path, 1)
        driver_browse = QtWidgets.QPushButton(tr("Browse..."))
        driver_browse.clicked.connect(self._browse_chromedriver)
        driver_layout.addWidget(driver_browse)
        nform.addRow("ChromeDriver", driver_row)
        nlayout.addWidget(proxy_frame)

        test_row = QtWidgets.QHBoxLayout()
        self.proxy_test_button = QtWidgets.QPushButton(tr("Test Connection"))
        self.proxy_test_button.clicked.connect(self._test_network)
        self.proxy_test_status = QtWidgets.QLabel("")
        self.proxy_test_status.setProperty("_i18n_dynamic", True)
        self.proxy_test_status.setWordWrap(True)
        test_row.addWidget(self.proxy_test_button)
        test_row.addWidget(self.proxy_test_status, 1)
        nlayout.addLayout(test_row)

        self.proxy_test_results = QtWidgets.QPlainTextEdit()
        self.proxy_test_results.setReadOnly(True)
        self.proxy_test_results.setMaximumHeight(150)
        self.proxy_test_results.setPlaceholderText(
            "Test results for Chile CSN, Mexico INEGI SFTP and Uruguay IGM will appear here."
        )
        nlayout.addWidget(self.proxy_test_results)
        nlayout.addStretch(1)
        self._network_test_worker = None
        self._network_test_pool = _QtCore.QThreadPool(self)
        self._network_test_pool.setMaxThreadCount(1)
        self.proxy_mode.currentIndexChanged.connect(self._update_proxy_fields)
        self._update_proxy_fields()
        tabs.addTab(network, "Network")
        layout.addWidget(tabs, 1)

        self.status = QtWidgets.QLabel("")
        self.status.setProperty("_i18n_dynamic", True)
        save = QtWidgets.QPushButton(tr("Save"))
        save.setObjectName("PrimaryButton")
        save.clicked.connect(self.save)
        footer = QtWidgets.QHBoxLayout()
        footer.addWidget(self.status)
        footer.addStretch(1)
        footer.addWidget(save)
        layout.addLayout(footer)

    def _network_settings_from_form(self) -> NetworkSettings:
        mode = str(self.proxy_mode.currentData() or "system")
        port = int(self.proxy_port.value())
        if mode == "http" and port == 0:
            port = 8080
        elif mode == "socks5" and port == 0:
            port = 1080
        return NetworkSettings(
            proxy=None,
            mode=mode,
            host=self.proxy_host.text().strip(),
            port=port,
            username=self.proxy_username.text().strip(),
            password=self.proxy_password.text(),
            use_for_http=self.proxy_http.isChecked(),
            use_for_sftp=self.proxy_sftp.isChecked(),
            use_for_ftp=self.proxy_ftp.isChecked(),
            chromedriver_path=self.chromedriver_path.text().strip(),
        )

    def _browse_chromedriver(self) -> None:
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select ChromeDriver",
            self.chromedriver_path.text().strip() or "",
            "ChromeDriver (chromedriver.exe chromedriver);;All files (*)",
        )
        if filename:
            self.chromedriver_path.setText(filename)

    def _update_proxy_fields(self) -> None:
        mode = str(self.proxy_mode.currentData() or "system")
        custom = mode in {"http", "socks5"}
        for widget in (
            self.proxy_host,
            self.proxy_port,
            self.proxy_username,
            self.proxy_password,
        ):
            widget.setEnabled(custom)
        if custom and self.proxy_port.value() == 0:
            self.proxy_port.setValue(1080 if mode == "socks5" else 8080)
        if mode == "system":
            self.proxy_test_status.setText(tr(
                "System mode: HTTP(S) uses the system proxy; SFTP also uses the discovered HTTP proxy as a CONNECT tunnel when available."
            ))
        elif mode == "direct":
            self.proxy_test_status.setText(tr("Direct mode: all protocols connect without a proxy."))
        else:
            self.proxy_test_status.setText("")

    def _test_network(self) -> None:
        try:
            network = self._network_settings_from_form()
            ProxyConfig.from_settings(network).validate()
        except Exception as exc:  # noqa: BLE001 - validation feedback for GUI.
            self.proxy_test_status.setText(str(exc))
            return
        self.proxy_test_button.setEnabled(False)
        self.proxy_test_status.setText(tr("Testing..."))
        self.proxy_test_results.clear()
        worker = FunctionWorker(test_network_settings, network, 8.0)
        self._network_test_worker = worker
        worker.signals.result.connect(self._network_test_finished)
        worker.signals.error.connect(self._network_test_failed)
        worker.signals.finished.connect(self._network_test_cleanup)
        self._network_test_pool.start(worker)

    def _network_test_finished(self, result) -> None:
        lines = [f"{name}: {status}" for name, status in dict(result).items()]
        self.proxy_test_results.setPlainText("\n".join(lines))
        self.proxy_test_status.setText(tr("Connection test completed."))

    def _network_test_failed(self, message: str) -> None:
        self.proxy_test_status.setText(tr("Test failed: {message}").format(message=message))

    def _network_test_cleanup(self) -> None:
        self.proxy_test_button.setEnabled(True)
        self._network_test_worker = None

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        orientation = _QtCore.Qt.Vertical if self.width() < 900 else _QtCore.Qt.Horizontal
        if self.provider_split.orientation() != orientation:
            self.provider_split.setOrientation(orientation)
            self.provider_split.setSizes([420, 420] if orientation == _QtCore.Qt.Horizontal else [260, 260])

    def save(self) -> None:
        settings = self.core.client.settings
        settings.archive.root = Path(self.data_root.text() or "./data")
        settings.archive.auto_extract = self.auto_extract.isChecked()
        settings.archive.keep_compressed = self.keep_compressed.isChecked()
        settings.download.workers = self.workers.value()
        settings.download.per_provider_workers = self.per_provider_workers.value()
        settings.download.retries = self.retries.value()
        settings.download.resume = self.resume.isChecked()
        settings.provider.priority = self.provider_priority.values()
        settings.products.provider_priority = self.product_provider_priority.values()
        network = self._network_settings_from_form()
        settings.network.proxy = None
        settings.network.mode = network.mode
        settings.network.host = network.host
        settings.network.port = network.port
        settings.network.username = network.username
        settings.network.password = network.password
        settings.network.use_for_http = network.use_for_http
        settings.network.use_for_sftp = network.use_for_sftp
        settings.network.use_for_ftp = network.use_for_ftp
        settings.network.chromedriver_path = network.chromedriver_path
        settings.appearance.theme = str(self.theme.currentData() or "system")
        settings.appearance.language = str(self.language.currentData() or "en")
        settings.stations.catalog_path = (
            Path(self.catalog_path.text()) if self.catalog_path.text() else None
        )
        app = QtWidgets.QApplication.instance()
        if app is not None:
            apply_app_theme(app, settings.appearance.theme)
        set_language(settings.appearance.language)
        try:
            path = save_user_settings(settings)
            self.status.setText(tr("Saved to {path}").format(path=path))
        except OSError as exc:
            self.status.setText(tr("Applied, but could not save: {message}").format(message=exc))


class PriorityListWidget(QtWidgets.QFrame):
    def __init__(self, values: list[str], parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("CardWidget")
        layout = QtWidgets.QVBoxLayout(self)

        heading = QtWidgets.QHBoxLayout()
        self.title = QtWidgets.QLabel("")
        self.title.setObjectName("SectionTitle")
        self._source_url = ""
        self.source_link = QtWidgets.QToolButton()
        self.source_link.setText(tr("Official source ↗"))
        self.source_link.setObjectName("LinkButton")
        self.source_link.setAutoRaise(True)
        self.source_link.setVisible(False)
        self.source_link.clicked.connect(self._open_source_link)
        heading.addWidget(self.title)
        heading.addStretch(1)
        heading.addWidget(self.source_link)
        layout.addLayout(heading)

        self.list = QtWidgets.QListWidget()
        self.list.addItems(values)
        self.list.currentRowChanged.connect(self._update_source_link)
        buttons = QtWidgets.QHBoxLayout()
        up = QtWidgets.QPushButton(tr("Move Up"))
        up.setObjectName("SecondaryButton")
        down = QtWidgets.QPushButton(tr("Move Down"))
        down.setObjectName("SecondaryButton")
        up.clicked.connect(lambda: self._move(-1))
        down.clicked.connect(lambda: self._move(1))
        buttons.addWidget(up)
        buttons.addWidget(down)
        buttons.addStretch(1)
        layout.addWidget(self.list)
        layout.addLayout(buttons)
        if self.list.count():
            self.list.setCurrentRow(0)

    def set_title(self, title: str) -> None:
        self.title.setText(title)

    def values(self) -> list[str]:
        return [self.list.item(index).text() for index in range(self.list.count())]

    def _move(self, offset: int) -> None:
        row = self.list.currentRow()
        target = row + offset
        if row < 0 or target < 0 or target >= self.list.count():
            return
        item = self.list.takeItem(row)
        self.list.insertItem(target, item)
        self.list.setCurrentRow(target)

    def _update_source_link(self, row: int) -> None:
        if row < 0 or row >= self.list.count():
            self._source_url = ""
            self.source_link.setVisible(False)
            return
        provider_id = self.list.item(row).text()
        info = provider_info(provider_id)
        self._source_url = info.url
        self.source_link.setVisible(bool(info.url))
        self.source_link.setToolTip(f"Open {info.name}" if info.url else "")

    def _open_source_link(self) -> None:
        if self._source_url:
            _QtGui.QDesktopServices.openUrl(_QtCore.QUrl(self._source_url))


def _regional_summary_rows() -> list[dict]:
    """Compact Settings view: one row per broad network / standalone region.

    Europe and SIRGAS are deliberately collapsed to one row each.  Countries
    are listed separately only when GNSS Go exposes a genuinely additional
    standalone source outside those broad federations.
    """
    registry = default_data_network_registry()

    europe_countries = [
        "Albania", "Austria", "Belgium", "Bosnia and Herzegovina", "Bulgaria",
        "Croatia", "Cyprus", "Czechia", "Denmark", "Estonia", "Finland",
        "France", "Germany", "Greece", "Hungary", "Iceland", "Ireland", "Italy",
        "Latvia", "Lithuania", "Luxembourg", "Malta", "Moldova", "Montenegro",
        "Netherlands", "North Macedonia", "Norway", "Poland", "Portugal",
        "Romania", "Serbia", "Slovakia", "Slovenia", "Spain", "Sweden",
        "Switzerland", "Türkiye", "Ukraine", "United Kingdom",
    ]
    latin_countries = [
        "Argentina", "Bolivia", "Brazil", "Chile", "Colombia", "Costa Rica",
        "Ecuador", "Mexico", "Panama", "Peru", "Uruguay",
    ]

    rows: list[dict] = [
        {
            "network_id": "europe",
            "region": "Europe",
            "source": "EPN / EPOS / national GNSS archives",
            "coverage": europe_countries,
            "coverage_label": "Europe multiple countries",
            "providers": registry.get("europe").providers,
            "url": "https://epncb.oma.be/",
            "access": "Public / federated official archives",
            "level": registry.get("europe").automation_level,
        },
        {
            "network_id": "sirgas",
            "region": "Latin America",
            "source": "SIRGAS national data centres",
            "coverage": latin_countries,
            "coverage_label": "South America multiple countries",
            "providers": registry.get("sirgas").providers,
            "url": "https://sirgas.ipgh.org/en/gnss-network/data-centres/",
            "access": "National official archives / portals",
            "level": registry.get("sirgas").automation_level,
        },
    ]

    # These are either outside the two broad federations above or provide an
    # additional national/regional source not represented by the broad row.
    standalone = [
        "japan", "china", "taiwan", "korea", "hong_kong", "mongolia",
        "singapore", "australia", "new_zealand", "canada", "united_states",
        "north_america", "south_africa", "france", "netherlands",
        "united_kingdom", "brazil",
    ]
    for network_id in standalone:
        try:
            network = registry.get(network_id)
        except Exception:
            continue
        infos = [provider_info(pid) for pid in network.providers]
        names = []
        for info in infos:
            name = info.name or info.id
            if name not in names:
                names.append(name)
        urls = [info.url for info in infos if info.url]
        accesses = []
        for info in infos:
            if info.access and info.access not in accesses:
                accesses.append(info.access)
        coverage = [network.name]
        if network_id == "north_america":
            coverage = ["United States", "Canada"]
        rows.append({
            "network_id": network_id,
            "region": network.name,
            "source": " / ".join(names) or network.name,
            "coverage": coverage,
            "providers": list(network.providers),
            "url": urls[0] if urls else "",
            "all_urls": urls,
            "access": " / ".join(accesses) or "—",
            "level": network.automation_level,
        })
    return rows


class RegionalSourcesTable(QtWidgets.QTableWidget):
    def __init__(self, parent=None) -> None:
        self._rows = _regional_summary_rows()
        super().__init__(len(self._rows), 6, parent)
        self.setHorizontalHeaderLabels([
            tr("Region"), tr("Data source"), tr("Coverage"), tr("Access"),
            tr("Official URL"), tr("GNSS Go"),
        ])
        self.setAlternatingRowColors(True)
        # Compact mode: every source stays on one visual line. Long source names
        # and URLs are elided instead of expanding row height / page width.
        self.setWordWrap(False)
        self.setTextElideMode(_QtCore.Qt.ElideRight)
        self.setHorizontalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        self.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        self.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.verticalHeader().setVisible(False)

        for row, entry in enumerate(self._rows):
            level = entry["level"]
            region_item = QtWidgets.QTableWidgetItem(tr(entry["region"]))
            region_item.setData(_QtCore.Qt.UserRole + 1, entry["region"])
            self.setItem(row, 0, region_item)

            source_item = QtWidgets.QTableWidgetItem(tr(entry["source"]))
            source_item.setData(_QtCore.Qt.UserRole, entry.get("url", ""))
            source_item.setData(_QtCore.Qt.UserRole + 1, entry["source"])
            providers = entry.get("providers", [])
            source_item.setToolTip("\n".join(
                f"{provider_info(pid).name}: {provider_info(pid).url}"
                for pid in providers if provider_info(pid).url
            ))
            self.setItem(row, 1, source_item)

            coverage_names = entry.get("coverage", [])
            coverage_full = ", ".join(tr(name) for name in coverage_names)
            coverage_label = entry.get("coverage_label")
            coverage_text = tr(coverage_label) if coverage_label else coverage_full
            coverage_item = QtWidgets.QTableWidgetItem(coverage_text)
            coverage_item.setData(_QtCore.Qt.UserRole + 2, list(coverage_names))
            coverage_item.setData(_QtCore.Qt.UserRole + 3, coverage_label or "")
            coverage_item.setToolTip(coverage_full)
            self.setItem(row, 2, coverage_item)
            if coverage_label:
                region_item.setToolTip(coverage_full)

            access_item = QtWidgets.QTableWidgetItem(tr(entry.get("access") or "—"))
            access_item.setData(_QtCore.Qt.UserRole + 1, entry.get("access") or "—")
            self.setItem(row, 3, access_item)

            url = entry.get("url", "")
            url_item = QtWidgets.QTableWidgetItem(url or "—")
            url_item.setData(_QtCore.Qt.UserRole, url)
            all_urls = entry.get("all_urls") or [url]
            url_item.setToolTip("\n".join(x for x in all_urls if x) or tr("Not configured"))
            self.setItem(row, 4, url_item)

            integration_source = _integration_label(level)
            integration_item = QtWidgets.QTableWidgetItem(tr(integration_source))
            integration_item.setData(_QtCore.Qt.UserRole + 1, integration_source)
            integration_item.setToolTip(_provider_status_text(level))
            self.setItem(row, 5, integration_item)

        header = self.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(5, QtWidgets.QHeaderView.ResizeToContents)
        self.verticalHeader().setDefaultSectionSize(34)
        self.verticalHeader().setMinimumSectionSize(30)
        # Keep the settings page compact even when many standalone national
        # sources are registered. The table scrolls internally instead.
        self.setMinimumHeight(210)
        self.setMaximumHeight(300)
        self.cellClicked.connect(self._open_provider_source)
        language_manager.changed.connect(self._retranslate_rows)
        self._retranslate_rows()

    def _retranslate_rows(self, _language: str | None = None) -> None:
        self.setHorizontalHeaderLabels([
            tr("Region"), tr("Data source"), tr("Coverage"), tr("Access"),
            tr("Official URL"), tr("GNSS Go"),
        ])
        for row in range(self.rowCount()):
            region = self.item(row, 0)
            if region is not None:
                source = region.data(_QtCore.Qt.UserRole + 1) or region.text()
                region.setText(tr(str(source)))
            source_item = self.item(row, 1)
            if source_item is not None:
                source_label = source_item.data(_QtCore.Qt.UserRole + 1) or source_item.text()
                source_item.setText(tr(str(source_label)))
            coverage = self.item(row, 2)
            if coverage is not None:
                names = coverage.data(_QtCore.Qt.UserRole + 2) or []
                full_text = ", ".join(tr(str(name)) for name in names)
                compact_label = coverage.data(_QtCore.Qt.UserRole + 3) or ""
                coverage.setText(tr(str(compact_label)) if compact_label else full_text)
                coverage.setToolTip(full_text)
                region_item = self.item(row, 0)
                if region_item is not None and compact_label:
                    region_item.setToolTip(full_text)
            for column in (3, 5):
                item = self.item(row, column)
                if item is None:
                    continue
                source = item.data(_QtCore.Qt.UserRole + 1)
                if source is not None:
                    item.setText(tr(str(source)))

    def _open_provider_source(self, row: int, column: int) -> None:
        if column not in {1, 4}:
            return
        item = self.item(row, column)
        if item is None:
            return
        url = str(item.data(_QtCore.Qt.UserRole) or "")
        if url:
            _QtGui.QDesktopServices.openUrl(_QtCore.QUrl(url))


def _provider_automation_level(
    network_level: AutomationLevel, provider_id: str
) -> AutomationLevel:
    overrides = {
        "geonet_jp": AutomationLevel.BROWSER_REQUIRED,
        "osnet_uk": AutomationLevel.AUTH_REQUIRED,
        "monpos_mn": AutomationLevel.AUTH_REQUIRED,
        "ngii_kr": AutomationLevel.FULL,
        "sirent_sg": AutomationLevel.AUTH_REQUIRED,
    }
    return overrides.get(provider_id, network_level)


def _provider_status_text(level: AutomationLevel) -> str:
    return {
        AutomationLevel.FULL: "FULLY_AUTOMATED + LIVE_VERIFIED",
        AutomationLevel.PARTIAL: "PARTIALLY_AUTOMATED + LIVE_VERIFIED",
        AutomationLevel.AUTH_REQUIRED: "AUTH_REQUIRED",
        AutomationLevel.INTERACTIVE_WEB: "INTERACTIVE_WEB",
        AutomationLevel.BROWSER_REQUIRED: "BROWSER_REQUIRED",
        AutomationLevel.MANUAL: "MANUAL",
        AutomationLevel.UNVERIFIED: "IMPLEMENTED_BUT_NOT_LIVE_VERIFIED",
    }[level]


def _short_status(level: AutomationLevel) -> str:
    return {
        AutomationLevel.FULL: "FULL/LIVE",
        AutomationLevel.PARTIAL: "PARTIAL/LIVE",
        AutomationLevel.AUTH_REQUIRED: "AUTH",
        AutomationLevel.INTERACTIVE_WEB: "WEB",
        AutomationLevel.BROWSER_REQUIRED: "BROWSE",
        AutomationLevel.MANUAL: "MANUAL",
        AutomationLevel.UNVERIFIED: "UNVERIFIED",
    }[level]


def _integration_label(level: AutomationLevel) -> str:
    if level == AutomationLevel.FULL:
        return "Ready"
    if level == AutomationLevel.PARTIAL:
        return "Partial"
    if level in {
        AutomationLevel.AUTH_REQUIRED,
        AutomationLevel.INTERACTIVE_WEB,
        AutomationLevel.BROWSER_REQUIRED,
    }:
        return "Open source"
    return "Not live verified"
