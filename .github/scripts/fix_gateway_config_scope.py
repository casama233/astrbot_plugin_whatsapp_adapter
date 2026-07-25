from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
adapter_path = ROOT / "whatsapp_adapter.py"
source = adapter_path.read_text("utf-8")
source = source.replace(
    "RUNTIME_DEFAULT_CONFIG: dict[str, Any] = {\n    \"dm_policy\": \"allowlist\",\n",
    "RUNTIME_DEFAULT_CONFIG: dict[str, Any] = {\n    **BASE_GATEWAY_CONFIG,\n    \"dm_policy\": \"allowlist\",\n",
    1,
)
source = source.replace(
    "DEFAULT_CONFIG: dict[str, Any] = {\n    **BASE_GATEWAY_CONFIG,\n    \"dm_policy\": \"allowlist\",\n",
    "DEFAULT_CONFIG: dict[str, Any] = {\n    \"dm_policy\": \"allowlist\",\n",
    1,
)
if "RUNTIME_DEFAULT_CONFIG: dict[str, Any] = {\n    **BASE_GATEWAY_CONFIG," not in source:
    raise RuntimeError("runtime Gateway defaults were not restored")
if "DEFAULT_CONFIG: dict[str, Any] = {\n    **BASE_GATEWAY_CONFIG," in source:
    raise RuntimeError("platform template still exposes Gateway defaults")
adapter_path.write_text(source, "utf-8")

precommit_path = ROOT / ".pre-commit-config.yaml"
config = precommit_path.read_text("utf-8")
hook = '''      - id: fix-gateway-config-scope
        name: Fix Gateway config scope
        entry: python .github/scripts/fix_gateway_config_scope.py
        language: system
        pass_filenames: false
'''
precommit_path.write_text(config.replace(hook, ""), "utf-8")
Path(__file__).unlink(missing_ok=True)
try:
    (ROOT / ".github/scripts").rmdir()
except OSError:
    pass
