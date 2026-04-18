import json
import os
import re
import subprocess
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, AIMessageChunk
from pydantic import ValidationError

import main


def _extract_inline_script(html: str) -> str:
    match = re.search(r"<script>(.*?)</script>", html, re.DOTALL)
    if match is None:
        raise AssertionError("expected inline script in chat page html")
    return match.group(1)


def _extract_sse_event_data(body: str, event: str) -> dict[str, object]:
    matches = re.findall(rf"event: {re.escape(event)}\ndata: (.+)", body)
    if not matches:
        raise AssertionError(f"expected {event} event in stream body")
    return json.loads(matches[-1])


def _stream_session_message(
    client: TestClient, session_id: str, user_input: str
) -> tuple[int, str, dict[str, object]]:
    with client.stream(
        "POST",
        f"/agent/sessions/{session_id}/messages/stream",
        json={"input": user_input},
    ) as response:
        body = "".join(response.iter_text())
    return response.status_code, body, _extract_sse_event_data(body, "final")


class FakeAutonomyController:
    def __init__(self) -> None:
        self.started = False
        self.start_calls: list[bool] = []
        self.stop_calls: list[bool] = []

    async def start(self, *, force: bool = False) -> None:
        self.start_calls.append(force)
        self.started = True

    async def stop(self, *, disable: bool = True) -> None:
        self.stop_calls.append(disable)
        self.started = False

    async def status(self) -> dict[str, object]:
        return {
            "enabled": self.started,
            "autostartConfigured": False,
            "running": self.started,
            "intervalSeconds": 60,
            "modelName": "gpt-4o-mini",
            "thresholds": {},
            "ledger": {
                "activeExecutions": [
                    {
                        "executionId": "exec-123",
                        "status": "running",
                    }
                ],
                "executionHistory": [
                    {
                        "executionId": "exec-122",
                        "status": "completed",
                    }
                ],
                "circuitBreaker": {
                    "state": "open",
                    "failureCount": 2,
                },
            },
        }

    async def tick(self) -> dict[str, object]:
        return {
            "context": {"wallet": {"balanceEth": "1.0"}},
            "decision": {"action": "hold"},
            "actionResult": {"action": "hold", "changedState": False},
        }


class FakeGraph:
    async def ainvoke(self, payload: dict[str, object]) -> dict[str, object]:
        messages = list(payload["messages"])  # type: ignore[index]
        messages.append(AIMessage(content="interactive reply"))
        return {"messages": messages}

    async def astream(self, payload: dict[str, object], stream_mode: str):
        messages = list(payload["messages"])  # type: ignore[index]
        reply = AIMessage(content="interactive reply")
        yield (
            "messages",
            (AIMessageChunk(content="interactive reply"), {"node": "agent"}),
        )
        yield "values", {"messages": [*messages, reply]}


class EmptyReplyGraph:
    async def ainvoke(self, payload: dict[str, object]) -> dict[str, object]:
        messages = list(payload["messages"])  # type: ignore[index]
        messages.append(
            AIMessage(
                content=[{"type": "text", "text": "   "}],
                response_metadata={"id": "chatcmpl-empty-123", "finish_reason": "stop"},
                additional_kwargs={"provider": "test-openai-compatible"},
            )
        )
        return {"messages": messages}

    async def astream(self, payload: dict[str, object], stream_mode: str):
        messages = list(payload["messages"])  # type: ignore[index]
        yield (
            "values",
            {
                "messages": [
                    *messages,
                    AIMessage(
                        content=[{"type": "text", "text": "   "}],
                        response_metadata={
                            "id": "chatcmpl-empty-123",
                            "finish_reason": "stop",
                        },
                        additional_kwargs={"provider": "test-openai-compatible"},
                    ),
                ]
            },
        )


class RecordingGraph:
    def __init__(self, replies: list[object]) -> None:
        self._replies = replies
        self.calls: list[list[object]] = []

    async def ainvoke(self, payload: dict[str, object]) -> dict[str, object]:
        messages = list(payload["messages"])  # type: ignore[index]
        self.calls.append(list(messages))
        messages.append(self._replies[len(self.calls) - 1])
        return {"messages": messages}

    async def astream(self, payload: dict[str, object], stream_mode: str):
        messages = list(payload["messages"])  # type: ignore[index]
        self.calls.append(list(messages))
        reply = self._replies[len(self.calls) - 1]
        text = main._normalize_message_content(getattr(reply, "content", None))
        if text:
            yield "messages", (AIMessageChunk(content=text), {"node": "agent"})
        yield "values", {"messages": [*messages, reply]}


class StreamingGraph:
    def __init__(self) -> None:
        self.calls: list[list[object]] = []

    async def astream(self, payload: dict[str, object], stream_mode: str):
        self.calls.append(list(payload["messages"]))  # type: ignore[index]
        assert stream_mode == ["messages", "values"]
        yield "messages", (AIMessageChunk(content="你"), {"node": "agent"})
        yield "messages", (AIMessageChunk(content="好"), {"node": "agent"})
        yield "values", {"messages": [*self.calls[-1], AIMessage(content="你好")]}


class FailingStreamingGraph:
    def __init__(self) -> None:
        self.calls: list[list[object]] = []

    async def astream(self, payload: dict[str, object], stream_mode: str):
        self.calls.append(list(payload["messages"]))  # type: ignore[index]
        assert stream_mode == ["messages", "values"]
        yield "messages", (AIMessageChunk(content="你"), {"node": "agent"})
        raise RuntimeError("stream exploded")


class DivergingStreamingGraph:
    def __init__(self) -> None:
        self.stream_calls: list[list[object]] = []
        self.invoke_calls: list[list[object]] = []

    async def astream(self, payload: dict[str, object], stream_mode: str):
        self.stream_calls.append(list(payload["messages"]))  # type: ignore[index]
        assert stream_mode == ["messages", "values"]
        yield "messages", (AIMessageChunk(content="先查工具"), {"node": "agent"})
        yield "messages", (AIMessageChunk(content="，稍等"), {"node": "agent"})
        yield (
            "values",
            {
                "messages": [
                    *self.stream_calls[-1],
                    AIMessage(content="工具调用中间态"),
                    AIMessage(content="最终答案"),
                ]
            },
        )

    async def ainvoke(self, payload: dict[str, object]) -> dict[str, object]:
        messages = list(payload["messages"])  # type: ignore[index]
        self.invoke_calls.append(list(messages))
        messages.append(AIMessage(content="second turn reply"))
        return {"messages": messages}


class DelayedFirstChunkStreamingGraph:
    async def astream(self, payload: dict[str, object], stream_mode: str):
        assert stream_mode == ["messages", "values"]
        await main.asyncio.sleep(0.25)
        messages = list(payload["messages"])  # type: ignore[index]
        yield "messages", (AIMessageChunk(content="你"), {"node": "agent"})
        yield "values", {"messages": [*messages, AIMessage(content="你")]}


class FirstIterationFailureStreamingGraph:
    async def astream(self, payload: dict[str, object], stream_mode: str):
        assert stream_mode == ["messages", "values"]
        raise RuntimeError("first iteration failed")
        yield  # pragma: no cover


class FakeToolClient:
    def __init__(self, tools: list[str]) -> None:
        self._tools = tools

    async def list_tools(self) -> list[str]:
        return self._tools


class RecordingMcpClient:
    def __init__(self, responses: dict[str, dict[str, object]]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def call_tool(
        self, tool_name: str, arguments: dict[str, object]
    ) -> dict[str, object]:
        self.calls.append((tool_name, arguments))
        return self._responses[tool_name]


class MainApiTests(unittest.TestCase):
    def setUp(self) -> None:
        main.get_session_store.cache_clear()
        main.get_chain_activity_store.cache_clear()
        main.clear_discovered_tool_cache()

    def test_sync_endpoint_is_removed(self) -> None:
        controller = FakeAutonomyController()
        with (
            patch.object(main, "get_autonomy_controller", return_value=controller),
            patch.object(main, "get_agent_graph", return_value=FakeGraph()),
        ):
            with TestClient(main.app) as client:
                create_response = client.post("/agent/sessions")
                self.assertEqual(create_response.status_code, 200)
                session_id = create_response.json()["sessionId"]

                send_response = client.post(
                    f"/agent/sessions/{session_id}/messages",
                    json={"input": "你好，先帮我看下 guard"},
                )
                self.assertEqual(send_response.status_code, 404)

                state_response = client.get(f"/agent/sessions/{session_id}")
                self.assertEqual(state_response.status_code, 200)
                self.assertEqual(state_response.json()["sessionId"], session_id)

    def test_stream_emits_start_delta_final_events(self) -> None:
        controller = FakeAutonomyController()
        graph = StreamingGraph()

        with (
            patch.object(main, "get_autonomy_controller", return_value=controller),
            patch.object(main, "get_agent_graph", return_value=graph),
        ):
            with TestClient(main.app) as client:
                create_response = client.post("/agent/sessions")
                self.assertEqual(create_response.status_code, 200)
                session_id = create_response.json()["sessionId"]

                with client.stream(
                    "POST",
                    f"/agent/sessions/{session_id}/messages/stream",
                    json={"input": "你好"},
                ) as response:
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(
                        response.headers["content-type"],
                        "text/event-stream; charset=utf-8",
                    )
                    body = "".join(response.iter_text())

                self.assertIn("event: start", body)
                self.assertGreaterEqual(body.count("event: delta"), 2)
                self.assertIn("event: final", body)
                self.assertIn('"output": "你好"', body)

    def test_stream_emits_start_before_first_model_chunk(self) -> None:
        graph = DelayedFirstChunkStreamingGraph()
        session = main.get_session_store().create()

        async def invoke_stream() -> object:
            return await main.stream_agent_session_message(
                session.session_id, main.AgentChatRequest(input="你好")
            )

        async def read_first_two_chunks(response: object) -> tuple[str, str]:
            first = await anext(response.body_iterator)  # type: ignore[attr-defined]
            second = await anext(response.body_iterator)  # type: ignore[attr-defined]
            return first, second

        with patch.object(main, "get_agent_graph", return_value=graph):
            response = main.asyncio.run(invoke_stream())
            first_chunk, second_chunk = main.asyncio.run(
                read_first_two_chunks(response)
            )

        self.assertIn("event: start", first_chunk)
        self.assertIn("event: delta", second_chunk)
        self.assertIn('"delta": "你"', second_chunk)
        self.assertEqual(getattr(session, "messages", None), [])

    def test_agent_session_stream_setup_failure_emits_error(self) -> None:
        controller = FakeAutonomyController()

        with (
            patch.object(main, "get_autonomy_controller", return_value=controller),
            patch.object(
                main, "get_agent_graph", side_effect=RuntimeError("graph unavailable")
            ),
        ):
            with TestClient(main.app) as client:
                create_response = client.post("/agent/sessions")
                self.assertEqual(create_response.status_code, 200)
                session_id = create_response.json()["sessionId"]

                with client.stream(
                    "POST",
                    f"/agent/sessions/{session_id}/messages/stream",
                    json={"input": "你好"},
                ) as response:
                    self.assertEqual(response.status_code, 200)
                    body = "".join(response.iter_text())

                self.assertIn("event: start", body)
                self.assertIn("event: error", body)
                self.assertNotIn("event: delta", body)
                self.assertNotIn("event: final", body)

                state_response = client.get(f"/agent/sessions/{session_id}")

        self.assertEqual(state_response.status_code, 200)
        self.assertEqual(state_response.json()["messageCount"], 0)

    def test_agent_session_stream_first_iteration_failure_emits_error(self) -> None:
        controller = FakeAutonomyController()

        with (
            patch.object(main, "get_autonomy_controller", return_value=controller),
            patch.object(
                main,
                "get_agent_graph",
                return_value=FirstIterationFailureStreamingGraph(),
            ),
        ):
            with TestClient(main.app) as client:
                create_response = client.post("/agent/sessions")
                self.assertEqual(create_response.status_code, 200)
                session_id = create_response.json()["sessionId"]

                with client.stream(
                    "POST",
                    f"/agent/sessions/{session_id}/messages/stream",
                    json={"input": "你好"},
                ) as response:
                    self.assertEqual(response.status_code, 200)
                    body = "".join(response.iter_text())

                self.assertIn("event: start", body)
                self.assertIn("event: error", body)
                self.assertNotIn("event: delta", body)
                self.assertNotIn("event: final", body)

                state_response = client.get(f"/agent/sessions/{session_id}")

        self.assertEqual(state_response.status_code, 200)
        self.assertEqual(state_response.json()["messageCount"], 0)

    def test_agent_session_stream_final_output_matches_final_message_semantics(
        self,
    ) -> None:
        controller = FakeAutonomyController()
        graph = DivergingStreamingGraph()

        with (
            patch.object(main, "get_autonomy_controller", return_value=controller),
            patch.object(main, "get_agent_graph", return_value=graph),
        ):
            with TestClient(main.app) as client:
                create_response = client.post("/agent/sessions")
                self.assertEqual(create_response.status_code, 200)
                session_id = create_response.json()["sessionId"]

                with client.stream(
                    "POST",
                    f"/agent/sessions/{session_id}/messages/stream",
                    json={"input": "第一轮"},
                ) as response:
                    self.assertEqual(response.status_code, 200)
                    body = "".join(response.iter_text())

                self.assertIn('"output": "最终答案"', body)
                self.assertNotIn('"output": "先查工具，稍等"', body)

                with client.stream(
                    "POST",
                    f"/agent/sessions/{session_id}/messages/stream",
                    json={"input": "第二轮继续"},
                ) as second_response:
                    self.assertEqual(second_response.status_code, 200)
                    "".join(second_response.iter_text())

        persisted_assistant_turns = [
            message
            for message in graph.stream_calls[1]
            if getattr(message, "type", None) == "ai"
        ]
        self.assertEqual(
            [
                getattr(message, "content", None)
                for message in persisted_assistant_turns
            ],
            ["工具调用中间态", "最终答案"],
        )

    def test_agent_session_stream_emits_error_and_does_not_persist_partial_reply(
        self,
    ) -> None:
        controller = FakeAutonomyController()
        graph = FailingStreamingGraph()

        with (
            patch.object(main, "get_autonomy_controller", return_value=controller),
            patch.object(main, "get_agent_graph", return_value=graph),
        ):
            with TestClient(main.app) as client:
                create_response = client.post("/agent/sessions")
                self.assertEqual(create_response.status_code, 200)
                session_id = create_response.json()["sessionId"]

                with client.stream(
                    "POST",
                    f"/agent/sessions/{session_id}/messages/stream",
                    json={"input": "你好"},
                ) as response:
                    self.assertEqual(response.status_code, 200)
                    body = "".join(response.iter_text())

                self.assertIn("event: start", body)
                self.assertIn("event: delta", body)
                self.assertIn("event: error", body)
                self.assertNotIn("event: final", body)

                state_response = client.get(f"/agent/sessions/{session_id}")
                self.assertEqual(state_response.status_code, 200)
                self.assertEqual(state_response.json()["messageCount"], 0)

    def test_empty_model_reply_diagnostics_logs_warning_and_returns_fallback_text(
        self,
    ) -> None:
        controller = FakeAutonomyController()

        with (
            patch.dict(
                os.environ,
                {
                    "BRAIN_AGENT_MODEL": "gpt-4.1-mini-empty-test",
                    "OPENAI_BASE_URL": "https://empty-reply.test/v1",
                },
                clear=False,
            ),
            patch.object(main, "get_autonomy_controller", return_value=controller),
            patch.object(main, "get_agent_graph", return_value=EmptyReplyGraph()),
            self.assertLogs(main.logger.name, level="WARNING") as logs,
        ):
            with TestClient(main.app) as client:
                create_response = client.post("/agent/sessions")
                session_id = create_response.json()["sessionId"]
                status_code, _body, final_event = _stream_session_message(
                    client,
                    session_id,
                    "为什么没有回复？",
                )

        self.assertEqual(status_code, 200)
        self.assertEqual(
            final_event["output"], "模型返回了空回复，请重试或更换模型配置。"
        )
        joined_logs = "\n".join(logs.output)
        self.assertIn("Agent returned empty final output", joined_logs)
        self.assertIn("gpt-4.1-mini-empty-test", joined_logs)
        self.assertIn("https://empty-reply.test/v1", joined_logs)
        self.assertIn("chatcmpl-empty-123", joined_logs)
        self.assertIn("final_message_python_type=AIMessage", joined_logs)
        self.assertIn("response_metadata_id=chatcmpl-empty-123", joined_logs)
        self.assertIn("response_metadata_finish_reason=stop", joined_logs)
        self.assertIn("additional_kwargs_keys=['provider']", joined_logs)
        self.assertIn(
            "response_metadata={'id': 'chatcmpl-empty-123', 'finish_reason': 'stop'}",
            joined_logs,
        )
        self.assertIn(
            "additional_kwargs={'provider': 'test-openai-compatible'}", joined_logs
        )

    def test_extract_final_output_empty_messages_logs_warning_and_returns_fallback(
        self,
    ) -> None:
        with self.assertLogs(main.logger.name, level="WARNING") as logs:
            output = main._extract_final_output([])

        self.assertEqual(output, "模型返回了空回复，请重试或更换模型配置。")
        joined_logs = "\n".join(logs.output)
        self.assertIn("Agent returned empty final output", joined_logs)
        self.assertIn("message_count=0", joined_logs)
        self.assertIn("final_message_type=None", joined_logs)

    def test_agent_run_empty_model_reply_logs_warning_and_returns_fallback(
        self,
    ) -> None:
        controller = FakeAutonomyController()

        with (
            patch.object(main, "get_autonomy_controller", return_value=controller),
            patch.object(main, "get_agent_graph", return_value=EmptyReplyGraph()),
            self.assertLogs(main.logger.name, level="WARNING") as logs,
        ):
            with TestClient(main.app) as client:
                response = client.post(
                    "/agent/run",
                    json={"input": "直接运行为什么没有回复？"},
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["output"], "模型返回了空回复，请重试或更换模型配置。"
        )
        self.assertIn("Agent returned empty final output", "\n".join(logs.output))

    def test_empty_model_reply_session_history_uses_fallback_text_on_next_turn(
        self,
    ) -> None:
        controller = FakeAutonomyController()
        graph = RecordingGraph(
            [
                AIMessage(
                    content=[{"type": "text", "text": "   "}],
                    response_metadata={"id": "chatcmpl-empty-123"},
                ),
                AIMessage(content="second turn reply"),
            ]
        )

        with (
            patch.object(main, "get_autonomy_controller", return_value=controller),
            patch.object(main, "get_agent_graph", return_value=graph),
        ):
            with TestClient(main.app) as client:
                create_response = client.post("/agent/sessions")
                session_id = create_response.json()["sessionId"]

                first_status, _first_body, first_final = _stream_session_message(
                    client,
                    session_id,
                    "第一轮为什么没回复？",
                )
                second_status, _second_body, second_final = _stream_session_message(
                    client,
                    session_id,
                    "第二轮继续",
                )

        self.assertEqual(first_status, 200)
        self.assertEqual(
            first_final["output"], "模型返回了空回复，请重试或更换模型配置。"
        )
        self.assertEqual(second_status, 200)
        self.assertEqual(second_final["output"], "second turn reply")
        persisted_assistant_turns = [
            message
            for message in graph.calls[1]
            if getattr(message, "type", None) == "ai"
        ]
        self.assertEqual(len(persisted_assistant_turns), 1)
        self.assertEqual(
            getattr(persisted_assistant_turns[0], "content", None),
            "模型返回了空回复，请重试或更换模型配置。",
        )

    def test_whitespace_only_string_ai_reply_logs_warning_and_returns_fallback(
        self,
    ) -> None:
        controller = FakeAutonomyController()
        graph = RecordingGraph([AIMessage(content="   ")])

        with (
            patch.object(main, "get_autonomy_controller", return_value=controller),
            patch.object(main, "get_agent_graph", return_value=graph),
            self.assertLogs(main.logger.name, level="WARNING") as logs,
        ):
            with TestClient(main.app) as client:
                create_response = client.post("/agent/sessions")
                session_id = create_response.json()["sessionId"]
                status_code, _body, final_event = _stream_session_message(
                    client,
                    session_id,
                    "空白 assistant 回复",
                )

        self.assertEqual(status_code, 200)
        self.assertEqual(
            final_event["output"], "模型返回了空回复，请重试或更换模型配置。"
        )
        self.assertIn("final_message_type=ai", "\n".join(logs.output))

    def test_non_empty_string_ai_reply_preserves_whitespace_and_skips_warning(
        self,
    ) -> None:
        controller = FakeAutonomyController()
        graph = RecordingGraph([AIMessage(content="  hello\n")])

        with (
            patch.object(main, "get_autonomy_controller", return_value=controller),
            patch.object(main, "get_agent_graph", return_value=graph),
            patch.object(main.logger, "warning") as warning_mock,
        ):
            with TestClient(main.app) as client:
                create_response = client.post("/agent/sessions")
                session_id = create_response.json()["sessionId"]
                status_code, _body, final_event = _stream_session_message(
                    client,
                    session_id,
                    "保留模型原始空白",
                )

        self.assertEqual(status_code, 200)
        self.assertEqual(final_event["output"], "  hello\n")
        warning_mock.assert_not_called()

    def test_non_empty_list_ai_reply_preserves_whitespace_and_skips_warning(
        self,
    ) -> None:
        controller = FakeAutonomyController()
        graph = RecordingGraph([AIMessage(content=[{"text": "  hello\n"}])])

        with (
            patch.object(main, "get_autonomy_controller", return_value=controller),
            patch.object(main, "get_agent_graph", return_value=graph),
            patch.object(main.logger, "warning") as warning_mock,
        ):
            with TestClient(main.app) as client:
                create_response = client.post("/agent/sessions")
                session_id = create_response.json()["sessionId"]
                status_code, _body, final_event = _stream_session_message(
                    client,
                    session_id,
                    "保留列表内容原始空白",
                )

        self.assertEqual(status_code, 200)
        self.assertEqual(final_event["output"], "  hello\n")
        warning_mock.assert_not_called()

    def test_multi_block_list_ai_reply_does_not_inject_newlines(self) -> None:
        controller = FakeAutonomyController()
        graph = RecordingGraph([AIMessage(content=[{"text": "hel"}, {"text": "lo"}])])

        with (
            patch.object(main, "get_autonomy_controller", return_value=controller),
            patch.object(main, "get_agent_graph", return_value=graph),
            patch.object(main.logger, "warning") as warning_mock,
        ):
            with TestClient(main.app) as client:
                create_response = client.post("/agent/sessions")
                session_id = create_response.json()["sessionId"]
                status_code, _body, final_event = _stream_session_message(
                    client,
                    session_id,
                    "多段文本不要插入换行",
                )

        self.assertEqual(status_code, 200)
        self.assertEqual(final_event["output"], "hello")
        warning_mock.assert_not_called()

    def test_empty_non_ai_tail_session_history_appends_fallback_text_on_next_turn(
        self,
    ) -> None:
        controller = FakeAutonomyController()
        graph = RecordingGraph(
            [
                main.HumanMessage(content=[{"type": "text", "text": "   "}]),
                AIMessage(content="second turn reply"),
            ]
        )

        with (
            patch.object(main, "get_autonomy_controller", return_value=controller),
            patch.object(main, "get_agent_graph", return_value=graph),
        ):
            with TestClient(main.app) as client:
                create_response = client.post("/agent/sessions")
                session_id = create_response.json()["sessionId"]

                first_status, _first_body, first_final = _stream_session_message(
                    client,
                    session_id,
                    "第一轮无 assistant 消息",
                )
                second_status, _second_body, second_final = _stream_session_message(
                    client,
                    session_id,
                    "第二轮继续",
                )

        self.assertEqual(first_status, 200)
        self.assertEqual(
            first_final["output"], "模型返回了空回复，请重试或更换模型配置。"
        )
        self.assertEqual(second_status, 200)
        self.assertEqual(second_final["output"], "second turn reply")
        self.assertEqual(getattr(graph.calls[1][-2], "type", None), "ai")
        self.assertEqual(
            getattr(graph.calls[1][-2], "content", None),
            "模型返回了空回复，请重试或更换模型配置。",
        )

    def test_empty_non_ai_tail_with_older_ai_preserves_old_reply_and_appends_fallback(
        self,
    ) -> None:
        controller = FakeAutonomyController()
        graph = RecordingGraph(
            [
                AIMessage(content="seed assistant reply"),
                main.HumanMessage(content=[{"type": "text", "text": "   "}]),
                AIMessage(content="third turn reply"),
            ]
        )

        with (
            patch.object(main, "get_autonomy_controller", return_value=controller),
            patch.object(main, "get_agent_graph", return_value=graph),
            self.assertLogs(main.logger.name, level="WARNING") as logs,
        ):
            with TestClient(main.app) as client:
                create_response = client.post("/agent/sessions")
                session_id = create_response.json()["sessionId"]

                seed_status, _seed_body, seed_final = _stream_session_message(
                    client,
                    session_id,
                    "先来一个正常回复",
                )
                first_status, _first_body, first_final = _stream_session_message(
                    client,
                    session_id,
                    "第二轮以非 assistant 空尾结束",
                )
                second_status, _second_body, second_final = _stream_session_message(
                    client,
                    session_id,
                    "第三轮继续",
                )

        self.assertEqual(seed_status, 200)
        self.assertEqual(seed_final["output"], "seed assistant reply")
        self.assertEqual(first_status, 200)
        self.assertEqual(
            first_final["output"], "模型返回了空回复，请重试或更换模型配置。"
        )
        self.assertEqual(second_status, 200)
        self.assertEqual(second_final["output"], "third turn reply")
        persisted_assistant_turns = [
            message
            for message in graph.calls[2]
            if getattr(message, "type", None) == "ai"
        ]
        self.assertEqual(
            [
                getattr(message, "content", None)
                for message in persisted_assistant_turns
            ],
            ["seed assistant reply", "模型返回了空回复，请重试或更换模型配置。"],
        )
        self.assertIn("final_message_type=human", "\n".join(logs.output))

    def test_non_empty_non_ai_tail_does_not_leak_raw_content_as_assistant_reply(
        self,
    ) -> None:
        controller = FakeAutonomyController()
        graph = RecordingGraph(
            [
                main.HumanMessage(content="tool or human tail content"),
                AIMessage(content="second turn reply"),
            ]
        )

        with (
            patch.object(main, "get_autonomy_controller", return_value=controller),
            patch.object(main, "get_agent_graph", return_value=graph),
            patch.object(main.logger, "warning") as warning_mock,
        ):
            with TestClient(main.app) as client:
                create_response = client.post("/agent/sessions")
                session_id = create_response.json()["sessionId"]

                first_status, _first_body, first_final = _stream_session_message(
                    client,
                    session_id,
                    "最后一条不是 assistant",
                )
                second_status, _second_body, second_final = _stream_session_message(
                    client,
                    session_id,
                    "第二轮继续",
                )

        self.assertEqual(first_status, 200)
        self.assertEqual(
            first_final["output"], "模型返回了空回复，请重试或更换模型配置。"
        )
        self.assertEqual(second_status, 200)
        self.assertEqual(second_final["output"], "second turn reply")
        self.assertNotIn("tool or human tail content", first_final["output"])
        self.assertEqual(getattr(graph.calls[1][-2], "type", None), "ai")
        self.assertEqual(
            getattr(graph.calls[1][-2], "content", None),
            "模型返回了空回复，请重试或更换模型配置。",
        )
        warning_mock.assert_not_called()

    def test_normal_model_reply_does_not_emit_empty_reply_warning(self) -> None:
        controller = FakeAutonomyController()

        with (
            patch.object(main, "get_autonomy_controller", return_value=controller),
            patch.object(main, "get_agent_graph", return_value=FakeGraph()),
            patch.object(main.logger, "warning") as warning_mock,
        ):
            with TestClient(main.app) as client:
                create_response = client.post("/agent/sessions")
                session_id = create_response.json()["sessionId"]
                status_code, _body, final_event = _stream_session_message(
                    client,
                    session_id,
                    "正常回复测试",
                )

        self.assertEqual(status_code, 200)
        self.assertEqual(final_event["output"], "interactive reply")
        warning_mock.assert_not_called()

    def test_chat_page_is_served(self) -> None:
        controller = FakeAutonomyController()
        with patch.object(main, "get_autonomy_controller", return_value=controller):
            with TestClient(main.app) as client:
                response = client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("OntologyAgent Console", response.text)
        self.assertIn("/agent/sessions", response.text)

    def test_chat_page_console_first_sections(self) -> None:
        controller = FakeAutonomyController()

        with patch.object(main, "get_autonomy_controller", return_value=controller):
            with TestClient(main.app) as client:
                response = client.get("/")

        self.assertEqual(response.status_code, 200)
        for section_heading in [
            "Runtime",
            "Freqtrade",
            "Chain",
            "Recent Chain Action",
            "管家",
        ]:
            self.assertIn(section_heading, response.text)

    def test_chat_page_console_layout_classes(self) -> None:
        controller = FakeAutonomyController()

        with patch.object(main, "get_autonomy_controller", return_value=controller):
            with TestClient(main.app) as client:
                response = client.get("/")

        self.assertEqual(response.status_code, 200)
        for marker in ["console-shell", "observability-grid", "detail-grid"]:
            self.assertIn(marker, response.text)
        normalized_html = " ".join(response.text.split())
        self.assertIn(
            "@media (max-width: 960px) { .topbar { padding: 16px; } .console-layout { gap: 16px; } .topbar-actions, .observability-grid, .detail-grid { grid-template-columns: 1fr; } .chat-panel { min-height: auto; } .composer-actions { flex-direction: column; align-items: stretch; }",
            normalized_html,
        )

    def test_chat_page_renders_runtime_freqtrade_chain_detail_targets(self) -> None:
        controller = FakeAutonomyController()

        with patch.object(main, "get_autonomy_controller", return_value=controller):
            with TestClient(main.app) as client:
                response = client.get("/")

        self.assertEqual(response.status_code, 200)
        for marker in [
            'id="runtime-status"',
            'id="freqtrade-state"',
            'id="chain-identifier"',
            'id="execution-snapshot"',
            'id="warnings-panel"',
            'id="warnings-text"',
            'id="action-result"',
        ]:
            self.assertIn(marker, response.text)

    def test_chat_page_console_action_buttons_and_refresh_helpers(self) -> None:
        controller = FakeAutonomyController()

        with patch.object(main, "get_autonomy_controller", return_value=controller):
            with TestClient(main.app) as client:
                response = client.get("/")

        self.assertEqual(response.status_code, 200)
        for marker in [
            'id="start-button"',
            'id="tick-button"',
            'id="stop-button"',
            "async function refreshDashboard",
        ]:
            self.assertIn(marker, response.text)

    def test_chat_page_includes_streaming_chat_helpers(self) -> None:
        controller = FakeAutonomyController()

        with patch.object(main, "get_autonomy_controller", return_value=controller):
            with TestClient(main.app) as client:
                response = client.get("/")

        self.assertEqual(response.status_code, 200)
        script_text = _extract_inline_script(response.text)
        self.assertIn("async function streamAgentMessage", script_text)
        self.assertIn("function appendOrUpdateStreamingAgentMessage", script_text)

    def test_chat_page_streaming_placeholder_state_is_separate_from_message_body(
        self,
    ) -> None:
        controller = FakeAutonomyController()

        with patch.object(main, "get_autonomy_controller", return_value=controller):
            with TestClient(main.app) as client:
                response = client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn('[data-streaming-state="loading"]::after', response.text)
        script_text = _extract_inline_script(response.text)
        self.assertIn('article.dataset.streamingState = "loading"', script_text)
        self.assertIn('appendOrUpdateStreamingAgentMessage("")', script_text)

    def test_chat_page_defines_streaming_cleanup_helper_for_transport_failures(
        self,
    ) -> None:
        controller = FakeAutonomyController()

        with patch.object(main, "get_autonomy_controller", return_value=controller):
            with TestClient(main.app) as client:
                response = client.get("/")

        self.assertEqual(response.status_code, 200)
        script_text = _extract_inline_script(response.text)
        self.assertIn("function clearStreamingAgentMessage", script_text)
        self.assertIn("state.streamingAgentMessageEl = null;", script_text)
        self.assertIn(
            "clearStreamingAgentMessage(`流式回复失败：${error.message}`, true);",
            script_text,
        )

    def test_chat_page_treats_stream_eof_without_terminal_event_as_failure(
        self,
    ) -> None:
        controller = FakeAutonomyController()

        with patch.object(main, "get_autonomy_controller", return_value=controller):
            with TestClient(main.app) as client:
                response = client.get("/")

        self.assertEqual(response.status_code, 200)
        script_text = _extract_inline_script(response.text)
        self.assertIn(
            'clearStreamingAgentMessage("流式回复异常结束", true);', script_text
        )

    def test_chat_page_tracks_pre_start_eof_and_surfaces_stream_failure(self) -> None:
        controller = FakeAutonomyController()

        with patch.object(main, "get_autonomy_controller", return_value=controller):
            with TestClient(main.app) as client:
                response = client.get("/")

        self.assertEqual(response.status_code, 200)
        script_text = _extract_inline_script(response.text)
        self.assertIn("let startEventReceived = false;", script_text)
        self.assertIn("startEventReceived = true;", script_text)
        self.assertIn(
            'addMessage("system", "System", "流式回复异常结束");', script_text
        )

    def test_chat_page_preserves_partial_stream_content_for_stream_errors_and_aborts(
        self,
    ) -> None:
        controller = FakeAutonomyController()

        with patch.object(main, "get_autonomy_controller", return_value=controller):
            with TestClient(main.app) as client:
                response = client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn('[data-streaming-state="error"]::after', response.text)
        script_text = _extract_inline_script(response.text)
        self.assertIn("if (isError && currentContent) {", script_text)
        self.assertIn(
            'const currentContent = article._messageBodyEl?.textContent ?? "";',
            script_text,
        )
        self.assertIn('article.className = "message agent";', script_text)
        self.assertIn('article._messageLabelEl.textContent = "Agent";', script_text)
        self.assertIn("article.dataset.streamingStatus = content;", script_text)
        self.assertIn(
            'clearStreamingAgentMessage(`流式回复失败：${payload.error ?? "未知错误"}`, true);',
            script_text,
        )
        self.assertIn(
            'clearStreamingAgentMessage("流式回复异常结束", true);', script_text
        )

    def test_chat_page_registers_periodic_dashboard_refresh(self) -> None:
        controller = FakeAutonomyController()

        with patch.object(main, "get_autonomy_controller", return_value=controller):
            with TestClient(main.app) as client:
                response = client.get("/")

        self.assertEqual(response.status_code, 200)
        script_text = _extract_inline_script(response.text)
        self.assertIn("setInterval(refreshDashboard", script_text)

    def test_chat_page_refresh_dashboard_skips_overlapping_fetches(self) -> None:
        controller = FakeAutonomyController()

        with patch.object(main, "get_autonomy_controller", return_value=controller):
            with TestClient(main.app) as client:
                response = client.get("/")

        self.assertEqual(response.status_code, 200)
        script_text = _extract_inline_script(response.text)
        self.assertIn("let dashboardRefreshPromise = null;", script_text)
        self.assertIn("if (dashboardRefreshPromise) {", script_text)
        self.assertIn("return dashboardRefreshPromise;", script_text)
        self.assertIn("dashboardRefreshPromise = (async () => {", script_text)
        self.assertIn("dashboardRefreshPromise = null;", script_text)

    def test_request_json_uses_http_status_when_error_body_is_not_json(self) -> None:
        controller = FakeAutonomyController()

        with patch.object(main, "get_autonomy_controller", return_value=controller):
            with TestClient(main.app) as client:
                response = client.get("/")

        self.assertEqual(response.status_code, 200)
        script_text = _extract_inline_script(response.text)
        node_result = subprocess.run(
            [
                "node",
                "-e",
                "const fs = require('fs');"
                "const scriptText = fs.readFileSync(0, 'utf8');"
                "const makeElement = () => ({"
                "textContent: '', style: {}, disabled: false, value: '/autonomy/status',"
                "appendChild() {}, append() {}, addEventListener() {}, focus() {},"
                "scrollTop: 0, scrollHeight: 0"
                "});"
                "global.document = {"
                "getElementById() { return makeElement(); },"
                "querySelectorAll() { return []; },"
                "createElement() { return makeElement(); },"
                "createTextNode(text) { return text; }"
                "};"
                "global.fetch = async () => ({"
                "ok: false,"
                "status: 502,"
                "statusText: 'Bad Gateway',"
                "json: async () => { throw new Error('invalid json'); }"
                "});"
                "global.setInterval = () => 0;"
                "(async () => {"
                "eval(scriptText);"
                "try {"
                "await requestJson('/health', { method: 'GET' });"
                "process.stdout.write(JSON.stringify({ message: null }));"
                "} catch (error) {"
                "process.stdout.write(JSON.stringify({ message: error.message }));"
                "}"
                "})().catch((error) => { console.error(error); process.exit(1); });",
            ],
            input=script_text,
            text=True,
            capture_output=True,
        )
        self.assertEqual(node_result.returncode, 0, node_result.stderr)
        helper_output = json.loads(node_result.stdout)
        self.assertEqual(helper_output["message"], "HTTP 502 Bad Gateway")

    def test_tick_action_does_not_overwrite_runtime_card_before_health_refresh(
        self,
    ) -> None:
        controller = FakeAutonomyController()

        with patch.object(main, "get_autonomy_controller", return_value=controller):
            with TestClient(main.app) as client:
                response = client.get("/")

        self.assertEqual(response.status_code, 200)
        script_text = _extract_inline_script(response.text)
        node_result = subprocess.run(
            [
                "node",
                "-e",
                "const fs = require('fs');"
                "const scriptText = fs.readFileSync(0, 'utf8');"
                "const elements = new Map();"
                "const makeElement = () => ({"
                "textContent: '', style: {}, disabled: false, value: '/autonomy/status',"
                "appendChild() {}, append() {}, addEventListener() {}, focus() {},"
                "scrollTop: 0, scrollHeight: 0"
                "});"
                "global.document = {"
                "getElementById(id) {"
                "if (!elements.has(id)) elements.set(id, makeElement());"
                "return elements.get(id);"
                "},"
                "querySelectorAll() { return []; },"
                "createElement() { return makeElement(); },"
                "createTextNode(text) { return text; }"
                "};"
                "const healthPayload = {"
                "autonomy: { enabled: true, running: true, summary: { circuitState: 'closed' }, ledger: { healthStatus: 'ok', lastDecision: { action: 'buy' } } }"
                "};"
                "const tickPayload = {"
                "context: { wallet: { balanceEth: '1.0' } },"
                "decision: { action: 'hold' },"
                "actionResult: { action: 'hold', changedState: false }"
                "};"
                "let healthCalls = 0;"
                "global.fetch = async (url) => {"
                "if (url === '/health') {"
                "healthCalls += 1;"
                "if (healthCalls === 1) { return { ok: true, json: async () => healthPayload }; }"
                "return { ok: true, json: async () => await new Promise(() => {}) };"
                "}"
                "if (url === '/autonomy/tick') { return { ok: true, json: async () => tickPayload }; }"
                "throw new Error(`Unexpected URL: ${url}`);"
                "};"
                "global.setInterval = () => 0;"
                "(async () => {"
                "eval(scriptText);"
                "await refreshDashboard();"
                "const before = elements.get('runtime-status').textContent;"
                "await runGuardAction('/autonomy/tick', '执行理财子 Tick');"
                "const after = elements.get('runtime-status').textContent;"
                "process.stdout.write(JSON.stringify({ before, after, actionResult: elements.get('action-result').textContent }));"
                "})().catch((error) => { console.error(error); process.exit(1); });",
            ],
            input=script_text,
            text=True,
            capture_output=True,
        )
        self.assertEqual(node_result.returncode, 0, node_result.stderr)
        helper_output = json.loads(node_result.stdout)
        self.assertEqual(
            helper_output["before"],
            "运行状态: 运行中\n健康状态: ok\n熔断状态: closed\n最近建议: buy",
        )
        self.assertEqual(helper_output["after"], helper_output["before"])
        self.assertIn('"action": "hold"', helper_output["actionResult"])

    def test_action_triggered_refresh_runs_once_more_after_in_flight_poll_settles(
        self,
    ) -> None:
        controller = FakeAutonomyController()

        with patch.object(main, "get_autonomy_controller", return_value=controller):
            with TestClient(main.app) as client:
                response = client.get("/")

        self.assertEqual(response.status_code, 200)
        script_text = _extract_inline_script(response.text)
        node_result = subprocess.run(
            [
                "node",
                "-e",
                "const fs = require('fs');"
                "const scriptText = fs.readFileSync(0, 'utf8');"
                "const elements = new Map();"
                "const makeElement = () => ({"
                "textContent: '', style: {}, disabled: false, value: '/autonomy/status',"
                "appendChild() {}, append() {}, addEventListener() {}, focus() {},"
                "scrollTop: 0, scrollHeight: 0"
                "});"
                "global.document = {"
                "getElementById(id) {"
                "if (!elements.has(id)) elements.set(id, makeElement());"
                "return elements.get(id);"
                "},"
                "querySelectorAll() { return []; },"
                "createElement() { return makeElement(); },"
                "createTextNode(text) { return text; }"
                "};"
                "const healthPayloads = ["
                "{ autonomy: { enabled: true, running: true, summary: { circuitState: 'closed' }, ledger: { healthStatus: 'ok', lastDecision: { action: 'buy' } } } },"
                "{ autonomy: { enabled: true, running: true, summary: { circuitState: 'closed' }, ledger: { healthStatus: 'ok', lastDecision: { action: 'sell' } } } }"
                "];"
                "let resolveFirstHealth;"
                "let healthCalls = 0;"
                "global.fetch = async (url) => {"
                "if (url === '/health') {"
                "const payload = healthPayloads[healthCalls];"
                "healthCalls += 1;"
                "if (healthCalls === 1) {"
                "return { ok: true, json: async () => await new Promise((resolve) => { resolveFirstHealth = () => resolve(payload); }) };"
                "}"
                "return { ok: true, json: async () => payload };"
                "}"
                "if (url === '/autonomy/tick') { return { ok: true, json: async () => ({ ok: true }) }; }"
                "throw new Error(`Unexpected URL: ${url}`);"
                "};"
                "global.setInterval = () => 0;"
                "(async () => {"
                "eval(scriptText);"
                "await runGuardAction('/autonomy/tick', '执行理财子 Tick');"
                "resolveFirstHealth();"
                "await new Promise((resolve) => setImmediate(resolve));"
                "await new Promise((resolve) => setImmediate(resolve));"
                "process.stdout.write(JSON.stringify({"
                "healthCalls,"
                "runtimeStatus: elements.get('runtime-status').textContent"
                "}));"
                "})().catch((error) => { console.error(error); process.exit(1); });",
            ],
            input=script_text,
            text=True,
            capture_output=True,
        )
        self.assertEqual(node_result.returncode, 0, node_result.stderr)
        helper_output = json.loads(node_result.stdout)
        self.assertEqual(helper_output["healthCalls"], 2)
        self.assertEqual(
            helper_output["runtimeStatus"],
            "运行状态: 运行中\n健康状态: ok\n熔断状态: closed\n最近建议: sell",
        )

    def test_chat_page_view_model_helpers_map_observability_payload_fields(
        self,
    ) -> None:
        controller = FakeAutonomyController()

        with patch.object(main, "get_autonomy_controller", return_value=controller):
            with TestClient(main.app) as client:
                response = client.get("/")

        self.assertEqual(response.status_code, 200)
        script_text = _extract_inline_script(response.text)
        payloads = {
            "runtime": {
                "autonomy": {
                    "enabled": True,
                    "running": False,
                    "summary": {"circuitState": "open"},
                    "ledger": {
                        "healthStatus": "degraded",
                        "lastDecision": {"action": "hold"},
                        "activeExecutions": [{"executionId": "exec-123"}],
                        "executionHistory": [{"executionId": "exec-122"}],
                        "circuitBreaker": {"state": "open"},
                        "lastTickAt": "2026-04-10T10:00:00Z",
                    },
                }
            },
            "freqtrade": {
                "freqtradeStatus": {
                    "state": "running",
                    "runmode": "dry_run",
                    "exchange": "binance",
                    "strategy": "SimpleAgentStrategy",
                    "openTradeCount": 2,
                }
            },
            "chain": {
                "chainWallet": {"wallet": {"address": "0xabc"}},
                "recentChainAction": {
                    "tool": "chain_submit_execution",
                    "summary": {
                        "kind": "submit_execution",
                        "txHash": "0x123",
                        "valueEth": "0.5",
                    },
                },
            },
            "chainWrapped": {
                "chainWallet": {"result": {"wallet": {"address": "0xdef"}}},
                "recentChainAction": {
                    "tool": "chain_submit_execution",
                    "summary": {
                        "kind": "submit_execution",
                        "txHash": "0x456",
                        "valueEth": "0.25",
                    },
                },
            },
            "health": {
                "autonomy": {
                    "enabled": False,
                    "running": False,
                    "error": "autonomy unavailable",
                    "summary": {"circuitState": "open"},
                    "ledger": {
                        "healthStatus": "critical",
                        "lastError": "tick failed",
                        "circuitBreaker": {"state": "open"},
                    },
                },
                "chainError": "chain offline",
                "chainWalletError": "wallet unavailable",
                "freqtradeError": "freqtrade offline",
                "freqtradeStatus": {
                    "runningState": "unavailable",
                    "error": "snapshot unavailable",
                },
                "chainWallet": {"wallet": {}},
            },
            "healthWrapped": {
                "autonomy": {
                    "enabled": False,
                    "running": False,
                    "summary": {"circuitState": "closed"},
                    "ledger": {"healthStatus": "ok"},
                },
                "chainWallet": {"result": {"wallet": {"address": "0xdef"}}},
            },
        }
        node_result = subprocess.run(
            [
                "node",
                "-e",
                "const fs = require('fs');"
                "const scriptText = fs.readFileSync(0, 'utf8');"
                f"const payloads = {json.dumps(payloads)};"
                "const makeElement = () => ({"
                "textContent: '', style: {}, disabled: false, value: '/autonomy/status',"
                "appendChild() {}, append() {}, addEventListener() {}, focus() {},"
                "scrollTop: 0, scrollHeight: 0"
                "});"
                "global.document = {"
                "getElementById() { return makeElement(); },"
                "querySelectorAll() { return []; },"
                "createElement() { return makeElement(); }"
                "};"
                "global.fetch = async () => ({ ok: true, json: async () => ({}) });"
                "global.setInterval = () => 0;"
                "eval(scriptText);"
                "process.stdout.write(JSON.stringify({"
                "runtime: buildRuntimeViewModel(payloads.runtime),"
                "freqtrade: buildFreqtradeViewModel(payloads.freqtrade),"
                "chain: buildChainViewModel(payloads.chain),"
                "chainWrapped: buildChainViewModel(payloads.chainWrapped),"
                "chainFailed: buildChainViewModel(payloads.health),"
                "executionSnapshot: buildExecutionSnapshotViewModel(payloads.runtime),"
                "warnings: buildWarningsViewModel(payloads.health),"
                "warningsWrapped: buildWarningsViewModel(payloads.healthWrapped)"
                "}));",
            ],
            input=script_text,
            text=True,
            capture_output=True,
        )
        self.assertEqual(node_result.returncode, 0, node_result.stderr)
        helper_output = json.loads(node_result.stdout)
        self.assertEqual(
            helper_output["runtime"],
            {
                "running": "已启用，待运行",
                "health": "degraded",
                "circuitState": "open",
                "decision": "hold",
            },
        )
        self.assertEqual(
            helper_output["freqtrade"],
            {
                "running": "running | dry_run | binance | SimpleAgentStrategy",
                "openTrades": "2",
            },
        )
        self.assertEqual(
            helper_output["chain"],
            {
                "signerAddress": "0xabc",
                "recentAction": "submit_execution | value=0.5 ETH | tx=0x123",
            },
        )
        self.assertEqual(
            helper_output["chainWrapped"],
            {
                "signerAddress": "0xdef",
                "recentAction": "submit_execution | value=0.25 ETH | tx=0x456",
            },
        )
        self.assertEqual(
            helper_output["chainFailed"],
            {
                "signerAddress": "读取失败",
                "recentAction": "暂无",
            },
        )
        self.assertEqual(
            helper_output["executionSnapshot"],
            {
                "activeExecutions": "1",
                "executionHistory": "1",
                "circuitState": "open",
                "lastTickAt": "2026-04-10T10:00:00Z",
            },
        )
        self.assertEqual(
            helper_output["warnings"],
            [
                "Runtime health: critical",
                "Circuit breaker: open",
                "Chain wallet error: wallet unavailable",
                "Chain error: chain offline",
                "Freqtrade error: freqtrade offline",
                "Freqtrade error: snapshot unavailable",
                "Autonomy error: autonomy unavailable",
                "Autonomy error: tick failed",
            ],
        )
        self.assertEqual(helper_output["warningsWrapped"], [])

    def test_autonomy_management_endpoints_use_controller(self) -> None:
        controller = FakeAutonomyController()
        with patch.object(main, "get_autonomy_controller", return_value=controller):
            with TestClient(main.app) as client:
                start_response = client.post("/autonomy/start")
                self.assertEqual(start_response.status_code, 200)
                self.assertTrue(start_response.json()["enabled"])
                self.assertEqual(
                    start_response.json()["summary"]["activeExecutionCount"], 1
                )

                tick_response = client.post("/autonomy/tick")
                self.assertEqual(tick_response.status_code, 200)
                self.assertEqual(tick_response.json()["decision"]["action"], "hold")

                stop_response = client.post("/autonomy/stop")
                self.assertEqual(stop_response.status_code, 200)
                self.assertFalse(stop_response.json()["enabled"])
                self.assertEqual(
                    stop_response.json()["summary"]["circuitState"], "open"
                )

        self.assertIn(True, controller.start_calls)
        self.assertIn(True, controller.stop_calls)

    def test_autonomy_status_exposes_active_execution_and_circuit_breaker(self) -> None:
        controller = FakeAutonomyController()

        with patch.object(main, "get_autonomy_controller", return_value=controller):
            with TestClient(main.app) as client:
                response = client.get("/autonomy/status")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(
            body["ledger"]["activeExecutions"][0]["executionId"], "exec-123"
        )
        self.assertEqual(body["ledger"]["circuitBreaker"]["state"], "open")
        self.assertEqual(body["summary"]["activeExecutionCount"], 1)

    def test_health_includes_autonomy_execution_summary(self) -> None:
        controller = FakeAutonomyController()

        with (
            patch.object(main, "get_autonomy_controller", return_value=controller),
            patch.object(
                main,
                "get_chain_mcp_client",
                return_value=FakeToolClient(["chain_get_wallet_state"]),
            ),
            patch.object(
                main,
                "get_freqtrade_mcp_client",
                return_value=FakeToolClient(["freqtrade_status"]),
            ),
            patch.object(
                main, "get_chain_wallet_state", return_value={"balanceEth": "1.0"}
            ),
        ):
            with TestClient(main.app) as client:
                response = client.get("/health")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(
            body["autonomy"]["ledger"]["activeExecutions"][0]["executionId"], "exec-123"
        )

    def test_health_includes_chain_and_freqtrade_status_fields(self) -> None:
        controller = FakeAutonomyController()

        class FakeChainClient:
            async def list_tools(self) -> list[str]:
                return ["chain_sign_transfer", "chain_get_wallet_state"]

        class FakeFreqtradeClient:
            async def list_tools(self) -> list[str]:
                return ["get_trading_status", "get_open_trades"]

        with (
            patch.object(main, "get_autonomy_controller", return_value=controller),
            patch.object(main, "get_chain_mcp_client", return_value=FakeChainClient()),
            patch.object(
                main, "get_freqtrade_mcp_client", return_value=FakeFreqtradeClient()
            ),
            patch.object(
                main,
                "get_chain_wallet_state",
                return_value={"wallet": {"address": "0xabc"}},
            ),
            patch.object(
                main,
                "get_freqtrade_status_snapshot",
                return_value={
                    "openTradeCount": 2,
                    "state": "running",
                    "runmode": "dry_run",
                    "exchange": "binance",
                    "strategy": "SimpleAgentStrategy",
                },
            ),
            patch.object(
                main,
                "get_chain_activity_store",
            ) as activity_store_factory,
        ):
            activity_store_factory.return_value.get.return_value = {
                "tool": "chain_sign_transfer",
                "summary": {"kind": "sign_transfer"},
            }
            with TestClient(main.app) as client:
                response = client.get("/health")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["chainWallet"]["wallet"]["address"], "0xabc")
        self.assertEqual(payload["recentChainAction"]["tool"], "chain_sign_transfer")
        self.assertEqual(payload["freqtradeStatus"]["openTradeCount"], 2)
        self.assertEqual(payload["freqtradeStatus"]["state"], "running")

    def test_health_console_fields_include_expected_contract(self) -> None:
        controller = FakeAutonomyController()

        with (
            patch.object(main, "get_autonomy_controller", return_value=controller),
            patch.object(
                main,
                "get_chain_mcp_client",
                return_value=FakeToolClient(["chain_get_wallet_state"]),
            ),
            patch.object(
                main,
                "get_freqtrade_mcp_client",
                return_value=FakeToolClient(["freqtrade_status"]),
            ),
            patch.object(
                main,
                "get_chain_wallet_state",
                return_value={"wallet": {"address": "0xabc", "balanceEth": "1.0"}},
            ),
            patch.object(
                main,
                "get_freqtrade_status_snapshot",
                return_value={"openTradeCount": 2, "state": "running"},
            ),
            patch.object(
                main,
                "get_chain_activity_store",
            ) as activity_store_factory,
        ):
            activity_store_factory.return_value.get.return_value = {
                "tool": "chain_sign_transfer",
                "summary": {"kind": "sign_transfer", "txHash": "0x123"},
            }
            with TestClient(main.app) as client:
                response = client.get("/health")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("autonomy", payload)
        self.assertIn("freqtradeStatus", payload)
        self.assertIn("chainWallet", payload)
        self.assertIn("recentChainAction", payload)
        self.assertEqual(payload["autonomy"]["summary"]["activeExecutionCount"], 1)
        self.assertEqual(payload["freqtradeStatus"]["state"], "running")
        self.assertEqual(payload["chainWallet"]["wallet"]["address"], "0xabc")
        self.assertEqual(payload["recentChainAction"]["summary"]["txHash"], "0x123")

    def test_health_reports_chain_wallet_error_when_wallet_lookup_fails(self) -> None:
        controller = FakeAutonomyController()

        with (
            patch.object(main, "get_autonomy_controller", return_value=controller),
            patch.object(
                main,
                "get_chain_mcp_client",
                return_value=FakeToolClient(["chain_get_wallet_state"]),
            ),
            patch.object(
                main,
                "get_freqtrade_mcp_client",
                return_value=FakeToolClient(["freqtrade_status"]),
            ),
            patch.object(
                main,
                "get_chain_wallet_state",
                side_effect=RuntimeError("wallet unavailable"),
            ),
        ):
            with TestClient(main.app) as client:
                response = client.get("/health")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIsNone(payload["chainWallet"])
        self.assertEqual(payload["chainWalletError"], "wallet unavailable")

    def test_autonomy_start_and_stop_summary_shape_matches_console_contract(
        self,
    ) -> None:
        controller = FakeAutonomyController()

        with patch.object(main, "get_autonomy_controller", return_value=controller):
            with TestClient(main.app) as client:
                start_response = client.post("/autonomy/start")
                stop_response = client.post("/autonomy/stop")

        self.assertEqual(start_response.status_code, 200)
        self.assertEqual(stop_response.status_code, 200)
        self.assertEqual(
            start_response.json()["summary"],
            {"activeExecutionCount": 1, "circuitState": "open"},
        )
        self.assertEqual(
            stop_response.json()["summary"],
            {"activeExecutionCount": 1, "circuitState": "open"},
        )

    def test_build_tools_exposes_chain_settlement_query_tools(self) -> None:
        main.set_discovered_tool_cache(
            chain_tools=[
                main._make_structured_tool(
                    "chain_get_transaction_receipt",
                    main.CHAIN_TOOL_REGISTRY["chain_get_transaction_receipt"],
                ),
                main._make_structured_tool(
                    "chain_get_user_operation_status",
                    main.CHAIN_TOOL_REGISTRY["chain_get_user_operation_status"],
                ),
            ],
            freqtrade_tools=[],
        )
        tool_names = {tool.name for tool in main.build_tools()}

        self.assertIn("chain_get_transaction_receipt", tool_names)
        self.assertIn("chain_get_user_operation_status", tool_names)

    def test_execute_freqtrade_trade_intent_routes_intent_to_chain_execution(
        self,
    ) -> None:
        freqtrade_client = RecordingMcpClient(
            {
                "emit_trade_intent": {
                    "intent": {
                        "intentId": "intent-123",
                        "pair": "ETH/USDC",
                        "side": "long",
                        "amount": 10.5,
                        "amountType": "quote",
                        "orderType": "market",
                        "limitPrice": None,
                        "maxSlippageBps": 75,
                        "reason": "agent_requested_trade",
                    }
                }
            }
        )
        chain_client = RecordingMcpClient(
            {
                "chain_execute_trade_intent": {
                    "status": "submitted",
                    "txHash": "0xtrade123",
                }
            }
        )

        with (
            patch.object(main, "get_freqtrade_mcp_client", return_value=freqtrade_client),
            patch.object(main, "get_chain_mcp_client", return_value=chain_client),
        ):
            result = main.asyncio.run(
                main.execute_freqtrade_trade_intent_tool(
                    pair="ETH/USDC",
                    stakeAmount=10.5,
                    maxSlippageBps=75,
                )
            )

        self.assertEqual(
            freqtrade_client.calls,
            [
                (
                    "emit_trade_intent",
                    {
                        "pair": "ETH/USDC",
                        "side": "long",
                        "stake_amount": 10.5,
                        "order_type": "market",
                        "max_slippage_bps": 75,
                        "reason": "agent_requested_trade",
                    },
                )
            ],
        )
        self.assertEqual(
            chain_client.calls,
            [
                (
                    "chain_execute_trade_intent",
                    {
                        "intentId": "intent-123",
                        "pair": "ETH/USDC",
                        "side": "long",
                        "amount": "10.5",
                        "amountType": "quote",
                        "orderType": "market",
                        "maxSlippageBps": 75,
                        "reason": "agent_requested_trade",
                    },
                )
            ],
        )
        self.assertEqual(
            result,
            {
                "tool": "execute_freqtrade_trade_intent",
                "tradeIntent": {
                    "intentId": "intent-123",
                    "pair": "ETH/USDC",
                    "side": "long",
                    "amount": 10.5,
                    "amountType": "quote",
                    "orderType": "market",
                    "limitPrice": None,
                    "maxSlippageBps": 75,
                    "reason": "agent_requested_trade",
                },
                "result": {
                    "status": "submitted",
                    "txHash": "0xtrade123",
                },
            },
        )

    def test_execute_freqtrade_trade_intent_rejects_limit_orders_in_v1(self) -> None:
        with self.assertRaisesRegex(ValidationError, "limit orders are unsupported in V1"):
            main.asyncio.run(
                main.execute_freqtrade_trade_intent_tool(
                    pair="ETH/USDC",
                    stakeAmount=10.5,
                    orderType="limit",
                    price=2000.0,
                )
            )

    def test_health_recent_chain_action_summarizes_current_settlement_shape(
        self,
    ) -> None:
        controller = FakeAutonomyController()

        with (
            patch.object(main, "get_autonomy_controller", return_value=controller),
            patch.object(
                main,
                "get_chain_mcp_client",
                return_value=FakeToolClient(["chain_get_wallet_state"]),
            ),
            patch.object(
                main,
                "get_freqtrade_mcp_client",
                return_value=FakeToolClient(["freqtrade_status"]),
            ),
            patch.object(
                main, "get_chain_wallet_state", return_value={"balanceEth": "1.0"}
            ),
        ):
            main.get_chain_activity_store().set(
                "chain_submit_execution",
                main._summarize_chain_result(
                    "chain_submit_execution",
                    {
                        "result": {
                            "execution": {"to": "0xabc", "valueEth": "0.25"},
                            "settlement": {
                                "identifier": "0xsettlement",
                                "kind": "submitted",
                                "status": "submitted",
                            },
                        }
                    },
                ),
            )

            with TestClient(main.app) as client:
                response = client.get("/health")

        self.assertEqual(response.status_code, 200)
        recent_chain_action = response.json()["recentChainAction"]
        self.assertEqual(recent_chain_action["summary"]["kind"], "submit_execution")
        self.assertEqual(recent_chain_action["summary"]["txHash"], "0xsettlement")
        self.assertEqual(recent_chain_action["summary"]["status"], "submitted")

    def test_health_recent_chain_action_summarizes_user_operation_target(self) -> None:
        controller = FakeAutonomyController()

        with (
            patch.object(main, "get_autonomy_controller", return_value=controller),
            patch.object(
                main,
                "get_chain_mcp_client",
                return_value=FakeToolClient(["chain_get_wallet_state"]),
            ),
            patch.object(
                main,
                "get_freqtrade_mcp_client",
                return_value=FakeToolClient(["freqtrade_status"]),
            ),
            patch.object(
                main, "get_chain_wallet_state", return_value={"balanceEth": "1.0"}
            ),
        ):
            main.get_chain_activity_store().set(
                "chain_submit_user_operation",
                main._summarize_chain_result(
                    "chain_submit_user_operation",
                    {
                        "result": {
                            "userOperation": {
                                "target": "0xdef",
                                "userOpHash": "0xuserop123",
                            },
                            "settlement": {
                                "identifier": "0xuserop123",
                                "kind": "user-operation",
                                "status": "submitted",
                            },
                        }
                    },
                ),
            )

            with TestClient(main.app) as client:
                response = client.get("/health")

        self.assertEqual(response.status_code, 200)
        recent_chain_action = response.json()["recentChainAction"]
        self.assertEqual(
            recent_chain_action["summary"]["kind"], "submit_user_operation"
        )
        self.assertEqual(recent_chain_action["summary"]["target"], "0xdef")
        self.assertEqual(recent_chain_action["summary"]["userOpHash"], "0xuserop123")


if __name__ == "__main__":
    unittest.main()
