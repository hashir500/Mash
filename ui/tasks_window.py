import os
import json
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
    QLineEdit, QPushButton, QFrame, QGraphicsOpacityEffect,
    QScrollArea, QCheckBox, QTextEdit, QDateTimeEdit, QCalendarWidget
)
from PyQt6.QtCore import Qt, pyqtSignal, QPropertyAnimation, QEasingCurve, QRect, QRectF, QPoint, QDateTime, QObject, QEvent
from PyQt6.QtGui import QColor, QPainter, QLinearGradient, QPainterPath, QPen, QFont, QRegion

class CalendarFilter(QObject):
    def eventFilter(self, obj, event):
        if event.type() in [QEvent.Type.Show, QEvent.Type.Resize]:
            path = QPainterPath()
            path.addRoundedRect(QRectF(obj.rect()), 16, 16)
            obj.setMask(QRegion(path.toFillPolygon().toPolygon()))
        return super().eventFilter(obj, event)

class TaskItem(QFrame):
    toggled = pyqtSignal(bool)
    deleted = pyqtSignal()
    
    def __init__(self, text, done=False, description="", deadline="", theme_colors=None):
        super().__init__()
        self.theme = theme_colors or {"text": "#ffffff", "subtext": "rgba(255,255,255,0.4)", "accent": "#6366f1"}
        self._done = done
        
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(15, 12, 15, 12)
        self.layout.setSpacing(4)
        
        # Top Row: Checkbox + Title + Delete
        top_row = QHBoxLayout()
        top_row.setSpacing(12)
        
        self.check = QCheckBox()
        self.check.setChecked(done)
        self.check.setCursor(Qt.CursorShape.PointingHandCursor)
        self.check.stateChanged.connect(self._on_toggle)
        
        self.title_label = QLabel(text)
        self.title_label.setFont(QFont("Inter", 13, QFont.Weight.Bold))
        self.title_label.setWordWrap(True)
        
        self.del_btn = QPushButton("✕")
        self.del_btn.setFixedSize(20, 20)
        self.del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.del_btn.clicked.connect(self.deleted.emit)
        self.del_btn.setStyleSheet("background: transparent; color: rgba(255,255,255,0.2); border: none; font-size: 11px;")
        
        top_row.addWidget(self.check)
        top_row.addWidget(self.title_label, 1)
        top_row.addWidget(self.del_btn)
        self.layout.addLayout(top_row)
        
        # Mid Row: Description
        if description:
            self.desc_label = QLabel(description)
            self.desc_label.setWordWrap(True)
            self.desc_label.setStyleSheet(f"color: {self.theme['subtext']}; font-size: 11px; margin-left: 32px;")
            self.layout.addWidget(self.desc_label)
        
        # Bottom Row: Deadline
        if deadline:
            self.dead_label = QLabel(deadline)
            self.dead_label.setStyleSheet(f"color: {self.theme['accent']}; font-size: 10px; font-weight: 600; margin-left: 32px;")
            self.layout.addWidget(self.dead_label)
            
        self.setObjectName("taskItem")
        self._update_style()

    def _on_toggle(self, state):
        self._done = (state == 2)
        self._update_style()
        self.toggled.emit(self._done)

    def _update_style(self):
        opacity = "0.4" if self._done else "1.0"
        decor = "line-through" if self._done else "none"
        self.title_label.setStyleSheet(f"color: {self.theme['text']}; text-decoration: {decor}; opacity: {opacity};")
        self.setStyleSheet(f"""
            QFrame#taskItem {{ 
                background: rgba(128, 128, 128, 0.05); 
                border: 1px solid rgba(255,255,255,0.05);
                border-radius: 14px; 
            }}
            QFrame#taskItem:hover {{
                background: rgba(128, 128, 128, 0.08);
                border: 1px solid rgba(255,255,255,0.1);
            }}
            QCheckBox::indicator {{
                width: 18px;
                height: 18px;
                border: 2px solid {self.theme['subtext']};
                border-radius: 5px;
                background: transparent;
            }}
            QCheckBox::indicator:checked {{
                background-color: {self.theme['accent']};
                border-color: {self.theme['accent']};
            }}
            QCheckBox::indicator:hover {{
                border-color: {self.theme['accent']};
            }}
        """)

class TasksWindow(QWidget):
    def __init__(self, parent=None):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | 
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(400, 560)
        
        self._is_hiding = False
        self._theme = "dark"
        self._theme_colors = {
            "bg_top": QColor(24, 24, 27, 248),
            "bg_bot": QColor(9, 9, 11, 255),
            "text": "#ffffff",
            "subtext": "rgba(255,255,255,0.4)",
            "accent": "#6366f1"
        }
        
        self._build_ui()
        self.apply_theme(self._theme) # Initialize styling
        self._setup_animation()
        self._drag_pos = None

    def _build_ui(self):
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(10, 10, 10, 10)

        self.container = QFrame()
        self.container.setObjectName("container")
        self.container_layout = QVBoxLayout(self.container)
        self.container_layout.setContentsMargins(25, 25, 25, 25)
        self.container_layout.setSpacing(15)

        # Header
        header = QHBoxLayout()
        title = QLabel("Tasks")
        title.setFont(QFont("Inter", 18, QFont.Weight.Bold))
        title.setStyleSheet("color: white;")
        
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(28, 28)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.hide_animated)
        close_btn.setStyleSheet("background: transparent; color: rgba(255,255,255,0.3); border: none; font-size: 16px;")
        
        header.addWidget(title)
        header.addStretch()
        header.addWidget(close_btn)
        self.container_layout.addLayout(header)

        # Enhanced Input Area
        self.input_box = QFrame()
        self.input_box.setStyleSheet("background: rgba(128, 128, 128, 0.08); border-radius: 16px; border: 1px solid rgba(255,255,255,0.1);")
        input_v_layout = QVBoxLayout(self.input_box)
        input_v_layout.setContentsMargins(12, 12, 12, 12)
        input_v_layout.setSpacing(10)

        # Row 1: Main Title + Add Button
        row1 = QHBoxLayout()
        self.task_input = QLineEdit()
        self.task_input.setPlaceholderText("What needs to be done?")
        self.task_input.setStyleSheet("background: transparent; border: none; color: white; font-size: 14px; font-weight: 500;")
        self.task_input.returnPressed.connect(self._add_task_from_input)
        
        self.add_btn = QPushButton("+")
        self.add_btn.setFixedSize(36, 36)
        self.add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_btn.setStyleSheet("background: #6366f1; color: white; border-radius: 10px; font-size: 20px; font-weight: bold;")
        self.add_btn.clicked.connect(self._add_task_from_input)
        
        row1.addWidget(self.task_input)
        row1.addWidget(self.add_btn)
        input_v_layout.addLayout(row1)

        # Row 2: Optional Details (Description + Deadline)
        details_row = QHBoxLayout()
        self.desc_input = QLineEdit()
        self.desc_input.setPlaceholderText("Description...")
        self.desc_input.setStyleSheet("background: rgba(255,255,255,0.05); border-radius: 8px; padding: 5px 10px; color: rgba(255,255,255,0.6); font-size: 11px;")
        
        self.dead_input = QDateTimeEdit(QDateTime.currentDateTime())
        self.dead_input.setCalendarPopup(True)
        self.dead_input.setDisplayFormat("MMM d, h:mm AP")
        self.dead_input.setMinimumDateTime(QDateTime.currentDateTime())
        self.dead_input.setObjectName("deadInput")
        
        details_row.addWidget(self.desc_input, 3)
        details_row.addWidget(self.dead_input, 2)
        input_v_layout.addLayout(details_row)

        self.container_layout.addWidget(self.input_box)

        # Scroll Area
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("background: transparent; border: none;")
        
        self.list_container = QWidget()
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(0, 5, 0, 5)
        self.list_layout.setSpacing(12)
        self.list_layout.addStretch()
        
        self.scroll.setWidget(self.list_container)
        self.container_layout.addWidget(self.scroll)

        self.main_layout.addWidget(self.container)

        self._tasks_data = []
        self._load_tasks()

    def _setup_animation(self):
        self._fx = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._fx)
        self._anim = QPropertyAnimation(self._fx, b"opacity")
        self._anim.setDuration(250)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.finished.connect(self._on_anim_finished)
        self._fx.setOpacity(0.0)

    def show_animated(self, pos=None):
        if pos:
            self.move(pos.x() - self.width() // 2, pos.y() + 50)
        self.show()
        self.raise_()
        self.activateWindow()
        self._is_hiding = False
        self._anim.stop()
        self._anim.setStartValue(self._fx.opacity())
        self._anim.setEndValue(1.0)
        self._anim.start()

    def hide_animated(self):
        self._is_hiding = True
        self._anim.stop()
        self._anim.setStartValue(self._fx.opacity())
        self._anim.setEndValue(0.0)
        self._anim.start()

    def _on_anim_finished(self):
        if self._is_hiding:
            self.hide()

    def apply_theme(self, theme: str):
        self._theme = theme
        THEMES = {
            "dark": {
                "bg_top":  QColor(24, 24, 27, 248),
                "bg_bot":  QColor(9, 9, 11, 255),
                "text":    "#ffffff",
                "subtext": "rgba(255, 255, 255, 0.4)",
                "accent":  "#6366f1",
            },
            "light": {
                "bg_top":  QColor(255, 255, 255, 255),
                "bg_bot":  QColor(255, 255, 255, 255),
                "text":    "#000000",
                "subtext": "rgba(0, 0, 0, 0.4)",
                "accent":  "#4f46e5",
            },
        }
        t = THEMES.get(theme, THEMES["dark"])
        self._theme_colors = t
        
        self.container.setStyleSheet(f"""
            QFrame#container {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {t['bg_top'].name()}, stop:1 {t['bg_bot'].name()});
                border: 1px solid {t['subtext']};
                border-radius: 28px;
            }}
        """)

        # Style for QDateTimeEdit and its internal buttons
        dt_style = f"""
            QDateTimeEdit {{
                background: rgba(255,255,255,0.05);
                border-radius: 8px;
                padding: 5px 5px;
                color: {t['text']};
                font-size: 10px;
            }}
            QDateTimeEdit::drop-down {{
                border: none;
                width: 22px;
                background: rgba(255,255,255,0.03);
                border-top-right-radius: 8px;
                border-bottom-right-radius: 8px;
            }}
            QDateTimeEdit::down-arrow {{
                border: none;
                width: 8px;
                height: 8px;
            }}
        """
        self.dead_input.setStyleSheet(dt_style)
        
        # Comprehensive Calendar Widget styling
        calendar = self.dead_input.calendarWidget()
        calendar.setGridVisible(False)
        calendar.setVerticalHeaderFormat(calendar.VerticalHeaderFormat.NoVerticalHeader)
        
        cal_qss = f"""
            QCalendarWidget {{
                background-color: {t['bg_bot'].name()};
                border: 1px solid {t['subtext']};
                border-radius: 15px;
            }}
            QCalendarWidget QWidget#qt_calendar_navigationbar {{
                background-color: {t['bg_top'].name()};
                border-top-left-radius: 15px;
                border-top-right-radius: 15px;
            }}
            QCalendarWidget QToolButton {{
                color: {t['text']};
                background-color: transparent;
                border: none;
                font-weight: bold;
                font-size: 11px;
                padding: 4px;
            }}
            QCalendarWidget QToolButton:hover {{
                background-color: rgba(255, 255, 255, 0.05);
                border-radius: 4px;
            }}
            QCalendarWidget QAbstractItemView {{
                background-color: {t['bg_bot'].name()};
                selection-background-color: {t['accent']};
                selection-color: white;
                color: {t['text']};
                outline: none;
                border-bottom-left-radius: 15px;
                border-bottom-right-radius: 15px;
            }}
            QCalendarWidget QHeaderView {{
                background-color: {t['bg_bot'].name()};
                color: {t['subtext']};
            }}
            /* Specific fix for the white header area */
            QCalendarWidget QWidget {{
                alternate-background-color: {t['bg_bot'].name()};
                border-radius: 15px;
            }}
            #qt_calendar_prevmonth {{ qproperty-text: "<"; }}
            #qt_calendar_nextmonth {{ qproperty-text: ">"; }}
        """
        calendar.setStyleSheet(cal_qss)
        calendar.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        # Install the rounding filter
        if not hasattr(self, "_cal_filter"):
            self._cal_filter = CalendarFilter(self)
            calendar.installEventFilter(self._cal_filter)
        
        # Manually force some colors on internal components that QSS sometimes misses
        from PyQt6.QtGui import QTextCharFormat
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(t['text']))
        calendar.setHeaderTextFormat(fmt)
        
        # Weekend colors
        wk_fmt = QTextCharFormat()
        wk_fmt.setForeground(QColor(t['accent']))
        calendar.setWeekdayTextFormat(Qt.DayOfWeek.Saturday, wk_fmt)
        calendar.setWeekdayTextFormat(Qt.DayOfWeek.Sunday, wk_fmt)
        
        # Update existing items
        for i in range(self.list_layout.count()):
            item = self.list_layout.itemAt(i).widget()
            if isinstance(item, TaskItem):
                item.theme = t
                item._update_style()

    def _add_task_from_input(self):
        text = self.task_input.text().strip()
        desc = self.desc_input.text().strip()
        # Only add deadline if it's in the future (simple heuristic)
        dead = self.dead_input.dateTime().toString("MMM d, h:mm AP")
        if text:
            self.add_task(text, description=desc, deadline=dead)
            self.task_input.clear()
            self.desc_input.clear()
            self.dead_input.setDateTime(QDateTime.currentDateTime())

    def add_task(self, text, done=False, description="", deadline=""):
        task_obj = {"text": text, "done": done, "description": description, "deadline": deadline}
        self._tasks_data.append(task_obj)
        self._render_task(task_obj)
        self._save_tasks()

    def _render_task(self, task_obj):
        item = TaskItem(
            task_obj["text"], 
            task_obj["done"], 
            task_obj.get("description", ""), 
            task_obj.get("deadline", ""), 
            self._theme_colors
        )
        self.list_layout.insertWidget(self.list_layout.count() - 1, item)
        item.toggled.connect(lambda d: self._on_task_toggled(task_obj, d))
        item.deleted.connect(lambda: self._on_task_deleted(task_obj, item))

    def _on_task_toggled(self, task_obj, done):
        task_obj["done"] = done
        self._save_tasks()

    def _on_task_deleted(self, task_obj, item_widget):
        if task_obj in self._tasks_data:
            self._tasks_data.remove(task_obj)
        item_widget.deleteLater()
        self._save_tasks()

    def _load_tasks(self):
        try:
            path = os.path.join(os.path.dirname(__file__), "..", "ai", "tasks.json")
            if os.path.exists(path):
                with open(path, "r") as f:
                    self._tasks_data = json.load(f)
                    for task in self._tasks_data:
                        self._render_task(task)
        except Exception as e:
            print(f"Error loading tasks: {e}")

    def _save_tasks(self):
        try:
            path = os.path.join(os.path.dirname(__file__), "..", "ai", "tasks.json")
            with open(path, "w") as f:
                json.dump(self._tasks_data, f, indent=2)
        except Exception as e:
            print(f"Error saving tasks: {e}")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton and self._drag_pos:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
