"""robot_session：工具循环、轮数上限、历史配对与中断写回。"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest

from chatagent import robot_session as session_mod
from chatagent.config import ApiConfig, Config, ContextConfig, ModelConfig, RobotConfig
from chatagent.persona import Persona
from chatagent.robot_session import FINAL_ANSWER_NOTICE, FINAL_FALLBACK_TEXT, RobotSession
from chatagent.robot_tools import tool_error_result

PERSONA = Persona(name="阿岩", role="你的深夜自习室同桌", personality="慢热", style="短句")


def make_config(rounds: int = 4) -> Config:
    return Config(
        api=ApiConfig(
            base_url="https://api.deepseek.com",
            model="deepseek-flash",
            timeout_seconds=60,
            max_retries=0,
        ),
        model=ModelConfig(thinking=False, temperature=0.8, max_tokens=4096),
        context=ContextConfig(budget_tokens=32000),
        robot=RobotConfig(max_tool_rounds=rounds),
    )


def tool_call(name: str, call_id: str, arguments: str = "{}") -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


@dataclass
class Step:
    text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


class FakeModel:
    def __init__(self, steps: list[Step]) -> None:
        self.steps = list(steps)
        self.calls: list[tuple[list[dict[str, Any]], object]] = []

    def chat_turn(
        self,
        messages: list[dict[str, Any]],
        cfg: Config,
        tools: object,
        result: Any,
    ) -> Iterator[str]:
        self.calls.append((messages, tools))
        step = self.steps.pop(0)
        result.text = step.text
        result.tool_calls = step.tool_calls
        result.finish_reason = "tool_calls" if step.tool_calls else "stop"
        if step.text:
            yield step.text


class FakeExecutor:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[tuple[str, str]] = []
        self.results: list[str] = []

    def execute(self, name: str, arguments: str) -> str:
        self.calls.append((name, arguments))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        text = str(outcome)
        self.results.append(text)
        return text


class FakeClient:
    def __init__(self) -> None:
        self.stops = 0

    def stop(self) -> dict[str, Any]:
        self.stops += 1
        return {"ok": True}


def make_session(
    monkeypatch: pytest.MonkeyPatch,
    steps: list[Step],
    executor: FakeExecutor | None = None,
    *,
    rounds: int = 4,
    callbacks: bool = False,
) -> tuple[RobotSession, FakeModel, FakeExecutor]:
    model = FakeModel(steps)
    monkeypatch.setattr(session_mod, "chat_turn", model.chat_turn)
    ex = executor or FakeExecutor([])
    calls: list[tuple[str, str]] = []
    results: list[tuple[str, str]] = []
    session = RobotSession(
        PERSONA,
        "系统提示",
        make_config(rounds),
        client=FakeClient(),  # type: ignore[arg-type]
        executor=ex,  # type: ignore[arg-type]
        on_tool_call=(lambda name, args: calls.append((name, args))) if callbacks else None,
        on_tool_result=(lambda name, text: results.append((name, text))) if callbacks else None,
    )
    return session, model, ex


def ok_move() -> str:
    return json.dumps(
        {
            "ok": True,
            "request_id": "rid",
            "state": "succeeded",
            "error": "",
            "measured": {"forward_m": 0.999},
        },
        ensure_ascii=False,
    )


def test_session_can_generate_the_robot_prompt_from_config() -> None:
    session = RobotSession(
        PERSONA,
        make_config(),
        client=FakeClient(),  # type: ignore[arg-type]
    )

    assert "机器人控制补充" in session.system_prompt
    assert "绝不能编造成功" not in session.system_prompt
    assert "绝不编造成功" in session.system_prompt


def test_plain_reply_records_user_and_assistant(monkeypatch: pytest.MonkeyPatch) -> None:
    session, model, _ = make_session(monkeypatch, [Step("你好")])

    assert list(session.ask("在吗")) == ["你好"]
    assert session.history == [
        {"role": "user", "content": "在吗"},
        {"role": "assistant", "content": "你好"},
    ]
    messages, tools = model.calls[0]
    assert tools is not None
    assert messages[0] == {"role": "system", "content": "系统提示"}
    assert messages[-1] == {"role": "user", "content": "在吗"}


def test_tool_round_then_final_answer_keeps_messages_paired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call = tool_call("robot_move", "call_1", '{"direction":"forward","distance_m":1}')
    executor = FakeExecutor([ok_move()])
    session, model, _ = make_session(
        monkeypatch,
        [Step(tool_calls=[call]), Step("走了一米。")],
        executor,
        callbacks=True,
    )

    assert list(session.ask("前进一米")) == ["走了一米。"]
    assert [message["role"] for message in session.history] == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ]
    assistant = session.history[1]
    assert assistant["content"] is None
    assert assistant["tool_calls"][0]["id"] == "call_1"
    assert session.history[2]["tool_call_id"] == "call_1"
    assert json.loads(session.history[2]["content"])["measured"]["forward_m"] == 0.999
    assert executor.calls == [("robot_move", '{"direction":"forward","distance_m":1}')]
    assert len(model.calls) == 2
    assert model.calls[1][0][-1]["role"] == "tool"


def test_round_limit_strips_tools_and_adds_final_instruction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call = tool_call("robot_status", "call_1")
    executor = FakeExecutor([ok_move(), ok_move()])
    session, model, _ = make_session(
        monkeypatch,
        [
            Step(tool_calls=[call]),
            Step(tool_calls=[call]),
            Step("根据状态，一切正常。"),
        ],
        executor,
        rounds=2,
    )

    assert list(session.ask("看看状态")) == ["根据状态，一切正常。"]
    assert len(model.calls) == 3
    assert model.calls[0][1] is not None
    assert model.calls[1][1] is not None
    assert model.calls[2][1] is None
    assert FINAL_ANSWER_NOTICE in model.calls[2][0][0]["content"]
    assert [message["role"] for message in session.history][-2:] == ["tool", "assistant"]


def test_final_request_without_text_yields_a_neutral_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, _, _ = make_session(
        monkeypatch,
        [Step(tool_calls=[tool_call("robot_status", "call_1")]), Step()],
        FakeExecutor([ok_move()]),
        rounds=1,
    )

    assert list(session.ask("看看")) == [FINAL_FALLBACK_TEXT]
    assert session.history[-1] == {"role": "assistant", "content": FINAL_FALLBACK_TEXT}


def test_multiple_action_calls_execute_one_and_report_the_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    move = tool_call("robot_move", "call_1", '{"direction":"forward","distance_m":1}')
    turn = tool_call("robot_turn", "call_2", '{"direction":"left","angle_deg":90}')
    executor = FakeExecutor([ok_move()])
    session, _, _ = make_session(
        monkeypatch,
        [Step(tool_calls=[move, turn]), Step("只走了一步。")],
        executor,
        callbacks=True,
    )

    assert list(session.ask("前进再左转")) == ["只走了一步。"]
    assert executor.calls == [("robot_move", '{"direction":"forward","distance_m":1}')]
    tool_messages = [m for m in session.history if m["role"] == "tool"]
    assert len(tool_messages) == 2
    assert tool_messages[0]["tool_call_id"] == "call_1"
    assert tool_messages[1]["tool_call_id"] == "call_2"
    skipped = json.loads(tool_messages[1]["content"])
    assert skipped["ok"] is False
    assert skipped["error_code"] == "multiple_action_calls"


def test_emergency_stop_is_executed_before_other_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    move = tool_call("robot_move", "call_1", '{"direction":"forward","distance_m":1}')
    estop = tool_call("robot_emergency_stop", "call_2")
    executor = FakeExecutor(
        [
            json.dumps({"ok": True, "request_id": None, "state": "succeeded"}),
        ]
    )
    session, _, _ = make_session(
        monkeypatch,
        [Step(tool_calls=[move, estop]), Step("已急停。")],
        executor,
    )

    assert list(session.ask("快停")) == ["已急停。"]
    assert executor.calls == [("robot_emergency_stop", "{}")]
    tool_messages = [m for m in session.history if m["role"] == "tool"]
    # 执行顺序是急停优先，但历史里的 tool 结果保持与 tool_calls 相同的顺序。
    assert tool_messages[0]["tool_call_id"] == "call_1"
    assert json.loads(tool_messages[0]["content"])["error_code"] == "multiple_action_calls"
    assert tool_messages[1]["tool_call_id"] == "call_2"
    assert json.loads(tool_messages[1]["content"])["ok"] is True


def test_keyboard_interrupt_writes_an_interrupted_tool_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call = tool_call("robot_move", "call_1", '{"direction":"forward","distance_m":1}')
    executor = FakeExecutor([KeyboardInterrupt()])
    session, _, _ = make_session(
        monkeypatch,
        [Step(tool_calls=[call])],
        executor,
    )

    with pytest.raises(KeyboardInterrupt):
        list(session.ask("前进一米"))

    assert [message["role"] for message in session.history] == [
        "user",
        "assistant",
        "tool",
    ]
    payload = json.loads(session.history[2]["content"])
    assert payload["state"] == "interrupted"
    assert payload["error_code"] == "interrupted"


def test_interrupt_marks_unfinished_calls_as_not_executed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tool_call("robot_status", "call_1")
    second = tool_call("robot_move", "call_2", '{"direction":"forward","distance_m":1}')
    executor = FakeExecutor([KeyboardInterrupt()])
    session, _, _ = make_session(
        monkeypatch,
        [Step(tool_calls=[first, second])],
        executor,
    )

    with pytest.raises(KeyboardInterrupt):
        list(session.ask("看看再走"))

    tool_messages = [m for m in session.history if m["role"] == "tool"]
    assert len(tool_messages) == 2
    states = {json.loads(m["content"])["state"] for m in tool_messages}
    assert states == {"interrupted", "aborted"}


def test_invalid_tool_arguments_are_returned_to_the_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call = tool_call("robot_move", "call_1", "{broken")
    executor = FakeExecutor([tool_error_result("参数不对", code="invalid_arguments")])
    session, model, _ = make_session(
        monkeypatch,
        [Step(tool_calls=[call]), Step("参数有问题，请重说。")],
        executor,
    )

    assert list(session.ask("走")) == ["参数有问题，请重说。"]
    payload = json.loads(session.history[2]["content"])
    assert payload["error_code"] == "invalid_arguments"
    assert len(model.calls) == 2
