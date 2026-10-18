"""集中配置管理。

仅保留与平台协议相关的常量；学习速率、心跳、阈值与并发已改为逐次调用的
显式策略参数（见 `core/video.py` 的 `VideoWatchOptions`），不再使用全局隐式常量。
"""

# 网络请求配置
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
# 统一默认请求头
DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Content-Type": "application/json",
}

# 作业提交并发上限：单次批量提交时的最大并发数。
MAX_WORKERS_HOMEWORK = 5
# 题目抓取并发上限：批量导出时并发拉取题目。
MAX_WORKERS_DOWNLOAD = 10
