#!/usr/bin/env python3
"""
Desktop Floating Chat Application
현재 화면에 플로팅 채팅 버튼을 추가하는 데스크톱 애플리케이션
"""

import tkinter as tk
from tkinter import ttk, scrolledtext
import requests
import json
import threading
import queue
from datetime import datetime
import os
import platform

class FloatingChatApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("JARVIS Floating Chat")
        
        # 한글 폰트 설정
        self.setup_korean_fonts()
        
        # API 설정
        self.API_BASE_URL = "http://localhost:8000"
        
        # 채팅 히스토리
        self.chat_history = []
        
        # 드래그 관련 변수
        self.drag_data = {"x": 0, "y": 0, "dragging": False}
        
        # 스레드 안전한 큐 시스템
        self.message_queue = queue.Queue()
        
        # 플로팅 버튼 생성
        self.create_floating_button()
        
        # 채팅창 생성 (초기에는 숨김)
        self.create_chat_window()
        
        # 항상 최상단에 표시
        self.root.attributes('-topmost', True)
        
        # ESC 키로 채팅창 닫기
        self.root.bind('<Escape>', self.close_chat_window)
        
        # Ctrl+C로 복사 기능 (채팅창에서)
        self.root.bind('<Control-c>', self.copy_selected_text)
        
        # 큐 처리 시작
        self.process_message_queue()

        # 추천 알림을 위한 변수
        self.recommendation_notification_visible = False

        # 추천 알림 확인 시작
        self.check_for_recommendations()
    
    def setup_korean_fonts(self):
        """한글 폰트를 설정합니다."""
        # Windows에서 사용 가능한 한글 폰트들
        korean_fonts = [
            'Malgun Gothic',  # 맑은 고딕 (Windows 기본)
            'Nanum Gothic',   # 나눔고딕
            'Nanum Barun Gothic',  # 나눔바른고딕
            'Dotum',          # 돋움
            'Gulim',          # 굴림
            'Batang',         # 바탕
            'Arial Unicode MS'  # Arial Unicode MS
        ]
        
        # 사용 가능한 폰트 찾기
        self.default_font = 'Arial'  # 기본값
        for font in korean_fonts:
            try:
                # 폰트 존재 여부 확인
                test_label = tk.Label(self.root, font=(font, 12))
                test_label.destroy()
                self.default_font = font
                print(f"한글 폰트 설정: {font}")
                break
            except:
                continue
        
        # 폰트 크기 설정
        self.title_font = (self.default_font, 18, 'bold')
        self.subtitle_font = (self.default_font, 12)
        self.message_font = (self.default_font, 12)
        self.input_font = (self.default_font, 14)
        self.button_font = (self.default_font, 12, 'bold')
        self.emoji_font = (self.default_font, 22)
    
    def process_message_queue(self):
        """메시지 큐를 처리합니다. - 메인 스레드에서만 GUI 업데이트"""
        try:
            while True:
                try:
                    # 큐에서 메시지 가져오기 (논블로킹)
                    message = self.message_queue.get_nowait()
                    
                    if message['type'] == 'api_request':
                        # 백그라운드 스레드에서 API 처리
                        threading.Thread(
                            target=self.process_api_request,
                            args=(message['message'], message['loading_widget']),
                            daemon=True
                        ).start()
                        
                    elif message['type'] == 'bot_response':
                        # 봇 응답 처리
                        self.handle_bot_response(message['response'], message['loading_widget'])
                        
                    elif message['type'] == 'update_loading':
                        # 로딩 메시지 업데이트
                        self.update_loading_message(message['loading_widget'], message['message'])
                        
                    elif message['type'] == 'show_recommendation':
                        # 추천 알림 표시
                        self.show_recommendation_notification(message['recommendations'])
                        
                except queue.Empty:
                    break
                    
        except Exception as e:
            print(f"큐 처리 중 오류: {e}")
        finally:
            # 100ms 후에 다시 큐 확인
            try:
                self.root.after(100, self.process_message_queue)
            except tk.TclError:
                # 윈도우가 파괴된 경우 중지
                return
        
    def create_floating_button(self):
        """플로팅 버튼 생성"""
        # 메인 윈도우를 완전히 투명하게
        self.root.configure(bg='black')

        system = platform.system()
        if system == "Darwin": # macOS
            self.root.wm_attributes('-transparent', True)
        else: # Windows
            self.root.wm_attributes('-transparentcolor', 'black')

        # 윈도우 테두리와 제목 표시줄 제거
        self.root.overrideredirect(True)
        
        # 윈도우 크기를 버튼 크기로 설정 (더 크게)
        self.root.geometry('70x70')
        
        # 화면 우측 하단에 위치
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        x = screen_width - 100
        y = screen_height - 150
        self.root.geometry(f'70x70+{x}+{y}')
        
        # 동그란 버튼을 위한 캔버스 생성
        self.button_canvas = tk.Canvas(
            self.root,
            width=70,
            height=70,
            bg='black',
            highlightthickness=0,
            relief='flat'
        )
        self.button_canvas.pack(fill='both', expand=True)
        
        # 동그란 버튼 그리기 (더 크게)
        self.button_canvas.create_oval(
            3, 3, 67, 67,
            fill='#4f46e5',
            outline='#4f46e5',
            tags='button'
        )
        
        # 이모지 텍스트 추가 (더 크게)
        self.button_canvas.create_text(
            35, 35,
            text="💬",
            font=self.emoji_font,
            fill='white',
            tags='text'
        )
        
        # 클릭 이벤트 바인딩
        self.button_canvas.bind('<Button-1>', self.on_button_click)
        self.button_canvas.bind('<B1-Motion>', self.on_drag)
        self.button_canvas.bind('<ButtonRelease-1>', self.stop_drag)
        
        # 우클릭 메뉴 이벤트 바인딩
        self.button_canvas.bind('<Button-3>', self.show_context_menu)
        
        # 호버 효과
        self.button_canvas.bind('<Enter>', self.on_hover)
        self.button_canvas.bind('<Leave>', self.on_leave)
        
    def on_button_click(self, event):
        """버튼 클릭 이벤트"""
        self.drag_data["x"] = event.x
        self.drag_data["y"] = event.y
        self.drag_data["dragging"] = False
        # 클릭 시 즉시 토글 실행하지 않고, 드래그 여부를 확인 후 실행
        
    def on_hover(self, event):
        """호버 효과"""
        self.button_canvas.itemconfig('button', fill='#4338ca')
        
    def on_leave(self, event):
        """호버 해제"""
        self.button_canvas.itemconfig('button', fill='#4f46e5')
        
    def on_drag(self, event):
        """드래그 중"""
        # 드래그 시작 시 dragging 플래그 설정
        if not self.drag_data["dragging"]:
            self.drag_data["dragging"] = True
            return
            
        # 마우스 커서를 정확히 따라가도록 수정
        # 현재 마우스 위치를 기준으로 윈도우 위치 계산
        mouse_x = self.root.winfo_pointerx()
        mouse_y = self.root.winfo_pointery()
        
        # 버튼 중앙이 마우스 커서 위치가 되도록 조정
        x = mouse_x - 35  # 버튼 중앙 (70/2)
        y = mouse_y - 35  # 버튼 중앙 (70/2)
        
        # 화면 경계 확인
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        
        if x < 0:
            x = 0
        elif x > screen_width - 70:
            x = screen_width - 70
            
        if y < 0:
            y = 0
        elif y > screen_height - 70:
            y = screen_height - 70
        
        self.root.geometry(f'70x70+{x}+{y}')
        
        # 드래그 데이터 업데이트
        self.drag_data["x"] = event.x
        self.drag_data["y"] = event.y
        
    def stop_drag(self, event):
        """드래그 종료"""
        # 드래그가 아니었다면 클릭으로 간주하여 채팅창 토글
        if not self.drag_data["dragging"]:
            self.toggle_chat_window()
        self.drag_data["dragging"] = False
        
    def show_context_menu(self, event):
        """우클릭 컨텍스트 메뉴 표시"""
        # 팝업 메뉴 생성
        context_menu = tk.Menu(self.root, tearoff=0)
        context_menu.add_command(label="시스템 종료", command=self.quit_system)
        
        # 메뉴를 마우스 위치에 표시
        try:
            context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            context_menu.grab_release()
            
    def quit_system(self):
        """시스템 종료"""
        # 종료 확인
        import tkinter.messagebox as messagebox
        result = messagebox.askyesno("시스템 종료", "정말로 JARVIS를 종료하시겠습니까?")
        if result:
            # 프로그램 완전 종료
            self.root.quit()
            self.root.destroy()
            import sys
            sys.exit(0)
        
    def create_chat_window(self):
        """채팅창 생성"""
        # 채팅창 윈도우 (헤더 높이 증가에 맞춰 높이 조정)
        self.chat_window = tk.Toplevel(self.root)
        self.chat_window.title("JARVIS AI Assistant")
        self.chat_window.geometry('500x620')
        self.chat_window.configure(bg='white')
        
        # 버튼과 같은 위치에 배치
        button_x = self.root.winfo_x()
        button_y = self.root.winfo_y()
        self.chat_window.geometry(f'500x620+{button_x}+{button_y}')
        
        # 항상 최상단에 표시
        self.chat_window.attributes('-topmost', True)
        
        # 윈도우 크기 조정 방지
        self.chat_window.resizable(False, False)
        
        # 헤더 (높이 증가)
        header_frame = tk.Frame(self.chat_window, bg='#4f46e5', height=100)
        header_frame.pack(fill='x', padx=0, pady=0)
        header_frame.pack_propagate(False)
        
        # 제목과 부제목을 담을 프레임
        title_container = tk.Frame(header_frame, bg='#4f46e5')
        title_container.pack(side='left', fill='both', expand=True, padx=20, pady=15)
        
        # 제목
        title_label = tk.Label(
            title_container,
            text="JARVIS AI Assistant",
            font=self.title_font,
            bg='#4f46e5',
            fg='white'
        )
        title_label.pack(anchor='w')
        
        # 부제목
        subtitle_label = tk.Label(
            title_container,
            text="Multi-Agent System",
            font=self.subtitle_font,
            bg='#4f46e5',
            fg='#e0e7ff'
        )
        subtitle_label.pack(anchor='w', pady=(5, 0))
        
        # --- 버튼 컨테이너 ---
        buttons_container = tk.Frame(header_frame, bg='#4f46e5')
        buttons_container.pack(side='right', padx=15, pady=25)

        # 추천 내역 버튼
        recommendation_button = tk.Button(
            buttons_container,
            text="💡",
            font=('Arial', 18),
            bg='#4f46e5',
            fg='white',
            relief='flat',
            cursor='hand2',
            command=self.open_recommendation_window,
            activebackground='#4338CA',
            activeforeground='white'
        )
        recommendation_button.pack(side='left', padx=(0, 5))

        # 설정 버튼
        settings_button = tk.Button(
            buttons_container,
            text="⚙️",
            font=('Arial', 18),
            bg='#4f46e5',
            fg='white',
            relief='flat',
            cursor='hand2',
            command=self.show_settings_menu,
            activebackground='#4338CA',
            activeforeground='white'
        )
        settings_button.pack(side='left')
        
        # 메시지 영역
        self.messages_frame = tk.Frame(self.chat_window, bg='white')
        self.messages_frame.pack(fill='both', expand=True, padx=15, pady=15)
        
        # 스크롤 가능한 메시지 영역
        self.messages_canvas = tk.Canvas(self.messages_frame, bg='white', highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.messages_frame, orient="vertical", command=self.messages_canvas.yview)
        self.scrollable_frame = tk.Frame(self.messages_canvas, bg='white')
        
        # 캔버스 창 생성 (먼저 생성해야 함)
        self.messages_canvas_window = self.messages_canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        
        def configure_scroll_region(event):
            # 캔버스 너비에 맞춰서 scrollable_frame의 너비를 제한
            canvas_width = event.width
            if canvas_width > 1:  # 유효한 너비인 경우에만
                self.messages_canvas.itemconfig(self.messages_canvas_window, width=canvas_width)
            self.messages_canvas.configure(scrollregion=self.messages_canvas.bbox("all"))
        
        self.scrollable_frame.bind("<Configure>", configure_scroll_region)
        self.messages_canvas.bind("<Configure>", configure_scroll_region)
        self.messages_canvas.configure(yscrollcommand=scrollbar.set)
        
        # 마우스 휠 스크롤 바인딩
        self.messages_canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.messages_canvas.bind("<Button-4>", self._on_mousewheel)  # Linux
        self.messages_canvas.bind("<Button-5>", self._on_mousewheel)  # Linux
        
        # 캔버스에 포커스 설정 (스크롤을 위해)
        self.messages_canvas.bind("<Button-1>", lambda e: self.messages_canvas.focus_set())
        
        self.messages_canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        
        # 입력 영역
        input_frame = tk.Frame(self.chat_window, bg='white', height=100)
        input_frame.pack(fill='x', padx=15, pady=15)
        input_frame.pack_propagate(False)
        
        # 메시지 입력
        self.message_input = tk.Entry(
            input_frame,
            font=self.input_font,
            relief='solid',
            borderwidth=2,
            bg='#f9fafb',
            fg='black'  # 글자색을 검은색으로 설정
        )
        self.message_input.pack(side='left', fill='x', expand=True, padx=(0, 15))
        self.message_input.bind('<Return>', self.send_message)
        
        # 전송 버튼
        send_button = tk.Button(
            input_frame,
            text="전송",
            font=self.button_font,
            bg='#4F46E5',
            fg='white',
            activebackground='#4338CA',
            activeforeground='white',
            relief='flat',
            cursor='hand2',
            command=self.send_message,
            width=8,
            height=2
        )
        send_button.pack(side='right')
        
        # 초기 메시지
        self.add_bot_message("안녕하세요! JARVIS AI Assistant입니다. 무엇을 도와드릴까요?")
        
        # 채팅창 초기에는 숨김
        self.chat_window.withdraw()
        
        # 채팅창 닫기 이벤트 바인딩
        self.chat_window.protocol("WM_DELETE_WINDOW", self.close_chat_window)
        
    def open_recommendation_window(self):
        """추천 내역을 보여주는 새 창을 엽니다."""
        rec_window = tk.Toplevel(self.chat_window)
        rec_window.title("JARVIS 추천 내역")
        rec_window.geometry("600x500")
        rec_window.configure(bg='white')
        rec_window.attributes('-topmost', True)

        # --- 상단 프레임: 버튼 및 제목 ---
        top_frame = tk.Frame(rec_window, bg='white')
        top_frame.pack(fill='x', padx=15, pady=10)

        title_label = tk.Label(top_frame, text="추천 히스토리", font=(self.default_font, 16, 'bold'), bg='white', fg='black')
        title_label.pack(side='left')

        generate_button = tk.Button(
            top_frame,
            text="새로운 추천 생성하기 🚀",
            font=self.button_font,
            bg='#3b82f6', fg='white', relief='flat',
            cursor='hand2',
            command=lambda: self.generate_new_recommendation(rec_window) # window 참조 전달
        )
        generate_button.pack(side='right')

        # --- 추천 목록 표시 영역 ---
        history_text = scrolledtext.ScrolledText(
            rec_window,
            wrap=tk.WORD,
            font=(self.default_font, 11),
            bg='#f9fafb',
            fg='black',
            relief='solid',
            borderwidth=1,
            padx=10,
            pady=10,
            state='disabled' # 읽기 전용
        )
        history_text.pack(fill='both', expand=True, padx=15, pady=(0, 15))

        # 추천 내역 로드
        self.load_recommendation_history(history_text)

    def load_recommendation_history(self, text_widget):
        """백그라운드에서 추천 내역을 불러와 위젯에 표시합니다."""
        text_widget.config(state='normal')
        text_widget.delete('1.0', 'end')
        text_widget.insert('1.0', "추천 내역을 불러오는 중입니다...")
        text_widget.config(state='disabled')

        threading.Thread(target=self._fetch_recommendation_history, args=(text_widget,), daemon=True).start()

    def _fetch_recommendation_history(self, text_widget):
        """[백그라운드 스레드] 추천 히스토리 API를 호출합니다."""
        try:
            from login_view import get_stored_token
            token = get_stored_token()
            if not token:
                self.update_text_widget(text_widget, "오류: 로그인이 필요합니다.")
                return

            response = requests.get(
                f"{self.API_BASE_URL}/api/v2/recommendations/history",
                headers={"Authorization": f"Bearer {token}"},
                timeout=15
            )

            if response.status_code == 200:
                result = response.json()
                if result.get("success") and result.get("recommendations"):
                    formatted_text = self.format_recommendations(result["recommendations"])
                    self.update_text_widget(text_widget, formatted_text)
                else:
                    self.update_text_widget(text_widget, "아직 생성된 추천이 없습니다.")
            else:
                error_msg = response.json().get("detail", "알 수 없는 오류")
                self.update_text_widget(text_widget, f"추천 내역을 불러오는데 실패했습니다: {error_msg}")

        except requests.exceptions.RequestException as e:
            self.update_text_widget(text_widget, f"오류: 서버에 연결할 수 없습니다.\n{e}")

    def generate_new_recommendation(self, window):
        """백그라운드에서 새 추천 생성을 요청합니다."""
        import tkinter.messagebox as messagebox
        
        # 사용자에게 대기 메시지 표시
        messagebox.showinfo("알림", "새로운 추천 생성을 요청했습니다. 잠시 후 목록이 업데이트됩니다.", parent=window)

        threading.Thread(target=self._request_new_recommendation, args=(window,), daemon=True).start()

    def _request_new_recommendation(self, window):
        """[백그라운드 스레드] 새 추천 생성 API를 호출합니다."""
        import tkinter.messagebox as messagebox
        try:
            from login_view import get_stored_token
            token = get_stored_token()
            if not token:
                messagebox.showerror("오류", "로그인이 필요합니다.", parent=window)
                return

            response = requests.post(
                f"{self.API_BASE_URL}/api/v2/recommendations/generate",
                headers={"Authorization": f"Bearer {token}"},
                timeout=30
            )

            if response.status_code == 200:
                result = response.json()
                if result.get("success"):
                    messagebox.showinfo("성공", result.get("message", "새로운 추천이 생성되었습니다!"), parent=window)
                    # UI 업데이트는 메인 스레드에서 실행
                    self.root.after(0, self.refresh_recommendation_window, window)
                else:
                    messagebox.showinfo("알림", result.get("message", "추천을 생성하지 못했습니다."), parent=window)
            elif response.status_code == 429: # Too Many Requests
                error_msg = response.json().get("detail")
                messagebox.showwarning("알림", error_msg, parent=window)
            else:
                error_msg = response.json().get("detail", "알 수 없는 오류")
                messagebox.showerror("오류", f"추천 생성에 실패했습니다: {error_msg}", parent=window)

        except requests.exceptions.RequestException as e:
            messagebox.showerror("오류", f"서버 연결에 실패했습니다: {e}", parent=window)

    def refresh_recommendation_window(self, window):
        """추천 창의 내용을 새로고침합니다."""
        # window에서 ScrolledText 위젯 찾기
        for widget in window.winfo_children():
            if isinstance(widget, scrolledtext.ScrolledText):
                self.load_recommendation_history(widget)
                break

    def update_text_widget(self, text_widget, content):
        """[메인 스레드 호출용] 텍스트 위젯 내용을 안전하게 업데이트합니다."""
        def _update():
            text_widget.config(state='normal')
            text_widget.delete('1.0', 'end')
            text_widget.insert('1.0', content)
            text_widget.config(state='disabled')
        self.root.after(0, _update)

    def format_recommendations(self, recommendations: list) -> str:
        """추천 목록을 서식이 있는 텍스트로 변환합니다."""
        formatted_lines = []
        for rec in recommendations:
            dt = datetime.fromtimestamp(rec['created_at'])
            date_str = dt.strftime('%Y-%m-%d %H:%M')
            rec_type = "수동 생성" if rec.get('type') == 'manual' else "자동 생성"
            
            formatted_lines.append(f"## {rec['title']} ##")
            formatted_lines.append(f"[{date_str} | {rec_type}]")
            formatted_lines.append(f"{rec['content']}")
            formatted_lines.append("-" * 40 + "\n")
        
        return "\n".join(formatted_lines)

    def toggle_chat_window(self):
        """채팅창 토글"""
        if self.chat_window.state() == 'withdrawn':
            # 버튼 숨기기
            self.root.withdraw()
            # 채팅창을 버튼 위치에 표시 
            button_x = self.root.winfo_x() - 420
            button_y = self.root.winfo_y() - 550
            self.chat_window.geometry(f'500x600+{button_x}+{button_y}')
            self.chat_window.deiconify()
            self.message_input.focus()
        else:
            self.chat_window.withdraw()
            self.root.deiconify()
            
    def close_chat_window(self, event=None):
        """채팅창 닫기"""
        self.chat_window.withdraw()
        # 버튼 다시 표시
        self.root.deiconify()
        self.root.lift()  # 윈도우를 최상단으로 올림
        self.root.focus_force()  # 포커스 강제 설정
        
        # 약간의 지연 후 다시 한번 확인
        self.root.after(100, self.ensure_button_visible)
        
    def ensure_button_visible(self):
        """버튼이 확실히 보이도록 보장"""
        if not self.root.winfo_viewable():
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
    
    def _on_mousewheel(self, event):
        """마우스 휠 스크롤 처리"""
        # Windows와 macOS에서 delta 값이 다름
        if event.delta:
            delta = -1 * (event.delta / 120)  # Windows
        else:
            delta = -1 if event.num == 4 else 1  # Linux
        
        # 스크롤 실행
        self.messages_canvas.yview_scroll(int(delta), "units")
        
    def add_user_message(self, message):
        """사용자 메시지 추가"""
        message_frame = tk.Frame(self.scrollable_frame, bg='white')
        message_frame.pack(fill='x', pady=8)
        
        # 사용자 메시지 컨테이너 (우측 정렬)
        user_container = tk.Frame(message_frame, bg='white')
        user_container.pack(side='right', padx=(50, 15))
        
        # 사용자 메시지 (Text 위젯으로 변경하여 텍스트 선택 가능)
        user_text = tk.Text(
            user_container,
            font=self.message_font,
            bg='#eef2ff',
            fg='black',
            wrap='word',
            width=35,
            height=1,
            relief='flat',
            borderwidth=0,
            padx=15,
            pady=10,
            state='disabled',
            cursor='arrow'
        )
        user_text.pack()
        
        # 텍스트 삽입 및 높이 자동 조정
        user_text.config(state='normal')
        user_text.insert('1.0', message)
        user_text.config(state='disabled')
        
        # 텍스트 높이에 맞게 조정
        user_text.update_idletasks()
        text_height = user_text.tk.call((user_text, 'count', '-update', '-displaylines', '1.0', 'end'))
        user_text.config(height=max(1, text_height))
        
        # 스크롤을 맨 아래로
        self.messages_canvas.update_idletasks()
        self.messages_canvas.yview_moveto(1)
        
    def add_bot_message(self, message):
        """봇 메시지 추가"""
        message_frame = tk.Frame(self.scrollable_frame, bg='white')
        message_frame.pack(fill='x', pady=8)
        
        # 봇 메시지 컨테이너 (좌측 정렬)
        bot_container = tk.Frame(message_frame, bg='white')
        bot_container.pack(side='left', padx=(15, 50))
        
        # 봇 메시지 (Text 위젯으로 변경하여 텍스트 선택 가능)
        bot_text = tk.Text(
            bot_container,
            font=self.message_font,
            bg='#f3f4f6',
            fg='black',
            wrap='word',
            width=35,
            height=1,
            relief='flat',
            borderwidth=0,
            padx=15,
            pady=10,
            state='disabled',
            cursor='arrow'
        )
        bot_text.pack()
        
        # 스크롤을 맨 아래로
        self.messages_canvas.update_idletasks()
        self.messages_canvas.yview_moveto(1)
        
        # 타이핑 애니메이션 시작
        self.animate_typing(bot_text, message)
    
    def animate_typing(self, text_widget, full_text, current_index=0):
        """타이핑 애니메이션을 실행합니다."""
        if current_index <= len(full_text):
            # 현재까지의 텍스트 표시
            current_text = full_text[:current_index]
            
            # Text 위젯에 텍스트 삽입
            text_widget.config(state='normal')
            text_widget.delete('1.0', 'end')
            text_widget.insert('1.0', current_text)
            text_widget.config(state='disabled')
            
            # 텍스트 높이에 맞게 조정
            text_widget.update_idletasks()
            text_height = text_widget.tk.call((text_widget, 'count', '-update', '-displaylines', '1.0', 'end'))
            text_widget.config(height=max(1, text_height))
            
            # 다음 글자로 진행
            if current_index < len(full_text):
                # 타이핑 속도 조절 (밀리초)
                typing_speed = 30  # 빠른 타이핑
                self.root.after(typing_speed, lambda: self.animate_typing(text_widget, full_text, current_index + 1))
            
            # 스크롤을 맨 아래로 유지
            self.messages_canvas.update_idletasks()
            self.messages_canvas.yview_moveto(1)
    
    def show_loading_message(self):
        """로딩 메시지를 표시합니다."""
        message_frame = tk.Frame(self.scrollable_frame, bg='white')
        message_frame.pack(fill='x', pady=8)
        
        # 로딩 메시지 컨테이너 (좌측 정렬)
        loading_container = tk.Frame(message_frame, bg='white')
        loading_container.pack(side='left', padx=(15, 50))
        
        # 로딩 메시지 (Text 위젯으로 변경)
        loading_text = tk.Text(
            loading_container,
            font=self.message_font,
            bg='#f3f4f6',
            fg='black',
            wrap='word',
            width=35,
            height=1,
            relief='flat',
            borderwidth=0,
            padx=15,
            pady=10,
            state='disabled',
            cursor='arrow'
        )
        loading_text.pack()
        
        # 초기 텍스트 삽입
        loading_text.config(state='normal')
        loading_text.insert('1.0', "답변을 생성하고 있습니다...")
        loading_text.config(state='disabled')
        
        # 로딩 애니메이션 시작
        self.animate_loading(loading_text)
        
        # 스크롤을 맨 아래로
        self.messages_canvas.update_idletasks()
        self.messages_canvas.yview_moveto(1)
        
        return loading_text
    
    def animate_loading(self, text_widget, dots=0):
        """로딩 애니메이션을 실행합니다."""
        dots_text = "." * (dots + 1)
        loading_text = f"답변을 생성하고 있습니다{dots_text}"
        
        # Text 위젯에 텍스트 삽입
        text_widget.config(state='normal')
        text_widget.delete('1.0', 'end')
        text_widget.insert('1.0', loading_text)
        text_widget.config(state='disabled')
        
        # 다음 애니메이션 프레임
        self.root.after(500, lambda: self.animate_loading(text_widget, (dots + 1) % 4))
    
    def remove_loading_message(self, loading_text_widget):
        """로딩 메시지를 제거합니다."""
        if loading_text_widget and loading_text_widget.winfo_exists():
            loading_text_widget.master.master.destroy()  # container의 부모인 message_frame 제거
    
    def update_loading_message(self, loading_text_widget, new_text):
        """로딩 메시지를 업데이트합니다."""
        if loading_text_widget and loading_text_widget.winfo_exists():
            loading_text_widget.config(state='normal')
            loading_text_widget.delete('1.0', 'end')
            loading_text_widget.insert('1.0', new_text)
            loading_text_widget.config(state='disabled')
    
    def send_message(self, event=None):
        """메시지 전송"""
        message = self.message_input.get().strip()
        if not message:
            return
            
        # 입력창 초기화
        self.message_input.delete(0, tk.END)
        
        # 사용자 메시지 표시
        self.add_user_message(message)
        
        # 로딩 메시지 표시
        loading_text_widget = self.show_loading_message()
        
        # 큐를 통해 API 요청 처리
        self.message_queue.put({
            'type': 'api_request',
            'message': message,
            'loading_widget': loading_text_widget
        })
        
    def process_api_request(self, message, loading_text_widget):
        """봇 응답 가져오기 - 재시도 로직 포함"""
        max_retries = 3
        retry_delay = 2  # 초
        timeout = 60  # 타임아웃을 30초에서 60초로 증가
        
        for attempt in range(max_retries):
            try:
                # API 호출 - Supervisor 기반 처리
                response = requests.post(
                    f"{self.API_BASE_URL}/api/v2/process",
                    json={"message": message, "user_id": 1},
                    timeout=timeout
                )
                
                if response.status_code == 200:
                    result = response.json()
                    # Supervisor 응답 구조에 맞게 처리
                    if result.get("success"):
                        bot_response = result.get("content", "응답을 처리할 수 없습니다.")
                    else:
                        bot_response = result.get("content", "처리 중 오류가 발생했습니다.")
                    
                    # 큐를 통해 결과 전달
                    self.message_queue.put({
                        'type': 'bot_response',
                        'response': bot_response,
                        'loading_widget': loading_text_widget
                    })
                    return
                else:
                    error_msg = f"Error: {response.status_code} - {response.text}"
                    self.message_queue.put({
                        'type': 'bot_response',
                        'response': error_msg,
                        'loading_widget': loading_text_widget
                    })
                    return
                    
            except requests.exceptions.Timeout:
                if attempt < max_retries - 1:
                    # 재시도 메시지
                    self.message_queue.put({
                        'type': 'update_loading',
                        'message': f"서버 응답 대기 중... (재시도 {attempt + 2}/{max_retries})",
                        'loading_widget': loading_text_widget
                    })
                    import time
                    time.sleep(retry_delay)
                    continue
                else:
                    error_msg = f"서버 응답 시간이 초과되었습니다. ({timeout}초 후 재시도 {max_retries}회 완료)"
                    self.message_queue.put({
                        'type': 'bot_response',
                        'response': error_msg,
                        'loading_widget': loading_text_widget
                    })
                    return
                    
            except requests.exceptions.ConnectionError:
                if attempt < max_retries - 1:
                    # 재시도 전에 로딩 메시지 업데이트 (큐를 통해 안전하게)
                    self.message_queue.put({
                        'type': 'update_loading',
                        'message': f"서버 연결 시도 중... (재시도 {attempt + 2}/{max_retries})",
                        'loading_widget': loading_text_widget
                    })
                    import time
                    time.sleep(retry_delay)
                    continue
                else:
                    error_msg = "서버에 연결할 수 없습니다. 서버가 실행 중인지 확인해주세요."
                    self.message_queue.put({
                        'type': 'bot_response',
                        'response': error_msg,
                        'loading_widget': loading_text_widget
                    })
                    return
                    
            except Exception as e:
                if attempt < max_retries - 1:
                    # 재시도 전에 로딩 메시지 업데이트 (큐를 통해 안전하게)
                    self.message_queue.put({
                        'type': 'update_loading',
                        'message': f"오류 발생, 재시도 중... (재시도 {attempt + 2}/{max_retries})",
                        'loading_widget': loading_text_widget
                    })
                    import time
                    time.sleep(retry_delay)
                    continue
                else:
                    error_msg = f"연결 중 오류가 발생했습니다: {str(e)}"
                    self.message_queue.put({
                        'type': 'bot_response',
                        'response': error_msg,
                        'loading_widget': loading_text_widget
                    })
                    return
    
    def handle_bot_response(self, bot_response, loading_text_widget):
        """봇 응답을 처리합니다."""
        # 로딩 메시지 제거
        self.remove_loading_message(loading_text_widget)
        
        # 타이핑 애니메이션으로 봇 메시지 표시
        self.add_bot_message(bot_response)
        
    def check_for_recommendations(self):
        """주기적으로 서버에 새로운 추천이 있는지 확인합니다."""
        # 백그라운드 스레드에서 API 호출
        threading.Thread(target=self._fetch_recommendations, daemon=True).start()
        
        # 5분 후에 다시 확인
        self.root.after(300000, self.check_for_recommendations)

    def _fetch_recommendations(self):
        """[백그라운드 스레드] 추천 API를 호출합니다."""
        try:
            from login_view import get_stored_token
            token = get_stored_token()
            if not token:
                return

            response = requests.get(
                f"{self.API_BASE_URL}/api/v2/recommendations",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10
            )
            if response.status_code == 200:
                result = response.json()
                if result.get("success") and result.get("recommendations"):
                    # UI 스레드에서 알림을 표시하도록 큐에 넣음
                    self.message_queue.put({
                        'type': 'show_recommendation',
                        'recommendations': result["recommendations"]
                    })
        except requests.exceptions.RequestException as e:
            print(f"추천 확인 중 오류: {e}")

    def show_recommendation_notification(self, recommendations):
        """새로운 추천 알림을 채팅창 헤더에 표시합니다."""
        if self.recommendation_notification_visible or not recommendations:
            return

        # 첫 번째 추천을 대표로 사용
        latest_rec = recommendations[0]

        self.notification_frame = tk.Frame(self.chat_window, bg='#10b981', height=40)
        self.notification_frame.pack(fill='x', side='top', before=self.messages_frame)
        self.notification_frame.pack_propagate(False)

        notification_label = tk.Label(
            self.notification_frame,
            text=f"💡 새로운 추천: {latest_rec['title']}",
            font=(self.default_font, 11),
            bg='#10b981',
            fg='white'
        )
        notification_label.pack(side='left', padx=15, pady=5)
        
        close_button = tk.Button(
            self.notification_frame,
            text="✕",
            font=(self.default_font, 11, 'bold'),
            bg='#10b981',
            fg='white',
            relief='flat',
            command=self.dismiss_recommendation_notification
        )
        close_button.pack(side='right', padx=10)

        self.recommendation_notification_visible = True

    def dismiss_recommendation_notification(self):
        """추천 알림을 닫습니다."""
        if hasattr(self, 'notification_frame') and self.notification_frame.winfo_exists():
            self.notification_frame.destroy()
        self.recommendation_notification_visible = False
    
    def run(self):
        """애플리케이션 실행"""
        try:
            self.root.mainloop()
        except Exception as e:
            print(f"애플리케이션 실행 중 오류: {e}")
        finally:
            # 애플리케이션 종료 시 정리
            self.cleanup()
    
    def cleanup(self):
        """애플리케이션 종료 시 정리 작업"""
        try:
            # 큐 정리
            while not self.message_queue.empty():
                try:
                    self.message_queue.get_nowait()
                except queue.Empty:
                    break
        except Exception as e:
            print(f"정리 작업 중 오류: {e}")
    
    def copy_text(self, text_widget):
        """선택된 텍스트를 클립보드에 복사"""
        try:
            # 선택된 텍스트가 있는지 확인
            selected_text = text_widget.get(tk.SEL_FIRST, tk.SEL_LAST)
            if selected_text:
                self.root.clipboard_clear()
                self.root.clipboard_append(selected_text)
        except tk.TclError:
            # 선택된 텍스트가 없는 경우 전체 텍스트 복사
            full_text = text_widget.get('1.0', 'end-1c')
            self.root.clipboard_clear()
            self.root.clipboard_append(full_text)
    
    def select_all_text(self, text_widget):
        """텍스트 위젯의 모든 텍스트 선택"""
        text_widget.config(state='normal')
        text_widget.tag_add(tk.SEL, '1.0', 'end-1c')
        text_widget.tag_config(tk.SEL, background='#0078d4', foreground='white')
        text_widget.config(state='disabled')
        text_widget.mark_set(tk.INSERT, '1.0')
        text_widget.see(tk.INSERT)
    
    def show_settings_menu(self):
        """설정 메뉴 표시"""
        import tkinter.messagebox as messagebox
        
        # 메뉴 생성
        menu = tk.Menu(self.chat_window, tearoff=0)
        menu.add_command(label="📁 데이터 폴더 변경", command=self.change_data_folder)
        menu.add_separator()
        menu.add_command(label="ℹ️ 정보", command=lambda: messagebox.showinfo("JARVIS", "JARVIS Multi-Agent System\nVersion 1.0", parent=self.chat_window))
        
        # 설정 버튼 위치에 메뉴 표시 (헤더 높이 증가에 맞춰 조정)
        button_x = self.chat_window.winfo_rootx() + 450
        button_y = self.chat_window.winfo_rooty() + 60
        menu.post(button_x, button_y)
    
    def change_data_folder(self):
        """데이터 폴더 변경"""
        import tkinter.messagebox as messagebox
        import sys
        from pathlib import Path
        
        # 확인 대화상자
        result = messagebox.askyesno(
            "데이터 폴더 변경",
            "데이터 폴더를 변경하면 기존 데이터가 모두 삭제되고\n새로운 폴더에서 데이터를 수집합니다.\n\n계속하시겠습니까?"
        )
        
        if not result:
            return
        
        # 폴더 선택 UI 표시
        try:
            sys.path.insert(0, str(Path("frontend")))
            from folder_selector import select_folders
            
            # 폴더 선택
            selected_folders = select_folders()
            
            if selected_folders == "cancelled":
                messagebox.showinfo("알림", "폴더 선택이 취소되었습니다.")
                return
            
            # 폴더 경로 결정
            if selected_folders is None:
                # 전체 사용자 폴더 스캔
                folder_path = ""
            elif selected_folders:
                # 첫 번째 폴더 사용
                folder_path = selected_folders[0]
            else:
                messagebox.showwarning("오류", "올바른 폴더를 선택해주세요.")
                return
            
            # 백엔드 API 호출
            self.call_update_folder_api(folder_path)
            
        except Exception as e:
            messagebox.showerror("오류", f"폴더 선택 중 오류가 발생했습니다: {e}")
    
    def call_update_folder_api(self, folder_path: str):
        """백엔드에 폴더 업데이트 요청"""
        import tkinter.messagebox as messagebox
        import sys
        from pathlib import Path
        
        try:
            # 토큰 조회
            sys.path.insert(0, str(Path("frontend")))
            from login_view import get_stored_token
            
            token = get_stored_token()
            if not token:
                messagebox.showerror("오류", "로그인이 필요합니다.")
                return
            
            # API 호출
            response = requests.post(
                f"{self.API_BASE_URL}/settings/update-folder",
                headers={"Authorization": f"Bearer {token}"},
                json={"new_folder_path": folder_path},
                timeout=30
            )
            
            if response.status_code == 200:
                messagebox.showinfo("완료", "데이터 폴더가 성공적으로 변경되었습니다.\n새 데이터가 수집되고 있습니다.")
            else:
                error_msg = response.json().get("detail", "알 수 없는 오류")
                messagebox.showerror("오류", f"폴더 변경 실패: {error_msg}")
                
        except Exception as e:
            messagebox.showerror("오류", f"API 호출 중 오류: {e}")
    
    def copy_selected_text(self, event=None):
        """현재 포커스된 텍스트 위젯에서 선택된 텍스트 복사"""
        try:
            # 현재 포커스된 위젯 확인
            focused_widget = self.root.focus_get()
            if isinstance(focused_widget, tk.Text):
                self.copy_text(focused_widget)
        except Exception as e:
            print(f"복사 중 오류: {e}")

def main():
    """메인 함수"""
    print("JARVIS Floating Chat Desktop App")
    print("=" * 50)
    print("화면 우측 하단에 플로팅 버튼이 나타납니다.")
    print("버튼을 클릭하면 채팅창이 열립니다.")
    print("버튼을 드래그하여 이동할 수 있습니다.")
    print("ESC 키로 채팅창을 닫을 수 있습니다.")
    print("=" * 50)
    
    app = FloatingChatApp()
    app.run()

if __name__ == "__main__":
    main()
