"""
보고서 인덱싱 유틸리티

생성된 보고서 파일을 파싱하여 SQLite와 Qdrant에 저장합니다.
채팅에서 보고서 내용에 대한 질문에 답할 수 있도록 합니다.
"""

import hashlib
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)


def index_report_file(
    file_path: str,
    user_id: int,
    keyword: str,
    repository=None,
    embedder=None
) -> bool:
    """
    생성된 보고서 파일을 파싱하여 SQLite와 Qdrant에 인덱싱합니다.
    
    Args:
        file_path: 보고서 파일 경로 (PDF 또는 Markdown)
        user_id: 사용자 ID
        keyword: 보고서 주제 키워드
        repository: Repository 인스턴스 (None이면 전역 인스턴스 사용)
        embedder: BGEM3Embedder 인스턴스 (None이면 전역 인스턴스 사용)
    
    Returns:
        성공 여부
    """
    try:
        from database.document_parser import DocumentParser
        from database.sqlite import SQLite
        
        # 파일 존재 확인
        if not Path(file_path).exists():
            logger.error(f"보고서 파일이 존재하지 않습니다: {file_path}")
            return False
        
        # doc_id 생성
        doc_id = f"report_{hashlib.md5(file_path.encode()).hexdigest()}"
        
        # 1. SQLite에 파일 정보 저장
        sqlite = SQLite()
        sqlite.upsert_file(
            doc_id=doc_id,
            user_id=user_id,
            file_path=file_path
        )
        logger.info(f"📄 SQLite에 보고서 파일 정보 저장: {doc_id}")
        
        # 2. 문서 파싱 및 청크 분할
        parser = DocumentParser()
        chunk_infos = parser.parse_and_chunk(file_path)
        
        if not chunk_infos:
            logger.warning(f"보고서 파싱 결과가 없습니다: {file_path}")
            return True  # 파일 정보는 저장됨
        
        logger.info(f"📄 보고서 파싱 완료: {len(chunk_infos)}개 청크")
        
        # 3. Repository와 Embedder 가져오기
        if repository is None or embedder is None:
            try:
                # 방법 1: main 모듈에서 전역 인스턴스 가져오기
                import main
                repository = repository or getattr(main, 'global_repository', None)
                embedder = embedder or getattr(main, 'global_embedder', None)
            except Exception as e:
                logger.warning(f"main 모듈에서 전역 인스턴스를 가져올 수 없습니다: {e}")
            
            # 방법 2: 전역 인스턴스가 None이면 새로 초기화
            if repository is None or embedder is None:
                logger.info("전역 Repository/Embedder가 없습니다. 새로 초기화합니다.")
                try:
                    from database.repository import Repository as RepoClass
                    from agents.chatbot_agent.rag.models.bge_m3_embedder import BGEM3Embedder
                    
                    if repository is None:
                        repository = RepoClass()
                        logger.info("✅ Repository 새로 초기화 완료")
                    if embedder is None:
                        embedder = BGEM3Embedder()
                        logger.info("✅ BGEM3Embedder 새로 초기화 완료")
                except Exception as init_error:
                    logger.error(f"Repository/Embedder 초기화 실패: {init_error}")
        
        if repository is None or embedder is None:
            logger.error("Repository 또는 Embedder가 초기화되지 않았습니다.")
            return False
        
        # 4. 청크 메타데이터 준비
        texts = []
        metas = []
        file_name = Path(file_path).name
        
        for chunk in chunk_infos:
            texts.append(chunk['text'])
            metas.append({
                'user_id': user_id,
                'source': 'report',  # 보고서 출처 구분
                'path': file_path,
                'doc_id': doc_id,
                'chunk_id': chunk['chunk_id'],
                'snippet': chunk.get('snippet', chunk['text'][:200]),
                'content': chunk['text'],
                'keyword': keyword,  # 보고서 주제 키워드
                'file_name': file_name
            })
        
        # 5. 임베딩 생성 및 Qdrant 업로드
        logger.info(f"🧠 보고서 {len(texts)}개 청크 임베딩 생성 중...")
        
        embeddings = embedder.encode_documents(texts, batch_size=32)
        dense_vectors = embeddings['dense_vecs'].tolist()
        sparse_vectors = [
            embedder.convert_sparse_to_qdrant_format(lw)
            for lw in embeddings['lexical_weights']
        ]
        
        if repository.qdrant.upsert_vectors(metas, dense_vectors, sparse_vectors):
            logger.info(f"✅ Qdrant에 보고서 청크 {len(texts)}개 인덱싱 완료")
        else:
            logger.error("❌ Qdrant 보고서 인덱싱 실패")
            return False
        
        # 6. 키워드 추출 및 저장 (선택적)
        try:
            from utils.keyword_extractor import get_keyword_extractor
            extractor = get_keyword_extractor()
            
            if extractor:
                # 전체 텍스트에서 키워드 추출
                full_text = " ".join(texts)
                keywords = extractor.extract(full_text, top_n=10)
                
                if keywords:
                    for kw, score in keywords:
                        sqlite.insert_content_keyword(
                            user_id=user_id,
                            source_type='report',
                            source_id=doc_id,
                            keyword=kw,
                            original_text=f"보고서: {keyword}"
                        )
                    logger.info(f"📝 보고서 키워드 {len(keywords)}개 저장")
        except Exception as e:
            logger.warning(f"보고서 키워드 추출 실패 (무시): {e}")
        
        return True
        
    except Exception as e:
        logger.exception(f"보고서 인덱싱 오류: {e}")
        return False


async def index_report_file_async(
    file_path: str,
    user_id: int,
    keyword: str,
    repository=None,
    embedder=None
) -> bool:
    """
    비동기로 보고서 파일을 인덱싱합니다.
    (동기 함수를 별도 스레드에서 실행)
    """
    import asyncio
    import concurrent.futures
    
    loop = asyncio.get_event_loop()
    
    with concurrent.futures.ThreadPoolExecutor() as executor:
        result = await loop.run_in_executor(
            executor,
            lambda: index_report_file(
                file_path=file_path,
                user_id=user_id,
                keyword=keyword,
                repository=repository,
                embedder=embedder
            )
        )
    
    return result

