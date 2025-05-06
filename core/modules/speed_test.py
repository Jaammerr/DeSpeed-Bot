import aiohttp
import asyncio
import time
import ssl

from aiohttp import WSMessage, WSMsgType, ClientHttpProxyError
from models import Account
from loguru import logger


class SpeedTest:
    LOCATE_URL = "https://locate.measurementlab.net/v2/nearest/ndt/ndt7"

    def __init__(self, account: Account, proxy: str, max_attempts: int = 3):
        self.account = account
        self.proxy = proxy
        self.max_attempts = max_attempts

    @staticmethod
    def create_ssl_context() -> ssl.SSLContext:
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        return ssl_context

    async def run_upload_test(self, upload_url: str) -> float:
        logger.info(f"Account: {self.account.email} | Performing upload test..")

        buffer = bytes(8192)
        total_bytes_sent = 0
        start_time = time.perf_counter()

        async with aiohttp.ClientSession(trust_env=True) as session:
            async with session.ws_connect(
                    upload_url,
                    proxy=self.proxy,
                    ssl=self.create_ssl_context(),
                    protocols=['net.measurementlab.ndt.v7']
            ) as ws:
                async def send_data():
                    nonlocal total_bytes_sent
                    while True:
                        try:
                            await ws.send_bytes(buffer)
                            total_bytes_sent += len(buffer)
                            await asyncio.sleep(0)
                        except Exception:
                            break

                sender_task = asyncio.create_task(send_data())

                try:
                    async for msg in ws:
                        if isinstance(msg, WSMessage):
                            if msg.type == aiohttp.WSMsgType.CLOSED:
                                break

                except Exception as error:
                    sender_task.cancel()
                    if isinstance(error, ClientHttpProxyError):
                        error = error.message

                    raise Exception(f"Error occurred during upload test: {error}")

        elapsed_time = time.perf_counter() - start_time
        final_speed_mbps = (total_bytes_sent * 8) / elapsed_time / 1e6

        logger.success(f"Account: {self.account.email} | Upload test completed, speed: {final_speed_mbps:.2f} Mbps")
        return final_speed_mbps

    async def run_download_test(self, download_url: str) -> float:
        logger.info(f"Account: {self.account.email} | Performing download test..")

        total_bytes = 0
        start_time = time.perf_counter()
        max_duration = 30

        async with aiohttp.ClientSession(trust_env=True) as session:
            async with session.ws_connect(
                    download_url,
                    ssl=self.create_ssl_context(),
                    proxy=self.proxy,
                    protocols=['net.measurementlab.ndt.v7']
            ) as ws:
                try:
                    async for msg in ws:
                        if isinstance(msg, WSMessage):
                            if msg.type == WSMsgType.BINARY:
                                total_bytes += len(msg.data)

                            elif msg.type == WSMsgType.CLOSED:
                                break

                        if time.perf_counter() - start_time > max_duration:
                            logger.info(f"Max duration {max_duration}s reached, stopping download test")
                            break

                    await ws.close()

                except Exception as error:
                    ...

        elapsed_time = time.perf_counter() - start_time
        final_speed_mbps = (total_bytes * 8) / elapsed_time / 1e6

        logger.success(f"Account: {self.account.email} | Download test completed, speed: {final_speed_mbps:.2f} Mbps")
        return final_speed_mbps

    async def fetch_servers(self) -> dict:
        try:
            async with aiohttp.ClientSession(
                    trust_env=True,
                    proxy=self.proxy
            ) as session:
                async with session.get(self.LOCATE_URL) as response:
                    data = await response.json()

            if not data.get("results") or len(data["results"]) == 0:
                raise Exception("No available servers found to perform the test")

            server = data["results"][0]
            return {
                'downloadUrl': server['urls']['wss:///ndt/v7/download'],
                'uploadUrl': server['urls']['wss:///ndt/v7/upload'],
            }

        except Exception as error:
            if isinstance(error, ClientHttpProxyError):
                error = error.message

            raise Exception(f"Error occurred while fetching servers: {error}")


    async def run_download_test_with_timeout(self, download_url: str, timeout: int) -> float:
        try:
            return await asyncio.wait_for(self.run_download_test(download_url), timeout)
        except asyncio.TimeoutError:
            logger.warning(f"Account: {self.account.email} | Download test timed out after {timeout} seconds.")
            return 0.0

    async def run_upload_test_with_timeout(self, upload_url: str, timeout: int) -> float:
        try:
            return await asyncio.wait_for(self.run_upload_test(upload_url), timeout)
        except asyncio.TimeoutError:
            logger.warning(f"Account: {self.account.email} | Upload test timed out after {timeout} seconds.")
            return 0.0

    async def perform_tests(self, server_urls: dict) -> tuple[float, float]:
        download_task = asyncio.create_task(self.run_download_test_with_timeout(server_urls['downloadUrl'], 60))
        upload_task = asyncio.create_task(self.run_upload_test_with_timeout(server_urls['uploadUrl'], 60))

        download_speed, upload_speed = await asyncio.gather(download_task, upload_task)
        return download_speed, upload_speed


    async def run(self) -> tuple[float, float]:
        for attempt in range(self.max_attempts):
            try:
                logger.info(f"Account: {self.account.email} | Starting speed test..")
                server_urls = await self.fetch_servers()

                download_speed, upload_speed = await self.perform_tests(server_urls)
                return download_speed, upload_speed

            except Exception as error:
                if attempt == self.max_attempts - 1:
                    logger.error(f"Account: {self.account.email} | Max attempts reached. Test failed.")
                    return 0, 0

                logger.error(f"Account: {self.account.email} | Speed test failed: {error} | Attempt: {attempt + 1}/{self.max_attempts} | Retrying...")
                await asyncio.sleep(2)
