"""Lifecycle state and update notifications for :mod:`matrix_e2ee.client`."""

from __future__ import annotations

from enum import StrEnum


class ClientState(StrEnum):
    """The only lifecycle state exposed by the client implementation."""

    RUNNING = "running"
    SOFT_LOGGED_OUT = "soft_logged_out"
    STOPPED = "stopped"
