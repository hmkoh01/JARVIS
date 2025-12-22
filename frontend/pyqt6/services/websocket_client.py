"""
JARVIS WebSocket Client
Real-time notification client using websocket-client library.

Phase 3: Implements WebSocket connection for notifications
"""

import json
import time
from typing import Optional, Dict, Any, Callable
from threading import Thread, Event
from dataclasses import dataclass
from enum import Enum

from PyQt6.QtCore import QObject, pyqtSignal

try:
    import websocket
    WEBSOCKET_AVAILABLE = True
except ImportError:
    WEBSOCKET_AVAILABLE = False
    print("Warning: websocket-client not installed. WebSocket notifications disabled.")


class NotificationType(Enum):
    """Types of WebSocket notifications."""
    NEW_RECOMMENDATION = "new_recommendation"
    REPORT_COMPLETED = "report_completed"
    REPORT_FAILED = "report_failed"
    ANALYSIS_COMPLETED = "analysis_completed"
    ANALYSIS_FAILED = "analysis_failed"
    CONNECTION_STATUS = "connection_status"
    UNKNOWN = "unknown"


@dataclass
class Notification:
    """Represents a WebSocket notification."""
    type: NotificationType
    data: Dict[str, Any]
    raw_message: str
    
    @classmethod
    def from_message(cls, message: str) -> "Notification":
        """Parse a WebSocket message into a Notification."""
        try:
            data = json.loads(message)
            msg_type = data.get("type", "unknown")
            
            try:
                notification_type = NotificationType(msg_type)
            except ValueError:
                notification_type = NotificationType.UNKNOWN
            
            return cls(
                type=notification_type,
                data=data.get("data", data),
                raw_message=message
            )
        except json.JSONDecodeError:
            return cls(
                type=NotificationType.UNKNOWN,
                data={"message": message},
                raw_message=message
            )


class NotificationWebSocket(QObject):
    """
    WebSocket client for real-time notifications.
    
    Uses websocket-client library in a background thread.
    Emits PyQt signals for thread-safe UI updates.
    
    Signals:
        connected: Emitted when WebSocket connects successfully
        disconnected: Emitted when WebSocket disconnects
        notification_received: Emitted for each notification
        recommendation_received: Emitted for new_recommendation
        report_completed: Emitted for report_completed
        report_failed: Emitted for report_failed
        analysis_completed: Emitted for analysis_completed
        analysis_failed: Emitted for analysis_failed
        error: Emitted on connection or message errors
    """
    
    # Connection signals
    connected = pyqtSignal()
    disconnected = pyqtSignal()
    error = pyqtSignal(str)
    
    # Generic notification
    notification_received = pyqtSignal(object)  # Notification object
    
    # Specific notification signals
    recommendation_received = pyqtSignal(dict)
    report_completed = pyqtSignal(dict)
    report_failed = pyqtSignal(dict)
    analysis_completed = pyqtSignal(dict)
    analysis_failed = pyqtSignal(dict)
    
    # Configuration
    INITIAL_RECONNECT_DELAY = 5
    MAX_RECONNECT_DELAY = 60
    PING_INTERVAL = 120
    PING_TIMEOUT = 60
    
    def __init__(
        self,
        base_url: str,
        token_provider: Callable[[], Optional[str]] = None,
        parent: Optional[QObject] = None
    ):
        super().__init__(parent)
        
        # Convert HTTP URL to WebSocket URL
        self.base_url = base_url.replace("http://", "ws://").replace("https://", "wss://")
        self.token_provider = token_provider
        
        self._ws: Optional[websocket.WebSocketApp] = None
        self._thread: Optional[Thread] = None
        self._stop_event = Event()
        self._is_connected = False
        self._reconnect_delay = self.INITIAL_RECONNECT_DELAY
    
    @property
    def is_connected(self) -> bool:
        """Check if WebSocket is currently connected."""
        return self._is_connected
    
    def start(self):
        """Start the WebSocket connection in a background thread."""
        if not WEBSOCKET_AVAILABLE:
            self.error.emit("websocket-client library not available")
            return
        
        self._stop_event.clear()
        self._thread = Thread(target=self._connection_loop, daemon=True)
        self._thread.start()
    
    def stop(self):
        """Stop the WebSocket connection and thread."""
        self._stop_event.set()
        
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
        
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        
        self._is_connected = False
        self._ws = None
        self._thread = None
    
    def _connection_loop(self):
        """Main connection loop with auto-reconnect."""
        while not self._stop_event.is_set():
            try:
                # Get token
                token = self._get_token()
                if not token:
                    print("[WebSocket] No token available, waiting...")
                    time.sleep(5)
                    continue
                
                # Build WebSocket URL
                ws_url = f"{self.base_url}/ws/{token}"
                print(f"[WebSocket] Connecting to {ws_url[:50]}...")
                
                # Create WebSocket connection
                self._ws = websocket.WebSocketApp(
                    ws_url,
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close
                )
                
                # Run (blocking) with ping/pong for keep-alive
                self._ws.run_forever(
                    ping_interval=self.PING_INTERVAL,
                    ping_timeout=self.PING_TIMEOUT
                )
                
            except Exception as e:
                print(f"[WebSocket] Connection error: {e}")
                self.error.emit(str(e))
            
            # Connection lost/closed - prepare for reconnect
            self._is_connected = False
            self.disconnected.emit()
            
            if not self._stop_event.is_set():
                print(f"[WebSocket] Reconnecting in {self._reconnect_delay}s...")
                
                # Wait with stop check
                for _ in range(self._reconnect_delay):
                    if self._stop_event.is_set():
                        return
                    time.sleep(1)
                
                # Exponential backoff (capped)
                self._reconnect_delay = min(
                    self._reconnect_delay * 2,
                    self.MAX_RECONNECT_DELAY
                )
    
    def _get_token(self) -> Optional[str]:
        """Get authentication token."""
        if self.token_provider:
            return self.token_provider()
        return None
    
    # =========================================================================
    # WebSocket Callbacks
    # =========================================================================
    
    def _on_open(self, ws):
        """Called when WebSocket connection opens."""
        self._is_connected = True
        self._reconnect_delay = self.INITIAL_RECONNECT_DELAY  # Reset backoff
        print("[WebSocket] ✅ Connected")
        self.connected.emit()
    
    def _on_message(self, ws, message: str):
        """Called when a message is received."""
        try:
            notification = Notification.from_message(message)
            print(f"[WebSocket] Message received: type={notification.type.value}")
            
            # Emit generic notification
            self.notification_received.emit(notification)
            
            # Emit specific signals based on type
            if notification.type == NotificationType.NEW_RECOMMENDATION:
                self.recommendation_received.emit(notification.data)
                
            elif notification.type == NotificationType.REPORT_COMPLETED:
                self.report_completed.emit(notification.data)
                
            elif notification.type == NotificationType.REPORT_FAILED:
                self.report_failed.emit(notification.data)
                
            elif notification.type == NotificationType.ANALYSIS_COMPLETED:
                self.analysis_completed.emit(notification.data)
                
            elif notification.type == NotificationType.ANALYSIS_FAILED:
                self.analysis_failed.emit(notification.data)
                
        except Exception as e:
            print(f"[WebSocket] Error processing message: {e}")
            self.error.emit(f"Message processing error: {e}")
    
    def _on_error(self, ws, error):
        """Called when a WebSocket error occurs."""
        print(f"[WebSocket] Error: {error}")
        self.error.emit(str(error))
    
    def _on_close(self, ws, close_status_code, close_msg):
        """Called when WebSocket connection closes."""
        self._is_connected = False
        print(f"[WebSocket] Closed: {close_status_code} - {close_msg}")
        # disconnected signal emitted in _connection_loop


class WebSocketManager(QObject):
    """
    Manages WebSocket lifecycle and provides a high-level interface.
    
    Handles connection state and provides convenience methods.
    """
    
    # Forward signals from NotificationWebSocket
    connected = pyqtSignal()
    disconnected = pyqtSignal()
    notification = pyqtSignal(object)
    error = pyqtSignal(str)
    
    def __init__(
        self,
        base_url: str,
        parent: Optional[QObject] = None
    ):
        super().__init__(parent)
        
        self.base_url = base_url
        self._token: Optional[str] = None
        self._ws_client: Optional[NotificationWebSocket] = None
    
    def set_token(self, token: str):
        """Set the authentication token."""
        self._token = token
    
    def clear_token(self):
        """Clear the authentication token."""
        self._token = None
    
    def _get_token(self) -> Optional[str]:
        """Token provider for WebSocket client."""
        return self._token
    
    def connect(self):
        """Start WebSocket connection."""
        if self._ws_client:
            self.disconnect()
        
        self._ws_client = NotificationWebSocket(
            base_url=self.base_url,
            token_provider=self._get_token,
            parent=self
        )
        
        # Forward signals
        self._ws_client.connected.connect(self.connected.emit)
        self._ws_client.disconnected.connect(self.disconnected.emit)
        self._ws_client.notification_received.connect(self.notification.emit)
        self._ws_client.error.connect(self.error.emit)
        
        self._ws_client.start()
    
    def disconnect(self):
        """Stop WebSocket connection."""
        if self._ws_client:
            self._ws_client.stop()
            self._ws_client = None
    
    @property
    def is_connected(self) -> bool:
        """Check if WebSocket is connected."""
        return self._ws_client.is_connected if self._ws_client else False
    
    @property
    def client(self) -> Optional[NotificationWebSocket]:
        """Get the underlying WebSocket client."""
        return self._ws_client
