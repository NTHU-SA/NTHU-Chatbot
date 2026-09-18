from datetime import datetime, timedelta, timezone
from uuid import uuid4

from google.cloud import firestore


class ConversationBusyError(Exception):
    pass


class UserRepository:
    def __init__(self, client: firestore.AsyncClient):
        self.client = client

    async def touch(self, user_id: str, followed: bool | None = None):
        values = {"last_seen_at": firestore.SERVER_TIMESTAMP}
        if followed is not None:
            values["followed"] = followed
        await self.client.collection("users").document(user_id).set(values, merge=True)

    async def begin(self, user_id: str, event_id: str):
        reference = self.client.collection("conversations").document(user_id)
        token = uuid4().hex
        now = datetime.now(timezone.utc)

        @firestore.async_transactional
        async def claim(transaction):
            snapshot = await reference.get(transaction=transaction)
            data = snapshot.to_dict() or {}
            if data.get("expires_at", now) <= now:
                data = {}
            if event_id and data.get("last_event_id") == event_id:
                return None, [], data["last_answer"]
            if data.get("lease_until", now) > now:
                raise ConversationBusyError()
            transaction.set(
                reference,
                {
                    "history": data.get("history", [])[-20:],
                    "lease_token": token,
                    "lease_until": now + timedelta(seconds=90),
                    "expires_at": now + timedelta(days=30),
                },
                merge=True,
            )
            return token, data.get("history", [])[-20:], None

        return await claim(self.client.transaction())

    async def finish(
        self,
        user_id: str,
        token: str,
        event_id: str,
        history: list[dict[str, str]],
        answer: str,
    ):
        reference = self.client.collection("conversations").document(user_id)

        @firestore.async_transactional
        async def save(transaction):
            snapshot = await reference.get(transaction=transaction)
            if (snapshot.to_dict() or {}).get("lease_token") != token:
                raise ConversationBusyError()
            transaction.set(
                reference,
                {
                    "history": history[-20:],
                    "last_event_id": event_id,
                    "last_answer": answer,
                    "updated_at": firestore.SERVER_TIMESTAMP,
                    "expires_at": datetime.now(timezone.utc) + timedelta(days=30),
                },
            )

        await save(self.client.transaction())

    async def release(self, user_id: str, token: str):
        reference = self.client.collection("conversations").document(user_id)

        @firestore.async_transactional
        async def unlock(transaction):
            snapshot = await reference.get(transaction=transaction)
            if (snapshot.to_dict() or {}).get("lease_token") == token:
                transaction.update(
                    reference,
                    {
                        "lease_token": firestore.DELETE_FIELD,
                        "lease_until": firestore.DELETE_FIELD,
                    },
                )

        await unlock(self.client.transaction())
