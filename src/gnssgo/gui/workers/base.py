from __future__ import annotations

from collections.abc import Callable

from gnssgo.gui.qt import require_qt

QtCore, _QtGui, _QtWidgets = require_qt()


class WorkerSignals(QtCore.QObject):
    started = QtCore.Signal()
    result = QtCore.Signal(object)
    progress = QtCore.Signal(object)
    error = QtCore.Signal(str)
    finished = QtCore.Signal()


class FunctionWorker(QtCore.QRunnable):
    def __init__(self, fn: Callable, *args, **kwargs) -> None:
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()

    @QtCore.Slot()
    def run(self) -> None:
        self.signals.started.emit()
        try:
            result = self.fn(*self.args, **self.kwargs)
        except Exception as exc:  # noqa: BLE001 - surfaced to GUI as concise message.
            self.signals.error.emit(str(exc))
        else:
            self.signals.result.emit(result)
        finally:
            self.signals.finished.emit()
