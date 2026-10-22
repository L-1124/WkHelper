"""浏览器 Cookie 输入解析。"""


def parse_cookie_string(raw: str) -> dict[str, str]:
    """解析浏览器 cookie 字符串为字典。"""
    result: dict[str, str] = {}
    for part in raw.split(";"):
        part = part.strip()
        if "=" in part:
            key, value = part.split("=", 1)
            result[key.strip()] = value.strip()
    return result
