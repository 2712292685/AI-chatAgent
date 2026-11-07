"""render 模块：宽度计算、横幅、提示符与状态文案（不依赖真实终端）。"""

from __future__ import annotations

import re
import sys

import pytest

from chatagent import render
from chatagent.persona import Persona

ANSI = re.compile(r"\x1b\[[0-9;]*m")
PERSONA = Persona(name="阿岩", role="你的深夜自习室同桌", personality="慢热", style="短句")


def width_of(line: str) -> int:
    return render.display_width(ANSI.sub("", line))


def test_display_width_counts_wide_characters_as_two() -> None:
    assert render.display_width("阿岩") == 4
    assert render.display_width("ab") == 2
    assert render.display_width("a岩") == 3
    assert render.display_width("") == 0


def test_banner_box_has_four_aligned_plain_lines() -> None:
    lines = render.banner_lines("阿岩", "你的深夜自习室同桌", color=False)

    assert len(lines) == 4
    assert all("\x1b" not in line for line in lines)
    assert lines[0].startswith("  ╭") and lines[0].endswith("╮")
    assert lines[3].startswith("  ╰") and lines[3].endswith("╯")
    assert len({width_of(line) for line in lines}) == 1


def test_banner_pads_wide_names_to_the_same_width() -> None:
    narrow = render.banner_lines("A", "role", color=False)
    wide = render.banner_lines("一二三四五六七八九十一二", "角色", color=False)

    assert len({width_of(line) for line in wide}) == 1
    assert width_of(wide[1]) == width_of(narrow[1])


def test_banner_grows_for_a_long_role() -> None:
    long_role = "这是一个非常非常长的角色描述用于测试宽度自适应"
    lines = render.banner_lines("阿岩", long_role, color=False)

    assert width_of(lines[1]) == width_of(lines[2])
    assert width_of(lines[0]) >= 2 + render.display_width(long_role) + 2


def test_banner_can_be_coloured() -> None:
    lines = render.banner_lines("阿岩", "角色", color=True)

    assert "\x1b[1;36m" in lines[1]
    assert "\x1b[2m" in lines[0]
    assert len({width_of(line) for line in lines}) == 1


def test_print_banner_falls_back_to_plain_text_off_a_tty(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(render, "_is_tty", False)

    render.print_banner(PERSONA)

    out = capsys.readouterr().out
    assert "阿岩" in out
    assert "╭" not in out
    assert "输入内容开始对话" in out


def test_print_banner_draws_the_box_on_a_tty(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(render, "_is_tty", True)
    monkeypatch.setattr(render, "_stdout_color", False)

    render.print_banner(PERSONA)

    out = capsys.readouterr().out
    assert "╭" in out
    assert "\x1b" not in out


def test_prompt_text_matches_the_spec(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def fake_input(prompt: str = "") -> str:
        seen.append(prompt)
        return "hi"

    monkeypatch.setattr("builtins.input", fake_input)

    assert render.prompt_user() == "hi"
    assert seen == ["你 › "]


def test_error_warning_and_retry_go_to_stderr(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(render, "_stderr_color", False)

    render.print_error("出错了")
    render.print_warning("小心")
    render.print_retry("网络超时", 1, 2)

    err = capsys.readouterr().err
    assert "✗ 出错了" in err
    assert "! 小心" in err
    assert "! 请求失败（网络超时），正在重试 (1/2)..." in err
    assert "\x1b" not in err


def test_error_is_painted_when_colour_is_on(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(render, "_stderr_color", True)

    render.print_error("出错了")

    assert "\x1b[31m" in capsys.readouterr().err


def test_interrupted_marker_starts_on_a_new_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(render, "_stdout_color", False)

    render.print_interrupted()

    assert capsys.readouterr().out == "\n[已中断]\n"


def test_reply_prefix_and_reply_use_the_name(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(render, "_stdout_color", False)

    render.print_reply_prefix("阿岩")
    render.end_stream()
    render.print_reply("阿岩", "拜拜")

    out = capsys.readouterr().out
    assert out == "阿岩 › \n阿岩 › 拜拜\n"


def test_stream_chunk_does_not_add_newlines(capsys: pytest.CaptureFixture[str]) -> None:
    render.stream_chunk("一")
    render.stream_chunk("二")

    assert capsys.readouterr().out == "一二"


def test_paint_respects_the_enabled_flag() -> None:
    assert render._paint("x", render._DIM, False) == "x"
    assert render._paint("x", render._DIM, True) == f"{render._DIM}x{render._RESET}"
    assert render._paint("", render._DIM, True) == ""


def test_isatty_tolerates_objects_without_the_method() -> None:
    assert render._isatty(object()) is False


def test_init_terminal_off_a_tty_keeps_colour_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(render, "_is_tty", True)
    monkeypatch.setattr(render, "_stdout_color", True)

    render.init_terminal()

    assert render._is_tty is False
    assert render._stdout_color is False
    assert render._stderr_color is False


def test_enable_windows_vt_is_a_no_op_elsewhere(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")

    assert render._enable_windows_vt(-11) is True
