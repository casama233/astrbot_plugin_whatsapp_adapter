from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
adapter_path = ROOT / "whatsapp_adapter.py"
source = adapter_path.read_text("utf-8")
source, runtime_count = re.subn(
    r'(?m)^RUNTIME_DEFAULT_CONFIG: dict\[str, Any\] = \{\n(?:    \*\*BASE_GATEWAY_CONFIG,\n)?',
    'RUNTIME_DEFAULT_CONFIG: dict[str, Any] = {\n    **BASE_GATEWAY_CONFIG,\n',
    source,
    count=1,
)
source, platform_count = re.subn(
    r'(?m)^DEFAULT_CONFIG: dict\[str, Any\] = \{\n(?:    \*\*BASE_GATEWAY_CONFIG,\n)?',
    'DEFAULT_CONFIG: dict[str, Any] = {\n',
    source,
    count=1,
)
if runtime_count != 1 or platform_count != 1:
    raise RuntimeError(
        f"Gateway config blocks not found: runtime={runtime_count} platform={platform_count}"
    )
if not re.search(
    r'(?m)^RUNTIME_DEFAULT_CONFIG: dict\[str, Any\] = \{\n    \*\*BASE_GATEWAY_CONFIG,',
    source,
):
    raise RuntimeError("runtime Gateway defaults were not restored")
if re.search(
    r'(?m)^DEFAULT_CONFIG: dict\[str, Any\] = \{\n    \*\*BASE_GATEWAY_CONFIG,',
    source,
):
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
