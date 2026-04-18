import unittest
from unittest.mock import patch, MagicMock
from kiwoom_mcp import kiwoom_api, kiwoom_config, KiwoomConfig, KiwoomApiManager

class TestKiwoomMcp(unittest.TestCase):
    def setUp(self):
        # 테스트용 설정 데이터 (MOCK 모드 기반)
        self.original_account = kiwoom_config.account_no
        self.test_account_1 = "1234-5678-00"
        self.test_account_2 = "9876-5432-11"

    def tearDown(self):
        # 테스트 후 원래 계좌로 복구
        kiwoom_api.set_account_no(self.original_account)

    def test_change_account_logic(self):
        """계좌 변경 로직이 내부 설정을 올바르게 업데이트하는지 테스트"""
        print(f"\n[테스트] 계좌 변경 시작: {self.original_account} -> {self.test_account_1}")
        
        # 계좌 변경 수행
        result = kiwoom_api.set_account_no(self.test_account_1)
        
        self.assertEqual(result["status"], "success")
        self.assertEqual(kiwoom_config.account_no, self.test_account_1)
        print(f"✅ 계좌 변경 확인 완료: {kiwoom_config.account_no}")

    @patch('kiwoom_mcp.KiwoomApiManager._request')
    def test_account_summary_uses_new_account(self, mock_request):
        """계좌 변경 후 API 요청 시 변경된 계좌 번호를 사용하는지 테스트"""
        # Given: 새로운 계좌 설정
        kiwoom_api.set_account_no(self.test_account_2)
        mock_request.return_value = {"status": "mock_success"}
        
        # When: 계좌 요약 정보 조회
        kiwoom_api.get_account_summary()
        
        # Then: _request 호출 시 전달된 body의 acc_no가 변경된 계좌(하이픈 제거 + 11 추가)인지 확인
        expected_acc_no = self.test_account_2.replace("-", "")
        # 만약 계좌번호가 8자리라면 11을 붙이는 로직이 kiwoom_mcp.py에 있음
        if len(expected_acc_no) == 8:
            expected_acc_no += "11"
            
        args, kwargs = mock_request.call_args
        actual_acc_no = kwargs['body']['acc_no']
        
        self.assertEqual(actual_acc_no, expected_acc_no)
        print(f"✅ API 요청 계좌 번호 일치 확인: {actual_acc_no} == {expected_acc_no}")

if __name__ == "__main__":
    unittest.main()
