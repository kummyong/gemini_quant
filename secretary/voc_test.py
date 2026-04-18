import os
import json
from google import genai

# 1. API 키 설정 (보안을 위해 환경 변수 사용 권장)
# 터미널에서 'export GEMINI_API_KEY=your_key_here' 명령으로 설정하세요.
API_KEY = os.environ.get("GEMINI_API_KEY")

if not API_KEY:
    print("[Error] GEMINI_API_KEY 환경 변수가 설정되지 않았습니다.")
    print("설정 방법: export GEMINI_API_KEY='본인의_API_키'")
    # 테스트를 위해 일시적으로 하드코딩된 키를 사용하려면 아래 주석을 해제하세요.
    # API_KEY = "AIzaSyB6cxOKDtBjEHd0DBTTGGv_Zd9THHmF4L8"
    if not API_KEY:
        exit(1)

client = genai.Client(api_key=API_KEY)

# 2. 가상의 VOC(고객의 소리) 데이터
voc_text = """
생일 선물로 보낸 건데 배송이 일주일이나 늦어졌고, 심지어 상자가 다 찌그러져서 왔어요. 
안에 든 향수 병이 깨졌는지 냄새가 진동을 합니다. 
생일 파티 이미 다 끝났는데 이게 뭐예요? 고객센터는 전화도 안 받고... 
당장 환불해 주시고 정신적 피해 보상도 해 주세요! 안 그러면 소비자 고발센터에 신고할 겁니다.
"""

# 3. Agentic AI에게 내리는 프롬프트 (명령)
# JSON 출력을 명시적으로 요청합니다.
prompt = f"""
당신은 CS 센터의 노련한 'VOC 분석 에이전트'입니다. 
다음 고객의 불만 사항을 읽고, 분석 리포트를 JSON 형식으로 생성해 주세요.

[요구사항]
- 반드시 유효한 JSON 형식으로만 응답하세요.
- JSON 키는 다음과 같아야 합니다:
  1. "emotion": 고객 감정 상태 (예: 분노, 실망 등)
  2. "keywords": 핵심 불만 키워드 (리스트 형태, 3~5개)
  3. "demand": 고객의 최종 요구 사항
  4. "priority": 처리 우선순위 (긴급, 보통, 낮음 중 선택)
  5. "reason": 우선순위 선정 이유
  6. "guide": 상담원 추천 대응 가이드 (어조 및 제안 보상 포함)

[고객 VOC]: 
{voc_text}
"""

print("🚀 Gemini Agent가 VOC를 분석하고 있습니다 (JSON 모드)...\n")

# 4. 모델 실행 및 결과 출력
try:
    response = client.models.generate_content(
        model='gemini-2.5-flash-lite',
        contents=prompt,
        config={
            'response_mime_type': 'application/json'
        }
    )

    # 결과 파싱 및 예쁘게 출력
    analysis_result = json.loads(response.text)
    print("=== [VOC 분석 리포트 (Structured)] ===")
    print(json.dumps(analysis_result, indent=2, ensure_ascii=False))

except Exception as e:
    print(f"[Error] 분석 중 오류 발생: {e}")
    if 'response' in locals():
        print(f"Raw Response: {response.text}")

