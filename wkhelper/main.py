import asyncio
import sys

import httpx

from wkhelper.core.exceptions import WKError
from wkhelper.core.runner import Runner
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

            # 初始化平台（登录）
            await platform.login()

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


def main() -> None:
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        print("\n👋 已取消，程序退出")
        return


if __name__ == "__main__":
    main()
