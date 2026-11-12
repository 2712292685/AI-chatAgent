"""机器人控制 HTTP 服务的最小客户端。

只依赖标准库 ``urllib``，不引入额外运行时依赖。所有错误都翻译成带中文说明的
:class:`RobotClientError`，调用方可以把它直接展示给用户。POST 请求使用 UUID4
作为 ``request_id``；提交阶段如遇网络抖动或 5xx，会用同一个 id 重试一次，
由服务端负责幂等。
"""

from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import Callable, Mapping
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .config import ROBOT_BASE_URL_ENV, ROBOT_TOKEN_ENV, Config, RobotConfig
from .errors import ChatAgentError

TERMINAL_JOB_STATES = frozenset({"succeeded", "failed", "aborted", "timed_out"})
POLL_INTERVAL_SECONDS = 0.2
BASE_URL_ENV = ROBOT_BASE_URL_ENV
TOKEN_ENV = ROBOT_TOKEN_ENV
_SUBMIT_RETRIES = 1
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class RobotClientError(ChatAgentError):
    """机器人控制服务不可达、拒绝请求或返回了非法数据。"""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        payload: Mapping[str, Any] | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message, retryable=retryable)
        self.status_code = status_code
        self.payload = dict(payload) if payload is not None else {}


# 兼容更直观的旧命名；新代码优先使用 RobotClientError。
RobotAPIError = RobotClientError


class RobotClient:
    """访问 agent_control HTTP API 的同步客户端。"""

    def __init__(
        self,
        cfg: RobotConfig | Config | None = None,
        *,
        base_url: str | None = None,
        token: str | None = None,
        environ: Mapping[str, str] | None = None,
        timeout_seconds: float | None = None,
        poll_interval: float = POLL_INTERVAL_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        config = cfg.robot if isinstance(cfg, Config) else cfg or RobotConfig()
        env = os.environ if environ is None else environ
        resolved = base_url or env.get(ROBOT_BASE_URL_ENV, "").strip() or config.base_url
        self.base_url = _normalise_base_url(resolved)
        self.token = env.get(ROBOT_TOKEN_ENV, "").strip() if token is None else token.strip()
        self.timeout_seconds = float(
            config.timeout_seconds if timeout_seconds is None else timeout_seconds
        )
        if poll_interval <= 0:
            raise ValueError("poll_interval 必须大于 0")
        self.poll_interval = float(poll_interval)
        self._sleep = sleep
        self._clock = clock

    def get_health(self, *, timeout_seconds: float | None = None) -> dict[str, Any]:
        """GET /api/health；服务或驱动不健康时抛 :class:`RobotClientError`。"""
        return self._request_json("GET", "/api/health", timeout_seconds=timeout_seconds)

    def health(self, *, timeout_seconds: float | None = None) -> dict[str, Any]:
        """``get_health`` 的短别名。"""
        return self.get_health(timeout_seconds=timeout_seconds)

    def get_status(self) -> dict[str, Any]:
        """GET /api/status，返回缓存状态。"""
        return self._request_json("GET", "/api/status")

    def status(self) -> dict[str, Any]:
        """``get_status`` 的短别名。"""
        return self.get_status()

    def move(
        self,
        direction: str,
        distance_m: float,
        *,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """提交移动任务（前进/后退/左右平移），返回 202 响应里的任务记录。"""
        return self._submit(
            "/api/move",
            {"direction": direction, "distance_m": distance_m},
            request_id,
        )

    def turn(
        self,
        direction: str,
        angle_deg: float,
        *,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """提交原地转向任务，返回任务记录。"""
        return self._submit(
            "/api/turn",
            {"direction": direction, "angle_deg": angle_deg},
            request_id,
        )

    def posture(self, command: str, *, request_id: str | None = None) -> dict[str, Any]:
        """提交姿态命令，返回任务记录。"""
        return self._submit("/api/command", {"command": command}, request_id)

    def command(self, command: str, *, request_id: str | None = None) -> dict[str, Any]:
        """``posture`` 的别名，强调对应 ``/api/command``。"""
        return self.posture(command, request_id=request_id)

    def stop(self) -> dict[str, Any]:
        """停止当前运动；该接口不锁存急停。"""
        return self._request_json("POST", "/api/stop", {})

    def emergency_stop(self) -> dict[str, Any]:
        """锁存软件急停。"""
        return self._request_json("POST", "/api/estop", {})

    def estop(self) -> dict[str, Any]:
        """``emergency_stop`` 的短别名。"""
        return self.emergency_stop()

    def clear_estop(self) -> dict[str, Any]:
        """解除急停；服务端会要求机器狗重新取得控制权并恢复姿态。"""
        return self._request_json("POST", "/api/estop/clear", {})

    def get_job(self, request_id: str) -> dict[str, Any]:
        """GET /api/jobs/<request_id>。"""
        if not request_id:
            raise ValueError("request_id 不能为空")
        data = self._request_json("GET", f"/api/jobs/{request_id}")
        return _as_job(data, request_id)

    def job(self, request_id: str) -> dict[str, Any]:
        """``get_job`` 的短别名。"""
        return self.get_job(request_id)

    def wait_for_job(
        self,
        request_id: str,
        *,
        timeout_seconds: float | None = None,
        poll_interval: float | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        """每 200 ms 轮询任务，直到进入终态。

        轮询期间客户端仍活着，服务端会据此续租；返回最后一个任务记录。
        本地等待超时抛 :class:`RobotClientError`，但不会伪造任务成功。
        """
        interval = self.poll_interval if poll_interval is None else float(poll_interval)
        if interval <= 0:
            raise ValueError("poll_interval 必须大于 0")
        deadline = None if timeout_seconds is None else self._clock() + float(timeout_seconds)
        while True:
            job = self.get_job(request_id)
            state = _job_state(job)
            if state in TERMINAL_JOB_STATES:
                return job
            if should_stop is not None and should_stop():
                return job
            if deadline is not None and self._clock() >= deadline:
                raise RobotClientError(
                    f"等待机器人任务超时（request_id={request_id}，最后状态={state or 'unknown'}）",
                    payload=job,
                )
            self._sleep(interval)

    # 更短的别名，便于调用方表达“轮询到结束”。
    poll_job = wait_for_job
    wait_job = wait_for_job

    def _submit(
        self,
        path: str,
        payload: Mapping[str, Any],
        request_id: str | None,
    ) -> dict[str, Any]:
        rid = request_id or str(uuid.uuid4())
        body = {"request_id": rid, **payload}
        attempt = 0
        while True:
            try:
                data = self._request_json("POST", path, body)
                return _as_job(data, rid)
            except RobotClientError as exc:
                if not exc.retryable or attempt >= _SUBMIT_RETRIES:
                    raise
                attempt += 1
                self._sleep(self.poll_interval)

    def _request_json(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        body: bytes | None = None
        headers = {
            "Accept": "application/json",
            "User-Agent": "chatagent-robot/0.5.0",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"

        request = Request(
            self.base_url + path,
            data=body,
            headers=headers,
            method=method,
        )
        timeout = self.timeout_seconds if timeout_seconds is None else float(timeout_seconds)
        try:
            with urlopen(request, timeout=timeout) as response:
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            data = _read_error_payload(exc)
            detail = _error_message(data)
            message = f"机器人控制服务返回 HTTP {exc.code}"
            if detail:
                message = f"{message}：{detail}"
            raise RobotClientError(
                message,
                status_code=exc.code,
                payload=data,
                retryable=exc.code >= 500 or exc.code == 429,
            ) from None
        except (URLError, TimeoutError) as exc:
            reason = getattr(exc, "reason", exc)
            raise RobotClientError(
                f"无法连接机器人控制服务 {self.base_url}：{reason}",
                retryable=True,
            ) from None
        except OSError as exc:
            raise RobotClientError(
                f"无法连接机器人控制服务 {self.base_url}：{exc}",
                retryable=True,
            ) from None

        if len(raw) > _MAX_RESPONSE_BYTES:
            raise RobotClientError("机器人控制服务返回的数据过大")
        if not raw.strip():
            return {}
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RobotClientError("机器人控制服务返回了无法解析的 JSON") from exc
        if not isinstance(value, dict):
            raise RobotClientError("机器人控制服务返回的 JSON 必须是对象")
        return value


def _normalise_base_url(value: str) -> str:
    text = value.strip()
    try:
        parsed = urlsplit(text)
        hostname = parsed.hostname
        _ = parsed.port  # 触发非法端口的 ValueError
    except ValueError:
        parsed_scheme = ""
        hostname = None
    else:
        parsed_scheme = parsed.scheme.lower()
    if parsed_scheme not in {"http", "https"} or not hostname:
        raise RobotClientError(
            f"机器人控制服务地址无效：{value!r}，应为 http://127.0.0.1:8100 这样的 URL"
        )
    return text.rstrip("/")


def _read_error_payload(exc: HTTPError) -> dict[str, Any]:
    try:
        raw = exc.read(_MAX_RESPONSE_BYTES)
    except OSError:
        return {}
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {"message": raw.decode("utf-8", errors="replace")[:500]}
    return value if isinstance(value, dict) else {}


def _error_message(payload: Mapping[str, Any]) -> str:
    value = payload.get("error")
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        message = value.get("message")
        if isinstance(message, str):
            return message
        code = value.get("code")
        if isinstance(code, str):
            return code
    message = payload.get("message")
    return message if isinstance(message, str) else ""


def _as_job(data: Mapping[str, Any], request_id: str) -> dict[str, Any]:
    value = data.get("job")
    job = dict(value) if isinstance(value, Mapping) else dict(data)
    outer = data.get("request_id")
    fallback = outer if isinstance(outer, str) and outer else request_id
    job.setdefault("request_id", fallback)
    return job


def _job_state(job: Mapping[str, Any]) -> str:
    state = job.get("state")
    return state if isinstance(state, str) else ""
