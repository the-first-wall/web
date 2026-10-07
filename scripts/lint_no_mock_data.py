#!/usr/bin/env python3
"""
The First Wall — Mock Data & Placeholder Linter
Ensures zero mock hashes (0x000...0001) or test placeholders can ever be committed.
"""

import os
import sys
import re

FORBIDDEN_PATTERNS = [
    re.compile(r"0x0000000000000000000000000000000000000000000000000000000000000001"),
    re.compile(r"0x00000000\.\.\.00000001"),
    re.compile(r"0x1234567890abcdef"),
    re.compile(r"0xdeadbeefdeadbeef"),
]

def check_directory(directory: str):
    failed = False
    for root, _, files in os.walk(directory):
        if ".git" in root or "__pycache__" in root:
            continue
        for f in files:
            if not (f.endswith(".json") or f.endswith(".html") or f.endswith(".js") or f.endswith(".md")):
                continue
            path = os.path.join(root, f)
            with open(path, "r", encoding="utf-8", errors="ignore") as file:
                content = file.read()
                for pattern in FORBIDDEN_PATTERNS:
                    match = pattern.search(content)
                    if match:
                        print(f"❌ [LINT FAILED] Found forbidden mock pattern '{match.group()}' in: {path}")
                        failed = True
    return not failed

if __name__ == "__main__":
    web_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    print(f"[*] Running Mock Data Linter across {web_dir}...")
    if check_directory(web_dir):
        print("✅ [LINT PASSED] Zero mock data or placeholder hashes found.")
        sys.exit(0)
    else:
        sys.exit(1)
