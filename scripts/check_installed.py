"""CI check of an installed (wheel) copy of aihealth: imported from
site-packages, not the source tree, with its dashboard package data.

Run with the installed interpreter from outside the checkout:
``$VENV/bin/python scripts/check_installed.py``.
"""

import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

import aihealth
from aihealth.web.app import HERE, create_app


def main() -> int:
    source_tree = Path(__file__).resolve().parent.parent / "src"
    location = Path(aihealth.__file__).resolve()
    if source_tree in location.parents:
        print(f"imported from the source tree, not an install: {location}")
        return 1
    for data in ("templates/base.html", "templates/overview.html", "static/charts.js"):
        if not (HERE / data).is_file():
            print(f"package data missing from the install: {data}")
            return 1
    with tempfile.TemporaryDirectory() as repo:
        client = TestClient(create_app(repo), base_url="http://127.0.0.1:8765")
        page = client.get("/")
        if page.status_code != 200 or "aihealth" not in page.text:
            print(f"dashboard page failed: {page.status_code}")
            return 1
    print(f"installed aihealth {aihealth.__version__} at {location.parent}: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
