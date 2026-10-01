from pathlib import Path

import pytest

from professor import importers, prompts
from professor.showcheck import Check
from professor.store import Store


def drawn(**_):
    """A show check that always passes, so these tests don't need a browser."""
    return Check(checked=True)

PROTOTYPE_DECK = Path(__file__).resolve().parent.parent / "prototype" / "deck.json"


@pytest.fixture
def store():
    return Store()


def test_the_main_line_is_the_slides_in_the_order_they_were_added(store):
    deck = store.create_deck("Subject")
    for title in ("one", "two", "three"):
        store.add_slide(deck.id, title=title, notes="n")
    beside = store.add_slide(deck.id, title="aside", notes="n", origin="made_up", on_main_line=False)
    assert [s.title for s in store.main_line(deck.id)] == ["one", "two", "three"]
    assert beside.position is None


def test_revising_a_slide_keeps_what_it_used_to_say(store):
    deck = store.create_deck("Subject")
    slide = store.add_slide(deck.id, title="old", notes="old notes")
    lecture = store.create_lecture(deck.id, "fake")
    visit = store.arrive(lecture, slide, "start")

    revised = store.revise_slide(slide.id, title="new", notes="new notes")

    assert revised.id == slide.id and revised.revision_id != slide.revision_id
    assert store.slide(slide.id).title == "new"                 # what's current
    assert store.main_line(deck.id)[0].title == "new"
    shown_then = store.path(lecture)[0]["slide_revision_id"]    # what that lecture actually showed
    assert shown_then == slide.revision_id and store.path(lecture)[0]["visit_id"] == visit


def test_what_a_listener_sees_never_includes_the_notes(store):
    deck = store.create_deck("Subject")
    slide = store.add_slide(deck.id, title="t", notes="secret notes", text="shown", sources=["a.py:3"])
    assert "secret notes" not in str(slide.shown())
    assert slide.shown()["sources"] == ["a.py:3"]


def test_a_lecture_keeps_its_path_and_its_words(store):
    deck = store.create_deck("Subject")
    slide = store.add_slide(deck.id, title="t", notes="n")
    lecture = store.create_lecture(deck.id, "fake")
    visit = store.arrive(lecture, slide, "start")
    store.utter(lecture, visit, "said", "First sentence.")
    drawn = store.utter(lecture, visit, "drawn", "graph TD")
    store.utter(lecture, visit, "said", "Lost sentence.", failed="no audio")
    store.record_draw_failure(drawn, "parse error")
    store.mark_covered(visit)

    assert [(u.kind, u.text, u.failed) for u in store.utterances(lecture)] == [
        ("said", "First sentence.", None), ("drawn", "graph TD", "parse error"),
        ("said", "Lost sentence.", "no audio")]
    assert store.path(lecture) == [{"visit_id": visit, "slide_id": slide.id,
                                    "slide_revision_id": slide.revision_id, "how": "start", "covered": 1}]
    assert store.lecture(lecture)["slide_id"] == slide.id


def test_the_store_survives_being_reopened(tmp_path):
    path = tmp_path / "deep" / "professor.db"
    deck = Store(path).create_deck("Subject", audience="anyone", minutes=5)
    assert Store(path).deck(deck.id) == deck


def test_the_prototype_deck_becomes_a_stored_deck(store):
    deck = importers.import_prototype_deck(store, PROTOTYPE_DECK, audience="a room of software engineers", check=drawn)
    slides = store.main_line(deck.id)
    assert deck.subject == "Two agents, one house: how Wren and Ash work"
    assert [s.title for s in slides] == ["Who is talking", "Memory is three layers, not one",
                                         "Every feature began as a quiet failure"]
    assert [s.seconds for s in slides] == [35, 50, 50] and deck.minutes == 2
    assert slides[1].diagram.startswith("flowchart LR") and not slides[0].diagram
    assert all(s.origin == "prepared" for s in slides)
    assert slides[1].show_checked and not slides[1].show_error


def test_an_imported_slide_that_fails_its_check_is_kept_and_marked(store):
    deck = importers.import_prototype_deck(
        store, PROTOTYPE_DECK, check=lambda diagram="", **_: Check(True, "Parse error" if diagram else None))
    slides = store.main_line(deck.id)
    assert [bool(s.show_error) for s in slides] == [False, True, False]
    # A diagram that's known not to draw is never sent to a listener to be tried again.
    assert slides[1].shown()["diagram"] == "" and slides[1].shown()["show_error"] == "Parse error"


def test_arranging_sets_the_main_line_and_deletes_nothing(store):
    deck = store.create_deck("Subject")
    a, b, c = (store.add_slide(deck.id, title=t, notes="n").id for t in "abc")
    assert [s.id for s in store.arrange(deck.id, [c, a])] == [c, a]
    assert [s.id for s in store.beside(deck.id)] == [b]
    with pytest.raises(ValueError, match="only be on the main line once"):
        store.arrange(deck.id, [a, a])
    with pytest.raises(ValueError, match="has no slide 999"):
        store.arrange(deck.id, [999])
    assert [s.id for s in store.main_line(deck.id)] == [c, a]      # a refused arrangement changes nothing


def test_the_standing_instructions_carry_the_deck_and_no_one_agents_persona(store):
    deck = importers.import_prototype_deck(store, PROTOTYPE_DECK, audience="a room of software engineers",
                                           background="Wren runs on a Raspberry Pi.", check=drawn)
    text = prompts.standing(deck, store.main_line(deck.id))
    assert "You are Wren" not in text and "Ash runs on" not in text
    assert "to a room of software engineers" in text
    assert "BACKGROUND. Wren runs on a Raspberry Pi." in text
    assert "Slide 2: Memory is three layers, not one (has a diagram on screen)" in text


def test_the_lecturer_is_told_what_the_room_can_and_cannot_see(store):
    deck = store.create_deck("Subject", minutes=3)
    slide = store.add_slide(deck.id, title="t", notes="the notes", diagram="graph TD\n A-->B", text="On screen",
                            sources=["server.py:12"])
    prompt = prompts.present(deck, slide, 0, 3, [])
    assert "about 60 seconds" in prompt and "the notes" in prompt
    assert "ALREADY on screen" in prompt and "do not read it out" in prompt and "server.py:12" in prompt

    store.record_show_error(slide.revision_id, "parse error")
    prompt = prompts.present(deck, store.slide(slide.id), 0, 3, [])
    assert "could not be drawn" in prompt and "ALREADY on screen" not in prompt
