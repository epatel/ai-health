"""Dashboard (M9): data shaping, pages and JSON API."""

from pathlib import Path

import pytest

from aihealth.config import Config
from aihealth.engine import scan
from aihealth.store import GitInfo, connect, save_scan
from aihealth.web import views

NESTED = (
    "def f(x):\n"
    "    if x:\n"
    "        for y in x:\n"
    "            if y:\n"
    "                while y:\n"
    "                    y -= 1\n"
    "    return x\n"
)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "bad.py").write_text(NESTED)
    (tmp_path / "pkg" / "ok.py").write_text("def g(x):\n    return x\n")
    (tmp_path / "main.dart").write_text("int add(int a, int b) => a + b;\n")
    return tmp_path


def _saved(project: Path, times: int = 1):
    conn = connect(project / ".aihealth" / "health.db")
    cfg = Config()
    ids = [
        save_scan(conn, scan(project, cfg), cfg, GitInfo("abc1234", "main", False))
        for _ in range(times)
    ]
    return conn, ids[-1]


# --- views (no web framework needed) ---------------------------------------------------


def test_treemap_tree_nests_by_directory():
    files = [
        {"path": "pkg/a.py", "loc": 10, "score": 9.5, "finding_count": 0},
        {"path": "pkg/sub/b.py", "loc": 0, "score": 3.0, "finding_count": 2},
        {"path": "c.dart", "loc": 5, "score": 6.0},
    ]
    tree = views.treemap_tree(files)
    assert [n["name"] for n in tree] == ["c.dart", "pkg"]
    pkg = tree[1]
    assert [n["name"] for n in pkg["children"]] == ["a.py", "sub"]
    leaf = pkg["children"][1]["children"][0]
    assert leaf == {
        "name": "b.py",
        "path": "pkg/sub/b.py",
        "value": 1,
        "score": 3.0,
        "band": "alert",
        "findings": 2,
    }


def test_overview_and_file_view(project):
    conn, scan_id = _saved(project)
    data = views.overview(conn, scan_id)
    # one mild smell (penalty 1.0 -> 9.42) keeps bad.py in the healthy band
    assert data["counts"] == {"healthy": 3, "warning": 0, "alert": 0}
    assert [f["path"] for f in data["worst"]] == ["pkg/bad.py"]
    assert data["smells"][0]["label"] == "Deep Nesting"
    assert data["top_smells"]["pkg/bad.py"][0] == "Deep Nesting"
    fv = views.file_view(conn, scan_id, "pkg/bad.py")
    assert fv["findings"][0]["hint"] and fv["functions"][0]["qualname"] == "f"
    assert views.file_view(conn, scan_id, "nope.py") is None
    assert views.overview(conn, 999) is None


# --- pages + api ------------------------------------------------------------------------

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from aihealth.web.app import allowed_hosts, create_app


def client(project: Path) -> TestClient:
    return TestClient(create_app(project), base_url="http://127.0.0.1:8765")


def test_empty_state_and_scan_now(project):
    c = client(project)
    r = c.get("/")
    assert r.status_code == 200 and "No scans yet" in r.text
    r = c.post("/scan", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/?scan=1"
    r = c.get("/")
    assert "Project score" in r.text and "pkg/bad.py" in r.text
    assert c.get("/treemap", follow_redirects=False).status_code == 200


def test_overview_page(project):
    _saved(project, times=2)
    r = client(project).get("/?scan=1")
    assert r.status_code == 200
    html = r.text
    assert 'id="trend-chart"' in html and 'id="smell-chart"' in html  # 2 scans -> trend
    assert "▲" in html or "✕" in html  # status icon next to colour
    assert "/file?path=pkg/bad.py&amp;scan=1" in html
    assert "echarts@5.5.1" in html
    assert client(project).get("/?scan=99").status_code == 404


def test_treemap_page_redirects_without_scans(project):
    r = client(project).get("/treemap", follow_redirects=False)
    assert r.status_code == 303
    _saved(project)
    r = client(project).get("/treemap")
    assert '"path": "pkg/bad.py"' in r.text and "Area = lines of code" in r.text


def test_file_page_uses_configured_limits(project):
    (project / ".aihealth.toml").write_text("[thresholds]\ndeep_nesting = 5\n")
    _saved(project)
    r = client(project).get("/file", params={"path": "pkg/bad.py", "scan": 1})
    assert r.status_code == 200
    assert "nesting 5" in r.text  # from config, not a hardcoded default
    assert client(project).get("/file", params={"path": "x.py"}).status_code == 404


def test_json_api(project):
    _saved(project, times=2)
    c = client(project)
    assert [s["id"] for s in c.get("/api/scans").json()] == [2, 1]
    scan_json = c.get("/api/scans/1").json()
    assert scan_json["file_count"] == 3 and scan_json["smells"][0]["smell"] == "deep_nesting"
    f = c.get("/api/scans/1/file", params={"path": "pkg/bad.py"}).json()
    assert f["functions"][0]["max_nesting"] == 4
    assert len(c.get("/api/trend").json()) == 2
    assert len(c.get("/api/trend", params={"path": "pkg/bad.py"}).json()) == 2
    assert c.get("/api/scans/9").status_code == 404
    assert c.get("/static/charts.js").status_code == 200


# --- hardening ----------------------------------------------------------------


def test_requests_for_other_hosts_are_refused(project):
    """DNS rebinding: a page on evil.example resolving to 127.0.0.1 sends
    `Host: evil.example`, and must not read the API."""
    c = client(project)
    assert c.get("/api/scans", headers={"host": "evil.example"}).status_code == 400
    assert c.get("/api/scans", headers={"host": "localhost:8765"}).status_code == 200


def test_allowed_hosts():
    assert allowed_hosts("127.0.0.1") == ["127.0.0.1", "::1", "localhost"]
    assert "box.lan" in allowed_hosts("box.lan")
    assert allowed_hosts("0.0.0.0") == ["*"]  # nothing to check against; serve warns


def test_scan_now_refuses_cross_site_posts(project):
    """CSRF: a form on another site posting to the dashboard."""
    c = client(project)
    evil = {"origin": "https://evil.example"}
    assert c.post("/scan", headers=evil, follow_redirects=False).status_code == 403
    cross = {"sec-fetch-site": "cross-site"}  # a browser that leaves out Origin
    assert c.post("/scan", headers=cross, follow_redirects=False).status_code == 403
    assert c.get("/").text.count("No scans yet") == 1  # nothing was scanned
    own = {"origin": "http://127.0.0.1:8765", "sec-fetch-site": "same-origin"}
    assert c.post("/scan", headers=own, follow_redirects=False).status_code == 303


def test_pages_cannot_be_framed_and_charts_are_pinned(project):
    r = client(project).get("/")
    assert r.headers["content-security-policy"] == "frame-ancestors 'none'"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert 'integrity="sha384-' in r.text and 'crossorigin="anonymous"' in r.text
