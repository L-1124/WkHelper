"""轻量 CDP WebSocket 异步客户端与事件分发器。"""

import asyncio
from typing import Any


class CdpClient:
    """轻量 CDP WebSocket 异步客户端与事件分发器。"""

    def __init__(self, ws: Any) -> None:
        self.ws = ws
        self._next_id = 1
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self.events: list[dict[str, Any]] = []
        self.alive = True
        self._pump_task = asyncio.create_task(self._pump())

    async def _pump(self) -> None:
        try:
            while True:
                msg = await self.ws.recv_json()
                if "id" in msg:
                    fut = self._pending.pop(msg["id"], None)
                    if fut and not fut.done():
                        if "error" in msg:
                            fut.set_exception(RuntimeError(str(msg["error"].get("message") or msg["error"])))
                        else:
                            fut.set_result(msg.get("result", {}))
                elif "method" in msg:
                    self.events.append(msg)
        except Exception as exc:
            # 遍历并中断所有尚未得到响应的 pending Future
            err = exc if isinstance(exc, Exception) else RuntimeError("CDP WebSocket 连接断开")
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(err)
            self._pending.clear()
        finally:
            self.alive = False
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(RuntimeError("CDP 客户端连接已关闭"))
            self._pending.clear()

    async def call(self, method: str, params: dict[str, Any] | None = None, timeout: float = 15.0) -> dict[str, Any]:
        if not self.alive:
            raise RuntimeError("CDP 客户端已关闭或断开连接")
        req_id = self._next_id
        self._next_id += 1
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        await self.ws.send_json({"id": req_id, "method": method, "params": params or {}})
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except TimeoutError:
            self._pending.pop(req_id, None)
            raise TimeoutError(f"CDP 方法调用超时 ({timeout}s): {method}") from None

    async def close(self) -> None:
        self._pump_task.cancel()
        try:
            await self.ws.close()
        except Exception:
            pass


__all__ = ["CdpClient"]
