import asyncio
import json
import logging
from typing import Callable, Awaitable

logger = logging.getLogger("IpcMessenger")

# 기본 IPC 포트 및 호스트 설정
DEFAULT_HOST = '127.0.0.1'
DEFAULT_PORT = 9999

class IpcPublisher:
    """전략 실행 프로세스에서 메시지를 발행(Publish)하는 IPC 클라이언트"""
    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
        self.host = host
        self.port = port

    async def send_message(self, event_type: str, payload: dict) -> bool:
        """JSON 데이터 전송"""
        message = {"event": event_type, "payload": payload}
        try:
            reader, writer = await asyncio.open_connection(self.host, self.port)
            data = json.dumps(message).encode('utf-8')
            writer.write(data + b'\n')  # 줄바꿈 구분자 활용
            await writer.drain()
            writer.close()
            await writer.wait_closed()
            return True
        except Exception as e:
            logger.error(f"⚠️ IPC 알림 송신 실패: {e}")
            return False

    def send_message_sync(self, event_type: str, payload: dict) -> bool:
        """동기 코드 블록에서 호출 가능하도록 래핑한 동기식 전송 도구.
        현재 스레드에 실행 중인 이벤트 루프가 있으면 그 위에서 블로킹 대기할 수 없으므로
        (기존 run_coroutine_threadsafe + result() 조합은 자기 루프 대기 데드락 위험)
        별도 스레드에서 전송을 완료하고 결과를 반환합니다."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # 실행 중인 루프 없음 — 새 루프에서 즉시 실행
            return asyncio.run(self.send_message(event_type, payload))

        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, self.send_message(event_type, payload))
            try:
                return future.result(timeout=5.0)
            except Exception:
                return False


class IpcListener:
    """알림 게이트웨이 데몬 프로세스에서 구동하는 IPC 서버"""
    def __init__(self, callback: Callable[[dict], Awaitable[None]], host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
        self.host = host
        self.port = port
        self.callback = callback
        self.server = None

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """클라이언트가 보낸 데이터 처리 및 콜백 호출"""
        try:
            data = await reader.readline()
            if data:
                message_str = data.decode('utf-8').strip()
                if message_str:
                    message = json.loads(message_str)
                    # 수신 콜백 비동기 호출
                    await self.callback(message)
        except Exception as e:
            logger.error(f"❌ IPC 메시지 수신 파싱 중 에러: {e}")
        finally:
            writer.close()
            await writer.wait_closed()

    async def start(self):
        """소켓 서버 백그라운드 구동 시작"""
        self.server = await asyncio.start_server(self.handle_client, self.host, self.port)
        logger.info(f"📡 IPC 리스너 서버 바인딩 완료: {self.host}:{self.port}")
        async with self.server:
            await self.server.serve_forever()

    async def stop(self):
        """서버 안전 종료"""
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            logger.info("📡 IPC 리스너 서버 종료 완료")
