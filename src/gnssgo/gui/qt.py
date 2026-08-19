from __future__ import annotations

import os


class QtUnavailableError(RuntimeError):
    pass


def require_qt():
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
    except ImportError as exc:  # pragma: no cover - depends on optional GUI dependency.
        raise QtUnavailableError(
            "PySide6 is required for the GNSS Go desktop GUI. "
            "Install it with: pip install -e \".[dev]\""
        ) from exc
    return QtCore, QtGui, QtWidgets


def require_webengine():
    """Return WebChannel/WebEngine modules when the desktop backend is usable.

    QtWebEngine is deliberately disabled for Qt's ``offscreen`` test platform.  The
    GUI then exercises the native vector-map fallback instead of starting Chromium.
    """

    if os.environ.get("QT_QPA_PLATFORM", "").lower() == "offscreen":
        return None, None, None

    # Chromium prints low-level socket/TLS reset messages directly to stderr
    # (for example net_error -101 while an OSM tile connection is reset).  Those
    # messages are unrelated to GNSS planning/cancellation and confuse desktop
    # users, so keep Chromium console logging at fatal-only unless the user has
    # explicitly configured another log level.
    flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
    if "--log-level=" not in flags:
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
            f"{flags} --log-level=3".strip()
        )

    try:
        from PySide6 import QtWebChannel, QtWebEngineCore, QtWebEngineWidgets
    except ImportError:
        return None, None, None
    return QtWebChannel, QtWebEngineCore, QtWebEngineWidgets
