from __future__ import annotations

import argparse
from pathlib import Path
from urllib.parse import quote

import httpx

from dduo_solo_founder.config import get_settings


def restore_snapshot(snapshot: Path, collection: str) -> None:
    """Upload one verified collection snapshot to the internal Qdrant service."""
    if not snapshot.is_file():
        raise FileNotFoundError(snapshot)
    settings = get_settings()
    endpoint = (
        f"{settings.qdrant_url}/collections/{quote(collection, safe='')}/snapshots/upload"
        "?priority=snapshot"
    )
    with snapshot.open("rb") as source:
        response = httpx.post(
            endpoint,
            files={"snapshot": (snapshot.name, source, "application/octet-stream")},
            timeout=600,
        )
    response.raise_for_status()


def main() -> None:
    """Run the isolated Qdrant snapshot restore helper."""
    parser = argparse.ArgumentParser(description="Restore one Qdrant collection snapshot.")
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("collection")
    arguments = parser.parse_args()
    restore_snapshot(arguments.snapshot, arguments.collection)


if __name__ == "__main__":
    main()
