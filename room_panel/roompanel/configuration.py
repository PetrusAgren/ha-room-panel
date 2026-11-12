"""Validation and public views of administrator configuration."""

from __future__ import annotations

import base64
import binascii
import re
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .control import Invalid, SECTIONS, number

DEFAULTS = {
    "name": "Room Panel", "primary": "#bad7f2", "background": "#f6f1e9", "logo": "",
    "base_url": "", "tls_cert": "", "tls_key": "", "timezone": "",
}
PANEL_SECTIONS = ["presets", *SECTIONS.values(), "calendar"]
ID = re.compile(r"[a-zA-Z0-9_-]{1,80}")
PIN = re.compile(r"[0-9]{4}")
COLOR = re.compile(r"#[0-9a-fA-F]{6}")
IMAGE = re.compile(r"data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/=]+)")


def string(value, label, maximum=200, required=False):
    if not isinstance(value, str) or len(value) > maximum:
        raise Invalid(f"Invalid {label}.")
    value = value.strip()
    if required and not value:
        raise Invalid(f"{label.capitalize()} is required.")
    return value


def unique_strings(values, label):
    if not isinstance(values, list) or len(values) > 512:
        raise Invalid(f"Invalid {label}.")
    return list(dict.fromkeys(string(value, label, required=True) for value in values))


def entity_ref(ha, value, domain, old=None):
    ref = string(value or "", domain)
    if not ref:
        return ""
    entity = ha.get_entity(ref)
    if entity and entity["domain"] == domain:
        return entity["id"]
    if ref == old and not entity:
        return ref
    raise Invalid(f"Select an existing Home Assistant {domain}.")


def normalize_calendar(ha, value, old):
    if not isinstance(value, dict):
        raise Invalid("Invalid calendar.")
    kind = value.get("kind", "none")
    if kind == "none":
        return {"kind": kind}
    if kind == "ha":
        ref = entity_ref(ha, value.get("entity_id"), "calendar", old.get("entity_id"))
        if not ref:
            raise Invalid("Select a Home Assistant calendar.")
        return {"kind": kind, "entity_id": ref}
    if kind == "outlook":
        mailbox = string(value.get("mailbox"), "mailbox", required=True)
        if not re.fullmatch(r"[^@\s/]+@[^@\s/]+", mailbox):
            raise Invalid("Enter the room mailbox email address.")
        return {"kind": kind, "mailbox": mailbox}
    raise Invalid("Unknown calendar provider.")


def normalize_presets(ha, values, old, make_id):
    if not isinstance(values, list) or len(values) > 128:
        raise Invalid("Invalid preset list.")
    previous = {preset["id"]: preset for preset in old}
    presets, seen = [], set()
    for value in values:
        if not isinstance(value, dict):
            raise Invalid("Invalid preset.")
        ident = value.get("id") or make_id()
        if not isinstance(ident, str) or not ID.fullmatch(ident) or ident in seen:
            raise Invalid("Invalid or duplicate preset ID.")
        seen.add(ident)
        before = previous.get(ident, {})
        volume = value.get("capture_volume", False)
        if type(volume) is not bool:
            raise Invalid("Invalid preset volume setting.")
        presets.append({
            **before, "id": ident, "name": string(value.get("name"), "preset name", required=True),
            "scene": entity_ref(ha, value.get("scene"), "scene", before.get("scene")),
            "script": entity_ref(ha, value.get("script"), "script", before.get("script")),
            "transition": number(0 if value.get("transition") is None else value["transition"], 0, 300, "Transition"),
            "capture_volume": volume, "revision": before.get("revision", 0),
        })
    return presets


def normalize_profile(ha, value, old, ident, make_id):
    name = string(value.get("name"), "name", required=True)
    area = string(value.get("area_id"), "area", required=True)
    if area not in {area["id"] for area in ha.areas} and area != old.get("area_id"):
        raise Invalid("Select an existing Home Assistant area.")
    mode = value.get("mode", "manual")
    if mode not in ("manual", "auto"):
        raise Invalid("Invalid entity selection mode.")
    label = string(value.get("label_id") or "", "label")
    if mode == "auto" and (not label or (label not in {item["id"] for item in ha.labels}
                                         and label != old.get("label_id"))):
        raise Invalid("Select a Home Assistant label for automatic discovery.")
    entities = []
    for ref in unique_strings(value.get("entities", []), "entities"):
        entity = ha.get_entity(ref)
        if not entity:
            if ref in old.get("entities", []):
                entities.append(ref)
                continue
            raise Invalid("One or more selected entities are missing.")
        if entity["domain"] not in SECTIONS or (entity["domain"] != "script" and entity["area_id"] != area):
            raise Invalid("Select controls from this area or a Home Assistant script.")
        entities.append(entity["id"])
    return {
        "id": ident, "name": name, "area_id": area, "mode": mode, "label_id": label,
        "entities": list(dict.fromkeys(entities)),
        "calendar": normalize_calendar(ha, value.get("calendar", {}), old.get("calendar", {})),
        "presets": normalize_presets(ha, value.get("presets", []), old.get("presets", []), make_id),
    }


def normalize_panel(store, value, ident):
    name = string(value.get("name"), "name", required=True)
    profile_id = string(value.get("profile_id"), "profile", required=True)
    if not store.get("profiles", profile_id):
        raise Invalid("Select an existing room profile.")
    kind = value.get("type")
    if kind not in ("control", "door"):
        raise Invalid("Select a panel type.")
    sections = unique_strings(value.get("sections", PANEL_SECTIONS), "sections")
    if set(sections) - set(PANEL_SECTIONS):
        raise Invalid("Unknown panel section.")
    return {"id": ident, "name": name, "profile_id": profile_id, "type": kind,
            "sections": sections if kind == "control" else []}


def normalize_origin(value, dev):
    value = string(value, "local panel URL")
    if not value:
        return ""
    try:
        url = urlsplit(value)
        port = url.port
    except ValueError as error:
        raise Invalid("Invalid panel URL.") from error
    schemes = {"http", "https"} if dev else {"https"}
    if (url.scheme not in schemes or not url.hostname or url.username or url.password
            or url.path not in ("", "/") or url.query or url.fragment
            or not re.fullmatch(r"[a-zA-Z0-9.:-]+", url.hostname)):
        raise Invalid("Use the local HTTPS origin of the panel listener.")
    host = f"[{url.hostname}]" if ":" in url.hostname else url.hostname
    default_port = 443 if url.scheme == "https" else 80
    return f"{url.scheme}://{host}" + (f":{port}" if port is not None and port != default_port else "")


def normalize_logo(logo):
    if logo == "":
        return logo
    if not isinstance(logo, str) or len(logo) > 1_400_000:
        raise Invalid("The logo must be at most 1 MB.")
    match = IMAGE.fullmatch(logo)
    if not match:
        raise Invalid("Upload a PNG, JPEG, or WebP logo.")
    try:
        data = base64.b64decode(match[2], validate=True)
    except binascii.Error as error:
        raise Invalid("Invalid logo image.") from error
    kind = match[1]
    valid = {
        "png": data.startswith(b"\x89PNG\r\n\x1a\n"),
        "jpeg": data.startswith(b"\xff\xd8\xff"),
        "webp": data.startswith(b"RIFF") and data[8:12] == b"WEBP",
    }
    if not valid[kind] or len(data) > 1_048_576:
        raise Invalid("Upload a valid image up to 1 MB.")
    return logo


def normalize_settings(value, old, dev):
    result = {**DEFAULTS, **old}
    result["name"] = string(value.get("name", result["name"]), "name", 80, required=True)
    for key in ("primary", "background"):
        color = value.get(key, result[key])
        if not isinstance(color, str) or not COLOR.fullmatch(color):
            raise Invalid(f"Invalid {key} color. Use a six-digit hex color.")
        result[key] = color
    result["logo"] = normalize_logo(value.get("logo", result["logo"]))
    result["base_url"] = normalize_origin(value.get("base_url", result["base_url"]), dev)
    for key in ("tls_cert", "tls_key"):
        result[key] = string(value.get(key, result[key]), key.replace("_", " "))
    zone = string(value.get("timezone", result["timezone"]), "time zone")
    if zone:
        try:
            ZoneInfo(zone)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise Invalid("Enter an IANA time zone, for example Europe/Stockholm.") from error
    result["timezone"] = zone
    if "outlook" in value:
        outlook = value["outlook"]
        if not isinstance(outlook, dict):
            raise Invalid("Invalid Outlook settings.")
        previous = result.get("outlook", {})
        result["outlook"] = {
            "tenant_id": string(outlook.get("tenant_id", previous.get("tenant_id", "")), "tenant ID"),
            "client_id": string(outlook.get("client_id", previous.get("client_id", "")), "client ID"),
            "client_secret": string(outlook.get("client_secret") or outlook.get("secret") or previous.get("client_secret", ""),
                                    "client secret", 4000),
        }
        if outlook.get("clear_secret") is True:
            result["outlook"]["client_secret"] = ""
    return result


def public_settings(settings):
    outlook = settings.get("outlook", {})
    return {**{key: settings[key] for key in DEFAULTS}, "pin_set": bool(settings.get("pin_hash")),
            "outlook": {"tenant_id": outlook.get("tenant_id", ""), "client_id": outlook.get("client_id", ""),
                        "secret_set": bool(outlook.get("client_secret"))}}


def public_preset(preset, *, admin=False):
    fields = ("id", "name", "revision")
    if admin:
        fields += ("scene", "script", "transition", "capture_volume")
    return {**{key: preset[key] for key in fields}, "can_restore": bool(preset.get("previous")),
            "can_apply": bool(preset.get("snapshot") or preset.get("scene") or preset.get("script"))}


def public_profile(profile):
    return {**profile, "presets": [public_preset(preset, admin=True) for preset in profile["presets"]]}
