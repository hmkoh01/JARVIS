#!/usr/bin/env python3
"""
EXE 배포를 위한 경로 처리 유틸리티
"""
import os
import sys
from pathlib import Path
from typing import Optional

def get_base_path() -> Path:
    """
    EXE 환경과 개발 환경을 모두 지원하는 기본 경로 반환
    
    Returns:
        Path: 프로젝트의 기본 경로
    """
    if getattr(sys, 'frozen', False):
        # EXE로 실행 중인 경우
        # PyInstaller가 생성한 임시 폴더에서 실행
        base_path = Path(sys._MEIPASS)
    else:
        # 개발 환경에서 실행 중인 경우
        # 현재 파일의 위치를 기준으로 프로젝트 루트 계산
        current_file = Path(__file__)
        base_path = current_file.parent.parent.parent.parent.parent
    
    return base_path

def get_config_path(config_filename: str = "configs.yaml") -> Path:
    """
    설정 파일 경로 반환 (EXE 환경 호환)
    
    Args:
        config_filename: 설정 파일명
        
    Returns:
        Path: 설정 파일 경로
    """
    base_path = get_base_path()
    
    # 여러 가능한 경로에서 설정 파일 찾기 (우선순위 순)
    possible_paths = [
        # 1. 현재 작업 디렉토리 (가장 높은 우선순위)
        Path.cwd() / config_filename,
        # 2. 프로젝트 루트
        base_path / config_filename,
        # 3. backend 디렉토리
        base_path / "backend" / config_filename,
        # 4. 상위 디렉토리들
        Path.cwd().parent / config_filename,
        Path.cwd().parent.parent / config_filename,
    ]
    
    for config_path in possible_paths:
        if config_path.exists():
            return config_path
    
    # 설정 파일을 찾지 못한 경우 현재 작업 디렉토리 반환
    return Path.cwd() / config_filename

def get_data_dir() -> Path:
    """
    사용자 데이터 디렉토리 경로 반환 (EXE 환경 호환)
    
    Returns:
        Path: 데이터 디렉토리 경로
    """
    if getattr(sys, 'frozen', False):
        # EXE 환경: 사용자 홈 디렉토리에 데이터 저장
        data_dir = Path.home() / ".jarvis" / "data"
    else:
        # 개발 환경: 프로젝트 내부에 데이터 저장
        base_path = get_base_path()
        data_dir = base_path / "backend" / "sqlite"
    
    # 디렉토리가 없으면 생성
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir

def get_db_path(db_filename: str = "meta.db") -> Path:
    """
    데이터베이스 파일 경로 반환 (EXE 환경 호환)
    
    Args:
        db_filename: 데이터베이스 파일명
        
    Returns:
        Path: 데이터베이스 파일 경로
    """
    data_dir = get_data_dir()
    return data_dir / db_filename

def get_cache_dir() -> Path:
    """
    캐시 디렉토리 경로 반환 (EXE 환경 호환)
    
    Returns:
        Path: 캐시 디렉토리 경로
    """
    if getattr(sys, 'frozen', False):
        # EXE 환경: 사용자 홈 디렉토리에 캐시 저장
        cache_dir = Path.home() / ".jarvis" / "cache"
    else:
        # 개발 환경: 프로젝트 내부에 캐시 저장
        base_path = get_base_path()
        cache_dir = base_path / "backend" / "cache"
    
    # 디렉토리가 없으면 생성
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir

def get_log_dir() -> Path:
    """
    로그 디렉토리 경로 반환 (EXE 환경 호환)
    
    Returns:
        Path: 로그 디렉토리 경로
    """
    if getattr(sys, 'frozen', False):
        # EXE 환경: 사용자 홈 디렉토리에 로그 저장
        log_dir = Path.home() / ".jarvis" / "logs"
    else:
        # 개발 환경: 프로젝트 내부에 로그 저장
        base_path = get_base_path()
        log_dir = base_path / "backend" / "logs"
    
    # 디렉토리가 없으면 생성
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir

def is_exe_environment() -> bool:
    """
    EXE 환경에서 실행 중인지 확인
    
    Returns:
        bool: EXE 환경이면 True, 개발 환경이면 False
    """
    return getattr(sys, 'frozen', False)

def get_model_cache_dir() -> Path:
    """
    모델 캐시 디렉토리 경로 반환 (Hugging Face 호환)
    
    Returns:
        Path: 모델 캐시 디렉토리 경로
    """
    if getattr(sys, 'frozen', False):
        # EXE 환경: 사용자 홈 디렉토리에 모델 캐시 저장
        cache_dir = Path.home() / ".cache" / "huggingface"
    else:
        # 개발 환경: 기본 Hugging Face 캐시 디렉토리 사용
        cache_dir = Path.home() / ".cache" / "huggingface"
    
    # 디렉토리가 없으면 생성
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir
