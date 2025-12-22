"""
JARVIS Settings Widget
User preferences and application settings.
"""

from typing import Optional, List
from pathlib import Path
from threading import Thread

import requests

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QFrame,
    QLineEdit,
    QCheckBox,
    QComboBox,
    QMessageBox,
    QGroupBox,
    QFileDialog
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont

try:
    from config import API_BASE_URL
except ImportError:
    API_BASE_URL = "http://localhost:8000"


class SettingsWidget(QWidget):
    """
    Settings interface widget.
    
    Sections:
    - Profile settings
    - Folder selection
    - Theme preferences
    - Notification settings
    - Account actions
    """
    
    # Signals
    folders_changed = pyqtSignal(list)  # List of selected folders
    theme_change_requested = pyqtSignal(str)  # 'dark' or 'light'
    logout_requested = pyqtSignal()
    settings_saved = pyqtSignal()
    rescan_requested = pyqtSignal()  # 재스캔 요청 시그널
    
    def __init__(self, theme_manager=None, parent=None):
        super().__init__(parent)
        
        self._theme_manager = theme_manager
        self._selected_folders: List[str] = []
        self._jwt_token: Optional[str] = None
        self._user_id: Optional[int] = None
        
        self._setup_ui()
    
    def _setup_ui(self):
        """Set up the widget UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Header
        header = self._create_header()
        layout.addWidget(header)
        
        # Scrollable content
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("background-color: #F3F4F6;")
        
        content = QWidget()
        content.setStyleSheet("background-color: #F3F4F6;")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(20, 20, 20, 20)
        content_layout.setSpacing(16)
        
        # Folder settings
        folder_section = self._create_folder_section()
        content_layout.addWidget(folder_section)
        
        # 테마 설정 제거됨 (라이트 모드 고정)
        
        # Notification settings
        notification_section = self._create_notification_section()
        content_layout.addWidget(notification_section)
        
        # Account section
        account_section = self._create_account_section()
        content_layout.addWidget(account_section)
        
        content_layout.addStretch()
        
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
    
    def _create_header(self) -> QWidget:
        """Create header widget."""
        header = QFrame()
        # 무채색 모던한 헤더 색상
        header.setStyleSheet("""
            QFrame {
                background-color: #2d2d2d;
            }
            QLabel {
                color: white;
            }
        """)
        
        layout = QVBoxLayout(header)
        layout.setContentsMargins(20, 15, 20, 15)
        
        title = QLabel("⚙️ 설정")
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)
        
        subtitle = QLabel("애플리케이션 설정을 관리합니다")
        subtitle.setStyleSheet("color: rgba(255, 255, 255, 0.8);")
        layout.addWidget(subtitle)
        
        return header
    
    def _create_section_card(self, title: str, icon: str = "") -> QFrame:
        """Create a settings section card."""
        card = QFrame()
        card.setStyleSheet("""
            QFrame {
                background-color: white;
                border: 1px solid #E5E7EB;
                border-radius: 12px;
            }
        """)
        
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)
        
        # Title
        title_label = QLabel(f"{icon} {title}" if icon else title)
        title_font = QFont()
        title_font.setPointSize(13)
        title_font.setBold(True)
        title_label.setFont(title_font)
        title_label.setStyleSheet("color: #1F2937;")
        layout.addWidget(title_label)
        
        return card
    
    def _create_folder_section(self) -> QWidget:
        """Create folder settings section."""
        card = self._create_section_card("데이터 수집 폴더", "📁")
        layout = card.layout()
        
        # Description
        desc = QLabel("JARVIS가 분석할 폴더를 선택하세요.\n선택된 폴더의 파일들이 인덱싱됩니다.")
        desc.setStyleSheet("color: #6B7280;")
        desc.setWordWrap(True)
        layout.addWidget(desc)
        
        # Selected folders display
        self._folders_display = QLabel("선택된 폴더가 없습니다")
        self._folders_display.setStyleSheet("""
            background-color: #F3F4F6;
            color: #374151;
            padding: 12px;
            border-radius: 6px;
        """)
        self._folders_display.setWordWrap(True)
        layout.addWidget(self._folders_display)
        
        # Buttons
        btn_layout = QHBoxLayout()
        
        change_btn = QPushButton("📂 폴더 변경")
        change_btn.setStyleSheet("""
            QPushButton {
                background-color: #1a1a1a;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 10px 20px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #2a2a2a;
            }
        """)
        change_btn.clicked.connect(self._on_change_folders)
        btn_layout.addWidget(change_btn)
        
        btn_layout.addStretch()
        
        rescan_btn = QPushButton("🔄 재스캔")
        rescan_btn.setStyleSheet("""
            QPushButton {
                background-color: #E5E7EB;
                color: #374151;
                border: none;
                border-radius: 6px;
                padding: 10px 20px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #D1D5DB;
            }
        """)
        rescan_btn.clicked.connect(self._on_rescan)
        btn_layout.addWidget(rescan_btn)
        
        layout.addLayout(btn_layout)
        
        return card
    
    # 테마 설정 섹션 제거됨 (라이트 모드 고정)
    
    def _create_notification_section(self) -> QWidget:
        """Create notification settings section."""
        card = self._create_section_card("알림 설정", "🔔")
        layout = card.layout()
        
        # Checkboxes for notification types
        self._notify_recommendations = QCheckBox("새로운 추천 알림")
        self._notify_recommendations.setChecked(True)
        self._notify_recommendations.setStyleSheet("color: #374151;")
        layout.addWidget(self._notify_recommendations)
        
        self._notify_reports = QCheckBox("리포트 완료 알림")
        self._notify_reports.setChecked(True)
        self._notify_reports.setStyleSheet("color: #374151;")
        layout.addWidget(self._notify_reports)
        
        self._notify_analysis = QCheckBox("분석 완료 알림")
        self._notify_analysis.setChecked(True)
        self._notify_analysis.setStyleSheet("color: #374151;")
        layout.addWidget(self._notify_analysis)
        
        return card
    
    def _create_account_section(self) -> QWidget:
        """Create account section."""
        card = self._create_section_card("계정", "👤")
        layout = card.layout()
        
        # User info display
        self._user_info_label = QLabel("로그인 정보가 없습니다")
        self._user_info_label.setStyleSheet("color: #6B7280;")
        layout.addWidget(self._user_info_label)
        
        # Buttons
        btn_layout = QHBoxLayout()
        
        logout_btn = QPushButton("🚪 로그아웃")
        logout_btn.setStyleSheet("""
            QPushButton {
                background-color: #FEE2E2;
                color: #DC2626;
                border: none;
                border-radius: 6px;
                padding: 10px 20px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #FECACA;
            }
        """)
        logout_btn.clicked.connect(self._on_logout)
        btn_layout.addWidget(logout_btn)
        
        btn_layout.addStretch()
        
        layout.addLayout(btn_layout)
        
        return card
    
    # =========================================================================
    # Event Handlers
    # =========================================================================
    
    def _on_change_folders(self):
        """Handle folder change request."""
        # Import and show folder dialog
        from .dialogs.folder_dialog import FolderDialog
        
        dialog = FolderDialog(self._selected_folders, self)
        result = dialog.exec()
        
        if result == FolderDialog.DialogCode.Accepted:
            new_folders = dialog.get_selected_paths()
            if new_folders:
                self._selected_folders = new_folders
                self._update_folders_display()
                self.folders_changed.emit(new_folders)
                
                # 백엔드에 폴더 설정 저장
                self._save_folder_to_backend(new_folders)
    
    def _save_folder_to_backend(self, folders: List[str]):
        """Save folder selection to backend."""
        if not self._jwt_token or not folders:
            return
        
        folder_path = folders[0] if folders else ""
        
        def send_request():
            try:
                response = requests.post(
                    f"{API_BASE_URL}/api/v2/settings/initial-setup",
                    headers={"Authorization": f"Bearer {self._jwt_token}"},
                    json={"folder_path": folder_path},
                    timeout=10
                )
                if response.status_code == 200:
                    print("✅ 폴더 설정 저장 완료")
                else:
                    print(f"⚠️ 폴더 설정 저장 실패: {response.status_code}")
            except Exception as e:
                print(f"⚠️ 폴더 설정 저장 오류: {e}")
        
        Thread(target=send_request, daemon=True).start()
    
    def _on_rescan(self):
        """Handle rescan request."""
        result = QMessageBox.question(
            self,
            "재스캔",
            "선택된 폴더를 다시 스캔하시겠습니까?\n새로운 파일이 인덱싱됩니다.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if result == QMessageBox.StandardButton.Yes:
            # 재스캔 API 호출
            self._trigger_rescan()
    
    def _trigger_rescan(self):
        """Trigger rescan via API."""
        if not self._jwt_token:
            QMessageBox.warning(self, "오류", "로그인이 필요합니다.")
            return
        
        def send_request():
            try:
                response = requests.post(
                    f"{API_BASE_URL}/api/v2/user-files/rescan",
                    headers={"Authorization": f"Bearer {self._jwt_token}"},
                    timeout=30
                )
                if response.status_code == 200:
                    data = response.json()
                    if data.get("success"):
                        print("✅ 재스캔 완료")
                    else:
                        print(f"⚠️ 재스캔 실패: {data.get('message', 'Unknown error')}")
                else:
                    print(f"⚠️ 재스캔 HTTP 오류: {response.status_code}")
            except Exception as e:
                print(f"⚠️ 재스캔 오류: {e}")
        
        Thread(target=send_request, daemon=True).start()
        QMessageBox.information(self, "재스캔", "재스캔이 시작되었습니다.\n완료까지 시간이 걸릴 수 있습니다.")
        self.rescan_requested.emit()
    
    # 테마 변경 핸들러 제거됨 (라이트 모드 고정)
    
    def _on_logout(self):
        """Handle logout request."""
        result = QMessageBox.question(
            self,
            "로그아웃",
            "로그아웃 하시겠습니까?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if result == QMessageBox.StandardButton.Yes:
            self.logout_requested.emit()
    
    def _update_folders_display(self):
        """Update folders display label."""
        if self._selected_folders:
            # Show first 3 folders with count
            display_text = []
            for folder in self._selected_folders[:3]:
                # Shorten path
                path = Path(folder)
                try:
                    rel_path = path.relative_to(Path.home())
                    display_text.append(f"~/{rel_path}")
                except ValueError:
                    display_text.append(str(path))
            
            text = "\n".join(display_text)
            if len(self._selected_folders) > 3:
                text += f"\n... 외 {len(self._selected_folders) - 3}개"
            
            self._folders_display.setText(text)
        else:
            self._folders_display.setText("선택된 폴더가 없습니다")
    
    # =========================================================================
    # Public Methods
    # =========================================================================
    
    def set_user_info(self, email: str, user_id: int):
        """Set user info display."""
        self._user_info_label.setText(f"📧 {email}\n🆔 User ID: {user_id}")
        self._user_id = user_id
    
    def set_credentials(self, jwt_token: str, user_id: int):
        """Set authentication credentials."""
        self._jwt_token = jwt_token
        self._user_id = user_id
    
    def set_selected_folders(self, folders: List[str]):
        """Set selected folders."""
        self._selected_folders = folders
        self._update_folders_display()
    
    def set_theme_manager(self, theme_manager):
        """Set theme manager reference."""
        self._theme_manager = theme_manager

