#!/usr/bin/env python3
"""
JARVIS Multi-Agent System Startup Script
멀티모달 RAG 시스템을 쉽게 시작할 수 있는 스크립트

Usage:
    python start.py              # PyQt6 프론트엔드로 실행 (기본)
    python start.py --backend    # 백엔드만 실행
    python start.py --no-docker  # Docker 확인 건너뛰기
"""

import os
import sys

# Windows Console Encoding Fix
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

import subprocess
import time
import argparse
from pathlib import Path
import logging
import requests
import yaml

logger = logging.getLogger(__name__)

# =============================================================================
# configs.yaml에서 API URL 로드
# =============================================================================
def _load_api_url():
    """configs.yaml에서 API URL을 로드합니다."""
    config_path = Path(__file__).parent / "configs.yaml"
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                configs = yaml.safe_load(f)
                return configs.get("api", {}).get("base_url", "http://localhost:8000")
        except Exception as e:
            logger.warning(f"configs.yaml 로드 실패: {e}")
    return "http://localhost:8000"


API_BASE_URL = _load_api_url()


# =============================================================================
# Docker & Qdrant 관련 함수
# =============================================================================

def check_docker():
    """Docker 설치 및 실행 상태 확인"""
    print("🐳 Docker 상태 확인 중...")
    
    try:
        result = subprocess.run(['docker', '--version'], 
                              capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            print("❌ Docker가 설치되지 않았습니다.")
            return False
        
        print(f"✅ Docker 설치됨: {result.stdout.strip()}")
        
        result = subprocess.run(['docker', 'info'], 
                              capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            print("❌ Docker 데몬이 실행되지 않았습니다.")
            return False
        
        print("✅ Docker 데몬 실행 중")
        return True
        
    except subprocess.TimeoutExpired:
        print("❌ Docker 응답 시간 초과")
        return False
    except FileNotFoundError:
        print("❌ Docker 명령어를 찾을 수 없습니다.")
        return False
    except Exception as e:
        print(f"❌ Docker 확인 중 오류: {e}")
        return False


def check_qdrant_server():
    """Qdrant 서버 실행 상태 확인"""
    try:
        response = requests.get("http://localhost:6333/readyz", timeout=3)
        if response.status_code == 200:
            print("✅ Qdrant 서버 실행 중")
            return True
    except requests.exceptions.RequestException:
        pass
    return False


def start_qdrant_server():
    """Qdrant 서버를 Docker로 시작"""
    print("🚀 Qdrant 서버 시작 중...")
    
    try:
        # 기존 컨테이너 제거
        subprocess.run(['docker', 'rm', '-f', 'qdrant'], 
                      capture_output=True, timeout=30)
        
        # 새 컨테이너 시작
        result = subprocess.run([
            'docker', 'run', '-d',
            '--name', 'qdrant',
            '-p', '6333:6333',
            '-p', '6334:6334',
            '-v', 'qdrant_storage:/qdrant/storage',
            'qdrant/qdrant:latest'
        ], capture_output=True, text=True, timeout=120)
        
        if result.returncode == 0:
            # 서버 시작 대기
            for i in range(30):
                if check_qdrant_server():
                    print("✅ Qdrant 서버 시작 완료")
                    return True
                time.sleep(1)
        
        print("❌ Qdrant 서버 시작 실패")
        return False
        
    except Exception as e:
        print(f"❌ Qdrant 시작 오류: {e}")
        return False


# =============================================================================
# 환경 및 의존성 확인
# =============================================================================

def check_env_file():
    """환경 변수 파일 존재 확인"""
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        print("⚠️ .env 파일이 없습니다.")
        print("   .env.example을 복사하여 .env 파일을 생성하세요.")
        return False
    print("✅ .env 파일 확인됨")
    return True


def check_pyqt6_frontend():
    """PyQt6 프론트엔드 파일 확인"""
    frontend_path = Path(__file__).parent / "frontend" / "pyqt6" / "app.py"
    if not frontend_path.exists():
        print("❌ frontend/pyqt6/app.py 파일을 찾을 수 없습니다.")
        return False
    print("✅ PyQt6 프론트엔드 확인됨")
    return True


def create_directories():
    """필요한 디렉토리 생성"""
    dirs = [
        "data/documents",
        "data/indices",
        "data/cache",
        "logs"
    ]
    
    for dir_path in dirs:
        Path(dir_path).mkdir(parents=True, exist_ok=True)
    
    print("✅ 디렉토리 구조 확인됨")


def initialize_database():
    """데이터베이스 초기화"""
    try:
        # backend 모듈 import
        sys.path.insert(0, str(Path(__file__).parent))
        from backend.database.sqlite import SQLite
        
        db = SQLite()
        db.init_db()
        print("✅ 데이터베이스 초기화 완료")
        return True
    except Exception as e:
        print(f"⚠️ 데이터베이스 초기화 오류: {e}")
        return True  # 오류가 있어도 계속 진행


# =============================================================================
# 프로세스 시작
# =============================================================================

def start_backend():
    """백엔드 서버 시작"""
    print("🔧 백엔드 서버 시작 중...")
    
    try:
        backend_script = Path(__file__).parent / "backend" / "main.py"
        
        process = subprocess.Popen(
            [sys.executable, str(backend_script)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            universal_newlines=True
        )
        
        time.sleep(3)
        
        if process.poll() is None:
            print("✅ 백엔드 서버 프로세스 시작됨")
            return process
        else:
            print("❌ 백엔드 서버 시작 실패")
            return None
            
    except Exception as e:
        print(f"❌ 백엔드 시작 오류: {e}")
        return None


def wait_for_backend_server(max_wait=60):
    """백엔드 서버 준비 대기"""
    print("⏳ 백엔드 서버 응답 대기 중...")
    
    for i in range(max_wait):
        try:
            response = requests.get(f"{API_BASE_URL}/health", timeout=2)
            if response.status_code == 200:
                print("✅ 백엔드 서버 준비 완료")
                return True
        except requests.exceptions.RequestException:
            pass
        time.sleep(1)
    
    print("❌ 백엔드 서버 응답 시간 초과")
    return False


def start_pyqt6_frontend():
    """PyQt6 프론트엔드 시작"""
    print("🎨 PyQt6 프론트엔드 시작 중...")
    
    try:
        pyqt6_main = Path(__file__).parent / "frontend" / "pyqt6" / "app.py"
        
        process = subprocess.Popen([sys.executable, str(pyqt6_main)])
        
        time.sleep(2)
        
        if process.poll() is None:
            print("✅ PyQt6 프론트엔드 시작됨")
            return process
        else:
            print("❌ PyQt6 프론트엔드 시작 실패")
            return None
            
    except Exception as e:
        print(f"❌ 프론트엔드 시작 오류: {e}")
        return None


# =============================================================================
# 메인 함수
# =============================================================================

def main():
    """메인 함수"""
    # 명령줄 인자 파싱
    parser = argparse.ArgumentParser(description="JARVIS Multi-Agent System")
    parser.add_argument(
        "--backend",
        action="store_true",
        help="백엔드만 실행"
    )
    parser.add_argument(
        "--no-docker",
        action="store_true",
        help="Docker 확인 건너뛰기"
    )
    args = parser.parse_args()
    
    print("🤖 JARVIS Multi-Agent System")
    print("=" * 60)
    
    # 현재 디렉토리를 프로젝트 루트로 설정
    project_root = Path(__file__).parent
    os.chdir(project_root)
    
    # Docker/Qdrant 확인 (선택적)
    if not args.no_docker:
        if check_docker():
            if not check_qdrant_server():
                print("\n⚠️ Qdrant 서버가 실행되지 않았습니다.")
                choice = input("Qdrant 자동 시작 (y) / 건너뛰기 (n): ").strip().lower()
                if choice == 'y':
                    start_qdrant_server()
        else:
            print("\n⚠️ Docker 없이 진행합니다. 벡터 검색이 제한됩니다.")
    
    # 환경 파일 확인
    if not check_env_file():
        return
    
    # 디렉토리 생성
    create_directories()
    
    # 데이터베이스 초기화
    initialize_database()
    
    # 백엔드 시작
    print("\n🔄 백엔드 서버를 시작합니다...")
    backend_process = start_backend()
    if not backend_process:
        print("❌ 백엔드 서버 시작 실패")
        return
    
    # 백엔드 대기
    if not wait_for_backend_server():
        print("❌ 백엔드 서버 응답 없음")
        backend_process.terminate()
        return
    
    # 백엔드만 실행 모드
    if args.backend:
        print("\n✅ 백엔드만 실행 중입니다.")
        print(f"🔗 API 문서: {API_BASE_URL}/docs")
        print("\n종료하려면 Ctrl+C를 누르세요...")
        
        try:
            while backend_process.poll() is None:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n🛑 종료 중...")
            backend_process.terminate()
        return
    
    # PyQt6 프론트엔드 확인
    if not check_pyqt6_frontend():
        backend_process.terminate()
        return
    
    # PyQt6 프론트엔드 시작
    frontend_process = start_pyqt6_frontend()
    if not frontend_process:
        backend_process.terminate()
        return
    
    print("\n🎉 JARVIS가 시작되었습니다!")
    print("=" * 60)
    print(f"🔗 API 문서: {API_BASE_URL}/docs")
    print("🔍 Qdrant 관리: http://localhost:6333/dashboard")
    print("=" * 60)
    print("\n종료하려면 Ctrl+C를 누르세요...")
    
    try:
        while True:
            # 프론트엔드 종료 감지
            if frontend_process.poll() is not None:
                print("\n📱 프론트엔드가 종료되었습니다.")
                break
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n🛑 시스템 종료 중...")
    finally:
        if frontend_process.poll() is None:
            frontend_process.terminate()
        backend_process.terminate()
        print("✅ 시스템이 종료되었습니다.")


if __name__ == "__main__":
    main()
