from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

helpers_path = ROOT / "whatsapp_helpers.py"
helpers = helpers_path.read_text("utf-8")
start = helpers.index("def format_whatsapp_markdown")
end = helpers.index("def chunk_text", start)
fragment = (ROOT / ".github/tmp/harden_helper.pyfrag").read_text("utf-8")
helpers_path.write_text(helpers[:start] + fragment + helpers[end:], "utf-8")

event_path = ROOT / "whatsapp_event.py"
event = event_path.read_text("utf-8")
old = '''                if chunk == last_sent and not has_remainder:
                    return
'''
new = '''                if chunk == last_sent:
                    if has_remainder:
                        # This prefix was already sent. Advance to the unsent
                        # remainder instead of repeating the same edit.
                        text_buffer = text_buffer[max_edit_length:]
                        message_id = None
                        last_sent = ""
                        fallback_sent_len = 0
                        edit_failed = False
                        continue
                    return
'''
if old not in event:
    raise RuntimeError("streaming duplicate-edit guard not found")
event_path.write_text(event.replace(old, new, 1), "utf-8")

for relative in (
    ".github/tmp/harden_helper.pyfrag",
    ".github/scripts/harden_markdown_converter.py",
    ".github/workflows/harden-markdown-converter.yml",
):
    path = ROOT / relative
    if path.exists():
        path.unlink()

for directory in (ROOT / ".github/tmp", ROOT / ".github/scripts"):
    try:
        directory.rmdir()
    except OSError:
        pass
