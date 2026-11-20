# Implementation plan

Planning baseline and evidence are in [research](01-research.md). Proposed contracts are in [architecture](02-architecture.md). This task produces documentation only. Feature development starts after review of the plan and the first-device compatibility inputs.

## Executive decision

Extend the existing Room Panel HA app with a shared parameter/control coordinator, a native OSC provider and an optional Companion provider. Prove fader mapping and continuous interaction against the existing HA route first. Preserve room profiles, automatic HA selection, pairing and reviewed PIN capture. Keep all Room Panel configuration in its existing administration UI. Companion can run as an optional HA app on the same host. The first delivery does not depend on a new external server or a broker.

The largest changes are a versioned control schema, server-side policy across every numeric command path, per-connection state, incremental SSE updates and stable frontend range elements. Reusing control.py avoids a second implementation of HA service translation. Reusing existing HTTP/SSE avoids a second browser transport.

## Actual file changes proposed

Paths are relative to repository root. Existing files are inspected at 7d693e0. New paths in this table are proposed additions.

| Existing path or proposed new path | Functions/classes and work | Dependency and compatibility |
| --- | --- | --- |
| room_panel/roompanel/control.py | Preserve profile_entities, command, light_command/player_command and capture/apply behavior. Resolve HA parameter identity through the coordinator. Validate all scoped numeric writes, including captured volume in apply and new domains. | Foundation/mapping. Keep legacy entity command payload accepted. Existing scenes/scripts need explicit opaque-action treatment. |
| room_panel/roompanel/app.py | Extend RoomPanel initialization, admin_config, save_profile, panel_state, events, command, automation_preset and both application routers. Add scoped control API, revisions, connection endpoints and per-connection availability. | Coordinator and schema. Retain both listener security boundaries and relative Ingress routes. |
| room_panel/roompanel/ha.py | HomeAssistant._load_snapshot preserves label IDs alongside display names. _reader/_event_worker detect overflow and trigger resnapshot. Pass relevant state changes to HA provider. Reuse service and _request. | Needed for new state model. Preserve old snapshot shape until callers migrate. |
| room_panel/roompanel/configuration.py | Extend normalize_profile/normalize_panel, public_profile/public_settings and PANEL_SECTIONS. Add typed validators for connection, parameter policy, mapping, controls and profile imports. | Schema migration. Existing profiles get empty controls and historical ranges. Reject incompatible edits using revisions. |
| room_panel/roompanel/store.py | Store._kind accepts new connection/policy kinds. Add schema version and explicit transaction helpers. Add compare-and-swap profile writes. Enforce existing pairing timestamp expiry when auth is touched. | Consistent backup first. Preserve IDs, previous presets, signing secret and sessions. |
| room_panel/roompanel/__main__.py | run starts/stops coordinator and enabled providers with bounded tasks. Close UDP transports and subscriptions on SIGTERM, and retain TabletListener.configure. | Transport/provider lifecycle. No additional web process. |
| room_panel/roompanel/static/admin.js | Extend profileEditor/panelEditor and openEditor forms. Add connection forms, profile/parameter selectors, mapping preview, revision conflicts and read-only test flow. | Descriptor APIs. Existing settings/presets remain editable. |
| room_panel/roompanel/static/admin.html and admin.css | Add Connections navigation/view and range/orientation fields following current readable admin palette. | Existing design. Keep forms usable inside Ingress and on iPad. |
| room_panel/roompanel/static/panel.js | Extend entityCard/renderSections, command, refresh/realtime and existing controls event delegation. Replace section-wide refresh blocking with per-control keyed updates. Add input throttling, keyboard/pointer lifecycle and pending/observed feedback. | SSE and coordinator contract. Preserve current RGBW/RGBWW Apply flow, navigation, paired/direct URLs and door mode. |
| room_panel/roompanel/static/panel.html and panel.css | Shared horizontal/vertical range markup, associated label/output, mute action and status/busy/pickup states. | Reuse range handling. New controls do not redesign the current page. |
| room_panel/config.yaml | Declare bounded optional UDP mappings and descriptions after networking spike. Document when network changes require restart. | Test amd64/aarch64 and VM networking. Retain with-contenv launch and homeassistant_api permission. |
| room_panel/requirements.txt | Add a tested pinned python-osc version with documented runtime compatibility. Reuse aiohttp for Companion HTTP/WebSocket. | Library qualification. Add no Node runtime or frontend framework. |
| room_panel/DOCS.md | Extend existing operational instructions with Connections setup, UDP slots, independent limits, backup/restore, feedback quality and Companion prerequisites. | Implementation verification. Planned examples remain in planning docs until implemented. |
| tests/test_app.py, test_ha.py, test_calendar.py | Extend HTTP auth/preset regressions, HA overflow/reconnect tests and retain calendar behavior. | Run baseline and identify pre-existing failures separately. |
| New room_panel/roompanel/parameters.py | Small coordinator, canonical parameter keys, policies, runtime state, gesture leases and bounded send slots. | Keep this small before extracting further abstractions. |
| New room_panel/roompanel/mapping.py | Pure forward/reverse mappings, silence handling, units, policy intersection and representable quantization. | Unit tests before transport. No frontend formulas. |
| New room_panel/roompanel/providers/ha.py, osc.py, companion.py | HA wrapper, bidirectional UDP and Companion HTTP/Satellite adapters, respectively. | Separate tasks and failure state, common coordinator contract. |
| New room_panel/roompanel/device_profiles.py and profiles/*.json | Versioned declarative profile loading/validation, generic OSC descriptor and one qualified product. | Profiles are packaged within the existing Docker COPY tree. Runtime imports live in /data and are validated data. |
| New tests/test_mapping.py, test_parameters.py, test_osc.py, test_companion.py, test_migrations.py | Regression-focused fixtures for the new behavior. | Tests target observable contracts and failure cases. |
| New tests/simulated_osc_device.py and tests/browser/ | Virtual device and focused browser interaction suite. | Development-only dependencies. Production image stays small. |

## Phase 0 — qualify the baseline and contracts

Objective is a reproducible baseline and a bounded first delivery. Depends on repository access and documented requirements.

1. Check out the pinned baseline and run README's unittest and JavaScript syntax commands in a supported Python environment with aiohttp installed. Record exact versions and any existing failures.
2. Capture a sanitized legacy database fixture containing manual/automatic room profiles, presets, two panel IDs, sessions, branding and calendar bindings.
3. Review labels in _load_snapshot versus label_id comparisons in profile_entities. Prepare the ID-preservation change with a fixture where label ID differs from name.
4. Confirm HAOS version, architecture, adapter mode, actual Companion API/modules and first mixer firmware. Read/export existing Companion config before experiments.
5. Approve architecture contracts for canonical units, parameter policy, gesture lease, observed quality and read-on-connect.

Accepted when the baseline can be reproduced, required environment facts are recorded and unresolved product details are explicitly deferred. Tests are baseline commands and configuration fixture load. Risks are assumptions about the installed system or stale local source. Done means a reviewed compatibility record with no equipment writes.

## Phase 1 — shared state, policy and additive schema

Objective is to route existing numeric HA controls through a common boundary. Depends on Phase 0.

1. Add schema migration, version checks, consistent backup and transactional writes.
2. Introduce canonical target IDs, runtime state and per-connection availability. Wrap existing HA service translations and state reads.
3. Add common target policy and scoped narrower control ranges. Wire legacy volume commands, admin test, Room Panel preset apply and the HA event path through the policy boundary. Introduce optional canonical parameter snapshots while reading existing HA snapshots. Preflight recalls and report per-target partial results without automatic audio rollback.
4. Add revision guards, scoped typed commands and error states without removing old API payloads.
5. Repair the label-ID mismatch during this extension and detect/resnapshot on HA event queue overflow. Enforce pairing token expiry against its stored created timestamp.

Accepted when an old panel still works and a raw request, preset or automation cannot exceed a configured Room Panel target ceiling. Tests cover legacy HTTP flows, policy path enumeration, auth, revision conflict, migration failure/rollback and HA snapshot overflow. Risks are bypass paths and implicit breaking schema changes. Done means preserved legacy fixtures and verified write authorization for every route.

## Phase 2 — reusable continuous fader

Objective is a smooth generic control with independent display/output ranges. Depends on Phase 1.

1. Implement linear mapping and inverse, quantization and finite-number checks, followed by log/dB and monotonic curve fixtures.
2. Extend the existing range renderer and event delegation with vertical/horizontal presentation, units, numeric display and optional mute.
3. Add local input updates, latest-value throttling, immediate final-value flush, backend queue bounds and parameter leases. Implement pointer capture/cancel and keyboard equivalents.
4. Extend SSE with scoped deltas and revision/epoch recovery. Patch stable DOM nodes so unrelated feedback stays live during a drag.
5. Present pending/observed/stale/busy and out-of-range pickup behavior. Keep authoritative observation separate from the thumb.

Accepted when the exact 0–70% and 20–80% examples pass in both directions, two panels synchronize, active thumbs remain stable, cancellations send no stale motion, and external values above limits display truthfully. Tests include mouse/touch/keyboard, final packet order, late feedback and two simultaneous operators. Risks are jumps, request backlog and false confirmation. Done means the fader works against the existing HA volume provider in a measured browser test.

## Phase 3 — generic native OSC and networking

Objective is direct typed bidirectional OSC in the existing app. Depends on Phases 1–2.

1. Qualify a python-osc release and codec behavior, including bounded malformed packets, nested bundles, timetags and exact type tags.
2. Add managed sockets, send/read handlers, source filtering, multiple devices and explicit feedback routing. Support one shared sending/receiving socket where required.
3. Add configurable remote ports and allocated local UDP slots. Verify actual mapped port and source reply behavior on HAOS VirtualBox.
4. Implement read-on-connect, bounded retries for read-only work, freshness, connection generations and discard-on-disconnect.
5. Add the virtual device and run loss/reorder/restart scenarios before using a console.

Accepted when two simulated devices can run independently, values and feedback round-trip with declared types, wrong-source packets are ignored, all sockets close cleanly and reconnect emits zero level writes. Tests include packet capture, CPU/resource bounds and HAOS UDP reachability. Risks are NAT reply ports, UDP loss and inferred connectivity. Done means the networking matrix works on the intended VM and unsupported modes give actionable diagnostics.

## Phase 4 — profiles and administration workflow

Objective is technician configuration without YAML. Depends on Phase 3 and a selected first product.

1. Add declarative profile schema/version/hash and generic OSC forms for arguments, feedback and fixed numeric channels. Extend PIN capture review to show direct-parameter freshness and exclude unknown values.
2. Build Connections and control-list forms inside existing administration. Add parameter search, label/orientation/ranges, five-point mapping preview and opt-in write test.
3. Implement one real profile with documented query, subscription renewal, value law, silence/mute and availability evidence.
4. Add profile-update preview and explicit migration, preserving older pinned profiles until accepted. Disallow deleting in-use bindings without reassignment.
5. Verify automatic HA discovery remains correct when a new HA device is added to an area and label.

Accepted when a technician creates a connection and fader in UI, reloads the app, tests a bounded write and sees feedback on two panels. Generic OSC must work without source edits. Tests cover validation, malicious imports, missing targets, profile-version drift and selected hardware endpoints. Risks are wrong parameter units and profile changes. Done means one product's claimed capabilities are backed by recorded test evidence.

## Phase 5 — Companion buttons and qualified continuous controls

Objective is reuse of current Companion actions. Shared contracts allow API qualification work after Phase 1, while full editor integration follows Phase 4.

1. Pilot an optional HA-hosted Companion candidate with a copied/exported config, or connect the existing external instance. Verify backups and resource load independently.
2. Implement redacted instance config, bounded HTTP location presses and connection diagnostics. Read installed API schema/version and negotiate Satellite capabilities.
3. Add a location picker/manual entry and bounded page export importer. Stream approved text/color feedback, subscribing only to authorized bindings.
4. Qualify an older surface fallback or mark older feedback unavailable. Test page moves, missing buttons and re-import without duplicate controls.
5. Prototype one numeric variable-to-module action plus actual device-feedback path. Enable a Companion fader only for an explicitly verified contract and rate. Add hold controls only after safe-release tests.

Accepted when an existing Companion button operates from Room Panel with live feedback and survives reconnect without replay. Numeric support must prove actual device movement and inverse feedback at endpoints. Tests use fake HTTP/Satellite servers and the selected real module. Risks are API drift, stuck down actions, false visual-state assumptions and variable-only writes. Done means capability diagnostics clearly state supported behavior per instance.

## Phase 6 — HA domains and optional entity export

Objective is HA automation compatibility with a single device write owner. Depends on Phase 4 and a reviewed bridge choice.

1. Add native HA number/switch/button/select controls and sensor display through the existing client, domain-specific validation and room filtering.
2. Extend the existing HA event pattern for approved direct-device commands. Document its app-running dependency and absence of native entities at this milestone.
3. Prototype optional MQTT discovery only if a broker is acceptable. Publish selected entities, availability and quality with stable IDs and no retained commands. Define delete/reconfigure cleanup.
4. Compare the broker prototype with the cost of a small provider-neutral Core integration. Select one optional export mechanism before production rollout.
5. Route all exported commands through common policy, exclude exports from rediscovery and verify native scene behavior for supported numeric entities.

Accepted when automation and UI reach the same bounded target once, an unavailable device is represented correctly, and restart/reconnect does not replay a retained level. Tests cover loops, deleted entities, scenes and multiple command origins. Risks are duplicated authority and install dependencies. Done means the chosen optional bridge has a documented installation, restore and rollback path.

## Phase 7 — measured deployment and hardening

Objective is a production-qualified pilot. Depends on the enabled feature phases.

Run simultaneous panel, mixer, HA and Companion interactions. Test Wi-Fi loss, VirtualBox restart, Supervisor/app/Core restart, invalid traffic, profile edits during a drag and cold backup restoration. Record the hardware, rates and versions for every performance measurement. Tune thresholds from evidence and document the supported load envelope.

Accepted when the test matrix below passes, bounded policies hold across all Room Panel paths, restart produces zero unintended level writes, and the existing calendar/preset/RGBW flows remain intact. Risks and mitigation owners are in [risk assessment](04-risk-assessment.md). Done means a reviewed pilot report, usable admin instructions and a tested rollback bundle.

## Verification matrix and provisional budgets

Numerical budgets are proposed acceptance targets for the pilot, with no current measurement implied.

| Area | Test and pass criterion |
| --- | --- |
| Mapping | Every example point, endpoints, non-zero minima, round-trip within device step, finite checks, integer/float32 rounding, empty policy intersection and unrepresentable interval. |
| Audio | dB/amplitude fixtures, zero sentinel, mute/unmute, unity gain, piecewise joins and manufacturer known values. Custom curves reject flat/non-monotonic input. |
| Policy | Direct, forged, legacy HA, admin-test, preset, automation and export commands all use common policy. Scene/opaque-action UI does not claim an enforceable ceiling. |
| State | Local command acknowledgement never creates observed quality. Test unsolicited, duplicate, delayed, dropped and reordered packets. |
| Multi-client | Two panels competing for the same parameter, two independent faders, lease expiry, slow client and revoked session. The losing panel cannot send late values. |
| Input | iPad Safari in browser and home-screen mode, Chrome desktop, keyboard arrows/Home/End, screen reader labels, pointercancel and page visibility. A minimum 44-pixel touch target is the proposed design target. |
| Latency | p95 local visual response under 16.7 ms on target iPad, pointer-to-backend send under 100 ms on the agreed LAN, newest value emitted within one send interval plus processing. Measure device feedback separately. |
| Traffic | Start at 20 Hz OSC/HA and 10 Hz Companion per active parameter. Each send queue has at most one current plus one replaceable pending level. Final values are not starved. |
| Load | Initial supported pilot target, five panels, twenty visible parameters each, ten simultaneously moving parameters. Record packet rate, SSE bytes, CPU/RAM, rendering and p95 delay over thirty minutes. |
| Recovery | HA outage leaves qualified OSC usable. Device restart renews read/subscriptions. App restart has no queued level writes. Stale quality becomes visible within the profile's freshness threshold. |
| Persistence | Atomic migration from legacy fixture, crash mid-save, concurrent admin edits, backup with WAL consistency and restore into a clean HAOS test instance. |
| Regression | Existing pairing, revocation, Ingress assets, TLS, PIN capture/restore, RGBW/RGBWW and private calendar filtering. |

The simulated OSC device should implement absolute level and mute, read requests, feedback to source port or fixed port, optional subscription expiry and configurable delay/drop/reorder. Use a random unprivileged port on loopback during automated tests, with a fixed documented port for HAOS networking tests. Persist captured packets as sanitized test evidence, never production secrets. Simulate Companion BEGIN/CAPS, errors, HTTP timeouts, subscriptions and variable output with a separate local test fixture.

## Migration, rollout and rollback

1. Make a consistent SQLite backup via SQLite backup facilities or cold app shutdown. Preserve the matching app image/version and Companion export. A bare copy of a live WAL database may omit committed data.
2. Migrate missing schema version as legacy v1, add optional controls and policies with identity/full-range defaults, and preserve all existing IDs and session hashes. New features remain opt-in per room.
3. Validate references and profile versions before swapping the runtime configuration. Failed migration rolls back its transaction and leaves the original database usable. A newer unsupported schema stops with instructions instead of silently rewriting it.
4. Pilot one room with the new control alongside normal observation. Test independent HA discovery and the panel-specific authorization. Direct audio testing needs an agreed safe listening setup.
5. If rollout fails, stop the new app and restore its pre-migration database with the previous app image. Restore Companion independently if it was updated. Preserve a copy of the failed new database for diagnosis. Do not assume the older app can round-trip new fields.

Configuration and record migrations are explicit reviewed changes. No new physical write-on-connect behavior is permitted by migration. Feature rollback removes new bindings only through the version-aware restore flow.
