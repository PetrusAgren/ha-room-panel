"""Home Assistant Supervisor websocket client used by Room Panel."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any, Callable

import aiohttp

_LOG = logging.getLogger(__name__)


class HAError(Exception):
    """A Home Assistant request failed."""


class HAUnavailable(HAError):
    """Home Assistant is not currently connected."""


class HomeAssistant:
    def __init__(self, session: aiohttp.ClientSession, url: str, token: str,
                 on_change: Callable[..., Any] | None = None,
                 on_preset: Callable[..., Any] | None = None):
        self.session, self.url, self.token = session, url.rstrip("/"), token
        self.on_change, self.on_preset = on_change, on_preset
        self.connected = False
        self.time_zone: str | None = None
        self.areas: list[dict[str, Any]] = []
        self.labels: list[dict[str, Any]] = []
        self.entities: list[dict[str, Any]] = []
        self._by_id: dict[str, dict[str, Any]] = {}
        self._by_entity: dict[str, dict[str, Any]] = {}
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._reader_task: asyncio.Task | None = None
        self._next_id = 1
        self._pending: dict[int, asyncio.Future] = {}
        self._stop = asyncio.Event()
        self._events: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=512)
        self._event_task: asyncio.Task | None = None
        self._snapshot_ready = asyncio.Event()

    def _ws_url(self) -> str:
        return self.url

    async def run(self) -> None:
        """Maintain a websocket connection, rebuilding the snapshot on reconnect."""
        delay = 1
        while not self._stop.is_set():
            try:
                await self._connect_once()
                delay = 1
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOG.exception("Home Assistant websocket disconnected")
            finally:
                await self._disconnect()
            if not self._stop.is_set():
                try:
                    await asyncio.wait_for(self._stop.wait(), delay)
                except asyncio.TimeoutError:
                    pass
                delay = min(delay * 2, 30)

    async def stop(self) -> None:
        self._stop.set()
        await self._disconnect()

    async def _connect_once(self) -> None:
        self._snapshot_ready.clear()
        while not self._events.empty():
            with contextlib.suppress(asyncio.QueueEmpty):
                self._events.get_nowait()
                self._events.task_done()
        self._ws = await self.session.ws_connect(self._ws_url(), heartbeat=30)
        msg = await asyncio.wait_for(self._ws.receive(), timeout=15)
        if msg.type != aiohttp.WSMsgType.TEXT or msg.json().get("type") != "auth_required":
            raise HAError("Unexpected Home Assistant websocket greeting")
        await self._ws.send_json({"type": "auth", "access_token": self.token})
        auth = await asyncio.wait_for(self._ws.receive(), timeout=15)
        if auth.type != aiohttp.WSMsgType.TEXT or auth.json().get("type") != "auth_ok":
            raise HAError("Home Assistant websocket authentication failed")
        # Start reader before any command so correlated replies cannot be lost.
        self._reader_task = asyncio.create_task(self._reader(), name="ha-websocket-reader")
        self._event_task = asyncio.create_task(self._event_worker(), name="ha-event-worker")
        for event_type in ("state_changed", "area_registry_updated", "label_registry_updated",
                           "entity_registry_updated", "device_registry_updated",
                           "room_panel_apply_preset"):
            await self._request({"type": "subscribe_events", "event_type": event_type})
        await self._load_snapshot()
        self._snapshot_ready.set()
        self.connected = True
        await self._notify(self.on_change)
        _LOG.info("Connected to Home Assistant")
        await self._reader_task

    async def _disconnect(self) -> None:
        was_connected = self.connected
        self.connected = False
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task
        self._reader_task = None
        if self._event_task and not self._event_task.done():
            self._event_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._event_task
        self._event_task = None
        self._ws = None
        if was_connected:
            await self._notify(self.on_change)
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(HAUnavailable("Home Assistant disconnected"))
        self._pending.clear()

    async def _reader(self) -> None:
        assert self._ws
        async for msg in self._ws:
            if msg.type != aiohttp.WSMsgType.TEXT:
                if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break
                continue
            try:
                packet = msg.json()
            except (ValueError, TypeError):
                continue
            if packet.get("type") == "event":
                # Event frames carry the subscription ID, so inspect type first.
                if self._events.full():
                    _LOG.warning("Dropping Home Assistant event because the event queue is full")
                else:
                    self._events.put_nowait(packet.get("event", {}))
            elif "id" in packet:
                fut = self._pending.pop(packet["id"], None)
                if fut and not fut.done():
                    if packet.get("success", False):
                        fut.set_result(packet.get("result"))
                    else:
                        fut.set_exception(HAError(packet.get("error", {}).get("message", "Request failed")))
        if not self._stop.is_set():
            raise HAUnavailable("Home Assistant websocket closed")

    async def _request(self, command: dict[str, Any]) -> Any:
        if not self._ws or self._ws.closed:
            raise HAUnavailable("Home Assistant is disconnected")
        ident = self._next_id
        self._next_id += 1
        fut = asyncio.get_running_loop().create_future()
        self._pending[ident] = fut
        await self._ws.send_json({**command, "id": ident})
        try:
            return await asyncio.wait_for(fut, 30)
        except BaseException:
            self._pending.pop(ident, None)
            if not fut.done():
                fut.cancel()
            raise

    async def _load_snapshot(self) -> None:
        # Registry calls may be unavailable on older HA releases; derive fallbacks
        # from state attributes and device registry where possible.
        states = await self._request({"type": "get_states"}) or []
        config, areas, labels, ereg, devices = await asyncio.gather(
            self._request({"type": "get_config"}),
            self._optional({"type": "config/area_registry/list"}),
            self._optional({"type": "config/label_registry/list"}),
            self._optional({"type": "config/entity_registry/list"}),
            self._optional({"type": "config/device_registry/list"}),
        )
        self.time_zone = (config or {}).get("time_zone")
        area_rows = areas or []
        label_rows = labels or []
        self.areas = [{"id": x.get("area_id", x.get("id")), "name": x.get("name")}
                      for x in area_rows if x.get("area_id", x.get("id"))]
        self.labels = [{"id": x.get("label_id", x.get("id")), "name": x.get("name")}
                       for x in label_rows if x.get("label_id", x.get("id"))]
        label_map = {x["id"]: x["name"] for x in self.labels}
        erows = {x.get("entity_id"): x for x in (ereg or []) if x.get("entity_id")}
        device_rows = {x.get("id"): x for x in (devices or []) if x.get("id")}
        dev_area: dict[str, str] = {}
        dev_labels: dict[str, list[str]] = {}
        for dev_id, dev in device_rows.items():
            if dev.get("area_id"):
                dev_area[dev_id] = dev["area_id"]
            dev_labels[dev_id] = dev.get("labels", []) or []
        result = []
        for state in states:
            entity_id = state.get("entity_id")
            if not entity_id:
                continue
            reg = erows.get(entity_id, {})
            if reg.get("disabled_by") or reg.get("hidden_by"):
                continue
            attrs = state.get("attributes") or {}
            device_id = reg.get("device_id")
            area_id = reg.get("area_id") or (dev_area.get(device_id) if device_id else None) or attrs.get("area_id")
            label_ids = list(dict.fromkeys((reg.get("labels") or []) +
                                           (dev_labels.get(device_id, []) if device_id else []) +
                                           (attrs.get("labels", []) or [])))
            eid = reg.get("id") or entity_id
            row = {"id": eid, "entity_id": entity_id, "area_id": area_id,
                   "labels": [label_map.get(lid, lid) for lid in label_ids],
                   "name": reg.get("name") or attrs.get("friendly_name") or entity_id,
                   "domain": entity_id.split(".", 1)[0], "state": state.get("state"), "attributes": attrs}
            result.append(row)
        self.entities = result
        self._by_id = {x["id"]: x for x in result}
        self._by_entity = {x["entity_id"]: x for x in result}

    async def _optional(self, cmd: dict[str, Any]) -> Any:
        try:
            return await self._request(cmd)
        except HAError:
            return None

    async def _handle_event(self, event: dict[str, Any]) -> None:
        event_type = event.get("event_type")
        data = event.get("data", {})
        if event_type == "state_changed":
            entity_id = data.get("entity_id")
            new = data.get("new_state")
            old = data.get("old_state")
            existing = self._by_entity.get(entity_id)
            if new is None:
                if existing:
                    self.entities.remove(existing)
                    self._by_entity.pop(entity_id, None)
                    self._by_id.pop(existing["id"], None)
            elif existing:
                existing["state"] = new.get("state")
                existing["attributes"] = new.get("attributes") or {}
                existing["name"] = existing.get("name") or existing["attributes"].get("friendly_name")
            else:
                # Registry metadata is needed for stable IDs and filtering.
                await self._load_snapshot()
            await self._notify(self.on_change)
        elif event_type == "room_panel_apply_preset":
            await self._notify(self.on_preset, event)
        elif event_type in ("area_registry_updated", "label_registry_updated", "entity_registry_updated", "device_registry_updated"):
            await self._load_snapshot()
            await self._notify(self.on_change)

    async def _event_worker(self) -> None:
        while True:
            event = await self._events.get()
            await self._snapshot_ready.wait()
            try:
                await self._handle_event(event)
            except Exception:
                _LOG.exception("Unable to apply Home Assistant event")
            finally:
                self._events.task_done()

    async def _notify(self, callback: Callable[..., Any] | None, *args: Any) -> None:
        if callback is None:
            return
        try:
            value = callback(*args)
            if asyncio.iscoroutine(value):
                await value
        except Exception:
            _LOG.exception("Home Assistant event callback failed")

    def get_entity(self, ref: str) -> dict[str, Any] | None:
        return self._by_id.get(ref) or self._by_entity.get(ref)

    async def service(self, domain: str, service: str, entity_id: str | list[str],
                      data: dict[str, Any] | None = None, return_response: bool = False) -> Any:
        cmd: dict[str, Any] = {"type": "call_service", "domain": domain, "service": service,
                               "target": {"entity_id": entity_id}, "service_data": data or {}}
        if return_response:
            cmd["return_response"] = True
        return await self._request(cmd)
