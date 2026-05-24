"""
모바일 엣지 NLP 인텐트 모델 재학습 모듈 (trainer.py)
─────────────────────────────────────────────────
주말 파라미터 최적화 엔진이 PC 서버(Trainer Node)로 이전됨에 따라,
본 파일은 모바일 엣지(Edge Node)의 로컬 인텐트 분류기(NLP) 재학습 기능만 담당합니다.
"""

import os
import sys
import sqlite3
import joblib
import logging
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.naive_bayes import MultinomialNB
from sklearn.pipeline import Pipeline
import numpy as np

# 경로 설정
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from config import DB_PATH, MODEL_PATH
from local_intent_router import TRAIN_DATA

# 로깅 설정
logger = logging.getLogger("TrainerNLP")

def retrain_model():
    """SQLite DB의 training_data 피드백 데이터를 반영하여 로컬 NLP 인텐트 모델을 재학습합니다."""
    logger.info("[NLP Retrain] 모델 재학습 시작...")
    
    # 1. 기본 학습 데이터 로드
    texts, labels = zip(*TRAIN_DATA)
    texts = list(texts)
    labels = list(labels)
    
    # 기본 데이터 가중치 (1.0)
    weights = [1.0] * len(texts)
    
    # 2. DB 피드백 데이터 수집
    try:
        with sqlite3.connect(DB_PATH, timeout=30.0) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT raw_text, actual_label FROM training_data")
            rows = cursor.fetchall()
            
            # 피드백이 있으면 추가하고 높은 가중치를 주어 우선적으로 학습
            for text, label in rows:
                if text and label:
                    texts.append(text)
                    labels.append(label)
                    weights.append(5.0)  # 사용자 피드백 가중치를 높임
            
            # 3. 모델 정의 및 학습
            new_model = Pipeline([
                ('tfidf', TfidfVectorizer(min_df=1)),
                ('clf', MultinomialNB())
            ])
            
            # sample_weight 전달하여 fit
            new_model.fit(texts, labels, clf__sample_weight=weights)
            
            # 4. 모델 저장
            os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
            joblib.dump(new_model, MODEL_PATH)
            
            # 5. 모든 데이터를 학습 완료 상태로 업데이트
            cursor.execute("UPDATE training_data SET is_trained = 1")
            conn.commit()
            logger.info("[NLP Retrain] 모델 재학습 및 저장 성공!")
            
    except Exception as e:
        logger.error(f"[NLP Retrain] 재학습 중 오류 발생: {e}")

if __name__ == "__main__":
    # 로깅 활성화
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
    retrain_model()
