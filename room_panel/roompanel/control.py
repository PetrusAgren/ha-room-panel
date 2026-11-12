"""Room scoping and translation of supported controls to Home Assistant services."""

from __future__ import annotations

import asyncio

from .ha import HAError, HAUnavailable

SECTIONS = {"light": "lights", "media_player": "music", "script": "scripts"}
PLAYER_FEATURES = {"play": 16384, "pause": 1, "volume": 4, "mute": 8, "source": 2048}
COLOR_MODES = {"hs", "xy", "rgb", "rgbw", "rgbww"}
COLORS = {"color_temp": "color_temp_kelvin", **{mode: f"{mode}_color" for mode in COLOR_MODES}}
ATTRIBUTES = {
    "brightness", "color_mode", "supported_color_modes", "color_temp_kelvin", "min_color_temp_kelvin",
    "max_color_temp_kelvin", "rgb_color", "media_title", "media_artist", "source", "source_list",
    "volume_level", "is_volume_muted", "supported_features",
}


class Invalid(ValueError):
    status = 400


class Denied(Invalid):
    status = 401


class Missing(Invalid):
    status = 404


class Conflict(Invalid):
    status = 409


class Limited(Invalid):
    status = 429


def number(value, lower, upper, label):
    if type(value) not in (int, float) or not lower <= value <= upper:
        raise Invalid(f"{label} must be between {lower} and {upper}.")
    return value


def boolean(value):
    if type(value) is not bool:
        raise Invalid("Expected an on/off value.")
    return value


def enabled_area(ha, profile):
    return profile["area_id"] in {area["id"] for area in ha.areas}


def profile_entities(ha, profile):
    if not enabled_area(ha, profile):
        return []
    selected = set(profile["entities"])
    visible = []
    for entity in ha.entities:
        explicit = entity["id"] in selected or entity["entity_id"] in selected
        if entity["domain"] == "script":
            permitted = explicit
        elif entity["domain"] in ("light", "media_player") and entity["area_id"] == profile["area_id"]:
            permitted = explicit if profile["mode"] == "manual" else profile["label_id"] in entity["labels"]
        else:
            permitted = False
        if permitted:
            visible.append(entity)
    return visible


def public_entity(entity):
    return {
        **{key: entity[key] for key in ("id", "entity_id", "name", "domain", "state")},
        "attributes": {key: value for key, value in entity["attributes"].items() if key in ATTRIBUTES},
    }


def available(entity):
    if entity["state"] in {None, "unknown", "unavailable"}:
        raise Conflict(f"{entity['name']} is unavailable in Home Assistant.")


def light_command(attrs, action, value):
    modes = set(attrs.get("supported_color_modes") or ())
    if action == "power":
        return ("turn_on" if boolean(value) else "turn_off"), {}
    if action == "brightness" and modes - {"onoff", "unknown"}:
        level = round(number(value, 0, 100, "Brightness"))
        return ("turn_on", {"brightness_pct": level}) if level else ("turn_off", {})
    if action == "color_temp" and "color_temp" in modes:
        lower = attrs.get("min_color_temp_kelvin") or 1000
        upper = attrs.get("max_color_temp_kelvin") or 10000
        return "turn_on", {"color_temp_kelvin": round(number(value, lower, upper, "Temperature"))}
    if action == "color" and modes & COLOR_MODES:
        if not isinstance(value, list) or len(value) != 3:
            raise Invalid("Expected three RGB values.")
        return "turn_on", {"rgb_color": [round(number(v, 0, 255, "RGB")) for v in value]}
    raise Invalid("This light does not support that action.")


def player_command(attrs, action, value):
    if not int(attrs.get("supported_features") or 0) & PLAYER_FEATURES.get(action, 0):
        raise Invalid("This player does not support that action.")
    if action == "volume":
        return "volume_set", {"volume_level": number(value, 0, 1, "Volume")}
    if action == "mute":
        return "volume_mute", {"is_volume_muted": boolean(value)}
    if action == "source":
        if not isinstance(value, str) or value not in (attrs.get("source_list") or []):
            raise Invalid("Unknown player source.")
        return "select_source", {"source": value}
    return f"media_{action}", {}


async def command(ha, entity, action, value):
    if not ha.connected:
        raise HAUnavailable("Home Assistant is disconnected.")
    available(entity)
    domain = entity["domain"]
    if domain == "light":
        service, data = light_command(entity["attributes"], action, value)
    elif domain == "media_player":
        service, data = player_command(entity["attributes"], action, value)
    elif domain == "script" and action == "run":
        service, data = "turn_on", {}
    else:
        raise Invalid("Unknown action.")
    await ha.service(domain, service, entity["entity_id"], data)


def light_snapshot(entity):
    state, attrs = entity["state"], entity["attributes"]
    if state not in {"on", "off"}:
        raise Conflict(f"{entity['name']} has no light state.")
    data = {}
    label = state.capitalize()
    if state == "on":
        if isinstance(attrs.get("brightness"), (int, float)):
            key = "white" if attrs.get("color_mode") == "white" else "brightness"
            data[key] = attrs["brightness"]
            label = f"{round(attrs['brightness'] * 100 / 255)}%"
        color = COLORS.get(attrs.get("color_mode"))
        if color and attrs.get(color) is not None:
            data[color] = attrs[color]
            if color == "color_temp_kelvin":
                label += f" · {data[color]} K"
            elif isinstance(data[color], (list, tuple)):
                data[color] = list(data[color])
                label += f" · {color.removesuffix('_color').upper()} " + "/".join(map(str, data[color]))
        if attrs.get("effect") in (attrs.get("effect_list") or ()):
            data["effect"] = attrs["effect"]
            label += f" · {data['effect']}"
    return {"domain": "light", "state": state, "data": data}, label


def capture(ha, profile, preset):
    if not ha.connected:
        raise HAUnavailable("Home Assistant is disconnected.")
    snapshot, values = {}, []
    for entity in profile_entities(ha, profile):
        if entity["domain"] == "light":
            available(entity)
            entry, label = light_snapshot(entity)
        elif entity["domain"] == "media_player" and preset["capture_volume"]:
            attrs = entity["attributes"]
            if not int(attrs.get("supported_features") or 0) & PLAYER_FEATURES["volume"]:
                continue
            available(entity)
            volume = number(attrs.get("volume_level"), 0, 1, "Volume")
            entry = {"domain": "media_player", "data": {"volume_level": volume}}
            label = f"Volume {round(volume * 100)}%"
        else:
            continue
        snapshot[entity["id"]] = entry
        values.append({"name": entity["name"], "value": label})
    if not snapshot:
        raise Conflict("There are no available lights or volume controls to capture.")
    return snapshot, values


def fixed_actions(ha, preset):
    actions = []
    if preset.get("transition"):
        actions.append(f"Light transition: {preset['transition']} s")
    if preset.get("script"):
        entity = ha.get_entity(preset["script"])
        actions.append(entity["name"] if entity else preset["script"])
    return actions


def action_entity(ha, ref, domain):
    entity = ha.get_entity(ref)
    if not entity or entity["domain"] != domain:
        raise Conflict(f"The preset's Home Assistant {domain} is missing.")
    available(entity)
    return entity["entity_id"]


async def apply(ha, profile, preset):
    if not ha.connected:
        raise HAUnavailable("Home Assistant is disconnected.")
    if not enabled_area(ha, profile):
        raise Conflict("The profile's Home Assistant area is missing.")
    script = action_entity(ha, preset["script"], "script") if preset.get("script") else None
    transition = {"transition": preset["transition"]} if preset.get("transition") else {}
    commands = []
    snapshot = preset.get("snapshot")
    if snapshot:
        permitted = {entity["id"]: entity for entity in profile_entities(ha, profile)}
        for key, entry in snapshot.items():
            entity = permitted.get(key)
            if not entity or entity["domain"] != entry["domain"]:
                raise Conflict("A captured device is outside this profile. Review and save the preset again.")
            available(entity)
            if entity["domain"] == "light":
                service = "turn_on" if entry["state"] == "on" else "turn_off"
                data = {**entry["data"], **transition}
            else:
                service, data = "volume_set", entry["data"]
            commands.append((entity["domain"], service, entity["entity_id"], data))
    elif preset.get("scene"):
        commands.append(("scene", "turn_on", action_entity(ha, preset["scene"], "scene"), transition))
    elif not script:
        raise Conflict("Save the preset or configure a starting scene or script first.")
    results = await asyncio.gather(*(ha.service(*command) for command in commands), return_exceptions=True)
    failures = sum(isinstance(result, BaseException) for result in results)
    if failures:
        raise HAError(f"{failures} of {len(commands)} preset commands failed. Check the room before retrying.")
    if script:
        await ha.service("script", "turn_on", script)
