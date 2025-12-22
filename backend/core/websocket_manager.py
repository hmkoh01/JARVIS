"""
WebSocket 연결 관리자
사용자별 WebSocket 연결을 관리하고 실시간 알림을 전송합니다.
"""
import json
import logging
from typing import Dict, List, Any, Optional
from fastapi import WebSocket

logger = logging.getLogger(__name__)


class WebSocketManager:
    """사용자별 WebSocket 연결을 관리하는 싱글톤 클래스"""
    
    def __init__(self):
        # user_id -> WebSocket 연결 목록 (한 사용자가 여러 기기에서 접속 가능)
        self.active_connections: Dict[int, List[WebSocket]] = {}
        # user_id -> 전송 실패한 메시지 큐 (연결이 끊어진 사용자에게 보낼 메시지)
        self.message_queue: Dict[int, List[Dict[str, Any]]] = {}
    
    async def connect(self, websocket: WebSocket, user_id: int):
        """WebSocket 연결 수락 및 등록"""
        await websocket.accept()
        
        if user_id not in self.active_connections:
            self.active_connections[user_id] = []
        
        self.active_connections[user_id].append(websocket)
        logger.info(f"✅ WebSocket 연결됨: user_id={user_id} (현재 연결 수: {len(self.active_connections[user_id])})")
        
        # 재연결 시 큐에 있는 메시지 전송
        if user_id in self.message_queue and self.message_queue[user_id]:
            queued_messages = self.message_queue[user_id].copy()
            self.message_queue[user_id] = []  # 전송 후 큐 비우기
            
            logger.info(f"📬 큐에 저장된 {len(queued_messages)}개 메시지 재전송: user_id={user_id}")
            for message in queued_messages:
                await self.send_to_user(user_id, message)
    
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
        """특정 사용자에게 메시지 전송
        
        연결이 없으면 큐에 저장하여 재연결 시 전송합니다.
        """
        if user_id not in self.active_connections:
            # 연결이 없으면 큐에 저장 (중요한 메시지만)
            msg_type = message.get('type', '')
            if msg_type in ['report_completed', 'report_failed', 'analysis_completed', 'analysis_failed', 'new_recommendation']:
                if user_id not in self.message_queue:
                    self.message_queue[user_id] = []
                self.message_queue[user_id].append(message)
                # 큐 크기 제한 (최근 10개만 유지)
                if len(self.message_queue[user_id]) > 10:
                    self.message_queue[user_id] = self.message_queue[user_id][-10:]
                logger.info(f"💾 메시지 큐에 저장: user_id={user_id}, type={msg_type} (연결 없음)")
            else:
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
        """새로운 추천을 사용자에게 전송
        
        전송 성공 시 추천 상태를 'shown'으로 변경하여 중복 표시를 방지합니다.
        """
        message = {
            "type": "new_recommendation",
            "data": recommendation
        }
        success = await self.send_to_user(user_id, message)
        
        # 전송 성공 시 상태를 'shown'으로 변경 (pending에서 제외)
        if success:
            try:
                from database.sqlite import SQLite
                db = SQLite()
                rec_id = recommendation.get('id')
                rec_user_id = recommendation.get('user_id', user_id)
                if rec_id:
                    db.update_recommendation_status(rec_user_id, rec_id, 'shown')
                    logger.info(f"💡 추천 상태 변경: id={rec_id}, status='shown'")
            except Exception as e:
                logger.warning(f"추천 상태 업데이트 실패: {e}")
        
        return success
    
    async def broadcast_report_completed(
        self, 
        user_id: int, 
        keyword: str, 
        file_path: str, 
        file_name: str,
        sources: List[Dict[str, str]] = None
    ):
        """보고서 생성 완료를 사용자에게 전송
        
        Args:
            user_id: 사용자 ID
            keyword: 보고서 주제 키워드
            file_path: 생성된 파일 경로
            file_name: 파일명
            sources: 출처 목록 (선택)
        """
        from datetime import datetime
        
        message = {
            "type": "report_completed",
            "keyword": keyword,
            "file_path": file_path,
            "file_name": file_name,
            "sources": sources or [],
            "timestamp": datetime.now().isoformat()
        }
        logger.info(f"📄 보고서 완료 알림 전송: user_id={user_id}, keyword={keyword}")
        return await self.send_to_user(user_id, message)
    
    async def broadcast_report_failed(
        self, 
        user_id: int, 
        keyword: str, 
        reason: str
    ):
        """보고서 생성 실패를 사용자에게 전송
        
        Args:
            user_id: 사용자 ID
            keyword: 보고서 주제 키워드
            reason: 실패 사유
        """
        from datetime import datetime
        
        message = {
            "type": "report_failed",
            "keyword": keyword,
            "reason": reason,
            "timestamp": datetime.now().isoformat()
        }
        logger.warning(f"📄 보고서 실패 알림 전송: user_id={user_id}, keyword={keyword}, reason={reason}")
        return await self.send_to_user(user_id, message)
    
    async def broadcast_analysis_completed(
        self, 
        user_id: int, 
        analysis_type: str, 
        title: str,
        analysis_id: Optional[int] = None
    ):
        """대시보드 분석 완료를 사용자에게 전송
        
        Args:
            user_id: 사용자 ID
            analysis_type: 분석 유형
            title: 분석 제목
            analysis_id: 분석 ID (선택)
        """
        from datetime import datetime
        
        message = {
            "type": "analysis_completed",
            "analysis_type": analysis_type,
            "title": title,
            "analysis_id": analysis_id,
            "timestamp": datetime.now().isoformat()
        }
        logger.info(f"📊 분석 완료 알림 전송: user_id={user_id}, title={title}")
        return await self.send_to_user(user_id, message)
    
    async def broadcast_analysis_failed(
        self, 
        user_id: int, 
        analysis_type: str, 
        title: str,
        reason: str
    ):
        """대시보드 분석 실패를 사용자에게 전송
        
        Args:
            user_id: 사용자 ID
            analysis_type: 분석 유형
            title: 분석 제목
            reason: 실패 사유
        """
        from datetime import datetime
        
        message = {
            "type": "analysis_failed",
            "analysis_type": analysis_type,
            "title": title,
            "reason": reason,
            "timestamp": datetime.now().isoformat()
        }
        logger.warning(f"📊 분석 실패 알림 전송: user_id={user_id}, title={title}, reason={reason}")
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

