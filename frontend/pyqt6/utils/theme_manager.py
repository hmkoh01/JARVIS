"""
JARVIS Theme Manager
Manages application themes and styles with OS detection and persistence.

Phase 1: Complete theme system implementation
"""

import sys
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal, QSettings
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QPalette


class ThemeManager(QObject):
    """
    Manages application theming with:
    - Light theme only (fixed)
    - Theme change signals for reactive UI updates
    """
    
    # Signal emitted when theme changes: 'dark' or 'light'
    theme_changed = pyqtSignal(str)
    
    THEMES = ["light"]
    DEFAULT_THEME = "light"
    
    # QSettings keys
    SETTINGS_GROUP = "Theme"
    SETTINGS_KEY = "current_theme"
    
    def __init__(self, resources_path: Optional[Path] = None, parent: Optional[QObject] = None):
        super().__init__(parent)
        
        if resources_path is None:
            # Default: pyqt6/resources/styles/
            self._styles_path = Path(__file__).parent.parent / "resources" / "styles"
        else:
            self._styles_path = resources_path / "styles"
        
        self._current_theme = self.DEFAULT_THEME
        self._settings = QSettings("JARVIS", "JARVIS")
    
    @property
    def current_theme(self) -> str:
        """Get current theme name."""
        return self._current_theme
    
    @property
    def is_dark(self) -> bool:
        """Check if current theme is dark."""
        return self._current_theme == "dark"
    
    @property
    def is_light(self) -> bool:
        """Check if current theme is light."""
        return self._current_theme == "light"
    
    # =========================================================================
    # OS Theme Detection
    # =========================================================================
    
    def detect_system_theme(self) -> str:
        """
        Detect the operating system's color scheme preference.
        
        Returns:
            'dark' or 'light' based on OS settings
        """
        # Try QPalette-based detection (works on most platforms)
        try:
            app = QApplication.instance()
            if app:
                palette = app.palette()
                # Compare window background luminance
                bg_color = palette.color(QPalette.ColorRole.Window)
                # Calculate luminance (simple formula)
                luminance = (
                    0.299 * bg_color.red() + 
                    0.587 * bg_color.green() + 
                    0.114 * bg_color.blue()
                )
                if luminance < 128:
                    return "dark"
                else:
                    return "light"
        except Exception:
            pass
        
        # Windows-specific registry check
        if sys.platform == "win32":
            try:
                import winreg
                key = winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
                )
                value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
                winreg.CloseKey(key)
                return "light" if value == 1 else "dark"
            except Exception:
                pass
        
        # macOS detection via defaults
        if sys.platform == "darwin":
            try:
                import subprocess
                result = subprocess.run(
                    ["defaults", "read", "-g", "AppleInterfaceStyle"],
                    capture_output=True,
                    text=True
                )
                if result.returncode == 0 and "Dark" in result.stdout:
                    return "dark"
                return "light"
            except Exception:
                pass
        
        # Default fallback
        return self.DEFAULT_THEME
    
    # =========================================================================
    # Theme Persistence
    # =========================================================================
    
    def load_saved_theme(self) -> str:
        """
        Load previously saved theme from QSettings.
        Note: Now fixed to light mode only.
        
        Returns:
            Always returns 'light'
        """
        # 항상 라이트 모드 반환
        return "light"
    
    def save_theme(self) -> None:
        """Save current theme to QSettings."""
        self._settings.beginGroup(self.SETTINGS_GROUP)
        self._settings.setValue(self.SETTINGS_KEY, self._current_theme)
        self._settings.endGroup()
        self._settings.sync()  # Force write to disk
    
    # =========================================================================
    # Theme Application
    # =========================================================================
    
    def apply_theme(self, theme: str, app: Optional[QApplication] = None) -> bool:
        """
        Apply a theme to the application.
        
        Args:
            theme: Theme name ('dark' or 'light')
            app: QApplication instance (uses qApp if None)
            
        Returns:
            True if theme was applied successfully
        """
        if theme not in self.THEMES:
            print(f"Warning: Unknown theme '{theme}', falling back to '{self.DEFAULT_THEME}'")
            theme = self.DEFAULT_THEME
        
        qss_file = self._styles_path / f"{theme}.qss"
        
        if not qss_file.exists():
            print(f"Warning: Theme file not found: {qss_file}")
            return False
        
        try:
            stylesheet = qss_file.read_text(encoding="utf-8")
            
            if app is None:
                app = QApplication.instance()
            
            if app:
                app.setStyleSheet(stylesheet)
                old_theme = self._current_theme
                self._current_theme = theme
                
                # Save to settings
                self.save_theme()
                
                # Emit signal if theme actually changed
                if old_theme != theme:
                    self.theme_changed.emit(theme)
                
                return True
                
        except Exception as e:
            print(f"Error applying theme: {e}")
        
        return False
    
    def set_dark(self, app: Optional[QApplication] = None) -> bool:
        """Apply dark theme."""
        return self.apply_theme("dark", app)
    
    def set_light(self, app: Optional[QApplication] = None) -> bool:
        """Apply light theme."""
        return self.apply_theme("light", app)
    
    def toggle_theme(self, app: Optional[QApplication] = None) -> str:
        """
        Toggle between dark and light theme.
        Note: Now fixed to light mode only.
        
        Returns:
            The new theme name (always 'light')
        """
        # 항상 라이트 모드 유지
        self.apply_theme("light", app)
        return self._current_theme
    
    def use_system_theme(self, app: Optional[QApplication] = None) -> str:
        """
        Detect and apply the system theme.
        Note: Now fixed to light mode only.
        
        Returns:
            The applied theme name (always 'light')
        """
        # 항상 라이트 모드 사용
        self.apply_theme("light", app)
        return self._current_theme
    
    def initialize(self, app: Optional[QApplication] = None, use_saved: bool = True) -> str:
        """
        Initialize theme system.
        
        Args:
            app: QApplication instance
            use_saved: If True, use saved theme; if False, detect system theme
            
        Returns:
            The applied theme name
        """
        if use_saved:
            theme = self.load_saved_theme()
        else:
            theme = self.detect_system_theme()
        
        self.apply_theme(theme, app)
        return self._current_theme
