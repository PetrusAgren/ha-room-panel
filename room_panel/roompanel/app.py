"""Separate Home Assistant Ingress and local tablet HTTP applications."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic

from aiohttp import web

from . import control
from .calendar import CalendarService
from .configuration import (
    DEFAULTS, ID, PIN, normalize_panel, normalize_profile, normalize_settings,
    public_preset, public_profile, public_settings,
)
from .control import Conflict, Denied, Invalid, Limited, Missing
from .ha import HAError, HAUnavailable
from .store import hash_pin, verify_pin

STATIC = Path(__file__).with_name("static")
LOG = logging.getLogger(__name__)
INGRESS_PEER = "172.30.32.2"
COOKIE = "room_panel"


def make_id():
    return secrets.token_hex(12)


def json_response(data, *, status=200):
    return web.json_response(data, status=status, headers={"Cache-Control": "no-store"})


async def body(request):
    try:
        value = await request.json()
    except (ValueError, UnicodeError) as error:
        raise Invalid("Invalid JSON request.") from error
    if not isinstance(value, dict):
        raise Invalid("Expected a JSON object.")
    return value


@web.middleware
async def errors(request, handler):
    try:
        return await handler(request)
    except Invalid as error:
        return json_response({"error": str(error)}, status=error.status)
    except HAUnavailable:
        return json_response({"error": "Home Assistant is disconnected."}, status=503)
    except HAError:
        return json_response({"error": "Home Assistant could not complete the action."}, status=502)


def asset(filename, *, device=False):
    headers = {
        "Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    }
    if filename.endswith(".html"):
        ancestors = "'none'" if device else "'self'"
        headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
            f"base-uri 'none'; form-action 'self'; frame-ancestors {ancestors}"
        )

    async def handle(_):
        return web.FileResponse(STATIC / filename, headers=headers)

    return handle


class RoomPanel:
    def __init__(self, store, ha, session, *, dev=False):
        self.store = store
        self.ha = ha
        self.dev = dev
        self.calendar = CalendarService(session, ha, self.settings)
        self.changed_event = asyncio.Event()
        self.settings_lock = asyncio.Lock()
        self.unlocks = {}
        self.attempts = {}
        self.previews = {}
        self.update_listener = None

    def settings(self):
        return {**DEFAULTS, **self.store.settings()}

    def changed(self):
        self.changed_event.set()
        self.changed_event = asyncio.Event()

    def get(self, kind, ident):
        if not isinstance(ident, str) or not ID.fullmatch(ident):
            raise Missing("Not found.")
        value = self.store.get(kind, ident)
        if not value:
            raise Missing("Not found.")
        return value

    def preset(self, profile, ident):
        for preset in profile["presets"]:
            if preset["id"] == ident:
                return preset
        raise Missing("Preset not found.")

    def panel_view(self, panel):
        status = self.store.session_status(panel["id"])
        seen = status["last_seen"]
        return {**panel, **status, "last_seen": datetime.fromtimestamp(seen, timezone.utc).isoformat() if seen else None}

    def clear_device_grants(self, panel_id):
        self.attempts.pop(panel_id, None)
        for collection in (self.unlocks, self.previews):
            for key in [key for key in collection if key[0] == panel_id]:
                del collection[key]

    def admin_application(self):
        @web.middleware
        async def ingress_only(request, handler):
            if not self.dev and (request.remote != INGRESS_PEER or not request.headers.get("X-Remote-User-Id")):
                raise web.HTTPForbidden()
            if request.method not in {"GET", "HEAD", "OPTIONS"} and request.headers.get("X-Room-Panel") != "1":
                raise web.HTTPForbidden()
            return await handler(request)

        app = web.Application(middlewares=[errors, ingress_only], client_max_size=1_500_000)
        app.router.add_get("/", asset("admin.html"))
        app.router.add_get("/admin.css", asset("admin.css"))
        app.router.add_get("/admin.js", asset("admin.js"))
        app.router.add_get("/api/config", self.admin_config)
        app.router.add_post("/api/profiles", self.save_profile)
        app.router.add_put("/api/profiles/{id}", self.save_profile)
        app.router.add_delete("/api/profiles/{id}", self.delete_profile)
        app.router.add_post("/api/profiles/{id}/presets/{preset}/restore", self.restore_preset)
        app.router.add_post("/api/panels", self.save_panel)
        app.router.add_put("/api/panels/{id}", self.save_panel)
        app.router.add_delete("/api/panels/{id}", self.delete_panel)
        app.router.add_post("/api/panels/{id}/pair", self.new_pairing)
        app.router.add_post("/api/panels/{id}/revoke", self.revoke_panel)
        app.router.add_put("/api/settings", self.save_settings)
        return app

    def panel_application(self):
        @web.middleware
        async def same_origin(request, handler):
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                origin = request.headers.get("Origin")
                allowed = self.settings()["base_url"]
                if request.headers.get("X-Room-Panel") != "1" or (not self.dev and (not allowed or origin != allowed)):
                    raise web.HTTPForbidden()
            return await handler(request)

        app = web.Application(middlewares=[errors, same_origin], client_max_size=16_384)
        app.router.add_get("/panel/{id}", self.show_panel)
        app.router.add_get("/static/panel.css", asset("panel.css", device=True))
        app.router.add_get("/static/panel.js", asset("panel.js", device=True))
        app.router.add_get("/api/panels/{id}/state", self.panel_state)
        app.router.add_get("/api/panels/{id}/events", self.events)
        app.router.add_post("/api/panels/{id}/pair", self.redeem_pairing)
        app.router.add_post("/api/panels/{id}/command", self.command)
        app.router.add_post("/api/panels/{id}/unlock", self.unlock)
        app.router.add_post("/api/panels/{id}/presets/{preset}/preview", self.preview_preset)
        app.router.add_post("/api/panels/{id}/presets/{preset}/save", self.save_preset)
        app.router.add_post("/api/panels/{id}/presets/{preset}/apply", self.apply_preset)
        return app

    async def admin_config(self, _):
        return json_response({
            "connected": self.ha.connected, "areas": self.ha.areas, "labels": self.ha.labels,
            "entities": [control.public_entity(entity) | {"area_id": entity["area_id"], "labels": entity["labels"]}
                         for entity in self.ha.entities if entity["domain"] in (*control.SECTIONS, "scene", "calendar")],
            "profiles": [public_profile(profile) for profile in self.store.list("profiles")],
            "panels": [self.panel_view(panel) for panel in self.store.list("panels")],
            "settings": public_settings(self.settings()),
        })

    async def save_profile(self, request):
        value = await body(request)
        ident = request.match_info.get("id")
        old = self.get("profiles", ident) if ident else {}
        profile = normalize_profile(self.ha, value, old, ident or make_id(), make_id)
        self.store.put("profiles", profile)
        self.changed()
        return json_response(public_profile(profile), status=200 if ident else 201)

    async def delete_profile(self, request):
        profile = self.get("profiles", request.match_info["id"])
        if any(panel["profile_id"] == profile["id"] for panel in self.store.list("panels")):
            raise Conflict("Remove or move the profile's panels before deleting it.")
        self.store.delete("profiles", profile["id"])
        self.changed()
        return json_response({"ok": True})

    async def save_panel(self, request):
        value = await body(request)
        ident = request.match_info.get("id")
        old = self.get("panels", ident) if ident else {}
        panel = normalize_panel(self.store, value, ident or make_id())
        if old and (old["profile_id"], old["type"]) != (panel["profile_id"], panel["type"]):
            self.store.revoke(ident)
            self.clear_device_grants(ident)
        self.store.put("panels", panel)
        self.changed()
        return json_response(self.panel_view(panel), status=200 if ident else 201)

    async def delete_panel(self, request):
        panel = self.get("panels", request.match_info["id"])
        self.store.revoke(panel["id"])
        self.clear_device_grants(panel["id"])
        self.store.delete("panels", panel["id"])
        self.changed()
        return json_response({"ok": True})

    async def new_pairing(self, request):
        panel = self.get("panels", request.match_info["id"])
        settings = self.settings()
        origin = settings["base_url"]
        if not origin or (not self.dev and not (settings["tls_cert"] and settings["tls_key"])):
            raise Conflict("Configure the panel HTTPS URL and certificate in Settings before pairing.")
        token = self.store.create_pairing(panel["id"])
        return json_response({"url": f"{origin}/panel/{panel['id']}#token={token}", "token": token})

    async def revoke_panel(self, request):
        panel = self.get("panels", request.match_info["id"])
        self.store.revoke(panel["id"])
        self.clear_device_grants(panel["id"])
        self.changed()
        return json_response({"ok": True})

    async def save_settings(self, request):
        value = await body(request)
        async with self.settings_lock:
            old = self.settings()
            settings = normalize_settings(value, old, self.dev)
            pin = value.get("pin")
            if pin:
                if not isinstance(pin, str) or not PIN.fullmatch(pin):
                    raise Invalid("The preset PIN must be exactly four digits.")
                settings["pin_hash"] = await asyncio.to_thread(hash_pin, pin)
            if self.update_listener:
                await self.update_listener(settings)
            self.store.save_settings(settings)
            if settings.get("pin_hash") != old.get("pin_hash"):
                self.unlocks.clear()
                self.previews.clear()
                self.attempts.clear()
        self.changed()
        return json_response(public_settings(settings))

    async def show_panel(self, request):
        self.get("panels", request.match_info["id"])
        return await asset("panel.html", device=True)(request)

    async def redeem_pairing(self, request):
        value = await body(request)
        panel = self.get("panels", request.match_info["id"])
        token = value.get("token")
        if not isinstance(token, str) or len(token) > 100:
            raise Denied("This pairing link is invalid or expired.")
        session = self.store.redeem_pairing(panel["id"], token)
        if not session:
            raise Denied("This pairing link is invalid or expired.")
        self.clear_device_grants(panel["id"])
        self.changed()
        response = json_response({"ok": True})
        response.set_cookie(COOKIE, session, httponly=True, secure=not self.dev, samesite="Strict",
                            path=f"/api/panels/{panel['id']}/", max_age=365 * 86400)
        return response

    def authorize(self, request, section=None):
        panel = self.get("panels", request.match_info["id"])
        cookie = request.cookies.get(COOKIE, "")
        if not cookie or not self.store.session(panel["id"], cookie):
            raise Denied("Pair this panel to continue.")
        if section and (panel["type"] != "control" or section not in panel["sections"]):
            raise Denied("This panel cannot use that feature.")
        return panel, self.get("profiles", panel["profile_id"]), cookie

    async def panel_state(self, request):
        panel, profile, _ = self.authorize(request)
        is_control = panel["type"] == "control"
        calendar = None
        if not is_control or "calendar" in panel["sections"]:
            calendar = await self.calendar.status(profile)
            current_panel, current_profile, _ = self.authorize(request)
            if current_panel != panel or current_profile["calendar"] != profile["calendar"]:
                raise Conflict("The panel configuration changed. Refresh and try again.")
            profile = current_profile
        settings = self.settings()
        return json_response({
            "panel": {key: panel[key] for key in ("id", "name", "type", "sections")},
            "room": {"name": profile["name"]}, "connected": self.ha.connected,
            "branding": {key: settings[key] for key in ("name", "primary", "background", "logo")},
            "entities": [control.public_entity(entity) for entity in control.profile_entities(self.ha, profile)
                         if control.SECTIONS[entity["domain"]] in panel["sections"]] if is_control else [],
            "presets": [public_preset(preset) for preset in profile["presets"]]
                       if is_control and "presets" in panel["sections"] else [],
            "calendar": calendar, "pin_set": is_control and bool(settings.get("pin_hash")),
        })

    async def events(self, request):
        panel, _, _ = self.authorize(request)
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream",
                                               "Cache-Control": "no-store", "X-Accel-Buffering": "no"})
        await response.prepare(request)
        refresh_message = b"event: refresh\ndata: {}\n\n"
        try:
            await response.write(refresh_message)
            while True:
                changed = self.changed_event
                try:
                    await asyncio.wait_for(changed.wait(), timeout=20)
                    refresh = True
                except asyncio.TimeoutError:
                    refresh = panel["type"] == "door" or "calendar" in panel["sections"]
                panel, _, _ = self.authorize(request)
                await response.write(refresh_message if refresh else b": heartbeat\n\n")
                if refresh:
                    await asyncio.sleep(0.15)
        except (ConnectionError, Denied, Missing):
            return response

    async def command(self, request):
        value = await body(request)
        panel, profile, _ = self.authorize(request)
        ref = value.get("entity")
        target = next((entity for entity in control.profile_entities(self.ha, profile)
                       if entity["id"] == ref or entity["entity_id"] == ref), None)
        if panel["type"] != "control" or not target or control.SECTIONS[target["domain"]] not in panel["sections"]:
            raise Denied("This panel cannot control that device.")
        action = value.get("action")
        if not isinstance(action, str):
            raise Invalid("Select an action.")
        await control.command(self.ha, target, action, value.get("value"))
        return json_response({"ok": True})

    @staticmethod
    def grant_key(panel, cookie):
        return panel["id"], hashlib.sha256(cookie.encode()).hexdigest()

    async def unlock(self, request):
        value = await body(request)
        panel, _, cookie = self.authorize(request, "presets")
        encoded = self.settings().get("pin_hash")
        pin = value.get("pin")
        if not encoded:
            raise Conflict("An administrator must configure a preset PIN first.")
        if not isinstance(pin, str) or not PIN.fullmatch(pin):
            raise Invalid("Enter four digits.")
        now = monotonic()
        count, until = self.attempts.get(panel["id"], (0, now + 60))
        if until <= now:
            count, until = 0, now + 60
        if count >= 5:
            raise Limited("Too many attempts. Wait a minute and try again.")
        self.attempts[panel["id"]] = (count + 1, until)
        if not await asyncio.to_thread(verify_pin, pin, encoded):
            raise Denied("Incorrect preset PIN.")
        self.authorize(request, "presets")
        if encoded != self.settings().get("pin_hash"):
            raise Conflict("The preset PIN has changed. Try again.")
        self.clear_device_grants(panel["id"])
        token = secrets.token_urlsafe(24)
        self.unlocks[self.grant_key(panel, cookie)] = (token, monotonic() + 120)
        return json_response({"token": token})

    def check_grant(self, panel, cookie, token):
        grant = self.unlocks.get(self.grant_key(panel, cookie))
        if (not grant or grant[1] <= monotonic() or not isinstance(token, str) or len(token) > 80
                or not secrets.compare_digest(grant[0].encode(), token.encode())):
            raise Denied("Enter the preset PIN again.")
        return grant

    async def preview_preset(self, request):
        value = await body(request)
        panel, profile, cookie = self.authorize(request, "presets")
        grant = self.check_grant(panel, cookie, value.get("token"))
        preset = self.preset(profile, request.match_info["preset"])
        snapshot, values = control.capture(self.ha, profile, preset)
        token = secrets.token_urlsafe(24)
        self.previews[self.grant_key(panel, cookie)] = {
            "token": token, "preset": preset, "profile": profile["id"],
            "grant": grant[0], "snapshot": snapshot,
        }
        return json_response({"token": token, "revision": preset["revision"], "values": values,
                              "fixed": control.fixed_actions(self.ha, preset)})

    async def save_preset(self, request):
        value = await body(request)
        panel, profile, cookie = self.authorize(request, "presets")
        preset = self.preset(profile, request.match_info["preset"])
        preview = self.previews.get(self.grant_key(panel, cookie))
        token = value.get("token")
        if (not preview or not isinstance(token, str) or len(token) > 80
                or not secrets.compare_digest(preview["token"].encode(), token.encode())):
            raise Conflict("Review the preset again before saving.")
        self.check_grant(panel, cookie, preview["grant"])
        current, _ = control.capture(self.ha, profile, preset)
        if preview["profile"] != profile["id"] or preview["preset"] != preset or preview["snapshot"] != current:
            raise Conflict("The preset or room values changed. Review them again.")
        previous = preset.get("snapshot")
        preset.update(snapshot=current, previous=previous, revision=preset["revision"] + 1)
        self.store.put("profiles", profile)
        self.clear_device_grants(panel["id"])
        self.changed()
        return json_response(public_preset(preset))

    async def restore_preset(self, request):
        profile = self.get("profiles", request.match_info["id"])
        preset = self.preset(profile, request.match_info["preset"])
        previous = preset.get("previous")
        if not previous:
            raise Conflict("There is no earlier saved version.")
        preset.update(snapshot=previous, previous=preset.get("snapshot"), revision=preset["revision"] + 1)
        self.store.put("profiles", profile)
        self.changed()
        return json_response(public_preset(preset))

    async def apply_preset(self, request):
        _, profile, _ = self.authorize(request, "presets")
        await control.apply(self.ha, profile, self.preset(profile, request.match_info["preset"]))
        return json_response({"ok": True})

    async def automation_preset(self, event):
        data = event.get("data") or {}
        try:
            profile = self.get("profiles", data.get("profile_id"))
            preset = self.preset(profile, data.get("preset_id"))
            await control.apply(self.ha, profile, preset)
        except (Invalid, HAError) as error:
            LOG.warning("Automation preset was not applied: %s", error)
