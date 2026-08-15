"""Constants for the matrix_e2ee integration."""

from __future__ import annotations

DOMAIN = "matrix_e2ee"

CONF_HOMESERVER = "homeserver"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_ALLOWED_ROOMS = "allowed_rooms"
CONF_ALLOWED_USERS = "allowed_users"
CONF_COMMAND_PREFIX = "command_prefix"

DEFAULT_COMMAND_PREFIX = "!"

SESSION_FILENAME = "matrix_e2ee_session.json"
STORE_DIRNAME = "matrix_e2ee_store"
SESSION_VERSION = 1

SERVICE_SEND_MESSAGE = "send_message"
ATTR_MESSAGE = "message"
ATTR_ROOM_ID = "room_id"

EVENT_COMMAND = "matrix_e2ee_command"
EVENT_ERROR = "matrix_e2ee_error"

DEVICE_NAME = "Home Assistant matrix_e2ee"

ERROR_SESSION_CORRUPT = "session_corrupt"
ERROR_SESSION_MISSING = "session_missing"
ERROR_PASSWORD_REQUIRED = "password_required"
ERROR_LOGIN_FAILED = "login_failed"
ERROR_RESTORE_FAILED = "restore_failed"
ERROR_DEVICE_MISMATCH = "device_mismatch"
ERROR_ROOM_NOT_ALLOWED = "room_not_allowed"
ERROR_SEND_FAILED = "send_failed"
ERROR_UNVERIFIED_DEVICE = "unverified_device"
ERROR_STORE_MISSING = "store_missing"
ERROR_ENCRYPTION_UNAVAILABLE = "encryption_unavailable"

# matrix-nio ClientConfig default. First login must never persist this value.
NIO_DEFAULT_PICKLE_KEY = "DEFAULT_KEY"

DATA_CLIENT = "client"
