from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from astrbot import logger
from astrbot.api import AstrBotConfig
from astrbot.api.star import Context, Star, register
from quart import jsonify

try:
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path as _get_astrbot_data_path
except ImportError:
    _get_astrbot_data_path = None

from .whatsapp_adapter import BASE_GATEWAY_CONFIG
from .whatsapp_client import GatewayProcess, WhatsAppGatewayClient


PLUGIN_NAME = "astrbot_plugin_whatsapp_adapter"
PLUGIN_DIR = Path(__file__).resolve().parent


@register(
    PLUGIN_NAME,
    "OpenCode",
    "WhatsApp Web platform adapter backed by a local Gateway process.",
    "0.2.2",
)
class WhatsAppAdapterPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = {**BASE_GATEWAY_CONFIG, **(dict(config or {}))}
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

        from .whatsapp_adapter import (  # noqa: F401
            WhatsAppPlatformAdapter,
            patch_platform_manager_hot_reload,
        )
        from .whatsapp_components import (  # noqa: F401
            WhatsAppButton,
            WhatsAppButtons,
            WhatsAppEdit,
            WhatsAppList,
            WhatsAppListRow,
            WhatsAppListSection,
            WhatsAppPoll,
        )

        patch_platform_manager_hot_reload()

    @property
    def _base_url(self) -> str:
        return f"http://{self.config['gateway_host']}:{int(self.config['gateway_port'])}"

    async def page_status(self):
        await self._ensure_page_gateway()
        try:
            status = await self.page_client.status()
            status["baseUrl"] = self._base_url
            status["plugin"] = PLUGIN_NAME
            status["gatewayHealthy"] = bool(status.get("ok", True) and status.get("ready"))
            status["configuredAuthDir"] = str(self._auth_dir())
            logger.debug("WhatsApp plugin page status requested: %s", self._safe_status(status))
            return jsonify(status)
        except Exception as exc:
            logger.warning("WhatsApp 管理页状态获取失败: %s", exc)
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
            logger.warning("WhatsApp 管理页 QR 获取失败: %s", exc)
            return jsonify({"ok": False, "error": str(exc), "baseUrl": self._base_url}), 503

    async def page_restart(self):
        await self._ensure_page_gateway()
        try:
            logger.info("WhatsApp Gateway 重启（来自管理页）")
            return jsonify(await self.page_client.restart())
        except Exception as exc:
            logger.warning("WhatsApp Gateway 重启失败（管理页）: %s", exc)
            return jsonify({"ok": False, "error": str(exc), "baseUrl": self._base_url}), 503

    async def page_logout(self):
        await self._ensure_page_gateway()
        try:
            logger.info("WhatsApp Gateway 登出（来自管理页）")
            return jsonify(await self.page_client.logout())
        except Exception as exc:
            logger.warning("WhatsApp Gateway 登出失败（管理页）: %s", exc)
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
            logger.info("WhatsApp 管理页自动启动已关闭，Gateway 需外部运行于 %s", self._base_url)
            return
        if self.page_gateway_process and self.page_gateway_process.process:
            if self.page_gateway_process.process.returncode is None:
                # Process still running but unhealthy — stop it before restarting
                logger.info("WhatsApp Gateway 进程状态异常，正在停止")
                await self.page_gateway_process.stop()
        self.page_gateway_process = GatewayProcess(
            node_executable=str(self.config["node_executable"]),
            script_path=PLUGIN_DIR / "gateway" / "whatsapp-gateway.mjs",
            host=str(self.config["gateway_host"]),
            port=int(self.config["gateway_port"]),
            auth_dir=self._auth_dir(),
            log_level=str(self.config["log_level"]),
            data_dir=self._data_dir(),
        )
        logger.info("正在启动 WhatsApp Gateway（管理页）: %s", self._base_url)
        await self.page_gateway_process.start()
        # 輪詢 Gateway 健康狀態，最長等待 30 秒
        health_client = WhatsAppGatewayClient(self._base_url, timeout=5.0)
        try:
            last_error: Exception | None = None
            for attempt in range(1, 31):
                try:
                    health = await health_client.health()
                    logger.info("WhatsApp Gateway 健康检查通过（第 %s 次）", attempt, health)
                    break
                except Exception as exc:
                    last_error = exc
                    await asyncio.sleep(1)
            else:
                logger.warning("WhatsApp Gateway 健康检查未通过: %s", last_error)
        finally:
            await health_client.close()

    def _auth_dir(self) -> Path:
        configured = str(self.config.get("auth_dir") or "").strip()
        if configured:
            return Path(configured).expanduser().resolve()
        return self._data_dir() / "whatsapp-auth"

    _migrated = False

    def _data_dir(self) -> Path:
        if not self.__class__._migrated:
            self._migrate_old_data()
            self.__class__._migrated = True
        return self._resolve_data_base() / PLUGIN_NAME

    @staticmethod
    def _resolve_data_base() -> Path:
        if _get_astrbot_data_path:
            return Path(_get_astrbot_data_path()) / "plugin_data"
        return Path.cwd() / "data"

    def _migrate_old_data(self) -> None:
        old_root = Path.cwd() / "data" / PLUGIN_NAME
        if not old_root.is_dir():
            return
        new_root = self._resolve_data_base() / PLUGIN_NAME
        if new_root.is_dir():
            return
        try:
            import shutil
            shutil.copytree(str(old_root), str(new_root), symlinks=False)
            logger.info("Migrated plugin page data from %s to %s", old_root, new_root)
        except Exception as exc:
            logger.warning("Failed to migrate old plugin page data from %s to %s: %s", old_root, new_root, exc)

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

    async def reload_config(self, new_config: dict | None = None) -> None:
        if new_config:
            self.config = {**BASE_GATEWAY_CONFIG, **dict(new_config)}
        logger.info("WhatsApp 插件配置已重载: gateway=%s", self._base_url)
        self.page_client.update_base_url(self._base_url)
        from .whatsapp_adapter import get_active_whatsapp_adapters

        for adapter in get_active_whatsapp_adapters():
            try:
                adapter._refresh_registered_commands()
                await adapter.reload(adapter._platform_config)
            except Exception as exc:
                logger.warning(
                    "Failed to propagate plugin config reload to WhatsApp adapter %s: %s",
                    getattr(adapter.meta(), "id", None),
                    exc,
                )

    async def initialize(self) -> None:
        await super().initialize()
        await self._restore_platform_adapters()

    async def _restore_platform_adapters(self) -> None:
        """After plugin reload, hot-swap adapter classes to use freshly imported code
        without disrupting Gateway connection or runtime state."""
        try:
            pm = getattr(self.context, 'platform_manager', None)
            if pm is None:
                return
            if not pm.platform_insts:
                return
            from .whatsapp_adapter import _ACTIVE_ADAPTERS, WhatsAppPlatformAdapter as NewAdapter
            platform_configs = getattr(pm, 'platforms_config', [])
            for config in platform_configs:
                if config.get('type') != 'whatsapp' or not config.get('enable', False):
                    continue
                pid = config.get('id')
                if not pid:
                    continue
                inst = self.context.get_platform_inst(pid)
                if inst is None:
                    continue
                # Only swap if the instance still carries the old (pre-reload) class
                if type(inst) is NewAdapter:
                    continue
                logger.info("正在热替换 WhatsApp 适配器类: id=%s", pid)
                # 完整終止舊執行階段，避免只取消 task 但殘留 Gateway/health task。
                try:
                    await inst.terminate()
                except Exception as exc:
                    logger.warning("终止旧 WhatsApp 适配器失败: id=%s error=%s", pid, exc)
                inst.__class__ = NewAdapter
                _ACTIVE_ADAPTERS.add(inst)
                inst.clear_errors()
                inst._stopped.clear()
                inst._reconnect_event.clear()
                inst._force_gateway_restart = True
                inst._refresh_registered_commands()
                inst._run_task = asyncio.create_task(inst.run())
                logger.info("WhatsApp 适配器运行循环已重启: id=%s", pid)
        except Exception as e:
            logger.warning("WhatsApp 适配器热替换失败: %s", e)

    async def terminate(self):
        logger.info("正在终止 WhatsApp 适配器插件（适配器由 PlatformManager 管理，保持存活）")
        await self.page_client.close()
        if self.page_gateway_process:
            await self.page_gateway_process.stop()
