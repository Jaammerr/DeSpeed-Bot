import asyncio
import json

from typing import Literal
from curl_cffi.requests import AsyncSession, Response

from utils.processing.handlers import require_access_token
from core.exceptions.base import APIError, ServerError, ProxyForbidden


class APIClient:
    API_URL = "https://app.despeed.net/v1/api"

    def __init__(self, proxy: str = None):
        self.proxy = proxy
        self.session = self._create_session()
        self.user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

    def _create_session(self) -> AsyncSession:
        session = AsyncSession(impersonate="chrome131", verify=False)
        session.timeout = 30

        if self.proxy:
            session.proxies = {
                "http": self.proxy,
                "https": self.proxy,
            }

        return session

    async def clear_request(self, url: str) -> Response:
        session = self._create_session()
        return await session.get(url, allow_redirects=True, verify=False)

    @staticmethod
    async def _verify_response(response_data: dict | list):
        if isinstance(response_data, dict):
            if "success" in str(response_data):
                if response_data.get("success") is False:
                    raise APIError(
                        f"API returned an error: {response_data}", response_data
                    )

    async def close_session(self) -> None:
        try:
            await self.session.close()
        except:
            pass

    async def send_request(
        self,
        request_type: Literal["POST", "GET", "OPTIONS"] = "POST",
        method: str = None,
        json_data: dict = None,
        params: dict = None,
        url: str = None,
        headers: dict = None,
        cookies: dict = None,
        verify: bool = True,
        max_retries: int = 2,
        retry_delay: float = 3.0,
    ) -> dict | Response:
        if not url:
            url = f"{self.API_URL}{method}"

        for attempt in range(max_retries):
            try:
                if request_type == "POST":
                    response = await self.session.post(
                        url,
                        json=json_data,
                        params=params,
                        headers=headers if headers else self.session.headers,
                        cookies=cookies,
                    )
                elif request_type == "OPTIONS":
                    response = await self.session.options(
                        url,
                        headers=headers if headers else self.session.headers,
                        cookies=cookies,
                    )
                else:
                    response = await self.session.get(
                        url,
                        params=params,
                        headers=headers if headers else self.session.headers,
                        cookies=cookies,
                    )

                if verify:
                    if response.status_code == 403 and "403 Forbidden" in response.text:
                        raise ProxyForbidden(f"Proxy forbidden - {response.status_code}")

                    elif response.status_code == 403:
                        raise ServerError(f"Response forbidden - 403: {response.text[:200]}")

                    if response.status_code in (500, 502, 503, 504):
                        raise ServerError(f"Server error - {response.status_code}")

                    try:
                        response_json = response.json()
                        await self._verify_response(response_json)
                        return response_json
                    except json.JSONDecodeError:
                        raise ServerError(f"Failed to decode response, most likely server error")

                return response

            except ServerError as error:
                if attempt == max_retries - 1:
                    raise error
                await asyncio.sleep(retry_delay)

            except (APIError, ProxyForbidden):
                raise

            except Exception as error:
                if attempt == max_retries - 1:
                    raise ServerError(
                        f"Failed to send request after {max_retries} attempts: {error}"
                    )
                await asyncio.sleep(retry_delay)

        raise ServerError(f"Failed to send request after {max_retries} attempts")


class DespeedAPI(APIClient):
    def __init__(self, access_token: str = None, proxy: str = None):
        super().__init__(proxy)
        self.access_token = access_token


    async def sign_up(self, email: str, password: str, username: str, hcaptcha_token: str, referral_code: str = None) -> dict:
        headers = {
            'accept': 'application/json, text/plain, */*',
            'accept-language': 'en-US,en;q=0.9,ru;q=0.8',
            'content-type': 'application/json',
            'origin': 'https://app.despeed.net',
            'referer': 'https://app.despeed.net/register',
            'user-agent': self.user_agent,
        }

        json_data = {
            'email': email,
            'username': username,
            'password': password,
            'confirmPassword': password,
            'referralCode': referral_code if referral_code else '',
            'captchaResponse': hcaptcha_token,
        }

        return await self.send_request(
            request_type="POST",
            method="/auth/signup",
            json_data=json_data,
            headers=headers,
        )

    async def verify_email(self, token: str) -> dict:
        headers = {
            'accept': 'application/json, text/plain, */*',
            'accept-language': 'en-US,en;q=0.9,ru;q=0.8',
            'referer': f'https://app.despeed.net/verify-email/{token}',
            'user-agent': self.user_agent,
        }

        return await self.send_request(
            request_type="GET",
            method=f"/auth/verify-email/{token}",
            headers=headers,
        )


    async def login(self, email_or_username: str, password: str, hcaptcha_token: str) -> str:
        headers = {
            'accept': 'application/json, text/plain, */*',
            'accept-language': 'en-US,en;q=0.9,ru;q=0.8',
            'content-type': 'application/json',
            'origin': 'https://app.despeed.net',
            'referer': 'https://app.despeed.net/',
            'user-agent': self.user_agent,
        }

        json_data = {
            'emailOrUsername': email_or_username,
            'password': password,
            'captchaResponse': hcaptcha_token,
        }

        response = await self.send_request(
            request_type="POST",
            method="/auth/login",
            json_data=json_data,
            headers=headers,
        )

        return response["data"]["accessToken"]

    @require_access_token
    async def profile_info(self) -> dict:
        headers = {
            'accept': 'application/json, text/plain, */*',
            'accept-language': 'en-US,en;q=0.9,ru;q=0.8',
            'authorization': f'Bearer {self.access_token}',
            'referer': 'https://app.despeed.net/dashboard',
            'user-agent': self.user_agent,
        }

        response = await self.send_request(
            request_type="GET",
            method="/auth/profile",
            headers=headers,
        )

        return response["data"]

    @require_access_token
    async def dashboard_stats(self) -> dict:
        headers = {
            'accept': 'application/json, text/plain, */*',
            'accept-language': 'en-US,en;q=0.9,ru;q=0.8',
            'authorization': f'Bearer {self.access_token}',
            'referer': 'https://app.despeed.net/dashboard',
            'user-agent': self.user_agent,
        }

        response = await self.send_request(
            request_type="GET",
            method="/api/dashboard-stats",
            headers=headers,
        )

        return response["data"]

    @require_access_token
    async def daily_claim(self) -> dict:
        headers = {
            'accept': 'application/json, text/plain, */*',
            'accept-language': 'en-US,en;q=0.9,ru;q=0.8',
            'authorization': f'Bearer {self.access_token}',
            'origin': 'https://app.despeed.net',
            'referer': 'https://app.despeed.net/dashboard',
            'user-agent': self.user_agent,
        }

        response = await self.send_request(
            request_type="POST",
            method="/daily-claim",
            headers=headers,
        )

        return response["data"]


    @require_access_token
    async def referrals_stats(self) -> dict:
        headers = {
            'accept': 'application/json, text/plain, */*',
            'accept-language': 'en-US,en;q=0.9,ru;q=0.8',
            'authorization': f'Bearer {self.access_token}',
            'referer': 'https://app.despeed.net/referral',
            'user-agent': self.access_token,
        }

        response = await self.send_request(
            request_type="GET",
            method="/api/referrals-stats",
            headers=headers,
        )

        return response["data"]

    @require_access_token
    async def seasons_earnings(self) -> dict:
        headers = {
            'accept': 'application/json, text/plain, */*',
            'accept-language': 'en-US,en;q=0.9,ru;q=0.8',
            'authorization': f'Bearer {self.access_token}',
            'referer': 'https://app.despeed.net/earnings',
            'user-agent': self.user_agent,
        }

        response = await self.send_request(
            request_type="GET",
            method="/earnings/seasons",
            headers=headers,
        )

        return response["data"]

    @require_access_token
    async def speedtest_eligibility(self) -> dict:
        headers = {
            'accept': '*/*',
            'accept-language': 'en-US,en;q=0.9,ru;q=0.8',
            'authorization': f'Bearer {self.access_token}',
            'origin': 'chrome-extension://ofpfdpleloialedjbfpocglfggbdpiem',
            'user-agent': self.user_agent,
        }

        json_data = {}
        response = await self.send_request(
            request_type="POST",
            method="/speedtest-eligibility-v2",
            json_data=json_data,
            headers=headers,
        )

        return response["data"]

    async def request_latitude_and_logitude(self) -> tuple[float, float]:
        headers = {
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'accept-language': 'en-US,en;q=0.9,ru;q=0.8',
            'user-agent': self.user_agent,
        }

        response = await self.send_request(
            request_type="GET",
            url="https://ipinfo.io/json",
            headers=headers,
            verify=False,
        )

        if response.status_code == 200:
            data = response.json()
            latitude, longitude = data["loc"].split(",")
            return float(latitude), float(longitude)
        else:
            raise APIError(f"Failed to get location data: {response.status_code}")


    @require_access_token
    async def send_speed_test_results(self, download_speed: float, upload_speed: float, latitude: float, logitude: float) -> dict:
        headers = {
            'accept': '*/*',
            'accept-language': 'en-US,en;q=0.9,ru;q=0.8',
            'authorization': f'Bearer {self.access_token}',
            'content-type': 'application/json',
            'origin': 'chrome-extension://ofpfdpleloialedjbfpocglfggbdpiem',
            'user-agent': self.user_agent,
        }

        download_speed = round(download_speed, 2) if download_speed > 0 else 0
        upload_speed = round(upload_speed, 2) if upload_speed > 0 else 0

        json_data = {
            'download_speed': download_speed,
            'upload_speed': upload_speed,
            'latitude': latitude,
            'logitude': logitude,
            'isClientUploadSpeed': False,
            'clientUploadSpeed': 0,
        }

        response = await self.send_request(
            request_type="POST",
            method="/points-v2",
            json_data=json_data,
            headers=headers,
        )
        return response["data"]
