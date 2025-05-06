from enum import Enum


class APIErrorType(Enum):
    UNVERIFIED_EMAIL = "Please verify your email to log in"
    ALREADY_REGISTERED = "Email is already in use."
    INVALID_CAPTCHA = "Captcha validation failed, please try again!"


class APIError(Exception):
    def __init__(self, error: str, response_data: dict = None):
        self.error = error
        self.response_data = response_data
        self.error_type = self._get_error_type()
        super().__init__(error)

    def _get_error_type(self) -> APIErrorType | None:
        return next(
            (error_type for error_type in APIErrorType if error_type.value == self.error_message),
            None
        )

    @property
    def error_message(self) -> str:
        if self.response_data and "message" in self.response_data:
            return self.response_data["message"]
        return self.error

    def __str__(self):
        return self.error


class CaptchaSolvingFailed(Exception):
    """Raised when the captcha solving failed"""

    pass


class ServerError(Exception):
    """Raised when the server returns an error"""

    pass


class NoAvailableProxies(Exception):
    """Raised when there are no available proxies"""

    pass


class ProxyForbidden(Exception):
    """Raised when the proxy is forbidden"""

    pass


class EmailValidationFailed(Exception):
    """Raised when the email validation failed"""

    pass
