"""机器人工具的 OpenAI schema、参数校验与执行器。

工具参数在这里做第二道硬校验：即使模型被提示词误导，也不能把布尔值当数字、
把 NaN 发给服务，或对左右平移使用前后的距离上限。执行器只根据 HTTP 服务返回
的任务结果陈述事实，绝不把失败包装成成功。
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .config import Config, RobotConfig
from .errors import ChatAgentError
from .robot_client import RobotClient, RobotClientError

FORWARD_DISTANCE_RANGE = (0.05, 5.0)
LATERAL_DISTANCE_RANGE = (0.05, 3.0)
TURN_ANGLE_RANGE = (1.0, 180.0)
MOVE_DIRECTIONS = frozenset({"forward", "backward", "left", "right"})
TURN_DIRECTIONS = frozenset({"left", "right"})
POSTURE_COMMANDS = frozenset({"stand_up", "sit_down", "balance_stand", "move_mode", "lock"})
STATUS_TOOL = "robot_status"
ACTION_TOOL_NAMES = frozenset(
    {
        "robot_move",
        "robot_turn",
        "robot_posture",
        "robot_stop",
        "robot_emergency_stop",
        "robot_clear_estop",
    }
)
TOOL_NAMES = frozenset({STATUS_TOOL, *ACTION_TOOL_NAMES})


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "robot_status",
            "description": "查询机器狗状态：连接、控制权、姿态、电量、位置和故障信息。",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "robot_move",
            "description": (
                "让机器狗移动指定距离。direction=forward/backward 为前进/后退，"
                "距离范围 0.05–5 米；left/right 为左右平移，距离范围 0.05–3 米，"
                "不是转向。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {
                        "type": "string",
                        "enum": ["forward", "backward", "left", "right"],
                        "description": "移动方向；left/right 是横向平移。",
                    },
                    "distance_m": {
                        "type": "number",
                        "minimum": 0.05,
                        "maximum": 5.0,
                        "description": ("移动距离（米）。前后 0.05–5；左右平移 0.05–3。"),
                    },
                },
                "required": ["direction", "distance_m"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "robot_turn",
            "description": "让机器狗原地转向指定角度；不是平移。",
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {
                        "type": "string",
                        "enum": ["left", "right"],
                        "description": "转向方向：左转或右转。",
                    },
                    "angle_deg": {
                        "type": "number",
                        "minimum": 1.0,
                        "maximum": 180.0,
                        "description": "原地转向角度（度），范围 1–180。",
                    },
                },
                "required": ["direction", "angle_deg"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "robot_posture",
            "description": "改变机器狗姿态。移动失败时不要自动调用，先征得用户同意。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "enum": [
                            "stand_up",
                            "sit_down",
                            "balance_stand",
                            "move_mode",
                            "lock",
                        ],
                        "description": "姿态命令：站立、趴下、平衡站立、移动模式或锁定。",
                    }
                },
                "required": ["command"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "robot_stop",
            "description": "停止当前运动，不锁存急停。用户要求停下时使用。",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "robot_emergency_stop",
            "description": "立即锁存软件急停。出现碰撞、失控或紧急危险时使用。",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "robot_clear_estop",
            "description": (
                "解除急停锁存。机器狗会重新取得控制权并恢复站立；"
                "必须由用户明确要求，CLI 还会二次确认。"
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
]

# OpenAI 请求里直接传这个列表；用副本避免调用方修改模块常量。
TOOLS = TOOL_SCHEMAS
TOOL_DEFINITIONS = TOOL_SCHEMAS


def tool_schemas() -> list[dict[str, Any]]:
    """返回工具 schema 列表；每次调用给出浅拷贝，避免调用方改模块常量。"""
    return [dict(schema) for schema in TOOL_SCHEMAS]


def get_tool_schemas() -> list[dict[str, Any]]:
    """``tool_schemas`` 的别名。"""
    return tool_schemas()


class ToolValidationError(ValueError):
    """工具名或参数不合法；``code`` 用于写回结构化工具结果。"""

    def __init__(self, message: str, *, code: str = "invalid_arguments") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


@dataclass(frozen=True)
class PlannedToolCall:
    """session 执行工具前的计划项；``priority`` 越小越先执行。"""

    tool_call: dict[str, Any]
    error: str | None = None
    priority: int = 2


class ToolExecutor:
    """把一个工具调用转换成 JSON 字符串结果。"""

    def __init__(
        self,
        client: RobotClient,
        cfg: RobotConfig | Config | None = None,
        *,
        confirm_clear_estop: Callable[[], bool] | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self.client = client
        config = cfg.robot if isinstance(cfg, Config) else cfg or RobotConfig()
        self.timeout_seconds = float(
            config.timeout_seconds if timeout_seconds is None else timeout_seconds
        )
        self._confirm_clear_estop = confirm_clear_estop or _deny_clear_estop

    def execute(
        self,
        name: str,
        arguments: str | Mapping[str, Any] | None = None,
    ) -> str:
        """执行一个工具并返回 JSON 字符串；可预期错误也编码进结果。"""
        try:
            args = validate_tool_call(name, arguments)
        except ToolValidationError as exc:
            return tool_error_result(exc.message, code=exc.code)
        except json.JSONDecodeError:
            return tool_error_result("工具参数不是合法 JSON", code="invalid_arguments")

        try:
            return self._execute_valid(name, args)
        except RobotClientError as exc:
            request_id = _request_id_from_payload(exc.payload)
            code = _error_code_from_payload(exc.payload) or "robot_api_error"
            return tool_error_result(
                exc.message,
                code=code,
                request_id=request_id,
                status_code=exc.status_code,
            )
        except ChatAgentError as exc:
            return tool_error_result(exc.message, code="robot_api_error")
        except Exception as exc:  # noqa: BLE001 - 工具结果必须成对写回，不能崩掉会话
            return tool_error_result(f"工具执行失败：{exc}", code="tool_failed")

    def _execute_valid(self, name: str, args: dict[str, Any]) -> str:
        if name == "robot_status":
            return self._status()
        if name == "robot_move":
            direction = _text_arg(args, "direction")
            distance = _number_arg(args, "distance_m")
            job = self.client.move(direction, distance)
            return self._wait_result(
                job,
                requested={"direction": direction, "distance_m": distance},
            )
        if name == "robot_turn":
            direction = _text_arg(args, "direction")
            angle = _number_arg(args, "angle_deg")
            job = self.client.turn(direction, angle)
            return self._wait_result(job, requested={"direction": direction, "angle_deg": angle})
        if name == "robot_posture":
            command = _text_arg(args, "command")
            job = self.client.posture(command)
            return self._wait_result(job, requested={"command": command})
        if name == "robot_stop":
            return _simple_result(self.client.stop())
        if name == "robot_emergency_stop":
            return _simple_result(self.client.emergency_stop())
        if name == "robot_clear_estop":
            if not self._confirm_clear_estop():
                return tool_error_result(
                    "用户取消了急停恢复；机器狗保持急停锁存。",
                    code="cancelled",
                    state="aborted",
                )
            return _simple_result(self.client.clear_estop())
        raise ToolValidationError(f"未知工具：{name}", code="unknown_tool")

    def _status(self) -> str:
        status = self.client.get_status()
        stale = bool(status.get("stale"))
        result: dict[str, Any] = {
            **status,
            "ok": not stale,
            "request_id": None,
            "state": "stale" if stale else "succeeded",
            "error": "机器人状态已过期（驱动可能断开）" if stale else "",
            "measured": {},
            "status": status,
        }
        return _dump(result)

    def _wait_result(
        self,
        job: Mapping[str, Any],
        *,
        requested: Mapping[str, Any],
    ) -> str:
        request_id = job.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            return tool_error_result("机器人服务没有返回 request_id", code="invalid_response")
        final_job = self.client.wait_for_job(
            request_id,
            timeout_seconds=self.timeout_seconds,
        )
        return _job_result(final_job, requested=requested)


def validate_tool_call(
    name: str,
    arguments: str | Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """校验工具名与参数，返回规范化后的参数副本。"""
    if name not in TOOL_NAMES:
        raise ToolValidationError(f"未知工具：{name}", code="unknown_tool")
    args = parse_arguments(arguments)
    _reject_unknown_keys(name, args)

    if name == STATUS_TOOL or name in {"robot_stop", "robot_emergency_stop", "robot_clear_estop"}:
        return {}
    if name == "robot_move":
        direction = _required_choice(args, "direction", MOVE_DIRECTIONS)
        distance = _required_finite_number(args, "distance_m")
        low, high = (
            FORWARD_DISTANCE_RANGE
            if direction in {"forward", "backward"}
            else LATERAL_DISTANCE_RANGE
        )
        if not low <= distance <= high:
            raise ToolValidationError(
                f"robot_move 的 distance_m 在 {direction} 方向必须位于 "
                f"{low:g}–{high:g} 米之间，当前为 {distance:g}"
            )
        return {"direction": direction, "distance_m": distance}
    if name == "robot_turn":
        direction = _required_choice(args, "direction", TURN_DIRECTIONS)
        angle = _required_finite_number(args, "angle_deg")
        low, high = TURN_ANGLE_RANGE
        if not low <= angle <= high:
            raise ToolValidationError(
                f"robot_turn 的 angle_deg 必须位于 {low:g}–{high:g} 度之间，当前为 {angle:g}"
            )
        return {"direction": direction, "angle_deg": angle}
    if name == "robot_posture":
        command = _required_choice(args, "command", POSTURE_COMMANDS)
        return {"command": command}
    raise ToolValidationError(f"未知工具：{name}", code="unknown_tool")


def parse_arguments(arguments: str | Mapping[str, Any] | None) -> dict[str, Any]:
    """解析工具参数；拒绝 NaN/Infinity，且顶层必须是 JSON 对象。"""
    if arguments is None:
        return {}
    if isinstance(arguments, Mapping):
        return dict(arguments)
    raw = arguments.strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw, parse_constant=_reject_json_constant)
    except json.JSONDecodeError as exc:
        raise ToolValidationError("工具参数不是合法 JSON") from exc
    except ValueError as exc:
        raise ToolValidationError(str(exc)) from exc
    if not isinstance(value, dict):
        raise ToolValidationError("工具参数必须是一个 JSON 对象")
    return value


def tool_result(
    *,
    ok: bool,
    request_id: str | None,
    state: str,
    error: str = "",
    measured: Mapping[str, Any] | None = None,
    requested: Mapping[str, Any] | None = None,
    **extra: Any,
) -> str:
    """构造统一的 JSON 工具结果。"""
    payload: dict[str, Any] = {
        "ok": ok,
        "request_id": request_id,
        "state": state,
        "error": error,
        "measured": dict(measured or {}),
        "requested": dict(requested or {}),
    }
    payload.update(extra)
    return _dump(payload)


def tool_error_result(
    message: str,
    *,
    code: str = "failed",
    state: str = "failed",
    request_id: str | None = None,
    **extra: Any,
) -> str:
    """构造失败/中止的工具结果，保留错误码和实际测量字段。"""
    payload: dict[str, Any] = {
        "ok": False,
        "request_id": request_id,
        "state": state,
        "error": message,
        "error_code": code,
        "measured": {},
        "requested": {},
    }
    payload.update(extra)
    return _dump(payload)


def execute_tool(
    client: RobotClient,
    name: str,
    arguments: str | Mapping[str, Any] | None = None,
    *,
    cfg: RobotConfig | Config | None = None,
    confirm_clear_estop: Callable[[], bool] | None = None,
) -> str:
    """便捷函数：用给定客户端执行一个工具。"""
    executor = ToolExecutor(
        client,
        cfg,
        confirm_clear_estop=confirm_clear_estop,
    )
    return executor.execute(name, arguments)


def plan_tool_calls(tool_calls: Sequence[Mapping[str, Any]]) -> list[PlannedToolCall]:
    """按“一次只执行一个动作 + 急停优先”的规则给工具调用排序。

    返回顺序与原 assistant 消息一致；``priority`` 供 session 决定实际执行顺序。
    被跳过的额外动作仍会生成一条 ``multiple_action_calls`` 工具结果，保证每个
    ``tool_call_id`` 都有配对结果。
    """
    calls = [dict(call) for call in tool_calls]
    action_indices = [
        index for index, call in enumerate(calls) if tool_call_name(call) in ACTION_TOOL_NAMES
    ]
    skipped: dict[int, str] = {}
    primary: int | None = None
    if action_indices:
        emergency = [
            index
            for index in action_indices
            if tool_call_name(calls[index]) == "robot_emergency_stop"
        ]
        primary = emergency[0] if emergency else action_indices[0]
        if len(action_indices) > 1:
            for index in action_indices:
                if index != primary:
                    skipped[index] = "multiple_action_calls"

    plans: list[PlannedToolCall] = []
    for index, call in enumerate(calls):
        name = tool_call_name(call)
        # 只有急停需要抢到最前面；其余（状态、选中的动作、被跳过的动作）保持
        # assistant 原始 tool_calls 顺序，避免无谓改变用户看到的执行顺序。
        priority = 0 if index == primary and name == "robot_emergency_stop" else 1
        plans.append(
            PlannedToolCall(
                call,
                error=skipped.get(index),
                priority=priority,
            )
        )
    return plans


def tool_call_name(call: Mapping[str, Any]) -> str:
    """从 OpenAI 工具调用中取函数名；结构异常时返回空字符串。"""
    function = call.get("function")
    if not isinstance(function, Mapping):
        return ""
    name = function.get("name")
    return name if isinstance(name, str) else ""


def tool_call_id(call: Mapping[str, Any]) -> str:
    """从 OpenAI 工具调用中取 id；缺失时返回空字符串。"""
    value = call.get("id")
    return value if isinstance(value, str) else ""


def _reject_unknown_keys(name: str, args: Mapping[str, Any]) -> None:
    allowed: set[str]
    if name == "robot_move":
        allowed = {"direction", "distance_m"}
    elif name == "robot_turn":
        allowed = {"direction", "angle_deg"}
    elif name == "robot_posture":
        allowed = {"command"}
    else:
        allowed = set()
    unknown = sorted(set(args) - allowed)
    if unknown:
        raise ToolValidationError(
            f"{name} 不接受参数：{', '.join(unknown)}",
            code="unknown_argument",
        )


def _required_choice(args: Mapping[str, Any], key: str, choices: frozenset[str]) -> str:
    if key not in args:
        raise ToolValidationError(f"缺少参数 {key}")
    value = args[key]
    if not isinstance(value, str):
        raise ToolValidationError(f"参数 {key} 必须是字符串")
    if value not in choices:
        allowed = " / ".join(sorted(choices))
        raise ToolValidationError(f"参数 {key} 必须是 {allowed} 之一，当前为 {value!r}")
    return value


def _required_finite_number(args: Mapping[str, Any], key: str) -> float:
    if key not in args:
        raise ToolValidationError(f"缺少参数 {key}")
    value = args[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ToolValidationError(f"参数 {key} 必须是数字（不能是布尔值或字符串）")
    number = float(value)
    if not math.isfinite(number):
        raise ToolValidationError(f"参数 {key} 必须是有限数字，不能是 NaN 或 Infinity")
    return number


def _text_arg(args: Mapping[str, Any], key: str) -> str:
    value = args[key]
    assert isinstance(value, str)
    return value


def _number_arg(args: Mapping[str, Any], key: str) -> float:
    value = args[key]
    assert isinstance(value, (int, float)) and not isinstance(value, bool)
    return float(value)


def _reject_json_constant(value: str) -> None:
    raise ToolValidationError(f"JSON 中不允许 {value}")


def _deny_clear_estop() -> bool:
    return False


def _dump(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _simple_result(data: Mapping[str, Any]) -> str:
    request_id = data.get("request_id")
    rid = request_id if isinstance(request_id, str) else None
    state = data.get("state")
    state_text = state if isinstance(state, str) else "succeeded"
    error = _error_text(data.get("error"))
    ok = not error and state_text not in {"failed", "aborted", "timed_out"}
    reserved = {
        "ok",
        "request_id",
        "state",
        "error",
        "measured",
        "requested",
    }
    extra = {key: value for key, value in data.items() if key not in reserved}
    return tool_result(
        ok=ok,
        request_id=rid,
        state=state_text,
        error=error,
        **extra,
    )


def _job_result(job: Mapping[str, Any], *, requested: Mapping[str, Any]) -> str:
    request_id = job.get("request_id")
    rid = request_id if isinstance(request_id, str) else None
    raw_state = job.get("state")
    state = raw_state if isinstance(raw_state, str) else "unknown"
    error = _error_text(job.get("error"))
    if not error and state != "succeeded":
        error = f"任务状态为 {state}"
    return tool_result(
        ok=state == "succeeded" and not error,
        request_id=rid,
        state=state,
        error=error,
        measured=_extract_measured(job),
        requested=requested,
        job=dict(job),
    )


def _extract_measured(job: Mapping[str, Any]) -> dict[str, Any]:
    measured: dict[str, Any] = {}
    for key in ("measured", "actual"):
        value = job.get(key)
        if isinstance(value, Mapping):
            measured.update(value)
    result = job.get("result")
    if isinstance(result, Mapping):
        for key in ("measured", "actual"):
            value = result.get(key)
            if isinstance(value, Mapping):
                measured.update(value)
        if not measured:
            measured.update(
                {
                    key: value
                    for key, value in result.items()
                    if key not in {"state", "error", "request_id", "requested"}
                }
            )
    for key, value in job.items():
        if key.startswith(("actual_", "measured_")):
            measured.setdefault(key.split("_", 1)[1], value)
    return measured


def _error_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        message = value.get("message")
        if isinstance(message, str):
            return message
        code = value.get("code")
        if isinstance(code, str):
            return code
    return ""


def _request_id_from_payload(payload: Mapping[str, Any]) -> str | None:
    value = payload.get("request_id")
    if isinstance(value, str):
        return value
    job = payload.get("job")
    if isinstance(job, Mapping):
        nested = job.get("request_id")
        if isinstance(nested, str):
            return nested
    return None


def _error_code_from_payload(payload: Mapping[str, Any]) -> str | None:
    value = payload.get("error")
    if isinstance(value, Mapping):
        code = value.get("code")
        if isinstance(code, str):
            return code
    code = payload.get("error_code")
    return code if isinstance(code, str) else None
