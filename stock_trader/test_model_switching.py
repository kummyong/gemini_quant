import os
import sys
import unittest
from unittest.mock import patch

# 경로 설정
BASE_DIR = "/root/workspace/gemini-quant/stock_trader"
sys.path.append(BASE_DIR)

from agent_skills import switch_ai_model, AVAILABLE_MODELS

class TestModelSwitching(unittest.TestCase):
    def test_show_list(self):
        """인자 없이 호출 시 리스트를 보여주는지 확인"""
        result = switch_ai_model()
        self.assertIn("사용 가능한 모델 리스트", result)
        for model in AVAILABLE_MODELS:
            self.assertIn(model, result)
        print("\n✅ 리스트 출력 테스트 통과")

    def test_model_auto_correction(self):
        """접두사가 없는 모델명 입력 시 자동 보정 및 .env 반영 확인"""
        test_model = "gemini-1.5-pro"
        expected_full_name = "models/gemini-1.5-pro"
        
        result = switch_ai_model(test_model)
        self.assertIn(expected_full_name, result)
        self.assertIn("✅ AI 모델이", result)
        
        # 실제 .env 파일 확인
        env_path = os.path.join(BASE_DIR, ".env")
        with open(env_path, 'r') as f:
            content = f.read()
            self.assertIn(f"GEMINI_MODEL_NAME={expected_full_name}", content)
        print(f"✅ 모델명 자동 보정 및 .env 반영 테스트 통과 ({test_model} -> {expected_full_name})")

    def test_full_model_name(self):
        """전체 모델명 입력 시 정상 처리 확인"""
        test_model = "models/gemini-2.0-flash"
        result = switch_ai_model(test_model)
        self.assertIn(test_model, result)
        print(f"✅ 전체 모델명 입력 테스트 통과 ({test_model})")

if __name__ == "__main__":
    # 테스트 실행 전 .env 백업 (선택사항이나 안전을 위해)
    unittest.main()
