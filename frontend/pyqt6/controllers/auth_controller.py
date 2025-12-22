"""
JARVIS Auth Controller
Handles authentication logic and token management.

Phase 5: Complete implementation with token_store integration
"""

from typing import Optional, Tuple, Dict, Any

import requests
from PyQt6.QtCore import QObject, pyqtSignal

try:
    from config import API_BASE_URL
except ImportError:
    API_BASE_URL = "http://localhost:8000"


class AuthController(QObject):
    """
    Controller for authentication operations.
    
    Manages:
    - Token loading/saving via token_store
    - Token validation and expiration checking
    - User info extraction from JWT
    
    Signals:
        auth_state_changed: Emitted when authentication state changes
        login_required: Emitted when login is needed
    """
    
    auth_state_changed = pyqtSignal(bool)  # is_authenticated
    login_required = pyqtSignal()
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        self._token: Optional[str] = None
        self._user_id: Optional[int] = None
        self._user_info: Optional[Dict[str, Any]] = None
        self._has_completed_setup: Optional[bool] = None  # 백엔드에서 조회한 값
        
        # Import token_store functions
        try:
            from token_store import (
                load_token,
                save_token,
                delete_token,
                is_expiring,
                get_user_id_from_token,
                get_valid_token_and_user,
                decode_token_claims
            )
            self._load_token = load_token
            self._save_token = save_token
            self._delete_token = delete_token
            self._is_expiring = is_expiring
            self._get_user_id = get_user_id_from_token
            self._get_valid_token_and_user = get_valid_token_and_user
            self._decode_claims = decode_token_claims
            self._token_store_available = True
        except ImportError as e:
            print(f"Warning: token_store not available: {e}")
            self._token_store_available = False
    
    def initialize(self) -> bool:
        """
        Initialize authentication state from stored token.
        
        Returns:
            True if valid token found, False otherwise
        """
        if not self._token_store_available:
            return False
        
        try:
            token, user_id = self._get_valid_token_and_user()
            
            if token and user_id:
                self._token = token
                self._user_id = user_id
                self._user_info = self._decode_claims(token)
                self.auth_state_changed.emit(True)
                return True
            else:
                self._clear_state()
                return False
                
        except Exception as e:
            print(f"Auth initialization error: {e}")
            self._clear_state()
            return False
    
    def save_token(self, token: str) -> bool:
        """
        Save token and update auth state.
        
        Args:
            token: JWT token to save
            
        Returns:
            True if saved successfully
        """
        if not self._token_store_available:
            return False
        
        try:
            self._save_token(token)
            self._token = token
            self._user_id = self._get_user_id(token)
            self._user_info = self._decode_claims(token)
            self.auth_state_changed.emit(True)
            return True
        except Exception as e:
            print(f"Token save error: {e}")
            return False
    
    def set_user_info(self, user_info: Dict[str, Any]) -> None:
        """
        Set user info from login response.
        
        Args:
            user_info: User info dict from login
        """
        self._user_info = user_info
        
        if 'jarvis_token' in user_info:
            self.save_token(user_info['jarvis_token'])
        
        if 'user_id' in user_info:
            self._user_id = user_info['user_id']
    
    def logout(self) -> None:
        """Clear authentication state and delete stored token."""
        if self._token_store_available:
            try:
                self._delete_token()
            except Exception as e:
                print(f"Token delete error: {e}")
        
        self._clear_state()
        self.auth_state_changed.emit(False)
    
    def _clear_state(self) -> None:
        """Clear internal auth state."""
        self._token = None
        self._user_id = None
        self._user_info = None
        self._has_completed_setup = None
    
    def is_authenticated(self) -> bool:
        """
        Check if user is currently authenticated with a valid token.
        
        Returns:
            True if authenticated with non-expired token
        """
        if not self._token:
            return False
        
        if not self._token_store_available:
            return bool(self._token)
        
        try:
            return not self._is_expiring(self._token)
        except Exception:
            return False
    
    def needs_initial_setup(self) -> bool:
        """
        Check if user needs to complete initial setup.
        
        백엔드 API를 호출해서 설정 완료 여부를 확인합니다.
        JWT 토큰에는 이 정보가 포함되지 않을 수 있으므로 API 조회가 필요합니다.
        
        Returns:
            True if setup is required
        """
        # 이미 백엔드에서 조회한 값이 있으면 사용
        if self._has_completed_setup is not None:
            return not self._has_completed_setup
        
        # 토큰과 user_id가 없으면 설정 필요
        if not self._token or not self._user_id:
            return True
        
        # 백엔드 API로 설정 완료 여부 조회
        try:
            response = requests.get(
                f"{API_BASE_URL}/api/v2/dashboard/summary",
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    user_data = data.get("data", {}).get("user", {})
                    self._has_completed_setup = bool(user_data.get("has_completed_setup", False))
                    print(f"✅ 설정 완료 상태 조회: has_completed_setup={self._has_completed_setup}")
                    return not self._has_completed_setup
            
            # API 호출 실패 시, 설문 완료 여부 조회 시도
            survey_response = requests.get(
                f"{API_BASE_URL}/api/v2/user-survey/{self._user_id}/completed",
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=10
            )
            
            if survey_response.status_code == 200:
                survey_data = survey_response.json()
                if survey_data.get("success"):
                    self._has_completed_setup = survey_data.get("completed", False)
                    print(f"✅ 설문 완료 상태 조회: completed={self._has_completed_setup}")
                    return not self._has_completed_setup
        
        except requests.exceptions.Timeout:
            # 타임아웃 시 기존 사용자는 설정 완료로 간주
            print(f"⚠️ 설정 상태 조회 타임아웃 - 기존 사용자로 간주하여 초기 설정 건너뜀")
            self._has_completed_setup = True
            return False
        
        except requests.exceptions.ConnectionError:
            # 연결 오류 시 기존 사용자는 설정 완료로 간주
            print(f"⚠️ 설정 상태 조회 연결 오류 - 기존 사용자로 간주하여 초기 설정 건너뜀")
            self._has_completed_setup = True
            return False
                    
        except Exception as e:
            print(f"⚠️ 설정 상태 조회 실패: {e}")
        
        # 조회 실패 시 user_info에서 확인 (폴백)
        if self._user_info:
            return self._user_info.get("has_completed_setup", 0) == 0
        
        # 토큰이 있는 기존 사용자는 설정 완료로 간주
        print(f"⚠️ 설정 상태 확인 불가 - 기존 사용자로 간주하여 초기 설정 건너뜀")
        return False
    
    def get_token(self) -> Optional[str]:
        """Get current authentication token."""
        return self._token
    
    def get_user_id(self) -> Optional[int]:
        """Get current user ID."""
        return self._user_id
    
    def get_user_info(self) -> Optional[Dict[str, Any]]:
        """Get user info dict."""
        return self._user_info
    
    def get_credentials(self) -> Tuple[Optional[str], Optional[int]]:
        """
        Get token and user_id tuple.
        
        Returns:
            (token, user_id) tuple
        """
        return self._token, self._user_id
