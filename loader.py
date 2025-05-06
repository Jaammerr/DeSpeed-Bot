import asyncio

from core.captcha.solvium import SolviumCaptchaSolver
from utils import load_config, FileOperations, ProxyManager

config = load_config()
captcha_solver = SolviumCaptchaSolver(
    api_key=config.captcha_settings.solvium_captcha_api_key,
    max_attempts=config.captcha_settings.max_captcha_solving_time // 3
)


file_operations = FileOperations()
semaphore = asyncio.Semaphore(config.application_settings.threads)
proxy_manager = ProxyManager(check_uniqueness=config.application_settings.check_uniqueness_of_proxies)
