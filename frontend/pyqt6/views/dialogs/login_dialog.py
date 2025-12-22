"""
JARVIS Login Dialog
Google OAuth login dialog with modern dark theme.
"""

import http.server
import socketserver
import threading
import time
import webbrowser
from urllib.parse import urlparse, parse_qs
from typing import Optional, Dict, Any

import requests

from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QFrame,
    QProgressBar
)
from PyQt6.QtCore import Qt, pyqtSignal, QThread
from PyQt6.QtGui import QFont, QPixmap

from utils.path_utils import get_resource_path

try:
    from token_store import save_token as store_save_token
except ImportError:
    store_save_token = None

try:
    from config import API_BASE_URL, CALLBACK_PORT, CALLBACK_URI
except ImportError:
    API_BASE_URL = "http://localhost:8000"
    CALLBACK_PORT = 9090
    CALLBACK_URI = f"http://127.0.0.1:{CALLBACK_PORT}/auth/callback"


class OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
    """HTTP handler for OAuth callback."""
    
    callback_code: Optional[str] = None
    callback_event: threading.Event = threading.Event()
    
    def do_GET(self):
        """Handle GET request from OAuth redirect."""
        if self.path.startswith('/auth/callback'):
            parsed_url = urlparse(self.path)
            query_params = parse_qs(parsed_url.query)
            
            code = query_params.get('code', [None])[0]
            
            if code:
                OAuthCallbackHandler.callback_code = code
                
                self.send_response(200)
                self.send_header('Content-type', 'text/html; charset=utf-8')
                self.end_headers()
                
                success_html = """
                <!DOCTYPE html>
                <html>
                <head>
                    <meta charset="UTF-8">
                    <title>인증 성공</title>
                    <style>
                        body { 
                            font-family: 'Segoe UI', system-ui, sans-serif; 
                            display: flex; 
                            justify-content: center; 
                            align-items: center; 
                            height: 100vh; 
                            margin: 0; 
                            background-color: #121212;
                        }
                        .container {
                            text-align: center;
                            background-color: #1a1a1a;
                            padding: 48px;
                            border-radius: 24px;
                            box-shadow: 0 25px 50px rgba(0,0,0,0.5);
                            border: 1px solid #2a2a2a;
                        }
                        h1 { 
                            color: #e8e8e8; 
                            margin-bottom: 16px;
                            font-size: 28px;
                        }
                        p { 
                            color: #6a6a6a; 
                            font-size: 16px;
                            margin: 8px 0;
                        }
                    </style>
                </head>
                <body>
                    <div class="container">
                        <h1>✅ 인증 성공!</h1>
                        <p>JARVIS 인증이 완료되었습니다.</p>
                        <p>이 창을 닫으셔도 됩니다.</p>
                    </div>
                </body>
                </html>
                """
                self.wfile.write(success_html.encode('utf-8'))
                OAuthCallbackHandler.callback_event.set()
            else:
                self.send_response(400)
                self.send_header('Content-type', 'text/html')
                self.end_headers()
                self.wfile.write(b"<html><body><h1>Error</h1></body></html>")
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        pass


class OAuthWorker(QThread):
    """Background worker for OAuth flow."""
    
    status_changed = pyqtSignal(str)
    login_success = pyqtSignal(dict)
    login_failed = pyqtSignal(str)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._server: Optional[socketserver.TCPServer] = None
        self._should_stop = False
    
    def run(self):
        """Execute OAuth flow."""
        try:
            self.status_changed.emit("콜백 서버 시작 중...")
            self._start_server()
            time.sleep(0.5)
            
            self.status_changed.emit("로그인 URL 요청 중...")
            response = requests.get(f"{API_BASE_URL}/auth/google/login", timeout=10)
            
            if response.status_code != 200:
                raise Exception(f"로그인 URL 요청 실패: {response.status_code}")
            
            login_data = response.json()
            login_url = login_data.get("login_url")
            
            if not login_url:
                raise Exception("로그인 URL을 받지 못했습니다")
            
            self.status_changed.emit("브라우저에서 로그인하세요...")
            webbrowser.open(login_url)
            
            OAuthCallbackHandler.callback_event.clear()
            OAuthCallbackHandler.callback_code = None
            
            if OAuthCallbackHandler.callback_event.wait(timeout=300):
                if OAuthCallbackHandler.callback_code:
                    self.status_changed.emit("토큰 교환 중...")
                    
                    exchange_response = requests.post(
                        f"{API_BASE_URL}/auth/google/exchange-code",
                        json={"code": OAuthCallbackHandler.callback_code},
                        timeout=30
                    )
                    
                    if exchange_response.status_code != 200:
                        raise Exception(f"토큰 교환 실패: {exchange_response.status_code}")
                    
                    auth_data = exchange_response.json()
                    jarvis_token = auth_data.get("jarvis_token")
                    
                    if not jarvis_token:
                        raise Exception("토큰을 받지 못했습니다")
                    
                    self.status_changed.emit("토큰 저장 중...")
                    if store_save_token:
                        store_save_token(jarvis_token)
                    
                    user_info = {
                        "user_id": auth_data.get("user_id"),
                        "email": auth_data.get("email"),
                        "has_completed_setup": auth_data.get("has_completed_setup"),
                        "selected_root_folder": auth_data.get("selected_root_folder"),
                        "jarvis_token": jarvis_token
                    }
                    
                    self.login_success.emit(user_info)
                else:
                    raise Exception("인증 코드를 받지 못했습니다")
            else:
                raise Exception("로그인 시간 초과 (5분)")
                
        except Exception as e:
            self.login_failed.emit(str(e))
        finally:
            self._stop_server()
    
    def _start_server(self):
        """Start local callback server."""
        try:
            self._server = socketserver.TCPServer(
                ("127.0.0.1", CALLBACK_PORT),
                OAuthCallbackHandler
            )
            self._server.allow_reuse_address = True
            
            def run_server():
                try:
                    self._server.serve_forever()
                except Exception:
                    pass
            
            threading.Thread(target=run_server, daemon=True).start()
        except Exception as e:
            raise Exception(f"콜백 서버 시작 실패: {e}")
    
    def _stop_server(self):
        """Stop local callback server."""
        if self._server:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass
    
    def stop(self):
        """Stop the OAuth flow."""
        self._should_stop = True
        OAuthCallbackHandler.callback_event.set()
        self._stop_server()


class LoginDialog(QDialog):
    """
    Login dialog with Google OAuth.
    Modern dark theme design.
    """
    
    login_success = pyqtSignal(dict)
    login_cancelled = pyqtSignal()
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        self._oauth_worker: Optional[OAuthWorker] = None
        self._user_info: Optional[Dict[str, Any]] = None
        
        self._setup_dialog()
        self._setup_ui()
    
    def _setup_dialog(self):
        """Configure dialog properties."""
        self.setWindowTitle("JARVIS Login")
        self.setFixedSize(480, 420)
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
        header = QFrame()
        header.setStyleSheet("""
            QFrame {
                background-color: #1a1a1a;
                border-bottom-left-radius: 32px;
                border-bottom-right-radius: 32px;
            }
        """)
        header.setFixedHeight(160)
        
        header_layout = QVBoxLayout(header)
        header_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        # Logo
        logo = QLabel()
        logo_pixmap = QPixmap(get_resource_path("icons/jarvis_logo.png"))
        if not logo_pixmap.isNull():
            logo.setPixmap(logo_pixmap.scaled(72, 72, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        else:
            logo.setText("J")
            logo_font = QFont()
            logo_font.setPointSize(48)
            logo_font.setBold(True)
            logo.setFont(logo_font)
            logo.setStyleSheet("color: #e8e8e8;")
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header_layout.addWidget(logo)
        
        # Title
        title = QLabel("JARVIS")
        title_font = QFont()
        title_font.setPointSize(28)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setStyleSheet("color: #e8e8e8;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header_layout.addWidget(title)
        
        layout.addWidget(header)
        
        # Content
        content = QFrame()
        content.setStyleSheet("background-color: #121212;")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(40, 32, 40, 32)
        content_layout.setSpacing(20)
        
        # Instruction
        instruction = QLabel("Google 계정으로 로그인하여\nJARVIS를 시작하세요")
        instruction.setAlignment(Qt.AlignmentFlag.AlignCenter)
        instruction.setStyleSheet("""
            color: #6a6a6a;
            font-size: 14px;
            line-height: 1.5;
        """)
        content_layout.addWidget(instruction)
        
        content_layout.addSpacing(8)
        
        # Google login button
        self._login_btn = QPushButton("  Google로 로그인")
        self._login_btn.setStyleSheet("""
            QPushButton {
                background-color: #e8e8e8;
                color: #1a1a1a;
                border: none;
                border-radius: 12px;
                padding: 16px 32px;
                font-size: 15px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #ffffff;
            }
            QPushButton:disabled {
                background-color: #2a2a2a;
                color: #6a6a6a;
            }
        """)
        self._login_btn.clicked.connect(self._start_login)
        content_layout.addWidget(self._login_btn)
        
        # Progress bar
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        self._progress.setStyleSheet("""
            QProgressBar {
                border: none;
                border-radius: 4px;
                background-color: #2a2a2a;
                height: 6px;
            }
            QProgressBar::chunk {
                background-color: #e8e8e8;
                border-radius: 4px;
            }
        """)
        content_layout.addWidget(self._progress)
        
        # Status label
        self._status_label = QLabel("")
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_label.setStyleSheet("color: #6a6a6a; font-size: 13px;")
        content_layout.addWidget(self._status_label)
        
        content_layout.addStretch()
        
        layout.addWidget(content, 1)
    
    def _start_login(self):
        """Start the OAuth login flow."""
        self._login_btn.setEnabled(False)
        self._login_btn.setText("로그인 중...")
        self._progress.setVisible(True)
        
        self._oauth_worker = OAuthWorker(self)
        self._oauth_worker.status_changed.connect(self._on_status_changed)
        self._oauth_worker.login_success.connect(self._on_login_success)
        self._oauth_worker.login_failed.connect(self._on_login_failed)
        self._oauth_worker.finished.connect(self._on_worker_finished)
        self._oauth_worker.start()
    
    def _on_worker_finished(self):
        """Handle worker thread finished."""
        # 스레드가 정상 종료되면 worker 참조 정리
        if self._oauth_worker:
            self._oauth_worker.deleteLater()
            self._oauth_worker = None
    
    def _on_status_changed(self, status: str):
        """Update status label."""
        self._status_label.setText(status)
        self._status_label.setStyleSheet("color: #6a6a6a; font-size: 13px;")
    
    def _on_login_success(self, user_info: dict):
        """Handle successful login."""
        self._user_info = user_info
        self.login_success.emit(user_info)
        
        # 스레드가 종료될 시간을 주고 다이얼로그 닫기
        # QTimer를 사용해 이벤트 루프가 처리될 수 있도록 함
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(100, self._finish_login)
    
    def _finish_login(self):
        """Finish login after giving thread time to complete."""
        self._cleanup_worker()
        self.accept()
    
    def _cleanup_worker(self):
        """Clean up the OAuth worker thread."""
        if self._oauth_worker is None:
            return
        
        try:
            # 스레드 종료 요청
            self._oauth_worker.stop()
            
            # 스레드가 아직 실행 중이면 대기
            if self._oauth_worker.isRunning():
                self._oauth_worker.wait(2000)  # 최대 2초 대기
        except RuntimeError:
            # 이미 삭제된 경우
            pass
        
        self._oauth_worker = None
    
    def _on_login_failed(self, error: str):
        """Handle login failure."""
        self._progress.setVisible(False)
        self._login_btn.setEnabled(True)
        self._login_btn.setText("  Google로 로그인")
        self._status_label.setText(f"❌ {error}")
        self._status_label.setStyleSheet("color: #F87171; font-size: 13px;")
    
    def closeEvent(self, event):
        """Handle dialog close."""
        self._cleanup_worker()
        
        if self._user_info is None:
            self.login_cancelled.emit()
        
        super().closeEvent(event)
    
    @property
    def user_info(self) -> Optional[Dict[str, Any]]:
        """Get user info after successful login."""
        return self._user_info


def show_login_dialog(parent=None) -> Optional[Dict[str, Any]]:
    """Show login dialog and return user info."""
    dialog = LoginDialog(parent)
    result = dialog.exec()
    
    if result == QDialog.DialogCode.Accepted:
        return dialog.user_info
    return None
