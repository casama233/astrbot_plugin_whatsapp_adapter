from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from astrbot import logger
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
        logger.info(
            "WhatsApp adapter plugin loaded: gateway=%s auto_start=%s auth_dir=%s log_level=%s",
            self._base_url,
            bool(self.config.get("auto_start_gateway", True)),
            str(self._auth_dir()),
            self.config.get("log_level"),
        )

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
        self._enable_whatsapp_pre_ack()

    @property
    def _base_url(self) -> str:
        return f"http://{self.config['gateway_host']}:{int(self.config['gateway_port'])}"

    async def page_status(self):
        await self._ensure_page_gateway()
        try:
            status = await self.page_client.status()
            logger.debug("WhatsApp plugin page status requested: %s", self._safe_status(status))
            return jsonify(status)
        except Exception as exc:
            logger.warning("WhatsApp plugin page status failed: %s", exc)
            return jsonify({"ok": False, "error": str(exc), "baseUrl": self._base_url}), 503

    async def page_qr(self):
        await self._ensure_page_gateway()
        try:
            qr = await self.page_client.qr()
            logger.debug(
                "WhatsApp plugin page QR requested: ready=%s status=%s has_qr=%s",
                qr.get("ready"),
                qr.get("status"),
                bool(qr.get("qr") or qr.get("qrDataUrl")),
            )
            return jsonify(qr)
        except Exception as exc:
            logger.warning("WhatsApp plugin page QR failed: %s", exc)
            return jsonify({"ok": False, "error": str(exc), "baseUrl": self._base_url}), 503

    async def page_restart(self):
        await self._ensure_page_gateway()
        try:
            logger.info("WhatsApp Gateway restart requested from plugin page")
            return jsonify(await self.page_client.restart())
        except Exception as exc:
            logger.warning("WhatsApp Gateway restart failed from plugin page: %s", exc)
            return jsonify({"ok": False, "error": str(exc), "baseUrl": self._base_url}), 503

    async def page_logout(self):
        await self._ensure_page_gateway()
        try:
            logger.info("WhatsApp Gateway logout requested from plugin page")
            return jsonify(await self.page_client.logout())
        except Exception as exc:
            logger.warning("WhatsApp Gateway logout failed from plugin page: %s", exc)
            return jsonify({"ok": False, "error": str(exc), "baseUrl": self._base_url}), 503

    async def _ensure_page_gateway(self) -> None:
        await self.page_client.start()
        try:
            health = await self.page_client.health()
            logger.debug("WhatsApp Gateway already healthy for plugin page: %s", self._safe_status(health))
            return
        except Exception:
            pass
        if not self.config.get("auto_start_gateway", True):
            logger.info("WhatsApp plugin page auto-start disabled; Gateway must already be running at %s", self._base_url)
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
        logger.info("Starting WhatsApp Gateway for plugin page at %s", self._base_url)
        await self.page_gateway_process.start()
        await asyncio.sleep(1)

    def _auth_dir(self) -> Path:
        configured = str(self.config.get("auth_dir") or "").strip()
        if configured:
            return Path(configured).expanduser().resolve()
        return self._data_dir() / "whatsapp-auth"

    def _data_dir(self) -> Path:
        return Path.cwd() / "data" / PLUGIN_NAME

    def _safe_status(self, status: dict[str, Any]) -> dict[str, Any]:
        safe = dict(status)
        if "config" in safe and isinstance(safe["config"], dict):
            config = dict(safe["config"])
            if "allowFrom" in config:
                config["allowFrom"] = f"<{len(config.get('allowFrom') or [])} entries>"
            if "groupAllowFrom" in config:
                config["groupAllowFrom"] = f"<{len(config.get('groupAllowFrom') or [])} entries>"
            if "groups" in config:
                config["groups"] = f"<{len(config.get('groups') or [])} entries>"
            safe["config"] = config
        if safe.get("qr"):
            safe["qr"] = "<hidden>"
        if safe.get("qrDataUrl"):
            safe["qrDataUrl"] = "<hidden>"
        return safe

    def _enable_whatsapp_pre_ack(self) -> None:
        try:
            from astrbot.core.pipeline.preprocess_stage.stage import PreProcessStage
        except Exception as exc:
            logger.debug("WhatsApp pre-ack patch skipped; preprocess stage unavailable: %s", exc)
            return

        constants = PreProcessStage.process.__code__.co_consts
        for const in constants:
            if isinstance(const, frozenset) and {"telegram", "lark", "discord"}.issubset(const):
                if "whatsapp" in const:
                    return
                PreProcessStage.process.__code__ = PreProcessStage.process.__code__.replace(
                    co_consts=tuple(
                        (const | {"whatsapp"}) if item is const else item
                        for item in constants
                    )
                )
                logger.info("WhatsApp pre-ack emoji support enabled")
                return
        logger.debug("WhatsApp pre-ack patch skipped; supported platform set not found")

    async def terminate(self):
        logger.info("Terminating WhatsApp adapter plugin")
        await self.page_client.close()
        if self.page_gateway_process:
            await self.page_gateway_process.stop()
