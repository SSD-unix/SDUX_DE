#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PyShell — полный исправленный main.py

Обновления:
 - FancyTerminal (QPlainTextEdit + QProcess), авто-прокрутка, моноширинный шрифт
 - MiniBrowser: QUrl.fromUserInput(...) для загрузки URL
 - Safe overlays creation (panel/dock/notifications) before showFullScreen
 - Robust error handling and logging
"""

import sys
import os
import signal
import json
import subprocess
from collections import deque
from datetime import datetime

from PyQt5.QtCore import (
    Qt,
    QTimer,
    QTime,
    QSize,
    QRect,
    QProcess,
    pyqtSignal,
    QUrl,
)
from PyQt5.QtGui import (
    QFont,
    QTextCursor,
)
from PyQt5.QtWidgets import (
    QApplication,
    QWidget,
    QLabel,
    QPushButton,
    QHBoxLayout,
    QVBoxLayout,
    QStackedLayout,
    QFrame,
    QLineEdit,
    QPlainTextEdit,
    QTextEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QFileDialog,
    QMessageBox,
    QDialog,
    QTabWidget,
    QShortcut,
)

# Try import WebEngine; if not available, show fallback
try:
    from PyQt5.QtWebEngineWidgets import QWebEngineView
    WEB_AVAILABLE = True
except Exception:
    QWebEngineView = None
    WEB_AVAILABLE = False

# -------------------------
# Constants & config paths
# -------------------------
APP_NAME = "PyShell"
APP_VERSION = "0.3"
PANEL_HEIGHT = 48
DOCK_WIDTH = 64
WORKSPACE_COUNT = 4
CONFIG_DIR = os.path.expanduser("~/.pyshell")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
LOG_FILE = os.path.join(CONFIG_DIR, "pyshell.log")

os.makedirs(CONFIG_DIR, exist_ok=True)

# -------------------------
# Utilities
# -------------------------
def load_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save_json(path, obj):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2, ensure_ascii=False)
            return True
    except Exception as e:
        print("Failed to save json:", e)
        return False

class Logger:
    def __init__(self, path=LOG_FILE):
        self.path = path

    def _write(self, level, *parts):
        s = " ".join(str(p) for p in parts)
        t = datetime.now().isoformat()
        line = f"[{t}] {level}: {s}\n"
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass

    def info(self, *parts):
        self._write("INFO", *parts)

    def warn(self, *parts):
        self._write("WARN", *parts)

    def error(self, *parts):
        self._write("ERR", *parts)

logger = Logger()

# -------------------------
# Theme manager
# -------------------------
class ThemeManager:
    def __init__(self):
        self.themes = {
            "dark": {
                "panel_bg": "#212121",
                "panel_fg": "#ffffff",
                "accent": "#00aaff",
                "dock_bg": "#111111",
                "desktop_bg": "#0b1220",
            },
            "light": {
                "panel_bg": "#f7f7f7",
                "panel_fg": "#111111",
                "accent": "#0077cc",
                "dock_bg": "#eeeeee",
                "desktop_bg": "#dfe9f3",
            },
        }
        self.current = "dark"

    def get(self, key):
        return self.themes[self.current].get(key, "#000000")

    def set_theme(self, name):
        if name in self.themes:
            self.current = name
            logger.info("Theme set to", name)
            return True
        return False

# -------------------------
# Notification center
# -------------------------
class NotificationCenter(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedWidth(320)
        self.vbox = QVBoxLayout()
        self.vbox.setContentsMargins(8, 8, 8, 8)
        self.vbox.setSpacing(6)
        self.setLayout(self.vbox)
        self.notifications = deque(maxlen=60)

    def push(self, title, text, timeout=5000):
        w = QWidget()
        w.setStyleSheet("background: rgba(30,30,30,0.9); color: white; border-radius: 8px; padding:8px;")
        v = QVBoxLayout()
        v.setContentsMargins(6,6,6,6)
        w.setLayout(v)
        t = QLabel(f"<b>{title}</b>")
        t.setWordWrap(True)
        s = QLabel(text)
        s.setWordWrap(True)
        v.addWidget(t)
        v.addWidget(s)
        self.vbox.addWidget(w)
        self.notifications.append((w, datetime.now()))
        QTimer.singleShot(timeout, lambda: self._remove(w))

    def _remove(self, widget):
        try:
            self.vbox.removeWidget(widget)
            widget.setParent(None)
        except Exception:
            pass

# -------------------------
# Internal Window
# -------------------------
class InternalWindow(QFrame):
    window_closed = pyqtSignal(object)

    def __init__(self, title="Window", content_widget=None, parent=None):
        # parent should be the workspace canvas to which this window belongs
        super().__init__(parent)
        self.setFrameShape(QFrame.StyledPanel)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setStyleSheet("background: #1b1b1b; color: white; border-radius:6px;")
        self.setFocusPolicy(Qt.ClickFocus)

        # state
        self.title = title
        self.is_maximized = False
        self.prev_geometry = QRect()

        # layouts
        self.vbox = QVBoxLayout()
        self.vbox.setContentsMargins(0,0,0,0)
        self.vbox.setSpacing(0)
        self.setLayout(self.vbox)

        # titlebar
        self._init_titlebar()

        # content
        self.content = QWidget()
        self.content_layout = QVBoxLayout()
        self.content_layout.setContentsMargins(6,6,6,6)
        self.content.setLayout(self.content_layout)
        if content_widget is not None:
            self.content_layout.addWidget(content_widget)
        else:
            te = QTextEdit()
            te.setPlainText("New internal window")
            self.content_layout.addWidget(te)
        self.vbox.addWidget(self.content)

        # initial size
        self.resize(640, 360)

        # interactions
        self._dragging = False
        self._drag_offset = None
        self._resizing = False
        self._resize_start = None
        self._resize_initial_geom = None

        # keyboard shortcuts
        QShortcut(Qt.CTRL + Qt.Key_W, self).activated.connect(self.on_close)
        QShortcut(Qt.Key_Escape, self).activated.connect(self.on_close)

    def _init_titlebar(self):
        self.titlebar = QWidget()
        self.titlebar.setFixedHeight(32)
        self.titlebar.setStyleSheet("background: rgba(0,0,0,0.18);")
        h = QHBoxLayout()
        h.setContentsMargins(8, 0, 8, 0)
        self.titlebar.setLayout(h)

        self.lbl_title = QLabel(self.title)
        self.lbl_title.setStyleSheet("font-weight:bold;")
        h.addWidget(self.lbl_title)
        h.addStretch()

        # buttons
        self.btn_min = QPushButton("_")
        self.btn_max = QPushButton("☐")
        self.btn_close = QPushButton("✕")
        for b in (self.btn_min, self.btn_max, self.btn_close):
            b.setFixedSize(28, 22)
            b.setStyleSheet("background: transparent; border: none; color: white; font-weight:bold;")
        self.btn_min.clicked.connect(self.on_min)
        self.btn_max.clicked.connect(self.on_max)
        self.btn_close.clicked.connect(self.on_close)
        h.addWidget(self.btn_min)
        h.addWidget(self.btn_max)
        h.addWidget(self.btn_close)

        self.vbox.addWidget(self.titlebar)

    # mouse/drag/resize handlers
    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            # start dragging if clicked on titlebar area
            local_y = ev.y()
            if local_y <= self.titlebar.height():
                self._dragging = True
                self._drag_offset = ev.globalPos() - self.frameGeometry().topLeft()
                ev.accept()
                return
            # bottom-right corner resize area (12x12)
            if ev.x() >= self.width() - 12 and ev.y() >= self.height() - 12:
                self._resizing = True
                self._resize_start = ev.globalPos()
                self._resize_initial_geom = self.geometry()
                ev.accept()
                return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        if self._dragging:
            new_top_left = ev.globalPos() - self._drag_offset
            self.move(new_top_left)
            ev.accept()
            return
        if self._resizing and self._resize_initial_geom is not None:
            delta = ev.globalPos() - self._resize_start
            new_w = max(200, self._resize_initial_geom.width() + delta.x())
            new_h = max(100, self._resize_initial_geom.height() + delta.y())
            self.setFixedSize(new_w, new_h)
            ev.accept()
            return
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        self._dragging = False
        self._resizing = False
        super().mouseReleaseEvent(ev)

    # window controls
    def on_min(self):
        self.hide()

    def on_max(self):
        parent_geom = self.parent().geometry() if self.parent() is not None else QApplication.primaryScreen().geometry()
        if not self.is_maximized:
            self.prev_geometry = self.geometry()
            self.setGeometry(10, PANEL_HEIGHT + 10, parent_geom.width() - 20, parent_geom.height() - PANEL_HEIGHT - 20)
            self.is_maximized = True
        else:
            try:
                self.setGeometry(self.prev_geometry)
            except Exception:
                pass
            self.is_maximized = False

    def on_close(self):
        try:
            self.window_closed.emit(self)
        except Exception:
            pass
        self.close()

    def focus_me(self):
        try:
            self.raise_()
            self.activateWindow()
            self.setFocus(Qt.OtherFocusReason)
        except Exception:
            pass

# -------------------------
# Fancy Terminal (QPlainTextEdit + QProcess)
# -------------------------
class FancyTerminal(QWidget):
    """
    Full-featured-ish terminal view using QProcess and QPlainTextEdit.
    Not a PTY: some interactive apps (nano, top, etc.) won't behave fully.
    But for typical CLI commands it works fine.
    """
    def __init__(self, shell_cmd=None, parent=None):
        super().__init__(parent)
        self.shell_cmd = shell_cmd or ["/bin/bash"]
        self.vbox = QVBoxLayout()
        self.vbox.setContentsMargins(6,6,6,6)
        self.setLayout(self.vbox)

        # Output area (QPlainTextEdit is faster and better for plain text)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setStyleSheet("""
            background: #0d0f12;
            color: #e6e6e6;
            border-radius: 6px;
            padding: 6px;
        """)
        mono = QFont("Monospace")
        mono.setStyleHint(QFont.Monospace)
        mono.setPointSize(11)
        self.output.setFont(mono)
        self.vbox.addWidget(self.output)

        # Input line
        self.input = QLineEdit()
        self.input.setPlaceholderText("Enter command and press Enter")
        self.input.returnPressed.connect(self.on_send)
        self.input.setStyleSheet("""
            background: #0b0b0b;
            color: #a8ff60;
            padding: 6px;
            border-radius: 6px;
        """)
        self.input.setFont(mono)
        self.vbox.addWidget(self.input)

        # Process
        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.MergedChannels)
        try:
            # start shell; leave arguments empty to run interactive shell
            self.proc.start(self.shell_cmd[0], self.shell_cmd[1:])
            started = self.proc.waitForStarted(800)
            if not started:
                self.append_text("[Warning] Shell did not start quickly or is not available.\n")
                logger.warn("FancyTerminal: shell did not start quickly")
            self.proc.readyReadStandardOutput.connect(self.read_output)
            self.proc.finished.connect(self.on_finished)
        except Exception as e:
            self.append_text(f"[Error starting shell: {e}]\n")
            logger.error("FancyTerminal start error:", e)

    def append_text(self, text):
        # append and auto-scroll
        self.output.moveCursor(QTextCursor.End)
        self.output.insertPlainText(text)
        self.output.moveCursor(QTextCursor.End)

    def read_output(self):
        try:
            raw = self.proc.readAllStandardOutput()
            if raw is None:
                return
            data = bytes(raw).decode("utf-8", errors="replace")
            self.append_text(data)
        except Exception as e:
            logger.warn("FancyTerminal read_output error:", e)

    def on_finished(self, exitCode, exitStatus=None):
        self.append_text(f"\n[Process exited with code {exitCode}]\n")
        logger.info("FancyTerminal process exited", exitCode)

    def on_send(self):
        text = self.input.text().strip()
        if not text:
            # send newline to shell to keep interactive session alive
            send = "\n"
        else:
            send = text + "\n"
        try:
            if self.proc.state() == QProcess.Running:
                self.proc.write(send.encode("utf-8"))
            else:
                self.append_text("[Process not running]\n")
        except Exception as e:
            self.append_text(f"[Failed to send: {e}]\n")
            logger.error("FancyTerminal write failed:", e)
        self.input.clear()

# -------------------------
# Simple Editor
# -------------------------
class SimpleEditor(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout()
        self.setLayout(v)
        toolbar = QWidget()
        th = QHBoxLayout()
        th.setContentsMargins(0,0,0,0)
        toolbar.setLayout(th)
        self.btn_open = QPushButton("Open")
        self.btn_save = QPushButton("Save")
        self.btn_clear = QPushButton("Clear")
        th.addWidget(self.btn_open)
        th.addWidget(self.btn_save)
        th.addWidget(self.btn_clear)
        self.text = QTextEdit()
        v.addWidget(toolbar)
        v.addWidget(self.text)

        self.btn_open.clicked.connect(self.open_file)
        self.btn_save.clicked.connect(self.save_file)
        self.btn_clear.clicked.connect(self.text.clear)
        self.current_path = None

    def open_file(self):
        p, _ = QFileDialog.getOpenFileName(self, "Open file", os.path.expanduser("~"))
        if p:
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = f.read()
                self.text.setPlainText(data)
                self.current_path = p
            except Exception as e:
                QMessageBox.critical(self, "Error", str(e))

    def save_file(self):
        if self.current_path is None:
            p, _ = QFileDialog.getSaveFileName(self, "Save file", os.path.expanduser("~"))
            if not p:
                return
            self.current_path = p
        try:
            with open(self.current_path, "w", encoding="utf-8") as f:
                f.write(self.text.toPlainText())
            QMessageBox.information(self, "Saved", "File saved.")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

# -------------------------
# MiniBrowser wrapper
# -------------------------
class MiniBrowser(QWidget):
    def __init__(self, url="https://www.example.com", parent=None):
        super().__init__(parent)
        v = QVBoxLayout()
        self.setLayout(v)
        self.address = QLineEdit(url)
        self.address.returnPressed.connect(self.load)
        v.addWidget(self.address)
        if WEB_AVAILABLE and QWebEngineView is not None:
            try:
                self.view = QWebEngineView()
                self.view.load(QUrl.fromUserInput(url))
                v.addWidget(self.view)
            except Exception as e:
                v.addWidget(QLabel(f"Failed to create browser view: {e}"))
                logger.error("MiniBrowser creation failed:", e)
        else:
            lbl = QLabel("QtWebEngine not available. Install PyQtWebEngine to enable browser.")
            lbl.setWordWrap(True)
            v.addWidget(lbl)

    def load(self):
        url = self.address.text()
        if WEB_AVAILABLE and QWebEngineView is not None:
            try:
                self.view.load(QUrl.fromUserInput(url))
            except Exception as e:
                QMessageBox.warning(self, "Load failed", str(e))
                logger.warn("MiniBrowser load failed:", e)

# -------------------------
# Top panel & Dock
# -------------------------
class TopPanel(QWidget):
    def __init__(self, shell, theme: ThemeManager):
        super().__init__(None)
        self.shell = shell
        self.theme = theme
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setFixedHeight(PANEL_HEIGHT)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        h = QHBoxLayout()
        h.setContentsMargins(8,4,8,4)
        self.setLayout(h)

        self.btn_menu = QPushButton("☰")
        self.btn_menu.setFixedSize(36,36)
        self.btn_menu.clicked.connect(self.on_menu)
        h.addWidget(self.btn_menu)

        self.btn_term = QPushButton("")
        self.btn_term.setFixedSize(36,36)
        self.btn_term.clicked.connect(lambda: shell.launch_app("terminal"))
        h.addWidget(self.btn_term)

        self.btn_browser = QPushButton("")
        self.btn_browser.setFixedSize(36,36)
        self.btn_browser.clicked.connect(lambda: shell.launch_app("browser"))
        h.addWidget(self.btn_browser)

        h.addStretch()
        self.lbl_clock = QLabel()
        self.lbl_clock.setFixedHeight(36)
        h.addWidget(self.lbl_clock)

        self.timer = QTimer()
        self.timer.timeout.connect(self.update_clock)
        self.timer.start(1000)
        self.update_clock()

    def update_clock(self):
        self.lbl_clock.setText(QTime.currentTime().toString("HH:mm:ss"))

    def on_menu(self):
        m = QMenu()
        m.addAction("Open Terminal", lambda: self.shell.launch_app("terminal"))
        m.addAction("Open Editor", lambda: self.shell.launch_app("editor"))
        m.addSeparator()
        m.addAction("Settings", lambda: self.shell.open_settings())
        m.addAction("Exit Shell", lambda: self.shell.quit())
        m.exec_(self.btn_menu.mapToGlobal(self.btn_menu.rect().bottomLeft()))

    def apply_theme(self):
        bg = self.theme.get("panel_bg")
        fg = self.theme.get("panel_fg")
        self.setStyleSheet(f"background: {bg}; color: {fg}; border-bottom: 1px solid rgba(0,0,0,0.2);")

class Dock(QWidget):
    def __init__(self, shell, theme: ThemeManager):
        super().__init__(None)
        self.shell = shell
        self.theme = theme
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setFixedWidth(DOCK_WIDTH)
        v = QVBoxLayout()
        v.setContentsMargins(6,6,6,6)
        v.setSpacing(8)
        self.setLayout(v)

        self.add_icon("Terminal", lambda: shell.launch_app("terminal"))
        self.add_icon("Browser", lambda: shell.launch_app("browser"))
        self.add_icon("Editor", lambda: shell.launch_app("editor"))
        self.add_icon("Files", lambda: shell.launch_app("files"))
        v.addStretch()

    def add_icon(self, text, cb):
        btn = QPushButton(text[0])
        btn.setToolTip(text)
        btn.setFixedSize(44,44)
        btn.setStyleSheet("border-radius:8px; background: rgba(255,255,255,0.04);")
        btn.clicked.connect(cb)
        self.layout().addWidget(btn)

    def apply_theme(self):
        bg = self.theme.get("dock_bg")
        self.setStyleSheet(f"background: {bg}; color: white;")

# -------------------------
# Workspace Manager
# -------------------------
class WorkspaceManager(QWidget):
    def __init__(self, parent=None, count=WORKSPACE_COUNT):
        super().__init__(parent)
        self.count = count
        self.stack = QStackedLayout()
        self.setLayout(self.stack)
        self.workspaces = []
        for i in range(count):
            w = QWidget()
            v = QVBoxLayout()
            v.setContentsMargins(0,0,0,0)
            w.setLayout(v)
            canvas = QWidget()
            canvas.setLayout(QVBoxLayout())
            canvas.layout().setContentsMargins(8,8,8,8)
            canvas.layout().setSpacing(8)
            v.addWidget(canvas)
            self.workspaces.append(canvas)
            self.stack.addWidget(w)
        self.current = 0

    def add_window(self, window: InternalWindow, workspace=None):
        idx = workspace if workspace is not None else self.current
        ws = self.workspaces[idx]
        ws.layout().addWidget(window)
        window.show()
        window.focus_me()

    def switch_to(self, index):
        self.current = index % self.count
        self.stack.setCurrentIndex(self.current)

    def list_windows(self):
        out = []
        for i, ws in enumerate(self.workspaces):
            items = [ws.layout().itemAt(j).widget() for j in range(ws.layout().count())]
            out.append((i, items))
        return out

# -------------------------
# Settings Dialog
# -------------------------
class SettingsDialog(QDialog):
    def __init__(self, shell):
        super().__init__(shell)
        self.shell = shell
        self.setWindowTitle("Settings - PyShell")
        self.resize(640,480)
        main = QVBoxLayout()
        self.setLayout(main)
        tabs = QTabWidget()
        # appearance tab
        t1 = QWidget()
        t1l = QVBoxLayout()
        t1.setLayout(t1l)
        t1l.addWidget(QLabel("Theme:"))
        self.theme_list = QListWidget()
        for k in self.shell.theme_manager.themes.keys():
            QListWidgetItem(k, self.theme_list)
        t1l.addWidget(self.theme_list)
        tabs.addTab(t1, "Appearance")
        # autostart
        t2 = QWidget()
        t2l = QVBoxLayout()
        t2.setLayout(t2l)
        t2l.addWidget(QLabel("Autostart commands (one per line):"))
        self.autostart = QTextEdit()
        t2l.addWidget(self.autostart)
        tabs.addTab(t2, "Autostart")
        main.addWidget(tabs)
        # buttons
        btns = QWidget()
        bhl = QHBoxLayout()
        bhl.addStretch()
        self.btn_ok = QPushButton("Save")
        self.btn_cancel = QPushButton("Cancel")
        bhl.addWidget(self.btn_ok)
        bhl.addWidget(self.btn_cancel)
        btns.setLayout(bhl)
        main.addWidget(btns)
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_ok.clicked.connect(self.on_save)
        self.load()

    def load(self):
        cfg = self.shell.config
        theme = cfg.get("theme", "dark")
        items = self.theme_list.findItems(theme, Qt.MatchExactly)
        if items:
            self.theme_list.setCurrentItem(items[0])
        self.autostart.setPlainText("\n".join(cfg.get("autostart", [])))

    def on_save(self):
        cfg = self.shell.config
        sel = self.theme_list.currentItem()
        if sel:
            cfg["theme"] = sel.text()
        cfg["autostart"] = [l for l in self.autostart.toPlainText().splitlines() if l.strip()]
        self.shell.config = cfg
        save_json(CONFIG_FILE, cfg)
        self.accept()

# -------------------------
# Main shell application
# -------------------------
class PyShellApp(QWidget):
    def __init__(self):
        super().__init__(None)
        self.setWindowTitle(f"{APP_NAME} {APP_VERSION}")
        self.theme_manager = ThemeManager()
        self.load_config()

        # create overlays and content BEFORE showing full-screen to avoid
        # resizeEvent before attributes exist
        self.panel = TopPanel(self, self.theme_manager)
        self.panel.apply_theme()

        self.workspace_manager = WorkspaceManager(self, count=WORKSPACE_COUNT)

        self.dock = Dock(self, self.theme_manager)
        self.dock.apply_theme()

        self.notifications = NotificationCenter(self)

        # main full-screen window
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.main_layout = QVBoxLayout()
        self.main_layout.setContentsMargins(0,0,0,0)
        self.main_layout.setSpacing(0)
        self.setLayout(self.main_layout)

        self.main_layout.addWidget(self.panel)

        self.content_holder = QWidget()
        self.content_holder.setLayout(QHBoxLayout())
        self.content_holder.layout().setContentsMargins(0,0,0,0)
        self.content_holder.layout().addWidget(self.workspace_manager)
        self.main_layout.addWidget(self.content_holder)

        # set overlay parents so they float above main window
        self.panel.setParent(self)
        self.dock.setParent(self)
        self.notifications.setParent(self)

        # show fullscreen AFTER overlays exist
        self.showFullScreen()
        self.resize(QApplication.primaryScreen().size())
        self.reposition_overlays()

        # shortcuts
        QShortcut(Qt.CTRL + Qt.Key_Q, self).activated.connect(self.quit)
        QShortcut(Qt.ALT + Qt.Key_Tab, self).activated.connect(self.cycle_windows)
        QShortcut(Qt.ALT + Qt.Key_Return, self).activated.connect(lambda: self.launch_app("terminal"))
        QShortcut(Qt.ALT + Qt.Key_Left, self).activated.connect(lambda: self.switch_workspace(-1))
        QShortcut(Qt.ALT + Qt.Key_Right, self).activated.connect(lambda: self.switch_workspace(1))

        # autostart slightly delayed
        QTimer.singleShot(1000, self.run_autostart)

        # welcome apps after stable start
        QTimer.singleShot(500, lambda: self.launch_app("browser"))
        QTimer.singleShot(800, lambda: self.launch_app("editor"))

        self.windows = []

    def reposition_overlays(self):
        if not (hasattr(self, "panel") and hasattr(self, "dock") and hasattr(self, "notifications")):
            return
        screen_sz = QApplication.primaryScreen().size()
        self.panel.setGeometry(0, 0, screen_sz.width(), PANEL_HEIGHT)
        self.dock.setGeometry(8, PANEL_HEIGHT + 8, DOCK_WIDTH, screen_sz.height() - PANEL_HEIGHT - 16)
        self.notifications.setGeometry(screen_sz.width() - 340, PANEL_HEIGHT + 8, 320, 400)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if hasattr(self, "panel") and hasattr(self, "dock") and hasattr(self, "notifications"):
            self.reposition_overlays()

    def load_config(self):
        cfg = load_json(CONFIG_FILE, default={}) or {}
        cfg.setdefault("theme", "dark")
        cfg.setdefault("autostart", [])
        cfg.setdefault("shortcuts", {})
        self.config = cfg
        self.theme_manager.set_theme(cfg.get("theme", "dark"))

    def launch_app(self, name):
        name = name.lower()
        try:
            if name == "terminal":
                t = FancyTerminal(shell_cmd=["/bin/bash"])
                win = InternalWindow("Terminal", t, parent=self.workspace_manager.workspaces[self.workspace_manager.current])
                win.window_closed.connect(self.on_window_closed)
                self.workspace_manager.add_window(win)
                self.windows.append(win)
                return win
            elif name == "editor":
                ed = SimpleEditor()
                win = InternalWindow("Editor", ed, parent=self.workspace_manager.workspaces[self.workspace_manager.current])
                win.window_closed.connect(self.on_window_closed)
                self.workspace_manager.add_window(win)
                self.windows.append(win)
                return win
            elif name == "browser":
                br = MiniBrowser("https://duckduckgo.com")
                win = InternalWindow("Browser", br, parent=self.workspace_manager.workspaces[self.workspace_manager.current])
                win.window_closed.connect(self.on_window_closed)
                self.workspace_manager.add_window(win)
                self.windows.append(win)
                return win
            elif name == "files":
                fm = SimpleEditor()  # placeholder
                win = InternalWindow("Files", fm, parent=self.workspace_manager.workspaces[self.workspace_manager.current])
                win.window_closed.connect(self.on_window_closed)
                self.workspace_manager.add_window(win)
                self.windows.append(win)
                return win
            else:
                # attempt to launch external program detached
                subprocess.Popen([name])
                return None
        except Exception as e:
            logger.error("launch_app failed:", e)
            self.notifications.push("Launch failed", str(e))
            return None

    def on_window_closed(self, win):
        try:
            if win in self.windows:
                self.windows.remove(win)
        except Exception:
            pass

    def cycle_windows(self):
        if not self.windows:
            return
        w = self.windows.pop(0)
        self.windows.append(w)
        try:
            w.raise_()
            w.focus_me()
        except Exception:
            pass

    def switch_workspace(self, delta):
        idx = (self.workspace_manager.current + delta) % self.workspace_manager.count
        self.workspace_manager.switch_to(idx)
        self.notifications.push("Workspace", f"Switched to {idx+1}", timeout=1200)

    def open_settings(self):
        dlg = SettingsDialog(self)
        if dlg.exec_():
            self.theme_manager.set_theme(self.config.get("theme", "dark"))
            self.panel.apply_theme()
            self.dock.apply_theme()

    def run_autostart(self):
        for cmd in self.config.get("autostart", []):
            try:
                subprocess.Popen(cmd.split())
            except Exception as e:
                logger.warn("autostart failed:", cmd, e)

    def quit(self):
        QApplication.quit()

# -------------------------
# Entrypoint
# -------------------------
def main():
    app = QApplication(sys.argv)
    # gracefully handle ctrl+c
    signal.signal(signal.SIGINT, lambda *args: QApplication.quit())
    shell = PyShellApp()
    shell.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
