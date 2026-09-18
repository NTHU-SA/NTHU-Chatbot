import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.application.services.chat_service import RESET_MESSAGE, ChatService
from src.infrastructure.ai.openai_agent import ALLOWED_TOOLS, MCP_URL, OpenAIAgent


class AgentTests(unittest.IsolatedAsyncioTestCase):
    def make_agent(self, **overrides):
        values = {"status": "completed", "output": [], "output_text": "Answer"}
        values.update(overrides)
        self.create = AsyncMock(return_value=SimpleNamespace(**values))
        return OpenAIAgent(
            SimpleNamespace(responses=SimpleNamespace(create=self.create)),
            "gpt-4.1-mini",
        )

    async def test_remote_mcp_configuration_and_history(self):
        agent = self.make_agent()
        history = [{"role": "user", "content": "Earlier"}]
        self.assertEqual(await agent.respond(history, "Next bus?"), "Answer")
        kwargs = self.create.call_args.kwargs
        self.assertFalse(kwargs["store"])
        self.assertEqual(kwargs["input"][-1], {"role": "user", "content": "Next bus?"})
        self.assertEqual(len(history), 1)
        self.assertEqual(kwargs["tools"][0]["server_url"], MCP_URL)
        self.assertEqual(kwargs["tools"][0]["allowed_tools"], ALLOWED_TOOLS)

    async def test_incomplete_empty_and_tool_error_are_rejected(self):
        for overrides in [
            {"status": "incomplete"},
            {"output_text": " "},
            {"output": [SimpleNamespace(type="mcp_call", error="unavailable")]},
            {"output": [SimpleNamespace(type="mcp_approval_request")]},
        ]:
            with self.subTest(overrides=overrides), self.assertRaises(RuntimeError):
                await self.make_agent(**overrides).respond([], "Question")

    async def test_answer_is_bounded(self):
        self.assertEqual(
            len(await self.make_agent(output_text="a" * 6000).respond([], "Question")),
            2000,
        )


class ChatServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.users = AsyncMock()
        self.users.begin.return_value = (
            "token",
            [{"role": "user", "content": "Old"}],
            None,
        )
        self.agent = AsyncMock()
        self.agent.respond.return_value = "Answer"
        self.service = ChatService(self.agent, self.users)

    async def test_history_is_loaded_and_saved_by_user(self):
        self.assertEqual(
            await self.service.respond("user-1", "Question", "event-1"), "Answer"
        )
        self.users.begin.assert_awaited_once_with("user-1", "event-1")
        args = self.users.finish.call_args.args
        self.assertEqual(args[:3], ("user-1", "token", "event-1"))
        self.assertEqual(args[3][-1], {"role": "assistant", "content": "Answer"})
        self.users.release.assert_awaited_once_with("user-1", "token")

    async def test_reset_clears_persistent_history_without_ai_call(self):
        self.assertEqual(
            await self.service.respond("user-1", "reset", "event-1"), RESET_MESSAGE
        )
        self.agent.respond.assert_not_awaited()
        self.users.finish.assert_awaited_once_with(
            "user-1", "token", "event-1", [], RESET_MESSAGE
        )

    async def test_failed_ai_does_not_save_and_releases_lease(self):
        self.agent.respond.side_effect = RuntimeError("Unavailable")
        with self.assertRaises(RuntimeError):
            await self.service.respond("user-1", "Question")
        self.users.finish.assert_not_awaited()
        self.users.release.assert_awaited_once()

    async def test_redelivery_reuses_answer_without_model_call(self):
        self.users.begin.return_value = (None, [], "Cached")
        self.assertEqual(
            await self.service.respond("user-1", "Question", "event-1"), "Cached"
        )
        self.agent.respond.assert_not_awaited()
        self.users.finish.assert_not_awaited()
        self.users.release.assert_not_awaited()

    async def test_oversize_input_does_not_access_storage_or_model(self):
        await self.service.respond("user-1", "a" * 2001)
        self.users.begin.assert_not_awaited()
        self.agent.respond.assert_not_awaited()
