"""
JARVIS PyQt6 Views Package
Contains all UI components and widgets.
"""

from .main_window import MainWindow
from .floating_button import FloatingButton
from .chat_widget import ChatWidget
from .dashboard_widget import DashboardWidget
from .recommendations_widget import RecommendationsWidget
from .settings_widget import SettingsWidget
from .toast_notification import ToastNotification, ToastManager, ToastType

__all__ = [
    "MainWindow",
    "FloatingButton", 
    "ChatWidget",
    "DashboardWidget",
    "RecommendationsWidget",
    "SettingsWidget",
    "ToastNotification",
    "ToastManager",
    "ToastType",
]
