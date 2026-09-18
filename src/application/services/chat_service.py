import asyncio

from src.infrastructure.ai.openai_agent import OpenAIAgent
from src.infrastructure.firebase.user_repository import UserRepository

RESET_MESSAGE = "\u5c0d\u8a71\u5df2\u91cd\u7f6e\uff0c\u8acb\u91cd\u65b0\u958b\u59cb\u5c0d\u8a71\u3002"


class ChatService:
    def __init__(self, agent: OpenAIAgent, users: UserRepository):
        self.agent = agent
        self.users = users

    async def respond(self, user_id: str, text: str, event_id: str = "") -> str:
        if not text.strip() or len(text) > 2000:
            return "\u8acb\u8f38\u5165 1 \u5230 2000 \u5b57\u7684\u554f\u984c\u3002"
        token, history, cached_answer = await self.users.begin(user_id, event_id)
        if cached_answer is not None:
            return cached_answer
        try:
            if text.strip().lower() == "reset":
                answer = RESET_MESSAGE
                history = []
            else:
                async with asyncio.timeout(45):
                    answer = await self.agent.respond(history, text)
                history = [
                    *history,
                    {"role": "user", "content": text},
                    {"role": "assistant", "content": answer},
                ]
            await self.users.finish(user_id, token, event_id, history, answer)
            return answer
        finally:
            await self.users.release(user_id, token)
