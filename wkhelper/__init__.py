"""wkhelper 包初始化。"""

import logging

# 默认启用全局调试日志，便于排查平台接口和异步流程问题。
logging.getLogger().setLevel(logging.DEBUG)
logging.getLogger("wkhelper").setLevel(logging.DEBUG)
