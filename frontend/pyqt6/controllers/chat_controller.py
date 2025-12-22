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

from models.message import Message
from services.api_client import APIClient, StreamingWorker
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
    - API streaming communication
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
        self._current_worker: Optional[StreamingWorker] = None
        self._message_history: List[Message] = []
        
        # 스트리밍 파서 상태
        self._stream_buffer = ""
        self._current_metadata: Optional[Dict[str, Any]] = None
        
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
        print("[ChatController] WebSocket client signals connected")
    
    # =========================================================================
    # Public Methods
    # =========================================================================
    
    @pyqtSlot(str)
    def send_message(self, text: str):
        """
        Send a message to the API.
        
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
        
        # Start streaming response
        self._chat_widget.start_streaming_response()
        
        # Make API request
        self._current_worker = self._api_client.send_message_streaming(
            message=text,
            on_started=self._on_stream_started,
            on_chunk=self._on_stream_chunk,
            on_completed=self._on_stream_completed,
            on_error=self._on_stream_error
        )
    
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
        if self._current_worker:
            self._current_worker.stop()
            self._current_worker = None
        
        self._is_sending = False
        self.sending_status_changed.emit(False)
        self._chat_widget.complete_streaming()
    
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
    # Streaming Callbacks
    # =========================================================================
    
    @pyqtSlot()
    def _on_stream_started(self):
        """Called when streaming begins."""
        print("[ChatController] Streaming started")
        self._stream_buffer = ""
        self._current_metadata = None
        self._chat_widget.set_status("Receiving...", sending=True)
    
    @pyqtSlot(str)
    def _on_stream_chunk(self, chunk: str):
        """Called for each chunk received. Filters metadata and displays content."""
        # 버퍼에 청크 추가
        self._stream_buffer += chunk
        
        # 메타데이터 마커 처리 및 필터링
        filtered_content = self._parse_and_filter_stream()
        
        if filtered_content:
            self._chat_widget.append_streaming_chunk(filtered_content)
    
    def _parse_and_filter_stream(self) -> str:
        """
        스트리밍 버퍼를 파싱하여 메타데이터를 처리하고 표시할 텍스트만 반환.
        
        백엔드에서 친근한 상태 메시지를 보내므로 복잡한 필터링 불필요.
        ---METADATA_START---{json}---METADATA_END--- 마커만 처리하면 됨.
        
        Returns:
            사용자에게 표시할 텍스트
        """
        result = ""
        
        # 새로운 형식: ---METADATA_START---{json}---METADATA_END---
        while True:
            match = re.search(self.METADATA_PATTERN, self._stream_buffer, re.DOTALL)
            
            if match:
                # 메타데이터 앞의 텍스트를 결과에 추가
                before_metadata = self._stream_buffer[:match.start()]
                if before_metadata:
                    result += before_metadata
                
                # 메타데이터 파싱 (emit은 _on_stream_completed에서 한 번만)
                try:
                    metadata_json = match.group(1).strip()
                    metadata = json.loads(metadata_json)
                    self._current_metadata = metadata
                    print(f"[ChatController] Metadata parsed: {metadata.get('action', 'unknown')}")
                except json.JSONDecodeError as e:
                    print(f"[ChatController] Metadata parse error: {e}, json: {match.group(1)[:100]}")
                
                # 버퍼에서 메타데이터 제거
                self._stream_buffer = self._stream_buffer[match.end():]
            else:
                # 레거시 형식도 확인 (호환성)
                legacy_match = re.search(self.LEGACY_METADATA_PATTERN, self._stream_buffer, re.DOTALL)
                if legacy_match:
                    before_metadata = self._stream_buffer[:legacy_match.start()]
                    if before_metadata:
                        result += before_metadata
                    
                    try:
                        metadata_json = legacy_match.group(1).strip()
                        metadata = json.loads(metadata_json)
                        self._current_metadata = metadata
                        print(f"[ChatController] Legacy metadata parsed: {metadata.get('action', 'unknown')}")
                    except json.JSONDecodeError as e:
                        print(f"[ChatController] Legacy metadata parse error: {e}")
                    
                    self._stream_buffer = self._stream_buffer[legacy_match.end():]
                else:
                    # 메타데이터 시작 마커가 있는지 확인
                    start_marker = "---METADATA_START---"
                    end_marker = "---METADATA_END---"
                    start_idx = self._stream_buffer.find(start_marker)
                    
                    if start_idx != -1:
                        # 시작 마커는 있는데 끝 마커가 없으면 버퍼에 보관 (메타데이터 완성 대기)
                        # 시작 마커 앞의 텍스트만 출력
                        if start_idx > 0:
                            result += self._stream_buffer[:start_idx]
                            self._stream_buffer = self._stream_buffer[start_idx:]
                        # 끝 마커가 올 때까지 대기
                        break
                    else:
                        # 시작 마커가 없으면 불완전한 마커 대비 끝부분만 남김
                        # ---METADATA_START--- 길이가 19자이므로 안전하게 20자 남김
                        marker_buffer_size = 20
                        if len(self._stream_buffer) > marker_buffer_size:
                            result += self._stream_buffer[:-marker_buffer_size]
                            self._stream_buffer = self._stream_buffer[-marker_buffer_size:]
                        break
        
        return result
    
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
    
    @pyqtSlot()
    def _on_stream_completed(self):
        """Called when streaming completes."""
        print("[ChatController] Streaming completed")
        
        # 남은 버퍼에서 메타데이터 추출 및 처리
        if self._stream_buffer.strip():
            remaining = self._stream_buffer
            
            # 새로운 형식의 메타데이터 추출 및 처리
            metadata_match = re.search(self.METADATA_PATTERN, remaining, re.DOTALL)
            if metadata_match:
                try:
                    metadata_json = metadata_match.group(1).strip()
                    metadata = json.loads(metadata_json)
                    self._current_metadata = metadata
                    print(f"[ChatController] Final metadata extracted: {metadata.get('action', 'unknown')}")
                except json.JSONDecodeError as e:
                    print(f"[ChatController] Final metadata parse error: {e}")
            
            # 레거시 형식도 확인
            if not metadata_match:
                legacy_match = re.search(self.LEGACY_METADATA_PATTERN, remaining, re.DOTALL)
                if legacy_match:
                    try:
                        metadata_json = legacy_match.group(1).strip()
                        metadata = json.loads(metadata_json)
                        self._current_metadata = metadata
                        print(f"[ChatController] Final legacy metadata extracted: {metadata.get('action', 'unknown')}")
                    except json.JSONDecodeError as e:
                        print(f"[ChatController] Final legacy metadata parse error: {e}")
            
            # 메타데이터 마커 제거하고 남은 텍스트만 표시
            remaining = re.sub(self.METADATA_PATTERN, '', remaining, flags=re.DOTALL)
            remaining = re.sub(self.LEGACY_METADATA_PATTERN, '', remaining, flags=re.DOTALL)
            remaining = remaining.strip()
            if remaining:
                self._chat_widget.append_streaming_chunk(remaining)
        
        # 버퍼 초기화
        self._stream_buffer = ""
        
        # Get the streaming message and add to history
        full_response_text = ""
        if self._chat_widget._streaming_bubble:
            message = self._chat_widget._streaming_bubble.message
            self._message_history.append(message)
            full_response_text = message.content
        
        self._chat_widget.complete_streaming()
        self._current_worker = None
        self._is_sending = False
        self.sending_status_changed.emit(False)
        
        # 확인이 필요한 메타데이터가 있으면 처리 (버튼 표시)
        if self._current_metadata:
            action = self._current_metadata.get('action', '')
            # request_topic은 버튼 없이 메시지만 표시
            if action in ('confirm_report', 'confirm_analysis', 'confirm_code', 'confirm_dashboard'):
                print(f"[ChatController] Emitting confirm_action_requested for action: {action}")
                self.confirm_action_requested.emit(self._current_metadata)
                self._current_metadata = None  # 중복 emit 방지
                return
            elif action == 'open_file':
                # 코드 파일 생성 완료 - 다운로드 시그널 emit
                file_path = self._current_metadata.get('file_path', '')
                file_name = self._current_metadata.get('file_name', '')
                if file_path and file_name:
                    print(f"[ChatController] Code file ready: {file_name}")
                    self.code_file_ready.emit({
                        'file_path': file_path,
                        'file_name': file_name
                    })
            elif action == 'request_topic':
                print(f"[ChatController] Request topic - no confirmation button needed")
            self._current_metadata = None  # 메타데이터 처리 완료
        
        # 메타데이터가 없으면 텍스트에서 확인 요청 감지
        if full_response_text:
            detected_metadata = self._detect_confirmation_in_text(full_response_text)
            if detected_metadata:
                print(f"[ChatController] Confirmation detected from text: {detected_metadata}")
                self.confirm_action_requested.emit(detected_metadata)
    
    @pyqtSlot(str)
    def _on_stream_error(self, error: str):
        """Called when a streaming error occurs."""
        print(f"[ChatController] Streaming error: {error}")
        self._stream_buffer = ""
        self._current_metadata = None
        self._chat_widget.handle_streaming_error(error)
        self._current_worker = None
        self._is_sending = False
        self.sending_status_changed.emit(False)
    
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
        self.report_notification.emit(notification_data)
        
        # Add system message
        keyword = data.get("keyword", "Report")
        if success:
            self._chat_widget.add_system_message(
                f"📄 Report completed: {keyword}"
            )
        else:
            reason = data.get("reason", "Unknown error")
            self._chat_widget.add_system_message(
                f"❌ Report failed: {keyword} - {reason}"
            )
    
    def _on_analysis_notification(self, success: bool, data: dict):
        """Called for analysis completed/failed notifications."""
        notification_data = {"success": success, **data}
        print(f"[ChatController] Analysis notification: {notification_data}")
        self.analysis_notification.emit(notification_data)
        
        # Add system message
        title = data.get("title", "Analysis")
        if success:
            self._chat_widget.add_system_message(
                f"📊 Analysis completed: {title}"
            )
        else:
            reason = data.get("reason", "Unknown error")
            self._chat_widget.add_system_message(
                f"❌ Analysis failed: {title} - {reason}"
            )
    
    # =========================================================================
    # Lifecycle
    # =========================================================================
    
    def start(self):
        """Start the controller (connect WebSocket, etc.)."""
        if self._ws_manager:
            self._ws_manager.connect()
    
    def stop(self):
        """Stop the controller (disconnect WebSocket, cancel requests)."""
        self.cancel_sending()
        
        if self._ws_manager:
            self._ws_manager.disconnect()
