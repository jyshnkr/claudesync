#!/usr/bin/env python3
"""
ClaudeSync remote agent — deployed to remote machines via SSH.
Run: python3 remote_agent.py <json-encoded-path-list>
Outputs: JSON { path: {hash, mtime} } to stdout.
"""
import hashlib
import json
import os
import sys

AGENT_VERSION = "1"


def hash_files(paths: list) -> dict:
    result = {}
    for p in paths:
        if os.path.isfile(p):
            h = hashlib.sha256()
            with open(p, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
            result[p] = {"hash": h.hexdigest(), "mtime": os.stat(p).st_mtime}
    return result


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--version":
        print(AGENT_VERSION)
        sys.exit(0)
    if len(sys.argv) != 2:
        sys.exit(1)
    try:
        paths = json.loads(sys.argv[1])
    except json.JSONDecodeError:
        sys.exit(1)
    print(json.dumps(hash_files(paths)))
