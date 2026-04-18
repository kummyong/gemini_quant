import sqlite3
import sqlite3
import os
import joblib
import sys
import numpy as np
from datetime import datetime
from sklearn.pipeline import Pipeline
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.naive_bayes import MultinomialNB

# 경로 설정
BASE_DIR = "/root/workspace/gemini-quant/stock_trader"
DB_PATH = os.path.join(BASE_DIR, "logs/system_monitor.db")
MODEL_PATH = os.path.join(BASE_DIR, "logs/intent_model.pkl")

def calculate_weights(dates):
    """현재 시점으로부터 가까운 날짜일수록 높은 가중치 부여"""
    weights = []
    now = datetime.now()
    for date_str in dates:
        try:
            dt = datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S')
            days_diff = (now - dt).days
            # 최근 1일 이내: 2.0배, 7일 이내: 1.5배, 그외: 1.0배 (지수 감쇠 방식 적용 가능)
            if days_diff <= 1: weight = 2.0
            elif days_diff <= 7: weight = 1.5
            elif days_diff <= 30: weight = 1.2
            else: weight = 1.0
            weights.append(weight)
        except:
            weights.append(1.0)
    return np.array(weights)

def retrain_model():
    print("🔄 [Trainer] 시간 가중치 기반 정밀 재학습 시작...")

    if not os.path.exists(DB_PATH): return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        # 모든 유효한 피드백 데이터 로드 (날짜 정보 포함)
        cursor.execute("SELECT raw_text, actual_label, created_at FROM training_data WHERE actual_label IS NOT NULL")
        rows = cursor.fetchall()

        if not rows:
            print("ℹ️ 학습할 데이터가 없습니다.")
            return

        texts = [row[0] for row in rows]
        labels = [row[1] for row in rows]
        dates = [row[2] for row in rows]

        # 1. 가중치 계산
        weights = calculate_weights(dates)

        # 2. 모델 파이프라인 구축
        print(f"🏗️ {len(texts)}개의 데이터를 {np.mean(weights):.2f}배 평균 가중치로 학습 중...")
        new_model = Pipeline([
            ('tfidf', TfidfVectorizer(ngram_range=(1, 2), min_df=1)),
            ('clf', MultinomialNB())
        ])

        # 3. 가중치 적용 학습 (fit 단계에서 sample_weight 전달)
        # Pipeline의 마지막 단계인 'clf'에 가중치 전달
        new_model.fit(texts, labels, clf__sample_weight=weights)

        # 4. 모델 저장
        joblib.dump(new_model, MODEL_PATH)

        # 5. 모든 데이터를 학습 완료 상태로 업데이트
        cursor.execute("UPDATE training_data SET is_trained = 1")
        conn.commit()
        print(f"✅ 학습 완료 및 모델 갱신 성공!")

    except Exception as e:
        print(f"❌ 재학습 중 오류 발생: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    retrain_model()

