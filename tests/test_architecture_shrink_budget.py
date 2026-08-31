from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

# P2 debt baselines captured from canonical main on 2026-08-31.
# These historical implementation modules are shrink-only: protocol-specific
# compatibility and new isolated behavior should move into focused modules
# instead of making the legacy bodies larger again.
SHRINK_ONLY_MAX_BYTES = {
    "_whatsapp_adapter_impl.py": 125_349,
    "_whatsapp_helpers_impl.py": 60_189,
}


def test_historical_impl_modules_are_shrink_only() -> None:
    over_budget: list[str] = []
    for relative_path, maximum in SHRINK_ONLY_MAX_BYTES.items():
        path = ROOT / relative_path
        actual = path.stat().st_size
        if actual > maximum:
            over_budget.append(
                f"{relative_path}: {actual:,} > {maximum:,} bytes"
            )

    assert not over_budget, (
        "historical WhatsApp implementation modules exceeded their P2 "
        "shrink-only architecture budget:\n- "
        + "\n- ".join(over_budget)
        + "\nMove new protocol compatibility or isolated behavior into a focused "
        "module with regression coverage. When an extraction makes an impl "
        "module smaller, lower its ceiling in this test."
    )
