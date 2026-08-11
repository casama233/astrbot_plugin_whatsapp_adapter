from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_NAME = "astrbot_plugin_whatsapp_adapter"
PLUGIN_AUTHOR = "casama233"
PLUGIN_REPOSITORY_URL = "https://github.com/casama233/astrbot_plugin_whatsapp_adapter"
PACKAGE_NAME = "astrbot-plugin-whatsapp-adapter-gateway"
ARCHIVE_ROOT = f"{PLUGIN_NAME}/"
ASTRBOT_MARKET_MAX_ZIP_BYTES = 16 * 1024 * 1024
SELF_UPDATER_MAX_EXTRACTED_BYTES = 150 * 1024 * 1024
SELF_UPDATER_MAX_FILES = 3000
SELF_UPDATER_MAX_SINGLE_FILE_BYTES = 50 * 1024 * 1024
SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
MAIN_VERSION_RE = re.compile(r'^PLUGIN_VERSION = "([^"]+)"$', re.MULTILINE)
MAIN_NAME_RE = re.compile(r'^PLUGIN_NAME = "([^"]+)"$', re.MULTILINE)
REQUIRED_MARKER_KEYS = {"version", "previous_version", "date", "commit_subject", "notes"}


class ReleaseContractError(RuntimeError):
    pass


@dataclass(frozen=True)
class RepoContract:
    version: str
    metadata: dict[str, str]


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def parse_metadata_text(text: str) -> tuple[dict[str, str], dict[str, list[str]]]:
    scalars: dict[str, str] = {}
    lists: dict[str, list[str]] = {}
    active_list: str | None = None
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if raw[:1].isspace():
            if active_list and stripped.startswith("-"):
                lists.setdefault(active_list, []).append(_unquote(stripped[1:].strip()))
            continue
        key, sep, value = raw.partition(":")
        if not sep:
            active_list = None
            continue
        key = key.strip()
        value = value.strip()
        if not value:
            active_list = key
            lists.setdefault(key, [])
        else:
            active_list = None
            scalars[key] = _unquote(value)
    return scalars, lists


def parse_semver(value: object) -> tuple[int, int, int]:
    text = str(value or "").strip()
    match = SEMVER_RE.fullmatch(text)
    if not match:
        raise ReleaseContractError(
            f"version {text!r} is not the stable x.y.z SemVer supported by this updater"
        )
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseContractError(f"cannot read valid JSON from {path}") from exc
    if not isinstance(value, dict):
        raise ReleaseContractError(f"{path} must contain a JSON object")
    return value


def _extract_main_identity(text: str) -> tuple[str, str]:
    version_match = MAIN_VERSION_RE.search(text)
    name_match = MAIN_NAME_RE.search(text)
    if not version_match or not name_match:
        raise ReleaseContractError("main.py must declare PLUGIN_NAME and PLUGIN_VERSION once")
    if len(MAIN_VERSION_RE.findall(text)) != 1 or len(MAIN_NAME_RE.findall(text)) != 1:
        raise ReleaseContractError("main.py contains ambiguous plugin identity declarations")
    return name_match.group(1), version_match.group(1)


def validate_repo(root: Path = ROOT, *, expected_version: str | None = None) -> RepoContract:
    metadata_path = root / "metadata.yaml"
    metadata, metadata_lists = parse_metadata_text(metadata_path.read_text(encoding="utf-8"))
    required = {"name", "display_name", "desc", "version", "author", "repo"}
    missing = sorted(key for key in required if not metadata.get(key))
    if missing:
        raise ReleaseContractError(f"metadata.yaml is missing required fields: {', '.join(missing)}")
    if metadata["name"] != PLUGIN_NAME:
        raise ReleaseContractError(f"metadata name must stay {PLUGIN_NAME!r}")
    if metadata["author"] != PLUGIN_AUTHOR:
        raise ReleaseContractError(f"metadata author must stay {PLUGIN_AUTHOR!r}")
    if metadata["repo"] != PLUGIN_REPOSITORY_URL:
        raise ReleaseContractError("metadata repo must be the canonical HTTPS GitHub repository URL")
    if "/" in metadata["name"] or "/" in metadata["author"]:
        raise ReleaseContractError("AstrBot market plugin identity fields cannot contain '/'")
    if metadata["name"].lower() != metadata["name"] or not metadata["name"].startswith("astrbot_plugin_"):
        raise ReleaseContractError("plugin name must remain lowercase and use the astrbot_plugin_ prefix")
    parse_semver(metadata["version"])

    # support_platforms describes pre-existing AstrBot adapter keys. This project
    # itself PROVIDES the WhatsApp adapter, so declaring an unregistered
    # 'whatsapp' compatibility key is semantically wrong until AstrBot core owns
    # such a key. Omit the field instead of publishing misleading metadata.
    if "support_platforms" in metadata or "support_platforms" in metadata_lists:
        raise ReleaseContractError(
            "platform-adapter package must omit support_platforms; it is not a consumer compatibility declaration"
        )

    astrbot_version = metadata.get("astrbot_version", "")
    if astrbot_version:
        if "v" in astrbot_version.lower() or not re.fullmatch(r"[0-9A-Za-z<>=!~.*,+\-\s]+", astrbot_version):
            raise ReleaseContractError("astrbot_version must remain a PEP-440-style range without a v prefix")

    main_text = (root / "main.py").read_text(encoding="utf-8")
    main_name, main_version = _extract_main_identity(main_text)
    package = _read_json(root / "package.json")
    lock = _read_json(root / "package-lock.json")
    lock_root = lock.get("packages", {}).get("") if isinstance(lock.get("packages"), dict) else None
    if not isinstance(lock_root, dict):
        raise ReleaseContractError("package-lock.json is missing the root package entry")

    versions = {
        "metadata.yaml": metadata["version"],
        "main.py": main_version,
        "package.json": str(package.get("version") or ""),
        "package-lock.json": str(lock.get("version") or ""),
        "package-lock root": str(lock_root.get("version") or ""),
    }
    if len(set(versions.values())) != 1:
        details = ", ".join(f"{key}={value}" for key, value in versions.items())
        raise ReleaseContractError(f"release version sources are out of sync: {details}")
    version = metadata["version"]
    if expected_version and version != expected_version:
        raise ReleaseContractError(f"repository version is {version}, expected {expected_version}")
    if main_name != PLUGIN_NAME:
        raise ReleaseContractError("main.py PLUGIN_NAME does not match metadata identity")
    if package.get("name") != PACKAGE_NAME or lock.get("name") != PACKAGE_NAME or lock_root.get("name") != PACKAGE_NAME:
        raise ReleaseContractError("Node package identity is inconsistent")
    return RepoContract(version=version, metadata=metadata)


def load_marker(path: Path, root: Path = ROOT) -> dict:
    try:
        spec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseContractError(f"release marker {path} is not valid JSON") from exc
    if not isinstance(spec, dict):
        raise ReleaseContractError("release marker must be a JSON object")
    missing = REQUIRED_MARKER_KEYS - set(spec)
    unknown = set(spec) - REQUIRED_MARKER_KEYS
    if missing:
        raise ReleaseContractError(f"release marker is missing: {', '.join(sorted(missing))}")
    if unknown:
        raise ReleaseContractError(f"release marker contains unknown fields: {', '.join(sorted(unknown))}")

    current = validate_repo(root).version
    version = str(spec["version"] or "").strip()
    previous = str(spec["previous_version"] or "").strip()
    target_tuple = parse_semver(version)
    previous_tuple = parse_semver(previous)
    if previous != current:
        raise ReleaseContractError(
            f"previous_version {previous} must equal the current repository version {current}"
        )
    if target_tuple <= previous_tuple:
        raise ReleaseContractError(f"release version {version} must be newer than {previous}")
    if path.stem not in {version, f"v{version}"}:
        raise ReleaseContractError(
            f"release marker filename must be {version}.json or v{version}.json"
        )

    release_date = str(spec["date"] or "").strip()
    try:
        date.fromisoformat(release_date)
    except ValueError as exc:
        raise ReleaseContractError("release date must use YYYY-MM-DD") from exc
    subject = str(spec["commit_subject"] or "").strip()
    if not subject or "\n" in subject or len(subject) > 100:
        raise ReleaseContractError("commit_subject must be one non-empty line of at most 100 characters")
    notes = spec["notes"]
    if not isinstance(notes, list) or not notes or any(not str(note).strip() for note in notes):
        raise ReleaseContractError("notes must be a non-empty list of non-empty strings")
    if any("\n" in str(note) for note in notes):
        raise ReleaseContractError("each release note must be a single line")
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    if f"## [{version}]" in changelog:
        raise ReleaseContractError(f"CHANGELOG.md already contains {version}")
    return {
        "version": version,
        "previous_version": previous,
        "date": release_date,
        "commit_subject": subject,
        "notes": [str(note).strip() for note in notes],
    }


def _replace_regex_once(path: Path, pattern: str, replacement: str) -> None:
    text = path.read_text(encoding="utf-8")
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE)
    if count != 1:
        raise ReleaseContractError(f"{path}: expected one release-version replacement, found {count}")
    path.write_text(updated, encoding="utf-8")


def apply_marker(path: Path, root: Path = ROOT) -> dict:
    spec = load_marker(path, root)
    old = spec["previous_version"]
    new = spec["version"]
    _replace_regex_once(root / "main.py", rf'^PLUGIN_VERSION = "{re.escape(old)}"$', f'PLUGIN_VERSION = "{new}"')
    _replace_regex_once(root / "metadata.yaml", rf"^version: {re.escape(old)}$", f"version: {new}")
    _replace_regex_once(root / "package.json", rf'^(  "version": "){re.escape(old)}(",$)', rf"\g<1>{new}\g<2>")
    _replace_regex_once(root / "package-lock.json", rf'^(  "version": "){re.escape(old)}(",$)', rf"\g<1>{new}\g<2>")
    _replace_regex_once(root / "package-lock.json", rf'^(      "version": "){re.escape(old)}(",$)', rf"\g<1>{new}\g<2>")

    changelog = root / "CHANGELOG.md"
    text = changelog.read_text(encoding="utf-8")
    heading = "# Changelog\n\n"
    if not text.startswith(heading):
        raise ReleaseContractError("CHANGELOG.md heading is not in the expected format")
    bullets = "\n".join(f"- {item}" for item in spec["notes"])
    section = f"## [{new}] - {spec['date']}\n\n{bullets}\n\n"
    changelog.write_text(heading + section + text[len(heading):], encoding="utf-8")
    path.unlink()
    validate_repo(root, expected_version=new)
    return spec


def emit_marker_environment(path: Path, github_env: Path, output_dir: Path, root: Path = ROOT) -> dict:
    spec = load_marker(path, root)
    output_dir.mkdir(parents=True, exist_ok=True)
    subject_file = output_dir / "release-subject.txt"
    notes_file = output_dir / "release-notes.md"
    subject_file.write_text(spec["commit_subject"], encoding="utf-8")
    notes_file.write_text(
        "## 更新內容\n\n" + "\n".join(f"- {item}" for item in spec["notes"]) + "\n",
        encoding="utf-8",
    )
    with github_env.open("a", encoding="utf-8") as env:
        values = {
            "RELEASE_VERSION": spec["version"],
            "PREVIOUS_VERSION": spec["previous_version"],
            "RELEASE_DATE": spec["date"],
            "RELEASE_MARKER": path.as_posix(),
            "RELEASE_SUBJECT_FILE": subject_file.as_posix(),
            "RELEASE_NOTES_FILE": notes_file.as_posix(),
        }
        for key, value in values.items():
            env.write(f"{key}={value}\n")
    return spec


def _zip_text(archive: zipfile.ZipFile, name: str) -> str:
    try:
        return archive.read(name).decode("utf-8-sig")
    except (KeyError, UnicodeDecodeError) as exc:
        raise ReleaseContractError(f"release ZIP cannot read {name}") from exc


def validate_archive(path: Path, expected_version: str) -> dict[str, object]:
    parse_semver(expected_version)
    size = path.stat().st_size
    if size <= 0:
        raise ReleaseContractError("release ZIP is empty")
    if size > ASTRBOT_MARKET_MAX_ZIP_BYTES:
        raise ReleaseContractError(
            f"release ZIP is {size} bytes, above AstrBot market's 16 MiB limit"
        )
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ReleaseContractError("release artifact is not a valid ZIP") from exc
    with archive:
        infos = archive.infolist()
        if not infos or len(infos) > SELF_UPDATER_MAX_FILES:
            raise ReleaseContractError("release ZIP has an invalid file count")
        extracted = 0
        roots: set[str] = set()
        forbidden_parts = {".git", ".github", ".release", "node_modules", "__pycache__", "tests"}
        for info in infos:
            normalized = info.filename.replace("\\", "/")
            member = PurePosixPath(normalized)
            if member.is_absolute() or ".." in member.parts or not member.parts:
                raise ReleaseContractError(f"release ZIP has unsafe path: {info.filename}")
            roots.add(member.parts[0])
            if any(part in forbidden_parts for part in member.parts):
                raise ReleaseContractError(f"release ZIP contains development/runtime junk: {info.filename}")
            if member.suffix == ".pyc":
                raise ReleaseContractError(f"release ZIP contains bytecode: {info.filename}")
            if info.file_size > SELF_UPDATER_MAX_SINGLE_FILE_BYTES:
                raise ReleaseContractError(f"release ZIP contains an oversized file: {info.filename}")
            extracted += info.file_size
            if extracted > SELF_UPDATER_MAX_EXTRACTED_BYTES:
                raise ReleaseContractError("release ZIP exceeds the self-updater extracted-size limit")
        if roots != {PLUGIN_NAME}:
            raise ReleaseContractError(f"release ZIP must have exactly one root folder named {PLUGIN_NAME}")

        prefix = ARCHIVE_ROOT
        required = ["metadata.yaml", "main.py", "package.json", "package-lock.json"]
        names = {info.filename.rstrip("/") for info in infos}
        missing = [name for name in required if f"{prefix}{name}" not in names]
        if missing:
            raise ReleaseContractError(f"release ZIP is missing required files: {', '.join(missing)}")
        metadata, metadata_lists = parse_metadata_text(_zip_text(archive, f"{prefix}metadata.yaml"))
        if metadata_lists.get("support_platforms") or "support_platforms" in metadata:
            raise ReleaseContractError("release ZIP reintroduced invalid support_platforms metadata")
        if metadata.get("name") != PLUGIN_NAME or metadata.get("author") != PLUGIN_AUTHOR:
            raise ReleaseContractError("release ZIP metadata identity does not match this plugin")
        if metadata.get("repo") != PLUGIN_REPOSITORY_URL or metadata.get("version") != expected_version:
            raise ReleaseContractError("release ZIP metadata repo/version does not match the release")
        main_name, main_version = _extract_main_identity(_zip_text(archive, f"{prefix}main.py"))
        package = json.loads(_zip_text(archive, f"{prefix}package.json"))
        lock = json.loads(_zip_text(archive, f"{prefix}package-lock.json"))
        lock_root = lock.get("packages", {}).get("")
        if main_name != PLUGIN_NAME or main_version != expected_version:
            raise ReleaseContractError("release ZIP main.py identity/version is inconsistent")
        if package.get("version") != expected_version or lock.get("version") != expected_version:
            raise ReleaseContractError("release ZIP Node version sources are inconsistent")
        if not isinstance(lock_root, dict) or lock_root.get("version") != expected_version:
            raise ReleaseContractError("release ZIP package-lock root version is inconsistent")

    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"size": size, "sha256": digest, "version": expected_version}


def _find_marker(root: Path) -> Path:
    markers = sorted((root / ".release").glob("*.json"))
    if len(markers) != 1:
        raise ReleaseContractError(f"expected exactly one release marker, found {len(markers)}")
    return markers[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate this plugin's AstrBot/GitHub release contract")
    sub = parser.add_subparsers(dest="command", required=True)
    repo_parser = sub.add_parser("validate-repo")
    repo_parser.add_argument("--expected-version")
    marker_parser = sub.add_parser("validate-marker")
    marker_parser.add_argument("marker", nargs="?")
    marker_parser.add_argument("--github-env")
    marker_parser.add_argument("--output-dir")
    apply_parser = sub.add_parser("apply-marker")
    apply_parser.add_argument("marker")
    archive_parser = sub.add_parser("validate-archive")
    archive_parser.add_argument("archive")
    archive_parser.add_argument("--expected-version", required=True)
    args = parser.parse_args(argv)

    try:
        if args.command == "validate-repo":
            contract = validate_repo(expected_version=args.expected_version)
            print(contract.version)
        elif args.command == "validate-marker":
            marker = Path(args.marker) if args.marker else _find_marker(ROOT)
            if args.github_env or args.output_dir:
                if not args.github_env or not args.output_dir:
                    raise ReleaseContractError("--github-env and --output-dir must be supplied together")
                spec = emit_marker_environment(marker, Path(args.github_env), Path(args.output_dir))
            else:
                spec = load_marker(marker)
            print(json.dumps(spec, ensure_ascii=False))
        elif args.command == "apply-marker":
            spec = apply_marker(Path(args.marker))
            print(json.dumps(spec, ensure_ascii=False))
        elif args.command == "validate-archive":
            result = validate_archive(Path(args.archive), args.expected_version)
            print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ReleaseContractError, OSError, json.JSONDecodeError) as exc:
        print(f"release contract failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
