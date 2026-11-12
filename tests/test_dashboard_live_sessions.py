"""Ordered replies and live pushes share one bounded output stream per tab."""
import asyncio

from ui.live_sessions import DashboardConnection


def test_private_replies_do_not_overtake_already_published_updates():
    async def exercise():
        class Socket:
            def __init__(self):
                self.messages = []

            async def send_json(self, message):
                self.messages.append(message)

        socket = Socket()
        connection = DashboardConnection(socket)
        try:
            connection.publish({"value": 1})
            await connection.send_json({"value": 2})
            await asyncio.sleep(0)
            assert socket.messages == [{"value": 1}, {"value": 2}]
        finally:
            await connection.close()
    asyncio.run(exercise())
