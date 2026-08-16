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
SERVICE_START_VERIFICATION = "start_verification"
SERVICE_CONFIRM_VERIFICATION = "confirm_verification"
SERVICE_CANCEL_VERIFICATION = "cancel_verification"
SERVICE_REAUTHENTICATE = "reauthenticate"
SERVICE_GET_FINGERPRINT = "get_fingerprint"
SERVICE_VERIFY_DEVICE_BY_FINGERPRINT = "verify_device_by_fingerprint"
ATTR_MESSAGE = "message"
ATTR_ROOM_ID = "room_id"
ATTR_USER_ID = "user_id"
ATTR_DEVICE_ID = "device_id"
ATTR_ED25519 = "ed25519"
ATTR_TRANSACTION_ID = "transaction_id"
ATTR_PASSWORD = "password"

EVENT_COMMAND = "matrix_e2ee_command"
EVENT_ERROR = "matrix_e2ee_error"
EVENT_VERIFICATION = "matrix_e2ee_verification"
EVENT_FINGERPRINT = "matrix_e2ee_fingerprint"

DEVICE_NAME = "Home Assistant matrix_e2ee"

# Integration-level SAS timeout. Must fire before nio's Sas._max_age (5 min),
# so we emit a clear `verification_timeout` event instead of nio silently canceling.
VERIFICATION_TIMEOUT_SECONDS = 240

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
ERROR_DEVICE_MISSING = "device_missing"
ERROR_FINGERPRINT_MISMATCH = "fingerprint_mismatch"
ERROR_INVALID_TRANSACTION = "invalid_transaction"
ERROR_VERIFICATION_TIMEOUT = "verification_timeout"
ERROR_VERIFICATION_PEER_DENIED = "verification_peer_denied"
ERROR_SOFT_LOGOUT = "soft_logout"
ERROR_HARD_LOGOUT = "hard_logout"
ERROR_REFRESH_TOKEN_UNSUPPORTED = "refresh_token_unsupported"
ERROR_INVALID_STATE = "invalid_state"

# matrix-nio ClientConfig default. First login must never persist this value.
NIO_DEFAULT_PICKLE_KEY = "DEFAULT_KEY"

DATA_CLIENT = "client"
