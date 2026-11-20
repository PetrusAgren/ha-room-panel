# Prioritized implementation backlog

Each item is intended to be independently reviewable. Dependencies are task IDs. P0 is required for the first safe direct-fader pilot, P1 completes the planned device/Companion workflow, and P2 provides optional HA entity export and later refinements. Estimates are deliberately omitted until the first-device/network qualification is complete.

Read [research](01-research.md) for verified current behavior, [architecture](02-architecture.md) for proposed contracts, [implementation plan](03-implementation-plan.md) for phases/tests and [risk assessment](04-risk-assessment.md) for risk IDs.

| ID | Priority | Work item and principal files | Depends on | Independently verifiable result |
| --- | --- | --- | --- | --- |
| T01 | P0 | Reproduce 0.1.2 baseline and save environment record. README development commands and existing tests. | — | Baseline results distinguish actual failures from missing dependencies. |
| T02 | P0 | Record HAOS/VM/Companion/product compatibility and perform read-only UDP topology spike. config.yaml deployment assumptions. | T01 | Hardware/model/firmware, CPU architecture, VM adapter and reply-port requirements documented. |
| T03 | P0 | Review canonical units, quality states, API commands, policy scope and gesture contract. Planning docs. | T01 | Reviewed examples include output cap, external overrange and reconnect. |
| T04 | P0 | Make sanitized legacy database and backup/restore fixture. Store. | T01 | Profiles/presets/sessions survive consistent fixture restore. |
| T05 | P0 | Introduce schema version and explicit atomic migration. store.py/configuration.py. | T03,T04 | Crash midway leaves valid old/new state, never mixed records. |
| T06 | P0 | Preserve label IDs alongside names in HA snapshot and filter tests. ha.py/control.py. | T01 | Automatic room selection survives differing names and label rename. |
| T07 | P0 | Enforce existing pairing TTL and test route authorization boundaries. store.py/app.py. | T01 | Expired/replayed links fail, legitimate sessions continue working. |
| T08 | P0 | Add runtime parameter state and small HA provider wrapper. parameters.py/providers/ha.py. | T03,T05 | Existing HA action payloads work and state quality is explicit. |
| T09 | P0 | Add pure linear forward/inverse mapping and range validation. mapping.py. | T03 | Exact five-point 0–70% and 20–80% tables pass. |
| T10 | P0 | Add canonical target policies, preset preflight and all-path enforcement. app.py/control.py/configuration.py. | T08,T09 | Legacy, generic, admin-test, preset and automation paths respect the same cap, with partial recall failures reported. |
| T11 | P0 | Add profile/control revision guards and redacted descriptors. app.py/store.py. | T05,T08 | Conflicting edits receive a conflict response, secrets never reach panels. |
| T12 | P0 | Implement scoped SSE deltas and epoch/snapshot recovery. app.py/ha.py. | T08,T11 | Two authorized panels receive relevant updates with reconnect resync. |
| T13 | P0 | Detect HA queue overflow and resnapshot. ha.py. | T08 | Overflow marks stale and eventually produces a fresh correct snapshot. |
| T14 | P0 | Add canonical parameter leases and sequence checks. parameters.py/app.py. | T10,T11 | Competing panel rejected, late/expired gesture writes discarded. |
| T15 | P0 | Add bounded latest-value scheduler with final flush/cancel. parameters.py. | T14 | Slow transport never emits old motion after final/cancel. |
| T16 | P0 | Extend existing range rendering for horizontal/vertical faders. panel.js/html/css. | T09,T11 | Labels, readout, units and touch targets remain readable at supported sizes. |
| T17 | P0 | Add continuous input and keyboard/pointer lifecycle. panel.js. | T14,T15,T16 | Local motion is immediate, final write ordered, cancel/background sends nothing stale. |
| T18 | P0 | Patch keyed controls and show pending/busy/stale/overrange. panel.js. | T12,T17 | Active fader stays stable while unrelated controls and other panels update. |
| T19 | P0 | Build bidirectional virtual OSC fixture. tests/simulated_osc_device.py. | T02,T03 | Configurable fixed/source replies, loss, reordering and subscription expiry. |
| T20 | P0 | Qualify python-osc codec release/runtime and parser bounds. requirements candidate and test_osc.py. | T02,T19 | Exact types and malformed/future bundle handling have reproducible tests. |
| T21 | P0 | Add managed UDP socket provider. providers/osc.py. | T08,T20 | Two devices communicate independently with correct source sockets and clean shutdown. |
| T22 | P0 | Add generation, query/renewal/freshness lifecycle. providers/osc.py. | T21 | Restart/reconnect queries state and sends zero cached level writes. |
| T23 | P0 | Qualify Supervisor UDP slots and VM networking. config.yaml and deployment tests. | T02,T21 | Real HAOS path works for required feedback mode, conflicts diagnosed. |
| T24 | P0 | Add generic OSC schema, parameter capture and connection CRUD/read-only test. configuration.py/app.py/device_profiles.py. | T11,T21,T22 | Admin creates a typed multi-argument target and reviews a freshness-labeled parameter snapshot without code edits. |
| T25 | P0 | Extend existing admin forms with Connections and control list. admin.js/html/css. | T16,T24 | Technician can add/reorder a fader with mapping preview and explicit bounded test write. |
| T26 | P1 | Add dB/log mapping, silence sentinel and custom monotonic curves. mapping.py. | T09,T20 | Round-trip, mute endpoint and quantization fixtures pass. Required before profiles using these modes. |
| T27 | P1 | Add pinned profile versions, safe imports and reviewed updates. device_profiles.py/store.py. | T05,T24 | Curve changes require a displayed migration with retained old binding. |
| T28 | P1 | Qualify first physical product profile. profiles and protocol handler if needed. | T22,T23,T26,T27 | Real device values, subscriptions, mute, endpoint law and recovery recorded. Required before a physical audio pilot. |
| T29 | P1 | Build Companion HTTP/Satellite compatibility fixtures. test_companion.py. | T02,T03 | Old/new API, CAPS absence, broken subscriptions and uncertain presses modeled. |
| T30 | P1 | Add Companion connection and atomic location press. providers/companion.py. | T08,T10,T29 | Explicit configured action executes once and a lost response causes no replay. |
| T31 | P1 | Add negotiated subscriptions and feedback quality. Companion provider. | T12,T30 | Approved button text/color updates, heartbeat and reconnect work. |
| T32 | P1 | Add location selection and bounded export import. admin.js/configuration.py. | T25,T31 | Moved/deleted button prompts rebind, no duplicate control creation. |
| T33 | P1 | Qualify optional HA Companion app with copied config/export. Operational docs. | T02,T30 | Architecture, backups, port exposure and VM CPU/RAM are verified. |
| T34 | P1 | Prototype one Companion numeric contract. Companion provider/editor. | T15,T26,T28,T31 | Variable write drives actual device parameter, feedback maps back and rate is sustainable. |
| T35 | P1 | Add native HA number/switch/button/select/sensor coverage. control.py/configuration.py/panel.js. | T08,T10,T18 | Each domain's actual service and state semantics work within room scope. |
| T36 | P1 | Extend HA command event allowlist through coordinator. app.py/ha.py. | T10,T24 | Automation controls approved parameter with same limits and no raw address payload. |
| T37 | P2 | Evaluate optional MQTT discovery against small Core bridge. Planning decision update. | T28,T35,T36 | One export path selected with installation/maintenance costs and source-of-truth rules. |
| T38 | P2 | Implement selected optional entity bridge and lifecycle. New bridge module chosen by T37. | T37 | Native entities have availability, stable IDs, delete/update cleanup and no retained command replay. |
| T39 | P2 | Verify HA scenes and exclude exported-entity loops. Bridge and HA discovery. | T38 | Scene/control changes produce one bounded command, with honest physical-state quality. |
| T40 | P0 | Run browser and multi-panel HA fader pilot. tests/browser. | T13,T17,T18 | Target Safari/desktop input, latency and state-conflict tests pass. |
| T41 | P0 | Run simulated OSC failure/load pilot. test_parameters/test_osc and VM. | T19,T22,T23,T25,T40 | Proposed load envelope measured, queues bounded and restart causes no level writes. |
| T42 | P1 | Run selected physical-profile and Companion pilot. Pilot evidence. | T28,T32,T33,T34,T41 | Real-device claims, backup restore and per-provider failure isolation verified. |
| T43 | P1 | Extend existing operational docs after tested behavior. room_panel/DOCS.md. | T25,T28,T32,T42 | Technician can install/configure/restore supported functions using verified instructions. |
| T44 | P1 | Final regression and matched rollback qualification. tests and clean HAOS VM. | T35,T36,T42,T43 | Existing pairing, presets, calendars and RGBW work, rollback restores prior operation. |

## Milestones

M1 completes T01–T18 and T40 for an HA-backed continuous fader with bounded output. M2 adds T19–T25 and T41 for generic bidirectional OSC on the target HAOS network. M3 adds T26–T28 for one real qualified device. M4 adds T29–T34 and T42 for existing Companion actions and one demonstrated numeric route. M5 completes native HA domain/event compatibility and operational release tasks. Optional entity export T37–T39 can follow after the pilot establishes a concrete automation need.

Phases describe complete capability groups. P0/P1 labels express relative implementation urgency, and do not permit a physical audio deployment before T28. Companion hardware/app qualification can run earlier against a copied configuration once T02/T30 prerequisites are met.

## Review checklist before coding

Confirm the first physical product and firmware, canonical units for its level parameter, expected behavior when feedback is missing, the UDP deployment mode, the Companion instance version/module and the actual pilot load. Review parameter-wide versus per-room limits, opaque scene handling and the chosen read-on-connect default. All are concrete review inputs. This planning task has not created runtime connections, installed optional apps or issued equipment commands.
