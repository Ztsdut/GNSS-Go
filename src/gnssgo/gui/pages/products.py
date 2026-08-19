from __future__ import annotations

from gnssgo.gui.i18n import language_manager, tr
from gnssgo.gui.models.tasks import GuiTaskType
from gnssgo.gui.pages.base import CorePage
from gnssgo.gui.qt import require_qt
from gnssgo.gui.widgets.date_range import DateRangeWidget
from gnssgo.gui.widgets.provider_selector import ProviderSelector
from gnssgo.models import ProductSystem, ProductType
from gnssgo.products import AnalysisCenterRegistry, ProductPresetRegistry

QtCore, _QtGui, QtWidgets = require_qt()


_PRODUCT_LABELS = {
    ProductType.ORBIT.value: "Orbit (SP3)",
    ProductType.CLOCK.value: "Clock (CLK)",
    ProductType.ERP.value: "Earth rotation (ERP)",
    ProductType.BIAS.value: "Bias (BIA/BSX)",
    ProductType.IONEX.value: "Ionosphere (IONEX)",
    ProductType.SINEX.value: "SINEX",
    ProductType.ANTEX.value: "ANTEX",
}


class ProductsPage(CorePage):
    def __init__(self, core, task_service, parent=None) -> None:
        super().__init__(core, task_service, parent)
        self._applying_quick_setup = False
        self._resolution_combos: dict[str, QtWidgets.QComboBox] = {}
        self._resolution_memory: dict[str, str | None] = {}

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)

        self.title = QtWidgets.QLabel(tr("Download Products"))
        self.title.setObjectName("PageTitle")
        self.subtitle = QtWidgets.QLabel(
            "Choose product types first. Each product then uses its own available resolution; "
            "when more than one resolution is available you can choose it explicitly."
        )
        self.subtitle.setObjectName("PageSubtitle")
        self.subtitle.setWordWrap(True)
        layout.addWidget(self.title)
        layout.addWidget(self.subtitle)

        split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.split = split
        split.setChildrenCollapsible(False)

        request_card = QtWidgets.QFrame()
        request_card.setObjectName("CardWidget")
        request_layout = QtWidgets.QVBoxLayout(request_card)
        request_layout.setContentsMargins(14, 12, 14, 12)
        request_layout.setSpacing(10)
        heading = QtWidgets.QLabel(tr("Product Request"))
        heading.setObjectName("SectionTitle")
        request_layout.addWidget(heading)

        self.dates = DateRangeWidget()
        self.dates.start.dateChanged.connect(self._request_changed)
        self.dates.end.dateChanged.connect(self._request_changed)
        request_layout.addWidget(self.dates)

        product_label = QtWidgets.QLabel(tr("Products"))
        product_label.setObjectName("SectionTitle")
        request_layout.addWidget(product_label)
        self.product_checks: dict[str, QtWidgets.QCheckBox] = {}
        product_grid = QtWidgets.QGridLayout()
        product_grid.setHorizontalSpacing(14)
        product_grid.setVerticalSpacing(7)
        for index, product_type in enumerate(ProductType):
            check = QtWidgets.QCheckBox(
                tr(_PRODUCT_LABELS.get(product_type.value, product_type.value))
            )
            check.setToolTip(f"Request {product_type.value} products")
            check.stateChanged.connect(self._product_selection_changed)
            self.product_checks[product_type.value] = check
            product_grid.addWidget(check, index // 2, index % 2)
        request_layout.addLayout(product_grid)

        self.resolution_title = QtWidgets.QLabel(tr("Temporal resolution"))
        self.resolution_title.setObjectName("SectionTitle")
        request_layout.addWidget(self.resolution_title)
        self.resolution_hint = QtWidgets.QLabel(
            "Only temporal resolution is shown here. A single available interval is selected "
            "automatically; when multiple temporal resolutions are available, you can choose one."
        )
        self.resolution_hint.setObjectName("PageSubtitle")
        self.resolution_hint.setWordWrap(True)
        request_layout.addWidget(self.resolution_hint)
        self.resolution_table = QtWidgets.QTableWidget(0, 2)
        self.resolution_table.setHorizontalHeaderLabels(["Product", "Temporal resolution"])
        self.resolution_table.verticalHeader().setVisible(False)
        self.resolution_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.resolution_table.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self.resolution_table.setMinimumHeight(118)
        rheader = self.resolution_table.horizontalHeader()
        rheader.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        rheader.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        request_layout.addWidget(self.resolution_table)

        form = QtWidgets.QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)

        self.quick_setup = QtWidgets.QComboBox()
        self.quick_setup.addItem("Custom", "none")
        self.quick_setup.addItem("PPP bundle", "ppp")
        self.quick_setup.addItem("Ionosphere bundle", "ionosphere")
        self.quick_setup.setToolTip(
            "Quick setup only checks a useful group of product types. "
            "It does not lock the selections; you can change them afterwards."
        )
        self.quick_setup.currentIndexChanged.connect(self._apply_quick_setup)

        self.tier = QtWidgets.QComboBox()
        self.tier.addItems(["auto", "final", "rapid", "ultra"])
        self.center = QtWidgets.QComboBox()
        self.center.addItem("auto")
        self.center.addItems([center.code for center in AnalysisCenterRegistry().centers()])
        self.system = QtWidgets.QComboBox()
        self.system.addItems([item.value for item in ProductSystem])
        self.system.setCurrentText("auto")
        self.provider = ProviderSelector(self.core.providers_for("products"))

        for widget in (self.tier, self.center, self.system):
            widget.currentTextChanged.connect(self._request_changed)
        self.provider.currentTextChanged.connect(self._request_changed)

        form.addRow("Quick setup", self.quick_setup)
        form.addRow("Tier", self.tier)
        form.addRow("Analysis Center", self.center)
        form.addRow("System", self.system)
        form.addRow("Provider", self.provider)

        self.output = QtWidgets.QLineEdit()
        self.output.setPlaceholderText(tr("Default archive location"))
        output_row = QtWidgets.QWidget()
        output_layout = QtWidgets.QHBoxLayout(output_row)
        output_layout.setContentsMargins(0, 0, 0, 0)
        output_layout.setSpacing(8)
        output_layout.addWidget(self.output, 1)
        self.output_browse = QtWidgets.QPushButton(tr("Browse…"))
        self.output_browse.setObjectName("SecondaryButton")
        self.output_browse.setMinimumWidth(96)
        self.output_browse.clicked.connect(self._browse_output)
        output_layout.addWidget(self.output_browse)
        form.addRow("Output", output_row)

        request_layout.addLayout(form)
        request_layout.addStretch(1)
        split.addWidget(request_card)

        bundle_card = QtWidgets.QFrame()
        bundle_card.setObjectName("CardWidget")
        bundle_layout = QtWidgets.QVBoxLayout(bundle_card)
        bundle_layout.setContentsMargins(14, 12, 14, 12)
        bundle_title = QtWidgets.QLabel(tr("Product Summary"))
        bundle_title.setObjectName("SectionTitle")
        bundle_layout.addWidget(bundle_title)
        self.bundle_summary = QtWidgets.QLabel("")
        self.bundle_summary.setObjectName("StatusBadge")
        self.bundle_summary.setProperty("_i18n_dynamic", True)
        bundle_layout.addWidget(self.bundle_summary)
        self.bundle_list = QtWidgets.QListWidget()
        bundle_layout.addWidget(self.bundle_list, 1)
        self.source_note = QtWidgets.QLabel(
            "ANTEX: current IGS antenna model is available from the public IGS Central "
            "Bureau files.\nSINEX: date-indexed combined solutions are searched in "
            "product archives; the current IGS station SINEX is also available from "
            "the IGS Central Bureau."
        )
        self.source_note.setObjectName("PageSubtitle")
        self.source_note.setProperty("_i18n_dynamic", True)
        self.source_note.setWordWrap(True)
        bundle_layout.addWidget(self.source_note)
        split.addWidget(bundle_card)
        split.setSizes([620, 440])
        layout.addWidget(split, 1)

        buttons = QtWidgets.QHBoxLayout()
        self.review = QtWidgets.QPushButton(tr("Review Plan"))
        self.review.setObjectName("PrimaryButton")
        self.review.clicked.connect(self.submit)
        buttons.addStretch(1)
        buttons.addWidget(self.review)
        layout.addLayout(buttons)

        language_manager.changed.connect(self._language_changed)
        self._refresh_resolution_rows()
        self._update_bundle_preview()
        self._update_source_note()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # Side-by-side product cards are comfortable on wide windows.  Stack
        # them vertically on narrower pages so date fields, forms and translated
        # labels never overlap or disappear behind the summary card.
        orientation = QtCore.Qt.Vertical if self.width() < 980 else QtCore.Qt.Horizontal
        if self.split.orientation() != orientation:
            self.split.setOrientation(orientation)
            if orientation == QtCore.Qt.Horizontal:
                self.split.setSizes([620, 440])
            else:
                self.split.setSizes([560, 360])

    def _language_changed(self, _language: str) -> None:
        self._remember_resolutions()
        self._refresh_resolution_rows()
        self._update_bundle_preview()
        self._update_source_note()

    def selected_products(self) -> list[str]:
        return [name for name, check in self.product_checks.items() if check.isChecked()]

    def sampling_by_product(self) -> dict[str, str | None]:
        return {
            product: combo.currentData()
            for product, combo in self._resolution_combos.items()
        }

    def submit(self) -> None:
        products = self.selected_products()
        if not products:
            QtWidgets.QMessageBox.information(
                self,
                "Select products",
                "Select at least one product type before reviewing the plan.",
            )
            return
        start, end = self.dates.values()
        request = {
            "product_types": products,
            "start": start,
            "end": end,
            "provider": self.provider.value(),
            "center": self.center.currentText(),
            "tier": self.tier.currentText(),
            "system": self.system.currentText(),
            "sampling_by_product": self.sampling_by_product(),
            "output": self.output.text() or None,
        }
        self.run_plan(
            name="Product download",
            task_type=GuiTaskType.PRODUCT,
            request=request,
            planner=lambda: self.core.plan_products(**request),
        )

    def _apply_quick_setup(self, _index: int) -> None:
        preset = str(self.quick_setup.currentData() or "none")
        if preset == "none":
            self._request_changed()
            return
        selected = {item.value for item in ProductPresetRegistry().get(preset).product_types}
        self._applying_quick_setup = True
        try:
            for name, check in self.product_checks.items():
                check.blockSignals(True)
                check.setChecked(name in selected)
                check.blockSignals(False)
        finally:
            self._applying_quick_setup = False
        self._request_changed()

    def _product_selection_changed(self) -> None:
        if not self._applying_quick_setup and self.quick_setup.currentData() != "none":
            self.quick_setup.blockSignals(True)
            self.quick_setup.setCurrentIndex(0)
            self.quick_setup.blockSignals(False)
        self._request_changed()

    def _request_changed(self, *_args) -> None:
        self._remember_resolutions()
        self._refresh_resolution_rows()
        self._update_bundle_preview()

    def _remember_resolutions(self) -> None:
        for product, combo in self._resolution_combos.items():
            self._resolution_memory[product] = combo.currentData()

    def _refresh_resolution_rows(self) -> None:
        products = self.selected_products()
        self.resolution_table.setRowCount(len(products))
        self._resolution_combos = {}
        start, _end = self.dates.values()
        for row, product in enumerate(products):
            label = QtWidgets.QTableWidgetItem(tr(_PRODUCT_LABELS.get(product, product)))
            self.resolution_table.setItem(row, 0, label)
            combo = QtWidgets.QComboBox()
            combo.setMinimumWidth(190)
            options = self.core.product_interval_options(
                product_type=product,
                day=start,
                center=self.center.currentText(),
                tier=self.tier.currentText(),
                system=self.system.currentText(),
            )
            if not options:
                text = (
                    "Current / source-defined"
                    if product in {"antex", "sinex"}
                    else "Automatic / source-defined"
                )
                combo.addItem(tr(text), None)
                combo.setEnabled(False)
            else:
                for option_label, value in options:
                    combo.addItem(tr(option_label), value)
                remembered = self._resolution_memory.get(product)
                index = combo.findData(remembered)
                combo.setCurrentIndex(index if index >= 0 else 0)
                combo.setEnabled(len(options) > 1)
                if len(options) == 1:
                    combo.setToolTip(
                        tr("Only one temporal resolution is available for the current selection.")
                    )
                else:
                    combo.setToolTip(
                        tr("Multiple temporal resolutions are available; choose the one you need.")
                    )
            combo.currentIndexChanged.connect(self._resolution_changed)
            self._resolution_combos[product] = combo
            self.resolution_table.setCellWidget(row, 1, combo)
        self.resolution_table.setVisible(bool(products))
        self.resolution_hint.setVisible(bool(products))

    def _resolution_changed(self, *_args) -> None:
        self._remember_resolutions()
        self._update_bundle_preview()

    def _update_bundle_preview(self, *_args) -> None:
        products = self.selected_products()
        tier = self.tier.currentText() if hasattr(self, "tier") else "auto"
        self.bundle_summary.setText(tr("{count} products").format(count=len(products)) + f" · {tier}")
        self.bundle_list.clear()
        if not products:
            self.bundle_list.addItem(tr("Select one or more product types"))
            return
        for product in products:
            combo = self._resolution_combos.get(product)
            interval = combo.currentText() if combo is not None else "source-defined"
            label = tr(_PRODUCT_LABELS.get(product, product))
            center = self.center.currentText()
            system = self.system.currentText()
            self.bundle_list.addItem(f"{label} · {interval} · {center} · {system}")

    def _update_source_note(self) -> None:
        if language_manager.language == "zh":
            self.source_note.setText(
                "ANTEX：从 IGS 官方公开文件获取当前 IGS20 天线模型。\n"
                "SINEX：按日期从精密产品归档中查找组合解；"
                "当前 IGS 测站 SINEX 也可从 IGS 官方公开文件获取。"
            )
        else:
            self.source_note.setText(
                "ANTEX: current IGS20 antenna model from the public IGS files.\n"
                "SINEX: date-indexed combined solutions from product archives; "
                "the current IGS station SINEX is also available from IGS."
            )

    def _browse_output(self) -> None:
        directory = QtWidgets.QFileDialog.getExistingDirectory(
            self, tr("Output directory")
        )
        if directory:
            self.output.setText(directory)
