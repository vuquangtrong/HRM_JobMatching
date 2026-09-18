#!/usr/bin/env python3
"""Prints HRM_Backend configuration values for shell scripts.

Usage:
    python3 get_config.py                  # dump full effective config as JSON
    python3 get_config.py llm.model        # print a scalar value
    python3 get_config.py llm.model <default>  # print value with a fallback
    python3 get_config.py fastembed        # print a nested object as JSON

Booleans are printed as 1/0 so bash scripts can compare against integers.
"""

import sys
import json

from config import get_config


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--"]
    cfg = get_config()

    if not args:
        print(json.dumps(cfg, indent=2, ensure_ascii=False))
        return 0

    path = args[0].strip()
    default = args[1] if len(args) > 1 else ""

    node = cfg
    for part in path.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            print(default)
            return 0

    if isinstance(node, bool):
        print("1" if node else "0")
    elif isinstance(node, (dict, list)):
        print(json.dumps(node, ensure_ascii=False))
    else:
        print(node)
    return 0


if __name__ == "__main__":
    sys.exit(main())