import datetime

# 한국거래소(KRX) 휴장일 (2025년 ~ 2027년)
KRX_HOLIDAYS = {
    # 2025년
    "2025-01-01", "2025-01-27", "2025-01-28", "2025-01-29", "2025-01-30",
    "2025-03-03", "2025-05-01", "2025-05-05", "2025-05-06", "2025-06-06",
    "2025-08-15", "2025-10-03", "2025-10-06", "2025-10-07", "2025-10-08",
    "2025-10-09", "2025-12-25", "2025-12-31",
    
    # 2026년
    "2026-01-01", "2026-02-16", "2026-02-17", "2026-02-18", "2026-03-02",
    "2026-05-01", "2026-05-05", "2026-05-25", "2026-06-03", "2026-07-17",
    "2026-08-17", "2026-09-24", "2026-09-25", "2026-10-05", "2026-10-09",
    "2026-12-25", "2026-12-31",
    
    # 2027년
    "2027-01-01", "2027-02-08", "2027-02-09", "2027-03-01", "2027-05-05",
    "2027-05-13", "2027-06-07", "2027-08-16", "2027-09-14", "2027-09-15",
    "2027-09-16", "2027-10-04", "2027-10-11", "2027-12-27", "2027-12-31"
}

def is_market_holiday(dt: datetime.datetime) -> bool:
    """
    주말(토, 일)이거나 KRX 지정 휴장일(공휴일, 근로자의 날, 연말 휴장일 등)이면 True 반환
    """
    # 주말 체크 (5: 토요일, 6: 일요일)
    if dt.weekday() >= 5:
        return True

    # 공휴일 및 휴장일 체크
    date_str = dt.strftime("%Y-%m-%d")
    return date_str in KRX_HOLIDAYS

def holiday_table_warning(dt: datetime.datetime) -> str:
    """KRX_HOLIDAYS 하드코딩 테이블의 소진 임박/소진 여부를 점검한다.

    테이블에 없는 연도는 주말만 휴장으로 처리되어 공휴일에 개장일로 오판된다
    (전략 엔진이 헛돌고 서킷브레이커 지수 조회가 오래된 데이터를 보게 됨).
    경고가 필요하면 메시지 문자열, 정상이면 빈 문자열을 반환한다."""
    years = {int(h[:4]) for h in KRX_HOLIDAYS}
    if dt.year not in years:
        return (f"🚨 [긴급] KRX 휴장일 테이블에 올해({dt.year}년) 데이터가 없습니다! "
                f"공휴일이 개장일로 오판됩니다. korean_market_calendar.py를 즉시 갱신하세요.")
    if dt.month >= 11 and (dt.year + 1) not in years:
        return (f"⚠️ KRX 휴장일 테이블에 내년({dt.year + 1}년) 데이터가 없습니다. "
                f"연말 전에 korean_market_calendar.py에 KRX 휴장일을 추가하세요.")
    return ""


def is_first_trading_day_of_month(dt: datetime.datetime) -> bool:
    """
    dt가 해당 월의 첫 거래일(주말/휴장일 제외)이면 True 반환.
    ETF 분배금/수정주가 반영을 위한 월 1회 강제 전체 재조회(force_full) 트리거용.
    """
    if is_market_holiday(dt):
        return False

    cursor = dt.replace(day=1)
    while cursor.date() < dt.date():
        if not is_market_holiday(cursor):
            return False
        cursor += datetime.timedelta(days=1)
    return True
