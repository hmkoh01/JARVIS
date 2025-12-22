"""
JARVIS PyQt6 Frontend - Main Entry Point

실행: 프로젝트 루트에서 `python frontend/pyqt6/main.py`

Phase 5: Complete Integration Flow
- Login → Initial Setup → Floating Button → Chat → Dashboard → Notifications
"""

import sys
import os
from pathlib import Path
from typing import Optional

# =============================================================================
# Windows Console Encoding Fix & Qt Warning Suppression
# =============================================================================
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# Suppress Qt layered window warnings (must be set before PyQt6 import)
os.environ["QT_LOGGING_RULES"] = "qt.qpa.window=false"

def _qt_message_handler(mode, context, message):
    """Custom Qt message handler to filter out known harmless warnings."""
    # Suppress UpdateLayeredWindowIndirect warnings on Windows
    if "UpdateLayeredWindowIndirect" in message:
        return
    # Print other messages normally
    print(message)

# Will be installed after PyQt6 import

# =============================================================================
# Path Setup
# =============================================================================

def setup_paths():
    """
    sys.path에 필요한 디렉토리를 추가하여
    config.py, token_store.py 및 pyqt6 내부 모듈을 import 가능하게 함.
    """
    current_file = Path(__file__).resolve()
    pyqt6_dir = current_file.parent
    frontend_dir = pyqt6_dir.parent
    project_root = frontend_dir.parent
    
    # pyqt6 디렉토리를 path에 추가 (내부 모듈 import용)
    if str(pyqt6_dir) not in sys.path:
        sys.path.insert(0, str(pyqt6_dir))
    
    # frontend 디렉토리를 path에 추가 (config.py, token_store.py import용)
    if str(frontend_dir) not in sys.path:
        sys.path.insert(0, str(frontend_dir))
    
    # 프로젝트 루트도 추가 (backend 등 다른 모듈 접근용)
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    
    return project_root, frontend_dir, pyqt6_dir


PROJECT_ROOT, FRONTEND_DIR, PYQT6_DIR = setup_paths()


# =============================================================================
# External Imports
# =============================================================================

try:
    from config import API_BASE_URL, WS_BASE_URL
    print(f"✅ config.py import 성공")
    print(f"   API_BASE_URL: {API_BASE_URL}")
    print(f"   WS_BASE_URL: {WS_BASE_URL}")
except ImportError as e:
    print(f"❌ config.py import 실패: {e}")
    API_BASE_URL = "http://localhost:8000"
    WS_BASE_URL = "ws://localhost:8000"

try:
    from token_store import load_token, get_valid_token_and_user, save_token
    print(f"✅ token_store.py import 성공")
    TOKEN_STORE_AVAILABLE = True
except ImportError as e:
    print(f"❌ token_store.py import 실패: {e}")
    load_token = None
    get_valid_token_and_user = None
    save_token = None
    TOKEN_STORE_AVAILABLE = False


# =============================================================================
# PyQt6 DLL Path Setup (MUST be before PyQt6 import)
# =============================================================================

def setup_qt_dll_paths():
    """PyInstaller 빌드에서 Qt6 DLL 경로를 설정합니다."""
    if getattr(sys, 'frozen', False):
        # PyInstaller로 빌드된 경우
        base_path = sys._MEIPASS
        
        # 가능한 Qt6 bin 경로들
        possible_paths = [
            os.path.join(base_path, 'PyQt6', 'Qt6', 'bin'),
            os.path.join(base_path, 'Qt6', 'bin'),
            base_path,  # DLL이 루트에 복사된 경우
        ]
        
        for qt_bin_path in possible_paths:
            if os.path.exists(qt_bin_path):
                # PATH 환경변수에 추가
                os.environ['PATH'] = qt_bin_path + os.pathsep + os.environ.get('PATH', '')
                
                # Windows DLL 검색 경로에 추가 (Python 3.8+)
                if hasattr(os, 'add_dll_directory'):
                    try:
                        os.add_dll_directory(qt_bin_path)
                    except Exception:
                        pass
        
        # Qt 플러그인 경로 설정
        qt_plugins_paths = [
            os.path.join(base_path, 'PyQt6', 'Qt6', 'plugins'),
            os.path.join(base_path, 'Qt6', 'plugins'),
        ]
        for plugins_path in qt_plugins_paths:
            if os.path.exists(plugins_path):
                os.environ['QT_PLUGIN_PATH'] = plugins_path
                break

setup_qt_dll_paths()


def get_resource_path(relative_path: str) -> str:
    """
    PyInstaller 번들 또는 일반 실행 환경에서 리소스 경로를 반환합니다.
    
    Args:
        relative_path: 리소스의 상대 경로 (예: 'resources/icons/jarvis.ico')
    
    Returns:
        절대 경로 문자열
    """
    if getattr(sys, 'frozen', False):
        # PyInstaller로 빌드된 경우
        base_path = Path(sys._MEIPASS)
    else:
        # 일반 Python 실행
        base_path = PYQT6_DIR
    return str(base_path / relative_path)


# =============================================================================
# PyQt6 Imports
# =============================================================================

from PyQt6.QtWidgets import QApplication, QMessageBox
from PyQt6.QtCore import Qt, QTimer, qInstallMessageHandler, QThread, pyqtSignal, QObject
from PyQt6.QtGui import QIcon

# Install custom message handler to suppress known harmless warnings
qInstallMessageHandler(_qt_message_handler)


# =============================================================================
# Local Imports
# =============================================================================

from utils.theme_manager import ThemeManager
from views.main_window import MainWindow
from views.floating_button import FloatingButton
from views.toast_notification import ToastManager, ToastType
from views.dialogs.login_dialog import LoginDialog
from views.dialogs.survey_dialog import SurveyDialog
from views.dialogs.folder_dialog import FolderDialog
from services.api_client import APIClient
from services.websocket_client import WebSocketManager
from controllers.chat_controller import ChatController
from controllers.auth_controller import AuthController


# =============================================================================
# Background Workers
# =============================================================================

class RecommendationResponseWorker(QThread):
    """
    Background worker for handling recommendation responses.
    Prevents UI blocking during API calls.
    """
    finished = pyqtSignal(dict)  # {success, action, keyword, result}
    error = pyqtSignal(str)
    
    def __init__(self, url: str, token: str, action: str, keyword: str, parent=None):
        super().__init__(parent)
        self.url = url
        self.token = token
        self.action = action
        self.keyword = keyword
    
    def run(self):
        import requests
        try:
            response = requests.post(
                self.url,
                headers={"Authorization": f"Bearer {self.token}"},
                json={"action": self.action},
                timeout=30
            )
            
            if response.status_code == 200:
                result = response.json()
                self.finished.emit({
                    "success": True,
                    "action": self.action,
                    "keyword": self.keyword,
                    "result": result
                })
            else:
                self.error.emit(f"서버 오류: {response.status_code}")
                
        except requests.exceptions.Timeout:
            self.error.emit("서버 응답이 너무 오래 걸립니다.")
        except requests.exceptions.ConnectionError:
            self.error.emit("서버에 연결할 수 없습니다.")
        except Exception as e:
            self.error.emit(f"오류: {str(e)}")


# =============================================================================
# Application Class
# =============================================================================

class JARVISApp:
    """
    Main application controller with complete integration flow.
    
    Flow:
    1. Initialize QApplication and ThemeManager
    2. Check authentication via AuthController
    3. If not authenticated → Show LoginDialog
    4. If needs initial setup → Show SurveyDialog → FolderDialog
    5. Create MainWindow (hidden) and FloatingButton (visible)
    6. Connect WebSocket for notifications
    7. Run event loop
    """
    
    def __init__(self):
        self._app: Optional[QApplication] = None
        self._theme_manager: Optional[ThemeManager] = None
        self._main_window: Optional[MainWindow] = None
        self._floating_button: Optional[FloatingButton] = None
        self._toast_manager: Optional[ToastManager] = None
        
        # Controllers
        self._auth_controller: Optional[AuthController] = None
        self._chat_controller: Optional[ChatController] = None
        
        # Services
        self._api_client: Optional[APIClient] = None
        self._ws_manager: Optional[WebSocketManager] = None
        
        # Initial setup state tracking
        self._is_initial_setup_in_progress = False
        self._initial_setup_progress = 0
        self._initial_setup_message = ""
        self._progress_poll_timer: Optional[QTimer] = None
    
    def initialize(self) -> bool:
        """Initialize the application."""
        print("=" * 60)
        print("JARVIS PyQt6 Frontend - Phase 5")
        print("Complete Integration Flow")
        print("=" * 60)
        
        # High DPI 스케일링 환경 변수 설정 (QApplication 생성 전에 설정해야 함)
        os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"
        os.environ["QT_SCALE_FACTOR_ROUNDING_POLICY"] = "Round"
        
        # Create QApplication
        self._app = QApplication(sys.argv)
        self._app.setApplicationName("JARVIS")
        self._app.setOrganizationName("JARVIS")
        
        # Set application icon (EXE 아이콘과 별도로 윈도우/작업표시줄 아이콘 설정)
        icon_path = get_resource_path('resources/icons/jarvis.ico')
        if os.path.exists(icon_path):
            self._app.setWindowIcon(QIcon(icon_path))
            print(f"✅ Application icon set: {icon_path}")
        else:
            print(f"⚠️ Icon file not found: {icon_path}")
        
        # Enable high DPI scaling with Round policy for sharper text
        self._app.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.Round
        )
        
        # Initialize theme manager
        self._theme_manager = ThemeManager(PYQT6_DIR / "resources")
        saved_theme = self._theme_manager.initialize(self._app, use_saved=True)
        print(f"✅ Theme initialized: {saved_theme}")
        
        # Initialize toast manager
        self._toast_manager = ToastManager()
        print("✅ Toast manager initialized")
        
        # Initialize auth controller
        self._auth_controller = AuthController()
        
        # Initialize services
        self._init_services()
        
        # =====================================================================
        # Authentication Flow
        # =====================================================================
        
        if not self._handle_authentication():
            print("❌ Authentication failed or cancelled")
            return False
        
        # =====================================================================
        # Initial Setup Flow (if needed)
        # =====================================================================
        
        if self._auth_controller.needs_initial_setup():
            print("📋 Initial setup required...")
            self._handle_initial_setup()
            # 초기 설정을 건너뛰거나 취소해도 앱은 계속 실행됨
            print("✅ Continuing with app initialization...")
        
        # =====================================================================
        # Create Main UI
        # =====================================================================
        
        # Update services with auth
        token, user_id = self._auth_controller.get_credentials()
        if token and user_id:
            self._api_client.set_auth(token, user_id)
            self._ws_manager.set_token(token)
        
        # Create main window
        self._main_window = MainWindow(theme_manager=self._theme_manager)
        self._setup_main_window()
        
        # Create floating button
        self._floating_button = FloatingButton(main_window=self._main_window)
        self._setup_floating_button()
        
        # Initialize chat controller
        self._init_chat_controller()
        
        # Setup dashboard and other widgets
        self._setup_dashboard()
        
        # Setup settings widget with user info
        self._setup_settings()
        
        print("✅ Application initialized successfully")
        return True
    
    def _init_services(self):
        """Initialize API and WebSocket services."""
        self._api_client = APIClient(API_BASE_URL)
        print(f"✅ API client initialized: {API_BASE_URL}")
        
        self._ws_manager = WebSocketManager(API_BASE_URL)
        print(f"✅ WebSocket manager initialized: {WS_BASE_URL}")
    
    def _handle_authentication(self) -> bool:
        """
        Handle authentication flow.
        
        Returns:
            True if authenticated successfully, False otherwise
        """
        print("\n🔐 Checking authentication...")
        
        # Try to load existing token
        if self._auth_controller.initialize():
            user_id = self._auth_controller.get_user_id()
            print(f"✅ Authenticated with existing token (User ID: {user_id})")
            return True
        
        # No valid token - show login dialog
        print("ℹ️ No valid token found, showing login dialog...")
        
        login_dialog = LoginDialog()
        login_dialog.login_success.connect(self._on_login_success)
        
        result = login_dialog.exec()
        print(f"🔐 Login dialog result: {result}")
        
        if result == LoginDialog.DialogCode.Accepted:
            user_info = login_dialog.user_info
            print(f"🔐 User info from dialog: {user_info}")
            
            if user_info:
                self._auth_controller.set_user_info(user_info)
                print(f"✅ Login successful (User ID: {user_info.get('user_id')})")
                return True
            elif self._auth_controller.is_authenticated():
                # 시그널을 통해 이미 인증 정보가 설정된 경우
                print(f"✅ Login successful via signal (User ID: {self._auth_controller.get_user_id()})")
                return True
        
        print("❌ Login cancelled or failed")
        return False
    
    def _on_login_success(self, user_info: dict):
        """Handle login success signal."""
        self._auth_controller.set_user_info(user_info)
    
    def _handle_initial_setup(self) -> bool:
        """
        Handle initial setup flow (survey + folder selection).
        
        Returns:
            True if setup completed, False if cancelled
        """
        print("\n📋 Starting initial setup...")
        
        user_id = self._auth_controller.get_user_id() or 1
        
        # Show survey dialog
        print("📝 Showing survey dialog...")
        survey_dialog = SurveyDialog(user_id)
        survey_result = survey_dialog.exec()
        
        if survey_result != SurveyDialog.DialogCode.Accepted:
            # Check if user explicitly cancelled
            result = QMessageBox.question(
                None,
                "설정 취소",
                "초기 설정을 건너뛰시겠습니까?\n나중에 설정에서 완료할 수 있습니다.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if result != QMessageBox.StandardButton.Yes:
                return False
            # User chose to skip - continue without survey
        
        # Show folder selection dialog
        print("📁 Showing folder selection dialog...")
        folder_dialog = FolderDialog()
        folder_result = folder_dialog.exec()
        
        selected_folders = None
        if folder_result == FolderDialog.DialogCode.Accepted:
            selected_folders = folder_dialog.get_selected_paths()
            print(f"✅ Selected folders: {len(selected_folders) if selected_folders else 0}")
        else:
            # Check if user wants to skip
            result = QMessageBox.question(
                None,
                "폴더 선택 취소",
                "폴더 선택을 건너뛰시겠습니까?\n나중에 설정에서 선택할 수 있습니다.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if result != QMessageBox.StandardButton.Yes:
                return False
        
        # Submit folder setup to backend and start data collection
        if selected_folders:
            self._submit_folder_setup(selected_folders)
            # Mark that we're in initial setup mode (will be checked later)
            self._is_initial_setup_in_progress = True
        
        print("✅ Initial setup dialog flow completed")
        return True
    
    def _submit_folder_setup(self, folders: list):
        """Submit folder selection to backend and start data collection."""
        import requests
        
        token = self._auth_controller.get_token()
        user_id = self._auth_controller.get_user_id()
        if not token or not user_id:
            return
        
        try:
            folder_path = folders[0] if folders else ""
            
            # 1. Submit initial setup (save folder path)
            response = requests.post(
                f"{API_BASE_URL}/api/v2/settings/initial-setup",
                headers={"Authorization": f"Bearer {token}"},
                json={"folder_path": folder_path},
                timeout=10
            )
            
            if response.status_code == 200:
                print("✅ Folder setup submitted to backend")
            else:
                print(f"⚠️ Folder setup submission failed: {response.status_code}")
                return
            
            # 2. Start data collection
            collection_response = requests.post(
                f"{API_BASE_URL}/api/v2/data-collection/start/{user_id}",
                headers={"Authorization": f"Bearer {token}"},
                json={"selected_folders": folders},
                timeout=10
            )
            
            if collection_response.status_code == 200:
                print("✅ Data collection started")
                self._start_initial_setup_tracking()
            else:
                print(f"⚠️ Data collection start failed: {collection_response.status_code}")
                
        except Exception as e:
            print(f"⚠️ Folder setup submission error: {e}")
    
    def _start_initial_setup_tracking(self):
        """Start tracking initial setup progress."""
        self._is_initial_setup_in_progress = True
        self._initial_setup_progress = 0
        self._initial_setup_message = "초기 데이터 수집 시작 중..."
        
        # Start progress polling timer (every 3 seconds)
        if self._progress_poll_timer is None:
            self._progress_poll_timer = QTimer()
            self._progress_poll_timer.timeout.connect(self._poll_initial_setup_progress)
        
        self._progress_poll_timer.start(3000)  # Poll every 3 seconds
        print("✅ Initial setup tracking started")
    
    def _poll_initial_setup_progress(self):
        """Poll backend for initial setup progress."""
        import requests
        
        token = self._auth_controller.get_token()
        user_id = self._auth_controller.get_user_id()
        
        if not token or not user_id:
            return
        
        try:
            response = requests.get(
                f"{API_BASE_URL}/api/v2/data-collection/status/{user_id}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=5
            )
            
            if response.status_code == 200:
                data = response.json()
                self._initial_setup_progress = data.get("progress", 0)
                self._initial_setup_message = data.get("progress_message", "처리 중...")
                is_done = data.get("is_done", False)
                
                print(f"📊 Initial setup progress: {self._initial_setup_progress}% - {self._initial_setup_message}")
                
                if is_done:
                    self._on_initial_setup_complete()
            elif response.status_code == 404:
                # Manager not found, might not have started yet
                pass
                
        except Exception as e:
            print(f"⚠️ Progress polling error: {e}")
    
    def _on_initial_setup_complete(self):
        """Handle initial setup completion."""
        print("✅ Initial setup completed!")
        
        # Stop progress polling
        if self._progress_poll_timer:
            self._progress_poll_timer.stop()
        
        # Update state
        self._is_initial_setup_in_progress = False
        self._initial_setup_progress = 100
        
        # Stop loading animation on floating button
        if self._floating_button:
            self._floating_button.set_loading(False)
        
        # Show completion toast
        if self._toast_manager:
            self._toast_manager.success(
                "🎉 초기 설정 완료",
                "데이터 수집이 완료되었습니다! 이제 JARVIS를 사용할 수 있습니다.",
                duration_ms=6000
            )
    
    def _setup_main_window(self):
        """Configure the main window."""
        self._main_window.window_hidden.connect(self._on_window_hidden)
        self._main_window.window_shown.connect(self._on_window_shown)
        
        # Update status based on auth
        user_id = self._auth_controller.get_user_id()
        if user_id:
            self._main_window.set_user_info(f"User ID: {user_id}")
            self._main_window.set_connection_status("Ready", True)
        else:
            self._main_window.set_user_info("Not logged in")
            self._main_window.set_connection_status("Not authenticated", False)
    
    def _setup_floating_button(self):
        """Configure the floating button."""
        self._floating_button.exit_requested.connect(self._on_exit_requested)
        self._floating_button.settings_requested.connect(self._on_settings_requested)
        
        # Set custom click handler to handle initial setup state
        self._floating_button.set_click_handler(self._on_floating_button_clicked)
        
        # Connect loading signals from API client
        self._connect_loading_signals()
    
    def _on_floating_button_clicked(self):
        """Handle floating button click - different behavior during initial setup."""
        if self._is_initial_setup_in_progress:
            # During initial setup, show progress toast instead of opening chat
            progress_text = f"{self._initial_setup_progress}% 완료"
            message = self._initial_setup_message if self._initial_setup_message else "데이터 수집 중..."
            
            self._toast_manager.info(
                f"⏳ 초기 설정 진행 중 ({progress_text})",
                message,
                duration_ms=3000
            )
        else:
            # Normal behavior - toggle main window
            self._toggle_main_window()
    
    def _toggle_main_window(self):
        """Toggle main window visibility."""
        if self._main_window is not None:
            if self._main_window.isVisible():
                self._main_window.hide()
            else:
                self._main_window.show()
                self._main_window.raise_()
                self._main_window.activateWindow()
    
    def _connect_loading_signals(self):
        """Connect loading state signals to floating button animation."""
        # API Client - general requests
        if self._api_client:
            self._api_client.request_started.connect(
                lambda: self._floating_button.set_loading(True)
            )
            self._api_client.request_completed.connect(
                lambda _: self._floating_button.set_loading(False)
            )
            self._api_client.request_error.connect(
                lambda _: self._floating_button.set_loading(False)
            )
    
    def _setup_dashboard(self):
        """Set up dashboard and recommendations with credentials."""
        token, user_id = self._auth_controller.get_credentials()
        
        if token and user_id:
            # Dashboard 초기화
            if hasattr(self._main_window, 'dashboard_widget'):
                self._main_window.dashboard_widget.set_credentials(token, user_id)
                
                # Connect dashboard loading signals to floating button
                self._main_window.dashboard_widget.loading_started.connect(
                    lambda: self._floating_button.set_loading(True)
                )
                self._main_window.dashboard_widget.loading_finished.connect(
                    lambda: self._floating_button.set_loading(False)
                )
                
                self._main_window.dashboard_widget.load_data()
                print("✅ Dashboard initialized with credentials")
            
            # Recommendations 초기화
            if hasattr(self._main_window, 'recommendations_widget'):
                self._main_window.recommendations_widget.set_credentials(token, user_id)
                
                # Connect recommendations loading signals to floating button
                self._main_window.recommendations_widget.loading_started.connect(
                    lambda: self._floating_button.set_loading(True)
                )
                self._main_window.recommendations_widget.loading_finished.connect(
                    lambda: self._floating_button.set_loading(False)
                )
                
                self._main_window.recommendations_widget.load_data()
                print("✅ Recommendations initialized with credentials")
    
    def _setup_settings(self):
        """Set up settings widget with user info."""
        user_info = self._auth_controller.get_user_info()
        token, user_id = self._auth_controller.get_credentials()
        
        if hasattr(self._main_window, 'settings_widget') and user_info:
            # 인증 정보 설정 (API 호출용)
            if token and user_id:
                self._main_window.settings_widget.set_credentials(token, user_id)
            
            # 이메일 정보 설정
            email = user_info.get('email', '') or user_info.get('sub', '') or f"User {user_id}"
            self._main_window.settings_widget.set_user_info(email, user_id or 0)
            
            # 선택된 폴더 정보 가져오기 (API에서)
            if token:
                self._load_user_folder_settings(token)
            
            # 로그아웃 시그널 연결
            self._main_window.settings_widget.logout_requested.connect(self._on_logout_requested)
            
            print("✅ Settings widget initialized with user info")
    
    def _load_user_folder_settings(self, token: str):
        """Load user folder settings from backend."""
        import requests
        try:
            response = requests.get(
                f"{API_BASE_URL}/api/v2/dashboard/summary",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    user_data = data.get("data", {}).get("user", {})
                    folder = user_data.get("selected_folder", "")
                    if folder:
                        self._main_window.settings_widget.set_selected_folders([folder])
        except Exception as e:
            print(f"⚠️ Failed to load folder settings: {e}")
    
    def _on_logout_requested(self):
        """Handle logout request from settings."""
        from token_store import delete_token
        try:
            delete_token()
            print("✅ Token deleted")
        except Exception as e:
            print(f"⚠️ Failed to delete token: {e}")
        
        # 앱 종료
        print("🚪 Logging out and closing application...")
        self._cleanup()
        
        # 모든 윈도우 강제 종료 (closeEvent 무시 방지)
        if self._main_window:
            self._main_window.hide()
            self._main_window.deleteLater()
        
        if self._floating_button:
            self._floating_button.hide()
            self._floating_button.deleteLater()
        
        if self._toast_manager:
            self._toast_manager.hide()
            self._toast_manager.deleteLater()
        
        # 앱 종료
        if self._app:
            self._app.quit()
        
        # 강제 종료 (위 quit()가 동작하지 않을 경우 대비)
        sys.exit(0)
    
    def _init_chat_controller(self):
        """Initialize the chat controller."""
        self._chat_controller = ChatController(
            chat_widget=self._main_window.chat_widget,
            api_client=self._api_client,
            ws_manager=self._ws_manager
        )
        
        # Connect notification signals for toast notifications
        self._chat_controller.notification_received.connect(self._on_notification)
        self._chat_controller.recommendation_received.connect(self._on_recommendation)
        self._chat_controller.report_notification.connect(self._on_report_notification)
        self._chat_controller.analysis_notification.connect(self._on_analysis_notification)
        
        # Connect confirmation action signal
        self._chat_controller.confirm_action_requested.connect(self._on_confirm_action_requested)
        
        # Connect chat widget confirmation signals
        self._main_window.chat_widget.confirmation_accepted.connect(self._on_confirmation_accepted)
        self._main_window.chat_widget.confirmation_rejected.connect(self._on_confirmation_rejected)
        
        # Connect chat streaming status to floating button loading animation
        self._chat_controller.sending_status_changed.connect(
            self._floating_button.set_loading
        )
        
        print("✅ Chat controller initialized")
    
    def _on_confirm_action_requested(self, metadata: dict):
        """Handle confirmation action request - show confirmation UI (buttons only)."""
        # 버튼만 표시 - 메시지는 이미 스트리밍으로 표시됨
        self._main_window.chat_widget.show_confirmation("", metadata)
    
    def _on_confirmation_accepted(self, metadata: dict):
        """Handle confirmation accepted - proceed with action."""
        action = metadata.get('action', '')
        keyword = metadata.get('keyword', '')
        recommendation_id = metadata.get('recommendation_id')
        
        # 액션 유형에 따라 처리
        if action == 'confirm_report':
            # 추천에서 온 경우 직접 API 호출, 아니면 채팅으로 처리
            if recommendation_id:
                self._create_report_from_recommendation(keyword, recommendation_id)
            else:
                self._chat_controller.send_message(f"네, '{keyword}' 보고서를 작성해주세요.")
            action_name = "보고서 작성"
        elif action == 'confirm_analysis':
            self._chat_controller.send_message(f"네, '{keyword}' 분석을 시작해주세요.")
            action_name = "분석"
        elif action == 'confirm_code':
            self._chat_controller.send_message(f"네, '{keyword}' 코드를 작성해주세요.")
            action_name = "코드 작성"
        elif action == 'confirm_dashboard':
            self._chat_controller.send_message("네, 대시보드 분석을 시작해주세요.")
            action_name = "대시보드 분석"
        else:
            self._chat_controller.send_message(f"네, '{keyword}' 작업을 진행해주세요.")
            action_name = "작업"
        
        self._toast_manager.success(
            f"{action_name} 시작",
            f"'{keyword}' {action_name}을(를) 시작합니다.",
            duration_ms=4000
        )
    
    def _create_report_from_recommendation(self, keyword: str, recommendation_id: int):
        """Create a deep-dive report from a recommendation."""
        import requests
        
        token, user_id = self._auth_controller.get_credentials()
        if not token:
            self._toast_manager.error("오류", "인증이 필요합니다.")
            return
        
        try:
            # 보고서 생성 API 호출 (백그라운드 처리됨)
            response = requests.post(
                f"{API_BASE_URL}/api/v2/reports/create",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "keyword": keyword,
                    "recommendation_id": recommendation_id
                },
                timeout=10
            )
            
            if response.status_code == 200:
                result = response.json()
                if result.get("success"):
                    # 채팅에 안내 메시지 추가
                    if hasattr(self._main_window, 'chat_widget'):
                        self._main_window.chat_widget.add_system_message(
                            f"📝 '{keyword}' 보고서 작성이 시작되었습니다. 완료되면 알려드릴게요!"
                        )
                    print(f"📝 Report creation started: {keyword}")
                else:
                    error_msg = result.get("message", "보고서 생성 요청 실패")
                    self._toast_manager.error("오류", error_msg)
            else:
                self._toast_manager.error("오류", f"서버 오류: {response.status_code}")
                
        except Exception as e:
            print(f"Error creating report: {e}")
            self._toast_manager.error("오류", f"보고서 생성 요청 중 오류: {str(e)}")
    
    def _on_confirmation_rejected(self, metadata: dict):
        """Handle confirmation rejected."""
        keyword = metadata.get('keyword', '')
        self._toast_manager.info(
            "작업 취소",
            f"'{keyword}' 작업이 취소되었습니다.",
            duration_ms=3000
        )
    
    # =========================================================================
    # Event Handlers
    # =========================================================================
    
    def _on_window_hidden(self):
        """Handle main window being hidden."""
        print("📦 Main window hidden")
    
    def _on_window_shown(self):
        """Handle main window being shown."""
        print("📱 Main window shown")
    
    def _on_exit_requested(self):
        """Handle exit request."""
        print("👋 Exit requested - cleaning up...")
        self._cleanup()
    
    def _on_settings_requested(self):
        """Handle settings request."""
        print("⚙️ Settings requested")
        self._toast_manager.info(
            "설정",
            "설정 기능은 준비 중입니다."
        )
    
    def _on_notification(self, notification):
        """Handle generic notification."""
        print(f"🔔 Notification: {notification}")
    
    def _on_recommendation(self, data: dict):
        """Handle recommendation notification - Show toast with action buttons."""
        from views.toast_notification import ToastAction
        
        keyword = data.get("keyword", "")
        recommendation_id = data.get("id")
        bubble_message = data.get("bubble_message", "")
        
        # 말풍선 메시지가 있으면 사용, 없으면 기본 메시지
        message = bubble_message if bubble_message else f"{keyword}에 대해 알아볼까요?"
        
        # 추천 수락/거절 콜백 함수
        def on_accept():
            self._handle_recommendation_response(recommendation_id, keyword, "accept")
        
        def on_reject():
            self._handle_recommendation_response(recommendation_id, keyword, "reject")
        
        # 액션 버튼이 있는 토스트 표시
        actions = [
            ToastAction("💡 관심 있어요", on_accept, primary=True),
            ToastAction("🚫 관심 없어요", on_reject, primary=False)
        ]
        
        self._toast_manager.info(
            f"📌 새로운 추천",
            message,
            duration_ms=15000,  # 액션 버튼이 있으므로 오래 표시
            actions=actions
        )
        print(f"📌 Recommendation toast shown: {keyword} (id={recommendation_id})")
    
    def _show_pending_recommendations(self):
        """앱 시작 시 대기 중인 추천을 API에서 가져와 토스트로 표시."""
        import requests
        
        token, user_id = self._auth_controller.get_credentials()
        if not token:
            return
        
        try:
            response = requests.get(
                f"{API_BASE_URL}/api/v2/recommendations",
                headers={"Authorization": f"Bearer {token}"},
                timeout=5
            )
            
            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    recommendations = data.get("recommendations", [])
                    if recommendations:
                        # 가장 최근 추천 1개만 토스트로 표시 (여러 개면 UI가 복잡해짐)
                        latest_rec = recommendations[0]
                        print(f"📌 대기 중인 추천 발견: {latest_rec.get('keyword')}")
                        self._on_recommendation(latest_rec)
                    else:
                        # 추천이 없으면 환영 메시지 표시
                        self._toast_manager.info(
                            "JARVIS 시작됨",
                            "안녕하세요! 무엇을 도와드릴까요?",
                            duration_ms=4000
                        )
        except Exception as e:
            print(f"⚠️ 대기 중인 추천 조회 실패: {e}")
            # 실패해도 환영 메시지 표시
            self._toast_manager.info(
                "JARVIS 시작됨",
                "안녕하세요! 무엇을 도와드릴까요?",
                duration_ms=4000
            )
    
    def _handle_recommendation_response(self, recommendation_id: int, keyword: str, action: str):
        """Handle user response to recommendation (accept/reject) - async."""
        token, user_id = self._auth_controller.get_credentials()
        if not token or not recommendation_id:
            self._toast_manager.error("오류", "추천 응답을 처리할 수 없습니다.")
            return
        
        # 로딩 표시
        self._floating_button.set_loading(True)
        
        # 진행 중 토스트 표시
        self._toast_manager.info(
            "⏳ 처리 중",
            f"'{keyword}' 요청을 처리하고 있습니다...",
            duration_ms=2000
        )
        
        # 비동기 워커 생성
        url = f"{API_BASE_URL}/api/v2/recommendations/{recommendation_id}/respond"
        worker = RecommendationResponseWorker(url, token, action, keyword)
        
        # 워커 완료 시 처리
        worker.finished.connect(
            lambda data: self._on_recommendation_response_finished(data, recommendation_id)
        )
        worker.error.connect(self._on_recommendation_response_error)
        
        # 워커 종료 시 정리
        worker.finished.connect(lambda: self._cleanup_recommendation_worker(worker))
        worker.error.connect(lambda: self._cleanup_recommendation_worker(worker))
        
        # 워커 저장 및 시작
        if not hasattr(self, '_recommendation_workers'):
            self._recommendation_workers = []
        self._recommendation_workers.append(worker)
        worker.start()
    
    def _cleanup_recommendation_worker(self, worker):
        """Clean up finished recommendation worker."""
        self._floating_button.set_loading(False)
        if hasattr(self, '_recommendation_workers') and worker in self._recommendation_workers:
            self._recommendation_workers.remove(worker)
    
    def _on_recommendation_response_finished(self, data: dict, recommendation_id: int):
        """Handle successful recommendation response."""
        action = data.get("action")
        keyword = data.get("keyword", "")
        result = data.get("result", {})
        
        if action == "accept" and result.get("success"):
            # 수락 성공: 채팅창 열고 리포트 내용 표시
            report_content = result.get("report_content", "")
            offer_deep_dive = result.get("offer_deep_dive", False)
            
            # 메인 윈도우 및 채팅 탭 열기
            self._floating_button.on_click()
            if hasattr(self._main_window, 'set_current_tab'):
                self._main_window.set_current_tab(0)  # 채팅 탭
            
            # 채팅에 추천 관련 시스템 메시지 및 리포트 내용 추가
            if hasattr(self._main_window, 'chat_widget'):
                self._main_window.chat_widget.add_system_message(
                    f"📌 **{keyword}**에 대한 정보입니다!"
                )
                
                # 심층 보고서 제안 (offer_deep_dive가 True면)
                # 확인 버튼은 타이핑 애니메이션 완료 후 표시
                def show_confirmation_after_typing():
                    if offer_deep_dive and hasattr(self._main_window, 'chat_widget'):
                        confirm_metadata = {
                            "action": "confirm_report",
                            "keyword": keyword,
                            "recommendation_id": recommendation_id,
                            "brief_description": f"{keyword}에 대한 심층 보고서를 PDF로 작성해드릴 수 있습니다."
                        }
                        self._main_window.chat_widget.show_confirmation(
                            "",
                            confirm_metadata
                        )
                
                if report_content:
                    # 타이핑 완료 후 확인 버튼 표시
                    self._main_window.chat_widget.add_assistant_message(
                        report_content,
                        typing_animation=True,
                        on_complete=show_confirmation_after_typing if offer_deep_dive else None
                    )
                elif offer_deep_dive:
                    # 리포트 내용이 없어도 확인 버튼 표시
                    show_confirmation_after_typing()
            
            self._toast_manager.success(
                "📌 추천 수락",
                f"'{keyword}'에 대한 정보를 채팅창에서 확인하세요!",
                duration_ms=4000
            )
            print(f"✅ Recommendation accepted: {keyword}")
            
        elif action == "reject" and result.get("success"):
            # 거절 성공
            self._toast_manager.info(
                "🚫 추천 거절",
                f"'{keyword}'는 더 이상 추천되지 않습니다.",
                duration_ms=4000
            )
            print(f"❌ Recommendation rejected: {keyword}")
        else:
            # 실패
            error_msg = result.get("message", "처리 중 오류가 발생했습니다.")
            self._toast_manager.error("오류", error_msg)
    
    def _on_recommendation_response_error(self, error_msg: str):
        """Handle recommendation response error."""
        print(f"Error handling recommendation response: {error_msg}")
        self._toast_manager.error("오류", error_msg)
    
    def _on_report_notification(self, data: dict):
        """Handle report notification - Show toast with folder action."""
        success = data.get("success", False)
        keyword = data.get("keyword", "Report")
        message = data.get("message", "")
        
        if success:
            # 항상 클라이언트 로컬의 기본 Reports 폴더 사용
            # (서버 경로는 Linux 경로일 수 있으므로 사용하지 않음)
            import os
            from pathlib import Path
            local_folder = str(Path.home() / "Documents" / "JARVIS" / "Reports")
            
            # 폴더가 없으면 생성
            os.makedirs(local_folder, exist_ok=True)
            
            self._toast_manager.success_with_folder_action(
                "📄 리포트 완료",
                f"{keyword} 리포트가 생성되었습니다.\n폴더를 열어 확인하시겠습니까?",
                local_folder
            )
            print(f"📄 Report completed toast: {keyword}")
        else:
            self._toast_manager.error(
                "📄 리포트 실패",
                message or f"{keyword} 리포트 생성에 실패했습니다.",
                duration_ms=8000
            )
            print(f"❌ Report failed toast: {keyword}")
    
    def _on_analysis_notification(self, data: dict):
        """Handle analysis notification - Show toast with dashboard action."""
        success = data.get("success", False)
        title = data.get("title", "Analysis")
        message = data.get("message", "")
        
        if success:
            # Refresh dashboard
            if hasattr(self._main_window, 'dashboard_widget'):
                self._main_window.dashboard_widget.load_data()
            
            # 대시보드 열기 액션과 함께 토스트 표시
            def open_dashboard():
                if self._main_window:
                    self._main_window.show()
                    self._main_window.raise_()
                    self._main_window.activateWindow()
                    self._main_window.switch_to_dashboard()
            
            self._toast_manager.success_with_dashboard_action(
                "📊 분석 완료",
                f"'{title}' 분석이 완료되었습니다.\n대시보드에서 결과를 확인하시겠습니까?",
                open_dashboard
            )
            print(f"📊 Analysis completed toast: {title}")
        else:
            self._toast_manager.error(
                "📊 분석 실패",
                message or f"'{title}' 분석에 실패했습니다.",
                duration_ms=8000
            )
            print(f"❌ Analysis failed toast: {title}")
    
    # =========================================================================
    # Lifecycle
    # =========================================================================
    
    def run(self) -> int:
        """Run the application event loop."""
        # Start services
        token = self._auth_controller.get_token()
        if token:
            self._chat_controller.start()
            print("✅ Chat controller started (WebSocket connecting...)")
            
            # Check if initial setup is in progress
            if self._is_initial_setup_in_progress:
                # Start loading animation on floating button
                self._floating_button.set_loading(True)
                
                # Show initial setup toast
                self._toast_manager.info(
                    "⏳ 초기 데이터 수집 시작",
                    "데이터를 수집하고 있습니다. 완료되면 알려드릴게요!\n버튼을 클릭하면 진행 상황을 확인할 수 있습니다.",
                    duration_ms=6000
                )
                print("🔄 Initial setup in progress - loading animation started")
            else:
                # 앱 시작 시 대기 중인 추천 확인 및 표시
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(2000, self._show_pending_recommendations)
        else:
            self._toast_manager.warning(
                "로그인 필요",
                "로그인 후 모든 기능을 사용하실 수 있습니다.",
                duration_ms=6000
            )
        
        # Show floating button
        self._floating_button.show()
        print("✅ Floating button displayed")
        print("💡 Click the button to toggle the main window")
        print("💡 Right-click for context menu")
        print("💡 Type a message and press Enter to send")
        
        # Start event loop
        return self._app.exec()
    
    def _cleanup(self):
        """Clean up resources before exit."""
        print("🧹 Cleaning up...")
        
        # Stop progress polling timer
        if self._progress_poll_timer:
            self._progress_poll_timer.stop()
        
        # Clear toasts
        if self._toast_manager:
            self._toast_manager.clear_all()
        
        # Stop chat controller (stops WebSocket)
        if self._chat_controller:
            self._chat_controller.stop()
        
        # Stop API client workers
        if self._api_client:
            self._api_client.stop_all()
        
        print("✅ Cleanup complete")


# =============================================================================
# Main Entry Point
# =============================================================================

def main():
    """애플리케이션 진입점"""
    app = JARVISApp()
    
    if not app.initialize():
        print("❌ Failed to initialize application")
        return 1
    
    return app.run()


if __name__ == "__main__":
    sys.exit(main())
