"""Qt 6 window and system tray icon."""

from __future__ import annotations

import argparse
import os
import queue
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox, QProgressBar, QPushButton,
    QRadioButton, QSpinBox, QStackedWidget, QSystemTrayIcon, QVBoxLayout, QWidget, QButtonGroup,
)

from . import __version__
from . import protocol as p
from .device import STATE_DIR, DeviceError, Mouse, MouseDisconnected, find_devices
from .i18n import tr

APP_ID = "compx-mouse-tool"
BUTTON_LABELS = ("Left button", "Right button", "Middle button (wheel)", "Back (side)", "Forward (side)",
                 "DPI button (bottom)")
AUTOSTART = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "autostart" / f"{APP_ID}.desktop"


class Worker(QObject):
    """Owns the hidraw channel. All I/O runs in one Python thread fed by a queue;
    results come back to the GUI thread through queued Qt signals."""

    state = Signal(object)
    battery = Signal(object, object)
    connection = Signal(bool, str)
    message = Signal(str)
    failed = Signal(str)

    def __init__(self):
        super().__init__()
        self.mouse: Mouse | None = None
        self.have_state = False
        self.jobs: queue.Queue = queue.Queue()
        self.thread = threading.Thread(target=self._run, name="compx-io", daemon=True)

    def start(self):
        self.hotplug = QTimer(self)
        self.hotplug.timeout.connect(lambda: self.jobs.put(("check_device",)))
        self.hotplug.start(3000)
        self.battery_timer = QTimer(self)
        self.battery_timer.timeout.connect(lambda: self.jobs.put(("read_battery",)))
        self.battery_timer.start(60_000)
        self.thread.start()
        self.jobs.put(("check_device",))

    def stop(self):
        self.jobs.put(None)
        self.thread.join(3)

    def refresh(self):
        self.jobs.put(("refresh",))

    def call(self, method: str, args: tuple):
        self.jobs.put(("_call", method, args))

    def _run(self):
        while (job := self.jobs.get()) is not None:
            name, *args = job
            if name == "check_device" and self.jobs.qsize() > 3:
                continue  # don't pile up hotplug checks behind slow writes
            try:
                getattr(self, name if name.startswith("_") else f"_{name}")(*args)
            except Exception as exc:  # keep the I/O thread alive whatever happens
                self.failed.emit(f"{type(exc).__name__}: {exc}")
                self._close()
        self._close()

    def _close(self):
        if self.mouse:
            self.mouse.close()
            self.mouse = None
            self.have_state = False

    def _check_device(self):
        devices = find_devices()
        if not devices:
            if self.mouse:
                self._close()
            self.connection.emit(False, tr("No mouse found. Plug in the receiver or the cable."))
            return
        if self.mouse and self.mouse.info == devices[0]:
            if not self.have_state:  # mouse was asleep, try again
                self._refresh()
            return
        self._close()
        try:
            self.mouse = Mouse(devices[0])
        except DeviceError as exc:
            self.connection.emit(False, tr(str(exc)))
            return
        self.connection.emit(True, "")
        self._refresh()

    def _refresh(self):
        if not self.mouse:
            return
        try:
            self.state.emit(self.mouse.read_state())
            self.have_state = True
        except DeviceError as exc:
            self.have_state = False
            self.failed.emit(tr(str(exc)))
            if isinstance(exc, MouseDisconnected):
                self._close()

    def _read_battery(self):
        if not self.mouse:
            return
        try:
            self.battery.emit(*self.mouse.read_battery())
        except DeviceError:
            pass  # asleep; keep the last known value

    def _call(self, method: str, args: tuple):
        if not self.mouse:
            return
        try:
            getattr(self.mouse, method)(*args)
            self.message.emit(tr("Saved."))
        except (DeviceError, p.ProtocolError) as exc:
            self.failed.emit(tr(str(exc)))
        self._refresh()


def battery_icon(percent: int | None, charging: bool | None) -> QIcon:
    pix = QPixmap(64, 64)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    fg = QApplication.palette().windowText().color()
    painter.setPen(fg)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    # mouse outline
    painter.drawRoundedRect(14, 2, 36, 58, 17, 17)
    painter.drawLine(32, 4, 32, 22)
    if percent is not None:
        color = QColor("#e5534b") if percent <= 15 else QColor("#e0a33a") if percent <= 35 else QColor("#3fb950")
        if charging:
            color = QColor("#3b8eea")
        height = round(30 * percent / 100)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(20, 54 - height, 24, max(height, 2), 4, 4)
    painter.end()
    return QIcon(pix)


class MainWindow(QMainWindow):
    request = Signal(str, object)
    refresh_requested = Signal()

    def __init__(self, settings: QSettings):
        super().__init__()
        self.settings = settings
        self.state: p.MouseState | None = None
        self.setWindowTitle("Compx Mouse Tool")
        self.setWindowIcon(QIcon.fromTheme("input-mouse"))
        self.resize(760, 520)

        self.nav = QListWidget()
        self.nav.setFixedWidth(170)
        self.pages = QStackedWidget()
        for icon, title, page in (
            ("input-mouse", tr("Overview"), self._overview_page()),
            ("speedometer", tr("Performance"), self._performance_page()),
            ("zoom-in", tr("DPI"), self._dpi_page()),
            ("input-keyboard", tr("Buttons"), self._buttons_page()),
            ("configure", tr("App"), self._app_page()),
        ):
            QListWidgetItem(QIcon.fromTheme(icon), title, self.nav)
            self.pages.addWidget(page)
        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.nav.setCurrentRow(0)

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.addWidget(self.nav)
        layout.addWidget(self.pages, 1)
        self.setCentralWidget(central)
        self.statusBar().showMessage(tr("Searching for the mouse …"))
        self._set_editable(False)

    # --- pages ---------------------------------------------------------

    @staticmethod
    def _page(title: str) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        layout = QVBoxLayout(page)
        heading = QLabel(f"<h2>{title}</h2>")
        layout.addWidget(heading)
        return page, layout

    @staticmethod
    def _hint(text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setEnabled(False)
        return label

    def _overview_page(self) -> QWidget:
        page, layout = self._page(tr("Mouse"))
        form = QFormLayout()
        self.ov_connection = QLabel("–")
        self.ov_firmware = QLabel("–")
        self.ov_battery = QProgressBar()
        self.ov_battery.setRange(0, 100)
        self.ov_dpi = QLabel("–")
        self.ov_polling = QLabel("–")
        form.addRow(tr("Connection") + ":", self.ov_connection)
        form.addRow(tr("Firmware") + ":", self.ov_firmware)
        form.addRow(tr("Battery") + ":", self.ov_battery)
        form.addRow(tr("Stored DPI stage") + ":", self.ov_dpi)
        form.addRow("", self._hint(tr("Stage changes made with the mouse buttons are not reported to the computer.")))
        form.addRow(tr("Polling rate") + ":", self.ov_polling)
        layout.addLayout(form)
        refresh = QPushButton(QIcon.fromTheme("view-refresh"), tr("Refresh"))
        refresh.clicked.connect(self.refresh_requested)
        layout.addWidget(refresh, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addStretch(1)
        layout.addWidget(self._hint(tr("Unofficial tool. Settings are stored in the mouse itself.") +
                                    f"  ·  v{__version__}"))
        return page

    def _performance_page(self) -> QWidget:
        page, layout = self._page(tr("Performance"))
        form = QFormLayout()
        self.polling = QComboBox()
        for hz in sorted(p.POLLING_RATES):
            self.polling.addItem(f"{hz} Hz", hz)
        self.polling.activated.connect(lambda _: self.request.emit("set_polling", (self.polling.currentData(),)))
        self.debounce = QComboBox()
        for ms in p.DEBOUNCE_MS:
            self.debounce.addItem(f"{ms} ms", ms)
        self.debounce.activated.connect(lambda _: self.request.emit("set_debounce", (self.debounce.currentData(),)))
        self.lift_off = QComboBox()
        for mm in sorted(p.LIFT_OFF_MM):
            self.lift_off.addItem(f"{mm} mm", mm)
        self.lift_off.activated.connect(lambda _: self.request.emit("set_lift_off", (self.lift_off.currentData(),)))
        self.motion_sync = QCheckBox()
        self.motion_sync.clicked.connect(lambda checked: self.request.emit("set_motion_sync", (checked,)))
        form.addRow(tr("Polling rate") + ":", self.polling)
        form.addRow("", self._hint(tr("Higher is smoother; 1000 Hz is the maximum of this mouse.")))
        form.addRow(tr("Debounce time") + ":", self.debounce)
        form.addRow("", self._hint(tr("Raise it if a click is sometimes registered twice.")))
        form.addRow(tr("Lift-off distance") + ":", self.lift_off)
        form.addRow("", self._hint(tr("Height at which the sensor stops tracking.")))
        form.addRow(tr("Motion sync") + ":", self.motion_sync)
        form.addRow("", self._hint(tr("Syncs sensor reads with USB polling: steadier, about 1 ms more latency.")))
        layout.addLayout(form)
        layout.addStretch(1)
        self.performance_widgets = [self.polling, self.debounce, self.lift_off, self.motion_sync]
        return page

    def _dpi_page(self) -> QWidget:
        page, layout = self._page(tr("DPI"))
        top = QFormLayout()
        self.stage_count = QSpinBox()
        self.stage_count.setRange(1, p.DPI_STAGES)
        self.stage_count.valueChanged.connect(self._update_stage_rows)
        top.addRow(tr("Number of stages") + ":", self.stage_count)
        layout.addLayout(top)
        grid = QGridLayout()
        self.stage_group = QButtonGroup(self)
        self.stage_radios, self.stage_spins, self.stage_swatches = [], [], []
        grid.addWidget(QLabel(tr("Switch to stage")), 0, 0)
        for i in range(p.DPI_STAGES):
            radio = QRadioButton(tr("Stage {n}", n=i + 1))
            self.stage_group.addButton(radio, i)
            spin = QSpinBox()
            spin.setRange(p.DPI_MIN, p.DPI_MAX)
            spin.setSingleStep(p.DPI_STEP)
            spin.setSuffix(" DPI")
            spin.setKeyboardTracking(False)
            swatch = QFrame()
            swatch.setFixedSize(18, 18)
            grid.addWidget(radio, i + 1, 0)
            grid.addWidget(spin, i + 1, 1)
            grid.addWidget(swatch, i + 1, 2)
            self.stage_radios.append(radio)
            self.stage_spins.append(spin)
            self.stage_swatches.append(swatch)
        layout.addLayout(grid)
        layout.addWidget(self._hint(tr("The bottom button cycles through the active stages.")))
        buttons = QHBoxLayout()
        self.dpi_apply = QPushButton(QIcon.fromTheme("dialog-ok-apply"), tr("Apply"))
        self.dpi_apply.clicked.connect(self._apply_dpi)
        self.dpi_reset = QPushButton(QIcon.fromTheme("edit-undo"), tr("Reset"))
        self.dpi_reset.clicked.connect(lambda: self.show_state(self.state))
        buttons.addWidget(self.dpi_apply)
        buttons.addWidget(self.dpi_reset)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        layout.addStretch(1)
        return page

    def _buttons_page(self) -> QWidget:
        page, layout = self._page(tr("Buttons"))
        form = QFormLayout()
        self.button_boxes = []
        for label in BUTTON_LABELS:
            box = QComboBox()
            for action in p.BUTTON_ACTIONS:
                box.addItem(tr(action), action)
            form.addRow(tr(label) + ":", box)
            self.button_boxes.append(box)
        layout.addLayout(form)
        buttons = QHBoxLayout()
        self.buttons_apply = QPushButton(QIcon.fromTheme("dialog-ok-apply"), tr("Apply"))
        self.buttons_apply.clicked.connect(self._apply_buttons)
        self.buttons_reset = QPushButton(QIcon.fromTheme("edit-undo"), tr("Reset"))
        self.buttons_reset.clicked.connect(lambda: self.show_state(self.state))
        buttons.addWidget(self.buttons_apply)
        buttons.addWidget(self.buttons_reset)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        layout.addStretch(1)
        return page

    def _app_page(self) -> QWidget:
        page, layout = self._page(tr("App"))
        self.autostart = QCheckBox(tr("Start in the system tray when I log in"))
        self.autostart.setChecked(AUTOSTART.exists())
        self.autostart.toggled.connect(set_autostart)
        self.close_to_tray = QCheckBox(tr("Close to tray"))
        self.close_to_tray.setChecked(self.settings.value("close_to_tray", True, type=bool))
        self.close_to_tray.toggled.connect(lambda v: self.settings.setValue("close_to_tray", v))
        low = QHBoxLayout()
        low.addWidget(QLabel(tr("Warn when the battery drops below")))
        self.low_battery = QSpinBox()
        self.low_battery.setRange(0, 50)
        self.low_battery.setSuffix(" %")
        self.low_battery.setValue(self.settings.value("low_battery", 15, type=int))
        self.low_battery.valueChanged.connect(lambda v: self.settings.setValue("low_battery", v))
        low.addWidget(self.low_battery)
        low.addStretch(1)
        layout.addWidget(self.autostart)
        layout.addWidget(self.close_to_tray)
        layout.addLayout(low)
        layout.addWidget(QLabel(f"<h3>{tr('Backups')}</h3>"))
        layout.addWidget(self._hint(tr("A backup of the settings is saved before the first change of each session in")
                                    + f" {STATE_DIR}"))
        layout.addStretch(1)
        return page

    # --- state ---------------------------------------------------------

    def _set_editable(self, enabled: bool):
        for widget in (*self.performance_widgets, self.stage_count, *self.stage_radios, *self.stage_spins,
                       self.dpi_apply, self.dpi_reset, *self.button_boxes, self.buttons_apply, self.buttons_reset):
            widget.setEnabled(enabled)
        if enabled:
            self._update_stage_rows(self.stage_count.value())

    def _update_stage_rows(self, count: int):
        for i in range(p.DPI_STAGES):
            self.stage_radios[i].setEnabled(i < count)
            self.stage_spins[i].setEnabled(i < count)
        if self.stage_group.checkedId() >= count:
            self.stage_radios[count - 1].setChecked(True)

    def set_connected(self, connected: bool, why: str):
        if not connected:
            self.state = None
            self._set_editable(False)
            self.ov_connection.setText(why)
            self.statusBar().showMessage(why)

    def show_state(self, state: p.MouseState | None):
        if state is None:
            return
        self.state = state
        if self.statusBar().currentMessage() == tr("Searching for the mouse …"):
            self.statusBar().clearMessage()
        self.ov_connection.setText(tr("USB cable") if state.wired else tr("2.4 GHz receiver"))
        self.ov_firmware.setText(state.firmware)
        self.show_battery(state.battery_percent, state.charging)
        stages = state.dpi_stages
        self.ov_dpi.setText(f"{stages[state.dpi_active]} ({tr('Stage {n}', n=state.dpi_active + 1)})")
        self.ov_polling.setText(f"{state.polling_hz} Hz")
        widgets = (self.polling, self.debounce, self.lift_off, self.motion_sync, self.stage_count,
                   *self.stage_spins, *self.stage_radios, *self.button_boxes)
        blockers = [QSignalBlocker(w) for w in widgets]
        self.polling.setCurrentIndex(self.polling.findData(state.polling_hz))
        self.debounce.setCurrentIndex(self.debounce.findData(state.debounce_ms))
        self.lift_off.setCurrentIndex(self.lift_off.findData(state.lift_off_mm))
        self.motion_sync.setChecked(state.motion_sync)
        self.stage_count.setValue(state.dpi_stage_count)
        for spin, dpi, swatch, color in zip(self.stage_spins, stages, self.stage_swatches, state.dpi_colors):
            spin.setValue(dpi or p.DPI_MIN)
            swatch.setStyleSheet(f"background: rgb{color}; border: 1px solid palette(mid); border-radius: 9px;")
        self.stage_radios[min(state.dpi_active, p.DPI_STAGES - 1)].setChecked(True)
        for box, action in zip(self.button_boxes, state.buttons):
            if action is None:
                box.setCurrentIndex(-1)
            else:
                box.setCurrentIndex(box.findData(action))
        del blockers
        writable = not state.problems
        self._set_editable(writable)
        if not writable:
            self.statusBar().showMessage(tr("Writing is disabled: {why}", why="; ".join(state.problems)))
        for box, action in zip(self.button_boxes, state.buttons):
            box.setEnabled(writable and None not in state.buttons)
        self.buttons_apply.setEnabled(writable and None not in state.buttons)

    def show_battery(self, percent, charging):
        self.ov_battery.setValue(percent or 0)
        text = "?" if percent is None else f"{percent} %"
        if charging:
            text += f" · {tr('charging')}"
        self.ov_battery.setFormat(text)

    def _apply_dpi(self):
        if not self.state:
            return
        stages = [spin.value() // p.DPI_STEP * p.DPI_STEP for spin in self.stage_spins]
        if stages != self.state.dpi_stages:
            self.request.emit("set_dpi_stages", (stages,))
        if self.stage_count.value() != self.state.dpi_stage_count:
            self.request.emit("set_dpi_stage_count", (self.stage_count.value(),))
        active = self.stage_group.checkedId()
        if active != self.state.dpi_active:
            self.request.emit("set_active_stage", (active,))

    def _apply_buttons(self):
        actions = [box.currentData() for box in self.button_boxes]
        if "left_click" not in actions:
            QMessageBox.warning(self, tr("Buttons"), tr("At least one button must stay a left click."))
            return
        self.request.emit("set_buttons", (actions,))

    def closeEvent(self, event):
        if QSystemTrayIcon.isSystemTrayAvailable() and self.settings.value("close_to_tray", True, type=bool):
            event.ignore()
            self.hide()
        else:
            QApplication.quit()


def set_autostart(enabled: bool):
    if enabled:
        AUTOSTART.parent.mkdir(parents=True, exist_ok=True)
        AUTOSTART.write_text(
            "[Desktop Entry]\nType=Application\nName=Compx Mouse Tool\n"
            f"Exec={APP_ID}-gui --tray\nIcon=input-mouse\nX-KDE-autostart-phase=2\n"
        )
    elif AUTOSTART.exists():
        AUTOSTART.unlink()


class Tray(QSystemTrayIcon):
    request = Signal(str, object)

    def __init__(self, window: MainWindow, settings: QSettings):
        super().__init__(battery_icon(None, None))
        self.window = window
        self.settings = settings
        self.warned = False
        self.last_battery = (None, None)
        menu = QMenu()
        open_action = menu.addAction(QIcon.fromTheme("window"), tr("Open"))
        open_action.triggered.connect(self.show_window)
        self.dpi_menu = menu.addMenu(QIcon.fromTheme("zoom-in"), tr("DPI"))
        self.dpi_group = QActionGroup(self.dpi_menu)
        menu.addSeparator()
        menu.addAction(QIcon.fromTheme("application-exit"), tr("Quit")).triggered.connect(QApplication.quit)
        self.setContextMenu(menu)
        self.activated.connect(lambda reason: self.show_window() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.setToolTip("Compx Mouse Tool")

    def show_window(self):
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def show_state(self, state: p.MouseState):
        self.dpi_menu.clear()
        for action in self.dpi_group.actions():
            self.dpi_group.removeAction(action)
        for i, dpi in enumerate(state.dpi_stages[: state.dpi_stage_count]):
            action = QAction(f"{dpi} DPI", self.dpi_menu, checkable=True, checked=i == state.dpi_active)
            action.triggered.connect(lambda _=False, index=i: self.request.emit("set_active_stage", (index,)))
            self.dpi_group.addAction(action)
            self.dpi_menu.addAction(action)
        self.show_battery(state.battery_percent, state.charging, state.dpi_stages[state.dpi_active])

    def show_battery(self, percent, charging, dpi=None):
        self.setIcon(battery_icon(percent, charging))
        text = f"{tr('Battery')}: {'?' if percent is None else f'{percent} %'}"
        if charging:
            text += f" ({tr('charging')})"
        if dpi:
            text += f"\n{tr('Stored DPI stage')}: {dpi}"
        self.setToolTip(f"Compx Mouse Tool\n{text}")
        threshold = self.settings.value("low_battery", 15, type=int)
        if percent is not None and not charging and percent < threshold:
            if not self.warned:
                self.showMessage(tr("Battery low"), tr("The mouse battery is at {p} %.", p=percent),
                                 QSystemTrayIcon.MessageIcon.Warning)
                self.warned = True
        else:
            self.warned = False

    def set_connected(self, connected: bool, why: str):
        if not connected:
            self.setIcon(battery_icon(None, None))
            self.setToolTip(f"Compx Mouse Tool\n{why}")
            self.dpi_menu.clear()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=f"{APP_ID}-gui")
    parser.add_argument("--tray", action="store_true", help="start hidden in the system tray")
    args, qt_args = parser.parse_known_args(argv)

    app = QApplication([sys.argv[0], *qt_args])
    app.setApplicationName(APP_ID)
    app.setDesktopFileName(APP_ID)
    app.setApplicationVersion(__version__)
    app.setQuitOnLastWindowClosed(False)
    settings = QSettings(APP_ID, APP_ID)

    window = MainWindow(settings)
    tray = Tray(window, settings) if QSystemTrayIcon.isSystemTrayAvailable() else None

    worker = Worker()
    window.request.connect(worker.call)
    window.refresh_requested.connect(worker.refresh)
    worker.state.connect(window.show_state)
    worker.battery.connect(window.show_battery)
    worker.connection.connect(window.set_connected)
    worker.message.connect(lambda text: window.statusBar().showMessage(text, 4000))
    worker.failed.connect(lambda text: window.statusBar().showMessage(text, 8000))
    if tray:
        tray.request.connect(worker.call)
        worker.state.connect(tray.show_state)
        worker.battery.connect(tray.show_battery)
        worker.connection.connect(tray.set_connected)
        tray.show()
    worker.start()

    if not (args.tray and tray):
        window.show()
    code = app.exec()
    worker.stop()
    return code


if __name__ == "__main__":
    sys.exit(main())
