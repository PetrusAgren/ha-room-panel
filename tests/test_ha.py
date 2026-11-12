import asyncio
import unittest

from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestServer

from room_panel.roompanel.ha import HomeAssistant


class SnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def test_snapshot_normalizes_registry_metadata_and_filters_hidden(self):
        ha = HomeAssistant(None, "http://supervisor", "token")
        responses = {
            "get_states": [
                {"entity_id": "light.kitchen", "state": "on", "attributes": {"friendly_name": "Kitchen"}},
                {"entity_id": "sensor.secret", "state": "3", "attributes": {}},
            ],
            "get_config": {"time_zone": "Europe/Paris"},
            "config/area_registry/list": [{"area_id": "kitchen", "name": "Kitchen"}],
            "config/label_registry/list": [{"label_id": "favorite", "name": "Favorite"}],
            "config/entity_registry/list": [
                {"id": "registry-light-1", "entity_id": "light.kitchen", "area_id": "kitchen",
                 "labels": ["favorite"], "name": "Ceiling"},
                {"id": "registry-hidden", "entity_id": "sensor.secret", "hidden_by": "user"},
            ],
            "config/device_registry/list": [],
        }

        async def request(command):
            return responses[command["type"]]

        ha._request = request
        await ha._load_snapshot()
        self.assertEqual(ha.time_zone, "Europe/Paris")
        self.assertEqual(ha.areas, [{"id": "kitchen", "name": "Kitchen"}])
        self.assertEqual(ha.labels, [{"id": "favorite", "name": "Favorite"}])
        self.assertEqual(len(ha.entities), 1)
        entity = ha.get_entity("registry-light-1")
        self.assertEqual(entity["entity_id"], "light.kitchen")
        self.assertEqual(entity["area_id"], "kitchen")
        self.assertEqual(entity["labels"], ["Favorite"])
        self.assertEqual(entity["domain"], "light")
        self.assertEqual(ha.get_entity("light.kitchen"), entity)
        self.assertIsNone(ha.get_entity("sensor.secret"))

    async def test_area_and_label_fallback_to_device_registry(self):
        ha = HomeAssistant(None, "http://supervisor", "token")
        responses = {
            "get_states": [{"entity_id": "switch.plug", "state": "off", "attributes": {}}],
            "get_config": {}, "config/area_registry/list": [], "config/label_registry/list": [],
            "config/entity_registry/list": [{"id": "e1", "entity_id": "switch.plug", "device_id": "d1"}],
            "config/device_registry/list": [{"id": "d1", "area_id": "office", "labels": ["work"]}],
        }

        async def request(command):
            return responses[command["type"]]

        ha._request = request
        await ha._load_snapshot()
        self.assertEqual(ha.entities[0]["area_id"], "office")
        self.assertEqual(ha.entities[0]["labels"], ["work"])


class WebSocketTests(unittest.IsolatedAsyncioTestCase):
    async def test_subscription_event_with_id_and_service_command(self):
        sockets = []
        commands = []

        async def websocket(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            sockets.append(ws)
            await ws.send_json({"type": "auth_required"})
            auth = await ws.receive_json()
            self.assertEqual(auth["type"], "auth")
            await ws.send_json({"type": "auth_ok"})
            async for message in ws:
                if message.type != WSMsgType.TEXT:
                    break
                cmd = message.json()
                commands.append(cmd)
                typ = cmd["type"]
                result = {
                    "get_states": [{"entity_id": "light.test", "state": "off", "attributes": {}}],
                    "get_config": {"time_zone": "UTC"},
                    "config/area_registry/list": [], "config/label_registry/list": [],
                    "config/entity_registry/list": [{"id": "stable-id", "entity_id": "light.test"}],
                    "config/device_registry/list": [],
                }.get(typ, {})
                await ws.send_json({"id": cmd["id"], "type": "result", "success": True, "result": result})
            return ws

        app = web.Application()
        app.router.add_get("/api/websocket", websocket)
        server = TestServer(app)
        await server.start_server()
        session = __import__("aiohttp").ClientSession()
        changed = asyncio.Event()
        ha = HomeAssistant(session, str(server.make_url("/api/websocket")).replace("http://", "ws://"), "secret",
                           on_change=changed.set)
        connect = asyncio.create_task(ha._connect_once())
        try:
            for _ in range(100):
                if ha.connected:
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(ha.connected)
            self.assertEqual(ha.get_entity("stable-id")["state"], "off")
            # Real HA event frames include the subscription ID; they are not replies.
            await sockets[0].send_json({"id": 6, "type": "event", "event": {
                "event_type": "state_changed", "data": {"entity_id": "light.test",
                    "old_state": {"state": "off"},
                    "new_state": {"state": "on", "attributes": {}}}}})
            for _ in range(100):
                if ha.get_entity("stable-id")["state"] == "on":
                    break
                await asyncio.sleep(0.01)
            self.assertEqual(ha.get_entity("stable-id")["state"], "on")
            await ha.service("light", "turn_on", "light.test", {"brightness": 42})
            service_cmd = next(c for c in commands if c["type"] == "call_service")
            self.assertEqual(service_cmd["service_data"], {"brightness": 42})
        finally:
            connect.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await connect
            await ha._disconnect()
            await session.close()
            await server.close()


if __name__ == "__main__":
    unittest.main()
