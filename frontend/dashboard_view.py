#!/usr/bin/env python3
"""
JARVIS Dashboard View
개인 대시보드 창 - 관심사 트렌드, 활동 요약, 노트 기능
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import threading
import requests
from datetime import datetime
from typing import Optional, Dict, Any, List, Callable

from theme import COLORS, style_button, BUTTON_STYLES
from config import API_BASE_URL


class DashboardWindow:
    """개인 대시보드 창"""
    
    def __init__(self, parent_app, user_id: int, jwt_token: str):
        """
        Args:
            parent_app: FloatingChatApp 인스턴스 (폰트 등 공유)
            user_id: 현재 사용자 ID
            jwt_token: API 인증용 JWT 토큰
        """
        self.parent_app = parent_app
        self.user_id = user_id
        self.jwt_token = jwt_token
        self.API_BASE_URL = f"{API_BASE_URL}/api/v2"
        
        # 폰트 설정
        self.default_font = getattr(parent_app, 'default_font', 'Malgun Gothic')
        self.title_font = (self.default_font, 18, 'bold')
        self.subtitle_font = (self.default_font, 14, 'bold')
        self.body_font = (self.default_font, 11)
        self.small_font = (self.default_font, 10)
        
        # 데이터 캐시
        self.dashboard_data: Dict[str, Any] = {}
        self.notes: List[Dict[str, Any]] = []
        self.current_note_id: Optional[int] = None
        self.latest_analysis: Optional[Dict[str, Any]] = None
        
        # 노트 페이지네이션
        self.notes_page = 0
        self.notes_per_page = 3
        
        # 창 생성
        self.window: Optional[tk.Toplevel] = None
        self._create_window()
    
    def _create_window(self):
        """대시보드 창 생성"""
        self.window = tk.Toplevel()
        self.window.title("JARVIS 대시보드")
        self.window.geometry("900x700")
        self.window.configure(bg=COLORS["surface_alt"])
        self.window.minsize(800, 600)
        
        # 창이 닫힐 때 정리
        self.window.protocol("WM_DELETE_WINDOW", self._on_close)
        
        # 메인 컨테이너 (스크롤 가능)
        self._create_main_layout()
        
        # 데이터 로드
        self._load_dashboard_data()
    
    def _create_main_layout(self):
        """메인 레이아웃 생성"""
        # 헤더
        self._create_header()
        
        # 스크롤 가능한 콘텐츠 영역
        content_container = tk.Frame(self.window, bg=COLORS["surface_alt"])
        content_container.pack(fill='both', expand=True, padx=20, pady=10)
        
        # 캔버스 + 스크롤바
        self.canvas = tk.Canvas(content_container, bg=COLORS["surface_alt"], highlightthickness=0)
        scrollbar = ttk.Scrollbar(content_container, orient="vertical", command=self.canvas.yview)
        self.scrollable_frame = tk.Frame(self.canvas, bg=COLORS["surface_alt"])
        
        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        
        self.canvas_window = self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=scrollbar.set)
        
        # 캔버스 너비 조정
        def on_canvas_configure(event):
            self.canvas.itemconfig(self.canvas_window, width=event.width)
        self.canvas.bind("<Configure>", on_canvas_configure)
        
        # 마우스 휠 스크롤 바인딩
        self._bind_scroll_events(self.canvas)
        self._bind_scroll_events(self.scrollable_frame)
        
        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        
        # 콘텐츠 섹션들
        self._create_profile_section()
        self._create_activity_section()
        self._create_interests_section()
        self._create_analysis_section()  # AI 분석 결과 섹션 추가
        self._create_notes_section()
    
    def _create_header(self):
        """헤더 생성"""
        header = tk.Frame(self.window, bg=COLORS["dashboard_header"], height=70)
        header.pack(fill='x')
        header.pack_propagate(False)
        
        # 제목
        title_frame = tk.Frame(header, bg=COLORS["dashboard_header"])
        title_frame.pack(side='left', padx=20, pady=15)
        
        tk.Label(
            title_frame,
            text="📊 내 대시보드",
            font=self.title_font,
            bg=COLORS["dashboard_header"],
            fg=COLORS["text_inverse"]
        ).pack(anchor='w')
        
        tk.Label(
            title_frame,
            text="관심사와 활동을 한눈에 확인하세요",
            font=self.small_font,
            bg=COLORS["dashboard_header"],
            fg=COLORS["text_muted"]
        ).pack(anchor='w')
        
        # 새로고침 버튼
        btn_frame = tk.Frame(header, bg=COLORS["dashboard_header"])
        btn_frame.pack(side='right', padx=20, pady=15)
        
        refresh_btn = tk.Button(
            btn_frame,
            text="🔄 새로고침",
            font=self.small_font,
            command=self._load_dashboard_data
        )
        style_button(refresh_btn, variant="ghost")
        refresh_btn.pack()
    
    def _create_card(self, parent, title: str, icon: str = "") -> tk.Frame:
        """카드 컴포넌트 생성"""
        card = tk.Frame(
            parent,
            bg=COLORS["dashboard_card"],
            highlightbackground=COLORS["dashboard_card_border"],
            highlightthickness=1
        )
        self._bind_scroll_events(card)
        
        # 카드 헤더
        header = tk.Frame(card, bg=COLORS["dashboard_card"])
        header.pack(fill='x', padx=15, pady=(15, 10))
        self._bind_scroll_events(header)
        
        title_label = tk.Label(
            header,
            text=f"{icon} {title}" if icon else title,
            font=self.subtitle_font,
            bg=COLORS["dashboard_card"],
            fg=COLORS["text_primary"]
        )
        title_label.pack(anchor='w')
        self._bind_scroll_events(title_label)
        
        # 구분선
        tk.Frame(card, bg=COLORS["border"], height=1).pack(fill='x', padx=15)
        
        # 콘텐츠 영역
        content = tk.Frame(card, bg=COLORS["dashboard_card"])
        content.pack(fill='both', expand=True, padx=15, pady=15)
        self._bind_scroll_events(content)
        
        return content
    
    def _create_profile_section(self):
        """프로필 섹션"""
        self.profile_card = self._create_card(self.scrollable_frame, "프로필", "👤")
        self.profile_card.master.pack(fill='x', pady=(0, 15))
        
        # 로딩 표시
        self.profile_loading = tk.Label(
            self.profile_card,
            text="로딩 중...",
            font=self.body_font,
            bg=COLORS["dashboard_card"],
            fg=COLORS["text_muted"]
        )
        self.profile_loading.pack(pady=20)
    
    def _create_activity_section(self):
        """활동 요약 섹션"""
        self.activity_card = self._create_card(self.scrollable_frame, "최근 활동 (7일)", "📈")
        self.activity_card.master.pack(fill='x', pady=(0, 15))
        
        self.activity_loading = tk.Label(
            self.activity_card,
            text="로딩 중...",
            font=self.body_font,
            bg=COLORS["dashboard_card"],
            fg=COLORS["text_muted"]
        )
        self.activity_loading.pack(pady=20)
    
    def _create_interests_section(self):
        """관심사 섹션"""
        self.interests_card = self._create_card(self.scrollable_frame, "관심사 TOP 5", "💡")
        self.interests_card.master.pack(fill='x', pady=(0, 15))
        
        self.interests_loading = tk.Label(
            self.interests_card,
            text="로딩 중...",
            font=self.body_font,
            bg=COLORS["dashboard_card"],
            fg=COLORS["text_muted"]
        )
        self.interests_loading.pack(pady=20)
    
    def _create_analysis_section(self):
        """AI 분석 결과 섹션"""
        self.analysis_card = self._create_card(self.scrollable_frame, "AI 분석 결과", "🔍")
        self.analysis_card.master.pack(fill='x', pady=(0, 15))
        
        self.analysis_loading = tk.Label(
            self.analysis_card,
            text="로딩 중...",
            font=self.body_font,
            bg=COLORS["dashboard_card"],
            fg=COLORS["text_muted"]
        )
        self.analysis_loading.pack(pady=20)
    
    def _create_notes_section(self):
        """노트 섹션"""
        notes_container = tk.Frame(self.scrollable_frame, bg=COLORS["surface_alt"])
        notes_container.pack(fill='x', pady=(0, 15))
        self._bind_scroll_events(notes_container)
        
        # 노트 카드
        self.notes_card = self._create_card(notes_container, "아이디어 노트", "📝")
        self.notes_card.master.pack(fill='x')
        
        # 노트 입력 영역
        input_frame = tk.Frame(self.notes_card, bg=COLORS["dashboard_card"])
        input_frame.pack(fill='x', pady=(0, 10))
        self._bind_scroll_events(input_frame)
        
        # 제목 입력
        title_label = tk.Label(
            input_frame,
            text="제목",
            font=self.small_font,
            bg=COLORS["dashboard_card"],
            fg=COLORS["text_secondary"]
        )
        title_label.pack(anchor='w')
        self._bind_scroll_events(title_label)
        
        self.note_title_entry = tk.Entry(
            input_frame,
            font=self.body_font,
            bg=COLORS["surface_alt"],
            fg=COLORS["text_primary"],
            relief='flat',
            highlightbackground=COLORS["border"],
            highlightthickness=1
        )
        self.note_title_entry.pack(fill='x', pady=(2, 10))
        self._bind_scroll_events(self.note_title_entry)
        
        # 내용 입력
        content_label = tk.Label(
            input_frame,
            text="내용",
            font=self.small_font,
            bg=COLORS["dashboard_card"],
            fg=COLORS["text_secondary"]
        )
        content_label.pack(anchor='w')
        self._bind_scroll_events(content_label)
        
        self.note_content_text = scrolledtext.ScrolledText(
            input_frame,
            font=self.body_font,
            bg=COLORS["note_bg"],
            fg=COLORS["text_primary"],
            relief='flat',
            height=4,
            wrap='word',
            highlightbackground=COLORS["note_border"],
            highlightthickness=1
        )
        self.note_content_text.pack(fill='x', pady=(2, 10))
        # ScrolledText 내부 위젯들에도 스크롤 바인딩 (부모 캔버스로 전파)
        self._bind_scrolled_text_to_canvas(self.note_content_text)
        
        # 버튼 영역
        btn_frame = tk.Frame(input_frame, bg=COLORS["dashboard_card"])
        btn_frame.pack(fill='x')
        self._bind_scroll_events(btn_frame)
        
        save_btn = tk.Button(
            btn_frame,
            text="💾 저장",
            font=self.small_font,
            command=self._save_note
        )
        style_button(save_btn, variant="secondary")
        save_btn.pack(side='left', padx=(0, 5))
        
        clear_btn = tk.Button(
            btn_frame,
            text="🗑️ 초기화",
            font=self.small_font,
            command=self._clear_note_form
        )
        style_button(clear_btn, variant="secondary")
        clear_btn.pack(side='left')
        
        # 노트 목록 영역
        tk.Frame(self.notes_card, bg=COLORS["border"], height=1).pack(fill='x', pady=15)
        
        saved_notes_label = tk.Label(
            self.notes_card,
            text="저장된 노트",
            font=self.small_font,
            bg=COLORS["dashboard_card"],
            fg=COLORS["text_secondary"]
        )
        saved_notes_label.pack(anchor='w', pady=(0, 10))
        self._bind_scroll_events(saved_notes_label)
        
        self.notes_list_frame = tk.Frame(self.notes_card, bg=COLORS["dashboard_card"])
        self.notes_list_frame.pack(fill='x')
        self._bind_scroll_events(self.notes_list_frame)
        
        self.notes_loading = tk.Label(
            self.notes_list_frame,
            text="로딩 중...",
            font=self.body_font,
            bg=COLORS["dashboard_card"],
            fg=COLORS["text_muted"]
        )
        self.notes_loading.pack(pady=10)
        self._bind_scroll_events(self.notes_loading)
    
    def _load_dashboard_data(self):
        """대시보드 데이터 로드 (비동기)"""
        def load():
            try:
                headers = {"Authorization": f"Bearer {self.jwt_token}"}
                
                # 대시보드 요약 API 호출
                response = requests.get(
                    f"{self.API_BASE_URL}/dashboard/summary",
                    headers=headers,
                    timeout=10
                )
                
                if response.status_code == 200:
                    data = response.json()
                    if data.get("success"):
                        self.dashboard_data = data.get("data", {})
                        self.window.after(0, self._update_profile_ui)
                        self.window.after(0, self._update_activity_ui)
                        self.window.after(0, self._update_interests_ui)
                
                # 노트 로드
                notes_response = requests.get(
                    f"{self.API_BASE_URL}/dashboard/notes",
                    headers=headers,
                    timeout=10
                )
                
                if notes_response.status_code == 200:
                    notes_data = notes_response.json()
                    if notes_data.get("success"):
                        self.notes = notes_data.get("data", {}).get("notes", [])
                        self.window.after(0, self._update_notes_ui)
                
                # AI 분석 결과 로드 (최신 1개만)
                analysis_response = requests.get(
                    f"{self.API_BASE_URL}/dashboard/analyses/latest",
                    headers=headers,
                    timeout=10
                )
                
                if analysis_response.status_code == 200:
                    analysis_data = analysis_response.json()
                    if analysis_data.get("success"):
                        self.latest_analysis = analysis_data.get("data", {}).get("analysis")
                        self.window.after(0, self._update_analysis_ui)
                        
            except Exception as e:
                print(f"[Dashboard] 데이터 로드 오류: {e}")
                self.window.after(0, lambda: self._show_error("데이터 로드 실패"))
        
        threading.Thread(target=load, daemon=True).start()
    
    def _update_profile_ui(self):
        """프로필 UI 업데이트"""
        # 기존 위젯 제거 (로딩 포함)
        for widget in self.profile_card.winfo_children():
            widget.destroy()
        
        user_data = self.dashboard_data.get("user", {})
        
        # 이메일
        email = user_data.get("email", "알 수 없음")
        email_label = tk.Label(
            self.profile_card,
            text=f"📧 {email}",
            font=self.body_font,
            bg=COLORS["dashboard_card"],
            fg=COLORS["text_primary"]
        )
        email_label.pack(anchor='w', pady=2)
        self._bind_scroll_events(email_label)
        
        # 선택된 폴더
        folder = user_data.get("selected_folder", "설정 안됨")
        folder_display = folder if folder else "설정 안됨"
        if len(folder_display) > 50:
            folder_display = "..." + folder_display[-47:]
        folder_label = tk.Label(
            self.profile_card,
            text=f"📁 {folder_display}",
            font=self.body_font,
            bg=COLORS["dashboard_card"],
            fg=COLORS["text_secondary"]
        )
        folder_label.pack(anchor='w', pady=2)
        self._bind_scroll_events(folder_label)
        
        # 가입일
        created = user_data.get("created_at", "")
        if created:
            try:
                dt = datetime.fromisoformat(created.replace('Z', '+00:00'))
                created_str = dt.strftime("%Y년 %m월 %d일")
            except:
                created_str = created
            created_label = tk.Label(
                self.profile_card,
                text=f"📅 가입일: {created_str}",
                font=self.small_font,
                bg=COLORS["dashboard_card"],
                fg=COLORS["text_muted"]
            )
            created_label.pack(anchor='w', pady=2)
            self._bind_scroll_events(created_label)
    
    def _update_activity_ui(self):
        """활동 UI 업데이트"""
        # 기존 위젯 제거 (로딩 포함)
        for widget in self.activity_card.winfo_children():
            widget.destroy()
        
        activity = self.dashboard_data.get("activity", {})
        
        # 활동 통계 그리드
        stats_frame = tk.Frame(self.activity_card, bg=COLORS["dashboard_card"])
        stats_frame.pack(fill='x')
        self._bind_scroll_events(stats_frame)
        
        stats = [
            ("💬", "채팅", activity.get("chat_messages", 0)),
            ("🌐", "웹 방문", activity.get("browser_visits", 0)),
            ("📄", "파일 처리", activity.get("files_processed", 0)),
        ]
        
        for i, (icon, label, value) in enumerate(stats):
            stat_frame = tk.Frame(stats_frame, bg=COLORS["surface_alt"], padx=15, pady=10)
            stat_frame.grid(row=0, column=i, padx=5, pady=5, sticky='ew')
            stats_frame.columnconfigure(i, weight=1)
            self._bind_scroll_events(stat_frame)
            
            icon_label = tk.Label(
                stat_frame,
                text=icon,
                font=('Arial', 20),
                bg=COLORS["surface_alt"]
            )
            icon_label.pack()
            self._bind_scroll_events(icon_label)
            
            value_label = tk.Label(
                stat_frame,
                text=str(value),
                font=(self.default_font, 16, 'bold'),
                bg=COLORS["surface_alt"],
                fg=COLORS["chart_primary"]
            )
            value_label.pack()
            self._bind_scroll_events(value_label)
            
            text_label = tk.Label(
                stat_frame,
                text=label,
                font=self.small_font,
                bg=COLORS["surface_alt"],
                fg=COLORS["text_muted"]
            )
            text_label.pack()
            self._bind_scroll_events(text_label)
        
        # 추천 통계
        rec = activity.get("recommendations", {})
        rec_frame = tk.Frame(self.activity_card, bg=COLORS["dashboard_card"])
        rec_frame.pack(fill='x', pady=(10, 0))
        self._bind_scroll_events(rec_frame)
        
        rec_label = tk.Label(
            rec_frame,
            text=f"💡 추천: {rec.get('total', 0)}건 (수락 {rec.get('accepted', 0)} / 거절 {rec.get('rejected', 0)})",
            font=self.body_font,
            bg=COLORS["dashboard_card"],
            fg=COLORS["text_secondary"]
        )
        rec_label.pack(anchor='w')
        self._bind_scroll_events(rec_label)
    
    def _update_interests_ui(self):
        """관심사 UI 업데이트"""
        # 기존 위젯 제거 (로딩 포함)
        for widget in self.interests_card.winfo_children():
            widget.destroy()
        
        interests_data = self.dashboard_data.get("interests", {})
        top_interests = interests_data.get("top_interests", [])
        
        if not top_interests:
            empty_label = tk.Label(
                self.interests_card,
                text="아직 관심사가 없습니다. 채팅을 통해 관심사를 쌓아보세요!",
                font=self.body_font,
                bg=COLORS["dashboard_card"],
                fg=COLORS["text_muted"]
            )
            empty_label.pack(pady=10)
            self._bind_scroll_events(empty_label)
            return
        
        # 관심사 막대 그래프 (간단한 버전)
        max_score = max(i.get("score", 0) for i in top_interests) if top_interests else 1
        
        for interest in top_interests:
            item_frame = tk.Frame(self.interests_card, bg=COLORS["dashboard_card"])
            item_frame.pack(fill='x', pady=3)
            self._bind_scroll_events(item_frame)
            
            keyword = interest.get("keyword", "")
            score = interest.get("score", 0)
            bar_width = int((score / max_score) * 200) if max_score > 0 else 0
            
            # 키워드
            keyword_label = tk.Label(
                item_frame,
                text=keyword,
                font=self.body_font,
                bg=COLORS["dashboard_card"],
                fg=COLORS["text_primary"],
                width=15,
                anchor='w'
            )
            keyword_label.pack(side='left')
            self._bind_scroll_events(keyword_label)
            
            # 막대
            bar_container = tk.Frame(item_frame, bg=COLORS["surface_alt"], width=200, height=20)
            bar_container.pack(side='left', padx=10)
            bar_container.pack_propagate(False)
            self._bind_scroll_events(bar_container)
            
            bar = tk.Frame(bar_container, bg=COLORS["chart_primary"], width=bar_width, height=20)
            bar.pack(side='left')
            self._bind_scroll_events(bar)
            
            # 점수
            score_label = tk.Label(
                item_frame,
                text=f"{score:.2f}",
                font=self.small_font,
                bg=COLORS["dashboard_card"],
                fg=COLORS["text_muted"]
            )
            score_label.pack(side='left', padx=5)
            self._bind_scroll_events(score_label)
    
    def _update_analysis_ui(self):
        """AI 분석 결과 UI 업데이트 (차트 위주)"""
        # 기존 위젯 제거 (로딩 포함)
        for widget in self.analysis_card.winfo_children():
            widget.destroy()
        
        if not self.latest_analysis:
            empty_label = tk.Label(
                self.analysis_card,
                text="아직 분석 결과가 없습니다.\n채팅에서 '내 활동 분석해줘', '관심사 트렌드 보여줘' 등을 요청해보세요!",
                font=self.body_font,
                bg=COLORS["dashboard_card"],
                fg=COLORS["text_muted"],
                justify='center'
            )
            empty_label.pack(pady=20)
            self._bind_scroll_events(empty_label)
            return
        
        analysis = self.latest_analysis
        
        # 분석 제목 및 날짜
        title_frame = tk.Frame(self.analysis_card, bg=COLORS["dashboard_card"])
        title_frame.pack(fill='x', pady=(0, 10))
        self._bind_scroll_events(title_frame)
        
        title = analysis.get("title", "데이터 분석")
        created_at = analysis.get("created_at", "")
        
        # 날짜 포맷
        date_str = ""
        if created_at:
            try:
                dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                date_str = dt.strftime("%Y.%m.%d %H:%M")
            except:
                date_str = created_at[:16] if len(created_at) > 16 else created_at
        
        title_label = tk.Label(
            title_frame,
            text=f"📊 {title}",
            font=(self.default_font, 12, 'bold'),
            bg=COLORS["dashboard_card"],
            fg=COLORS["text_primary"],
            anchor='w'
        )
        title_label.pack(side='left')
        self._bind_scroll_events(title_label)
        
        date_label = tk.Label(
            title_frame,
            text=date_str,
            font=self.small_font,
            bg=COLORS["dashboard_card"],
            fg=COLORS["text_muted"]
        )
        date_label.pack(side='right')
        self._bind_scroll_events(date_label)
        
        # 📊 차트 표시 (여러 개 지원)
        chart_data = analysis.get("chart_data", {})
        charts = []
        
        # 새 형식: {"charts": [...]}
        if chart_data and isinstance(chart_data, dict) and "charts" in chart_data:
            charts = chart_data.get("charts", [])
        # 기존 형식: 단일 차트 객체
        elif chart_data and chart_data.get("type") and chart_data.get("type") != "empty":
            charts = [chart_data]
        
        # 여러 차트 표시 (최대 3개까지 대시보드에 표시)
        for i, single_chart in enumerate(charts[:3]):
            if single_chart and single_chart.get("type") != "empty":
                self._draw_analysis_chart(single_chart, compact=(i > 0))
        
        # 💡 핵심 인사이트 (간단하게 1-2줄)
        insights = analysis.get("insights", [])
        if insights:
            insights_frame = tk.Frame(self.analysis_card, bg=COLORS["primary_soft"], padx=10, pady=8)
            insights_frame.pack(fill='x', pady=(10, 10))
            self._bind_scroll_events(insights_frame)
            
            # 첫 번째 인사이트만 크게 표시
            main_insight = insights[0] if insights else ""
            insight_label = tk.Label(
                insights_frame,
                text=f"💡 {main_insight}",
                font=(self.default_font, 10, 'bold'),
                bg=COLORS["primary_soft"],
                fg=COLORS["text_primary"],
                anchor='w',
                wraplength=500,
                justify='left'
            )
            insight_label.pack(anchor='w')
            self._bind_scroll_events(insight_label)
        
        # 전체 보기 버튼
        btn_frame = tk.Frame(self.analysis_card, bg=COLORS["dashboard_card"])
        btn_frame.pack(fill='x', pady=(5, 0))
        self._bind_scroll_events(btn_frame)
        
        view_btn = tk.Button(
            btn_frame,
            text="📋 전체 분석 결과 보기",
            font=self.small_font,
            command=lambda: self._show_full_analysis(analysis),
            relief='flat',
            bg=COLORS["primary"],
            fg=COLORS["text_inverse"],
            activebackground=COLORS["primary_dark"],
            activeforeground=COLORS["text_inverse"],
            cursor='hand2',
            padx=15,
            pady=5
        )
        view_btn.pack(anchor='w')
        self._bind_scroll_events(view_btn)
    
    def _draw_analysis_chart(self, chart_data: Dict[str, Any], compact: bool = False):
        """차트를 캔버스에 직접 그립니다.
        
        Args:
            chart_data: 차트 데이터 딕셔너리
            compact: True면 작은 크기로 표시 (두 번째 이후 차트용)
        """
        chart_type = chart_data.get("type", "")
        chart_title = chart_data.get("title", "")
        
        # 차트 프레임
        chart_frame = tk.Frame(self.analysis_card, bg=COLORS["surface_alt"], padx=10, pady=8 if compact else 10)
        chart_frame.pack(fill='x', pady=(0, 5))
        self._bind_scroll_events(chart_frame)
        
        # 차트 제목
        if chart_title:
            chart_title_label = tk.Label(
                chart_frame,
                text=chart_title,
                font=(self.default_font, 9 if compact else 10, 'bold'),
                bg=COLORS["surface_alt"],
                fg=COLORS["text_primary"]
            )
            chart_title_label.pack(anchor='w', pady=(0, 5))
            self._bind_scroll_events(chart_title_label)
        
        # 캔버스 생성 (compact 모드에서는 더 작게)
        canvas_width = 500
        canvas_height = 150 if compact else 200
        chart_canvas = tk.Canvas(
            chart_frame,
            width=canvas_width,
            height=canvas_height,
            bg=COLORS["surface_alt"],
            highlightthickness=0
        )
        chart_canvas.pack(fill='x')
        self._bind_scroll_events(chart_canvas)
        
        # Plotly JSON에서 데이터 추출하여 간단한 막대 그래프 그리기
        try:
            import json
            plotly_json = chart_data.get("plotly_json", "")
            if plotly_json:
                plotly_data = json.loads(plotly_json) if isinstance(plotly_json, str) else plotly_json
                data_traces = plotly_data.get("data", [])
                
                if data_traces:
                    trace = data_traces[0]
                    
                    # 막대 그래프 (수평)
                    if chart_type == "bar" and trace.get("orientation") == "h":
                        self._draw_horizontal_bar_chart(chart_canvas, trace, canvas_width, canvas_height)
                    # 막대 그래프 (수직)
                    elif chart_type in ("bar", "grouped_bar"):
                        self._draw_vertical_bar_chart(chart_canvas, data_traces, canvas_width, canvas_height)
                    # 파이 차트
                    elif chart_type == "pie":
                        self._draw_pie_chart(chart_canvas, trace, canvas_width, canvas_height)
                    else:
                        # 기본: 수직 막대
                        self._draw_vertical_bar_chart(chart_canvas, data_traces, canvas_width, canvas_height)
        except Exception as e:
            print(f"[Dashboard] 차트 그리기 오류: {e}")
            # 차트 그리기 실패 시 메시지 표시
            chart_canvas.create_text(
                canvas_width // 2, canvas_height // 2,
                text="차트를 표시할 수 없습니다",
                fill=COLORS["text_muted"],
                font=self.body_font
            )
    
    def _draw_horizontal_bar_chart(self, canvas, trace, width, height):
        """수평 막대 그래프 그리기"""
        x_values = trace.get("x", [])
        y_labels = trace.get("y", [])
        
        if not x_values or not y_labels:
            return
        
        # 상위 5개만
        x_values = x_values[:5]
        y_labels = y_labels[:5]
        
        max_val = max(x_values) if x_values else 1
        bar_height = 25
        spacing = 10
        left_margin = 100
        right_margin = 50
        top_margin = 10
        
        colors = ["#6366F1", "#8B5CF6", "#A78BFA", "#C4B5FD", "#DDD6FE"]
        
        for i, (val, label) in enumerate(zip(x_values, y_labels)):
            y = top_margin + i * (bar_height + spacing)
            bar_width = int((val / max_val) * (width - left_margin - right_margin))
            
            # 라벨
            label_text = str(label)[:12] + "..." if len(str(label)) > 12 else str(label)
            canvas.create_text(
                left_margin - 5, y + bar_height // 2,
                text=label_text,
                anchor='e',
                fill=COLORS["text_primary"],
                font=self.small_font
            )
            
            # 막대
            color = colors[i % len(colors)]
            canvas.create_rectangle(
                left_margin, y,
                left_margin + bar_width, y + bar_height,
                fill=color,
                outline=""
            )
            
            # 값
            canvas.create_text(
                left_margin + bar_width + 5, y + bar_height // 2,
                text=f"{val:.1f}" if isinstance(val, float) else str(val),
                anchor='w',
                fill=COLORS["text_muted"],
                font=self.small_font
            )
    
    def _draw_vertical_bar_chart(self, canvas, traces, width, height):
        """수직 막대 그래프 그리기"""
        if not traces:
            return
        
        trace = traces[0]
        x_labels = trace.get("x", [])
        y_values = trace.get("y", [])
        
        if not x_labels or not y_values:
            return
        
        # 상위 5개만
        x_labels = x_labels[:5]
        y_values = y_values[:5]
        
        max_val = max(y_values) if y_values else 1
        bar_width = 50
        spacing = 20
        left_margin = 50
        bottom_margin = 40
        top_margin = 20
        
        colors = ["#3B82F6", "#10B981", "#F59E0B", "#EF4444", "#8B5CF6"]
        chart_height = height - top_margin - bottom_margin
        
        for i, (label, val) in enumerate(zip(x_labels, y_values)):
            x = left_margin + i * (bar_width + spacing)
            bar_height = int((val / max_val) * chart_height) if max_val > 0 else 0
            y = height - bottom_margin - bar_height
            
            # 막대
            color = colors[i % len(colors)]
            canvas.create_rectangle(
                x, y,
                x + bar_width, height - bottom_margin,
                fill=color,
                outline=""
            )
            
            # 값 (막대 위)
            canvas.create_text(
                x + bar_width // 2, y - 5,
                text=str(val),
                fill=COLORS["text_primary"],
                font=self.small_font
            )
            
            # 라벨 (아래)
            label_text = str(label)[:6] + ".." if len(str(label)) > 6 else str(label)
            canvas.create_text(
                x + bar_width // 2, height - bottom_margin + 15,
                text=label_text,
                fill=COLORS["text_secondary"],
                font=self.small_font
            )
    
    def _draw_pie_chart(self, canvas, trace, width, height):
        """파이 차트 그리기"""
        labels = trace.get("labels", [])
        values = trace.get("values", [])
        
        if not labels or not values:
            return
        
        # 상위 4개만
        labels = labels[:4]
        values = values[:4]
        
        total = sum(values) if values else 1
        colors = ["#10B981", "#EF4444", "#F59E0B", "#6B7280"]
        
        cx = width // 3
        cy = height // 2
        radius = min(cx, cy) - 20
        
        start_angle = 0
        for i, (label, val) in enumerate(zip(labels, values)):
            extent = (val / total) * 360 if total > 0 else 0
            color = colors[i % len(colors)]
            
            # 파이 조각
            canvas.create_arc(
                cx - radius, cy - radius,
                cx + radius, cy + radius,
                start=start_angle,
                extent=extent,
                fill=color,
                outline="white",
                width=2
            )
            
            start_angle += extent
        
        # 범례
        legend_x = width // 2 + 30
        legend_y = 30
        for i, (label, val) in enumerate(zip(labels, values)):
            color = colors[i % len(colors)]
            pct = (val / total * 100) if total > 0 else 0
            
            # 색상 박스
            canvas.create_rectangle(
                legend_x, legend_y + i * 25,
                legend_x + 15, legend_y + i * 25 + 15,
                fill=color,
                outline=""
            )
            
            # 라벨
            canvas.create_text(
                legend_x + 20, legend_y + i * 25 + 7,
                text=f"{label}: {pct:.0f}%",
                anchor='w',
                fill=COLORS["text_primary"],
                font=self.small_font
            )
    
    def _show_full_analysis(self, analysis: Dict[str, Any]):
        """전체 분석 결과를 새 창에서 표시"""
        # 새 창 생성
        detail_window = tk.Toplevel(self.window)
        detail_window.title(f"분석 결과: {analysis.get('title', '데이터 분석')}")
        detail_window.geometry("700x600")
        detail_window.configure(bg=COLORS["surface_alt"])
        
        # 헤더
        header = tk.Frame(detail_window, bg=COLORS["dashboard_header"], height=50)
        header.pack(fill='x')
        header.pack_propagate(False)
        
        title_label = tk.Label(
            header,
            text=f"📊 {analysis.get('title', '데이터 분석')}",
            font=self.subtitle_font,
            bg=COLORS["dashboard_header"],
            fg=COLORS["text_inverse"]
        )
        title_label.pack(side='left', padx=20, pady=10)
        
        # 닫기 버튼
        close_btn = tk.Button(
            header,
            text="✕",
            font=self.body_font,
            command=detail_window.destroy,
            relief='flat',
            bg=COLORS["dashboard_header"],
            fg=COLORS["text_inverse"],
            activebackground=COLORS["danger_bg"],
            cursor='hand2'
        )
        close_btn.pack(side='right', padx=10, pady=10)
        
        # 콘텐츠 영역 (스크롤 가능)
        content_frame = tk.Frame(detail_window, bg=COLORS["surface_alt"])
        content_frame.pack(fill='both', expand=True, padx=20, pady=15)
        
        # 스크롤바
        canvas = tk.Canvas(content_frame, bg=COLORS["surface_alt"], highlightthickness=0)
        scrollbar = ttk.Scrollbar(content_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = tk.Frame(canvas, bg=COLORS["surface_alt"])
        
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        
        canvas_window = canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        # 캔버스 너비를 창 크기에 맞게 자동 조정
        def on_canvas_configure(event):
            canvas.itemconfig(canvas_window, width=event.width)
        canvas.bind("<Configure>", on_canvas_configure)
        
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        
        # 마우스 휠 스크롤을 위한 함수
        def on_mousewheel(event):
            import platform
            system = platform.system()
            if system == "Darwin":  # macOS
                delta = -1 * event.delta
            elif system == "Windows":
                delta = -1 * (event.delta // 120)
            else:  # Linux
                if event.num == 4:
                    delta = -1
                elif event.num == 5:
                    delta = 1
                else:
                    delta = -1 * (event.delta // 120)
            canvas.yview_scroll(int(delta), "units")
        
        # 위젯에 스크롤 이벤트 바인딩하는 함수
        def bind_scroll_to_widget(widget):
            widget.bind("<MouseWheel>", on_mousewheel)
            widget.bind("<Button-4>", on_mousewheel)  # Linux scroll up
            widget.bind("<Button-5>", on_mousewheel)  # Linux scroll down
            for child in widget.winfo_children():
                bind_scroll_to_widget(child)
        
        # 캔버스와 스크롤 프레임에 초기 바인딩
        bind_scroll_to_widget(canvas)
        bind_scroll_to_widget(scrollable_frame)
        
        # 📊 차트들 표시 (전체 보기에서는 모든 차트 표시)
        chart_data = analysis.get("chart_data", {})
        charts = []
        
        # 새 형식: {"charts": [...]}
        if chart_data and isinstance(chart_data, dict) and "charts" in chart_data:
            charts = chart_data.get("charts", [])
        # 기존 형식: 단일 차트 객체
        elif chart_data and chart_data.get("type") and chart_data.get("type") != "empty":
            charts = [chart_data]
        
        if charts:
            charts_section = tk.Label(
                scrollable_frame,
                text="📊 시각화",
                font=(self.default_font, 12, 'bold'),
                bg=COLORS["surface_alt"],
                fg=COLORS["text_primary"],
                anchor='w'
            )
            charts_section.pack(fill='x', pady=(10, 10))
            
            for single_chart in charts:
                if single_chart and single_chart.get("type") != "empty":
                    self._draw_full_analysis_chart(scrollable_frame, single_chart)
        
        # 분석 내용 표시
        content = analysis.get("content", "분석 내용이 없습니다.")
        
        # 마크다운을 간단히 파싱하여 표시
        lines = content.split('\n')
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # 헤딩 처리
            if line.startswith('### '):
                label = tk.Label(
                    scrollable_frame,
                    text=line[4:],
                    font=(self.default_font, 11, 'bold'),
                    bg=COLORS["surface_alt"],
                    fg=COLORS["text_primary"],
                    anchor='w'
                )
                label.pack(fill='x', pady=(15, 5))
            elif line.startswith('## '):
                label = tk.Label(
                    scrollable_frame,
                    text=line[3:],
                    font=(self.default_font, 12, 'bold'),
                    bg=COLORS["surface_alt"],
                    fg=COLORS["text_primary"],
                    anchor='w'
                )
                label.pack(fill='x', pady=(15, 5))
            elif line.startswith('# '):
                label = tk.Label(
                    scrollable_frame,
                    text=line[2:],
                    font=(self.default_font, 14, 'bold'),
                    bg=COLORS["surface_alt"],
                    fg=COLORS["text_primary"],
                    anchor='w'
                )
                label.pack(fill='x', pady=(15, 5))
            elif line.startswith('- ') or line.startswith('• '):
                text = line[2:].replace('**', '').replace('*', '')
                label = tk.Label(
                    scrollable_frame,
                    text=f"  • {text}",
                    font=self.body_font,
                    bg=COLORS["surface_alt"],
                    fg=COLORS["text_secondary"],
                    anchor='w',
                    wraplength=620,
                    justify='left'
                )
                label.pack(fill='x', pady=2)
            else:
                text = line.replace('**', '').replace('*', '')
                label = tk.Label(
                    scrollable_frame,
                    text=text,
                    font=self.body_font,
                    bg=COLORS["surface_alt"],
                    fg=COLORS["text_secondary"],
                    anchor='w',
                    wraplength=620,
                    justify='left'
                )
                label.pack(fill='x', pady=2)
        
        # 모든 자식 위젯에 스크롤 바인딩 적용 (콘텐츠 추가 후)
        bind_scroll_to_widget(scrollable_frame)
    
    def _draw_full_analysis_chart(self, parent_frame: tk.Frame, chart_data: Dict[str, Any]):
        """전체 분석 보기 창에서 차트를 그립니다."""
        chart_type = chart_data.get("type", "")
        chart_title = chart_data.get("title", "")
        
        # 차트 컨테이너
        chart_container = tk.Frame(parent_frame, bg=COLORS["surface"], padx=15, pady=10)
        chart_container.pack(fill='x', pady=(0, 10))
        
        # 차트 제목
        if chart_title:
            title_label = tk.Label(
                chart_container,
                text=chart_title,
                font=(self.default_font, 10, 'bold'),
                bg=COLORS["surface"],
                fg=COLORS["text_primary"]
            )
            title_label.pack(anchor='w', pady=(0, 8))
        
        # 캔버스 생성
        canvas_width = 600
        canvas_height = 220
        chart_canvas = tk.Canvas(
            chart_container,
            width=canvas_width,
            height=canvas_height,
            bg=COLORS["surface"],
            highlightthickness=1,
            highlightbackground=COLORS["border_light"]
        )
        chart_canvas.pack(fill='x')
        
        # Plotly JSON에서 데이터 추출하여 차트 그리기
        try:
            import json
            plotly_json = chart_data.get("plotly_json", "")
            if plotly_json:
                plotly_data = json.loads(plotly_json) if isinstance(plotly_json, str) else plotly_json
                data_traces = plotly_data.get("data", [])
                
                if data_traces:
                    trace = data_traces[0]
                    
                    # 막대 그래프 (수평)
                    if chart_type == "bar" and trace.get("orientation") == "h":
                        self._draw_horizontal_bar_chart(chart_canvas, trace, canvas_width, canvas_height)
                    # 막대 그래프 (수직)
                    elif chart_type in ("bar", "grouped_bar"):
                        self._draw_vertical_bar_chart(chart_canvas, data_traces, canvas_width, canvas_height)
                    # 파이 차트
                    elif chart_type == "pie":
                        self._draw_pie_chart(chart_canvas, trace, canvas_width, canvas_height)
                    else:
                        # 기본: 수직 막대
                        self._draw_vertical_bar_chart(chart_canvas, data_traces, canvas_width, canvas_height)
        except Exception as e:
            print(f"[Dashboard] 전체 분석 차트 그리기 오류: {e}")
            chart_canvas.create_text(
                canvas_width // 2, canvas_height // 2,
                text="차트를 표시할 수 없습니다",
                fill=COLORS["text_muted"],
                font=self.body_font
            )
    
    def _update_notes_ui(self):
        """노트 목록 UI 업데이트 (페이지네이션 포함)"""
        # 기존 위젯 제거
        for widget in self.notes_list_frame.winfo_children():
            widget.destroy()
        
        if not self.notes:
            empty_label = tk.Label(
                self.notes_list_frame,
                text="저장된 노트가 없습니다.",
                font=self.body_font,
                bg=COLORS["dashboard_card"],
                fg=COLORS["text_muted"]
            )
            empty_label.pack(pady=10)
            self._bind_scroll_events(empty_label)
            return
        
        # 페이지네이션 계산
        total_notes = len(self.notes)
        total_pages = (total_notes + self.notes_per_page - 1) // self.notes_per_page
        
        # 현재 페이지가 범위를 벗어나면 조정
        if self.notes_page >= total_pages:
            self.notes_page = max(0, total_pages - 1)
        
        start_idx = self.notes_page * self.notes_per_page
        end_idx = min(start_idx + self.notes_per_page, total_notes)
        
        # 현재 페이지의 노트만 표시
        for note in self.notes[start_idx:end_idx]:
            self._create_note_item(note)
        
        # 페이지네이션 UI (노트가 3개 초과일 때만 표시)
        if total_notes > self.notes_per_page:
            self._create_notes_pagination(total_pages)
    
    def _create_notes_pagination(self, total_pages: int):
        """노트 페이지네이션 UI 생성"""
        pagination_frame = tk.Frame(self.notes_list_frame, bg=COLORS["dashboard_card"])
        pagination_frame.pack(fill='x', pady=(10, 0))
        self._bind_scroll_events(pagination_frame)
        
        # 중앙 정렬을 위한 컨테이너
        center_frame = tk.Frame(pagination_frame, bg=COLORS["dashboard_card"])
        center_frame.pack(anchor='center')
        self._bind_scroll_events(center_frame)
        
        # 이전 버튼
        prev_state = 'normal' if self.notes_page > 0 else 'disabled'
        prev_btn = tk.Button(
            center_frame,
            text="◀ 이전",
            font=self.small_font,
            command=self._prev_notes_page,
            relief='flat',
            bg=COLORS["surface_alt"],
            fg=COLORS["text_primary"],
            activebackground=COLORS["primary"],
            activeforeground=COLORS["text_inverse"],
            cursor='hand2',
            state=prev_state
        )
        prev_btn.pack(side='left', padx=5)
        self._bind_scroll_events(prev_btn)
        
        # 페이지 표시
        page_label = tk.Label(
            center_frame,
            text=f"{self.notes_page + 1} / {total_pages}",
            font=self.body_font,
            bg=COLORS["dashboard_card"],
            fg=COLORS["text_secondary"]
        )
        page_label.pack(side='left', padx=15)
        self._bind_scroll_events(page_label)
        
        # 다음 버튼
        next_state = 'normal' if self.notes_page < total_pages - 1 else 'disabled'
        next_btn = tk.Button(
            center_frame,
            text="다음 ▶",
            font=self.small_font,
            command=self._next_notes_page,
            relief='flat',
            bg=COLORS["surface_alt"],
            fg=COLORS["text_primary"],
            activebackground=COLORS["primary"],
            activeforeground=COLORS["text_inverse"],
            cursor='hand2',
            state=next_state
        )
        next_btn.pack(side='left', padx=5)
        self._bind_scroll_events(next_btn)
    
    def _prev_notes_page(self):
        """이전 노트 페이지로 이동"""
        if self.notes_page > 0:
            self.notes_page -= 1
            self._update_notes_ui()
    
    def _next_notes_page(self):
        """다음 노트 페이지로 이동"""
        total_pages = (len(self.notes) + self.notes_per_page - 1) // self.notes_per_page
        if self.notes_page < total_pages - 1:
            self.notes_page += 1
            self._update_notes_ui()
    
    def _create_note_item(self, note: Dict[str, Any]):
        """노트 아이템 생성"""
        note_id = note.get("id")
        title = note.get("title", "") or "제목 없음"
        content = note.get("content", "")
        pinned = note.get("pinned", False)
        updated = note.get("updated_at", "")
        
        item_frame = tk.Frame(
            self.notes_list_frame,
            bg=COLORS["note_bg"] if pinned else COLORS["surface_alt"],
            highlightbackground=COLORS["note_border"] if pinned else COLORS["border"],
            highlightthickness=1
        )
        item_frame.pack(fill='x', pady=3)
        self._bind_scroll_events(item_frame)
        
        # 내용 영역
        content_frame = tk.Frame(item_frame, bg=item_frame.cget("bg"))
        content_frame.pack(fill='x', padx=10, pady=8)
        self._bind_scroll_events(content_frame)
        
        # 제목 + 핀 아이콘
        title_text = f"📌 {title}" if pinned else title
        title_label = tk.Label(
            content_frame,
            text=title_text,
            font=(self.default_font, 11, 'bold'),
            bg=item_frame.cget("bg"),
            fg=COLORS["text_primary"],
            anchor='w'
        )
        title_label.pack(anchor='w')
        self._bind_scroll_events(title_label)
        
        # 내용 미리보기
        preview = content[:100] + "..." if len(content) > 100 else content
        preview_label = tk.Label(
            content_frame,
            text=preview,
            font=self.small_font,
            bg=item_frame.cget("bg"),
            fg=COLORS["text_secondary"],
            anchor='w',
            wraplength=500,
            justify='left'
        )
        preview_label.pack(anchor='w', pady=(3, 0))
        self._bind_scroll_events(preview_label)
        
        # 버튼 영역
        btn_frame = tk.Frame(content_frame, bg=item_frame.cget("bg"))
        btn_frame.pack(anchor='e', pady=(5, 0))
        self._bind_scroll_events(btn_frame)
        
        # 편집 버튼
        edit_btn = tk.Button(
            btn_frame,
            text="✏️",
            font=self.small_font,
            command=lambda: self._edit_note(note),
            relief='flat',
            bg=item_frame.cget("bg"),
            fg=COLORS["text_secondary"],
            cursor='hand2'
        )
        edit_btn.pack(side='left', padx=2)
        self._bind_scroll_events(edit_btn)
        
        # 삭제 버튼
        delete_btn = tk.Button(
            btn_frame,
            text="🗑️",
            font=self.small_font,
            command=lambda: self._delete_note(note_id),
            relief='flat',
            bg=item_frame.cget("bg"),
            fg=COLORS["danger_text"],
            cursor='hand2'
        )
        delete_btn.pack(side='left', padx=2)
        self._bind_scroll_events(delete_btn)
    
    def _save_note(self):
        """노트 저장"""
        title = self.note_title_entry.get().strip()
        content = self.note_content_text.get("1.0", tk.END).strip()
        
        if not content:
            messagebox.showwarning("경고", "노트 내용을 입력해주세요.")
            return
        
        def save():
            try:
                headers = {"Authorization": f"Bearer {self.jwt_token}"}
                
                if self.current_note_id:
                    # 업데이트
                    response = requests.put(
                        f"{self.API_BASE_URL}/dashboard/notes/{self.current_note_id}",
                        headers=headers,
                        json={"title": title, "content": content},
                        timeout=10
                    )
                else:
                    # 새로 생성
                    response = requests.post(
                        f"{self.API_BASE_URL}/dashboard/notes",
                        headers=headers,
                        json={"title": title, "content": content},
                        timeout=10
                    )
                
                if response.status_code == 200:
                    self.window.after(0, self._clear_note_form)
                    self.window.after(0, self._load_dashboard_data)
                else:
                    self.window.after(0, lambda: messagebox.showerror("오류", "노트 저장에 실패했습니다."))
                    
            except Exception as e:
                print(f"[Dashboard] 노트 저장 오류: {e}")
                self.window.after(0, lambda: messagebox.showerror("오류", "노트 저장에 실패했습니다."))
        
        threading.Thread(target=save, daemon=True).start()
    
    def _edit_note(self, note: Dict[str, Any]):
        """노트 편집 모드"""
        self.current_note_id = note.get("id")
        self.note_title_entry.delete(0, tk.END)
        self.note_title_entry.insert(0, note.get("title", ""))
        self.note_content_text.delete("1.0", tk.END)
        self.note_content_text.insert("1.0", note.get("content", ""))
        
        # 스크롤을 노트 입력 영역으로
        self.canvas.yview_moveto(0.5)
    
    def _delete_note(self, note_id: int):
        """노트 삭제"""
        if not messagebox.askyesno("확인", "이 노트를 삭제하시겠습니까?"):
            return
        
        def delete():
            try:
                headers = {"Authorization": f"Bearer {self.jwt_token}"}
                response = requests.delete(
                    f"{self.API_BASE_URL}/dashboard/notes/{note_id}",
                    headers=headers,
                    timeout=10
                )
                
                if response.status_code == 200:
                    self.window.after(0, self._load_dashboard_data)
                else:
                    self.window.after(0, lambda: messagebox.showerror("오류", "노트 삭제에 실패했습니다."))
                    
            except Exception as e:
                print(f"[Dashboard] 노트 삭제 오류: {e}")
        
        threading.Thread(target=delete, daemon=True).start()
    
    def _clear_note_form(self):
        """노트 입력 폼 초기화"""
        self.current_note_id = None
        self.note_title_entry.delete(0, tk.END)
        self.note_content_text.delete("1.0", tk.END)
    
    def _show_error(self, message: str):
        """에러 메시지 표시"""
        messagebox.showerror("오류", message)
    
    def _on_mousewheel(self, event):
        """마우스 휠 스크롤 처리 (macOS/Windows/Linux 호환)"""
        import platform
        system = platform.system()
        
        if system == "Darwin":  # macOS
            delta = -1 * event.delta
        elif system == "Windows":
            delta = -1 * (event.delta // 120)
        else:  # Linux
            if event.num == 4:
                delta = -1
            elif event.num == 5:
                delta = 1
            else:
                delta = -1 * (event.delta // 120)
        
        self.canvas.yview_scroll(int(delta), "units")
    
    def _bind_scroll_events(self, widget):
        """위젯에 스크롤 이벤트 바인딩"""
        widget.bind("<MouseWheel>", self._on_mousewheel)
        widget.bind("<Button-4>", self._on_mousewheel)  # Linux scroll up
        widget.bind("<Button-5>", self._on_mousewheel)  # Linux scroll down
    
    def _bind_scroll_to_children(self, widget):
        """위젯과 모든 자식 위젯에 스크롤 이벤트 바인딩"""
        self._bind_scroll_events(widget)
        for child in widget.winfo_children():
            self._bind_scroll_to_children(child)
    
    def _bind_scrolled_text_to_canvas(self, scrolled_text_widget):
        """ScrolledText 위젯의 스크롤을 부모 캔버스로 전파"""
        # ScrolledText 내부의 Text 위젯에 바인딩
        # 스크롤 이벤트를 부모 캔버스로 전파하되, 기본 동작은 막음
        def on_scroll(event):
            self._on_mousewheel(event)
            return "break"  # 기본 Text 위젯 스크롤 동작 방지
        
        scrolled_text_widget.bind("<MouseWheel>", on_scroll)
        scrolled_text_widget.bind("<Button-4>", on_scroll)
        scrolled_text_widget.bind("<Button-5>", on_scroll)
        
        # ScrolledText의 프레임과 스크롤바에도 바인딩
        for child in scrolled_text_widget.winfo_children():
            child.bind("<MouseWheel>", on_scroll)
            child.bind("<Button-4>", on_scroll)
            child.bind("<Button-5>", on_scroll)
    
    def _on_close(self):
        """창 닫기 처리"""
        self.window.destroy()
        self.window = None
    
    def show(self):
        """창 표시"""
        if self.window and self.window.winfo_exists():
            self.window.lift()
            self.window.focus_force()
        else:
            self._create_window()
    
    def is_open(self) -> bool:
        """창이 열려있는지 확인"""
        return self.window is not None and self.window.winfo_exists()

