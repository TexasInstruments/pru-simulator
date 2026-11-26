"""Live browser views of the dashboard's single simulator instance."""
import asyncio
from contextlib import suppress

from starlette.websockets import WebSocketDisconnect


class DashboardConnection:
    def __init__(self, websocket):
        self.websocket = websocket
        self.capture = False
        self.closed = False
        self.pending = asyncio.Queue(maxsize=64)
        self.writer = asyncio.create_task(self._write_updates())

    def __getattr__(self, name):
        return getattr(self.websocket, name)

    async def send_json(self, message):
        await self._send(message, text=False)

    async def send_text(self, message):
        await self._send(message, text=True)

    async def _send(self, message, text):
        done = asyncio.get_running_loop().create_future()
        self._enqueue(message, text, done)
        await done

    def publish(self, message):
        if not self.closed:
            self._enqueue(message, False, None)

    def _enqueue(self, message, text, done):
        if self.closed:
            raise WebSocketDisconnect(1013)
        try:
            self.pending.put_nowait((message, text, done))
        except asyncio.QueueFull:
            # A slow view reconnects rather than silently losing signal samples
            # or holding up the simulator's controlling tab.
            self.closed = True
            self.writer.cancel()
            asyncio.create_task(self.websocket.close(code=1013))
            if done is not None:
                done.set_exception(WebSocketDisconnect(1013))

    async def _write_updates(self):
        done = None
        try:
            while True:
                message, text, done = await self.pending.get()
                send = self.websocket.send_text if text else self.websocket.send_json
                await asyncio.wait_for(send(message), timeout=2)
                if done is not None and not done.done():
                    done.set_result(None)
                done = None
        except (asyncio.TimeoutError, TimeoutError, WebSocketDisconnect, RuntimeError):
            with suppress(WebSocketDisconnect, RuntimeError):
                await self.websocket.close(code=1013)
        finally:
            self.closed = True
            if done is not None and not done.done():
                done.set_exception(WebSocketDisconnect(1013))
            while not self.pending.empty():
                _, _, pending = self.pending.get_nowait()
                if pending is not None and not pending.done():
                    pending.set_exception(WebSocketDisconnect(1013))

    async def close(self):
        self.writer.cancel()
        with suppress(asyncio.CancelledError):
            await self.writer
