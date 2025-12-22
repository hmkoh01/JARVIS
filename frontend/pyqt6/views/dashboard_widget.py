"""
JARVIS Dashboard Widget
Widget for displaying dashboard information, activity summary, and notes.

Phase 4: Full implementation with API data fetching
"""

from datetime import datetime
from typing import Optional, Dict, Any, List

import requests

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QFrame,
    QGridLayout,
    QPushButton,
    QLineEdit,
    QTextEdit,
    QSizePolicy,
    QMessageBox
)
from PyQt6.QtCore import Qt, pyqtSignal, QThread
from PyQt6.QtGui import QFont

try:
    from config import API_BASE_URL
except ImportError:
    API_BASE_URL = "http://localhost:8000"


class DashboardDataWorker(QThread):
    """Background worker for fetching dashboard data."""
    
    data_loaded = pyqtSignal(dict)
    notes_loaded = pyqtSignal(list)
    analysis_loaded = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)
    
    def __init__(self, jwt_token: str, parent=None):
        super().__init__(parent)
        self.jwt_token = jwt_token
        self._api_base = f"{API_BASE_URL}/api/v2"
    
    def run(self):
        """Fetch all dashboard data."""
        headers = {"Authorization": f"Bearer {self.jwt_token}"}
        
        try:
            # Dashboard summary
            response = requests.get(
                f"{self._api_base}/dashboard/summary",
                headers=headers,
                timeout=30
            )
            
            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    self.data_loaded.emit(data.get("data", {}))
                else:
                    # API returned success=False
                    self.error_occurred.emit(data.get("message", "대시보드 요약 조회 실패"))
                    return
            elif response.status_code == 401:
                self.error_occurred.emit("인증이 만료되었습니다. 다시 로그인해주세요.")
                return
            else:
                self.error_occurred.emit(f"서버 오류: HTTP {response.status_code}")
                return
            
            # Notes (실패해도 계속 진행)
            try:
                notes_response = requests.get(
                    f"{self._api_base}/dashboard/notes",
                    headers=headers,
                    timeout=30
                )
                
                if notes_response.status_code == 200:
                    notes_data = notes_response.json()
                    if notes_data.get("success"):
                        self.notes_loaded.emit(notes_data.get("data", {}).get("notes", []))
            except Exception as e:
                print(f"⚠️ 노트 로드 실패 (무시): {e}")
            
            # Latest analysis (실패해도 계속 진행)
            try:
                analysis_response = requests.get(
                    f"{self._api_base}/dashboard/analyses/latest",
                    headers=headers,
                    timeout=30
                )
                
                if analysis_response.status_code == 200:
                    analysis_data = analysis_response.json()
                    if analysis_data.get("success"):
                        analysis = analysis_data.get("data", {}).get("analysis")
                        if analysis:
                            self.analysis_loaded.emit(analysis)
            except Exception as e:
                print(f"⚠️ 분석 로드 실패 (무시): {e}")
                        
        except requests.exceptions.Timeout:
            self.error_occurred.emit("서버 응답 시간 초과")
        except requests.exceptions.ConnectionError:
            self.error_occurred.emit("서버에 연결할 수 없습니다")
        except Exception as e:
            self.error_occurred.emit(str(e))


class NoteSaveWorker(QThread):
    """Background worker for saving notes."""
    
    save_success = pyqtSignal(dict)
    save_failed = pyqtSignal(str)
    
    def __init__(
        self,
        jwt_token: str,
        title: str,
        content: str,
        note_id: Optional[int] = None,
        parent=None
    ):
        super().__init__(parent)
        self.jwt_token = jwt_token
        self.title = title
        self.content = content
        self.note_id = note_id
        self._api_base = f"{API_BASE_URL}/api/v2"
    
    def run(self):
        """Save note to backend."""
        headers = {"Authorization": f"Bearer {self.jwt_token}"}
        
        try:
            if self.note_id:
                # Update existing note
                response = requests.put(
                    f"{self._api_base}/dashboard/notes/{self.note_id}",
                    headers=headers,
                    json={"title": self.title, "content": self.content},
                    timeout=30
                )
            else:
                # Create new note
                response = requests.post(
                    f"{self._api_base}/dashboard/notes",
                    headers=headers,
                    json={"title": self.title, "content": self.content},
                    timeout=30
                )
            
            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    self.save_success.emit(data.get("data", {}).get("note", {}))
                else:
                    self.save_failed.emit(data.get("message", "Save failed"))
            else:
                self.save_failed.emit(f"HTTP {response.status_code}")
                
        except Exception as e:
            self.save_failed.emit(str(e))


class DashboardWidget(QWidget):
    """
    Dashboard interface widget with API data display.
    
    Displays:
    - User profile summary
    - Activity statistics
    - Top interests
    - Notes CRUD
    """
    
    # Signals
    refresh_requested = pyqtSignal()
    loading_started = pyqtSignal()
    loading_finished = pyqtSignal()
    
    def __init__(self, jwt_token: str = "", user_id: int = 0, parent=None):
        super().__init__(parent)
        
        self._jwt_token = jwt_token
        self._user_id = user_id
        self._dashboard_data: Dict[str, Any] = {}
        self._notes: List[Dict[str, Any]] = []
        self._current_note_id: Optional[int] = None
        
        self._data_worker: Optional[DashboardDataWorker] = None
        self._note_worker: Optional[NoteSaveWorker] = None
        
        self._setup_ui()
    
    def set_credentials(self, jwt_token: str, user_id: int):
        """Set authentication credentials."""
        self._jwt_token = jwt_token
        self._user_id = user_id
    
    def _setup_ui(self):
        """Set up the dashboard interface."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Dashboard header
        header = self._create_header()
        layout.addWidget(header)
        
        # Main content (scrollable)
        content = self._create_content()
        layout.addWidget(content, 1)
    
    def _create_header(self) -> QWidget:
        """Create the dashboard header."""
        header = QFrame()
        # 무채색 모던한 헤더 색상
        header.setStyleSheet("""
            QFrame {
                background-color: #2d2d2d;
            }
            QLabel {
                color: white;
            }
            QPushButton {
                background-color: rgba(255, 255, 255, 0.2);
                color: white;
                border: none;
                border-radius: 4px;
                padding: 6px 12px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.3);
            }
        """)
        
        layout = QVBoxLayout(header)
        layout.setContentsMargins(20, 15, 20, 15)
        
        # Title row
        title_row = QHBoxLayout()
        
        title = QLabel("📊 내 대시보드")
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title.setFont(title_font)
        title_row.addWidget(title)
        
        title_row.addStretch()
        
        refresh_btn = QPushButton("🔄 새로고침")
        refresh_btn.clicked.connect(self.load_data)
        title_row.addWidget(refresh_btn)
        
        layout.addLayout(title_row)
        
        # Subtitle
        subtitle = QLabel("관심사와 활동을 한눈에 확인하세요")
        subtitle.setStyleSheet("color: rgba(255, 255, 255, 0.8);")
        layout.addWidget(subtitle)
        
        return header
    
    def _create_content(self) -> QWidget:
        """Create the main dashboard content."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("background-color: #F8FAFC;")
        
        container = QWidget()
        container.setStyleSheet("background-color: #F8FAFC;")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)
        
        # Profile card
        self._profile_card = self._create_card("👤 프로필")
        self._profile_content = QLabel("로딩 중...")
        self._profile_content.setStyleSheet("color: #1F2937; font-size: 13px;")
        self._profile_card.layout().addWidget(self._profile_content)
        layout.addWidget(self._profile_card)
        
        # Activity card
        self._activity_card = self._create_card("📈 최근 활동 (7일)")
        self._activity_container = QWidget()
        self._activity_layout = QHBoxLayout(self._activity_container)
        self._activity_layout.setContentsMargins(0, 0, 0, 0)
        self._activity_card.layout().addWidget(self._activity_container)
        self._activity_loading = QLabel("로딩 중...")
        self._activity_loading.setStyleSheet("color: #374151; font-size: 13px;")
        self._activity_layout.addWidget(self._activity_loading)
        layout.addWidget(self._activity_card)
        
        # Interests card
        self._interests_card = self._create_card("💡 관심사 TOP 5")
        self._interests_container = QWidget()
        self._interests_layout = QVBoxLayout(self._interests_container)
        self._interests_layout.setContentsMargins(0, 0, 0, 0)
        self._interests_card.layout().addWidget(self._interests_container)
        self._interests_loading = QLabel("로딩 중...")
        self._interests_loading.setStyleSheet("color: #374151; font-size: 13px;")
        self._interests_layout.addWidget(self._interests_loading)
        layout.addWidget(self._interests_card)
        
        # AI Analysis card (복원됨)
        self._analysis_card = self._create_card("🤖 AI 분석")
        self._analysis_container = QWidget()
        self._analysis_layout = QVBoxLayout(self._analysis_container)
        self._analysis_layout.setContentsMargins(0, 0, 0, 0)
        self._analysis_card.layout().addWidget(self._analysis_container)
        self._analysis_loading = QLabel("분석 데이터가 없습니다.\n채팅을 통해 분석을 요청해보세요!")
        self._analysis_loading.setStyleSheet("color: #374151; font-size: 13px;")
        self._analysis_layout.addWidget(self._analysis_loading)
        layout.addWidget(self._analysis_card)
        
        # Notes card
        notes_card = self._create_card("📝 아이디어 노트")
        notes_layout = notes_card.layout()
        
        # Note input section
        input_container = QWidget()
        input_layout = QVBoxLayout(input_container)
        input_layout.setContentsMargins(0, 0, 0, 10)
        
        # Title input
        title_label = QLabel("제목")
        title_label.setStyleSheet("color: #374151; font-size: 13px; font-weight: 500;")
        input_layout.addWidget(title_label)
        
        self._note_title_input = QLineEdit()
        self._note_title_input.setPlaceholderText("노트 제목을 입력하세요")
        self._note_title_input.setStyleSheet("""
            QLineEdit {
                border: 1px solid #D1D5DB;
                border-radius: 4px;
                padding: 8px;
                background-color: #F9FAFB;
            }
        """)
        input_layout.addWidget(self._note_title_input)
        
        # Content input
        content_label = QLabel("내용")
        content_label.setStyleSheet("color: #374151; font-size: 13px; font-weight: 500;")
        input_layout.addWidget(content_label)
        
        self._note_content_input = QTextEdit()
        self._note_content_input.setPlaceholderText("노트 내용을 입력하세요")
        self._note_content_input.setMaximumHeight(100)
        self._note_content_input.setStyleSheet("""
            QTextEdit {
                border: 1px solid #D1D5DB;
                border-radius: 4px;
                padding: 8px;
                background-color: #FFFBEB;
            }
        """)
        input_layout.addWidget(self._note_content_input)
        
        # Buttons
        btn_layout = QHBoxLayout()
        
        save_btn = QPushButton("💾 저장")
        save_btn.setStyleSheet("""
            QPushButton {
                background-color: #1a1a1a;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 8px 16px;
            }
            QPushButton:hover {
                background-color: #2a2a2a;
            }
        """)
        save_btn.clicked.connect(self._save_note)
        btn_layout.addWidget(save_btn)
        
        clear_btn = QPushButton("🗑️ 초기화")
        clear_btn.setStyleSheet("""
            QPushButton {
                background-color: #E5E7EB;
                color: #374151;
                border: none;
                border-radius: 4px;
                padding: 8px 16px;
            }
            QPushButton:hover {
                background-color: #D1D5DB;
            }
        """)
        clear_btn.clicked.connect(self._clear_note_form)
        btn_layout.addWidget(clear_btn)
        
        btn_layout.addStretch()
        input_layout.addLayout(btn_layout)
        
        notes_layout.addWidget(input_container)
        
        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("background-color: #E5E7EB;")
        sep.setFixedHeight(1)
        notes_layout.addWidget(sep)
        
        # Notes list
        saved_label = QLabel("저장된 노트")
        saved_label.setStyleSheet("color: #374151; font-size: 12px; margin-top: 10px;")
        notes_layout.addWidget(saved_label)
        
        self._notes_container = QWidget()
        self._notes_list_layout = QVBoxLayout(self._notes_container)
        self._notes_list_layout.setContentsMargins(0, 0, 0, 0)
        self._notes_loading = QLabel("로딩 중...")
        self._notes_loading.setStyleSheet("color: #374151;")
        self._notes_list_layout.addWidget(self._notes_loading)
        notes_layout.addWidget(self._notes_container)
        
        layout.addWidget(notes_card)
        
        layout.addStretch()
        
        scroll.setWidget(container)
        return scroll
    
    def _create_card(self, title: str) -> QFrame:
        """Create a card widget."""
        card = QFrame()
        card.setStyleSheet("""
            QFrame {
                background-color: white;
                border: 1px solid #E5E7EB;
                border-radius: 8px;
            }
        """)
        
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 12, 16, 16)
        layout.setSpacing(12)
        
        # Title
        title_label = QLabel(title)
        title_font = QFont()
        title_font.setPointSize(12)
        title_font.setBold(True)
        title_label.setFont(title_font)
        layout.addWidget(title_label)
        
        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("background-color: #E5E7EB;")
        sep.setFixedHeight(1)
        layout.addWidget(sep)
        
        return card
    
    def _create_stat_widget(self, icon: str, value: str, label: str) -> QWidget:
        """Create a stat display widget."""
        widget = QWidget()
        widget.setStyleSheet("""
            QWidget {
                background-color: #F3F4F6;
                border-radius: 8px;
                padding: 10px;
            }
        """)
        
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        # Icon
        icon_label = QLabel(icon)
        icon_font = QFont()
        icon_font.setPointSize(20)
        icon_label.setFont(icon_font)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(icon_label)
        
        # Value
        value_label = QLabel(value)
        value_font = QFont()
        value_font.setPointSize(16)
        value_font.setBold(True)
        value_label.setFont(value_font)
        value_label.setStyleSheet("color: #1a1a1a;")
        value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(value_label)
        
        # Label
        text_label = QLabel(label)
        text_label.setStyleSheet("color: #374151;")
        text_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(text_label)
        
        return widget
    
    def load_data(self):
        """Load dashboard data from API."""
        if not self._jwt_token:
            return
        
        # Emit loading started signal
        self.loading_started.emit()
        
        self._data_worker = DashboardDataWorker(self._jwt_token, self)
        self._data_worker.data_loaded.connect(self._on_data_loaded)
        self._data_worker.notes_loaded.connect(self._on_notes_loaded)
        self._data_worker.analysis_loaded.connect(self._on_analysis_loaded)
        self._data_worker.error_occurred.connect(self._on_error)
        self._data_worker.finished.connect(lambda: self.loading_finished.emit())
        self._data_worker.start()
    
    def _on_data_loaded(self, data: dict):
        """Handle dashboard data loaded."""
        self._dashboard_data = data
        self._update_profile_ui()
        self._update_activity_ui()
        self._update_interests_ui()
    
    def _on_notes_loaded(self, notes: list):
        """Handle notes data loaded."""
        self._notes = notes
        self._update_notes_ui()
    
    def _on_analysis_loaded(self, analysis: dict):
        """Handle analysis data loaded."""
        self._update_analysis_ui(analysis)
    
    def _update_analysis_ui(self, analysis: dict):
        """Update AI analysis section with data."""
        # Clear existing
        while self._analysis_layout.count():
            item = self._analysis_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        
        if not analysis:
            empty_label = QLabel("분석 데이터가 없습니다.\n채팅을 통해 분석을 요청해보세요!")
            empty_label.setStyleSheet("color: #374151; font-size: 13px;")
            self._analysis_layout.addWidget(empty_label)
            return
        
        # Analysis title
        title = analysis.get("title", "최근 분석")
        title_label = QLabel(f"📊 {title}")
        title_font = QFont()
        title_font.setPointSize(12)
        title_font.setBold(True)
        title_label.setFont(title_font)
        title_label.setStyleSheet("color: #1a1a1a;")
        self._analysis_layout.addWidget(title_label)
        
        # Analysis summary
        summary = analysis.get("summary", "")
        if summary:
            summary_label = QLabel(summary[:300] + "..." if len(summary) > 300 else summary)
            summary_label.setWordWrap(True)
            summary_label.setStyleSheet("color: #374151; font-size: 12px; margin-top: 8px;")
            self._analysis_layout.addWidget(summary_label)
        
        # Analysis date
        created_at = analysis.get("created_at", "")
        if created_at:
            try:
                dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                date_str = dt.strftime("%Y-%m-%d %H:%M")
            except:
                date_str = created_at
            date_label = QLabel(f"⏰ {date_str}")
            date_label.setStyleSheet("color: #6B7280; font-size: 11px; margin-top: 8px;")
            self._analysis_layout.addWidget(date_label)
    
    def _on_error(self, error: str):
        """Handle data loading error."""
        try:
            self._profile_content.setText(f"❌ 데이터 로드 실패: {error}")
            print(f"⚠️ 대시보드 로드 에러: {error}")
        except RuntimeError:
            pass  # 위젯이 이미 삭제됨
    
    def _update_profile_ui(self):
        """Update profile section."""
        user_data = self._dashboard_data.get("user", {})
        
        email = user_data.get("email", "알 수 없음")
        folder = user_data.get("selected_folder", "설정 안됨") or "설정 안됨"
        
        if len(folder) > 50:
            folder = "..." + folder[-47:]
        
        created = user_data.get("created_at", "")
        created_str = ""
        if created:
            try:
                dt = datetime.fromisoformat(created.replace('Z', '+00:00'))
                created_str = dt.strftime("%Y년 %m월 %d일")
            except:
                created_str = created
        
        profile_text = f"📧 {email}\n📁 {folder}"
        if created_str:
            profile_text += f"\n📅 가입일: {created_str}"
        
        self._profile_content.setText(profile_text)
    
    def _update_activity_ui(self):
        """Update activity section."""
        # Clear existing
        while self._activity_layout.count():
            item = self._activity_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        
        activity = self._dashboard_data.get("activity", {})
        
        stats = [
            ("💬", str(activity.get("chat_messages", 0)), "채팅"),
            ("🌐", str(activity.get("browser_visits", 0)), "웹 방문"),
            ("📄", str(activity.get("files_processed", 0)), "파일 처리"),
        ]
        
        for icon, value, label in stats:
            stat_widget = self._create_stat_widget(icon, value, label)
            self._activity_layout.addWidget(stat_widget)
        
        self._activity_layout.addStretch()
    
    def _update_interests_ui(self):
        """Update interests section."""
        # Clear existing
        while self._interests_layout.count():
            item = self._interests_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        
        interests_data = self._dashboard_data.get("interests", {})
        top_interests = interests_data.get("top_interests", [])
        
        if not top_interests:
            empty_label = QLabel("아직 관심사가 없습니다. 채팅을 통해 관심사를 쌓아보세요!")
            empty_label.setStyleSheet("color: #374151;")
            self._interests_layout.addWidget(empty_label)
            return
        
        max_score = max(i.get("score", 0) for i in top_interests) if top_interests else 1
        
        for interest in top_interests:
            keyword = interest.get("keyword", "")
            score = interest.get("score", 0)
            bar_width = int((score / max_score) * 100) if max_score > 0 else 0
            
            item = QWidget()
            item_layout = QHBoxLayout(item)
            item_layout.setContentsMargins(0, 4, 0, 4)
            
            # Keyword
            keyword_label = QLabel(keyword)
            keyword_label.setFixedWidth(120)
            item_layout.addWidget(keyword_label)
            
            # Bar container
            bar_container = QFrame()
            bar_container.setFixedHeight(20)
            bar_container.setStyleSheet("background-color: #E5E7EB; border-radius: 4px;")
            bar_layout = QHBoxLayout(bar_container)
            bar_layout.setContentsMargins(0, 0, 0, 0)
            
            # Bar fill
            bar = QFrame()
            bar.setFixedWidth(int(bar_width * 2))  # Scale up for visibility
            bar.setStyleSheet("background-color: #1a1a1a; border-radius: 4px;")
            bar_layout.addWidget(bar)
            bar_layout.addStretch()
            
            item_layout.addWidget(bar_container, 1)
            
            # Score
            score_label = QLabel(f"{score:.2f}")
            score_label.setStyleSheet("color: #374151;")
            score_label.setFixedWidth(50)
            item_layout.addWidget(score_label)
            
            self._interests_layout.addWidget(item)
    
    def _update_notes_ui(self):
        """Update notes list."""
        # Clear existing
        while self._notes_list_layout.count():
            item = self._notes_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        
        if not self._notes:
            empty_label = QLabel("저장된 노트가 없습니다.")
            empty_label.setStyleSheet("color: #374151;")
            self._notes_list_layout.addWidget(empty_label)
            return
        
        for note in self._notes[:5]:  # Show max 5 notes
            note_widget = self._create_note_item(note)
            self._notes_list_layout.addWidget(note_widget)
    
    def _create_note_item(self, note: dict) -> QWidget:
        """Create a note list item."""
        item = QFrame()
        item.setStyleSheet("""
            QFrame {
                background-color: #FFFBEB;
                border: 1px solid #FEF3C7;
                border-radius: 4px;
                margin: 4px 0;
            }
        """)
        
        layout = QVBoxLayout(item)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(4)
        
        # Title row
        title_row = QHBoxLayout()
        
        title = QLabel(note.get("title", "제목 없음"))
        title_font = QFont()
        title_font.setBold(True)
        title.setFont(title_font)
        title_row.addWidget(title)
        
        title_row.addStretch()
        
        # Edit button
        edit_btn = QPushButton("✏️")
        edit_btn.setFixedSize(24, 24)
        edit_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                border: none;
            }
            QPushButton:hover {
                background-color: rgba(0, 0, 0, 0.1);
                border-radius: 4px;
            }
        """)
        edit_btn.clicked.connect(lambda: self._edit_note(note))
        title_row.addWidget(edit_btn)
        
        layout.addLayout(title_row)
        
        # Content preview
        content = note.get("content", "")
        if len(content) > 100:
            content = content[:100] + "..."
        
        content_label = QLabel(content)
        content_label.setStyleSheet("color: #374151;")
        content_label.setWordWrap(True)
        layout.addWidget(content_label)
        
        return item
    
    def _save_note(self):
        """Save current note."""
        title = self._note_title_input.text().strip()
        content = self._note_content_input.toPlainText().strip()
        
        if not title:
            QMessageBox.warning(self, "입력 오류", "제목을 입력해주세요.")
            return
        
        if not self._jwt_token:
            QMessageBox.warning(self, "인증 오류", "로그인이 필요합니다.")
            return
        
        self._note_worker = NoteSaveWorker(
            self._jwt_token,
            title,
            content,
            self._current_note_id,
            self
        )
        self._note_worker.save_success.connect(self._on_note_saved)
        self._note_worker.save_failed.connect(self._on_note_save_failed)
        self._note_worker.start()
    
    def _on_note_saved(self, note: dict):
        """Handle note saved successfully."""
        self._clear_note_form()
        self.load_data()  # Reload all data
    
    def _on_note_save_failed(self, error: str):
        """Handle note save failure."""
        QMessageBox.critical(self, "저장 오류", f"노트 저장에 실패했습니다:\n{error}")
    
    def _clear_note_form(self):
        """Clear the note input form."""
        self._note_title_input.clear()
        self._note_content_input.clear()
        self._current_note_id = None
    
    def _edit_note(self, note: dict):
        """Load note into edit form."""
        self._current_note_id = note.get("id")
        self._note_title_input.setText(note.get("title", ""))
        self._note_content_input.setText(note.get("content", ""))
    
    def refresh(self):
        """Refresh all dashboard data."""
        self.load_data()
