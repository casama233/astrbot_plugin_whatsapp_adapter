"""Release-pinned self-update primitives for the WhatsApp adapter.

Updater v2 deliberately separates release discovery, artifact verification,
archive validation, transaction ownership, and directory replacement.  The
caller must install the exact ReleaseDetails candidate that the user confirmed;
there is no source-archive fallback.
"""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import re
import shutil
import stat
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urljoin, urlparse

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
MAX_REDIRECTS = 6

_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_METADATA_KEYS = {"name", "version", "repo", "astrbot_version"}
_MAIN_VERSION_RE = re.compile(r'^PLUGIN_VERSION\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)
_EXPECTED_REPO_URL = f"https://github.com/{PLUGIN_REPOSITORY}"


class PluginUpdateError(RuntimeError):
    """A safe, user-displayable update failure."""


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


def normalize_sha256_digest(value: object) -> str:
    text = str(value or "").strip().lower()
    if text.startswith("sha256:"):
        text = text.split(":", 1)[1]
    if not _SHA256_RE.fullmatch(text):
        raise PluginUpdateError("GitHub Release ZIP 缺少有效的 SHA-256 digest")
    return text


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


def _candidate_token(
    *,
    version: str,
    release_id: int,
    asset_id: int,
    asset_digest: str,
    target_commitish: str,
) -> str:
    material = json.dumps(
        {
            "version": normalize_version(version),
            "releaseId": int(release_id),
            "assetId": int(asset_id),
            "assetDigest": normalize_sha256_digest(asset_digest),
            "targetCommitish": str(target_commitish or "").strip(),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


@dataclass(frozen=True, slots=True)
class ReleaseDetails:
    version: str
    tag_name: str
    published_at: str
    notes: str
    html_url: str
    download_url: str
    asset_name: str
    release_id: int
    asset_id: int
    asset_digest: str
    target_commitish: str
    candidate_token: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "tagName": self.tag_name,
            "publishedAt": self.published_at,
            "notes": self.notes,
            "htmlUrl": self.html_url,
            "downloadUrl": self.download_url,
            "assetName": self.asset_name,
            "releaseId": self.release_id,
            "assetId": self.asset_id,
            "assetDigest": f"sha256:{self.asset_digest}",
            "targetCommitish": self.target_commitish,
            "candidateToken": self.candidate_token,
        }

    @classmethod
    def from_dict(cls, value: object) -> "ReleaseDetails":
        if not isinstance(value, dict):
            raise PluginUpdateError("更新候选 Release 状态无效，请重新检查更新")
        version = normalize_version(value.get("version"))
        try:
            release_id = int(value.get("releaseId"))
            asset_id = int(value.get("assetId"))
        except (TypeError, ValueError) as exc:
            raise PluginUpdateError("更新候选 Release 标识无效，请重新检查更新") from exc
        if release_id <= 0 or asset_id <= 0:
            raise PluginUpdateError("更新候选 Release 标识无效，请重新检查更新")
        digest = normalize_sha256_digest(value.get("assetDigest"))
        target_commitish = str(value.get("targetCommitish") or "").strip()
        if not target_commitish:
            raise PluginUpdateError("更新候选缺少目标 commit，请重新检查更新")
        expected_token = _candidate_token(
            version=version,
            release_id=release_id,
            asset_id=asset_id,
            asset_digest=digest,
            target_commitish=target_commitish,
        )
        token = str(value.get("candidateToken") or "").strip()
        if token != expected_token:
            raise PluginUpdateError("更新候选身份校验失败，请重新检查更新")
        return cls(
            version=version,
            tag_name=str(value.get("tagName") or f"v{version}"),
            published_at=str(value.get("publishedAt") or ""),
            notes=str(value.get("notes") or ""),
            html_url=validate_download_url(value.get("htmlUrl")),
            download_url=validate_download_url(value.get("downloadUrl")),
            asset_name=str(value.get("assetName") or ""),
            release_id=release_id,
            asset_id=asset_id,
            asset_digest=digest,
            target_commitish=target_commitish,
            candidate_token=token,
        )


def _release_asset(release: dict[str, Any], version: str) -> tuple[int, str, str, str]:
    expected_name = f"astrbot_plugin_whatsapp_adapter-v{version}.zip"
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise PluginUpdateError(f"v{version} Release 缺少正式 ZIP artifact")

    matches: list[dict[str, Any]] = []
    for asset in assets:
        if isinstance(asset, dict) and str(asset.get("name") or "").strip() == expected_name:
            matches.append(asset)
    if len(matches) != 1:
        raise PluginUpdateError(
            f"v{version} Release 必须且只能包含一个正式 artifact：{expected_name}"
        )

    asset = matches[0]
    try:
        asset_id = int(asset.get("id"))
    except (TypeError, ValueError) as exc:
        raise PluginUpdateError("GitHub Release artifact 缺少有效 asset id") from exc
    if asset_id <= 0:
        raise PluginUpdateError("GitHub Release artifact 缺少有效 asset id")
    digest = normalize_sha256_digest(asset.get("digest"))
    url = validate_download_url(asset.get("browser_download_url"))
    return asset_id, expected_name, url, digest


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
    try:
        release_id = int(release.get("id"))
    except (TypeError, ValueError) as exc:
        raise PluginUpdateError("GitHub Release 缺少有效 release id") from exc
    if release_id <= 0:
        raise PluginUpdateError("GitHub Release 缺少有效 release id")
    target_commitish = str(release.get("target_commitish") or "").strip()
    if not target_commitish:
        raise PluginUpdateError("GitHub Release 缺少 target commit")

    asset_id, asset_name, download_url, asset_digest = _release_asset(release, version)
    notes = str(release.get("body") or "").strip()
    if len(notes) > 12000:
        notes = notes[:12000] + "\n…"
    token = _candidate_token(
        version=version,
        release_id=release_id,
        asset_id=asset_id,
        asset_digest=asset_digest,
        target_commitish=target_commitish,
    )
    return ReleaseDetails(
        version=version,
        tag_name=str(release.get("tag_name") or f"v{version}"),
        published_at=str(release.get("published_at") or ""),
        notes=notes,
        html_url=validate_download_url(release.get("html_url")),
        download_url=download_url,
        asset_name=asset_name,
        release_id=release_id,
        asset_id=asset_id,
        asset_digest=asset_digest,
        target_commitish=target_commitish,
        candidate_token=token,
    )


async def fetch_latest_release(current_version: str) -> ReleaseDetails:
    timeout = aiohttp.ClientTimeout(total=20, connect=8, sock_read=12)
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "astrbot-plugin-whatsapp-adapter-updater-v2",
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


async def download_release_archive(
    url: str,
    destination: Path,
    *,
    expected_sha256: str,
) -> str:
    current_url = validate_download_url(url)
    expected = normalize_sha256_digest(expected_sha256)
    timeout = aiohttp.ClientTimeout(total=300, connect=15, sock_read=60)
    headers = {"User-Agent": "astrbot-plugin-whatsapp-adapter-updater-v2"}
    digest = hashlib.sha256()
    downloaded = 0
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            for redirect_count in range(MAX_REDIRECTS + 1):
                async with session.get(current_url, allow_redirects=False) as response:
                    if response.status in {301, 302, 303, 307, 308}:
                        if redirect_count >= MAX_REDIRECTS:
                            raise PluginUpdateError("Release 下载重定向次数过多")
                        location = response.headers.get("Location")
                        if not location:
                            raise PluginUpdateError("Release 下载返回无目标的重定向")
                        current_url = validate_download_url(urljoin(current_url, location))
                        continue
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
                    break
            else:  # pragma: no cover - loop is bounded above
                raise PluginUpdateError("Release 下载重定向失败")
    except PluginUpdateError:
        destination.unlink(missing_ok=True)
        raise
    except (aiohttp.ClientError, TimeoutError, OSError) as exc:
        destination.unlink(missing_ok=True)
        raise PluginUpdateError(f"Release 下载失败：{exc}") from exc
    if downloaded == 0:
        destination.unlink(missing_ok=True)
        raise PluginUpdateError("Release 下载结果为空")
    actual = digest.hexdigest()
    if actual != expected:
        destination.unlink(missing_ok=True)
        raise PluginUpdateError(
            f"Release SHA-256 校验失败：期望 {expected}，实际 {actual}"
        )
    return actual


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


def _read_json_file(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PluginUpdateError(f"Release {label} 无法读取") from exc
    if not isinstance(value, dict):
        raise PluginUpdateError(f"Release {label} 格式无效")
    return value


def _validate_extracted_versions(destination: Path, expected_version: str) -> None:
    try:
        main_text = (destination / "main.py").read_text(encoding="utf-8")
    except OSError as exc:
        raise PluginUpdateError("Release main.py 无法读取") from exc
    main_match = _MAIN_VERSION_RE.search(main_text)
    if not main_match or normalize_version(main_match.group(1)) != expected_version:
        raise PluginUpdateError("Release main.py 的 PLUGIN_VERSION 与 Release 不一致")

    package_json = destination / "package.json"
    package_lock = destination / "package-lock.json"
    if not package_json.is_file() or not package_lock.is_file():
        raise PluginUpdateError("Release 缺少 package.json 或 package-lock.json")
    package = _read_json_file(package_json, "package.json")
    lock = _read_json_file(package_lock, "package-lock.json")
    if normalize_version(package.get("version")) != expected_version:
        raise PluginUpdateError("Release package.json 版本与 Release 不一致")
    lock_version = lock.get("version")
    root_package = lock.get("packages", {}).get("") if isinstance(lock.get("packages"), dict) else None
    root_version = root_package.get("version") if isinstance(root_package, dict) else None
    values = [value for value in (lock_version, root_version) if value is not None]
    if not values or any(normalize_version(value) != expected_version for value in values):
        raise PluginUpdateError("Release package-lock.json 版本与 Release 不一致")


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
        repo = str(metadata.get("repo") or "").strip().rstrip("/")
        if repo and repo != _EXPECTED_REPO_URL:
            raise PluginUpdateError("Release metadata repo 与官方仓库不一致")
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
        _validate_extracted_versions(destination, expected_version)
        return metadata


def _requirements_fingerprint(root: Path) -> tuple[str, ...]:
    path = root / "requirements.txt"
    if not path.is_file():
        return ()
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        raise PluginUpdateError("无法读取 Python requirements.txt") from exc
    return tuple(
        line.strip()
        for line in lines
        if line.strip() and not line.lstrip().startswith("#")
    )


def validate_python_requirements_unchanged(current_dir: Path, staged_dir: Path) -> None:
    """Fail closed instead of mutating AstrBot's global Python environment.

    Node dependencies are plugin-local and can be staged safely.  Python plugin
    requirements are global to AstrBot, so a self-update is allowed only when
    the dependency contract is unchanged.  Dependency-changing releases must be
    installed through AstrBot's own plugin manager/restart path.
    """

    current = _requirements_fingerprint(current_dir)
    staged = _requirements_fingerprint(staged_dir)
    if current != staged:
        raise PluginUpdateError(
            "新版本修改了 Python requirements.txt；内置更新器拒绝修改 AstrBot 全局 Python 环境。"
            "请改用 AstrBot 插件管理器更新并重启。"
        )


def _try_rename_exchange(first: Path, second: Path) -> bool:
    if os.name != "posix":
        return False
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = getattr(libc, "renameat2")
    except (AttributeError, OSError):
        return False
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    at_fdcwd = -100
    rename_exchange = 2
    result = renameat2(
        at_fdcwd,
        os.fsencode(first),
        at_fdcwd,
        os.fsencode(second),
        rename_exchange,
    )
    if result == 0:
        return True
    error = ctypes.get_errno()
    if error in {errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP, errno.EXDEV, errno.ENOENT}:
        return False
    raise OSError(error, os.strerror(error))


def atomic_swap_plugin(current_dir: Path, staged_dir: Path, backup_dir: Path) -> str:
    """Replace the plugin directory and preserve a rollback copy.

    Linux filesystems with renameat2(RENAME_EXCHANGE) get a single atomic
    current<->staged exchange.  Other platforms use a guarded two-rename
    fallback.  backup_dir must live outside the plugin scan directory.
    """

    if backup_dir.exists():
        raise PluginUpdateError("更新备份目录已存在")
    if not current_dir.is_dir() or not staged_dir.is_dir():
        raise PluginUpdateError("更新目录状态无效")
    backup_dir.parent.mkdir(parents=True, exist_ok=True)
    if current_dir.parent.stat().st_dev != staged_dir.parent.stat().st_dev:
        raise PluginUpdateError("暂存目录与插件目录不在同一文件系统，无法安全切换")

    if _try_rename_exchange(current_dir, staged_dir):
        try:
            os.replace(staged_dir, backup_dir)
        except BaseException:
            try:
                _try_rename_exchange(current_dir, staged_dir)
            except BaseException:
                pass
            raise
        return "rename-exchange"

    os.replace(current_dir, backup_dir)
    try:
        os.replace(staged_dir, current_dir)
    except BaseException:
        os.replace(backup_dir, current_dir)
        raise
    return "rename-pair"


def restore_plugin_backup(current_dir: Path, backup_dir: Path, failed_dir: Path) -> None:
    if not backup_dir.is_dir():
        raise PluginUpdateError("无法回滚：旧版本备份不存在")
    failed_dir.parent.mkdir(parents=True, exist_ok=True)
    if failed_dir.exists():
        shutil.rmtree(failed_dir)
    if current_dir.exists():
        os.replace(current_dir, failed_dir)
    os.replace(backup_dir, current_dir)
    if failed_dir.exists():
        shutil.rmtree(failed_dir)


def acquire_update_transaction(lock_path: Path, transaction_id: str) -> None:
    transaction_id = str(transaction_id or "").strip()
    if not transaction_id:
        raise PluginUpdateError("更新 transaction id 无效")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "transactionId": transaction_id,
        "pid": os.getpid(),
        "createdAt": time.time(),
    }
    try:
        fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        try:
            existing = json.loads(lock_path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
        if (
            isinstance(existing, dict)
            and existing.get("transactionId") == transaction_id
            and int(existing.get("pid") or -1) == os.getpid()
        ):
            return
        raise PluginUpdateError("已有另一项插件更新 transaction 正在执行") from exc
    try:
        os.write(fd, json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


def transaction_lock_active(lock_path: Path) -> bool:
    if not lock_path.is_file():
        return False
    try:
        data = json.loads(lock_path.read_text(encoding="utf-8"))
    except Exception:
        return True
    if not isinstance(data, dict):
        return True
    try:
        return int(data.get("pid")) == os.getpid()
    except (TypeError, ValueError):
        return True


def recover_stale_update_transaction(lock_path: Path) -> dict[str, Any] | None:
    """Remove a lock inherited from a previous AstrBot process.

    Concurrent AstrBot processes sharing one data directory are unsupported. A
    different PID therefore identifies an interrupted previous runtime.
    """

    if not lock_path.is_file():
        return None
    try:
        data = json.loads(lock_path.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    try:
        same_pid = int(data.get("pid")) == os.getpid() if isinstance(data, dict) else False
    except (TypeError, ValueError):
        same_pid = False
    if same_pid:
        return None
    lock_path.unlink(missing_ok=True)
    return data if isinstance(data, dict) else {}


def release_update_transaction(lock_path: Path, transaction_id: str) -> None:
    if not lock_path.exists():
        return
    try:
        data = json.loads(lock_path.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    if isinstance(data, dict) and data.get("transactionId") not in {None, transaction_id}:
        return
    lock_path.unlink(missing_ok=True)
