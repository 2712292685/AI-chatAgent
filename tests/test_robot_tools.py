"""robot_tools：schema、硬校验、真实结果、确认与单动作规则。"""

from __future__ import annotations

import json
import math
from typing import Any

import pytest

from chatagent.robot_client import RobotClientError
from chatagent.robot_tools import (
    ACTION_TOOL_NAMES,
    TOOL_NAMES,
    TOOL_SCHEMAS,
    TOOLS,
    ToolExecutor,
    ToolValidationError,
    plan_tool_calls,
    tool_call_name,
    validate_tool_call,
)


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.final_job: dict[str, Any] = {
            "request_id": "rid",
            "state": "succeeded",
            "measured": {"forward_m": 0.997},
        }
        self.status: dict[str, Any] = {
            "stale": False,
            "battery": {"power": 87},
            "estop_latched": False,
        }
        self.error: Exception | None = None

    def _record(self, name: str, *args: Any) -> None:
        self.calls.append((name, args))
        if self.error is not None:
            raise self.error

    def move(self, direction: str, distance_m: float) -> dict[str, Any]:
        self._record("move", direction, distance_m)
        return {"request_id": "rid", "state": "running"}

    def turn(self, direction: str, angle_deg: float) -> dict[str, Any]:
        self._record("turn", direction, angle_deg)
        return {"request_id": "rid", "state": "running"}

    def posture(self, command: str) -> dict[str, Any]:
        self._record("posture", command)
        return {"request_id": "rid", "state": "running"}

    def stop(self) -> dict[str, Any]:
        self._record("stop")
        return {"ok": True}

    def emergency_stop(self) -> dict[str, Any]:
        self._record("emergency_stop")
        return {"ok": True, "estop_latched": True}

    def clear_estop(self) -> dict[str, Any]:
        self._record("clear_estop")
        return {"ok": True, "estop_latched": False}

    def get_status(self) -> dict[str, Any]:
        self._record("status")
        return self.status

    def wait_for_job(
        self,
        request_id: str,
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        self.calls.append(("wait", request_id, timeout_seconds))
        return self.final_job


def executor(client: FakeClient, *, confirm: bool = True) -> ToolExecutor:
    return ToolExecutor(client, confirm_clear_estop=lambda: confirm)


def result_json(text: str) -> dict[str, Any]:
    value = json.loads(text)
    assert isinstance(value, dict)
    return value


def test_schemas_cover_exactly_the_required_tools_and_no_speed_tool() -> None:
    assert {
        "robot_status",
        "robot_move",
        "robot_turn",
        "robot_posture",
        "robot_stop",
        "robot_emergency_stop",
        "robot_clear_estop",
    } == TOOL_NAMES
    assert TOOLS is TOOL_SCHEMAS
    names = {item["function"]["name"] for item in TOOL_SCHEMAS}
    assert names == TOOL_NAMES
    assert "robot_speed" not in names
    move = next(item for item in TOOL_SCHEMAS if item["function"]["name"] == "robot_move")
    description = move["function"]["description"]
    assert "平移" in description
    assert "0.05–5" in description
    assert "0.05–3" in description


def test_move_validates_direction_specific_ranges() -> None:
    assert validate_tool_call("robot_move", {"direction": "forward", "distance_m": 3.5}) == {
        "direction": "forward",
        "distance_m": 3.5,
    }
    assert validate_tool_call("robot_move", {"direction": "right", "distance_m": 3}) == {
        "direction": "right",
        "distance_m": 3.0,
    }

    with pytest.raises(ToolValidationError) as excinfo:
        validate_tool_call("robot_move", {"direction": "left", "distance_m": 3.01})
    assert "0.05–3" in excinfo.value.message

    with pytest.raises(ToolValidationError):
        validate_tool_call("robot_move", {"direction": "forward", "distance_m": 5.01})

    with pytest.raises(ToolValidationError):
        validate_tool_call("robot_move", {"direction": "diagonal", "distance_m": 1})


def test_boole_nan_infinity_and_strings_are_rejected() -> None:
    for value in (True, False, "1.0", math.nan, math.inf, -math.inf):
        with pytest.raises(ToolValidationError):
            validate_tool_call("robot_move", {"direction": "forward", "distance_m": value})

    with pytest.raises(ToolValidationError):
        validate_tool_call("robot_move", '{"direction":"forward","distance_m":NaN}')

    with pytest.raises(ToolValidationError):
        validate_tool_call("robot_move", '{"direction":"forward","distance_m":Infinity}')


def test_unknown_fields_and_malformed_json_are_rejected() -> None:
    with pytest.raises(ToolValidationError) as excinfo:
        validate_tool_call("robot_move", {"direction": "forward", "distance_m": 1, "speed": 2})
    assert "speed" in excinfo.value.message

    with pytest.raises(ToolValidationError):
        validate_tool_call("robot_turn", "{broken")

    with pytest.raises(ToolValidationError):
        validate_tool_call("robot_status", {"x": 1})


def test_turn_and_posture_boundaries() -> None:
    assert validate_tool_call("robot_turn", {"direction": "left", "angle_deg": 1})
    assert validate_tool_call("robot_turn", {"direction": "right", "angle_deg": 180})
    with pytest.raises(ToolValidationError):
        validate_tool_call("robot_turn", {"direction": "left", "angle_deg": 0})
    with pytest.raises(ToolValidationError):
        validate_tool_call("robot_turn", {"direction": "left", "angle_deg": 180.1})
    with pytest.raises(ToolValidationError):
        validate_tool_call("robot_posture", {"command": "dance"})


def test_executor_returns_actual_measurement_and_request_id() -> None:
    client = FakeClient()

    payload = result_json(
        executor(client).execute("robot_move", {"direction": "forward", "distance_m": 1})
    )

    assert payload["ok"] is True
    assert payload["request_id"] == "rid"
    assert payload["state"] == "succeeded"
    assert payload["measured"] == {"forward_m": 0.997}
    assert payload["requested"] == {"direction": "forward", "distance_m": 1.0}
    assert payload["job"]["request_id"] == "rid"
    assert client.calls[0] == ("move", ("forward", 1.0))


def test_failed_job_is_not_reported_as_success() -> None:
    client = FakeClient()
    client.final_job = {
        "request_id": "rid",
        "state": "timed_out",
        "measured": {"forward_m": 0.32},
        "error": {"code": "timeout", "message": "运动超时"},
    }

    payload = result_json(
        executor(client).execute("robot_move", {"direction": "forward", "distance_m": 1})
    )

    assert payload["ok"] is False
    assert payload["state"] == "timed_out"
    assert payload["measured"] == {"forward_m": 0.32}
    assert payload["error"] == "运动超时"


def test_http_error_becomes_a_tool_result_without_fake_measurement() -> None:
    client = FakeClient()
    client.error = RobotClientError(
        "服务忙",
        status_code=409,
        payload={
            "request_id": "known-rid",
            "error": {"code": "busy", "message": "服务忙"},
        },
    )

    payload = result_json(
        executor(client).execute("robot_move", {"direction": "forward", "distance_m": 1})
    )

    assert payload["ok"] is False
    assert payload["request_id"] == "known-rid"
    assert payload["measured"] == {}
    assert payload["error"] == "服务忙"
    assert payload["error_code"] == "busy"


def test_clear_estop_requires_confirmation_and_can_be_cancelled() -> None:
    client = FakeClient()
    denied = result_json(executor(client, confirm=False).execute("robot_clear_estop", "{}"))

    assert denied["ok"] is False
    assert denied["state"] == "aborted"
    assert denied["error_code"] == "cancelled"
    assert client.calls == []

    allowed = result_json(executor(client, confirm=True).execute("robot_clear_estop"))
    assert allowed["ok"] is True
    assert client.calls[-1] == ("clear_estop", ())


def test_status_result_keeps_status_fields_and_flags_staleness() -> None:
    client = FakeClient()
    client.status = {"stale": True, "battery": {"power": 10}}

    payload = result_json(executor(client).execute("robot_status"))

    assert payload["ok"] is False
    assert payload["state"] == "stale"
    assert payload["battery"] == {"power": 10}
    assert "过期" in payload["error"]


def test_stop_paths_and_simple_results() -> None:
    client = FakeClient()
    ex = executor(client)

    assert result_json(ex.execute("robot_stop"))["ok"] is True
    estop = result_json(ex.execute("robot_emergency_stop"))
    assert estop["ok"] is True
    assert estop["estop_latched"] is True
    assert [name for name, _ in client.calls] == ["stop", "emergency_stop"]


def test_invalid_arguments_become_a_tool_result_not_an_exception() -> None:
    client = FakeClient()
    text = executor(client).execute("robot_move", '{"direction":"forward","distance_m":true}')
    payload = result_json(text)

    assert payload["ok"] is False
    assert payload["error_code"] == "invalid_arguments"
    assert client.calls == []


def call(name: str, call_id: str) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": "{}"},
    }


def test_plan_limits_one_action_per_assistant_turn() -> None:
    plans = plan_tool_calls([call("robot_move", "1"), call("robot_turn", "2")])

    assert [plan.priority for plan in plans] == [1, 1]
    assert plans[1].error == "multiple_action_calls"


def test_estop_has_priority_over_other_actions_and_is_executed_first() -> None:
    plans = plan_tool_calls(
        [call("robot_move", "1"), call("robot_emergency_stop", "2"), call("robot_turn", "3")]
    )

    assert plans[0].error == "multiple_action_calls"
    assert plans[1].priority == 0
    assert plans[1].error is None
    assert plans[2].error == "multiple_action_calls"
    ordered = sorted(enumerate(plans), key=lambda item: (item[1].priority, item[0]))
    assert [plan.tool_call["id"] for _, plan in ordered][0] == "2"


def test_status_calls_can_repeat_alongside_one_action() -> None:
    plans = plan_tool_calls(
        [call("robot_status", "1"), call("robot_status", "2"), call("robot_move", "3")]
    )

    assert all(plan.error is None for plan in plans)
    assert tool_call_name(plans[0].tool_call) == "robot_status"
    assert ACTION_TOOL_NAMES
