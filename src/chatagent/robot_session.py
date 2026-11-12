"""机器人会话：模型流式回答、工具执行、历史与中断写回。"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Generator, Mapping, Sequence
from typing import Any

from .config import Config
from .persona import Persona
from .robot_client import RobotClient
from .robot_context import trim_messages
from .robot_llm import TurnResult, chat_turn
from .robot_policy import build_robot_system_prompt
from .robot_tools import (
    TOOLS,
    PlannedToolCall,
    ToolExecutor,
    plan_tool_calls,
    tool_call_id,
    tool_call_name,
    tool_error_result,
)

FINAL_ANSWER_NOTICE = (
    "【工具调用已达上限】请根据已有工具结果直接回答用户，不要再调用工具；"
    "如果结果不足，请如实说明还缺什么。"
)
FINAL_FALLBACK_TEXT = "工具调用次数已达上限，暂时无法得到更多结果，请稍后再试。"

ToolStartCallback = Callable[[str, str], None]
ToolResultCallback = Callable[[str, str], None]
Message = dict[str, Any]


class RobotSession:
    """一次进程内的机器人对话；历史保持 OpenAI 消息格式。"""

    def __init__(
        self,
        persona: Persona,
        system_prompt: str | Config | None = None,
        cfg: Config | None = None,
        *,
        client: RobotClient | None = None,
        executor: ToolExecutor | None = None,
        confirm_clear_estop: Callable[[], bool] | None = None,
        on_tool_call: ToolStartCallback | None = None,
        on_tool_result: ToolResultCallback | None = None,
    ) -> None:
        # 兼容两种构造方式：RobotSession(persona, system_prompt, cfg) 与
        # RobotSession(persona, cfg=cfg)（后者自动生成机器人 system prompt）。
        if isinstance(system_prompt, Config):
            if cfg is not None:
                raise TypeError("cfg 不能同时通过第二个位置参数和关键字参数传入")
            cfg = system_prompt
            system_prompt = None
        if cfg is None:
            raise TypeError("RobotSession 缺少 cfg 配置")
        if system_prompt is not None and not isinstance(system_prompt, str):
            raise TypeError("system_prompt 必须是字符串或省略")
        self.persona = persona
        self.system_prompt = (
            system_prompt if isinstance(system_prompt, str) else build_robot_system_prompt(persona)
        )
        self.cfg = cfg
        self.client = client or RobotClient(cfg.robot)
        self.executor = executor or ToolExecutor(
            self.client,
            cfg.robot,
            confirm_clear_estop=confirm_clear_estop,
        )
        self.history: list[Message] = []
        self._on_tool_call = on_tool_call
        self._on_tool_result = on_tool_result

    def ask(self, user_text: str) -> Generator[str, None, None]:
        """处理一条用户消息，逐块产出模型文本，并在需要时执行工具。"""
        self.history.append({"role": "user", "content": user_text})
        tool_rounds = 0
        max_rounds = self.cfg.robot.max_tool_rounds

        while True:
            tools_allowed = tool_rounds < max_rounds
            system_prompt = self.system_prompt
            if not tools_allowed:
                system_prompt = f"{system_prompt}\n\n{FINAL_ANSWER_NOTICE}"

            messages = [
                {"role": "system", "content": system_prompt},
                *trim_messages(
                    self.history,
                    system_prompt,
                    self.cfg.context.budget_tokens,
                ),
            ]
            result = TurnResult()
            stream = chat_turn(
                messages,
                self.cfg,
                TOOLS if tools_allowed else None,
                result,
            )
            try:
                yield from stream
            finally:
                stream.close()

            if not tools_allowed:
                # 最后一次请求已经明确不带 tools；若模型仍返回工具调用，不能写进
                # 没有配对 tool 结果的历史，否则下一轮请求会非法。
                text = result.text
                if not text:
                    text = FINAL_FALLBACK_TEXT
                    yield text
                self.history.append({"role": "assistant", "content": text})
                return

            calls = _normalise_tool_calls(result.tool_calls)
            if not calls:
                if result.text:
                    self.history.append({"role": "assistant", "content": result.text})
                return

            assistant: Message = {
                "role": "assistant",
                "content": result.text or None,
                "tool_calls": calls,
            }
            self.history.append(assistant)
            self._run_tools(calls)
            tool_rounds += 1

    def _run_tools(self, calls: Sequence[Mapping[str, Any]]) -> None:
        plans = plan_tool_calls(calls)
        indexed = sorted(enumerate(plans), key=lambda item: (item[1].priority, item[0]))
        results: dict[int, tuple[str, str]] = {}
        completed: set[int] = set()

        for index, plan in indexed:
            name = tool_call_name(plan.tool_call)
            raw_arguments = _raw_arguments(plan.tool_call)
            self._notify_call(name, raw_arguments)

            if plan.error is not None:
                text = tool_error_result(
                    "同一轮只能执行一个动作工具；请一次只做一个动作。",
                    code=plan.error,
                )
                self._set_result(results, index, plan, name, text)
                completed.add(index)
                continue

            try:
                text = self.executor.execute(name, raw_arguments)
            except KeyboardInterrupt:
                interrupted = tool_error_result(
                    "用户中断了工具执行；已停止当前任务。",
                    code="interrupted",
                    state="interrupted",
                )
                self._set_result(results, index, plan, name, interrupted)
                completed.add(index)
                self._fill_unfinished(plans, results, completed)
                self._write_tool_history(plans, results)
                raise
            except Exception as exc:  # noqa: BLE001 - 必须给每个 tool_call_id 写回配对结果
                text = tool_error_result(
                    f"工具执行失败：{exc}",
                    code="tool_failed",
                )
            self._set_result(results, index, plan, name, text)
            completed.add(index)

        self._write_tool_history(plans, results)

    def _set_result(
        self,
        results: dict[int, tuple[str, str]],
        index: int,
        plan: PlannedToolCall,
        name: str,
        text: str,
    ) -> None:
        results[index] = (name, text)
        if self._on_tool_result is not None:
            self._on_tool_result(name, text)

    def _fill_unfinished(
        self,
        plans: Sequence[PlannedToolCall],
        results: dict[int, tuple[str, str]],
        completed: set[int],
    ) -> None:
        for index, plan in enumerate(plans):
            if index in completed:
                continue
            name = tool_call_name(plan.tool_call)
            if plan.error is not None:
                text = tool_error_result(
                    "同一轮只能执行一个动作工具；请一次只做一个动作。",
                    code=plan.error,
                )
            else:
                text = tool_error_result(
                    "前一个工具被中断，本工具没有执行。",
                    code="not_executed",
                    state="aborted",
                )
            self._set_result(results, index, plan, name, text)
            completed.add(index)

    def _write_tool_history(
        self,
        plans: Sequence[PlannedToolCall],
        results: Mapping[int, tuple[str, str]],
    ) -> None:
        for index, plan in enumerate(plans):
            result = results.get(index)
            if result is None:
                continue
            _, text = result
            self.history.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call_id(plan.tool_call),
                    "content": text,
                }
            )

    def _notify_call(self, name: str, arguments: str) -> None:
        if self._on_tool_call is not None:
            self._on_tool_call(name, arguments)


def _normalise_tool_calls(
    calls: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for call in calls:
        if not isinstance(call, Mapping):
            continue
        function = call.get("function")
        function_map = dict(function) if isinstance(function, Mapping) else {}
        name = function_map.get("name")
        arguments = function_map.get("arguments")
        normalized.append(
            {
                "id": tool_call_id(call) or f"call_{uuid.uuid4().hex}",
                "type": "function",
                "function": {
                    "name": name if isinstance(name, str) else "",
                    "arguments": (
                        arguments
                        if isinstance(arguments, str)
                        else json.dumps(arguments, ensure_ascii=False)
                        if arguments is not None
                        else ""
                    ),
                },
            }
        )
    return normalized


def _raw_arguments(call: Mapping[str, Any]) -> str:
    function = call.get("function")
    if not isinstance(function, Mapping):
        return ""
    arguments = function.get("arguments")
    if isinstance(arguments, str):
        return arguments
    if arguments is None:
        return ""
    return json.dumps(arguments, ensure_ascii=False)
