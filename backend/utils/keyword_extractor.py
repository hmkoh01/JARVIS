"""
KeyBERT 기반 키워드 추출기 모듈

Required packages:
    pip install keybert sentence-transformers

Uses 'paraphrase-multilingual-MiniLM-L12-v2' for Korean & English support.

Features:
    - 긴 텍스트 자동 청킹 (BERT 512 토큰 제한 대응)
    - 청크별 키워드 추출 후 병합 및 중복 제거
"""
import logging
from typing import List, Tuple, Optional
from collections import defaultdict
import threading

logger = logging.getLogger(__name__)

# BERT 토큰 제한 관련 상수
MAX_CHARS_PER_CHUNK = 2000  # 대략 512 토큰에 해당 (한글 기준 보수적 추정)
CHUNK_OVERLAP_CHARS = 200  # 청크 간 오버랩

# 싱글톤 인스턴스 및 락
_keyword_extractor_instance: Optional['KeywordExtractor'] = None
_keyword_extractor_lock = threading.Lock()


class KeywordExtractor:
    """
    KeyBERT 기반 키워드 추출기 (Singleton Pattern)
    
    한국어와 영어를 모두 지원하는 다국어 모델을 사용합니다.
    모델 로딩 시간이 걸리므로 싱글톤 패턴으로 인스턴스를 재사용합니다.
    
    Usage:
        extractor = KeywordExtractor.get_instance()
        keywords = extractor.extract("텍스트 내용", top_n=10)
    """
    
    _MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"
    
    def __init__(self):
        """
        KeyBERT 모델 초기화
        
        Note: 직접 생성하지 말고 get_instance()를 사용하세요.
        """
        self._model = None
        self._initialized = False
        self._init_lock = threading.Lock()
    
    def _ensure_initialized(self):
        """모델이 초기화되지 않았다면 초기화합니다 (Lazy Loading)."""
        if self._initialized:
            return
        
        with self._init_lock:
            if self._initialized:
                return
            
            try:
                from keybert import KeyBERT
                
                logger.info(f"🔑 KeyBERT 모델 로딩 중... ({self._MODEL_NAME})")
                self._model = KeyBERT(model=self._MODEL_NAME)
                self._initialized = True
                logger.info("✅ KeyBERT 모델 초기화 완료")
                
            except ImportError:
                logger.error(
                    "❌ keybert 패키지가 설치되지 않았습니다. "
                    "'pip install keybert sentence-transformers'를 실행하세요."
                )
                raise
            except Exception as e:
                logger.error(f"❌ KeyBERT 모델 초기화 실패: {e}", exc_info=True)
                raise
    
    @classmethod
    def get_instance(cls) -> 'KeywordExtractor':
        """
        싱글톤 인스턴스를 반환합니다.
        
        Returns:
            KeywordExtractor: 싱글톤 인스턴스
        """
        global _keyword_extractor_instance
        
        if _keyword_extractor_instance is None:
            with _keyword_extractor_lock:
                if _keyword_extractor_instance is None:
                    _keyword_extractor_instance = cls()
        
        return _keyword_extractor_instance
    
    def _chunk_text_for_bert(self, text: str) -> List[str]:
        """
        긴 텍스트를 BERT 입력 제한에 맞게 청킹합니다.
        
        Args:
            text: 분할할 텍스트
        
        Returns:
            청크 리스트
        """
        if len(text) <= MAX_CHARS_PER_CHUNK:
            return [text]
        
        chunks = []
        separators = ["\n\n", "\n", ". ", " "]
        
        # 재귀적 분할
        current_pos = 0
        while current_pos < len(text):
            end_pos = min(current_pos + MAX_CHARS_PER_CHUNK, len(text))
            
            if end_pos < len(text):
                # 가장 적절한 분할 지점 찾기
                best_split = end_pos
                for sep in separators:
                    # 청크 크기 내에서 마지막 구분자 위치 찾기
                    search_start = max(current_pos, end_pos - 200)  # 마지막 200자 내에서 검색
                    last_sep = text.rfind(sep, search_start, end_pos)
                    if last_sep > current_pos:
                        best_split = last_sep + len(sep)
                        break
                
                chunk = text[current_pos:best_split].strip()
                if chunk:
                    chunks.append(chunk)
                
                # 오버랩 적용
                current_pos = max(current_pos + 1, best_split - CHUNK_OVERLAP_CHARS)
            else:
                chunk = text[current_pos:end_pos].strip()
                if chunk:
                    chunks.append(chunk)
                break
        
        return chunks
    
    def _merge_keywords(
        self, 
        all_keywords: List[List[Tuple[str, float]]], 
        top_n: int
    ) -> List[Tuple[str, float]]:
        """
        여러 청크에서 추출된 키워드를 병합하고 중복을 제거합니다.
        
        같은 키워드가 여러 청크에서 등장하면 점수를 합산합니다.
        """
        keyword_scores = defaultdict(float)
        keyword_counts = defaultdict(int)
        
        for chunk_keywords in all_keywords:
            for keyword, score in chunk_keywords:
                keyword_lower = keyword.lower()
                keyword_scores[keyword_lower] += score
                keyword_counts[keyword_lower] += 1
        
        # 평균 점수 계산 및 정렬
        merged = []
        for keyword, total_score in keyword_scores.items():
            count = keyword_counts[keyword]
            # 여러 청크에서 등장한 키워드에 약간의 보너스
            avg_score = (total_score / count) * (1 + 0.1 * min(count - 1, 3))
            merged.append((keyword, round(avg_score, 4)))
        
        # 점수순 정렬 후 상위 N개 반환
        merged.sort(key=lambda x: x[1], reverse=True)
        return merged[:top_n]
    
    def extract(
        self,
        text: str,
        top_n: int = 10,
        keyphrase_ngram_range: Tuple[int, int] = (1, 2),
        stop_words: Optional[List[str]] = None,
        use_mmr: bool = True,
        diversity: float = 0.5
    ) -> List[Tuple[str, float]]:
        """
        텍스트에서 핵심 키워드를 추출합니다.
        
        긴 텍스트(약 2000자 초과)는 자동으로 청킹하여 각 청크에서 
        키워드를 추출한 후 병합합니다.
        
        Args:
            text: 키워드를 추출할 텍스트
            top_n: 추출할 키워드 개수 (기본값: 10)
            keyphrase_ngram_range: n-gram 범위 (기본값: (1, 2) - 단어 1~2개)
            stop_words: 제외할 불용어 목록 (기본값: None - 영어 불용어 사용)
            use_mmr: Maximal Marginal Relevance 사용 여부 (다양성 확보)
            diversity: MMR 다양성 계수 (0~1, 높을수록 다양)
        
        Returns:
            List[Tuple[str, float]]: (키워드, 점수) 튜플 리스트
            
        Example:
            >>> extractor = KeywordExtractor.get_instance()
            >>> keywords = extractor.extract("Python은 데이터 분석에 많이 사용됩니다.", top_n=5)
            >>> print(keywords)
            [('Python', 0.82), ('데이터 분석', 0.75), ...]
        """
        # 빈 텍스트 또는 너무 짧은 텍스트 처리
        if not text or len(text.strip()) < 10:
            logger.debug("텍스트가 너무 짧아 키워드 추출을 건너뜁니다.")
            return []
        
        self._ensure_initialized()
        
        try:
            # 기본 불용어 설정 (영어 + 한국어 일부)
            if stop_words is None:
                stop_words = "english"
            
            # 긴 텍스트 청킹
            chunks = self._chunk_text_for_bert(text)
            
            if len(chunks) > 1:
                logger.debug(f"긴 텍스트를 {len(chunks)}개 청크로 분할하여 키워드 추출")
            
            all_keywords = []
            
            for chunk in chunks:
                if len(chunk.strip()) < 10:
                    continue
                
                # KeyBERT 키워드 추출
                if use_mmr:
                    keywords = self._model.extract_keywords(
                        chunk,
                        keyphrase_ngram_range=keyphrase_ngram_range,
                        stop_words=stop_words,
                        top_n=top_n,
                        use_mmr=True,
                        diversity=diversity
                    )
                else:
                    keywords = self._model.extract_keywords(
                        chunk,
                        keyphrase_ngram_range=keyphrase_ngram_range,
                        stop_words=stop_words,
                        top_n=top_n
                    )
                
                # 후처리: 빈 키워드 제거
                processed = []
                for keyword, score in keywords:
                    keyword = keyword.strip()
                    if keyword and len(keyword) >= 2:
                        processed.append((keyword, round(score, 4)))
                
                if processed:
                    all_keywords.append(processed)
            
            # 여러 청크에서 추출된 키워드 병합
            if len(all_keywords) > 1:
                return self._merge_keywords(all_keywords, top_n)
            elif len(all_keywords) == 1:
                return all_keywords[0][:top_n]
            else:
                return []
            
        except Exception as e:
            logger.error(f"키워드 추출 중 오류 발생: {e}", exc_info=True)
            return []
    
    def extract_simple(self, text: str, top_n: int = 10) -> List[str]:
        """
        텍스트에서 핵심 키워드만 추출합니다 (점수 제외).
        
        Args:
            text: 키워드를 추출할 텍스트
            top_n: 추출할 키워드 개수
        
        Returns:
            List[str]: 키워드 리스트
        """
        keywords_with_scores = self.extract(text, top_n=top_n)
        return [kw for kw, _ in keywords_with_scores]
    
    def is_available(self) -> bool:
        """KeyBERT 모델이 사용 가능한지 확인합니다."""
        try:
            self._ensure_initialized()
            return self._initialized and self._model is not None
        except Exception:
            return False


def get_keyword_extractor() -> KeywordExtractor:
    """
    KeywordExtractor 싱글톤 인스턴스를 반환하는 헬퍼 함수.
    
    Returns:
        KeywordExtractor: 싱글톤 인스턴스
    """
    return KeywordExtractor.get_instance()

