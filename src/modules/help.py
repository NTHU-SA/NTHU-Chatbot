import os

from src.app.handlers.command_handler import command_handler
from templates.messages import help_message

# 不列在說明裡的模組：說明本身與選單
HIDDEN_MODULES = {"help", "menu"}


def visible_prefixes() -> list[str]:
    return [
        prefix
        for module, prefix in command_handler.module_name_to_prefix.items()
        if module not in HIDDEN_MODULES
    ]


@command_handler.add_default_menu()
def usage(event):
    """`@說明`：列出聊天室內的使用方式與可用指令。"""
    return [help_message(visible_prefixes(), os.getenv("LIFF_ID") or None)]
