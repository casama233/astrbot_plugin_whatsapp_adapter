from __future__ import annotations

import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
runpy.run_path(str(ROOT / ".github/scripts/refine_config_scopes.py"), run_name="__main__")

config_path = ROOT / ".pre-commit-config.yaml"
config = config_path.read_text("utf-8")
temporary_hook = '''      - id: apply-config-scope-refinement
        name: Apply config scope refinement
        entry: python .github/scripts/run_refine_config_scopes.py
        language: system
        pass_filenames: false
'''
config_path.write_text(config.replace(temporary_hook, ""), "utf-8")

Path(__file__).unlink(missing_ok=True)
