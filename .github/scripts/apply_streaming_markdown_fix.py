from __future__ import annotations

import base64
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def decoded(path: str) -> str:
    return base64.b64decode((ROOT / path).read_text("ascii")).decode("utf-8")


helpers_path = ROOT / "whatsapp_helpers.py"
helpers = helpers_path.read_text("utf-8")
start = helpers.index("def format_whatsapp_markdown")
end = helpers.index("def format_markdown_from_whatsapp", start)
helpers_path.write_text(
    helpers[:start] + decoded(".github/tmp/helper_block.b64") + helpers[end:],
    "utf-8",
)

event_path = ROOT / "whatsapp_event.py"
event = event_path.read_text("utf-8")
start = event.index("    async def _send_streaming_edit")
end = event.index("    async def send_typing", start)
event_path.write_text(
    event[:start] + decoded(".github/tmp/event_block.b64") + event[end:],
    "utf-8",
)

metadata_path = ROOT / "metadata.yaml"
metadata = metadata_path.read_text("utf-8")
metadata = re.sub(
    r"(?m)^version:\s*.*$",
    "version: 0.2.19",
    metadata,
    count=1,
)
metadata_path.write_text(metadata, "utf-8")

tests_path = ROOT / "tests" / "test_whatsapp_markdown.py"
tests_path.parent.mkdir(parents=True, exist_ok=True)
tests_path.write_text(decoded(".github/tmp/tests.b64"), "utf-8")

for relative in (
    ".github/tmp/helper_block.b64",
    ".github/tmp/event_block.b64",
    ".github/tmp/tests.b64",
    ".github/scripts/apply_streaming_markdown_fix.py",
    ".github/workflows/apply-streaming-markdown-fix.yml",
):
    path = ROOT / relative
    if path.exists():
        path.unlink()

for directory in (ROOT / ".github/tmp", ROOT / ".github/scripts"):
    try:
        directory.rmdir()
    except OSError:
        pass
