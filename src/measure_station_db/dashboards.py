"""Grafana's dashboards and playlists, with copies kept in this repo.

Dashboards are designed in Grafana, in the browser, and Grafana holds the master copy.
This program copies them into deploy/grafana/, so they're kept in git with the rest of
the project, and can be put back::

    python -m measure_station_db.dashboards save      Grafana -> deploy/grafana/
    python -m measure_station_db.dashboards restore   deploy/grafana/ -> Grafana

See docs/saving-dashboards.md.

- **save** needs no login: anyone on the LAN may view dashboards. It makes the folder a
  mirror of Grafana: a dashboard deleted in Grafana has its file deleted too, so git
  shows the deletion (and can bring the file back).
- **restore** needs the admin login. It creates missing folders, and replaces the
  dashboards and playlists that have the same ID (uid) as a copy. Anything else in
  Grafana is left alone.
- **The files** are Grafana's own JSON, without the fields that change at every save
  (``id``, ``version``), and with sorted keys, so a git diff shows only real changes::

      folders.json                       each folder's directory name, uid, title, parent
      dashboards/<folder>/<title>.json   one per dashboard ("general": in no folder)
      playlists/<name>.json              one per playlist

  A dashboard file is what Grafana's own Export gives, so it can also be imported by
  hand (Dashboards, New, Import).
- **Dashboards refer to the database by its uid,** ``measure-station-db``, which the
  setup guide gives the data source, so the copies work on a rebuilt Pi unchanged.
- **Not copied:** the home dashboard setting, the data source (the setup guide's file),
  logins, and Grafana's own settings.
"""

from __future__ import annotations

import base64
import getpass
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from measure_station_db import sysconfig

EX_USAGE = 64

FOLDERS_FILE = "folders.json"
DASHBOARDS_DIR = "dashboards"
PLAYLISTS_DIR = "playlists"
# The directory for dashboards that are in no folder (Grafana's top level).
GENERAL = "general"

# Fields Grafana changes at every save. Leaving them out keeps git diffs to real changes.
_VOLATILE = ("id", "version", "iteration")

USAGE = """\
Usage: python -m measure_station_db.dashboards save | restore
  save      copy Grafana's dashboards and playlists into deploy/grafana/ (no login)
  restore   put the copies in deploy/grafana/ back into Grafana (asks for the password)
Grafana's address is GRAFANA_URL in sysconfig.py. See docs/saving-dashboards.md."""


class GrafanaError(Exception):
    """Grafana answered with an error, or couldn't be reached (``status`` None)."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class CopiesError(Exception):
    """A file in deploy/grafana/ can't be used."""


class Grafana:
    """A few calls of Grafana's HTTP API, with an optional login (basic auth)."""

    def __init__(
        self,
        url: str,
        username: str | None = None,
        password: str | None = None,
        timeout: float = 15.0,
    ) -> None:
        self.url = url.rstrip("/")
        self.timeout = timeout
        self._auth: str | None = None
        if username is not None and password is not None:
            token = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
            self._auth = f"Basic {token}"

    def get(self, path: str) -> Any:
        return self.request("GET", path)

    def request(self, method: str, path: str, body: Any = None) -> Any:
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(self.url + path, data=data, method=method)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if self._auth:
            req.add_header("Authorization", self._auth)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            raise GrafanaError(f"{method} {path}: {exc.code} {_reason(exc)}", exc.code) from None
        except (urllib.error.URLError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise GrafanaError(f"Can't reach Grafana at {self.url}: {reason}") from None
        return json.loads(raw) if raw else None

    def exists(self, path: str) -> bool:
        try:
            self.get(path)
        except GrafanaError as exc:
            if exc.status == 404:
                return False
            raise
        return True


def _reason(exc: urllib.error.HTTPError) -> str:
    """Grafana's own explanation of an error, if it gave one."""
    try:
        return str(json.loads(exc.read())["message"])
    except Exception:
        return str(exc.reason)


def _q(uid: str) -> str:
    return urllib.parse.quote(uid, safe="")


@dataclass
class Copies:
    """Everything that's copied, as Grafana's JSON."""

    # Folder uid -> {"uid", "title", and "parentUid" for a folder inside another}.
    folders: dict[str, dict[str, Any]] = field(default_factory=dict)
    # (the folder's uid, or "" for none; the dashboard)
    dashboards: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    # Each {"uid", "name", "interval", "items": [{"type", "value"}, ...]}
    playlists: list[dict[str, Any]] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{_count(len(self.dashboards), 'dashboard')}, "
            f"{_count(len(self.playlists), 'playlist')} and "
            f"{_count(len(self.folders), 'folder')}"
        )


def _count(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


# --------------------------------------------------------------------------- #
# Grafana -> files
# --------------------------------------------------------------------------- #
def fetch(grafana: Grafana) -> Copies:
    """Read every dashboard and playlist from Grafana, and the folders they're in."""
    copies = Copies()
    hits = grafana.get("/api/search?type=dash-db&limit=5000") or []
    for uid in sorted(hit["uid"] for hit in hits):
        got = grafana.get(f"/api/dashboards/uid/{_q(uid)}")
        folder_uid = (got.get("meta") or {}).get("folderUid") or ""
        copies.dashboards.append((folder_uid, _without_volatile(got["dashboard"])))

    wanted = [folder_uid for folder_uid, _ in copies.dashboards if folder_uid]
    while wanted:
        uid = wanted.pop()
        if uid in copies.folders:
            continue
        folder = grafana.get(f"/api/folders/{_q(uid)}")
        entry: dict[str, Any] = {"uid": folder["uid"], "title": folder["title"]}
        if folder.get("parentUid"):
            entry["parentUid"] = folder["parentUid"]
            wanted.append(folder["parentUid"])
        copies.folders[uid] = entry

    for uid in sorted(p["uid"] for p in grafana.get("/api/playlists") or []):
        playlist = grafana.get(f"/api/playlists/{_q(uid)}")
        items = playlist.get("items")
        if items is None:
            items = grafana.get(f"/api/playlists/{_q(uid)}/items") or []
        copies.playlists.append(
            {
                "uid": playlist["uid"],
                "name": playlist["name"],
                "interval": playlist["interval"],
                "items": [{"type": i["type"], "value": i["value"]} for i in items],
            }
        )
    return copies


def _without_volatile(dashboard: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in dashboard.items() if k not in _VOLATILE}


def slug(text: str) -> str:
    """A file name from a title: "CO2 & Temperature" -> "co2-temperature"."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "untitled"


def _unique(name: str, uid: str, taken: set[str]) -> str:
    """``name``, or if it's taken, ``name-<uid>``: titles can differ only in punctuation."""
    candidate, n = name, 1
    while candidate in taken:
        candidate = f"{name}-{slug(uid)}" + ("" if n == 1 else f"-{n}")
        n += 1
    taken.add(candidate)
    return candidate


def layout(copies: Copies) -> dict[str, Any]:
    """The files to write: path (relative to deploy/grafana/) -> JSON content."""
    dirs: dict[str, str] = {}
    taken_dirs = {GENERAL}
    for uid, folder in sorted(copies.folders.items(), key=lambda f: (f[1]["title"].lower(), f[0])):
        dirs[uid] = _unique(slug(folder["title"]), uid, taken_dirs)

    files: dict[str, Any] = {FOLDERS_FILE: {dirs[uid]: f for uid, f in copies.folders.items()}}

    taken_names: dict[str, set[str]] = {}
    for folder_uid, dashboard in sorted(
        copies.dashboards, key=lambda d: (str(d[1].get("title", "")).lower(), d[1]["uid"])
    ):
        directory = dirs[folder_uid] if folder_uid else GENERAL
        taken = taken_names.setdefault(directory, set())
        name = _unique(slug(str(dashboard.get("title", ""))), dashboard["uid"], taken)
        files[f"{DASHBOARDS_DIR}/{directory}/{name}.json"] = dashboard

    taken_playlists: set[str] = set()
    for playlist in sorted(copies.playlists, key=lambda p: (p["name"].lower(), p["uid"])):
        name = _unique(slug(playlist["name"]), playlist["uid"], taken_playlists)
        files[f"{PLAYLISTS_DIR}/{name}.json"] = playlist
    return files


def render(content: Any) -> str:
    """The file's text: sorted keys, so the same dashboard always gives the same file."""
    return json.dumps(content, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


@dataclass
class Changes:
    added: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)


def _managed(root: Path) -> set[str]:
    """The files in deploy/grafana/ that save writes (it leaves any others alone)."""
    found = [root / FOLDERS_FILE] if (root / FOLDERS_FILE).is_file() else []
    found += (root / DASHBOARDS_DIR).glob("*/*.json")
    found += (root / PLAYLISTS_DIR).glob("*.json")
    return {p.relative_to(root).as_posix() for p in found}


def write_copies(root: Path, files: dict[str, Any]) -> Changes:
    """Make deploy/grafana/ match ``files``: write new and changed ones, remove the rest."""
    changes = Changes()
    existing = _managed(root)
    for rel, content in sorted(files.items()):
        path = root / rel
        text = render(content)
        if rel in existing:
            if path.read_text(encoding="utf-8") == text:
                continue
            changes.changed.append(rel)
        else:
            changes.added.append(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    for rel in sorted(existing - files.keys()):
        (root / rel).unlink()
        changes.removed.append(rel)
    for directory in sorted((root / DASHBOARDS_DIR).glob("*/")):
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()
    return changes


# --------------------------------------------------------------------------- #
# Files -> Grafana
# --------------------------------------------------------------------------- #
def _read_json(path: Path, root: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CopiesError(f"Can't read {path.relative_to(root).as_posix()}: {exc}") from None


def load(root: Path) -> Copies:
    """Read the copies in deploy/grafana/."""
    copies = Copies()
    by_dir: dict[str, dict[str, Any]] = {}
    if (root / FOLDERS_FILE).is_file():
        by_dir = _read_json(root / FOLDERS_FILE, root)
    copies.folders = {folder["uid"]: folder for folder in by_dir.values()}
    for path in sorted((root / DASHBOARDS_DIR).glob("*/*.json")):
        directory = path.parent.name
        if directory == GENERAL:
            folder_uid = ""
        elif directory in by_dir:
            folder_uid = by_dir[directory]["uid"]
        else:
            raise CopiesError(
                f"{path.relative_to(root).as_posix()} is in a folder that isn't in "
                f"{FOLDERS_FILE}: {directory!r}"
            )
        copies.dashboards.append((folder_uid, _read_json(path, root)))
    for path in sorted((root / PLAYLISTS_DIR).glob("*.json")):
        copies.playlists.append(_read_json(path, root))
    return copies


def _parents_first(folders: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Folders in an order that creates each one after the folder it's in."""

    def depth(uid: str) -> int:
        n, seen = 0, {uid}
        while (parent := folders[uid].get("parentUid")) in folders and parent not in seen:
            seen.add(parent)
            uid = parent
            n += 1
        return n

    return [folders[uid] for uid in sorted(folders, key=lambda u: (depth(u), u))]


def restore(grafana: Grafana, copies: Copies) -> int:
    """Put the copies into Grafana. Returns how many folders had to be created."""
    created = 0
    for folder in _parents_first(copies.folders):
        if not grafana.exists(f"/api/folders/{_q(folder['uid'])}"):
            body = {"uid": folder["uid"], "title": folder["title"]}
            if folder.get("parentUid"):
                body["parentUid"] = folder["parentUid"]
            grafana.request("POST", "/api/folders", body)
            created += 1
    for folder_uid, dashboard in copies.dashboards:
        grafana.request(
            "POST",
            "/api/dashboards/db",
            {
                "dashboard": {**dashboard, "id": None},
                "folderUid": folder_uid,
                "overwrite": True,
                "message": "Restored from the copy in the repo",
            },
        )
    for playlist in copies.playlists:
        body = {k: playlist[k] for k in ("name", "interval", "items")}
        path = f"/api/playlists/{_q(playlist['uid'])}"
        if grafana.exists(path):
            grafana.request("PUT", path, body)
        else:
            grafana.request("POST", "/api/playlists", {"uid": playlist["uid"], **body})
    return created


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def _explain(exc: Exception, username: str | None = None) -> str:
    status = getattr(exc, "status", None)
    if status == 401 and username is None:
        return (
            f"Grafana asked for a login ({exc}). Viewing without signing in may be "
            "switched off: see docs/setting-up-the-server-pi.md, Step 8."
        )
    if status == 401:
        return f"Grafana refused the login for {username}: check the password."
    if status == 403:
        return f"The login {username} isn't allowed to change dashboards ({exc})."
    return str(exc)


def save() -> int:
    root = Path(sysconfig.GRAFANA_COPIES_DIR)
    try:
        copies = fetch(Grafana(sysconfig.GRAFANA_URL))
        changes = write_copies(root, layout(copies))
    except (GrafanaError, OSError) as exc:
        print(_explain(exc), file=sys.stderr)
        return 1
    print(f"Saved {copies.summary()} from {sysconfig.GRAFANA_URL} into {root}")
    for label, paths in (
        ("new", changes.added),
        ("changed", changes.changed),
        ("removed (no longer in Grafana)", changes.removed),
    ):
        for rel in paths:
            print(f"  {label}: {rel}")
    if changes.added or changes.changed or changes.removed:
        print("Commit these files to keep this version.")
    else:
        print("Nothing has changed since the last save.")
    return 0


def restore_command() -> int:
    root = Path(sysconfig.GRAFANA_COPIES_DIR)
    try:
        copies = load(root)
    except CopiesError as exc:
        print(exc, file=sys.stderr)
        return 1
    if not (copies.dashboards or copies.playlists):
        print(f"There are no copies in {root}. Run 'save' first.", file=sys.stderr)
        return 1
    username = sysconfig.GRAFANA_USERNAME
    print(f"This puts {copies.summary()} from {root} into Grafana at {sysconfig.GRAFANA_URL}.")
    print("Any with the same ID are replaced. Everything else in Grafana is left alone.")
    try:
        password = sysconfig.GRAFANA_PASSWORD or getpass.getpass(
            f"Grafana password for {username} (Ctrl+C to cancel): "
        )
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled: nothing was changed.", file=sys.stderr)
        return 1
    try:
        created = restore(Grafana(sysconfig.GRAFANA_URL, username, password), copies)
    except GrafanaError as exc:
        print(_explain(exc, username), file=sys.stderr)
        return 1
    print(f"Restored {copies.summary()} ({_count(created, 'folder')} created).")
    return 0


def run(args: list[str]) -> int:
    if args == ["save"]:
        return save()
    if args == ["restore"]:
        return restore_command()
    print(USAGE, file=sys.stderr)
    return EX_USAGE


def main() -> None:
    sys.exit(run(sys.argv[1:]))


if __name__ == "__main__":
    main()
