"""
JARVIS Folder Selection Dialog
File explorer style folder/file selection UI with dark theme.
"""

import os
import platform
from pathlib import Path
from typing import Optional, List, Set, Dict, Any

from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QFrame,
    QWidget,
    QListWidget,
    QListWidgetItem,
    QSplitter,
    QMessageBox,
    QAbstractItemView
)
from PyQt6.QtCore import Qt, pyqtSignal, QThread
from PyQt6.QtGui import QFont, QPixmap

from utils.path_utils import get_resource_path


class DirectoryScanWorker(QThread):
    """Background worker for directory scanning."""
    
    scan_complete = pyqtSignal(list)
    scan_error = pyqtSignal(str)
    
    def __init__(self, path: Path, parent=None):
        super().__init__(parent)
        self.path = path
    
    def run(self):
        """Scan directory contents."""
        try:
            entries = []
            
            with os.scandir(self.path) as scanner:
                for entry in scanner:
                    try:
                        is_dir = entry.is_dir()
                        name = entry.name
                        
                        if name.startswith('.'):
                            continue
                        
                        entries.append({
                            'name': name,
                            'path': Path(entry.path),
                            'is_dir': is_dir
                        })
                    except (PermissionError, OSError):
                        continue
            
            entries.sort(key=lambda x: (not x['is_dir'], x['name'].lower()))
            self.scan_complete.emit(entries)
            
        except PermissionError:
            self.scan_error.emit(f"접근 권한 없음: {self.path}")
        except Exception as e:
            self.scan_error.emit(str(e))


class FolderDialog(QDialog):
    """
    File explorer style folder/file selection dialog.
    Modern dark theme design.
    """
    
    selection_complete = pyqtSignal(list)
    selection_cancelled = pyqtSignal()
    
    def __init__(
        self,
        initial_selections: Optional[List[str]] = None,
        parent=None
    ):
        super().__init__(parent)
        
        self._current_path = Path.home()
        self._history: List[Path] = []
        self._selected_items: Set[Path] = set()
        self._current_entries: Dict[int, Dict[str, Any]] = {}
        self._scan_worker: Optional[DirectoryScanWorker] = None
        
        if initial_selections:
            for item in initial_selections:
                path = Path(item) if isinstance(item, str) else item
                if path.exists():
                    self._selected_items.add(path)
        
        self._setup_dialog()
        self._setup_ui()
        self._navigate_to(self._current_path)
    
    def _setup_dialog(self):
        """Configure dialog properties."""
        self.setWindowTitle("JARVIS - 폴더/파일 선택")
        self.setMinimumSize(1000, 700)
        self.resize(1100, 750)
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
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        
        # Header
        header = self._create_header()
        layout.addWidget(header)
        
        # Navigation bar
        nav = self._create_navigation()
        layout.addWidget(nav)
        
        # Main content
        content = self._create_content()
        layout.addWidget(content, 1)
        
        # Footer
        footer = self._create_footer()
        layout.addWidget(footer)
    
    def _create_header(self) -> QWidget:
        """Create header widget with dark theme."""
        header = QFrame()
        header.setStyleSheet("""
            QFrame {
                background-color: #1a1a1a;
                border-radius: 12px;
                border: 1px solid #2a2a2a;
            }
        """)
        
        layout = QHBoxLayout(header)
        layout.setContentsMargins(20, 16, 20, 16)
        
        # Logo icon
        icon = QLabel()
        icon_pixmap = QPixmap(get_resource_path("icons/jarvis_logo.png"))
        if not icon_pixmap.isNull():
            icon.setPixmap(icon_pixmap.scaled(48, 48, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        else:
            icon.setText("📂")
            icon_font = QFont()
            icon_font.setPointSize(28)
            icon.setFont(icon_font)
        layout.addWidget(icon)
        
        layout.addSpacing(12)
        
        text_layout = QVBoxLayout()
        text_layout.setSpacing(2)
        
        title = QLabel("JARVIS 파일 탐색기")
        title_font = QFont()
        title_font.setPointSize(18)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setStyleSheet("color: #e8e8e8;")
        text_layout.addWidget(title)
        
        desc = QLabel("수집할 폴더와 파일을 선택하세요")
        desc.setStyleSheet("color: #6a6a6a;")
        text_layout.addWidget(desc)
        
        layout.addLayout(text_layout, 1)
        
        return header
    
    def _create_navigation(self) -> QWidget:
        """Create navigation bar."""
        nav = QFrame()
        nav.setStyleSheet("""
            QFrame {
                background-color: #1a1a1a;
                border-radius: 10px;
            }
        """)
        
        layout = QHBoxLayout(nav)
        layout.setContentsMargins(12, 10, 12, 10)
        
        btn_style = """
            QPushButton {
                background-color: #2a2a2a;
                color: #e8e8e8;
                border: none;
                border-radius: 6px;
                padding: 8px 14px;
                font-weight: 600;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #3a3a3a;
            }
        """
        
        up_btn = QPushButton("⬆ 상위")
        up_btn.setStyleSheet(btn_style)
        up_btn.clicked.connect(self._go_to_parent)
        layout.addWidget(up_btn)
        
        back_btn = QPushButton("◀ 뒤로")
        back_btn.setStyleSheet(btn_style)
        back_btn.clicked.connect(self._go_back)
        layout.addWidget(back_btn)
        
        home_btn = QPushButton("🏠 홈")
        home_btn.setStyleSheet(btn_style)
        home_btn.clicked.connect(self._go_home)
        layout.addWidget(home_btn)
        
        sep = QFrame()
        sep.setFixedWidth(1)
        sep.setStyleSheet("background-color: #2a2a2a;")
        layout.addWidget(sep)
        
        self._breadcrumb_label = QLabel()
        self._breadcrumb_label.setStyleSheet("color: #6a6a6a; padding: 0 12px;")
        layout.addWidget(self._breadcrumb_label, 1)
        
        return nav
    
    def _create_content(self) -> QWidget:
        """Create main content with splitter."""
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setStyleSheet("""
            QSplitter::handle {
                background-color: #2a2a2a;
                width: 2px;
            }
        """)
        
        left_panel = self._create_explorer_panel()
        splitter.addWidget(left_panel)
        
        center_panel = self._create_center_buttons()
        splitter.addWidget(center_panel)
        
        right_panel = self._create_basket_panel()
        splitter.addWidget(right_panel)
        
        splitter.setSizes([550, 80, 400])
        
        return splitter
    
    def _create_explorer_panel(self) -> QWidget:
        """Create file explorer panel."""
        panel = QFrame()
        panel.setStyleSheet("""
            QFrame {
                background-color: #1a1a1a;
                border-radius: 10px;
            }
        """)
        
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        header = QFrame()
        header.setStyleSheet("background-color: #242424; border-radius: 10px 10px 0 0;")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(14, 10, 14, 10)
        
        title = QLabel("📁 현재 폴더")
        title.setStyleSheet("color: #e8e8e8; font-weight: bold;")
        header_layout.addWidget(title)
        
        self._item_count_label = QLabel("")
        self._item_count_label.setStyleSheet("color: #6a6a6a;")
        header_layout.addWidget(self._item_count_label)
        
        header_layout.addStretch()
        
        layout.addWidget(header)
        
        self._explorer_list = QListWidget()
        self._explorer_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._explorer_list.setStyleSheet("""
            QListWidget {
                border: none;
                background-color: #1a1a1a;
                color: #e8e8e8;
            }
            QListWidget::item {
                padding: 10px 14px;
                border-bottom: 1px solid #2a2a2a;
            }
            QListWidget::item:selected {
                background-color: #383838;
                color: #ffffff;
            }
            QListWidget::item:hover:!selected {
                background-color: #242424;
            }
        """)
        self._explorer_list.itemDoubleClicked.connect(self._on_explorer_double_click)
        layout.addWidget(self._explorer_list)
        
        return panel
    
    def _create_center_buttons(self) -> QWidget:
        """Create center action buttons."""
        widget = QWidget()
        widget.setFixedWidth(90)
        widget.setStyleSheet("background-color: transparent;")
        
        layout = QVBoxLayout(widget)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        btn_style = """
            QPushButton {{
                background-color: {bg};
                color: {fg};
                border: none;
                border-radius: 8px;
                padding: 10px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {hover};
            }}
        """
        
        add_btn = QPushButton("▶ 추가")
        add_btn.setStyleSheet(btn_style.format(
            bg="#3a3a3a", fg="#e8e8e8", hover="#4a4a4a"
        ))
        add_btn.clicked.connect(self._add_selected_to_basket)
        layout.addWidget(add_btn)
        
        remove_btn = QPushButton("◀ 제거")
        remove_btn.setStyleSheet(btn_style.format(
            bg="#2a2a2a", fg="#9a9a9a", hover="#3a3a3a"
        ))
        remove_btn.clicked.connect(self._remove_from_basket)
        layout.addWidget(remove_btn)
        
        return widget
    
    def _create_basket_panel(self) -> QWidget:
        """Create selected items basket panel."""
        panel = QFrame()
        panel.setStyleSheet("""
            QFrame {
                background-color: #1a1a1a;
                border-radius: 10px;
            }
        """)
        
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        header = QFrame()
        header.setStyleSheet("background-color: #242424; border-radius: 10px 10px 0 0; border-bottom: 1px solid #2a2a2a;")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(14, 10, 14, 10)
        
        title = QLabel("✅ 선택됨")
        title.setStyleSheet("color: #e8e8e8; font-weight: bold;")
        header_layout.addWidget(title)
        
        header_layout.addStretch()
        
        self._selected_count_label = QLabel("0개")
        self._selected_count_label.setStyleSheet("color: #6a6a6a;")
        header_layout.addWidget(self._selected_count_label)
        
        layout.addWidget(header)
        
        self._basket_list = QListWidget()
        self._basket_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._basket_list.setStyleSheet("""
            QListWidget {
                border: none;
                background-color: #1a1a1a;
                color: #e8e8e8;
            }
            QListWidget::item {
                padding: 10px 14px;
                border-bottom: 1px solid #2a2a2a;
            }
            QListWidget::item:selected {
                background-color: #3a3a3a;
                color: #ffffff;
            }
        """)
        self._basket_list.itemDoubleClicked.connect(self._on_basket_double_click)
        layout.addWidget(self._basket_list)
        
        return panel
    
    def _create_footer(self) -> QWidget:
        """Create footer with action buttons."""
        footer = QFrame()
        footer.setStyleSheet("""
            QFrame {
                background-color: #1a1a1a;
                border-radius: 10px;
            }
        """)
        
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(16, 12, 16, 12)
        
        self._status_label = QLabel("📂 폴더를 탐색하세요")
        self._status_label.setStyleSheet("color: #6a6a6a;")
        layout.addWidget(self._status_label)
        
        layout.addStretch()
        
        btn_style = """
            QPushButton {
                background-color: #2a2a2a;
                color: #e8e8e8;
                border: none;
                border-radius: 8px;
                padding: 10px 18px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #3a3a3a;
            }
        """
        
        refresh_btn = QPushButton("🔄 새로고침")
        refresh_btn.setStyleSheet(btn_style)
        refresh_btn.clicked.connect(self._refresh_current)
        layout.addWidget(refresh_btn)
        
        clear_btn = QPushButton("🗑 초기화")
        clear_btn.setStyleSheet(btn_style)
        clear_btn.clicked.connect(self._clear_selection)
        layout.addWidget(clear_btn)
        
        full_scan_btn = QPushButton("💾 전체 홈")
        full_scan_btn.setStyleSheet(btn_style)
        full_scan_btn.clicked.connect(self._select_full_home)
        layout.addWidget(full_scan_btn)
        
        start_btn = QPushButton("🚀 시작하기")
        start_btn.setStyleSheet("""
            QPushButton {
                background-color: #4a4a4a;
                color: #e8e8e8;
                border: none;
                border-radius: 8px;
                padding: 10px 24px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #5a5a5a;
            }
        """)
        start_btn.clicked.connect(self._confirm_selection)
        layout.addWidget(start_btn)
        
        return footer
    
    def _navigate_to(self, path: Path, add_to_history: bool = True):
        """Navigate to a directory."""
        path = Path(path)
        
        if not path.exists():
            QMessageBox.critical(self, "오류", f"경로가 없습니다:\n{path}")
            return
        
        if not path.is_dir():
            return
        
        if add_to_history and self._current_path != path:
            self._history.append(self._current_path)
            if len(self._history) > 50:
                self._history.pop(0)
        
        self._current_path = path
        self._update_breadcrumb()
        
        self._explorer_list.clear()
        self._explorer_list.addItem("⏳ 로딩 중...")
        self._status_label.setText(f"📂 {path}")
        
        self._scan_worker = DirectoryScanWorker(path, self)
        self._scan_worker.scan_complete.connect(self._on_scan_complete)
        self._scan_worker.scan_error.connect(self._on_scan_error)
        self._scan_worker.start()
    
    def _update_breadcrumb(self):
        """Update breadcrumb display."""
        parts = self._current_path.parts
        display = " › ".join(parts) if parts else "/"
        self._breadcrumb_label.setText(display)
    
    def _on_scan_complete(self, entries: list):
        """Handle scan completion."""
        self._explorer_list.clear()
        self._current_entries.clear()
        
        if not entries:
            item = QListWidgetItem("📂 (빈 폴더)")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self._explorer_list.addItem(item)
            self._item_count_label.setText("0개")
            return
        
        for i, entry in enumerate(entries):
            icon = "📁 " if entry['is_dir'] else "📄 "
            item = QListWidgetItem(f"{icon}{entry['name']}")
            item.setData(Qt.ItemDataRole.UserRole, entry)
            self._explorer_list.addItem(item)
            self._current_entries[i] = entry
        
        folder_count = sum(1 for e in entries if e['is_dir'])
        file_count = len(entries) - folder_count
        self._item_count_label.setText(f"📁{folder_count} 📄{file_count}")
        self._status_label.setText(f"✅ {self._current_path}")
    
    def _on_scan_error(self, error: str):
        """Handle scan error."""
        self._explorer_list.clear()
        self._explorer_list.addItem(f"❌ {error}")
        self._status_label.setText(f"⚠️ {error}")
    
    def _on_explorer_double_click(self, item: QListWidgetItem):
        """Handle double-click in explorer."""
        entry = item.data(Qt.ItemDataRole.UserRole)
        if entry and entry.get('is_dir'):
            self._navigate_to(entry['path'])
    
    def _on_basket_double_click(self, item: QListWidgetItem):
        """Handle double-click in basket."""
        path = item.data(Qt.ItemDataRole.UserRole)
        if path:
            self._selected_items.discard(path)
            self._update_basket()
    
    def _add_selected_to_basket(self):
        """Add selected items to basket."""
        for item in self._explorer_list.selectedItems():
            entry = item.data(Qt.ItemDataRole.UserRole)
            if entry:
                self._selected_items.add(entry['path'])
        self._update_basket()
    
    def _remove_from_basket(self):
        """Remove selected items from basket."""
        for item in self._basket_list.selectedItems():
            path = item.data(Qt.ItemDataRole.UserRole)
            if path:
                self._selected_items.discard(path)
        self._update_basket()
    
    def _update_basket(self):
        """Update basket list display."""
        self._basket_list.clear()
        
        sorted_items = sorted(
            self._selected_items,
            key=lambda p: (not p.is_dir() if p.exists() else True, str(p).lower())
        )
        
        for path in sorted_items:
            icon = "📁 " if (path.exists() and path.is_dir()) else "📄 "
            
            try:
                display = f"{icon}~/{path.relative_to(Path.home())}"
            except ValueError:
                display = f"{icon}{path}"
            
            item = QListWidgetItem(display)
            item.setData(Qt.ItemDataRole.UserRole, path)
            self._basket_list.addItem(item)
        
        self._selected_count_label.setText(f"{len(self._selected_items)}개")
    
    def _go_to_parent(self):
        parent = self._current_path.parent
        if parent != self._current_path:
            self._navigate_to(parent)
    
    def _go_back(self):
        if self._history:
            self._navigate_to(self._history.pop(), add_to_history=False)
    
    def _go_home(self):
        self._navigate_to(Path.home())
    
    def _refresh_current(self):
        self._navigate_to(self._current_path, add_to_history=False)
    
    def _clear_selection(self):
        self._selected_items.clear()
        self._update_basket()
        self._status_label.setText("🗑 선택 초기화됨")
    
    def _select_full_home(self):
        result = QMessageBox.question(
            self, "전체 스캔",
            f"전체 홈 폴더를 스캔하시겠습니까?\n{Path.home()}\n\n시간이 오래 걸릴 수 있습니다.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if result == QMessageBox.StandardButton.Yes:
            self._selected_items.clear()
            self._selected_items.add(Path.home())
            self._update_basket()
    
    def _confirm_selection(self):
        if not self._selected_items:
            result = QMessageBox.question(
                self, "선택 없음",
                "선택된 항목이 없습니다.\n전체 홈 폴더를 스캔하시겠습니까?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            
            if result == QMessageBox.StandardButton.Yes:
                self._selected_items.add(Path.home())
            else:
                return
        
        self.selection_complete.emit([str(p) for p in self._selected_items])
        self.accept()
    
    def closeEvent(self, event):
        result = QMessageBox.question(
            self, "종료",
            "폴더 선택을 취소하시겠습니까?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if result == QMessageBox.StandardButton.Yes:
            self.selection_cancelled.emit()
            super().closeEvent(event)
        else:
            event.ignore()
    
    def get_selected_paths(self) -> List[str]:
        return [str(p) for p in self._selected_items]


def show_folder_dialog(
    initial_selections: Optional[List[str]] = None,
    parent=None
) -> Optional[List[str]]:
    dialog = FolderDialog(initial_selections, parent)
    result = dialog.exec()
    
    if result == QDialog.DialogCode.Accepted:
        return dialog.get_selected_paths()
    return None
