import os
import joblib
import re
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.naive_bayes import MultinomialNB
from sklearn.pipeline import Pipeline
import numpy as np

from stock_trader.config import MODEL_PATH

# 초기 학습 데이터 (함수 호출 매핑)
# 라벨: get_account_summary, get_balance, place_order, search_history, get_system_status, get_stock_price, switch_ai_model
import json
from stock_trader.config import PROJECT_DIR
import os

_DATA_PATH = os.path.join(PROJECT_DIR, 'stock_trader', 'data', 'intent_train_data.json')
try:
    with open(_DATA_PATH, 'r', encoding='utf-8') as f:
        TRAIN_DATA = json.load(f)
except FileNotFoundError:
    TRAIN_DATA = []


class LocalIntentRouter:
    def __init__(self):
        self.model = None
        # 통합 종목 매핑 사전 사용
        from stock_trader.core.stock_universe import STOCK_MAP
        self.stock_dict = STOCK_MAP
        self._initialize_model()

    def _initialize_model(self):
        if os.path.exists(MODEL_PATH):
            try:
                self.model = joblib.load(MODEL_PATH)
                return
            except: pass

        self.model = Pipeline([
            ('tfidf', TfidfVectorizer(min_df=1)),
            ('clf', MultinomialNB())
        ])
        texts, labels = zip(*TRAIN_DATA)
        self.model.fit(texts, labels)
        os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
        joblib.dump(self.model, MODEL_PATH)

    def extract_params(self, text):
        """로컬 규칙 기반 파라미터 추출 (종목, 수량, 모델명 등)"""
        params = {}
        
        # 1. 지칭어 처리 (이전 맥락 유지 힌트)
        is_referring = any(kw in text for kw in ["그거", "이거", "아까", "방금", "그거말야", "해당"])
        
        # 2. 수량 추출 (숫자 + 주)
        qty_match = re.search(r'(\d+)\s*주', text)
        if qty_match:
            params['quantity'] = int(qty_match.group(1))
        
        # 3. 종목 추출 (사전 기반)
        found_stock = False
        for name, code in self.stock_dict.items():
            if name in text:
                params['stock_code'] = code
                found_stock = True
                break
        
        # 만약 지칭어를 썼는데 새로운 종목이 없다면, 이전 종목을 유지하도록 설계됨 (merge_params에서 처리)
        
        # 4. 매수/매도 방향
        if any(kw in text for kw in ["사", "매수", "담아", "롱", "질러"]):
            params['side'] = "BUY"
        elif any(kw in text for kw in ["팔", "매도", "손절", "익절", "정리", "숏", "비워"]):
            params['side'] = "SELL"
            
        # 5. 모델명 추출 (switch_ai_model)
        if "프로" in text or "pro" in text:
            params['model_name'] = "models/gemini-1.5-pro"
        elif "플래시" in text or "flash" in text or "2.0" in text:
            params['model_name'] = "models/gemini-2.0-flash"
        elif "라이트" in text or "lite" in text:
            params['model_name'] = "models/gemini-2.0-flash-lite"
            
        return params

    def predict(self, text: str):
        """의도 예측 및 신뢰도 반환 (기본)"""
        top_intents = self.predict_top_n(text, n=1)
        intent, confidence = top_intents[0]
        params = self.extract_params(text)
        return intent, params, confidence

    def predict_top_n(self, text: str, n: int = 3):
        """확신도 기준 상위 N개의 인텐트와 확률 리스트 반환"""
        probs = self.model.predict_proba([text])[0]
        classes = self.model.classes_
        
        # 확률 순 정렬
        sorted_indices = np.argsort(probs)[::-1]
        
        results = []
        for i in range(min(n, len(classes))):
            idx = sorted_indices[i]
            results.append((classes[idx], probs[idx]))
            
        return results

router = LocalIntentRouter()

def get_local_decision(text: str):
    return router.predict(text)

def get_top_n_decisions(text: str, n: int = 3):
    """상위 N개 선택지 제공용"""
    return router.predict_top_n(text, n)
