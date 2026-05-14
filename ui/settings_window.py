"""SettingsWindow — Premium glassmorphic settings panel with sidebar navigation."""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
    QLineEdit, QPushButton, QFrame, QGraphicsOpacityEffect,
    QScrollArea, QComboBox, QTextEdit, QStackedWidget, QCheckBox, QMenu
)
from PyQt6.QtCore import Qt, pyqtSignal, QPropertyAnimation, QEasingCurve, QRect, QRectF, QTimer, QPoint
from PyQt6.QtGui import QColor, QPainter, QLinearGradient, QPainterPath, QPen, QFont

class SettingsWindow(QWidget):
    branding_changed = pyqtSignal(str, str) # mode, custom_text
    animation_changed = pyqtSignal(str)     # anim_mode
    spotify_toggled = pyqtSignal(bool)      # enabled
    lock_notch_toggled = pyqtSignal(bool)   # locked
    config_updated = pyqtSignal(dict)       # general config including models and api key
    theme_changed = pyqtSignal(str)         # dark / light / neon
    char_anim_changed = pyqtSignal(str)     # orb / pulse / orbit

    def __init__(self, parent=None):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | 
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(680, 580) # Wider for sidebar
        
        self._is_hiding = False
        self._build_ui()
        self._setup_animation()
        self._drag_pos = None

    def _build_ui(self):
        # Outer Layout
        self.main_layout = QHBoxLayout(self)
        self.main_layout.setContentsMargins(10, 10, 10, 10)

        # Glass Container
        self.container = QFrame()
        self.container.setStyleSheet("background: transparent; border: none;")
        self.container_layout = QHBoxLayout(self.container)
        self.container_layout.setContentsMargins(0, 0, 0, 0)
        self.container_layout.setSpacing(0)

        # ── SIDEBAR ──
        self.sidebar = QFrame()
        self.sidebar.setFixedWidth(180)
        self.sidebar.setStyleSheet("background: rgba(255, 255, 255, 0.02); border-right: 1px solid rgba(255, 255, 255, 0.05);")
        self.sidebar_layout = QVBoxLayout(self.sidebar)
        self.sidebar_layout.setContentsMargins(20, 40, 20, 20)
        self.sidebar_layout.setSpacing(10)

        # Sidebar Title
        sb_title = QLabel("GLOBAL")
        sb_title.setStyleSheet("color: rgba(255, 255, 255, 0.2); font-weight: 700; font-size: 9px; letter-spacing: 1.5px;")
        self.sidebar_layout.addWidget(sb_title)

        self.btn_gen = self._create_sidebar_btn("General", True)
        self.btn_mod = self._create_sidebar_btn("Models", False)
        self.btn_cust = self._create_sidebar_btn("Customization", False)
        
        self.sidebar_layout.addWidget(self.btn_gen)
        self.sidebar_layout.addWidget(self.btn_mod)
        self.sidebar_layout.addWidget(self.btn_cust)
        
        self.sidebar_layout.addStretch()

        # Footer branding in sidebar
        sb_footer = QLabel("MASH 2.0")
        sb_footer.setStyleSheet("color: rgba(255, 255, 255, 0.1); font-size: 9px; letter-spacing: 2px;")
        self.sidebar_layout.addWidget(sb_footer)

        self.container_layout.addWidget(self.sidebar)

        # ── MAIN CONTENT AREA ──
        self.content_area = QFrame()
        self.content_layout = QVBoxLayout(self.content_area)
        self.content_layout.setContentsMargins(40, 40, 40, 40)
        self.content_layout.setSpacing(0)

        # Top Bar (Header + Close)
        header_layout = QHBoxLayout()
        self.lbl_title = QLabel("General Settings")
        self.lbl_title.setFont(QFont("Inter", 13, QFont.Weight.Bold))
        self.lbl_title.setStyleSheet("color: #ffffff;")
        header_layout.addWidget(self.lbl_title)
        header_layout.addStretch()
        
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(24, 24)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.hide_animated)
        close_btn.setStyleSheet("color: rgba(255,255,255,0.3); background:transparent; border:none; font-size:14px;")
        header_layout.addWidget(close_btn)
        self.content_layout.addLayout(header_layout)
        self.content_layout.addSpacing(30)

        # Stacked Widget
        self.stack = QStackedWidget()
        self.tab_gen = self._init_general_tab()
        self.tab_mod = self._init_models_tab()
        self.tab_cust = self._init_customization_tab()
        self.stack.addWidget(self.tab_gen)
        self.stack.addWidget(self.tab_mod)
        self.stack.addWidget(self.tab_cust)
        self.content_layout.addWidget(self.stack)

        # Bottom Bar (Save Button)
        self.content_layout.addSpacing(20)
        self.save_btn = QPushButton("SAVE CHANGES")
        self.save_btn.setFixedHeight(44)
        self.save_btn.setFixedWidth(160)
        self.save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.save_btn.setStyleSheet("""
            QPushButton {
                background: #6366f1; color: white; border-radius: 12px;
                font-family: 'Inter'; font-weight: 700; font-size: 10px; letter-spacing: 1px;
            }
            QPushButton:hover { background: #4f46e5; }
        """)
        self.save_btn.clicked.connect(self._save_and_close)
        self.content_layout.addWidget(self.save_btn, alignment=Qt.AlignmentFlag.AlignRight)

        self.container_layout.addWidget(self.content_area)
        self.main_layout.addWidget(self.container)

        # Connect Sidebar
        self.btn_gen.clicked.connect(lambda: self._switch_tab(0))
        self.btn_mod.clicked.connect(lambda: self._switch_tab(1))
        self.btn_cust.clicked.connect(lambda: self._switch_tab(2))

    def _create_sidebar_btn(self, text, active):
        btn = QPushButton(text)
        btn.setCheckable(True)
        btn.setChecked(active)
        btn.setFixedHeight(36)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_sb_btn_style(btn, active)
        return btn

    def _update_sb_btn_style(self, btn, active):
        if active:
            btn.setStyleSheet("""
                QPushButton {
                    background: rgba(255, 255, 255, 0.08); color: #ffffff;
                    border: none; border-radius: 8px; text-align: left;
                    padding-left: 12px; font-size: 11px; font-weight: 600;
                }
            """)
        else:
            btn.setStyleSheet("""
                QPushButton {
                    background: transparent; color: rgba(255, 255, 255, 0.4);
                    border: none; border-radius: 8px; text-align: left;
                    padding-left: 12px; font-size: 11px;
                }
                QPushButton:hover { background: rgba(255, 255, 255, 0.03); color: rgba(255, 255, 255, 0.6); }
            """)

    def _switch_tab(self, index):
        self.stack.setCurrentIndex(index)
        titles = ["General Settings", "Model Configuration", "Customization"]
        self.lbl_title.setText(titles[index] if index < len(titles) else "Settings")
        btns = [self.btn_gen, self.btn_mod, self.btn_cust]
        for i, b in enumerate(btns):
            self._update_sb_btn_style(b, i == index)
            b.setChecked(i == index)

    def _init_general_tab(self):
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setStyleSheet("background: transparent; border: none;")
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 15, 0)
        layout.setSpacing(35)

        self.btn_branding_mode = QPushButton("MASH")
        self.btn_branding_mode.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_branding_mode.setFixedWidth(200)
        
        self.branding_menu = QMenu(self)
        self.branding_menu.setWindowFlags(self.branding_menu.windowFlags() | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
        self.branding_menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        # Style the menu to match AnimatedMenu
        from PyQt6.QtGui import QPalette, QColor
        pal = self.branding_menu.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor("#18181b"))
        pal.setColor(QPalette.ColorRole.Base, QColor("#18181b"))
        self.branding_menu.setPalette(pal)
        
        self.branding_menu.setStyleSheet("""
            QMenu {
                background-color: #18181b;
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 12px;
                padding: 6px;
                color: white;
            }
            QMenu::item {
                padding: 10px 24px;
                border-radius: 8px;
                margin: 2px 4px;
                color: rgba(255, 255, 255, 0.7);
            }
            QMenu::item:selected {
                background-color: rgba(99, 102, 241, 0.4);
                color: white;
            }
        """)

        for mode in ["MASH", "Date", "Time", "Memory Usage", "CPU Usage", "Custom"]:
            action = self.branding_menu.addAction(mode)
            action.triggered.connect(lambda checked, m=mode: self._set_branding_mode(m))

        self.btn_branding_mode.setMenu(self.branding_menu)

        # Animation Mode Selection
        self.btn_branding_anim = QPushButton("None")
        self.btn_branding_anim.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_branding_anim.setFixedWidth(200)
        
        self.anim_menu = QMenu(self)
        self.anim_menu.setWindowFlags(self.anim_menu.windowFlags() | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
        self.anim_menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.anim_menu.setPalette(pal)
        self.anim_menu.setStyleSheet(self.branding_menu.styleSheet()) # Share the premium style

        for anim in ["None", "Geometry Dash", "Chrome Dino", "Car Drive"]:
            a_action = self.anim_menu.addAction(anim)
            a_action.triggered.connect(lambda checked, a=anim: self._set_animation_mode(a))
        
        self.btn_branding_anim.setMenu(self.anim_menu)

        self.edit_branding = QLineEdit("MASH")
        self.edit_branding.setPlaceholderText("Enter custom text...")
        
        # Wrap custom text in a container to toggle label + input together
        self.custom_branding_container = QWidget()
        custom_vbox = QVBoxLayout(self.custom_branding_container)
        custom_vbox.setContentsMargins(0, 0, 0, 0)
        custom_vbox.setSpacing(6)
        
        custom_lbl = QLabel("Custom Text")
        custom_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 11px; font-weight: 500;")
        custom_vbox.addWidget(custom_lbl)
        custom_vbox.addWidget(self.edit_branding)
        self.custom_branding_container.setVisible(False)

        group_branding = self._create_section("NOTCH BRANDING", [
            ("Display Mode", self.btn_branding_mode),
            ("Notch Animation", self.btn_branding_anim),
            ("", self.custom_branding_container),
        ])
        layout.addWidget(group_branding)

        # Media
        self.check_spotify = QCheckBox("Show Spotify Playback")
        self.check_spotify.setChecked(False)
        group_media = self._create_section("MEDIA", [
            ("", self.check_spotify),
        ])
        layout.addWidget(group_media)

        self.check_collapse = QCheckBox("Automatically shrink notch when inactive")
        self.check_collapse.setChecked(True)
        self.check_lock_notch = QCheckBox("Lock Notch Position")
        self.check_lock_notch.setChecked(True)
        group_behavior = self._create_section("BEHAVIOR", [
            ("", self.check_collapse),
            ("", self.check_lock_notch),
        ])
        layout.addWidget(group_behavior)

        layout.addStretch()
        area.setWidget(content)
        return area

    def _init_models_tab(self):
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setStyleSheet("background: transparent; border: none;")
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 15, 0)
        layout.setSpacing(35)

        # API Key
        self.edit_api_key = QLineEdit()
        self.edit_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.edit_api_key.setPlaceholderText("sk-or-v1-...")
        group_api = self._create_section("AUTHENTICATION", [
            ("OpenRouter API Key", self.edit_api_key),
        ])
        layout.addWidget(group_api)

        # Models List
        models = [
            ("Ring 2.6 1T (128k, High-performance Reasoning)", "inclusionai/ring-2.6-1t:free"),
            ("Baidu CoBuddy (32k, General Assistant)", "baidu/cobuddy:free"),
            ("Nemotron-3 Nano Omni (32k, Reasoning)", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"),
            ("Laguna M.1 (32k, Coding)", "poolside/laguna-m.1:free"),
            ("Laguna XS.2 (16k, Fast Coding)", "poolside/laguna-xs.2:free"),
            ("DeepSeek v4 Flash (128k, Fast / General)", "deepseek/deepseek-v4-flash:free"),
            ("Gemma-4 26B (32k, Instruct)", "google/gemma-4-26b-a4b-it:free"),
            ("Gemma-4 31B (32k, Instruct)", "google/gemma-4-31b-it:free"),
            ("Trinity Large Thinking (128k, Deep Reasoning)", "arcee-ai/trinity-large-thinking:free"),
            ("Nemotron-3 Super 120B (32k, Advanced Reasoning)", "nvidia/nemotron-3-super-120b-a12b:free"),
            ("Llama Nemotron Embed VL (128k, Vision/Embeddings)", "nvidia/llama-nemotron-embed-vl-1b-v2:free"),
            ("MiniMax M2.5 (32k, General)", "minimax/minimax-m2.5:free"),
            ("LFM 2.5 1.2B Thinking (128k, Fast Reasoning)", "liquid/lfm-2.5-1.2b-thinking:free"),
            ("LFM 2.5 1.2B Instruct (128k, Fast Instruct)", "liquid/lfm-2.5-1.2b-instruct:free"),
        ]

        self.models_dict = {id_: text for text, id_ in models}

        self.btn_model_gen = QPushButton("MiniMax M2.5 (32k, General)")
        self.btn_model_res = QPushButton("MiniMax M2.5 (32k, General)")
        self.btn_model_cod = QPushButton("MiniMax M2.5 (32k, General)")
        
        self.btn_model_gen.model_id = "minimax/minimax-m2.5:free"
        self.btn_model_res.model_id = "minimax/minimax-m2.5:free"
        self.btn_model_cod.model_id = "minimax/minimax-m2.5:free"

        for btn in [self.btn_model_gen, self.btn_model_res, self.btn_model_cod]:
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            
            menu = QMenu(self)
            menu.setWindowFlags(menu.windowFlags() | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
            menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            
            from PyQt6.QtGui import QPalette, QColor
            pal = menu.palette()
            pal.setColor(QPalette.ColorRole.Window, QColor("#18181b"))
            pal.setColor(QPalette.ColorRole.Base, QColor("#18181b"))
            menu.setPalette(pal)
            
            menu.setStyleSheet("""
                QMenu {
                    background-color: #18181b;
                    border: 1px solid rgba(255, 255, 255, 0.1);
                    border-radius: 12px;
                    padding: 6px;
                    color: white;
                }
                QMenu::item {
                    padding: 10px 24px;
                    border-radius: 8px;
                    margin: 2px 4px;
                    color: rgba(255, 255, 255, 0.7);
                }
                QMenu::item:selected {
                    background-color: rgba(99, 102, 241, 0.4);
                    color: white;
                }
            """)
            
            for text, id_ in models:
                action = menu.addAction(text)
                def make_handler(b, t, i):
                    return lambda checked, bb=b, tt=t, ii=i: (bb.setText(tt), setattr(bb, 'model_id', ii))
                action.triggered.connect(make_handler(btn, text, id_))
            
            btn.setMenu(menu)

        group_models = self._create_section("MODELS", [
            ("General Model", self.btn_model_gen),
            ("Reasoning Model", self.btn_model_res),
            ("Coding Model", self.btn_model_cod),
        ])
        layout.addWidget(group_models)

        self.edit_soul = QTextEdit("You are Mash, a premium minimalist AI...")
        group_soul = self._create_section("AGENT PERSONALITY", [
            ("System Prompt", self.edit_soul),
        ])
        layout.addWidget(group_soul)

        layout.addStretch()
        area.setWidget(content)
        return area

    def _create_section(self, title, fields):
        group = QFrame()
        layout = QVBoxLayout(group)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        if title:
            lbl = QLabel(title)
            lbl.setStyleSheet("color: rgba(255, 255, 255, 0.2); font-weight: 700; font-size: 9px; letter-spacing: 1px;")
            layout.addWidget(lbl)
        for item in fields:
            name, widget = item[0], item[1]
            if isinstance(widget, QComboBox):
                if len(item) > 2:
                    widget.addItems(item[2])
                # Modernize the popup view
                from PyQt6.QtWidgets import QListView
                from PyQt6.QtGui import QPalette
                view = QListView()
                
                # Force the window background to match our dark theme
                pal = view.palette()
                pal.setColor(QPalette.ColorRole.Window, QColor("#18181b"))
                pal.setColor(QPalette.ColorRole.Base, QColor("#18181b"))
                view.setPalette(pal)
                view.setAutoFillBackground(True)

                view.setStyleSheet("""
                    QListView {
                        background-color: #18181b;
                        border: 1px solid rgba(255, 255, 255, 0.1);
                        border-radius: 10px;
                        color: white;
                        outline: none;
                    }
                    QListView::item {
                        padding: 10px 16px;
                        color: white;
                    }
                    QListView::item:selected {
                        background-color: rgba(99, 102, 241, 0.4);
                        color: white;
                    }
                """)
                widget.setView(view)
            
            f_layout = QVBoxLayout()
            f_layout.setSpacing(6)
            if name:
                f_lbl = QLabel(name)
                f_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 11px; font-weight: 500;")
                f_layout.addWidget(f_lbl)
            widget.setStyleSheet("""
                QLineEdit, QComboBox, QTextEdit, QCheckBox, QPushButton {
                    background: rgba(255, 255, 255, 0.03);
                    border: 1px solid rgba(255, 255, 255, 0.06);
                    border-radius: 8px; padding: 12px;
                    color: white; font-size: 12px;
                    text-align: left;
                }
                QPushButton::menu-indicator { image: none; }
                QComboBox {
                    padding-right: 24px;
                }
                QComboBox::drop-down {
                    border: none;
                    width: 24px;
                }
                QComboBox::down-arrow {
                    image: none;
                    border-left: 4px solid transparent;
                    border-right: 4px solid transparent;
                    border-top: 4px solid rgba(255, 255, 255, 0.5);
                    margin-right: 12px;
                    margin-top: 2px;
                }
                QCheckBox { border: none; background: transparent; padding: 0; }
                QLineEdit:focus, QTextEdit:focus, QComboBox:focus { 
                    border: 1px solid rgba(99, 102, 241, 0.3); 
                    background: rgba(255, 255, 255, 0.05); 
                }
            """)
            if isinstance(widget, QTextEdit): widget.setFixedHeight(120)
            f_layout.addWidget(widget)
            layout.addLayout(f_layout)
        return group

    def _set_branding_mode(self, mode):
        self.btn_branding_mode.setText(mode)
        self.custom_branding_container.setVisible(mode == "Custom")

    def _set_animation_mode(self, anim):
        self.btn_branding_anim.setText(anim)

    # ── Customization tab init ───────────────────────────────────────────

    def _init_customization_tab(self):
        from PyQt6.QtWidgets import QScrollArea
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setStyleSheet("background: transparent; border: none;")
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 15, 0)
        layout.setSpacing(35)

        # Theme buttons
        self._selected_theme = "dark"
        theme_group = self._create_section("THEME", [])
        theme_inner = QVBoxLayout()
        theme_inner.setSpacing(10)

        self._theme_btns = {}
        theme_options = [
            ("dark",  "Dark",  "Deep black glassmorphic — the default."),
            ("light", "Light", "Clean white surface with indigo accents."),
            ("neon",  "Neon",  "Dark purple base with neon green #00ff88 glows."),
        ]
        for tid, label, desc in theme_options:
            row = QHBoxLayout()
            btn = QPushButton(label)
            btn.setFixedHeight(38)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setProperty("tid", tid)
            btn.clicked.connect(lambda _, t=tid: self._select_theme(t))
            self._theme_btns[tid] = btn
            desc_lbl = QLabel(desc)
            desc_lbl.setStyleSheet("color: rgba(255,255,255,0.35); font-size: 10px;")
            row.addWidget(btn)
            row.addWidget(desc_lbl)
            row.addStretch()
            theme_inner.addLayout(row)

        theme_group.layout().addLayout(theme_inner)
        layout.addWidget(theme_group)
        self._refresh_theme_btns()

        # Idle animation buttons
        self._selected_char_anim = "orb"
        anim_group = self._create_section("MASH IDLE ANIMATION", [])
        anim_inner = QVBoxLayout()
        anim_inner.setSpacing(10)

        self._anim_btns = {}
        anim_options = [
            ("orb",   "Orb",   "Classic robot face: blinks, rolls eyes, yawns."),
            ("pulse", "Pulse", "Eyes breathe in sync with a soft radial ripple."),
            ("orbit", "Orbit", "A glowing satellite orbits the face slowly."),
        ]
        for aid, label, desc in anim_options:
            row = QHBoxLayout()
            btn = QPushButton(label)
            btn.setFixedHeight(38)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setProperty("aid", aid)
            btn.clicked.connect(lambda _, a=aid: self._select_char_anim(a))
            self._anim_btns[aid] = btn
            desc_lbl = QLabel(desc)
            desc_lbl.setStyleSheet("color: rgba(255,255,255,0.35); font-size: 10px;")
            row.addWidget(btn)
            row.addWidget(desc_lbl)
            row.addStretch()
            anim_inner.addLayout(row)

        anim_group.layout().addLayout(anim_inner)
        layout.addWidget(anim_group)
        self._refresh_anim_btns()

        layout.addStretch()
        area.setWidget(content)
        return area

    def _select_theme(self, tid):
        self._selected_theme = tid
        self._refresh_theme_btns()
        self.theme_changed.emit(tid)

    def _select_char_anim(self, aid):
        self._selected_char_anim = aid
        self._refresh_anim_btns()
        self.char_anim_changed.emit(aid)

    _BTN_ACTIVE = """
        QPushButton {
            background: rgba(99, 102, 241, 0.35); color: #ffffff;
            border: 1px solid rgba(99, 102, 241, 0.7);
            border-radius: 8px; padding: 0 18px; font-size: 11px; font-weight: 700;
        }
    """
    _BTN_INACTIVE = """
        QPushButton {
            background: rgba(255, 255, 255, 0.04); color: rgba(255,255,255,0.5);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 8px; padding: 0 18px; font-size: 11px;
        }
        QPushButton:hover { background: rgba(255,255,255,0.08); color: rgba(255,255,255,0.8); }
    """

    def _refresh_theme_btns(self):
        for tid, btn in self._theme_btns.items():
            btn.setStyleSheet(self._BTN_ACTIVE if tid == self._selected_theme else self._BTN_INACTIVE)

    def _refresh_anim_btns(self):
        for aid, btn in self._anim_btns.items():
            btn.setStyleSheet(self._BTN_ACTIVE if aid == self._selected_char_anim else self._BTN_INACTIVE)

    def _save_and_close(self):
        mode = self.btn_branding_mode.text()
        text = self.edit_branding.text()
        anim = self.btn_branding_anim.text()
        spotify = self.check_spotify.isChecked()
        locked = self.check_lock_notch.isChecked()
        self.branding_changed.emit(mode, text)
        self.animation_changed.emit(anim)
        self.spotify_toggled.emit(spotify)
        self.lock_notch_toggled.emit(locked)
        self.theme_changed.emit(getattr(self, "_selected_theme", "dark"))
        self.char_anim_changed.emit(getattr(self, "_selected_char_anim", "orb"))

        config_data = {
            "api_key": self.edit_api_key.text(),
            "model_general": self.btn_model_gen.model_id,
            "model_reasoning": self.btn_model_res.model_id,
            "model_coding": self.btn_model_cod.model_id,
            "system_prompt": self.edit_soul.toPlainText()
        }
        self.config_updated.emit(config_data)

    def _setup_animation(self):
        self._fx = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._fx)
        self._anim = QPropertyAnimation(self._fx, b"opacity")
        self._anim.setDuration(280)
        self._anim.setEasingCurve(QEasingCurve.Type.OutQuad)

    def show_animated(self, pos):
        self._is_hiding = False
        try: self._anim.finished.disconnect()
        except: pass
        
        self.move(pos.x() - self.width() // 2, pos.y() + 40)
        self.show()
        self._anim.stop()
        self._anim.setStartValue(self._fx.opacity())
        self._anim.setEndValue(1.0)
        self._anim.start()

    def hide_animated(self):
        if self._is_hiding: return
        self._is_hiding = True
        
        self._anim.stop()
        self._anim.setStartValue(self._fx.opacity())
        self._anim.setEndValue(0.0)
        try: self._anim.finished.disconnect()
        except: pass
        self._anim.finished.connect(self._on_hide_finished)
        self._anim.start()

    def _on_hide_finished(self):
        if self._is_hiding:
            self.hide()
            self._is_hiding = False

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(10, 10, -10, -10)
        path = QPainterPath()
        path.addRoundedRect(rect, 20, 20)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 100))
        p.drawPath(path.translated(0, 8))
        bg = QLinearGradient(0, 0, 0, self.height())
        bg.setColorAt(0, QColor(24, 24, 27, 248))
        bg.setColorAt(1, QColor(9, 9, 11, 255))
        p.fillPath(path, bg)
        inner_glow = QPainterPath()
        inner_glow.addRoundedRect(rect.adjusted(0.8, 0.8, -0.8, -0.8), 20, 20)
        p.setPen(QPen(QColor(255, 255, 255, 30), 1))
        p.drawPath(inner_glow)
        p.setPen(QPen(QColor(255, 255, 255, 12), 1.0))
        p.drawPath(path)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton and self._drag_pos:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
