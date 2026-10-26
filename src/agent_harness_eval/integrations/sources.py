"""Local source observations and independent admitted/recommended pointers."""
import json
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from ..workspace.models import identity


def repository(value):
    if value.startswith("https://"):
        url = urlsplit(value)
        if url.hostname != "github.com" or url.username or url.password or url.port or url.query or url.fragment or not re.fullmatch(r"/[\w.-]+/[\w.-]+(?:\.git)?/?", url.path):
            raise ValueError("Expected a public GitHub repository URL without credentials")
        return "https://github.com/" + url.path.strip("/").removesuffix(".git").lower() + ".git"
    path = Path(value)
    if not path.is_absolute() or not (path / ".git").exists():
        raise ValueError("Expected a GitHub URL or an explicit local Git repository")
    return str(path.resolve())


class Sources:
    def __init__(self, root):
        self.root = Path(root).resolve()
        directory = self.root / ".workspace"
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "sources.sqlite"
        with self.transaction() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sources (
                    id TEXT PRIMARY KEY, repository TEXT NOT NULL, ref TEXT NOT NULL,
                    recipe TEXT, observed TEXT, admitted TEXT, recommended TEXT,
                    enabled INTEGER NOT NULL DEFAULT 1, failures INTEGER NOT NULL DEFAULT 0,
                    next_poll REAL NOT NULL DEFAULT 0, refresh_job TEXT, poll_sequence INTEGER NOT NULL DEFAULT 0,
                    error TEXT, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS revisions (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, sha TEXT NOT NULL,
                    status TEXT NOT NULL, job TEXT, run_job TEXT, release TEXT, error TEXT, observed REAL NOT NULL, attempt INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(source,sha));
                CREATE TABLE IF NOT EXISTS source_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL, created REAL NOT NULL);
            """)
            if "attempt" not in {row[1] for row in db.execute("PRAGMA table_info(revisions)")}:
                db.execute("ALTER TABLE revisions ADD COLUMN attempt INTEGER NOT NULL DEFAULT 0")
            if "pinned" not in {row[1] for row in db.execute("PRAGMA table_info(revisions)")}:
                db.execute("ALTER TABLE revisions ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0")

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE")
            with db:
                yield db
        finally:
            db.close()

    def event(self, db, source, kind, value):
        db.execute("INSERT INTO source_events(source,kind,payload,created) VALUES(?,?,?,?)", (source, kind, json.dumps(value), time.time()))

    def add(self, url, ref="main", recipe=None):
        url = repository(url)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", ref) or ".." in ref or "//" in ref or ref.endswith(("/", ".lock")):
            raise ValueError("Invalid source ref")
        key = identity({"repository": url, "ref": ref})
        if recipe:
            from .schema import load

            path = Path(recipe).resolve()
            value = load(path)
            if repository(value.source.repository) != url:
                raise ValueError("Recipe source differs from tracked repository")
            recipe = str(path)
        with self.transaction() as db:
            existing = db.execute("SELECT * FROM sources WHERE id=?", (key,)).fetchone()
            if existing:
                if recipe and recipe != existing["recipe"]:
                    raise ValueError("Source already exists; submit a repair recipe explicitly")
                return dict(existing)
            db.execute("INSERT INTO sources(id,repository,ref,recipe,updated) VALUES(?,?,?,?,?)", (key, url, ref, recipe, time.time()))
            self.event(db, key, "registered", {"repository": url, "ref": ref})
        return self.get(key)

    def get(self, source):
        with self.transaction() as db:
            row = db.execute("SELECT * FROM sources WHERE id=?", (source,)).fetchone()
            if row is None:
                raise KeyError(source)
            return dict(row)

    def list(self):
        with self.transaction() as db:
            return [dict(row) for row in db.execute("SELECT * FROM sources ORDER BY updated DESC")]

    def enable(self, source, enabled):
        self.get(source)
        with self.transaction() as db:
            db.execute("UPDATE sources SET enabled=?,next_poll=0 WHERE id=?", (int(enabled), source))
            self.event(db, source, "monitor_resumed" if enabled else "monitor_paused", {})
        return self.get(source)

    def revisions(self, source):
        with self.transaction() as db:
            return [dict(row) for row in db.execute("SELECT * FROM revisions WHERE source=? ORDER BY sequence", (source,))]

    def observe(self, source, sha):
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError("Source observation requires a full commit SHA")
        with self.transaction() as db:
            row = db.execute("SELECT * FROM sources WHERE id=?", (source,)).fetchone()
            if not row:
                raise KeyError(source)
            db.execute("INSERT OR IGNORE INTO revisions(source,sha,status,observed) VALUES(?,?,'observed',?)", (source, sha, time.time()))
            if row["observed"] != sha:
                self.event(db, source, "observed", {"previous": row["observed"], "sha": sha})
                db.execute("UPDATE revisions SET observed=? WHERE source=? AND sha=?", (time.time(), source, sha))
                previous = db.execute("SELECT * FROM revisions WHERE source=? AND sha=?", (source, sha)).fetchone()
                if previous["status"] == "admitted":
                    db.execute("UPDATE sources SET admitted=? WHERE id=?", (previous["release"], source))
                elif previous["status"] == "superseded":
                    self.event(db, source, "revived", dict(previous))
                    db.execute("UPDATE revisions SET status='observed',job=NULL,run_job=NULL,release=NULL,error=NULL,attempt=attempt+1 WHERE source=? AND sha=?", (source, sha))
            # An old frozen run never appears here; only later admission can adopt.
            db.execute("UPDATE sources SET observed=?,failures=0,error=NULL,next_poll=?,updated=? WHERE id=?", (sha, time.time() + 60, time.time(), source))
        return self.get(source)

    def pin_merge(self, source, sha):
        """Retain an actual merge SHA without pretending it is the current branch tip."""
        self.get(source)
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError("Merge observation requires a full commit SHA")
        with self.transaction() as db:
            previous = db.execute("SELECT * FROM revisions WHERE source=? AND sha=?", (source, sha)).fetchone()
            db.execute("INSERT OR IGNORE INTO revisions(source,sha,status,observed,pinned) VALUES(?,?,'observed',0,1)", (source, sha))
            db.execute("UPDATE revisions SET pinned=1 WHERE source=? AND sha=?", (source, sha))
            if not previous or not previous["pinned"]:
                self.event(db, source, "merge_pinned", {"sha": sha})
            if previous and previous["status"] == "superseded":
                db.execute("UPDATE revisions SET status='observed',job=NULL,run_job=NULL,error=NULL,attempt=attempt+1 WHERE source=? AND sha=?", (source, sha))

    def failure(self, source, message):
        with self.transaction() as db:
            row = db.execute("SELECT failures FROM sources WHERE id=?", (source,)).fetchone()
            failures = row[0] + 1
            db.execute("UPDATE sources SET failures=?,error=?,next_poll=?,updated=? WHERE id=?", (failures, message, time.time() + min(3600, 30 * 2 ** min(failures, 7)), time.time(), source))
            self.event(db, source, "refresh_failed", {"reason": message})

    def update_revision(self, source, sha, **values):
        if not values or set(values) - {"status", "job", "run_job", "release", "error"}:
            raise ValueError("Invalid revision update")
        with self.transaction() as db:
            if not db.execute("SELECT 1 FROM revisions WHERE source=? AND sha=?", (source, sha)).fetchone():
                raise KeyError(sha)
            db.execute("UPDATE revisions SET " + ",".join(name + "=?" for name in values) + " WHERE source=? AND sha=?", (*values.values(), source, sha))

    def admit(self, source, sha, release_id, check):
        from .store import Releases, verify

        catalog = Releases(self.root)
        catalog.agent(release_id)
        receipt = verify(catalog.directory / release_id)
        if repository((receipt["release"].get("source") or {}).get("repository", "")) != self.get(source)["repository"]:
            raise ValueError("Release belongs to another source")
        if (receipt["release"].get("source", {}).get("commit") != sha or check.get("release_id") != release_id
                or not check.get("levels", {}).get("runnable", {}).get("passed")):
            raise ValueError("Admission requires matching source and successful runtime evidence")
        if identity({key: value for key, value in check.items() if key != "check_id"}) != check.get("check_id"):
            raise ValueError("Invalid conformance identity")
        with self.transaction() as db:
            revision = db.execute("SELECT * FROM revisions WHERE source=? AND sha=?", (source, sha)).fetchone()
            if not revision:
                raise KeyError(sha)
            db.execute("UPDATE revisions SET status='admitted',release=?,error=NULL WHERE source=? AND sha=?", (release_id, source, sha))
            latest = db.execute("SELECT release FROM revisions WHERE source=? AND status='admitted' ORDER BY observed DESC,sequence DESC LIMIT 1", (source,)).fetchone()[0]
            db.execute("UPDATE sources SET admitted=?,updated=? WHERE id=?", (latest, time.time(), source))
            self.event(db, source, "admitted", {"sha": sha, "release": release_id, "check": check["check_id"]})

    def round_release(self, source):
        value = self.get(source)
        if not value["admitted"]:
            raise ValueError("Source has no admitted Release")
        from .store import Releases

        # Returned immutable bundle identity is copied into the next Plan.
        catalog = Releases(self.root)
        if (catalog.directory / value["admitted"] / "disabled.json").exists():
            raise ValueError("Admitted Release is disabled; enable it or admit a replacement")
        return catalog.agent(value["admitted"])
