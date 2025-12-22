# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('../config.py', '.'), ('../token_store.py', '.'), ('../../configs.yaml', '.'), ('resources/styles', 'resources/styles'), ('resources/icons', 'resources/icons'), ('resources/icons/jarvis.ico', '.'), ('resources/icons/jarvis_logo.png', '.'), ('utils', 'utils'), ('views', 'views'), ('services', 'services'), ('controllers', 'controllers'), ('models', 'models')]
binaries = []
hiddenimports = ['uuid', 'datetime', 'dataclasses', 'enum', 'typing', 'json', 'threading', 'queue', 'time', 'pathlib', 'logging', 'platform', 'webbrowser', 'http.server', 'socketserver', 'urllib.parse', 'subprocess', 'utils.theme_manager', 'utils.path_utils', 'views.main_window', 'views.floating_button', 'views.chat_widget', 'views.dashboard_widget', 'views.recommendations_widget', 'views.settings_widget', 'views.toast_notification', 'views.dialogs', 'views.dialogs.login_dialog', 'views.dialogs.survey_dialog', 'views.dialogs.folder_dialog', 'services.api_client', 'services.websocket_client', 'controllers.auth_controller', 'controllers.chat_controller', 'controllers.file_controller', 'models.message', 'models.recommendation', 'models.user']
tmp_ret = collect_all('PyQt6')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='JARVIS',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # 터미널(콘솔) 창 숨기기
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='resources/icons/jarvis.ico',  # EXE 파일 아이콘
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='JARVIS_pyqt6',
)
