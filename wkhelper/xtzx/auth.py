import json
from io import BytesIO

import qrcode
import requests
import websocket
from PIL import Image
from pyzbar.pyzbar import decode

from ..utils import log


def get_cookie() -> dict:
    """扫码登录获取Cookie"""
    login_data = {}

    def on_message(ws, message):
        msg = json.loads(message)
        if "ticket" in msg and msg["ticket"]:
            resp = requests.get(msg["ticket"])
            img = Image.open(BytesIO(resp.content))

            url = decode(img)[0].data.decode("utf-8")
            qr = qrcode.QRCode()
            qr.add_data(url)
            qr.print_ascii(invert=True)
            print("\n请使用微信扫码登录...")

        if msg.get("op") == "loginsuccess":
            login_data.update(msg)
            ws.close()

    def on_open(ws):
        ws.send(
            json.dumps({
                "op": "requestlogin",
                "role": "web",
                "version": "1.4",
                "purpose": "login",
                "xtbz": "xt",
                "x-client": "web",
            })
        )

    ws = websocket.WebSocketApp(
        "wss://www.xuetangx.com/wsapp/", on_message=on_message, on_open=on_open
    )
    ws.run_forever()

    response = requests.post(
        "https://www.xuetangx.com/api/v1/u/login/wx/",
        json={
            "s_s": login_data["token"],
            "preset_properties": {
                "$timezone_offset": -480,
                "$screen_height": 1067,
                "$screen_width": 1707,
                "$lib": "js",
                "$lib_version": "1.19.14",
                "$latest_traffic_source_type": "直接流量",
                "$latest_search_keyword": "未取到值_直接打开",
                "$latest_referrer": "",
                "$is_first_day": False,
                "$referrer": "https://www.xuetangx.com/",
                "$referrer_host": "www.xuetangx.com",
                "$url": "https://www.xuetangx.com/",
                "$url_path": "/",
                "$title": "学堂在线 - 精品在线课程学习平台",
                "_distinct_id": "19a16647ffb7cf-0590d22341cefa4-4c657b58-1821369-19a16647ffc129c",
            },
            "page_name": "首页",
        },
    )

    return {
        "csrftoken": response.cookies.get("csrftoken", ""),
        "sessionid": response.cookies.get("sessionid", ""),
    }


def init_session() -> requests.Session:
    log("🔐 正在获取学堂在线Cookie...")
    cookies = get_cookie()

    if not cookies["csrftoken"] or not cookies["sessionid"]:
        log("❌ Cookie获取失败！")
        exit(1)

    log("✅ Cookie获取成功！")

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Content-Type": "application/json",
        "X-CSRFToken": cookies["csrftoken"],
        "Xtbz": "xt",
    })
    session.cookies.update(cookies)
    return session
