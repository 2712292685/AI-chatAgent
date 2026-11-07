"""上下文裁剪与 token 估算测试。"""

from chatagent.context import Message, build_payload, estimate_tokens

# estimate_tokens("sys") == int(3 * 0.4) + 1 == 2，加 8 的固定开销后 used 从 10 起算。
SYSTEM = "sys"


def round_of(index: int) -> list[Message]:
    """构造一轮 user/assistant 消息，内容是单字，估算开销各为 2 + 4 = 6。"""
    return [
        Message(role="user", content=str(index)),
        Message(role="assistant", content=str(index)),
    ]


def test_empty_history_returns_empty_list() -> None:
    assert build_payload([], SYSTEM, 32000) == []


def test_single_round_within_budget_is_returned_as_is() -> None:
    history = round_of(1)

    payload = build_payload(history, SYSTEM, 32000)

    assert payload == history


def test_over_budget_drops_the_oldest_round() -> None:
    history = [*round_of(1), *round_of(3)]

    # used 从 10 起，每轮 12；预算 22 只装得下最后一轮。
    payload = build_payload(history, SYSTEM, 22)

    assert payload == round_of(3)
    assert payload[0].role == "user"


def test_leading_assistant_is_trimmed() -> None:
    history = [
        Message(role="assistant", content="孤"),
        Message(role="user", content="一"),
        Message(role="assistant", content="二"),
    ]

    payload = build_payload(history, SYSTEM, 32000)

    assert payload == history[1:]
    assert payload[0].role == "user"


def test_single_oversized_message_is_still_returned() -> None:
    history = [Message(role="user", content="长" * 100)]

    payload = build_payload(history, SYSTEM, 10)

    assert payload == history


def test_estimate_tokens_for_pure_chinese() -> None:
    assert estimate_tokens("你好") == 3
    assert estimate_tokens("中国人") == 4


def test_estimate_tokens_for_pure_english() -> None:
    assert estimate_tokens("hello") == 3
    assert estimate_tokens("abcdefghij") == 5


def test_estimate_tokens_for_mixed_text() -> None:
    assert estimate_tokens("你好world") == 5
    assert estimate_tokens("") == 1


def test_build_payload_is_deterministic_and_does_not_mutate_history() -> None:
    history = [*round_of(1), *round_of(2), *round_of(3)]
    snapshot = list(history)

    first = build_payload(history, SYSTEM, 28)
    second = build_payload(history, SYSTEM, 28)

    assert first == second
    assert history == snapshot
    assert first is not history


def test_zero_budget_still_keeps_the_newest_user_message() -> None:
    # 真实调用时历史末尾总是本轮 user 消息。
    history = [*round_of(1), Message(role="user", content="2")]

    payload = build_payload(history, SYSTEM, 0)

    assert payload == [history[-1]]
    assert payload[0].role == "user"


def test_exact_budget_keeps_every_round() -> None:
    # 固定开销 10，每轮 2 条 × 5 = 10，两轮刚好 30。
    history = [*round_of(1), *round_of(3)]

    assert build_payload(history, SYSTEM, 30) == history


def test_one_token_short_drops_the_oldest_round() -> None:
    history = [*round_of(1), *round_of(3)]

    assert build_payload(history, SYSTEM, 29) == round_of(3)


def test_history_with_only_an_assistant_message_is_trimmed_to_empty() -> None:
    history = [Message(role="assistant", content="孤")]

    assert build_payload(history, SYSTEM, 32000) == []
