import json

import qrcode
import requests
import websocket
from utils import log


def get_ykt_cookie() -> dict:
    """雨课堂扫码登录获取Cookie"""
    login_data = {}

    def on_message(ws, message):
        msg = json.loads(message)
        if "qrcode" in msg and msg["qrcode"]:
            qr = qrcode.QRCode()
            qr.add_data(msg["qrcode"])
            qr.print_ascii(invert=True)
            print("\n请使用雨课堂扫码登录...")

        if msg.get("op") == "loginsuccess":
            login_data.update(msg)
            ws.close()

    def on_open(ws):
        ws.send(
            json.dumps({
                "op": "requestlogin",
                "role": "web",
                "version": 1.4,
                "type": "qrcode",
            })
        )

    ws = websocket.WebSocketApp(
        "wss://www.yuketang.cn/wsapp/", on_message=on_message, on_open=on_open
    )
    ws.run_forever()

    if "Auth" not in login_data or "UserID" not in login_data:
        log("❌ 登录失败，未获取到登录信息")
        exit(1)

    response = requests.post(
        "https://www.yuketang.cn/pc/web_login",
        json={"Auth": login_data["Auth"], "UserID": str(login_data["UserID"])},
    )

    return {
        "csrftoken": response.cookies.get("csrftoken"),
        "sessionid": response.cookies.get("sessionid"),
    }


def init_session() -> requests.Session:
    log("🔐 正在获取雨课堂Cookie...")
    # cookies = get_ykt_cookie()
    # print(cookies)
    # cookies = {
    #     "csrftoken": "PEJRun5G257tuzixrAMJkUD68qd5QpKi",
    #     "sessionid": "hoaqtgfa3in0trymdm0mur4r0t8nonil",
    # }
    cookies = {
        "csrftoken": "NlEHVywHUKJxQyTFOjH14fNKn3NZqd7w",
        "sessionid": "6ykf8yzdmtdber887o3ccnnfvw4tx908",
    }

    if not cookies["csrftoken"] or not cookies["sessionid"]:
        log("❌ Cookie获取失败！")
        exit(1)

    log("✅ Cookie获取成功！")

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Content-Type": "application/json",
        "Referer": "https://www.yuketang.cn/",
        "X-CSRFToken": cookies["csrftoken"],
        "Xtbz": "ykt",
    })
    session.verify = False
    session.cookies.update(cookies)
    return session
