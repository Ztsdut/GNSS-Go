from __future__ import annotations

from gnssgo.gui.qt import require_qt
from gnssgo.provider_info import provider_info

QtCore, _QtGui, QtWidgets = require_qt()


class ProviderSelector(QtWidgets.QComboBox):
    def __init__(self, providers: list[str] | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(150)
        self.setSizeAdjustPolicy(
            QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.setMinimumContentsLength(12)
        self.currentIndexChanged.connect(self._sync_tooltip)
        self.set_providers(providers or ["auto"])

    def set_providers(self, providers: list[str]) -> None:
        current = self.currentText()
        self.clear()
        for provider_id in providers:
            self.addItem(provider_id)
            if provider_id == "auto":
                tooltip = "Automatic provider selection and fallback"
            else:
                info = provider_info(provider_id)
                tooltip = info.name
                if info.access:
                    tooltip += f" · {info.access}"
                if info.url:
                    tooltip += f"\n{info.url}"
            self.setItemData(self.count() - 1, tooltip, QtCore.Qt.ToolTipRole)
        if current in providers:
            self.setCurrentText(current)
        self._sync_tooltip()

    def value(self) -> str:
        return self.currentText()

    def _sync_tooltip(self) -> None:
        index = self.currentIndex()
        if index < 0:
            self.setToolTip("")
            return
        self.setToolTip(str(self.itemData(index, QtCore.Qt.ToolTipRole) or ""))
