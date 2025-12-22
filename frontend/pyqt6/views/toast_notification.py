"""
JARVIS Toast Notification System
Non-intrusive notification popups in the bottom-right corner.

Phase 4: Toast notifications for WebSocket events and general alerts
"""

from typing import Optional, List, Callable, Dict, Any
from enum import Enum
from dataclasses import dataclass, field

from PyQt6.QtWidgets import (
    QWidget,
    QLabel,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QGraphicsOpacityEffect,
    QApplication,
    QSizePolicy
)
from PyQt6.QtCore import (
    Qt,
    QTimer,
    QPropertyAnimation,
    QEasingCurve,
    pyqtSignal,
    QPoint,
    QSize
)
from PyQt6.QtGui import QFont, QColor


@dataclass
class ToastAction:
    """Action button configuration for toast."""
    text: str
    callback: Callable[[], None]
    primary: bool = False  # Primary button style


class ToastType(Enum):
    """Types of toast notifications."""
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


@dataclass
class ToastConfig:
    """Configuration for toast appearance."""
    icon: str
    bg_color: str
    border_color: str
    text_color: str


# Toast type configurations - Modern Monochrome
TOAST_CONFIGS = {
    ToastType.INFO: ToastConfig(
        icon="ℹ️",
        bg_color="#1a1a1a",
        border_color="#3a3a3a",
        text_color="#e8e8e8"
    ),
    ToastType.SUCCESS: ToastConfig(
        icon="✅",
        bg_color="#1a2a1a",
        border_color="#2a3a2a",
        text_color="#a0c0a0"
    ),
    ToastType.WARNING: ToastConfig(
        icon="⚠️",
        bg_color="#2a2a1a",
        border_color="#3a3a2a",
        text_color="#c0c0a0"
    ),
    ToastType.ERROR: ToastConfig(
        icon="❌",
        bg_color="#2a1a1a",
        border_color="#3a2a2a",
        text_color="#c0a0a0"
    ),
}


class ToastNotification(QWidget):
    """
    A single toast notification widget.
    
    Features:
    - Auto-dismiss after duration
    - Click to dismiss
    - Fade in/out animations
    - Typing animation for messages
    - Slide-in effect
    - Type-based styling (info/success/warning/error)
    - Optional action buttons
    """
    
    closed = pyqtSignal(object)  # Emitted when toast is closed
    action_clicked = pyqtSignal(str)  # Emitted when an action button is clicked
    
    # Dimensions
    MAX_WIDTH = 400
    MIN_WIDTH = 300
    
    # Animation settings
    TYPING_SPEED_MS = 12  # ms per tick (faster typing)
    TYPING_CHUNK_SIZE = 3  # Characters per tick
    SLIDE_DURATION_MS = 300
    
    def __init__(
        self,
        title: str,
        message: str = "",
        toast_type: ToastType = ToastType.INFO,
        duration_ms: int = 5000,
        actions: Optional[List[ToastAction]] = None,
        typing_animation: bool = True,
        parent: Optional[QWidget] = None
    ):
        super().__init__(parent)
        
        self.title = title
        self.message = message
        self.toast_type = toast_type
        self.duration_ms = duration_ms
        self.actions = actions or []
        self.typing_animation = typing_animation
        
        self._config = TOAST_CONFIGS[toast_type]
        self._dismiss_timer: Optional[QTimer] = None
        self._typing_timer: Optional[QTimer] = None
        self._typing_index = 0
        self._msg_label: Optional[QLabel] = None
        self._full_message = message
        
        self._setup_window()
        self._setup_ui()
        # Note: dismiss timer is started after animation completes
    
    def _setup_window(self):
        """Configure window properties."""
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool |
            Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        
        # Size constraints
        self.setMinimumWidth(self.MIN_WIDTH)
        self.setMaximumWidth(self.MAX_WIDTH)
    
    def _setup_ui(self):
        """Set up the toast UI."""
        # Main container with styling
        self.setStyleSheet(f"""
            QWidget#toastContainer {{
                background-color: {self._config.bg_color};
                border: 2px solid {self._config.border_color};
                border-radius: 8px;
            }}
            QLabel {{
                color: {self._config.text_color};
            }}
            QPushButton {{
                background: transparent;
                border: none;
                color: {self._config.text_color};
                padding: 2px;
            }}
            QPushButton:hover {{
                background-color: rgba(255, 255, 255, 0.1);
                border-radius: 4px;
            }}
        """)
        
        # Layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # Container widget
        container = QWidget()
        container.setObjectName("toastContainer")
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(12, 10, 12, 10)
        container_layout.setSpacing(4)
        
        # Header row (icon + title + close button)
        header = QHBoxLayout()
        header.setSpacing(8)
        
        # Icon
        icon_label = QLabel(self._config.icon)
        icon_font = QFont()
        icon_font.setPointSize(14)
        icon_label.setFont(icon_font)
        header.addWidget(icon_label)
        
        # Title
        title_label = QLabel(self.title)
        title_font = QFont()
        title_font.setPointSize(11)
        title_font.setBold(True)
        title_label.setFont(title_font)
        title_label.setWordWrap(True)
        header.addWidget(title_label, 1)
        
        # Close button
        close_btn = QPushButton("×")
        close_font = QFont()
        close_font.setPointSize(14)
        close_btn.setFont(close_font)
        close_btn.setFixedSize(20, 20)
        close_btn.clicked.connect(self.dismiss)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        header.addWidget(close_btn)
        
        container_layout.addLayout(header)
        
        # Message (if provided)
        if self.message:
            self._msg_label = QLabel(self._full_message)  # Set full text first for size calculation
            msg_font = QFont()
            msg_font.setPointSize(10)
            self._msg_label.setFont(msg_font)
            self._msg_label.setWordWrap(True)
            self._msg_label.setStyleSheet(f"color: {self._config.text_color}; opacity: 0.9;")
            self._msg_label.setSizePolicy(
                QSizePolicy.Policy.Preferred,
                QSizePolicy.Policy.Minimum
            )
            container_layout.addWidget(self._msg_label)
            
            # If typing animation is enabled, we'll clear and retype
            # The size is already calculated with full text
        
        # Action buttons (if provided)
        if self.actions:
            btn_layout = QHBoxLayout()
            btn_layout.setSpacing(8)
            btn_layout.addStretch()
            
            for action in self.actions:
                btn = QPushButton(action.text)
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                
                if action.primary:
                    btn.setStyleSheet(f"""
                        QPushButton {{
                            background-color: #10B981;
                            color: white;
                            border: none;
                            border-radius: 4px;
                            padding: 6px 12px;
                            font-weight: 600;
                            font-size: 11px;
                        }}
                        QPushButton:hover {{
                            background-color: #059669;
                        }}
                    """)
                else:
                    btn.setStyleSheet(f"""
                        QPushButton {{
                            background-color: rgba(255, 255, 255, 0.2);
                            color: {self._config.text_color};
                            border: none;
                            border-radius: 4px;
                            padding: 6px 12px;
                            font-size: 11px;
                        }}
                        QPushButton:hover {{
                            background-color: rgba(255, 255, 255, 0.3);
                        }}
                    """)
                
                # 콜백 연결 (클로저 문제 방지)
                callback = action.callback
                btn.clicked.connect(lambda checked, cb=callback: self._on_action_clicked(cb))
                btn_layout.addWidget(btn)
            
            container_layout.addLayout(btn_layout)
        
        layout.addWidget(container)
        
        # Adjust size
        self.adjustSize()
    
    def _on_action_clicked(self, callback: Callable[[], None]):
        """Handle action button click."""
        try:
            callback()
        except Exception as e:
            print(f"Toast action error: {e}")
        self.dismiss()
    
    def _start_dismiss_timer(self):
        """Start the auto-dismiss timer."""
        if self.duration_ms > 0:
            self._dismiss_timer = QTimer(self)
            self._dismiss_timer.setSingleShot(True)
            self._dismiss_timer.timeout.connect(self.dismiss)
            self._dismiss_timer.start(self.duration_ms)
    
    def mousePressEvent(self, event):
        """Dismiss on click."""
        self.dismiss()
    
    def dismiss(self):
        """Close the toast with fade-out animation."""
        if self._dismiss_timer:
            self._dismiss_timer.stop()
        
        if self._typing_timer:
            self._typing_timer.stop()
        
        # Fade out animation
        if hasattr(self, '_opacity_effect') and self._opacity_effect:
            self._fade_out = QPropertyAnimation(self._opacity_effect, b"opacity")
            self._fade_out.setDuration(150)
            self._fade_out.setStartValue(1)
            self._fade_out.setEndValue(0)
            self._fade_out.setEasingCurve(QEasingCurve.Type.InCubic)
            self._fade_out.finished.connect(self._on_fade_out_complete)
            self._fade_out.start()
        else:
            self._on_fade_out_complete()
    
    def _on_fade_out_complete(self):
        """Called when fade-out animation is complete."""
        # Emit closed signal
        self.closed.emit(self)
        
        # Close
        self.close()
        self.deleteLater()
    
    def show_animated(self):
        """Show with slide-in and fade-in animation, then start typing animation."""
        # Store original position for slide animation
        original_pos = self.pos()
        
        # Set up opacity effect
        self._opacity_effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity_effect)
        self._opacity_effect.setOpacity(0)
        
        # Start position (slide from right)
        start_x = original_pos.x() + 50
        self.move(start_x, original_pos.y())
        
        # Show first
        self.show()
        
        # Animate opacity (fade in)
        self._fade_animation = QPropertyAnimation(self._opacity_effect, b"opacity")
        self._fade_animation.setDuration(self.SLIDE_DURATION_MS)
        self._fade_animation.setStartValue(0)
        self._fade_animation.setEndValue(1)
        self._fade_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        
        # Animate position (slide in from right)
        self._slide_animation = QPropertyAnimation(self, b"pos")
        self._slide_animation.setDuration(self.SLIDE_DURATION_MS)
        self._slide_animation.setStartValue(QPoint(start_x, original_pos.y()))
        self._slide_animation.setEndValue(original_pos)
        self._slide_animation.setEasingCurve(QEasingCurve.Type.OutBack)
        
        # Start animations
        self._fade_animation.start()
        self._slide_animation.start()
        
        # Start typing animation after slide completes
        if self.typing_animation and self._msg_label and self._full_message:
            QTimer.singleShot(self.SLIDE_DURATION_MS, self._start_typing_animation)
        elif self._msg_label and self._full_message:
            # No typing animation - show full message immediately
            self._msg_label.setText(self._full_message)
            self._start_dismiss_timer()
        else:
            self._start_dismiss_timer()
    
    def _start_typing_animation(self):
        """Start the character-by-character typing animation."""
        self._typing_index = 0
        # Clear label before starting (size is already calculated with full text)
        self._msg_label.setText("▌")
        self._typing_timer = QTimer(self)
        self._typing_timer.timeout.connect(self._type_next_char)
        self._typing_timer.start(self.TYPING_SPEED_MS)
    
    def _type_next_char(self):
        """Add the next chunk of characters to the message."""
        if self._typing_index < len(self._full_message):
            # Add multiple characters per tick for faster typing
            self._typing_index = min(
                self._typing_index + self.TYPING_CHUNK_SIZE, 
                len(self._full_message)
            )
            displayed_text = self._full_message[:self._typing_index]
            # Add cursor effect
            if self._typing_index < len(self._full_message):
                displayed_text += "▌"
            self._msg_label.setText(displayed_text)
        else:
            # Typing complete
            if self._typing_timer:
                self._typing_timer.stop()
                self._typing_timer = None
            self._msg_label.setText(self._full_message)
            # Start dismiss timer after typing completes
            self._start_dismiss_timer()


class ToastManager(QWidget):
    """
    Manages a stack of toast notifications.
    
    Features:
    - Stacks toasts vertically from bottom-right
    - Repositions when toasts are dismissed
    - Limits maximum number of visible toasts
    """
    
    # Configuration
    # 플로팅 버튼: 120x120px, 화면 우하단에서 30px 여백
    # 버튼 영역: x=right-150 ~ right-30, y=bottom-150 ~ bottom-30
    MARGIN_RIGHT = 160   # 버튼 왼쪽 끝(right-150)보다 더 왼쪽에 표시
    MARGIN_BOTTOM = 170  # 버튼 위쪽 끝(bottom-150)보다 더 위에 표시
    SPACING = 10
    MAX_TOASTS = 5
    
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        
        self._toasts: List[ToastNotification] = []
        
        # Hide this widget (it's just a manager)
        self.hide()
    
    def show_toast(
        self,
        title: str,
        message: str = "",
        toast_type: ToastType = ToastType.INFO,
        duration_ms: int = 5000,
        actions: Optional[List[ToastAction]] = None,
        typing_animation: bool = True
    ) -> ToastNotification:
        """
        Show a new toast notification.
        
        Args:
            title: Toast title
            message: Optional message body
            toast_type: Type of toast (info/success/warning/error)
            duration_ms: Auto-dismiss duration (0 = no auto-dismiss)
            actions: Optional list of action buttons
            typing_animation: Enable typing animation for message
            
        Returns:
            The created ToastNotification widget
        """
        # Remove oldest if at max
        while len(self._toasts) >= self.MAX_TOASTS:
            oldest = self._toasts[0]
            oldest.dismiss()
        
        # 액션 버튼이 있으면 자동 닫기 시간 늘리기
        if actions and duration_ms < 10000:
            duration_ms = 15000  # 액션 버튼 있을 때는 15초
        
        # Create toast
        toast = ToastNotification(
            title=title,
            message=message,
            toast_type=toast_type,
            duration_ms=duration_ms,
            actions=actions,
            typing_animation=typing_animation
        )
        
        # Connect closed signal
        toast.closed.connect(self._on_toast_closed)
        
        # Add to list
        self._toasts.append(toast)
        
        # Position and show
        self._position_toasts()
        toast.show_animated()
        
        return toast
    
    def _on_toast_closed(self, toast: ToastNotification):
        """Handle toast being closed."""
        if toast in self._toasts:
            self._toasts.remove(toast)
            self._position_toasts()
    
    def _position_toasts(self):
        """Position all toasts from bottom-right."""
        screen = QApplication.primaryScreen()
        if not screen:
            return
        
        screen_geometry = screen.availableGeometry()
        
        # Start from bottom
        y = screen_geometry.bottom() - self.MARGIN_BOTTOM
        
        for toast in reversed(self._toasts):
            toast_height = toast.sizeHint().height()
            toast_width = toast.sizeHint().width()
            
            # Position
            x = screen_geometry.right() - toast_width - self.MARGIN_RIGHT
            y -= toast_height
            
            toast.move(x, y)
            
            # Add spacing for next toast
            y -= self.SPACING
    
    def clear_all(self):
        """Dismiss all toasts."""
        for toast in self._toasts[:]:
            toast.dismiss()
    
    # =========================================================================
    # Convenience Methods
    # =========================================================================
    
    def info(self, title: str, message: str = "", duration_ms: int = 5000, 
             actions: Optional[List[ToastAction]] = None):
        """Show an info toast."""
        return self.show_toast(title, message, ToastType.INFO, duration_ms, actions)
    
    def success(self, title: str, message: str = "", duration_ms: int = 5000,
                actions: Optional[List[ToastAction]] = None):
        """Show a success toast."""
        return self.show_toast(title, message, ToastType.SUCCESS, duration_ms, actions)
    
    def warning(self, title: str, message: str = "", duration_ms: int = 5000,
                actions: Optional[List[ToastAction]] = None):
        """Show a warning toast."""
        return self.show_toast(title, message, ToastType.WARNING, duration_ms, actions)
    
    def error(self, title: str, message: str = "", duration_ms: int = 8000,
              actions: Optional[List[ToastAction]] = None):
        """Show an error toast (longer duration by default)."""
        return self.show_toast(title, message, ToastType.ERROR, duration_ms, actions)
    
    def success_with_folder_action(
        self, 
        title: str, 
        message: str, 
        folder_path: str,
        duration_ms: int = 15000
    ):
        """
        Show a success toast with an action to open a folder.
        
        Args:
            title: Toast title
            message: Toast message
            folder_path: Path to the folder to open
            duration_ms: Auto-dismiss duration
        """
        import os
        import subprocess
        import sys
        
        def open_folder():
            try:
                if sys.platform == 'win32':
                    os.startfile(folder_path)
                elif sys.platform == 'darwin':
                    subprocess.run(['open', folder_path])
                else:
                    subprocess.run(['xdg-open', folder_path])
            except Exception as e:
                print(f"Failed to open folder: {e}")
        
        actions = [
            ToastAction("📂 폴더 열기", open_folder, primary=True)
        ]
        return self.show_toast(title, message, ToastType.SUCCESS, duration_ms, actions)
    
    def success_with_dashboard_action(
        self,
        title: str,
        message: str,
        dashboard_callback: Callable[[], None],
        duration_ms: int = 15000
    ):
        """
        Show a success toast with an action to open dashboard.
        
        Args:
            title: Toast title
            message: Toast message
            dashboard_callback: Callback to open/switch to dashboard
            duration_ms: Auto-dismiss duration
        """
        actions = [
            ToastAction("📊 대시보드 열기", dashboard_callback, primary=True)
        ]
        return self.show_toast(title, message, ToastType.SUCCESS, duration_ms, actions)


# Global toast manager instance
_toast_manager: Optional[ToastManager] = None


def get_toast_manager() -> ToastManager:
    """Get or create the global toast manager."""
    global _toast_manager
    if _toast_manager is None:
        _toast_manager = ToastManager()
    return _toast_manager


def show_toast(
    title: str,
    message: str = "",
    toast_type: ToastType = ToastType.INFO,
    duration_ms: int = 5000,
    actions: Optional[List[ToastAction]] = None
) -> ToastNotification:
    """Convenience function to show a toast using the global manager."""
    return get_toast_manager().show_toast(title, message, toast_type, duration_ms, actions)


# Export all public classes and functions
__all__ = [
    'ToastType',
    'ToastAction',
    'ToastConfig',
    'ToastNotification',
    'ToastManager',
    'get_toast_manager',
    'show_toast',
]

