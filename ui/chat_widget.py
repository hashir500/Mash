"""ChatWidget — streaming message display with PDF export."""
import os
import re
import datetime
import markdown
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea, QLabel,
    QFrame, QSizePolicy, QPushButton, QFileDialog, QMessageBox
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont


def _md_to_html(text: str) -> str:
    try:
        html = markdown.markdown(text, extensions=['tables', 'fenced_code', 'nl2br'])
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
        return text.replace('\n', '<br>')


def _strip_non_latin1(text: str) -> str:
    """Remove characters that Helvetica can't render (emojis, CJK, etc.)."""
    return text.encode('latin-1', errors='ignore').decode('latin-1')


def _export_to_pdf(text: str, parent=None) -> str | None:
    """Converts raw markdown text to a clean PDF. Returns saved path or None."""
    try:
        from fpdf import FPDF

        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        default_name = os.path.expanduser(f"~/Downloads/Mash_Export_{ts}.pdf")

        path, _ = QFileDialog.getSaveFileName(
            parent, "Save as PDF", default_name, "PDF Files (*.pdf)"
        )
        if not path:
            return None

        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=20)
        pdf.add_page()
        pdf.set_margins(20, 20, 20)

        # Header
        pdf.set_font("Helvetica", "B", 18)
        pdf.set_text_color(20, 20, 20)
        pdf.cell(0, 12, "Mash Export", ln=True)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(120, 120, 120)
        pdf.cell(0, 6, datetime.datetime.now().strftime("%B %d, %Y  %H:%M"), ln=True)
        pdf.ln(4)
        pdf.set_draw_color(180, 180, 180)
        pdf.line(20, pdf.get_y(), 190, pdf.get_y())
        pdf.ln(6)

        safe_w = pdf.w - pdf.l_margin - pdf.r_margin

        def clean_inline(s: str) -> str:
            s = re.sub(r'\*\*\*(.+?)\*\*\*', r'\1', s)
            s = re.sub(r'\*\*(.+?)\*\*', r'\1', s)
            s = re.sub(r'\*(.+?)\*', r'\1', s)
            s = re.sub(r'__(.+?)__', r'\1', s)
            s = re.sub(r'_(.+?)_', r'\1', s)
            s = re.sub(r'`(.+?)`', r'\1', s)
            s = re.sub(r'\[(.+?)\]\(.+?\)', r'\1', s)
            s = s.replace('\u00bd', '1/2').replace('\u00bc', '1/4').replace('\u00be', '3/4')
            return s.strip()

        def is_table_row(s):
            return s.startswith('|') and s.endswith('|')

        def is_table_sep(s):
            return bool(re.match(r'^\|[\s\-:|]+\|', s))

        def parse_table_row(s):
            return [clean_inline(c) for c in s.strip('|').split('|')]

        def render_table(pdf, rows, safe_w):
            """Draw a markdown table as a proper PDF grid."""
            if not rows:
                return
            # Determine column count from the widest row
            num_cols = max(len(r) for r in rows)
            if num_cols == 0:
                return
            col_w = safe_w / num_cols

            for row_idx, row in enumerate(rows):
                # Pad short rows
                while len(row) < num_cols:
                    row.append('')
                is_header = (row_idx == 0)

                # Measure max needed height for this row (for multi-line cells)
                pdf.set_font("Helvetica", "B" if is_header else "", 9)
                line_h = 5.5
                row_h = line_h  # minimal default

                x_start = pdf.l_margin
                y_start = pdf.get_y()

                # Check if row would overflow page
                if y_start + row_h > pdf.h - pdf.b_margin:
                    pdf.add_page()
                    y_start = pdf.get_y()

                for col_idx, cell in enumerate(row[:num_cols]):
                    x = x_start + col_idx * col_w
                    pdf.set_xy(x, y_start)

                    if is_header:
                        pdf.set_fill_color(230, 230, 235)
                        pdf.set_font("Helvetica", "B", 9)
                        pdf.set_text_color(20, 20, 20)
                        pdf.cell(col_w, row_h + 2, cell[:40], border=1, align='C', fill=True)
                    else:
                        pdf.set_fill_color(255, 255, 255)
                        pdf.set_font("Helvetica", "", 9)
                        pdf.set_text_color(40, 40, 40)
                        fill = (row_idx % 2 == 0)
                        if fill:
                            pdf.set_fill_color(248, 248, 252)
                        pdf.cell(col_w, row_h + 2, cell[:40], border=1, align='L', fill=fill)

                pdf.set_xy(x_start, y_start + row_h + 2)
            pdf.ln(4)

        # ── First pass: group lines into typed blocks ─────────────────────
        blocks = []  # Each: ('type', content)
        raw_lines = text.split('\n')
        i = 0
        in_code = False
        code_buf = []

        while i < len(raw_lines):
            line = _strip_non_latin1(raw_lines[i])
            stripped = line.strip()

            if stripped.startswith('```'):
                if in_code:
                    blocks.append(('code', '\n'.join(code_buf)))
                    code_buf = []
                    in_code = False
                else:
                    in_code = True
                i += 1
                continue

            if in_code:
                code_buf.append(line.rstrip())
                i += 1
                continue

            # Collect a table block
            if is_table_row(stripped):
                table_rows = []
                while i < len(raw_lines):
                    tline = _strip_non_latin1(raw_lines[i]).strip()
                    if is_table_row(tline):
                        if not is_table_sep(tline):
                            table_rows.append(parse_table_row(tline))
                        i += 1
                    else:
                        break
                if table_rows:
                    blocks.append(('table', table_rows))
                continue

            blocks.append(('line', stripped))
            i += 1

        if in_code and code_buf:
            blocks.append(('code', '\n'.join(code_buf)))

        # ── Second pass: render each block ────────────────────────────────
        for btype, bcontent in blocks:
            try:
                if btype == 'table':
                    render_table(pdf, bcontent, safe_w)

                elif btype == 'code':
                    pdf.set_font("Courier", "", 9)
                    pdf.set_text_color(60, 60, 60)
                    pdf.set_fill_color(240, 240, 240)
                    for cline in bcontent.split('\n'):
                        if cline.strip():
                            pdf.multi_cell(safe_w, 5, cline, fill=True)
                        else:
                            pdf.ln(3)
                    pdf.ln(2)

                elif btype == 'line':
                    s = bcontent
                    if not s:
                        pdf.ln(3)
                    elif re.match(r'^-{3,}$', s) or re.match(r'^\*{3,}$', s):
                        pdf.set_draw_color(180, 180, 180)
                        pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + safe_w, pdf.get_y())
                        pdf.ln(4)
                    elif s.startswith('#### '):
                        c = clean_inline(s[5:])
                        if c:
                            pdf.set_font("Helvetica", "B", 11); pdf.set_text_color(30, 30, 30)
                            pdf.multi_cell(safe_w, 6, c); pdf.ln(1)
                    elif s.startswith('### '):
                        c = clean_inline(s[4:])
                        if c:
                            pdf.set_font("Helvetica", "B", 12); pdf.set_text_color(20, 20, 20)
                            pdf.multi_cell(safe_w, 7, c); pdf.ln(2)
                    elif s.startswith('## '):
                        c = clean_inline(s[3:])
                        if c:
                            pdf.set_font("Helvetica", "B", 14); pdf.set_text_color(15, 15, 15)
                            pdf.multi_cell(safe_w, 8, c); pdf.ln(2)
                    elif s.startswith('# '):
                        c = clean_inline(s[2:])
                        if c:
                            pdf.set_font("Helvetica", "B", 16); pdf.set_text_color(10, 10, 10)
                            pdf.multi_cell(safe_w, 10, c); pdf.ln(3)
                    elif re.match(r'^[-*+] ', s):
                        c = clean_inline(s[2:])
                        if c:
                            pdf.set_font("Helvetica", "", 10); pdf.set_text_color(40, 40, 40)
                            pdf.set_x(pdf.l_margin + 5)
                            pdf.multi_cell(safe_w - 5, 6, f"- {c}")
                    elif re.match(r'^\d+[.)]\s', s):
                        num = re.match(r'^(\d+)', s).group(1)
                        c = clean_inline(re.sub(r'^\d+[.)]\s+', '', s))
                        if c:
                            pdf.set_font("Helvetica", "", 10); pdf.set_text_color(40, 40, 40)
                            pdf.set_x(pdf.l_margin + 5)
                            pdf.multi_cell(safe_w - 5, 6, f"{num}. {c}")
                    else:
                        c = clean_inline(s)
                        if c:
                            pdf.set_font("Helvetica", "", 10); pdf.set_text_color(40, 40, 40)
                            pdf.multi_cell(safe_w, 6, c)
            except Exception:
                pdf.ln(2)

        pdf.output(path)


        return path


    except Exception as e:
        QMessageBox.critical(parent, "Export Failed", f"Could not export PDF:\n{e}")
        return None



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
    """Single chat message with optional export button for assistant messages."""

    def __init__(self, role: str, parent=None):
        super().__init__(parent)
        self.role = role
        self._text = ""
        self._build_ui()

    def _build_ui(self):
        self.setObjectName("bubble")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 7, 10, 7)
        outer.setSpacing(4)

        self._label = QLabel()
        self._label.setWordWrap(True)
        self._label.setTextFormat(Qt.TextFormat.RichText)
        self._label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._label.setFont(QFont("Inter", 10))
        self._label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        outer.addWidget(self._label)

        if self.role == "user":
            self.setStyleSheet("""
                QFrame#bubble { background: transparent; border: none; margin-top: 12px; }
                QLabel { color: #ffffff; }
            """)
        else:
            self.setStyleSheet("""
                QFrame#bubble { background: transparent; border: none; margin-bottom: 4px; }
                QLabel { color: #b3b3b3; }
            """)
            # Export button row (hidden until finalized)
            btn_row = QHBoxLayout()
            btn_row.setContentsMargins(0, 0, 0, 0)
            self._export_btn = QPushButton("⬇ Export PDF")
            self._export_btn.setFixedHeight(26)
            self._export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._export_btn.setStyleSheet("""
                QPushButton {
                    background: rgba(255,255,255,0.06);
                    color: rgba(255,255,255,0.55);
                    border: 1px solid rgba(255,255,255,0.12);
                    border-radius: 13px;
                    padding: 0 14px;
                    font-size: 10px;
                    font-family: Inter;
                }
                QPushButton:hover {
                    background: rgba(255,255,255,0.13);
                    color: rgba(255,255,255,0.9);
                    border: 1px solid rgba(255,255,255,0.25);
                }
                QPushButton:pressed { background: rgba(255,255,255,0.2); }
            """)
            self._export_btn.setVisible(False)
            self._export_btn.clicked.connect(self._do_export)
            btn_row.addWidget(self._export_btn)
            btn_row.addStretch()
            outer.addLayout(btn_row)

    def set_text(self, text: str):
        self._text = text
        prefix = "<b>You:</b> " if self.role == "user" else ""
        self._label.setText(prefix + _md_to_html(text))

    def append_text(self, token: str):
        self._text += token
        prefix = "<b>You:</b> " if self.role == "user" else ""
        self._label.setText(prefix + _md_to_html(self._text))

    def show_export_button(self):
        if self.role == "assistant" and self._text.strip():
            self._export_btn.setVisible(True)

    def _do_export(self):
        path = _export_to_pdf(self._text, parent=self)
        if path:
            self._export_btn.setText("✓ Saved!")
            QTimer.singleShot(3000, lambda: self._export_btn.setText("⬇ Export PDF"))


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
        if self._current_bubble:
            self._current_bubble.show_export_button()
        self._current_bubble = None
        self._update_size()

    def _update_size(self):
        content_w = self._container.sizeHint().width()
        content_h = self._container.sizeHint().height() + 10
        new_h = max(40, min(content_h, 400))
        self.setMinimumHeight(new_h)
        self.content_size_changed.emit(content_w, new_h)

    def _scroll_to_bottom(self):
        QTimer.singleShot(30, lambda: self._scroll.verticalScrollBar().setValue(
            self._scroll.verticalScrollBar().maximum()
        ))
