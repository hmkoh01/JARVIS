#!/usr/bin/env python3
"""
JARVIS Multi-Agent System Startup Script
멀티모달 RAG 시스템을 쉽게 시작할 수 있는 스크립트
"""

import os
import sys
import subprocess
import time
import threading
from pathlib import Path
from tqdm import tqdm
import logging
import requests
import json

logger = logging.getLogger(__name__)

# 전역 변수: 선택된 폴더 목록
selected_folders_global = None

def check_docker():
    """Docker 설치 및 실행 상태 확인"""
    print("🐳 Docker 상태 확인 중...")
    
    try:
        # Docker 설치 확인
        result = subprocess.run(['docker', '--version'], 
                              capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            print("❌ Docker가 설치되지 않았습니다.")
            print("Docker를 설치하고 다시 시도하세요: https://docs.docker.com/get-docker/")
            return False
        
        print(f"✅ Docker 설치됨: {result.stdout.strip()}")
        
        # Docker 데몬 실행 확인
        result = subprocess.run(['docker', 'info'], 
                              capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            print("❌ Docker 데몬이 실행되지 않았습니다.")
            print("Docker Desktop을 시작하고 다시 시도하세요.")
            return False
        
        print("✅ Docker 데몬이 실행 중입니다.")
        return True
        
    except subprocess.TimeoutExpired:
        print("❌ Docker 응답 시간 초과")
        return False
    except FileNotFoundError:
        print("❌ Docker 명령어를 찾을 수 없습니다.")
        print("Docker가 설치되지 않았거나 PATH에 등록되지 않았습니다.")
        print("\n💡 해결 방법:")
        print("1. Docker를 설치하세요: https://docs.docker.com/get-docker/")
        print("2. 설치 후 시스템을 재시작하세요")
        print("3. 또는 다음 명령어로 설치하세요:")
        print("   - Windows (Chocolatey): choco install docker-desktop")
        print("   - macOS (Homebrew): brew install --cask docker")
        print("   - Ubuntu: sudo apt-get install docker.io")
        print("   - CentOS/RHEL: sudo yum install docker")
        return False
    except Exception as e:
        print(f"❌ Docker 확인 중 오류: {e}")
        return False

def check_qdrant_server():
    """Qdrant 서버 실행 상태 확인"""
    print("🔍 Qdrant 서버 상태 확인 중...")
    
    try:
        import requests
        # 루트 경로로 확인 (Qdrant의 실제 응답 경로)
        response = requests.get("http://localhost:6333/", timeout=5)
        if response.status_code == 200:
            print("✅ Qdrant 서버가 이미 실행 중입니다.")
            return True
    except ImportError:
        print("⚠️ requests 모듈이 없습니다. pip install requests로 설치하세요.")
        return False
    except Exception:
        pass
    
    print("⚠️ Qdrant 서버가 실행되지 않았습니다.")
    return False

def start_qdrant_server():
    """Qdrant 서버를 Docker로 시작"""
    print("🚀 Qdrant 서버 시작 중...")
    
    try:
        # 기존 Qdrant 컨테이너가 있는지 확인
        result = subprocess.run([
            'docker', 'ps', '-a', '--filter', 'name=qdrant', '--format', '{{.Names}}'
        ], capture_output=True, text=True, timeout=10)
        
        if 'qdrant' in result.stdout:
            print("🔄 기존 Qdrant 컨테이너를 시작합니다...")
            # 기존 컨테이너 시작
            result = subprocess.run(['docker', 'start', 'qdrant'], 
                                  capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                print("✅ 기존 Qdrant 컨테이너가 시작되었습니다.")
            else:
                print("❌ 기존 컨테이너 시작 실패, 새로 생성합니다...")
                # 기존 컨테이너 제거 후 새로 생성
                subprocess.run(['docker', 'rm', '-f', 'qdrant'], 
                             capture_output=True, timeout=10)
                return _create_new_qdrant_container()
        else:
            print("🆕 새로운 Qdrant 컨테이너를 생성합니다...")
            return _create_new_qdrant_container()
            
    except subprocess.TimeoutExpired:
        print("❌ Qdrant 서버 시작 시간 초과")
        return None
    except Exception as e:
        print(f"❌ Qdrant 서버 시작 중 오류: {e}")
        return None

def _create_new_qdrant_container():
    """새로운 Qdrant 컨테이너 생성"""
    try:
        # Qdrant 컨테이너 생성 및 실행
        result = subprocess.run([
            'docker', 'run', '-d',
            '--name', 'qdrant',
            '-p', '6333:6333',
            '-p', '6334:6334',
            '-v', 'qdrant_storage:/qdrant/storage',
            'qdrant/qdrant:latest'
        ], capture_output=True, text=True, timeout=60)
        
        if result.returncode == 0:
            print("✅ Qdrant 컨테이너가 생성되었습니다.")
            print("⏳ Qdrant 서버 시작 대기 중...")
            
            # 서버 시작 대기 (최대 30초)
            for i in range(30):
                time.sleep(1)
                if check_qdrant_server():
                    print("✅ Qdrant 서버가 성공적으로 시작되었습니다.")
                    return True
                if i % 5 == 0:  # 5초마다 로그 출력
                    print(f"⏳ 대기 중... ({i+1}/30)")
            
            print("❌ Qdrant 서버 시작 시간 초과")
            return False
        else:
            print(f"❌ Qdrant 컨테이너 생성 실패: {result.stderr}")
            return False
            
    except subprocess.TimeoutExpired:
        print("❌ Qdrant 컨테이너 생성 시간 초과")
        return False
    except Exception as e:
        print(f"❌ Qdrant 컨테이너 생성 중 오류: {e}")
        return False

def stop_qdrant_server():
    """Qdrant 서버 중지"""
    print("🛑 Qdrant 서버 중지 중...")
    
    try:
        result = subprocess.run(['docker', 'stop', 'qdrant'], 
                              capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            print("✅ Qdrant 서버가 중지되었습니다.")
        else:
            print("⚠️ Qdrant 서버 중지 중 경고 (이미 중지되었을 수 있음)")
    except Exception as e:
        print(f"⚠️ Qdrant 서버 중지 중 오류: {e}")

def check_dependencies():
    """필요한 의존성 확인"""
    print("🔍 의존성 확인 중...")
    
    required_packages = [
        ('fastapi', 'fastapi'),
        ('uvicorn', 'uvicorn'),
        ('sqlalchemy', 'sqlalchemy'),
        ('pydantic', 'pydantic'),
        ('langgraph', 'langgraph'),
        ('requests', 'requests')  # Qdrant 서버 확인용
    ]
    
    missing_packages = []
    for package_name, import_name in required_packages:
        try:
            __import__(import_name)
        except ImportError:
            missing_packages.append(package_name)
    
    if missing_packages:
        print(f"❌ 누락된 패키지: {', '.join(missing_packages)}")
        print("다음 명령어로 설치하세요:")
        print("pip install -r requirements.txt")
        return False
    
    print("✅ 모든 의존성이 설치되어 있습니다.")
    return True

def check_env_file():
    """환경 변수 파일 확인"""
    print("🔍 환경 변수 파일 확인 중...")
    
    env_file = Path(".env")
    if not env_file.exists():
        print("❌ .env 파일이 없습니다.")
        print("다음 내용으로 .env 파일을 생성하세요:")
        print("""
# Gemini API 설정
GEMINI_API_KEY=your_gemini_api_key_here

# 데이터베이스 설정
DATABASE_URL=sqlite:///./jarvis.db

# API 설정
API_HOST=0.0.0.0
API_PORT=8000

# 이미지 업로드 설정
IMAGE_UPLOAD_PATH=./uploads/images
MAX_IMAGE_SIZE_MB=10
        """)
        return False
    
    print("✅ .env 파일이 존재합니다.")
    return True

def create_directories():
    """필요한 디렉토리 생성"""
    print("📁 디렉토리 생성 중...")
    
    directories = [
        "uploads",
        "uploads/images",
        "backend/logs"
    ]
    
    for directory in directories:
        Path(directory).mkdir(parents=True, exist_ok=True)
    
    print("✅ 필요한 디렉토리가 생성되었습니다.")

def check_frontend_file():
    """프론트엔드 파일 확인"""
    print("🔍 프론트엔드 파일 확인 중...")
    
    frontend_file = Path("frontend/front.py")
    if not frontend_file.exists():
        print("❌ frontend/front.py 파일이 없습니다.")
        print("프론트엔드 파일을 생성해야 합니다.")
        return False
    
    print("✅ 프론트엔드 파일이 존재합니다.")
    return True

def initialize_database():
    """데이터베이스 초기화"""
    print("🗄️ 데이터베이스 초기화 중...")
    
    try:
        # backend 디렉토리를 Python 경로에 추가
        backend_path = Path("backend")
        if backend_path.exists():
            # backend 디렉토리를 절대 경로로 추가
            backend_abs_path = backend_path.absolute()
            if str(backend_abs_path) not in sys.path:
                sys.path.insert(0, str(backend_abs_path))
            
            try:
                # 직접 connection.py 파일을 실행
                import subprocess
                result = subprocess.run([
                    sys.executable, str(backend_abs_path / "database" / "connection.py")
                ], capture_output=True, text=True, cwd=str(backend_abs_path))
                
                if result.returncode == 0:
                    print("✅ 데이터베이스가 초기화되었습니다.")
                    return True
                else:
                    print(f"⚠️ 데이터베이스 초기화 중 경고: {result.stderr}")
                    print("✅ 기본 데이터베이스 초기화 완료")
                    return True
            except Exception as import_error:
                print(f"⚠️ database.connection 모듈 실행 중 오류: {import_error}")
                print("✅ 기본 데이터베이스 초기화로 진행합니다.")
                return True
        else:
            print("❌ backend 디렉토리를 찾을 수 없습니다.")
            return False
    except Exception as e:
        print(f"❌ 데이터베이스 초기화 실패: {e}")
        return False

def wait_for_backend_server():
    """백엔드 서버가 완전히 시작될 때까지 대기합니다."""
    import requests
    
    max_attempts = 30  # 최대 30초 대기
    for attempt in range(max_attempts):
        try:
            response = requests.get("http://localhost:8000/api/v2/health", timeout=2)
            if response.status_code == 200:
                print("✅ 백엔드 서버가 준비되었습니다.")
                return True
        except:
            pass
        
        print(f"⏳ 서버 시작 대기 중... ({attempt + 1}/{max_attempts})")
        time.sleep(1)
    
    print("❌ 백엔드 서버 시작 시간 초과")
    return False

def get_stored_token():
    """저장된 토큰 조회"""
    # login_view 모듈에서 get_stored_token 함수를 import하여 사용
    try:
        # frontend 디렉토리를 경로에 추가
        frontend_dir = str(Path("frontend").resolve())
        if frontend_dir not in sys.path:
            sys.path.insert(0, frontend_dir)
        
        from login_view import get_stored_token as login_get_token
        return login_get_token()
    except ImportError as e:
        print(f"토큰 조회 실패: {e}")
        return None

def check_auth_and_get_user_info():
    """인증 확인 및 사용자 정보 반환 - 항상 로그인 창 표시"""
    print("\n🔐 Google 계정으로 로그인합니다...")
    
    # login_view 모듈 임포트
    try:
        # frontend 디렉토리를 경로에 추가
        frontend_dir = str(Path("frontend").resolve())
        if frontend_dir not in sys.path:
            sys.path.insert(0, frontend_dir)
        
        from login_view import main as login_main
    except ImportError as e:
        print(f"❌ 로그인 모듈 import 실패: {e}")
        import traceback
        traceback.print_exc()
        return None
    
    # 항상 로그인 화면 실행 (기존 토큰 무시)
    print("📱 Google 로그인 창을 표시합니다...")
    user_info = login_main()
    return user_info

def perform_user_survey(user_id):
    """사용자 설문지를 실행합니다."""
    print("\n📋 사용자 설문지를 시작합니다...")
    
    try:
        from frontend.survey_dialog import show_survey_dialog
        
        # 설문지 다이얼로그 실행
        success = show_survey_dialog(user_id)
        
        if success:
            print("✅ 설문지가 완료되었습니다.")
            return True
        else:
            print("❌ 설문지가 취소되었습니다.")
            return False
        
    except ImportError as e:
        print(f"❌ 설문지 UI 모듈 import 오류: {e}")
        return False
    except Exception as e:
        print(f"❌ 설문지 실행 중 오류: {e}")
        return False

def submit_folder_setup(folder_path, token):
    """폴더 경로를 백엔드에 전송"""
    try:
        response = requests.post(
            "http://localhost:8000/api/v2/settings/initial-setup",
            headers={"Authorization": f"Bearer {token}"},
            json={"folder_path": folder_path},
            timeout=10
        )
        
        if response.status_code == 200:
            print("✅ 폴더 경로가 백엔드에 저장되었습니다.")
            return True
        else:
            print(f"❌ 백엔드 저장 실패: {response.text}")
            return False
    
    except Exception as e:
        print(f"❌ 백엔드 저장 중 오류: {e}")
        return False

def perform_folder_selection():
    """폴더 선택 UI를 실행합니다."""
    print("\n📁 폴더 선택을 시작합니다...")
    
    try:
        from frontend.folder_selector import select_folders
        
        # 폴더 선택 UI 실행
        selected_folders = select_folders()
        
        # 전역 변수 선언
        global selected_folders_global
        
        if selected_folders == "cancelled":
            print("❌ 폴더 선택이 취소되었습니다.")
            return False
        elif selected_folders is None:
            print("✅ 전체 사용자 폴더 스캔이 선택되었습니다.")
            # 전역 변수에 저장하여 나중에 사용
            selected_folders_global = None
        else:
            print(f"✅ 선택된 폴더: {len(selected_folders)}개")
            for folder in selected_folders:
                print(f"  - {folder}")
            # 전역 변수에 저장하여 나중에 사용
            selected_folders_global = selected_folders
        
        return True
        
    except ImportError as e:
        print(f"❌ 폴더 선택 UI 모듈 import 오류: {e}")
        return False
    except Exception as e:
        print(f"❌ 폴더 선택 중 오류: {e}")
        return False

def perform_initial_data_collection_with_progress(user_id: int):
    """선택된 폴더로 데이터 수집을 시작하고 진행률을 표시합니다."""
    print(f"\n📊 사용자 {user_id}의 데이터 수집을 시작합니다...")
    
    try:
        from backend.database.data_collector import get_manager
        
        manager = get_manager(user_id)
        
        collection_thread = threading.Thread(
            target=manager.perform_initial_collection, args=(selected_folders_global,), daemon=True
        )
        collection_thread.start()
        
        # 프로그레스 바 표시
        with tqdm(total=100, desc="초기 데이터 수집", unit="%", 
                  bar_format="{l_bar}{bar}| {n:.1f}% [{elapsed}<{remaining}, {desc}]") as pbar:
            
            # 초기 데이터 수집이 완료될 때까지 대기
            while not manager.initial_collection_done:
                current_progress = manager.progress
                pbar.update(current_progress - pbar.n)
                pbar.set_description_str(manager.progress_message)
                time.sleep(0.5)

            # 완료 시 100%로 채우고 최종 메시지 표시
            pbar.update(100 - pbar.n)
            pbar.set_description_str(manager.progress_message)
        
        if "오류" in manager.progress_message:
             print(f"❌ 초기 데이터 수집 중 오류가 발생했습니다: {manager.progress_message}")
             return False

        print("✅ 초기 데이터 수집이 성공적으로 완료되었습니다.")

        # ✨ 추가: 초기 수집 완료 후, 백그라운드 증분 수집을 시작합니다.
        print("\n🔄 백그라운드에서 증분 데이터 수집을 시작합니다...")
        manager.start_collection(selected_folders_global)

        return True
        
    except ImportError as e:
        print(f"❌ 모듈 import 오류: {e}")
        return False
    except Exception as e:
        print(f"❌ 초기 데이터 수집 중 오류 발생: {e}")
        return False

def start_backend():
    """백엔드 서버 시작"""
    print("🚀 백엔드 서버 시작 중...")
    logger.info("백엔드 서버 시작 시도")
    
    try:
        backend_dir = Path("backend")
        if not backend_dir.exists():
            print("❌ backend 디렉토리를 찾을 수 없습니다.")
            logger.error("backend 디렉토리를 찾을 수 없습니다.")
            return None
        
        logger.info(f"백엔드 프로세스를 '{backend_dir}' 디렉토리에서 시작합니다.")
        
        # 백엔드 서버 시작 (cwd 인자 사용)
        process = subprocess.Popen(
            [sys.executable, 'main.py'],
            # stdout=subprocess.PIPE,
            # stderr=subprocess.PIPE,
            text=True,
            cwd=backend_dir  
        )
        
        # 서버 시작 대기
        logger.info("서버 시작 대기 중...")
        time.sleep(3)
        
        # 프로세스 상태 확인
        if process.poll() is None:
            print("✅ 백엔드 서버가 시작되었습니다.")
            print("🌐 API 문서: http://localhost:8000/docs")
            logger.info("백엔드 서버 시작 성공")
            return process
        else:
            print("❌ 백엔드 서버 시작에 실패했습니다.")
            logger.error("백엔드 서버 시작 실패")
            
            # 오류 출력 읽기
            try:
                stdout, stderr = process.communicate(timeout=5)
                if stdout:
                    print(f"stdout: {stdout}")
                    logger.error(f"stdout: {stdout}")
                if stderr:
                    print(f"stderr: {stderr}")
                    logger.error(f"stderr: {stderr}")
            except subprocess.TimeoutExpired:
                print("프로세스 출력 읽기 시간 초과")
                logger.error("프로세스 출력 읽기 시간 초과")
            
            return None
    except Exception as e:
        print(f"❌ 백엔드 서버 시작 중 오류: {e}")
        logger.error(f"백엔드 서버 시작 중 오류: {e}")
        import traceback
        traceback.print_exc()
        return None

def start_frontend():
    """프론트엔드 시작 (데스크톱 플로팅 채팅 앱)"""
    print("🎨 데스크톱 플로팅 채팅 앱 시작 중...")
    
    try:
        frontend_file = Path("frontend/front.py")
        if not frontend_file.exists():
            print("❌ frontend/front.py 파일을 찾을 수 없습니다.")
            return None
        
        # 데스크톱 플로팅 채팅 앱 실행
        process = subprocess.Popen([
            sys.executable, str(frontend_file)
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        # 서버 시작 대기
        time.sleep(3)
        
        if process.poll() is None:
            print("✅ 데스크톱 플로팅 채팅 앱이 시작되었습니다.")
            print("💬 화면 우측 하단에 플로팅 버튼이 나타납니다.")
            return process
        else:
            stdout, stderr = process.communicate()
            print(f"❌ 데스크톱 앱 시작 실패:")
            if stdout:
                print(f"stdout: {stdout.decode()}")
            if stderr:
                print(f"stderr: {stderr.decode()}")
            return None
    except Exception as e:
        print(f"❌ 데스크톱 앱 시작 중 오류: {e}")
        return None

def main():
    """메인 함수"""
    print("🤖 JARVIS Multi-Agent System")
    print("=" * 60)
    
    # 현재 디렉토리를 프로젝트 루트로 설정
    project_root = Path(__file__).parent
    os.chdir(project_root)
    
    # 의존성 확인
    if not check_dependencies():
        return
    
    # Docker 확인 및 Qdrant 서버 자동 시작
    if not check_docker():
        print("\n⚠️ Docker가 없어도 시스템을 실행할 수 있지만, 벡터 검색 기능이 제한됩니다.")
        choice = input("계속 진행하시겠습니까? (y/n): ").strip().lower()
        if choice != 'y':
            return
    else:
        # Qdrant 서버 확인 및 시작
        if not check_qdrant_server():
            print("\n🚀 Qdrant 서버를 자동으로 시작하시겠습니까?")
            choice = input("자동 시작 (y) / 수동 시작 (n) / 건너뛰기 (s): ").strip().lower()
            
            if choice == 'y':
                if not start_qdrant_server():
                    print("❌ Qdrant 서버 시작에 실패했습니다.")
                    print("\n💡 수동으로 다음 명령어를 실행하세요:")
                    print("docker run -d --name qdrant -p 6333:6333 -p 6334:6334 -v qdrant_storage:/qdrant/storage qdrant/qdrant:latest")
            elif choice == 'n':
                print("\n💡 수동으로 다음 명령어를 실행하세요:")
                print("docker run -d --name qdrant -p 6333:6333 -p 6334:6334 -v qdrant_storage:/qdrant/storage qdrant/qdrant:latest")
                print("\n📋 추가 명령어:")
                print("- 컨테이너 상태 확인: docker ps")
                print("- 컨테이너 중지: docker stop qdrant")
                print("- 컨테이너 제거: docker rm qdrant")
            else:
                print("⚠️ Qdrant 서버 없이 진행합니다. 벡터 검색 기능이 제한됩니다.")
    
    # 환경 변수 파일 확인
    if not check_env_file():
        return
    
    # 프론트엔드 파일 확인
    if not check_frontend_file():
        print("프론트엔드 파일이 없습니다. 백엔드만 시작하거나 파일을 생성하세요.")
        choice = input("백엔드만 시작하시겠습니까? (y/n): ").strip().lower()
        if choice != 'y':
            return
    
    # 디렉토리 생성
    create_directories()
    
    # 데이터베이스 초기화
    if not initialize_database():
        return
    
    # 백엔드를 먼저 시작해야 인증 API에 접근 가능
    print("\n🔄 백엔드 서버를 먼저 시작합니다...")
    backend_process = start_backend()
    if not backend_process:
        print("❌ 백엔드 서버 시작에 실패했습니다.")
        return
    
    # 백엔드 서버 시작 대기
    if not wait_for_backend_server():
        print("❌ 백엔드 서버 시작에 실패했습니다.")
        backend_process.terminate()
        return
    
    # 인증 확인 및 사용자 정보 받기
    user_info = check_auth_and_get_user_info()
    if user_info is None:
        print("❌ 사용자 인증에 실패했습니다. 시스템을 종료합니다.")
        backend_process.terminate()
        return
    
    # 토큰 저장 (나중에 사용)
    token = get_stored_token()
    
    # 사용자 설정 완료 여부 확인
    has_completed_setup = user_info.get("has_completed_setup", 0)
    user_id = user_info.get("user_id")

    # has_completed_setup에 따라 분기
    if has_completed_setup == 0:
        # 신규 사용자: 설문지 + 폴더 선택 진행
        print("\n📋 신규 사용자 설정을 진행합니다...")
        print("   - 사용자 설문지 작성")
        print("   - 폴더 선택")
        print("   - 초기 데이터 수집")
        
        # 설문지 실행
        if not perform_user_survey(user_id):
            print("❌ 설문지가 취소되었습니다. 시스템을 종료합니다.")
            backend_process.terminate()
            return
        
        # 폴더 선택 수행
        if not perform_folder_selection():
            print("❌ 폴더 선택이 취소되었습니다. 시스템을 종료합니다.")
            backend_process.terminate()
            return
        
        # 선택된 폴더를 백엔드에 전송
        if selected_folders_global:
            # 여러 폴더가 선택된 경우 첫 번째 폴더를 사용
            folder_path = selected_folders_global[0]
        else:
            # 전체 사용자 폴더 스캔 선택됨
            folder_path = None
        
        # 백엔드에 폴더 경로 전송
        if not submit_folder_setup(folder_path or "", token):
            print("❌ 폴더 경로를 백엔드에 저장하는데 실패했습니다. 시스템을 종료합니다.")
            backend_process.terminate()
            return
        
        print("✅ 초기 설정이 완료되었습니다.")
    else:
        # 기존 사용자: 설문지와 폴더 선택 건너뛰기
        print("\n✅ 기존 사용자입니다. 초기 설정을 건너뜁니다.")
        print("   - 설문지: 이미 완료됨")
        print("   - 폴더 선택: 이미 완료됨")
        print("   - 기존 데이터 사용")
        
    # 초기 데이터 수집 수행 (완료될 때까지 대기)
    if has_completed_setup == 0:
        print("\n📊 초기 데이터 수집을 시작합니다...")
        if not perform_initial_data_collection_with_progress(user_id):
            print("❌ 초기 데이터 수집에 실패했습니다. 시스템을 종료합니다.")
            backend_process.terminate()
            return
    else:
        print("\n📊 기존 데이터를 사용합니다.")
        print("   - 이미 수집된 파일 데이터 사용")
        print("   - 이미 수집된 브라우저 히스토리 사용")
        print("   - 이미 수집된 앱 사용 기록 사용")
        
    # 프론트엔드 시작
    frontend_process = start_frontend()
    if not frontend_process:
        backend_process.terminate()
        return
        
    print("\n🎉 JARVIS Multi-Agent System이 성공적으로 시작되었습니다!")
    print("=" * 60)
    print("🔗 API 문서: http://localhost:8000/docs")
    print("📊 시스템 정보: http://localhost:8000/info")
    print("🔍 Qdrant 관리: http://localhost:6333/dashboard")
    print("=" * 60)
    print("\n시스템을 종료하려면 Ctrl+C를 누르세요...")
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n🛑 시스템을 종료합니다...")
        backend_process.terminate()
        frontend_process.terminate()
        # Qdrant 서버도 중지할지 묻기
        if check_docker():
            choice = input("Qdrant 서버도 중지하시겠습니까? (y/n): ").strip().lower()
            if choice == 'y':
                stop_qdrant_server()
        print("✅ 시스템이 종료되었습니다.")


if __name__ == "__main__":
    main()