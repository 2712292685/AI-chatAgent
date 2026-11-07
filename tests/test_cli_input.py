"""输入解析：退出词、反斜杠续行与多行拼接。"""

from __future__ import annotations

import pytest

from chatagent import cli


@pytest.mark.parametrize("line", ["exit", "EXIT", "  quit  ", "Quit", "quit"])
def test_exit_words_are_recognised(line: str) -> None:
    assert cli.is_exit_word(line) is True


@pytest.mark.parametrize("line", ["exit 是什么", "exiting", "", "   ", "退出"])
def test_other_content_is_not_an_exit_word(line: str) -> None:
    assert cli.is_exit_word(line) is False


def test_exit_word_does_not_match_a_multiline_message() -> None:
    assert cli.is_exit_word("exit\nexit") is False


def test_single_trailing_backslash_continues() -> None:
    assert cli.is_continuation("line1\\") is True
    assert cli.is_continuation("\\") is True


def test_double_trailing_backslash_is_a_literal() -> None:
    assert cli.is_continuation("line1\\\\") is False


def test_plain_line_does_not_continue() -> None:
    assert cli.is_continuation("line1") is False
    assert cli.is_continuation("") is False


def test_strip_removes_the_continuation_marker() -> None:
    assert cli.strip_continuation("line1\\") == "line1"
    assert cli.strip_continuation("\\") == ""


def test_strip_unescapes_a_literal_backslash() -> None:
    assert cli.strip_continuation("line1\\\\") == "line1\\"


def test_strip_leaves_plain_lines_alone() -> None:
    assert cli.strip_continuation("line1") == "line1"


def test_multiline_message_joins_lines_with_newline(monkeypatch: pytest.MonkeyPatch) -> None:
    prompts = iter(["line1\\", "line2\\\\"])
    monkeypatch.setattr(cli, "prompt_user", lambda: next(prompts))
    monkeypatch.setattr(cli, "prompt_continuation", lambda: next(prompts))

    # 第一行续行、第二行以字面反斜杠收尾：拼成一条带换行的消息。
    assert cli._read_message() == "line1\nline2\\"


def test_continuation_prompt_matches_the_spec(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def fake_input(prompt: str = "") -> str:
        seen.append(prompt)
        return ""

    monkeypatch.setattr("builtins.input", fake_input)
    cli.prompt_continuation()

    assert seen == ["   ... "]


def test_blank_line_reads_as_an_empty_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "prompt_user", lambda: "")

    assert cli._read_message() == ""


def test_whitespace_only_line_is_kept_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "prompt_user", lambda: " \t ")

    message = cli._read_message()

    assert message == " \t "
    assert not message.strip()


def test_three_continuations_join_into_one_message(monkeypatch: pytest.MonkeyPatch) -> None:
    prompts = iter(["a\\", "b\\", "c\\", "d"])
    monkeypatch.setattr(cli, "prompt_user", lambda: next(prompts))
    monkeypatch.setattr(cli, "prompt_continuation", lambda: next(prompts))

    assert cli._read_message() == "a\nb\nc\nd"


def test_eof_in_the_middle_of_a_continuation_is_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "prompt_user", lambda: "only-line\\")

    def raise_eof() -> str:
        raise EOFError

    monkeypatch.setattr(cli, "prompt_continuation", raise_eof)

    with pytest.raises(EOFError):
        cli._read_message()
