"""Stable wire contract shared by dDuo clients and project runtimes.

This module is intentionally independent from distribution and update policy.
Agent clients may learn that a newer release exists, but the runtime never
downloads or activates code on their behalf.
"""

CLIENT_PROTOCOL_VERSION = "1"
