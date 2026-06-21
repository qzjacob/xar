#!/usr/bin/env python3
"""License-clean gate (design §7). Fails if any INSTALLED core dependency carries
a copyleft/non-commercial license that would contaminate a permissive release.
Optional extras (cn/graph/crawl/...) are intentionally excluded — they are meant
to run isolated or stay un-shipped.

Usage: python scripts/check_licenses.py
"""
from __future__ import annotations

import sys
from importlib.metadata import metadata

# Core deps that get linked into the shipped package (from pyproject [project].dependencies)
CORE = [
    "pydantic", "pydantic-settings", "typer", "rich", "httpx", "tenacity",
    "python-dateutil", "fastapi", "uvicorn", "jinja2", "psycopg", "pgvector",
    "litellm", "anthropic", "fastembed", "edgartools", "trafilatura", "pdfplumber",
]

FORBIDDEN = ["AGPL", "GPL-3", "GPLV3", "GPL-2", "SSPL", "CC-BY-NC", "NONCOMMERCIAL", "RAIL"]
# LGPL and "GPL with classpath/linking exception" are allowed; we only flag strong copyleft.
ALLOW_SUBSTR = ["LGPL", "CLASSPATH", "LINKING EXCEPTION"]


def license_of(pkg: str) -> str:
    try:
        m = metadata(pkg)
    except Exception:
        return "NOT-INSTALLED"
    lic = (m.get("License") or "").strip()
    classifiers = [c for c in m.get_all("Classifier", []) if "License" in c]
    return (lic + " | " + " ; ".join(classifiers)).upper()


def main() -> int:
    problems = []
    for pkg in CORE:
        lic = license_of(pkg)
        if lic == "NOT-INSTALLED":
            continue
        if any(a in lic for a in ALLOW_SUBSTR):
            continue
        hit = next((f for f in FORBIDDEN if f in lic), None)
        if hit:
            problems.append(f"{pkg}: {hit} in license metadata -> {lic[:120]}")
    if problems:
        print("LICENSE GATE FAILED:")
        for p in problems:
            print("  ✗", p)
        return 1
    print(f"✓ license gate passed ({len(CORE)} core deps checked, no strong copyleft/NC)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
