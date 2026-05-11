import asyncio
import sys

import httpx

from wkhelper.core.exceptions import AuthError, WKError
from wkhelper.core.runner import Runner
from wkhelper.platform.base import BasePlatform
from wkhelper.platform.xuetangx import XuetangXPlatform
from wkhelper.platform.yuketang import YuketangPlatform
from wkhelper.ui.rich_ui import RichUI


async def async_main() -> None:
    """异步主入口。"""
    ui = RichUI()
    choice = await ui.select_one(
        "请选择学习平台",
        ["雨课堂 (yuketang.cn)", "学堂在线 (xuetangx.com)", "退出"],
    )

    if choice == "退出":
        return

    async with httpx.AsyncClient(timeout=30, verify=False) as client:
        try:
            match choice:
                case "雨课堂 (yuketang.cn)":
                    platform = YuketangPlatform(client, ui)
                case "学堂在线 (xuetangx.com)":
                    platform = XuetangXPlatform(client, ui)
                case _:
                    return

            # 选择登录方式
            login_method = await ui.select_one(
                "请选择登录方式",
                ["扫码登录", "Cookie 登录", "退出"],
            )

            if login_method == "退出":
                return

            if login_method == "扫码登录":
                await platform.login()
            else:
                await _login_with_cookie_input(platform, ui)

            # 运行程序
            runner = Runner(platform)
            await runner.run_main_menu()

        except WKError as e:
            print(f"\n❌ 错误: {e}")
            sys.exit(1)
        except Exception as e:
            print(f"\n❌ 未知错误: {e}")
            import traceback

            traceback.print_exc()
            sys.exit(1)


async def _login_with_cookie_input(platform: BasePlatform, ui: RichUI) -> None:
    """提示用户输入 Cookie 字符串并登录。"""
    cookie_raw = await ui.input_text("请粘贴浏览器 Cookie 字符串（csrftoken=xxx; sessionid=yyy; ...）：")
    if not cookie_raw:
        print("❌ 未输入 Cookie，返回")
        sys.exit(1)

    cookies = BasePlatform.parse_cookie_string(cookie_raw)
    try:
        await platform.login(cookies=cookies)
    except AuthError as e:
        print(f"\n❌ Cookie 登录失败: {e}")
        sys.exit(1)


def main() -> None:
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        print("\n👋 已取消，程序退出")
        return


if __name__ == "__main__":
    main()
