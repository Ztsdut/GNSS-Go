from __future__ import annotations

from pathlib import Path

from gnssgo.gui.i18n import language_manager, retranslate_widget_tree, tr
from gnssgo.gui.pages.home import HomePage
from gnssgo.gui.pages.navigation import NavigationPage
from gnssgo.gui.pages.observations import ObservationsPage
from gnssgo.gui.pages.products import ProductsPage
from gnssgo.gui.pages.settings import SettingsPage
from gnssgo.gui.pages.tasks import TasksPage
from gnssgo.gui.qt import require_qt
from gnssgo.gui.services.core_service import CoreService
from gnssgo.gui.services.task_service import TaskService

_QtCore, _QtGui, QtWidgets = require_qt()


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, core: CoreService | None = None, parent=None) -> None:
        super().__init__(parent)
        self.core = core or CoreService()
        self.task_service = TaskService(self.core)
        # DownloadManager emits a terminal TASK_* event and the worker result is
        # then folded into the same GuiTask.  Both updates are useful internally,
        # but only one desktop completion dialog should be shown per terminal run.
        self._terminal_notifications: dict[str, str] = {}
        self.setWindowTitle("GNSS Go")
        self.setMinimumSize(1280, 820)

        root = QtWidgets.QWidget()
        self.setCentralWidget(root)
        layout = QtWidgets.QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.nav = QtWidgets.QListWidget()
        self.nav.setFixedWidth(150)
        self.nav.setObjectName("MainNavigation")
        self.stack = QtWidgets.QStackedWidget()
        layout.addWidget(self.nav)
        layout.addWidget(self.stack, 1)

        home = HomePage(self.core, self.task_service)
        self._pages = [
            ("Home", home),
            ("Observations", ObservationsPage(self.core, self.task_service)),
            ("Navigation", NavigationPage(self.core, self.task_service)),
            ("Products", ProductsPage(self.core, self.task_service)),
            ("Downloads", TasksPage(self.core, self.task_service)),
            ("Settings", SettingsPage(self.core, self.task_service)),
        ]
        icon_dir = Path(__file__).resolve().parent / "resources" / "icons" / "nav"
        nav_icons = {
            "Home": "home.png",
            "Observations": "globe.png",
            "Navigation": "satellite.png",
            "Products": "database.png",
            "Downloads": "download.png",
            "Settings": "settings.png",
        }
        self.nav.setIconSize(_QtCore.QSize(22, 22))
        for name, page in self._pages:
            item = QtWidgets.QListWidgetItem(tr(name))
            item.setData(_QtCore.Qt.UserRole, name)
            icon_name = nav_icons.get(name)
            if icon_name:
                icon_path = icon_dir / icon_name
                if icon_path.exists():
                    item.setIcon(_QtGui.QIcon(str(icon_path)))
            self.nav.addItem(item)
            self.stack.addWidget(page)

        home.openPage.connect(self._open_page)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(1)
        self.statusBar().showMessage(tr("Ready"))
        self.task_service.subscribe(self._task_status_changed)
        language_manager.changed.connect(self._retranslate_all)
        retranslate_widget_tree(self)

    def _open_page(self, name: str) -> None:
        aliases = {"Stations": "Observations", "Tasks": "Downloads"}
        target = aliases.get(name, name)
        for row in range(self.nav.count()):
            item = self.nav.item(row)
            if item.data(_QtCore.Qt.UserRole) == target:
                self.nav.setCurrentRow(row)
                return

    def _retranslate_all(self, _language: str) -> None:
        for row in range(self.nav.count()):
            item = self.nav.item(row)
            source = str(item.data(_QtCore.Qt.UserRole) or item.text())
            item.setText(tr(source))
        retranslate_widget_tree(self)

    def _task_status_changed(self, task) -> None:
        terminal = task.state.value in {"completed", "partial", "failed", "cancelled"}
        if terminal:
            message = task.message or task.state.value
            self.statusBar().showMessage(f"{tr(task.name)}: {message}", 12000)
            marker = task.state.value
            already_shown = self._terminal_notifications.get(task.id) == marker
            if not already_shown:
                self._terminal_notifications[task.id] = marker
                QtWidgets.QApplication.alert(self, 2500)
                # No-data planning already has its own explicit dialog; do not
                # follow it with a misleading second "Download completed" box.
                if not (marker == "completed" and task.total_files == 0):
                    self._show_download_notification(task)
            return

        # A retry moves the task back to a non-terminal state; allow one fresh
        # terminal notification when that new run finishes.
        self._terminal_notifications.pop(task.id, None)

        # The status bar is a lightweight global indicator only.  Continuous
        # byte/file progress belongs in Downloads, where the plan-level bar is
        # large enough to be useful and remains visible after the transfer ends.
        if task.total_files:
            done = task.completed_files + task.failed_files
            message = f"{tr(task.name)} · {tr(task.state.value.title())} · {done}/{task.total_files} {tr('Files')}"
        else:
            message = f"{tr(task.name)} · {tr(task.state.value.title())}"
        self.statusBar().showMessage(message)

    def _show_download_notification(self, task) -> None:
        if task.state.value == "completed":
            icon = QtWidgets.QMessageBox.Information
            title = tr("Download completed")
        elif task.state.value == "partial":
            icon = QtWidgets.QMessageBox.Warning
            title = tr("Download partially completed")
        elif task.state.value == "failed":
            icon = QtWidgets.QMessageBox.Critical
            title = tr("Download failed")
        else:
            return
        box = QtWidgets.QMessageBox(icon, title, task.message or task.state.value, parent=self)
        box.setAttribute(_QtCore.Qt.WA_DeleteOnClose, True)
        box.setStandardButtons(QtWidgets.QMessageBox.Ok)
        box.show()
