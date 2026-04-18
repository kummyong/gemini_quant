import os
from google_auth_oauthlib.flow import InstalledAppFlow

# 권한 범위 설정
SCOPES = [
    'https://www.googleapis.com/auth/calendar',
    'https://www.googleapis.com/auth/tasks'
]

def main():
    # 경로 설정
    credentials_path = 'credentials.json'
    token_path = 'token.json'
    
    if not os.path.exists(credentials_path):
        print(f"[Error] {credentials_path} 파일이 없습니다. 먼저 다운로드하여 업로드해주세요.")
        return

    # 인증 흐름 설정 (리다이렉트 URI를 http://localhost로 고정)
    flow = InstalledAppFlow.from_client_secrets_file(
        credentials_path, 
        SCOPES,
        redirect_uri='http://localhost'
    )
    
    # 인증 URL 생성
    auth_url, _ = flow.authorization_url(prompt='consent', access_type='offline')
    
    print("\n" + "="*60)
    print("1. 아래 URL을 복사하여 브라우저에서 방문하세요:")
    print(f"\n{auth_url}\n")
    print("2. 구글 계정으로 로그인 후 '승인'을 클릭하세요.")
    print("3. 브라우저 주소창(http://localhost/?state=...&code=...)을 전체 복사하세요.")
    print("="*60 + "\n")
    
    try:
        # 사용자로부터 결과 URL 또는 코드 입력 받기
        response_url = input("승인 후 리다이렉트된 전체 URL(또는 코드)을 입력하세요: ").strip()
        
        # URL에서 코드만 추출 (사용자가 URL 전체를 입력했을 경우 대비)
        if 'code=' in response_url:
            from urllib.parse import urlparse, parse_qs
            query = urlparse(response_url).query
            code = parse_qs(query).get('code', [None])[0]
        else:
            code = response_url
            
        if not code:
            print("[Error] 코드를 찾을 수 없습니다. 다시 시도해주세요.")
            return

        # 토큰 가져오기
        flow.fetch_token(code=code)
        creds = flow.credentials
        
        # token.json 저장
        with open(token_path, 'w') as token:
            token.write(creds.to_json())
            
        print(f"\n[Success] 인증 완료! '{token_path}' 파일이 생성되었습니다.")
        
    except Exception as e:
        print(f"\n[Error] 인증 중 오류 발생: {e}")

if __name__ == "__main__":
    main()
