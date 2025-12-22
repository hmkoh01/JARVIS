"""
JARVIS API Client
HTTP client for backend API communication with streaming support.

Phase 3: Implements streaming chat API with background threading
"""

import json
from typing import Optional, Dict, Any
from threading import Thread, Event

import requests

from PyQt6.QtCore import QObject, pyqtSignal


class StreamingWorker(QObject):
    """
    Worker for handling HTTP streaming in a background thread.
    
    Signals:
        stream_started: Emitted when streaming begins
        chunk_received: Emitted for each chunk of data
        completed: Emitted when streaming completes successfully
        error: Emitted when an error occurs
    """
    
    stream_started = pyqtSignal()
    chunk_received = pyqtSignal(str)
    completed = pyqtSignal()
    error = pyqtSignal(str)
    
    def __init__(
        self,
        url: str,
        payload: Dict[str, Any],
        headers: Optional[Dict[str, str]] = None,
        timeout: int = 300,
        parent: Optional[QObject] = None
    ):
        super().__init__(parent)
        
        self.url = url
        self.payload = payload
        self.headers = headers or {}
        self.timeout = timeout
        
        self._stop_event = Event()
        self._thread: Optional[Thread] = None
    
    def start(self):
        """Start streaming in a background thread."""
        self._stop_event.clear()
        self._thread = Thread(target=self._run, daemon=True)
        self._thread.start()
    
    def stop(self):
        """Stop the streaming operation."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
    
    def _run(self):
        """Execute the streaming request."""
        max_retries = 3
        retry_delay = 2
        
        for attempt in range(max_retries):
            if self._stop_event.is_set():
                return
            
            try:
                response = requests.post(
                    self.url,
                    json=self.payload,
                    headers={
                        "Accept": "text/event-stream",
                        **self.headers
                    },
                    timeout=self.timeout,
                    stream=True
                )
                
                if response.status_code == 200:
                    self.stream_started.emit()
                    
                    # Process streaming response
                    try:
                        for chunk in response.iter_content(
                            chunk_size=64, 
                            decode_unicode=True
                        ):
                            if self._stop_event.is_set():
                                response.close()
                                return
                            
                            if chunk:
                                self.chunk_received.emit(chunk)
                        
                        self.completed.emit()
                        
                    except Exception as e:
                        if not self._stop_event.is_set():
                            self.error.emit(f"스트리밍 처리 중 오류: {str(e)}")
                    
                    return
                    
                elif response.status_code == 401:
                    self.error.emit("인증이 필요합니다. 다시 로그인해주세요.")
                    return
                    
                elif response.status_code == 429:
                    self.error.emit("요청이 너무 많습니다. 잠시 후 다시 시도해주세요.")
                    return
                    
                else:
                    error_msg = f"Error: {response.status_code}"
                    try:
                        error_data = response.json()
                        if "detail" in error_data:
                            error_msg = error_data["detail"]
                    except Exception:
                        pass
                    self.error.emit(error_msg)
                    return
                    
            except requests.exceptions.Timeout:
                if attempt < max_retries - 1:
                    import time
                    time.sleep(retry_delay)
                    continue
                else:
                    self.error.emit("서버 응답 시간이 초과되었습니다.")
                    return
                    
            except requests.exceptions.ConnectionError:
                if attempt < max_retries - 1:
                    import time
                    time.sleep(retry_delay)
                    continue
                else:
                    self.error.emit("서버에 연결할 수 없습니다.")
                    return
                    
            except Exception as e:
                if attempt < max_retries - 1:
                    import time
                    time.sleep(retry_delay)
                    continue
                else:
                    self.error.emit(f"오류가 발생했습니다: {str(e)}")
                    return


class APIClient(QObject):
    """
    HTTP client for backend API calls.
    
    Handles authentication, streaming, and general API requests.
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
        self._active_workers: list = []
    
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
    # Streaming Chat API
    # =========================================================================
    
    def send_message_streaming(
        self,
        message: str,
        on_started: callable = None,
        on_chunk: callable = None,
        on_completed: callable = None,
        on_error: callable = None
    ) -> StreamingWorker:
        """
        Send a chat message and receive streaming response.
        
        Args:
            message: The message to send
            on_started: Callback when streaming starts
            on_chunk: Callback for each chunk received
            on_completed: Callback when streaming completes
            on_error: Callback for errors
            
        Returns:
            StreamingWorker instance for controlling the stream
        """
        url = f"{self.base_url}/api/v2/message"
        
        payload = {
            "message": message,
            "user_id": self._user_id or 1,
            "stream": True
        }
        
        worker = StreamingWorker(
            url=url,
            payload=payload,
            headers=self._get_headers(),
            parent=self
        )
        
        # Connect signals
        if on_started:
            worker.stream_started.connect(on_started)
        if on_chunk:
            worker.chunk_received.connect(on_chunk)
        if on_completed:
            worker.completed.connect(on_completed)
        if on_error:
            worker.error.connect(on_error)
        
        # Track active workers
        self._active_workers.append(worker)
        worker.completed.connect(lambda: self._remove_worker(worker))
        worker.error.connect(lambda _: self._remove_worker(worker))
        
        worker.start()
        return worker
    
    def _remove_worker(self, worker: StreamingWorker):
        """Remove a worker from the active list."""
        if worker in self._active_workers:
            self._active_workers.remove(worker)
    
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
        """Stop all active streaming workers."""
        for worker in self._active_workers[:]:
            worker.stop()
        self._active_workers.clear()
