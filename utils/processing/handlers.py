import pytz

from datetime import datetime
from functools import wraps
from typing import Callable

from core.exceptions.base import APIError


def require_access_token(func: Callable) -> Callable:
    @wraps(func)
    async def wrapper(self, *args, **kwargs):
        if not self.access_token:
            raise APIError("Authentication token is required.")
        return await func(self, *args, **kwargs)

    return wrapper


async def handle_sleep(email: str, sleep_until) -> bool:
    current_time = datetime.now(pytz.UTC)
    sleep_until = sleep_until.replace(tzinfo=pytz.UTC)

    if sleep_until > current_time:
        # sleep_duration = (sleep_until - current_time).total_seconds() / 60
        # logger.debug(
        #     f"Account: {email} | Sleeping until next speed test {sleep_until} (duration: {sleep_duration:.0f} minutes)"
        # )
        return True

    return False
