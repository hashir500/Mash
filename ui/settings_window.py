"""SettingsWindow — Premium glassmorphic settings panel for Mash."""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
    QLineEdit, QPushButton, QFrame, QGraphicsOpacityEffect,
    QScrollArea, QComboBox, QTextEdit, QGraphicsDropShadowEffect
)
from PyQt6.QtCore import Qt, pyqtSignal, QPropertyAnimation, QEasingCurve, QRect, QRectF, QTimer
from PyQt6.QtGui import QColor, QPainter, QLinearGradient, QPainterPath, QPen, QFont

class SettingsWindow(QWidget):
    closed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | 
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(500, 600)
        
        self._build_ui()
        self._setup_animation()
        self._drag_pos = None

    def _build_ui(self):
        # Outer layout to allow for shadow/border space
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(10, 10, 10, 10)

        # Background Container (This acts as our glass pane)
        self.container = QFrame()
        self.container.setObjectName("SettingsPanel")
        # We'll use the paintEvent for the background, so make the frame transparent
        self.container.setStyleSheet("background: transparent; border: none;")
        
        self.container_layout = QVBoxLayout(self.container)
        self.container_layout.setContentsMargins(30, 35, 30, 30)
        self.container_layout.setSpacing(20)

        # Header
        header_layout = QHBoxLayout()
        header = QLabel("SETTINGS")
        header.setFont(QFont("Inter", 11, QFont.Weight.Bold))
        header.setStyleSheet("color: rgba(255, 255, 255, 0.5); letter-spacing: 3px;")
        header_layout.addWidget(header)
        header_layout.addStretch()
        
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(24, 24)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.hide_animated)
        close_btn.setStyleSheet("""
            QPushButton {
                color: rgba(255, 255, 255, 0.3);
                background: transparent;
                border: none;
                font-size: 14px;
            }
            QPushButton:hover { color: #ff5555; }
        """)
        header_layout.addWidget(close_btn)
        self.container_layout.addLayout(header_layout)

        # Settings Content (Scrollable)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("background: transparent; border: none;")
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        
        content_widget = QWidget()
        content_widget.setStyleSheet("background: transparent;")
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(0, 0, 5, 0)
        content_layout.setSpacing(28)

        # ── Section: Model ──
        model_group = self._create_section("MODEL SELECTION", [
            ("Provider", QComboBox(), ["OpenRouter", "Google Gemini"]),
            ("Reasoning Model", QLineEdit("minimax/minimax-m2.5:free")),
            ("Coding Model", QLineEdit("minimax/minimax-m2.5:free")),
        ])
        content_layout.addWidget(model_group)

        # ── Section: Soul ──
        soul_group = self._create_section("AGENT SOUL", [
            ("System Instructions", QTextEdit("You are Mash, a premium minimalist AI...")),
        ])
        content_layout.addWidget(soul_group)

        # ── Section: Keys ──
        keys_group = self._create_section("CREDENTIALS", [
            ("OpenRouter API Key", QLineEdit("••••••••••••••••")),
        ])
        content_layout.addWidget(keys_group)

        content_layout.addStretch()
        self.scroll.setWidget(content_widget)
        self.container_layout.addWidget(self.scroll)

        # Footer
        self.save_btn = QPushButton("SAVE CHANGES")
        self.save_btn.setFixedHeight(44)
        self.save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.save_btn.setStyleSheet("""
            QPushButton {
                background: #6366f1;
                color: white;
                border-radius: 22px;
                font-family: 'Inter';
                font-weight: 700;
                font-size: 11px;
                letter-spacing: 1.5px;
            }
            QPushButton:hover { background: #4f46e5; }
        """)
        self.save_btn.clicked.connect(self.hide_animated)
        self.container_layout.addWidget(self.save_btn)

        self.main_layout.addWidget(self.container)

    def _create_section(self, title, fields):
        group = QFrame()
        layout = QVBoxLayout(group)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        lbl = QLabel(title)
        lbl.setFont(QFont("Inter", 9, QFont.Weight.DemiBold))
        lbl.setStyleSheet("color: #6366f1; letter-spacing: 1.5px;")
        layout.addWidget(lbl)

        for item in fields:
            name, widget = item[0], item[1]
            if isinstance(widget, QComboBox) and len(item) > 2:
                widget.addItems(item[2])
            
            f_layout = QVBoxLayout()
            f_layout.setSpacing(6)
            f_lbl = QLabel(name)
            f_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.5); font-size: 10px; font-weight: 500;")
            f_layout.addWidget(f_lbl)
            
            widget.setStyleSheet("""
                QLineEdit, QComboBox, QTextEdit {
                    background: rgba(255, 255, 255, 0.04);
                    border: 1px solid rgba(255, 255, 255, 0.08);
                    border-radius: 10px;
                    padding: 10px 14px;
                    color: white;
                    font-size: 12px;
                }
                QLineEdit:focus, QComboBox:focus, QTextEdit:focus {
                    border: 1px solid rgba(99, 102, 241, 0.4);
                    background: rgba(255, 255, 255, 0.07);
                }
            """)
            if isinstance(widget, QTextEdit):
                widget.setFixedHeight(90)
            
            f_layout.addWidget(widget)
            layout.addLayout(f_layout)

        return group

    def _setup_animation(self):
        self._fx = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._fx)
        self._anim = QPropertyAnimation(self._fx, b"opacity")
        self._anim.setDuration(250)
        self._anim.setEasingCurve(QEasingCurve.Type.OutQuad)

    def show_animated(self, pos):
        # Center horizontally under the notch
        self.move(pos.x() - self.width() // 2, pos.y() + 40)
        self.show()
        self._anim.stop()
        self._anim.setStartValue(self._fx.opacity())
        self._anim.setEndValue(1.0)
        self._anim.start()

    def hide_animated(self):
        self._anim.stop()
        self._anim.setStartValue(self._fx.opacity())
        self._anim.setEndValue(0.0)
        try:
            self._anim.finished.disconnect()
        except: pass
        self._anim.finished.connect(self.hide)
        self._anim.start()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Match the Notch Design
        rect = QRectF(self.rect()).adjusted(10, 10, -10, -10)
        corner = 28
        path = QPainterPath()
        path.addRoundedRect(rect, corner, corner)
        
        # 1. Deep Shadow
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 80))
        p.drawPath(path.translated(0, 6))

        # 2. Main Linear Gradient (Match Notch)
        bg = QLinearGradient(0, 0, 0, self.height())
        bg.setColorAt(0, QColor(24, 24, 27, 245))  # Slate-900
        bg.setColorAt(1, QColor(9, 9, 11, 252))   # Slate-950
        p.fillPath(path, bg)

        # 3. 1px Inner Glow (Premium Hardware Look)
        inner_glow = QPainterPath()
        inner_glow.addRoundedRect(rect.adjusted(0.8, 0.8, -0.8, -0.8), corner, corner)
        p.setPen(QPen(QColor(255, 255, 255, 35), 1))
        p.drawPath(inner_glow)

        # 4. Subtle Outer Border
        p.setPen(QPen(QColor(255, 255, 255, 15), 1.0))
        p.drawPath(path)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton and self._drag_pos:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
