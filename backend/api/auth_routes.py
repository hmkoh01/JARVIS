"""
Google OAuth 2.0 인증 API 엔드포인트
"""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

# 현재 스크립트의 상위 디렉토리(backend)를 Python 경로에 추가
backend_dir = Path(__file__).parent.parent.absolute()
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from fastapi import APIRouter, HTTPException, Depends, Header
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timedelta
import logging

from authlib.integrations.httpx_client import AsyncOAuth2Client
from jose import JWTError, jwt
import httpx

from config.settings import settings
from database.sqlite import SQLite

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["Authentication"])
security = HTTPBearer()

# Authlib OAuth 클라이언트 초기화
google_oauth_client = AsyncOAuth2Client(
    client_id=settings.GOOGLE_CLIENT_ID,
    client_secret=settings.GOOGLE_CLIENT_SECRET,
    redirect_uri=settings.GOOGLE_REDIRECT_URI,
    scope="openid email profile",
    authorization_base_url="https://accounts.google.com/o/oauth2/v2/auth",
    token_endpoint="https://oauth2.googleapis.com/token",
    userinfo_endpoint="https://openidconnect.googleapis.com/v1/userinfo"
)

# SQLite 인스턴스
db = SQLite()


# ============================================================================
# 요청/응답 스키마
# ============================================================================

class ExchangeCodeRequest(BaseModel):
    """코드 교환 요청"""
    code: str


class LoginResponse(BaseModel):
    """로그인 URL 응답"""
    login_url: str


class AuthResponse(BaseModel):
    """인증 응답"""
    jarvis_token: str
    user_id: int
    email: str
    has_completed_setup: int
    selected_root_folder: Optional[str] = None


class UserInfoResponse(BaseModel):
    """사용자 정보 응답"""
    user_id: int
    email: str
    has_completed_setup: int
    selected_root_folder: Optional[str] = None


# ============================================================================
# 보조 함수
# ============================================================================

def create_jwt_token(user_id: int) -> str:
    """JWT 토큰 생성"""
    payload = {
        "user_id": user_id,
        "exp": datetime.utcnow() + timedelta(hours=settings.JWT_EXPIRATION_HOURS),
        "iat": datetime.utcnow()
    }
    token = jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    return token


def verify_jwt_token(token: str) -> dict:
    """JWT 토큰 검증 및 페이로드 추출"""
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        return payload
    except JWTError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    """요청 헤더의 JWT 토큰을 검증하고 user_id를 반환하는 의존성"""
    token = credentials.credentials
    payload = verify_jwt_token(token)
    user_id = payload.get("user_id")
    
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token payload")
    
    return {"user_id": user_id}


# ============================================================================
# API 엔드포인트
# ============================================================================

@router.get("/google/login", response_model=LoginResponse)
async def google_login():
    """
    Google 로그인 URL 생성
    
    Returns:
        LoginResponse: 프론트엔드가 리디렉션할 Google 로그인 URL
    """
    try:
        if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_CLIENT_SECRET:
            raise HTTPException(
                status_code=500,
                detail="Google OAuth credentials not configured"
            )
        
        # Google OAuth 인증 URL 생성
        uri, state = google_oauth_client.create_authorization_url(
            "https://accounts.google.com/o/oauth2/v2/auth",
            access_type="offline",  # refresh_token을 받기 위해 필요
            prompt="consent"
        )
        
        return LoginResponse(login_url=uri)
    
    except Exception as e:
        logger.error(f"Google 로그인 URL 생성 오류: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Google 로그인 URL 생성 오류: {str(e)}")


@router.post("/google/exchange-code", response_model=AuthResponse)
async def exchange_code(request: ExchangeCodeRequest):
    """
    Google 인증 코드를 액세스 토큰으로 교환하고 사용자 정보를 가져옵니다.
    
    Args:
        request: 프론트엔드가 Google로부터 받은 인증 코드
        
    Returns:
        AuthResponse: JWT 토큰 및 사용자 정보
    """
    try:
        if not request.code:
            raise HTTPException(status_code=400, detail="Code is required")
        
        # 1. 액세스 토큰으로 교환
        async with httpx.AsyncClient() as client:
            token_response = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "code": request.code,
                    "client_id": settings.GOOGLE_CLIENT_ID,
                    "client_secret": settings.GOOGLE_CLIENT_SECRET,
                    "redirect_uri": settings.GOOGLE_REDIRECT_URI,
                    "grant_type": "authorization_code"
                }
            )
            
            if token_response.status_code != 200:
                logger.error(f"Token exchange failed: {token_response.text}")
                raise HTTPException(status_code=400, detail="Failed to exchange code for token")
            
            token_data = token_response.json()
            access_token = token_data.get("access_token")
            refresh_token = token_data.get("refresh_token")
            
            if not access_token:
                raise HTTPException(status_code=400, detail="No access token received")
        
        # 2. Google 사용자 정보 가져오기
        async with httpx.AsyncClient() as client:
            user_info_response = await client.get(
                "https://openidconnect.googleapis.com/v1/userinfo",
                headers={"Authorization": f"Bearer {access_token}"}
            )
            
            if user_info_response.status_code != 200:
                logger.error(f"User info fetch failed: {user_info_response.text}")
                raise HTTPException(status_code=400, detail="Failed to fetch user info")
            
            user_info = user_info_response.json()
            google_user_id = user_info.get("sub")
            email = user_info.get("email")
            
            if not google_user_id or not email:
                raise HTTPException(status_code=400, detail="Invalid user info from Google")
        
        # 3. 데이터베이스에서 사용자 조회/생성
        user = db.get_or_create_user_by_google(
            google_id=google_user_id,
            email=email,
            refresh_token=refresh_token  # DB에 저장 (향후 토큰 갱신용)
        )
        
        if not user:
            raise HTTPException(status_code=500, detail="Failed to create/retrieve user")
        
        user_id = user["user_id"]
        
        # 4. JWT 토큰 생성
        jarvis_token = create_jwt_token(user_id)
        
        # 5. 응답 반환
        return AuthResponse(
            jarvis_token=jarvis_token,
            user_id=user_id,
            email=email,
            has_completed_setup=user.get("has_completed_setup", 0),
            selected_root_folder=user.get("selected_root_folder")
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"코드 교환 오류: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"인증 처리 중 오류가 발생했습니다: {str(e)}")


@router.get("/me", response_model=UserInfoResponse)
async def get_current_user_info(current_user: dict = Depends(get_current_user)):
    """
    현재 로그인한 사용자 정보를 반환합니다.
    
    Args:
        current_user: JWT 검증 의존성에서 추출한 사용자 정보
        
    Returns:
        UserInfoResponse: 사용자 정보
    """
    try:
        user_id = current_user["user_id"]
        
        # 데이터베이스에서 사용자 정보 조회
        user = db.get_user_by_id(user_id)
        
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        return UserInfoResponse(
            user_id=user["user_id"],
            email=user["email"],
            has_completed_setup=user.get("has_completed_setup", 0),
            selected_root_folder=user.get("selected_root_folder")
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"사용자 정보 조회 오류: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"사용자 정보 조회 중 오류가 발생했습니다: {str(e)}")
