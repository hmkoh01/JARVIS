#!/usr/bin/env python3
"""
User Survey Dialog
사용자 초기 정보 수집을 위한 설문지 창
"""

import tkinter as tk
from tkinter import ttk, messagebox
import json
import sys
import os
from datetime import datetime

# Add the backend directory to Python path for database access
backend_path = os.path.join(os.path.dirname(__file__), '..', 'backend')
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)

try:
    from backend.database.sqlite import SQLite
except ImportError:
    print("Warning: Could not import SQLite. Survey responses will not be saved.")
    SQLite = None

class SurveyDialog:
    def __init__(self, user_id=1):
        self.user_id = user_id
        self.root = tk.Tk()
        self.root.title("JARVIS 초기 설정")
        self.root.configure(bg='#f8fafc')
        self.root.resizable(False, False)
        
        # 창을 화면 중앙에 배치 (geometry 설정 전에)
        self.center_window()
        
        # 한글 폰트 설정
        self.setup_korean_fonts()
        
        # 설문지 응답 데이터
        self.survey_data = {
            'job_field': '',
            'job_field_other': '',
            'interests': [],
            'help_preferences': [],
            'custom_keywords': ''
        }
        
        # UI 생성
        self.create_ui()
        
        # 창 닫기 이벤트 바인딩
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
    
    def center_window(self):
        """창을 화면 중앙에 배치"""
        # 창 크기 설정
        window_width = 600
        window_height = 800
        
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
        import platform
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
        self.button_font = (self.default_font, 12, 'bold')
    
    def create_ui(self):
        """UI를 생성합니다."""
        # 메인 컨테이너
        main_container = tk.Frame(self.root, bg='#f8fafc')
        main_container.pack(fill='both', expand=True, padx=20, pady=20)
        
        # 스크롤 가능한 프레임
        self.canvas = tk.Canvas(main_container, bg='#f8fafc', highlightthickness=0)
        scrollbar = ttk.Scrollbar(main_container, orient="vertical", command=self.canvas.yview)
        self.scrollable_frame = tk.Frame(self.canvas, bg='#f8fafc')
        
        # 스크롤 영역 설정
        self.scrollable_frame.bind(
            "<Configure>",
            self._on_frame_configure
        )
        
        self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=scrollbar.set)
        
        # 마우스 휠 스크롤 바인딩
        self._bind_mousewheel()
        
        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        
        # 헤더
        header_frame = tk.Frame(self.scrollable_frame, bg='white', relief='flat', bd=1)
        header_frame.pack(fill='x', pady=(0, 20))
        
        # 제목
        title_frame = tk.Frame(header_frame, bg='white')
        title_frame.pack(fill='x', padx=30, pady=30)
        
        icon_label = tk.Label(title_frame, text="🤖", font=('Arial', 32), bg='white', fg='#4f46e5')
        icon_label.pack(side='left', padx=(0, 15))
        
        text_frame = tk.Frame(title_frame, bg='white')
        text_frame.pack(side='left', fill='x', expand=True)
        
        title_label = tk.Label(text_frame, text="JARVIS 초기 설정", font=self.title_font, bg='white', fg='#1f2937')
        title_label.pack(anchor='w')
        
        subtitle_label = tk.Label(text_frame, text="안녕하세요! JARVIS가 당신에게 꼭 맞는 비서가 될 수 있도록 몇 가지만 알려주시겠어요?", 
                                font=self.subtitle_font, bg='white', fg='#6b7280', wraplength=500, justify='left')
        subtitle_label.pack(anchor='w', pady=(5, 0))
        
        # 설문지 내용
        self.create_survey_content(self.scrollable_frame)
        
        # 버튼 영역
        button_frame = tk.Frame(self.scrollable_frame, bg='#f8fafc')
        button_frame.pack(fill='x', pady=(30, 20), padx=20)
        
        # 버튼들을 중앙 정렬하기 위한 컨테이너
        button_container = tk.Frame(button_frame, bg='#f8fafc')
        button_container.pack(expand=True)
        
        # 건너뛰기 버튼
        skip_button = tk.Button(button_container, text="건너뛰기", font=self.button_font, 
                       bg='#e2e8f0', fg='black', relief='flat', bd=0, cursor='hand2',
                       command=self.skip_survey, width=12, pady=10)
        skip_button.pack(side='left', padx=(0, 10))

        # 제출 버튼
        submit_button = tk.Button(button_container, text="제출하기", font=self.button_font,
                                 bg='#4f46e5', fg='white', relief='flat', bd=0, cursor='hand2',
                                 command=self.submit_survey, width=12, pady=10)
        submit_button.pack(side='left', padx=(10, 0))
    
    def create_survey_content(self, parent):
            """설문지 내용을 생성합니다."""
            # 질문 1: 직업/활동 분야
            q1_frame = tk.Frame(parent, bg='white', relief='flat', bd=1)
            q1_frame.pack(fill='x', pady=(0, 20))
            
            q1_content = tk.Frame(q1_frame, bg='white')
            q1_content.pack(fill='x', padx=30, pady=20)
            
            q1_label = tk.Label(q1_content, text="1. 현재 당신의 직업 또는 주된 활동 분야는 무엇인가요? (단일 선택)", 
                                font=self.message_font, bg='white', fg='#1f2937', wraplength=500, justify='left')
            q1_label.pack(anchor='w', pady=(0, 15))
            
            # 직업 선택 라디오 버튼들
            self.job_var = tk.StringVar()
            job_options = [
                ("학생", "student"),
                ("개발자 / 엔지니어", "developer"),
                ("디자이너", "designer"),
                ("기획자 / 마케터", "planner"),
                ("연구원 / 교육자", "researcher"),
                ("기타 (직접 입력)", "other")
            ]
            
            for i, (text, value) in enumerate(job_options):
                radio = tk.Radiobutton(q1_content, text=text, variable=self.job_var, value=value,
                                    font=self.message_font, bg='white', fg='#374151',
                                    selectcolor='#ffffff', # 라디오 버튼은 그대로 두거나 흰색으로 변경
                                    activebackground='white',
                                    activeforeground='#374151', indicatoron=True, 
                                    command=self.on_job_selection_change)
                radio.pack(anchor='w', pady=2)
            
            # 기타 직접 입력 필드
            self.job_other_entry = tk.Entry(q1_content, font=self.message_font, relief='solid', 
                                            borderwidth=1, bg='#f9fafb')
            # Initially hidden using pack_forget()
            self.job_other_entry.pack(fill='x', pady=(10, 0))
            self.job_other_entry.config(state='disabled')
            self.job_other_entry.pack_forget()
            
            # 질문 2: 관심 주제
            q2_frame = tk.Frame(parent, bg='white', relief='flat', bd=1)
            q2_frame.pack(fill='x', pady=(0, 20))
            
            q2_content = tk.Frame(q2_frame, bg='white')
            q2_content.pack(fill='x', padx=30, pady=20)
            
            q2_label = tk.Label(q2_content, text="2. 요즘 가장 흥미를 느끼는 주제는 무엇인가요? (최대 3개 선택)", 
                                font=self.message_font, bg='white', fg='#1f2937', wraplength=500, justify='left')
            q2_label.pack(anchor='w', pady=(0, 15))
            
            # 관심 주제 체크박스들
            self.interest_vars = {}
            interest_options = [
                ("IT / 최신 기술", "tech"),
                ("경제 / 금융 / 투자", "finance"),
                ("인공지능 / 데이터 과학", "ai"),
                ("디자인 / 예술", "design"),
                ("마케팅 / 비즈니스", "marketing"),
                ("생산성 / 자기계발", "productivity"),
                ("건강 / 운동", "health"),
                ("여행 / 문화", "travel")
            ]
            
            for text, value in interest_options:
                var = tk.BooleanVar()
                self.interest_vars[value] = var
                checkbox = tk.Checkbutton(q2_content, text=text, variable=var,
                                        font=self.message_font, bg='white', fg='#374151',
                                        activebackground='white',
                                        activeforeground='#374151', indicatoron=True) # selectcolor 제거
                checkbox.pack(anchor='w', pady=2)
            
            # 질문 3: 도움 받고 싶은 영역
            q3_frame = tk.Frame(parent, bg='white', relief='flat', bd=1)
            q3_frame.pack(fill='x', pady=(0, 20))
            
            q3_content = tk.Frame(q3_frame, bg='white')
            q3_content.pack(fill='x', padx=30, pady=20)
            
            q3_label = tk.Label(q3_content, text="3. JARVIS를 통해 주로 어떤 도움을 받고 싶으신가요? (최대 2개 선택)", 
                                font=self.message_font, bg='white', fg='#1f2937', wraplength=500, justify='left')
            q3_label.pack(anchor='w', pady=(0, 15))
            
            # 도움 영역 체크박스들
            self.help_vars = {}
            help_options = [
                ("업무 관련 정보 검색 및 요약", "work_search"),
                ("새로운 아이디어나 영감 얻기", "inspiration"),
                ("글쓰기 (이메일, 보고서 등) 보조", "writing"),
                ("개인적인 학습 및 지식 확장", "learning")
            ]
            
            for text, value in help_options:
                var = tk.BooleanVar()
                self.help_vars[value] = var
                checkbox = tk.Checkbutton(q3_content, text=text, variable=var,
                                        font=self.message_font, bg='white', fg='#374151',
                                        activebackground='white',
                                        activeforeground='#374151', indicatoron=True) # selectcolor 제거
                checkbox.pack(anchor='w', pady=2)
            
            # 질문 4: 사용자 정의 키워드
            q4_frame = tk.Frame(parent, bg='white', relief='flat', bd=1)
            q4_frame.pack(fill='x', pady=(0, 20))
            
            q4_content = tk.Frame(q4_frame, bg='white')
            q4_content.pack(fill='x', padx=30, pady=20)
            
            q4_label = tk.Label(q4_content, text="4. 그 외에 특별히 자주 찾아보거나 배우고 싶은 키워드가 있다면 자유롭게 알려주세요. (선택 사항)", 
                                font=self.message_font, bg='white', fg='#1f2937', wraplength=500, justify='left')
            q4_label.pack(anchor='w', pady=(0, 10))
            
            example_label = tk.Label(q4_content, text="(예: 딥러닝, NFT, 행동경제학, 클린 아키텍처)", 
                                    font=self.message_font, bg='white', fg='#6b7280', wraplength=500, justify='left')
            example_label.pack(anchor='w', pady=(0, 10))
            
            self.custom_keywords_entry = tk.Entry(q4_content, font=self.message_font, relief='solid', 
                                                borderwidth=1, bg='#f9fafb')
            self.custom_keywords_entry.pack(fill='x')
    
    def _on_frame_configure(self, event):
        """스크롤 영역 업데이트"""
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
    
    def _bind_mousewheel(self):
        """마우스 휠 스크롤 바인딩"""
        import platform
        system = platform.system()
        
        def _on_mousewheel(event):
            if system == "Darwin":  # macOS
                # macOS는 delta가 매우 작은 값
                self.canvas.yview_scroll(int(-1 * event.delta), "units")
            else:
                # Windows는 delta가 120 단위
                self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        
        def _bind_to_mousewheel(event):
            self.canvas.bind_all("<MouseWheel>", _on_mousewheel)
        
        def _unbind_from_mousewheel(event):
            self.canvas.unbind_all("<MouseWheel>")
        
        # 캔버스에 포커스가 있을 때만 스크롤
        self.canvas.bind('<Enter>', _bind_to_mousewheel)
        self.canvas.bind('<Leave>', _unbind_from_mousewheel)
        
        # Linux 마우스 휠 바인딩
        self.canvas.bind("<Button-4>", lambda e: self.canvas.yview_scroll(-1, "units"))
        self.canvas.bind("<Button-5>", lambda e: self.canvas.yview_scroll(1, "units"))
    
    def on_job_selection_change(self):
        """직업 선택이 변경될 때 호출"""
        if self.job_var.get() == "other":
            self.job_other_entry.pack(fill='x', pady=(10, 0))
            self.job_other_entry.config(state='normal')
        else:
            self.job_other_entry.pack_forget()
            self.job_other_entry.config(state='disabled')
            self.job_other_entry.delete(0, tk.END)
    
    def collect_survey_data(self):
        """설문지 데이터를 수집합니다."""
        # 직업 분야
        job_field = self.job_var.get()
        job_field_other = self.job_other_entry.get().strip() if job_field == "other" else ""
        
        # 관심 주제 (최대 3개)
        selected_interests = [key for key, var in self.interest_vars.items() if var.get()]
        if len(selected_interests) > 3:
            messagebox.showwarning("선택 제한", "관심 주제는 최대 3개까지만 선택할 수 있습니다.")
            return None
        
        # 도움 받고 싶은 영역 (최대 2개)
        selected_help = [key for key, var in self.help_vars.items() if var.get()]
        if len(selected_help) > 2:
            messagebox.showwarning("선택 제한", "도움 받고 싶은 영역은 최대 2개까지만 선택할 수 있습니다.")
            return None
        
        # 사용자 정의 키워드
        custom_keywords = self.custom_keywords_entry.get().strip()
        
        return {
            'job_field': job_field,
            'job_field_other': job_field_other,
            'interests': selected_interests,
            'help_preferences': selected_help,
            'custom_keywords': custom_keywords
        }
    
    def save_survey_data(self, survey_data):
        """설문지 데이터를 데이터베이스에 저장하고 Qdrant에 인덱싱합니다."""
        if SQLite is None:
            print("Warning: SQLite not available. Survey data not saved.")
            return False
        
        try:
            # 1. SQLite에 저장
            db = SQLite()
            success = db.insert_survey_response(self.user_id, survey_data)
            if success:
                print("✅ 설문지 응답이 SQLite에 저장되었습니다.")
                
                # 2. Qdrant에 프로필 인덱싱 (API를 통해 처리)
                try:
                    import requests
                    response = requests.post(
                        f"http://localhost:8000/api/v2/user-profile/{self.user_id}/update",
                        json=survey_data
                    )
                    if response.status_code == 200:
                        print("✅ 사용자 프로필이 검색 시스템(Qdrant)에 인덱싱되었습니다.")
                    else:
                        print("⚠️ 프로필 인덱싱 실패 (검색은 가능하나 개인화 기능이 제한될 수 있습니다)")
                except Exception as e:
                    print(f"⚠️ 프로필 인덱싱 오류: {e}")
                    print("   (검색은 가능하나 개인화 기능이 제한될 수 있습니다)")
                
                return True
            else:
                print("❌ 설문지 응답 저장에 실패했습니다.")
                return False
        except Exception as e:
            print(f"❌ 설문지 응답 저장 중 오류: {e}")
            return False
    
    def submit_survey(self):
        """설문지 제출"""
        survey_data = self.collect_survey_data()
        if survey_data is None:
            return
        
        # 데이터 저장 (SQLite만 먼저 저장)
        if self.save_survey_data_to_sqlite(survey_data):
            messagebox.showinfo("설문 완료", "설문이 성공적으로 제출되었습니다!\n이제 파일 수집을 시작합니다.")
            
            # Qdrant 인덱싱은 백그라운드에서 실행 (tkinter 참조를 피하기 위해 user_id만 전달)
            user_id_for_indexing = self.user_id
            self.root.destroy()  # 창을 완전히 닫음
            
            # 백그라운드 인덱싱 시작
            self._start_background_indexing(user_id_for_indexing)
        else:
            messagebox.showerror("저장 오류", "설문 데이터 저장에 실패했습니다. 다시 시도해주세요.")
    
    def save_survey_data_to_sqlite(self, survey_data):
        """설문지 데이터를 SQLite에만 저장합니다 (빠른 저장)."""
        if SQLite is None:
            print("Warning: SQLite not available. Survey data not saved.")
            return False
        
        try:
            db = SQLite()
            success = db.insert_survey_response(self.user_id, survey_data)
            if success:
                print("✅ 설문지 응답이 SQLite에 저장되었습니다.")
                return True
            else:
                print("❌ 설문지 응답 저장에 실패했습니다.")
                return False
        except Exception as e:
            print(f"❌ 설문지 응답 저장 중 오류: {e}")
            return False
    
    @staticmethod
    def _start_background_indexing(user_id):
        """사용자 프로필을 백그라운드에서 Qdrant에 인덱싱합니다."""
        import threading
        
        def background_indexing(uid):
            try:
                import requests
                from backend.database.sqlite import SQLite
                
                # SQLite에서 설문 데이터 가져오기
                db = SQLite()
                survey_data = db.get_user_survey_response(uid)
                
                if survey_data:
                    # API를 통해 프로필 업데이트
                    response = requests.post(
                        f"http://localhost:8000/api/v2/user-profile/{uid}/update",
                        json=survey_data
                    )
                    if response.status_code == 200:
                        print("✅ 사용자 프로필이 검색 시스템(Qdrant)에 인덱싱되었습니다.")
                    else:
                        print("⚠️ 프로필 인덱싱 실패 (검색은 가능하나 개인화 기능이 제한될 수 있습니다)")
                else:
                    print("⚠️ 설문 데이터를 찾을 수 없습니다.")
            except Exception as e:
                print(f"⚠️ 프로필 인덱싱 오류: {e}")
                print("   (검색은 가능하나 개인화 기능이 제한될 수 있습니다)")
        
        # 백그라운드 스레드에서 실행 (self 참조 없이 user_id만 전달)
        threading.Thread(target=background_indexing, args=(user_id,), daemon=True).start()
    
    def skip_survey(self):
        """설문 건너뛰기"""
        result = messagebox.askyesno("설문 건너뛰기", "설문을 건너뛰고 바로 파일 수집을 시작하시겠습니까?")
        if result:
            self.root.destroy()  # 창을 완전히 닫음
    
    def on_closing(self):
        """창 닫기 이벤트"""
        result = messagebox.askyesno("종료", "설문을 완료하지 않고 종료하시겠습니까?")
        if result:
            self.root.destroy()  # 창을 완전히 닫음
    
    def run(self):
        """설문지 창을 실행합니다."""
        self.root.mainloop()
        
        # Tcl_AsyncDelete 오류 방지를 위해, mainloop 종료 후 창을 확실하게 파괴합니다.
        try:
            if self.root.winfo_exists():
                self.root.destroy()
        except tk.TclError:
            pass  # 이미 파괴된 경우 무시
        
        return True

def show_survey_dialog(user_id=1):
    """설문지 다이얼로그를 표시합니다."""
    import gc
    try:
        dialog = SurveyDialog(user_id)
        result = dialog.run()
        # tkinter 객체 정리
        del dialog
        # 가비지 컬렉션 강제 수행
        gc.collect()
        return result
    except Exception as e:
        print(f"설문지 다이얼로그 오류: {e}")
        return False

if __name__ == "__main__":
    show_survey_dialog()
