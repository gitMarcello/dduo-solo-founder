#!/usr/bin/env python3
"""Compatibility entry point for installations updated while a session is open."""

from dduo_solo_founder.hooks import session_start


if __name__ == "__main__":
    session_start()
