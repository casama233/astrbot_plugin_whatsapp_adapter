"""Independent, release-based self-update helpers for the plugin page.

The official marketplace may lag behind GitHub releases.  This module keeps the
manual updater deterministic and testable without importing AstrBot itself.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse

import aiohttp

PLUGIN_REPOSITORY = "casama233/astrbot_plugin_whatsapp_adapter"
RELEASES_API_URL = f"https://api.github.com/repos/{PLUGIN_REPOSITORY}/releases?per_page=20"
TRUSTED_DOWNLOAD_HOSTS = frozenset(
    {
        "api.github.com",
        "codeload.github.com",
        "github.com",
        "objects.githubusercontent.com",
        "release-assets.githubusercontent.com",
    }
)
MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
MAX_EXTRACTED_BYTES = 150 * 1024 * 1024
MAX_ARCHIVE_FILES = 3000
MAX_SINGLE_FILE_BYTES = 50 * 1024 * 1024

_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
_METADATA_KEYS = {"name", "version", "repo", "astrbot_version"}


class PluginUpdateError(RuntimeError):
    """A safe, user-displayable update failure."""


@dataclass(frozen=True, slots=True)
class ReleaseDetails:
    version: str
    tag_name: str
    published_at: str
    notes: str
    html_url: str
    download_url: str
    asset_name: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "tagName": self.tag_name,
            "publishedAt": self.published_at,
            "notes": self.notes,
            "htmlUrl": self.html_url,
            "downloadUrl": self.download_url,
            "assetName": self.asset_name,
        }


def normalize_version(value: object) -> str:
    text = str(value or "").strip()
    match = _VERSION_RE.fullmatch(text)
    if not match:
        raise PluginUpdateError(f"不支持的版本号：{text or '空'}")
    return ".".join(match.groups())


def version_tuple(value: object) -> tuple[int, int, int]:
    return tuple(int(part) for part in normalize_version(value).split("."))  # type: ignore[return-value]


def is_newer_version(candidate: object, current: object) -> bool:
    return version_tuple(candidate) > version_tuple(current)


def validate_download_url(url: object) -> str:
    text = str(url or "").strip()
    parsed = urlparse(text)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in TRUSTED_DOWNLOAD_HOSTS:
        raise PluginUpdateError("更新下载地址不是受信任的 GitHub HTTPS 地址")
    if parsed.username or parsed.password:
        raise PluginUpdateError("更新下载地址不得包含认证信息")
    if parsed.port not in {None, 443}:
        raise PluginUpdateError("更新下载地址使用了非标准 HTTPS 端口")
    return text


def _release_download(release: dict[str, Any], version: str) -> tuple[str, str]:
    assets = release.get("assets")
    if isinstance(assets, list):
        expected_tokens = (
            "astrbot_plugin_whatsapp_adapter",
            "astrbot-plugin-whatsapp-adapter",
        )
        candidates: list[tuple[int, str, str]] = []
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            name = str(asset.get("name") or "").strip()
            url = str(asset.get("browser_download_url") or "").strip()
            lower_name = name.lower()
            if not lower_name.endswith(".zip") or not url:
                continue
            score = int(any(token in lower_name for token in expected_tokens)) * 2
            score += int(version in lower_name)
            if score >= 2:
                candidates.append((score, name, url))
        if candidates:
            _, name, url = max(candidates, key=lambda item: (item[0], item[1]))
            return validate_download_url(url), name

    zipball_url = validate_download_url(release.get("zipball_url"))
    return zipball_url, f"source-{version}.zip"


def select_latest_release(payload: object, current_version: str) -> ReleaseDetails:
    normalize_version(current_version)
    if not isinstance(payload, list):
        raise PluginUpdateError("GitHub Release 返回了无效数据")

    candidates: list[tuple[tuple[int, int, int], dict[str, Any], str]] = []
    for item in payload:
        if not isinstance(item, dict) or item.get("draft") or item.get("prerelease"):
            continue
        try:
            version = normalize_version(item.get("tag_name") or item.get("name"))
        except PluginUpdateError:
            continue
        candidates.append((version_tuple(version), item, version))

    if not candidates:
        raise PluginUpdateError("仓库中没有可用的稳定 Release")

    _, release, version = max(candidates, key=lambda item: item[0])
    download_url, asset_name = _release_download(release, version)
    notes = str(release.get("body") or "").strip()
    if len(notes) > 12000:
        notes = notes[:12000] + "\n…"
    return ReleaseDetails(
        version=version,
        tag_name=str(release.get("tag_name") or f"v{version}"),
        published_at=str(release.get("published_at") or ""),
        notes=notes,
        html_url=validate_download_url(release.get("html_url")),
        download_url=download_url,
        asset_name=asset_name,
    )


async def fetch_latest_release(current_version: str) -> ReleaseDetails:
    timeout = aiohttp.ClientTimeout(total=20, connect=8, sock_read=12)
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "astrbot-plugin-whatsapp-adapter-updater",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(RELEASES_API_URL, allow_redirects=False) as response:
                if response.status != 200:
                    remaining = response.headers.get("X-RateLimit-Remaining")
                    suffix = "（GitHub API 配额已用尽）" if remaining == "0" else ""
                    raise PluginUpdateError(f"GitHub Release 检查失败：HTTP {response.status}{suffix}")
                payload = await response.json(content_type=None)
    except PluginUpdateError:
        raise
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise PluginUpdateError(f"无法连接 GitHub Release：{exc}") from exc
    except ValueError as exc:
        raise PluginUpdateError("GitHub Release 返回的不是有效 JSON") from exc
    return select_latest_release(payload, current_version)


async def download_release_archive(url: str, destination: Path) -> str:
    validate_download_url(url)
    timeout = aiohttp.ClientTimeout(total=300, connect=15, sock_read=60)
    headers = {"User-Agent": "astrbot-plugin-whatsapp-adapter-updater"}
    digest = hashlib.sha256()
    downloaded = 0
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(url, allow_redirects=True) as response:
                if response.status != 200:
                    raise PluginUpdateError(f"Release 下载失败：HTTP {response.status}")
                validate_download_url(str(response.url))
                content_length = response.content_length
                if content_length and content_length > MAX_ARCHIVE_BYTES:
                    raise PluginUpdateError("Release 压缩包超过 50 MB 安全上限")
                with destination.open("wb") as output:
                    async for chunk in response.content.iter_chunked(64 * 1024):
                        downloaded += len(chunk)
                        if downloaded > MAX_ARCHIVE_BYTES:
                            raise PluginUpdateError("Release 压缩包超过 50 MB 安全上限")
                        digest.update(chunk)
                        output.write(chunk)
    except PluginUpdateError:
        destination.unlink(missing_ok=True)
        raise
    except (aiohttp.ClientError, TimeoutError, OSError) as exc:
        destination.unlink(missing_ok=True)
        raise PluginUpdateError(f"Release 下载失败：{exc}") from exc
    if downloaded == 0:
        destination.unlink(missing_ok=True)
        raise PluginUpdateError("Release 下载结果为空")
    return digest.hexdigest()


def _parse_metadata_text(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_line in text.splitlines():
        if not raw_line or raw_line[0].isspace() or raw_line.lstrip().startswith("#"):
            continue
        key, separator, value = raw_line.partition(":")
        key = key.strip()
        if not separator or key not in _METADATA_KEYS:
            continue
        normalized = value.strip()
        if len(normalized) >= 2 and normalized[0] == normalized[-1] and normalized[0] in {'"', "'"}:
            normalized = normalized[1:-1]
        result[key] = normalized.strip()
    return result


def _safe_member_path(name: str) -> PurePosixPath:
    if "\x00" in name:
        raise PluginUpdateError("Release 包含无效的空字符路径")
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts:
        raise PluginUpdateError(f"Release 包含不安全路径：{name}")
    if path.parts and ":" in path.parts[0]:
        raise PluginUpdateError(f"Release 包含不安全路径：{name}")
    return path


def extract_validated_release(
    archive_path: Path,
    destination: Path,
    *,
    expected_name: str,
    expected_version: str,
) -> dict[str, str]:
    expected_version = normalize_version(expected_version)
    try:
        archive = zipfile.ZipFile(archive_path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise PluginUpdateError("下载内容不是有效的 ZIP Release") from exc

    with archive:
        infos = archive.infolist()
        if not infos or len(infos) > MAX_ARCHIVE_FILES:
            raise PluginUpdateError("Release 文件数量异常")

        total_size = 0
        seen_paths: set[str] = set()
        safe_paths: dict[zipfile.ZipInfo, PurePosixPath] = {}
        metadata_candidates: list[tuple[zipfile.ZipInfo, PurePosixPath, dict[str, str]]] = []
        for info in infos:
            path = _safe_member_path(info.filename)
            mode = info.external_attr >> 16
            portable_key = path.as_posix().rstrip("/").casefold()
            if portable_key in seen_paths:
                raise PluginUpdateError(f"Release 包含重复路径：{info.filename}")
            seen_paths.add(portable_key)
            if stat.S_ISLNK(mode):
                raise PluginUpdateError(f"Release 不允许符号链接：{info.filename}")
            file_type = stat.S_IFMT(mode)
            if mode and file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
                raise PluginUpdateError(f"Release 包含不支持的特殊文件：{info.filename}")
            if info.flag_bits & 0x1:
                raise PluginUpdateError("Release 不允许加密文件")
            if info.file_size > MAX_SINGLE_FILE_BYTES:
                raise PluginUpdateError(f"Release 单个文件过大：{info.filename}")
            total_size += info.file_size
            if total_size > MAX_EXTRACTED_BYTES:
                raise PluginUpdateError("Release 解压后超过 150 MB 安全上限")
            safe_paths[info] = path
            if not info.is_dir() and path.name in {"metadata.yaml", "metadata.yml"}:
                try:
                    metadata = _parse_metadata_text(archive.read(info).decode("utf-8-sig"))
                except (UnicodeDecodeError, OSError) as exc:
                    raise PluginUpdateError("Release metadata 无法读取") from exc
                if metadata.get("name") == expected_name:
                    metadata_candidates.append((info, path, metadata))

        matching = []
        for candidate in metadata_candidates:
            try:
                if normalize_version(candidate[2].get("version")) == expected_version:
                    matching.append(candidate)
            except PluginUpdateError:
                continue
        if len(matching) != 1:
            raise PluginUpdateError("Release 中找不到唯一且版本匹配的插件 metadata")

        _, metadata_path, metadata = matching[0]
        root_parts = metadata_path.parts[:-1]
        destination.mkdir(parents=True, exist_ok=False)
        destination_root = destination.resolve()

        extracted_files = 0
        for info, path in safe_paths.items():
            member_mode = info.external_attr >> 16
            parts = path.parts
            if root_parts and parts[: len(root_parts)] != root_parts:
                continue
            relative_parts = parts[len(root_parts) :]
            if not relative_parts:
                continue
            target = destination.joinpath(*relative_parts)
            resolved = target.resolve()
            if resolved != destination_root and destination_root not in resolved.parents:
                raise PluginUpdateError(f"Release 解压路径越界：{info.filename}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output, length=64 * 1024)
            if permissions := member_mode & 0o777:
                target.chmod(0o755 if permissions & 0o111 else 0o644)
            extracted_files += 1

        if extracted_files == 0 or not (destination / "main.py").is_file():
            raise PluginUpdateError("Release 缺少插件入口 main.py")
        if not (destination / "package-lock.json").is_file():
            raise PluginUpdateError("Release 缺少可复现依赖锁 package-lock.json")
        return metadata


def atomic_swap_plugin(current_dir: Path, staged_dir: Path, backup_dir: Path) -> None:
    if backup_dir.exists():
        raise PluginUpdateError("更新备份目录已存在")
    os.replace(current_dir, backup_dir)
    try:
        os.replace(staged_dir, current_dir)
    except BaseException:
        os.replace(backup_dir, current_dir)
        raise


def restore_plugin_backup(current_dir: Path, backup_dir: Path, failed_dir: Path) -> None:
    if not backup_dir.is_dir():
        raise PluginUpdateError("无法回滚：旧版本备份不存在")
    if failed_dir.exists():
        shutil.rmtree(failed_dir)
    if current_dir.exists():
        os.replace(current_dir, failed_dir)
    os.replace(backup_dir, current_dir)
    if failed_dir.exists():
        shutil.rmtree(failed_dir)
