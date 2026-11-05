"""新手任務：進度不存，全部編在按鈕的 postback 裡。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from linebot.v3.messaging import TextMessage

from src.app.handlers.command_handler import command_handler
from src.modules import onboarding
from src.modules.bus import bus
from templates.messages import onboarding_message as quest


def pressed(data: str):
    """模擬一顆任務按鈕被按下：和 command_handler 解析 postback 的結果一樣。"""
    _module, _prefix, _name, params = command_handler.parse_command(data)
    return SimpleNamespace(user_id="user-1", params=params)


def test_welcome_is_a_greeting_and_a_task_card_with_the_first_button():
    """第一顆按鈕在卡上：Quick Reply 一點別的就消失，`@新手` 又不在說明裡。"""
    greeting, card = quest.welcome_messages("小明")
    assert greeting.text.startswith("小明，初次見面")
    assert len(greeting.text) <= 300  # 手機第一眼要看得完，不是公告
    assert greeting.quick_reply is None and card.quick_reply is None

    lines = [row.text for row in card.contents.body.contents]
    assert sum(line.startswith("⬜") for line in lines) == 3
    (button,) = card.contents.footer.contents
    assert button.action.data == "@新手/任務 n=bus"
    assert button.action.display_text == "開始任務 1"


def test_welcome_without_a_name_still_greets():
    assert quest.welcome_text(None).startswith("嗨！初次見面")


def test_progress_is_carried_in_the_next_button_not_stored():
    """使用者點到任務 3 時，前兩個任務只靠按鈕裡的 done= 記得。"""
    card = quest.progress_card({"bus", "library"})
    lines = [row["text"] for row in card["body"]["contents"]]
    assert lines[:3] == [
        "✅ 任務 1　查一次校車",
        "✅ 任務 2　看圖書館剩幾個位子",
        "⬜ 任務 3　逛一次學生餐廳",
    ]
    assert lines[3] == "最後一個了！"
    (button,) = card["footer"]["contents"]
    assert button["action"]["data"] == "@新手/任務 n=dining done=bus,library"


def test_progress_card_is_the_small_notice_size():
    """Quick Reply 下一則就消失，進度得是會留在聊天室的卡；但它是配角，用小卡。"""
    assert quest.progress_card({"bus"})["size"] == "kilo"


def test_finished_quest_has_no_next_button():
    assert "footer" not in quest.progress_card({"bus", "library", "dining"})


def test_garbled_done_values_are_ignored():
    assert quest.parse_done("bus,hacker,,library") == {"bus", "library"}
    assert quest.parse_done(None) == set()


async def test_task_button_runs_the_feature_then_appends_progress():
    """點「開始任務 1」= 真的查一次公車，後面接進度卡。"""
    feature = [TextMessage(text="公車選單")]
    with patch.object(
        command_handler, "process_message", new=AsyncMock(return_value=feature)
    ) as run:
        messages = await onboarding.start_task(pressed("@新手/任務 n=bus"))
    run.assert_awaited_once_with("@公車/動態公車查詢", "user-1")
    assert messages[0] is feature[0]
    assert messages[-1].alt_text == "新手任務進度"
    button = messages[-1].contents.footer.contents[0]
    assert button.action.data == "@新手/任務 n=library done=bus"


async def test_the_features_quick_reply_moves_to_the_last_message():
    """LINE 只顯示最後一則的 Quick Reply：公車的上山／下山要跟著搬到進度卡上。"""
    # 用真的公車指令，不是假回覆：要守住的正是兩個 PR 接起來之後的形狀
    with patch.object(
        bus.nthuapi, "get", new=AsyncMock(side_effect=AssertionError("方向選擇不該打 API"))
    ):
        messages = await onboarding.start_task(pressed("@新手/任務 n=bus"))
    assert [m.quick_reply is None for m in messages] == [True] * (len(messages) - 1) + [False]
    assert messages[-1].alt_text == "新手任務進度"
    datas = [item.action.data for item in messages[-1].quick_reply.items]
    assert datas == ["@公車/選擇站點 direction=up", "@公車/選擇站點 direction=down"]


def test_each_task_opens_what_its_checklist_line_promises():
    """「看圖書館剩幾個位子」直接打開空間狀況，不是圖書館選單。"""
    for module, *_rest, command in quest.TASKS:
        _mod, _prefix, name, _params = command_handler.parse_command(command)
        if module == "library":
            assert name == "空間狀況"
        if module == "bus":
            assert name == "動態公車查詢"


async def test_the_progress_card_survives_a_long_feature_reply():
    feature = [TextMessage(text=str(index)) for index in range(5)]
    with patch.object(command_handler, "process_message", new=AsyncMock(return_value=feature)):
        messages = await onboarding.start_task(pressed("@新手/任務 n=bus"))
    assert len(messages) == onboarding.MAX_REPLY_MESSAGES
    assert [m.text for m in messages[:-1]] == ["0", "1", "2", "3"]
    assert messages[-1].alt_text == "新手任務進度"


async def test_last_task_awards_the_title():
    with patch.object(command_handler, "process_message", new=AsyncMock(return_value="今日菜單")):
        messages = await onboarding.start_task(pressed("@新手/任務 n=dining done=bus,library"))
    assert messages[0].text == "今日菜單"
    assert quest.TITLE in messages[-1].text


async def test_unknown_task_points_back_to_the_start():
    with patch.object(command_handler, "process_message", new=AsyncMock()) as run:
        (message,) = await onboarding.start_task(pressed("@新手/任務 n=hacker"))
    run.assert_not_awaited()
    assert "@新手" in message.text


async def test_restart_command_resends_the_welcome():
    greeting, card = await command_handler.process_message("@新手", "user-1")
    assert "初次見面" in greeting.text
    assert card.contents.footer.contents[0].action.data == "@新手/任務 n=bus"


def test_onboarding_is_hidden_from_help():
    from src.modules.help import HIDDEN_MODULES

    assert "onboarding" in HIDDEN_MODULES
    assert onboarding  # 模組有被載入
