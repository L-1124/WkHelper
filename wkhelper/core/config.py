"""集中配置管理"""

# 网络请求配置
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
# 统一默认请求头
DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Content-Type": "application/json",
}

# 视频学习配置
# 学习速率倍数：心跳中上报的“学习帧”推进速度，越大理论学习越快。
LEARNING_RATE = 8
# 心跳间隔（秒）：每轮心跳发送与进度刷新的时间间隔。
HEARTBEAT_INTERVAL = 1.5
# 视频完成阈值：达到该进度视为完成（平台进度有时难到 100%）。
VIDEO_THRESHOLD = 0.95

# 并发配置
# 视频并发任务上限：批量学习视频时的最大并发数。
MAX_WORKERS_VIDEO = 5
# 作业并发任务上限：批量处理作业时的最大并发数。
MAX_WORKERS_HOMEWORK = 5
# 答案下载并发任务上限：批量抓取题目答案时的最大并发数。
MAX_WORKERS_DOWNLOAD = 10
