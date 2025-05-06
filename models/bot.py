from typing import Literal, TypedDict


ModuleType = Literal["register", "stats", "accounts", "verify", "login"]


class OperationResult(TypedDict):
    identifier: str
    data: str | dict
    status: bool
