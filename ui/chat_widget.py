"""ChatWidget — streaming message display."""
import markdown
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QScrollArea, QLabel, QFrame, QSizePolicy
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont


def _md_to_html(text: str) -> str:
    """Convert full markdown to HTML using the python-markdown library."""
    try:
        html = markdown.markdown(text, extensions=['tables', 'fenced_code', 'nl2br'])
        # Inject some basic CSS to make tables and code blocks look good in PyQt
        style = """<style>
            table { border-collapse: collapse; margin-top: 8px; margin-bottom: 8px; }
            th, td { border: 1px solid rgba(255,255,255,0.2); padding: 6px 10px; }
            th { background-color: rgba(255,255,255,0.1); font-weight: bold; }
            code { background: rgba(255,255,255,0.1); padding: 2px 4px; border-radius: 4px; font-family: monospace; }
            pre { background: rgba(0,0,0,0.3); padding: 10px; border-radius: 6px; }
            h1, h2, h3 { margin-top: 12px; margin-bottom: 6px; }
        </style>"""
        return style + html
    except Exception:
        # Fallback if markdown parsing fails during streaming
        return text.replace('\n', '<br>')


_STYLE = """
QScrollArea { background: transparent; border: none; }
QScrollBar:vertical {
    background: rgba(255,255,255,0.04);
    width: 4px; border-radius: 2px;
}
QScrollBar::handle:vertical {
    background: rgba(255,255,255,0.3);
    border-radius: 2px; min-height: 20px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }

QScrollBar:horizontal {
    background: rgba(255,255,255,0.04);
    height: 4px; border-radius: 2px;
}
QScrollBar::handle:horizontal {
    background: rgba(255,255,255,0.3);
    border-radius: 2px; min-width: 20px;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0px; }
"""


class MessageBubble(QFrame):
    """Single chat message bubble."""

    def __init__(self, role: str, parent=None):
        super().__init__(parent)
        self.role = role
        self._text = ""
        self.setWordWrap_label()

    def setWordWrap_label(self):
        self.setObjectName("bubble")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 7, 10, 7)

        self._label = QLabel()
        self._label.setWordWrap(True)
        self._label.setTextFormat(Qt.TextFormat.RichText)
        self._label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._label.setFont(QFont("Inter", 10))
        self._label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout.addWidget(self._label)

        if self.role == "user":
            self.setStyleSheet("""
                QFrame#bubble {
                    background: transparent;
                    border: none;
                    margin-top: 12px;
                }
                QLabel { color: #ffffff; }
            """)
        else:
            self.setStyleSheet("""
                QFrame#bubble {
                    background: transparent;
                    border: none;
                    margin-bottom: 12px;
                }
                QLabel { color: #b3b3b3; }
            """)

    def set_text(self, text: str):
        self._text = text
        prefix = "<b>You:</b> " if self.role == "user" else ""
        self._label.setText(prefix + _md_to_html(text))

    def append_text(self, token: str):
        self._text += token
        prefix = "<b>You:</b> " if self.role == "user" else ""
        self._label.setText(prefix + _md_to_html(self._text))


class ChatWidget(QWidget):
    """Scrollable chat history with streaming support."""
    content_size_changed = pyqtSignal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self._scroll = QScrollArea()
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setWidgetResizable(True)
        self._scroll.setStyleSheet(_STYLE)

        self._container = QWidget()
        self._container.setStyleSheet("background: transparent;")
        self._layout = QVBoxLayout(self._container)
        self._layout.setContentsMargins(10, 8, 10, 8)
        self._layout.setSpacing(8)
        self._layout.addStretch()

        self._scroll.setWidget(self._container)
        outer.addWidget(self._scroll)

        self._current_bubble: MessageBubble | None = None

    def add_user_message(self, text: str):
        bubble = MessageBubble("user")
        bubble.set_text(text)
        self._layout.insertWidget(self._layout.count() - 1, bubble)
        self._update_size()
        self._scroll_to_bottom()

    def start_assistant_message(self):
        """Begin a new streaming assistant bubble."""
        self._current_bubble = MessageBubble("assistant")
        self._current_bubble.set_text("")
        self._layout.insertWidget(self._layout.count() - 1, self._current_bubble)
        self._update_size()
        self._scroll_to_bottom()

    def append_token(self, token: str):
        if self._current_bubble:
            self._current_bubble.append_text(token)
            self._update_size()
            self._scroll_to_bottom()

    def finalize_assistant_message(self):
        self._current_bubble = None
        self._update_size()

    def _update_size(self):
        # Auto-expand height up to 400px based on content
        content_w = self._container.sizeHint().width()
        content_h = self._container.sizeHint().height() + 10
        new_h = max(40, min(content_h, 400))
        self.setMinimumHeight(new_h)
        self.content_size_changed.emit(content_w, new_h)

    def _scroll_to_bottom(self):
        QTimer.singleShot(30, lambda: self._scroll.verticalScrollBar().setValue(
            self._scroll.verticalScrollBar().maximum()
        ))
