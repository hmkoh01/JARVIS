#!/usr/bin/env python3
"""
Folder Selection UI
시스템 시작 시 폴더 선택을 위한 독립적인 UI
"""

import tkinter as tk
from tkinter import ttk, messagebox
import requests
import threading
import time
import logging
import queue  # 1. 스레드 간 안전한 통신을 위해 queue 모듈 추가
import platform
import os

# 로깅 설정
logging.basicConfig(level=logging.INFO, format='%(asctime)s:%(levelname)s:%(name)s:%(message)s')
logger = logging.getLogger(__name__)

class FolderSelector:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("JARVIS - 폴더 선택")
        self.root.configure(bg='#f8fafc')
        self.root.resizable(True, True)
        self.root.minsize(750, 650)
        
        # OS-specific settings
        self.platform = platform.system()
        if self.platform == "Windows":
            self.scan_root_name = "C드라이브"
            self.example_scan_path = f"🔍 {os.path.expanduser('~')}\\Desktop 폴더를 스캔하고 있습니다..."
        elif self.platform == "Darwin": # macOS
            self.scan_root_name = "메인 드라이브"
            self.example_scan_path = f"🔍 {os.path.expanduser('~')}/Desktop 폴더를 스캔하고 있습니다..."
        else: # Linux etc.
            self.scan_root_name = "파일 시스템"
            self.example_scan_path = f"🔍 {os.path.expanduser('~')}/Desktop 폴더를 스캔하고 있습니다..."
            
        # 창을 화면 중앙에 배치 (geometry 설정 전에)
        self.center_window()
        self.setup_korean_fonts()
        
        self.API_BASE_URL = "http://localhost:8000"
        self.selected_folders = None
        self.folder_data = []

        # 2. 스레드 통신을 위한 큐 생성
        self.folder_queue = queue.Queue()
        
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.create_ui() # UI는 기존과 동일하게 생성
        
        # 3. UI가 완전히 그려진 후 폴더 로딩 시작
        self.root.after(200, self.load_folders)
        
        # 4. 큐를 주기적으로 확인하여 UI를 안전하게 업데이트하는 로직 시작
        self.process_queue()
    
    def center_window(self):
        """창을 화면 중앙에 배치"""
        # 창 크기 설정
        window_width = 850
        window_height = 750
        
        # 화면 중앙 계산
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        
        x = int((screen_width - window_width) / 2)
        y = int((screen_height - window_height) / 2)
        
        # 화면 경계 내에 위치하도록 조정
        x = max(0, x)
        y = max(0, y)
        
        # 창 크기와 위치를 한 번에 설정
        self.root.geometry(f'{window_width}x{window_height}+{x}+{y}')
        self.root.lift()
        self.root.attributes('-topmost', True)
        self.root.after_idle(lambda: self.root.attributes('-topmost', False))
    
    def setup_korean_fonts(self):
        """한글 폰트를 설정합니다."""
        # 플랫폼별 한글 폰트 설정
        if self.platform == "Darwin":  # macOS
            korean_fonts = [
                'Apple SD Gothic Neo',  # macOS 기본 한글 폰트
                'AppleGothic',          # macOS 기본 고딕
                'Nanum Gothic',         # 나눔고딕 (설치된 경우)
                'Helvetica Neue',       # macOS 기본 영문 폰트
                'Lucida Grande',        # macOS 시스템 폰트
                'Arial Unicode MS'      # Unicode 폰트
            ]
        else:  # Windows/Linux
            korean_fonts = [
                'Malgun Gothic',        # 맑은 고딕 (Windows 기본)
                'Nanum Gothic',         # 나눔고딕
                'Nanum Barun Gothic',   # 나눔바른고딕
                'Dotum',                # 돋움
                'Gulim',                # 굴림
                'Batang',               # 바탕
                'Arial Unicode MS'      # Unicode 폰트
            ]
        
        self.default_font = 'Arial'
        for font in korean_fonts:
            try:
                test_label = tk.Label(self.root, font=(font, 12))
                test_label.destroy()
                self.default_font = font
                break
            except:
                continue
        self.title_font = (self.default_font, 20, 'bold')
        self.subtitle_font = (self.default_font, 14)
        self.message_font = (self.default_font, 12)
        self.button_font = (self.default_font, 11, 'bold')
    
    def create_ui(self):
        """UI를 생성합니다."""
        # ======================================================================
        # 이 함수는 사용자가 제공한 원본 UI 코드와 100% 동일합니다.
        # ======================================================================
        main_container = tk.Frame(self.root, bg='#f8fafc')
        main_container.pack(fill='both', expand=True)
        center_frame = tk.Frame(main_container, bg='#f8fafc')
        center_frame.pack(expand=True, fill='both')
        main_frame = tk.Frame(center_frame, bg='white', relief='flat', bd=0)
        main_frame.pack(expand=True, fill='both', padx=40, pady=40)
        shadow_frame = tk.Frame(main_frame, bg='#e2e8f0', height=2)
        shadow_frame.pack(fill='x', side='bottom')
        header_frame = tk.Frame(main_frame, bg='white')
        header_frame.pack(fill='x', padx=30, pady=(30, 20))
        title_frame = tk.Frame(header_frame, bg='white')
        title_frame.pack(fill='x')
        icon_label = tk.Label(title_frame, text="📁", font=('Arial', 32), bg='white', fg='#4f46e5')
        icon_label.pack(side='left', padx=(0, 15))
        text_frame = tk.Frame(title_frame, bg='white')
        text_frame.pack(side='left', fill='x', expand=True)
        title_label = tk.Label(text_frame, text="JARVIS 파일 수집", font=(self.default_font, 24, 'bold'), bg='white', fg='#1f2937')
        title_label.pack(anchor='w')
        subtitle_label = tk.Label(text_frame, text="폴더 선택", font=(self.default_font, 16), bg='white', fg='#6b7280')
        subtitle_label.pack(anchor='w')
        desc_frame = tk.Frame(main_frame, bg='white')
        desc_frame.pack(fill='x', padx=30, pady=(0, 25))
        desc_label = tk.Label(main_frame, text="파일 수집할 폴더를 선택하세요.\n사용자 폴더 내의 주요 폴더들이 표시됩니다.\n선택하지 않으면 전체 폴더를 스캔합니다.", font=(self.default_font, 12), bg='white', fg='#6b7280', wraplength=650, justify='left')
        desc_label.pack(anchor='w', in_=desc_frame)
        list_container = tk.Frame(main_frame, bg='white')
        list_container.pack(fill='both', expand=True, padx=30, pady=(0, 25))
        list_header = tk.Frame(list_container, bg='#f8fafc', relief='flat', bd=1)
        list_header.pack(fill='x', pady=(0, 10))
        header_label = tk.Label(list_header, text="📂 사용 가능한 폴더", font=(self.default_font, 14, 'bold'), bg='#f8fafc', fg='#374151', pady=10)
        header_label.pack(side='left', padx=15)
        list_frame = tk.Frame(list_container, bg='#f8fafc', relief='flat', bd=1)
        list_frame.pack(fill='both', expand=True)
        scrollbar = ttk.Scrollbar(list_frame, orient='vertical')
        scrollbar.pack(side='right', fill='y', padx=(0, 5), pady=5)
        self.folder_listbox = tk.Listbox(list_frame, font=(self.default_font, 11), selectmode='multiple', yscrollcommand=scrollbar.set, bg='white', fg='#1f2937', selectbackground='#4f46e5', selectforeground='white', relief='flat', bd=0, highlightthickness=0, activestyle='none', height=12)
        self.folder_listbox.pack(side='left', fill='both', expand=True, padx=5, pady=5)
        scrollbar.config(command=self.folder_listbox.yview)

        # --- 버튼 UI 개선 ---
        button_container = tk.Frame(main_frame, bg='white')
        button_container.pack(fill='x', padx=30, pady=(0, 30))
        button_container.columnconfigure([0, 1, 2, 3, 4], weight=1)

        # 버튼 스타일 정의
        button_style = {
            'font': self.button_font,
            'relief': 'flat',
            'bd': 0,
            'cursor': 'hand2',
            'pady': 12,
            'width': 12
        }
        
        # 1. 새로고침
        refresh_button = tk.Button(button_container, text="🔄 새로고침", **button_style,
                                    bg='#e2e8f0', fg='#334155', activebackground='#cbd5e1', activeforeground='#334155',
                                    command=self.load_folders)
        refresh_button.grid(row=0, column=0, sticky='ew', padx=4)

        # 2. 전체 선택
        select_all_button = tk.Button(button_container, text="✅ 전체 선택", **button_style,
                                       bg='#3b82f6', fg='white', activebackground='#2563eb', activeforeground='white',
                                       command=self.select_all_folders)
        select_all_button.grid(row=0, column=1, sticky='ew', padx=4)

        # 3. 선택 해제
        deselect_all_button = tk.Button(button_container, text="❌ 선택 해제", **button_style,
                                         bg='#e2e8f0', fg='#334155', activebackground='#cbd5e1', activeforeground='#334155',
                                         command=self.deselect_all_folders)
        deselect_all_button.grid(row=0, column=2, sticky='ew', padx=4)

        # 4. 전체 스캔
        full_scan_button = tk.Button(button_container, text="💾 전체 스캔", **button_style,
                                     bg='#8b5cf6', fg='white', activebackground='#7c3aed', activeforeground='white',
                                     command=self.select_full_drive)
        full_scan_button.grid(row=0, column=3, sticky='ew', padx=4)

        # 5. 시작하기 (메인 액션)
        confirm_button = tk.Button(button_container, text="🚀 시작하기", **button_style,
                                    bg='#4f46e5', fg='white', activebackground='#4338ca', activeforeground='white',
                                    command=self.confirm_selection)
        confirm_button.grid(row=0, column=4, sticky='ew', padx=4)
        
        status_frame = tk.Frame(main_frame, bg='#f0f9ff', relief='flat', bd=1)
        status_frame.pack(fill='x', padx=30, pady=(20, 30))
        self.status_label = tk.Label(status_frame, text="⏳ 폴더 목록을 불러오는 중...", font=(self.default_font, 11), bg='#f0f9ff', fg='#0369a1', pady=12)
        self.status_label.pack()

    def load_folders(self):
        """[UI 스레드] 폴더 로딩을 시작하고, UI에 로딩 상태를 표시합니다."""
        self.status_label.config(text="⏳ 폴더 목록을 서버에 요청하는 중...")
        self.show_loading_message() # 리스트박스에 로딩 애니메이션 표시

        # 5. UI를 차단하지 않도록 별도의 스레드에서 네트워크 요청 실행
        thread = threading.Thread(target=self.load_folders_in_background, daemon=True)
        thread.start()

    def load_folders_in_background(self):
        """[백그라운드 스레드] 서버에서 폴더 목록을 가져옵니다."""
        try:
            # 6. 타임아웃을 120초로 늘려 서버가 파일을 스캔할 시간을 충분히 줍니다.
            logger.info(f"API 호출 시도: {self.API_BASE_URL}/api/v2/data-collection/folders")
            response = requests.get(f"{self.API_BASE_URL}/api/v2/data-collection/folders", timeout=120)
            
            logger.info(f"API 응답 상태 코드: {response.status_code}")
            if response.status_code == 200:
                result = response.json()
                if result.get("success"):
                    # 성공 결과를 큐에 넣습니다.
                    self.folder_queue.put({'status': 'success', 'data': result.get("folders", [])})
                else:
                    self.folder_queue.put({'status': 'error', 'message': result.get("message", "알 수 없는 서버 오류")})
            else:
                self.folder_queue.put({'status': 'error', 'message': f"서버 응답 오류 (코드: {response.status_code})"})
        except requests.exceptions.RequestException as e:
            logger.error(f"폴더 로딩 중 예외 발생: {e}")
            self.folder_queue.put({'status': 'error', 'message': "서버 연결에 실패했습니다. 백엔드가 실행 중인지 확인하세요."})

    def process_queue(self):
        """[UI 스레드] 큐를 주기적으로 확인하고 UI를 안전하게 업데이트합니다."""
        try:
            message = self.folder_queue.get_nowait() # 큐에서 메시지를 즉시 가져옴 (UI 멈춤 없음)
            
            if message['status'] == 'success':
                folders = message['data']
                self.populate_folder_list(folders)
                if folders:
                    self.status_label.config(text=f"✅ 폴더 목록을 불러왔습니다. ({len(folders)}개 폴더)")
                else:
                    self.status_label.config(text="📂 표시할 폴더가 없습니다.")
            elif message['status'] == 'error':
                self.folder_listbox.delete(0, tk.END) # 로딩 메시지 제거
                self.folder_listbox.insert(tk.END, "❌ 폴더 목록 로딩에 실패했습니다.")
                self.status_label.config(text=f"❌ {message['message']}")

        except queue.Empty:
            # 큐가 비어있으면 아무 작업도 하지 않음
            pass
        finally:
            # 7. 0.1초 후에 다시 큐를 확인하도록 예약 (폴링 방식)
            self.root.after(100, self.process_queue)

    def populate_folder_list(self, folders):
        """폴더 목록을 리스트박스에 채웁니다."""
        # 이 함수는 사용자의 기존 코드와 동일합니다.
        self.folder_listbox.delete(0, tk.END)
        self.folder_data.clear()
        
        if not folders:
            self.folder_listbox.insert(tk.END, "📂 표시할 폴더가 없습니다.")
            return
        
        for folder in sorted(folders, key=lambda x: x.get('name', '').lower()):
            name = folder.get('name', '')
            path = folder.get('path', '')
            
            # 백엔드에서 제공하는 size_formatted 필드 사용
            size_formatted = folder.get('size_formatted', '(0 bytes)')
            
            display_text = f"📁 {name}    {size_formatted}"
            self.folder_listbox.insert(tk.END, display_text)
            self.folder_data.append(path)
    
    def show_loading_message(self):
        """로딩 중 메시지를 표시합니다."""
        # 이 함수는 사용자의 기존 코드와 동일합니다.
        self.folder_listbox.delete(0, tk.END)
        self.folder_data.clear()
        
        loading_messages = [
            "⏳ 폴더를 검색하는 중입니다...",
            "🔍 C:\\Users\\koh\\Desktop 폴더를 스캔하고 있습니다...",
            "📁 폴더 정보를 수집하는 중입니다...",
            "⏳ 잠시만 기다려주세요..."
        ]
        
        self.folder_listbox.insert(tk.END, loading_messages[0])
        self.loading_index = 0
        self.animate_loading()
    
    def animate_loading(self):
        """로딩 메시지 애니메이션"""
        # 이 함수는 사용자의 기존 코드와 동일합니다.
        loading_messages = [
            "⏳ 폴더를 검색하는 중입니다...",
            self.example_scan_path,
            "📁 폴더 정보를 수집하는 중입니다...",
            "⏳ 잠시만 기다려주세요..."
        ]
        
        # 폴더 목록이 아직 로딩되지 않았을 때만 애니메이션 실행
        if self.folder_listbox.size() > 0 and self.folder_listbox.get(0).startswith("⏳"):
            self.folder_listbox.delete(0, tk.END)
            self.folder_listbox.insert(tk.END, loading_messages[self.loading_index])
            self.loading_index = (self.loading_index + 1) % len(loading_messages)
            self.root.after(2000, self.animate_loading)
    
    def select_all_folders(self):
        self.folder_listbox.select_set(0, tk.END)
    
    def deselect_all_folders(self):
        self.folder_listbox.select_clear(0, tk.END)
    
    def select_full_drive(self):
        result = messagebox.askyesno("전체 스캔", f"전체 {self.scan_root_name}를 스캔하시겠습니까?\n시간이 오래 걸릴 수 있습니다.")
        if result:
            self.selected_folders = None
            self.root.destroy()
    
    def confirm_selection(self):
        selected_indices = self.folder_listbox.curselection()
        if not selected_indices:
            result = messagebox.askyesno("전체 스캔", f"폴더를 선택하지 않았습니다.\n전체 {self.scan_root_name}를 스캔하시겠습니까?")
            if result:
                self.selected_folders = None
                self.root.destroy()
        else:
            self.selected_folders = [self.folder_data[i] for i in selected_indices]
            self.root.destroy()
    
    def on_closing(self):
        result = messagebox.askyesno("종료", "폴더 선택을 취소하고 시스템을 종료하시겠습니까?")
        if result:
            self.selected_folders = "cancelled"
            self.root.destroy()
    
    def run(self):
        """UI의 메인 루프를 시작하고, 종료 시 선택된 폴더를 반환합니다."""
        self.root.mainloop()
        
        # 8. Tcl_AsyncDelete 오류 방지를 위해, mainloop 종료 후 창을 확실하게 파괴합니다.
        try:
            if self.root.winfo_exists():
                self.root.destroy()
        except tk.TclError:
            pass # 이미 파괴된 경우 무시
        return self.selected_folders

def select_folders():
    """폴더 선택 UI를 실행하고 선택된 폴더를 반환합니다."""
    import gc
    try:
        app = FolderSelector()
        result = app.run()
        # tkinter 객체 정리
        del app
        # 가비지 컬렉션 강제 수행
        gc.collect()
        return result
    except Exception as e:
        logger.error(f"폴더 선택 UI 오류: {e}")
        return "cancelled"