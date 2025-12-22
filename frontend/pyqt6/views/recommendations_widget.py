"""
JARVIS Recommendations Widget
Displays personalized recommendations with accept/reject functionality.
"""

import os
import subprocess
import platform
from typing import Optional, List, Dict, Any

import requests

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QFrame,
    QMessageBox
)
from PyQt6.QtCore import Qt, pyqtSignal, QThread
from PyQt6.QtGui import QFont

try:
    from config import API_BASE_URL
except ImportError:
    API_BASE_URL = "http://localhost:8000"


class RecommendationsWorker(QThread):
    """Background worker for fetching recommendations."""
    
    data_loaded = pyqtSignal(list)
    error_occurred = pyqtSignal(str)
    
    def __init__(self, jwt_token: str, parent=None):
        super().__init__(parent)
        self.jwt_token = jwt_token
        self._api_base = f"{API_BASE_URL}/api/v2"
    
    def run(self):
        """Fetch recommendations."""
        try:
            headers = {"Authorization": f"Bearer {self.jwt_token}"}
            
            # 백엔드 API 엔드포인트: /api/v2/recommendations (읽지 않은 추천 목록)
            response = requests.get(
                f"{self._api_base}/recommendations",
                headers=headers,
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    # 백엔드 응답 구조: {"success": True, "recommendations": [...]}
                    recommendations = data.get("recommendations", [])
                    self.data_loaded.emit(recommendations)
                else:
                    self.error_occurred.emit("Failed to load recommendations")
            else:
                self.error_occurred.emit(f"HTTP {response.status_code}")
        except Exception as e:
            self.error_occurred.emit(str(e))


class RecommendationCard(QFrame):
    """A single recommendation card."""
    
    accepted = pyqtSignal(int)  # recommendation_id
    rejected = pyqtSignal(int)
    
    def __init__(self, recommendation: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.recommendation = recommendation
        self.rec_id = recommendation.get("id", 0)
        self.status = recommendation.get("status", "pending")  # 상태: pending, accepted, rejected
        self.report_path = recommendation.get("report_file_path", "")  # 보고서 파일 경로
        
        self._setup_ui()
    
    def _setup_ui(self):
        """Set up the card UI."""
        self.setObjectName("recommendationCard")
        self.setStyleSheet("""
            #recommendationCard {
                background-color: #FFFFFF;
                border: 1px solid #E5E7EB;
                border-radius: 12px;
                margin: 4px 0;
            }
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        
        # Header row (keyword + status badge + type badge)
        header = QHBoxLayout()
        
        keyword = self.recommendation.get("keyword", "추천")
        keyword_label = QLabel(keyword)
        keyword_font = QFont()
        keyword_font.setPointSize(13)
        keyword_font.setBold(True)
        keyword_label.setFont(keyword_font)
        keyword_label.setStyleSheet("color: #1F2937;")
        header.addWidget(keyword_label)
        
        header.addStretch()
        
        # Status badge (수락/거절 상태 표시)
        if self.status == "accepted":
            status_badge = QLabel("✓ 수락됨")
            status_badge.setStyleSheet("""
                background-color: #D1FAE5;
                color: #065F46;
                padding: 4px 10px;
                border-radius: 12px;
                font-size: 11px;
                font-weight: 600;
            """)
            header.addWidget(status_badge)
        elif self.status == "rejected":
            status_badge = QLabel("✕ 거절됨")
            status_badge.setStyleSheet("""
                background-color: #FEE2E2;
                color: #991B1B;
                padding: 4px 10px;
                border-radius: 12px;
                font-size: 11px;
                font-weight: 600;
            """)
            header.addWidget(status_badge)
        
        # Type badge
        rec_type = self.recommendation.get("type", "general")
        type_badge = QLabel(f"#{rec_type}")
        type_badge.setStyleSheet("""
            background-color: #EEF2FF;
            color: #4F46E5;
            padding: 4px 10px;
            border-radius: 12px;
            font-size: 11px;
            font-weight: 600;
        """)
        header.addWidget(type_badge)
        
        layout.addLayout(header)
        
        # Title
        title = self.recommendation.get("title", "")
        if title:
            title_label = QLabel(title)
            title_font = QFont()
            title_font.setPointSize(11)
            title_label.setFont(title_font)
            title_label.setStyleSheet("color: #374151;")
            title_label.setWordWrap(True)
            layout.addWidget(title_label)
        
        # Summary
        summary = self.recommendation.get("summary", "")
        if summary:
            summary_label = QLabel(summary[:200] + "..." if len(summary) > 200 else summary)
            summary_label.setStyleSheet("color: #6B7280; font-size: 12px;")
            summary_label.setWordWrap(True)
            layout.addWidget(summary_label)
        
        # URL preview (if available)
        url = self.recommendation.get("url", "")
        if url:
            url_label = QLabel(f"🔗 {url[:60]}..." if len(url) > 60 else f"🔗 {url}")
            url_label.setStyleSheet("color: #6a6a6a; font-size: 11px;")
            layout.addWidget(url_label)
        
        # Action buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)
        
        btn_layout.addStretch()
        
        # 보고서 열기 버튼 (보고서가 있을 때만 활성화)
        self.report_btn = QPushButton("📄 보고서 열기")
        has_report = bool(self.report_path and os.path.exists(self.report_path))
        
        if has_report:
            self.report_btn.setStyleSheet("""
                QPushButton {
                    background-color: #3B82F6;
                    color: white;
                    border: none;
                    border-radius: 6px;
                    padding: 8px 16px;
                    font-weight: 600;
                    font-size: 12px;
                }
                QPushButton:hover {
                    background-color: #2563EB;
                }
            """)
            self.report_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.report_btn.clicked.connect(self._open_report_location)
        else:
            self.report_btn.setStyleSheet("""
                QPushButton {
                    background-color: #E5E7EB;
                    color: #9CA3AF;
                    border: none;
                    border-radius: 6px;
                    padding: 8px 16px;
                    font-weight: 600;
                    font-size: 12px;
                }
            """)
            self.report_btn.setEnabled(False)
            self.report_btn.setToolTip("보고서가 아직 생성되지 않았습니다")
        
        btn_layout.addWidget(self.report_btn)
        
        # pending 상태일 때만 관심없음/확인하기 버튼 표시
        if self.status == "pending":
            reject_btn = QPushButton("✕ 관심없음")
            reject_btn.setStyleSheet("""
                QPushButton {
                    background-color: #FEE2E2;
                    color: #DC2626;
                    border: none;
                    border-radius: 6px;
                    padding: 8px 16px;
                    font-weight: 600;
                    font-size: 12px;
                }
                QPushButton:hover {
                    background-color: #FECACA;
                }
            """)
            reject_btn.clicked.connect(lambda: self.rejected.emit(self.rec_id))
            btn_layout.addWidget(reject_btn)
            
            accept_btn = QPushButton("✓ 확인하기")
            accept_btn.setStyleSheet("""
                QPushButton {
                    background-color: #10B981;
                    color: white;
                    border: none;
                    border-radius: 6px;
                    padding: 8px 16px;
                    font-weight: 600;
                    font-size: 12px;
                }
                QPushButton:hover {
                    background-color: #059669;
                }
            """)
            accept_btn.clicked.connect(lambda: self.accepted.emit(self.rec_id))
            btn_layout.addWidget(accept_btn)
        
        layout.addLayout(btn_layout)
    
    def _open_report_location(self):
        """보고서 파일 위치 열기 (파일 탐색기에서 해당 파일 선택)"""
        if not self.report_path or not os.path.exists(self.report_path):
            return
        
        try:
            system = platform.system()
            if system == "Windows":
                # Windows: 파일 탐색기에서 파일 선택
                subprocess.run(['explorer', '/select,', os.path.normpath(self.report_path)])
            elif system == "Darwin":
                # macOS: Finder에서 파일 선택
                subprocess.run(['open', '-R', self.report_path])
            else:
                # Linux: 파일이 있는 폴더 열기
                folder = os.path.dirname(self.report_path)
                subprocess.run(['xdg-open', folder])
        except Exception as e:
            print(f"파일 위치 열기 실패: {e}")
    
    def update_report_path(self, report_path: str):
        """보고서 경로 업데이트 및 버튼 활성화"""
        self.report_path = report_path
        if report_path and os.path.exists(report_path):
            self.report_btn.setEnabled(True)
            self.report_btn.setStyleSheet("""
                QPushButton {
                    background-color: #3B82F6;
                    color: white;
                    border: none;
                    border-radius: 6px;
                    padding: 8px 16px;
                    font-weight: 600;
                    font-size: 12px;
                }
                QPushButton:hover {
                    background-color: #2563EB;
                }
            """)
            self.report_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.report_btn.setToolTip("")
            self.report_btn.clicked.connect(self._open_report_location)


class RecommendationsWidget(QWidget):
    """
    Widget displaying personalized recommendations.
    
    Features:
    - List of recommendation cards
    - Accept/reject functionality
    - Pull to refresh
    """
    
    recommendation_accepted = pyqtSignal(int)
    recommendation_rejected = pyqtSignal(int)
    loading_started = pyqtSignal()
    loading_finished = pyqtSignal()
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        self._jwt_token: Optional[str] = None
        self._user_id: Optional[int] = None
        self._recommendations: List[Dict[str, Any]] = []
        self._worker: Optional[RecommendationsWorker] = None
        
        self._setup_ui()
    
    def set_credentials(self, jwt_token: str, user_id: int):
        """Set authentication credentials."""
        self._jwt_token = jwt_token
        self._user_id = user_id
    
    def _setup_ui(self):
        """Set up the widget UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Header
        header = self._create_header()
        layout.addWidget(header)
        
        # Content area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("background-color: #F3F4F6;")
        
        self._content_widget = QWidget()
        self._content_widget.setStyleSheet("background-color: #F3F4F6;")
        self._content_layout = QVBoxLayout(self._content_widget)
        self._content_layout.setContentsMargins(16, 16, 16, 16)
        self._content_layout.setSpacing(12)
        
        # Loading/empty state
        self._status_label = QLabel("로딩 중...")
        self._status_label.setStyleSheet("color: #6B7280; padding: 40px;")
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._content_layout.addWidget(self._status_label)
        
        self._content_layout.addStretch()
        
        scroll.setWidget(self._content_widget)
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
        
        title = QLabel("📌 추천")
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
        subtitle = QLabel("당신에게 맞춤화된 콘텐츠 추천입니다")
        subtitle.setStyleSheet("color: rgba(255, 255, 255, 0.8);")
        layout.addWidget(subtitle)
        
        return header
    
    def load_data(self):
        """Load recommendations from API."""
        if not self._jwt_token:
            self._status_label.setText("로그인이 필요합니다")
            return
        
        # Emit loading started signal
        self.loading_started.emit()
        
        self._status_label.setText("로딩 중...")
        self._status_label.show()
        
        self._worker = RecommendationsWorker(self._jwt_token, self)
        self._worker.data_loaded.connect(self._on_data_loaded)
        self._worker.error_occurred.connect(self._on_error)
        self._worker.finished.connect(lambda: self.loading_finished.emit())
        self._worker.start()
    
    def _on_data_loaded(self, recommendations: list):
        """Handle recommendations loaded."""
        self._recommendations = recommendations
        self._update_ui()
    
    def _on_error(self, error: str):
        """Handle loading error."""
        self._status_label.setText(f"❌ 로드 실패: {error}")
    
    def _update_ui(self):
        """Update UI with recommendations."""
        # Clear existing cards
        while self._content_layout.count() > 1:
            item = self._content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        
        if not self._recommendations:
            self._status_label.setText("📭 새로운 추천이 없습니다\n\n채팅을 통해 관심사를 알려주시면\n맞춤형 추천을 받으실 수 있습니다")
            self._status_label.show()
            return
        
        self._status_label.hide()
        
        for rec in self._recommendations:
            card = RecommendationCard(rec)
            card.accepted.connect(self._on_recommendation_accepted)
            card.rejected.connect(self._on_recommendation_rejected)
            self._content_layout.insertWidget(self._content_layout.count() - 1, card)
    
    def _on_recommendation_accepted(self, rec_id: int):
        """Handle recommendation accepted."""
        self._send_feedback(rec_id, "accept")  # 백엔드 API는 "accept"을 기대
        self.recommendation_accepted.emit(rec_id)
        self._remove_card(rec_id)
    
    def _on_recommendation_rejected(self, rec_id: int):
        """Handle recommendation rejected."""
        self._send_feedback(rec_id, "reject")  # 백엔드 API는 "reject"을 기대
        self.recommendation_rejected.emit(rec_id)
        self._remove_card(rec_id)
    
    def _send_feedback(self, rec_id: int, action: str):
        """Send feedback to backend in a background thread."""
        if not self._jwt_token:
            return
        
        # 백그라운드 스레드에서 API 호출 (UI 블로킹 방지)
        from threading import Thread
        
        def send_request():
            try:
                headers = {"Authorization": f"Bearer {self._jwt_token}"}
                # 백엔드 API 엔드포인트: /api/v2/recommendations/{id}/respond
                # action: "accept" 또는 "reject"
                response = requests.post(
                    f"{API_BASE_URL}/api/v2/recommendations/{rec_id}/respond",
                    headers=headers,
                    json={"action": action},
                    timeout=10
                )
                if response.status_code == 200:
                    data = response.json()
                    if data.get("success"):
                        print(f"✅ 추천 피드백 전송 성공: {action}")
                    else:
                        print(f"⚠️ 추천 피드백 실패: {data.get('message', 'Unknown error')}")
                else:
                    print(f"⚠️ 추천 피드백 HTTP 오류: {response.status_code}")
            except Exception as e:
                print(f"Feedback error: {e}")
        
        thread = Thread(target=send_request, daemon=True)
        thread.start()
    
    def _remove_card(self, rec_id: int):
        """Remove a recommendation card from UI with fade animation."""
        from PyQt6.QtCore import QPropertyAnimation, QEasingCurve
        from PyQt6.QtWidgets import QGraphicsOpacityEffect
        
        for i in range(self._content_layout.count()):
            item = self._content_layout.itemAt(i)
            if item and item.widget():
                widget = item.widget()
                if isinstance(widget, RecommendationCard) and widget.rec_id == rec_id:
                    # 페이드 아웃 효과 적용
                    effect = QGraphicsOpacityEffect(widget)
                    widget.setGraphicsEffect(effect)
                    
                    animation = QPropertyAnimation(effect, b"opacity")
                    animation.setDuration(300)
                    animation.setStartValue(1.0)
                    animation.setEndValue(0.0)
                    animation.setEasingCurve(QEasingCurve.Type.OutCubic)
                    animation.finished.connect(widget.deleteLater)
                    animation.start()
                    
                    # 참조 저장 (가비지 컬렉션 방지)
                    widget._fade_animation = animation
                    
                    self._recommendations = [r for r in self._recommendations if r.get("id") != rec_id]
                    break
        
        # Show empty state if no more cards
        if not self._recommendations:
            self._status_label.setText("📭 모든 추천을 확인했습니다!")
            self._status_label.show()

