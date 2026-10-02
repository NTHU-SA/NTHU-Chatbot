import asyncio
import importlib
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml
from linebot.v3.messaging import (
    Action,
    CarouselColumn,
    CarouselTemplate,
    MessageAction,
    TemplateMessage,
)

from log import logger


@dataclass
class MenuInfo:
    """選單資訊。"""

    title: str
    description: str
    actions: list[Action]
    image_url: str | None = None


@dataclass
class Command:
    """指令定義。"""

    module: str
    names: list[str]
    function: Callable
    menu_info: MenuInfo | None = None


@dataclass
class ModuleConfig:
    """模組配置。"""

    commands: dict[str, Command] = field(default_factory=dict)
    default_menu: Callable | None = None
    default_reply: Callable | None = None


class CommandEvent:
    """命令事件。"""

    def __init__(self, user_id, text, params):
        self.user_id = user_id
        self.message = text
        self.params = params


class CommandHandlerError(Exception):
    """命令處理器基底例外類別"""

    pass


class ModuleNotFoundError(CommandHandlerError):
    """模組未找到例外"""

    def __init__(self, module_name):
        super().__init__(f"模組 {module_name} 未註冊")
        self.module_name = module_name


class CommandNotFoundError(CommandHandlerError):
    """指令未找到例外"""

    def __init__(self, prefix, command_name):
        super().__init__(f"找不到指令【{prefix}/{command_name}】")
        self.prefix = prefix
        self.command_name = command_name


class CommandHandler:
    """命令處理器。

    負責註冊、管理和執行來自不同模組的命令。
    透過載入設定檔進行初始化，並提供方法來註冊命令、處理訊息和產生選單。
    """

    def __init__(self, config_path: str = "bot_config.yaml"):
        """初始化命令處理器。"""
        self.modules: dict[str, ModuleConfig] = {}
        self.config = self._load_config(config_path)
        self.command_prefix = "@"
        self._initialize_module_mappings()
        self._menu_cache: dict[str, list[TemplateMessage]] = {}

    def _initialize_module_mappings(self) -> None:
        """初始化模組名稱與前綴的映射關係。"""
        modules_config = self.config.get("modules", {})
        self.module_name_to_prefix = {
            module: info["prefix"] for module, info in modules_config.items() if "prefix" in info
        }
        self.prefix_to_module_name = {
            info["prefix"]: module for module, info in modules_config.items() if "prefix" in info
        }

    @staticmethod
    @lru_cache(maxsize=1)
    def _load_config(config_path: str) -> dict:
        """載入設定檔。

        使用 `lru_cache` 避免重複讀取設定檔，提升效能。
        """
        config_path_obj = Path(config_path)
        if not config_path_obj.exists():
            raise FileNotFoundError(f"設定檔不存在: {config_path}")

        with config_path_obj.open("r", encoding="UTF-8") as file:
            return yaml.safe_load(file)

    def add_command(self, name: str) -> Callable:
        """添加基礎命令裝飾器。

        用於註冊不帶選單資訊的命令。
        命令名稱可以使用 '|' 分隔多個名稱。
        """

        def decorator(func: Callable) -> Callable:
            module = self._get_module_name(func.__module__)
            if module not in self.modules:
                self.modules[module] = ModuleConfig()
            if isinstance(name, list):
                command_names = [n.strip() for n in name]
            else:
                command_names = [n.strip() for n in name.split("|")]
            command = Command(module=module, names=command_names, function=func)

            for cmd_name in command_names:
                self.modules[module].commands[cmd_name] = command

            self._clear_menu_cache(module)
            return func

        return decorator

    def add_command_with_menu(
        self,
        name: str | list[str],
        title: str,
        description: str,
        actions: list[Action],
        image_url: str | None = None,
    ) -> Callable:
        """添加帶選單資訊的命令裝飾器。

        用於註冊帶有選單資訊的命令，例如標題、描述和操作。
        命令名稱可以使用 '|' 分隔多個名稱。
        """

        def decorator(func: Callable) -> Callable:
            module = self._get_module_name(func.__module__)
            if module not in self.modules:
                self.modules[module] = ModuleConfig()

            menu_info = MenuInfo(
                title=title,
                description=description,
                actions=actions,
                image_url=image_url,
            )
            if isinstance(name, list):
                command_names = [n.strip() for n in name]
            else:
                command_names = [n.strip() for n in name.split("|")]
            command = Command(
                module=module,
                names=command_names,
                function=func,
                menu_info=menu_info,
            )
            for cmd_name in command_names:
                self.modules[module].commands[cmd_name] = command

            self._clear_menu_cache(module)
            return func

        return decorator

    def add_default_menu(self) -> Callable:
        """添加預設選單處理器裝飾器。

        用於註冊模組的預設選單處理函數。
        當使用者輸入模組前綴但沒有指定命令時，將會呼叫此函數。
        """

        def decorator(func: Callable) -> Callable:
            module = self._get_module_name(func.__module__)
            if module not in self.modules:
                self.modules[module] = ModuleConfig()
            self.modules[module].default_menu = func
            self._clear_menu_cache(module)
            return func

        return decorator

    def add_default_reply(self) -> Callable:
        """添加預設回覆處理器裝飾器。

        用於註冊模組的預設回覆處理函數。
        當使用者輸入的命令無法識別時，將會呼叫此函數。
        """

        def decorator(func: Callable) -> Callable:
            module = self._get_module_name(func.__module__)
            if module not in self.modules:
                self.modules[module] = ModuleConfig()
            self.modules[module].default_reply = func
            return func

        return decorator

    @staticmethod
    def _get_module_name(full_module_name: str) -> str:
        """從完整模組路徑中提取模組名稱。"""
        parts = full_module_name.split(".")
        if len(parts) >= 3:
            return parts[2]
        return ""

    def has_menu_info(self, module: str) -> bool:
        """檢查模組是否包含選單資訊。"""
        module_config = self.modules.get(module)
        if not module_config:
            return False
        return any(cmd.menu_info is not None for cmd in module_config.commands.values())

    def _create_carousel_column(self, command: Command, prefix: str) -> CarouselColumn:
        """創建輪播選單列。"""
        if not command.menu_info:
            default_text = f"{self.command_prefix}{prefix}/{command.names[0]}"
            actions = [MessageAction(label=command.names[0], text=default_text)]
        else:
            actions = command.menu_info.actions

        return CarouselColumn(
            title=(command.menu_info.title if command.menu_info else command.names[0]),
            text=(command.menu_info.description if command.menu_info else ""),
            actions=actions,
            thumbnail_image_url=(command.menu_info.image_url if command.menu_info else None),
        )

    async def auto_generate_default_menu(self, module: str) -> list[TemplateMessage]:
        """自動生成預設選單。

        為指定模組自動生成輪播選單，顯示模組下所有帶有選單資訊的指令。
        """
        if module in self._menu_cache:
            return self._menu_cache[module]

        module_config = self.modules.get(module)
        if not module_config:
            raise ModuleNotFoundError(module)

        prefix = self.module_name_to_prefix.get(module)
        if not prefix:
            raise ValueError(f"模組 {module} 未配置前綴")

        menu_columns = [
            self._create_carousel_column(cmd, prefix)
            for cmd in module_config.commands.values()
            if cmd.menu_info
        ]

        if not menu_columns:
            raise ValueError(f"{prefix} 模組沒有可用的選單資訊")

        self._menu_cache[module] = [
            TemplateMessage(
                alt_text=f"{prefix} 模組選單",
                template=CarouselTemplate(columns=menu_columns),
            )
        ]
        return self._menu_cache[module]

    def _clear_menu_cache(self, module: str):
        """清除指定模組的選單快取。"""
        if module in self._menu_cache:
            del self._menu_cache[module]

    def parse_command(self, message: str) -> tuple[str, str, str, dict]:
        """解析命令字串。

        從使用者輸入的訊息中解析出模組名稱、前綴、命令名稱和參數。
        支援解析 `key=value` 格式的參數。
        """
        if not message.startswith(self.command_prefix):
            return "", "", "", {}

        command_text = message[len(self.command_prefix) :]
        parts = command_text.split("/", 1)

        if len(parts) < 2:
            prefix = parts[0]
            module = self.prefix_to_module_name.get(prefix, "")
            return module, prefix, "", {}

        prefix, command_string = parts

        params = {}
        command_name = ""

        param_delimiter_index = command_string.find(" ")
        if param_delimiter_index != -1:
            command_name = command_string[:param_delimiter_index].strip()
            params_string = command_string[param_delimiter_index:].strip()

            param_pairs = params_string.split(" ")
            for pair in param_pairs:
                if "=" in pair:
                    key, value = pair.split("=", 1)
                    params[key.strip()] = value.strip()
                elif pair:
                    # 不記錄參數與指令內容（隱私權政策：@ 指令只記錄使用的功能）
                    logger.warning("參數格式錯誤（缺少等號），已忽略")
        else:
            command_name = command_string.strip()

        module = self.prefix_to_module_name.get(prefix, "")
        return module, prefix, command_name, params

    async def execute_command(
        self,
        module: str,
        prefix: str,
        command_name: str,
        user_id: str,
        text: str,
        params: dict = None,
    ):
        """執行命令。

        根據解析出的模組名稱、命令名稱和參數，執行對應的命令函數。
        """
        if not module:
            raise ModuleNotFoundError("")

        module_config = self.modules.get(module)
        if not module_config:
            raise ModuleNotFoundError(module)

        command_event = CommandEvent(user_id=user_id, text=text, params=params or {})
        # 只記錄模組名稱：指令名稱與參數都是使用者輸入的文字，不寫進 log
        logger.info("執行命令: {}", module)

        if not command_name:
            if module_config.default_menu:
                if asyncio.iscoroutinefunction(module_config.default_menu):
                    return await module_config.default_menu(command_event)
                else:
                    return module_config.default_menu(command_event)
            if self.has_menu_info(module):
                return await self.auto_generate_default_menu(module)
            raise CommandNotFoundError(prefix, "")

        command = module_config.commands.get(command_name)
        if command:
            if asyncio.iscoroutinefunction(command.function):
                return await command.function(command_event)
            else:
                return command.function(command_event)
        else:
            if module_config.default_reply:
                if asyncio.iscoroutinefunction(module_config.default_reply):
                    return await module_config.default_reply(command_event)
                else:
                    return module_config.default_reply(command_event)
            raise CommandNotFoundError(prefix, command_name)

    async def process_message(self, message: str, user_id: str):
        """處理訊息入口。

        接收使用者輸入的訊息，解析命令並執行對應的處理函數。
        """
        module, prefix, command_name, params = self.parse_command(message)
        try:
            return await self.execute_command(
                module, prefix, command_name, user_id, message, params
            )
        except ModuleNotFoundError as e:
            # 例外訊息含使用者輸入的指令文字：只記錄類型
            logger.warning("處理訊息失敗: {}", type(e).__name__)
            return "模組未找到，請確認指令是否正確。"
        except CommandNotFoundError as e:
            logger.warning("處理訊息失敗: {}", type(e).__name__)
            return f"找不到指令【{e.prefix}/{e.command_name}】，請確認指令是否正確。"
        except CommandHandlerError as e:
            logger.warning("處理訊息失敗: {}", type(e).__name__)
            return "指令處理錯誤，請稍後再試。"
        except Exception as e:
            logger.error("處理訊息時發生未預期錯誤: {}", type(e).__name__)
            return "處理訊息時發生錯誤，請稍後再試。"


command_handler = CommandHandler()

for module_name in command_handler.config["modules"]:
    logger.debug(f"載入模組 {module_name}...")
    try:
        importlib.import_module(f"src.modules.{module_name}")
    except ImportError as e:
        logger.error(f"載入模組 {module_name} 失敗: {e}")
    logger.info(f"模組 {module_name} 載入完成")
