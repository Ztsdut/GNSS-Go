from __future__ import annotations

import sys
from pathlib import Path

from gnssgo.gui.qt import QtUnavailableError, require_qt


def main(argv: list[str] | None = None) -> int:
    try:
        _qtcore, QtGui, QtWidgets = require_qt()
    except QtUnavailableError as exc:
        print(exc)
        return 1

    from gnssgo import GNSSGo
    from gnssgo.config import load_user_settings
    from gnssgo.gui.i18n import set_language
    from gnssgo.gui.main_window import MainWindow
    from gnssgo.gui.services.core_service import CoreService
    from gnssgo.gui.styles.tokens import apply_app_theme

    app = QtWidgets.QApplication(argv or sys.argv)
    app.setApplicationName("GNSS Go")
    app.setOrganizationName("GNSS Go")
    app.setOrganizationDomain("gnssgo.org")
    icon_path = Path(__file__).resolve().parent / "resources" / "icons" / "gnss_go.png"
    if icon_path.exists():
        app.setWindowIcon(QtGui.QIcon(str(icon_path)))

    settings = load_user_settings()
    # Probe OpenStreetMap reachability before the main window is built.  Follow
    # the user's direct/system/custom proxy settings, prefer OSM when reachable,
    # and fall back to the bundled offline map without delaying startup for long.
    from gnssgo.gui.network_probe import openstreetmap_available

    app.setProperty(
        "osmAvailable",
        openstreetmap_available(timeout=2.2, network_settings=settings.network),
    )
    set_language(settings.appearance.language)
    apply_app_theme(app, settings.appearance.theme)
    core = CoreService(GNSSGo(settings=settings))
    window = MainWindow(core=core)
    window.show()
    return app.exec()
