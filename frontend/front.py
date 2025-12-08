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
import subprocess  # 파일/폴더 열기용
import websocket  # WebSocket 클라이언트

# Dashboard import
from dashboard_view import DashboardWindow

# Token management
from token_store import (
    load_token, save_token, delete_token, 
    is_expiring, get_valid_token_and_user, get_user_id_from_token
)

# Theme import (중앙 집중식 색상/스타일 관리)
from theme import COLORS, BUTTON_STYLES, STATUS_BADGE_STYLES

class FloatingChatApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("JARVIS Floating Chat")
        
        # 한글 폰트 설정
        self.setup_korean_fonts()
        
        # API 설정
        self.API_BASE_URL = "http://localhost:8000"
        
        # =========================================================================
        # 토큰/유저 상태 초기화 (앱 시작 시 저장된 토큰 로드)
        # =========================================================================
        self.jwt_token = None
        self.user_id = None
        self._load_auth_state()
        
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
        
        # 복사 기능 (채팅창에서) - 플랫폼별 단축키
        system = platform.system()
        if system == "Darwin":  # macOS
            self.root.bind('<Command-c>', self.copy_selected_text)
        else:  # Windows/Linux
            self.root.bind('<Control-c>', self.copy_selected_text)
        
        # 큐 처리 시작
        self.process_message_queue()

        # 추천 말풍선을 위한 변수
        self.recommendation_bubble = None
        self.recommendation_bubble_visible = False
        self.current_recommendation = None
        self.bubble_auto_close_id = None
        
        # 기존 알림 변수 (호환성 유지)
        self.recommendation_notification_visible = False
        
        # 보고서 알림 말풍선을 위한 변수 (추천과 별도 관리)
        self.report_notification_window = None
        self.report_notification_visible = False
        self.report_auto_close_id = None
        
        # 대시보드 창 인스턴스
        self.dashboard_window = None

        # WebSocket 연결 변수
        self.ws = None
        self.ws_connected = False
        self.ws_reconnect_delay = 5  # 재연결 대기 시간 (초)
        
        # =========================================================================
        # 데이터 수집 상태 관련 변수
        # =========================================================================
        self.is_collecting_data = False  # 현재 데이터 수집 중인지
        self.collection_progress = 0.0  # 수집 진행률 (0-100)
        self.collection_message = ""  # 현재 수집 단계 메시지
        self.collection_check_id = None  # 수집 상태 체크 타이머 ID
        
        # 스피너 애니메이션 관련 변수
        self.spinner_angle = 0
        self.spinner_animation_id = None
        
        # 수집 상태 말풍선 관련 변수
        self.collection_status_bubble = None
        self.collection_status_visible = False
        self.collection_bubble_auto_close_id = None
        
        # 수집 중 대기 중인 추천 (수집 완료 후 표시)
        self.pending_recommendations_queue = []
        
        # WebSocket 연결 시작 (실시간 추천 알림용)
        self.connect_websocket()
        
        # 환경 변수 확인하여 데이터 수집 모드로 시작할지 결정
        self._check_and_start_collection_mode()
    
    # =========================================================================
    # 토큰/인증 상태 관리 메서드
    # =========================================================================
    
    def _load_auth_state(self):
        """저장된 토큰을 로드하고 user_id를 복원합니다."""
        try:
            token, user_id = get_valid_token_and_user()
            if token and user_id:
                self.jwt_token = token
                self.user_id = user_id
                print(f"[Auth] 저장된 토큰 로드 완료 (user_id={user_id})")
            else:
                print("[Auth] 유효한 저장된 토큰 없음")
                self.jwt_token = None
                self.user_id = None
        except Exception as e:
            print(f"[Auth] 토큰 로드 오류: {e}")
            self.jwt_token = None
            self.user_id = None
    
    def set_auth(self, token: str, user_id: int):
        """로그인 성공 시 토큰과 user_id를 설정합니다."""
        self.jwt_token = token
        self.user_id = user_id
        save_token(token)
        print(f"[Auth] 인증 정보 설정 완료 (user_id={user_id})")
    
    def clear_auth(self):
        """로그아웃 시 토큰과 user_id를 초기화합니다."""
        self.jwt_token = None
        self.user_id = None
        delete_token()
        print("[Auth] 인증 정보 초기화")
    
    def is_logged_in(self) -> bool:
        """현재 로그인 상태인지 확인합니다."""
        if not self.jwt_token or not self.user_id:
            return False
        # 토큰 만료 체크
        if is_expiring(self.jwt_token):
            print("[Auth] 토큰이 만료되었거나 곧 만료됩니다.")
            return False
        return True
    
    def ensure_logged_in(self) -> bool:
        """로그인 상태를 확인하고, 미로그인 시 경고 메시지를 표시합니다.
        
        Returns:
            True if logged in, False otherwise.
        """
        if self.is_logged_in():
            return True
        
        # 토큰 재로드 시도 (다른 프로세스에서 로그인했을 수 있음)
        self._load_auth_state()
        if self.is_logged_in():
            return True
        
        # 로그인 필요 메시지
        from tkinter import messagebox
        messagebox.showwarning(
            "로그인 필요", 
            "이 기능을 사용하려면 로그인이 필요합니다.\n앱을 재시작하여 로그인해주세요."
        )
        return False
    
    def setup_korean_fonts(self):
        """한글 폰트를 설정합니다."""
        # 플랫폼별 한글 폰트 설정
        system = platform.system()
        
        if system == "Darwin":  # macOS
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
                'Arial Unicode MS'      # Arial Unicode MS
            ]
        
        # 사용 가능한 폰트 찾기
        self.default_font = 'Arial'  # 기본값
        for font in korean_fonts:
            try:
                # 폰트 존재 여부 확인
                test_label = tk.Label(self.root, font=(font, 12))
                test_label.destroy()
                self.default_font = font
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
    
    def _bind_right_click(self, widget, callback):
        """플랫폼별 우클릭 이벤트를 바인딩합니다."""
        system = platform.system()
        
        # 모든 플랫폼에서 Button-3 바인딩 (표준 우클릭)
        widget.bind('<Button-3>', callback)
        
        if system == "Darwin":  # macOS
            # macOS: Button-2 (미들 클릭이 우클릭으로 매핑되는 경우)
            widget.bind('<Button-2>', callback)
            # macOS: Control + 좌클릭 (트랙패드 우클릭)
            widget.bind('<Control-Button-1>', callback)
    
    def _setup_window_for_macos(self, window, is_popup=False):
        """macOS에서 창이 올바르게 표시되도록 설정합니다."""
        system = platform.system()
        
        if system == "Darwin":
            # macOS에서 overrideredirect 창이 보이도록 lift() 호출
            window.lift()
            window.update_idletasks()
            # 포커스 없이도 클릭 이벤트 받을 수 있도록
            window.attributes('-topmost', True)
            # 추가로 윈도우를 다시 올림
            window.after(100, lambda: window.lift() if window.winfo_exists() else None)

    def _get_status_badge_style(self, status: str) -> dict:
        """상태별 배지 색상을 반환합니다."""
        return STATUS_BADGE_STYLES.get(status, STATUS_BADGE_STYLES["default"])

    def _style_button(self, button: tk.Button, variant: str = "outlined", disabled: bool = False):
        """버튼에 일관된 테마 스타일을 적용합니다.
        
        theme.py의 style_button 함수를 래핑합니다.
        variant: "outlined" (기본), "ghost", "danger", "success"
                 "primary", "secondary"도 하위 호환성을 위해 지원
        """
        from theme import style_button
        style_button(button, variant=variant, disabled=disabled)
    
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
                        self.handle_bot_response(
                            message['response'], 
                            message['loading_widget'],
                            message.get('deep_dive_info')
                        )
                        
                    elif message['type'] == 'update_loading':
                        # 로딩 메시지 업데이트
                        self.update_loading_message(message['loading_widget'], message['message'])
                        
                    elif message['type'] == 'show_recommendation':
                        # 추천 알림 표시
                        self.show_recommendation_notification(message['recommendations'])
                    
                    elif message['type'] == 'create_streaming_message':
                        # 스트리밍용 빈 봇 메시지 생성
                        self.create_streaming_bot_message(message['loading_widget'])
                    
                    elif message['type'] == 'update_streaming':
                        # 스트리밍 메시지 업데이트
                        self.update_streaming_message(message['text'])
                    
                    elif message['type'] == 'complete_streaming':
                        # 스트리밍 완료
                        self.complete_streaming_message()
                    
                    elif message['type'] == 'stream_chunk':
                        # 스트리밍 청크 처리
                        self.handle_stream_chunk(message['chunk'])
                    
                    elif message['type'] == 'show_report_notification':
                        # 보고서 완료/실패 알림 표시
                        self.show_report_notification(message['data'])
                    
                    elif message['type'] == 'show_deep_dive_offer':
                        # 심층 보고서 제안 UI 표시
                        self.show_deep_dive_offer(
                            message['keyword'],
                            message['recommendation_id']
                        )
                        
                except queue.Empty:
                    break
                    
        except Exception as e:
            pass  # 큐 처리 중 오류 무시
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
            # macOS에서 투명 배경 설정
            try:
                self.root.wm_attributes('-transparent', True)
            except tk.TclError:
                # 일부 macOS 버전에서 지원하지 않을 수 있음
                pass
        else: # Windows
            self.root.wm_attributes('-transparentcolor', 'black')

        # 윈도우 테두리와 제목 표시줄 제거
        self.root.overrideredirect(True)
        
        # macOS에서 overrideredirect 창이 올바르게 표시되도록 설정
        self._setup_window_for_macos(self.root)
        
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
            fill=COLORS["primary"],
            outline=COLORS["primary"],
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
        
        # 우클릭 메뉴 이벤트 바인딩 (플랫폼별)
        self._bind_right_click(self.button_canvas, self.show_context_menu)
        
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
        self.button_canvas.itemconfig('button', fill=COLORS["primary_dark"])
        
    def on_leave(self, event):
        """호버 해제"""
        self.button_canvas.itemconfig('button', fill=COLORS["primary"])
        
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
        # 드래그가 아니었다면 클릭으로 간주
        if not self.drag_data["dragging"]:
            # 데이터 수집 중이면 상태 말풍선 표시, 아니면 채팅창 토글
            if self.is_collecting_data:
                self.show_collection_status_bubble()
            else:
                self.toggle_chat_window()
        self.drag_data["dragging"] = False
    
    # =========================================================================
    # 데이터 수집 상태 및 스피너 애니메이션 메서드
    # =========================================================================
    
    def start_data_collection_mode(self, selected_folders: list = None):
        """
        데이터 수집 모드를 시작합니다.
        - 스피너 애니메이션 시작
        - '초기 데이터 수집을 시작합니다.' 말풍선 3초 표시
        - 백엔드 API로 수집 시작 요청
        - 진행률 모니터링 시작
        """
        self.is_collecting_data = True
        self.collection_progress = 0.0
        self.collection_message = "초기화 중..."
        self.selected_folders_for_collection = selected_folders or []
        
        # 스피너 애니메이션 시작
        self._start_spinner_animation()
        
        # 시작 말풍선 표시 (3초 후 자동 닫힘)
        self._show_temporary_message_bubble("🚀 초기 데이터 수집을 시작합니다.", 3000)
        
        # 백엔드에 데이터 수집 시작 요청
        threading.Thread(target=self._start_collection_api_call, daemon=True).start()
        
        # 진행률 모니터링 시작
        self._start_collection_progress_monitoring()
    
    def _start_collection_api_call(self):
        """백엔드 API를 호출하여 데이터 수집을 시작합니다."""
        try:
            if not self.jwt_token or not self.user_id:
                print("[Collection] 인증 정보가 없어 데이터 수집을 시작할 수 없습니다.")
                return
            
            response = requests.post(
                f"{self.API_BASE_URL}/api/v2/data-collection/start/{self.user_id}",
                headers={"Authorization": f"Bearer {self.jwt_token}"},
                json={"selected_folders": self.selected_folders_for_collection},
                timeout=30
            )
            
            if response.status_code == 200:
                print("[Collection] 데이터 수집 시작 요청 성공")
            else:
                print(f"[Collection] 데이터 수집 시작 실패: {response.text}")
        except Exception as e:
            print(f"[Collection] 데이터 수집 시작 API 호출 오류: {e}")
    
    def _start_collection_progress_monitoring(self):
        """데이터 수집 진행률을 주기적으로 모니터링합니다."""
        if self.collection_check_id:
            self.root.after_cancel(self.collection_check_id)
        
        self._check_collection_progress()
    
    def _check_collection_progress(self):
        """백엔드에서 데이터 수집 진행률을 조회합니다."""
        if not self.is_collecting_data:
            return
        
        def fetch_progress():
            try:
                if not self.jwt_token or not self.user_id:
                    return
                
                response = requests.get(
                    f"{self.API_BASE_URL}/api/v2/data-collection/status/{self.user_id}",
                    headers={"Authorization": f"Bearer {self.jwt_token}"},
                    timeout=30  # 임베딩 중 API가 느려질 수 있어 타임아웃 증가
                )
                
                if response.status_code == 200:
                    data = response.json()
                    progress = data.get("progress", 0.0) or 0.0
                    message = data.get("progress_message", "")
                    is_done = data.get("is_done", False)
                    
                    # UI 업데이트는 메인 스레드에서
                    self.root.after(0, lambda: self._update_collection_progress(progress, message, is_done))
            except Exception as e:
                print(f"[Collection] 진행률 조회 오류: {e}")
        
        threading.Thread(target=fetch_progress, daemon=True).start()
        
        # 3초마다 다시 체크
        if self.is_collecting_data:
            self.collection_check_id = self.root.after(3000, self._check_collection_progress)
    
    def _update_collection_progress(self, progress: float, message: str, is_done: bool):
        """수집 진행률 정보를 업데이트합니다."""
        self.collection_progress = progress
        self.collection_message = message
        
        if is_done:
            self._on_collection_complete()
    
    def _on_collection_complete(self):
        """데이터 수집이 완료되었을 때 호출됩니다."""
        self.is_collecting_data = False
        
        # 스피너 애니메이션 중지
        self._stop_spinner_animation()
        
        # 진행률 모니터링 중지
        if self.collection_check_id:
            self.root.after_cancel(self.collection_check_id)
            self.collection_check_id = None
        
        # 상태 말풍선 닫기 (열려있다면)
        self._close_collection_status_bubble()
        
        # 완료 말풍선 표시 (3초 후 자동 닫힘)
        self._show_temporary_message_bubble("🎉 초기 데이터 수집이 완료되었습니다!", 3000)
        
        print("[Collection] 데이터 수집 완료!")
        
        # 5초 후 대기 중인 추천 표시
        if self.pending_recommendations_queue:
            print(f"[Recommendation] 5초 후 대기 중인 추천 {len(self.pending_recommendations_queue)}개를 표시합니다.")
            self.root.after(5000, self._show_pending_recommendations)
    
    def _show_pending_recommendations(self):
        """대기 중인 추천을 표시합니다."""
        if self.pending_recommendations_queue:
            recommendations = self.pending_recommendations_queue
            self.pending_recommendations_queue = []
            self.show_recommendation_notification(recommendations)
    
    def _start_spinner_animation(self):
        """스피너 애니메이션을 시작합니다."""
        self.spinner_angle = 0
        self._animate_spinner()
    
    def _animate_spinner(self):
        """스피너 프레임을 그립니다."""
        if not self.is_collecting_data:
            return
        
        # 기존 스피너 삭제
        self.button_canvas.delete('spinner')
        
        # 회전하는 arc 그리기 (270도 원호)
        # 버튼 크기(70x70) 기준, 안쪽에 여백을 두고 그림
        self.button_canvas.create_arc(
            8, 8, 62, 62,
            start=self.spinner_angle, extent=270,
            outline='white', width=3,
            style='arc', tags='spinner'
        )
        
        self.spinner_angle = (self.spinner_angle + 15) % 360
        self.spinner_animation_id = self.root.after(50, self._animate_spinner)
    
    def _stop_spinner_animation(self):
        """스피너 애니메이션을 중지합니다."""
        if self.spinner_animation_id:
            self.root.after_cancel(self.spinner_animation_id)
            self.spinner_animation_id = None
        
        self.button_canvas.delete('spinner')
    
    def _show_temporary_message_bubble(self, message: str, duration_ms: int = 3000):
        """임시 메시지 말풍선을 표시합니다 (지정된 시간 후 자동 닫힘)."""
        # 기존 말풍선 닫기
        self._close_collection_status_bubble()
        
        # 새 말풍선 생성
        bubble = tk.Toplevel(self.root)
        bubble.wm_overrideredirect(True)
        bubble.attributes('-topmost', True)
        bubble.configure(bg=COLORS["primary"])
        
        # macOS 설정
        self._setup_window_for_macos(bubble, is_popup=True)
        
        # 메인 프레임
        main_frame = tk.Frame(bubble, bg=COLORS["primary"], padx=15, pady=12)
        main_frame.pack(fill='both', expand=True)
        
        # 메시지 라벨
        msg_label = tk.Label(
            main_frame,
            text=message,
            font=(self.default_font, 12, 'bold'),
            bg=COLORS["primary"],
            fg=COLORS["text_inverse"],
            wraplength=250
        )
        msg_label.pack()
        
        # 위치 계산 (플로팅 버튼 위)
        bubble.update_idletasks()
        bubble_width = bubble.winfo_reqwidth()
        bubble_height = bubble.winfo_reqheight()
        
        button_x = self.root.winfo_x()
        button_y = self.root.winfo_y()
        
        x = button_x + 35 - bubble_width // 2
        y = button_y - bubble_height - 15
        
        # 화면 경계 체크
        screen_width = self.root.winfo_screenwidth()
        if x < 10:
            x = 10
        elif x + bubble_width > screen_width - 10:
            x = screen_width - bubble_width - 10
        if y < 10:
            y = button_y + 80
        
        bubble.geometry(f"+{x}+{y}")
        
        self.collection_status_bubble = bubble
        self.collection_status_visible = True
        
        # 자동 닫기 타이머
        self.collection_bubble_auto_close_id = self.root.after(
            duration_ms, 
            self._close_collection_status_bubble
        )
    
    def show_collection_status_bubble(self):
        """현재 데이터 수집 상태를 말풍선으로 표시합니다."""
        if not self.is_collecting_data:
            return
        
        # 기존 말풍선 닫기
        self._close_collection_status_bubble()
        
        # 새 말풍선 생성
        bubble = tk.Toplevel(self.root)
        bubble.wm_overrideredirect(True)
        bubble.attributes('-topmost', True)
        bubble.configure(bg='white')
        
        # macOS 설정
        self._setup_window_for_macos(bubble, is_popup=True)
        
        # 메인 프레임
        main_frame = tk.Frame(bubble, bg='white', padx=2, pady=2)
        main_frame.pack(fill='both', expand=True)
        
        inner_frame = tk.Frame(main_frame, bg=COLORS["panel_bg"], padx=15, pady=15)
        inner_frame.pack(fill='both', expand=True)
        
        # 헤더 (닫기 버튼 제거 - 3초 후 자동 닫힘)
        tk.Label(
            inner_frame,
            text="📊 데이터 수집 현황",
            font=(self.default_font, 13, 'bold'),
            bg=COLORS["panel_bg"],
            fg=COLORS["text_primary"]
        ).pack(pady=(0, 10))
        
        # 진행률 바 배경
        progress_bg = tk.Frame(inner_frame, bg=COLORS["border"], height=8)
        progress_bg.pack(fill='x', pady=(0, 10))
        progress_bg.pack_propagate(False)
        
        # 진행률 바
        progress_width = max(int(self.collection_progress * 2.5), 1)  # 최대 250px
        progress_bar = tk.Frame(progress_bg, bg=COLORS["primary"], width=progress_width, height=8)
        progress_bar.pack(side='left')
        
        # 진행률 텍스트
        tk.Label(
            inner_frame,
            text=f"{int(self.collection_progress)}%",
            font=(self.default_font, 16, 'bold'),
            bg=COLORS["panel_bg"],
            fg=COLORS["primary"]
        ).pack(pady=(0, 5))
        
        # 현재 단계 메시지
        status_message = self._get_collection_status_detail()
        tk.Label(
            inner_frame,
            text=status_message,
            font=(self.default_font, 11),
            bg=COLORS["panel_bg"],
            fg=COLORS["text_secondary"],
            wraplength=250,
            justify='center'
        ).pack(pady=(0, 10))
        
        # 안내 메시지
        tk.Label(
            inner_frame,
            text="💡 이 작업은 보통 3~5분 정도 걸려요.\n조금만 기다려주세요.",
            font=(self.default_font, 10),
            bg=COLORS["panel_bg"],
            fg=COLORS["text_muted"],
            justify='center'
        ).pack()
        
        # 위치 계산
        bubble.update_idletasks()
        bubble_width = bubble.winfo_reqwidth()
        bubble_height = bubble.winfo_reqheight()
        
        button_x = self.root.winfo_x()
        button_y = self.root.winfo_y()
        
        x = button_x + 35 - bubble_width // 2
        y = button_y - bubble_height - 15
        
        # 화면 경계 체크
        screen_width = self.root.winfo_screenwidth()
        if x < 10:
            x = 10
        elif x + bubble_width > screen_width - 10:
            x = screen_width - bubble_width - 10
        if y < 10:
            y = button_y + 80
        
        bubble.geometry(f"+{x}+{y}")
        
        self.collection_status_bubble = bubble
        self.collection_status_visible = True
        
        # 3초 후 자동 닫기
        self.collection_bubble_auto_close_id = self.root.after(
            3000, 
            self._close_collection_status_bubble
        )
    
    def _get_collection_status_detail(self) -> str:
        """현재 진행률에 따른 상세 상태 메시지를 반환합니다."""
        progress = self.collection_progress
        
        if progress < 50:
            return "📁 파일을 스캔하고 있어요...\n선택하신 폴더에서 문서를 찾고 있습니다."
        elif progress < 65:
            return "🌐 브라우저 기록을 수집하고 있어요...\n최근 방문한 웹사이트를 확인하고 있습니다."
        elif progress < 85:
            return "📄 파일을 분석하고 있어요...\n문서에서 핵심 키워드를 추출하고 있습니다."
        elif progress < 95:
            return "🔍 웹 콘텐츠를 분석하고 있어요...\n방문한 웹페이지의 내용을 분석하고 있습니다."
        else:
            return "✨ 마무리 중이에요...\n거의 다 됐습니다!"
    
    def _close_collection_status_bubble(self):
        """수집 상태 말풍선을 닫습니다."""
        if self.collection_bubble_auto_close_id:
            self.root.after_cancel(self.collection_bubble_auto_close_id)
            self.collection_bubble_auto_close_id = None
        
        if self.collection_status_bubble and self.collection_status_bubble.winfo_exists():
            self.collection_status_bubble.destroy()
        
        self.collection_status_bubble = None
        self.collection_status_visible = False
    
    def _check_and_start_collection_mode(self):
        """환경 변수를 확인하여 데이터 수집 모드로 시작할지 결정합니다."""
        start_collection = os.environ.get("JARVIS_START_COLLECTION", "0")
        
        if start_collection == "1":
            # 선택된 폴더 목록 파싱
            selected_folders_json = os.environ.get("JARVIS_SELECTED_FOLDERS", "[]")
            try:
                selected_folders = json.loads(selected_folders_json)
            except json.JSONDecodeError:
                selected_folders = []
            
            print(f"[Collection] 데이터 수집 모드로 시작합니다. 폴더: {len(selected_folders)}개")
            
            # 약간의 딜레이 후 수집 모드 시작 (UI가 완전히 로드된 후)
            self.root.after(500, lambda: self.start_data_collection_mode(selected_folders))
        
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
    
        
        # 헤더 (높이 증가)
        header_frame = tk.Frame(self.chat_window, bg=COLORS["primary"], height=100)
        header_frame.pack(fill='x', padx=0, pady=0)
        header_frame.pack_propagate(False)
        
        # 제목과 부제목을 담을 프레임
        title_container = tk.Frame(header_frame, bg=COLORS["primary"])
        title_container.pack(side='left', fill='both', expand=True, padx=20, pady=15)
        
        # 제목
        title_label = tk.Label(
            title_container,
            text="JARVIS AI Assistant",
            font=self.title_font,
            bg=COLORS["primary"],
            fg=COLORS["text_inverse"]
        )
        title_label.pack(anchor='w')
        
        # 부제목
        subtitle_label = tk.Label(
            title_container,
            text="Multi-Agent System",
            font=self.subtitle_font,
            bg='#4f46e5',
            fg=COLORS["primary_soft"]
        )
        subtitle_label.pack(anchor='w', pady=(5, 0))
        
        # --- 버튼 컨테이너 ---
        buttons_container = tk.Frame(header_frame, bg=COLORS["primary"])
        buttons_container.pack(side='right', padx=15, pady=25)

        # 대시보드 버튼
        dashboard_button = tk.Button(
            buttons_container,
            text="📊",
            font=('Arial', 18),
            bg=COLORS["primary"],
            fg=COLORS["text_inverse"],
            relief='flat',
            cursor='hand2',
            command=self.open_dashboard_window,
            activebackground='#4338CA',
            activeforeground='white'
        )
        dashboard_button.pack(side='left', padx=(0, 5))

        # 추천 내역 버튼
        recommendation_button = tk.Button(
            buttons_container,
            text="💡",
            font=('Arial', 18),
            bg=COLORS["primary"],
            fg=COLORS["text_inverse"],
            relief='flat',
            cursor='hand2',
            command=self.open_recommendation_window,
            activebackground='#4338CA',
            activeforeground='white'
        )
        recommendation_button.pack(side='left', padx=(0, 5))

        # 폴더 변경 버튼
        folder_button = tk.Button(
            buttons_container,
            text="📁",
            font=('Arial', 18),
            bg=COLORS["primary"],
            fg=COLORS["text_inverse"],
            relief='flat',
            cursor='hand2',
            command=self.prompt_change_data_folder,
            activebackground='#4338CA',
            activeforeground='white'
        )
        folder_button.pack(side='left', padx=(0, 5))
        
        # 설정 버튼
        settings_button = tk.Button(
            buttons_container,
            text="⚙️",
            font=('Arial', 18),
            bg=COLORS["primary"],
            fg=COLORS["text_inverse"],
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
        self._bind_canvas_scroll_events(self.messages_frame)
        
        # 스크롤 가능한 메시지 영역
        self.messages_canvas = tk.Canvas(self.messages_frame, bg='white', highlightthickness=0)
        self._bind_canvas_scroll_events(self.messages_canvas)
        scrollbar = ttk.Scrollbar(self.messages_frame, orient="vertical", command=self.messages_canvas.yview)
        self.scrollable_frame = tk.Frame(self.messages_canvas, bg='white')
        self._bind_canvas_scroll_events(self.scrollable_frame)
        
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
            bg=COLORS["surface_alt"],
            fg='black'  # 글자색을 검은색으로 설정
        )
        self.message_input.pack(side='left', fill='x', expand=True, padx=(0, 15))
        self.message_input.bind('<Return>', self.send_message)
        
        # 전송 버튼
        send_button = tk.Button(
            input_frame,
            text="전송",
            font=self.button_font,
            command=self.send_message,
            width=8,
            height=2
        )
        self._style_button(send_button, variant="secondary")
        send_button.pack(side='right')
        
        # 초기 메시지
        self.add_bot_message("안녕하세요! JARVIS AI Assistant입니다. 무엇을 도와드릴까요?")
        
        # 채팅창 초기에는 숨김
        self.chat_window.withdraw()
        
        # 채팅창 닫기 이벤트 바인딩
        self.chat_window.protocol("WM_DELETE_WINDOW", self.close_chat_window)
    
    def open_dashboard_window(self):
        """대시보드 창을 엽니다."""
        # 이미 열려있으면 포커스
        if self.dashboard_window and self.dashboard_window.is_open():
            self.dashboard_window.show()
            return
        
        # 로그인 상태 확인 (통합 헬퍼 사용)
        if not self.ensure_logged_in():
            return
        
        # 대시보드 창 생성
        self.dashboard_window = DashboardWindow(
            parent_app=self,
            user_id=self.user_id,
            jwt_token=self.jwt_token
        )
        
    def open_recommendation_window(self):
        """추천 내역을 보여주는 새 창을 엽니다 (카드 기반 UI)."""
        rec_window = tk.Toplevel(self.chat_window)
        rec_window.title("JARVIS 추천 내역")
        rec_window.geometry("650x600")
        rec_window.configure(bg=COLORS["surface"])
        rec_window.attributes('-topmost', True)
        
        # 페이지네이션 상태 저장
        rec_window.recommendations_data = []
        rec_window.current_page = 0
        rec_window.items_per_page = 5

        # --- 상단 프레임: 제목 ---
        top_frame = tk.Frame(rec_window, bg=COLORS["primary"], height=60)
        top_frame.pack(fill='x')
        top_frame.pack_propagate(False)

        title_label = tk.Label(
            top_frame, 
            text="💡 추천 히스토리", 
            font=(self.default_font, 16, 'bold'), 
            bg=COLORS["primary"], 
            fg=COLORS["text_inverse"]
        )
        title_label.pack(side='left', padx=20, pady=15)

        # --- 카드 목록 영역 (Canvas + Frame + Scrollbar) ---
        cards_container = tk.Frame(rec_window, bg=COLORS["panel_bg"])
        cards_container.pack(fill='both', expand=True, padx=15, pady=10)
        
        # Canvas와 Scrollbar 설정
        cards_canvas = tk.Canvas(cards_container, bg=COLORS["panel_bg"], highlightthickness=0)
        cards_scrollbar = ttk.Scrollbar(cards_container, orient="vertical", command=cards_canvas.yview)
        cards_frame = tk.Frame(cards_canvas, bg=COLORS["panel_bg"])
        
        cards_canvas_window = cards_canvas.create_window((0, 0), window=cards_frame, anchor="nw")
        
        def configure_cards_scroll(event):
            cards_canvas.configure(scrollregion=cards_canvas.bbox("all"))
            # 캔버스 너비에 맞춰 프레임 너비 조정
            canvas_width = event.width
            if canvas_width > 1:
                cards_canvas.itemconfig(cards_canvas_window, width=canvas_width)
        
        cards_frame.bind("<Configure>", configure_cards_scroll)
        cards_canvas.bind("<Configure>", configure_cards_scroll)
        cards_canvas.configure(yscrollcommand=cards_scrollbar.set)
        
        # 마우스 휠 스크롤 (Windows, macOS, Linux 모두 지원)
        def on_cards_mousewheel(event):
            system = platform.system()
            if system == "Darwin":
                # macOS: delta 값이 작음 (-1 ~ 1 정도)
                cards_canvas.yview_scroll(-1 * event.delta, "units")
            elif event.delta:
                # Windows: delta가 120 단위
                cards_canvas.yview_scroll(-1 * (event.delta // 120), "units")
            elif event.num == 4:
                # Linux: Button-4 = 위로 스크롤
                cards_canvas.yview_scroll(-1, "units")
            elif event.num == 5:
                # Linux: Button-5 = 아래로 스크롤
                cards_canvas.yview_scroll(1, "units")
        
        # 캔버스와 프레임에 스크롤 이벤트 바인딩
        cards_canvas.bind("<MouseWheel>", on_cards_mousewheel)
        cards_canvas.bind("<Button-4>", on_cards_mousewheel)
        cards_canvas.bind("<Button-5>", on_cards_mousewheel)
        cards_frame.bind("<MouseWheel>", on_cards_mousewheel)
        cards_frame.bind("<Button-4>", on_cards_mousewheel)
        cards_frame.bind("<Button-5>", on_cards_mousewheel)
        
        # 자식 위젯들에도 스크롤 이벤트 전파를 위한 헬퍼 함수
        def bind_scroll_to_children(widget):
            widget.bind("<MouseWheel>", on_cards_mousewheel)
            widget.bind("<Button-4>", on_cards_mousewheel)
            widget.bind("<Button-5>", on_cards_mousewheel)
            for child in widget.winfo_children():
                bind_scroll_to_children(child)
        
        rec_window.bind_scroll_to_children = bind_scroll_to_children
        
        cards_canvas.pack(side="left", fill="both", expand=True)
        cards_scrollbar.pack(side="right", fill="y")
        
        # 참조 저장
        rec_window.cards_frame = cards_frame
        rec_window.cards_canvas = cards_canvas
        rec_window.on_cards_mousewheel = on_cards_mousewheel

        # --- 하단 페이지네이션 프레임 ---
        pagination_frame = tk.Frame(rec_window, bg=COLORS["surface"], height=50)
        pagination_frame.pack(fill='x', padx=15, pady=(0, 10))
        pagination_frame.pack_propagate(False)
        
        # 이전 버튼
        prev_btn = tk.Button(
            pagination_frame,
            text="◀ 이전",
            font=(self.default_font, 10),
            padx=15,
            pady=5,
            command=lambda: self._change_recommendation_page(rec_window, -1)
        )
        self._style_button(prev_btn, variant="secondary", disabled=True)
        prev_btn.pack(side='left', padx=(0, 10))
        rec_window.prev_btn = prev_btn
        
        # 페이지 정보 라벨
        page_label = tk.Label(
            pagination_frame,
            text="",
            font=(self.default_font, 10),
            bg=COLORS["surface"],
            fg=COLORS["text_muted"]
        )
        page_label.pack(side='left', expand=True)
        rec_window.page_label = page_label
        
        # 다음 버튼
        next_btn = tk.Button(
            pagination_frame,
            text="다음 ▶",
            font=(self.default_font, 10),
            padx=15,
            pady=5,
            command=lambda: self._change_recommendation_page(rec_window, 1)
        )
        self._style_button(next_btn, variant="secondary", disabled=True)
        next_btn.pack(side='right', padx=(10, 0))
        rec_window.next_btn = next_btn

        # 추천 내역 로드
        self._load_recommendation_cards(rec_window)

    def _load_recommendation_cards(self, rec_window):
        """백그라운드에서 추천 내역을 불러와 카드로 표시합니다."""
        # 로딩 상태 표시
        self._show_recommendation_loading(rec_window)
        threading.Thread(
            target=self._fetch_recommendation_cards, 
            args=(rec_window,), 
            daemon=True
        ).start()

    def _fetch_recommendation_cards(self, rec_window):
        """[백그라운드 스레드] 추천 히스토리 API를 호출합니다."""
        try:
            from login_view import get_stored_token
            token = get_stored_token()
            if not token:
                self.root.after(0, lambda: self._show_recommendation_error(rec_window, "로그인이 필요합니다."))
                return

            response = requests.get(
                f"{self.API_BASE_URL}/api/v2/recommendations/history",
                headers={"Authorization": f"Bearer {token}"},
                timeout=15
            )

            if response.status_code == 200:
                result = response.json()
                if result.get("success") and result.get("recommendations"):
                    recommendations = result["recommendations"]
                    self.root.after(0, lambda: self._render_recommendation_cards(rec_window, recommendations))
                else:
                    self.root.after(0, lambda: self._show_recommendation_empty(rec_window))
            else:
                error_msg = response.json().get("detail", "알 수 없는 오류")
                self.root.after(0, lambda: self._show_recommendation_error(rec_window, error_msg))

        except requests.exceptions.RequestException as e:
            error_str = str(e)
            self.root.after(0, lambda err=error_str: self._show_recommendation_error(rec_window, f"서버 연결 오류: {err}"))

    def _show_recommendation_loading(self, rec_window):
        """로딩 상태를 표시합니다."""
        cards_frame = rec_window.cards_frame
        for widget in cards_frame.winfo_children():
            widget.destroy()
        
        loading_frame = tk.Frame(cards_frame, bg=COLORS["panel_bg"])
        loading_frame.pack(fill='both', expand=True, pady=100)
        
        tk.Label(
            loading_frame,
            text="⏳",
            font=('Arial', 32),
            bg=COLORS["panel_bg"]
        ).pack()
        
        tk.Label(
            loading_frame,
            text="추천 내역을 불러오는 중...",
            font=(self.default_font, 12),
            bg=COLORS["panel_bg"],
            fg=COLORS["text_muted"]
        ).pack(pady=(10, 0))

        rec_window.page_label.config(text="")
        rec_window.prev_btn.config(state='disabled')
        rec_window.next_btn.config(state='disabled')
        self._style_button(rec_window.prev_btn, variant="secondary", disabled=True)
        self._style_button(rec_window.next_btn, variant="secondary", disabled=True)

    def _show_recommendation_empty(self, rec_window):
        """빈 상태를 표시합니다."""
        cards_frame = rec_window.cards_frame
        for widget in cards_frame.winfo_children():
            widget.destroy()
        
        empty_frame = tk.Frame(cards_frame, bg=COLORS["panel_bg"])
        empty_frame.pack(fill='both', expand=True, pady=100)
        
        tk.Label(
            empty_frame,
            text="💭",
            font=('Arial', 48),
            bg=COLORS["panel_bg"]
        ).pack()
        
        tk.Label(
            empty_frame,
            text="아직 추천이 없어요",
            font=(self.default_font, 14, 'bold'),
            bg=COLORS["panel_bg"],
            fg=COLORS["text_secondary"]
        ).pack(pady=(15, 5))
        
        tk.Label(
            empty_frame,
            text="활동을 계속하면 맞춤형 추천을 준비해 드릴게요!",
            font=(self.default_font, 11),
            bg=COLORS["panel_bg"],
            fg=COLORS["text_muted"]
        ).pack()
        
        # 페이지네이션 숨기기
        rec_window.page_label.config(text="")
        rec_window.prev_btn.config(state='disabled')
        rec_window.next_btn.config(state='disabled')
        self._style_button(rec_window.prev_btn, variant="secondary", disabled=True)
        self._style_button(rec_window.next_btn, variant="secondary", disabled=True)

    def _show_recommendation_error(self, rec_window, error_msg):
        """에러 상태를 표시합니다."""
        cards_frame = rec_window.cards_frame
        for widget in cards_frame.winfo_children():
            widget.destroy()
        
        error_frame = tk.Frame(cards_frame, bg=COLORS["danger_bg"], padx=20, pady=20)
        error_frame.pack(fill='x', padx=20, pady=50)
        
        tk.Label(
            error_frame,
            text="❌",
            font=('Arial', 24),
            bg=COLORS["danger_bg"]
        ).pack()
        
        tk.Label(
            error_frame,
            text="오류가 발생했습니다",
            font=(self.default_font, 12, 'bold'),
            bg=COLORS["danger_bg"],
            fg=COLORS["danger_text"]
        ).pack(pady=(10, 5))
        
        tk.Label(
            error_frame,
            text=error_msg,
            font=(self.default_font, 10),
            bg=COLORS["danger_bg"],
            fg=COLORS["danger_text"],
            wraplength=400
        ).pack()
        
        # 페이지네이션 숨기기
        rec_window.page_label.config(text="")
        rec_window.prev_btn.config(state='disabled')
        rec_window.next_btn.config(state='disabled')
        self._style_button(rec_window.prev_btn, variant="secondary", disabled=True)
        self._style_button(rec_window.next_btn, variant="secondary", disabled=True)

    def _render_recommendation_cards(self, rec_window, recommendations):
        """추천 카드들을 렌더링합니다."""
        rec_window.recommendations_data = recommendations
        rec_window.current_page = 0
        self._render_current_page(rec_window)

    def _render_current_page(self, rec_window):
        """현재 페이지의 카드들을 렌더링합니다."""
        cards_frame = rec_window.cards_frame
        
        # 기존 카드 제거
        for widget in cards_frame.winfo_children():
            widget.destroy()
        
        recommendations = rec_window.recommendations_data
        current_page = rec_window.current_page
        items_per_page = rec_window.items_per_page
        
        # 페이지 계산
        total_items = len(recommendations)
        total_pages = (total_items + items_per_page - 1) // items_per_page
        start_idx = current_page * items_per_page
        end_idx = min(start_idx + items_per_page, total_items)
        
        page_items = recommendations[start_idx:end_idx]
        
        # 카드 렌더링
        for rec in page_items:
            self._create_recommendation_card(cards_frame, rec, rec_window)
        
        # 페이지네이션 업데이트
        rec_window.page_label.config(text=f"{current_page + 1} / {total_pages} 페이지 (총 {total_items}개)")
        prev_enabled = current_page > 0
        next_enabled = current_page < total_pages - 1
        rec_window.prev_btn.config(state='normal' if prev_enabled else 'disabled')
        rec_window.next_btn.config(state='normal' if next_enabled else 'disabled')
        self._style_button(rec_window.prev_btn, variant="secondary", disabled=not prev_enabled)
        self._style_button(rec_window.next_btn, variant="secondary", disabled=not next_enabled)
        
        # 스크롤 맨 위로
        rec_window.cards_canvas.yview_moveto(0)

    def _create_recommendation_card(self, parent, rec, rec_window):
        """개별 추천 카드를 생성합니다."""
        # 데이터 추출
        rec_id = rec.get('id')
        keyword = rec.get('keyword') or "추천"
        bubble_message = rec.get('bubble_message') or ""
        report_content = rec.get('report_content') or bubble_message
        status = rec.get('status', 'pending')
        report_file_path = rec.get('report_file_path')
        
        # 날짜 파싱
        created_at = rec.get('created_at')
        if isinstance(created_at, str):
            try:
                dt = datetime.fromisoformat(created_at)
            except ValueError:
                dt = datetime.now()
        elif isinstance(created_at, (int, float)):
            dt = datetime.fromtimestamp(created_at)
        else:
            dt = datetime.now()
        date_str = dt.strftime('%Y-%m-%d %H:%M')
        
        # 상태 텍스트/색상
        status_texts = {
            'pending': '대기',
            'accepted': '수락',
            'rejected': '거절',
            'shown': '표시됨',
            'completed': '완료',
        }
        status_text = status_texts.get(status, '알 수 없음')
        
        # 카드 프레임
        card = tk.Frame(
            parent,
            bg=COLORS["surface"],
            highlightbackground=COLORS["border"],
            highlightcolor=COLORS["border"],
            highlightthickness=1,
            bd=0
        )
        card.pack(fill='x', padx=10, pady=8)
        
        # 카드 내부 패딩
        card_inner = tk.Frame(card, bg=COLORS["surface"], padx=15, pady=12)
        card_inner.pack(fill='x')
        
        # --- 헤더: 키워드 + 상태 배지 ---
        header_frame = tk.Frame(card_inner, bg=COLORS["surface"])
        header_frame.pack(fill='x')
        
        # 키워드
        keyword_label = tk.Label(
            header_frame,
            text=f"🔑 {keyword}",
            font=(self.default_font, 12, 'bold'),
            bg=COLORS["surface"],
            fg=COLORS["text_primary"]
        )
        keyword_label.pack(side='left')
        
        # 상태 배지
        status_style = self._get_status_badge_style(status)
        status_badge = tk.Label(
            header_frame,
            text=status_text,
            font=(self.default_font, 9),
            bg=status_style["bg"],
            fg=status_style["fg"],
            padx=8,
            pady=2
        )
        status_badge.pack(side='right')
        
        # --- 날짜 ---
        date_label = tk.Label(
            card_inner,
            text=f"📅 {date_str}",
            font=(self.default_font, 9),
            bg=COLORS["surface"],
            fg=COLORS["text_muted"]
        )
        date_label.pack(anchor='w', pady=(5, 0))
        
        # --- 요약 + 툴팁 아이콘 ---
        summary_frame = tk.Frame(card_inner, bg=COLORS["surface"])
        summary_frame.pack(fill='x', pady=(8, 0))
        
        # 요약 텍스트 (최대 100자)
        summary_text = report_content[:100] + "..." if len(report_content) > 100 else report_content
        summary_text = summary_text.replace('\n', ' ')  # 줄바꿈 제거
        
        summary_label = tk.Label(
            summary_frame,
            text=summary_text,
            font=(self.default_font, 10),
            bg=COLORS["surface"],
            fg=COLORS["text_secondary"],
            wraplength=450,
            justify='left',
            anchor='w'
        )
        summary_label.pack(side='left', fill='x', expand=True)
        
        # 툴팁 아이콘 (전체 내용 보기)
        if len(report_content) > 100:
            info_icon = tk.Label(
                summary_frame,
                text="ℹ️",
                font=('Arial', 12),
                bg=COLORS["surface"],
                cursor='hand2'
            )
            info_icon.pack(side='right', padx=(5, 0))
            
            # 툴팁 이벤트 바인딩
            info_icon.bind("<Enter>", lambda e, content=report_content, kw=keyword: 
                          self._show_recommendation_tooltip(e, kw, content, rec_window))
            info_icon.bind("<Leave>", lambda e: self._hide_recommendation_tooltip(rec_window))
        
        # --- 액션 버튼 ---
        button_frame = tk.Frame(card_inner, bg=COLORS["surface"])
        button_frame.pack(fill='x', pady=(12, 0))
        
        # 보고서 열기 버튼 (report_file_path가 있을 때만 활성)
        open_btn = tk.Button(
            button_frame,
            text="📄 보고서 열기",
            font=(self.default_font, 9),
            padx=10,
            pady=4,
            command=lambda path=report_file_path: self._open_report_file(path) if path else None
        )
        self._style_button(open_btn, variant="outlined", disabled=not bool(report_file_path))
        open_btn.pack(side='left', padx=(0, 8))
        
        # 관심 없음 버튼 (이미 거절된 상태가 아닐 때만)
        if status != 'rejected':
            reject_btn = tk.Button(
                button_frame,
                text="🚫 관심 없음",
                font=(self.default_font, 9),
                padx=10,
                pady=4,
                command=lambda rid=rec_id, win=rec_window: self._reject_from_history(rid, win)
            )
            self._style_button(reject_btn, variant="danger")
            reject_btn.pack(side='left')
        
        # 카드와 모든 자식 위젯에 스크롤 이벤트 바인딩
        if hasattr(rec_window, 'bind_scroll_to_children'):
            rec_window.bind_scroll_to_children(card)

    def _show_recommendation_tooltip(self, event, keyword, content, rec_window):
        """추천 카드의 전체 내용 툴팁을 표시합니다."""
        # 기존 툴팁 제거
        self._hide_recommendation_tooltip(rec_window)
        
        # 툴팁 윈도우 생성
        tooltip = tk.Toplevel(self.root)
        tooltip.wm_overrideredirect(True)
        tooltip.configure(bg='white', relief='solid', borderwidth=1)
        tooltip.attributes('-topmost', True)
        
        rec_window.recommendation_tooltip = tooltip
        
        # 내용 프레임
        frame = tk.Frame(tooltip, bg='white', padx=12, pady=12)
        frame.pack(fill='both', expand=True)
        
        # 제목
        tk.Label(
            frame,
            text=f"🔑 {keyword}",
            font=(self.default_font, 11, 'bold'),
            bg='white',
            fg='#1f2937'
        ).pack(anchor='w')
        
        # 구분선
        tk.Frame(frame, height=1, bg=COLORS["border"]).pack(fill='x', pady=8)
        
        # 본문 (스크롤 가능)
        body_frame = tk.Frame(frame, bg='white')
        body_frame.pack(fill='both', expand=True)
        
        scrollbar = ttk.Scrollbar(body_frame, orient='vertical')
        scrollbar.pack(side='right', fill='y')
        
        body_text = tk.Text(
            body_frame,
            font=(self.default_font, 10),
            bg='white',
            fg='#4b5563',
            wrap='word',
            relief='flat',
            borderwidth=0,
            height=15,
            width=50
        )
        body_text.pack(side='left', fill='both', expand=True)
        body_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.configure(command=body_text.yview)
        
        body_text.insert('1.0', content)
        body_text.config(state='disabled')
        
        # 위치 계산
        tooltip.update_idletasks()
        tooltip_width = tooltip.winfo_reqwidth()
        tooltip_height = tooltip.winfo_reqheight()
        
        screen_width = tooltip.winfo_screenwidth()
        screen_height = tooltip.winfo_screenheight()
        
        x = event.x_root + 15
        y = event.y_root + 15
        
        # 화면 경계 보정
        if x + tooltip_width > screen_width:
            x = event.x_root - tooltip_width - 15
        if y + tooltip_height > screen_height:
            y = event.y_root - tooltip_height - 15
        
        tooltip.geometry(f"+{x}+{y}")

    def _hide_recommendation_tooltip(self, rec_window):
        """추천 카드 툴팁을 숨깁니다."""
        if hasattr(rec_window, 'recommendation_tooltip') and rec_window.recommendation_tooltip:
            try:
                rec_window.recommendation_tooltip.destroy()
            except:
                pass
            rec_window.recommendation_tooltip = None

    def _change_recommendation_page(self, rec_window, delta):
        """페이지를 변경합니다."""
        rec_window.current_page += delta
        self._render_current_page(rec_window)

    def _open_report_file(self, file_path):
        """보고서 파일을 엽니다."""
        try:
            if not file_path or not os.path.exists(file_path):
                print(f"[UI] 파일을 찾을 수 없습니다: {file_path}")
                return
            
            system = platform.system()
            if system == "Windows":
                os.startfile(file_path)
            elif system == "Darwin":
                subprocess.call(['open', file_path])
            else:
                subprocess.call(['xdg-open', file_path])
            
            print(f"[UI] 보고서 파일 열기: {file_path}")
        except Exception as e:
            print(f"[UI] 파일 열기 오류: {e}")

    def _reject_from_history(self, recommendation_id, rec_window):
        """히스토리에서 추천을 거절합니다 (블랙리스트 추가)."""
        print(f"[UI] 히스토리에서 추천 {recommendation_id} 거절")
        
        def do_reject():
            try:
                from login_view import get_stored_token
                token = get_stored_token()
                if not token:
                    return
                
                response = requests.post(
                    f"{self.API_BASE_URL}/api/v2/recommendations/{recommendation_id}/respond",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"action": "reject"},
                    timeout=15
                )
                
                if response.status_code == 200:
                    # 성공 시 카드 목록 새로고침
                    self.root.after(0, lambda: self._load_recommendation_cards(rec_window))
                else:
                    print(f"[UI] 거절 실패: {response.status_code}")
                    
            except Exception as e:
                print(f"[UI] 거절 API 호출 오류: {e}")
        
        threading.Thread(target=do_reject, daemon=True).start()

    def refresh_recommendation_window(self, window):
        """추천 창의 내용을 새로고침합니다."""
        if hasattr(window, 'cards_frame'):
            self._load_recommendation_cards(window)

    def update_text_widget(self, text_widget, content):
        """[메인 스레드 호출용] 텍스트 위젯 내용을 안전하게 업데이트합니다."""
        def _update():
            text_widget.config(state='normal')
            text_widget.delete('1.0', 'end')
            text_widget.insert('1.0', content)
            text_widget.config(state='disabled')
        self.root.after(0, _update)

    def format_recommendations(self, recommendations: list) -> str:
        """추천 목록을 서식이 있는 텍스트로 변환합니다. (Legacy 호환용)"""
        formatted_lines = []
        for rec in recommendations:
            # created_at이 문자열(ISO format)인 경우와 Unix timestamp인 경우 모두 처리
            created_at = rec.get('created_at')
            if isinstance(created_at, str):
                try:
                    dt = datetime.fromisoformat(created_at)
                except ValueError:
                    dt = datetime.now()
            elif isinstance(created_at, (int, float)):
                dt = datetime.fromtimestamp(created_at)
            else:
                dt = datetime.now()
            
            date_str = dt.strftime('%Y-%m-%d %H:%M')
            # trigger_type으로 생성 유형 표시
            trigger_type = rec.get('trigger_type', '')
            rec_type = "수동 생성" if trigger_type == 'manual' else "자동 생성"
            
            # 실제 DB 필드명에 맞게 수정: bubble_message, report_content, keyword
            title = rec.get('bubble_message') or rec.get('keyword') or "추천"
            content = rec.get('report_content') or rec.get('bubble_message') or ""
            
            formatted_lines.append(f"## {title} ##")
            formatted_lines.append(f"[{date_str} | {rec_type}]")
            formatted_lines.append(f"{content}")
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
        system = platform.system()
        
        if system == "Darwin":  # macOS
            # macOS는 delta가 매우 작은 값 (보통 -1 ~ 1)
            delta = -1 * event.delta
        elif event.delta:
            # Windows는 delta가 120 단위
            delta = -1 * (event.delta / 120)
        else:
            # Linux는 Button-4/5 사용
            delta = -1 if event.num == 4 else 1
        
        # 스크롤 실행
        self.messages_canvas.yview_scroll(int(delta), "units")
    
    def _update_messages_scrollregion(self):
        """메시지 영역의 스크롤 범위를 최신 상태로 유지"""
        if hasattr(self, 'messages_canvas') and self.messages_canvas.winfo_exists():
            self.messages_canvas.update_idletasks()
            bbox = self.messages_canvas.bbox("all")
            if bbox:
                self.messages_canvas.configure(scrollregion=bbox)
    
    def _calculate_display_lines(self, text_widget, force_tk=False):
        """현재 위젯의 실제 표시 라인 수를 계산합니다 (word wrap 고려).
        
        Args:
            text_widget: 대상 Text 위젯
            force_tk: True이면 Tk 값과 추정치 중 큰 값을 반환 (보수적 계산)
        """
        if not text_widget or not text_widget.winfo_exists():
            return 1

        text_widget.update_idletasks()

        # 1) 텍스트 내용 기반 예상 줄 수 계산 (fallback)
        try:
            content = text_widget.get('1.0', 'end-1c')
        except Exception:
            return 1

        if not content.strip():
            return 1

        lines = content.split('\n')
        estimated_lines = 0

        # Text 위젯 width(문자 수)를 기준으로 대략적인 wrap 계산
        try:
            max_chars = int(text_widget.cget('width'))
        except Exception:
            max_chars = 35  # 실패 시 기본값

        for line in lines:
            if not line.strip():
                estimated_lines += 1
            else:
                # 한글 기준으로 1줄당 max_chars * 0.7 정도로 가정
                approx_per_line = max(1, int(max_chars * 0.7))
                estimated_lines += max(1, (len(line) + approx_per_line - 1) // approx_per_line)

        estimated_lines = max(1, estimated_lines)

        # 2) Tk의 displaylines 결과 얻기
        tk_lines = 1
        try:
            result = text_widget.tk.call(
                text_widget._w, 'count', '-update', '-displaylines', '1.0', 'end-1c'
            )
            if isinstance(result, (list, tuple)):
                result = result[0]
            tk_lines = max(1, int(result))
        except Exception:
            tk_lines = 1

        # 3) 최종 결정
        if force_tk:
            # force_tk=True: Tk 값을 신뢰하되, 너무 작으면 추정치 사용
            if tk_lines < estimated_lines:
                return estimated_lines
            return tk_lines
        else:
            # force_tk=False: Tk 값이 비정상적으로 크면(2배 이상) 추정치 사용
            if tk_lines > estimated_lines * 2:
                return estimated_lines
            else:
                return tk_lines
    
    def _adjust_text_widget_height(self, text_widget, force_tk=False):
        """텍스트 위젯의 높이를 텍스트 내용에 맞게 정확하게 조정합니다.
        
        Args:
            text_widget: 대상 Text 위젯
            force_tk: True이면 Tk count 결과를 무조건 신뢰 (렌더링 완료 후 호출 시)
        """
        if not text_widget or not text_widget.winfo_exists():
            return
        
        try:
            text_height = self._calculate_display_lines(text_widget, force_tk=force_tk)
            text_widget.config(height=text_height)
        except Exception as e:
            # 오류 발생 시 기본 높이 유지
            pass

    def _remove_trailing_newline(self, text_widget):
        """텍스트 위젯 끝에 자동으로 추가된 개행을 안전하게 제거"""
        if not text_widget or not text_widget.winfo_exists():
            return
        try:
            # end-1c 는 항상 개행이므로, 최소 길이를 확인한 뒤 제거
            if text_widget.compare('end-1c', '==', '1.0'):
                # 내용이 없는 경우
                return
            # 실제 마지막 문자 확인
            last_char_index = text_widget.index('end-2c')
            if text_widget.get(last_char_index, 'end-1c') == '\n':
                text_widget.delete(last_char_index, 'end-1c')
        except tk.TclError:
            # 텍스트 길이가 짧아 인덱스 계산이 불가능한 경우 무시
            pass
    
    def _bind_canvas_scroll_events(self, widget):
        """canvas와 동일한 스크롤 동작을 위젯에 바인딩"""
        if widget:
            widget.bind("<MouseWheel>", self._on_mousewheel)
            widget.bind("<Button-4>", self._on_mousewheel)
            widget.bind("<Button-5>", self._on_mousewheel)
    
    def _disable_text_widget_scroll(self, text_widget):
        """Text 위젯의 내부 스크롤을 비활성화하고, 대신 canvas 스크롤로 전달합니다."""
        if not text_widget:
            return
        # Text 위젯의 기본 스크롤 동작을 막고, canvas 스크롤로 전달
        text_widget.bind("<MouseWheel>", lambda e: (self._on_mousewheel(e), "break")[1])
        text_widget.bind("<Button-4>", lambda e: (self._on_mousewheel(e), "break")[1])
        text_widget.bind("<Button-5>", lambda e: (self._on_mousewheel(e), "break")[1])
    
    def _bind_popup_text_scroll(self, text_widget):
        """팝업 내 텍스트 위젯 스크롤 바인딩"""
        if not text_widget:
            return
        text_widget.bind("<MouseWheel>", lambda e: self._on_popup_mousewheel(e, text_widget))
        text_widget.bind("<Button-4>", lambda e: self._on_popup_mousewheel(e, text_widget))
        text_widget.bind("<Button-5>", lambda e: self._on_popup_mousewheel(e, text_widget))
    
    def _on_popup_mousewheel(self, event, text_widget):
        """팝업 텍스트 위젯용 스크롤 처리"""
        system = platform.system()
        
        if system == "Darwin":  # macOS
            delta = -1 * event.delta
        elif event.delta:
            delta = -1 * (event.delta / 120)  # Windows
        else:
            delta = -1 if event.num == 4 else 1  # Linux
        
        text_widget.yview_scroll(int(delta), "units")
        return "break"
    
    def _update_citation_details(self, text_widget, content):
        """참고 문헌 섹션에서 라벨 및 본문 정보를 추출하고 표시를 정리합니다."""
        if "[참고 문헌]" not in content:
            return
        
        ref_start = content.find("[참고 문헌]")
        if ref_start == -1:
            return
        
        ref_lines = content[ref_start:].splitlines()
        if len(ref_lines) <= 1:
            return
        
        details = {}
        current_num = None
        body_lines = []
        
        for line in ref_lines[1:]:
            stripped = line.strip()
            if not stripped:
                continue
            
            if stripped.startswith('[') and ']' in stripped:
                if current_num and body_lines:
                    details[current_num]["body"] = " ".join(body_lines).strip()
                body_lines = []
                num = stripped[1:stripped.find(']')].strip()
                if not num:
                    continue
                label = stripped[stripped.find(']') + 1:].strip()
                details[num] = {
                    "label": label or f"출처 {num}",
                    "body": ""
                }
                current_num = num
            else:
                if current_num:
                    cleaned = stripped.lstrip('>').strip()
                    if cleaned:
                        body_lines.append(cleaned)
        
        if current_num and current_num in details and body_lines:
            details[current_num]["body"] = " ".join(body_lines).strip()
        
        if not details:
            return
        
        text_widget.citation_details = details
        self._rewrite_reference_section(text_widget, details)
    
    def _rewrite_reference_section(self, text_widget, details):
        """참고 문헌 섹션을 라벨만 남도록 재작성합니다."""
        ref_idx = text_widget.search("[참고 문헌]", "1.0", tk.END)
        if not ref_idx:
            return
        
        start_idx = ref_idx
        end_idx = "end-1c"
        text_widget.delete(start_idx, end_idx)
        
        lines = ["[참고 문헌]", ""]
        for num in sorted(details.keys(), key=lambda x: (0, int(x)) if x.isdigit() else (1, x)):
            label = details[num].get("label", f"출처 {num}")
            lines.append(f"[{num}] {label}")
        
        reference_text = "\n".join(lines).strip()
        if reference_text:
            text_widget.insert("end", "\n" + reference_text + "\n")
        
    def add_user_message(self, message):
        """사용자 메시지 추가"""
        message_frame = tk.Frame(self.scrollable_frame, bg='white')
        message_frame.pack(fill='x', pady=8)
        self._bind_canvas_scroll_events(message_frame)
        
        # 사용자 메시지 컨테이너 (우측 정렬)
        user_container = tk.Frame(message_frame, bg='white')
        user_container.pack(side='right', padx=(50, 15))
        self._bind_canvas_scroll_events(user_container)
        
        # 사용자 메시지 (Text 위젯으로 변경하여 텍스트 선택 가능)
        user_text = tk.Text(
            user_container,
            font=self.message_font,
            bg=COLORS["primary_soft"],
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
        
        # Text 위젯 내부 스크롤 비활성화 (canvas 스크롤로 전달)
        self._disable_text_widget_scroll(user_text)
        
        # 텍스트 삽입 및 높이 자동 조정
        user_text.config(state='normal')
        user_text.insert('1.0', message)
        user_text.config(state='disabled')
        
        # Tk 레이아웃 완료 후 높이 조정 (after_idle로 지연)
        def adjust_height():
            if user_text.winfo_exists():
                self._adjust_text_widget_height(user_text)
                self._update_messages_scrollregion()
                self.messages_canvas.yview_moveto(1)
        self.root.after_idle(adjust_height)
        
        # 스크롤을 맨 아래로
        self._update_messages_scrollregion()
        self.messages_canvas.yview_moveto(1)
        
    def add_bot_message(self, message):
        """봇 메시지 추가"""
        message_frame = tk.Frame(self.scrollable_frame, bg='white')
        message_frame.pack(fill='x', pady=8)
        self._bind_canvas_scroll_events(message_frame)
        
        # 봇 메시지 컨테이너 (좌측 정렬)
        bot_container = tk.Frame(message_frame, bg='white')
        bot_container.pack(side='left', padx=(15, 50))
        self._bind_canvas_scroll_events(bot_container)
        
        # 봇 메시지 (Text 위젯으로 변경하여 텍스트 선택 가능)
        bot_text = tk.Text(
            bot_container,
            font=self.message_font,
            bg=COLORS["panel_bg"],
            fg='black',
            wrap='word',
            width=50,
            height=1,
            relief='flat',
            borderwidth=0,
            padx=15,
            pady=10,
            state='disabled',
            cursor='arrow'
        )
        bot_text.pack()
        
        # Text 위젯 내부 스크롤 비활성화 (canvas 스크롤로 전달)
        self._disable_text_widget_scroll(bot_text)
        
        # 인용 태그 설정
        self.setup_citation_tags(bot_text)
        
        # 스크롤을 맨 아래로
        self._update_messages_scrollregion()
        self.messages_canvas.yview_moveto(1)
        
        # 타이핑 애니메이션 시작
        # 환영 메시지인지 확인 (force_tk=False로 설정하여 높이 계산 오류 방지)
        is_welcome_message = "안녕하세요! JARVIS AI Assistant입니다" in message
        self.animate_typing(bot_text, message, force_tk_final=not is_welcome_message)
    
    def setup_citation_tags(self, text_widget):
        """인용 태그 스타일 및 이벤트를 설정합니다."""
        text_widget.tag_config("citation", foreground="#4f46e5", font=(self.default_font, 10, "bold"))
        
        # 이벤트 바인딩
        text_widget.tag_bind("citation", "<Enter>", lambda e: self.show_citation_popup(e, text_widget))
        text_widget.tag_bind("citation", "<Leave>", lambda e: self.hide_citation_popup(e))
        text_widget.tag_bind("citation", "<Button-1>", lambda e: self.toggle_citation_persist(e, text_widget))
        
        # 팝업 관련 상태 변수 초기화 (위젯별로 관리하기 위해 속성 추가)
        text_widget.citation_popup = None
        text_widget.citation_persist = False
        text_widget.citation_details = {}

    def highlight_citations(self, text_widget):
        """텍스트 내의 인용 번호 [n]을 찾아 하이라이트합니다."""
        text_widget.config(state='normal')
        
        # 기존 태그 제거
        text_widget.tag_remove("citation", "1.0", "end")
        
        # 정규식으로 [숫자] 패턴 찾기
        import re
        content = text_widget.get("1.0", "end")
        
        # [참고 문헌] 섹션 이전까지만 하이라이트 적용 (본문만)
        ref_idx = content.find("[참고 문헌]")
        search_content = content[:ref_idx] if ref_idx != -1 else content
        
        for match in re.finditer(r'\[(\d+)\]', search_content):
            start_idx = f"1.0 + {match.start()} chars"
            end_idx = f"1.0 + {match.end()} chars"
            text_widget.tag_add("citation", start_idx, end_idx)

        # 참고 문헌 메타데이터 업데이트 및 표시 정리
        self._update_citation_details(text_widget, content)
        
        text_widget.config(state='disabled')

    def get_citation_content(self, text_widget, citation_num):
        """해당 인용 번호의 내용을 추출합니다."""
        citation_details = getattr(text_widget, 'citation_details', {})
        detail = citation_details.get(str(citation_num))
        if detail:
            label = detail.get("label") or f"출처 {citation_num}"
            body = detail.get("body") or "내용을 불러올 수 없습니다."
            return {"label": label, "body": body}
        
        # Fallback: 최소한 라벨만 제공
        return {
            "label": f"출처 {citation_num}",
            "body": "내용을 불러올 수 없습니다."
        }

    def show_citation_popup(self, event, text_widget):
        """인용 팝업을 표시합니다 (화면 경계 보정 포함)."""
        # 이미 유지된 팝업이 있으면 무시
        if getattr(text_widget, 'citation_persist', False):
            return
            
        text_widget.config(cursor="hand2")
        
        try:
            # 마우스 위치의 인용 번호 확인
            index = text_widget.index(f"@{event.x},{event.y}")
            tags = text_widget.tag_names(index)
            if "citation" not in tags:
                return
                
            # 인용 번호 추출
            ranges = text_widget.tag_ranges("citation")
            citation_num = None
            for i in range(0, len(ranges), 2):
                start = ranges[i]
                end = ranges[i+1]
                if text_widget.compare(start, "<=", index) and text_widget.compare(index, "<", end):
                    citation_text = text_widget.get(start, end)
                    citation_num = citation_text.strip("[]")
                    break
            
            if not citation_num:
                return
                
            # 내용 가져오기
            citation_data = self.get_citation_content(text_widget, citation_num)
            if not citation_data:
                return



            # 기존 팝업 제거
            if getattr(text_widget, 'citation_popup', None):
                text_widget.citation_popup.destroy()



            # 팝업 윈도우 생성
            popup = tk.Toplevel(self.root)
            popup.wm_overrideredirect(True) # 테두리 제거
            popup.configure(bg='white', relief='solid', borderwidth=1)
            
            # 항상 최상단
            popup.attributes('-topmost', True)
            
            # macOS에서 팝업이 올바르게 표시되도록 설정
            self._setup_window_for_macos(popup, is_popup=True)
            
            # 내용 표시 프레임
            frame = tk.Frame(popup, bg='white', padx=12, pady=12)
            frame.pack(fill='both', expand=True)
            
            # 제목
            tk.Label(
                frame,
                text=f"[{citation_num}] {citation_data['label']}",
                font=(self.default_font, 10, 'bold'),
                bg='white',
                fg='#111827',
                wraplength=420,
                justify='left'
            ).pack(anchor='w')
            
            # 구분선
            tk.Frame(frame, height=1, bg='#e5e7eb', width=400).pack(fill='x', pady=8)
            
            # 본문 - 스크롤 가능한 텍스트 위젯
            body_frame = tk.Frame(frame, bg='white')
            body_frame.pack(fill='both', expand=True)
            
            scrollbar = ttk.Scrollbar(body_frame, orient='vertical')
            scrollbar.pack(side='right', fill='y')
            
            body_text_widget = tk.Text(
                body_frame,
                font=(self.default_font, 10),
                bg='white',
                fg='#4b5563',
                wrap='word',
                relief='flat',
                borderwidth=0,
                padx=0,
                pady=0,
                height=12,
                width=50 # 대략적인 폭 설정
            )
            body_text_widget.pack(side='left', fill='both', expand=True)
            body_text_widget.configure(yscrollcommand=scrollbar.set)
            scrollbar.configure(command=body_text_widget.yview)
            
            body_text_widget.insert('1.0', citation_data['body'])
            body_text_widget.config(state='disabled')
            self._bind_popup_text_scroll(body_text_widget)
            
            text_widget.citation_popup = popup
            
            # --- 위치 계산 및 보정 로직 ---
            # 팝업 크기를 계산하기 위해 업데이트
            popup.update_idletasks()
            
            popup_width = popup.winfo_reqwidth()
            popup_height = popup.winfo_reqheight()
            
            screen_width = popup.winfo_screenwidth()
            screen_height = popup.winfo_screenheight()
            
            # 기본 위치: 마우스 오른쪽 아래
            x = event.x_root + 15
            y = event.y_root + 15
            
            # 오른쪽 화면 밖으로 나가는지 확인
            if x + popup_width > screen_width:
                # 마우스 왼쪽으로 이동
                x = event.x_root - popup_width - 15
            
            # 아래쪽 화면 밖으로 나가는지 확인
            if y + popup_height > screen_height:
                # 마우스 위쪽으로 이동
                y = event.y_root - popup_height - 15
                
            # 왼쪽/위쪽 경계 확인 (너무 왼쪽이나 위로 가지 않게)
            x = max(0, x)
            y = max(0, y)
            
            popup.geometry(f"+{x}+{y}")
            
        except Exception:
            pass  # 팝업 표시 오류 무시

    def hide_citation_popup(self, event):
        """팝업을 숨깁니다 (유지 상태가 아닐 때만)."""
        widget = event.widget
        widget.config(cursor="arrow")
        
        if not getattr(widget, 'citation_persist', False):
            if getattr(widget, 'citation_popup', None):
                widget.citation_popup.destroy()
                widget.citation_popup = None

    def toggle_citation_persist(self, event, text_widget):
        """팝업 유지 상태를 토글합니다."""
        # 현재 상태 확인
        is_persisted = getattr(text_widget, 'citation_persist', False)
        
        if is_persisted:
            # 이미 유지 중이면 닫기
            text_widget.citation_persist = False
            if getattr(text_widget, 'citation_popup', None):
                text_widget.citation_popup.destroy()
                text_widget.citation_popup = None
        else:
            # 유지 상태로 변경
            text_widget.citation_persist = True
            # 팝업이 없으면 생성 (클릭으로 바로 띄우는 경우)
            if not getattr(text_widget, 'citation_popup', None):
                self.show_citation_popup(event, text_widget)
            
            # 다른 곳 클릭 시 닫기 위한 전역 바인딩 (한 번만 동작하도록)
            def close_on_outside_click(e):
                # 팝업 내부 클릭은 무시해야 하지만, Toplevel이라 이벤트가 분리됨
                # 여기서는 간단히 위젯 외부 클릭 시 닫기로 처리
                if e.widget != text_widget and getattr(text_widget, 'citation_persist', False):
                    text_widget.citation_persist = False
                    if getattr(text_widget, 'citation_popup', None):
                        text_widget.citation_popup.destroy()
                        text_widget.citation_popup = None
                    self.root.unbind_all("<Button-1>") # 바인딩 해제
            
            # 약간의 지연 후 바인딩 (현재 클릭 이벤트가 전파되어 바로 닫히는 것 방지)
            self.root.after(100, lambda: self.root.bind_all("<Button-1>", close_on_outside_click, add="+"))

    def on_citation_click(self, event, text_widget):
        """(Deprecated) 기존 클릭 핸들러 - toggle_citation_persist로 대체됨"""
        pass

    def scroll_to_citation_source(self, text_widget, citation_num):
        """(Deprecated) 기존 스크롤 핸들러 - 팝업으로 대체됨"""
        pass

    def animate_typing(self, text_widget, full_text, current_index=0, force_tk_final=True):
        """타이핑 애니메이션을 실행합니다 (append 방식 + chunk 단위).
        
        Args:
            text_widget: 텍스트 위젯
            full_text: 전체 텍스트
            current_index: 현재 타이핑 인덱스
            force_tk_final: 타이핑 완료 시 force_tk 옵션 사용 여부 (기본값: True)
        """
        if not text_widget or not text_widget.winfo_exists():
            return
        
        total_length = len(full_text)
        
        if current_index < total_length:
            # chunk 크기 결정 (남은 텍스트 양에 따라 동적 조절)
            remaining = total_length - current_index
            if remaining > 200:
                chunk_size = 8  # 긴 텍스트는 8자씩
            elif remaining > 50:
                chunk_size = 5  # 중간 텍스트는 5자씩
            else:
                chunk_size = 3  # 짧은 텍스트는 3자씩
            
            # 새로 추가할 텍스트 chunk
            end_index = min(current_index + chunk_size, total_length)
            new_chunk = full_text[current_index:end_index]
            
            # append 방식: 기존 텍스트를 지우지 않고 끝에 추가
            text_widget.config(state='normal')
            text_widget.insert('end', new_chunk)
            text_widget.config(state='disabled')
            
            # 높이 조정 (줄바꿈이 포함되거나 20자마다)
            if '\n' in new_chunk or end_index % 20 == 0:
                self.root.after_idle(lambda: self._adjust_text_widget_height(text_widget) if text_widget.winfo_exists() else None)
            
            # 타이핑 속도 (밀리초) - 줄바꿈 후에는 약간 더 긴 딜레이
            if '\n' in new_chunk:
                typing_speed = 50  # 줄바꿈 후 약간 멈춤
            else:
                typing_speed = 20  # 일반 타이핑
            
            self.root.after(typing_speed, lambda: self.animate_typing(text_widget, full_text, end_index, force_tk_final))
            
            # 스크롤을 맨 아래로 유지
            if self.messages_canvas.yview()[1] > 0.9:
                self._update_messages_scrollregion()
                self.messages_canvas.yview_moveto(1)
        else:
            # 타이핑 완료 시 인용 하이라이트 및 최종 높이 조정
            self.highlight_citations(text_widget)

            def _final_adjust():
                if text_widget.winfo_exists():
                    self._adjust_text_widget_height(text_widget, force_tk=force_tk_final)

            # force_tk_final 파라미터에 따라 높이 계산 방식 결정
            self.root.after_idle(_final_adjust)
            # 렌더링이 완전히 끝난 뒤 한 번 더 보정 (일부 시스템에서 지연 필요)
            self.root.after(150, _final_adjust)

            self._update_messages_scrollregion()
            self.messages_canvas.yview_moveto(1)
            
            # 타이핑 완료 후 대기 중인 deep_dive_offer가 있으면 표시
            if hasattr(self, 'pending_deep_dive_info') and self.pending_deep_dive_info:
                deep_dive_info = self.pending_deep_dive_info
                self.pending_deep_dive_info = None  # 초기화
                # 약간의 지연 후 버튼 표시 (메시지 렌더링 완료 후)
                self.root.after(200, lambda: self.show_deep_dive_offer(
                    deep_dive_info['keyword'],
                    deep_dive_info['recommendation_id']
                ))
    
    def show_loading_message(self):
        """로딩 메시지를 표시합니다."""
        message_frame = tk.Frame(self.scrollable_frame, bg='white')
        message_frame.pack(fill='x', pady=8)
        self._bind_canvas_scroll_events(message_frame)
        
        # 로딩 메시지 컨테이너 (좌측 정렬)
        loading_container = tk.Frame(message_frame, bg='white')
        loading_container.pack(side='left', padx=(15, 50))
        self._bind_canvas_scroll_events(loading_container)
        
        # 로딩 메시지 (Text 위젯으로 변경)
        loading_text = tk.Text(
            loading_container,
            font=self.message_font,
            bg=COLORS["panel_bg"],
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
        
        # Text 위젯 내부 스크롤 비활성화 (canvas 스크롤로 전달)
        self._disable_text_widget_scroll(loading_text)
        
        # 초기 텍스트 삽입
        loading_text.config(state='normal')
        loading_text.insert('1.0', "답변을 생성하고 있습니다...")
        loading_text.config(state='disabled')
        
        # 로딩 애니메이션 시작
        self.animate_loading(loading_text)
        
        # 스크롤을 맨 아래로
        self._update_messages_scrollregion()
        self.messages_canvas.yview_moveto(1)
        
        return loading_text
    
    def animate_loading(self, text_widget, dots=0):
        """로딩 애니메이션을 실행합니다."""
        # 위젯이 파괴되었는지 확인
        if not text_widget or not text_widget.winfo_exists():
            return  # 위젯이 파괴되었으면 애니메이션 중지
        
        try:
            dots_text = "." * (dots + 1)
            loading_text = f"답변을 생성하고 있습니다{dots_text}"
            
            # Text 위젯에 텍스트 삽입
            text_widget.config(state='normal')
            text_widget.delete('1.0', 'end')
            text_widget.insert('1.0', loading_text)
            text_widget.config(state='disabled')
            
            # 다음 애니메이션 프레임 (위젯이 여전히 존재하는지 다시 확인)
            if text_widget.winfo_exists():
                self.root.after(500, lambda: self.animate_loading(text_widget, (dots + 1) % 4))
        except tk.TclError:
            # 위젯이 파괴되었거나 접근할 수 없는 경우 예외 처리
            return
    
    def remove_loading_message(self, loading_text_widget):
        """로딩 메시지를 제거합니다."""
        if loading_text_widget and loading_text_widget.winfo_exists():
            loading_text_widget.master.master.destroy()  # container의 부모인 message_frame 제거
            self._update_messages_scrollregion()
    
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
        """봇 응답 가져오기 - 스트리밍 응답 지원"""
        max_retries = 3
        retry_delay = 2
        timeout = 120
        
        for attempt in range(max_retries):
            try:
                response = requests.post(
                    f"{self.API_BASE_URL}/api/v2/process",
                    json={"message": message, "user_id": 1},
                    headers={"Accept": "text/event-stream"},
                    timeout=timeout,
                    stream=True
                )
                
                if response.status_code == 200:
                    # 빈 봇 메시지 생성
                    self.message_queue.put({
                        'type': 'create_streaming_message',
                        'loading_widget': loading_text_widget
                    })
                    
                    # 스트리밍 응답 읽기 (decode_unicode=True로 설정하여 안전하게 읽기)
                    try:
                        # chunk_size=None으로 설정하여 스트림이 도착하는 대로 받음
                        for chunk_text in response.iter_content(chunk_size=None, decode_unicode=True):
                            if chunk_text:
                                self.message_queue.put({
                                    'type': 'stream_chunk',
                                    'chunk': chunk_text
                                })
                        
                        self.message_queue.put({'type': 'complete_streaming'})
                        
                    except Exception as e:
                        error_msg = f"스트리밍 처리 중 오류가 발생했습니다: {str(e)}"
                        self.message_queue.put({
                            'type': 'bot_response',
                            'response': error_msg,
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
                    import time
                    time.sleep(retry_delay)
                    continue
                else:
                    error_msg = f"서버 응답 시간이 초과되었습니다."
                    self.message_queue.put({
                        'type': 'bot_response',
                        'response': error_msg,
                        'loading_widget': loading_text_widget
                    })
                    return
            except Exception as e:
                if attempt < max_retries - 1:
                    import time
                    time.sleep(retry_delay)
                    continue
                else:
                    error_msg = f"오류가 발생했습니다: {str(e)}"
                    self.message_queue.put({
                        'type': 'bot_response',
                        'response': error_msg,
                        'loading_widget': loading_text_widget
                    })
                    return
    
    def handle_bot_response(self, bot_response, loading_text_widget, deep_dive_info=None):
        """봇 응답을 처리합니다."""
        # 로딩 메시지 제거
        self.remove_loading_message(loading_text_widget)
        
        # 타이핑 애니메이션으로 봇 메시지 표시
        # deep_dive_info가 있으면 메시지 출력 완료 후 버튼 표시를 위해 저장
        self.pending_deep_dive_info = deep_dive_info
        self.add_bot_message(bot_response)
    
    def create_streaming_bot_message(self, loading_text_widget):
        """스트리밍용 빈 봇 메시지를 생성합니다."""
        self.remove_loading_message(loading_text_widget)
        
        # 봇 메시지 프레임 생성
        message_frame = tk.Frame(self.scrollable_frame, bg='white')
        message_frame.pack(fill='x', pady=8)
        self._bind_canvas_scroll_events(message_frame)
        
        # 봇 메시지 컨테이너
        bot_container = tk.Frame(message_frame, bg='white')
        bot_container.pack(side='left', padx=(15, 50))
        self._bind_canvas_scroll_events(bot_container)
        
        # 봇 메시지 위젯
        bot_text = tk.Text(
            bot_container,
            font=self.message_font,
            bg=COLORS["panel_bg"],
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
        
        # Text 위젯 내부 스크롤 비활성화 (canvas 스크롤로 전달)
        self._disable_text_widget_scroll(bot_text)
        
        self.setup_citation_tags(bot_text)
        
        # 스트리밍 관련 변수 초기화
        self.streaming_text_widget = bot_text
        self.streaming_bot_container = bot_container  # 버튼 추가를 위해 저장
        self.streaming_text_buffer = ""
        self.streaming_displayed_length = 0
        self.streaming_typing_active = False
        self.stream_finished_flag = False  # 네트워크 수신 완료 여부 플래그
        self._reference_marker_logged = False
        self.pending_metadata = None  # 스트리밍 중 수신한 메타데이터
        
        # 초기 높이 조정 (after_idle로 지연)
        self.root.after_idle(lambda: self._adjust_text_widget_height(bot_text) if bot_text.winfo_exists() else None)
        
        self._update_messages_scrollregion()
        self.messages_canvas.yview_moveto(1)
    
    def handle_stream_chunk(self, chunk):
        """스트리밍 청크를 처리하고 누적합니다."""
        if not hasattr(self, 'streaming_text_widget') or not self.streaming_text_widget.winfo_exists():
            return
        
        # 청크를 버퍼에 추가
        if not hasattr(self, 'streaming_text_buffer'):
            self.streaming_text_buffer = ""
        
        self.streaming_text_buffer += chunk
        
        # 메타데이터 구분자 감지 및 처리
        metadata_separator = "\n\n---METADATA---\n"
        if metadata_separator in self.streaming_text_buffer:
            parts = self.streaming_text_buffer.split(metadata_separator, 1)
            self.streaming_text_buffer = parts[0]  # 텍스트 부분만 유지
            
            # 메타데이터 파싱
            if len(parts) > 1:
                try:
                    metadata_json = parts[1].strip()
                    self.pending_metadata = json.loads(metadata_json)
                    print(f"[UI] 메타데이터 수신: {self.pending_metadata}")
                except json.JSONDecodeError as e:
                    print(f"[UI] 메타데이터 파싱 오류: {e}")
                    self.pending_metadata = None
        
        # 타이핑 애니메이션이 진행 중이 아니면 시작
        if not self.streaming_typing_active:
            self.animate_streaming_typing()
    
    def animate_streaming_typing(self):
        """스트리밍 메시지를 타이핑 애니메이션으로 표시합니다 (참고문헌 숨김 처리)."""
        if not hasattr(self, 'streaming_text_widget') or not self.streaming_text_widget.winfo_exists():
            self.streaming_typing_active = False
            return
        
        if not hasattr(self, 'streaming_text_buffer'):
            self.streaming_text_buffer = ""
        
        if not hasattr(self, 'streaming_displayed_length'):
            self.streaming_displayed_length = 0
        
        # 버퍼에서 [참고 문헌] 위치를 찾습니다.
        # 애니메이션은 이 위치까지만 진행하고 멈춥니다.
        ref_marker = "[참고 문헌]"
        limit_index = self.streaming_text_buffer.find(ref_marker)
        
        # 전체 길이 계산 (limit_index가 있으면 거기까지만)
        total_length = len(self.streaming_text_buffer)
        if limit_index != -1:
            total_length = limit_index
            self._reference_marker_logged = True
        
        # 표시할 새 텍스트가 있다면
        if self.streaming_displayed_length < total_length:
            self.streaming_typing_active = True
            
            # 속도 조절
            remaining = total_length - self.streaming_displayed_length
            if remaining > 200:
                chars_to_add = 5 
            elif remaining > 50:
                chars_to_add = 3
            elif remaining > 10:
                chars_to_add = 2
            else:
                chars_to_add = 1
            
            # 텍스트 추가
            start_idx = self.streaming_displayed_length
            end_idx = min(start_idx + chars_to_add, total_length)
            new_text_chunk = self.streaming_text_buffer[start_idx:end_idx]
            
            self.streaming_displayed_length = end_idx
            
            self.streaming_text_widget.config(state='normal')
            self.streaming_text_widget.insert('end', new_text_chunk)
            self.streaming_text_widget.config(state='disabled')
            
            # 높이 조정 (매 프레임마다 after_idle로 지연 실행)
            self.root.after_idle(lambda: self._adjust_text_widget_height(self.streaming_text_widget) if self.streaming_text_widget.winfo_exists() else None)
            
            # 자동 스크롤
            if self.messages_canvas.yview()[1] > 0.9:
                self._update_messages_scrollregion()
                self.messages_canvas.yview_moveto(1)
            
            self.root.after(15, self.animate_streaming_typing)
            
        else:
            # 버퍼를 (제한선까지) 다 비웠음
            stream_finished = getattr(self, 'stream_finished_flag', False)
            
            if not stream_finished:
                # 아직 네트워크 수신 중이면 대기
                self.root.after(50, self.animate_streaming_typing)
            else:
                # 종료 처리
                self.streaming_typing_active = False
                self.finalize_streaming_display()
    
    def update_streaming_message(self, text):
        """스트리밍 메시지를 업데이트합니다. (사용 안 함 - animate_streaming_typing 사용)"""
        # 이 메서드는 더 이상 사용하지 않지만 호환성을 위해 유지
        pass
    
    def complete_streaming_message(self):
        """스트리밍 수신 완료 신호 처리"""
        self.stream_finished_flag = True
    
    def finalize_streaming_display(self):
        """스트리밍 종료 후 최종 화면 처리를 담당합니다."""
        if hasattr(self, 'streaming_text_widget') and self.streaming_text_widget.winfo_exists():
            # 최종 텍스트 (전체 버퍼)
            final_text = self.streaming_text_buffer if hasattr(self, 'streaming_text_buffer') else ""
            
            # 1. 화면에 전체 텍스트를 일단 넣음 (highlight_citations가 처리할 수 있도록)
            self.streaming_text_widget.config(state='normal')
            self.streaming_text_widget.delete('1.0', 'end')
            self.streaming_text_widget.insert('1.0', final_text)
            self._remove_trailing_newline(self.streaming_text_widget)
            self.streaming_text_widget.config(state='disabled')
            
            # 2. 하이라이트 및 [참고 문헌] 정리 실행
            self.highlight_citations(self.streaming_text_widget)
            
            # 3. 최종 높이 및 스크롤 조정
            def finalize_height():
                if self.streaming_text_widget.winfo_exists():
                    current_height = self.streaming_text_widget.cget('height')
                    new_height = self._calculate_display_lines(self.streaming_text_widget, force_tk=True)
                    final_height = max(current_height, new_height)
                    self.streaming_text_widget.config(height=final_height)
                    self._update_messages_scrollregion()
                    self.messages_canvas.yview_moveto(1)
            
            self.root.after_idle(finalize_height)
            self.root.after(150, finalize_height)
            
            # 4. 메타데이터에 따른 버튼 추가 (action="open_file")
            if hasattr(self, 'pending_metadata') and self.pending_metadata:
                if self.pending_metadata.get("action") == "open_file":
                    file_path = self.pending_metadata.get("file_path", "")
                    file_name = self.pending_metadata.get("file_name", "파일")
                    if file_path and hasattr(self, 'streaming_bot_container'):
                        self.add_open_file_button(
                            self.streaming_bot_container,
                            file_path,
                            file_name
                        )
            
        # 변수 정리
        if hasattr(self, 'streaming_text_buffer'):
            delattr(self, 'streaming_text_buffer')
        if hasattr(self, 'pending_metadata'):
            self.pending_metadata = None
    
    # ============================================================
    # 파일 열기 버튼 (CodingAgent 등에서 사용)
    # ============================================================
    
    def add_open_file_button(self, container, file_path, file_name):
        """
        봇 메시지 하단에 파일/폴더 열기 버튼을 추가합니다.
        
        Args:
            container: 버튼을 추가할 부모 위젯 (bot_container)
            file_path: 열 파일의 전체 경로
            file_name: 표시할 파일명
        """
        # 버튼 프레임 생성
        button_frame = tk.Frame(container, bg=COLORS["panel_bg"])
        button_frame.pack(fill='x', pady=(8, 4), padx=10)
        self._bind_canvas_scroll_events(button_frame)
        
        # 파일 열기 버튼
        open_file_btn = tk.Button(
            button_frame,
            text=f"📂 {file_name} 열기",
            font=('맑은 고딕', 9),
            bg='#3b82f6',
            fg='white',
            relief='flat',
            cursor='hand2',
            padx=12,
            pady=6,
            command=lambda: self._open_code_file(file_path)
        )
        open_file_btn.pack(side='left', padx=(0, 8))
        
        # 폴더 열기 버튼
        open_folder_btn = tk.Button(
            button_frame,
            text="📁 폴더 열기",
            font=('맑은 고딕', 9),
            bg='#6b7280',
            fg='white',
            relief='flat',
            cursor='hand2',
            padx=12,
            pady=6,
            command=lambda: self._open_code_folder(file_path)
        )
        open_folder_btn.pack(side='left')
        
        # 스크롤 영역 업데이트
        self.root.after_idle(self._update_messages_scrollregion)
        self.root.after(100, lambda: self.messages_canvas.yview_moveto(1))
        
        print(f"[UI] 파일 열기 버튼 추가: {file_name}")
    
    def _open_code_file(self, file_path):
        """코드 파일을 OS 기본 편집기로 엽니다."""
        try:
            if not file_path or not os.path.exists(file_path):
                print(f"[UI] 파일을 찾을 수 없습니다: {file_path}")
                return
            
            system = platform.system()
            if system == "Windows":
                os.startfile(file_path)
            elif system == "Darwin":
                subprocess.call(['open', file_path])
            else:
                subprocess.call(['xdg-open', file_path])
            
            print(f"[UI] 코드 파일 열기: {file_path}")
        except Exception as e:
            print(f"[UI] 파일 열기 오류: {e}")
    
    def _open_code_folder(self, file_path):
        """코드 파일이 있는 폴더를 탐색기로 엽니다."""
        try:
            if not file_path:
                print("[UI] 파일 경로가 없습니다.")
                return
            
            # 폴더 경로 추출
            folder_path = os.path.dirname(file_path)
            if not os.path.exists(folder_path):
                print(f"[UI] 폴더를 찾을 수 없습니다: {folder_path}")
                return
            
            system = platform.system()
            if system == "Windows":
                # Windows: explorer로 폴더 열기 (파일 선택)
                if os.path.isfile(file_path):
                    subprocess.run(['explorer', '/select,', file_path])
                else:
                    os.startfile(folder_path)
            elif system == "Darwin":
                # macOS: Finder로 열기
                if os.path.isfile(file_path):
                    subprocess.call(['open', '-R', file_path])
                else:
                    subprocess.call(['open', folder_path])
            else:
                # Linux: xdg-open으로 열기
                subprocess.call(['xdg-open', folder_path])
            
            print(f"[UI] 코드 폴더 열기: {folder_path}")
        except Exception as e:
            print(f"[UI] 폴더 열기 오류: {e}")
        
    # ============================================================
    # WebSocket 연결 (실시간 추천 알림)
    # ============================================================
    
    def connect_websocket(self):
        """WebSocket 연결을 시작합니다."""
        threading.Thread(target=self._websocket_thread, daemon=True).start()
    
    def _websocket_thread(self):
        """[백그라운드 스레드] WebSocket 연결을 관리합니다."""
        while True:
            try:
                from login_view import get_stored_token
                token = get_stored_token()
                
                if not token:
                    print("[WebSocket] 토큰이 없습니다. 5초 후 재시도...")
                    import time
                    time.sleep(5)
                    continue
                
                # WebSocket URL 구성 (http -> ws, https -> wss)
                ws_url = self.API_BASE_URL.replace("http://", "ws://").replace("https://", "wss://")
                ws_url = f"{ws_url}/ws/{token}"
                
                print(f"[WebSocket] 연결 시도: {ws_url[:50]}...")
                
                # WebSocket 연결
                self.ws = websocket.WebSocketApp(
                    ws_url,
                    on_open=self._on_ws_open,
                    on_message=self._on_ws_message,
                    on_error=self._on_ws_error,
                    on_close=self._on_ws_close
                )
                
                # 연결 유지 (블로킹) - 보고서 생성 등 오래 걸리는 작업을 위해 ping 간격 증가
                # ping_interval > ping_timeout 이어야 함
                self.ws.run_forever(ping_interval=120, ping_timeout=60)
                
            except Exception as e:
                print(f"[WebSocket] 연결 오류: {e}")
            
            # 연결 끊어지면 재연결 시도
            self.ws_connected = False
            print(f"[WebSocket] {self.ws_reconnect_delay}초 후 재연결 시도...")
            import time
            time.sleep(self.ws_reconnect_delay)
    
    def _on_ws_open(self, ws):
        """WebSocket 연결 성공 시 호출"""
        self.ws_connected = True
        print("[WebSocket] ✅ 연결 성공!")
    
    def _on_ws_message(self, ws, message):
        """WebSocket 메시지 수신 시 호출"""
        try:
            data = json.loads(message)
            msg_type = data.get('type')
            
            print(f"[WebSocket] 메시지 수신: type={msg_type}")
            
            if msg_type == 'new_recommendation':
                # 새로운 추천 알림 처리
                recommendation = data.get('data')
                if recommendation:
                    # UI 스레드에서 말풍선 표시
                    self.message_queue.put({
                        'type': 'show_recommendation',
                        'recommendations': [recommendation]
                    })
            
            elif msg_type == 'report_completed':
                # 보고서 생성 완료 알림
                print(f"[WebSocket] 📄 보고서 완료: {data.get('keyword')}")
                self.message_queue.put({
                    'type': 'show_report_notification',
                    'data': {
                        'success': True,
                        'keyword': data.get('keyword', ''),
                        'file_path': data.get('file_path', ''),
                        'file_name': data.get('file_name', ''),
                        'sources': data.get('sources', [])
                    }
                })
            
            elif msg_type == 'report_failed':
                # 보고서 생성 실패 알림
                print(f"[WebSocket] 📄 보고서 실패: {data.get('keyword')} - {data.get('reason')}")
                self.message_queue.put({
                    'type': 'show_report_notification',
                    'data': {
                        'success': False,
                        'keyword': data.get('keyword', ''),
                        'reason': data.get('reason', '알 수 없는 오류')
                    }
                })
                    
        except json.JSONDecodeError as e:
            print(f"[WebSocket] JSON 파싱 오류: {e}")
        except Exception as e:
            print(f"[WebSocket] 메시지 처리 오류: {e}")
    
    def _on_ws_error(self, ws, error):
        """WebSocket 오류 시 호출"""
        print(f"[WebSocket] ❌ 오류: {error}")
    
    def _on_ws_close(self, ws, close_status_code, close_msg):
        """WebSocket 연결 종료 시 호출"""
        self.ws_connected = False
        print(f"[WebSocket] 연결 종료 (code={close_status_code}, msg={close_msg})")
    
    # ============================================================
    # Recommendation Bubble UI (Active Agent Integration)
    # ============================================================
    
    def show_recommendation_notification(self, recommendations):
        """새로운 추천이 있으면 말풍선을 표시합니다."""
        if not recommendations:
            return
        
        # 데이터 수집 중이면 추천을 대기열에 추가하고 나중에 표시
        if self.is_collecting_data:
            print("[Recommendation] 데이터 수집 중이므로 추천을 대기열에 추가합니다.")
            self.pending_recommendations_queue.extend(recommendations)
            return
        
        # 이미 말풍선이 떠있으면 닫고 새로 띄움
        if self.recommendation_bubble_visible:
            self.close_recommendation_bubble()
        
        # 첫 번째 pending 추천 사용
        self.current_recommendation = recommendations[0]
        self.create_recommendation_bubble(self.current_recommendation)
    
    def create_recommendation_bubble(self, recommendation):
        """플로팅 버튼 위에 말풍선 UI를 생성합니다."""
        if self.recommendation_bubble_visible:
            return
        
        # 말풍선 Toplevel 윈도우 생성
        self.recommendation_bubble = tk.Toplevel(self.root)
        self.recommendation_bubble.wm_overrideredirect(True)
        self.recommendation_bubble.attributes('-topmost', True)
        self.recommendation_bubble.configure(bg='white')
        
        # macOS에서 팝업이 올바르게 표시되도록 설정
        self._setup_window_for_macos(self.recommendation_bubble, is_popup=True)
        
        # 메시지 내용
        bubble_message = recommendation.get('bubble_message', '새로운 추천이 있어요!')
        keyword = recommendation.get('keyword', '')
        rec_id = recommendation.get('id')
        
        # 메인 프레임 (둥근 모서리 효과를 위한 패딩)
        main_frame = tk.Frame(self.recommendation_bubble, bg='white', padx=2, pady=2)
        main_frame.pack(fill='both', expand=True)
        
        # 내부 컨테이너
        inner_frame = tk.Frame(main_frame, bg=COLORS["panel_bg"], padx=15, pady=12)
        inner_frame.pack(fill='both', expand=True)
        
        # 상단: 아이콘과 닫기 버튼
        header_frame = tk.Frame(inner_frame, bg=COLORS["panel_bg"])
        header_frame.pack(fill='x', pady=(0, 8))
        
        # 💡 아이콘
        icon_label = tk.Label(
            header_frame,
            text="💡",
            font=('Arial', 16),
            bg=COLORS["panel_bg"]
        )
        icon_label.pack(side='left')
        
        # 키워드 라벨
        if keyword:
            keyword_label = tk.Label(
                header_frame,
                text=keyword,
                font=(self.default_font, 10, 'bold'),
                bg=COLORS["panel_bg"],
                fg='#4f46e5'
            )
            keyword_label.pack(side='left', padx=(8, 0))
        
        # 닫기 버튼
        close_btn = tk.Button(
            header_frame,
            text="✕",
            font=(self.default_font, 10),
            bg=COLORS["panel_bg"],
            fg='#9ca3af',
            relief='flat',
            cursor='hand2',
            command=lambda: self.close_recommendation_bubble(auto_reject=False),
            activebackground=COLORS["surface_alt"]
        )
        close_btn.pack(side='right')
        
        # 메시지 라벨 (Word wrap 적용)
        message_label = tk.Label(
            inner_frame,
            text=bubble_message,
            font=(self.default_font, 11),
            bg=COLORS["panel_bg"],
            fg='#1f2937',
            wraplength=250,
            justify='left'
        )
        message_label.pack(fill='x', pady=(0, 12))
        
        # 버튼 프레임
        button_frame = tk.Frame(inner_frame, bg='#f8fafc')
        button_frame.pack(fill='x')
        
        # [네, 궁금해요] 버튼
        accept_btn = tk.Button(
            button_frame,
            text="네, 궁금해요 👀",
            font=(self.default_font, 10, 'bold'),
            bg=COLORS["primary"],
            fg=COLORS["text_primary"],
            relief='flat',
            cursor='hand2',
            padx=12,
            pady=6,
            command=lambda: self.handle_recommendation_accept(rec_id),
            activebackground='#4338ca',
            activeforeground='white'
        )
        accept_btn.pack(side='left', padx=(0, 8))
        
        # [관심 없음] 버튼
        reject_btn = tk.Button(
            button_frame,
            text="관심 없음",
            font=(self.default_font, 10),
            bg=COLORS["border"],
            fg='#4b5563',
            relief='flat',
            cursor='hand2',
            padx=12,
            pady=6,
            command=lambda: self.handle_recommendation_reject(rec_id),
            activebackground='#d1d5db'
        )
        reject_btn.pack(side='left')
        
        # 말풍선 꼬리 (삼각형) - Canvas로 구현
        tail_canvas = tk.Canvas(
            self.recommendation_bubble,
            width=20,
            height=10,
            bg='white',
            highlightthickness=0
        )
        tail_canvas.pack(side='bottom')
        tail_canvas.create_polygon(
            0, 0,
            10, 10,
            20, 0,
            fill=COLORS["panel_bg"],
            outline=COLORS["panel_bg"]
        )
        
        # 그림자 효과 (테두리로 대체)
        self.recommendation_bubble.configure(
            highlightbackground=COLORS["border"],
            highlightthickness=1
        )
        
        # 위치 계산: 플로팅 버튼 바로 위
        self.recommendation_bubble.update_idletasks()
        bubble_width = self.recommendation_bubble.winfo_reqwidth()
        bubble_height = self.recommendation_bubble.winfo_reqheight()
        
        button_x = self.root.winfo_x()
        button_y = self.root.winfo_y()
        
        # 버튼 중앙 위에 배치
        x = button_x + 35 - (bubble_width // 2)
        y = button_y - bubble_height - 10
        
        # 화면 경계 확인
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        
        if x < 10:
            x = 10
        if x + bubble_width > screen_width - 10:
            x = screen_width - bubble_width - 10
        if y < 10:
            # 버튼 아래쪽에 표시
            y = button_y + 80
        
        self.recommendation_bubble.geometry(f"+{x}+{y}")
        
        self.recommendation_bubble_visible = True
        
        # 15초 후 자동 닫기
        self.bubble_auto_close_id = self.root.after(15000, self.close_recommendation_bubble)
    
    def close_recommendation_bubble(self, auto_reject=True):
        """말풍선을 닫습니다.
        
        Args:
            auto_reject: True면 무응답으로 인해 자동 닫힘임을 의미 (블랙리스트 처리 X)
        """
        # 자동 닫기 타이머 취소
        if self.bubble_auto_close_id:
            self.root.after_cancel(self.bubble_auto_close_id)
            self.bubble_auto_close_id = None
        
        # 무응답 자동 닫힘 안내 (블랙리스트 처리 X)
        if auto_reject and self.current_recommendation:
            rec_id = self.current_recommendation.get('id')
            if rec_id:
                keyword = self.current_recommendation.get('keyword', '')
                print(f"[UI] 추천 {rec_id} 무응답으로 말풍선만 닫힘 (추천 유지) — keyword='{keyword}'")
        
        # 말풍선 파괴
        if self.recommendation_bubble and self.recommendation_bubble.winfo_exists():
            self.recommendation_bubble.destroy()
        
        self.recommendation_bubble = None
        self.recommendation_bubble_visible = False
        self.current_recommendation = None
    
    def handle_recommendation_accept(self, recommendation_id):
        """[네, 궁금해요] 클릭 처리"""
        print(f"[UI] 추천 {recommendation_id} 수락")
        
        # 말풍선 닫기 (이미 수락 처리하므로 auto_reject=False)
        self.close_recommendation_bubble(auto_reject=False)
        
        # 채팅창 열기
        if self.chat_window.state() == 'withdrawn':
            self.toggle_chat_window()
        
        # 로딩 메시지 표시
        loading_widget = self.show_loading_message()
        self.update_loading_message(loading_widget, "리포트를 생성하고 있습니다...")
        
        # 백그라운드에서 API 호출
        threading.Thread(
            target=self._call_recommendation_respond_api,
            args=(recommendation_id, 'accept', loading_widget),
            daemon=True
        ).start()
    
    def handle_recommendation_reject(self, recommendation_id):
        """[관심 없음] 클릭 처리"""
        print(f"[UI] 추천 {recommendation_id} 거절")
        
        # 말풍선 닫기 (이미 거절 처리하므로 auto_reject=False)
        self.close_recommendation_bubble(auto_reject=False)
        
        # 백그라운드에서 API 호출
        threading.Thread(
            target=self._call_recommendation_respond_api,
            args=(recommendation_id, 'reject', None),
            daemon=True
        ).start()
    
    def _call_recommendation_respond_api(self, recommendation_id, action, loading_widget):
        """[백그라운드 스레드] 추천 응답 API를 호출합니다."""
        try:
            from login_view import get_stored_token
            token = get_stored_token()
            if not token:
                if loading_widget:
                    self.message_queue.put({
                        'type': 'bot_response',
                        'response': "오류: 로그인이 필요합니다.",
                        'loading_widget': loading_widget
                    })
                return

            response = requests.post(
                f"{self.API_BASE_URL}/api/v2/recommendations/{recommendation_id}/respond",
                headers={"Authorization": f"Bearer {token}"},
                json={"action": action},
                timeout=30
            )
            
            if response.status_code == 200:
                result = response.json()
                
                if action == 'accept' and result.get('success'):
                    # 리포트 내용을 채팅창에 표시
                    report_content = result.get('report_content', '리포트를 불러올 수 없습니다.')
                    
                    # 심층 보고서 제안 정보도 함께 전달 (메시지 출력 완료 후 버튼 표시)
                    deep_dive_info = None
                    if result.get('offer_deep_dive'):
                        keyword = result.get('keyword', '')
                        rec_id = result.get('recommendation_id')
                        if keyword:
                            deep_dive_info = {
                                'keyword': keyword,
                                'recommendation_id': rec_id
                            }
                    
                    self.message_queue.put({
                        'type': 'bot_response',
                        'response': report_content,
                        'loading_widget': loading_widget,
                        'deep_dive_info': deep_dive_info
                    })
                            
                elif action == 'reject':
                    print(f"[UI] 추천 거절 완료: {result.get('message')}")
                else:
                    if loading_widget:
                        self.message_queue.put({
                            'type': 'bot_response',
                            'response': result.get('message', '처리 중 오류가 발생했습니다.'),
                            'loading_widget': loading_widget
                        })
            else:
                error_msg = f"오류: 서버 응답 {response.status_code}"
                try:
                    error_detail = response.json().get('detail', '')
                    if error_detail:
                        error_msg = f"오류: {error_detail}"
                except:
                    pass
                
                if loading_widget:
                    self.message_queue.put({
                        'type': 'bot_response',
                        'response': error_msg,
                        'loading_widget': loading_widget
                    })
                    
        except requests.exceptions.RequestException as e:
            print(f"추천 응답 API 호출 오류: {e}")
            if loading_widget:
                self.message_queue.put({
                    'type': 'bot_response',
                    'response': f"서버 연결 오류: {str(e)}",
                    'loading_widget': loading_widget
                })
    
    # ============================================================
    # Deep Dive Report (심층 보고서) 기능
    # ============================================================
    
    def show_deep_dive_offer(self, keyword, recommendation_id):
        """심층 보고서 제안 UI를 채팅창에 표시합니다."""
        # 채팅창이 열려있는지 확인
        if self.chat_window.state() == 'withdrawn':
            return
        
        # 제안 메시지 프레임 생성
        offer_frame = tk.Frame(
            self.scrollable_frame,
            bg=COLORS["info_bg"],
            padx=12,
            pady=10,
            highlightbackground=COLORS["border"],
            highlightthickness=1,
            bd=0
        )
        offer_frame.pack(fill='x', padx=10, pady=(5, 10))
        
        # 제안 메시지
        offer_label = tk.Label(
            offer_frame,
            text=f"📄 '{keyword}'에 대한 심층 보고서를 PDF로 작성해 드릴까요?",
            font=(self.default_font, 10),
            bg=COLORS["info_bg"],
            fg=COLORS["info_text"],
            wraplength=350,
            justify='left'
        )
        offer_label.pack(anchor='w', pady=(0, 8))
        
        # 버튼 컨테이너 (별도 Frame)
        button_container = tk.Frame(offer_frame, bg=COLORS["info_bg"])
        button_container.pack(anchor='w')
        
        # "응" 버튼
        yes_btn = tk.Button(
            button_container,
            text="응, 작성해줘 📝",
            font=(self.default_font, 9, 'bold'),
            padx=10,
            pady=4,
            command=lambda: self._handle_deep_dive_yes(keyword, recommendation_id, offer_frame),
        )
        self._style_button(yes_btn, variant="secondary")
        yes_btn.pack(side='left', padx=(0, 8))
        
        # "아니" 버튼
        no_btn = tk.Button(
            button_container,
            text="아니, 괜찮아",
            font=(self.default_font, 9),
            padx=10,
            pady=4,
            command=lambda: self._handle_deep_dive_no(offer_frame)
        )
        self._style_button(no_btn, variant="ghost")
        no_btn.pack(side='left')
        
        # 스크롤을 맨 아래로
        self._update_messages_scrollregion()
        self.messages_canvas.yview_moveto(1.0)
    
    def _handle_deep_dive_yes(self, keyword, recommendation_id, offer_frame):
        """'응' 버튼 클릭 - 심층 보고서 생성 요청"""
        print(f"[UI] 심층 보고서 생성 요청: keyword='{keyword}'")
        
        # 버튼 영역 제거
        if offer_frame and offer_frame.winfo_exists():
            offer_frame.destroy()
        
        # 확인 메시지 표시
        confirm_frame = tk.Frame(
            self.scrollable_frame,
            bg=COLORS["success_bg"],
            padx=12,
            pady=8,
            highlightbackground=COLORS["border"],
            highlightthickness=1,
            bd=0
        )
        confirm_frame.pack(fill='x', padx=10, pady=(0, 10))
        
        confirm_label = tk.Label(
            confirm_frame,
            text=f"✅ '{keyword}' 보고서 생성을 시작했어요. 완료되면 알려드릴게요!",
            font=(self.default_font, 10),
            bg=COLORS["success_bg"],
            fg=COLORS["success_text"],
            wraplength=350,
            justify='left'
        )
        confirm_label.pack(anchor='w')
        
        self._update_messages_scrollregion()
        self.messages_canvas.yview_moveto(1.0)
        
        # 백그라운드에서 API 호출
        threading.Thread(
            target=self._call_report_create_api,
            args=(keyword, recommendation_id),
            daemon=True
        ).start()
        
        # 1초 후 채팅창 닫고 플로팅 아이콘 상태로 전환
        self.root.after(1000, self._close_chat_after_report_request)
    
    def _handle_deep_dive_no(self, offer_frame):
        """'아니' 버튼 클릭 - 제안 UI 제거"""
        print("[UI] 심층 보고서 제안 거절")
        
        # 버튼 영역만 제거
        if offer_frame and offer_frame.winfo_exists():
            offer_frame.destroy()
    
    def _close_chat_after_report_request(self):
        """심층 보고서 요청 후 채팅창을 닫고 플로팅 아이콘 상태로 전환"""
        try:
            if self.chat_window and self.chat_window.winfo_exists():
                self.chat_window.withdraw()
                self.is_chat_open = False
                
                # 플로팅 버튼 다시 표시
                self.root.deiconify()
                self.root.lift()
                self.root.focus_force()
                
                # 버튼이 확실히 보이도록 재확인
                self.root.after(100, self.ensure_button_visible)
                
                print("[UI] 보고서 생성 요청 후 채팅창 자동 닫힘 - 플로팅 아이콘 유지")
        except Exception as e:
            print(f"[UI] 채팅창 닫기 오류: {e}")
    
    def _call_report_create_api(self, keyword, recommendation_id=None):
        """[백그라운드 스레드] 보고서 생성 API를 호출합니다."""
        try:
            from login_view import get_stored_token
            token = get_stored_token()
            
            if not token:
                self.message_queue.put({
                    'type': 'bot_response',
                    'response': "오류: 로그인이 필요합니다.",
                    'loading_widget': None
                })
                return
            
            payload = {"keyword": keyword}
            if recommendation_id:
                payload["recommendation_id"] = recommendation_id
            
            response = requests.post(
                f"{self.API_BASE_URL}/api/v2/reports/create",
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
                timeout=10  # API는 즉시 202 반환하므로 짧은 타임아웃
            )
            
            if response.status_code in [200, 202]:
                # 성공 - 아무 메시지 출력하지 않음 (WebSocket으로 완료 알림 받을 예정)
                print(f"[UI] 보고서 생성 요청 성공: {response.json()}")
            else:
                # 실패 - 오류 메시지 표시
                error_msg = f"보고서 생성 요청 실패 (상태 코드: {response.status_code})"
                try:
                    error_detail = response.json().get('detail', '')
                    if error_detail:
                        error_msg = f"오류: {error_detail}"
                except:
                    pass
                
                self.message_queue.put({
                    'type': 'bot_response',
                    'response': error_msg,
                    'loading_widget': None
                })
                
        except requests.exceptions.Timeout:
            # 타임아웃 발생해도 백그라운드에서 진행 중 - 사용자에게 안내
            print(f"[UI] 보고서 생성 API 응답 지연 - 백그라운드에서 계속 진행 중")
            # 타임아웃은 정상적인 상황일 수 있음 (백엔드가 백그라운드 작업 시작함)
            # 사용자에게 알림 없이 조용히 처리 - WebSocket으로 완료 알림 받을 예정
        except requests.exceptions.RequestException as e:
            error_str = str(e)
            # Read timed out은 백그라운드 작업이 진행 중일 수 있음
            if "Read timed out" in error_str:
                print(f"[UI] 보고서 생성 API Read timeout - 백그라운드에서 계속 진행 중")
                # 타임아웃은 정상적인 상황일 수 있음 - WebSocket으로 완료 알림 받을 예정
            else:
                print(f"보고서 생성 API 호출 오류: {e}")
                self.message_queue.put({
                    'type': 'bot_response',
                    'response': f"서버 연결 오류: {error_str}",
                    'loading_widget': None
                })
    
    # ============================================================
    # Report Notification (보고서 완료/실패 알림)
    # ============================================================
    
    def show_report_notification(self, data):
        """보고서 생성 완료/실패 알림 말풍선을 표시합니다."""
        # 기존 보고서 알림이 있으면 닫기
        self.close_report_notification()
        
        success = data.get('success', False)
        keyword = data.get('keyword', '')
        
        # 말풍선 Toplevel 윈도우 생성
        self.report_notification_window = tk.Toplevel(self.root)
        self.report_notification_window.wm_overrideredirect(True)
        self.report_notification_window.attributes('-topmost', True)
        
        # macOS에서 팝업이 올바르게 표시되도록 설정
        self._setup_window_for_macos(self.report_notification_window, is_popup=True)
        
        if success:
            # 성공 알림
            file_path = data.get('file_path', '')
            file_name = data.get('file_name', '')
            self._create_report_success_bubble(keyword, file_path, file_name)
        else:
            # 실패 알림
            reason = data.get('reason', '알 수 없는 오류')
            self._create_report_failure_bubble(keyword, reason)
        
        self.report_notification_visible = True
        
        # 20초 후 자동 닫기
        self.report_auto_close_id = self.root.after(20000, self.close_report_notification)
    
    def _create_report_success_bubble(self, keyword, file_path, file_name):
        """보고서 성공 알림 말풍선 UI를 생성합니다."""
        self.report_notification_window.configure(bg='white')
        
        # 메인 프레임
        main_frame = tk.Frame(self.report_notification_window, bg='white', padx=2, pady=2)
        main_frame.pack(fill='both', expand=True)
        
        # 내부 컨테이너 (성공: 녹색 계열)
        inner_frame = tk.Frame(main_frame, bg=COLORS["success_bg"], padx=15, pady=12)
        inner_frame.pack(fill='both', expand=True)
        
        # 상단: 아이콘과 닫기 버튼
        header_frame = tk.Frame(inner_frame, bg=COLORS["success_bg"])
        header_frame.pack(fill='x', pady=(0, 8))
        
        # 📄 아이콘
        icon_label = tk.Label(
            header_frame,
            text="📄",
            font=('Arial', 16),
            bg='#f0fdf4'
        )
        icon_label.pack(side='left')
        
        # 키워드 라벨
        keyword_label = tk.Label(
            header_frame,
            text=f"'{keyword}' 보고서",
            font=(self.default_font, 10, 'bold'),
            bg=COLORS["success_bg"],
            fg='#166534'
        )
        keyword_label.pack(side='left', padx=(8, 0))
        
        # 닫기 버튼
        close_btn = tk.Button(
            header_frame,
            text="✕",
            font=(self.default_font, 10),
            bg=COLORS["success_bg"],
            fg='#9ca3af',
            relief='flat',
            cursor='hand2',
            command=self.close_report_notification,
            activebackground='#dcfce7'
        )
        close_btn.pack(side='right')
        
        # 메시지 라벨
        message_label = tk.Label(
            inner_frame,
            text=f"보고서를 PDF로 저장했어요! 열어볼까요?",
            font=(self.default_font, 11),
            bg=COLORS["success_bg"],
            fg='#1f2937',
            wraplength=250,
            justify='left'
        )
        message_label.pack(fill='x', pady=(0, 4))
        
        # 파일명 표시
        if file_name:
            filename_label = tk.Label(
                inner_frame,
                text=f"📁 {file_name}",
                font=(self.default_font, 9),
                bg=COLORS["success_bg"],
                fg='#6b7280',
                wraplength=250,
                justify='left'
            )
            filename_label.pack(fill='x', pady=(0, 12))
        
        # 버튼 프레임
        button_frame = tk.Frame(inner_frame, bg='#f0fdf4')
        button_frame.pack(fill='x')
        
        # [폴더 열기] 버튼
        open_btn = tk.Button(
            button_frame,
            text="폴더 열기 📂",
            font=(self.default_font, 10, 'bold'),
            bg='#22c55e',
            fg='white',
            relief='flat',
            cursor='hand2',
            padx=12,
            pady=6,
            command=lambda: self._open_report_folder(file_path),
            activebackground='#16a34a',
            activeforeground='white'
        )
        open_btn.pack(side='left', padx=(0, 8))
        
        # [닫기] 버튼
        dismiss_btn = tk.Button(
            button_frame,
            text="닫기",
            font=(self.default_font, 10),
            bg=COLORS["border"],
            fg='#4b5563',
            relief='flat',
            cursor='hand2',
            padx=12,
            pady=6,
            command=self.close_report_notification,
            activebackground='#d1d5db'
        )
        dismiss_btn.pack(side='left')
        
        # 말풍선 꼬리
        tail_canvas = tk.Canvas(
            self.report_notification_window,
            width=20,
            height=10,
            bg='white',
            highlightthickness=0
        )
        tail_canvas.pack(side='bottom')
        tail_canvas.create_polygon(
            0, 0,
            10, 10,
            20, 0,
            fill='#f0fdf4',
            outline='#f0fdf4'
        )
        
        # 테두리
        self.report_notification_window.configure(
            highlightbackground='#bbf7d0',
            highlightthickness=1
        )
        
        # 위치 계산
        self._position_report_bubble()
    
    def _create_report_failure_bubble(self, keyword, reason):
        """보고서 실패 알림 말풍선 UI를 생성합니다."""
        self.report_notification_window.configure(bg='white')
        
        # 메인 프레임
        main_frame = tk.Frame(self.report_notification_window, bg='white', padx=2, pady=2)
        main_frame.pack(fill='both', expand=True)
        
        # 내부 컨테이너 (실패: 빨간색 계열)
        inner_frame = tk.Frame(main_frame, bg='#fef2f2', padx=15, pady=12)
        inner_frame.pack(fill='both', expand=True)
        
        # 상단: 아이콘과 닫기 버튼
        header_frame = tk.Frame(inner_frame, bg='#fef2f2')
        header_frame.pack(fill='x', pady=(0, 8))
        
        # ❌ 아이콘
        icon_label = tk.Label(
            header_frame,
            text="❌",
            font=('Arial', 16),
            bg='#fef2f2'
        )
        icon_label.pack(side='left')
        
        # 키워드 라벨
        keyword_label = tk.Label(
            header_frame,
            text=f"'{keyword}' 보고서",
            font=(self.default_font, 10, 'bold'),
            bg='#fef2f2',
            fg='#991b1b'
        )
        keyword_label.pack(side='left', padx=(8, 0))
        
        # 닫기 버튼
        close_btn = tk.Button(
            header_frame,
            text="✕",
            font=(self.default_font, 10),
            bg='#fef2f2',
            fg='#9ca3af',
            relief='flat',
            cursor='hand2',
            command=self.close_report_notification,
            activebackground='#fee2e2'
        )
        close_btn.pack(side='right')
        
        # 메시지 라벨
        message_label = tk.Label(
            inner_frame,
            text=f"보고서 생성 중 오류가 발생했어요.",
            font=(self.default_font, 11),
            bg='#fef2f2',
            fg='#1f2937',
            wraplength=250,
            justify='left'
        )
        message_label.pack(fill='x', pady=(0, 4))
        
        # 오류 사유
        reason_label = tk.Label(
            inner_frame,
            text=f"사유: {reason}",
            font=(self.default_font, 9),
            bg='#fef2f2',
            fg='#6b7280',
            wraplength=250,
            justify='left'
        )
        reason_label.pack(fill='x', pady=(0, 12))
        
        # 버튼 프레임
        button_frame = tk.Frame(inner_frame, bg='#fef2f2')
        button_frame.pack(fill='x')
        
        # [닫기] 버튼
        dismiss_btn = tk.Button(
            button_frame,
            text="확인",
            font=(self.default_font, 10),
            bg=COLORS["border"],
            fg='#4b5563',
            relief='flat',
            cursor='hand2',
            padx=12,
            pady=6,
            command=self.close_report_notification,
            activebackground='#d1d5db'
        )
        dismiss_btn.pack(side='left')
        
        # 말풍선 꼬리
        tail_canvas = tk.Canvas(
            self.report_notification_window,
            width=20,
            height=10,
            bg='white',
            highlightthickness=0
        )
        tail_canvas.pack(side='bottom')
        tail_canvas.create_polygon(
            0, 0,
            10, 10,
            20, 0,
            fill='#fef2f2',
            outline='#fef2f2'
        )
        
        # 테두리
        self.report_notification_window.configure(
            highlightbackground='#fecaca',
            highlightthickness=1
        )
        
        # 위치 계산
        self._position_report_bubble()
    
    def _position_report_bubble(self):
        """보고서 알림 말풍선 위치를 계산합니다."""
        self.report_notification_window.update_idletasks()
        bubble_width = self.report_notification_window.winfo_reqwidth()
        bubble_height = self.report_notification_window.winfo_reqheight()
        
        button_x = self.root.winfo_x()
        button_y = self.root.winfo_y()
        
        # 버튼 중앙 위에 배치 (추천 알림과 겹치지 않도록 약간 오른쪽으로)
        x = button_x + 35 - (bubble_width // 2) + 50
        y = button_y - bubble_height - 10
        
        # 화면 경계 확인
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        
        if x < 10:
            x = 10
        if x + bubble_width > screen_width - 10:
            x = screen_width - bubble_width - 10
        if y < 10:
            y = button_y + 80
        
        self.report_notification_window.geometry(f"+{x}+{y}")
    
    def _open_report_folder(self, file_path):
        """보고서 파일이 있는 폴더를 엽니다. (플랫폼별 처리)"""
        try:
            if not file_path:
                print("[UI] 파일 경로가 없습니다.")
                return
            
            # 파일의 디렉토리 경로 추출
            folder_path = os.path.dirname(file_path)
            if not folder_path:
                folder_path = file_path
            
            system = platform.system()
            
            if system == "Windows":
                # Windows: explorer로 폴더 열기 (파일 선택)
                if os.path.isfile(file_path):
                    subprocess.run(['explorer', '/select,', file_path])
                else:
                    os.startfile(folder_path)
            elif system == "Darwin":
                # macOS: Finder로 열기
                if os.path.isfile(file_path):
                    subprocess.call(['open', '-R', file_path])
                else:
                    subprocess.call(['open', folder_path])
            else:
                # Linux: xdg-open으로 열기
                subprocess.call(['xdg-open', folder_path])
            
            print(f"[UI] 폴더 열기: {folder_path}")
            
        except Exception as e:
            print(f"[UI] 폴더 열기 오류: {e}")
        
        # 알림 닫기
        self.close_report_notification()
    
    def close_report_notification(self):
        """보고서 알림 말풍선을 닫습니다."""
        # 자동 닫기 타이머 취소
        if self.report_auto_close_id:
            self.root.after_cancel(self.report_auto_close_id)
            self.report_auto_close_id = None
        
        # 말풍선 파괴
        if self.report_notification_window and self.report_notification_window.winfo_exists():
            self.report_notification_window.destroy()
        
        self.report_notification_window = None
        self.report_notification_visible = False
    
    # ============================================================
    # Legacy Recommendation Notification (Backward Compatibility)
    # ============================================================
    
    def check_for_recommendations(self):
        """(Legacy) 주기적으로 서버에 새로운 추천이 있는지 확인합니다."""
        # poll_recommendations로 대체되었으므로 아무것도 하지 않음
        pass

    def _fetch_recommendations(self):
        """(Legacy) 추천 API를 호출합니다."""
        # poll_recommendations로 대체됨
        pass

    def dismiss_recommendation_notification(self):
        """(Legacy) 추천 알림을 닫습니다."""
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
        menu.add_command(label="ℹ️ 정보", command=lambda: messagebox.showinfo("JARVIS", "JARVIS Multi-Agent System\nVersion 1.0", parent=self.chat_window))
        
        # 설정 버튼 위치에 메뉴 표시 (헤더 높이 증가에 맞춰 조정)
        button_x = self.chat_window.winfo_rootx() + 450
        button_y = self.chat_window.winfo_rooty() + 60
        menu.post(button_x, button_y)
    
    def prompt_change_data_folder(self):
        """폴더 아이콘 클릭 시 확인 대화상자 표시 후 폴더 변경 진행"""
        import tkinter.messagebox as messagebox
        
        result = messagebox.askyesno(
            "데이터 폴더 변경",
            "데이터 폴더를 변경하시겠습니까?\n\n기존 데이터가 모두 삭제되고\n새로운 폴더에서 데이터를 수집합니다.",
            parent=self.chat_window
        )
        
        if result:
            self._proceed_change_data_folder()
    
    def _proceed_change_data_folder(self):
        """폴더 변경 진행 (확인 후 호출됨)"""
        import tkinter.messagebox as messagebox
        import sys
        from pathlib import Path
        
        # 기존 선택된 폴더 가져오기
        current_folders = self._get_current_selected_folders()
        
        # 폴더 선택 UI 표시
        try:
            sys.path.insert(0, str(Path("frontend")))
            from folder_selector import select_folders
            
            # 폴더 선택 (기존 선택 항목 전달)
            selected_folders = select_folders(initial_selections=current_folders)
            
            if selected_folders == "cancelled":
                messagebox.showinfo("알림", "폴더 선택이 취소되었습니다.", parent=self.chat_window)
                return
            
            # 폴더 경로 결정
            if selected_folders is None:
                # 전체 사용자 폴더 스캔
                folder_path = ""
            elif selected_folders:
                # 첫 번째 폴더 사용
                folder_path = selected_folders[0]
            else:
                messagebox.showwarning("오류", "올바른 폴더를 선택해주세요.", parent=self.chat_window)
                return
            
            # 백엔드 API 호출
            self.call_update_folder_api(folder_path)
            
        except Exception as e:
            messagebox.showerror("오류", f"폴더 선택 중 오류가 발생했습니다: {e}", parent=self.chat_window)
    
    def change_data_folder(self):
        """데이터 폴더 변경 (레거시 호환용 - prompt_change_data_folder 호출)"""
        self.prompt_change_data_folder()
    
    def _get_current_selected_folders(self) -> list:
        """현재 설정된 폴더 경로를 API에서 가져옵니다."""
        try:
            token = self.jwt_token or load_token()
            if not token:
                return []
            
            # /auth/me 엔드포인트 사용 (selected_root_folder 반환)
            response = requests.get(
                f"{self.API_BASE_URL}/api/v2/auth/me",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                selected_folder = data.get("selected_root_folder")
                if selected_folder:
                    # 콤마로 구분된 여러 폴더일 수 있음
                    if "," in selected_folder:
                        return [f.strip() for f in selected_folder.split(",") if f.strip()]
                    return [selected_folder]
            return []
        except Exception as e:
            print(f"[UI] 현재 폴더 정보 가져오기 실패: {e}")
            return []
    
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
