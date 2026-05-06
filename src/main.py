import csv
import logging
import os
import sys
from collections import deque
from datetime import datetime, timedelta, timezone

import psutil
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtCore import QSettings


DEFAULT_INTERVAL_MS = 1000
DISPLAY_DURATION = 60
STORAGE_DURATION = 3600


def resource_path(relative_path):
    """Возвращает корректный путь к ресурсу при запуске из PyInstaller."""
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)


class MplCanvas(FigureCanvas):
    """Канва matplotlib для отображения графиков системных метрик в интерфейсе PyQt5.

    Поддерживает отображение истории значений, текущего значения, порогового уровня,
    а также динамическое обновление заголовка с дополнительной информацией (например,
    частота CPU или объёмы RAM/диска).
    """

    def __init__(self, base_title, unit="%", threshold=90, color="#1f77b4", is_disk_or_ram=False, is_cpu=False):
        """Инициализирует MplCanvas.

        Args:
            base_title (str): Название метрики (например, "Использование ЦП").
            unit (str, optional): Единица измерения. По умолчанию "%".
            threshold (float, optional): Порог срабатывания предупреждения. По умолчанию 90.
            color (str, optional): Цвет графика в hex-формате. По умолчанию "#1f77b4".
            is_disk_or_ram (bool, optional): True, если график для RAM/диска. По умолчанию False.
            is_cpu (bool, optional): True, если график для CPU. По умолчанию False.
        """
        self.fig = Figure(figsize=(4, 2), dpi=100)
        self.ax = self.fig.add_subplot(111)
        super().__init__(self.fig)
        self.base_title = base_title
        self.unit = unit
        self.threshold = threshold
        self.color = color
        self.is_disk_or_ram = is_disk_or_ram
        self.is_cpu = is_cpu
        self.ax.set_ylim(0, 100)
        self.ax.set_yticks([])
        self.ax.set_xticks(range(0, DISPLAY_DURATION + 1, 1))
        self.ax.grid(True, alpha=0.3, axis='both')
        for i in range(0, 101, 10):
            self.ax.axhline(y=i, color='gray', linewidth=0.5, alpha=0.5)
        self.threshold_line = self.ax.axhline(
            y=threshold, color='r', linestyle='--', alpha=0.5
        )
        self.ax.set_ylabel('')
        self.ax.set_xlabel('')
        self.fill = self.ax.fill_between([], [], color=self.color, alpha=0.3)
        self.line, = self.ax.plot([], [], color=self.color, linewidth=2, alpha=0.8)
        self.current_point, = self.ax.plot([], [], 'o', color=self.color, markersize=5)
        self.history = deque()
        self.timestamps = deque()

        if "CPU" in base_title or "RAM" in base_title or "Swap" in base_title:
            self.fig.subplots_adjust(top=0.87, left=0.01, right=0.99, bottom=0.01)
        else:
            self.fig.subplots_adjust(top=0.87, left=0.05, right=0.95, bottom=0.01)

    def update_value(self, percent, total_gb=None, used_gb=None, cpu_freq_ghz=None, timestamp=None):
        """Обновляет график новым значением метрики.

        Обрезает историю до STORAGE_DURATION секунд, обновляет график за последние
        DISPLAY_DURATION секунд, перерисовывает заголовок и возвращает флаг превышения порога.

        Args:
            percent (float): Текущее значение метрики в процентах.
            total_gb (float, optional): Общий объём (для RAM/диска) в ГБ.
            used_gb (float, optional): Используемый объём (для RAM/диска) в ГБ.
            cpu_freq_ghz (float, optional): Частота CPU в ГГц.
            timestamp (datetime.datetime, optional): Временная метка. По умолчанию — текущее время.

        Returns:
            bool: True, если percent > threshold, иначе False.
        """
        now = timestamp or datetime.now()
        self.history.append(percent)
        self.timestamps.append(now)
        cutoff_storage = now - timedelta(seconds=STORAGE_DURATION)
        while self.timestamps and self.timestamps[0] < cutoff_storage:
            self.timestamps.popleft()
            self.history.popleft()
        cutoff_display = now - timedelta(seconds=DISPLAY_DURATION)
        display_data = [
            (t, v) for t, v in zip(self.timestamps, self.history) if t >= cutoff_display
        ]
        if display_data:
            times, values = zip(*display_data)
            time_diffs = [(t - times[0]).total_seconds() for t in times]
        else:
            time_diffs, values = [], []
        self.line.set_data(time_diffs, values)
        if time_diffs:
            self.current_point.set_data([time_diffs[-1]], [values[-1]])
        else:
            self.current_point.set_data([], [])
        self.fill.remove()
        self.fill = self.ax.fill_between(
            time_diffs, 0, values, color=self.color, alpha=0.3
        )
        self.ax.set_xlim(0, DISPLAY_DURATION)
        if self.is_cpu:
            if cpu_freq_ghz is not None:
                title = f"{self.base_title} ({percent:.1f}% | {cpu_freq_ghz:.2f} GHz)"
            else:
                title = f"{self.base_title} ({percent:.1f}{self.unit})"
        elif self.is_disk_or_ram and total_gb is not None and used_gb is not None:
            title = (
                f"{self.base_title} "
                f"({percent:.1f}% | {used_gb:.1f}/{total_gb:.1f} GB)"
            )
        else:
            title = f"{self.base_title} ({percent:.1f}{self.unit})"
        above = percent > self.threshold
        self.ax.set_title(title, fontsize=9, fontweight='bold', pad=6)
        self.draw_idle()
        return above

    def update_threshold(self, new_threshold):
        """Обновляет пороговое значение и перерисовывает линию порога.

        Args:
            new_threshold (float): Новое пороговое значение.
        """
        self.threshold = new_threshold
        self.threshold_line.remove()
        self.threshold_line = self.ax.axhline(
            y=new_threshold, color='r', linestyle='--', alpha=0.5
        )
        self.draw_idle()

    def get_export_data(self):
        """Возвращает данные для экспорта в формате списка кортежей (время, значение).

        Returns:
            list[tuple[datetime.datetime, float]]: Список временных меток и значений.
        """
        return list(zip(self.timestamps, self.history))

    def set_dark_style(self):
        """Применяет тёмную тему оформления к графику."""
        self.ax.set_facecolor('#2b2b2c')
        self.fig.patch.set_facecolor('#2b2b2c')
        self.ax.tick_params(
            colors='white',
            left=False,
            bottom=False,
            labelleft=False,
            labelbottom=False
        )
        for spine in self.ax.spines.values():
            spine.set_color('white')
        self.ax.yaxis.label.set_color('white')
        self.ax.xaxis.label.set_color('white')
        self.ax.title.set_color('white')
        self.ax.grid(True, alpha=0.3, color='gray', axis='both')
        self.draw_idle()

    def set_light_style(self):
        """Применяет светлую тему оформления к графику."""
        self.ax.set_facecolor('#f0f0f0')
        self.fig.patch.set_facecolor('#f0f0f0')
        self.ax.tick_params(
            colors='black',
            left=False,
            bottom=False,
            labelleft=False,
            labelbottom=False
        )
        for spine in self.ax.spines.values():
            spine.set_color('black')
        self.ax.yaxis.label.set_color('black')
        self.ax.xaxis.label.set_color('black')
        self.ax.title.set_color('black')
        self.ax.grid(True, alpha=0.3, color='gray', axis='both')
        self.draw_idle()


class SystemMonitor(QtWidgets.QMainWindow):
    """Главное окно приложения системного монитора.

    Отображает графики загрузки CPU, RAM, Swap и дисков.
    Поддерживает уведомления, экспорт логов, смену темы, настройки порогов и сворачивание в трей.
    """

    def __init__(self):
        """Инициализирует главное окно и запускает инициализацию компонентов."""
        super().__init__()
        self.setWindowTitle("Системный монитор")
        self.setWindowIcon(QtGui.QIcon(resource_path('style/sys-mon.ico')))
        self.resize(1000, 700)
        self.setMinimumSize(900, 700)
        self._init()

    def _init(self):
        """Загружает настройки, инициализирует интерфейс и запускает таймер обновления."""
        self.settings = QSettings("SystemMonitor", "App")
        self.interval_ms = self.settings.value(
            "interval_ms", DEFAULT_INTERVAL_MS, type=int
        )
        self.thresholds = {
            k: self.settings.value(f"{k}_threshold", d, type=int)
            for k, d in [('cpu', 90), ('ram', 85), ('swap', 80), ('disk', 90)]
        }
        self.alerts_shown = {'cpu': False, 'ram': False, 'swap': False}
        self.swap_notified = False
        self.alert_cooldown = {}
        self.is_dark_theme = self.settings.value("is_dark_theme", False, type=bool)
        self.show_swap_anyway = self.settings.value(
            "show_swap_anyway", False, type=bool
        )
        self.minimize_to_tray = self.settings.value(
            "minimize_to_tray", True, type=bool
        )
        self.dont_show_swap_warning = self.settings.value(
            "dont_show_swap_warning", False, type=bool
        )

        try:
            psutil.swap_memory()
            self.swap_available = True
        except Exception:
            self.swap_available = False
            if not self.dont_show_swap_warning:
                self.show_swap_anyway = self._ask_show_swap_anyway()
                self.settings.setValue("show_swap_anyway", self.show_swap_anyway)
            else:
                self.show_swap_anyway = self.settings.value(
                    "show_swap_anyway", False, type=bool
                )
            logging.warning("Файл подкачки недоступен")

        self._init_ui()
        self._init_tray()
        if self.is_dark_theme:
            self._apply_dark_theme()
        else:
            self._apply_light_theme()

        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.update_metrics)
        self.timer.start(self.interval_ms)

    def _ask_show_swap_anyway(self):
        """Отображает диалог с предложением показать Swap, несмотря на его отсутствие.

        Returns:
            bool: True, если пользователь нажал "Всё равно показать", иначе False.
        """
        msg = QtWidgets.QMessageBox(self)
        msg.setIcon(QtWidgets.QMessageBox.Warning)
        msg.setWindowTitle("Внимание")
        msg.setText("Файл подкачки недоступен на вашей системе.")
        show_anyway_btn = msg.addButton("Всё равно показать", QtWidgets.QMessageBox.ActionRole)
        ok_btn = msg.addButton("Ок", QtWidgets.QMessageBox.RejectRole)
        msg.setDefaultButton(ok_btn)
        msg.setEscapeButton(ok_btn)

        dont_show_cb = QtWidgets.QCheckBox("Не показывать снова")
        msg.setCheckBox(dont_show_cb)

        msg.exec_()

        if dont_show_cb.isChecked():
            self.settings.setValue("dont_show_swap_warning", True)

        return msg.clickedButton() == show_anyway_btn

    def _init_ui(self):
        """Инициализирует пользовательский интерфейс: панель управления, графики и диски."""
        central = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        ctrl = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(ctrl)
        h.setContentsMargins(10, 5, 10, 5)
        h.addWidget(QtWidgets.QLabel("Интервал обновления (мс):"))

        self.interval_spin = QtWidgets.QSpinBox()
        self.interval_spin.setRange(1, 10000)
        self.interval_spin.setSingleStep(100)
        self.interval_spin.setValue(self.interval_ms)
        self.interval_spin.valueChanged.connect(self._change_interval)
        h.addWidget(self.interval_spin)

        self.pause_btn = QtWidgets.QPushButton("Пауза")
        self.pause_btn.setCheckable(True)
        self.pause_btn.clicked.connect(self._toggle_pause)
        h.addWidget(self.pause_btn)
        h.addStretch()

        self.theme_btn = QtWidgets.QPushButton("Тема")
        self.theme_btn.clicked.connect(self._toggle_theme)
        h.addWidget(self.theme_btn)

        self.export_btn = QtWidgets.QPushButton("Экспорт")
        self.settings_btn = QtWidgets.QPushButton("Настройки")
        self.export_btn.clicked.connect(self._export_logs)
        self.settings_btn.clicked.connect(self._show_settings_dialog)
        h.addWidget(self.export_btn)
        h.addWidget(self.settings_btn)

        layout.addWidget(ctrl)

        graphs_container = QtWidgets.QWidget()
        graphs_layout = QtWidgets.QVBoxLayout(graphs_container)
        graphs_layout.setContentsMargins(0, 0, 0, 0)

        grid = QtWidgets.QWidget()
        gl = QtWidgets.QGridLayout(grid)
        self.cpu_canvas = MplCanvas(
            "Использование ЦП (CPU)", threshold=self.thresholds['cpu'], color="#1f77b4", is_cpu=True
        )
        self.mem_canvas = MplCanvas(
            "Использование ОЗУ (RAM)", threshold=self.thresholds['ram'], color="#9467bd",
            is_disk_or_ram=True
        )
        gl.addWidget(self.cpu_canvas, 0, 0)
        gl.addWidget(self.mem_canvas, 1, 0)

        if self.swap_available or self.show_swap_anyway:
            self.swap_canvas = MplCanvas(
                "Использование файла подкачки (Swap)", threshold=self.thresholds['swap'], color="#ff7f0e"
            )
            gl.addWidget(self.swap_canvas, 2, 0)
        else:
            self.swap_canvas = None

        gl.setRowStretch(0, 1)
        gl.setRowStretch(1, 1)
        if self.swap_canvas:
            gl.setRowStretch(2, 1)
        graphs_layout.addWidget(grid)

        self.disk_container = QtWidgets.QWidget()
        self.disk_layout = QtWidgets.QHBoxLayout(self.disk_container)
        self.disk_layout.setContentsMargins(10, 0, 10, 10)
        self.disk_canvases = {}
        for part in psutil.disk_partitions(all=False):
            if 'cdrom' in part.opts.lower() or 'network' in part.fstype.lower():
                continue
            try:
                usage = psutil.disk_usage(part.mountpoint)
                if usage.total < 1024 ** 3:
                    continue
                total_gb = usage.total / (1024 ** 3)
                if sys.platform == "win32":
                    label = f"{part.device[0]}:\\"
                else:
                    label = part.mountpoint
                canvas = MplCanvas(
                    label, threshold=self.thresholds['disk'], color="#2ca02c",
                    is_disk_or_ram=True
                )
                canvas.setMinimumHeight(160)
                self.disk_layout.addWidget(canvas)
                self.disk_canvases[part.mountpoint] = (canvas, total_gb)
            except Exception:
                continue
        graphs_layout.addWidget(self.disk_container)
        layout.addWidget(graphs_container, 1)
        self.setCentralWidget(central)

    def _init_tray(self):
        """Инициализирует иконки в системном трее (CPU и RAM)."""
        self.tray_cpu = QtWidgets.QSystemTrayIcon(self)
        self.tray_ram = QtWidgets.QSystemTrayIcon(self)
        self._update_tray_icon(0, 0, 0, 0)

        menu = QtWidgets.QMenu()
        show_action = menu.addAction("Показать")
        show_action.triggered.connect(self.showNormal)
        pause = menu.addAction("Пауза")
        pause.setCheckable(True)
        pause.triggered.connect(self._toggle_pause)
        menu.addSeparator()
        settings_action = menu.addAction("Настройки")
        settings_action.triggered.connect(self._show_settings_dialog)
        menu.addAction("Выйти").triggered.connect(self._quit_app)

        self.tray_cpu.setContextMenu(menu)
        self.tray_ram.setContextMenu(menu)
        self.tray_cpu.activated.connect(self._tray_activated)
        self.tray_ram.activated.connect(self._tray_activated)

        self.tray_cpu.show()
        self.tray_ram.show()

    def _tray_activated(self, reason):
        """Обрабатывает двойной клик по иконке в трее.

        Args:
            reason (QtWidgets.QSystemTrayIcon.ActivationReason): Причина активации.
        """
        if (reason == QtWidgets.QSystemTrayIcon.DoubleClick
                and not self.isVisible()):
            self.showNormal()

    def _load_qss(self, relative_path):
        """Загружает QSS-файл стилей.

        Args:
            path (str): Путь к файлу стилей.

        Returns:
            str: Содержимое файла или пустая строка в случае ошибки.
        """
        path = resource_path(relative_path)
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            logging.warning(f"Не удалось загрузить QSS файл: {e}")
            return ""

    def _apply_dark_theme(self):
        """Применяет тёмную тему к интерфейсу и графикам."""
        qss = self._load_qss("style/dark_theme.qss")
        if qss:
            self.setStyleSheet(qss)
        self.cpu_canvas.set_dark_style()
        self.mem_canvas.set_dark_style()
        if self.swap_canvas:
            self.swap_canvas.set_dark_style()
        for canvas, _ in self.disk_canvases.values():
            canvas.set_dark_style()

    def _apply_light_theme(self):
        """Применяет светлую тему к интерфейсу и графикам."""
        qss = self._load_qss("style/default_theme.qss")
        if qss:
            self.setStyleSheet(qss)
        else:
            self.setStyleSheet("")
        self.cpu_canvas.set_light_style()
        self.mem_canvas.set_light_style()
        if self.swap_canvas:
            self.swap_canvas.set_light_style()
        for canvas, _ in self.disk_canvases.values():
            canvas.set_light_style()

    def _toggle_theme(self):
        """Переключает тему оформления между тёмной и светлой."""
        self.is_dark_theme = not self.is_dark_theme
        self.settings.setValue("is_dark_theme", self.is_dark_theme)
        if self.is_dark_theme:
            self._apply_dark_theme()
        else:
            self._apply_light_theme()

    def update_metrics(self):
        """Обновляет все системные метрики и обновляет графики и иконки в трее.

        Вызывается по таймеру.
        """
        try:
            now = datetime.now()
            cpu_percent = psutil.cpu_percent()
            cpu_freq_ghz = None
            try:
                freq = psutil.cpu_freq(percpu=False)
                if freq and hasattr(freq, 'current') and freq.current is not None:
                    cpu_freq_ghz = freq.current / 1000.0
            except (AttributeError, NotImplementedError, OSError):
                pass

            self.cpu_canvas.update_value(
                cpu_percent, cpu_freq_ghz=cpu_freq_ghz, timestamp=now
            )
            self._check_alert(
                'cpu', cpu_percent, cpu_percent > self.thresholds['cpu'], "CPU"
            )

            mem = psutil.virtual_memory()
            mem_percent = mem.percent
            mem_used_gb = mem.used / (1024 ** 3)
            mem_total_gb = mem.total / (1024 ** 3)
            self.mem_canvas.update_value(
                mem_percent,
                total_gb=mem_total_gb,
                used_gb=mem_used_gb,
                timestamp=now
            )
            self._check_alert(
                'ram', mem_percent, mem_percent > self.thresholds['ram'], "RAM"
            )

            if self.swap_canvas:
                try:
                    swap = psutil.swap_memory().percent
                    self.swap_canvas.update_value(swap, timestamp=now)
                    self._check_alert(
                        'swap', swap, swap > self.thresholds['swap'], "Swap"
                    )
                except Exception:
                    if self.show_swap_anyway:
                        self.swap_canvas.update_value(0, timestamp=now)
                    if not self.swap_notified:
                        logging.warning("Невозможно измерить файл подкачки")
                        self.swap_notified = True

            for mount, (canvas, total_gb) in self.disk_canvases.items():
                try:
                    usage = psutil.disk_usage(mount)
                    used_gb = usage.used / (1024 ** 3)
                    percent = usage.percent
                    canvas.update_value(
                        percent, total_gb=total_gb, used_gb=used_gb, timestamp=now
                    )
                    if (percent > self.thresholds['disk']
                            and mount not in self.alert_cooldown.get('disks', [])):
                        logging.warning(f"Disk {mount} usage is {percent:.1f}%")
                        QtWidgets.QMessageBox.warning(
                            self,
                            "Диск замолнен",
                            f"Диск {mount} заполнен на {percent:.1f}%\n"
                            f"Лимит: {self.thresholds['disk']}%"
                        )
                        self.alert_cooldown.setdefault('disks', []).append(mount)
                except Exception:
                    pass

            self._update_tray_icon(cpu_percent, mem_percent, mem_used_gb, mem_total_gb)

            cpu_tooltip = f"CPU: {cpu_percent:.0f}%"
            if cpu_freq_ghz is not None:
                cpu_tooltip += f" ({cpu_freq_ghz:.2f} GHz)"
            self.tray_cpu.setToolTip(cpu_tooltip)

            mem_tooltip = (
                f"RAM: {mem_percent:.0f}% ({mem_used_gb:.2f}/{mem_total_gb:.2f} GB)"
            )
            self.tray_ram.setToolTip(mem_tooltip)

            if now.minute % 5 == 0 and now.second == 0:
                self.alert_cooldown.clear()

        except Exception as e:
            logging.error(f"Ошибка: {e}")

    def _check_alert(self, metric, percent_value, is_above, name):
        """Проверяет и отображает предупреждение, если метрика превысила порог.

        Args:
            metric (str): Идентификатор метрики ('cpu', 'ram', 'swap').
            percent_value (float): Текущее значение метрики в процентах.
            is_above (bool): True, если значение превысило порог.
            name (str): Человекочитаемое название метрики.
        """
        if is_above:
            dont_show = self.settings.value(f"dont_show_{metric}", False, type=bool)
            if dont_show:
                logging.info(f"[{name}] Ошибка заглушена в связи с нажатым 'Не показывать снова'.")
                return
            logging.warning(
                f"[{name}] используется на {percent_value:.1f}% "
                f"(лимит: {self.thresholds[metric]}%)"
            )
            msg = QtWidgets.QMessageBox(self)
            msg.setIcon(QtWidgets.QMessageBox.Warning)
            msg.setWindowTitle("Внимание высокая нагрузка")
            msg.setText(
                f"{name} используется на {percent_value:.1f}%\n"
                f"Лимит: {self.thresholds[metric]}%"
            )
            dont_show_cb = QtWidgets.QCheckBox("Не показывать снова")
            msg.setCheckBox(dont_show_cb)
            msg.exec_()
            if dont_show_cb.isChecked():
                self.settings.setValue(f"dont_show_{metric}", True)
                logging.info(f"[{name}] Пользователь выбрал 'Не показывать снова'.")

    def _update_tray_icon(self, cpu, ram, ram_used, ram_total):
        """Обновляет иконки CPU и RAM в системном трее.

        Args:
            cpu (float): Загрузка CPU в процентах.
            ram (float): Загрузка RAM в процентах.
            ram_used (float): Используемая RAM в ГБ.
            ram_total (float): Общая RAM в ГБ.
        """
        p = QtGui.QPixmap(32, 32)
        p.fill(QtCore.Qt.transparent)
        painter = QtGui.QPainter(p)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setBrush(QtGui.QColor(50, 50, 50, 200))
        painter.drawEllipse(1, 1, 30, 30)
        pen = QtGui.QPen()
        pen.setWidth(3)
        pen.setColor(
            QtGui.QColor(0x1f, 0x77, 0xb4) if cpu <= 80 else QtCore.Qt.red
        )
        painter.setPen(pen)
        painter.drawArc(2, 2, 28, 28, 90 * 16, -int(cpu * 3.6) * 16)
        font_size = 12 if cpu < 100 else 8
        painter.setPen(QtCore.Qt.white)
        painter.setFont(QtGui.QFont("Arial", font_size))
        painter.drawText(QtCore.QRect(0, 0, 32, 32), QtCore.Qt.AlignCenter, f"{cpu:.0f}")
        painter.end()
        self.tray_cpu.setIcon(QtGui.QIcon(p))

        p = QtGui.QPixmap(32, 32)
        p.fill(QtCore.Qt.transparent)
        painter = QtGui.QPainter(p)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setBrush(QtGui.QColor(50, 50, 50, 200))
        painter.drawEllipse(1, 1, 30, 30)
        pen = QtGui.QPen()
        pen.setWidth(3)
        pen.setColor(
            QtGui.QColor(0x94, 0x67, 0xbd) if ram <= 85 else QtCore.Qt.red
        )
        painter.setPen(pen)
        painter.drawArc(2, 2, 28, 28, 90 * 16, -int(ram * 3.6) * 16)
        font_size = 12 if ram < 100 else 8
        painter.setPen(QtCore.Qt.white)
        painter.setFont(QtGui.QFont("Arial", font_size))
        painter.drawText(QtCore.QRect(0, 0, 32, 32), QtCore.Qt.AlignCenter, f"{ram:.0f}")
        painter.end()
        self.tray_ram.setIcon(QtGui.QIcon(p))

    def _change_interval(self, v):
        """Изменяет интервал обновления метрик.

        Args:
            v (int): Новое значение интервала в миллисекундах.
        """
        self.interval_ms = v
        self.settings.setValue("interval_ms", v)
        self.timer.setInterval(v)

    def _toggle_pause(self, checked):
        """Останавливает или возобновляет таймер обновления.

        Args:
            checked (bool): Состояние кнопки паузы.
        """
        if checked:
            self.timer.stop()
            self.pause_btn.setText("Продолжить")
        else:
            self.timer.start(self.interval_ms)
            self.pause_btn.setText("Пауза")

    def _show_settings_dialog(self):
        """Отображает диалог настройки порогов и других параметров."""
        d = QtWidgets.QDialog(self)
        d.setWindowTitle("Настройки")
        lay = QtWidgets.QFormLayout(d)
        spins = {}
        checkboxes = {}

        for m in ['cpu', 'ram', 'swap', 'disk']:
            h_layout = QtWidgets.QHBoxLayout()
            s = QtWidgets.QSpinBox()
            s.setRange(0, 100)
            s.setValue(self.thresholds[m])
            s.setSuffix("%")
            h_layout.addWidget(s)
            cb = QtWidgets.QCheckBox("Показывать предупреждения")
            cb.setChecked(not self.settings.value(f"dont_show_{m}", False, type=bool))
            h_layout.addWidget(cb)
            lay.addRow(f"{m.upper()} Лимит:", h_layout)
            spins[m] = s
            checkboxes[m] = cb

        swap_warning_cb = QtWidgets.QCheckBox("Показывать предупреждения файла подкачки")
        swap_warning_cb.setChecked(
            not self.settings.value("dont_show_swap_warning", False, type=bool)
        )
        lay.addRow(swap_warning_cb)

        minimize_to_tray_cb = QtWidgets.QCheckBox("Свернуть в трей")
        minimize_to_tray_cb.setChecked(self.minimize_to_tray)
        lay.addRow(minimize_to_tray_cb)

        b = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        b.accepted.connect(d.accept)
        b.rejected.connect(d.reject)
        lay.addRow(b)

        if d.exec_() == QtWidgets.QDialog.Accepted:
            for m, s in spins.items():
                self.thresholds[m] = s.value()
                self.settings.setValue(f"{m}_threshold", s.value())
                self.settings.setValue(f"dont_show_{m}", not checkboxes[m].isChecked())
            self.cpu_canvas.update_threshold(self.thresholds['cpu'])
            self.mem_canvas.update_threshold(self.thresholds['ram'])
            if self.swap_canvas:
                self.swap_canvas.update_threshold(self.thresholds['swap'])
            for canvas, _ in self.disk_canvases.values():
                canvas.update_threshold(self.thresholds['disk'])

            self.settings.setValue(
                "dont_show_swap_warning", not swap_warning_cb.isChecked()
            )
            self.minimize_to_tray = minimize_to_tray_cb.isChecked()
            self.settings.setValue("minimize_to_tray", self.minimize_to_tray)

            logging.info(f"Лимиты обновлены: {self.thresholds}")

    def _export_logs(self):
        """Экспортирует историю метрик в CSV-файлы."""
        log_dir = "performance_log"
        os.makedirs(log_dir, exist_ok=True)

        def safe_write_csv(filename, data):
            """Безопасно записывает данные в CSV.

            Args:
                filename (str): Имя файла.
                data (list[tuple[datetime, float]]): Данные для записи.
            """
            if not data:
                return
            path = os.path.join(log_dir, filename)
            try:
                with open(path, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.writer(f)
                    writer.writerow(['Timestamp (ISO)', 'Value (%)'])
                    for ts, val in data:
                        if ts.tzinfo is None:
                            ts = ts.replace(tzinfo=timezone.utc)
                        writer.writerow([ts.isoformat(), f"{val:.3f}"])
            except Exception as e:
                logging.error(f"Ошибка экспорта для {filename}: {e}")

        safe_write_csv("cpu_log.csv", self.cpu_canvas.get_export_data())
        safe_write_csv("ram_log.csv", self.mem_canvas.get_export_data())
        if self.swap_canvas:
            safe_write_csv("swap_log.csv", self.swap_canvas.get_export_data())
        for mount, (canvas, _) in self.disk_canvases.items():
            if sys.platform == "win32":
                name = mount[0].upper()
            else:
                name = mount.replace("/", "_").replace("\\", "_").strip("_")
                if not name:
                    name = "root"
            safe_write_csv(f"disk_log_{name}.csv", canvas.get_export_data())

        abs_path = os.path.abspath(log_dir)
        QtWidgets.QMessageBox.information(
            self,
            "Файлы успешно экспортированны",
            f"Лог файлы были экспортированны в:\n{abs_path}"
        )
        logging.info(f"Логи производительности экспортированны в '{abs_path}'")

    def _quit_app(self):
        """Завершает приложение корректно."""
        self.tray_cpu.hide()
        self.tray_ram.hide()
        self.timer.stop()
        QtWidgets.QApplication.quit()

    def closeEvent(self, e):
        """Обрабатывает закрытие окна (сворачивание в трей или выход).

        Args:
            e (QtGui.QCloseEvent): Событие закрытия.
        """
        if self.minimize_to_tray:
            e.ignore()
            self.hide()
        else:
            self._quit_app()


if __name__ == '__main__':
    app = QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    win = SystemMonitor()
    win.show()
    sys.exit(app.exec_())
