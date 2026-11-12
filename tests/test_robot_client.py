"""robot_client：HTTP 契约、鉴权、错误与任务轮询测试。"""

from __future__ import annotations

import json
import socket
import threading
from collections.abc import Iterator
from contextlib import closing, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from chatagent import robot_client as robot_client_mod
from chatagent.config import RobotConfig
from chatagent.robot_client import TERMINAL_JOB_STATES, RobotClient, RobotClientError


class MockRobotServer:
    """在回环地址上提供一个可编程的最小机器人 API。"""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str, dict[str, str], dict[str, Any]]] = []
        self.job_states = ["running", "succeeded"]
        self.never_finish = False
        self.fail_move_once = False
        self._move_attempts = 0
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                self._handle()

            def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                self._handle()

            def _handle(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length) if length else b""
                payload = json.loads(raw) if raw else {}
                server.requests.append((self.command, self.path, dict(self.headers), payload))

                if self.path == "/api/health":
                    self._json(200, {"ok": True})
                elif self.path == "/api/status":
                    self._json(200, {"ok": True, "stale": False, "battery": {"power": 88}})
                elif self.path in {"/api/move", "/api/turn", "/api/command"}:
                    server._move_attempts += 1
                    if server.fail_move_once and server._move_attempts == 1:
                        self._json(500, {"error": {"message": "temporary"}})
                        return
                    self._json(
                        202,
                        {
                            "request_id": payload["request_id"],
                            "state": "running",
                            "kind": self.path.rsplit("/", 1)[-1],
                        },
                    )
                elif self.path.startswith("/api/jobs/"):
                    if server.never_finish:
                        state = "running"
                    elif len(server.job_states) > 1:
                        state = server.job_states.pop(0)
                    else:
                        state = "succeeded"
                    request_id = self.path.rsplit("/", 1)[-1]
                    job: dict[str, Any] = {
                        "request_id": request_id,
                        "state": state,
                        "kind": "move",
                    }
                    if state == "succeeded":
                        job["result"] = {"measured": {"forward_m": 0.998}}
                    self._json(200, job)
                elif self.path == "/api/stop":
                    self._json(202, {"ok": True})
                elif self.path == "/api/estop":
                    self._json(202, {"ok": True, "estop_latched": True})
                elif self.path == "/api/estop/clear":
                    self._json(202, {"ok": True, "estop_latched": False})
                elif self.path == "/api/boom":
                    self._json(500, {"error": {"message": "server exploded"}})
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

    def last(self) -> tuple[str, str, dict[str, str], dict[str, Any]]:
        return self.requests[-1]


@contextmanager
def unused_port() -> Iterator[int]:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        yield int(sock.getsockname()[1])


def test_health_status_and_bearer_token_are_sent() -> None:
    with MockRobotServer() as server:
        client = RobotClient(
            RobotConfig(),
            base_url=server.base_url,
            token="secret-token",
            environ={},
        )

        assert client.get_health() == {"ok": True}
        status = client.get_status()

    assert status["battery"]["power"] == 88
    _, path, headers, _ = server.last()
    assert path == "/api/status"
    assert headers["Authorization"] == "Bearer secret-token"


def test_environment_overrides_base_url_and_reads_token() -> None:
    client = RobotClient(
        RobotConfig(),
        environ={
            "AGENT_CONTROL_BASE_URL": "http://127.0.0.1:9999/",
            "AGENT_CONTROL_TOKEN": " from-env ",
        },
    )

    assert client.base_url == "http://127.0.0.1:9999"
    assert client.token == "from-env"


def test_move_posts_json_with_uuid_request_id_and_polls_until_terminal() -> None:
    with MockRobotServer() as server:
        sleeps: list[float] = []
        client = RobotClient(
            RobotConfig(),
            base_url=server.base_url,
            token="",
            environ={},
            sleep=sleeps.append,
        )

        job = client.move("forward", 1.0)
        result = client.wait_for_job(job["request_id"])

    _, path, headers, payload = next(
        request for request in server.requests if request[1] == "/api/move"
    )
    assert path == "/api/move"
    assert headers["Content-Type"].startswith("application/json")
    assert payload["direction"] == "forward"
    assert payload["distance_m"] == 1.0
    request_id = payload["request_id"]
    assert len(request_id) == 36
    assert request_id.count("-") == 4
    assert result["state"] == "succeeded"
    assert result["result"]["measured"]["forward_m"] == 0.998
    assert sleeps == [0.2]


def test_post_retry_reuses_the_same_request_id() -> None:
    with MockRobotServer() as server:
        server.fail_move_once = True
        sleeps: list[float] = []
        client = RobotClient(
            RobotConfig(),
            base_url=server.base_url,
            token="",
            environ={},
            sleep=sleeps.append,
        )

        job = client.move("backward", 0.5)

    move_requests = [item for item in server.requests if item[1] == "/api/move"]
    assert len(move_requests) == 2
    assert move_requests[0][3]["request_id"] == move_requests[1][3]["request_id"]
    assert job["request_id"] == move_requests[0][3]["request_id"]
    assert sleeps == [0.2]


def test_turn_posture_stop_and_estop_paths_are_wired() -> None:
    with MockRobotServer() as server:
        client = RobotClient(RobotConfig(), base_url=server.base_url, token="", environ={})

        job = client.turn("left", 90)
        assert job["request_id"]
        assert client.stop()["ok"] is True
        assert client.emergency_stop()["estop_latched"] is True
        assert client.clear_estop()["estop_latched"] is False

    assert [item[1] for item in server.requests] == [
        "/api/turn",
        "/api/stop",
        "/api/estop",
        "/api/estop/clear",
    ]


def test_http_4xx_becomes_a_chinese_client_error() -> None:
    with MockRobotServer() as server:
        client = RobotClient(RobotConfig(), base_url=server.base_url, token="", environ={})
        server.job_states = ["running"]

        # 不存在的路径走 handler 的 404 分支。
        with pytest.raises(RobotClientError) as excinfo:
            client._request_json("GET", "/missing")

    assert excinfo.value.status_code == 404
    assert "not found" in excinfo.value.message


def test_http_5xx_is_retryable() -> None:
    with MockRobotServer() as server:
        client = RobotClient(RobotConfig(), base_url=server.base_url, token="", environ={})

        with pytest.raises(RobotClientError) as excinfo:
            client._request_json("GET", "/api/boom")

    assert excinfo.value.status_code == 500
    assert excinfo.value.retryable is True
    assert "server exploded" in excinfo.value.message


def test_connection_refused_is_wrapped() -> None:
    with unused_port() as port:
        client = RobotClient(
            RobotConfig(),
            base_url=f"http://127.0.0.1:{port}",
            token="",
            environ={},
            timeout_seconds=0.2,
        )

        with pytest.raises(RobotClientError) as excinfo:
            client.get_health()

    assert "无法连接机器人控制服务" in excinfo.value.message
    assert excinfo.value.retryable is True


def test_timeout_is_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_timeout(*args: object, **kwargs: object) -> object:
        raise TimeoutError("slow")

    monkeypatch.setattr(robot_client_mod, "urlopen", raise_timeout)
    client = RobotClient(
        RobotConfig(),
        base_url="http://127.0.0.1:8100",
        token="",
        environ={},
    )

    with pytest.raises(RobotClientError) as excinfo:
        client.get_health()

    assert "无法连接机器人控制服务" in excinfo.value.message
    assert excinfo.value.retryable is True


def test_wait_for_job_local_timeout_does_not_fake_success() -> None:
    with MockRobotServer() as server:
        server.never_finish = True
        times = iter([0.0, 0.0, 60.0])
        client = RobotClient(
            RobotConfig(),
            base_url=server.base_url,
            token="",
            environ={},
            sleep=lambda seconds: None,
            clock=lambda: next(times),
        )

        with pytest.raises(RobotClientError) as excinfo:
            client.wait_for_job("rid", timeout_seconds=55)

    assert "等待机器人任务超时" in excinfo.value.message
    assert excinfo.value.payload["state"] in {"running", "succeeded"}


def test_job_nested_in_response_is_unwrapped() -> None:
    assert {"succeeded", "failed", "aborted", "timed_out"} == TERMINAL_JOB_STATES
    wrapped = robot_client_mod._as_job(
        {"request_id": "outer", "job": {"state": "succeeded"}},
        "rid",
    )

    assert wrapped == {"request_id": "outer", "state": "succeeded"}
