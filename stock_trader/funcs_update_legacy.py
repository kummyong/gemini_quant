def get_stock_code_by_name(name):
    """종목명으로 종목코드를 검색 (유연한 매핑)"""
    if not name: return None
    clean_name = str(name).replace(" ", "").strip()
    mapping = {
        "삼성전자": "005930", "삼성": "005930",
        "SK하이닉스": "000660", "하이닉스": "000660",
        "현대차": "005380", "현대자동차": "005380",
        "LG에너지솔루션": "373220", "엔솔": "373220",
        "삼성바이오로직스": "207940", "삼바": "207940",
        "기아": "000270", "셀트리온": "068270", "POSCO홀딩스": "005490",
        "포스코홀딩스": "005490", "KB금융": "105560", "신한지주": "055550",
        "NAVER": "035420", "네이버": "035420", "카카오": "035720"
    }
    return mapping.get(clean_name)

def get_intent_hybrid(text):
    """AI(Gemini)와 규칙 기반(Regex)을 혼합한 인텐트 분석기 (v5.4)"""
    load_dotenv(env_path)
    current_key = os.getenv("GEMINI_API_KEY")
    if current_key:
        try:
            prompt = f"사용자 메시지: \"{text}\"\n위 메시지의 의도를 BUY, SELL, ANALYZE, STATUS, SYSINFO, REMIND 중 하나로 분류하고 관련 매개변수를 JSON으로만 응답해.\n- BUY/SELL 시: 'ticker'(6자리 코드), 'name'(종목명), 'qty'(수량) 필수.\n- 삼성전자는 005930, SK하이닉스는 000660 처럼 주식 종목에 맞는 6자리 코드를 반드시 적어줘."
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent?key={current_key}"
            res = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"response_mime_type": "application/json"}}, timeout=10)
            if res.status_code == 200:
                data = json.loads(res.json()['candidates'][0]['content']['parts'][0]['text'])
                intent, params = data.get('intent', 'REMIND'), data.get('params', {})
                if not params.get('ticker') or len(str(params.get('ticker'))) < 6:
                    params['ticker'] = get_stock_code_by_name(params.get('name'))
                return intent, params, params.get('time_str')
        except: pass
    
    # 규칙 기반 폴백
    text_clean = text.lower()
    if any(k in text_clean for k in ["뉴스", "시황", "전략", "브리핑", "레포트", "분석"]): return "ANALYZE", {}, None
    if any(k in text_clean for k in ["status", "잔고", "현황", "계좌", "돈", "자산"]): return "STATUS", {}, None
    if any(k in text_clean for k in ["sysinfo", "서버", "지표", "시스템", "상태", "리소스", "cpu", "메모리"]): return "SYSINFO", {}, None
    m = re.search(r'([가-힣A-Z0-9]+)\s*(?:(?:\()?(\d{6})(?:\))?)?\s*(\d+)\s*주\s*(매수|매도)', text)
    if m:
        name, ticker, qty, act = m.groups()
        if not ticker: ticker = get_stock_code_by_name(name)
        return ("BUY" if "매수" in act else "SELL"), {"name":name, "ticker":ticker, "qty":int(qty)}, None
    return "REMIND", {}, None
