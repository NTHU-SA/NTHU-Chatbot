"""刪除一個 user 的所有資料（使用者自己要求刪除，或身分被移到別的 user 後留下的孤兒帳號）。"""

from __future__ import annotations

from collections.abc import Callable

from src.application.services.chat_store import ChatStore
from src.application.services.user_store import UserStore


async def erase_user(
    chats: ChatStore,
    users: UserStore,
    user_id: str,
    on_marked: Callable[[], None] | None = None,
) -> None:
    """
    1. 先把帳號標成 deleting：其他請求（其他實例在狀態快取過期後）一律被擋，只能再呼叫刪除；
    2. 刪除對話與 user 資料，每一步都重新查詢確認清空；user 文件最後換成不含個資的墓碑；
    3. 再刪一次對話：涵蓋墓碑寫入前還在進行的請求建立的對話（之後建立的會自己撤銷）。

    `on_marked` 在標成 deleting 之後立刻呼叫（清掉本實例的身分快取，讓狀態快取不再放行）。
    沒刪乾淨時拋出 DeletionIncompleteError，帳號維持 deleting，可以再呼叫一次。
    """
    await users.begin_deletion(user_id)
    if on_marked is not None:
        on_marked()
    await chats.delete_all_sessions(user_id)
    await users.delete_user(user_id)
    await chats.delete_all_sessions(user_id)
