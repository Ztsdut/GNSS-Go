from __future__ import annotations

from gnssgo.gui.qt import require_qt

_QtCore, _QtGui, QtWidgets = require_qt()


class StatusBadge(QtWidgets.QLabel):
    def __init__(self, text: str = "READY", parent=None) -> None:
        super().__init__(text, parent)
        self.setObjectName("statusBadge")
        self.setMargin(4)


def primary_button(text: str, parent=None) -> QtWidgets.QPushButton:
    button = QtWidgets.QPushButton(text, parent)
    button.setObjectName("primaryButton")
    return button
