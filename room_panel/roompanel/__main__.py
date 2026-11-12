"""Start the Supervisor Ingress and local tablet listeners."""

import asyncio
from contextlib import suppress
import logging
import os
from pathlib import Path
import signal
import ssl

from aiohttp import ClientSession, web

from .app import RoomPanel
from .control import Invalid
from .ha import HomeAssistant
from .store import Store

LOG = logging.getLogger(__name__)


class TabletListener:
    def __init__(self, runner, host, port, ssl_dir, dev):
        self.runner = runner
        self.host = host
        self.port = port
        self.ssl_dir = ssl_dir.resolve()
        self.dev = dev
        self.site = None
        self.context = None

    def ssl_path(self, value):
        path = (self.ssl_dir / value).resolve()
        if not path.is_relative_to(self.ssl_dir) or path == self.ssl_dir:
            raise Invalid("Certificate and key must be files inside Home Assistant's SSL folder.")
        return path

    async def configure(self, settings):
        cert, key, origin = (settings[field] for field in ("tls_cert", "tls_key", "base_url"))
        if self.dev and (not origin or origin.startswith("http://")):
            if not self.site:
                await self.start(None)
            return
        if not (cert or key or origin):
            if self.site:
                await self.site.stop()
                self.site = self.context = None
            return
        if not (cert and key and origin):
            raise Invalid("Provide the local HTTPS address, certificate and key together.")
        certificate, private_key = self.ssl_path(cert), self.ssl_path(key)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        try:
            context.load_cert_chain(certificate, private_key)
            if self.context:
                self.context.load_cert_chain(certificate, private_key)
                return
        except (OSError, ssl.SSLError) as error:
            raise Invalid("The SSL certificate or private key could not be loaded.") from error
        if self.site:
            await self.site.stop()
        await self.start(context)

    async def start(self, context):
        site = web.TCPSite(self.runner, self.host, self.port, ssl_context=context)
        try:
            await site.start()
        except OSError as error:
            self.site = self.context = None
            raise Invalid("The tablet listener could not start. Check the app's network port.") from error
        self.site, self.context = site, context


async def run():
    dev = os.getenv("ROOM_PANEL_DEV") == "1"
    host = "127.0.0.1" if dev else "0.0.0.0"
    admin_port = int(os.getenv("ROOM_PANEL_ADMIN_PORT", "8099"))
    panel_port = int(os.getenv("ROOM_PANEL_PORT", "8443"))
    data = Path(os.getenv("ROOM_PANEL_DATA", "/data"))
    store = Store(data / "roompanel.sqlite3")
    stopping = asyncio.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(signum, stopping.set)
    if dev and not store.settings().get("base_url"):
        store.save_settings({**store.settings(), "base_url": f"http://127.0.0.1:{panel_port}"})

    token = os.getenv("SUPERVISOR_TOKEN", "")
    if not token:
        LOG.warning("No Supervisor token found. Home Assistant controls will remain disconnected.")

    async with ClientSession() as session:
        ha = HomeAssistant(session, os.getenv("ROOM_PANEL_HA_WS", "ws://supervisor/core/websocket"), token)
        panel = RoomPanel(store, ha, session, dev=dev)
        ha.on_change = panel.changed
        ha.on_preset = panel.automation_preset
        admin = web.AppRunner(panel.admin_application(), access_log=None, shutdown_timeout=3)
        device = web.AppRunner(panel.panel_application(), access_log=None, shutdown_timeout=3)
        await admin.setup()
        await device.setup()
        listener = TabletListener(device, host, panel_port, Path(os.getenv("ROOM_PANEL_SSL", "/ssl")), dev)
        panel.update_listener = listener.configure
        ha_task = None
        try:
            await web.TCPSite(admin, host, admin_port).start()
            try:
                await listener.configure(panel.settings())
            except Invalid as error:
                LOG.warning("Tablet listener unavailable: %s", error)
            if token:
                ha_task = asyncio.create_task(ha.run())
            LOG.info("Room Panel administration is ready.")
            await stopping.wait()
        finally:
            if ha_task:
                ha_task.cancel()
                with suppress(asyncio.CancelledError):
                    await ha_task
            await asyncio.gather(admin.cleanup(), device.cleanup())
            store.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(run())
