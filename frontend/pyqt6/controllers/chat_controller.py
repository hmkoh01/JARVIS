"""
JARVIS Chat Controller
Manages chat state, API communication, and WebSocket notifications.

Phase 3: Connects ChatWidget with API and WebSocket services
"""

import json
import re
from typing import Optional, List, Callable, Dict, Any
from datetime import datetime

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from threading import Thread

from models.message import Message
from services.api_client import APIClient
from services.websocket_client import (
    NotificationWebSocket, 
    WebSocketManager, 
    Notification,
    NotificationType
)
from views.chat_widget import ChatWidget


class ChatController(QObject):
    """
    Controller for chat functionality.
    
    Manages:
    - Message history
    - Sending state (prevents duplicate sends)
    - API communication (non-streaming with typing animation)
    - WebSocket notification handling
    - UI updates through ChatWidget
    
    Signals:
        notification_received: Emitted when a notification is received
        recommendation_received: Emitted for new recommendation notifications
        report_notification: Emitted for report completed/failed notifications
        analysis_notification: Emitted for analysis completed/failed notifications
        confirm_action_requested: Emitted when user confirmation is needed
    """
    
    # Notification signals for external handlers (e.g., toast notifications)
    notification_received = pyqtSignal(object)  # Notification object
    recommendation_received = pyqtSignal(dict)
    report_notification = pyqtSignal(dict)  # {success: bool, ...}
    analysis_notification = pyqtSignal(dict)  # {success: bool, ...}
    confirm_action_requested = pyqtSignal(dict)  # {action: str, keyword: str, ...}
    code_file_ready = pyqtSignal(dict)  # {file_path: str, file_name: str} - for code download
    initial_setup_complete = pyqtSignal(dict)  # {file_count, browser_count} - for initial setup completion
    
    # Status signals
    connection_status_changed = pyqtSignal(bool)  # True = connected
    sending_status_changed = pyqtSignal(bool)  # True = sending
    
    # 메타데이터 마커 패턴 (버튼 표시용 - 유일하게 필터링 필요)
    # 새로운 형식: ---METADATA_START---{json}---METADATA_END---
    METADATA_PATTERN = r'---METADATA_START---(.+?)---METADATA_END---'
    # 레거시 형식도 지원 (호환성)
    LEGACY_METADATA_PATTERN = r'---METADATA---\n(.+?)(?:\n|$)'
    
    # 확인 요청 감지 패턴 (채팅 텍스트에서 감지)
    CONFIRMATION_PATTERNS = [
        (r'(.+?)(?:에 대한|에 관한|에 대해|관련)?\s*보고서를?\s*(?:작성|생성)(?:할까요|하시겠습니까|해 드릴까요)\??', 'confirm_report'),
        (r'(.+?)(?:에 대한|에 관한|에 대해|관련)?\s*분석을?\s*(?:시작|진행)(?:할까요|하시겠습니까|해 드릴까요)\??', 'confirm_analysis'),
        (r'(.+?)(?:에 대한|에 관한|에 대해|관련)?\s*코드를?\s*(?:작성|생성)(?:할까요|하시겠습니까|해 드릴까요)\??', 'confirm_code'),
        (r'대시보드\s*분석을?\s*(?:시작|진행|업데이트)(?:할까요|하시겠습니까|해 드릴까요)\??', 'confirm_dashboard'),
    ]
    
    # 룰베이스 응답 패턴 (LLM 없이 직접 응답)
    RULE_BASED_RESPONSES = [
        # 인사말
        (r'^(안녕|하이|헬로|hi|hello|hey)[\s!?\.]*$', [
            "안녕하세요! 😊 무엇을 도와드릴까요?",
            "반갑습니다! 오늘 무엇을 도와드릴까요?",
            "안녕하세요! JARVIS입니다. 어떤 도움이 필요하신가요?"
        ]),
        (r'^(안녕하세요|반갑습니다|반가워)[\s!?\.]*$', [
            "안녕하세요! 😊 오늘 하루도 좋은 하루 되세요! 무엇을 도와드릴까요?",
            "반갑습니다! 무엇이든 물어보세요.",
            "안녕하세요! 어떤 작업을 도와드릴까요?"
        ]),
        # 감사
        (r'^(고마워|감사합니다|감사해요|땡큐|thank|thanks)[\s!?\.]*$', [
            "천만에요! 😊 더 필요한 게 있으시면 말씀해주세요.",
            "도움이 되었다니 기쁘네요! 또 언제든 불러주세요.",
            "별말씀을요! 더 도와드릴 일이 있으면 말씀해주세요."
        ]),
        # 작별
        (r'^(바이|잘\s*가|안녕히|bye|goodbye)[\s!?\.]*$', [
            "안녕히 가세요! 👋 좋은 하루 되세요!",
            "다음에 또 뵙겠습니다! 좋은 하루 보내세요.",
            "네, 안녕히 가세요! 언제든 다시 찾아주세요."
        ]),
        # 자기소개 요청
        (r'^(넌\s*뭐야|너\s*누구|자기\s*소개|뭐\s*할\s*수\s*있어|뭘\s*할\s*수\s*있어|뭐\s*해줄\s*수\s*있어)[\s?]*$', [
            "저는 JARVIS입니다! 🤖\n\n다음과 같은 일을 도와드릴 수 있어요:\n• 📄 **보고서 작성**: 관심 주제에 대한 상세 리포트\n• 💻 **코드 생성**: Python 코드 작성\n• 📊 **데이터 분석**: 수집된 데이터 분석\n• 💬 **질문 답변**: 다양한 질문에 대한 답변\n\n무엇을 도와드릴까요?",
        ]),
        # 상태 확인
        (r'^(어때|기분\s*어때|잘\s*있어|괜찮아)[\s?]*$', [
            "저는 항상 최상의 상태입니다! 😊 무엇을 도와드릴까요?",
            "잘 지내고 있어요! 덕분에 오늘도 열심히 일하고 있습니다.",
            "좋아요! 도움이 필요하시면 말씀해주세요."
        ]),
        # 도움말
        (r'^(도움|도움말|help|헬프)[\s?!]*$', [
            "**JARVIS 도움말** 📖\n\n**사용 가능한 기능:**\n• \"AI 트렌드 보고서 작성해줘\" - 보고서 생성\n• \"데이터 시각화 코드 만들어줘\" - 코드 생성\n• \"내 활동 분석해줘\" - 데이터 분석\n• 일반 질문도 자유롭게 하세요!\n\n**팁:** 추천이 나타나면 버튼을 눌러 빠르게 작업을 시작할 수 있어요!",
        ]),
    ]
    
    def __init__(
        self,
        chat_widget: ChatWidget,
        api_client: APIClient,
        ws_manager: Optional[WebSocketManager] = None,
        parent: Optional[QObject] = None
    ):
        super().__init__(parent)
        
        self._chat_widget = chat_widget
        self._api_client = api_client
        self._ws_manager = ws_manager
        
        self._is_sending = False
        self._current_thread: Optional[Thread] = None
        self._message_history: List[Message] = []
        
        # 스트리밍 관련 멤버 변수
        self._stream_buffer = ""
        self._current_metadata = None
        
        self._setup_connections()
    
    def _setup_connections(self):
        """Set up signal connections."""
        # Connect chat widget message_sent signal
        self._chat_widget.message_sent.connect(self.send_message)
        
        # Connect WebSocket signals if available
        if self._ws_manager:
            self._setup_websocket_connections()
    
    def _setup_websocket_connections(self):
        """Set up WebSocket signal connections."""
        if not self._ws_manager:
            return
        
        ws = self._ws_manager
        
        # Connection status
        ws.connected.connect(self._on_ws_connected)
        ws.disconnected.connect(self._on_ws_disconnected)
        ws.error.connect(self._on_ws_error)
        
        # Notifications
        ws.notification.connect(self._on_notification)
        
        # Try to connect client signals if client already exists
        self._connect_client_signals()
    
    def _connect_client_signals(self):
        """Connect signals from the WebSocket client (called when client is available)."""
        if not self._ws_manager or not self._ws_manager.client:
            return
        
        client = self._ws_manager.client
        
        # Check if already connected to avoid duplicate connections
        # Using try/except to check if already connected
        try:
            client.recommendation_received.disconnect(self._on_recommendation)
        except TypeError:
            pass  # Not connected yet
        
        try:
            client.report_completed.disconnect()
        except TypeError:
            pass
        
        try:
            client.report_failed.disconnect()
        except TypeError:
            pass
        
        try:
            client.analysis_completed.disconnect()
        except TypeError:
            pass
        
        try:
            client.analysis_failed.disconnect()
        except TypeError:
            pass
        
        # Connect signals
        client.recommendation_received.connect(self._on_recommendation)
        client.report_completed.connect(
            lambda d: self._on_report_notification(True, d)
        )
        client.report_failed.connect(
            lambda d: self._on_report_notification(False, d)
        )
        client.analysis_completed.connect(
            lambda d: self._on_analysis_notification(True, d)
        )
        client.analysis_failed.connect(
            lambda d: self._on_analysis_notification(False, d)
        )
        client.initial_setup_complete.connect(self._on_initial_setup_complete)
        print("[ChatController] WebSocket client signals connected")
    
    def _on_initial_setup_complete(self, data: dict):
        """Handle initial setup complete notification from backend."""
        print(f"[ChatController] Initial setup complete: {data}")
        self.initial_setup_complete.emit(data)
    
    # =========================================================================
    # Public Methods
    # =========================================================================
    
    @pyqtSlot(str)
    def send_message(self, text: str):
        """
        Send a message to the API with streaming support.
        
        Args:
            text: The message text to send
        """
        if self._is_sending:
            print("[ChatController] Already sending, ignoring duplicate request")
            return
        
        if not text.strip():
            return
        
        # 룰베이스 응답 체크 (LLM 필요 없는 간단한 응답)
        rule_response = self._check_rule_based_response(text.strip())
        if rule_response:
            print(f"[ChatController] Rule-based response matched")
            self._handle_rule_based_response(text, rule_response)
            return
        
        self._is_sending = True
        self.sending_status_changed.emit(True)
        
        # Add user message to UI
        user_message = self._chat_widget.add_user_message(text)
        self._message_history.append(user_message)
        
        # Show thinking indicator
        self._chat_widget.set_status("생각하고 있어요...", sending=True)
        
        # 스트리밍 상태 초기화
        self._stream_buffer = ""
        self._current_metadata = None
        
        # 스트리밍 버블 시작
        self._chat_widget.start_streaming()
        
        # Make streaming API request
        self._current_thread = self._api_client.send_message(
            message=text,
            on_chunk=self._on_stream_chunk,
            on_completed=self._on_streaming_completed,
            on_error=self._on_response_error
        )
    
    def _on_stream_chunk(self, chunk: str):
        """
        스트리밍 청크를 처리합니다 (백그라운드 스레드에서 호출됨).
        
        Args:
            chunk: 수신된 텍스트 청크
        """
        # 버퍼에 청크 추가
        self._stream_buffer += chunk
        
        # 메타데이터 마커 확인 및 필터링
        clean_chunk = chunk
        
        # 메타데이터 시작 마커가 있으면 저장하고 표시하지 않음
        if '---METADATA_START---' in self._stream_buffer:
            # 메타데이터 추출 시도
            match = re.search(self.METADATA_PATTERN, self._stream_buffer, flags=re.DOTALL)
            if match:
                try:
                    metadata_json = match.group(1)
                    self._current_metadata = json.loads(metadata_json)
                    print(f"[ChatController] 메타데이터 추출: {self._current_metadata.get('action', 'unknown')}")
                except json.JSONDecodeError:
                    pass
                # 메타데이터 부분 제거
                clean_chunk = re.sub(self.METADATA_PATTERN, '', chunk, flags=re.DOTALL)
            elif '---METADATA_START---' in chunk:
                # 메타데이터 시작했지만 아직 완료되지 않음 - 청크 표시 안함
                clean_chunk = chunk.split('---METADATA_START---')[0]
        
        # 레거시 메타데이터 패턴도 처리
        clean_chunk = re.sub(self.LEGACY_METADATA_PATTERN, '', clean_chunk, flags=re.DOTALL)
        
        # UI 스레드에서 버블 업데이트
        if clean_chunk.strip():
            from PyQt6.QtCore import QMetaObject, Qt, Q_ARG
            # QMetaObject.invokeMethod로 UI 스레드에서 실행
            QMetaObject.invokeMethod(
                self._chat_widget, 
                "append_streaming_chunk",
                Qt.ConnectionType.QueuedConnection,
                Q_ARG(str, clean_chunk)
            )
    
    def _on_streaming_completed(self, data: dict):
        """스트리밍 완료 처리 (백그라운드 스레드에서 호출됨)"""
        print(f"[ChatController] Streaming completed")
        
        # 메타데이터를 인스턴스 변수에 저장 (UI 스레드에서 사용)
        self._pending_metadata = self._current_metadata
        print(f"[ChatController] 저장된 메타데이터: {self._pending_metadata}")
        
        # UI 스레드에서 완료 처리 호출
        from PyQt6.QtCore import QMetaObject, Qt
        QMetaObject.invokeMethod(
            self, 
            "_complete_streaming_ui",
            Qt.ConnectionType.QueuedConnection
        )
    
    @pyqtSlot()
    def _complete_streaming_ui(self):
        """UI 스레드에서 스트리밍 완료 처리"""
        print(f"[ChatController] _complete_streaming_ui 실행")
        
        # 버퍼 초기화
        self._stream_buffer = ""
        
        # Get the streaming message and add to history
        if self._chat_widget._streaming_bubble:
            message = self._chat_widget._streaming_bubble.message
            self._message_history.append(message)
        
        # 스트리밍 완료
        self._chat_widget.complete_streaming()
        self._current_thread = None
        self._is_sending = False
        self.sending_status_changed.emit(False)
        self._chat_widget.set_status("Ready")
        
        # 확인이 필요한 메타데이터가 있으면 처리 (버튼 표시)
        metadata_to_process = getattr(self, '_pending_metadata', None)
        print(f"[ChatController] 메타데이터 처리 시작: {metadata_to_process is not None}")
        if metadata_to_process:
            action = metadata_to_process.get('action', '')
            print(f"[ChatController] action: {action}")
            if action in ('confirm_report', 'confirm_analysis', 'confirm_code', 'confirm_dashboard'):
                print(f"[ChatController] Emitting confirm_action_requested for action: {action}")
                self.confirm_action_requested.emit(metadata_to_process)
            elif action == 'open_file':
                # 코드 파일 생성 완료 - 다운로드 시그널 emit
                file_path = metadata_to_process.get('file_path', '')
                file_name = metadata_to_process.get('file_name', '')
                if file_path and file_name:
                    print(f"[ChatController] Code file ready: {file_name}")
                    self.code_file_ready.emit({
                        'file_path': file_path,
                        'file_name': file_name
                    })
        
        # 메타데이터 초기화
        self._current_metadata = None
        self._pending_metadata = None
    
    def _check_rule_based_response(self, text: str) -> Optional[str]:
        """
        Check if the message matches any rule-based response pattern.
        
        Args:
            text: User message text (stripped)
        
        Returns:
            Response string if matched, None otherwise
        """
        import random
        
        text_lower = text.lower()
        
        for pattern, responses in self.RULE_BASED_RESPONSES:
            if re.match(pattern, text_lower, re.IGNORECASE):
                # 여러 응답 중 랜덤 선택
                return random.choice(responses)
        
        return None
    
    def _handle_rule_based_response(self, user_text: str, response: str):
        """
        Handle a rule-based response with typing animation.
        
        Args:
            user_text: Original user message
            response: Pre-defined response text
        """
        # Add user message to UI
        user_message = self._chat_widget.add_user_message(user_text)
        self._message_history.append(user_message)
        
        # Add assistant response with typing animation
        # 짧은 딜레이 후 응답 시작 (더 자연스럽게)
        from PyQt6.QtCore import QTimer
        
        def show_response():
            assistant_message = self._chat_widget.add_assistant_message(
                response,
                typing_animation=True,
                on_complete=None
            )
            self._message_history.append(assistant_message)
        
        QTimer.singleShot(300, show_response)
    
    def cancel_sending(self):
        """Cancel the current message send operation."""
        # Note: Thread cannot be easily cancelled, but we mark as not sending
        self._current_thread = None
        self._is_sending = False
        self.sending_status_changed.emit(False)
        self._chat_widget.set_status("Ready")
    
    def clear_history(self):
        """Clear message history."""
        self._message_history.clear()
        self._chat_widget.clear_messages()
    
    def get_history(self) -> List[Message]:
        """Get message history."""
        return self._message_history.copy()
    
    @property
    def is_sending(self) -> bool:
        """Check if currently sending a message."""
        return self._is_sending
    
    # =========================================================================
    # Continue Agents (Multi-Agent Continuation)
    # =========================================================================
    
    def send_continue_agents_request(self, request_data: dict):
        """
        남은 에이전트들을 실행하기 위해 /continue-agents API를 호출합니다.
        
        Args:
            request_data: {
                'message': '원본 메시지',
                'user_id': 1,
                'remaining_agents': ['coding', ...],
                'sub_tasks': {...},
                'previous_results': [...]
            }
        """
        if self._is_sending:
            print("[ChatController] Already sending, queuing continue-agents request")
            # 현재 작업 완료 후 재시도하기 위해 저장
            if not hasattr(self, '_pending_continue_request'):
                self._pending_continue_request = request_data
            return
        
        self._is_sending = True
        self.sending_status_changed.emit(True)
        
        remaining_agents = request_data.get('remaining_agents', [])
        print(f"[ChatController] Starting continue-agents for: {remaining_agents}")
        
        # 상태 표시
        agent_names = ', '.join(remaining_agents)
        self._chat_widget.set_status(f"{agent_names} 작업 중...", sending=True)
        
        # 백그라운드 스레드에서 스트리밍 요청 실행
        def _run():
            import requests
            import json
            
            try:
                url = f"{self._api_client.base_url}/api/v2/continue-agents"
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self._api_client.token}"
                }
                
                # 스트리밍 요청
                response = requests.post(
                    url,
                    json=request_data,
                    headers=headers,
                    stream=True,
                    timeout=300
                )
                
                if response.status_code == 200:
                    full_content = []
                    extracted_metadata = None
                    
                    for chunk in response.iter_content(chunk_size=None, decode_unicode=True):
                        if chunk:
                            # 메타데이터 추출
                            metadata_match = re.search(self.METADATA_PATTERN, chunk)
                            if metadata_match:
                                try:
                                    extracted_metadata = json.loads(metadata_match.group(1))
                                    print(f"[ChatController] Continue-agents 메타데이터 추출: {extracted_metadata.get('action', 'unknown')}")
                                except json.JSONDecodeError:
                                    pass
                            
                            # 레거시 패턴도 확인
                            legacy_match = re.search(self.LEGACY_METADATA_PATTERN, chunk)
                            if legacy_match and not extracted_metadata:
                                try:
                                    extracted_metadata = json.loads(legacy_match.group(1))
                                    print(f"[ChatController] Continue-agents 레거시 메타데이터 추출: {extracted_metadata.get('action', 'unknown')}")
                                except json.JSONDecodeError:
                                    pass
                            
                            # 메타데이터 마커 필터링
                            clean_chunk = re.sub(self.METADATA_PATTERN, '', chunk, flags=re.DOTALL)
                            clean_chunk = re.sub(self.LEGACY_METADATA_PATTERN, '', clean_chunk, flags=re.DOTALL)
                            
                            if clean_chunk.strip():
                                full_content.append(clean_chunk)
                    
                    content = ''.join(full_content)
                    self._on_continue_agents_completed(content, extracted_metadata)
                else:
                    error_msg = f"API 오류: {response.status_code}"
                    try:
                        error_data = response.json()
                        error_msg = error_data.get("detail", error_msg)
                    except:
                        pass
                    self._on_continue_agents_error(error_msg)
                    
            except Exception as e:
                self._on_continue_agents_error(str(e))
        
        thread = Thread(target=_run, daemon=True)
        thread.start()
    
    def _on_continue_agents_completed(self, content: str, metadata: Optional[Dict[str, Any]] = None):
        """Continue-agents 요청 완료 처리"""
        print(f"[ChatController] Continue-agents completed, content length: {len(content)}")
        if metadata:
            print(f"[ChatController] Continue-agents 메타데이터: {metadata.get('action', 'none')}")
        
        # 컨텐츠와 메타데이터 저장
        self._continue_agents_content = content
        self._continue_agents_metadata = metadata
        
        # UI 스레드에서 실행하기 위해 QMetaObject.invokeMethod 사용
        from PyQt6.QtCore import QMetaObject, Qt, Q_ARG
        QMetaObject.invokeMethod(
            self, "_complete_continue_agents_ui",
            Qt.ConnectionType.QueuedConnection
        )
    
    @pyqtSlot()
    def _complete_continue_agents_ui(self):
        """Continue-agents UI 업데이트 (UI 스레드에서 실행)"""
        print("[ChatController] _complete_continue_agents_ui 실행")
        
        content = getattr(self, '_continue_agents_content', '')
        metadata = getattr(self, '_continue_agents_metadata', None)
        
        if content.strip():
            # 응답 내용을 채팅에 추가 (타이핑 애니메이션)
            assistant_message = self._chat_widget.add_assistant_message(
                content.strip(),
                typing_animation=True,
                on_complete=None
            )
            self._message_history.append(assistant_message)
            print(f"[ChatController] Continue-agents 응답 표시: {len(content)} chars")
        
        self._is_sending = False
        self.sending_status_changed.emit(False)
        self._chat_widget.set_status("Ready")
        
        # 메타데이터 처리 (파일 다운로드 등)
        if metadata:
            action = metadata.get('action', '')
            print(f"[ChatController] Continue-agents action 처리: {action}")
            
            if action == 'open_file':
                # 코드 파일 다운로드 시그널 emit
                file_path = metadata.get('file_path', '')
                file_name = metadata.get('file_name', '')
                if file_path and file_name:
                    print(f"[ChatController] Code file ready from continue-agents: {file_name}")
                    self.code_file_ready.emit({
                        'file_path': file_path,
                        'file_name': file_name
                    })
            elif action in ('confirm_report', 'confirm_analysis', 'confirm_code'):
                # 확인 요청
                self.confirm_action_requested.emit(metadata)
        
        # 대기 중인 continue 요청이 있으면 처리
        if hasattr(self, '_pending_continue_request') and self._pending_continue_request:
            pending = self._pending_continue_request
            self._pending_continue_request = None
            self.send_continue_agents_request(pending)
    
    def _on_continue_agents_error(self, error_msg: str):
        """Continue-agents 요청 오류 처리"""
        print(f"[ChatController] Continue-agents error: {error_msg}")
        
        # 에러 메시지 저장
        self._continue_agents_error = error_msg
        
        # UI 스레드에서 실행
        from PyQt6.QtCore import QMetaObject, Qt
        QMetaObject.invokeMethod(
            self, "_show_continue_agents_error",
            Qt.ConnectionType.QueuedConnection
        )
    
    @pyqtSlot()
    def _show_continue_agents_error(self):
        """Continue-agents 오류 UI 업데이트 (UI 스레드에서 실행)"""
        error_msg = getattr(self, '_continue_agents_error', '알 수 없는 오류')
        
        # 오류 메시지 표시
        self._chat_widget.add_assistant_message(
            f"❌ 추가 작업 중 오류가 발생했어요: {error_msg}",
            typing_animation=True
        )
        
        self._is_sending = False
        self.sending_status_changed.emit(False)
        self._chat_widget.set_status("Ready")
    
    # =========================================================================
    # Non-Streaming Response Callbacks
    # =========================================================================
    
    def _on_response_received(self, data: dict):
        """Called when non-streaming response is received."""
        print(f"[ChatController] Response received")
        
        # Extract content and metadata from response
        content = data.get("content", data.get("response", ""))
        metadata = data.get("metadata", {})
        
        # Clean content - remove metadata markers if present
        content = re.sub(self.METADATA_PATTERN, '', content, flags=re.DOTALL)
        content = re.sub(self.LEGACY_METADATA_PATTERN, '', content, flags=re.DOTALL)
        content = content.strip()
        
        if not content:
            content = "응답을 처리할 수 없습니다."
        
        # Add assistant message with typing animation
        def show_response():
            assistant_message = self._chat_widget.add_assistant_message(
                content,
                typing_animation=True,
                on_complete=lambda: self._handle_response_metadata(metadata, content)
            )
            self._message_history.append(assistant_message)
        
        # 짧은 딜레이 후 응답 표시 (자연스러운 느낌)
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(100, show_response)
        
        self._current_thread = None
        self._is_sending = False
        self.sending_status_changed.emit(False)
        self._chat_widget.set_status("Ready")
    
    def _handle_response_metadata(self, metadata: dict, content: str):
        """Handle metadata after typing animation completes."""
        if metadata:
            action = metadata.get('action', '')
            # request_topic은 버튼 없이 메시지만 표시
            if action in ('confirm_report', 'confirm_analysis', 'confirm_code', 'confirm_dashboard'):
                print(f"[ChatController] Emitting confirm_action_requested for action: {action}")
                self.confirm_action_requested.emit(metadata)
                return
            elif action == 'open_file':
                # 코드 파일 생성 완료 - 다운로드 시그널 emit
                file_path = metadata.get('file_path', '')
                file_name = metadata.get('file_name', '')
                if file_path and file_name:
                    print(f"[ChatController] Code file ready: {file_name}")
                    self.code_file_ready.emit({
                        'file_path': file_path,
                        'file_name': file_name
                    })
            elif action == 'request_topic':
                print(f"[ChatController] Request topic - no confirmation button needed")
        
        # 메타데이터가 없으면 텍스트에서 확인 요청 감지
        if content:
            detected_metadata = self._detect_confirmation_in_text(content)
            if detected_metadata:
                print(f"[ChatController] Confirmation detected from text: {detected_metadata}")
                self.confirm_action_requested.emit(detected_metadata)
    
    def _detect_confirmation_in_text(self, text: str) -> Optional[Dict[str, Any]]:
        """
        채팅 텍스트에서 확인 요청 패턴을 감지합니다.
        
        Args:
            text: 분석할 텍스트
            
        Returns:
            확인 요청 메타데이터 또는 None
        """
        for pattern, action_type in self.CONFIRMATION_PATTERNS:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                # 키워드 추출 (첫 번째 그룹 또는 전체 매치)
                keyword = ""
                if match.groups():
                    keyword = match.group(1).strip()
                    # 불필요한 문자 제거
                    keyword = re.sub(r'^["\'\s]+|["\'\s]+$', '', keyword)
                    keyword = re.sub(r'^(그럼|그러면|네,?\s*)', '', keyword).strip()
                
                if not keyword:
                    keyword = "요청된 작업"
                
                return {
                    'action': action_type,
                    'keyword': keyword,
                    'brief_description': match.group(0),
                    'detected_from_text': True
                }
        return None
    
    def _on_response_error(self, error: str):
        """Called when a response error occurs."""
        print(f"[ChatController] Response error: {error}")
        
        # Add error message to chat
        self._chat_widget.add_assistant_message(
            f"❌ 오류가 발생했어요: {error}",
            typing_animation=False
        )
        
        self._current_thread = None
        self._is_sending = False
        self.sending_status_changed.emit(False)
        self._chat_widget.set_status("Error", connected=False)
    
    # =========================================================================
    # WebSocket Callbacks
    # =========================================================================
    
    @pyqtSlot()
    def _on_ws_connected(self):
        """Called when WebSocket connects."""
        print("[ChatController] WebSocket connected")
        self._chat_widget.set_status("Connected")
        self.connection_status_changed.emit(True)
        
        # Connect client-specific signals now that client exists
        self._connect_client_signals()
    
    @pyqtSlot()
    def _on_ws_disconnected(self):
        """Called when WebSocket disconnects."""
        print("[ChatController] WebSocket disconnected")
        self._chat_widget.set_status("Disconnected", connected=False)
        self.connection_status_changed.emit(False)
    
    @pyqtSlot(str)
    def _on_ws_error(self, error: str):
        """Called on WebSocket error."""
        print(f"[ChatController] WebSocket error: {error}")
    
    @pyqtSlot(object)
    def _on_notification(self, notification: Notification):
        """Called for any WebSocket notification."""
        self.notification_received.emit(notification)
        # Note: recommendation_received signal is handled by client.recommendation_received
        # to avoid duplicate handling
    
    @pyqtSlot(dict)
    def _on_recommendation(self, data: dict):
        """Called for new recommendation notifications."""
        print(f"[ChatController] New recommendation: {data}")
        # Emit signal - app.py will handle showing toast with action buttons
        self.recommendation_received.emit(data)
    
    def _on_report_notification(self, success: bool, data: dict):
        """Called for report completed/failed notifications."""
        notification_data = {"success": success, **data}
        print(f"[ChatController] Report notification: {notification_data}")
        # app.py의 _on_report_notification에서 토스트로 표시하므로 시그널만 emit
        self.report_notification.emit(notification_data)
    
    def _on_analysis_notification(self, success: bool, data: dict):
        """Called for analysis completed/failed notifications."""
        notification_data = {"success": success, **data}
        print(f"[ChatController] Analysis notification: {notification_data}")
        # app.py의 _on_analysis_notification에서 토스트로 표시하므로 시그널만 emit
        self.analysis_notification.emit(notification_data)
    
    # =========================================================================
    # Lifecycle
    # =========================================================================
    
    def start(self):
        """Start the controller (connect WebSocket, etc.)."""
        if self._ws_manager:
            self._ws_manager.connect()
            # WebSocket 연결 후 시그널 연결 (연결 완료 시점에 다시 시도)
            self._connect_client_signals()
            
            # connected 시그널에도 연결 (비동기 연결 대응)
            if self._ws_manager.client:
                try:
                    self._ws_manager.client.connected.disconnect(self._connect_client_signals)
                except TypeError:
                    pass
                self._ws_manager.client.connected.connect(self._connect_client_signals)
    
    def stop(self):
        """Stop the controller (disconnect WebSocket, cancel requests)."""
        self.cancel_sending()
        
        if self._ws_manager:
            self._ws_manager.disconnect()
