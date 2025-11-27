"""
WebSocket 연결 관리자
사용자별 WebSocket 연결을 관리하고 실시간 알림을 전송합니다.
"""
import json
import logging
from typing import Dict, List, Any
from fastapi import WebSocket

logger = logging.getLogger(__name__)


class WebSocketManager:
    """사용자별 WebSocket 연결을 관리하는 싱글톤 클래스"""
    
    def __init__(self):
        # user_id -> WebSocket 연결 목록 (한 사용자가 여러 기기에서 접속 가능)
        self.active_connections: Dict[int, List[WebSocket]] = {}
    
    async def connect(self, websocket: WebSocket, user_id: int):
        """WebSocket 연결 수락 및 등록"""
        await websocket.accept()
        
        if user_id not in self.active_connections:
            self.active_connections[user_id] = []
        
        self.active_connections[user_id].append(websocket)
        logger.info(f"✅ WebSocket 연결됨: user_id={user_id} (현재 연결 수: {len(self.active_connections[user_id])})")
    
    def disconnect(self, websocket: WebSocket, user_id: int):
        """WebSocket 연결 해제"""
        if user_id in self.active_connections:
            if websocket in self.active_connections[user_id]:
                self.active_connections[user_id].remove(websocket)
                logger.info(f"❌ WebSocket 연결 해제: user_id={user_id}")
            
            # 해당 사용자의 연결이 모두 끊어지면 딕셔너리에서 제거
            if not self.active_connections[user_id]:
                del self.active_connections[user_id]
    
    async def send_to_user(self, user_id: int, message: Dict[str, Any]):
        """특정 사용자에게 메시지 전송"""
        if user_id not in self.active_connections:
            logger.debug(f"사용자 {user_id}에게 보낼 활성 WebSocket 연결이 없습니다.")
            return False
        
        message_json = json.dumps(message, ensure_ascii=False)
        disconnected = []
        
        for websocket in self.active_connections[user_id]:
            try:
                await websocket.send_text(message_json)
                logger.info(f"📤 WebSocket 메시지 전송: user_id={user_id}, type={message.get('type')}")
            except Exception as e:
                logger.warning(f"WebSocket 메시지 전송 실패: {e}")
                disconnected.append(websocket)
        
        # 전송 실패한 연결 제거
        for ws in disconnected:
            self.disconnect(ws, user_id)
        
        return True
    
    async def broadcast_recommendation(self, user_id: int, recommendation: Dict[str, Any]):
        """새로운 추천을 사용자에게 전송"""
        message = {
            "type": "new_recommendation",
            "data": recommendation
        }
        return await self.send_to_user(user_id, message)
    
    def is_user_connected(self, user_id: int) -> bool:
        """사용자가 현재 연결되어 있는지 확인"""
        return user_id in self.active_connections and len(self.active_connections[user_id]) > 0
    
    def get_connected_user_count(self) -> int:
        """현재 연결된 사용자 수 반환"""
        return len(self.active_connections)


# 전역 싱글톤 인스턴스
websocket_manager = WebSocketManager()


def get_websocket_manager() -> WebSocketManager:
    """WebSocket 관리자 싱글톤 인스턴스 반환"""
    return websocket_manager

