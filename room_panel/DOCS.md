# Room Panel

## First run

Open Room Panel from **Settings → Apps**. The administration interface runs through Home Assistant Ingress. The app uses the Supervisor API for Home Assistant entities and commands.

1. In **Installation**, set your display name, brand colors, optional PNG/JPEG/WebP logo and preset PIN.
2. For tablets on the local network, configure a **Base URL** like `https://panel.example.net:8443` and a trusted TLS certificate and key stored under Home Assistant's `/ssl`. Use paths such as `fullchain.pem` and `privkey.pem`. Enable the tablet host port in the app's **Network** settings. Home Assistant Ingress and the tablet listener use separate ports.
3. Create a room profile from a Home Assistant area. Select devices manually or choose a Home Assistant label for automatic discovery. Explicitly add any scripts you want the panel to run.
4. Create a **Control panel** or **Door panel** for the profile and use its pairing link. Open that link in the browser or kiosk app that will run the display. Save the stable panel URL as the start page.

Each pairing link is single use and expires. Revoking a panel disconnects that tablet and requires a new link.

## Calendars

Select a calendar per room profile. **Home Assistant calendar** works with calendar entities, including Google Calendar and iCal feeds added through the appropriate Home Assistant integration. **Outlook** uses an optional Microsoft Graph connection. Enter the Entra tenant ID, client ID, and client secret in Installation. Set the room mailbox in the profile. Scope read access to the rooms intended for display.

## Wall buttons and presets

Use an event action in a Home Assistant automation to activate a Room Panel preset:

```yaml
actions:
  - event: room_panel_apply_preset
    event_data:
      profile_id: "<profile ID>"
      preset_id: "<preset ID>"
```

Find the IDs in the room profile editor. Set a starting Home Assistant scene for new presets if desired. Capturing values from a tablet uses the preset PIN. The preset keeps its configured script and transition. Use **Restore** in the room profile to return to the previous capture. Activation from an event requires the app to be running.

## Backups

Settings, profiles, presets and panel sessions live under `/data` and are included when Room Panel is selected in a Supervisor backup. The TLS files are managed separately in Home Assistant's `/ssl`.
