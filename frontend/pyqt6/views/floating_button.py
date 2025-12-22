"""
JARVIS Floating Button
Always-on-top floating button for quick access.

Phase 2: Core UI component - Frameless, draggable, with context menu
Features JARVIS icon with rotating loading animation.
"""

import sys
import math
from typing import Optional, TYPE_CHECKING

from PyQt6.QtWidgets import (
    QWidget, 
    QApplication, 
    QMenu, 
    QMessageBox,
    QGraphicsDropShadowEffect,
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton
)
from PyQt6.QtCore import (
    Qt, 
    QPoint, 
    QPointF,
    QSize, 
    pyqtSignal,
    QPropertyAnimation,
    QEasingCurve,
    QTimer
)
from PyQt6.QtGui import (
    QPainter, 
    QColor, 
    QBrush, 
    QPen, 
    QFont,
    QCursor,
    QMouseEvent,
    QPaintEvent,
    QEnterEvent
)

if TYPE_CHECKING:
    from .main_window import MainWindow


class FloatingButton(QWidget):
    """
    Always-on-top floating button for quick access to JARVIS.
    
    Features:
    - Frameless and transparent background
    - Always on top of other windows
    - Circular button with icon/text
    - Draggable (distinguishes click from drag)
    - Right-click context menu
    - Hover effects
    """
    
    # Signals
    clicked = pyqtSignal()
    double_clicked = pyqtSignal()
    exit_requested = pyqtSignal()
    settings_requested = pyqtSignal()
    
    # Button configuration
    BUTTON_SIZE = 64
    BUTTON_MARGIN = 8
    SHADOW_PADDING = 20  # Extra padding for shadow effect to prevent clipping
    DRAG_THRESHOLD = 5  # Pixels to move before considering it a drag
    
    # Colors - Modern Monochrome (matching JARVIS icon)
    COLOR_PRIMARY = QColor("#1a1a1a")  # Dark charcoal
    COLOR_PRIMARY_HOVER = QColor("#2a2a2a")  # Lighter charcoal
    COLOR_PRIMARY_PRESSED = QColor("#0a0a0a")  # Darker
    COLOR_TEXT = QColor("#e8e8e8")  # Off-white
    
    def __init__(self, main_window: Optional["MainWindow"] = None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        
        self._main_window = main_window
        
        # Drag state
        self._drag_start_pos: Optional[QPoint] = None
        self._is_dragging = False
        self._mouse_press_pos: Optional[QPoint] = None
        
        # Visual state
        self._is_hovered = False
        self._is_pressed = False
        
        # Loading animation state
        self._is_loading = False
        self._rotation_angle = 225.0  # Default angle (bottom-left direction)
        
        # Rotation timer for smooth animation (~60 FPS)
        self._rotation_timer = QTimer(self)
        self._rotation_timer.timeout.connect(self._update_rotation)
        self._rotation_timer.setInterval(16)
        
        # Animation
        self._hover_animation: Optional[QPropertyAnimation] = None
        self._current_scale = 1.0
        
        # Custom click handler (can be overridden)
        self._custom_click_handler = None
        
        self._setup_window()
        self._setup_ui()
        self._position_default()
    
    def _setup_window(self):
        """Configure window properties for floating button behavior."""
        # Frameless window
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool  # Prevents taskbar entry
        )
        
        # Transparent background
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        # Track mouse for hover effects
        self.setMouseTracking(True)
        
        # Fixed size - include shadow padding to prevent Windows layered window clipping errors
        total_size = self.BUTTON_SIZE + self.BUTTON_MARGIN * 2 + self.SHADOW_PADDING * 2
        self.setFixedSize(total_size, total_size)
    
    def _setup_ui(self):
        """Set up the visual appearance."""
        # Add drop shadow effect
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(15)
        shadow.setColor(QColor(0, 0, 0, 80))
        shadow.setOffset(0, 4)
        self.setGraphicsEffect(shadow)
        
        # Set cursor
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
    
    def _position_default(self):
        """Position the button at the bottom-right of the primary screen."""
        screen = QApplication.primaryScreen()
        if screen:
            geometry = screen.availableGeometry()
            x = geometry.right() - self.width() - 30
            y = geometry.bottom() - self.height() - 30
            self.move(x, y)
    
    def showEvent(self, event):
        """Ensure button is within screen bounds when shown."""
        super().showEvent(event)
        self._ensure_on_screen()
    
    def _ensure_on_screen(self):
        """Check if button is on screen, reset to default if not."""
        current_pos = self.pos()
        
        # Check if current position is on any available screen
        screen = QApplication.screenAt(current_pos)
        if screen is None:
            # Button is off-screen, reset to default position
            print(f"⚠️ 플로팅 버튼이 화면 밖에 있어 기본 위치로 이동합니다")
            self._position_default()
            return
        
        # Ensure button is fully visible within screen bounds
        geometry = screen.availableGeometry()
        x, y = current_pos.x(), current_pos.y()
        
        # Check if button is fully visible
        if (x < geometry.left() or x + self.width() > geometry.right() or
            y < geometry.top() or y + self.height() > geometry.bottom()):
            # Constrain to screen
            new_pos = self._constrain_to_screen(current_pos)
            if new_pos != current_pos:
                print(f"⚠️ 플로팅 버튼 위치 조정: ({x}, {y}) → ({new_pos.x()}, {new_pos.y()})")
                self.move(new_pos)
    
    def set_main_window(self, main_window: "MainWindow"):
        """Set the main window reference for toggling."""
        self._main_window = main_window
    
    # =========================================================================
    # Loading Animation
    # =========================================================================
    
    def _update_rotation(self):
        """Smoothly update rotation angle for loading animation."""
        self._rotation_angle = (self._rotation_angle + 4) % 360
        
        # 디버그용: 일정 주기마다만 로그 출력 (약 0.5초에 1번)
        if not hasattr(self, "_rot_tick"):
            self._rot_tick = 0
        self._rot_tick += 1
        if self._rot_tick % 30 == 0:
            print(f"🔄 tick={self._rot_tick}, angle={self._rotation_angle:.1f}")
        self.update()
    
    def set_loading(self, loading: bool):
        """
        Set loading state - starts/stops the rotation animation.
        
        Args:
            loading: True to start loading animation, False to stop
        """
        print(f"🔄 FloatingButton.set_loading({loading}), current={self._is_loading}")
        if self._is_loading == loading:
            print(f"  ↳ 이미 같은 상태, 무시")
            return
            
        self._is_loading = loading
        if loading:
            self._rotation_timer.start()
            print(f"  ↳ 타이머 시작됨, isActive={self._rotation_timer.isActive()}")
        else:
            self._rotation_timer.stop()
            self._rotation_angle = 225.0  # Reset to default position
            print(f"  ↳ 타이머 중지됨")
        self.update()
    
    def is_loading(self) -> bool:
        """Check if currently in loading state."""
        return self._is_loading
    
    # =========================================================================
    # Paint Event
    # =========================================================================
    
    def paintEvent(self, event: QPaintEvent):
        """Draw the JARVIS icon with optional loading animation."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Calculate button rect (centered with margin + shadow padding)
        total_padding = self.BUTTON_MARGIN + self.SHADOW_PADDING
        button_rect = self.rect().adjusted(total_padding, total_padding, -total_padding, -total_padding)
        center = QPointF(button_rect.center())
        radius = button_rect.width() / 2
        
        # === Background ===
        if self._is_pressed:
            bg_color = self.COLOR_PRIMARY_PRESSED
        elif self._is_hovered:
            bg_color = self.COLOR_PRIMARY_HOVER
        else:
            bg_color = self.COLOR_PRIMARY
        
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(bg_color))
        painter.drawEllipse(button_rect)
        
        # === JARVIS Icon ===
        icon_color = self.COLOR_TEXT
        
        # Proportions matching the original icon
        outer_radius = radius * 0.72
        ring_width = radius * 0.11  # Outer ring thickness
        inner_circle_radius = radius * 0.20  # Inner filled circle
        line_width = radius * 0.09  # Line thickness
        
        # 1. Outer ring (circular outline)
        pen = QPen(icon_color, ring_width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(center, outer_radius, outer_radius)
        
        # 2. Calculate line endpoint based on rotation angle
        angle_rad = math.radians(self._rotation_angle)
        line_length = outer_radius * 0.55  # From center to inner circle center
        
        end_x = center.x() + line_length * math.cos(angle_rad)
        end_y = center.y() + line_length * math.sin(angle_rad)
        end_point = QPointF(end_x, end_y)
        
        # 3. Line from center to inner circle (with round cap)
        line_pen = QPen(icon_color, line_width)
        line_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(line_pen)
        painter.drawLine(center, end_point)
        
        # 4. Inner filled circle
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(icon_color))
        painter.drawEllipse(end_point, inner_circle_radius, inner_circle_radius)
        
        painter.end()
    
    # =========================================================================
    # Mouse Events
    # =========================================================================
    
    def _is_point_in_button(self, pos: QPoint) -> bool:
        """Check if a point is within the circular button area."""
        total_padding = self.BUTTON_MARGIN + self.SHADOW_PADDING
        center_x = self.width() / 2
        center_y = self.height() / 2
        radius = self.BUTTON_SIZE / 2
        
        dx = pos.x() - center_x
        dy = pos.y() - center_y
        return (dx * dx + dy * dy) <= (radius * radius)
    
    def mousePressEvent(self, event: QMouseEvent):
        """Handle mouse press - start potential drag."""
        if event.button() == Qt.MouseButton.LeftButton:
            # Only register press if within the button circle
            if not self._is_point_in_button(event.pos()):
                super().mousePressEvent(event)
                return
            
            self._drag_start_pos = event.globalPosition().toPoint()
            self._mouse_press_pos = event.globalPosition().toPoint()
            self._is_dragging = False
            self._is_pressed = True
            self.update()
        
        super().mousePressEvent(event)
    
    def mouseMoveEvent(self, event: QMouseEvent):
        """Handle mouse move - drag if threshold exceeded, and update hover state."""
        # Update hover state based on whether mouse is over the button circle
        new_hovered = self._is_point_in_button(event.pos())
        if new_hovered != self._is_hovered:
            self._is_hovered = new_hovered
            self.update()
        
        if self._drag_start_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            # Calculate distance moved
            diff = event.globalPosition().toPoint() - self._mouse_press_pos
            distance = (diff.x() ** 2 + diff.y() ** 2) ** 0.5
            
            # Start dragging if threshold exceeded
            if distance >= self.DRAG_THRESHOLD:
                self._is_dragging = True
                
                # Move window to follow cursor (center button on cursor)
                new_pos = event.globalPosition().toPoint() - QPoint(self.width() // 2, self.height() // 2)
                
                # Constrain to screen bounds
                new_pos = self._constrain_to_screen(new_pos)
                
                self.move(new_pos)
        
        super().mouseMoveEvent(event)
    
    def mouseReleaseEvent(self, event: QMouseEvent):
        """Handle mouse release - click if not dragging."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._is_pressed = False
            self.update()
            
            if not self._is_dragging:
                # This was a click, not a drag
                self._on_click()
            
            self._drag_start_pos = None
            self._mouse_press_pos = None
            self._is_dragging = False
        
        super().mouseReleaseEvent(event)
    
    def mouseDoubleClickEvent(self, event: QMouseEvent):
        """Handle double-click - optional exit confirmation."""
        if event.button() == Qt.MouseButton.LeftButton:
            if not self._is_point_in_button(event.pos()):
                super().mouseDoubleClickEvent(event)
                return
            self.double_clicked.emit()
            # Optionally show exit confirmation
            # self._confirm_exit()
        
        super().mouseDoubleClickEvent(event)
    
    def enterEvent(self, event: QEnterEvent):
        """Handle mouse enter - hover effect."""
        # Will be updated properly in mouseMoveEvent for accurate circle detection
        super().enterEvent(event)
    
    def leaveEvent(self, event):
        """Handle mouse leave - remove hover effect."""
        self._is_hovered = False
        self.update()
        super().leaveEvent(event)
    
    def contextMenuEvent(self, event):
        """Handle right-click - show context menu only on button area."""
        if not self._is_point_in_button(event.pos()):
            super().contextMenuEvent(event)
            return
        
        menu = QMenu(self)
        
        # Style the menu
        menu.setStyleSheet("""
            QMenu {
                background-color: #27272A;
                color: #E4E4E7;
                border: 1px solid #3D3D4D;
                border-radius: 8px;
                padding: 4px;
            }
            QMenu::item {
                padding: 8px 24px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #2e2e2e;
                color: white;
            }
            QMenu::separator {
                height: 1px;
                background-color: #3D3D4D;
                margin: 4px 8px;
            }
        """)
        
        # Add menu items
        show_action = menu.addAction("📱 창 열기/닫기")
        show_action.triggered.connect(self._toggle_main_window)
        
        menu.addSeparator()
        
        settings_action = menu.addAction("⚙️ 설정")
        settings_action.triggered.connect(self._on_settings)
        
        menu.addSeparator()
        
        exit_action = menu.addAction("❌ 종료")
        exit_action.triggered.connect(self._confirm_exit)
        
        menu.exec(event.globalPos())
    
    # =========================================================================
    # Helper Methods
    # =========================================================================
    
    def _constrain_to_screen(self, pos: QPoint) -> QPoint:
        """Constrain position to screen bounds."""
        screen = QApplication.screenAt(pos)
        if screen is None:
            screen = QApplication.primaryScreen()
        
        if screen:
            geometry = screen.availableGeometry()
            
            x = max(geometry.left(), min(pos.x(), geometry.right() - self.width()))
            y = max(geometry.top(), min(pos.y(), geometry.bottom() - self.height()))
            
            return QPoint(x, y)
        
        return pos
    
    def set_click_handler(self, handler):
        """Set a custom click handler to override default behavior.
        
        Args:
            handler: Callable to execute on click. Set to None to restore default.
        """
        self._custom_click_handler = handler
    
    def on_click(self):
        """Handle button click. Can be overridden via set_click_handler."""
        self.clicked.emit()
        
        if self._custom_click_handler is not None:
            self._custom_click_handler()
        else:
            self._toggle_main_window()
    
    def _on_click(self):
        """Internal click handler called by mouseReleaseEvent."""
        self.on_click()
    
    def _toggle_main_window(self):
        """Toggle main window visibility."""
        if self._main_window is not None:
            if self._main_window.isVisible():
                self._main_window.hide()
            else:
                self._main_window.show()
                self._main_window.raise_()
                self._main_window.activateWindow()
    
    def _on_settings(self):
        """Handle settings request."""
        self.settings_requested.emit()
        # TODO: Phase 4 - Open settings dialog
    
    def _confirm_exit(self):
        """Show exit confirmation dialog."""
        # 커스텀 다이얼로그 생성
        dialog = QDialog(self)
        dialog.setWindowTitle("JARVIS 종료")
        dialog.setFixedSize(240, 120)
        dialog.setWindowFlags(
            Qt.WindowType.Dialog |
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint
        )
        dialog.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        # 메인 컨테이너
        container = QWidget(dialog)
        container.setGeometry(0, 0, 240, 120)
        container.setStyleSheet("""
            QWidget {
                background-color: #1E1E1E;
                border-radius: 12px;
                border: 1px solid #3D3D4D;
            }
        """)
        
        # 레이아웃
        layout = QVBoxLayout(container)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(16)
        
        # 메시지 라벨
        label = QLabel("JARVIS를 종료하시겠습니까?")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet("""
            QLabel {
                color: #E4E4E7;
                font-size: 14px;
                font-weight: 500;
                background: transparent;
                border: none;
            }
        """)
        layout.addWidget(label)
        
        # 버튼 레이아웃
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)
        
        # 취소 버튼
        cancel_btn = QPushButton("취소")
        cancel_btn.setFixedSize(90, 36)
        cancel_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: #3D3D4D;
                color: #E4E4E7;
                border: none;
                border-radius: 8px;
                font-size: 13px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #4D4D5D;
            }
            QPushButton:pressed {
                background-color: #2D2D3D;
            }
        """)
        cancel_btn.clicked.connect(dialog.reject)
        btn_layout.addWidget(cancel_btn)
        
        # 종료 버튼
        exit_btn = QPushButton("종료")
        exit_btn.setFixedSize(90, 36)
        exit_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        exit_btn.setStyleSheet("""
            QPushButton {
                background-color: #DC2626;
                color: white;
                border: none;
                border-radius: 8px;
                font-size: 13px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #EF4444;
            }
            QPushButton:pressed {
                background-color: #B91C1C;
            }
        """)
        exit_btn.clicked.connect(dialog.accept)
        btn_layout.addWidget(exit_btn)
        
        layout.addLayout(btn_layout)
        
        # 다이얼로그를 화면 중앙에 위치
        screen = QApplication.primaryScreen()
        if screen:
            screen_geo = screen.availableGeometry()
            x = screen_geo.center().x() - dialog.width() // 2
            y = screen_geo.center().y() - dialog.height() // 2
            dialog.move(x, y)
        
        # 다이얼로그 실행
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.exit_requested.emit()
            QApplication.quit()
    
    # =========================================================================
    # Public Methods
    # =========================================================================
    
    def set_icon_text(self, text: str):
        """Set the icon/emoji displayed on the button."""
        # Store and repaint
        self._icon_text = text
        self.update()
    
    def set_colors(self, primary: QColor, hover: QColor = None, pressed: QColor = None):
        """Set custom button colors."""
        self.COLOR_PRIMARY = primary
        if hover:
            self.COLOR_PRIMARY_HOVER = hover
        if pressed:
            self.COLOR_PRIMARY_PRESSED = pressed
        self.update()
    
    def show_notification(self, message: str, duration_ms: int = 3000):
        """Show a brief notification near the button."""
        # TODO: Implement tooltip/bubble notification
        pass
