import os
import yaml
from typing import List, Dict, Any, Tuple
import numpy as np
import torch
from FlagEmbedding import BGEM3FlagModel
import logging
from pathlib import Path
from utils.path_utils import get_config_path, get_model_cache_dir

logger = logging.getLogger(__name__)

# 프로젝트 루트 경로 (EXE 환경 호환)
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent.parent.parent

class BGEM3Embedder:
    """BGE-M3 기반 하이브리드 임베더 (dense + sparse 벡터)"""
    
    def __init__(self, model_name: str = "BAAI/bge-m3", device: str = "cuda", config_path: str = "configs.yaml"):
        """
        BGE-M3 임베더 초기화
        
        Args:
            model_name: Hugging Face 모델 이름
            device: 'cuda' 또는 'cpu'
            config_path: 설정 파일 경로
        """
        # CUDA 사용을 우선시하고, 사용 불가 시 명확한 경고 출력
        if torch.cuda.is_available():
            self.device = "cuda"
            logger.info("✅ CUDA (GPU) 사용이 감지되었습니다.")
        else:
            self.device = "cpu"
            logger.error("!" * 79)
            logger.error("! CUDA를 사용할 수 없어 CPU 모드로 폴백합니다. 임베딩 속도가 매우 느려집니다.")
            logger.error("! NVIDIA 드라이버, CUDA Toolkit, PyTorch(CUDA) 설치를 확인하세요.")
            logger.error("!" * 79)
        self.config = self._load_config(config_path)
        self.dense_dim = self.config.get('embedding', {}).get('dense_dim', 1024)
        self.batch_size = self.config.get('embedding', {}).get('batch_size', 12)
        
        self.model_name = model_name
        self.model = None
        self._load_model()
    
    def _load_config(self, config_path: str) -> dict:
        """설정 파일 로드 (EXE 환경 호환)"""
        try:
            config_file_path = get_config_path(config_path)
            
            if config_file_path.exists():
                with open(config_file_path, 'r', encoding='utf-8') as f:
                    config = yaml.safe_load(f)
                    logger.info(f"BGE-M3 설정 파일 로드 성공: {config_file_path}")
                    return config
            else:
                logger.warning(f"BGE-M3 설정 파일을 찾을 수 없습니다: {config_file_path}")
                return {}
                
        except Exception as e:
            logger.error(f"BGE-M3 설정 로드 오류: {e}")
            return {}

    def _load_model(self):
        """BGE-M3 모델 로드 (최적화된 버전)"""
        try:
            logger.info(f"'{self.model_name}' 모델 로딩 중...")
            
            # 모델이 이미 로드되어 있는지 확인
            if self.model is not None:
                logger.info("BGE-M3 모델이 이미 로드되어 있습니다.")
                return
            
            # BGE-M3 모델 로드
            # use_fp16=True for faster inference
            self.model = BGEM3FlagModel(
                self.model_name,
                use_fp16=True if self.device == "cuda" else False,
                device=self.device
            )
            
            logger.info("✅ BGE-M3 모델 로드 완료")

        except Exception as e:
            logger.error(f"BGE-M3 모델 로드 오류: {e}")
            logger.warning("모델이 로드되지 않았습니다.")
            self.model = None
    
    def encode_queries(self, queries: List[str], batch_size: int = None) -> Dict[str, np.ndarray]:
        """
        쿼리 텍스트를 인코딩 (검색용)
        
        Args:
            queries: 쿼리 텍스트 리스트
            batch_size: 배치 크기
        
        Returns:
            {
                'dense_vecs': np.ndarray shape (len(queries), 1024),
                'sparse_vecs': List[Dict] sparse vectors
            }
        """
        if self.model is None:
            logger.error("모델이 로드되지 않았습니다.")
            raise RuntimeError("BGE-M3 모델이 로드되지 않았습니다.")
        
        if batch_size is None:
            batch_size = self.batch_size
        
        try:
            # BGE-M3의 encode 메서드 사용 (query용)
            embeddings = self.model.encode(
                queries,
                batch_size=batch_size,
                max_length=8192,  # BGE-M3는 최대 8192 토큰 지원
                return_dense=True,
                return_sparse=True,
                return_colbert_vecs=False  # ColBERT는 사용하지 않음
            )
            
            return {
                'dense_vecs': embeddings['dense_vecs'],  # (batch_size, 1024)
                'lexical_weights': embeddings['lexical_weights']  # sparse vectors
            }
            
        except Exception as e:
            logger.error(f"쿼리 인코딩 오류: {e}")
            raise RuntimeError(f"쿼리 인코딩 실패: {e}")
    
    def encode_documents(self, documents: List[str], batch_size: int = None) -> Dict[str, np.ndarray]:
        """
        문서 텍스트를 인코딩 (인덱싱용)
        
        Args:
            documents: 문서 텍스트 리스트
            batch_size: 배치 크기
        
        Returns:
            {
                'dense_vecs': np.ndarray shape (len(documents), 1024),
                'sparse_vecs': List[Dict] sparse vectors
            }
        """
        if self.model is None:
            logger.error("모델이 로드되지 않았습니다.")
            raise RuntimeError("BGE-M3 모델이 로드되지 않았습니다.")
        
        if batch_size is None:
            batch_size = self.batch_size
        
        try:
            # BGE-M3의 encode 메서드 사용 (document용)
            embeddings = self.model.encode(
                documents,
                batch_size=batch_size,
                max_length=8192,
                return_dense=True,
                return_sparse=True,
                return_colbert_vecs=False
            )
            
            return {
                'dense_vecs': embeddings['dense_vecs'],  # (batch_size, 1024)
                'lexical_weights': embeddings['lexical_weights']  # sparse vectors
            }
            
        except Exception as e:
            logger.error(f"문서 인코딩 오류: {e}")
            raise RuntimeError(f"문서 인코딩 실패: {e}")
    
    def encode_single_query(self, query: str) -> Dict[str, Any]:
        """
        단일 쿼리 인코딩
        
        Args:
            query: 쿼리 텍스트
        
        Returns:
            {
                'dense_vec': np.ndarray shape (1024,),
                'sparse_vec': Dict sparse vector
            }
        """
        result = self.encode_queries([query])
        return {
            'dense_vec': result['dense_vecs'][0],
            'sparse_vec': result['lexical_weights'][0] if result['lexical_weights'] else {}
        }
    
    def get_embedding_dim(self) -> int:
        """Dense 임베딩 차원 반환"""
        return self.dense_dim
    
    def convert_sparse_to_qdrant_format(self, sparse_dict: Dict[int, float]) -> Dict[str, Any]:
        """
        BGE-M3 sparse 벡터를 Qdrant 형식으로 변환
        
        Args:
            sparse_dict: {token_id: weight, ...}
        
        Returns:
            {
                'indices': List[int],
                'values': List[float]
            }
        """
        if not sparse_dict:
            return {'indices': [], 'values': []}
        
        indices = list(sparse_dict.keys())
        values = list(sparse_dict.values())
        
        return {
            'indices': indices,
            'values': values
        }

