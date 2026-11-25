"""Shared AriaCast <-> Home Assistant ecosystem core.

This package is the single source of truth for the logic that is used both
by the HACS custom_component (running inside Home Assistant core) and by the
`ariacast_core` add-on (running as a standalone Docker container). It has no
hard dependency on `homeassistant` so it can run in either context.
"""
