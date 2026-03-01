"""Platform 域综合测试：异步契约与适配逻辑。"""

import pytest


@pytest.mark.asyncio
async def test_platform_async_call_chain(fake_platform):
    """验证平台实例的异步方法可以被正常 await。"""
    user = await fake_platform.login()
    assert user.name == "tester"

    courses = await fake_platform.get_courses()
    assert len(courses) > 0
