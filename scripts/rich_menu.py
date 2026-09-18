import os
from pathlib import Path

from dotenv import load_dotenv
from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    CreateRichMenuAliasRequest,
    MessageAction,
    MessagingApi,
    MessagingApiBlob,
    RichMenuArea,
    RichMenuBounds,
    RichMenuRequest,
    RichMenuSize,
    RichMenuSwitchAction,
)

from log import logger

load_dotenv()

rich_menu_list = [
    {
        "richmenu_alias": "menu-main",
        "richmenu_image": Path("data/richmenu/menu-main.png"),
        "richmenu_content": RichMenuRequest(
            size=RichMenuSize(width=2500, height=1000),
            chatBarText="呼叫情報員！",
            selected=False,
            name="menu-main",
            areas=[
                RichMenuArea(
                    bounds=RichMenuBounds(x=0, y=0, width=1250, height=200),
                    action=RichMenuSwitchAction(
                        label="常用功能", richMenuAliasId="menu-main", data="menu-main"
                    ),
                ),
                RichMenuArea(
                    bounds=RichMenuBounds(x=1250, y=0, width=1250, height=200),
                    action=RichMenuSwitchAction(
                        label="更多功能", richMenuAliasId="menu-more", data="menu-more"
                    ),
                ),
                RichMenuArea(
                    bounds=RichMenuBounds(x=0, y=200, width=800, height=800),
                    action=MessageAction(label="校園交通", text="@公車"),
                ),
                RichMenuArea(
                    bounds=RichMenuBounds(x=900, y=270, width=700, height=300),
                    action=MessageAction(label="校園公佈欄", text="@公佈欄"),
                ),
                RichMenuArea(
                    bounds=RichMenuBounds(x=1700, y=270, width=700, height=300),
                    action=MessageAction(label="校務資訊", text="@校務專區"),
                ),
                RichMenuArea(
                    bounds=RichMenuBounds(x=900, y=630, width=700, height=300),
                    action=MessageAction(label="校園地圖", text="@地圖"),
                ),
                RichMenuArea(
                    bounds=RichMenuBounds(x=1700, y=630, width=700, height=300),
                    action=MessageAction(label="神奇海螺", text="@神奇海螺"),
                ),
            ],
        ),
    },
    {
        "richmenu_alias": "menu-more",
        "richmenu_image": Path("data/richmenu/menu-more.png"),
        "richmenu_content": RichMenuRequest(
            size=RichMenuSize(width=2500, height=1000),
            chatBarText="呼叫情報員！",
            selected=False,
            name="menu-more",
            areas=[
                RichMenuArea(
                    bounds=RichMenuBounds(x=0, y=0, width=1250, height=200),
                    action=RichMenuSwitchAction(
                        label="常用功能", richMenuAliasId="menu-main", data="menu-main"
                    ),
                ),
                RichMenuArea(
                    bounds=RichMenuBounds(x=1250, y=0, width=1250, height=200),
                    action=RichMenuSwitchAction(
                        label="更多功能", richMenuAliasId="menu-more", data="menu-more"
                    ),
                ),
                RichMenuArea(
                    bounds=RichMenuBounds(x=100, y=270, width=700, height=300),
                    action=MessageAction(label="圖書館", text="@圖書館"),
                ),
                RichMenuArea(
                    bounds=RichMenuBounds(x=900, y=270, width=700, height=300),
                    action=MessageAction(label="校園公佈欄", text="@學生餐廳"),
                ),
                RichMenuArea(
                    bounds=RichMenuBounds(x=1700, y=270, width=700, height=300),
                    action=MessageAction(label="載物書院", text="@載物書院"),
                ),
                RichMenuArea(
                    bounds=RichMenuBounds(x=100, y=630, width=2300, height=300),
                    action=MessageAction(label="開發中", text="@開發中，敬請期待"),
                ),
            ],
        ),
    },
]

configuration = Configuration(access_token=os.getenv("LINE_CHANNEL_ACCESS_TOKEN"))


def set_rich_menu():
    if not configuration.access_token:
        raise ValueError("LINE_CHANNEL_ACCESS_TOKEN is required")
    for menu in rich_menu_list:
        if not menu["richmenu_image"].is_file():
            raise FileNotFoundError(menu["richmenu_image"])
    # Enter a context with an instance of the API client
    with ApiClient(configuration) as api_client:
        # Create an instance of the API class
        line_bot_api = MessagingApi(api_client)
        line_bot_blob_api = MessagingApiBlob(api_client)
        # Delete all rich menus
        api_response = line_bot_api.get_rich_menu_list()
        for rich_menu in api_response.richmenus:
            logger.info(f"Deleting Rich Menu ID: {rich_menu.rich_menu_id}")
            logger.debug(rich_menu)
            line_bot_api.delete_rich_menu(rich_menu.rich_menu_id)
        logger.info("All Rich Menus have been deleted.")
        # Create new rich menus
        for new_richmenus in rich_menu_list:
            richmenu_alias = new_richmenus["richmenu_alias"]
            richmenu_image = new_richmenus["richmenu_image"]
            richmenu_content = new_richmenus["richmenu_content"]
            api_response = line_bot_api.create_rich_menu(richmenu_content)
            richmenu_id = api_response.rich_menu_id
            logger.info(f"Created Rich Menu ID: {richmenu_id}")

            # Set the rich menu image
            with open(richmenu_image, "rb") as f:
                image = f.read()
                # Pass image_data as the body and specify content_type for binary upload
                line_bot_blob_api.set_rich_menu_image(
                    richmenu_id, body=image, _headers={"Content-Type": "image/png"}
                )

            # Set the rich menu alias
            # 需要先有圖片和 Rich Menu 才能設定 Alias
            try:
                line_bot_api.delete_rich_menu_alias(richmenu_alias)
            except Exception as e:
                logger.error(e)
            line_bot_api.create_rich_menu_alias(
                CreateRichMenuAliasRequest(
                    richMenuAliasId=richmenu_alias, richMenuId=richmenu_id
                )
            )

            if richmenu_alias == "menu-main":
                line_bot_api.set_default_rich_menu(richmenu_id)

        logger.info("Current Rich Menu List:")
        logger.debug(line_bot_api.get_rich_menu_list())


if __name__ == "__main__":
    set_rich_menu()
