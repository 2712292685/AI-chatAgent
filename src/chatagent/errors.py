"""项目异常体系：所有面向用户的错误都带一句中文说明。"""


class ChatAgentError(Exception):
    """chatagent 可预期错误的基类。

    ``message`` 是可以直接展示给终端用户的中文说明，不包含堆栈或原始 JSON。
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ConfigError(ChatAgentError):
    """配置文件缺失、字段缺失、类型错误或取值越界。"""


class AuthError(ChatAgentError):
    """密钥缺失或无效。"""


class RateLimitError(ChatAgentError):
    """请求被服务端限流。"""


class NetworkError(ChatAgentError):
    """网络连接失败或超时。"""


class ApiError(ChatAgentError):
    """服务端返回的其他错误。"""
