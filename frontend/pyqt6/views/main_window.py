"""
JARVIS Main Window
Central window that hosts all major UI components.

Features:
- Chat, Recommendations, Dashboard, Settings tabs
- Theme toggle in header
- Status bar with connection info
"""

import os
import sys
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from PyQt6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QFrame,
    QStatusBar,
    QSizePolicy
)
from PyQt6.QtCore import Qt, pyqtSignal, QSize
from PyQt6.QtGui import QFont, QCloseEvent, QIcon, QPixmap

from .chat_widget import ChatWidget
from .dashboard_widget import DashboardWidget
from .recommendations_widget import RecommendationsWidget
from .settings_widget import SettingsWidget

if TYPE_CHECKING:
    from utils.theme_manager import ThemeManager


class MainWindow(QMainWindow):
    """
    Main application window for JARVIS.
    
    Features:
    - Header with title, subtitle, and theme toggle
    - Tabbed interface (Chat / Recommendations / Dashboard / Settings)
    - Hide instead of close behavior
    - Status bar for connection status
    """
    
    # Signals
    theme_toggle_requested = pyqtSignal()
    window_hidden = pyqtSignal()
    window_shown = pyqtSignal()
    logout_requested = pyqtSignal()
    
    def __init__(
        self, 
        theme_manager: Optional["ThemeManager"] = None,
        parent: Optional[QWidget] = None
    ):
        super().__init__(parent)
        
        self._theme_manager = theme_manager
        
        # Connect to theme manager signals if available
        if self._theme_manager:
            self._theme_manager.theme_changed.connect(self._on_theme_changed)
        
        self._setup_window()
        self._setup_ui()
        self._setup_status_bar()
    
    def _setup_window(self):
        """Configure window properties."""
        self.setWindowTitle("JARVIS")
        self.setMinimumSize(900, 700)
        
        # Default size
        self.resize(1000, 750)
        
        # Center on screen
        self._center_on_screen()
    
    def _center_on_screen(self):
        """Center the window on the primary screen."""
        from PyQt6.QtWidgets import QApplication
        screen = QApplication.primaryScreen()
        if screen:
            geometry = screen.availableGeometry()
            x = (geometry.width() - self.width()) // 2 + geometry.x()
            y = (geometry.height() - self.height()) // 2 + geometry.y()
            self.move(x, y)
    
    def _setup_ui(self):
        """Set up the main UI layout."""
        # Central widget
        central = QWidget()
        self.setCentralWidget(central)
        
        # Main layout
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Header
        header = self._create_header()
        layout.addWidget(header)
        
        # Tab widget
        self._tab_widget = self._create_tabs()
        layout.addWidget(self._tab_widget, 1)
    
    def _get_resource_path(self, relative_path: str) -> str:
        """Get resource path for both dev and PyInstaller builds."""
        if getattr(sys, 'frozen', False):
            base_path = Path(sys._MEIPASS)
        else:
            base_path = Path(__file__).parent.parent
        return str(base_path / relative_path)
    
    def _create_header(self) -> QWidget:
        """Create the application header."""
        header = QFrame()
        header.setObjectName("mainHeader")
        # 무채색의 모던하고 세련된 색상 (#2d2d2d - 차콜 그레이)
        header.setStyleSheet("""
            #mainHeader {
                background-color: #2d2d2d;
                border-bottom: 1px solid #3d3d3d;
            }
            #mainHeader QLabel {
                color: white;
            }
        """)
        
        layout = QHBoxLayout(header)
        layout.setContentsMargins(20, 12, 20, 12)
        layout.setSpacing(16)
        
        # Logo/Icon area - jarvis_logo.png 사용
        logo_label = QLabel()
        logo_path = self._get_resource_path('resources/icons/jarvis_logo.png')
        if os.path.exists(logo_path):
            pixmap = QPixmap(logo_path)
            scaled_pixmap = pixmap.scaled(40, 40, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            logo_label.setPixmap(scaled_pixmap)
        else:
            # Fallback: 텍스트 로고
            logo_label.setText("J")
            logo_font = QFont()
            logo_font.setPointSize(24)
            logo_font.setBold(True)
            logo_label.setFont(logo_font)
        layout.addWidget(logo_label)
        
        # Title section
        title_section = QVBoxLayout()
        title_section.setSpacing(2)
        
        # Main title
        self._title_label = QLabel("JARVIS")
        title_font = QFont()
        title_font.setPointSize(18)
        title_font.setBold(True)
        self._title_label.setFont(title_font)
        title_section.addWidget(self._title_label)
        
        # Subtitle
        self._subtitle_label = QLabel("Your AI Assistant")
        subtitle_font = QFont()
        subtitle_font.setPointSize(11)
        self._subtitle_label.setFont(subtitle_font)
        self._subtitle_label.setStyleSheet("color: rgba(255, 255, 255, 0.7);")
        title_section.addWidget(self._subtitle_label)
        
        layout.addLayout(title_section)
        layout.addStretch()
        
        # 테마 토글 버튼 제거됨 (라이트 모드 고정)
        
        return header
    
    def _create_tabs(self) -> QTabWidget:
        """Create the main tab widget."""
        tabs = QTabWidget()
        tabs.setDocumentMode(True)
        tabs.setStyleSheet("""
            QTabWidget::pane {
                border: none;
            }
            QTabBar::tab {
                padding: 12px 24px;
                margin-right: 4px;
                border: none;
                border-bottom: 3px solid transparent;
                font-weight: 600;
                color: #5a5a5a;
            }
            QTabBar::tab:selected {
                border-bottom-color: #1a1a1a;
                color: #1a1a1a;
            }
            QTabBar::tab:hover:!selected {
                background-color: rgba(0, 0, 0, 0.05);
            }
        """)
        
        # Chat tab
        self._chat_widget = ChatWidget()
        tabs.addTab(self._chat_widget, "💬 채팅")
        
        # Recommendations tab
        self._recommendations_widget = RecommendationsWidget()
        tabs.addTab(self._recommendations_widget, "📌 추천")
        
        # Dashboard tab
        self._dashboard_widget = DashboardWidget()
        tabs.addTab(self._dashboard_widget, "📊 대시보드")
        
        # Settings tab
        self._settings_widget = SettingsWidget(self._theme_manager)
        self._settings_widget.logout_requested.connect(self.logout_requested.emit)
        tabs.addTab(self._settings_widget, "⚙️ 설정")
        
        return tabs
    
    def _setup_status_bar(self):
        """Set up the status bar."""
        self._status_bar = QStatusBar()
        self._status_bar.setStyleSheet("""
            QStatusBar {
                background-color: #f5f5f5;
                border-top: 1px solid #e0e0e0;
            }
            QStatusBar QLabel {
                color: #1a1a1a;
                padding: 4px 8px;
            }
        """)
        self.setStatusBar(self._status_bar)
        
        # Connection status
        self._connection_label = QLabel("🟢 연결됨")
        self._status_bar.addWidget(self._connection_label)
        
        # Spacer
        self._status_bar.addWidget(QLabel(""), 1)
        
        # User info
        self._user_label = QLabel("로그인 필요")
        self._status_bar.addPermanentWidget(self._user_label)
    
    # =========================================================================
    # Theme Management (라이트 모드 고정)
    # =========================================================================
    
    def _on_theme_changed(self, theme: str):
        """Handle theme change signal (no-op, fixed to light mode)."""
        pass
    
    def _on_theme_change_from_settings(self, theme: str):
        """Handle theme change from settings (no-op, fixed to light mode)."""
        pass
    
    def set_theme_manager(self, theme_manager: "ThemeManager"):
        """Set the theme manager reference."""
        self._theme_manager = theme_manager
        if self._theme_manager:
            self._theme_manager.theme_changed.connect(self._on_theme_changed)
        
        self._settings_widget.set_theme_manager(theme_manager)
    
    # =========================================================================
    # Window Events
    # =========================================================================
    
    def closeEvent(self, event: QCloseEvent):
        """Handle window close - hide instead of closing."""
        event.ignore()
        self.hide()
        self.window_hidden.emit()
    
    def showEvent(self, event):
        """Handle window show event."""
        super().showEvent(event)
        self.window_shown.emit()
    
    # =========================================================================
    # Public Methods
    # =========================================================================
    
    def set_title(self, title: str):
        """Set the main title."""
        self._title_label.setText(title)
        self.setWindowTitle(title)
    
    def set_subtitle(self, subtitle: str):
        """Set the subtitle."""
        self._subtitle_label.setText(subtitle)
    
    def set_connection_status(self, status: str, connected: bool = True):
        """Update the connection status in the status bar."""
        icon = "🟢" if connected else "🔴"
        self._connection_label.setText(f"{icon} {status}")
        
        # Also update chat widget status
        self._chat_widget.set_status(status, connected)
    
    def set_user_info(self, info: str):
        """Update the user info in the status bar."""
        self._user_label.setText(info)
    
    def set_credentials(self, token: str, user_id: int, email: str = ""):
        """Set credentials for all widgets that need them."""
        self._dashboard_widget.set_credentials(token, user_id)
        self._recommendations_widget.set_credentials(token, user_id)
        
        if email:
            self._settings_widget.set_user_info(email, user_id)
    
    def load_all_data(self):
        """Load data for all widgets."""
        self._dashboard_widget.load_data()
        self._recommendations_widget.load_data()
    
    def switch_to_chat(self):
        """Switch to the chat tab."""
        self._tab_widget.setCurrentWidget(self._chat_widget)
    
    def switch_to_recommendations(self):
        """Switch to the recommendations tab."""
        self._tab_widget.setCurrentWidget(self._recommendations_widget)
    
    def switch_to_dashboard(self):
        """Switch to the dashboard tab."""
        self._tab_widget.setCurrentWidget(self._dashboard_widget)
    
    def switch_to_settings(self):
        """Switch to the settings tab."""
        self._tab_widget.setCurrentWidget(self._settings_widget)
    
    @property
    def chat_widget(self) -> ChatWidget:
        """Get the chat widget instance."""
        return self._chat_widget
    
    @property
    def dashboard_widget(self) -> DashboardWidget:
        """Get the dashboard widget instance."""
        return self._dashboard_widget
    
    @property
    def recommendations_widget(self) -> RecommendationsWidget:
        """Get the recommendations widget instance."""
        return self._recommendations_widget
    
    @property
    def settings_widget(self) -> SettingsWidget:
        """Get the settings widget instance."""
        return self._settings_widget
