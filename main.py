from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from astrbot.api import AstrBotConfig
from astrbot.api.star import Context, Star, register
from quart import jsonify

from .whatsapp_client import GatewayProcess, WhatsAppGatewayClient


PLUGIN_NAME = "astrbot_plugin_whatsapp_adapter"
PLUGIN_DIR = Path(__file__).resolve().parent


DEFAULT_PAGE_CONFIG: dict[str, Any] = {
    "gateway_host": "127.0.0.1",
    "gateway_port": 18789,
    "auto_start_gateway": True,
    "node_executable": "node",
    "auth_dir": "",
    "log_level": "info",
}


@register(
    PLUGIN_NAME,
    "OpenCode",
    "WhatsApp Web platform adapter backed by a local Gateway process.",
    "0.1.0",
)
class WhatsAppAdapterPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = {**DEFAULT_PAGE_CONFIG, **(dict(config or {}))}
        self.page_client = WhatsAppGatewayClient(self._base_url)
        self.page_gateway_process: GatewayProcess | None = None

        context.register_web_api(
            f"/{PLUGIN_NAME}/status",
            self.page_status,
            ["GET"],
            "WhatsApp Gateway status",
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/qr",
            self.page_qr,
            ["GET"],
            "WhatsApp login QR",
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/restart",
            self.page_restart,
            ["POST"],
            "Restart WhatsApp Gateway socket",
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/logout",
            self.page_logout,
            ["POST"],
            "Logout WhatsApp Web session",
        )

        from .whatsapp_adapter import WhatsAppPlatformAdapter  # noqa: F401

    @property
    def _base_url(self) -> str:
        return f"http://{self.config['gateway_host']}:{int(self.config['gateway_port'])}"

    async def page_status(self):
        await self._ensure_page_gateway()
        try:
            return jsonify(await self.page_client.status())
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc), "baseUrl": self._base_url}), 503

    async def page_qr(self):
        await self._ensure_page_gateway()
        try:
            return jsonify(await self.page_client.qr())
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc), "baseUrl": self._base_url}), 503

    async def page_restart(self):
        await self._ensure_page_gateway()
        try:
            return jsonify(await self.page_client.restart())
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc), "baseUrl": self._base_url}), 503

    async def page_logout(self):
        await self._ensure_page_gateway()
        try:
            return jsonify(await self.page_client.logout())
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc), "baseUrl": self._base_url}), 503

    async def _ensure_page_gateway(self) -> None:
        await self.page_client.start()
        if not self.config.get("auto_start_gateway", True):
            return
        if self.page_gateway_process and self.page_gateway_process.process:
            if self.page_gateway_process.process.returncode is None:
                return
        self.page_gateway_process = GatewayProcess(
            node_executable=str(self.config["node_executable"]),
            script_path=PLUGIN_DIR / "gateway" / "whatsapp-gateway.mjs",
            host=str(self.config["gateway_host"]),
            port=int(self.config["gateway_port"]),
            auth_dir=self._auth_dir(),
            log_level=str(self.config["log_level"]),
            data_dir=self._data_dir(),
        )
        await self.page_gateway_process.start()
        await asyncio.sleep(1)

    def _auth_dir(self) -> Path:
        configured = str(self.config.get("auth_dir") or "").strip()
        if configured:
            return Path(configured).expanduser().resolve()
        return self._data_dir() / "whatsapp-auth"

    def _data_dir(self) -> Path:
        return Path.cwd() / "data" / PLUGIN_NAME

    async def terminate(self):
        await self.page_client.close()
        if self.page_gateway_process:
            await self.page_gateway_process.stop()
