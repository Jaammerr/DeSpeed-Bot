import asyncio
import random
from typing import Optional, Literal, Any, Coroutine

from loguru import logger
from better_proxy import Proxy

from loader import config, file_operations, captcha_solver, proxy_manager
from models import Account, OperationResult
from database import Accounts
from core.api.despeed import DespeedAPI
from core.exceptions.base import (
    APIError,
    CaptchaSolvingFailed,
    APIErrorType,
    EmailValidationFailed
)
from utils import (
    EmailValidator, LinkExtractor, operation_failed, operation_success,
    validate_error, handle_sleep, generate_password, generate_username,
    parse_iso_to_pytz_utc, get_sleep_duration
)


class Bot:
    def __init__(self, account_data: Account):
        self.account_data = account_data

    @staticmethod
    async def handle_invalid_account(email: str, password: str, reason: Literal["unverified", "unregistered", "unlogged"], log: bool = True) -> None:
        if reason == "unverified":
            if log:
                logger.error(f"Account: {email} | Email not verified, run <<Register & Verify accounts>> module | Removed from list")
            await file_operations.export_invalid_account(email, password, "unverified")

        elif reason == "unregistered":
            if log:
                logger.error(f"Account: {email} | Email not registered, run <<Register & Verify accounts>> module | Removed from list")
            await file_operations.export_invalid_account(email, password, "unregistered")

        elif reason == "unlogged":
            if log:
                logger.error(f"Account: {email} | Account not logged in, run <<Login accounts>> module | Removed from list")
            await file_operations.export_invalid_account(email, password, "unlogged")

        for account in config.accounts_to_farm:
            if account.email == email:
                config.accounts_to_farm.remove(account)

    async def _validate_email(self, proxy: str = None) -> dict:
        proxy = Proxy.from_str(proxy) if proxy else None

        if config.redirect_settings.enabled:
            result = await EmailValidator(
                config.redirect_settings.imap_server,
                config.redirect_settings.email,
                config.redirect_settings.password
            ).validate(None if config.imap_settings.use_proxy_for_imap is False else proxy)
        else:
            result = await EmailValidator(
                self.account_data.imap_server,
                self.account_data.email,
                self.account_data.password
            ).validate(None if config.imap_settings.use_proxy_for_imap is False else proxy)

        return result

    async def _is_email_valid(self, proxy: str = None) -> bool:
        result = await self._validate_email(proxy)
        if not result["status"]:
            if "validation failed" in result["data"]:
                raise EmailValidationFailed(f"Email validation failed: {result['data']}")

            logger.error(f"Account: {self.account_data.email} | Email is invalid: {result['data']}")
            return False
        return True

    async def _extract_link(self, proxy: str = None, check_msg_age: bool = True) -> dict:
        if config.redirect_settings.enabled:
            confirm_url = await LinkExtractor(
                imap_server=config.redirect_settings.imap_server,
                email=config.redirect_settings.email,
                password=config.redirect_settings.password,
                redirect_email=self.account_data.email
            ).extract_link(None if config.imap_settings.use_proxy_for_imap is False else proxy, check_msg_age=check_msg_age)
        else:
            confirm_url = await LinkExtractor(
                imap_server=self.account_data.imap_server,
                email=self.account_data.email,
                password=self.account_data.password,
            ).extract_link(None if config.imap_settings.use_proxy_for_imap is False else proxy, check_msg_age=check_msg_age)

        return confirm_url

    async def _update_account_proxy(self, account_data: Accounts, attempt: int | str) -> None:
        max_attempts = config.attempts_and_delay_settings.max_register_attempts if config.module == "registration" else config.attempts_and_delay_settings.max_login_attempts if config.module == "login" else config.attempts_and_delay_settings.max_tasks_attempts if config.module == "complete_tasks" else config.attempts_and_delay_settings.max_stats_attempts if config.module == "export_stats" else config.attempts_and_delay_settings.max_farm_attempts

        proxy_changed_log = (
            f"Account: {self.account_data.email} | Proxy changed | "
            f"Retrying in {config.attempts_and_delay_settings.error_delay}s.. | "
            f"Attempt: {attempt + 1}/{max_attempts}.."
        )

        if not account_data:
            logger.info(proxy_changed_log)
            await asyncio.sleep(config.attempts_and_delay_settings.error_delay)
            return

        if account_data.active_account_proxy:
            await proxy_manager.release_proxy(account_data.active_account_proxy)

        proxy = await proxy_manager.get_proxy()
        await account_data.update_account_proxy(proxy.as_url if isinstance(proxy, Proxy) else proxy)

        logger.info(proxy_changed_log)
        await asyncio.sleep(config.attempts_and_delay_settings.error_delay)

    async def get_hcaptcha_token(self) -> Optional[str]:
        max_attempts = config.attempts_and_delay_settings.max_captcha_attempts

        async def handle_hcaptcha() -> Optional[str]:
            logger.info(f"Account: {self.account_data.email} | Solving hCaptcha | Attempt: {attempt + 1}/{max_attempts}")
            success, result = await captcha_solver.solve_hcaptcha(
                site_key="88768106-0292-4886-b0a2-fc92c48ea536",
                page_url="https://app.despeed.net/"
            )

            if success:
                logger.success(f"Account: {self.account_data.email} | hCaptcha solved successfully")
                return result

            raise ValueError(f"{result}")

        for attempt in range(max_attempts):
            try:
                return await handle_hcaptcha()
            except Exception as e:
                logger.error(
                    f"Account: {self.account_data.email} | Error occurred while solving hCaptcha: {str(e)} | Retrying..."
                )
                if attempt == max_attempts - 1:
                    raise CaptchaSolvingFailed(f"Failed to solve hCaptcha after {max_attempts} attempts")

    async def _extract_token_and_confirm_account(self, api: DespeedAPI, check_msg_age: bool = True) -> dict:
        data = await self._extract_link(check_msg_age=check_msg_age)
        if not data["status"]:
            return {}

        return await api.verify_email(data["data"])

    async def _register_account(self, api: DespeedAPI) -> dict:
        if not self.account_data.account_password:
            self.account_data.account_password = generate_password(random.randint(12, 16)) if config.application_settings.gen_random_pass_for_accounts else self.account_data.password

        username = generate_username(length=random.randint(12, 20))
        referral_code = random.choice(config.referral_codes)
        hcaptcha_token = await self.get_hcaptcha_token()

        return await api.sign_up(
            email=self.account_data.email,
            password=self.account_data.account_password,
            username=username,
            hcaptcha_token=hcaptcha_token,
            referral_code=referral_code
        )

    async def _login_account(self, api: DespeedAPI) -> tuple[str, str]:
        hcaptcha_token = await self.get_hcaptcha_token()
        return await api.login(
            email_or_username=self.account_data.email,
            password=self.account_data.account_password,
            hcaptcha_token=hcaptcha_token
        )

    async def _refresh_token(self, db_account_value: Accounts, api: DespeedAPI) -> bool:
        try:
            access_token, refresh_token = await api.refresh_token(db_account_value.refresh_token)
            await db_account_value.update_account(access_token=access_token, refresh_token=refresh_token)
            return True

        except Exception as error:
            logger.error(f"Account: {self.account_data.email} | Error occurred while refreshing token: {error}")
            return False

    @staticmethod
    async def _prepare_account_proxy(db_account_value: Accounts) -> str:
        if db_account_value and db_account_value.active_account_proxy:
            proxy = db_account_value.active_account_proxy
            if not proxy:
                proxy = await proxy_manager.get_proxy()
                await db_account_value.update_account(proxy=proxy.as_url if isinstance(proxy, Proxy) else proxy)
        else:
            proxy = await proxy_manager.get_proxy()

        return proxy.as_url if isinstance(proxy, Proxy) else proxy

    async def _save_account(self, db_account_value: Accounts, proxy: str, access_token: str, refresh_token: str) -> None:
        if db_account_value:
            await db_account_value.update_account(
                account_password=self.account_data.account_password,
                access_token=access_token,
                refresh_token=refresh_token,
                proxy=proxy
            )
        else:
            await Accounts.create(
                email=self.account_data.email,
                account_password=self.account_data.account_password,
                access_token=access_token,
                refresh_token=refresh_token,
                active_account_proxy=proxy
            )

    async def process_registration(self) -> OperationResult | None:
        max_attempts = config.attempts_and_delay_settings.max_register_attempts

        for attempt in range(max_attempts):
            db_account_value, last_completed_action, api, access_token = None, None, None, None

            try:
                db_account_value = await Accounts.get_account(email=self.account_data.email)
                if db_account_value and db_account_value.access_token:
                    logger.warning(f"Account: {self.account_data.email} | Account already logged in, skipped")
                    return operation_success(self.account_data.email, self.account_data.password)

                proxy = await self._prepare_account_proxy(db_account_value)
                api = DespeedAPI(proxy=proxy)

                if last_completed_action is None:
                    if not await self._is_email_valid(proxy):
                        return operation_failed(self.account_data.email, self.account_data.password)

                    last_completed_action = "email_validation"

                if last_completed_action == "email_validation":
                    logger.info(f"Account: {self.account_data.email} | Email is valid, registering...")
                    await self._register_account(api=api)

                    logger.success(f"Account: {self.account_data.email} | Confirmation email sent")
                    last_completed_action = "registration"

                if last_completed_action == "registration":
                    if not await self._extract_token_and_confirm_account(api=api):
                        logger.error(f"Account: {self.account_data.email} | Confirmation link not found | Exported to <<unverified_accounts.txt>>")
                        await self.handle_invalid_account(self.account_data.email, f"{self.account_data.password}:{self.account_data.account_password}", "unverified", log=False)
                        return None

                    last_completed_action = "confirmation_code"

                if last_completed_action == "confirmation_code":
                    logger.info(f"Account: {self.account_data.email} | Registration verified and completed, logging in..")
                    hcaptcha_token = await self.get_hcaptcha_token()
                    access_token, refresh_token = await api.login(
                        email_or_username=self.account_data.email,
                        password=self.account_data.account_password,
                        hcaptcha_token=hcaptcha_token
                    )

                await self._save_account(db_account_value, proxy, access_token, refresh_token)
                logger.success(f"Account: {self.account_data.email} | Logged in | Session saved to database")
                return operation_success(self.account_data.email, f"{self.account_data.password}:{self.account_data.account_password}")

            except APIError as error:
                if error.error_type == APIErrorType.ALREADY_REGISTERED:
                    logger.warning(f"Account: {self.account_data.email} | Email already registered")
                    return operation_success(self.account_data.email, self.account_data.password)

                elif error.error_type == APIErrorType.INVALID_CAPTCHA:
                    logger.error(f"Account: {self.account_data.email} | Invalid captcha | Attempt: {attempt + 1}/{max_attempts} | Retrying in {config.attempts_and_delay_settings.error_delay} seconds")
                    await asyncio.sleep(config.attempts_and_delay_settings.error_delay)
                    continue

                elif error.error_type == APIErrorType.DOMAIN_BLOCKED:
                    domain = self.account_data.email.split("@")[1]
                    logger.error(f"Account: {self.account_data.email} | Most likely domain <<{domain}>> is blocked | Skipped permanently")
                    return operation_failed(self.account_data.email, self.account_data.password)

                if last_completed_action in ("registration", "confirmation_code"):
                    logger.warning(f"Account: {self.account_data.email} | Email registered but not verified, error: {error} | Exported to <<unverified_accounts.txt>>")
                    await self.handle_invalid_account(self.account_data.email, f"{self.account_data.password}:{self.account_data.account_password}", "unverified", log=False)
                    return None

                logger.error(f"Account: {self.account_data.email} | Error occurred during registration (APIError): {error} | Skipped permanently")
                return operation_failed(self.account_data.email, self.account_data.password)

            except EmailValidationFailed as error:
                if attempt == max_attempts - 1:
                    logger.error(f"Account: {self.account_data.email} | Max attempts reached, unable to register")
                    return operation_failed(self.account_data.email, self.account_data.password)

                logger.error(f"Account: {self.account_data.email} | {error}")
                await self._update_account_proxy(db_account_value, attempt)

            except Exception as error:
                is_last_attempt = attempt == max_attempts - 1
                if is_last_attempt:
                    if last_completed_action in ("registration", "confirmation_code"):
                        logger.warning(f"Account: {self.account_data.email} | Email registered but not verified | Exported to <<unverified_accounts.txt>>")
                        await self.handle_invalid_account(self.account_data.email, f"{self.account_data.password}:{self.account_data.account_password}", "unverified", log=False)
                        return None

                    logger.error(f"Account: {self.account_data.email} | Max attempts reached, unable to register")
                    return operation_failed(self.account_data.email, self.account_data.password)

                error = validate_error(error)
                logger.error(f"Account: {self.account_data.email} | Error occurred during registration (Generic Exception): {error}")
                await self._update_account_proxy(db_account_value, attempt)

            finally:
                if api:
                    await api.close_session()

    async def process_verify(self):
        max_attempts = config.attempts_and_delay_settings.max_verify_attempts

        for attempt in range(max_attempts):
            db_account_value, last_completed_action, api, access_token, refresh_token = None, None, None, None, None

            try:
                db_account_value = await Accounts.get_account(email=self.account_data.email)
                if db_account_value and db_account_value.access_token:
                    logger.warning(f"Account: {self.account_data.email} | Account already logged in, skipped")
                    return operation_success(self.account_data.email, self.account_data.password)

                proxy = await self._prepare_account_proxy(db_account_value)
                api = DespeedAPI(proxy=proxy)

                if last_completed_action is None:
                    if not await self._is_email_valid(proxy):
                        return operation_failed(self.account_data.email, self.account_data.password)

                    last_completed_action = "email_validation"

                if last_completed_action == "email_validation":
                    if not await self._extract_token_and_confirm_account(api=api, check_msg_age=False):
                        logger.error(f"Account: {self.account_data.email} | Confirmation link not found | Exported to <<unverified_accounts.txt>>")
                        await self.handle_invalid_account(self.account_data.email, f"{self.account_data.password}:{self.account_data.account_password}", "unverified", log=False)
                        return None

                    last_completed_action = "confirmation_code"

                if last_completed_action == "confirmation_code":
                    logger.info(f"Account: {self.account_data.email} | Email verified, logging in..")
                    hcaptcha_token = await self.get_hcaptcha_token()
                    access_token, refresh_token = await api.login(
                        email_or_username=self.account_data.email,
                        password=self.account_data.account_password,
                        hcaptcha_token=hcaptcha_token
                    )

                await self._save_account(db_account_value, proxy, access_token, refresh_token)
                logger.success(f"Account: {self.account_data.email} | Logged in | Session saved to database")
                return operation_success(self.account_data.email, f"{self.account_data.password}:{self.account_data.account_password}")

            except APIError as error:
                if error.error_type == APIErrorType.INVALID_CAPTCHA:
                    logger.error(f"Account: {self.account_data.email} | Invalid captcha | Attempt: {attempt + 1}/{max_attempts} | Retrying in {config.attempts_and_delay_settings.error_delay} seconds")
                    await asyncio.sleep(config.attempts_and_delay_settings.error_delay)
                    continue

                if last_completed_action in ("login", "confirmation_code"):
                    logger.warning(f"Account: {self.account_data.email} | Email registered but not verified, error: {error} | Exported to <<unverified_accounts.txt>>")
                    await self.handle_invalid_account(self.account_data.email, f"{self.account_data.password}:{self.account_data.account_password}", "unverified", log=False)
                    return None

                logger.error(f"Account: {self.account_data.email} | Error occurred during verification (APIError): {error} | Skipped permanently")
                return operation_failed(self.account_data.email, f"{self.account_data.password}:{self.account_data.account_password}")

            except EmailValidationFailed as error:
                if attempt == max_attempts - 1:
                    logger.error(f"Account: {self.account_data.email} | Max attempts reached, unable to verify")
                    return operation_failed(self.account_data.email, f"{self.account_data.password}:{self.account_data.account_password}")

                logger.error(f"Account: {self.account_data.email} | {error}")
                await self._update_account_proxy(db_account_value, attempt)

            except Exception as error:
                is_last_attempt = attempt == max_attempts - 1
                if is_last_attempt:
                    if last_completed_action in ("email_validation", "confirmation_code"):
                        logger.warning(f"Account: {self.account_data.email} | Email registered but not verified | Exported to <<unverified_accounts.txt>>")
                        await self.handle_invalid_account(self.account_data.email, f"{self.account_data.password}:{self.account_data.account_password}", "unverified", log=False)
                        return None

                    logger.error(f"Account: {self.account_data.email} | Max attempts reached, unable to verify")
                    return operation_failed(self.account_data.email, f"{self.account_data.password}:{self.account_data.account_password}")

                error = validate_error(error)
                logger.error(f"Account: {self.account_data.email} | Error occurred during verification (Generic Exception): {error}")
                await self._update_account_proxy(db_account_value, attempt)

            finally:
                if api:
                    await api.close_session()

    async def process_login(self) -> OperationResult | None:
        max_attempts = config.attempts_and_delay_settings.max_login_attempts

        for attempt in range(max_attempts):
            db_account_value = None
            api = None

            try:
                db_account_value = await Accounts.get_account(email=self.account_data.email)
                if config.application_settings.skip_logged_accounts and db_account_value and db_account_value.access_token:
                    logger.warning(f"Account: {self.account_data.email} | Account already logged in, skipped")
                    return operation_success(self.account_data.email, self.account_data.account_password)

                logger.info(f"Account: {self.account_data.email} | Logging in")
                proxy = await self._prepare_account_proxy(db_account_value)
                api = DespeedAPI(proxy=proxy)

                access_token, refresh_token = await self._login_account(api=api)
                await self._save_account(db_account_value, proxy, access_token, refresh_token)

                logger.success(f"Account: {self.account_data.email} | Logged in | Session saved to database")
                return operation_success(self.account_data.email, self.account_data.account_password)

            except APIError as error:
                if error.error_type == APIErrorType.UNVERIFIED_EMAIL:
                    await self.handle_invalid_account(self.account_data.email, self.account_data.account_password, "unverified")
                    return None

                elif error.error_type == APIErrorType.INVALID_CAPTCHA:
                    logger.error(f"Account: {self.account_data.email} | Invalid captcha | Attempt: {attempt + 1}/{max_attempts} | Retrying in {config.attempts_and_delay_settings.error_delay} seconds")
                    await asyncio.sleep(config.attempts_and_delay_settings.error_delay)
                    continue

                logger.error(f"Account: {self.account_data.email} | Error occurred while logging in (APIError): {error} | Skipped permanently")
                return operation_failed(self.account_data.email, self.account_data.account_password)

            except Exception as error:
                is_last_attempt = attempt == max_attempts - 1
                if is_last_attempt:
                    logger.error(f"Account: {self.account_data.email} | Max attempts reached, unable to login")
                    return operation_failed(self.account_data.email, self.account_data.account_password)
                else:
                    error = validate_error(error)
                    logger.error(f"Account: {self.account_data.email} | Error occurred while logging in (Generic Exception): {error}")
                    await self._update_account_proxy(db_account_value, attempt)

            finally:
                if api:
                    await api.close_session()

    async def process_claim_daily_reward(self):
        max_attempts = config.attempts_and_delay_settings.max_daily_reward_attempts

        for attempt in range(max_attempts):
            db_account_value, api = None, None

            try:
                db_account_value = await Accounts.get_account(email=self.account_data.email)
                if not db_account_value or not db_account_value.access_token:
                    await self.handle_invalid_account(self.account_data.email, self.account_data.password, "unlogged")
                    return

                proxy = await self._prepare_account_proxy(db_account_value)
                api = DespeedAPI(access_token=db_account_value.access_token, proxy=proxy)

                profile_info = await api.profile_info()
                if profile_info["last_claimed_at"] is None:
                    logger.info(f"Account: {self.account_data.email} | Claiming daily reward..")
                    await api.daily_claim()
                    logger.success(f"Account: {self.account_data.email} | Daily reward claimed")

                daily_earning = profile_info["daily_earning"]
                season_earning = profile_info["season_earning"]

                logger.info(f"Account: {self.account_data.email} | Daily earning: {daily_earning} | Season earning: {season_earning}")
                return

            except APIError as error:
                is_last_attempt = attempt == max_attempts - 1
                if is_last_attempt:
                    logger.error(f"Account: {self.account_data.email} | Max attempts reached, unable to claim daily reward")
                    return
                else:
                    if error.error_type == APIErrorType.UNVERIFIED_EMAIL:
                        await self.handle_invalid_account(self.account_data.email, self.account_data.account_password, "unverified")
                        return

                    elif error.error_type == APIErrorType.TOKEN_EXPIRED:
                        logger.warning(f"Account: {self.account_data.email} | Token expired, refreshing..")

                        if await self._refresh_token(db_account_value, api):
                            logger.success(f"Account: {self.account_data.email} | Token refreshed | Retrying in {config.attempts_and_delay_settings.error_delay} seconds")
                            await asyncio.sleep(config.attempts_and_delay_settings.error_delay)
                            continue

                    logger.error(f"Account: {self.account_data.email} | Error occurred while claiming daily reward (APIError): {error} | Skipped until next cycle")

            except Exception as error:
                is_last_attempt = attempt == max_attempts - 1
                if is_last_attempt:
                    logger.error(f"Account: {self.account_data.email} | Max attempts reached, unable to claim daily reward")
                    return
                else:
                    error = validate_error(error)
                    logger.error(f"Account: {self.account_data.email} | Error occurred while claiming daily reward (Generic Exception): {error}")
                    await self._update_account_proxy(db_account_value, attempt)

            finally:
                if api:
                    await api.close_session()

    async def process_export_stats(self):
        max_attempts = config.attempts_and_delay_settings.max_stats_attempts

        for attempt in range(max_attempts):
            db_account_value, api = None, None

            try:
                db_account_value = await Accounts.get_account(email=self.account_data.email)
                if not db_account_value or not db_account_value.access_token:
                    await self.handle_invalid_account(self.account_data.email, self.account_data.password, "unlogged")
                    return

                logger.info(f"Account: {self.account_data.email} | Exporting stats..")
                proxy = await self._prepare_account_proxy(db_account_value)
                api = DespeedAPI(access_token=db_account_value.access_token, proxy=proxy)

                profile_info = await api.profile_info()
                profile_info["account_password"] = db_account_value.account_password
                await self._refresh_token(db_account_value, api)
                logger.success(f"Account: {self.account_data.email} | Stats exported")
                return operation_success(self.account_data.email, profile_info)

            except APIError as error:
                is_last_attempt = attempt == max_attempts - 1
                if is_last_attempt:
                    logger.error(f"Account: {self.account_data.email} | Max attempts reached, unable to export stats")
                    return operation_failed(self.account_data.email, {})
                else:
                    if error.error_type == APIErrorType.UNVERIFIED_EMAIL:
                        await self.handle_invalid_account(self.account_data.email, self.account_data.account_password, "unverified")
                        return None

                    elif error.error_type == APIErrorType.TOKEN_EXPIRED:
                        logger.warning(f"Account: {self.account_data.email} | Token expired, refreshing..")

                        if await self._refresh_token(db_account_value, api):
                            logger.success(f"Account: {self.account_data.email} | Token refreshed | Retrying in {config.attempts_and_delay_settings.error_delay} seconds")
                            await asyncio.sleep(config.attempts_and_delay_settings.error_delay)
                            continue

                    logger.error(f"Account: {self.account_data.email} | Error occurred while exporting stats (APIError): {error} | Skipped permanently")
                    return operation_failed(self.account_data.email, {})

            except Exception as error:
                is_last_attempt = attempt == max_attempts - 1
                if is_last_attempt:
                    logger.error(f"Account: {self.account_data.email} | Max attempts reached, unable to export stats")
                    return operation_failed(self.account_data.email, {})
                else:
                    error = validate_error(error)
                    logger.error(f"Account: {self.account_data.email} | Error occurred while exporting stats (Generic Exception): {error}")
                    await self._update_account_proxy(db_account_value, attempt)

            finally:
                if api:
                    await api.close_session()

    async def process_farm(self):
        max_attempts = config.attempts_and_delay_settings.max_farm_attempts

        for attempt in range(max_attempts):
            db_account_value, api = None, None

            try:
                db_account_value = await Accounts.get_account(email=self.account_data.email)
                if not db_account_value or not db_account_value.access_token:
                    await self.handle_invalid_account(self.account_data.email, self.account_data.password, "unlogged")
                    return

                proxy = await self._prepare_account_proxy(db_account_value)
                api = DespeedAPI(access_token=db_account_value.access_token, proxy=proxy)

                if db_account_value.sleep_until:
                    sleep_duration = await handle_sleep(self.account_data.email, db_account_value.sleep_until)
                    if sleep_duration is True:
                        return

                speedtest_eligibility = await api.speedtest_eligibility()
                if speedtest_eligibility["isEligible"] is False and not db_account_value.sleep_until:
                    sleep_until = speedtest_eligibility["timing"]["nextTime"]
                    sleep_until_utc = parse_iso_to_pytz_utc(sleep_until)
                    sleep_duration = get_sleep_duration(sleep_until=sleep_until_utc, to_minutes=True)

                    logger.info(f"Account: {self.account_data.email} | Account not eligible for speed test, next test will be available in {sleep_duration:.2f} minutes")

                    await db_account_value.set_sleep_until(sleep_until_utc)
                    if config.application_settings.auto_claim_daily_reward:
                        return await self.process_claim_daily_reward()
                    return

                else:
                    logger.info(f"Account: {self.account_data.email} | Account is eligible for speed test, starting test..")
                    # DISABLED WHILE THE EXTENSION IS NOT CHECKING FOR THE SPEED TEST NOW #
                    # speed_test_client = SpeedTest(account=self.account_data, proxy=proxy)
                    # download_speed, upload_speed = await speed_test_client.run()
                    latitude, longitude = await api.request_latitude_and_logitude()

                    response = await api.send_speed_test_results(
                        download_speed=0,
                        upload_speed=0,
                        latitude=latitude,
                        logitude=longitude
                    )

                    next_speed_test_time = response["nextEligibilty"]["timing"]["nextTime"]
                    next_speed_test_time_utc = parse_iso_to_pytz_utc(next_speed_test_time)
                    sleep_duration = get_sleep_duration(sleep_until=next_speed_test_time_utc, to_minutes=True)

                    logger.success(f"Account: {self.account_data.email} | Speed test completed, next will be available in {sleep_duration:.2f} minutes..")

                    await db_account_value.set_sleep_until(next_speed_test_time_utc)
                    if config.application_settings.auto_claim_daily_reward:
                        return await self.process_claim_daily_reward()
                    return

            except APIError as error:
                is_last_attempt = attempt == max_attempts - 1
                if is_last_attempt:
                    logger.error(f"Account: {self.account_data.email} | Max attempts reached, unable to farm")
                    return
                else:
                    if error.error_type == APIErrorType.UNVERIFIED_EMAIL:
                        await self.handle_invalid_account(self.account_data.email, self.account_data.account_password, "unverified")
                        return None

                    elif error.error_type == APIErrorType.TOKEN_EXPIRED:
                        logger.warning(f"Account: {self.account_data.email} | Token expired, refreshing..")

                        if await self._refresh_token(db_account_value, api):
                            logger.success(f"Account: {self.account_data.email} | Token refreshed | Retrying in {config.attempts_and_delay_settings.error_delay} seconds")
                            await asyncio.sleep(config.attempts_and_delay_settings.error_delay)
                            continue

                    logger.error(f"Account: {self.account_data.email} | Error occurred while farm (APIError): {error} | Skipped until next cycle")

            except Exception as error:
                is_last_attempt = attempt == max_attempts - 1
                if is_last_attempt:
                    logger.error(f"Account: {self.account_data.email} | Max attempts reached, unable to farm")
                    return
                else:
                    error = validate_error(error)
                    logger.error(f"Account: {self.account_data.email} | Error occurred while farm (Generic Exception): {error}")
                    await self._update_account_proxy(db_account_value, attempt)

            finally:
                if api:
                    await api.close_session()
