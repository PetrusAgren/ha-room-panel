"""Exercise room administration and tablet authorization through real HTTP requests."""

import tempfile
import unittest
from pathlib import Path

from aiohttp import CookieJar
from aiohttp.test_utils import TestClient, TestServer

from roompanel.app import RoomPanel
from roompanel.store import Store


class FakeHA:
    connected = True
    time_zone = "Europe/Stockholm"
    areas = [{"id": "living", "name": "Living Room"}]
    labels = [{"id": "visible", "name": "Panel"}]

    def __init__(self):
        self.calls = []
        self.entities = [
            {"id": "light-1", "entity_id": "light.lamp", "area_id": "living",
             "labels": ["visible"], "name": "Lamp", "domain": "light", "state": "on",
             "attributes": {"brightness": 100, "supported_color_modes": ["brightness"]}},
            {"id": "light-2", "entity_id": "light.office", "area_id": "office",
             "labels": ["visible"], "name": "Office lamp", "domain": "light", "state": "on",
             "attributes": {"brightness": 120, "supported_color_modes": ["brightness"]}},
            {"id": "script-1", "entity_id": "script.fixed_scene", "area_id": None,
             "labels": [], "name": "Fixed action", "domain": "script", "state": "off", "attributes": {}},
        ]

    def get_entity(self, ref):
        return next((entity for entity in self.entities if ref in (entity["id"], entity["entity_id"])), None)

    async def service(self, domain, service, entity_id, data=None, return_response=False):
        self.calls.append((domain, service, entity_id, data, return_response))


class RoomPanelHTTPTests(unittest.IsolatedAsyncioTestCase):
    headers = {"X-Room-Panel": "1"}

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "store.sqlite3")
        self.ha = FakeHA()
        self.panel = RoomPanel(self.store, self.ha, None, dev=True)
        self.admin = TestClient(TestServer(self.panel.admin_application()))
        self.device = TestClient(TestServer(self.panel.panel_application()), cookie_jar=CookieJar(unsafe=True))
        await self.admin.start_server()
        await self.device.start_server()
        self.store.save_settings({"base_url": str(self.device.make_url("/")).rstrip("/")})

    async def asyncTearDown(self):
        await self.admin.close()
        await self.device.close()
        self.store.close()
        self.temp.cleanup()

    async def checked(self, response, expected=200):
        self.assertEqual(response.status, expected, await response.text())
        return await response.json()

    async def create_profile(self, **overrides):
        payload = {"name": "Living", "area_id": "living", "mode": "manual", "entities": ["light-1"],
                   "presets": [{"id": "evening", "name": "Evening", "script": "script-1", "transition": 2}]}
        response = await self.admin.post("/api/profiles", json={**payload, **overrides}, headers=self.headers)
        return await self.checked(response, 201)

    async def create_panel(self, profile_id, kind="control"):
        response = await self.admin.post("/api/panels", headers=self.headers, json={
            "name": "Tablet", "profile_id": profile_id, "type": kind,
            "sections": ["lights", "presets"],
        })
        return (await self.checked(response, 201))["id"]

    async def pair(self, panel_id):
        url = f"/api/panels/{panel_id}/pair"
        pairing = await self.checked(await self.admin.post(url, headers=self.headers))
        await self.checked(await self.device.post(url, json={"token": pairing["token"]}, headers=self.headers))
        return pairing

    async def test_pairing_is_single_use_and_session_is_scoped_and_revocable(self):
        profile = await self.create_profile(mode="auto", label_id="visible", entities=[])
        panel_id = await self.create_panel(profile["id"])
        other_id = await self.create_panel(profile["id"])
        pairing = await self.pair(panel_id)
        state_url = f"/api/panels/{panel_id}/state"
        state = await self.checked(await self.device.get(state_url))
        self.assertEqual([item["id"] for item in state["entities"]], ["light-1"])
        self.assertEqual((await self.device.get(f"/api/panels/{other_id}/state")).status, 401)
        replay = await self.device.post(f"/api/panels/{panel_id}/pair",
                                        json={"token": pairing["token"]}, headers=self.headers)
        self.assertEqual(replay.status, 401)
        await self.checked(await self.admin.post(f"/api/panels/{panel_id}/revoke", headers=self.headers))
        self.assertEqual((await self.device.get(state_url)).status, 401)

    async def test_door_panel_only_sees_availability(self):
        profile = await self.create_profile()
        panel_id = await self.create_panel(profile["id"], "door")
        await self.pair(panel_id)
        base = f"/api/panels/{panel_id}"
        state = await self.checked(await self.device.get(f"{base}/state"))
        self.assertEqual(state["entities"], [])
        self.assertEqual(state["presets"], [])
        self.assertEqual(state["calendar"]["status"], "unknown")
        response = await self.device.post(f"{base}/command", headers=self.headers,
                                          json={"entity": "light-1", "action": "power", "value": True})
        self.assertEqual(response.status, 401)
        self.assertEqual(self.ha.calls, [])

    async def test_settings_require_write_header_and_do_not_return_secret(self):
        self.assertEqual((await self.admin.put("/api/settings", json={"pin": "1234"})).status, 403)
        data = await self.checked(await self.admin.put("/api/settings", headers=self.headers, json={
            "pin": "1234", "outlook": {"client_secret": "example-secret"},
        }))
        self.assertTrue(data["pin_set"])
        self.assertTrue(data["outlook"]["secret_set"])
        self.assertNotIn("pin_hash", data)
        self.assertNotIn("client_secret", data["outlook"])
        self.assertNotEqual(self.store.settings()["pin_hash"], "1234")

    async def test_preset_capture_conflict_save_restore_and_apply(self):
        await self.checked(await self.admin.put("/api/settings", json={"pin": "1234"}, headers=self.headers))
        profile = await self.create_profile()
        panel_id = await self.create_panel(profile["id"])
        await self.pair(panel_id)
        base = f"/api/panels/{panel_id}"

        async def post(suffix, value, expected=200):
            return await self.checked(await self.device.post(base + suffix, json=value, headers=self.headers), expected)

        await post("/unlock", {"pin": "0000"}, 401)
        unlock = await post("/unlock", {"pin": "1234"})
        preview = await post("/presets/evening/preview", unlock)
        self.assertIn("Fixed action", preview["fixed"])
        self.ha.entities[0]["attributes"]["brightness"] = 110
        await post("/presets/evening/save", preview, 409)
        preview = await post("/presets/evening/preview", unlock)
        saved = await post("/presets/evening/save", preview)
        self.assertEqual(saved["revision"], 1)
        await post("/presets/evening/apply", {})
        self.assertEqual(self.ha.calls[-2][3], {"brightness": 110, "transition": 2})
        self.assertEqual(self.ha.calls[-1][:3], ("script", "turn_on", "script.fixed_scene"))
        self.ha.entities[0]["attributes"]["brightness"] = 180
        unlock = await post("/unlock", {"pin": "1234"})
        preview = await post("/presets/evening/preview", unlock)
        await post("/presets/evening/save", preview)
        restore = f"/api/profiles/{profile['id']}/presets/evening/restore"
        await self.checked(await self.admin.post(restore, headers=self.headers))
        await post("/presets/evening/apply", {})
        self.assertEqual(self.ha.calls[-2][3]["brightness"], 110)
