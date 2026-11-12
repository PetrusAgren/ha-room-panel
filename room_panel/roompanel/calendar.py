"""Calendar aggregation for Home Assistant and Microsoft Graph."""
from __future__ import annotations

import inspect
import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class CalendarService:
    def __init__(self, session: Any, ha: Any, settings: Any):
        self.session, self.ha, self.settings = session, ha, settings
        self._cache: dict[str, tuple[float, list[dict[str, datetime]]]] = {}
        self._inflight: dict[str, asyncio.Task] = {}
        self.cache_seconds = 20

    async def _await(self, value: Any) -> Any:
        return await value if inspect.isawaitable(value) else value

    @staticmethod
    def _parse_time(value: Any, naive_tz: Any) -> datetime | None:
        if not isinstance(value, str):
            return None
        try:
            if len(value) == 10 and value[4] == "-":
                dt = datetime.fromisoformat(value).replace(tzinfo=naive_tz)
                return dt.astimezone(timezone.utc)
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=naive_tz)
            return dt.astimezone(timezone.utc)
        except (ValueError, OverflowError):
            return None

    def _settings(self) -> dict[str, Any]:
        try:
            data = self.settings() if callable(self.settings) else self.settings
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    async def _ha_events(self, ref: str, start: datetime, end: datetime, tz: Any) -> list[dict[str, datetime]]:
        if not self.ha or not getattr(self.ha, "connected", True):
            raise RuntimeError("Home Assistant unavailable")
        entity = self.ha.get_entity(ref)
        if inspect.isawaitable(entity):
            entity = await entity
        if (not isinstance(entity, dict) or entity.get("domain") != "calendar"
                or entity.get("state") in {"unavailable", "unknown"}
                or not isinstance(entity.get("entity_id"), str)):
            raise ValueError("unknown calendar entity")
        entity_id = entity["entity_id"]
        response = await self._await(self.ha.service(
            "calendar", "get_events", entity_id,
            {"start_date_time": start.isoformat(), "end_date_time": end.isoformat()},
            return_response=True,
        ))
        if not isinstance(response, dict) or not isinstance(response.get("response"), dict):
            raise ValueError("malformed Home Assistant response")
        events = response["response"].get(entity_id)
        if not isinstance(events, dict) or not isinstance(events.get("events"), list):
            raise ValueError("malformed event list")
        out = []
        for event in events["events"]:
            if not isinstance(event, dict):
                raise ValueError("malformed event")
            begin, finish = self._parse_time(event.get("start"), tz), self._parse_time(event.get("end"), tz)
            if begin is None or finish is None or finish <= begin:
                raise ValueError("malformed event interval")
            if not event.get("is_cancelled", False) and str(event.get("availability", "busy")).lower() != "free":
                out.append({"start": begin, "end": finish})
        return out

    async def _graph_events(self, config: dict[str, Any], mailbox: str, start: datetime, end: datetime) -> list[dict[str, datetime]]:
        tenant, client, secret = (config.get(k) for k in ("tenant_id", "client_id", "client_secret"))
        if not all(isinstance(x, str) and x for x in (tenant, client, secret)):
            raise ValueError("Graph app credentials are missing")
        token_url = f"https://login.microsoftonline.com/{quote(tenant, safe='')}/oauth2/v2.0/token"
        base = f"https://graph.microsoft.com/v1.0/users/{quote(mailbox, safe='')}/calendarView"
        params: dict[str, str] | None = {"startDateTime": start.isoformat(), "endDateTime": end.isoformat(), "$top": "100", "$select": "start,end,isCancelled,showAs"}
        client_session = self.session
        if client_session is None:
            raise RuntimeError("HTTP session unavailable")
        async with client_session.post(token_url, data={"client_id": client, "client_secret": secret, "scope": "https://graph.microsoft.com/.default", "grant_type": "client_credentials"}, timeout=15, allow_redirects=False) as res:
            if res.status != 200:
                raise RuntimeError("Graph token request failed")
            token_data = await res.json()
        token = token_data.get("access_token") if isinstance(token_data, dict) else None
        if not isinstance(token, str) or not token:
            raise ValueError("malformed Graph token response")
        out: list[dict[str, datetime]] = []
        url = base
        for _ in range(20):
            parsed = urlparse(url)
            if parsed.scheme != "https" or parsed.hostname != "graph.microsoft.com" or parsed.port not in (None, 443) or not parsed.path.startswith("/v1.0/users/"):
                raise ValueError("unsafe Graph pagination URL")
            async with client_session.get(url, params=params, headers={"Authorization": f"Bearer {token}", "Prefer": 'outlook.timezone="UTC"'}, timeout=15, allow_redirects=False) as res:
                params = None
                if res.status != 200:
                    raise RuntimeError("Graph calendar request failed")
                payload = await res.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("value"), list):
                raise ValueError("malformed Graph response")
            for event in payload["value"]:
                if not isinstance(event, dict):
                    raise ValueError("malformed Graph event")
                if event.get("isCancelled") or str(event.get("showAs", "busy")).lower() in ("free", "workingelsewhere"):
                    continue
                begin_val, end_val = event.get("start"), event.get("end")
                if isinstance(begin_val, dict): begin_val = begin_val.get("dateTime")
                if isinstance(end_val, dict): end_val = end_val.get("dateTime")
                begin, finish = self._parse_time(begin_val, timezone.utc), self._parse_time(end_val, timezone.utc)
                if begin is None or finish is None or finish <= begin:
                    raise ValueError("malformed Graph event interval")
                out.append({"start": begin, "end": finish})
            nxt = payload.get("@odata.nextLink")
            if nxt is None:
                break
            if not isinstance(nxt, str):
                raise ValueError("malformed pagination URL")
            url = nxt
        else:
            raise ValueError("pagination limit exceeded")
        return out
    async def status(self, profile: Any) -> dict[str, Any]:
        cfg = profile if isinstance(profile, dict) else {}
        calendar = cfg.get("calendar") if isinstance(cfg.get("calendar"), dict) else {}
        settings = self._settings()
        graph_settings = settings.get("outlook") if isinstance(settings.get("outlook"), dict) else settings
        tz_name = settings.get("timezone") or cfg.get("timezone") or getattr(self.ha, "time_zone", None) or "UTC"
        try:
            tz = ZoneInfo(str(tz_name))
        except (ZoneInfoNotFoundError, TypeError, ValueError):
            tz = timezone.utc
            tz_name = "UTC"
        kind = calendar.get("kind", "none")
        if kind == "none":
            return {"status": "unknown", "intervals": [], "timezone": str(tz_name)}
        source_key = (kind, calendar.get("entity_id"), calendar.get("mailbox"),
                      graph_settings.get("tenant_id"), graph_settings.get("client_id"), graph_settings.get("client_secret"), str(tz_name))
        key = repr((cfg.get("id", repr(profile)), source_key))
        now_mono = time.monotonic()
        cached = self._cache.get(key)
        events = None
        if cached and now_mono - cached[0] <= self.cache_seconds:
            events = cached[1]
        if events is None:
            task = self._inflight.get(key)
            if task is None:
                async def fetch():
                    now = datetime.now(timezone.utc)
                    midnight = now.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
                    start, end = midnight - timedelta(days=1), midnight + timedelta(days=2)
                    async with asyncio.timeout(20):
                        if kind == "ha":
                            return await self._ha_events(str(calendar.get("entity_id", "")), start, end, tz)
                        if kind == "outlook":
                            return await self._graph_events(graph_settings, str(calendar.get("mailbox", "")), start, end)
                    raise ValueError("unknown calendar provider")
                task = asyncio.create_task(fetch())
                self._inflight[key] = task
            try:
                events = await task
                self._cache[key] = (time.monotonic(), events)
            except Exception:
                result = {"status": "unknown", "intervals": [], "timezone": str(tz_name)}
                return result
            finally:
                if self._inflight.get(key) is task:
                    self._inflight.pop(key, None)
        now = datetime.now(timezone.utc)
        intervals = self._merge(events or [], now)
        active = next((x for x in intervals if x["start"] <= now < x["end"]), None)
        result = {
            "status": "busy" if active else "free",
            "intervals": [{"start": x["start"].astimezone(tz).isoformat(), "end": x["end"].astimezone(tz).isoformat()} for x in intervals],
            "timezone": str(tz_name),
        }
        if active:
            result["until"] = active["end"].astimezone(tz).isoformat()
        return result

    @staticmethod
    def _merge(events: list[dict[str, datetime]], now: datetime) -> list[dict[str, datetime]]:
        valid = sorted((x["start"], x["end"]) for x in events if x["end"] > x["start"] and x["end"] > now)
        merged: list[list[datetime]] = []
        for begin, finish in valid:
            if merged and begin <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], finish)
            else:
                merged.append([begin, finish])
        return [{"start": begin, "end": finish} for begin, finish in merged]
