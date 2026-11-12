"""robot_context：工具消息估算与按完整轮次裁剪。"""

from __future__ import annotations

from chatagent.context import estimate_tokens
from chatagent.robot_context import (
    RobotMessage,
    build_payload,
    estimate_message_tokens,
    estimate_messages_tokens,
    trim_messages,
)

SYSTEM = "系统提示"


def assistant_tool_call(
    arguments: str = '{"direction":"forward","distance_m":1}',
) -> dict[str, object]:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "robot_move", "arguments": arguments},
            }
        ],
    }


ROUND_ONE = [
    {"role": "user", "content": "前进一米"},
    assistant_tool_call(),
    {"role": "tool", "tool_call_id": "call_1", "content": '{"ok":true,"state":"succeeded"}'},
    {"role": "assistant", "content": "走完了。"},
]
ROUND_TWO = [
    {"role": "user", "content": "再左转九十度"},
    {"role": "assistant", "content": "好。"},
]


def test_robot_message_converts_nullable_content_and_tool_calls() -> None:
    message = RobotMessage.from_dict(assistant_tool_call())
    payload = message.to_dict()

    assert message.role == "assistant"
    assert message.content is None
    assert payload["content"] is None
    assert payload["tool_calls"] == assistant_tool_call()["tool_calls"]
    assert payload["tool_calls"] is not message.tool_calls


def test_tool_result_message_keeps_tool_call_id() -> None:
    message = RobotMessage(role="tool", content="{}", tool_call_id="call_1")

    assert message.to_dict() == {
        "role": "tool",
        "content": "{}",
        "tool_call_id": "call_1",
    }


def test_estimate_includes_tool_arguments_and_call_id() -> None:
    short = assistant_tool_call('{"direction":"left"}')
    long = assistant_tool_call('{"direction":"left","padding":"' + "x" * 500 + '"}')
    result = {"role": "tool", "tool_call_id": "call_1", "content": "{}"}

    assert estimate_message_tokens(long) > estimate_message_tokens(short)
    assert estimate_message_tokens(result) > estimate_tokens("{}") + 4


def test_trim_keeps_the_newest_whole_round_and_drops_old_rounds() -> None:
    history = [*ROUND_ONE, *ROUND_TWO]
    budget = estimate_messages_tokens(ROUND_TWO, SYSTEM)

    payload = trim_messages(history, SYSTEM, budget)

    assert payload == ROUND_TWO
    assert payload[0]["role"] == "user"


def test_tool_calls_and_tool_results_are_never_split() -> None:
    budget = estimate_messages_tokens(ROUND_ONE, SYSTEM)

    payload = trim_messages(ROUND_ONE, SYSTEM, budget)

    assert payload == ROUND_ONE
    assert any(message.get("tool_calls") for message in payload)
    assert payload[-2]["tool_call_id"] == "call_1"


def test_oversized_newest_round_is_kept_whole() -> None:
    payload = trim_messages(ROUND_ONE, SYSTEM, 0)

    assert payload == ROUND_ONE


def test_leading_assistant_or_tool_messages_are_dropped() -> None:
    history = [
        {"role": "assistant", "content": "孤儿"},
        {"role": "tool", "tool_call_id": "x", "content": "{}"},
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "在。"},
    ]

    payload = build_payload(history, SYSTEM, 32000)

    assert payload == history[2:]


def test_empty_history_and_only_orphans_return_empty() -> None:
    assert trim_messages([], SYSTEM, 32000) == []
    assert trim_messages([{"role": "assistant", "content": "孤"}], SYSTEM, 32000) == []


def test_trim_does_not_mutate_input_and_returns_copies() -> None:
    history = [*ROUND_ONE, *ROUND_TWO]
    snapshot = [dict(message) for message in history]

    payload = trim_messages(history, SYSTEM, 32000)

    assert history == snapshot
    assert payload is not history
    payload[0]["content"] = "changed"
    assert history[0]["content"] == "前进一米"
