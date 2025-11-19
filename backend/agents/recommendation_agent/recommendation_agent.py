import numpy as np
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from collections import Counter
import logging

from ..base_agent import BaseAgent, AgentResponse
from database.sqlite_meta import SQLiteMeta  # 변경됨: SQLAlchemy 대신 SQLiteMeta 사용
from config.settings import settings

logger = logging.getLogger(__name__)

class RecommendationAgent(BaseAgent):
    """추천 및 제안 관련 작업을 처리하는 에이전트"""
    
    def __init__(self):
        super().__init__(
            agent_type="recommendation",
            description="추천, 제안, 추천해줘 등의 요청을 처리합니다."
        )
        self.sqlite_meta = SQLiteMeta()  # SQLite 메타데이터 접근
    
    def process(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """상태를 받아서 처리하고 수정된 상태를 반환합니다."""
        question = state.get("question", "")
        user_id = state.get("user_id")
        
        if not question:
            return {**state, "answer": "질문이 제공되지 않았습니다.", "evidence": []}
        
        try:
            # 사용자 설문지 데이터 가져오기
            survey_data = self._get_user_survey_data(user_id)
            
            # 설문지 데이터를 기반으로 개인화된 추천 생성
            response_content = self._generate_personalized_recommendation(question, survey_data)
            
            return {
                **state,
                "answer": response_content,
                "evidence": [],
                "agent_type": "recommendation",
                "metadata": {
                    "query": question,
                    "user_id": user_id,
                    "agent_type": "recommendation",
                    "survey_data_used": survey_data is not None
                }
            }
        except Exception as e:
            return {
                **state,
                "answer": f"추천 에이전트 처리 중 오류가 발생했습니다: {str(e)}",
                "evidence": [],
                "agent_type": "recommendation"
            }
    
    def _get_user_survey_data(self, user_id: Optional[int]) -> Optional[Dict[str, Any]]:
        """사용자의 설문지 데이터를 가져옵니다."""
        if not user_id:
            return None
        
        try:
            return self.sqlite_meta.get_user_survey_response(user_id)
        except Exception as e:
            print(f"설문지 데이터 조회 오류: {e}")
            return None
    
    def _generate_personalized_recommendation(self, question: str, survey_data: Optional[Dict[str, Any]]) -> str:
        """설문지 데이터를 기반으로 개인화된 추천을 생성합니다."""
        if not survey_data:
            return f"추천 에이전트가 '{question}' 요청을 처리했습니다. 개인화된 추천을 위해 초기 설문지를 완료해주세요."
        
        # 설문지 데이터에서 정보 추출
        job_field = survey_data.get('job_field', '')
        job_field_other = survey_data.get('job_field_other', '')
        interests = survey_data.get('interests', [])
        help_preferences = survey_data.get('help_preferences', [])
        custom_keywords = survey_data.get('custom_keywords', '')
        
        # 직업 분야에 따른 맞춤형 추천
        job_recommendations = self._get_job_based_recommendations(job_field, job_field_other)
        
        # 관심사에 따른 추천
        interest_recommendations = self._get_interest_based_recommendations(interests)
        
        # 도움 받고 싶은 영역에 따른 추천
        help_recommendations = self._get_help_based_recommendations(help_preferences)
        
        # 사용자 정의 키워드 활용
        keyword_recommendations = self._get_keyword_based_recommendations(custom_keywords)
        
        # 모든 추천을 종합하여 응답 생성
        response_parts = []
        
        if job_recommendations:
            response_parts.append(f"📋 {job_field} 분야 관련: {job_recommendations}")
        
        if interest_recommendations:
            response_parts.append(f"🎯 관심사 기반: {interest_recommendations}")
        
        if help_recommendations:
            response_parts.append(f"💡 도움 영역: {help_recommendations}")
        
        if keyword_recommendations:
            response_parts.append(f"🔍 맞춤 키워드: {keyword_recommendations}")
        
        if not response_parts:
            return f"'{question}'에 대한 개인화된 추천을 준비했습니다. 설문지 정보를 바탕으로 맞춤형 제안을 드릴 수 있습니다."
        
        return f"'{question}'에 대한 개인화된 추천입니다:\n\n" + "\n\n".join(response_parts)
    
    def _get_job_based_recommendations(self, job_field: str, job_field_other: str) -> str:
        """직업 분야에 따른 추천을 생성합니다."""
        job_recommendations = {
            "student": "학습 자료, 연구 논문, 학술 자료를 추천드릴 수 있습니다.",
            "developer": "최신 기술 트렌드, 개발 도구, 프로그래밍 자료를 추천드릴 수 있습니다.",
            "designer": "디자인 트렌드, 창작 영감, 디자인 도구를 추천드릴 수 있습니다.",
            "planner": "비즈니스 전략, 마케팅 자료, 기획 도구를 추천드릴 수 있습니다.",
            "researcher": "연구 자료, 학술 논문, 실험 데이터를 추천드릴 수 있습니다.",
            "other": f"'{job_field_other}' 분야에 특화된 자료를 추천드릴 수 있습니다."
        }
        
        return job_recommendations.get(job_field, "전문 분야에 맞는 자료를 추천드릴 수 있습니다.")
    
    async def run_periodic_analysis(self, user_id: int, recommendation_type: str = 'scheduled') -> (bool, str):
        """
        지난 1주일간의 사용자 데이터를 분석하여 추천을 생성합니다.
        :return: (성공 여부, 메시지) 튜플
        """
        logger.info(f"사용자 {user_id}에 대한 주기적 분석 시작 (타입: {recommendation_type})...")
        
        try:
            # 0. 사용자 설문 데이터 미리 조회 (관심사 기반 가중치에 사용)
            survey_data = self._get_user_survey_data(user_id)

            # 1. 지난 1주일 데이터 조회
            one_week_ago = int((datetime.now() - timedelta(days=7)).timestamp())
            files = self.sqlite_meta.get_collected_files_since(user_id, one_week_ago)
            history = self.sqlite_meta.get_collected_browser_history_since(user_id, one_week_ago)
            data_source = "최근 활동"

            # 1차 폴백: 전체 기간 데이터 조회
            if not files and not history:
                logger.info(f"User {user_id}: 지난 1주일간 데이터가 없어 전체 데이터를 조회합니다.")
                files = self.sqlite_meta.get_collected_files(user_id)
                history = self.sqlite_meta.get_collected_browser_history(user_id)
                data_source = "전체 활동"

            # 2. 추천에 사용할 "문서" 리스트 구성 (파일 + 브라우저 + 설문)
            documents: List[str] = []
            
            # 2-1. 파일 이름 / 카테고리 / 내용 프리뷰를 하나의 문서로 결합
            if files:
                for f in files:
                    parts = [
                        f.get('file_name', ''),
                        f.get('file_category', ''),
                        f.get('content_preview', '')
                    ]
                    doc_text = " ".join(p for p in parts if p)
                    if doc_text.strip():
                        documents.append(doc_text)

            # 2-2. 브라우저 기록 제목을 문서로 사용
            if history:
                for h in history:
                    title = h.get('title', '')
                    if title:
                        documents.append(title)

            # 2-3. 2차 폴백: 설문 데이터 기반 문서 구성 (활동 로그가 거의 없을 때)
            if not documents:
                logger.info(f"User {user_id}: 활동 데이터가 없어 설문 데이터로 추천을 생성합니다.")
                if survey_data:
                    data_source = "설문"
                    job_field = survey_data.get('job_field_other', '') or survey_data.get('job_field', '')
                    interests = survey_data.get('interests', [])
                    custom_keywords_str = survey_data.get('custom_keywords', '')

                    # 설문 기반 문서들 구성
                    if job_field:
                        documents.append(str(job_field))
                    for it in interests or []:
                        documents.append(str(it))
                    if custom_keywords_str:
                        documents.append(custom_keywords_str)
            
            if not documents:
                msg = f"User {user_id}: 분석할 데이터가 전혀 없습니다."
                logger.info(msg)
                return False, "분석할 데이터가 부족하여 추천을 생성할 수 없습니다."
                
            # 3. 설문 기반 관심사와의 유사도를 고려한 키워드 선택 (없으면 TF-IDF로 폴백)
            top_keywords = self._select_keywords_by_interest_similarity(documents, survey_data, top_n=5)

            if not top_keywords:
                msg = f"User {user_id}: 주요 활동 주제를 찾지 못했습니다."
                logger.info(msg)
                return False, "데이터에서 주요 활동 주제를 찾지 못했습니다."

            # 4. LLM을 사용해 "추천 키워드" 1개 생성 (핵심 키워드 기반)
            recommended_keyword = self._generate_llm_recommendation_keyword(top_keywords)

            # 5. 추천 생성 및 저장
            title = "활동 요약 및 추천"
            if recommended_keyword:
                content = (
                    f"'{data_source}' 데이터를 분석한 결과, '{', '.join(top_keywords)}' 주제에 많은 관심을 보이셨습니다. "
                    f"이 중에서도 특히 '{recommended_keyword}' 주제를 중심으로 더 깊이 있는 정보를 찾아보거나 "
                    f"새로운 프로젝트를 시작해 보는 것을 추천드립니다."
                )
            else:
                content = (
                    f"'{data_source}' 데이터를 분석한 결과, '{', '.join(top_keywords)}' 주제에 많은 관심을 보이셨습니다. "
                    f"이와 관련하여 더 깊이 있는 정보를 찾아보거나 새로운 프로젝트를 시작해 보는 것은 어떠신가요?"
                )
            
            # TODO: 중복 추천 방지 로직 추가
            
            if self.sqlite_meta.insert_recommendation(user_id, title, content, recommendation_type=recommendation_type):
                logger.info(f"✅ User {user_id}: 새로운 주간 추천을 생성했습니다: {top_keywords}")
                return True, "새로운 추천을 성공적으로 생성했습니다."
            else:
                logger.error(f"❌ User {user_id}: 추천 저장에 실패했습니다.")
                return False, "데이터베이스에 추천을 저장하는 데 실패했습니다."
        except Exception as e:
            logger.error(f"User {user_id} 분석 중 오류: {e}", exc_info=True)
            return False, f"추천 생성 중 오류가 발생했습니다: {e}"

    def _extract_keywords_from_text(self, text: str) -> list:
        """텍스트에서 의미 있는 키워드를 추출합니다. (불용어 처리 강화)"""
        if not text:
            return []
        
        import re
        
        # 1. 텍스트 전처리: 소문자 변환 및 특수문자 제거 (한글, 영문, 숫자, 공백만 유지)
        # URL 제거
        text = re.sub(r'http\S+', '', text)
        processed_text = re.sub(r'[^ \w가-힣]', ' ', text.lower())
        words = processed_text.split()
        
        # 2. 불용어 리스트 확장 (쓰레기 데이터 필터링)
        stopwords = {
            # 일반적인 영어 불용어
            'and', 'the', 'for', 'with', 'this', 'that', 'from', 'to', 'in', 'on', 'at', 'by', 'of', 'is', 'are', 'was', 'were',
            'it', 'its', 'as', 'be', 'have', 'has', 'had', 'do', 'does', 'did', 'can', 'could', 'will', 'would', 'should',
            'about', 'which', 'what', 'when', 'where', 'who', 'how', 'why', 'not', 'no', 'yes', 'or', 'but', 'if', 'so',
            
            # 웹/브라우저 관련 쓰레기 데이터
            'http', 'https', 'www', 'com', 'net', 'org', 'co', 'kr', 'ac', 'io', 'html', 'htm', 'php', 'jsp', 'asp',
            'google', 'naver', 'daum', 'kakao', 'youtube', 'facebook', 'twitter', 'instagram', 'linkedin', 'github',
            'login', 'signin', 'signup', 'logout', 'signout', 'account', 'password', 'id', 'user', 'profile',
            'search', 'query', 'find', 'result', 'results', 'index', 'home', 'main', 'site', 'web', 'page',
            'new', 'tab', 'window', 'untitled', 'loading', 'error', '404', '500', 'server', 'client', 'localhost',
            'docs', 'drive', 'sheet', 'slide', 'document', 'file', 'folder', 'image', 'video', 'audio',
            'pdf', 'doc', 'docx', 'ppt', 'pptx', 'xls', 'xlsx', 'csv', 'txt', 'hwp', 'zip', 'rar', 'tar', 'gz',
            'receipt', 'success', 'final', 'draft', 'copy', 'sample',
            
            # 일반적인 한글 불용어 및 웹 관련
            '및', '위한', '통해', '관련', '대한', '입니다', '으로', '에서', '하고', '있는', '하는', '되는',
            '구글', '네이버', '다음', '카카오', '유튜브', '페이스북', '트위터', '인스타그램', '링크드인', '깃허브',
            '로그인', '회원가입', '로그아웃', '계정', '비밀번호', '아이디', '사용자', '프로필', '내정보',
            '검색', '통합검색', '결과', '메인', '홈', '사이트', '웹페이지', '페이지', '새탭', '무제',
            '로딩중', '오류', '에러', '서버', '클라이언트', '파일', '폴더', '문서', '이미지', '동영상',
            '저장', '열기', '닫기', '수정', '삭제', '취소', '확인', '완료', '설정', '관리', '보기', '더보기',
            '성공', '인증', '결제', '영수증', '최종', '사본', '임시', '백업', '검토', '초안', '샘플'
        }
        
        # 파일 확장자/형식, 이메일, 무작위 문자열 제거를 위한 패턴
        file_ext_pattern = re.compile(r'\.(pdf|docx?|pptx?|xlsx?|xls|csv|md|txt|jpg|jpeg|png|gif|zip|rar|hwp)$', re.IGNORECASE)
        email_pattern = re.compile(r'^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$')
        random_string_pattern = re.compile(r'^[a-zA-Z0-9]{6,}$')
        alpha_pattern = re.compile(r'^[a-zA-Z가-힣]{2,}$')

        keywords = []
        for word in words:
            # 2글자 미만 제외
            if len(word) < 2:
                continue
                
            # 숫자만 있는 경우 제외
            if word.isdigit():
                continue
                
            # 불용어 제외
            if word in stopwords:
                continue
                
            # 한글 조사/어미 간단 제거 (끝글자 기반)
            # 완벽하진 않지만 '데이터를' -> '데이터' 정도로 정제
            if re.match(r'[가-힣]+', word):
                original_word = word
                # 흔한 조사들
                josa_list = ['은', '는', '이', '가', '을', '를', '에', '의', '로', '으로', '과', '와', '도', '만', '서', '께']
                for josa in josa_list:
                    if word.endswith(josa) and len(word) > len(josa) + 1: # 조사 떼고도 2글자 이상일 때만
                        word = word[:-len(josa)]
                        break
                
                # 다시 한번 불용어 체크 (조사 떼고 나니 불용어일 수 있음)
                if word in stopwords:
                    continue
            
            # 파일 확장자/형식 제거
            if file_ext_pattern.search(word):
                continue

            # 이메일 주소 제거
            if email_pattern.match(word):
                continue

            # 무작위 문자열(영문/숫자 혼합 6자 이상) 제거
            if random_string_pattern.match(word) and not alpha_pattern.match(word):
                continue

            keywords.append(word)
            
        return list(set(keywords))

    def _extract_keywords_tfidf(self, documents: List[str], top_n: int = 30) -> List[str]:
        """
        (폴백용) TF-IDF를 사용하여 문서 집합에서 상위 키워드를 추출합니다.
        신규 로직에서는 `_compute_tfidf_term_scores`를 사용하고,
        이 함수는 설문 데이터가 없거나 유사도 계산이 어려운 경우에만 사용합니다.
        """
        term_scores, _, _ = self._compute_tfidf_term_scores(documents)
        if not term_scores:
            return []
        sorted_terms = sorted(term_scores.items(), key=lambda x: x[1], reverse=True)
        return [t for t, _ in sorted_terms[:top_n]]
    def _compute_tfidf_term_scores(self, documents: List[str]) -> (Dict[str, float], Optional["TfidfVectorizer"], Dict[str, int]):
        """
        전처리된 문서 리스트를 대상으로 TF-IDF를 계산하고,
        각 단어(토큰)별 중요도 점수와 문서 빈도(Doc Frequency)를 반환합니다.
        """
        if not documents:
            return {}, None, {}

        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
        except ImportError:
            logger.warning("scikit-learn이 설치되지 않아 TF-IDF 기반 점수 계산을 사용할 수 없습니다.")
            # 간단한 빈도수 기반으로 대체
            all_tokens: List[str] = []
            for doc in documents:
                all_tokens.extend(self._extract_keywords_from_text(doc))
            counter = Counter(all_tokens)
            scores = {k: float(v) for k, v in counter.items()}
            doc_freqs = {k: len(documents) for k in counter.keys()}
            return scores, None, doc_freqs

        try:
            # 1) 각 문서를 전처리하여 키워드 토큰 문자열로 변환
            processed_docs: List[str] = []
            for doc in documents:
                tokens = self._extract_keywords_from_text(doc)
                if tokens:
                    processed_docs.append(" ".join(tokens))

            if not processed_docs:
                return {}, None, {}

            # 2) TF-IDF 벡터화 (단어 단위)
            vectorizer = TfidfVectorizer(
                token_pattern=r"(?u)\b\w+\b",
                max_features=2000,
                norm="l2",
            )
            tfidf_matrix = vectorizer.fit_transform(processed_docs)
            feature_names = vectorizer.get_feature_names_out()
            doc_freq_arr = (tfidf_matrix > 0).sum(axis=0).A1

            # 3) 각 단어의 전체 문서에서의 중요도 합산
            scores_arr = tfidf_matrix.sum(axis=0).A1
            term_scores = {term: float(score) for term, score in zip(feature_names, scores_arr)}
            doc_freqs = {term: int(df) for term, df in zip(feature_names, doc_freq_arr)}

            return term_scores, vectorizer, doc_freqs

        except Exception as e:
            logger.error(f"TF-IDF 기반 단어 점수 계산 중 오류: {e}", exc_info=True)
            return {}, None, {}

    def _select_keywords_by_interest_similarity(
        self,
        documents: List[str],
        survey_data: Optional[Dict[str, Any]],
        top_n: int = 5
    ) -> List[str]:
        """
        (개선된 버전)
        1) 문서 전체에 대한 TF-IDF 점수(term_scores)를 계산하고,
        2) 설문에서 제공된 관심사 키워드와 각 단어의 코사인 유사도를 계산하여,
        3) weighted_fit_score = 1 * tfidf_score_norm + 9 * cosine_similarity
           를 기준으로 상위 N개 키워드를 선택합니다.
        설문 데이터가 없거나 유사도를 계산할 수 없으면 TF-IDF 결과로 폴백합니다.
        """
        # 1. TF-IDF 기반 단어 중요도 계산
        term_scores, vectorizer, doc_freqs = self._compute_tfidf_term_scores(documents)
        if not term_scores:
            return self._extract_keywords_tfidf(documents, top_n=top_n)

        total_docs = max(len(documents), 1)

        # TF-IDF 상위 일부만 후보로 사용 (너무 많은 단어는 노이즈이므로 제한)
        sorted_terms = sorted(term_scores.items(), key=lambda x: x[1], reverse=True)
        candidate_keywords = []
        for term, _ in sorted_terms:
            df_ratio = doc_freqs.get(term, 0) / total_docs
            if df_ratio >= 0.4:  # 비정상적으로 자주 등장하는 단어 제거
                continue
            candidate_keywords.append(term)
            if len(candidate_keywords) >= 200:
                break

        # 2. 설문 기반 관심사 키워드 추출
        interest_terms: List[str] = []
        if survey_data:
            job_field = survey_data.get('job_field_other', '') or survey_data.get('job_field', '')
            if job_field:
                interest_terms.append(str(job_field))

            interests = survey_data.get('interests', [])
            if isinstance(interests, list):
                for it in interests:
                    if it:
                        interest_terms.append(str(it))

            custom_keywords_str = survey_data.get('custom_keywords', '')
            if custom_keywords_str:
                interest_terms.extend(self._extract_keywords_from_text(custom_keywords_str))

        # 설문 정보가 전혀 없으면 TF-IDF로 폴백
        interest_terms = list({t for t in interest_terms if t})
        if not interest_terms:
            return self._extract_keywords_tfidf(documents, top_n=top_n)

        # vectorizer가 없다면(=빈도 기반 폴백) TF-IDF 순위만 사용
        if vectorizer is None:
            return self._extract_keywords_tfidf(documents, top_n=top_n)

        try:
            # 3. 설문 관심사를 하나의 텍스트로 합쳐 벡터화
            interest_text = " ".join(interest_terms)
            interest_vec = vectorizer.transform([interest_text])  # (1, D)
            if interest_vec.nnz == 0:
                return self._extract_keywords_tfidf(documents, top_n=top_n)

            # TF-IDF 점수 정규화 (0~1 범위)
            max_tfidf = max(term_scores.values()) if term_scores else 1.0
            if max_tfidf == 0:
                max_tfidf = 1.0

            scored_candidates = []
            for term in candidate_keywords:
                base_score = term_scores.get(term, 0.0)
                tfidf_norm = base_score / max_tfidf

                term_vec = vectorizer.transform([term])
                if term_vec.nnz == 0:
                    cosine_sim = 0.0
                else:
                    # norm='l2' 이므로 dot product == cosine similarity
                    cosine_sim = float(term_vec @ interest_vec.T)

                weighted_fit_score = 1.0 * tfidf_norm + 9.0 * cosine_sim
                scored_candidates.append((term, weighted_fit_score))

            # 의미 없는(점수 너무 낮은) 후보 제거
            scored_candidates = [item for item in scored_candidates if item[1] > 0.05]
            if not scored_candidates:
                return self._extract_keywords_tfidf(documents, top_n=top_n)

            scored_candidates.sort(key=lambda x: x[1], reverse=True)
            top = [w for w, _ in scored_candidates[:top_n]]
            return top

        except Exception as e:
            logger.error(f"관심사 기반 가중치 키워드 선택 중 오류: {e}", exc_info=True)
            return self._extract_keywords_tfidf(documents, top_n=top_n)


    def _generate_llm_recommendation_keyword(self, base_keywords: List[str]) -> Optional[str]:
        """
        추출된 핵심 키워드들을 기반으로 LLM(Gemini)을 호출하여
        사용자가 앞으로 더 탐색해 보면 좋을 만한 '추천 키워드' 1개를 생성합니다.
        """
        if not base_keywords:
            return None

        # Gemini API 키가 없으면 스킵
        try:
            import google.generativeai as genai
        except ImportError:
            logger.warning("google-generativeai가 설치되지 않아 LLM 추천 키워드를 생성할 수 없습니다.")
            return None

        if not settings.GEMINI_API_KEY:
            logger.warning("GEMINI_API_KEY가 설정되지 않아 LLM 추천 키워드를 생성할 수 없습니다.")
            return None

        try:
            genai.configure(api_key=settings.GEMINI_API_KEY)
            
            # Safety Settings: 모든 차단 필터 해제
            safety_settings = [
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
            ]

            model = genai.GenerativeModel(
                model_name="gemini-2.5-flash",
                generation_config={
                    "temperature": 0.7,
                    "top_p": 0.8,
                    "top_k": 40,
                    "max_output_tokens": 32,
                    "response_mime_type": "text/plain",
                },
                safety_settings=safety_settings,
            )

            keywords_str = ", ".join(base_keywords)
            prompt = (
                "당신은 사용자의 관심사를 분석하는 분석가입니다.\n"
                f"다음은 사용자가 최근 관심을 보인 키워드 목록입니다: {keywords_str}\n\n"
                "이 키워드들을 종합했을 때, 사용자가 추가로 탐색해보면 좋을 만한 '연관 주제'를 단 한 단어로 추천해 주세요.\n"
                "규칙:\n"
                "1. 반드시 한국어 단어 하나만 출력하세요.\n"
                "2. 부가 설명, 기호, 공백 없이 오직 단어만 출력하세요.\n"
                "3. 선정적이거나 위험한 단어는 제외하고, 학술적/실용적 주제를 우선하세요."
            )

            response = model.generate_content(prompt, request_options={"timeout": 10})

            # Gemini 응답 안전 파싱
            candidates = getattr(response, "candidates", None) or []
            if not candidates:
                # Safety Feedback 등 확인
                feedback = getattr(response, "prompt_feedback", None)
                logger.warning("LLM 추천 키워드 응답이 비어 있습니다. prompt_feedback=%s", feedback)
                return None

            candidate = candidates[0]
            finish_reason = getattr(candidate, "finish_reason", None)
            # 0: FINISH, 1: MAX_TOKENS, 2: SAFETY, 3: RECITATION, 4: OTHER
            # SAFETY(2) 등으로 막혀도 혹시나 텍스트가 일부라도 있으면 가져오도록 시도할 수 있으나,
            # 보통 막히면 parts가 아예 없음. 로그만 남기고 리턴.
            if finish_reason not in (None, 0, 1):  # MAX_TOKENS까지는 허용
                logger.warning("LLM 응답이 finish_reason=%s 로 종료되었습니다. (Safety Filter 등)", finish_reason)
                # 강제 반환하지 않고 아래 파싱 로직 시도 (혹시 모를 텍스트 존재 가능성)
            
            content_parts = getattr(getattr(candidate, "content", None), "parts", None) or []
            extracted_chunks: List[str] = []
            for part in content_parts:
                text_chunk = getattr(part, "text", None)
                if text_chunk:
                    extracted_chunks.append(text_chunk)

            if not extracted_chunks:
                # fallback: response.text accessor (예외 방지)
                try:
                    fallback_text = (response.text or "").strip()
                except Exception:
                    fallback_text = ""
                if fallback_text:
                    extracted_chunks.append(fallback_text)

            if not extracted_chunks:
                return None

            text = "\n".join(extracted_chunks).strip()
            if not text:
                return None

            # 첫 줄만 사용하고, 양쪽 공백 및 따옴표 제거
            keyword = text.splitlines()[0].strip().strip("\"'“”'‘’")
            # 너무 길거나 이상한 경우는 무시
            if not keyword or len(keyword) > 20:
                return None
            return keyword

        except Exception as e:
            logger.error(f"LLM 추천 키워드 생성 중 오류: {e}", exc_info=True)
            return None

    def _get_interest_based_recommendations(self, interests: List[str]) -> str:
        """관심사에 따른 추천을 생성합니다."""
        if not interests:
            return ""
        
        interest_mapping = {
            "tech": "최신 IT 기술, 프로그래밍, 소프트웨어 개발",
            "finance": "경제 동향, 투자 정보, 금융 뉴스",
            "ai": "인공지능 연구, 머신러닝, 데이터 사이언스",
            "design": "디자인 트렌드, 창작 영감, 예술 작품",
            "marketing": "마케팅 전략, 브랜딩, 광고 캠페인",
            "productivity": "생산성 도구, 시간 관리, 자기계발",
            "health": "건강 정보, 운동 루틴, 웰빙 팁",
            "travel": "여행 정보, 문화 체험, 관광지"
        }
        
        recommendations = [interest_mapping.get(interest, interest) for interest in interests]
        return f"관심 주제 '{', '.join(recommendations)}'에 관련된 자료를 추천드릴 수 있습니다."
    
    def _get_help_based_recommendations(self, help_preferences: List[str]) -> str:
        """도움 받고 싶은 영역에 따른 추천을 생성합니다."""
        if not help_preferences:
            return ""
        
        help_mapping = {
            "work_search": "업무 관련 정보 검색 및 요약 도구",
            "inspiration": "창의적 아이디어와 영감을 주는 자료",
            "writing": "글쓰기 보조 도구와 템플릿",
            "learning": "개인 학습을 위한 교육 자료와 강의"
        }
        
        recommendations = [help_mapping.get(pref, pref) for pref in help_preferences]
        return f"'{', '.join(recommendations)}' 영역에서 도움을 드릴 수 있습니다."
    
    def _get_keyword_based_recommendations(self, custom_keywords: str) -> str:
        """사용자 정의 키워드에 따른 추천을 생성합니다."""
        if not custom_keywords:
            return ""
        
        return f"'{custom_keywords}'와 관련된 맞춤형 자료를 추천드릴 수 있습니다."
    
    async def process_async(self, user_input: str, user_id: Optional[int] = None) -> AgentResponse:
        """사용자 입력을 처리합니다. (기존 호환성을 위한 메서드)"""
        try:
            # 간단한 추천 관련 응답
            response_content = f"추천 에이전트가 '{user_input}' 요청을 처리했습니다. 현재는 기본 응답만 제공합니다."
            
            return AgentResponse(
                success=True,
                content=response_content,
                agent_type=self.agent_type,
                metadata={
                    "query": user_input,
                    "user_id": user_id,
                    "agent_type": "recommendation"
                }
            )
        except Exception as e:
            return AgentResponse(
                success=False,
                content=f"추천 에이전트 처리 중 오류가 발생했습니다: {str(e)}",
                agent_type=self.agent_type
            )
    
    def _analyze_recommendation_type(self, user_input: str) -> str:
        """추천 타입을 분석합니다."""
        input_lower = user_input.lower()
        
        if any(word in input_lower for word in ["지식", "knowledge", "정보"]):
            return "knowledge"
        elif any(word in input_lower for word in ["콘텐츠", "content", "자료"]):
            return "content"
        elif any(word in input_lower for word in ["학습", "learning", "경로", "path"]):
            return "learning_path"
        else:
            return "knowledge"
    
    async def _recommend_knowledge(self, user_id: int, user_input: str) -> AgentResponse:
        """지식 기반 추천을 생성합니다."""
        try:
            # SQLite에서 사용자 데이터 조회
            collected_files = self.sqlite_meta.get_collected_files(user_id)
            collected_browser = self.sqlite_meta.get_collected_browser_history(user_id)
            collected_apps = self.sqlite_meta.get_collected_apps(user_id)
            
            # 사용자 관심사 추출 (간단한 방법)
            interests = self._extract_interests_from_data(collected_files, collected_browser, collected_apps)
            
            # 기본 추천 로직
            recommendations = self._generate_basic_recommendations(interests, user_input)
            
            return AgentResponse(
                success=True,
                content=f"추천 결과: {recommendations}",
                agent_type=self.agent_type,
                metadata={"user_id": user_id, "interests": interests}
            )
            
        except Exception as e:
            return AgentResponse(
                success=False,
                content=f"지식 추천 중 오류: {str(e)}",
                agent_type=self.agent_type
            )
    
    async def _recommend_content(self, user_id: int, user_input: str) -> AgentResponse:
        """콘텐츠 추천을 생성합니다."""
        try:
            # 사용자 관심사 기반으로 웹 검색
            interests = await self._get_user_interests(user_id)
            
            if not interests:
                return AgentResponse(
                    success=True,
                    content="사용자 관심사를 파악할 수 없어 추천을 생성할 수 없습니다.",
                    agent_type=self.agent_type
                )
            
            # 관심사별로 웹 검색
            recommendations = []
            for interest in interests[:3]:  # 상위 3개 관심사만 사용
                search_result = await self.execute_tool(
                    "web_search_tool",
                    query=f"{interest} 관련 최신 정보",
                    max_results=2
                )
                
                if search_result.success:
                    for item in search_result.data:
                        recommendations.append({
                            "title": item.get("title", ""),
                            "url": item.get("url", ""),
                            "snippet": item.get("snippet", ""),
                            "interest": interest,
                            "type": "web_content"
                        })
            
            return AgentResponse(
                success=True,
                content={
                    "recommendations": recommendations[:10],  # 최대 10개
                    "user_interests": interests
                },
                agent_type=self.agent_type,
                tools_used=["web_search_tool"],
                metadata={"recommendation_type": "content"}
            )
            
        except Exception as e:
            return AgentResponse(
                success=False,
                content=f"콘텐츠 추천 생성 중 오류: {str(e)}",
                agent_type=self.agent_type
            )
    
    async def _recommend_learning_path(self, user_id: int, user_input: str) -> AgentResponse:
        """학습 경로 추천을 생성합니다."""
        try:
            # 사용자 현재 수준과 목표 분석
            user_profile = await self._analyze_user_profile(user_id)
            
            # 학습 경로 생성
            learning_path = self._generate_learning_path(user_profile, user_input)
            
            return AgentResponse(
                success=True,
                content={
                    "learning_path": learning_path,
                    "user_profile": user_profile
                },
                agent_type=self.agent_type,
                metadata={"recommendation_type": "learning_path"}
            )
            
        except Exception as e:
            return AgentResponse(
                success=False,
                content=f"학습 경로 추천 생성 중 오류: {str(e)}",
                agent_type=self.agent_type
            )
    
    def _extract_interests_from_data(self, collected_files: List[Dict[str, Any]], 
                                   collected_browser: List[Dict[str, Any]], 
                                   collected_apps: List[Dict[str, Any]]) -> List[str]:
        """수집된 데이터에서 사용자 관심사를 추출합니다."""
        interests = []
        
        # 파일명에서 관심사 추출
        for file_info in collected_files:
            file_name = file_info.get('file_name', '').lower()
            file_path = file_info.get('file_path', '').lower()
            
            # 일반적인 관심사 키워드
            interest_keywords = [
                "python", "javascript", "java", "c++", "machine learning", "ai", "data science",
                "web development", "mobile development", "database", "cloud", "devops",
                "프로그래밍", "코딩", "개발", "학습", "프로젝트", "알고리즘", "자료구조"
            ]
            
            for keyword in interest_keywords:
                if keyword in file_name or keyword in file_path:
                    interests.append(keyword)
        
        # 브라우저 히스토리에서 관심사 추출
        for browser_info in collected_browser:
            url = browser_info.get('url', '').lower()
            title = browser_info.get('title', '').lower()
            
            for keyword in interest_keywords:
                if keyword in url or keyword in title:
                    interests.append(keyword)
        
        # 앱 사용에서 관심사 추출
        for app_info in collected_apps:
            app_name = app_info.get('app_name', '').lower()
            window_title = app_info.get('window_title', '').lower()
            
            for keyword in interest_keywords:
                if keyword in app_name or keyword in window_title:
                    interests.append(keyword)
        
        # 중복 제거 및 빈도순 정렬
        interest_counts = {}
        for interest in interests:
            interest_counts[interest] = interest_counts.get(interest, 0) + 1
        
        sorted_interests = sorted(interest_counts.items(), key=lambda x: x[1], reverse=True)
        return [interest for interest, count in sorted_interests[:10]]  # 상위 10개
    
    async def _get_user_interests(self, user_id: int) -> List[str]:
        """사용자 관심사를 가져옵니다."""
        try:
            # SQLite에서 사용자 데이터 조회
            collected_files = self.sqlite_meta.get_collected_files(user_id)
            collected_browser = self.sqlite_meta.get_collected_browser_history(user_id)
            collected_apps = self.sqlite_meta.get_collected_apps(user_id)
            
            return self._extract_interests_from_data(collected_files, collected_browser, collected_apps)
        except Exception as e:
            print(f"사용자 관심사 추출 오류: {e}")
            return []
    
    def _calculate_relevance_score(self, item: Dict[str, Any], interests: List[str], user_input: str) -> float:
        """지식 항목의 관련성 점수를 계산합니다."""
        score = 0.0
        
        # 사용자 관심사와의 매칭
        item_content_lower = item.get('content', '').lower()
        item_title_lower = item.get('title', '').lower()
        
        for interest in interests:
            if interest.lower() in item_content_lower:
                score += 2.0
            if interest.lower() in item_title_lower:
                score += 3.0
        
        # 사용자 입력과의 매칭
        user_input_lower = user_input.lower()
        if user_input_lower in item_content_lower:
            score += 1.5
        if user_input_lower in item_title_lower:
            score += 2.0
        
        # 태그 매칭
        tags = item.get('tags', [])
        for tag in tags:
            if tag.lower() in user_input_lower:
                score += 1.0
        
        return score
    
    def _generate_basic_recommendations(self, interests: List[str], user_input: str) -> List[Dict[str, Any]]:
        """기본 추천을 생성합니다."""
        recommendations = []
        
        # 관심사 기반 추천
        for interest in interests[:5]:  # 상위 5개 관심사
            recommendations.append({
                "type": "interest_based",
                "title": f"{interest} 관련 학습 자료",
                "description": f"{interest}에 대한 학습 자료를 추천합니다.",
                "interest": interest,
                "priority": "high"
            })
        
        # 사용자 입력 기반 추천
        if user_input:
            recommendations.append({
                "type": "query_based",
                "title": f"'{user_input}' 관련 추천",
                "description": f"사용자 질문 '{user_input}'에 대한 관련 자료를 추천합니다.",
                "query": user_input,
                "priority": "high"
            })
        
        return recommendations
    
    async def _analyze_user_profile(self, user_id: int) -> Dict[str, Any]:
        """사용자 프로필을 분석합니다."""
        try:
            # SQLite에서 사용자 데이터 조회
            collected_files = self.sqlite_meta.get_collected_files(user_id)
            collected_browser = self.sqlite_meta.get_collected_browser_history(user_id)
            collected_apps = self.sqlite_meta.get_collected_apps(user_id)
            
            # 관심사 추출
            interests = self._extract_interests_from_data(collected_files, collected_browser, collected_apps)
            
            # 간단한 사용자 프로필 생성
            total_interactions = len(collected_files) + len(collected_browser) + len(collected_apps)
            experience_level = self._estimate_experience_level_simple(total_interactions)
            
            return {
                "user_id": user_id,
                "username": f"User_{user_id}",
                "total_interactions": total_interactions,
                "agent_usage": {"general": total_interactions},
                "interests": interests,
                "experience_level": experience_level
            }
        except Exception as e:
            return {
                "user_id": user_id,
                "username": "Unknown",
                "total_interactions": 0,
                "agent_usage": {},
                "interests": [],
                "experience_level": "beginner"
            }
    
    def _estimate_experience_level_simple(self, total_interactions: int) -> str:
        """사용자의 경험 수준을 추정합니다."""
        if total_interactions < 10:
            return "beginner"
        elif total_interactions < 50:
            return "intermediate"
        else:
            return "advanced"
    
    def _generate_learning_path(self, user_profile: Dict[str, Any], user_input: str) -> List[Dict[str, Any]]:
        """학습 경로를 생성합니다."""
        experience_level = user_profile.get("experience_level", "beginner")
        interests = user_profile.get("interests", [])
        
        # 기본 학습 경로 템플릿
        learning_paths = {
            "beginner": [
                {
                    "step": 1,
                    "title": "기초 개념 학습",
                    "description": "프로그래밍의 기본 개념을 이해합니다.",
                    "estimated_time": "2-3주",
                    "resources": ["온라인 튜토리얼", "기초 교재"]
                },
                {
                    "step": 2,
                    "title": "실습 프로젝트",
                    "description": "간단한 프로젝트를 통해 실습합니다.",
                    "estimated_time": "1-2주",
                    "resources": ["미니 프로젝트", "코딩 연습"]
                }
            ],
            "intermediate": [
                {
                    "step": 1,
                    "title": "심화 개념 학습",
                    "description": "고급 프로그래밍 개념을 학습합니다.",
                    "estimated_time": "3-4주",
                    "resources": ["고급 교재", "온라인 강의"]
                },
                {
                    "step": 2,
                    "title": "실무 프로젝트",
                    "description": "실무 수준의 프로젝트를 진행합니다.",
                    "estimated_time": "4-6주",
                    "resources": ["오픈소스 프로젝트", "팀 프로젝트"]
                }
            ],
            "advanced": [
                {
                    "step": 1,
                    "title": "전문 분야 심화",
                    "description": "특정 분야의 전문 지식을 습득합니다.",
                    "estimated_time": "6-8주",
                    "resources": ["전문 서적", "컨퍼런스 참석"]
                },
                {
                    "step": 2,
                    "title": "리더십 및 멘토링",
                    "description": "다른 개발자를 가르치고 리드합니다.",
                    "estimated_time": "지속적",
                    "resources": ["멘토링 프로그램", "기술 블로그 운영"]
                }
            ]
        }
        
        base_path = learning_paths.get(experience_level, learning_paths["beginner"])
        
        # 관심사에 맞게 커스터마이징
        customized_path = []
        for step in base_path:
            customized_step = step.copy()
            if interests:
                customized_step["description"] += f" (관심 분야: {', '.join(interests[:3])})"
            customized_path.append(customized_step)
        
        return customized_path 