"""扫码登录辅助工具：临时二维码生成与本地轻量 Web 展示 (带状态自动响应)。"""

import asyncio
import json
import secrets
import tempfile
import webbrowser
from pathlib import Path
from typing import Any

import qrcode
from qrcode.image.svg import SvgImage

from wkhelper.core.events import emit_event


class QrCodeSession:
    """扫码登录会话管理：包含临时文件生成与本地 HTTP 服务。"""

    def __init__(
        self,
        display: str = "image",
        open_browser: bool = False,
        platform_name: str = "雨课堂",
        tip: str | None = None,
    ) -> None:
        self.display = display
        self.open_browser = open_browser
        self.platform_name = platform_name
        self.tip = tip or (
            "请使用 微信 扫描二维码完成学堂在线登录" if platform_name == "学堂在线" else "请使用 微信 或 雨课堂 App 扫描二维码完成登录"
        )
        self.image_path: Path | None = None
        self.token = secrets.token_urlsafe(16)
        self.server: asyncio.Server | None = None
        self.port: int | None = None
        self.status = "waiting"  # "waiting", "success", "expired"
        self.user_name = ""

    def mark_success(self, name: str = "") -> None:
        """标记登录成功并设置用户名。"""
        self.status = "success"
        self.user_name = name

    def create_image(self, data_or_bytes: str | bytes) -> Path:
        """根据数据字符串或图片字节生成本地临时文件"""
        if self.image_path and self.image_path.exists():
            try:
                self.image_path.unlink()
            except OSError:
                pass

        if isinstance(data_or_bytes, bytes):
            temp_file = tempfile.NamedTemporaryFile(prefix="wk_qr_", suffix=".png", delete=False)
            temp_file.close()
            temp_path = Path(temp_file.name)
            temp_path.write_bytes(data_or_bytes)
        else:
            temp_file = tempfile.NamedTemporaryFile(prefix="wk_qr_", suffix=".svg", delete=False)
            temp_file.close()
            temp_path = Path(temp_file.name)
            qr = qrcode.QRCode(border=1, image_factory=SvgImage)
            qr.add_data(data_or_bytes)
            qr.make(fit=True)
            img = qr.make_image()
            with open(temp_path, "wb") as f:
                img.save(f)

        self.image_path = temp_path
        return temp_path

    @staticmethod
    async def _read_headers(reader: asyncio.StreamReader) -> dict[str, str]:
        """读取 HTTP 请求头并统一为小写键。"""
        headers: dict[str, str] = {}
        while True:
            raw = await reader.readline()
            if not raw or raw in (b"\r\n", b"\n"):
                break
            name, _, value = raw.decode("utf-8", errors="ignore").partition(":")
            if name.strip():
                headers[name.strip().lower()] = value.strip()
        return headers

    @staticmethod
    async def _reject(writer: asyncio.StreamWriter, status_line: str) -> None:
        """返回统一的最小化拒绝响应并关闭连接。"""
        body = b"Forbidden" if "403" in status_line else b""
        headers = f"HTTP/1.1 {status_line}\r\nContent-Type: text/plain\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n"
        writer.write(headers.encode("utf-8") + body)
        await writer.drain()
        writer.close()

    async def _handle_request(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await reader.readline()
            if not line:
                writer.close()
                return
            request_line = line.decode("utf-8", errors="ignore").strip()
            parts = request_line.split()
            if len(parts) < 2 or parts[0] != "GET":
                await self._reject(writer, "405 Method Not Allowed")
                return

            headers = await self._read_headers(reader)
            loopback_hosts = (f"127.0.0.1:{self.port}", f"localhost:{self.port}")
            if headers.get("host", "") not in loopback_hosts:
                await self._reject(writer, "403 Forbidden")
                return
            origin = headers.get("origin")
            if origin is not None and origin not in (f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}"):
                await self._reject(writer, "403 Forbidden")
                return

            path = parts[1]
            if f"token={self.token}" not in path:
                await self._reject(writer, "403 Forbidden")
                return

            if path.startswith("/status"):
                data = json.dumps({"status": self.status, "name": self.user_name})
                body = data.encode("utf-8")
                headers = (
                    "HTTP/1.1 200 OK\r\n"
                    "Content-Type: application/json\r\n"
                    f"Content-Length: {len(body)}\r\n"
                    "Cache-Control: no-store\r\n"
                    "Connection: close\r\n\r\n"
                )
                writer.write(headers.encode("utf-8") + body)
                await writer.drain()
                writer.close()
                return

            if (
                (path.startswith("/qr") or path.startswith("/qr.svg") or path.startswith("/qr.png"))
                and self.image_path
                and self.image_path.exists()
            ):
                content = self.image_path.read_bytes()
                mime = "image/svg+xml" if self.image_path.suffix == ".svg" else "image/png"
                headers = (
                    "HTTP/1.1 200 OK\r\n"
                    f"Content-Type: {mime}\r\n"
                    f"Content-Length: {len(content)}\r\n"
                    "Cache-Control: no-store\r\n"
                    "Connection: close\r\n\r\n"
                )
                writer.write(headers.encode("utf-8") + content)
                await writer.drain()
                writer.close()
                return

            html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>WkHelper 扫码登录 - {self.platform_name}</title>
<style>
body {{ font-family: -apple-system, sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }}
.card {{ background: #fff; padding: 2.5rem 2rem; border-radius: 16px; text-align: center; max-width: 360px; width: 100%; }}
h2 {{ margin-top: 0; font-size: 1.35rem; }}
p {{ color: #64748b; font-size: 0.9rem; margin-bottom: 1.5rem; }}
img {{ width: 230px; height: 230px; border-radius: 10px; border: 1px solid #e2e8f0; }}
.success-box {{ display: none; padding: 1.5rem 0; }}
.success-name {{ font-size: 1.15rem; font-weight: 600; color: #0f172a; margin-bottom: 0.5rem; }}
.success-tip {{ font-size: 0.875rem; color: #64748b; }}
</style>
</head>
<body>
<div class="card" id="card">
  <div id="qr-box">
    <h2>扫码登录（{self.platform_name}）</h2>
    <p>{self.tip}</p>
    <img src="/qr?token={self.token}" alt="登录二维码" id="qr-img" />
  </div>
  <div class="success-box" id="success-box">
    <div class="success-name" id="user-display">登录成功</div>
    <div class="success-tip">已成功接入 WkHelper，请返回终端继续操作</div>
  </div>
</div>
<script>
const token = "{self.token}";
let pollTimer = setInterval(async () => {{
  try {{
    const res = await fetch("/status?token=" + token);
    if (!res.ok) return;
    const data = await res.json();
    if (data.status === "success") {{
      clearInterval(pollTimer);
      document.getElementById("qr-box").style.display = "none";
      const sBox = document.getElementById("success-box");
      sBox.style.display = "block";
      if (data.name) {{
        document.getElementById("user-display").innerText = "欢迎，" + data.name;
      }}
    }}
  }} catch (e) {{}}
}}, 800);
</script>
</body>
</html>
"""
            body = html.encode("utf-8")
            headers = (
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: text/html; charset=utf-8\r\n"
                f"Content-Length: {len(body)}\r\n"
                "Cache-Control: no-store\r\n"
                "Connection: close\r\n\r\n"
            )
            writer.write(headers.encode("utf-8") + body)
            await writer.drain()
            writer.close()
        except Exception:
            try:
                writer.close()
            except Exception:
                pass

    async def start(self) -> None:
        """若需要开启本地浏览器展示，启动 loopback HTTP 服务器。"""
        if self.display == "browser":
            self.server = await asyncio.start_server(self._handle_request, "127.0.0.1", 0)
            sockets = self.server.sockets
            if sockets:
                self.port = sockets[0].getsockname()[1]

    def emit_challenge(self, expires_in: float) -> dict[str, Any]:
        """向 stderr 发送 challenge 事件。"""
        local_url = None
        if self.port:
            local_url = f"http://127.0.0.1:{self.port}/?token={self.token}"
            if self.open_browser:
                try:
                    webbrowser.open(local_url)
                except Exception:
                    emit_event("auth.browser_open_failed", {"url": local_url})

        payload = {
            "image_path": str(self.image_path) if self.image_path else None,
            "local_url": local_url,
            "display": self.display,
            "expires_in": expires_in,
        }
        emit_event("auth.challenge", payload)
        return payload

    async def close(self) -> None:
        """关闭服务并清理临时文件。"""
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            self.server = None

        if self.image_path and self.image_path.exists():
            try:
                self.image_path.unlink()
            except OSError:
                pass
            self.image_path = None
