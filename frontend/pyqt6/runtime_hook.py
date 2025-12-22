# -*- coding: utf-8 -*-
"""
PyInstaller 런타임 훅 - PyQt6 DLL 경로 설정
"""
import os
import sys

def setup_qt_paths():
    """Qt6 플러그인 및 라이브러리 경로 설정"""
    if getattr(sys, 'frozen', False):
        # PyInstaller로 빌드된 경우
        base_path = sys._MEIPASS
        
        # Qt6 바이너리 경로
        qt_bin_path = os.path.join(base_path, 'PyQt6', 'Qt6', 'bin')
        qt_plugins_path = os.path.join(base_path, 'PyQt6', 'Qt6', 'plugins')
        
        # 대체 경로 (collect_data_files 구조)
        if not os.path.exists(qt_bin_path):
            qt_bin_path = os.path.join(base_path, 'Qt6', 'bin')
            qt_plugins_path = os.path.join(base_path, 'Qt6', 'plugins')
        
        # PATH에 Qt6 bin 추가
        if os.path.exists(qt_bin_path):
            os.environ['PATH'] = qt_bin_path + os.pathsep + os.environ.get('PATH', '')
        
        # Qt 플러그인 경로 설정
        if os.path.exists(qt_plugins_path):
            os.environ['QT_PLUGIN_PATH'] = qt_plugins_path
        
        # QT_QPA_PLATFORM_PLUGIN_PATH 설정
        platforms_path = os.path.join(qt_plugins_path, 'platforms')
        if os.path.exists(platforms_path):
            os.environ['QT_QPA_PLATFORM_PLUGIN_PATH'] = platforms_path

setup_qt_paths()

