import asyncio
import sys

import niquests

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

    async with niquests.AsyncSession(timeout=10) as client:
        try:
            match choice:
                case "雨课堂 (yuketang.cn)":
                    platform_id = "ykt"
                    platform = YuketangPlatform(client, ui)
                case "学堂在线 (xuetangx.com)":
                    platform_id = "xtzx"
                    platform = XuetangXPlatform(client, ui)
                case _:
                    return

            from wkhelper.core.credential_store import credential_store

            while True:
                accounts = credential_store.list_accounts(platform_id)
                options = []
                for acc in accounts:
                    school_str = f" - {acc['school']}" if acc["school"] else ""
                    options.append(f"账号: {acc['name']}{school_str}")

                options.extend(["扫码登录新账号", "Cookie 登录新账号"])
                if accounts:
                    options.append("删除已保存账号")
                options.append("退出")

                # 选择登录方式
                login_method = await ui.select_one("请选择登录方式", options)

                if login_method == "退出":
                    return
                elif login_method == "扫码登录新账号":
                    await platform.login()
                    if platform.user and platform.current_cookies:
                        credential_store.save(platform_id, platform.user, platform.current_cookies)
                    break
                elif login_method == "Cookie 登录新账号":
                    await _login_with_cookie_input(platform, ui)
                    if platform.user and platform.current_cookies:
                        credential_store.save(platform_id, platform.user, platform.current_cookies)
                    break
                elif login_method == "删除已保存账号":
                    del_options = []
                    for acc in accounts:
                        school_str = f" - {acc['school']}" if acc["school"] else ""
                        del_options.append(f"账号: {acc['name']}{school_str}")
                    del_options.append("返回")

                    del_choice = await ui.select_one("请选择要删除的账号", del_options)
                    if del_choice and del_choice != "返回":
                        idx = del_options.index(del_choice)
                        acc_to_del = accounts[idx]
                        credential_store.delete(platform_id, acc_to_del["user_id"])
                        print(f"✅ 已删除账号: {acc_to_del['name']}")
                    continue
                elif login_method and login_method != "返回":
                    # 选择了已保存的账号
                    idx = options.index(login_method)
                    acc = accounts[idx]
                    cookies = credential_store.get_cookies(platform_id, acc["user_id"])
                    if not cookies:
                        print("❌ 无法读取该账号的 Cookie")
                        continue

                    try:
                        await platform.login(cookies=cookies)
                        if platform.user and platform.current_cookies:
                            # 刷新 saved_at
                            credential_store.save(platform_id, platform.user, platform.current_cookies)
                        break
                    except AuthError:
                        print(f"\n❌ 账号 {acc['name']} 的登录凭证已失效，已自动清理，请重新登录")
                        credential_store.delete(platform_id, acc["user_id"])
                        continue

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
        raise AuthError("未输入 Cookie")

    cookies = BasePlatform.parse_cookie_string(cookie_raw)
    try:
        await platform.login(cookies=cookies)
    except AuthError as e:
        raise AuthError(f"Cookie 登录失败: {e}") from e


def main() -> None:
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        print("\n👋 已取消，程序退出")
        return


if __name__ == "__main__":
    main()
