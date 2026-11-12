# Room Panel

Room Panel adds room-control tablets and door displays to **Home Assistant OS**. Configure each room in Home Assistant, pair its displays with individual links, and manage the installation name, colors, and logo from the app.

- **Control panels** show selected lights, media players, scripts, and presets.
- **Door displays** show availability from an optional room calendar. Tablets receive booking times and status.
- **Room profiles** use a Home Assistant area. Select entities manually or use a label to include matching devices in that area as they are added.
- **Presets** capture lights and optional volume behind a four-digit PIN. Presets can run an optional Home Assistant script, restore the previous capture, and be activated from wall-button automations.

## Install on Home Assistant OS

1. Download or clone this repository. Copy **`room_panel`** into the Home Assistant local apps folder, commonly `/addons/room_panel`. When using Home Assistant's Samba app, use the `local_apps` share.
2. In **Settings → Apps → App Store**, use the menu to check for updates. Open **Room Panel** under **Local apps**, install it, and start it. Supervisor builds the app for **amd64** or **aarch64**.
3. Select **Open web UI**. Go to **Installation** to set branding, the preset PIN, and tablet HTTPS. Then add a room profile and pair a panel.

This repository is private. The local-app installation works with a downloaded checkout. See [app documentation](room_panel/DOCS.md) for HTTPS, calendars, backups, and Home Assistant automation examples.

## Development

```sh
python -m pip install -r room_panel/requirements.txt
PYTHONPATH=room_panel python -m unittest discover -s tests -v
node --check room_panel/roompanel/static/admin.js
node --check room_panel/roompanel/static/panel.js
```

See [LICENSE](LICENSE) for the MIT license.
