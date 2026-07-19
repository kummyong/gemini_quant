import logging
import datetime
from typing import Dict
import FinanceDataReader as fdr

logger = logging.getLogger("MacroIndicators")

class MacroIndicatorProvider:
    """거시 파생 지표 (VIX 등) 수집 모듈"""
    
    def fetch_vix_disparity(self) -> Dict[str, float]:
        """FRED로부터 VIXCLS(CBOE Volatility Index)를 가져와 20일 이동평균 대비 괴리율을 계산한다.
        반환: {"vix_current": float, "vix_ma20": float, "vix_disparity": float}
        """
        result = {"vix_current": 20.0, "vix_ma20": 20.0, "vix_disparity": 0.0}
        try:
            # 최근 60일치 데이터 가져오기 (영업일 기준 20MA 계산을 위해 충분히)
            start_date = (datetime.datetime.now() - datetime.timedelta(days=60)).strftime("%Y-%m-%d")
            df = fdr.DataReader('VIXCLS', data_source='fred', start=start_date)
            
            if df is not None and not df.empty:
                df = df.dropna()
                if len(df) >= 20:
                    current_vix = float(df.iloc[-1].iloc[0]) # VIXCLS 열
                    ma20_vix = float(df.iloc[:, 0].rolling(window=20).mean().iloc[-1])
                    disparity = ((current_vix - ma20_vix) / ma20_vix) * 100.0
                    
                    result["vix_current"] = round(current_vix, 2)
                    result["vix_ma20"] = round(ma20_vix, 2)
                    result["vix_disparity"] = round(disparity, 2)
                    logger.info(f"🌐 [거시지표] VIX: {result['vix_current']}, 20일선 대비 괴리율: {result['vix_disparity']}%")
        except Exception as e:
            logger.error(f"VIX 지표 수집 실패: {e}")
            
        return result
