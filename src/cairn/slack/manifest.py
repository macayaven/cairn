"""Pure builders for a workspace owner's internal Slack app manifest."""

import json
import re
from urllib.parse import quote, urlsplit

SCOPES: tuple[str, ...] = ("chat:write", "commands", "im:history", "users:read")
BOT_EVENTS: tuple[str, ...] = ("app_uninstalled", "message.im", "tokens_revoked")


def build_manifest(
    *, public_url: str, install: str, command: str = "/cairn", name: str = "cairn"
) -> dict:
    """Build the HTTPS callbacks and minimal permissions for one install.

    A deployment path prefix is retained; trailing slashes are removed. Credentials,
    query strings and fragments cannot form a callback base. Invalid callback bases,
    install IDs and slash commands raise ValueError naming the offending option.
    """
    try:
        url = urlsplit(public_url)
        valid_url = (
            url.scheme == "https"
            and bool(url.hostname)
            and url.username is None
            and url.password is None
            and not url.query
            and not url.fragment
            and "?" not in public_url
            and "#" not in public_url
            and "\\" not in public_url
            and not url.netloc.endswith(":")
            and not any(character.isspace() or ord(character) < 32 for character in public_url)
        )
        # Accessing port also validates malformed and out-of-range numeric ports.
        _ = url.port
    except ValueError:
        valid_url = False
    if not valid_url:
        raise ValueError(
            "public_url must be an HTTPS base URL without credentials, query or fragment"
        )
    if re.fullmatch(r"sl_[0-9a-f]{12}", install) is None:
        raise ValueError("install must be sl_ followed by 12 lowercase hexadecimal characters")
    if re.fullmatch(r"/[A-Za-z0-9_-]{1,31}", command) is None:
        raise ValueError("command must start with / and contain 1 to 31 letters, digits, _ or -")

    base = f"{public_url.rstrip('/')}/integrations/slack/{install}"
    return {
        "display_information": {"name": name},
        "features": {
            "app_home": {
                "messages_tab_enabled": True,
                "messages_tab_read_only_enabled": False,
            },
            "bot_user": {"display_name": name},
            "slash_commands": [
                {
                    "command": command,
                    "description": "Find knowledge or file a note in cairn",
                    "url": f"{base}/commands",
                }
            ],
            "shortcuts": [
                {
                    "name": "File in cairn",
                    "type": "message",
                    "callback_id": "file_in_cairn",
                    "description": "File this message as a note in cairn",
                }
            ],
        },
        "oauth_config": {"scopes": {"bot": list(SCOPES)}},
        "settings": {
            "event_subscriptions": {
                "request_url": f"{base}/events",
                "bot_events": list(BOT_EVENTS),
            },
            "interactivity": {"is_enabled": True, "request_url": f"{base}/actions"},
        },
    }


def manifest_link(manifest: dict) -> str:
    """Open Slack's documented app-creation URL with one percent-encoded JSON manifest."""
    encoded = quote(json.dumps(manifest, ensure_ascii=False, separators=(",", ":")), safe="")
    return f"https://api.slack.com/apps?new_app=1&manifest_json={encoded}"
