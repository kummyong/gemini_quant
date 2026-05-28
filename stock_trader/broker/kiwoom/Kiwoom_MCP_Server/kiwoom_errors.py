KIWOOM_ERROR_CODES = {
    "1501": "API ID가 Null이거나 값이 없습니다",
    "1504": "해당 URI에서는 지원하는 API ID가 아닙니다. API ID={?}, URI={?}",
    "1505": "해당 API ID는 존재하지 않습니다. API ID={?}",
    "1511": "필수 입력 값에 값이 존재하지 않습니다. 필수입력 파라미터={?}",
    "1512": "Http header에 값이 설정되지 않았거나 읽을 수 없습니다",
    "1513": "Http Header에 authorization 필드가 설정되어 있어야 합니다",
    "1514": "입력으로 들어온 Http Header의 authorization 필드 형식이 맞지 않습니다",
    "1515": "Http Header의 authorization 필드 내 Grant Type이 미리 정의된 형식이 아닙니다",
    "1516": "Http Header의 authorization 필드 내 Token이 정의되어 있지 않습니다",
    "1517": "입력 값 형식이 올바르지 않습니다. 파라미터={?} 실패사유= {?}",
    "1687": "재귀 호출이 발생하여 API 호출을 제한합니다, API ID={?}",
    "1700": "허용된 요청 개수를 초과하였습니다. API ID={?}",
    "1901": "시장 코드값이 존재하지 않습니다. 종목코드={?}",
    "1902": "종목 정보가 없습니다. 입력한 종목코드 값을 확인바랍니다. 종목코드={?}",
    "1999": "예기치 못한 에러가 발생했습니다. 실패사유={?}",
    "8001": "App Key와 Secret Key 검증에 실패했습니다",
    "8002": "App Key와 Secret Key 검증에 실패했습니다. 실패사유={?}",
    "8003": "Access Token을 조회하는데 실패했습니다. 실패사유={?}",
    "8005": "Token이 유효하지 않습니다",
    "8006": "Access Token을 생성하는데 실패했습니다. 실패사유={?}",
    "8009": "Access Token을 발급하는데 실패했습니다. 실패사유={?}",
    "8010": "Token을 발급받은 IP와 서비스를 요청한 IP가 동일하지 않습니다",
    "8011": "Access Token을 발급하는데 실패했습니다. 입력값에 grant_type이 들어오지 않았습니다",
    "8012": "Access Token을 발급하는데 실패했습니다. grant_type의 값이 맞지 않습니다",
    "8015": "Access Token을 폐기하는데 실패했습니다. 실패사유={?}",
    "8016": "Access Token을 폐기하는데 실패했습니다. 입력값에 Token이 들어오지 않았습니다",
    "8020": "입력파라미터로 appkey 또는 secretkey가 들어오지 않았습니다.",
    "8030": "투자구분(실전/모의)이 달라서 Appkey를 사용할수가 없습니다",
    "8031": "투자구분(실전/모의)이 달라서 Token를 사용할수가 없습니다",
    "8040": "단말기 인증에 실패했습니다",
    "8050": "지정단말기 인증에 실패했습니다",
    "8103": "토큰 인증 또는 단말기인증에 실패했습니다. 실패사유={?}"
}

def get_error_message(error_code, default_msg=None):
    return KIWOOM_ERROR_CODES.get(str(error_code), default_msg or f"알 수 없는 오류 (Code: {error_code})")

def format_error_telegram(error_code, api_id=None, detail=None):
    """키움 API 오류를 텔레그램 메시지 형식으로 포맷팅합니다."""
    reason = get_error_message(error_code)
    
    msg = f"⚠️ *API 요청 실패 (Error {error_code})*\n"
    msg += f"- **사유:** {reason}\n"
    if api_id:
        msg += f"- **API ID:** {api_id}\n"
    if detail:
        msg += f"- **상세:** {detail}\n"
    msg += "조치: 입력 파라미터를 다시 확인하거나 시스템 로그를 점검해 주세요."
    return msg
