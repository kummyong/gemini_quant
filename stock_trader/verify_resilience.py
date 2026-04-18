import sys
import time
import logging
import functools
import requests

# 테스트를 위해 로깅 설정
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

# 실제 파일에 적용한 것과 동일한 데코레이터 로직
def retry_with_backoff(max_retries=5, base_delay=2):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            retries = 0
            while retries < max_retries:
                try:
                    return func(*args, **kwargs)
                except (requests.exceptions.RequestException, ConnectionError, TimeoutError) as e:
                    retries += 1
                    delay = base_delay * (2 ** (retries - 1))
                    logging.warning(f"⚠️ [통신 장애] {func.__name__} 실패. {delay}초 후 재시도 ({retries}/{max_retries}) | 에러: {e}")
                    
                    if retries >= max_retries:
                        logging.critical(f"🔥 [CRITICAL] 재시도 횟수 초과. 프로세스 종료 및 재시작 유도.")
                        # 테스트를 위해 sys.exit 대신 예외 발생으로 대체 확인
                        raise SystemExit("System exited with code 1")
                    
                    # 테스트 속도를 위해 실제 sleep은 생략하거나 아주 짧게 설정
                    # time.sleep(delay) 
                    print(f"[Test] 실제 환경이었다면 {delay}초 대기했을 것입니다.")
                    
                except Exception as e:
                    logging.error(f"❌ [Logic Error] {func.__name__} 오류: {e}")
                    raise e
            return None
        return wrapper
    return decorator

# --- 테스트 함수 정의 ---

# 1. 2번 실패 후 3번째 성공하는 케이스
fail_count = 0
@retry_with_backoff(max_retries=5, base_delay=2)
def test_partial_failure():
    global fail_count
    if fail_count < 2:
        fail_count += 1
        raise requests.exceptions.RequestException("Temporary Network Error")
    return "SUCCESS"

# 2. 5번 모두 실패하는 케이스
@retry_with_backoff(max_retries=5, base_delay=2)
def test_permanent_failure():
    raise requests.exceptions.RequestException("Permanent Network Down")

def run_test():
    print("\n--- [테스트 1] 2회 실패 후 재시도 성공 검증 ---")
    try:
        result = test_partial_failure()
        print(f"결과: {result}")
        if result == "SUCCESS":
            print("✨ 검증 결과: [성공] 재시도 로직이 정상 작동하여 최종적으로 성공했습니다.")
    except Exception as e:
        print(f"❌ 검증 결과: [실패] {e}")

    print("\n--- [테스트 2] 5회 연속 실패 시 프로세스 종료 검증 ---")
    try:
        test_permanent_failure()
    except SystemExit as e:
        print(f"✨ 검증 결과: [성공] 5회 실패 후 {e} (프로세스 종료 확인)")
    except Exception as e:
        print(f"❌ 검증 결과: [실패] 예상치 못한 에러: {e}")

if __name__ == "__main__":
    run_test()
