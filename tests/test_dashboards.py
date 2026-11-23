"""Copies of Grafana's dashboards: save (Grafana -> files) and restore (files -> Grafana).

Grafana is replaced by FakeGrafana: a real HTTP server on localhost that answers the
API calls the program makes, keeping its dashboards, folders and playlists in memory.
"""

from __future__ import annotations

import base64
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import unquote, urlsplit

import pytest

from measure_station_db import dashboards, sysconfig

PASSWORD = "grafana-admin-pw"


class FakeGrafana:
    """Grafana's API, as far as the program uses it. Writes need the admin login."""

    def __init__(self) -> None:
        self.folders: dict[str, dict[str, Any]] = {}
        self.dashboards: dict[str, tuple[str, dict[str, Any]]] = {}
        self.playlists: dict[str, dict[str, Any]] = {}
        self.requests: list[tuple[str, str]] = []
        self._next_id = 1
        self._lock = threading.Lock()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def _answer(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length)) if length else None
                with fake._lock:
                    status, reply = fake.handle(
                        self.command, self.path, self.headers.get("Authorization"), body
                    )
                data = json.dumps(reply).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_PUT = _answer

            def log_message(self, *args: Any) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def _id(self) -> int:
        self._next_id += 1
        return self._next_id

    # Setting up what's in Grafana.
    def add_folder(self, uid: str, title: str, parent: str | None = None) -> None:
        folder = {"id": self._id(), "uid": uid, "title": title, "version": 3, "url": "/x"}
        if parent:
            folder["parentUid"] = parent
        self.folders[uid] = folder

    def add_dashboard(self, uid: str, title: str, folder: str = "", **extra: Any) -> None:
        model = {
            "id": self._id(),
            "uid": uid,
            "title": title,
            "version": 7,
            "panels": [{"id": 1, "type": "timeseries", "title": "CO2"}],
            **extra,
        }
        self.dashboards[uid] = (folder, model)

    def add_playlist(self, uid: str, name: str, interval: str, uids: list[str]) -> None:
        items = [{"type": "dashboard_by_uid", "value": u} for u in uids]
        self.playlists[uid] = {"uid": uid, "name": name, "interval": interval, "items": items}

    # The API.
    def handle(self, method: str, raw_path: str, auth: str | None, body: Any) -> tuple[int, Any]:
        path = urlsplit(raw_path).path
        self.requests.append((method, path))
        good = "Basic " + base64.b64encode(f"admin:{PASSWORD}".encode()).decode()
        if auth is not None and auth != good:
            return 401, {"message": "Invalid username or password"}
        if method != "GET" and auth is None:
            return 401, {"message": "Unauthorized"}
        parts = [unquote(p) for p in path.strip("/").split("/")]

        if method == "GET" and parts == ["api", "search"]:
            return 200, [
                {"uid": uid, "title": model["title"], "type": "dash-db", "folderUid": f}
                for uid, (f, model) in self.dashboards.items()
            ]
        if method == "GET" and parts[:3] == ["api", "dashboards", "uid"]:
            if parts[3] not in self.dashboards:
                return 404, {"message": "Dashboard not found"}
            folder, model = self.dashboards[parts[3]]
            return 200, {"dashboard": model, "meta": {"folderUid": folder, "slug": "x"}}
        if method == "POST" and parts == ["api", "dashboards", "db"]:
            folder = body.get("folderUid") or ""
            if folder and folder not in self.folders:
                return 400, {"message": "folder not found"}
            model = dict(body["dashboard"])
            old = self.dashboards.get(model["uid"])
            model["id"] = old[1]["id"] if old else self._id()
            model["version"] = old[1]["version"] + 1 if old else 1
            self.dashboards[model["uid"]] = (folder, model)
            return 200, {"status": "success", "uid": model["uid"]}
        if parts[:2] == ["api", "folders"]:
            if method == "GET" and len(parts) == 3:
                if parts[2] not in self.folders:
                    return 404, {"message": "folder not found"}
                return 200, self.folders[parts[2]]
            if method == "POST" and len(parts) == 2:
                if body.get("parentUid") and body["parentUid"] not in self.folders:
                    return 400, {"message": "parent folder not found"}
                self.add_folder(body["uid"], body["title"], body.get("parentUid"))
                return 200, self.folders[body["uid"]]
        if parts[:2] == ["api", "playlists"]:
            if method == "GET" and len(parts) == 2:
                return 200, [
                    {"id": 1, "uid": p["uid"], "name": p["name"], "interval": p["interval"]}
                    for p in self.playlists.values()
                ]
            if method == "GET" and len(parts) == 3:
                if parts[2] not in self.playlists:
                    return 404, {"message": "playlist not found"}
                p = self.playlists[parts[2]]
                items = [
                    {"id": n, "playlistUid": p["uid"], "order": n, **item}
                    for n, item in enumerate(p["items"], 1)
                ]
                return 200, {**p, "id": 9, "items": items}
            if method == "POST" and len(parts) == 2:
                self.playlists[body["uid"]] = dict(body)
                return 200, body
            if method == "PUT" and len(parts) == 3:
                self.playlists[parts[2]] = {"uid": parts[2], **body}
                return 200, body
        return 404, {"message": "Not found"}


@pytest.fixture
def grafana():
    fake = FakeGrafana()
    yield fake
    fake.close()


@pytest.fixture
def copies_dir(tmp_path, monkeypatch):
    root = tmp_path / "grafana"
    monkeypatch.setattr(sysconfig, "GRAFANA_COPIES_DIR", root)
    monkeypatch.setattr(sysconfig, "GRAFANA_PASSWORD", PASSWORD)
    return root


def _use(monkeypatch, fake: FakeGrafana) -> None:
    monkeypatch.setattr(sysconfig, "GRAFANA_URL", fake.url)


def _tv_setup(fake: FakeGrafana) -> None:
    fake.add_folder("f-tv", "TV")
    fake.add_dashboard("d-co2", "CO2", "f-tv")
    fake.add_dashboard("d-weather", "Weather & Wind", "f-tv")
    fake.add_dashboard("d-live", "Live")
    fake.add_playlist("p-tv", "TV", "1m", ["d-co2", "d-weather"])


def _files(root) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes().decode()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


# --------------------------------------------------------------------------- #
# save
# --------------------------------------------------------------------------- #
def test_save_writes_a_file_per_dashboard_and_playlist(grafana, copies_dir, monkeypatch, capsys):
    _tv_setup(grafana)
    _use(monkeypatch, grafana)

    assert dashboards.run(["save"]) == 0

    assert set(_files(copies_dir)) == {
        "folders.json",
        "dashboards/general/live.json",
        "dashboards/tv/co2.json",
        "dashboards/tv/weather-wind.json",
        "playlists/tv.json",
    }
    assert json.loads((copies_dir / "folders.json").read_text()) == {
        "tv": {"uid": "f-tv", "title": "TV"}
    }
    assert json.loads((copies_dir / "playlists/tv.json").read_text()) == {
        "uid": "p-tv",
        "name": "TV",
        "interval": "1m",
        "items": [
            {"type": "dashboard_by_uid", "value": "d-co2"},
            {"type": "dashboard_by_uid", "value": "d-weather"},
        ],
    }
    out = capsys.readouterr().out
    assert "Saved 3 dashboards, 1 playlist and 1 folder" in out
    assert "new: dashboards/tv/co2.json" in out


def test_saved_files_leave_out_what_changes_at_every_save(grafana, copies_dir, monkeypatch):
    _tv_setup(grafana)
    _use(monkeypatch, grafana)
    dashboards.run(["save"])

    text = (copies_dir / "dashboards/tv/co2.json").read_bytes().decode()
    model = json.loads(text)
    assert "id" not in model and "version" not in model
    assert model["panels"][0]["id"] == 1  # a panel's id is part of the design: kept
    assert text == dashboards.render(model)  # sorted keys, 2-space indent
    assert text.endswith("}\n") and "\r\n" not in text


def test_saving_again_reports_only_real_changes(grafana, copies_dir, monkeypatch, capsys):
    _tv_setup(grafana)
    _use(monkeypatch, grafana)
    dashboards.run(["save"])
    capsys.readouterr()

    # Saved again in Grafana without changes: only its version number went up.
    grafana.dashboards["d-co2"][1]["version"] += 1
    dashboards.run(["save"])
    assert "Nothing has changed since the last save" in capsys.readouterr().out

    grafana.dashboards["d-co2"][1]["refresh"] = "30s"
    dashboards.run(["save"])
    out = capsys.readouterr().out
    assert "changed: dashboards/tv/co2.json" in out
    assert "weather" not in out


def test_save_removes_copies_of_deleted_dashboards_only(grafana, copies_dir, monkeypatch, capsys):
    _tv_setup(grafana)
    _use(monkeypatch, grafana)
    dashboards.run(["save"])
    (copies_dir / "README.md").write_text("notes\n")
    capsys.readouterr()

    del grafana.dashboards["d-live"]
    dashboards.run(["save"])

    assert "removed (no longer in Grafana): dashboards/general/live.json" in capsys.readouterr().out
    assert not (copies_dir / "dashboards/general").exists()  # its empty folder too
    assert (copies_dir / "README.md").exists()  # not one of save's files: left alone


def test_titles_that_give_the_same_name_get_the_uid_added(grafana, copies_dir, monkeypatch):
    grafana.add_dashboard("d-a", "CO2")
    grafana.add_dashboard("d-b", "CO2!")
    grafana.add_folder("f-gen", "General")
    grafana.add_dashboard("d-c", "CO2", "f-gen")
    _use(monkeypatch, grafana)
    dashboards.run(["save"])

    assert set(_files(copies_dir)) == {
        "folders.json",
        "dashboards/general/co2.json",
        "dashboards/general/co2-d-b.json",
        # A folder called General mustn't share the top level's directory.
        "dashboards/general-f-gen/co2.json",
    }


def test_the_folder_a_folder_is_in_is_saved_too(grafana, copies_dir, monkeypatch):
    grafana.add_folder("f-screens", "Screens")
    grafana.add_folder("f-tv", "TV", parent="f-screens")
    grafana.add_dashboard("d-co2", "CO2", "f-tv")
    _use(monkeypatch, grafana)
    dashboards.run(["save"])

    assert json.loads((copies_dir / "folders.json").read_text()) == {
        "screens": {"uid": "f-screens", "title": "Screens"},
        "tv": {"uid": "f-tv", "title": "TV", "parentUid": "f-screens"},
    }


def test_save_explains_when_grafana_wants_a_login(copies_dir, monkeypatch, capsys):
    class NoAnonymous(FakeGrafana):
        def handle(self, method, raw_path, auth, body):
            return 401, {"message": "Unauthorized"}

    fake = NoAnonymous()
    try:
        _use(monkeypatch, fake)
        assert dashboards.run(["save"]) == 1
    finally:
        fake.close()
    assert "Viewing without signing in may be switched off" in capsys.readouterr().err
    assert not copies_dir.exists()


def test_save_explains_when_grafana_cant_be_reached(copies_dir, monkeypatch, capsys):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    monkeypatch.setattr(sysconfig, "GRAFANA_URL", f"http://127.0.0.1:{port}")

    assert dashboards.run(["save"]) == 1
    assert "Can't reach Grafana at" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# restore
# --------------------------------------------------------------------------- #
def test_restore_into_a_new_grafana_gives_back_the_same_copies(
    grafana, copies_dir, monkeypatch, capsys
):
    _tv_setup(grafana)
    grafana.add_folder("f-screens", "Screens")
    grafana.folders["f-tv"]["parentUid"] = "f-screens"
    _use(monkeypatch, grafana)
    dashboards.run(["save"])
    saved = _files(copies_dir)

    rebuilt = FakeGrafana()
    try:
        _use(monkeypatch, rebuilt)
        assert dashboards.run(["restore"]) == 0
        assert "(2 folders created)" in capsys.readouterr().out
        # The folder that holds TV was created first.
        posts = [p for m, p in rebuilt.requests if m == "POST" and p == "/api/folders"]
        assert len(posts) == 2
        assert list(rebuilt.folders) == ["f-screens", "f-tv"]

        for p in copies_dir.rglob("*.json"):
            p.unlink()
        assert dashboards.run(["save"]) == 0
        assert _files(copies_dir) == saved
    finally:
        rebuilt.close()


def test_restore_replaces_existing_ones_and_leaves_the_rest(grafana, copies_dir, monkeypatch):
    _tv_setup(grafana)
    _use(monkeypatch, grafana)
    dashboards.run(["save"])

    grafana.dashboards["d-co2"][1]["title"] = "Changed by hand"
    grafana.playlists["p-tv"]["interval"] = "5m"
    grafana.add_dashboard("d-new", "Not in the repo")
    grafana.requests.clear()

    assert dashboards.run(["restore"]) == 0

    assert grafana.dashboards["d-co2"][1]["title"] == "CO2"
    assert grafana.dashboards["d-co2"][0] == "f-tv"
    assert grafana.playlists["p-tv"]["interval"] == "1m"
    assert "d-new" in grafana.dashboards
    assert ("PUT", "/api/playlists/p-tv") in grafana.requests
    assert ("POST", "/api/folders") not in grafana.requests  # TV was already there


def test_restore_with_the_wrong_password(grafana, copies_dir, monkeypatch, capsys):
    _tv_setup(grafana)
    _use(monkeypatch, grafana)
    dashboards.run(["save"])
    monkeypatch.setattr(sysconfig, "GRAFANA_PASSWORD", "wrong")

    assert dashboards.run(["restore"]) == 1
    assert "refused the login for admin" in capsys.readouterr().err


def test_restore_asks_for_the_password_and_can_be_cancelled(
    grafana, copies_dir, monkeypatch, capsys
):
    _tv_setup(grafana)
    _use(monkeypatch, grafana)
    dashboards.run(["save"])
    monkeypatch.setattr(sysconfig, "GRAFANA_PASSWORD", None)

    def cancel(prompt):
        raise KeyboardInterrupt

    monkeypatch.setattr(dashboards.getpass, "getpass", cancel)
    grafana.requests.clear()

    assert dashboards.run(["restore"]) == 1
    assert "nothing was changed" in capsys.readouterr().err
    assert grafana.requests == []


def test_restore_with_no_copies(copies_dir, capsys):
    assert dashboards.run(["restore"]) == 1
    assert "Run 'save' first" in capsys.readouterr().err


def test_restore_refuses_a_dashboard_in_an_unknown_folder(copies_dir, capsys):
    (copies_dir / "dashboards/kitchen").mkdir(parents=True)
    (copies_dir / "dashboards/kitchen/co2.json").write_text('{"uid": "x", "title": "CO2"}')

    assert dashboards.run(["restore"]) == 1
    assert "isn't in folders.json: 'kitchen'" in capsys.readouterr().err


def test_usage(capsys):
    assert dashboards.run([]) == dashboards.EX_USAGE
    assert dashboards.run(["load"]) == dashboards.EX_USAGE
    assert "save | restore" in capsys.readouterr().err
