"""One-shot login LaunchAgent plist for the DevCache mount helper."""

from __future__ import annotations

from xml.sax.saxutils import escape


def render_mount_plist(*, helper: list[str], label: str) -> str:
    arguments = "\n".join(
        f"        <string>{escape(item)}</string>" for item in helper
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{escape(label)}</string>
    <key>ProgramArguments</key>
    <array>
{arguments}
    </array>
    <key>RunAtLoad</key>
    <true/>
</dict>
</plist>
"""
