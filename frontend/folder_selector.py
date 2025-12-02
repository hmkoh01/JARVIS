#!/usr/bin/env python3
"""
Folder Selection UI - File Explorer Style
파일 탐색기 형태의 폴더/파일 선택 UI
"""

import tkinter as tk
from tkinter import ttk, messagebox
import threading
import logging
import queue
import platform
import os
from pathlib import Path

# Theme 임포트
from theme import COLORS, style_button

# 로깅 설정
logging.basicConfig(level=logging.INFO, format='%(asctime)s:%(levelname)s:%(name)s:%(message)s')
logger = logging.getLogger(__name__)


class DirectoryScanner(threading.Thread):
    """백그라운드에서 디렉터리 내용을 스캔하는 스레드"""
    
    def __init__(self, request_queue: queue.Queue, response_queue: queue.Queue):
        super().__init__(daemon=True)
        self.request_queue = request_queue
        self.response_queue = response_queue
        self.running = True
    
    def run(self):
        while self.running:
            try:
                # 요청 대기 (0.1초 타임아웃)
                command = self.request_queue.get(timeout=0.1)
                
                if command is None:
                    # 종료 신호
                    break
                
                cmd_type, target_path = command
                
                if cmd_type == "LIST_DIR":
                    self._list_directory(target_path)
                    
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"DirectoryScanner error: {e}")
    
    def _list_directory(self, target_path: Path):
        """디렉터리 내용을 스캔하고 결과를 응답 큐에 넣습니다."""
        try:
            entries = []
            
            with os.scandir(target_path) as scanner:
                for entry in scanner:
                    try:
                        is_dir = entry.is_dir()
                        # 숨김 파일/폴더 제외 (선택적)
                        name = entry.name
                        if name.startswith('.'):
                            continue
                        
                        entries.append({
                            'name': name,
                            'path': Path(entry.path),
                            'is_dir': is_dir
                        })
                    except (PermissionError, OSError):
                        # 개별 항목 접근 오류는 무시
                        continue
            
            # 정렬: 폴더 먼저, 그 다음 파일 (알파벳순)
            entries.sort(key=lambda x: (not x['is_dir'], x['name'].lower()))
            
            self.response_queue.put({
                'status': 'success',
                'type': 'DIR_LIST',
                'path': target_path,
                'entries': entries
            })
            
        except PermissionError:
            self.response_queue.put({
                'status': 'error',
                'type': 'PERMISSION_ERROR',
                'path': target_path,
                'message': f"접근 권한이 없습니다: {target_path}"
            })
        except Exception as e:
            self.response_queue.put({
                'status': 'error',
                'type': 'ERROR',
                'path': target_path,
                'message': f"오류 발생: {str(e)}"
            })
    
    def stop(self):
        self.running = False
        self.request_queue.put(None)


class FolderSelector:
    def __init__(self, initial_selections: list = None):
        """
        폴더/파일 선택 UI
        
        Args:
            initial_selections: 초기 선택 항목 리스트 (문자열 경로 또는 Path 객체)
        """
        self.root = tk.Tk()
        self.root.title("JARVIS - 폴더/파일 선택")
        self.root.configure(bg=COLORS["panel_bg"])
        self.root.resizable(True, True)
        self.root.minsize(1000, 700)
        
        # OS-specific settings
        self.platform = platform.system()
        
        # 현재 탐색 경로
        self.current_path = Path.home()
        
        # 선택된 항목들 (set of Path objects)
        self.selected_items = set()
        
        # 초기 선택 항목 설정
        if initial_selections:
            for item in initial_selections:
                path = Path(item) if isinstance(item, str) else item
                if path.exists():
                    self.selected_items.add(path)
        
        # 히스토리 스택 (뒤로 가기용)
        self.history_stack = []
        
        # 현재 디렉터리 항목들 (path -> entry info)
        self.current_entries = {}
        
        # 더블클릭 감지를 위한 타이머
        self.click_timer = None
        self.last_clicked_index = None
        
        # 스레드 통신 큐
        self.request_queue = queue.Queue()
        self.response_queue = queue.Queue()
        
        # 큐 폴링 타이머 ID (창 닫힐 때 취소용)
        self._queue_poll_id = None
        
        # 창이 닫혔는지 여부
        self._is_closing = False
        
        # 백그라운드 스캐너 시작
        self.scanner = DirectoryScanner(self.request_queue, self.response_queue)
        self.scanner.start()
        
        # 창 설정
        self.center_window()
        self.setup_korean_fonts()
        
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.create_ui()
        
        # 초기 선택 항목이 있으면 오른쪽 패널 업데이트
        if self.selected_items:
            self.root.after(150, self._update_selected_listbox)
        
        # 초기 디렉터리 로드
        self.root.after(100, self.navigate_to, self.current_path)
        
        # 큐 폴링 시작
        self.process_queue()
    
    def center_window(self):
        """창을 화면 중앙에 배치"""
        window_width = 1100
        window_height = 750
        
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        
        x = max(0, int((screen_width - window_width) / 2))
        y = max(0, int((screen_height - window_height) / 2))
        
        self.root.geometry(f'{window_width}x{window_height}+{x}+{y}')
        self.root.lift()
        self.root.attributes('-topmost', True)
        self.root.after_idle(lambda: self.root.attributes('-topmost', False))
    
    def setup_korean_fonts(self):
        """한글 폰트를 설정합니다."""
        if self.platform == "Darwin":
            korean_fonts = ['Apple SD Gothic Neo', 'AppleGothic', 'Nanum Gothic', 
                          'Helvetica Neue', 'Lucida Grande', 'Arial Unicode MS']
        else:
            korean_fonts = ['Malgun Gothic', 'Nanum Gothic', 'Nanum Barun Gothic',
                          'Dotum', 'Gulim', 'Batang', 'Arial Unicode MS']
        
        self.default_font = 'Arial'
        for font in korean_fonts:
            try:
                test_label = tk.Label(self.root, font=(font, 12))
                test_label.destroy()
                self.default_font = font
                break
            except:
                continue
        
        self.title_font = (self.default_font, 18, 'bold')
        self.subtitle_font = (self.default_font, 12)
        self.list_font = (self.default_font, 11)
        self.button_font = (self.default_font, 10, 'bold')
        self.breadcrumb_font = (self.default_font, 10)
    
    def create_ui(self):
        """UI를 생성합니다."""
        main_container = tk.Frame(self.root, bg=COLORS["panel_bg"])
        main_container.pack(fill='both', expand=True, padx=15, pady=15)
        
        # ==================== 헤더 영역 ====================
        header_frame = tk.Frame(main_container, bg=COLORS["surface"])
        header_frame.pack(fill='x', pady=(0, 10))
        
        # 타이틀
        title_row = tk.Frame(header_frame, bg=COLORS["surface"])
        title_row.pack(fill='x', padx=15, pady=10)
        
        icon_label = tk.Label(title_row, text="📂", font=('Arial', 24), 
                             bg=COLORS["surface"], fg=COLORS["primary"])
        icon_label.pack(side='left', padx=(0, 10))
        
        title_label = tk.Label(title_row, text="JARVIS 파일 탐색기", 
                              font=self.title_font, bg=COLORS["surface"], 
                              fg=COLORS["text_primary"])
        title_label.pack(side='left')
        
        desc_label = tk.Label(title_row, text="   수집할 폴더와 파일을 선택하세요", 
                             font=self.subtitle_font, bg=COLORS["surface"], 
                             fg=COLORS["text_muted"])
        desc_label.pack(side='left')
        
        # ==================== 네비게이션 영역 ====================
        nav_frame = tk.Frame(main_container, bg=COLORS["surface"])
        nav_frame.pack(fill='x', pady=(0, 10))
        
        nav_inner = tk.Frame(nav_frame, bg=COLORS["surface"])
        nav_inner.pack(fill='x', padx=10, pady=8)
        
        # 상위 폴더 버튼
        self.up_button = tk.Button(nav_inner, text="⬆ 상위", font=self.button_font,
                                   command=self.go_to_parent, width=8)
        style_button(self.up_button, variant="secondary")
        self.up_button.pack(side='left', padx=(0, 10))
        
        # 뒤로 버튼
        self.back_button = tk.Button(nav_inner, text="◀ 뒤로", font=self.button_font,
                                     command=self.go_back, width=8)
        style_button(self.back_button, variant="secondary")
        self.back_button.pack(side='left', padx=(0, 10))
        
        # 홈 버튼
        self.home_button = tk.Button(nav_inner, text="🏠 홈", font=self.button_font,
                                     command=self.go_home, width=8)
        style_button(self.home_button, variant="secondary")
        self.home_button.pack(side='left', padx=(0, 15))
        
        # 구분선
        sep = tk.Frame(nav_inner, width=2, bg=COLORS["border"])
        sep.pack(side='left', fill='y', padx=(0, 15), pady=2)
        
        # 브레드크럼 컨테이너 (가로 스크롤 가능)
        self.breadcrumb_canvas = tk.Canvas(nav_inner, height=30, bg=COLORS["surface"],
                                           highlightthickness=0)
        self.breadcrumb_canvas.pack(side='left', fill='x', expand=True)
        
        self.breadcrumb_frame = tk.Frame(self.breadcrumb_canvas, bg=COLORS["surface"])
        self.breadcrumb_canvas.create_window((0, 0), window=self.breadcrumb_frame, anchor='w')
        
        # 브레드크럼 프레임 크기 변경 시 캔버스 스크롤 영역 업데이트
        self.breadcrumb_frame.bind('<Configure>', self._on_breadcrumb_configure)
        
        # ==================== 메인 콘텐츠 영역 (PanedWindow) ====================
        content_frame = tk.Frame(main_container, bg=COLORS["surface"])
        content_frame.pack(fill='both', expand=True, pady=(0, 10))
        
        # PanedWindow로 좌/우 패널 분할
        self.paned = tk.PanedWindow(content_frame, orient='horizontal', 
                                    bg=COLORS["border"], sashwidth=6,
                                    sashrelief='flat')
        self.paned.pack(fill='both', expand=True, padx=10, pady=10)
        
        # ===== 왼쪽 패널: 파일 탐색기 =====
        left_panel = tk.Frame(self.paned, bg=COLORS["surface"])
        
        left_header = tk.Frame(left_panel, bg=COLORS["panel_bg"])
        left_header.pack(fill='x')
        
        left_title = tk.Label(left_header, text="📁 현재 폴더 내용", 
                             font=(self.default_font, 12, 'bold'),
                             bg=COLORS["panel_bg"], fg=COLORS["text_secondary"], 
                             pady=8, padx=10)
        left_title.pack(side='left')
        
        # 항목 수 표시
        self.item_count_label = tk.Label(left_header, text="", 
                                        font=self.subtitle_font,
                                        bg=COLORS["panel_bg"], fg=COLORS["text_muted"], 
                                        pady=8, padx=10)
        self.item_count_label.pack(side='right')
        
        # 파일 리스트
        list_frame = tk.Frame(left_panel, bg=COLORS["surface"])
        list_frame.pack(fill='both', expand=True)
        
        scrollbar = ttk.Scrollbar(list_frame, orient='vertical')
        scrollbar.pack(side='right', fill='y')
        
        self.explorer_listbox = tk.Listbox(
            list_frame, 
            font=self.list_font,
            selectmode='extended',  # 다중 선택 지원
            yscrollcommand=scrollbar.set,
            bg=COLORS["surface"],
            fg=COLORS["text_primary"],
            selectbackground=COLORS["primary"],
            selectforeground=COLORS["text_inverse"],
            relief='flat',
            bd=0,
            highlightthickness=1,
            highlightbackground=COLORS["border"],
            activestyle='none',
            height=20
        )
        self.explorer_listbox.pack(side='left', fill='both', expand=True)
        scrollbar.config(command=self.explorer_listbox.yview)
        
        # 이벤트 바인딩
        self.explorer_listbox.bind('<ButtonRelease-1>', self._on_single_click)
        self.explorer_listbox.bind('<Double-Button-1>', self._on_double_click)
        
        self.paned.add(left_panel, minsize=400, width=550)
        
        # ===== 중앙 버튼 영역 =====
        center_buttons = tk.Frame(self.paned, bg=COLORS["surface"], width=80)
        
        btn_container = tk.Frame(center_buttons, bg=COLORS["surface"])
        btn_container.place(relx=0.5, rely=0.5, anchor='center')
        
        add_btn = tk.Button(btn_container, text="▶ 추가", font=self.button_font,
                            command=self._add_selected_to_basket, width=10)
        style_button(add_btn, variant="outlined")
        add_btn.pack(pady=5)
        
        remove_btn = tk.Button(btn_container, text="◀ 제거", font=self.button_font,
                              command=self._remove_from_basket, width=10)
        style_button(remove_btn, variant="ghost")
        remove_btn.pack(pady=5)
        
        self.paned.add(center_buttons, minsize=80, width=90)
        
        # ===== 오른쪽 패널: 선택된 항목 =====
        right_panel = tk.Frame(self.paned, bg=COLORS["surface"])
        
        right_header = tk.Frame(right_panel, bg=COLORS["primary_soft"])
        right_header.pack(fill='x')
        
        right_title = tk.Label(right_header, text="✅ 선택된 항목", 
                              font=(self.default_font, 12, 'bold'),
                              bg=COLORS["primary_soft"], fg=COLORS["primary"], 
                              pady=8, padx=10)
        right_title.pack(side='left')
        
        # 선택된 항목 수
        self.selected_count_label = tk.Label(right_header, text="0개", 
                                            font=self.subtitle_font,
                                            bg=COLORS["primary_soft"], fg=COLORS["primary"], 
                                            pady=8, padx=10)
        self.selected_count_label.pack(side='right')
        
        # 선택된 항목 리스트
        selected_frame = tk.Frame(right_panel, bg=COLORS["surface"])
        selected_frame.pack(fill='both', expand=True)
        
        selected_scrollbar = ttk.Scrollbar(selected_frame, orient='vertical')
        selected_scrollbar.pack(side='right', fill='y')
        
        self.selected_listbox = tk.Listbox(
            selected_frame,
            font=self.list_font,
            selectmode='extended',
            yscrollcommand=selected_scrollbar.set,
            bg=COLORS["surface"],
            fg=COLORS["text_primary"],
            selectbackground=COLORS["danger_bg"],
            selectforeground=COLORS["danger_text"],
            relief='flat',
            bd=0,
            highlightthickness=1,
            highlightbackground=COLORS["border"],
            activestyle='none',
            height=20
        )
        self.selected_listbox.pack(side='left', fill='both', expand=True)
        selected_scrollbar.config(command=self.selected_listbox.yview)
        
        # 선택 패널 더블클릭으로 제거
        self.selected_listbox.bind('<Double-Button-1>', self._on_basket_double_click)
        
        self.paned.add(right_panel, minsize=300, width=400)
        
        # ==================== 하단 버튼 영역 ====================
        bottom_frame = tk.Frame(main_container, bg=COLORS["surface"])
        bottom_frame.pack(fill='x')
        
        bottom_inner = tk.Frame(bottom_frame, bg=COLORS["surface"])
        bottom_inner.pack(fill='x', padx=15, pady=15)
        
        # 왼쪽: 상태 메시지
        self.status_label = tk.Label(bottom_inner, text="📂 폴더를 탐색하세요", 
                                    font=self.subtitle_font, 
                                    bg=COLORS["surface"], fg=COLORS["text_muted"])
        self.status_label.pack(side='left')
        
        # 오른쪽: 액션 버튼들
        btn_frame = tk.Frame(bottom_inner, bg=COLORS["surface"])
        btn_frame.pack(side='right')
        
        btn_style = {'font': self.button_font, 'width': 12}
        
        # 새로고침
        refresh_btn = tk.Button(btn_frame, text="🔄 새로고침", **btn_style,
                               command=self.refresh_current)
        style_button(refresh_btn, variant="secondary")
        refresh_btn.pack(side='left', padx=3)
        
        # 전체 선택 (현재 폴더)
        select_all_btn = tk.Button(btn_frame, text="✅ 전체 추가", **btn_style,
                                  command=self._add_all_to_basket)
        style_button(select_all_btn, variant="secondary")
        select_all_btn.pack(side='left', padx=3)
        
        # 선택 초기화
        clear_btn = tk.Button(btn_frame, text="🗑 선택 초기화", **btn_style,
                             command=self.clear_selection)
        style_button(clear_btn, variant="secondary")
        clear_btn.pack(side='left', padx=3)
        
        # 전체 스캔 (홈 폴더)
        full_scan_btn = tk.Button(btn_frame, text="💾 전체 스캔", **btn_style,
                                 command=self.select_full_home)
        style_button(full_scan_btn, variant="secondary")
        full_scan_btn.pack(side='left', padx=3)
        
        # 시작하기 (확정)
        start_btn = tk.Button(btn_frame, text="🚀 시작하기", **btn_style,
                             command=self.confirm_selection)
        style_button(start_btn, variant="secondary")
        start_btn.pack(side='left', padx=3)
    
    def _on_breadcrumb_configure(self, event):
        """브레드크럼 프레임 크기 변경 시 스크롤 영역 업데이트"""
        self.breadcrumb_canvas.configure(scrollregion=self.breadcrumb_canvas.bbox('all'))
    
    def _update_breadcrumb(self):
        """현재 경로에 맞게 브레드크럼 버튼들을 업데이트"""
        # 기존 버튼들 제거
        for widget in self.breadcrumb_frame.winfo_children():
            widget.destroy()
        
        # 경로 파트 추출
        parts = self.current_path.parts
        
        # Windows의 경우 드라이브 문자 처리
        for i, part in enumerate(parts):
            # 경로 재구성
            if i == 0:
                target_path = Path(part)
                if self.platform == "Windows":
                    target_path = Path(part + "\\")
            else:
                target_path = Path(*parts[:i+1])
            
            # 버튼 생성
            display_name = part
            if display_name.endswith(('\\', '/')):
                display_name = display_name.rstrip('\\/')
            if not display_name:
                display_name = "/"
            
            btn = tk.Button(
                self.breadcrumb_frame, 
                text=display_name,
                font=self.breadcrumb_font,
                command=lambda p=target_path: self.navigate_to(p),
                relief='flat',
                bd=0,
                bg=COLORS["surface"],
                fg=COLORS["primary"],
                activebackground=COLORS["primary_soft"],
                activeforeground=COLORS["primary_dark"],
                cursor='hand2',
                padx=5,
                pady=2
            )
            btn.pack(side='left')
            
            # 구분자 (마지막 제외)
            if i < len(parts) - 1:
                sep_label = tk.Label(self.breadcrumb_frame, text=" › ",
                                    font=self.breadcrumb_font,
                                    bg=COLORS["surface"], fg=COLORS["text_muted"])
                sep_label.pack(side='left')
    
    def navigate_to(self, path: Path, add_to_history: bool = True):
        """지정된 경로로 이동"""
        path = Path(path)
        
        if not path.exists():
            messagebox.showerror("오류", f"경로가 존재하지 않습니다:\n{path}")
            return
        
        if not path.is_dir():
            messagebox.showinfo("알림", "파일은 열 수 없습니다. 폴더만 탐색할 수 있습니다.")
            return
        
        # 히스토리에 현재 경로 추가 (이전 경로가 다를 경우만)
        if add_to_history and self.current_path != path:
            self.history_stack.append(self.current_path)
            # 히스토리 크기 제한
            if len(self.history_stack) > 50:
                self.history_stack.pop(0)
        
        self.current_path = path
        
        # 브레드크럼 업데이트
        self._update_breadcrumb()
        
        # 로딩 상태 표시
        self.explorer_listbox.delete(0, tk.END)
        self.explorer_listbox.insert(tk.END, "⏳ 로딩 중...")
        self.status_label.config(text=f"📂 {path}")
        
        # 백그라운드에서 디렉터리 스캔 요청
        self.request_queue.put(("LIST_DIR", path))
    
    def go_to_parent(self):
        """상위 폴더로 이동"""
        parent = self.current_path.parent
        if parent != self.current_path:
            self.navigate_to(parent)
    
    def go_back(self):
        """히스토리에서 이전 경로로 이동"""
        if self.history_stack:
            prev_path = self.history_stack.pop()
            self.navigate_to(prev_path, add_to_history=False)
    
    def go_home(self):
        """홈 폴더로 이동"""
        self.navigate_to(Path.home())
    
    def refresh_current(self):
        """현재 폴더 새로고침"""
        self.navigate_to(self.current_path, add_to_history=False)

    def process_queue(self):
        """큐를 주기적으로 확인하고 UI를 업데이트"""
        # 창이 닫히는 중이면 더 이상 폴링하지 않음
        if self._is_closing:
            return
        
        try:
            message = self.response_queue.get_nowait()
            
            if message['status'] == 'success' and message['type'] == 'DIR_LIST':
                # 경로가 현재 경로와 일치하는지 확인 (오래된 응답 무시)
                if message['path'] == self.current_path:
                    self._populate_explorer(message['entries'])
                    
            elif message['status'] == 'error':
                self.explorer_listbox.delete(0, tk.END)
                self.explorer_listbox.insert(tk.END, f"❌ {message['message']}")
                self.status_label.config(text=f"⚠️ {message['message']}")

        except queue.Empty:
            pass
        finally:
            # 창이 닫히지 않았을 때만 다음 폴링 예약
            if not self._is_closing:
                try:
                    self._queue_poll_id = self.root.after(50, self.process_queue)
                except tk.TclError:
                    # 창이 이미 파괴된 경우
                    pass
    
    def _populate_explorer(self, entries: list):
        """탐색기 리스트박스에 항목들을 채움"""
        self.explorer_listbox.delete(0, tk.END)
        self.current_entries.clear()
        
        if not entries:
            self.explorer_listbox.insert(tk.END, "📂 (빈 폴더)")
            self.item_count_label.config(text="0개 항목")
            return
        
        for entry in entries:
            icon = "📁 " if entry['is_dir'] else "📄 "
            display_text = f"{icon}{entry['name']}"
            
            self.explorer_listbox.insert(tk.END, display_text)
            
            # 인덱스로 경로 매핑 저장
            idx = self.explorer_listbox.size() - 1
            self.current_entries[idx] = entry
        
        folder_count = sum(1 for e in entries if e['is_dir'])
        file_count = len(entries) - folder_count
        self.item_count_label.config(text=f"📁 {folder_count}개 폴더, 📄 {file_count}개 파일")
        self.status_label.config(text=f"✅ {self.current_path}")
    
    def _on_single_click(self, event):
        """단일 클릭 처리 - 더블클릭과 구분하기 위해 딜레이 사용"""
        # 현재 클릭된 항목
        selection = self.explorer_listbox.curselection()
        if not selection:
            return
        
        clicked_index = self.explorer_listbox.nearest(event.y)
        
        # 기존 타이머가 있으면 취소 (더블클릭 감지용)
        if self.click_timer:
            self.root.after_cancel(self.click_timer)
        
        # 동일 항목 더블클릭 감지를 위해 인덱스 저장
        self.last_clicked_index = clicked_index
        
        # 250ms 후에 실제 단일 클릭 처리 (더블클릭이 아닐 경우)
        self.click_timer = self.root.after(250, self._process_single_click, clicked_index)
    
    def _process_single_click(self, index: int):
        """실제 단일 클릭 처리 (선택 토글)"""
        self.click_timer = None
        
        if index not in self.current_entries:
            return
        
        entry = self.current_entries[index]
        path = entry['path']
        
        # 선택 토글
        if path in self.selected_items:
            self.selected_items.discard(path)
        else:
            self.selected_items.add(path)
        
        self._update_selected_listbox()
    
    def _on_double_click(self, event):
        """더블 클릭 처리 - 폴더 진입"""
        # 단일 클릭 타이머 취소
        if self.click_timer:
            self.root.after_cancel(self.click_timer)
            self.click_timer = None
        
        selection = self.explorer_listbox.curselection()
        if not selection:
            return
        
        clicked_index = self.explorer_listbox.nearest(event.y)
        
        if clicked_index not in self.current_entries:
            return
        
        entry = self.current_entries[clicked_index]
        
        if entry['is_dir']:
            # 폴더면 해당 폴더로 이동
            self.navigate_to(entry['path'])
        # 파일이면 아무것도 안 함
    
    def _add_selected_to_basket(self):
        """탐색기에서 선택된 항목들을 선택 바구니에 추가"""
        selection = self.explorer_listbox.curselection()
        
        for idx in selection:
            if idx in self.current_entries:
                path = self.current_entries[idx]['path']
                self.selected_items.add(path)
        
        self._update_selected_listbox()
    
    def _add_all_to_basket(self):
        """현재 폴더의 모든 항목을 선택 바구니에 추가"""
        for idx, entry in self.current_entries.items():
            self.selected_items.add(entry['path'])
        
        self._update_selected_listbox()
    
    def _remove_from_basket(self):
        """선택 바구니에서 선택된 항목 제거"""
        selection = self.selected_listbox.curselection()
        
        # 선택된 항목의 경로들 수집
        paths_to_remove = []
        items_list = list(self.selected_items)
        
        for idx in selection:
            if 0 <= idx < len(items_list):
                paths_to_remove.append(items_list[idx])
        
        for path in paths_to_remove:
            self.selected_items.discard(path)
        
        self._update_selected_listbox()
    
    def _on_basket_double_click(self, event):
        """선택 바구니에서 더블클릭 시 해당 항목 제거"""
        selection = self.selected_listbox.curselection()
        if not selection:
            return
        
        clicked_index = self.selected_listbox.nearest(event.y)
        items_list = list(self.selected_items)
        
        if 0 <= clicked_index < len(items_list):
            self.selected_items.discard(items_list[clicked_index])
            self._update_selected_listbox()
    
    def _update_selected_listbox(self):
        """선택된 항목 리스트박스 업데이트"""
        self.selected_listbox.delete(0, tk.END)
        
        # 정렬해서 표시 (폴더 먼저, 그 다음 파일)
        sorted_items = sorted(self.selected_items, 
                             key=lambda p: (not p.is_dir() if p.exists() else True, str(p).lower()))
        
        for path in sorted_items:
            if path.exists():
                icon = "📁 " if path.is_dir() else "📄 "
            else:
                icon = "❓ "
            
            # 경로를 좀 더 짧게 표시 (홈 상대 경로)
            try:
                display_path = path.relative_to(Path.home())
                display_text = f"{icon}~/{display_path}"
            except ValueError:
                display_text = f"{icon}{path}"
            
            self.selected_listbox.insert(tk.END, display_text)
        
        self.selected_count_label.config(text=f"{len(self.selected_items)}개")
    
    def clear_selection(self):
        """모든 선택 초기화"""
        self.selected_items.clear()
        self._update_selected_listbox()
        self.status_label.config(text="🗑 선택이 초기화되었습니다")
    
    def select_full_home(self):
        """전체 홈 폴더 스캔 선택"""
        result = messagebox.askyesno(
            "전체 스캔",
            f"전체 홈 폴더를 스캔하시겠습니까?\n{Path.home()}\n\n시간이 오래 걸릴 수 있습니다."
        )
        if result:
            self.selected_items.clear()
            self.selected_items.add(Path.home())
            self._update_selected_listbox()
    
    def confirm_selection(self):
        """선택 확정"""
        if not self.selected_items:
            result = messagebox.askyesno(
                "선택 없음",
                "선택된 항목이 없습니다.\n전체 홈 폴더를 스캔하시겠습니까?"
            )
            if result:
                self.selected_items.add(Path.home())
        else:
                return
        
        # 정리 후 창 닫기 (선택 결과 유지)
        self._cleanup_and_close()
    
    def on_closing(self):
        """창 닫기 처리"""
        result = messagebox.askyesno("종료", "폴더 선택을 취소하시겠습니까?")
        if result:
            self._cleanup_and_close("cancelled")
    
    def _cleanup_and_close(self, result_value=None):
        """리소스 정리 및 창 닫기"""
        # 닫힘 플래그 설정 (process_queue 중단)
        self._is_closing = True
        
        # 예약된 after 콜백 취소
        if self._queue_poll_id:
            try:
                self.root.after_cancel(self._queue_poll_id)
            except tk.TclError:
                pass
            self._queue_poll_id = None
        
        if self.click_timer:
            try:
                self.root.after_cancel(self.click_timer)
            except tk.TclError:
                pass
            self.click_timer = None
        
        # 결과 설정
        if result_value is not None:
            self.selected_items = result_value
        
        # 스캐너 중지
        self.scanner.stop()
        
        # 창 파괴
        try:
            self.root.destroy()
        except tk.TclError:
            pass
    
    def run(self):
        """UI 메인 루프 실행"""
        self.root.mainloop()
        
        # 메인 루프 종료 후 정리 (아직 정리되지 않은 경우)
        if not self._is_closing:
            self._is_closing = True
            self.scanner.stop()
        
        try:
            if self.root.winfo_exists():
                self.root.destroy()
        except tk.TclError:
            pass
        
        # 결과 반환
        if self.selected_items == "cancelled":
            return "cancelled"
        elif not self.selected_items:
            return None
        else:
            # Path 객체를 문자열로 변환
            return [str(p) for p in self.selected_items]


def select_folders(initial_selections: list = None):
    """
    폴더 선택 UI를 실행하고 선택된 폴더를 반환합니다.
    
    Args:
        initial_selections: 초기 선택 항목 리스트 (문자열 경로)
                           폴더 변경 시 기존 선택을 보여주기 위해 사용
    
    Returns:
        - 선택된 폴더/파일 경로 리스트
        - None: 아무것도 선택하지 않음 (전체 스캔)
        - "cancelled": 사용자가 취소함
    """
    import gc
    try:
        app = FolderSelector(initial_selections=initial_selections)
        result = app.run()
        del app
        gc.collect()
        return result
    except Exception as e:
        logger.error(f"폴더 선택 UI 오류: {e}")
        return "cancelled"
