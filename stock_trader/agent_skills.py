import json
import sys
import os
import logging
import sqlite3
import pytz
from datetime import datetime

# 타임존 설정
KST = pytz.timezone('Asia/Seoul')
...
# 경로 설정
BASE_DIR = "/root/workspace/gemini-quant/stock_trader"
sys.path.append(BASE_DIR)
sys.path.append(os.path.join(BASE_DIR, "Kiwoom_MCP_Server"))

# Secretary 경로 추가
SECRETARY_DIR = "/root/workspace/gemini-quant/secretary"
sys.path.append(SECRETARY_DIR)

# DB 경로
DB_PATH = os.path.join(BASE_DIR, "logs/system_monitor.db")

# 모듈 임포트 시도
try:
    from Kiwoom_MCP_Server.kiwoom_mcp import KiwoomConfig, KiwoomApiManager
    kiwoom_config = KiwoomConfig(mode="MOCK")
    kiwoom_api = KiwoomApiManager(kiwoom_config)
except Exception as e:
    logging.error(f"Kiwoom API Load Error: {e}")
    kiwoom_api = None

try:
    import mcp_google_server as secretary
except Exception as e:
    logging.error(f"Secretary Module Load Error: {e}")
    secretary = None

try:
    import system_monitor
except Exception as e:
    logging.error(f"System Monitor Load Error: {e}")
    system_monitor = None

# 가용한 Gemini 모델 리스트
AVAILABLE_MODELS = [
    "models/gemini-2.0-flash",       # (권장) 속도와 지능의 균형
    "models/gemini-2.0-flash-lite",  # 초고속 응답용
    "models/gemini-1.5-pro",         # 복잡한 분석 및 긴 컨텍스트용
    "models/gemini-1.5-flash",       # 가성비 모델
]

def switch_ai_model(model_name: str = None) -> str:
    """사용 중인 Gemini AI 모델을 교체하거나 가용한 모델 리스트를 보여줍니다."""
    if not model_name or model_name.strip() == "":
        models_str = "\n".join([f"- {m}" for m in AVAILABLE_MODELS])
        return f"📋 **사용 가능한 모델 리스트:**\n{models_str}\n\n사용법: /switch_ai_model [모델명]"

    # 리스트에 없는 모델일 경우 검증 (유연성을 위해 포함 여부만 체크)
    clean_name = model_name.strip()
    
    # 접두사 models/ 가 빠진 경우 보완 (검증 로직 개선)
    is_valid_in_list = clean_name in AVAILABLE_MODELS or any(clean_name == m.split("/")[-1] for m in AVAILABLE_MODELS)
    
    if not clean_name.startswith("models/") and not is_valid_in_list:
        # 리스트에 없더라도 기본적으로 models/ 를 붙여서 시도 (안정성)
        clean_name = f"models/{clean_name}"
    elif not clean_name.startswith("models/") and is_valid_in_list:
        # 리스트에 이름만 있는 경우 접두사 보정
        clean_name = f"models/{clean_name}"
        
    # .env 파일 업데이트
    env_path = os.path.join(BASE_DIR, ".env")
    try:
        with open(env_path, 'r') as f:
            lines = f.readlines()
        
        with open(env_path, 'w') as f:
            found = False
            for line in lines:
                if line.startswith("GEMINI_MODEL_NAME="):
                    f.write(f"GEMINI_MODEL_NAME={clean_name}\n")
                    found = True
                else:
                    f.write(line)
            if not found:
                f.write(f"GEMINI_MODEL_NAME={clean_name}\n")
                
        return f"✅ AI 모델이 '{clean_name}'으로 변경되었습니다.\n⚠️ 시스템에 반영하려면 잠시 후 리스너가 자동 재기동될 때까지 기다려주세요."
    except Exception as e:
        return f"❌ 모델 교체 중 오류 발생: {e}"

def get_order_history() -> str:
    """당일 주식 주문 및 체결 내역을 조회하여 텍스트로 반환합니다."""
    if not kiwoom_api: return "Kiwoom API를 사용할 수 없습니다."
    res = kiwoom_api.get_order_history()
    return json.dumps(res, ensure_ascii=False, indent=2)

class ResponseFormatter:
    """데이터를 텔레그램에 적합한 읽기 좋은 텍스트로 변환합니다."""
    
    @staticmethod
    def format_account_summary(data: dict) -> str:
        if not data or "tot_evlt_amt" not in data: return "❌ 계좌 요약 정보를 불러올 수 없습니다."
        
        def to_won(val): return f"{int(val):,}원"
        
        tot_pur = to_won(data.get("tot_pur_amt", 0))
        tot_evlt = to_won(data.get("tot_evlt_amt", 0))
        tot_pl = to_won(data.get("tot_evlt_pl", 0))
        prft_rt = f"{float(data.get('tot_prft_rt', 0)):.2f}%"
        cash = to_won(data.get("prsm_dpst_aset_amt", 0))
        pl_emoji = "📈" if float(data.get("tot_evlt_pl", 0)) >= 0 else "📉"
        
        return (
            "💰 **[계좌 요약 리포트]**\n"
            f"━━━━━━━━━━━━━━\n"
            f"💵 **총 예수금:** {cash}\n"
            f"📊 **총 매입금:** {tot_pur}\n"
            f"✨ **총 평가액:** {tot_evlt}\n"
            f"{pl_emoji} **총 손익:** {tot_pl} ({prft_rt})\n"
            f"━━━━━━━━━━━━━━\n"
            "💡 '잔고 확인'을 입력하시면 종목을 볼 수 있습니다."
        )

    @staticmethod
    def format_balance(data: dict, is_detailed: bool = False) -> str:
        items = data.get("acnt_evlt_remn_indv_tot", [])
        if not items: return "ℹ️ 현재 보유 중인 종목이 없습니다."
        
        report = ["📂 **[보유 종목 현황]**", "━━━━━━━━━━━━━━"]
        for i, item in enumerate(items):
            nm = item.get("stk_nm", "알수없음")
            rt = f"{float(item.get('prft_rt', 0)):.2f}%"
            pl = int(item.get("evltv_prft", 0))
            emoji = "🔴" if pl > 0 else "🔵" if pl < 0 else "⚪"
            
            if is_detailed:
                qty = f"{int(item.get('rmnd_qty', 0)):,}주"
                price = f"{int(item.get('cur_prc', 0)):,}원"
                report.append(f"{i+1}. **{nm}** ({item.get('stk_cd')})")
                report.append(f"   └ {emoji} {qty} | {price} | {rt}")
            else:
                report.append(f"{emoji} **{nm}**: {rt}")
        
        report.append("━━━━━━━━━━━━━━")
        if not is_detailed: report.append("💡 '자세히'를 붙여 말하면 상세 수량을 보여줍니다.")
        return "\n".join(report)

    @staticmethod
    def format_stock_price(data: dict) -> str:
        if not data or "stk_nm" not in data: return "❌ 종목 정보를 찾을 수 없습니다."
        nm = data.get("stk_nm")
        # 현재가가 음수로 들어오는 경우(하락 시)를 대비해 절대값 처리
        cur_prc = abs(int(data.get('cur_prc', 0)))
        prc = f"{cur_prc:,}원"
        
        # 대비 금액 및 등락률 (대비 금액도 절대값으로 표시하여 이모지와 조화)
        raw_diff = int(data.get("diff", 0))
        diff = f"{abs(raw_diff):,}원"
        rt = f"{float(data.get('diff_rt', 0)):.2f}%"
        
        emoji = "🔺" if raw_diff > 0 else "🔻" if raw_diff < 0 else "🔹"
        return f"🔍 **[{nm}] 현재가 정보**\n━━━━━━━━━━━━━━\n💰 **현재가:** {prc}\n{emoji} **대비:** {diff} ({rt})\n━━━━━━━━━━━━━━"

    @staticmethod
    def format_events(events: list) -> str:
        if not events: return "📅 다가오는 일정이 없습니다."
        report = ["📅 **[구글 캘린더 일정]**", "━━━━━━━━━━━━━━"]
        for ev in events:
            time_str = ev.get('start', {}).get('dateTime', ev.get('start', {}).get('date', '알수없음'))
            summary = ev.get('summary', '제목 없음')
            clean_time = time_str[5:16].replace('T', ' ') if len(time_str) > 10 else time_str
            report.append(f"⏰ {clean_time} | **{summary}**")
        report.append("━━━━━━━━━━━━━━")
        return "\n".join(report)

    @staticmethod
    def format_system_status(metrics: dict) -> str:
        if not metrics: return "❌ 시스템 메트릭을 불러올 수 없습니다."
        cpu, mem = metrics.get('cpu_usage', '0'), metrics.get('memory_usage', '0')
        batt = metrics.get('battery_level', '확인불가')
        temp = metrics.get('cpu_temp', 'N/A')
        
        status_emoji = "✅" if float(cpu) < 80 else "⚠️"
        batt_str = f"{batt}" if "%" in str(batt) else f"⚠️ {batt} (권한제한)"
        
        return (
            f"📱 **[시스템/휴대폰 상태]**\n━━━━━━━━━━━━━━\n"
            f"{status_emoji} **CPU:** {cpu}%\n🧠 **메모리:** {mem}%\n🔋 **배터리:** {batt_str}\n🌡️ **온도:** {temp}°C\n"
            f"━━━━━━━━━━━━━━\n🕒 **체크시간:** {datetime.now(KST).strftime('%H:%M:%S')}"
        )

    @staticmethod
    def format_order_result(res: dict) -> str:
        if not res or res.get('rt_cd') != '0':
            msg = res.get('msg1', '주문 실패')
            return f"❌ **[주문 실패]**\n━━━━━━━━━━━━━━\n사유: {msg}\n━━━━━━━━━━━━━━"
        
        return (
            f"✅ **[주문 접수 완료]**\n━━━━━━━━━━━━━━\n"
            f"📦 **종목:** {res.get('stk_nm')} ({res.get('stk_cd')})\n"
            f"🔢 **수량:** {res.get('qty')}주\n"
            f"💰 **가격:** {res.get('price')}원\n"
            f"🏷️ **구분:** {res.get('side')}\n"
            f"━━━━━━━━━━━━━━"
        )

    @staticmethod
    def format_search_history(keyword: str, res: str) -> str:
        return (
            f"🔍 **[검색 결과: {keyword}]**\n━━━━━━━━━━━━━━\n"
            f"{res}\n"
            f"━━━━━━━━━━━━━━"
        )

# --- [Core Skills as Python Functions for Gemini SDK] ---

def get_account_summary(is_detailed: bool = False) -> str:
    """계좌의 예수금, 총 평가금액, 당일 손익 등 요약 정보를 조회하여 텍스트로 반환합니다."""
    if not kiwoom_api: return "Kiwoom API를 사용할 수 없습니다."
    res = kiwoom_api.get_account_summary()
    return ResponseFormatter.format_account_summary(res)

def get_balance(qry_tp: str = "2", is_detailed: bool = False) -> str:
    """보유 중인 종목 현황과 상세 잔고 정보를 조회합니다. qry_tp: 1(합산), 2(개별)"""
    if not kiwoom_api: return "Kiwoom API를 사용할 수 없습니다."
    res = kiwoom_api.get_account_balance(qry_tp)
    return ResponseFormatter.format_balance(res, is_detailed)

def get_stock_price(stock_code: str) -> str:
    """특정 종목의 현재가 및 기본 정보를 조회하여 텍스트로 반환합니다."""
    if not kiwoom_api: return "Kiwoom API를 사용할 수 없습니다."
    res = kiwoom_api.get_stock_info(stock_code)
    return ResponseFormatter.format_stock_price(res)

def place_order(stock_code: str, quantity: int, price: int, side: str, ord_tp: str = "00") -> str:
    """주식을 매수 또는 매도 주문합니다."""
    if not kiwoom_api: return "Kiwoom API를 사용할 수 없습니다."
    res = kiwoom_api.place_order(stock_code=stock_code, quantity=quantity, price=price, side=side, ord_tp=ord_tp)
    return ResponseFormatter.format_order_result(res)

def search_history(keyword: str) -> str:
    """과거 대화 내용이나 기록에서 특정 키워드를 검색합니다."""
    if not secretary: return "Secretary 모듈을 사용할 수 없습니다."
    res = secretary.search_history(keyword=keyword)
    return ResponseFormatter.format_search_history(keyword, res)

def list_google_events(max_results: int = 5) -> str:
    """구글 캘린더에서 다가오는 일정을 조회합니다."""
    if not secretary: return "Secretary 모듈을 사용할 수 없습니다."
    res = secretary.list_google_events(max_results=max_results)
    if isinstance(res, list): return ResponseFormatter.format_events(res)
    return str(res)

def get_help(**kwargs) -> str:
    """Gemini Quant 비서의 주요 기능과 사용 가능한 명령어를 안내합니다."""
    help_text = (
        "🤖 **[Gemini Quant 비서 도움말]**\n"
        "━━━━━━━━━━━━━━\n"
        "저는 주식 거래와 일정 관리를 돕는 AI 비서입니다.\n\n"
        "📌 **주요 기능 (대화형):**\n"
        "- \"삼성전자 주가 알려줘\"\n"
        "- \"현재 내 계좌 상태는?\"\n"
        "- \"내일 일정 확인해줘\"\n"
        "- \"폰 상태 어때?\"\n\n"
        "⚡ **직속 명령어 (Slash Commands):**\n"
        "- `/help` : 이 도움말을 출력합니다.\n"
        "- `/sysinfo` : 시스템/폰 상태를 즉시 조회합니다.\n"
        "- `/balance` : 현재 잔고 현황을 보여줍니다.\n"
        "- `/summary` : 계좌 요약을 보여줍니다.\n"
        "━━━━━━━━━━━━━━"
    )
    return help_text

def get_system_status(**kwargs) -> str:
    """서버 시스템의 상태(CPU 부하, 메모리 사용량 등)를 조회합니다."""
    if not system_monitor: return "System Monitor를 사용할 수 없습니다."
    metrics = system_monitor.get_system_metrics()
    return ResponseFormatter.format_system_status(metrics)

def save_voc_request(raw_text: str) -> str:
    """사용자의 새로운 기능 요청을 VOC로 저장합니다."""
    try:
        now_kst = datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO voc_requests (raw_text, created_at)
            VALUES (?, ?)
        """, (raw_text, now_kst))
        conn.commit()
        conn.close()
        return f"📝 VOC 등록 완료: '{raw_text}'"
    except Exception as e:
        return f"❌ VOC 저장 중 오류 발생: {e}"

def save_training_feedback(raw_text: str, predicted_label: str, actual_label: str, confidence: float = 1.0) -> str:
    """
    사용자의 피드백이나 정정 사항을 학습 데이터베이스에 저장합니다. (강화학습용)
    - raw_text: 사용자의 원문 메시지
    - predicted_label: AI가 처음에 예측했던 의도나 행동
    - actual_label: 사용자가 수정한 실제 정답이나 의도
    - confidence: 예측에 대한 확신도
    """
    try:
        now_kst = datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO training_data (raw_text, predicted_label, actual_label, confidence, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (raw_text, predicted_label, actual_label, confidence, now_kst))
        conn.commit()
        conn.close()
        return f"✅ 피드백이 학습 데이터베이스에 성공적으로 기록되었습니다. (시간: {now_kst})"
    except Exception as e:
        return f"❌ 피드백 저장 중 오류 발생: {e}"

# --- [Gemini Function Calling Schema (Legacy / Manual Request Support)] ---

SYSTEM_TOOLS = [
    {
        "name": "get_account_summary",
        "description": "계좌의 예수금, 총 평가금액, 당일 손익 등 요약 정보를 조회합니다.",
        "parameters": {"type": "object", "properties": {}}
    },
    {
        "name": "get_balance",
        "description": "보유 중인 종목 현황과 상세 잔고 정보를 조회합니다.",
        "parameters": {
            "type": "object",
            "properties": {
                "qry_tp": {"type": "string", "enum": ["1", "2"], "description": "1:합산, 2:개별 (기본값 2)"},
                "is_detailed": {"type": "boolean", "description": "True일 경우 상세 수량과 평단가를 보여줍니다."}
            }
        }
    },
    {
        "name": "get_stock_price",
        "description": "특정 종목의 현재가 정보를 조회합니다. 종목명을 입력받으면 반드시 6자리 종목코드로 변환해서 호출하세요. (예: 삼성전자 -> 005930, 진에어 -> 272450)",
        "parameters": {
            "type": "object",
            "properties": {
                "stock_code": {"type": "string", "description": "6자리 종목코드"}
            },
            "required": ["stock_code"]
        }
    },
    {
        "name": "place_order",
        "description": "주식을 매수 또는 매도 주문합니다. 종목명은 코드로 변환하세요.",
        "parameters": {
            "type": "object",
            "properties": {
                "stock_code": {"type": "string", "description": "6자리 종목코드"},
                "quantity": {"type": "integer", "description": "주문 수량"},
                "price": {"type": "integer", "description": "주문 단가"},
                "side": {"type": "string", "enum": ["BUY", "SELL"], "description": "매수(BUY) 또는 매도(SELL)"},
                "ord_tp": {"type": "string", "enum": ["00", "03"], "description": "00:지정가, 03:시장가"}
            },
            "required": ["stock_code", "quantity", "price", "side"]
        }
    },
    {
        "name": "get_system_status",
        "description": "서버 시스템의 상태(CPU 부하, 메모리 사용량 등)를 조회합니다.",
        "parameters": {"type": "object", "properties": {}}
    },
    {
        "name": "get_help",
        "description": "사용 가능한 기능 리스트와 사용법(도움말)을 안내합니다.",
        "parameters": {"type": "object", "properties": {}}
    },
    {
        "name": "list_google_events",
        "description": "구글 캘린더에서 다가오는 일정(최대 5개)을 조회합니다.",
        "parameters": {
            "type": "object",
            "properties": {
                "max_results": {"type": "integer", "description": "조회할 일정 개수"}
            }
        }
    },
    {
        "name": "search_history",
        "description": "과거 대화 내용이나 기록에서 특정 키워드를 검색합니다.",
        "parameters": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "검색할 키워드"}
            },
            "required": ["keyword"]
        }
    },
    {
        "name": "save_training_feedback",
        "description": "사용자의 피드백이나 정정 사항을 학습 데이터베이스에 저장합니다.",
        "parameters": {
            "type": "object",
            "properties": {
                "raw_text": {"type": "string"},
                "predicted_label": {"type": "string"},
                "actual_label": {"type": "string"},
                "confidence": {"type": "number"}
            },
            "required": ["raw_text", "actual_label"]
        }
    }
]

def get_daily_chart(stock_code: str) -> str:
    """특정 종목의 일봉 차트 데이터를 조회합니다."""
    if not kiwoom_api: return "Kiwoom API를 사용할 수 없습니다."
    # 일봉 차트 조회 로직 (현재는 기본 정보 반환으로 대체하거나 차트 메시지 생성)
    res = kiwoom_api.get_stock_info(stock_code)
    nm = res.get("stk_nm", "알수없음")
    return f"📈 **[{nm}] 일봉 차트 정보**\n━━━━━━━━━━━━━━\n(현재 차트 이미지 생성 기능을 준비 중입니다.)\n💰 **현재가:** {int(res.get('cur_prc', 0)):,}원\n━━━━━━━━━━━━━━"

def skill_router(function_name, arguments):
    """이전 방식(Manual Tool Calling) 지원용 라우터"""
    funcs = {
        "get_account_summary": get_account_summary,
        "get_balance": get_balance,
        "get_stock_price": get_stock_price,
        "get_daily_chart": get_daily_chart,
        "get_order_history": get_order_history,
        "place_order": place_order,
        "search_history": search_history,
        "list_google_events": list_google_events,
        "get_system_status": get_system_status,
        "get_help": get_help,
        "save_training_feedback": save_training_feedback,
        "switch_ai_model": switch_ai_model
    }
    if function_name in funcs:
        try:
            # arguments가 dict 형태이므로 언패킹하여 호출 (또는 arguments 없이 호출)
            if arguments: return funcs[function_name](**arguments)
            else: return funcs[function_name]()
        except Exception as e:
            return f"Error executing {function_name}: {e}"
    return f"Unknown function: {function_name}"

# 하위 호환성을 위한 개별 함수 노출 (사용 중인 파일이 있을 수 있음)
def get_account_status(): return get_account_summary()
def execute_market_order(ticker, action, ratio): return f"Market order interface for {ticker}"
def update_profit_cut(ratio): return f"Profit cut interface for {ratio}"
