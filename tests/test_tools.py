"""The agent's tools: reading decks and lectures, and preparing a deck."""
import io
import json
import shutil

import pytest

from professor import mcp_server, showcheck, tools
from professor.showcheck import Check
from professor.store import Store


def check(diagram="", math=""):
    """A stand-in for the browser: anything containing "broken" can't be drawn."""
    bad = [f"The {name} couldn't be drawn: Parse error" for name, value in (("diagram", diagram), ("equation", math))
           if "broken" in value]
    return Check(checked=True, error="\n".join(bad) or None)


@pytest.fixture
def t():
    return tools.Tools(Store(), check=check)


def call(t, name, **args):
    text, failed = t.call(name, args)
    return json.loads(text), failed


def test_a_deck_is_described_then_filled_with_slides(t):
    deck, failed = call(t, "describe", subject="How Professor works", audience="new contributors", minutes=5,
                        background="Professor gives live lectures.")
    assert not failed and deck["deck"] == 1 and deck["main_line"] == []

    one, _ = call(t, "write_slide", deck=1, title="The loop", notes="The server owns the loop.",
                  diagram="flowchart LR\n A --> B", sources=["src/professor/lecture.py:1"], seconds=40)
    two, _ = call(t, "write_slide", deck=1, title="The store", notes="One SQLite file.", code="SELECT 1",
                  code_language="sql", math="a^2 + b^2 = c^2")
    assert (one["number"], two["number"]) == (1, 2) and one["show"] == "checked, can be shown"

    deck, _ = call(t, "deck", deck=1)
    assert [(s["number"], s["title"], s["shows"]) for s in deck["main_line"]] == [
        (1, "The loop", ["diagram"]), (2, "The store", ["code", "math"])]
    assert deck["audience"] == "new contributors" and deck["planned_seconds"] == 40
    assert "notes" not in json.dumps(deck)                      # the notes are read one slide at a time
    assert call(t, "slide", slide=one["slide"])[0]["notes"] == "The server owns the loop."
    assert call(t, "deck")[0] == {"decks": [{"deck": 1, "subject": "How Professor works",
                                             "audience": "new contributors", "slides": 2}]}


def test_a_slide_that_cannot_be_shown_is_not_saved_and_comes_back_to_be_fixed(t):
    call(t, "describe", subject="Subject")
    result, failed = call(t, "write_slide", deck=1, title="Bad", notes="n", diagram="flowchart broken")
    assert failed and result["error"].startswith("Not saved. The diagram couldn't be drawn: Parse error")
    assert call(t, "deck", deck=1)[0]["main_line"] == []

    # The same goes for a revision: the slide stays as it was.
    good, _ = call(t, "write_slide", deck=1, title="Good", notes="n", diagram="flowchart LR\n A --> B")
    result, failed = call(t, "write_slide", slide=good["slide"], math="\\broken{")
    assert failed and "The equation couldn't be drawn" in result["error"]
    still, _ = call(t, "slide", slide=good["slide"])
    assert still["math"] == "" and still["show"] == "checked, can be shown"


def test_revising_a_slide_changes_only_what_is_given():
    store = Store()
    t = tools.Tools(store, check=check)
    call(t, "describe", subject="Subject")
    made, _ = call(t, "write_slide", deck=1, title="Old title", notes="Old notes", text="On screen", seconds=30)
    revised, _ = call(t, "write_slide", slide=made["slide"], title="New title")
    assert (revised["title"], revised["notes"], revised["text"], revised["seconds"]) == (
        "New title", "Old notes", "On screen", 30)
    assert revised["slide"] == made["slide"] and len(store.main_line(1)) == 1


def test_a_slide_that_could_not_be_checked_is_saved_with_a_warning():
    t = tools.Tools(Store(), check=lambda **_: Check(checked=False, error="no browser was found"))
    call(t, "describe", subject="Subject")
    made, failed = call(t, "write_slide", deck=1, title="T", notes="n", diagram="flowchart LR\n A --> B")
    assert not failed and made["show"] == "not checked"
    assert made["warning"].startswith("Saved, but it could not be checked")


def test_arranging_orders_the_main_line_and_keeps_what_is_left_out(t):
    call(t, "describe", subject="Subject")
    a, b, c = (call(t, "write_slide", deck=1, title=x, notes="n")[0]["slide"] for x in "abc")
    deck, failed = call(t, "arrange", deck=1, main_line=[c, a])
    assert not failed and [s["title"] for s in deck["main_line"]] == ["c", "a"]
    assert [s["title"] for s in deck["beside"]] == ["b"]


@pytest.mark.parametrize("name, args, message", [
    ("write_slide", {"title": "T", "notes": "n"}, "a new slide needs the deck it belongs to"),
    ("write_slide", {"deck": 1, "title": "T"}, "a slide needs speaker notes"),
    ("write_slide", {"deck": 1, "notes": "n"}, "a slide needs a title"),
    ("write_slide", {"deck": 9, "title": "T", "notes": "n"}, "no deck 9"),
    ("write_slide", {"deck": 1, "title": "T", "notes": "n", "script": "x"}, "a slide has no 'script'"),
    ("describe", {}, "a new deck needs a subject"),
    ("slide", {"slide": 9}, "no slide 9"),
    ("arrange", {"deck": 1, "main_line": [9]}, "deck 1 has no slide 9"),
    ("lecture", {}, "no lecture has been given yet"),
    ("fly", {}, "there is no tool called 'fly'"),
    ("deck", {"decks": 1}, "arguments it doesn't take"),
])
def test_a_tool_that_fails_says_why(t, name, args, message):
    call(t, "describe", subject="Subject")
    result, failed = call(t, name, **args)
    assert failed and message in result["error"]


def test_a_past_lecture_can_be_read_back_when_revising(t):
    store = t.store
    call(t, "describe", subject="Subject")
    one, two = (call(t, "write_slide", deck=1, title=x, notes="n")[0]["slide"] for x in ("One", "Two"))
    lecture = store.create_lecture(1, "fake")
    visit = store.arrive(lecture, store.slide(one), "start")
    said = store.utter(lecture, visit, "said", "The first slide, said aloud.")
    q = store.ask(lecture, "Priya", "Why is that?")
    store.utter(lecture, visit, "said", "Because it is.", question_id=q.id)
    store.set_question(q.id, "answered")
    store.mark_covered(visit)

    read, _ = call(t, "lecture")
    assert read["path"] == [{"slide": one, "title": "One", "how": "start", "covered": True}]
    assert read["not_covered"] == [{"slide": two, "title": "Two"}] and read["questions"] == {"answered": 1}
    asked, _ = call(t, "questions")
    assert asked["questions"] == [{"question": q.id, "who": "Priya", "text": "Why is that?", "state": "answered",
                                   "asked_on": {"slide": one, "title": "One"},
                                   "just_after": "The first slide, said aloud.", "reason": "",
                                   "reply": "Because it is."}]
    assert said and call(t, "deck", deck=1)[0]["lectures"] == [lecture]


def test_the_tools_are_served_over_mcp(tmp_path):
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "describe", "arguments": {"subject": "Over MCP"}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "slide", "arguments": {"slide": 7}}},
        {"jsonrpc": "2.0", "id": 5, "method": "nonsense"},
    ]
    out = io.StringIO()
    mcp_server.serve(tmp_path / "p.db", io.StringIO("".join(json.dumps(r) + "\n" for r in requests) + "garbage\n"),
                     out)
    replies = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [r["id"] for r in replies] == [1, 2, 3, 4, 5, None]
    assert replies[0]["result"]["serverInfo"]["name"] == "professor"
    assert [tool["name"] for tool in replies[1]["result"]["tools"]] == [
        "deck", "slide", "lecture", "questions", "describe", "write_slide", "arrange"]
    assert all(tool["inputSchema"]["type"] == "object" for tool in replies[1]["result"]["tools"])
    assert replies[2]["result"]["isError"] is False
    assert json.loads(replies[2]["result"]["content"][0]["text"])["subject"] == "Over MCP"
    assert replies[3]["result"]["isError"] is True and "no slide 7" in replies[3]["result"]["content"][0]["text"]
    assert replies[4]["error"]["code"] == -32601 and replies[5]["error"]["code"] == -32700
    assert Store(tmp_path / "p.db").decks()[0].subject == "Over MCP"       # it was really saved


needs_browser = pytest.mark.skipif(showcheck.browser() is None, reason="no browser to draw with")


@needs_browser
def test_the_show_check_draws_with_the_pages_own_renderers():
    assert showcheck.check(diagram='flowchart LR\n  A["one & <two>"] --> B["three"]', math="a^2 + b^2 = c^2").ok
    bad = showcheck.check(diagram="flowchart LR\n  A[broken -->", math="\\frac{1}{")
    assert bad.checked and not bad.ok
    assert "The diagram couldn't be drawn: Parse error" in bad.error
    assert "The equation couldn't be drawn: KaTeX parse error" in bad.error


def test_with_no_browser_the_check_says_it_could_not_be_made(monkeypatch):
    monkeypatch.setenv("PROFESSOR_BROWSER", "no-such-browser")
    result = showcheck.check(diagram="flowchart LR\n A --> B")
    assert not result.checked and not result.ok and "no browser was found" in result.error
    assert showcheck.check().ok                                   # nothing to draw is nothing to fail
    assert shutil.which("no-such-browser") is None
