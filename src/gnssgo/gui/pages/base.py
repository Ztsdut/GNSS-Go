from __future__ import annotations

import importlib
import importlib.util
import subprocess
import sys
from datetime import date

from gnssgo.download.events import CancellationToken
from gnssgo.gui.i18n import language_manager
from gnssgo.gui.models.tasks import GuiTaskType
from gnssgo.gui.qt import require_qt
from gnssgo.gui.widgets.download_plan import DownloadPlanDialog
from gnssgo.gui.workers.base import FunctionWorker

QtCore, _QtGui, QtWidgets = require_qt()


class CorePage(QtWidgets.QWidget):
    def __init__(self, core, task_service, parent=None) -> None:
        super().__init__(parent)
        self.core = core
        self.task_service = task_service
        self.thread_pool = QtCore.QThreadPool.globalInstance()
        # Planning must never queue behind station-catalog refreshes or downloads.
        # A dedicated pool also keeps QRunnable/Signal ownership deterministic on
        # PySide6/Windows, where a local QRunnable can otherwise be collected while
        # the global pool is busy.
        self.plan_thread_pool = QtCore.QThreadPool(self)
        self.plan_thread_pool.setMaxThreadCount(2)
        self._plan_workers: set[object] = set()

    def run_plan(self, *, name: str, task_type: GuiTaskType, request: dict, planner) -> None:
        # Planning is only a preview.  Do not create a download-queue task until
        # the user explicitly presses Download in the plan dialog.  Previously a
        # READY row appeared in Downloads as soon as Review Plan was clicked,
        # which made the preview dialog look redundant and suggested that a
        # transfer had already been queued.
        token = CancellationToken()

        def guarded_planner():
            token.raise_if_cancelled()
            result = planner()
            token.raise_if_cancelled()
            return result

        def on_started() -> None:
            if token.cancelled:
                return
            window = self.window()
            status = getattr(window, "statusBar", None)
            if callable(status):
                status().showMessage("Planning download…")

        def on_result(plan) -> None:
            if token.cancelled:
                return
            window = self.window()
            status = getattr(window, "statusBar", None)
            if callable(status):
                status().showMessage("Plan ready", 3000)
            self._show_plan(name=name, task_type=task_type, request=request, plan=plan)

        def on_error(message: str) -> None:
            if token.cancelled:
                return
            window = self.window()
            status = getattr(window, "statusBar", None)
            if callable(status):
                status().showMessage("Planning failed", 5000)
            box = QtWidgets.QMessageBox(self)
            box.setIcon(QtWidgets.QMessageBox.Critical)
            box.setWindowTitle("Planning failed")
            box.setText(message)
            box.setStandardButtons(QtWidgets.QMessageBox.Ok)
            box.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
            box.open()

        worker = FunctionWorker(guarded_planner)
        # Keep a Python reference until the worker's finished signal is delivered.
        self._plan_workers.add(worker)
        worker.signals.started.connect(on_started)
        worker.signals.result.connect(on_result)
        worker.signals.error.connect(on_error)
        worker.signals.finished.connect(lambda w=worker: self._plan_workers.discard(w))
        self.plan_thread_pool.start(worker)

    def _show_plan(self, *, name: str, task_type: GuiTaskType, request: dict, plan) -> None:
        # A valid provider response can still contain zero files (for example a
        # GA station/day with no archived RINEX).  Treat that as a completed
        # planning result, not a READY download with zero files.
        if (
            task_type == GuiTaskType.OBS
            and not plan.remote_files
            and not plan.existing_files
        ):
            self._show_no_data_message(plan)
            return

        dialog = DownloadPlanDialog(plan, self)
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            # Only now does the plan become a real download task and enter the
            # Downloads page. Closing the preview leaves no queue entry behind.
            if not self._ensure_download_dependencies(plan):
                return
            task = self.task_service.create_task(
                name=name, task_type=task_type, request=request
            )
            self.task_service.attach_plan(task, plan)
            self.run_download(task, plan)

    def _ensure_download_dependencies(self, plan) -> bool:
        """Ensure protocol/proxy extras exist before a download task is queued."""
        planned_remotes = [
            getattr(task, "remote", None) for task in getattr(plan, "download_tasks", [])
        ]
        if not planned_remotes:
            planned_remotes = list(getattr(plan, "remote_files", []))
        schemes = {
            str(getattr(remote, "url", "")).split(":", 1)[0].lower()
            for remote in planned_remotes
            if remote is not None and ":" in str(getattr(remote, "url", ""))
        }

        requirements: list[tuple[str, str]] = []
        if "sftp" in schemes and importlib.util.find_spec("paramiko") is None:
            requirements.append(("paramiko>=3.5", "paramiko"))

        network = self.core.client.settings.network
        if str(getattr(network, "mode", "")).lower() == "socks5":
            if (
                "sftp" in schemes
                and bool(getattr(network, "use_for_sftp", True))
                and importlib.util.find_spec("socks") is None
            ):
                requirements.append(("PySocks>=1.7.1", "socks"))
            if (
                schemes.intersection({"http", "https"})
                and bool(getattr(network, "use_for_http", True))
                and importlib.util.find_spec("socksio") is None
            ):
                requirements.append(("socksio>=1.0.0", "socksio"))

        seen: set[str] = set()
        requirements = [
            item for item in requirements
            if not (item[0] in seen or seen.add(item[0]))
        ]
        if not requirements:
            return True

        packages = [item[0] for item in requirements]
        zh = language_manager.language == "zh"
        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Warning)
        box.setWindowTitle("缺少网络支持组件" if zh else "Network support is missing")
        package_text = "\n".join(f"• {item}" for item in packages)
        box.setText(
            (
                "当前下载/代理设置需要以下 Python 组件，但当前环境尚未安装：\n\n"
                f"{package_text}\n\n"
                "是否现在自动安装？安装完成后会继续下载。"
            )
            if zh
            else (
                "The current download/proxy settings require these Python packages, "
                "but they are missing from this environment:\n\n"
                f"{package_text}\n\nInstall them now and continue?"
            )
        )
        box.setStandardButtons(QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel)
        box.setDefaultButton(QtWidgets.QMessageBox.Yes)
        if box.exec() != QtWidgets.QMessageBox.Yes:
            return False

        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.CursorShape.WaitCursor)
        try:
            process = subprocess.run(
                [sys.executable, "-m", "pip", "install", *packages],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=240,
                check=False,
            )
        except Exception as exc:
            process = None
            install_error = f"{exc.__class__.__name__}: {exc}"
        else:
            install_error = (process.stderr or process.stdout or "").strip()
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

        importlib.invalidate_caches()
        installed = process is not None and process.returncode == 0
        if installed:
            for _package, module in requirements:
                try:
                    importlib.import_module(module)
                except Exception as exc:
                    installed = False
                    install_error = f"{module}: {exc.__class__.__name__}: {exc}"
                    break

        if installed:
            ok = QtWidgets.QMessageBox(self)
            ok.setIcon(QtWidgets.QMessageBox.Information)
            ok.setWindowTitle("安装完成" if zh else "Installation complete")
            ok.setText(
                "网络支持组件已安装，点击 OK 后开始下载。"
                if zh
                else "Network support packages are installed. Click OK to start the download."
            )
            ok.exec()
            return True

        detail = install_error[-1800:] if install_error else "Unknown pip error"
        fail = QtWidgets.QMessageBox(self)
        fail.setIcon(QtWidgets.QMessageBox.Critical)
        fail.setWindowTitle("安装失败" if zh else "Installation failed")
        fail.setText(
            (
                "无法在当前 Python 环境自动安装所需网络组件，因此没有创建下载任务。\n\n"
                f"{detail}"
            )
            if zh
            else (
                "The required network packages could not be installed, so the "
                f"download task was not created.\n\n{detail}"
            )
        )
        fail.exec()
        return False

    def _show_no_data_message(self, plan) -> None:
        missing = list(plan.unavailable or plan.missing)
        shown = missing[:20]
        remaining = max(0, len(missing) - len(shown))
        if language_manager.language == "zh":
            title = "未找到数据"
            intro = "所选数据源在指定站点和日期没有找到可下载的观测文件。"
            details = "\n".join(f"• {item}" for item in shown)
            more = f"\n… 另有 {remaining} 项" if remaining else ""
            hint = "请尝试更换日期、站点或数据源。"
        else:
            title = "No data found"
            intro = "No downloadable observation files were found for the selected provider, stations and dates."
            details = "\n".join(f"• {item}" for item in shown)
            more = f"\n… and {remaining} more" if remaining else ""
            hint = "Try another date, station, or provider."
        current_day_note = ""
        request = plan.requests[0] if plan.requests else None
        date_range = getattr(request, "date_range", None)
        if date_range is not None and getattr(date_range, "end", None) is not None:
            if date_range.end >= date.today():
                if language_manager.language == "zh":
                    current_day_note = (
                        "\n\n提示：你选择了当天或未来日期。01D 日文件通常要等 UTC "
                        "当天结束并由数据中心生成后才会发布；请优先尝试前一天。"
                    )
                else:
                    current_day_note = (
                        "\n\nNote: the selection includes today or a future date. 01D daily files "
                        "are commonly published only after the UTC day has finished and the "
                        "data center has generated the archive; try the previous day first."
                    )
        body = f"{intro}\n\n{details}{more}\n\n{hint}{current_day_note}" if details else f"{intro}\n\n{hint}{current_day_note}"
        # Avoid a nested modal event loop inside a worker-result signal.
        # On some PySide6/Windows builds this can race with QRunnable cleanup
        # and crash immediately after the user clicks OK.
        previous = getattr(self, "_no_data_message_box", None)
        if previous is not None:
            try:
                previous.close()
            except RuntimeError:
                pass
        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Information)
        box.setWindowTitle(title)
        box.setText(body)
        box.setStandardButtons(QtWidgets.QMessageBox.Ok)
        box.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
        self._no_data_message_box = box

        def clear_box(*_args) -> None:
            if getattr(self, "_no_data_message_box", None) is box:
                self._no_data_message_box = None

        box.destroyed.connect(clear_box)
        QtCore.QTimer.singleShot(0, box.open)

    def run_download(self, task, plan) -> None:
        self.task_service.mark_downloading(task)
        token = CancellationToken()
        task.cancellation_token = token
        worker = FunctionWorker(
            self.core.execute_plan,
            plan,
            cancellation_token=token,
            event_callback=lambda event: worker.signals.progress.emit(event),
        )
        worker.signals.progress.connect(
            lambda event: self.task_service.handle_download_event(task, event)
        )
        worker.signals.result.connect(
            lambda results: self.task_service.complete_from_results(task, results)
        )
        worker.signals.error.connect(lambda message: self.task_service.fail(task, message))
        self.thread_pool.start(worker)
