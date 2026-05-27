import os
import configparser
from typing import Dict, List
from core.broker_interface import BrokerInterface
from kiwoom_adapter import KiwoomAdapter
from adapters.mock_adapters import KoreaInvestmentAdapter, NhInvestmentAdapter

class BrokerFactory:
    """
    다중 증권사 어댑터를 설정 로드 후 생성 및 관리하는 싱글톤 팩토리입니다.
    """
    _instance = None
    _brokers: Dict[str, BrokerInterface] = {}

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(BrokerFactory, cls).__new__(cls, *args, **kwargs)
        return cls._instance

    @classmethod
    def get_broker(cls, name: str) -> BrokerInterface:
        name_upper = name.upper()
        if name_upper not in cls._brokers:
            if name_upper == "KIWOOM":
                cls._brokers[name_upper] = KiwoomAdapter()
            elif name_upper == "KOREA_INVEST":
                cls._brokers[name_upper] = KoreaInvestmentAdapter()
            elif name_upper == "NH_INVEST":
                cls._brokers[name_upper] = NhInvestmentAdapter()
            else:
                raise ValueError(f"지원하지 않는 증권사입니다: {name}")
        return cls._brokers[name_upper]

    @classmethod
    def get_active_brokers(cls) -> List[str]:
        # 기본적으로 모든 지원 브로커 활성화 (설정 파일을 통해 필터링 가능)
        return ["KIWOOM", "KOREA_INVEST", "NH_INVEST"]
