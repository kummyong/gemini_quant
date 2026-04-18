import os
from google_auth_oauthlib.flow import InstalledAppFlow

# 권한 범위 설정
SCOPES = [
    'https://www.googleapis.com/auth/calendar',
    'https://www.googleapis.com/auth/tasks'
]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_PATH = os.path.join(BASE_DIR, 'credentials.json')
TOKEN_PATH = os.path.join(BASE_DIR, 'token.json')

# 사용자가 제공한 코드
AUTH_CODE = '4/0Aci98-kTtyo54f5h8v50lKEpzT84z3BmYKoNSZFEqLtQH9txH1MdiaxpbfhVitcQBa1ng'

def generate_token():
    flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
    # redirect_uri를 'urn:ietf:wg:oauth:2.0:oob'로 설정하여 코드를 직접 입력받는 방식 시도
    # (일부 라이브러리 버전에서는 localhost 리다이렉트를 강제하므로 flow에 맞게 조정 필요)
    flow.redirect_uri = 'http://localhost:52173/' # 사용자가 제공한 URL의 리다이렉트 주소와 일치해야 함
    
    # fetch_token 실행
    flow.fetch_token(code=AUTH_CODE)
    creds = flow.credentials
    
    with open(TOKEN_PATH, 'w') as token:
        token.write(creds.to_json())
    print(f"[Success] token.json이 성공적으로 생성되었습니다: {TOKEN_PATH}")

if __name__ == "__main__":
    generate_token()
