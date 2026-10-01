"""The store: decks, slides, lectures and what each lecture kept.

One SQLite file. Nothing outside the core writes to it. Slide revisions and
utterances are only ever added to: correcting a slide adds a revision, and a
lecture's record of what was said and drawn is never rewritten. The only things
filled in afterwards are whether a diagram could be drawn, which is known once a
page has tried, and whether a sentence was cut off by a listener partway.
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

# Each entry takes the store from the version before it to its own.
MIGRATIONS = {
    2: """
CREATE TABLE questions (
    id            INTEGER PRIMARY KEY,
    lecture_id    INTEGER NOT NULL REFERENCES lectures(id),
    asker         TEXT NOT NULL,          -- '' when they haven't said who they are
    text          TEXT NOT NULL,
    visit_id      INTEGER REFERENCES visits(id),          -- the slide that was showing
    after_utterance_id INTEGER REFERENCES utterances(id), -- what had just been said
    state         TEXT NOT NULL CHECK (state IN
                      ('waiting', 'heard', 'answering', 'put_off', 'answered', 'declined')),
    until_slide_id INTEGER REFERENCES slides(id),         -- put off until here; NULL means the end
    reason        TEXT NOT NULL DEFAULT '',               -- why it was declined
    asked_at      TEXT NOT NULL,
    settled_at    TEXT
);
ALTER TABLE utterances ADD COLUMN question_id INTEGER REFERENCES questions(id);
""",
    3: """
-- 1 once a revision has been drawn in a check and came out right.
ALTER TABLE slide_revisions ADD COLUMN show_checked INTEGER NOT NULL DEFAULT 0;
""",
    4: """
-- The question a made-up slide was made for.
ALTER TABLE slides ADD COLUMN question_id INTEGER REFERENCES questions(id);
""",
}
VERSION = max(MIGRATIONS)

# The states a question can be left in for good.
SETTLED = ("answered", "declined")


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
    show_error: str | None = None   # why it couldn't be shown, once a check or a page has tried
    show_checked: bool = False      # a check drew it and it came out right
    code_language: str = ""
    math: str = ""
    anchor_slide_id: int | None = None  # for a made-up slide: the slide the lecture was on
    question_id: int | None = None      # for a made-up slide: the question it was made for

    def shown(self) -> dict:
        """What a listener sees. Never the notes. A diagram or equation that's known
        not to draw isn't sent to be tried again: the slide says it couldn't be shown."""
        failed = bool(self.show_error)
        return {"id": self.id, "title": self.title, "text": self.text,
                "diagram": "" if failed else self.diagram, "math": "" if failed else self.math,
                "code": self.code, "code_language": self.code_language, "sources": self.sources,
                "show_error": self.show_error}


@dataclass(frozen=True)
class Utterance:
    id: int
    visit_id: int
    kind: str
    text: str
    failed: str | None
    question_id: int | None = None


@dataclass(frozen=True)
class Question:
    id: int
    lecture_id: int
    asker: str
    text: str
    visit_id: int | None
    after_utterance_id: int | None
    state: str
    until_slide_id: int | None
    reason: str


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
        if version > VERSION:
            raise RuntimeError(f"{path} is at schema version {version}; this code only knows up to {VERSION}")
        if version == 0:
            self.db.executescript(SCHEMA)
            version = 1
        for target in range(version + 1, VERSION + 1):
            self.db.executescript(MIGRATIONS[target])
        self.db.execute(f"PRAGMA user_version = {VERSION}")
        self.db.commit()

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

    def describe_deck(self, deck_id: int, *, subject: str | None = None, audience: str | None = None,
                      minutes: int | None = None, background: str | None = None) -> Deck:
        """Change what a deck says about itself. Anything left out stays as it was."""
        deck = self.deck(deck_id)
        with self.db:
            self.db.execute(
                "UPDATE decks SET subject = ?, audience = ?, minutes = ?, background = ? WHERE id = ?",
                (deck.subject if subject is None else subject, deck.audience if audience is None else audience,
                 deck.minutes if minutes is None else minutes,
                 deck.background if background is None else background, deck_id))
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
                  code: str = "", code_language: str = "", math: str = "", sources: list[str] | None = None,
                  seconds: int | None = None, show_checked: bool = False, show_error: str | None = None,
                  origin: str = "prepared", on_main_line: bool = True,
                  anchor_slide_id: int | None = None, question_id: int | None = None) -> Slide:
        """Add a slide. On the main line it goes at the end; otherwise it sits beside it."""
        position = None
        if on_main_line:
            position = self.db.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM slides WHERE deck_id = ?",
                                       (deck_id,)).fetchone()[0]
        slide_id = self._insert(
            "INSERT INTO slides (deck_id, position, anchor_slide_id, question_id, origin, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)", deck_id, position, anchor_slide_id, question_id, origin, _now())
        self.revise_slide(slide_id, title=title, notes=notes, text=text, diagram=diagram, code=code,
                          code_language=code_language, math=math, sources=sources, seconds=seconds,
                          show_checked=show_checked, show_error=show_error)
        return self.slide(slide_id)

    def revise_slide(self, slide_id: int, *, title: str, notes: str, text: str = "", diagram: str = "",
                     code: str = "", code_language: str = "", math: str = "",
                     sources: list[str] | None = None, seconds: int | None = None,
                     show_checked: bool = False, show_error: str | None = None) -> Slide:
        """Add a revision. The latest is what the slide currently says."""
        shown = {"text": text, "diagram": diagram, "code": code, "code_language": code_language, "math": math}
        self._insert(
            "INSERT INTO slide_revisions (slide_id, title, shown, notes, sources, seconds, show_checked, "
            "show_error, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            slide_id, title, json.dumps(shown), notes, json.dumps(sources or []), seconds,
            int(show_checked), show_error, _now())
        return self.slide(slide_id)

    _SLIDE = """
        SELECT s.id, s.deck_id, s.position, s.origin, s.anchor_slide_id, s.question_id,
               r.id AS revision_id, r.title, r.shown, r.notes,
               r.sources, r.seconds, r.show_error, r.show_checked
        FROM slides s JOIN slide_revisions r ON r.slide_id = s.id
        WHERE r.id = (SELECT MAX(id) FROM slide_revisions WHERE slide_id = s.id)
    """

    @staticmethod
    def _slide(row: sqlite3.Row) -> Slide:
        shown = json.loads(row["shown"])
        return Slide(id=row["id"], deck_id=row["deck_id"], position=row["position"], origin=row["origin"],
                     revision_id=row["revision_id"], title=row["title"], text=shown.get("text", ""),
                     diagram=shown.get("diagram", ""), code=shown.get("code", ""), notes=row["notes"],
                     sources=json.loads(row["sources"]), seconds=row["seconds"], show_error=row["show_error"],
                     show_checked=bool(row["show_checked"]), code_language=shown.get("code_language", ""),
                     math=shown.get("math", ""), anchor_slide_id=row["anchor_slide_id"],
                     question_id=row["question_id"])

    def slide(self, slide_id: int) -> Slide:
        row = self.db.execute(self._SLIDE + " AND s.id = ?", (slide_id,)).fetchone()
        if row is None:
            raise KeyError(f"no slide {slide_id}")
        return self._slide(row)

    def main_line(self, deck_id: int) -> list[Slide]:
        """The slides in the order they'd be given if nobody interrupted."""
        return [self._slide(row) for row in self.db.execute(
            self._SLIDE + " AND s.deck_id = ? AND s.position IS NOT NULL ORDER BY s.position", (deck_id,))]

    def beside(self, deck_id: int) -> list[Slide]:
        """The slides that aren't on the main line."""
        return [self._slide(row) for row in self.db.execute(
            self._SLIDE + " AND s.deck_id = ? AND s.position IS NULL ORDER BY s.id", (deck_id,))]

    def arrange(self, deck_id: int, slide_ids: list[int]) -> list[Slide]:
        """Set the main line: these slides, in this order. Every other slide of
        the deck sits beside it. Nothing is deleted."""
        mine = {row[0] for row in self.db.execute("SELECT id FROM slides WHERE deck_id = ?", (deck_id,))}
        if len(set(slide_ids)) != len(slide_ids):
            raise ValueError("a slide can only be on the main line once")
        if strangers := [i for i in slide_ids if i not in mine]:
            raise ValueError(f"deck {deck_id} has no slide {strangers[0]}")
        with self.db:
            self.db.execute("UPDATE slides SET position = NULL WHERE deck_id = ?", (deck_id,))
            for position, slide_id in enumerate(slide_ids):
                self.db.execute("UPDATE slides SET position = ? WHERE id = ?", (position, slide_id))
        return self.main_line(deck_id)

    def record_show_error(self, revision_id: int, error: str) -> None:
        """A page tried to draw this revision and couldn't, whatever a check said before."""
        with self.db:
            self.db.execute("UPDATE slide_revisions SET show_error = ?, show_checked = 0 WHERE id = ?",
                            (error, revision_id))

    def lectures(self, deck_id: int) -> list[dict]:
        return [dict(row) for row in self.db.execute(
            "SELECT * FROM lectures WHERE deck_id = ? ORDER BY id", (deck_id,))]

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

    def utter(self, lecture_id: int, visit_id: int, kind: str, text: str, failed: str | None = None,
              question_id: int | None = None) -> int:
        """Record something said or drawn. `question_id` marks it as part of the reply to a question."""
        return self._insert(
            "INSERT INTO utterances (lecture_id, visit_id, kind, text, failed, question_id, at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            lecture_id, visit_id, kind, text, failed, question_id, _now())

    def record_draw_failure(self, utterance_id: int, error: str) -> None:
        """A drawing is recorded when it's sent; whether it drew is only known once a page has tried."""
        with self.db:
            self.db.execute("UPDATE utterances SET failed = ? WHERE id = ? AND kind = 'drawn' AND failed IS NULL",
                            (error, utterance_id))

    def cut_off(self, utterance_id: int) -> None:
        """A listener stopped the lecturer partway through this sentence."""
        with self.db:
            self.db.execute("UPDATE utterances SET failed = 'cut off' WHERE id = ? AND failed IS NULL",
                            (utterance_id,))

    def utterances(self, lecture_id: int, visit_id: int | None = None) -> list[Utterance]:
        sql = "SELECT id, visit_id, kind, text, failed, question_id FROM utterances WHERE lecture_id = ?"
        args: tuple = (lecture_id,)
        if visit_id is not None:
            sql, args = sql + " AND visit_id = ?", (lecture_id, visit_id)
        return [Utterance(**row) for row in self.db.execute(sql + " ORDER BY id", args)]

    # Questions

    _QUESTION = ("SELECT id, lecture_id, asker, text, visit_id, after_utterance_id, state, until_slide_id, "
                 "reason FROM questions")

    def ask(self, lecture_id: int, asker: str, text: str, *, state: str = "heard") -> Question:
        """Keep a question, with the moment it was asked: the slide that was
        showing and the last thing that had been said."""
        visit = self.db.execute("SELECT MAX(id) FROM visits WHERE lecture_id = ?", (lecture_id,)).fetchone()[0]
        last = self.db.execute("SELECT MAX(id) FROM utterances WHERE lecture_id = ? AND kind = 'said' "
                               "AND failed IS NULL", (lecture_id,)).fetchone()[0]
        question_id = self._insert(
            "INSERT INTO questions (lecture_id, asker, text, visit_id, after_utterance_id, state, asked_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)", lecture_id, asker, text, visit, last, state, _now())
        return self.question(question_id)

    def question(self, question_id: int) -> Question:
        row = self.db.execute(self._QUESTION + " WHERE id = ?", (question_id,)).fetchone()
        if row is None:
            raise KeyError(f"no question {question_id}")
        return Question(**row)

    def questions(self, lecture_id: int, state: str | None = None) -> list[Question]:
        """A lecture's questions in the order they were asked."""
        sql, args = self._QUESTION + " WHERE lecture_id = ?", (lecture_id,)
        if state is not None:
            sql, args = sql + " AND state = ?", (lecture_id, state)
        return [Question(**row) for row in self.db.execute(sql + " ORDER BY id", args)]

    def set_question(self, question_id: int, state: str, *, until_slide_id: int | None = None,
                     reason: str = "") -> Question:
        with self.db:
            self.db.execute(
                "UPDATE questions SET state = ?, until_slide_id = ?, reason = ?, settled_at = ? WHERE id = ?",
                (state, until_slide_id, reason, _now() if state in SETTLED else None, question_id))
        return self.question(question_id)

    def reply(self, question_id: int) -> str:
        """What was said in reply to a question, in writing."""
        return " ".join(row[0] for row in self.db.execute(
            "SELECT text FROM utterances WHERE question_id = ? AND kind = 'said' "
            "AND (failed IS NULL OR failed = 'cut off') ORDER BY id", (question_id,)))

    def made_for(self, question_id: int) -> list[Slide]:
        """The slides that were made up for a question."""
        return [self._slide(row) for row in self.db.execute(
            self._SLIDE + " AND s.question_id = ? ORDER BY s.id", (question_id,))]

    def said_on(self, lecture_id: int, slide_id: int, before_visit: int) -> str:
        """What this lecture has already said while giving a slide, on earlier visits to it."""
        return " ".join(row[0] for row in self.db.execute(
            "SELECT u.text FROM utterances u JOIN visits v ON v.id = u.visit_id "
            "JOIN slide_revisions r ON r.id = v.slide_revision_id "
            "WHERE u.lecture_id = ? AND r.slide_id = ? AND v.id < ? AND u.kind = 'said' "
            "AND u.failed IS NULL AND u.question_id IS NULL ORDER BY u.id",
            (lecture_id, slide_id, before_visit)))

    def moment(self, question: Question) -> tuple[Slide | None, str]:
        """The moment a question was asked: the slide as it was shown then, and
        the last thing that had been said."""
        slide = None
        if question.visit_id is not None:
            row = self.db.execute(
                "SELECT r.slide_id FROM visits v JOIN slide_revisions r ON r.id = v.slide_revision_id "
                "WHERE v.id = ?", (question.visit_id,)).fetchone()
            slide = self.slide(row[0])
        said = ""
        if question.after_utterance_id is not None:
            said = self.db.execute("SELECT text FROM utterances WHERE id = ?",
                                   (question.after_utterance_id,)).fetchone()[0]
        return slide, said
