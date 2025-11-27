#!/usr/bin/env python3
"""
Google 로그인 UI 및 OAuth 인증 처리
"""
import tkinter as tk
from tkinter import ttk
import threading
import http.server
import socketserver
from urllib.parse import urlparse, parse_qs
import webbrowser
import requests
import json
import time
import sys
import os

# keyring 라이브러리 임포트
try:
    import keyring
    KEYRING_AVAILABLE = True
except ImportError:
    KEYRING_AVAILABLE = False
    print("keyring 라이브러리가 설치되지 않았습니다. token.json 파일에 저장합니다.")

# API 설정
API_BASE_URL = "http://localhost:8000"

# 로컬 콜백 서버 설정
CALLBACK_PORT = 9090
CALLBACK_URI = f"http://127.0.0.1:{CALLBACK_PORT}/auth/callback"

# 전역 변수
callback_code = None
callback_event = threading.Event()
server_thread = None
local_server = None


class CallbackHandler(http.server.BaseHTTPRequestHandler):
    """OAuth 콜백을 처리하는 HTTP 핸들러"""
    
    def do_GET(self):
        """GET 요청 처리"""
        global callback_code
        
        # /auth/callback 경로만 처리
        if self.path.startswith('/auth/callback'):
            # URL에서 code 파라미터 추출
            parsed_url = urlparse(self.path)
            query_params = parse_qs(parsed_url.query)
            
            code = query_params.get('code', [None])[0]
            
            if code:
                callback_code = code
                
                # 성공 응답 페이지
                self.send_response(200)
                self.send_header('Content-type', 'text/html; charset=utf-8')
                self.end_headers()
                
                success_html = """
                <!DOCTYPE html>
                <html>
                <head>
                    <meta charset="UTF-8">
                    <title>인증 성공</title>
                    <style>
                        body { 
                            font-family: 'Segoe UI', Arial, sans-serif; 
                            display: flex; 
                            justify-content: center; 
                            align-items: center; 
                            height: 100vh; 
                            margin: 0; 
                            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                        }
                        .container {
                            text-align: center;
                            background: white;
                            padding: 40px;
                            border-radius: 10px;
                            box-shadow: 0 10px 40px rgba(0,0,0,0.2);
                        }
                        h1 { color: #4f46e5; margin-bottom: 20px; }
                        p { color: #666; font-size: 16px; }
                    </style>
                </head>
                <body>
                    <div class="container">
                        <h1>✅ 인증 성공!</h1>
                        <p>JARVIS 인증이 완료되었습니다.</p>
                        <p>이 창을 닫으셔도 됩니다.</p>
                    </div>
                </body>
                </html>
                """
                
                self.wfile.write(success_html.encode('utf-8'))
                callback_event.set()
            else:
                # 에러 응답
                self.send_response(400)
                self.send_header('Content-type', 'text/html')
                self.end_headers()
                error_html = "<html><body><h1>인증 오류</h1><p>인증 코드를 받지 못했습니다.</p></body></html>"
                self.wfile.write(error_html.encode('utf-8'))
        else:
            # 다른 경로는 404
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        """로그 메시지 출력 방지"""
        pass


def start_local_server():
    """로컬 콜백 서버를 별도 스레드에서 실행"""
    global local_server, server_thread
    
    try:
        # HTTP 서버 생성
        local_server = socketserver.TCPServer(("127.0.0.1", CALLBACK_PORT), CallbackHandler)
        local_server.allow_reuse_address = True
        
        # 서버를 별도 스레드에서 실행
        def run_server():
            try:
                local_server.serve_forever()
            except Exception as e:
                print(f"서버 실행 오류: {e}")
        
        server_thread = threading.Thread(target=run_server, daemon=True)
        server_thread.start()
        print(f"로컬 콜백 서버 시작: {CALLBACK_URI}")
        
    except Exception as e:
        print(f"로컬 서버 시작 오류: {e}")
        raise


def stop_local_server():
    """로컬 콜백 서버 종료"""
    global local_server
    if local_server:
        try:
            local_server.shutdown()
            local_server.server_close()
            print("로컬 콜백 서버 종료")
        except Exception as e:
            print(f"서버 종료 오류: {e}")


def save_token(token):
    """토큰을 안전하게 저장"""
    global KEYRING_AVAILABLE
    
    # keyring 사용 가능 시 keyring에 저장
    if KEYRING_AVAILABLE:
        try:
            keyring.set_password("jarvis", "jwt_token", token)
            print("토큰을 keyring에 저장했습니다.")
            return
        except Exception as e:
            print(f"keyring 저장 실패: {e}")
    
    # keyring 사용 불가 시 token.json 파일에 저장
    try:
        token_path = os.path.join(os.path.expanduser("~"), ".jarvis_token.json")
        with open(token_path, 'w') as f:
            json.dump({"jarvis_token": token}, f)
        print(f"토큰을 {token_path}에 저장했습니다.")
    except Exception as e:
        print(f"토큰 파일 저장 실패: {e}")


def get_stored_token():
    """저장된 토큰 조회"""
    global KEYRING_AVAILABLE
    
    # keyring에서 조회
    if KEYRING_AVAILABLE:
        try:
            token = keyring.get_password("jarvis", "jwt_token")
            if token:
                return token
        except Exception as e:
            print(f"keyring 조회 실패: {e}")
    
    # 파일에서 조회
    try:
        token_path = os.path.join(os.path.expanduser("~"), ".jarvis_token.json")
        if os.path.exists(token_path):
            with open(token_path, 'r') as f:
                data = json.load(f)
                return data.get("jarvis_token")
    except Exception as e:
        print(f"토큰 파일 조회 실패: {e}")
    
    return None


def start_google_login():
    """Google 로그인 프로세스 시작"""
    global callback_code, callback_event
    
    try:
        # 1. 로컬 콜백 서버 실행
        print("로컬 콜백 서버 시작 중...")
        start_local_server()
        time.sleep(0.5)  # 서버 시작 대기
        
        # 2. 백엔드에서 Google 로그인 URL 가져오기
        print("Google 로그인 URL 요청 중...")
        response = requests.get(f"{API_BASE_URL}/auth/google/login")
        
        if response.status_code != 200:
            raise Exception(f"로그인 URL 요청 실패: {response.text}")
        
        login_data = response.json()
        login_url = login_data.get("login_url")
        
        if not login_url:
            raise Exception("로그인 URL을 받지 못했습니다.")
        
        # 3. 브라우저 열기
        print(f"브라우저 열기: {login_url}")
        webbrowser.open(login_url)
        
        # 4. 콜백 대기 (최대 5분)
        print("Google 로그인 대기 중...")
        callback_event.clear()
        callback_code = None
        
        # 콜백을 받을 때까지 대기
        if callback_event.wait(timeout=300):  # 5분 타임아웃
            if callback_code:
                print(f"인증 코드 받음: {callback_code[:20]}...")
                
                # 5. 토큰 교환
                print("토큰 교환 중...")
                exchange_response = requests.post(
                    f"{API_BASE_URL}/auth/google/exchange-code",
                    json={"code": callback_code}
                )
                
                if exchange_response.status_code != 200:
                    raise Exception(f"토큰 교환 실패: {exchange_response.text}")
                
                auth_data = exchange_response.json()
                jarvis_token = auth_data.get("jarvis_token")
                user_info = {
                    "user_id": auth_data.get("user_id"),
                    "email": auth_data.get("email"),
                    "has_completed_setup": auth_data.get("has_completed_setup"),
                    "selected_root_folder": auth_data.get("selected_root_folder")
                }
                
                if not jarvis_token:
                    raise Exception("JWT 토큰을 받지 못했습니다.")
                
                # 6. 토큰 저장
                print("토큰 저장 중...")
                save_token(jarvis_token)
                
                print("로그인 완료!")
                
                # 사용자 상태 정보 표시
                if user_info.get("has_completed_setup", 0) == 1:
                    print("✅ 기존 사용자 - 초기 설정 완료됨")
                else:
                    print("📋 신규 사용자 - 초기 설정 필요")
                
                return user_info
            else:
                raise Exception("인증 코드를 받지 못했습니다.")
        else:
            raise Exception("로그인 타임아웃 (5분)")
    
    except Exception as e:
        print(f"Google 로그인 오류: {e}")
        raise
    
    finally:
        # 7. 로컬 서버 종료
        stop_local_server()


class LoginWindow:
    """로그인 윈도우"""
    
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("JARVIS 로그인")
        self.root.geometry("600x400")
        self.root.configure(bg='white')
        
        # 화면 중앙에 배치
        self.center_window()
        
        # 한글 폰트 설정
        self.setup_fonts()
        
        # UI 생성
        self.create_widgets()
        
        # user_info 저장
        self.user_info = None
    
    def center_window(self):
        """윈도우를 화면 중앙에 배치"""
        self.root.update_idletasks()
        width = self.root.winfo_width()
        height = self.root.winfo_height()
        x = (self.root.winfo_screenwidth() // 2) - (width // 2)
        y = (self.root.winfo_screenheight() // 2) - (height // 2)
        self.root.geometry(f'{width}x{height}+{x}+{y}')
    
    def setup_fonts(self):
        """폰트 설정"""
        import platform
        system = platform.system()
        
        if system == "Darwin":  # macOS
            korean_fonts = [
                'Apple SD Gothic Neo',  # macOS 기본 한글 폰트
                'AppleGothic',          # macOS 기본 고딕
                'Nanum Gothic',         # 나눔고딕 (설치된 경우)
                'Helvetica Neue',       # macOS 기본 영문 폰트
                'Arial Unicode MS',     # Unicode 폰트
                'Arial'
            ]
        else:  # Windows/Linux
            korean_fonts = [
                'Malgun Gothic',        # 맑은 고딕 (Windows 기본)
                'Nanum Gothic',         # 나눔고딕
                'Arial Unicode MS',     # Unicode 폰트
                'Arial'
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
        
        self.title_font = (self.default_font, 24, 'bold')
        self.subtitle_font = (self.default_font, 14)
        self.button_font = (self.default_font, 14, 'bold')
        self.status_font = (self.default_font, 12)
    
    def create_widgets(self):
        """UI 위젯 생성"""
        # 헤더
        header_frame = tk.Frame(self.root, bg='#4f46e5', height=80)
        header_frame.pack(fill='x')
        header_frame.pack_propagate(False)
        
        title_label = tk.Label(
            header_frame,
            text="JARVIS",
            font=self.title_font,
            bg='#4f46e5',
            fg='white'
        )
        title_label.pack(pady=20)
        
        # 본문
        content_frame = tk.Frame(self.root, bg='white')
        content_frame.pack(fill='both', expand=True, padx=40, pady=40)
        
        # 안내 텍스트
        info_label = tk.Label(
            content_frame,
            text="Google 계정으로 로그인하여\nJARVIS를 사용하세요",
            font=self.subtitle_font,
            bg='white',
            fg='#666',
            justify='center'
        )
        info_label.pack(pady=(20, 40))
        
        # Google 로그인 버튼
        self.login_button = tk.Button(
            content_frame,
            text="Google로 로그인",
            font=self.button_font,
            bg='#4285F4',
            fg='white',
            activebackground='#357AE8',
            activeforeground='white',
            relief='flat',
            cursor='hand2',
            pady=10,
            command=self.handle_login
        )
        self.login_button.pack(pady=20, fill='x')
        
        # 상태 텍스트
        self.status_label = tk.Label(
            content_frame,
            text="",
            font=self.status_font,
            bg='white',
            fg='#666'
        )
        self.status_label.pack(pady=10)
    
    def update_status(self, message):
        """상태 메시지 업데이트"""
        self.status_label.config(text=message)
        self.root.update()
    
    def handle_login(self):
        """로그인 버튼 클릭 핸들러"""
        # 버튼 비활성화
        self.login_button.config(state='disabled', text="로그인 중...")
        self.update_status("Google 로그인 페이지를 여는 중...")
        
        # 별도 스레드에서 로그인 프로세스 실행
        def login_thread():
            try:
                self.update_status("브라우저를 열어주세요...")
                user_info = start_google_login()
                
                # 로그인 성공
                self.user_info = user_info
                self.root.after(0, lambda: self.root.destroy())
                
            except Exception as e:
                error_msg = f"로그인 실패: {str(e)}"
                print(error_msg)
                self.root.after(0, lambda: self.update_status(error_msg))
                self.root.after(0, lambda: self.login_button.config(state='normal', text="Google로 로그인"))
        
        threading.Thread(target=login_thread, daemon=True).start()
    
    def run(self):
        """로그인 윈도우 실행"""
        self.root.mainloop()
        return self.user_info


def main():
    """메인 함수 - 항상 로그인 창 표시"""
    # 항상 로그인 윈도우 표시 (저장된 토큰 무시)
    print("🔐 Google 계정으로 로그인하세요...")
    login_window = LoginWindow()
    user_info = login_window.run()
    
    return user_info


if __name__ == "__main__":
    try:
        user_info = main()
        if user_info:
            print("\n로그인 성공!")
            print(f"사용자 ID: {user_info['user_id']}")
            print(f"이메일: {user_info['email']}")
            print(f"설정 완료: {user_info['has_completed_setup']}")
            sys.exit(0)
        else:
            print("\n로그인이 취소되었습니다.")
            sys.exit(1)
    except KeyboardInterrupt:
        print("\n\n로그인이 취소되었습니다.")
        sys.exit(1)
    except Exception as e:
        print(f"\n오류 발생: {e}")
        sys.exit(1)
