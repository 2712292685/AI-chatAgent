"""端到端：mock OpenAI SSE + mock 机器人 HTTP + 真实 session/工具/客户端。

不访问外网，不需要真实 API key，也不会连接真实机器狗。
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from chatagent.config import (
    ApiConfig,
    Config,
    ContextConfig,
    ModelConfig,
    RobotConfig,
)
from chatagent.persona import Persona
from chatagent.robot_client import RobotClient
from chatagent.robot_policy import build_robot_system_prompt
from chatagent.robot_session import RobotSession
from chatagent.robot_tools import ToolExecutor

PERSONA = Persona(name="阿岩", role="你的深夜自习室同桌", personality="慢热", style="短句")


class MockOpenAIServer:
    """只实现 chat.completions 的 OpenAI 兼容 SSE 服务。"""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length))
                server.requests.append(body)
                if len(server.requests) == 1:
                    chunks = _tool_call_chunks()
                else:
                    chunks = _text_chunks("已前进 0.5 米，实测位移 0.498 米。")
                payload = "".join(
                    f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n" for chunk in chunks
                )
                payload += "data: [DONE]\n\n"
                raw = payload.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                self.wfile.flush()

            def log_message(self, format: str, *args: object) -> None:
                return

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self) -> MockOpenAIServer:
        self.thread.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)

    @property
    def base_url(self) -> str:
        host, port = self.httpd.server_address[:2]
        return f"http://{host}:{port}"


class MockRobotServer:
    """异步任务机器人 API：第一次轮询 running，第二次 succeeded。"""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str, dict[str, Any]]] = []
        self.job_polls = 0
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:  # noqa: N802
                self._handle("GET")

            def do_POST(self) -> None:  # noqa: N802
                self._handle("POST")

            def _handle(self, method: str) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length) if length else b""
                payload = json.loads(raw) if raw else {}
                server.requests.append((method, self.path, payload))

                if self.path == "/api/health":
                    self._json(200, {"ok": True})
                elif self.path == "/api/status":
                    self._json(
                        200,
                        {
                            "stale": False,
                            "connected": True,
                            "estop_latched": False,
                            "battery": {"power": 90},
                        },
                    )
                elif self.path in {"/api/move", "/api/turn", "/api/command"}:
                    self._json(
                        202,
                        {
                            "request_id": payload["request_id"],
                            "state": "running",
                            "kind": self.path.rsplit("/", 1)[-1],
                        },
                    )
                elif self.path.startswith("/api/jobs/"):
                    server.job_polls += 1
                    request_id = self.path.rsplit("/", 1)[-1]
                    if server.job_polls == 1:
                        self._json(
                            200,
                            {"request_id": request_id, "state": "running"},
                        )
                    else:
                        self._json(
                            200,
                            {
                                "request_id": request_id,
                                "state": "succeeded",
                                "result": {"measured": {"forward_m": 0.498}},
                            },
                        )
                elif self.path == "/api/stop":
                    self._json(202, {"ok": True})
                else:
                    self._json(404, {"error": {"message": "not found"}})

            def _json(self, status: int, payload: dict[str, Any]) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                return

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self) -> MockRobotServer:
        self.thread.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)

    @property
    def base_url(self) -> str:
        host, port = self.httpd.server_address[:2]
        return f"http://{host}:{port}"


def _chunk(delta: dict[str, Any], finish_reason: str | None = None) -> dict[str, Any]:
    return {
        "id": "chatcmpl-mock",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "deepseek-flash",
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason,
            }
        ],
    }


def _tool_call_chunks() -> list[dict[str, Any]]:
    return [
        _chunk(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_e2e",
                        "type": "function",
                        "function": {
                            "name": "robot_move",
                            "arguments": '{"direction":"forward",',
                        },
                    }
                ],
            }
        ),
        _chunk(
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "function": {"arguments": '"distance_m":0.5}'},
                    }
                ]
            }
        ),
        _chunk({}, finish_reason="tool_calls"),
    ]


def _text_chunks(text: str) -> list[dict[str, Any]]:
    return [
        _chunk({"role": "assistant", "content": text[:3]}),
        _chunk({"content": text[3:]}),
        _chunk({}, finish_reason="stop"),
    ]


@pytest.fixture
def no_proxy(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-mock")
    yield


def test_full_tool_call_loop_with_mock_model_and_mock_robot(no_proxy: None) -> None:
    del no_proxy
    with MockOpenAIServer() as model_server, MockRobotServer() as robot_server:
        cfg = Config(
            api=ApiConfig(
                base_url=model_server.base_url,
                model="deepseek-flash",
                timeout_seconds=5,
                max_retries=0,
            ),
            model=ModelConfig(thinking=False, temperature=0.8, max_tokens=4096),
            context=ContextConfig(budget_tokens=32000),
            robot=RobotConfig(
                base_url=robot_server.base_url,
                timeout_seconds=5,
                max_tool_rounds=4,
            ),
        )
        client = RobotClient(cfg.robot, poll_interval=0.001)
        executor = ToolExecutor(client, cfg.robot, confirm_clear_estop=lambda: True)
        tool_events: list[tuple[str, str]] = []
        session = RobotSession(
            PERSONA,
            build_robot_system_prompt(PERSONA),
            cfg,
            client=client,
            executor=executor,
            on_tool_call=lambda name, args: tool_events.append(("call", f"{name} {args}")),
            on_tool_result=lambda name, text: tool_events.append(("result", f"{name} {text}")),
        )

        reply = "".join(session.ask("请前进 0.5 米"))

    assert reply == "已前进 0.5 米，实测位移 0.498 米。"
    assert [kind for kind, _ in tool_events] == ["call", "result"]
    assert tool_events[0][1].startswith("robot_move ")
    assert '"measured":{"forward_m":0.498}' in tool_events[1][1]

    move_requests = [
        payload for method, path, payload in robot_server.requests if path == "/api/move"
    ]
    assert len(move_requests) == 1
    assert move_requests[0]["direction"] == "forward"
    assert move_requests[0]["distance_m"] == 0.5
    request_id = move_requests[0]["request_id"]
    assert request_id

    assert len(model_server.requests) == 2
    first_messages = model_server.requests[0]["messages"]
    assert first_messages[0]["role"] == "system"
    assert "机器人控制补充" in first_messages[0]["content"]
    assert model_server.requests[0]["tools"]
    second_messages = model_server.requests[1]["messages"]
    assert any(message.get("tool_calls") for message in second_messages)
    tool_messages = [message for message in second_messages if message.get("role") == "tool"]
    assert len(tool_messages) == 1
    result = json.loads(tool_messages[0]["content"])
    assert result["request_id"] == request_id
    assert result["state"] == "succeeded"
    assert result["measured"]["forward_m"] == 0.498

    assert [message["role"] for message in session.history] == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ]
    assert robot_server.job_polls == 2
