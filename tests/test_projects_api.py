"""Project management endpoints used by the web UI's project menu."""
import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from sightflow import server  # noqa: E402


def test_new_open_rename(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "PROJECTS", tmp_path / "projects")
    monkeypatch.setattr(server, "DEFAULT_PROJECT", tmp_path / "none.json")
    first = tmp_path / "a.json"
    first.write_text(json.dumps(server.new_project("first")), encoding="utf-8")
    c = TestClient(server.create_app(first))

    assert [p["name"] for p in c.get("/api/projects").json()["projects"]] == ["first"]

    assert c.post("/api/projects/new", json={"name": "テスト ゲーム"}).json()["ok"]
    projects = c.get("/api/projects").json()["projects"]
    assert {p["name"] for p in projects} == {"first", "テスト ゲーム"}
    assert c.get("/api/state").json()["project"]["name"] == "テスト ゲーム"

    c.put("/api/project/name", json={"name": "renamed"})
    assert c.get("/api/state").json()["project"]["name"] == "renamed"

    other = next(p for p in projects if p["name"] == "first")
    assert c.post("/api/projects/open", json={"file": other["file"]}).json()["ok"]
    assert c.get("/api/state").json()["project"]["name"] == "first"
    # files outside the known list are refused
    assert c.post("/api/projects/open", json={"file": "/etc/passwd"}).status_code == 404
