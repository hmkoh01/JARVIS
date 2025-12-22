"""
JARVIS API Client
HTTP client for backend API communication.

Provides non-streaming API calls with background threading.
"""

import json
from typing import Optional, Dict, Any
from threading import Thread

import requests

from PyQt6.QtCore import QObject, pyqtSignal


class APIClient(QObject):
    """
    HTTP client for backend API calls.
    
    Handles authentication and general API requests.
    All network calls run in background threads to avoid blocking UI.
    """
    
    # Signals for async responses
    request_started = pyqtSignal()
    request_completed = pyqtSignal(dict)
    request_error = pyqtSignal(str)
    
    def __init__(self, base_url: str, parent: Optional[QObject] = None):
        super().__init__(parent)
        
        self.base_url = base_url.rstrip("/")
        self._token: Optional[str] = None
        self._user_id: Optional[int] = None
    
    @property
    def token(self) -> Optional[str]:
        """Get current authentication token."""
        return self._token
    
    @property
    def user_id(self) -> Optional[int]:
        """Get current user ID."""
        return self._user_id
    
    def set_auth(self, token: str, user_id: int) -> None:
        """Set authentication credentials."""
        self._token = token
        self._user_id = user_id
    
    def clear_auth(self) -> None:
        """Clear authentication credentials."""
        self._token = None
        self._user_id = None
    
    def _get_headers(self) -> Dict[str, str]:
        """Get headers with authentication if available."""
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers
    
    # =========================================================================
    # Chat API
    # =========================================================================
    
    def send_message(
        self,
        message: str,
        on_completed: callable = None,
        on_error: callable = None
    ) -> Thread:
        """
        Send a chat message and receive non-streaming response.
        
        Args:
            message: The message to send
            on_completed: Callback when response is received (receives dict with content, metadata)
            on_error: Callback for errors (receives error string)
            
        Returns:
            Thread running the request
        """
        url = f"{self.base_url}/api/v2/message"
        
        payload = {
            "message": message,
            "user_id": self._user_id or 1,
            "stream": False  # Non-streaming
        }
        
        def _run():
            max_retries = 3
            retry_delay = 2
            
            for attempt in range(max_retries):
                try:
                    response = requests.post(
                        url,
                        json=payload,
                        headers=self._get_headers(),
                        timeout=120  # 긴 타임아웃 (LLM 응답 대기)
                    )
                    
                    if response.status_code == 200:
                        data = response.json()
                        if on_completed:
                            on_completed(data)
                        return
                        
                    elif response.status_code == 401:
                        if on_error:
                            on_error("인증이 필요합니다. 다시 로그인해주세요.")
                        return
                        
                    elif response.status_code == 429:
                        if on_error:
                            on_error("요청이 너무 많습니다. 잠시 후 다시 시도해주세요.")
                        return
                        
                    else:
                        error_msg = f"Error: {response.status_code}"
                        try:
                            error_data = response.json()
                            if "detail" in error_data:
                                error_msg = error_data["detail"]
                        except Exception:
                            pass
                        if on_error:
                            on_error(error_msg)
                        return
                        
                except requests.exceptions.Timeout:
                    if attempt < max_retries - 1:
                        import time
                        time.sleep(retry_delay)
                        continue
                    else:
                        if on_error:
                            on_error("서버 응답 시간이 초과되었습니다.")
                        return
                        
                except requests.exceptions.ConnectionError:
                    if attempt < max_retries - 1:
                        import time
                        time.sleep(retry_delay)
                        continue
                    else:
                        if on_error:
                            on_error("서버에 연결할 수 없습니다.")
                        return
                        
                except Exception as e:
                    if attempt < max_retries - 1:
                        import time
                        time.sleep(retry_delay)
                        continue
                    else:
                        if on_error:
                            on_error(f"오류가 발생했습니다: {str(e)}")
                        return
        
        self.request_started.emit()
        thread = Thread(target=_run, daemon=True)
        thread.start()
        return thread
    
    # =========================================================================
    # Non-Streaming API Methods
    # =========================================================================
    
    def get(self, endpoint: str, params: Dict = None) -> Thread:
        """
        Make a GET request in background.
        
        Returns:
            Thread running the request
        """
        def _run():
            try:
                response = requests.get(
                    f"{self.base_url}{endpoint}",
                    params=params,
                    headers=self._get_headers(),
                    timeout=30
                )
                
                if response.status_code == 200:
                    self.request_completed.emit(response.json())
                else:
                    self.request_error.emit(f"Error: {response.status_code}")
                    
            except Exception as e:
                self.request_error.emit(str(e))
        
        self.request_started.emit()
        thread = Thread(target=_run, daemon=True)
        thread.start()
        return thread
    
    def post(self, endpoint: str, data: Dict = None) -> Thread:
        """
        Make a POST request in background.
        
        Returns:
            Thread running the request
        """
        def _run():
            try:
                response = requests.post(
                    f"{self.base_url}{endpoint}",
                    json=data,
                    headers=self._get_headers(),
                    timeout=30
                )
                
                if response.status_code in (200, 201):
                    self.request_completed.emit(response.json())
                else:
                    self.request_error.emit(f"Error: {response.status_code}")
                    
            except Exception as e:
                self.request_error.emit(str(e))
        
        self.request_started.emit()
        thread = Thread(target=_run, daemon=True)
        thread.start()
        return thread
    
    # =========================================================================
    # Cleanup
    # =========================================================================
    
    def stop_all(self):
        """Cleanup method (no-op for non-streaming client)."""
        pass
