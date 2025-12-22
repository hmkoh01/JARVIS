"""
JARVIS Survey Dialog
Initial user survey for personalization.

Modern, clean design with improved color scheme.
"""

from typing import Optional, Dict, Any, List

import requests

from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QFrame,
    QScrollArea,
    QWidget,
    QRadioButton,
    QCheckBox,
    QLineEdit,
    QButtonGroup,
    QMessageBox
)
from PyQt6.QtCore import Qt, pyqtSignal, QThread
from PyQt6.QtGui import QFont, QPixmap

from utils.path_utils import get_resource_path

try:
    from config import API_BASE_URL
except ImportError:
    API_BASE_URL = "http://localhost:8000"


class SurveySubmitWorker(QThread):
    """Background worker for submitting survey data."""
    
    success = pyqtSignal()
    failed = pyqtSignal(str)
    
    def __init__(self, user_id: int, survey_data: dict, parent=None):
        super().__init__(parent)
        self.user_id = user_id
        self.survey_data = survey_data
    
    def run(self):
        """Submit survey data to backend."""
        try:
            response = requests.post(
                f"{API_BASE_URL}/api/v2/user-profile/{self.user_id}/update",
                json=self.survey_data,
                timeout=30
            )
            
            if response.status_code == 200:
                result = response.json()
                if result.get("success"):
                    self.success.emit()
                else:
                    self.failed.emit(result.get("message", "Unknown error"))
            else:
                self.failed.emit(f"HTTP {response.status_code}")
        except requests.exceptions.ConnectionError:
            self.failed.emit("서버에 연결할 수 없습니다")
        except Exception as e:
            self.failed.emit(str(e))


class SurveyDialog(QDialog):
    """
    Survey dialog for collecting user preferences.
    
    Modern design with gradient header and clean card layout.
    """
    
    survey_completed = pyqtSignal(bool)
    
    # Survey options
    JOB_OPTIONS = [
        ("👨‍🎓 학생", "student"),
        ("👨‍💻 개발자 / 엔지니어", "developer"),
        ("🎨 디자이너", "designer"),
        ("📊 기획자 / 마케터", "planner"),
        ("🔬 연구원 / 교육자", "researcher"),
        ("✏️ 기타 (직접 입력)", "other")
    ]
    
    INTEREST_OPTIONS = [
        ("💻 IT / 최신 기술", "tech"),
        ("💰 경제 / 금융 / 투자", "finance"),
        ("🤖 인공지능 / 데이터 과학", "ai"),
        ("🎨 디자인 / 예술", "design"),
        ("📈 마케팅 / 비즈니스", "marketing"),
        ("⚡ 생산성 / 자기계발", "productivity"),
        ("🏃 건강 / 운동", "health"),
        ("✈️ 여행 / 문화", "travel")
    ]
    
    HELP_OPTIONS = [
        ("🔍 업무 관련 정보 검색 및 요약", "work_search"),
        ("💡 새로운 아이디어나 영감 얻기", "inspiration"),
        ("✍️ 글쓰기 (이메일, 보고서 등) 보조", "writing"),
        ("📚 개인적인 학습 및 지식 확장", "learning")
    ]
    
    def __init__(self, user_id: int = 1, parent=None):
        super().__init__(parent)
        
        self.user_id = user_id
        self._submit_worker: Optional[SurveySubmitWorker] = None
        self._was_submitted = False
        
        # UI state
        self._job_group: Optional[QButtonGroup] = None
        self._job_other_input: Optional[QLineEdit] = None
        self._interest_checkboxes: Dict[str, QCheckBox] = {}
        self._help_checkboxes: Dict[str, QCheckBox] = {}
        self._keywords_input: Optional[QLineEdit] = None
        
        self._setup_dialog()
        self._setup_ui()
    
    def _setup_dialog(self):
        """Configure dialog properties."""
        self.setWindowTitle("JARVIS 초기 설정")
        self.setMinimumSize(650, 800)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self._center_on_screen()
    
    def _center_on_screen(self):
        """Center dialog on screen."""
        from PyQt6.QtWidgets import QApplication
        screen = QApplication.primaryScreen()
        if screen:
            geometry = screen.availableGeometry()
            x = (geometry.width() - self.width()) // 2 + geometry.x()
            y = (geometry.height() - self.height()) // 2 + geometry.y()
            self.move(x, y)
    
    def _setup_ui(self):
        """Set up the dialog UI."""
        self.setStyleSheet("""
            QDialog {
                background-color: #121212;
            }
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Header with gradient
        header = self._create_header()
        layout.addWidget(header)
        
        # Scrollable content
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setStyleSheet("""
            QScrollArea {
                border: none;
                background-color: #121212;
            }
            QScrollBar:vertical {
                background-color: #1a1a1a;
                width: 8px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical {
                background-color: #475569;
                border-radius: 4px;
                min-height: 40px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0;
            }
        """)
        
        content_widget = QWidget()
        content_widget.setStyleSheet("background-color: #121212;")
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(24, 24, 24, 24)
        content_layout.setSpacing(20)
        
        # Questions
        q1 = self._create_question_card(
            "1",
            "현재 당신의 직업 또는 주된 활동 분야는?",
            "(단일 선택)",
            self._create_job_content()
        )
        content_layout.addWidget(q1)
        
        q2 = self._create_question_card(
            "2",
            "요즘 가장 흥미를 느끼는 주제는?",
            "(최대 3개 선택)",
            self._create_interests_content()
        )
        content_layout.addWidget(q2)
        
        q3 = self._create_question_card(
            "3",
            "JARVIS를 통해 어떤 도움을 받고 싶으신가요?",
            "(최대 2개 선택)",
            self._create_help_content()
        )
        content_layout.addWidget(q3)
        
        q4 = self._create_question_card(
            "4",
            "특별히 관심있는 키워드가 있다면?",
            "(선택 사항)",
            self._create_keywords_content()
        )
        content_layout.addWidget(q4)
        
        content_layout.addStretch()
        
        scroll_area.setWidget(content_widget)
        layout.addWidget(scroll_area, 1)
        
        # Footer
        footer = self._create_footer()
        layout.addWidget(footer)
    
    def _create_header(self) -> QWidget:
        """Create header with dark theme matching chat UI."""
        header = QFrame()
        header.setStyleSheet("""
            QFrame {
                background-color: #1a1a1a;
                border-bottom: 1px solid #2a2a2a;
            }
        """)
        
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(32, 24, 32, 24)
        
        # Logo Icon
        icon = QLabel()
        icon_pixmap = QPixmap(get_resource_path("icons/jarvis_logo.png"))
        if not icon_pixmap.isNull():
            icon.setPixmap(icon_pixmap.scaled(56, 56, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        else:
            icon.setText("J")
            icon_font = QFont()
            icon_font.setPointSize(36)
            icon_font.setBold(True)
            icon.setFont(icon_font)
            icon.setStyleSheet("color: #e8e8e8;")
        header_layout.addWidget(icon)
        
        header_layout.addSpacing(16)
        
        # Title and subtitle
        text_layout = QVBoxLayout()
        text_layout.setSpacing(6)
        
        title = QLabel("JARVIS 초기 설정")
        title_font = QFont()
        title_font.setPointSize(20)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setStyleSheet("color: #e8e8e8;")
        text_layout.addWidget(title)
        
        subtitle = QLabel("맞춤형 AI 어시스턴트를 위해 몇 가지만 알려주세요")
        subtitle.setStyleSheet("color: #6a6a6a; font-size: 13px;")
        subtitle.setWordWrap(True)
        text_layout.addWidget(subtitle)
        
        header_layout.addLayout(text_layout, 1)
        
        return header
    
    def _create_question_card(self, number: str, question: str, hint: str, content: QWidget) -> QWidget:
        """Create a styled question card."""
        card = QFrame()
        card.setStyleSheet("""
            QFrame {
                background-color: #1a1a1a;
                border: 1px solid #2a2a2a;
                border-radius: 16px;
            }
        """)
        
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(14)
        
        # Question header
        header_layout = QHBoxLayout()
        
        # Number badge
        number_badge = QLabel(number)
        number_badge.setFixedSize(28, 28)
        number_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        number_badge.setStyleSheet("""
            background-color: #3a3a3a;
            color: #e8e8e8;
            border-radius: 14px;
            font-weight: bold;
            font-size: 13px;
        """)
        header_layout.addWidget(number_badge)
        
        header_layout.addSpacing(12)
        
        # Question text
        q_layout = QVBoxLayout()
        q_layout.setSpacing(2)
        
        q_label = QLabel(question)
        q_font = QFont()
        q_font.setPointSize(13)
        q_font.setBold(True)
        q_label.setFont(q_font)
        q_label.setStyleSheet("color: #F1F5F9;")
        q_label.setWordWrap(True)
        q_layout.addWidget(q_label)
        
        hint_label = QLabel(hint)
        hint_label.setStyleSheet("color: #94A3B8; font-size: 11px;")
        q_layout.addWidget(hint_label)
        
        header_layout.addLayout(q_layout, 1)
        
        layout.addLayout(header_layout)
        layout.addWidget(content)
        
        return card
    
    def _create_job_content(self) -> QWidget:
        """Create job selection content."""
        widget = QWidget()
        widget.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        
        self._job_group = QButtonGroup(self)
        
        for label, value in self.JOB_OPTIONS:
            radio = QRadioButton(label)
            radio.setProperty("value", value)
            radio.setStyleSheet("""
                QRadioButton {
                    color: #E2E8F0;
                    padding: 10px 12px;
                    background-color: #242424;
                    border-radius: 8px;
                    font-size: 13px;
                }
                QRadioButton:hover {
                    background-color: #2a2a2a;
                }
                QRadioButton::indicator {
                    width: 18px;
                    height: 18px;
                    border: 2px solid #4a4a4a;
                    border-radius: 9px;
                    background-color: transparent;
                }
                QRadioButton::indicator:checked {
                    background-color: #1a1a1a;
                    border-color: #e8e8e8;
                }
            """)
            radio.toggled.connect(self._on_job_changed)
            self._job_group.addButton(radio)
            layout.addWidget(radio)
        
        # Other input field
        self._job_other_input = QLineEdit()
        self._job_other_input.setPlaceholderText("직접 입력...")
        self._job_other_input.setEnabled(False)
        self._job_other_input.setStyleSheet("""
            QLineEdit {
                padding: 12px 14px;
                border: 1px solid #3a3a3a;
                border-radius: 8px;
                background-color: #242424;
                color: #E2E8F0;
                font-size: 13px;
            }
            QLineEdit:disabled {
                background-color: #1a1a1a;
                color: #5a5a5a;
            }
            QLineEdit:focus {
                border-color: #6a6a6a;
            }
        """)
        layout.addWidget(self._job_other_input)
        
        return widget
    
    def _on_job_changed(self, checked: bool):
        """Handle job selection change."""
        if not checked:
            return
        
        button = self._job_group.checkedButton()
        if button and button.property("value") == "other":
            self._job_other_input.setEnabled(True)
            self._job_other_input.setFocus()
        else:
            self._job_other_input.setEnabled(False)
            self._job_other_input.clear()
    
    def _create_interests_content(self) -> QWidget:
        """Create interests selection content."""
        widget = QWidget()
        widget.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        
        for label, value in self.INTEREST_OPTIONS:
            checkbox = QCheckBox(label)
            checkbox.setProperty("value", value)
            checkbox.setStyleSheet("""
                QCheckBox {
                    color: #E2E8F0;
                    padding: 10px 12px;
                    background-color: #242424;
                    border-radius: 8px;
                    font-size: 13px;
                }
                QCheckBox:hover {
                    background-color: #2a2a2a;
                }
                QCheckBox::indicator {
                    width: 18px;
                    height: 18px;
                    border: 2px solid #4a4a4a;
                    border-radius: 4px;
                    background-color: transparent;
                }
                QCheckBox::indicator:checked {
                    background-color: #1a1a1a;
                    border-color: #e8e8e8;
                }
            """)
            self._interest_checkboxes[value] = checkbox
            layout.addWidget(checkbox)
        
        return widget
    
    def _create_help_content(self) -> QWidget:
        """Create help preferences content."""
        widget = QWidget()
        widget.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        
        for label, value in self.HELP_OPTIONS:
            checkbox = QCheckBox(label)
            checkbox.setProperty("value", value)
            checkbox.setStyleSheet("""
                QCheckBox {
                    color: #E2E8F0;
                    padding: 10px 12px;
                    background-color: #242424;
                    border-radius: 8px;
                    font-size: 13px;
                }
                QCheckBox:hover {
                    background-color: #2a2a2a;
                }
                QCheckBox::indicator {
                    width: 18px;
                    height: 18px;
                    border: 2px solid #4a4a4a;
                    border-radius: 4px;
                    background-color: transparent;
                }
                QCheckBox::indicator:checked {
                    background-color: #1a1a1a;
                    border-color: #e8e8e8;
                }
            """)
            self._help_checkboxes[value] = checkbox
            layout.addWidget(checkbox)
        
        return widget
    
    def _create_keywords_content(self) -> QWidget:
        """Create custom keywords content."""
        widget = QWidget()
        widget.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        
        example = QLabel("예: 딥러닝, NFT, 행동경제학, 클린 아키텍처")
        example.setStyleSheet("color: #64748B; font-size: 12px;")
        layout.addWidget(example)
        
        self._keywords_input = QLineEdit()
        self._keywords_input.setPlaceholderText("관심 키워드를 입력하세요...")
        self._keywords_input.setStyleSheet("""
            QLineEdit {
                padding: 14px 16px;
                border: 1px solid #3a3a3a;
                border-radius: 10px;
                background-color: #242424;
                color: #E2E8F0;
                font-size: 13px;
            }
            QLineEdit:focus {
                border-color: #6a6a6a;
            }
        """)
        layout.addWidget(self._keywords_input)
        
        return widget
    
    def _create_footer(self) -> QWidget:
        """Create footer with buttons."""
        footer = QFrame()
        footer.setStyleSheet("""
            QFrame {
                background-color: #1a1a1a;
                border-top: 1px solid #2a2a2a;
            }
        """)
        
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(24, 16, 24, 16)
        
        layout.addStretch()
        
        # Skip button
        skip_btn = QPushButton("건너뛰기")
        skip_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #94A3B8;
                border: 1px solid #475569;
                border-radius: 10px;
                padding: 12px 28px;
                font-weight: 600;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #2a2a2a;
                color: #E2E8F0;
            }
        """)
        skip_btn.clicked.connect(self._skip_survey)
        layout.addWidget(skip_btn)
        
        layout.addSpacing(12)
        
        # Submit button
        self._submit_btn = QPushButton("완료 →")
        self._submit_btn.setStyleSheet("""
            QPushButton {
                background-color: #e8e8e8;
                color: #1a1a1a;
                border: none;
                border-radius: 10px;
                padding: 12px 32px;
                font-weight: 600;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #ffffff;
            }
            QPushButton:disabled {
                background-color: #2a2a2a;
                color: #6a6a6a;
            }
        """)
        self._submit_btn.clicked.connect(self._submit_survey)
        layout.addWidget(self._submit_btn)
        
        return footer
    
    def _collect_survey_data(self) -> Optional[Dict[str, Any]]:
        """Collect survey data from form."""
        job_field = ""
        job_field_other = ""
        
        if self._job_group.checkedButton():
            job_field = self._job_group.checkedButton().property("value")
            if job_field == "other":
                job_field_other = self._job_other_input.text().strip()
        
        interests = [
            value for value, checkbox in self._interest_checkboxes.items()
            if checkbox.isChecked()
        ]
        
        if len(interests) > 3:
            QMessageBox.warning(self, "선택 제한", "관심 주제는 최대 3개까지만 선택할 수 있습니다.")
            return None
        
        help_prefs = [
            value for value, checkbox in self._help_checkboxes.items()
            if checkbox.isChecked()
        ]
        
        if len(help_prefs) > 2:
            QMessageBox.warning(self, "선택 제한", "도움 받고 싶은 영역은 최대 2개까지만 선택할 수 있습니다.")
            return None
        
        custom_keywords = self._keywords_input.text().strip() if self._keywords_input else ""
        
        return {
            "job_field": job_field,
            "job_field_other": job_field_other,
            "interests": interests,
            "help_preferences": help_prefs,
            "custom_keywords": custom_keywords
        }
    
    def _submit_survey(self):
        """Submit survey data."""
        survey_data = self._collect_survey_data()
        if survey_data is None:
            return
        
        self._submit_btn.setEnabled(False)
        self._submit_btn.setText("제출 중...")
        
        self._submit_worker = SurveySubmitWorker(self.user_id, survey_data, self)
        self._submit_worker.success.connect(self._on_submit_success)
        self._submit_worker.failed.connect(self._on_submit_failed)
        self._submit_worker.start()
    
    def _on_submit_success(self):
        """Handle successful submission."""
        self._was_submitted = True
        self.survey_completed.emit(True)
        self.accept()
    
    def _on_submit_failed(self, error: str):
        """Handle submission failure."""
        self._submit_btn.setEnabled(True)
        self._submit_btn.setText("완료 →")
        QMessageBox.critical(self, "저장 오류", f"설문 저장에 실패했습니다.\n{error}")
    
    def _skip_survey(self):
        """Skip the survey."""
        result = QMessageBox.question(
            self,
            "설문 건너뛰기",
            "설문을 건너뛰시겠습니까?\n나중에 설정에서 입력할 수 있습니다.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        
        if result == QMessageBox.StandardButton.Yes:
            self.survey_completed.emit(False)
            self.accept()
    
    def closeEvent(self, event):
        """Handle dialog close."""
        if not self._was_submitted:
            result = QMessageBox.question(
                self,
                "종료",
                "설문을 완료하지 않고 종료하시겠습니까?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            
            if result != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        
        super().closeEvent(event)


def show_survey_dialog(user_id: int = 1, parent=None) -> bool:
    """Show survey dialog."""
    dialog = SurveyDialog(user_id, parent)
    result = dialog.exec()
    return result == QDialog.DialogCode.Accepted and dialog._was_submitted
