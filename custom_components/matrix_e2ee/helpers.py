"""Pure, security-sensitive helpers used by the Matrix client.

Keeping these functions independent from the client makes their fail-closed
rules directly testable and prevents Home Assistant concerns leaking into the
Matrix protocol implementation.
"""

from __future__ import annotations

from typing import Any

_ENCRYPTED_EVENT_TYPES = frozenset(
    {"m.room.encrypted", "MegolmEvent", "OlmEvent", "EncryptedEvent"}
)


def room_allowed(room_id: str, allowed_rooms: list[str]) -> bool:
    """Return whether a room is explicitly allowlisted."""
    return bool(allowed_rooms) and room_id in allowed_rooms


def user_allowed(user_id: str, allowed_users: list[str]) -> bool:
    """Return whether a user is explicitly allowlisted."""
    return bool(allowed_users) and user_id in allowed_users


def parse_command(body: str, prefix: str) -> tuple[str, list[str]] | None:
    """Parse a prefixed command without retaining the raw message body."""
    if not prefix or not body.startswith(prefix):
        return None
    rest = body[len(prefix) :].strip()
    if not rest:
        return None
    parts = rest.split()
    return parts[0], parts[1:]


def requires_verified_sender(room: Any, event: Any) -> bool:
    """Return whether an inbound event must have a verified sender."""
    if getattr(room, "encrypted", False) or getattr(event, "decrypted", False):
        return True
    event_type = getattr(event, "type", None)
    return (
        event_type in _ENCRYPTED_EVENT_TYPES
        or type(event).__name__ in _ENCRYPTED_EVENT_TYPES
    )
