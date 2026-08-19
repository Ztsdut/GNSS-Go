from __future__ import annotations

from pathlib import Path

FONT_STACK = '"Segoe UI", "Microsoft YaHei UI", Arial, sans-serif'

LIGHT = {
    "background": "#f3f6fb",
    "surface": "#ffffff",
    "surface_muted": "#eef4fb",
    "border": "#d6e1ee",
    "text": "#17324d",
    "muted": "#6b7b8c",
    "accent": "#2a7be4",
    "accent_hover": "#1f68c3",
    "danger": "#b42318",
    "selection": "#d9e9ff",
    "selection_strong": "#2a7be4",
    "selected_text": "#ffffff",
    "hover": "#eef5ff",
    "disabled": "#8b98a6",
}

DARK = {
    "background": "#121820",
    "surface": "#1b2530",
    "surface_muted": "#243242",
    "border": "#344557",
    "text": "#edf3f8",
    "muted": "#a6b4c3",
    "accent": "#4fa3c7",
    "accent_hover": "#78bddb",
    "danger": "#f97066",
    "selection": "#17384a",
    "selection_strong": "#2d799a",
    "selected_text": "#ffffff",
    "hover": "#293b4b",
    "disabled": "#728194",
}


def app_qss(theme: str = "light") -> str:
    colors = DARK if theme == "dark" else LIGHT
    icon_name = "chevron-down-dark.png" if theme == "dark" else "chevron-down-light.png"
    arrow_icon = (
        Path(__file__).resolve().parent.parent / "resources" / "icons" / icon_name
    ).as_posix()
    return f"""
    QWidget {{
        font-family: {FONT_STACK};
        font-size: 13px;
        color: {colors["text"]};
        background: {colors["background"]};
    }}
    QMainWindow, QStackedWidget {{
        background: {colors["background"]};
    }}
    QListWidget {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #0f172e, stop:1 #18294a);
        border: none;
        border-right: 1px solid #13213d;
        padding: 10px 8px;
        outline: none;
    }}
    QListWidget::item {{
        min-height: 40px;
        padding: 10px 12px;
        border-radius: 8px;
        color: #d7e3ff;
    }}
    QListWidget::item:hover:!selected {{
        background: rgba(255,255,255,0.10);
        color: #ffffff;
    }}
    QListWidget::item:selected {{
        background: #2a7be4;
        color: #ffffff;
    }}
    QLabel {{
        background: transparent;
    }}
    QLabel#PageTitle {{
        font-size: 23px;
        font-weight: 700;
        color: {colors["accent"]};
    }}
    QLabel#PageSubtitle {{
        color: {colors["muted"]};
        font-size: 13px;
    }}
    QLabel#SectionTitle {{
        font-size: 15px;
        font-weight: 600;
        color: {colors["accent"]};
    }}
    QLabel#ProviderLink {{
        color: {colors["accent"]};
    }}
    QLabel#StatusBadge {{
        padding: 5px 8px;
        border: 1px solid {colors["border"]};
        border-radius: 4px;
        background: {colors["surface_muted"]};
        color: {colors["muted"]};
    }}
    QFrame#CardWidget {{
        background: {colors["surface"]};
        border: 1px solid {colors["border"]};
        border-radius: 10px;
    }}

    QFrame#RightControlPanel {{
        background: {colors["surface"]};
        border: 1px solid {colors["border"]};
        border-radius: 12px;
    }}
    QFrame#AvailabilityPanel {{
        background: {colors["surface"]};
        border: 1px solid {colors["border"]};
        border-radius: 10px;
    }}
    QLabel#ControlLabel {{
        color: {colors["muted"]};
        font-size: 12px;
        font-weight: 600;
    }}
    QGroupBox {{
        background: {colors["surface"]};
        border: 1px solid {colors["border"]};
        border-radius: 10px;
        margin-top: 12px;
        padding: 14px 10px 10px 10px;
        font-weight: 600;
    }}
    QLineEdit, QPlainTextEdit, QComboBox, QDateEdit, QSpinBox, QDoubleSpinBox {{
        min-height: 36px;
        padding: 4px 10px;
        border: 1px solid {colors["border"]};
        border-radius: 8px;
        background: {colors["surface"]};
        color: {colors["text"]};
        selection-background-color: {colors["selection_strong"]};
        selection-color: {colors["selected_text"]};
    }}
    QComboBox {{
        min-width: 150px;
        padding-right: 36px;
    }}
    QDateEdit {{
        min-width: 168px;
        padding-right: 36px;
    }}
    QComboBox::drop-down, QDateEdit::drop-down {{
        subcontrol-origin: padding;
        subcontrol-position: top right;
        width: 28px;
        border-left: 1px solid {colors["border"]};
        background: {colors["surface_muted"]};
        border-top-right-radius: 5px;
        border-bottom-right-radius: 5px;
    }}
    QComboBox::down-arrow, QDateEdit::down-arrow {{
        image: url("{arrow_icon}");
        width: 12px;
        height: 8px;
    }}
    QComboBox::drop-down:hover, QDateEdit::drop-down:hover {{
        background: {colors["hover"]};
    }}
    QComboBox QAbstractItemView {{
        min-width: 190px;
        background: {colors["surface"]};
        color: {colors["text"]};
        border: 1px solid {colors["border"]};
        selection-background-color: {colors["selection_strong"]};
        selection-color: {colors["selected_text"]};
        outline: none;
        padding: 4px;
    }}
    QComboBox QAbstractItemView::item {{
        min-height: 28px;
        padding: 4px 8px;
    }}
    QProgressBar {{
        min-height: 18px;
        border: 1px solid {colors["border"]};
        border-radius: 8px;
        background: {colors["surface_muted"]};
        color: {colors["text"]};
        text-align: center;
    }}
    QProgressBar::chunk {{
        background: {colors["accent"]};
        border-radius: 4px;
    }}
    QLineEdit:disabled, QPlainTextEdit:disabled, QComboBox:disabled,
    QDateEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
        color: {colors["disabled"]};
        background: {colors["surface_muted"]};
    }}
    QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus,
    QDateEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
        border-color: {colors["accent"]};
    }}
    QPushButton {{
        min-height: 36px;
        padding: 5px 12px;
        border: 1px solid {colors["border"]};
        border-radius: 8px;
        background: {colors["surface"]};
        color: {colors["text"]};
    }}
    QPushButton:hover {{
        border-color: {colors["accent"]};
    }}
    QPushButton#primaryButton, QPushButton#PrimaryButton {{
        background: {colors["accent"]};
        color: #ffffff;
        border-color: {colors["accent"]};
    }}
    QPushButton#primaryButton:hover, QPushButton#PrimaryButton:hover {{
        background: {colors["accent_hover"]};
    }}
    QPushButton#SecondaryButton {{
        background: {colors["surface"]};
        color: {colors["text"]};
    }}
    QPushButton#DangerButton {{
        background: {colors["danger"]};
        color: #ffffff;
        border-color: {colors["danger"]};
    }}
    QPushButton:disabled, QToolButton:disabled {{
        color: {colors["disabled"]};
        background: {colors["surface_muted"]};
        border-color: {colors["border"]};
    }}
    QLabel:disabled, QCheckBox:disabled, QRadioButton:disabled {{
        color: {colors["disabled"]};
    }}
    QToolButton {{
        min-height: 28px;
        padding: 4px 10px;
        border: 1px solid {colors["border"]};
        border-radius: 4px;
        background: {colors["surface"]};
        color: {colors["text"]};
    }}
    QToolButton:hover {{
        border-color: {colors["accent"]};
    }}
    QToolButton:checked {{
        background: {colors["accent"]};
        color: #ffffff;
        border-color: {colors["accent"]};
    }}
    QToolButton#LinkButton {{
        min-height: 24px;
        padding: 2px 4px;
        border: none;
        background: transparent;
        color: {colors["accent"]};
    }}
    QToolButton#LinkButton:hover {{
        background: {colors["hover"]};
        color: {colors["accent_hover"]};
    }}
    QFrame#mapSidePanel {{
        background: {colors["surface"]};
        border: 1px solid {colors["border"]};
        border-radius: 10px;
    }}
    QFrame#mapCanvasPanel {{
        background: {colors["surface"]};
        border: 1px solid {colors["border"]};
        border-radius: 10px;
    }}
    QFrame#mapToolbar {{
        background: {colors["surface"]};
        border-bottom: 1px solid {colors["border"]};
        border-top-left-radius: 6px;
        border-top-right-radius: 6px;
    }}
    QFrame#mapStatusStrip {{
        background: {colors["surface_muted"]};
        border-top: 1px solid {colors["border"]};
        border-bottom-left-radius: 6px;
        border-bottom-right-radius: 6px;
    }}
    QLabel#mapToolbarTitle, QLabel#mapPanelTitle {{
        color: {colors["accent"]};
        font-size: 15px;
        font-weight: 600;
    }}
    QLabel#mapPanelSummary, QLabel#mapHintLabel {{
        color: {colors["muted"]};
    }}
    QTabWidget::pane {{
        border: 1px solid {colors["border"]};
        border-radius: 10px;
        background: {colors["surface"]};
    }}
    QTabBar::tab {{
        min-height: 30px;
        padding: 5px 12px;
        color: {colors["muted"]};
    }}
    QTabBar::tab:selected {{
        color: {colors["accent"]};
    }}
    QTableWidget, QTableView {{
        gridline-color: {colors["border"]};
        background: {colors["surface"]};
        alternate-background-color: {colors["surface_muted"]};
        color: {colors["text"]};
        selection-background-color: {colors["selection_strong"]};
        selection-color: {colors["selected_text"]};
        outline: none;
    }}
    QTableWidget::item, QTableView::item {{
        color: {colors["text"]};
        padding: 5px 7px;
    }}
    QTableWidget::item:hover:!selected, QTableView::item:hover:!selected {{
        background: {colors["hover"]};
        color: {colors["text"]};
    }}
    QTableWidget::item:selected, QTableView::item:selected {{
        background: {colors["selection_strong"]};
        color: {colors["selected_text"]};
    }}
    QHeaderView::section {{
        background: {colors["surface_muted"]};
        color: {colors["text"]};
        border: none;
        border-bottom: 1px solid {colors["border"]};
        padding: 7px;
    }}
    QCheckBox, QRadioButton {{
        color: {colors["text"]};
        spacing: 7px;
    }}
    QMenu {{
        background: {colors["surface"]};
        color: {colors["text"]};
        border: 1px solid {colors["border"]};
    }}
    QMenu::item:selected {{
        background: {colors["selection_strong"]};
        color: {colors["selected_text"]};
    }}
    QToolTip {{
        background: {colors["surface"]};
        color: {colors["text"]};
        border: 1px solid {colors["border"]};
        padding: 4px;
    }}
    QStatusBar {{
        background: {colors["surface"]};
        color: {colors["muted"]};
        border-top: 1px solid {colors["border"]};
    }}
    """


def resolve_theme(theme: str, app=None) -> str:
    if theme in {"light", "dark"}:
        return theme
    if app is not None:
        try:
            scheme = str(app.styleHints().colorScheme()).lower()
            if "dark" in scheme:
                return "dark"
        except Exception:
            return "light"
    return "light"


def apply_app_theme(app, theme: str) -> str:
    effective = resolve_theme(theme, app)
    app.setProperty("gnssgo_theme", theme)
    app.setProperty("gnssgo_effective_theme", effective)
    app.setStyleSheet(app_qss(effective))
    for widget in app.allWidgets():
        setter = getattr(widget, "set_theme", None)
        if callable(setter):
            try:
                setter(effective)
            except Exception:
                continue
    return effective
