"""The store: decks, slides, lectures and what each lecture kept.

One SQLite file. Nothing outside the core writes to it. Slide revisions and
utterances are only ever added to: correcting a slide adds a revision, and a
lecture's record of what was said and drawn is never rewritten. The one thing
filled in afterwards is whether a diagram could be drawn, which is only known
once a page has tried.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

SCHEMA = """
CREATE TABLE decks (
    id          INTEGER PRIMARY KEY,
    subject     TEXT NOT NULL,
    audience    TEXT NOT NULL DEFAULT '',
    minutes     INTEGER,
    background  TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);
CREATE TABLE slides (
    id              INTEGER PRIMARY KEY,
    deck_id         INTEGER NOT NULL REFERENCES decks(id),
    position        INTEGER,            -- place on the main line; NULL when beside it
    anchor_slide_id INTEGER REFERENCES slides(id),
    origin          TEXT NOT NULL CHECK (origin IN ('prepared', 'made_up')),
    created_at      TEXT NOT NULL
);
CREATE TABLE slide_revisions (
    id          INTEGER PRIMARY KEY,
    slide_id    INTEGER NOT NULL REFERENCES slides(id),
    title       TEXT NOT NULL,
    shown       TEXT NOT NULL,          -- JSON: text, diagram, code
    notes       TEXT NOT NULL,
    sources     TEXT NOT NULL,          -- JSON list
    seconds     INTEGER,
    show_error  TEXT,                   -- why it couldn't be shown, once that's known
    created_at  TEXT NOT NULL
);
CREATE TABLE lectures (
    id            INTEGER PRIMARY KEY,
    deck_id       INTEGER NOT NULL REFERENCES decks(id),
    host          TEXT NOT NULL,
    host_session  TEXT NOT NULL DEFAULT '',
    state         TEXT NOT NULL,
    state_message TEXT NOT NULL DEFAULT '',
    slide_id      INTEGER REFERENCES slides(id),
    detours       TEXT NOT NULL DEFAULT '[]',
    created_at    TEXT NOT NULL,
    ended_at      TEXT
);
CREATE TABLE visits (
    id                INTEGER PRIMARY KEY,
    lecture_id        INTEGER NOT NULL REFERENCES lectures(id),
    slide_revision_id INTEGER NOT NULL REFERENCES slide_revisions(id),
    how               TEXT NOT NULL,    -- how the lecture got here: start, next, ...
    covered           INTEGER NOT NULL DEFAULT 0,
    arrived_at        TEXT NOT NULL
);
CREATE TABLE utterances (
    id         INTEGER PRIMARY KEY,
    lecture_id INTEGER NOT NULL REFERENCES lectures(id),
    visit_id   INTEGER NOT NULL REFERENCES visits(id),
    kind       TEXT NOT NULL CHECK (kind IN ('said', 'drawn')),
    text       TEXT NOT NULL,
    failed     TEXT,                    -- why it wasn't heard or seen, if it wasn't
    at         TEXT NOT NULL
);
"""
VERSION = 1


def default_path() -> Path:
    return Path(os.environ.get("PROFESSOR_DB", Path.home() / ".professor" / "professor.db"))


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")


@dataclass(frozen=True)
class Deck:
    id: int
    subject: str
    audience: str
    minutes: int | None
    background: str


@dataclass(frozen=True)
class Slide:
    """A slide as it currently stands: the slide and its latest revision."""
    id: int
    deck_id: int
    position: int | None
    origin: str
    revision_id: int
    title: str
    text: str
    diagram: str
    code: str
    notes: str
    sources: list[str] = field(default_factory=list)
    seconds: int | None = None
    show_error: str | None = None

    def shown(self) -> dict:
        """What a listener sees. Never the notes."""
        return {"id": self.id, "title": self.title, "text": self.text, "diagram": self.diagram,
                "code": self.code, "sources": self.sources}


@dataclass(frozen=True)
class Utterance:
    id: int
    visit_id: int
    kind: str
    text: str
    failed: str | None


class Store:
    def __init__(self, path: Path | str = ":memory:"):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        if path != ":memory:":
            self.db.execute("PRAGMA journal_mode = WAL")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version == 0:
            self.db.executescript(SCHEMA)
            self.db.execute(f"PRAGMA user_version = {VERSION}")
            self.db.commit()
        elif version != VERSION:
            raise RuntimeError(f"{path} is at schema version {version}; this code expects {VERSION}")

    def close(self) -> None:
        self.db.close()

    def _insert(self, sql: str, *args) -> int:
        with self.db:
            return self.db.execute(sql, args).lastrowid

    # Decks

    def create_deck(self, subject: str, *, audience: str = "", minutes: int | None = None,
                    background: str = "") -> Deck:
        deck_id = self._insert(
            "INSERT INTO decks (subject, audience, minutes, background, created_at) VALUES (?, ?, ?, ?, ?)",
            subject, audience, minutes, background, _now())
        return self.deck(deck_id)

    def deck(self, deck_id: int) -> Deck:
        row = self.db.execute("SELECT id, subject, audience, minutes, background FROM decks WHERE id = ?",
                              (deck_id,)).fetchone()
        if row is None:
            raise KeyError(f"no deck {deck_id}")
        return Deck(**row)

    def decks(self) -> list[Deck]:
        return [Deck(**row) for row in
                self.db.execute("SELECT id, subject, audience, minutes, background FROM decks ORDER BY id")]

    # Slides

    def add_slide(self, deck_id: int, *, title: str, notes: str, text: str = "", diagram: str = "",
                  code: str = "", sources: list[str] | None = None, seconds: int | None = None,
                  origin: str = "prepared", on_main_line: bool = True) -> Slide:
        """Add a slide. On the main line it goes at the end; otherwise it sits beside it."""
        position = None
        if on_main_line:
            position = self.db.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM slides WHERE deck_id = ?",
                                       (deck_id,)).fetchone()[0]
        slide_id = self._insert(
            "INSERT INTO slides (deck_id, position, origin, created_at) VALUES (?, ?, ?, ?)",
            deck_id, position, origin, _now())
        self.revise_slide(slide_id, title=title, notes=notes, text=text, diagram=diagram, code=code,
                          sources=sources, seconds=seconds)
        return self.slide(slide_id)

    def revise_slide(self, slide_id: int, *, title: str, notes: str, text: str = "", diagram: str = "",
                     code: str = "", sources: list[str] | None = None, seconds: int | None = None) -> Slide:
        """Add a revision. The latest is what the slide currently says."""
        self._insert(
            "INSERT INTO slide_revisions (slide_id, title, shown, notes, sources, seconds, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            slide_id, title, json.dumps({"text": text, "diagram": diagram, "code": code}), notes,
            json.dumps(sources or []), seconds, _now())
        return self.slide(slide_id)

    _SLIDE = """
        SELECT s.id, s.deck_id, s.position, s.origin, r.id AS revision_id, r.title, r.shown, r.notes,
               r.sources, r.seconds, r.show_error
        FROM slides s JOIN slide_revisions r ON r.slide_id = s.id
        WHERE r.id = (SELECT MAX(id) FROM slide_revisions WHERE slide_id = s.id)
    """

    @staticmethod
    def _slide(row: sqlite3.Row) -> Slide:
        shown = json.loads(row["shown"])
        return Slide(id=row["id"], deck_id=row["deck_id"], position=row["position"], origin=row["origin"],
                     revision_id=row["revision_id"], title=row["title"], text=shown.get("text", ""),
                     diagram=shown.get("diagram", ""), code=shown.get("code", ""), notes=row["notes"],
                     sources=json.loads(row["sources"]), seconds=row["seconds"], show_error=row["show_error"])

    def slide(self, slide_id: int) -> Slide:
        row = self.db.execute(self._SLIDE + " AND s.id = ?", (slide_id,)).fetchone()
        if row is None:
            raise KeyError(f"no slide {slide_id}")
        return self._slide(row)

    def main_line(self, deck_id: int) -> list[Slide]:
        """The slides in the order they'd be given if nobody interrupted."""
        return [self._slide(row) for row in self.db.execute(
            self._SLIDE + " AND s.deck_id = ? AND s.position IS NOT NULL ORDER BY s.position", (deck_id,))]

    def record_show_error(self, revision_id: int, error: str) -> None:
        with self.db:
            self.db.execute("UPDATE slide_revisions SET show_error = ? WHERE id = ?", (error, revision_id))

    # Lectures

    def create_lecture(self, deck_id: int, host: str) -> int:
        return self._insert("INSERT INTO lectures (deck_id, host, state, created_at) VALUES (?, ?, 'ready', ?)",
                            deck_id, host, _now())

    def lecture(self, lecture_id: int) -> dict:
        row = self.db.execute("SELECT * FROM lectures WHERE id = ?", (lecture_id,)).fetchone()
        if row is None:
            raise KeyError(f"no lecture {lecture_id}")
        return dict(row)

    def set_state(self, lecture_id: int, state: str, message: str = "") -> None:
        with self.db:
            self.db.execute("UPDATE lectures SET state = ?, state_message = ?, ended_at = ? WHERE id = ?",
                            (state, message, _now() if state == "ended" else None, lecture_id))

    def set_host_session(self, lecture_id: int, session: str) -> None:
        with self.db:
            self.db.execute("UPDATE lectures SET host_session = ? WHERE id = ?", (session, lecture_id))

    def arrive(self, lecture_id: int, slide: Slide, how: str) -> int:
        """The lecture has reached a slide: move its place and record the visit."""
        with self.db:
            self.db.execute("UPDATE lectures SET slide_id = ? WHERE id = ?", (slide.id, lecture_id))
            return self.db.execute(
                "INSERT INTO visits (lecture_id, slide_revision_id, how, arrived_at) VALUES (?, ?, ?, ?)",
                (lecture_id, slide.revision_id, how, _now())).lastrowid

    def mark_covered(self, visit_id: int) -> None:
        with self.db:
            self.db.execute("UPDATE visits SET covered = 1 WHERE id = ?", (visit_id,))

    def path(self, lecture_id: int) -> list[dict]:
        """Which slides the lecture showed, in order, and whether each was covered."""
        return [dict(row) for row in self.db.execute(
            "SELECT v.id AS visit_id, r.slide_id, v.slide_revision_id, v.how, v.covered "
            "FROM visits v JOIN slide_revisions r ON r.id = v.slide_revision_id "
            "WHERE v.lecture_id = ? ORDER BY v.id", (lecture_id,))]

    def utter(self, lecture_id: int, visit_id: int, kind: str, text: str, failed: str | None = None) -> int:
        return self._insert(
            "INSERT INTO utterances (lecture_id, visit_id, kind, text, failed, at) VALUES (?, ?, ?, ?, ?, ?)",
            lecture_id, visit_id, kind, text, failed, _now())

    def record_draw_failure(self, utterance_id: int, error: str) -> None:
        """A drawing is recorded when it's sent; whether it drew is only known once a page has tried."""
        with self.db:
            self.db.execute("UPDATE utterances SET failed = ? WHERE id = ? AND kind = 'drawn' AND failed IS NULL",
                            (error, utterance_id))

    def utterances(self, lecture_id: int, visit_id: int | None = None) -> list[Utterance]:
        sql = "SELECT id, visit_id, kind, text, failed FROM utterances WHERE lecture_id = ?"
        args: tuple = (lecture_id,)
        if visit_id is not None:
            sql, args = sql + " AND visit_id = ?", (lecture_id, visit_id)
        return [Utterance(**row) for row in self.db.execute(sql + " ORDER BY id", args)]
