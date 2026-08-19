from __future__ import annotations

from gnssgo.gui.qt import require_qt

_QtCore, _QtGui, QtWidgets = require_qt()


class LogPanel(QtWidgets.QPlainTextEdit):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumBlockCount(1000)

    def append_message(self, message: str) -> None:
        self.appendPlainText(message)
