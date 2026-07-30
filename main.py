from __future__ import annotations

import asyncio
import re
import shutil
import time
from pathlib import Path
from typing import Any

from astrbot import logger
from astrbot.api import AstrBotConfig
from astrbot.api.star import Context, Star, register
from astrbot.api.web import json_response

try:
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path as _get_astrbot_data_path
except ImportError:
    _get_astrbot_data_path = None

from .whatsapp_adapter import BASE_GATEWAY_CONFIG
from .whatsapp_config_policy import (
    adopt_legacy_gateway_defaults,
    set_runtime_plugin_defaults,
    set_runtime_wake_prefixes,
)
from .whatsapp_client import GatewayProcess, WhatsAppGatewayClient


PLUGIN_NAME = "astrbot_plugin_whatsapp_adapter"
PLUGIN_DIR = Path(__file__).resolve().parent


@register(
    PLUGIN_NAME,
    "casama233",
    "WhatsApp Web platform adapter backed by a local Gateway process.",
    "0.2.27",
)
class WhatsAppAdapterPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = {**BASE_GATEWAY_CONFIG, **(dict(config or {}))}
        self._sync_runtime_policy()
        self.page_client = WhatsAppGatewayClient(self._base_url)
        self.page_gateway_process: GatewayProcess | None = None
        self._runtime_cache: dict[str, Any] | None = None
        self._runtime_checked_at = 0.0
        self._runtime_lock = asyncio.Lock()
        self._page_gateway_lock = asyncio.Lock()
        logger.info(
            "WhatsApp adapter plugin loaded: gateway=%s auto_start=%s auth_dir=%s log_level=%s",
            self._base_url,
            bool(self.config.get("auto_start_gateway", True)),
            str(self._auth_dir()),
            self.config.get("log_level"),
        )

        context.register_web_api(
            f"/{PLUGIN_NAME}/runtime",
            self.page_runtime,
            ["GET"],
            "WhatsApp Gateway runtime requirements",
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
        context.register_web_api(
            f"/{PLUGIN_NAME}/session/reset",
            self.page_reset_session,
            ["POST"],
            "Reset invalid WhatsApp Web session and generate a fresh QR",
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

    async def page_runtime(self):
        try:
            return json_response(await self._runtime_requirements())
        except Exception as exc:
            logger.warning("WhatsApp 管理页运行环境检测失败: %s", exc)
            return json_response(
                {"ok": False, "ready": False, "error": str(exc)},
                status_code=503,
            )

    async def page_status(self):
        runtime: dict[str, Any] = {"ok": False, "ready": False}
        try:
            runtime = await self._runtime_requirements()
            await self._ensure_page_gateway()
            status = await self.page_client.status()
            status["baseUrl"] = self._base_url
            status["plugin"] = PLUGIN_NAME
            # Gateway liveness and WhatsApp login readiness are separate signals.
            status["gatewayHealthy"] = bool(status.get("ok", True))
            status["configuredAuthDir"] = str(self._auth_dir())
            status["runtimeRequirements"] = runtime
            logger.debug("WhatsApp 管理页状态请求: %s", self._safe_status(status))
            return json_response(status)
        except Exception as exc:
            logger.warning("WhatsApp 管理页状态获取失败: %s", exc)
            return json_response(
                {
                    "ok": False,
                    "error": str(exc),
                    "baseUrl": self._base_url,
                    "runtimeRequirements": runtime,
                },
                status_code=503,
            )

    async def page_qr(self):
        try:
            await self._ensure_page_gateway()
            qr = await self.page_client.qr()
            logger.debug(
                "WhatsApp plugin page QR requested: ready=%s status=%s has_qr=%s",
                qr.get("ready"),
                qr.get("status"),
                bool(qr.get("qr") or qr.get("qrDataUrl")),
            )
            return json_response(qr)
        except Exception as exc:
            logger.warning("WhatsApp 管理页 QR 获取失败: %s", exc)
            return json_response(
                {"ok": False, "error": str(exc), "baseUrl": self._base_url},
                status_code=503,
            )

    async def page_restart(self):
        try:
            await self._ensure_page_gateway()
            logger.info("WhatsApp Gateway 重启（来自管理页）")
            return json_response(await self.page_client.restart())
        except Exception as exc:
            logger.warning("WhatsApp Gateway 重启失败（管理页）: %s", exc)
            return json_response(
                {"ok": False, "error": str(exc), "baseUrl": self._base_url},
                status_code=503,
            )

    async def page_logout(self):
        try:
            await self._ensure_page_gateway()
            logger.info("WhatsApp Gateway 登出（来自管理页）")
            return json_response(await self.page_client.logout())
        except Exception as exc:
            logger.warning("WhatsApp Gateway 登出失败（管理页）: %s", exc)
            return json_response(
                {"ok": False, "error": str(exc), "baseUrl": self._base_url},
                status_code=503,
            )

    async def page_reset_session(self):
        try:
            await self._ensure_page_gateway()
            logger.info("WhatsApp 登录 session 重建（来自管理页）")
            return json_response(await self.page_client.reset_session())
        except Exception as exc:
            logger.warning("WhatsApp 登录 session 重建失败（管理页）: %s", exc)
            return json_response(
                {"ok": False, "error": str(exc), "baseUrl": self._base_url},
                status_code=503,
            )

    async def _runtime_requirements(self) -> dict[str, Any]:
        """Return a cached, side-effect-free Gateway runtime preflight."""
        now = time.monotonic()
        if self._runtime_cache is not None and now - self._runtime_checked_at < 300:
            return self._runtime_cache

        async with self._runtime_lock:
            now = time.monotonic()
            if self._runtime_cache is not None and now - self._runtime_checked_at < 300:
                return self._runtime_cache

            configured_node = str(self.config.get("node_executable") or "node").strip()
            node_path = shutil.which(configured_node)
            node_version: str | None = None
            node_major: int | None = None
            node_error: str | None = None
            if node_path:
                try:
                    process = await asyncio.create_subprocess_exec(
                        node_path,
                        "--version",
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=3)
                    output = (stdout or stderr).decode(errors="replace").strip()
                    if process.returncode == 0:
                        node_version = output
                        match = re.match(r"v?(\d+)", output)
                        if match:
                            node_major = int(match.group(1))
                    else:
                        node_error = output or f"exit code {process.returncode}"
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
                    node_error = "version check timed out"
                except OSError as exc:
                    node_error = str(exc)

            npm_path = shutil.which("npm")
            dependencies_installed = (
                PLUGIN_DIR / "node_modules" / "@whiskeysockets" / "baileys"
            ).is_dir()
            node_supported = node_major is not None and node_major >= 20
            ready = bool(
                node_path
                and node_supported
                and (dependencies_installed or npm_path)
            )

            if not node_path:
                message = f"找不到 Node.js：{configured_node}"
            elif node_error:
                message = f"Node.js 无法执行：{node_error}"
            elif node_major is None:
                message = f"无法识别 Node.js 版本：{node_version or '无输出'}"
            elif not node_supported:
                message = f"需要 Node.js 20+，当前为 {node_version}"
            elif not dependencies_installed and not npm_path:
                message = "尚未安装 Baileys 依赖，且找不到 npm"
            elif not dependencies_installed:
                message = "运行环境可用；首次启动会自动执行 npm install --omit=dev"
            else:
                message = "Node.js 与 Baileys 依赖均已就绪"

            result = {
                "ok": True,
                "ready": ready,
                "minimumNodeMajor": 20,
                "node": {
                    "configured": configured_node,
                    "path": node_path,
                    "version": node_version,
                    "major": node_major,
                    "supported": node_supported,
                    "error": node_error,
                },
                "npm": {"path": npm_path, "available": bool(npm_path)},
                "dependenciesInstalled": dependencies_installed,
                "message": message,
            }
            self._runtime_cache = result
            self._runtime_checked_at = time.monotonic()
            return result

    async def _ensure_page_gateway(self) -> None:
        # Status, QR, and action routes may overlap while the Gateway is slow to
        # start. Keep the health-check/start sequence single-flight.
        async with self._page_gateway_lock:
            await self._ensure_page_gateway_unlocked()

    async def _ensure_page_gateway_unlocked(self) -> None:
        await self.page_client.start()
        try:
            health = await self.page_client.health()
            logger.debug("WhatsApp Gateway 已就绪（管理页）: %s", self._safe_status(health))
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
        return Path.cwd() / "data" / "plugin_data"

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
            logger.info("已迁移管理页数据: %s → %s", old_root, new_root)
        except Exception as exc:
            logger.warning("迁移管理页数据失败: %s → %s: %s", old_root, new_root, exc)

    def _root_config(self) -> dict[str, Any]:
        try:
            config = self.context.get_config()
            return dict(config or {})
        except Exception:
            return {}

    def _platform_configs(self) -> list[dict[str, Any]]:
        manager = getattr(self.context, "platform_manager", None)
        configs = getattr(manager, "platforms_config", None)
        return list(configs or [])

    def _adopt_legacy_platform_gateway_defaults(self) -> None:
        effective, migrated = adopt_legacy_gateway_defaults(
            self.config,
            self._platform_configs(),
        )
        self.config = effective
        if migrated:
            logger.warning(
                "已从旧 WhatsApp 平台实例迁移 Gateway 配置到本次运行的插件全局配置: keys=%s。请在插件配置页确认后保存。",
                sorted(migrated),
            )

    def _sync_runtime_policy(self) -> None:
        set_runtime_plugin_defaults(self.config)
        set_runtime_wake_prefixes(self._root_config().get("wake_prefix", ["/"]))

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
        self._adopt_legacy_platform_gateway_defaults()
        self._sync_runtime_policy()
        logger.info("WhatsApp 插件配置已重载: gateway=%s", self._base_url)
        self.page_client.update_base_url(self._base_url)
        await self._reload_active_adapters()

    async def _reload_active_adapters(self) -> None:
        from .whatsapp_adapter import get_active_whatsapp_adapters

        for adapter in get_active_whatsapp_adapters():
            try:
                await adapter.reload(adapter._platform_config)
            except Exception as exc:
                logger.warning(
                    "Failed to propagate plugin config reload to WhatsApp adapter %s: %s",
                    getattr(adapter.meta(), "id", None),
                    exc,
                )

    async def initialize(self) -> None:
        await super().initialize()
        self._adopt_legacy_platform_gateway_defaults()
        self._sync_runtime_policy()
        self.page_client.update_base_url(self._base_url)
        await self._restore_platform_adapters()
        await self._reload_active_adapters()

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
            from .whatsapp_adapter import sanitize_whatsapp_platform_config
            from .whatsapp_config_policy import extract_legacy_command_prefix
            platform_configs = getattr(pm, 'platforms_config', [])
            for idx, config in enumerate(platform_configs):
                if config.get('type') != 'whatsapp' or not config.get('enable', False):
                    continue
                sanitized_config = sanitize_whatsapp_platform_config(config)
                if sanitized_config != config:
                    platform_configs[idx] = sanitized_config
                    config.clear()
                    config.update(sanitized_config)
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
                inst._platform_config = sanitized_config
                inst._platform_settings = self.context.get_config().get("platform_settings", {})
                inst.config = inst._merged_config(sanitized_config)
                inst.client.update_base_url(inst._base_url)
                inst._legacy_command_prefix = extract_legacy_command_prefix(sanitized_config)
                inst._registered_commands = []
                inst._refresh_registered_commands()
                inst._ensure_send_buffer_state()
                inst.clear_errors()
                inst._stopped.clear()
                inst._reconnect_event.clear()
                inst._force_gateway_restart = True
                inst._restarting = False
                inst._run_task = asyncio.create_task(inst.run())
                logger.info("WhatsApp 适配器运行循环已重启: id=%s", pid)
        except Exception as e:
            logger.warning("WhatsApp 适配器热替换失败: %s", e)

    async def terminate(self):
        logger.info("正在终止 WhatsApp 适配器插件（适配器由 PlatformManager 管理，保持存活）")
        await self.page_client.close()
        if self.page_gateway_process:
            await self.page_gateway_process.stop()
