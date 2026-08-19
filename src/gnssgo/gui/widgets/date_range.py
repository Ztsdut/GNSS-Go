from __future__ import annotations

from gnssgo.gui.i18n import language_manager, tr

from gnssgo.gui.qt import require_qt
from gnssgo.utils.dates import datetime_to_doy

QtCore, _QtGui, QtWidgets = require_qt()


class DateRangeWidget(QtWidgets.QWidget):
    """Compact date range that remains readable when the parent narrows."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._layout = QtWidgets.QGridLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setHorizontalSpacing(8)
        self._layout.setVerticalSpacing(4)
        layout = self._layout

        self.start_label = QtWidgets.QLabel(tr("Start"))
        self.end_label = QtWidgets.QLabel(tr("End"))
        self.start = QtWidgets.QDateEdit(QtCore.QDate.currentDate())
        self.end = QtWidgets.QDateEdit(QtCore.QDate.currentDate())
        for edit in (self.start, self.end):
            edit.setCalendarPopup(True)
            edit.setDisplayFormat("yyyy-MM-dd")
            # Keep date selection compact: the user only needs the calendar date,
            # not weekday names (Mon/Tue/星期一…) or ISO week numbers.
            calendar = edit.calendarWidget()
            try:
                calendar.setHorizontalHeaderFormat(
                    QtWidgets.QCalendarWidget.HorizontalHeaderFormat.NoHorizontalHeader
                )
                calendar.setVerticalHeaderFormat(
                    QtWidgets.QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader
                )
            except AttributeError:
                calendar.setHorizontalHeaderFormat(QtWidgets.QCalendarWidget.NoHorizontalHeader)
                calendar.setVerticalHeaderFormat(QtWidgets.QCalendarWidget.NoVerticalHeader)
            edit.setMinimumWidth(132)
            edit.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

        self.doy_label = QtWidgets.QLabel()
        self.doy_label.setSizePolicy(QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Fixed)

        layout.addWidget(self.start_label, 0, 0)
        layout.addWidget(self.start, 0, 1)
        layout.addWidget(self.end_label, 0, 2)
        layout.addWidget(self.end, 0, 3)
        layout.addWidget(self.doy_label, 0, 4)
        layout.setColumnStretch(1, 1)
        layout.setColumnStretch(3, 1)

        self.start.dateChanged.connect(self._update_doy)
        self.end.dateChanged.connect(self._update_doy)
        language_manager.changed.connect(self._language_changed)
        self._language_changed(language_manager.language)
        self._update_doy()


    def _language_changed(self, language: str) -> None:
        if str(language).lower().startswith("zh"):
            locale = QtCore.QLocale(QtCore.QLocale.Chinese, QtCore.QLocale.China)
        else:
            locale = QtCore.QLocale(QtCore.QLocale.English, QtCore.QLocale.UnitedStates)
        for edit in (self.start, self.end):
            edit.setLocale(locale)
            try:
                edit.calendarWidget().setLocale(locale)
            except Exception:
                pass

    def set_compact_mode(self, compact: bool = True) -> None:
        """Switch between a single-row range and a narrow vertical layout."""
        layout = self._layout
        for widget in (self.start_label, self.start, self.end_label, self.end, self.doy_label):
            layout.removeWidget(widget)
        if compact:
            self.start.setMinimumWidth(0)
            self.end.setMinimumWidth(0)
            layout.addWidget(self.start_label, 0, 0)
            layout.addWidget(self.start, 1, 0)
            layout.addWidget(self.end_label, 2, 0)
            layout.addWidget(self.end, 3, 0)
            layout.addWidget(self.doy_label, 4, 0)
            layout.setColumnStretch(0, 1)
        else:
            self.start.setMinimumWidth(132)
            self.end.setMinimumWidth(132)
            layout.addWidget(self.start_label, 0, 0)
            layout.addWidget(self.start, 0, 1)
            layout.addWidget(self.end_label, 0, 2)
            layout.addWidget(self.end, 0, 3)
            layout.addWidget(self.doy_label, 0, 4)
            layout.setColumnStretch(1, 1)
            layout.setColumnStretch(3, 1)

    def values(self) -> tuple[str, str]:
        return self.start.date().toString("yyyy-MM-dd"), self.end.date().toString("yyyy-MM-dd")

    def _update_doy(self) -> None:
        py_date = self.start.date().toPython()
        self.doy_label.setText(f"DOY {datetime_to_doy(py_date):03d}")
