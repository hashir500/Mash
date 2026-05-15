import os
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
    QPushButton, QFrame, QGraphicsColorizeEffect, QSizePolicy, QGridLayout,
    QScrollArea, QApplication
)
from PyQt6.QtCore import Qt, QSize, pyqtSignal, QPropertyAnimation, QEasingCurve, QRect, QRectF, QPoint
from PyQt6.QtGui import QIcon, QColor, QFont, QPainter, QLinearGradient, QPainterPath, QPen
import datetime
from screentime_tracker import tracker

class HeatmapCell(QPushButton):
    def __init__(self, date_str, total_minutes, focused_minutes, parent=None):
        super().__init__(parent)
        self.date_str = date_str
        self.total_minutes = total_minutes
        self.focused_minutes = focused_minutes
        self.hover_info_callback = None
        self.setCursor(Qt.CursorShape.PointingHandCursor)
    
    def enterEvent(self, event):
        if self.hover_info_callback:
            self.hover_info_callback(self.date_str, self.total_minutes, self.focused_minutes)
        super().enterEvent(event)
    
    def leaveEvent(self, event):
        if self.hover_info_callback:
            self.hover_info_callback("", 0, 0)
        super().leaveEvent(event)

class ScreenTimeWindow(QWidget):
    def __init__(self, parent=None):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        self.setFixedSize(1400, 920)
        self._drag_pos = None
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15) 
        
        # Main background frame
        self.main_frame = QFrame()
        self.main_frame.setObjectName("mainFrame")
        self.main_layout = QVBoxLayout(self.main_frame)
        self.main_layout.setContentsMargins(50, 45, 50, 45)
        self.main_layout.setSpacing(35)
        
        # Close button
        self.close_btn = QPushButton("✕")
        self.close_btn.setFixedSize(32, 32)
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.clicked.connect(self.hide)
        self.close_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.08);
                color: rgba(255, 255, 255, 0.6);
                border-radius: 16px;
                font-size: 14px;
                border: 1px solid rgba(255,255,255,0.05);
            }
            QPushButton:hover {
                background: rgba(255, 60, 60, 0.3);
                color: white;
                border: 1px solid rgba(255,60,60,0.4);
            }
        """)
        
        # Header Row
        header_row = QHBoxLayout()
        header_row.setSpacing(20)
        
        title_layout = QVBoxLayout()
        title_layout.setSpacing(2)
        self.title_label = QLabel("Screen Time")
        self.title_label.setFont(QFont("Outfit", 36, QFont.Weight.Bold))
        self.title_label.setStyleSheet("color: white; border: none; background: transparent;")
        
        self.subtitle_label = QLabel("Your digital footprint today")
        self.subtitle_label.setFont(QFont("Inter", 13))
        self.subtitle_label.setStyleSheet("color: rgba(255,255,255,0.4); border: none; background: transparent;")
        
        title_layout.addWidget(self.title_label)
        title_layout.addWidget(self.subtitle_label)
        
        header_row.addLayout(title_layout)
        header_row.addStretch()
        
        # Day/Week Toggle
        toggle_frame = QFrame()
        toggle_frame.setFixedSize(180, 44)
        toggle_frame.setStyleSheet("""
            QFrame {
                background: rgba(255,255,255,0.04);
                border-radius: 22px;
                border: 1px solid rgba(255,255,255,0.08);
            }
        """)
        toggle_layout = QHBoxLayout(toggle_frame)
        toggle_layout.setContentsMargins(4, 4, 4, 4)
        toggle_layout.setSpacing(0)
        
        self.day_btn = QPushButton("Day")
        self.day_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.day_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.day_btn.setStyleSheet("""
            QPushButton {
                background: #238636;
                color: white;
                border-radius: 18px;
                font-weight: bold;
                font-size: 13px;
            }
        """)
        
        self.week_btn = QPushButton("Week")
        self.week_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.week_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.week_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: rgba(255,255,255,0.4);
                border-radius: 18px;
                font-size: 13px;
                font-weight: 500;
            }
            QPushButton:hover { color: white; background: rgba(255,255,255,0.03); }
        """)
        
        toggle_layout.addWidget(self.day_btn)
        toggle_layout.addWidget(self.week_btn)
        
        header_row.addWidget(toggle_frame)
        header_row.addSpacing(10)
        header_row.addWidget(self.close_btn)
        
        self.main_layout.addLayout(header_row)
        
        # Content Layout with Scroll Area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("background: transparent; border: none;")
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        
        # Scroll area custom styling
        scroll.verticalScrollBar().setStyleSheet("""
            QScrollBar:vertical {
                border: none;
                background: transparent;
                width: 8px;
                margin: 0px 0px 0px 0px;
            }
            QScrollBar::handle:vertical {
                background: rgba(255, 255, 255, 0.1);
                min-height: 20px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(255, 255, 255, 0.2);
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)
        
        content_widget = QWidget()
        content_widget.setStyleSheet("background: transparent;")
        self.content_layout = QVBoxLayout(content_widget)
        self.content_layout.setContentsMargins(0, 0, 15, 0)
        self.content_layout.setSpacing(25)
        
        # 1. Heatmap (Full Width)
        heatmap = self.create_heatmap()
        self.content_layout.addWidget(heatmap)
        
        # 2. Stats Row (Focused Time + Top Apps)
        stats_row = QHBoxLayout()
        stats_row.setSpacing(25)
        
        focused_time_card = self.create_focused_time_card()
        top_apps_card = self.create_top_apps_card()
        
        stats_row.addWidget(focused_time_card, 1)
        stats_row.addWidget(top_apps_card, 1)
        self.content_layout.addLayout(stats_row)
        
        # 3. Today Activity (Full Width)
        timeline = self.create_timeline()
        self.content_layout.addWidget(timeline)
        
        scroll.setWidget(content_widget)
        self.main_layout.addWidget(scroll)
        
        layout.addWidget(self.main_frame)
        
        self._apply_theme_styles()
        
    def _apply_theme_styles(self):
        self.setStyleSheet("""
            QFrame#mainFrame {
                background: rgba(18, 18, 24, 235);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 45px;
            }
            QLabel { color: white; background: transparent; }
        """)

    def create_heatmap(self):
        frame = QFrame()
        frame.setStyleSheet("""
            QFrame {
                background: rgba(255, 255, 255, 0.03);
                border: 1px solid rgba(255, 255, 255, 0.05);
                border-radius: 30px;
            }
        """)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(20)
        
        header_row = QHBoxLayout()
        header = QLabel("ACTIVITY HEATMAP")
        header.setFont(QFont("Inter", 11, QFont.Weight.Bold))
        header.setStyleSheet("color: rgba(255,255,255,0.4); border: none;")
        header_row.addWidget(header)
        header_row.addStretch()
        
        # Intensity Legend
        legend_layout = QHBoxLayout()
        legend_layout.setSpacing(5)
        colors = ["rgba(255,255,255,0.05)", "#0e4429", "#006d32", "#26a641", "#39d353"]
        for c in colors:
            sq = QFrame()
            sq.setFixedSize(10, 10)
            sq.setStyleSheet(f"background: {c}; border-radius: 2px;")
            legend_layout.addWidget(sq)
        header_row.addLayout(legend_layout)
        layout.addLayout(header_row)
        
        # Grid for months - Two rows of 6 months to fit width
        months_container = QVBoxLayout()
        months_container.setSpacing(35)
        
        row1 = QHBoxLayout()
        row1.setSpacing(0)
        
        row2 = QHBoxLayout()
        row2.setSpacing(0)
        
        heatmap_data = tracker.get_heatmap_data(365)
        from collections import defaultdict
        monthly_data = defaultdict(list)
        for entry in heatmap_data:
            d = datetime.datetime.strptime(entry["date"], "%Y-%m-%d")
            monthly_data[d.strftime("%Y-%m")].append(entry)
            
        current_year = datetime.datetime.now().year
        months_order = ["01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12"]
        
        for m_idx in range(0, 12):
            month_key = f"{current_year}-{months_order[m_idx]}"
            month_data = monthly_data.get(month_key, [])
            
            month_box_container = QWidget()
            month_box = QVBoxLayout(month_box_container)
            month_box.setContentsMargins(0,0,0,0)
            month_box.setSpacing(10)
            
            m_label = QLabel(datetime.datetime.strptime(month_key+"-01", "%Y-%m-%d").strftime("%b").upper())
            m_label.setStyleSheet("color: rgba(255,255,255,0.25); font-size: 11px; font-weight: 800; letter-spacing: 1px;")
            m_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            month_box.addWidget(m_label)
            
            grid = QGridLayout()
            grid.setSpacing(5)
            
            import calendar
            first_day = datetime.datetime.strptime(month_key+"-01", "%Y-%m-%d")
            start_col = (first_day.weekday() + 1) % 7
            _, days_in_month = calendar.monthrange(first_day.year, first_day.month)
            
            row, col = 0, start_col
            for d in range(1, days_in_month + 1):
                level = 0
                date_str = f"{month_key}-{d:02d}"
                for entry in month_data:
                    if entry["date"] == date_str:
                        level = entry["level"]
                        break
                
                cell = HeatmapCell(date_str, 0, 0)
                cell.setFixedSize(16, 16) # Slightly larger cells for the larger window
                cell.hover_info_callback = self.update_hover_info
                cell.setStyleSheet(f"background: {colors[level]}; border-radius: 4px; border: none;")
                grid.addWidget(cell, row, col)
                
                col += 1
                if col > 6:
                    col = 0
                    row += 1
            
            month_box.addLayout(grid)
            
            if m_idx < 6:
                if m_idx > 0: row1.addStretch(1)
                row1.addWidget(month_box_container)
            else:
                if m_idx > 6: row2.addStretch(1)
                row2.addWidget(month_box_container)
            
        months_container.addLayout(row1)
        months_container.addLayout(row2)
        layout.addLayout(months_container)
        
        self.hover_info_label = QLabel(" ")
        self.hover_info_label.setStyleSheet("color: rgba(255,255,255,0.4); font-size: 12px;")
        self.hover_info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.hover_info_label)
        
        return frame

    def update_hover_info(self, date_str, total, focused):
        if date_str:
            self.hover_info_label.setText(f"{date_str} • Optimized digital usage detected")
        else:
            self.hover_info_label.setText(" ")

    def create_focused_time_card(self):
        card = QFrame()
        card.setStyleSheet("""
            QFrame {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(35, 134, 54, 0.2), stop:1 rgba(35, 134, 54, 0.05));
                border: 1px solid rgba(35, 134, 54, 0.3);
                border-radius: 32px;
            }
        """)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(30, 30, 30, 30)
        card_layout.setSpacing(8)
        
        label = QLabel("FOCUSED TIME")
        label.setStyleSheet("color: #39d353; font-size: 11px; font-weight: 800; border: none;")
        
        time = QLabel("5h 42m")
        time.setFont(QFont("Outfit", 36, QFont.Weight.Bold))
        time.setStyleSheet("color: white; border: none;")
        
        sub = QLabel("+12% from yesterday")
        sub.setStyleSheet("color: rgba(255,255,255,0.3); font-size: 12px;")
        
        card_layout.addWidget(label)
        card_layout.addWidget(time)
        card_layout.addWidget(sub)
        return card

    def create_top_apps_card(self):
        card = QFrame()
        card.setStyleSheet("""
            QFrame {
                background: rgba(255, 255, 255, 0.04);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 32px;
            }
        """)
        apps_layout = QVBoxLayout(card)
        apps_layout.setContentsMargins(30, 30, 30, 30)
        apps_layout.setSpacing(15)
        
        h = QLabel("TOP APPS")
        h.setStyleSheet("color: rgba(255,255,255,0.4); font-size: 11px; font-weight: 800; border: none;")
        apps_layout.addWidget(h)
        
        grid_row = QHBoxLayout()
        grid_row.setSpacing(15)
        
        apps = [
            ("VS Code", "assets/laptop.svg"),
            ("Chrome", "assets/globe.svg"),
            ("Slack", "assets/coffee.svg"),
            ("Spotify", "assets/coffee.svg")
        ]
        
        for name, icon_path in apps:
            app = QFrame()
            app.setFixedSize(65, 65)
            app.setStyleSheet("background: rgba(255,255,255,0.06); border-radius: 18px; border: none;")
            app_l = QVBoxLayout(app)
            app_l.setContentsMargins(0,0,0,0)
            app_l.setAlignment(Qt.AlignmentFlag.AlignCenter)
            
            icon = QLabel()
            icon.setFixedSize(26, 26)
            icon.setPixmap(QIcon(icon_path).pixmap(26, 26))
            icon.setScaledContents(True)
            colorize = QGraphicsColorizeEffect()
            colorize.setColor(QColor(255,255,255, 200))
            icon.setGraphicsEffect(colorize)
            
            app_l.addWidget(icon)
            grid_row.addWidget(app)
        
        grid_row.addStretch()
        apps_layout.addLayout(grid_row)
        return card

    def create_timeline(self):
        frame = QFrame()
        frame.setStyleSheet("""
            QFrame {
                background: rgba(255, 255, 255, 0.03);
                border: 1px solid rgba(255, 255, 255, 0.05);
                border-radius: 32px;
            }
        """)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(25)
        
        h = QLabel("TODAY ACTIVITY")
        h.setFont(QFont("Inter", 11, QFont.Weight.Bold))
        h.setStyleSheet("color: rgba(255,255,255,0.4); border: none;")
        layout.addWidget(h)
        
        activities = [
            ("VS Code", "Coding Mash UI - Enhancing layouts", "09:15 AM", "assets/laptop.svg"),
            ("Browser", "Searching PyQt6 Animation Docs", "10:45 AM", "assets/globe.svg"),
            ("Terminal", "Git push - Automated commit", "03:45 PM", "assets/terminal.svg")
        ]
        
        for title, sub, time, icon_path in activities:
            row = QHBoxLayout()
            row.setSpacing(20)
            
            icon_box = QFrame()
            icon_box.setFixedSize(44, 44)
            icon_box.setStyleSheet("background: rgba(255,255,255,0.05); border-radius: 14px; border: none;")
            icon_box_l = QVBoxLayout(icon_box)
            icon_box_l.setContentsMargins(0,0,0,0)
            icon_box_l.setAlignment(Qt.AlignmentFlag.AlignCenter)
            
            icon = QLabel()
            icon.setFixedSize(20, 20)
            icon.setPixmap(QIcon(icon_path).pixmap(20, 20))
            colorize = QGraphicsColorizeEffect()
            colorize.setColor(QColor(255,255,255, 180))
            icon.setGraphicsEffect(colorize)
            icon_box_l.addWidget(icon)
            
            txt_l = QVBoxLayout()
            txt_l.setSpacing(2)
            t = QLabel(title)
            t.setFont(QFont("Inter", 14, QFont.Weight.Bold))
            t.setStyleSheet("color: white; border: none;")
            s = QLabel(sub)
            s.setStyleSheet("color: rgba(255,255,255,0.4); font-size: 12px; border: none;")
            txt_l.addWidget(t)
            txt_l.addWidget(s)
            
            tm = QLabel(time)
            tm.setStyleSheet("color: rgba(255,255,255,0.2); font-size: 12px; font-weight: bold; border: none;")
            
            row.addWidget(icon_box)
            row.addLayout(txt_l)
            row.addStretch()
            row.addWidget(tm)
            
            layout.addLayout(row)
            
        return frame

    # ── Mouse Events for Draggability ─────────────────────────────────────────
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton and self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        event.accept()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Outer glow/shadow
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(10, 10, -10, -10), 45, 45)
        
        # Subtle rim highlight
        p.setPen(QPen(QColor(255, 255, 255, 25), 1.5))
        p.drawPath(path)
        
    def show_animated(self):
        self.show()
        self.center_window()
        self.raise_()
        self.activateWindow()
        
    def center_window(self):
        screen = QApplication.primaryScreen().geometry()
        size = self.geometry()
        x = (screen.width() - size.width()) // 2
        y = (screen.height() - size.height()) // 2
        self.move(x, y)
