import pytest

from gnssgo.gui.qt import QtUnavailableError, require_qt


def test_gui_qt_dependency_gate() -> None:
    try:
        require_qt()
    except QtUnavailableError:
        pytest.skip("PySide6 is not installed in this environment.")
