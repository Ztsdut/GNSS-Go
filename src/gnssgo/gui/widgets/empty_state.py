from __future__ import annotations

from gnssgo.gui.qt import require_qt

_QtCore, _QtGui, QtWidgets = require_qt()


class EmptyState(QtWidgets.QLabel):
    def __init__(self, text: str, parent=None) -> None:
        super().__init__(text, parent)
        self.setAlignment(QtWidgets.Qt.AlignCenter)
