from __future__ import annotations

import asyncio
import json
import re
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from astrbot import logger
from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.api.web import json_response, request

try:
    from astrbot.core.utils.astrbot_path import (
        get_astrbot_data_path as _get_astrbot_data_path,
    )
except ImportError:
    _get_astrbot_data_path = None

from .plugin_updater import (
    PluginUpdateError,
    ReleaseDetails,
    atomic_swap_plugin,
    download_release_archive,
    extract_validated_release,
    fetch_latest_release,
    is_newer_version,
    restore_plugin_backup,
)
from .whatsapp_adapter import BASE_GATEWAY_CONFIG
from .whatsapp_client import (
    GatewayProcess,
    WhatsAppGatewayClient,
    WhatsAppGatewayError,
)
from .whatsapp_config_policy import (
    adopt_legacy_gateway_defaults,
    set_runtime_plugin_defaults,
    set_runtime_wake_prefixes,
)
from .whatsapp_ai_tools import (
    WhatsAppToolRejected,
    create_event,
    create_poll,
    share_contact,
)

PLUGIN_NAME = "astrbot_plugin_whatsapp_adapter"
PLUGIN_VERSION = "0.2.34"
PLUGIN_DIR = Path(__file__).resolve().parent
_UPDATE_BUSY_PHASES = {
    "queued",
    "checking",
    "downloading",
    "validating",
    "installing_dependencies",
    "installing",
    "reloading",
    "rolling_back",
}
_PAIR_CODE_ERROR_MESSAGES = {
    409: "当前 WhatsApp 已登录，无需再生成配对码。",
    429: "配对码请求过于频繁，请稍后再试。",
    501: "当前 Gateway 运行环境不支持手机号配对码。",
    503: "WhatsApp 登录连接尚未准备好，请等待二维码出现或重建登录会话。",
}


@register(
    PLUGIN_NAME,
    "casama233",
    "WhatsApp Web platform adapter backed by a local Gateway process.",
    PLUGIN_VERSION,
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
        self._update_lock = asyncio.Lock()
        self._update_task: asyncio.Task[None] | None = None
        self._latest_release_cache: ReleaseDetails | None = None
        self._latest_release_checked_at = 0.0
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
            f"/{PLUGIN_NAME}/pair-code",
            self.page_pair_code,
            ["POST"],
            "Generate a WhatsApp phone pairing code through the authenticated Dashboard API",
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
        context.register_web_api(
            f"/{PLUGIN_NAME}/update/status",
            self.page_update_status,
            ["GET"],
            "WhatsApp adapter independent update status",
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/update/check",
            self.page_update_check,
            ["POST"],
            "Check GitHub Releases independently of the plugin marketplace",
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/update/install",
            self.page_update_install,
            ["POST"],
            "Safely install the latest WhatsApp adapter GitHub Release",
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

    @filter.llm_tool(name="whatsapp_create_poll")
    async def whatsapp_create_poll(
        self,
        event: AstrMessageEvent,
        question: str,
        options: list[str],
        selectable_count: int = 1,
    ) -> str:
        """在当前 WhatsApp 会话发送原生投票；不得用于其他平台或指定其他收件人。

        Args:
            question(string): 投票问题，最多 255 个字符。
            options(array[string]): 2 到 12 个不重复的投票选项。
            selectable_count(number): 每人可选数量；1 为单选，0 为多选。
        """
        try:
            await create_poll(event, question, options, selectable_count)
        except WhatsAppToolRejected as exc:
            return f"拒绝发送 WhatsApp 投票：{exc}"
        return "已在当前 WhatsApp 会话发送原生投票。"

    @filter.llm_tool(name="whatsapp_share_contact")
    async def whatsapp_share_contact(
        self,
        event: AstrMessageEvent,
        display_name: str,
        phone_number: str,
        organization: str = "",
    ) -> str:
        """在当前 WhatsApp 会话分享一个原生联系人名片；不会更改收件会话。

        Args:
            display_name(string): 联系人的显示姓名。
            phone_number(string): 含国家或地区代码的电话号码，例如 +85212345678。
            organization(string): 可选的公司或组织名称。
        """
        try:
            await share_contact(
                event,
                display_name,
                phone_number,
                organization,
            )
        except WhatsAppToolRejected as exc:
            return f"拒绝分享 WhatsApp 联系人：{exc}"
        return "已在当前 WhatsApp 会话分享原生联系人名片。"

    @filter.llm_tool(name="whatsapp_create_event")
    async def whatsapp_create_event(
        self,
        event: AstrMessageEvent,
        name: str,
        start_time: str,
        end_time: str = "",
        description: str = "",
        location_name: str = "",
        location_address: str = "",
        extra_guests_allowed: bool = False,
    ) -> str:
        """在当前 WhatsApp 会话建立原生活动；时间必须包含明确时区。

        Args:
            name(string): 活动名称。
            start_time(string): 含时区的 ISO 8601 开始时间，例如 2026-08-15T09:00:00+08:00。
            end_time(string): 可选的含时区 ISO 8601 结束时间，必须晚于开始时间。
            description(string): 可选的活动说明。
            location_name(string): 可选的地点名称。
            location_address(string): 可选的地点地址。
            extra_guests_allowed(boolean): 是否允许参与者携带额外来宾。
        """
        try:
            await create_event(
                event,
                name,
                start_time,
                end_time,
                description,
                location_name,
                location_address,
                extra_guests_allowed,
            )
        except WhatsAppToolRejected as exc:
            return f"拒绝建立 WhatsApp 活动：{exc}"
        return "已在当前 WhatsApp 会话建立原生活动。"

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

    async def page_pair_code(self):
        """Generate a pairing code without logging the phone number or code."""
        try:
            payload = await request.json(default={})
            if not isinstance(payload, dict):
                return json_response(
                    {"ok": False, "error": "请求数据必须是 JSON 对象。"},
                    status_code=400,
                )
            phone = payload.get("phone")
            if not isinstance(phone, str) or not re.fullmatch(
                r"\+?[1-9][0-9]{6,14}",
                phone,
            ):
                return json_response(
                    {
                        "ok": False,
                        "error": "手机号必须包含国家或地区代码，并使用 7 到 15 位数字。",
                    },
                    status_code=400,
                )

            await self._ensure_page_gateway()
            result = await self.page_client.pair_code(phone)
            code = result.get("code")
            if not isinstance(code, str) or not re.fullmatch(
                r"[A-Za-z0-9-]{4,32}",
                code,
            ):
                logger.warning("WhatsApp Gateway returned an invalid pairing-code response")
                return json_response(
                    {"ok": False, "error": "Gateway 未返回有效配对码。"},
                    status_code=502,
                )
            logger.info("WhatsApp 手机号配对码已安全生成")
            return json_response({"ok": True, "code": code})
        except WhatsAppGatewayError as exc:
            gateway_status = int(exc.status_code or 0)
            status_code = gateway_status if gateway_status in _PAIR_CODE_ERROR_MESSAGES else 502
            logger.warning(
                "WhatsApp 手机号配对码生成失败: gateway_status=%s",
                gateway_status or "unknown",
            )
            return json_response(
                {
                    "ok": False,
                    "error": _PAIR_CODE_ERROR_MESSAGES.get(
                        status_code,
                        "Gateway 暂时无法生成配对码，请稍后重试。",
                    ),
                },
                status_code=status_code,
            )
        except Exception:
            logger.warning("WhatsApp 手机号配对码请求处理失败")
            return json_response(
                {"ok": False, "error": "配对码请求处理失败，请稍后重试。"},
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

    async def page_update_status(self):
        state = self._read_update_state()
        try:
            state_age = time.time() - float(state.get("updatedAt") or 0)
        except (TypeError, ValueError):
            state_age = float("inf")
        if (
            state.get("phase") == "reloading"
            and state.get("targetVersion") == PLUGIN_VERSION
        ):
            state = self._write_update_state(
                {
                    **state,
                    "phase": "completed",
                    "message": f"已更新到 v{PLUGIN_VERSION}",
                    "completedAt": time.time(),
                }
            )
        elif (
            state.get("phase") in _UPDATE_BUSY_PHASES
            and not self._update_operation_active()
            and state_age > 120
        ):
            state = self._write_update_state(
                {
                    **state,
                    "phase": "failed",
                    "message": "上次更新任务意外中断，插件目录未切换或已由启动流程恢复。请重新检查更新。",
                    "error": "update task interrupted",
                    "failedAt": time.time(),
                }
            )
        return json_response(self._update_payload(state))

    async def page_update_check(self):
        if self._update_operation_active():
            return json_response(
                self._update_payload(self._read_update_state()),
                status_code=409,
            )
        async with self._update_lock:
            self._write_update_state(
                {
                    "phase": "checking",
                    "message": "正在直接检查 GitHub Release…",
                    "startedAt": time.time(),
                }
            )
            try:
                release = await self._latest_release(force=True)
                available = is_newer_version(release.version, PLUGIN_VERSION)
                state = self._write_update_state(
                    {
                        "phase": "available" if available else "up_to_date",
                        "message": (
                            f"发现新版本 v{release.version}"
                            if available
                            else f"当前已是最新版本 v{PLUGIN_VERSION}"
                        ),
                        "checkedAt": time.time(),
                        "release": release.as_dict(),
                        "targetVersion": release.version,
                    }
                )
                return json_response(self._update_payload(state))
            except Exception as exc:
                message = self._safe_update_error(exc)
                logger.warning("WhatsApp 插件独立更新检查失败: %s", message)
                state = self._write_update_state(
                    {
                        "phase": "check_failed",
                        "message": message,
                        "error": message,
                        "checkedAt": time.time(),
                    }
                )
                return json_response(self._update_payload(state), status_code=503)

    async def page_update_install(self):
        if self._update_operation_active():
            return json_response(
                self._update_payload(self._read_update_state()),
                status_code=409,
            )
        state = self._write_update_state(
            {
                "phase": "queued",
                "message": "更新任务已建立，正在准备检查 GitHub Release…",
                "startedAt": time.time(),
            }
        )
        self._update_task = asyncio.create_task(self._perform_update())
        return json_response(self._update_payload(state), status_code=202)

    async def _perform_update(self) -> None:
        async with self._update_lock:
            work_dir: Path | None = None
            backup_dir: Path | None = None
            swapped = False
            try:
                release = await self._latest_release(force=True)
                if not is_newer_version(release.version, PLUGIN_VERSION):
                    self._write_update_state(
                        {
                            "phase": "up_to_date",
                            "message": f"当前已是最新版本 v{PLUGIN_VERSION}",
                            "checkedAt": time.time(),
                            "release": release.as_dict(),
                            "targetVersion": release.version,
                        }
                    )
                    return

                manager = getattr(self.context, "_star_manager", None)
                if manager is None or not hasattr(manager, "reload"):
                    raise PluginUpdateError("当前 AstrBot 未提供可用的插件重载接口")

                parent = PLUGIN_DIR.parent
                work_dir = Path(
                    tempfile.mkdtemp(prefix=f".{PLUGIN_NAME}-update-", dir=str(parent))
                )
                archive_path = work_dir / "release.zip"
                staged_dir = work_dir / "plugin"
                backup_dir = parent / (
                    f".{PLUGIN_NAME}-backup-{PLUGIN_VERSION}-{uuid.uuid4().hex[:8]}"
                )

                self._write_update_state(
                    {
                        "phase": "downloading",
                        "message": f"正在下载 v{release.version}…",
                        "startedAt": time.time(),
                        "release": release.as_dict(),
                        "targetVersion": release.version,
                    }
                )
                sha256 = await download_release_archive(release.download_url, archive_path)

                self._write_update_state(
                    {
                        "phase": "validating",
                        "message": "正在验证 Release 结构、版本与兼容范围…",
                        "startedAt": time.time(),
                        "release": release.as_dict(),
                        "targetVersion": release.version,
                        "sha256": sha256,
                    }
                )
                metadata = await asyncio.to_thread(
                    extract_validated_release,
                    archive_path,
                    staged_dir,
                    expected_name=PLUGIN_NAME,
                    expected_version=release.version,
                )
                valid, reason = manager._validate_astrbot_version_specifier(
                    metadata.get("astrbot_version")
                )
                if not valid:
                    raise PluginUpdateError(reason or "新版本不兼容当前 AstrBot")

                self._write_update_state(
                    {
                        "phase": "installing_dependencies",
                        "message": "正在预装并验证新版本依赖…",
                        "startedAt": time.time(),
                        "release": release.as_dict(),
                        "targetVersion": release.version,
                        "sha256": sha256,
                    }
                )
                await self._prepare_staged_plugin(staged_dir, manager)

                self._write_update_state(
                    {
                        "phase": "installing",
                        "message": "依赖验证通过，正在原子切换插件版本…",
                        "startedAt": time.time(),
                        "release": release.as_dict(),
                        "targetVersion": release.version,
                        "sha256": sha256,
                    }
                )
                await asyncio.to_thread(
                    atomic_swap_plugin,
                    PLUGIN_DIR,
                    staged_dir,
                    backup_dir,
                )
                swapped = True
                try:
                    self._write_update_state(
                        {
                            "phase": "reloading",
                            "message": f"v{release.version} 已安装，正在重载插件…",
                            "startedAt": time.time(),
                            "release": release.as_dict(),
                            "targetVersion": release.version,
                            "sha256": sha256,
                        }
                    )
                except Exception:
                    failed_dir = backup_dir.with_name(f"{backup_dir.name}-failed-new")
                    await asyncio.to_thread(
                        restore_plugin_backup,
                        PLUGIN_DIR,
                        backup_dir,
                        failed_dir,
                    )
                    swapped = False
                    raise
                await self._reload_after_update(manager, release, backup_dir, work_dir)
            except Exception as exc:
                message = self._safe_update_error(exc)
                logger.exception("WhatsApp 插件手动更新失败: %s", message)
                if not swapped and work_dir is not None:
                    await asyncio.to_thread(shutil.rmtree, work_dir, True)
                self._write_update_state(
                    {
                        "phase": "failed",
                        "message": message,
                        "error": message,
                        "failedAt": time.time(),
                    }
                )

    def _update_operation_active(self) -> bool:
        return self._update_lock.locked() or bool(
            self._update_task and not self._update_task.done()
        )

    async def _latest_release(self, *, force: bool = False) -> ReleaseDetails:
        if (
            not force
            and self._latest_release_cache is not None
            and time.monotonic() - self._latest_release_checked_at < 300
        ):
            return self._latest_release_cache
        release = await fetch_latest_release(PLUGIN_VERSION)
        self._latest_release_cache = release
        self._latest_release_checked_at = time.monotonic()
        return release

    async def _prepare_staged_plugin(self, staged_dir: Path, manager: Any) -> None:
        await manager._ensure_plugin_requirements(str(staged_dir), PLUGIN_NAME)

        package_json = staged_dir / "package.json"
        package_lock = staged_dir / "package-lock.json"
        if package_json.is_file() or package_lock.is_file():
            if not package_json.is_file() or not package_lock.is_file():
                raise PluginUpdateError("新版本的 Node package.json 与 lockfile 不完整")
            npm = shutil.which("npm")
            if not npm:
                raise PluginUpdateError("找不到 npm，无法预装新版本 Gateway 依赖")
            await self._run_update_command(
                "npm 生产依赖安装",
                npm,
                "ci",
                "--omit=dev",
                "--no-audit",
                "--no-fund",
                cwd=staged_dir,
                timeout=900,
            )

        await self._run_update_command(
            "Python 语法验证",
            sys.executable,
            "-m",
            "compileall",
            "-q",
            "-x",
            r"(^|/)(node_modules|\.git)(/|$)",
            ".",
            cwd=staged_dir,
            timeout=120,
        )
        gateway_script = staged_dir / "gateway" / "whatsapp-gateway.mjs"
        if gateway_script.is_file():
            node = shutil.which(str(self.config.get("node_executable") or "node"))
            if not node:
                raise PluginUpdateError("找不到 Node.js，无法验证新版本 Gateway")
            await self._run_update_command(
                "Gateway 语法验证",
                node,
                "--check",
                str(gateway_script),
                cwd=staged_dir,
                timeout=60,
            )

    @staticmethod
    async def _run_update_command(
        label: str,
        *command: str,
        cwd: Path,
        timeout: float,
    ) -> None:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            process.kill()
            await process.wait()
            raise PluginUpdateError(f"{label}超时") from exc
        output = stdout.decode(errors="replace").strip()
        if process.returncode != 0:
            detail = output[-2000:] if output else f"exit code {process.returncode}"
            raise PluginUpdateError(f"{label}失败：{detail}")

    async def _reload_after_update(
        self,
        manager: Any,
        release: ReleaseDetails,
        backup_dir: Path,
        work_dir: Path,
    ) -> None:
        await asyncio.sleep(1)
        try:
            success, error = await manager.reload(PLUGIN_NAME)
            if not success:
                raise PluginUpdateError(error or "AstrBot 重载新版本失败")
            self._write_update_state(
                {
                    "phase": "completed",
                    "message": f"已成功更新到 v{release.version}",
                    "targetVersion": release.version,
                    "release": release.as_dict(),
                    "completedAt": time.time(),
                }
            )
            await asyncio.to_thread(shutil.rmtree, backup_dir, True)
            await asyncio.to_thread(shutil.rmtree, work_dir, True)
            logger.info("WhatsApp 插件已通过内置管理页更新到 v%s", release.version)
            return
        except Exception as exc:
            reload_error = self._safe_update_error(exc)
            logger.exception("WhatsApp 插件新版本重载失败，正在回滚: %s", reload_error)

        self._write_update_state(
            {
                "phase": "rolling_back",
                "message": "新版本重载失败，正在恢复旧版本…",
                "targetVersion": release.version,
                "error": reload_error,
            }
        )
        failed_dir = backup_dir.with_name(f"{backup_dir.name}-failed-new")
        rollback_error: str | None = None
        try:
            await asyncio.to_thread(
                restore_plugin_backup,
                PLUGIN_DIR,
                backup_dir,
                failed_dir,
            )
            root_dir = PLUGIN_DIR.name
            if root_dir in getattr(manager, "failed_plugin_dict", {}):
                success, error = await manager.reload_failed_plugin(root_dir)
            elif manager.context.get_registered_star(PLUGIN_NAME):
                success, error = await manager.reload(PLUGIN_NAME)
            else:
                success, error = await manager.load(specified_dir_name=root_dir)
            if not success:
                raise PluginUpdateError(error or "旧版本重新载入失败")
        except Exception as exc:
            rollback_error = self._safe_update_error(exc)
            logger.exception("WhatsApp 插件自动回滚失败: %s", rollback_error)
        finally:
            await asyncio.to_thread(shutil.rmtree, work_dir, True)

        message = f"新版本重载失败，已自动恢复 v{PLUGIN_VERSION}：{reload_error}"
        if rollback_error:
            message = f"更新与自动回滚均失败：{reload_error}；回滚错误：{rollback_error}"
        self._write_update_state(
            {
                "phase": "failed",
                "message": message,
                "targetVersion": release.version,
                "error": reload_error,
                "rolledBack": rollback_error is None,
                "rollbackError": rollback_error,
                "failedAt": time.time(),
            }
        )

    def _update_state_path(self) -> Path:
        return self._data_dir() / "update-state.json"

    def _read_update_state(self) -> dict[str, Any]:
        try:
            value = json.loads(self._update_state_path().read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    def _write_update_state(self, state: dict[str, Any]) -> dict[str, Any]:
        normalized = {**state, "updatedAt": time.time()}
        path = self._update_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f".tmp-{uuid.uuid4().hex[:8]}")
        temporary.write_text(
            json.dumps(normalized, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary.replace(path)
        return normalized

    @staticmethod
    def _safe_update_error(exc: BaseException) -> str:
        if isinstance(exc, PluginUpdateError):
            return str(exc)
        text = str(exc).strip() or exc.__class__.__name__
        return text[-2000:]

    @staticmethod
    def _update_payload(state: dict[str, Any]) -> dict[str, Any]:
        phase = str(state.get("phase") or "idle")
        release = state.get("release") if isinstance(state.get("release"), dict) else {}
        latest_version = str(release.get("version") or state.get("targetVersion") or "")
        available = False
        if latest_version:
            try:
                available = is_newer_version(latest_version, PLUGIN_VERSION)
            except PluginUpdateError:
                pass
        return {
            "ok": phase not in {"failed", "check_failed"},
            "phase": phase,
            "busy": phase in _UPDATE_BUSY_PHASES,
            "currentVersion": PLUGIN_VERSION,
            "latestVersion": latest_version or None,
            "updateAvailable": available,
            **state,
        }

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
            pm = getattr(self.context, "platform_manager", None)
            if pm is None:
                return
            if not pm.platform_insts:
                return
            from .whatsapp_adapter import (
                _ACTIVE_ADAPTERS,
                sanitize_whatsapp_platform_config,
            )
            from .whatsapp_adapter import WhatsAppPlatformAdapter as NewAdapter
            from .whatsapp_config_policy import extract_legacy_command_prefix
            platform_configs = getattr(pm, "platforms_config", [])
            for idx, config in enumerate(platform_configs):
                if config.get("type") != "whatsapp" or not config.get("enable", False):
                    continue
                sanitized_config = sanitize_whatsapp_platform_config(config)
                if sanitized_config != config:
                    platform_configs[idx] = sanitized_config
                    config.clear()
                    config.update(sanitized_config)
                pid = config.get("id")
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
