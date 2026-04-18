# Kiwoom MCP Server (`kiwoom_mcp.py`) 핵심 요약

본 문서는 키움증권 REST API MCP 서버의 구조와 주요 기능을 요약하여, 다음 작업 시 빠른 파악을 돕기 위해 작성되었습니다.

## 1. 파일 개요
- **경로**: `D:\workspace_py\MCPServer\kiwoom_mcp.py`
- **역할**: 키움증권 REST API를 호출하여 계좌 정보, 주가 조회, 주문 실행 기능을 제공하는 MCP 서버.
- **주요 라이브러리**: `requests`, `FastMCP`, `configparser`.

## 2. 핵심 클래스 및 로직

### 2.1 `KiwoomConfig`
- `config.ini`에서 설정 정보를 읽어옴.
- **모드 전환**: 파일 하단의 `KiwoomConfig(mode="MOCK")` 부분에서 `MOCK`, `REAL2` 등으로 변경 가능.

### 2.2 `KiwoomApiManager` (핵심 엔진)
- **`_get_access_token`**: **지수 백오프(Exponential Backoff)** 기반 재시도 로직 포함 (최대 5회). 429 Error(레이트 리밋) 자동 대응.
- **`_request`**: 모든 API 요청의 공통 게이트웨이. 429(레이트 리밋) 및 401(토큰 만료) 발생 시 **자동 재시도 및 토큰 갱신** 수행.
- **`place_order`**: 매수(`kt10000`)/매도(`kt10001`) 주문 처리.
  - **MOCK 호환성**: `trde_tp` 필드에 주문 구분(`00`: 보통, `03`: 시장가)을 매핑하여 모의투자 오류(`RC4080`) 해결.
  - **필수값**: `dmst_stex_tp="KRX"`, `pw="0000"`, `unit_tp="1"`.

## 3. 제공되는 MCP 도구 (Tools)

| 도구명 | 설명 | 주요 파라미터 |
| :--- | :--- | :--- |
| `get_account_list` | 사용 가능한 모든 계좌번호 목록 조회 | - |
| `get_account_summary` | 계좌 예수금, 총 자산 평가액 조회 | - |
| `get_balance` | 계좌 내 보유 종목 현황 및 잔고 조회 | `qry_tp`: 1(합산), 2(개별) |
| `get_stock_price` | 특정 종목의 현재가 및 기본 정보 조회 | `stock_code` (예: 005930) |
| `get_stock_list` | 시장별 전체 종목 리스트 조회 | `market_type`: 0(코스피), 10(코스닥) |
| `get_daily_chart` | 특정 종목의 일봉 차트 데이터 조회 | `stock_code`, `base_date` (YYYYMMDD) |
| `place_order` | 주식 매수/매도 주문 실행 | `stock_code`, `quantity`, `price`, `side`(BUY/SELL), `ord_tp`(00/03) |

## 4. 운영 팁 (Next Steps)
- **모드 변경**: 실전 투자 시 하단의 `mode="REAL2"`로 수정. (현재 `MOCK` 활성)
- **로그 확인**: `logging.INFO` 수준의 로그가 표준 출력으로 나오므로 API 요청/응답 전문 확인 가능.
- **비밀번호**: 현재 `place_order` 내에 `pw="0000"`으로 하드코딩되어 있음 (모의투자용). 실전 전환 시 `config.ini`에서 읽어오도록 수정 필요할 수 있음.

---
**최종 업데이트**: 2026-03-19
**상태**: 검증 완료 (Stable)
