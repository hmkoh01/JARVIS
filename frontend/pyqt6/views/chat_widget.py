"""
JARVIS Chat Widget
Widget for displaying chat messages and input with streaming support.

Phase 3: Full chat implementation with message bubbles and streaming
"""

import re
from typing import Optional, List
from datetime import datetime

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QTextEdit,
    QPushButton,
    QScrollArea,
    QFrame,
    QSizePolicy,
    QSpacerItem,
    QApplication
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QSize, QPropertyAnimation, QEasingCurve
from PyQt6.QtWidgets import QGraphicsOpacityEffect
from PyQt6.QtGui import QFont, QKeyEvent, QTextCursor, QColor

from models.message import Message


class MessageBubble(QFrame):
    """
    A chat message bubble widget.
    
    Displays user or assistant messages with appropriate styling.
    Supports streaming content updates for assistant messages.
    Supports typing animation for non-streaming messages.
    """
    
    # Bubble colors - Modern Light Theme (all black text)
    USER_BG_COLOR = "#1a1a1a"
    USER_TEXT_COLOR = "#ffffff"
    ASSISTANT_BG_COLOR = "#f0f0f0"
    ASSISTANT_TEXT_COLOR = "#1a1a1a"
    ASSISTANT_BG_DARK = "#f0f0f0"
    ASSISTANT_TEXT_DARK = "#1a1a1a"
    
    # Typing animation settings
    TYPING_SPEED_MS = 15  # ms per character (faster than toast for chat flow)
    TYPING_CHUNK_SIZE = 3  # Characters per tick (for faster typing)
    
    # Signal emitted when typing animation completes
    typing_completed = pyqtSignal()
    
    def __init__(self, message: Message, parent: Optional[QWidget] = None):
        super().__init__(parent)
        
        self.message = message
        self._typing_timer: Optional[QTimer] = None
        self._typing_index = 0
        self._full_content = ""
        self._is_typing = False
        
        self._setup_ui()
    
    def _setup_ui(self):
        """Set up the bubble UI."""
        self.setObjectName("messageBubble")
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(0)
        
        # Create bubble content
        bubble = QFrame()
        bubble.setObjectName("bubbleContent")
        bubble.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Minimum)
        bubble.setMaximumWidth(500)
        
        bubble_layout = QVBoxLayout(bubble)
        bubble_layout.setContentsMargins(12, 8, 12, 8)
        bubble_layout.setSpacing(4)
        
        # Message content
        self._content_label = QLabel()
        self._content_label.setWordWrap(True)
        self._content_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse |
            Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        self._content_label.setOpenExternalLinks(True)
        self._update_content()
        bubble_layout.addWidget(self._content_label)
        
        # Timestamp (optional)
        if self.message.timestamp:
            time_label = QLabel(self.message.timestamp.strftime("%H:%M"))
            time_label.setObjectName("timestamp")
            time_label.setStyleSheet("font-size: 10px; opacity: 0.7;")
            bubble_layout.addWidget(time_label, alignment=Qt.AlignmentFlag.AlignRight)
        
        # Apply styling based on role
        self._apply_bubble_style(bubble)
        
        # Position based on role
        if self.message.is_user:
            layout.addStretch()
            layout.addWidget(bubble)
        else:
            layout.addWidget(bubble)
            layout.addStretch()
    
    def _apply_bubble_style(self, bubble: QFrame):
        """Apply appropriate styling based on message role."""
        if self.message.is_user:
            bubble.setStyleSheet(f"""
                #bubbleContent {{
                    background-color: {self.USER_BG_COLOR};
                    border-radius: 12px;
                    border-bottom-right-radius: 4px;
                }}
                #bubbleContent QLabel {{
                    color: {self.USER_TEXT_COLOR};
                }}
            """)
        else:
            # Use dark mode colors if parent has dark theme
            # (This will be overridden by QSS in practice)
            bubble.setStyleSheet(f"""
                #bubbleContent {{
                    background-color: palette(alternate-base);
                    border-radius: 12px;
                    border-bottom-left-radius: 4px;
                }}
            """)
    
    def _update_content(self):
        """Update the displayed content."""
        content = self.message.content
        
        if not content:
            content = ""
        
        # Apply basic markdown formatting
        formatted = self._format_content(content)
        self._content_label.setText(formatted)
    
    def _format_content(self, content: str) -> str:
        """Apply basic markdown-like formatting."""
        if not content:
            return content
        
        # Escape HTML
        content = content.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        
        # Code blocks (```...```)
        def replace_code_block(match):
            code = match.group(1)
            return f'<pre style="background-color: #1e1e2e; color: #e4e4e7; padding: 8px; border-radius: 4px; font-family: Consolas, monospace;">{code}</pre>'
        
        content = re.sub(r'```(?:\w+)?\n?(.*?)```', replace_code_block, content, flags=re.DOTALL)
        
        # Inline code (`...`)
        content = re.sub(
            r'`([^`]+)`',
            r'<code style="background-color: #374151; color: #e5e7eb; padding: 2px 4px; border-radius: 3px; font-family: Consolas, monospace;">\1</code>',
            content
        )
        
        # Bold (**...**)
        content = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', content)
        
        # Italic (*...*)
        content = re.sub(r'\*(.+?)\*', r'<i>\1</i>', content)
        
        # Line breaks
        content = content.replace('\n', '<br>')
        
        return content
    
    def start_typing_animation(self, content: str):
        """
        Start typing animation for the message content.
        
        Args:
            content: The full content to animate
        """
        self._full_content = content
        self._typing_index = 0
        self._is_typing = True
        self.message.content = ""
        self._content_label.setText("▌")  # Show cursor
        
        self._typing_timer = QTimer(self)
        self._typing_timer.timeout.connect(self._type_next_chunk)
        self._typing_timer.start(self.TYPING_SPEED_MS)
    
    def _type_next_chunk(self):
        """Add the next chunk of characters to the message."""
        if self._typing_index < len(self._full_content):
            # Add chunk of characters
            end_idx = min(self._typing_index + self.TYPING_CHUNK_SIZE, len(self._full_content))
            self._typing_index = end_idx
            
            # Update message content
            current_text = self._full_content[:self._typing_index]
            self.message.content = current_text
            
            # Format and display with cursor
            formatted = self._format_content(current_text)
            if self._typing_index < len(self._full_content):
                formatted += '<span style="color: #888;">▌</span>'
            self._content_label.setText(formatted)
        else:
            # Typing complete
            self._complete_typing()
    
    def _complete_typing(self):
        """Complete the typing animation."""
        if self._typing_timer:
            self._typing_timer.stop()
            self._typing_timer = None
        
        self._is_typing = False
        self.message.content = self._full_content
        self._update_content()
        self.typing_completed.emit()
    
    def skip_typing_animation(self):
        """Skip the typing animation and show full content immediately."""
        if self._is_typing and self._full_content:
            self._complete_typing()


class ChatInputWidget(QTextEdit):
    """
    Custom text input widget for chat.
    
    Supports:
    - Enter to send (emits send_requested)
    - Shift+Enter for new line
    - Dynamic height adjustment
    """
    
    send_requested = pyqtSignal(str)
    
    MAX_HEIGHT = 150
    MIN_HEIGHT = 40
    
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        
        self.setPlaceholderText("Type your message here...")
        self.setMinimumHeight(self.MIN_HEIGHT)
        self.setMaximumHeight(self.MAX_HEIGHT)
        
        # Adjust height on content change
        self.textChanged.connect(self._adjust_height)
    
    def keyPressEvent(self, event: QKeyEvent):
        """Handle key press events."""
        if event.key() == Qt.Key.Key_Return:
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                # Shift+Enter: insert newline
                super().keyPressEvent(event)
            else:
                # Enter: send message
                text = self.toPlainText().strip()
                if text:
                    self.send_requested.emit(text)
                    self.clear()
        else:
            super().keyPressEvent(event)
    
    def _adjust_height(self):
        """Adjust widget height based on content."""
        doc_height = self.document().size().height()
        new_height = min(max(doc_height + 20, self.MIN_HEIGHT), self.MAX_HEIGHT)
        self.setFixedHeight(int(new_height))


class ConfirmationWidget(QFrame):
    """
    확인/거절 버튼을 표시하는 위젯.
    보고서 작성 확인 등에 사용됩니다.
    """
    
    confirmed = pyqtSignal()
    cancelled = pyqtSignal()
    
    def __init__(self, message: str = "계속하시겠습니까?", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._setup_ui(message)
    
    def _setup_ui(self, message: str):
        """Set up the confirmation UI."""
        self.setObjectName("confirmationWidget")
        # 투명 배경 - 버튼만 표시
        self.setStyleSheet("""
            #confirmationWidget {
                background-color: transparent;
                border: none;
                margin: 4px 8px;
            }
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)
        
        # Message label (only show if message is provided)
        if message and message.strip():
            msg_label = QLabel(message)
            msg_label.setWordWrap(True)
            msg_label.setStyleSheet("color: #374151; font-size: 13px;")
            layout.addWidget(msg_label)
        
        # Buttons - 왼쪽 정렬 (AI 메시지처럼)
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)
        
        cancel_btn = QPushButton("❌ 취소")
        cancel_btn.setStyleSheet("""
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
        cancel_btn.clicked.connect(self._on_cancel)
        btn_layout.addWidget(cancel_btn)
        
        confirm_btn = QPushButton("✓ 확인")
        confirm_btn.setStyleSheet("""
            QPushButton {
                background-color: #10B981;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 10px 20px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #059669;
            }
        """)
        confirm_btn.clicked.connect(self._on_confirm)
        btn_layout.addWidget(confirm_btn)
        
        # 왼쪽 정렬을 위해 오른쪽에 stretch 추가
        btn_layout.addStretch()
        
        layout.addLayout(btn_layout)
    
    def _on_confirm(self):
        """Handle confirm button click."""
        self.confirmed.emit()
        self.deleteLater()
    
    def _on_cancel(self):
        """Handle cancel button click."""
        self.cancelled.emit()
        self.deleteLater()


class ChatWidget(QWidget):
    """
    Main chat interface widget.
    
    Features:
    - Message display with bubbles
    - Typing animation for assistant responses
    - Input with Enter to send / Shift+Enter for newline
    - Auto-scroll to latest message
    - Confirmation buttons for report/action requests
    """
    
    # Signals
    message_sent = pyqtSignal(str)  # Emitted when user sends a message
    confirmation_accepted = pyqtSignal(dict)  # Emitted when user confirms an action
    confirmation_rejected = pyqtSignal(dict)  # Emitted when user rejects an action
    
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        
        self._messages: List[Message] = []
        self._message_widgets: List[MessageBubble] = []
        self._is_sending = False
        self._pending_confirmation: Optional[dict] = None
        
        self._setup_ui()
    
    def _setup_ui(self):
        """Set up the chat interface."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Chat header
        header = self._create_header()
        layout.addWidget(header)
        
        # Messages area
        self._messages_area = self._create_messages_area()
        layout.addWidget(self._messages_area, 1)
        
        # Input area
        input_area = self._create_input_area()
        layout.addWidget(input_area)
    
    def _create_header(self) -> QWidget:
        """Create the chat header."""
        header = QFrame()
        header.setObjectName("chatHeader")
        header.setStyleSheet("""
            #chatHeader {
                background-color: transparent;
                border-bottom: 1px solid palette(mid);
            }
        """)
        
        layout = QHBoxLayout(header)
        layout.setContentsMargins(16, 12, 16, 12)
        
        # Title
        title = QLabel("💬 Chat with JARVIS")
        title_font = QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)
        
        layout.addStretch()
        
        # Status indicator
        self._status_label = QLabel("🟢 Ready")
        self._status_label.setProperty("muted", True)
        layout.addWidget(self._status_label)
        
        return header
    
    def _create_messages_area(self) -> QWidget:
        """Create the messages display area."""
        # Scroll area for messages
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setObjectName("messagesScroll")
        
        # Container for messages
        container = QWidget()
        container.setObjectName("messagesContainer")
        
        self._messages_layout = QVBoxLayout(container)
        self._messages_layout.setContentsMargins(8, 8, 8, 8)
        self._messages_layout.setSpacing(4)
        self._messages_layout.addStretch()  # Push messages to top
        
        scroll.setWidget(container)
        self._scroll_area = scroll
        
        return scroll
    
    def _create_input_area(self) -> QWidget:
        """Create the message input area."""
        container = QFrame()
        container.setObjectName("inputArea")
        container.setStyleSheet("""
            #inputArea {
                border-top: 1px solid palette(mid);
            }
        """)
        
        layout = QHBoxLayout(container)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)
        
        # Text input
        self._input_field = ChatInputWidget()
        self._input_field.send_requested.connect(self._on_send)
        layout.addWidget(self._input_field, 1)
        
        # Send button
        self._send_btn = QPushButton("Send")
        self._send_btn.setProperty("primary", True)
        self._send_btn.clicked.connect(self._on_send_clicked)
        self._send_btn.setMinimumWidth(70)
        self._send_btn.setMinimumHeight(40)
        layout.addWidget(self._send_btn)
        
        return container
    
    def _on_send_clicked(self):
        """Handle send button click."""
        text = self._input_field.toPlainText().strip()
        if text:
            self._on_send(text)
            self._input_field.clear()
    
    def _on_send(self, text: str):
        """Handle message send request."""
        if self._is_sending:
            return  # Prevent duplicate sends
        
        self.message_sent.emit(text)
    
    def _scroll_to_bottom(self):
        """Scroll to the bottom of the messages area."""
        QTimer.singleShot(50, lambda: 
            self._scroll_area.verticalScrollBar().setValue(
                self._scroll_area.verticalScrollBar().maximum()
            )
        )
    
    # =========================================================================
    # Public Methods
    # =========================================================================
    
    def add_user_message(self, content: str) -> Message:
        """Add a user message and return the Message object."""
        message = Message.user_message(content)
        self._add_message_bubble(message)
        return message
    
    def add_assistant_message(
        self, 
        content: str, 
        typing_animation: bool = True,
        on_complete: callable = None
    ) -> Message:
        """
        Add an assistant message with optional typing animation.
        
        Args:
            content: Message content
            typing_animation: If True, show typing animation. Default True.
            on_complete: Optional callback to call when typing animation completes.
            
        Returns:
            The Message object
        """
        # Create message with empty content initially if animating
        if typing_animation and len(content) > 10:
            message = Message.assistant_message("")
            bubble = self._add_message_bubble(message, animate=True)
            # Start typing animation
            bubble.start_typing_animation(content)
            # Connect to scroll when typing updates
            bubble.typing_completed.connect(self._scroll_to_bottom)
            # Connect completion callback if provided
            if on_complete:
                bubble.typing_completed.connect(on_complete)
        else:
            message = Message.assistant_message(content)
            self._add_message_bubble(message)
            # Call completion callback immediately for non-animated messages
            if on_complete:
                QTimer.singleShot(100, on_complete)
        return message
    
    def add_system_message(self, content: str) -> Message:
        """Add a system message and return the Message object."""
        message = Message.system_message(content)
        self._add_message_bubble(message)
        return message
    
    def _add_message_bubble(self, message: Message, animate: bool = True) -> MessageBubble:
        """Add a message bubble to the display with slide-in and fade-in animation."""
        self._messages.append(message)
        
        bubble = MessageBubble(message)
        self._message_widgets.append(bubble)
        
        # Insert before the stretch
        self._messages_layout.insertWidget(
            self._messages_layout.count() - 1,
            bubble
        )
        
        # Apply fade-in + slide-in animation
        if animate:
            # Set up opacity effect for fade
            opacity_effect = QGraphicsOpacityEffect(bubble)
            bubble.setGraphicsEffect(opacity_effect)
            opacity_effect.setOpacity(0)
            
            # Fade animation
            fade_animation = QPropertyAnimation(opacity_effect, b"opacity")
            fade_animation.setDuration(300)
            fade_animation.setStartValue(0)
            fade_animation.setEndValue(1)
            fade_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
            
            # Store reference to prevent garbage collection
            bubble._fade_animation = fade_animation
            bubble._opacity_effect = opacity_effect
            fade_animation.start()
            
            # Slide animation (slide up from below)
            # Get initial position after layout
            QTimer.singleShot(10, lambda: self._animate_slide_in(bubble, message.is_user))
        
        self._scroll_to_bottom()
        return bubble
    
    def _animate_slide_in(self, bubble: MessageBubble, is_user: bool):
        """Animate the bubble sliding in from the side."""
        # Store original geometry
        original_geometry = bubble.geometry()
        
        # Calculate start position (slide from side)
        slide_distance = 30
        if is_user:
            # User messages slide from right
            start_x = original_geometry.x() + slide_distance
        else:
            # Assistant messages slide from left
            start_x = original_geometry.x() - slide_distance
        
        start_geometry = original_geometry.translated(
            start_x - original_geometry.x(), 
            15  # Also slide up slightly
        )
        
        # Set start position
        bubble.setGeometry(start_geometry)
        
        # Animate to final position
        slide_animation = QPropertyAnimation(bubble, b"geometry")
        slide_animation.setDuration(300)
        slide_animation.setStartValue(start_geometry)
        slide_animation.setEndValue(original_geometry)
        slide_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        
        # Store reference to prevent garbage collection
        bubble._slide_animation = slide_animation
        slide_animation.start()
    
    def set_status(self, status: str, connected: bool = True, sending: bool = False):
        """Update the connection status indicator."""
        if sending:
            icon = "⏳"
        elif connected:
            icon = "🟢"
        else:
            icon = "🔴"
        self._status_label.setText(f"{icon} {status}")
    
    def set_input_enabled(self, enabled: bool):
        """Enable or disable the input field."""
        self._input_field.setEnabled(enabled)
        self._send_btn.setEnabled(enabled)
    
    def clear_messages(self):
        """Clear all messages from the display."""
        for widget in self._message_widgets:
            widget.deleteLater()
        
        self._messages.clear()
        self._message_widgets.clear()
        self._streaming_bubble = None
    
    def get_messages(self) -> List[Message]:
        """Get all messages."""
        return self._messages.copy()
    
    @property
    def is_sending(self) -> bool:
        """Check if currently sending/streaming."""
        return self._is_sending
    
    # =========================================================================
    # Confirmation UI
    # =========================================================================
    
    def show_confirmation(self, message: str, metadata: dict):
        """
        Show confirmation buttons for an action (e.g., report generation).
        
        Args:
            message: The message to display
            metadata: Metadata about the action to confirm
        """
        self._pending_confirmation = metadata
        
        confirmation = ConfirmationWidget(message, self)
        confirmation.confirmed.connect(self._on_confirmation_accepted)
        confirmation.cancelled.connect(self._on_confirmation_rejected)
        
        # Insert before the stretch
        self._messages_layout.insertWidget(
            self._messages_layout.count() - 1,
            confirmation
        )
        
        self._scroll_to_bottom()
    
    def _on_confirmation_accepted(self):
        """Handle confirmation accepted."""
        if self._pending_confirmation:
            self.confirmation_accepted.emit(self._pending_confirmation)
            self._pending_confirmation = None
    
    def _on_confirmation_rejected(self):
        """Handle confirmation rejected."""
        if self._pending_confirmation:
            self.confirmation_rejected.emit(self._pending_confirmation)
            self._pending_confirmation = None
            # Add a message indicating cancellation
            self.add_system_message("❌ 작업이 취소되었습니다.")
