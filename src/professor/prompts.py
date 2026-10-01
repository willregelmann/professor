"""What the lecturer is told: the standing instructions, the same for every deck,
and the prompt for each moment.

The standing instructions are the prototype's preamble with Wren and Ash taken
out. Who the listeners are and what the lecturer should stay inside come from
the deck.
"""
from __future__ import annotations

from professor.store import Deck, Question, Slide, Utterance

STANDING = (
    "You are giving a live lecture, out loud{audience}. Everything you write is turned into speech "
    "the moment you write it, so: plain spoken prose only, no markdown, no lists, no headings, no emoji. "
    "Short sentences. Speak in the first person as yourself. Do not use tools unless a question genuinely "
    "needs a live check.\n\n"
    "VISUAL AIDS. You can put a diagram on the screen behind you. Write a Mermaid diagram inside "
    "a block that starts with a line ```mermaid and ends with a line ```. That block is DRAWN, "
    "never spoken, and appears on screen at the moment your speech reaches it, so introduce it "
    "in speech first (for example: \"here is how that flows\") and then talk the room through it. "
    "Use one only when a picture genuinely helps: flows between components, sequences of messages, "
    "layers, state machines. At most one per turn. Keep it small, about twelve nodes or fewer, and "
    "prefer flowchart LR, flowchart TD, or sequenceDiagram. In a flowchart, put every node label "
    "containing spaces or punctuation in double quotes, e.g. A[\"api_server (port 8642)\"]. In a "
    "sequenceDiagram use NO quotes at all -- participant aliases and message text are literal there, "
    "so quotes would appear on screen. No styling, no classDef, no click handlers.\n\n"
    "BEING WRONG. Stay inside what the speaker notes and the background below give you. When you are "
    "not sure of something, say so at the moment you say it. When something goes beyond what you have, "
    "say that plainly rather than inventing detail.\n\n"
    "QUESTIONS. The room can ask questions in a chat, and you will be handed them one at a time. Whatever "
    "you say in reply is heard by everyone, so start by making clear whose question it is and what it "
    "asks, in a few words. Then do exactly one of three things.\n"
    "Answer it, briefly. If it asks whether something you said is right, check what you said against "
    "your notes rather than defending it, and say so plainly if you were wrong. If you do not know, "
    "say \"I don't know\".\n"
    "Or put it off: if a later slide covers it, say out loud that you will come to it and when, and "
    "then write a block that starts with a line ```later, holds the number of that slide (or the word "
    "end), and ends with a line ```. You will be handed the question again when the lecture gets there.\n"
    "Or decline it: if it is not something you should answer here, say so out loud and why, and then "
    "write a block that starts with a line ```decline, holds the reason in one short sentence, and ends "
    "with a line ```.\n"
    "These blocks are never spoken. If you write neither, the question counts as answered. When a "
    "question is really a request about pace, such as \"slower\" or \"less detail\", do not discuss it: "
    "say \"of course\" in a few words and do it from then on."
)


def standing(deck: Deck, slides: list[Slide]) -> str:
    """Sent once, at the start of a host session."""
    parts = [STANDING.format(audience=f", to {deck.audience}" if deck.audience else "")]
    if deck.background:
        parts.append(f"BACKGROUND. {deck.background}")
    outline = "\n".join(f"Slide {i + 1}: {s.title}" + (" (has a diagram on screen)" if s.diagram else "")
                        for i, s in enumerate(slides))
    parts.append(f"The lecture is \"{deck.subject}\". Here is the whole deck so you know where it is going:\n\n"
                 + outline)
    return "\n\n".join(parts)


def _seconds(deck: Deck, slide: Slide, n: int) -> int | None:
    if slide.seconds:
        return slide.seconds
    return round(deck.minutes * 60 / n) if deck.minutes and n else None


def present(deck: Deck, slide: Slide, index: int, n: int, already_said: list[Utterance]) -> str:
    """The lecture has reached a slide."""
    seconds = _seconds(deck, slide, n)
    length = f", about {seconds} seconds of speech" if seconds else ""
    text = (f"Present slide {index + 1} of {n}, \"{slide.title}\". Speaker notes (cover these in your own "
            f"words{length}, then stop):\n{slide.notes}")
    if slide.text:
        text += f"\n\nThe slide shows this text. Talk about it; do not read it out:\n{slide.text}"
    if slide.code:
        text += f"\n\nThe slide shows this code. Talk the room through it; do not read it out:\n{slide.code}"
    if slide.diagram and slide.show_error:
        text += ("\n\nThis slide was meant to show a diagram, but it could not be drawn, so the room cannot "
                 "see it. Say so briefly and describe it in words instead. Do not draw another:\n"
                 + slide.diagram)
    elif slide.diagram:
        text += ("\n\nThis diagram is ALREADY on screen for this slide; walk the room through it and do "
                 f"not draw another:\n{slide.diagram}")
    if slide.sources:
        text += "\n\nThe slide cites: " + "; ".join(slide.sources)
    heard, lost = [], []  # what the room has heard of this slide, and what was cut off since
    for u in already_said:
        if u.kind != "said" or u.question_id is not None:
            continue
        if not u.failed:
            heard.append(u.text)
            lost = []  # said after an earlier stop, so that thought was picked up
        elif u.failed in ("cut off", "unsaid"):
            lost.append(u.text)
    said, lost = " ".join(heard), " ".join(lost)
    if said:
        text += ("\n\nYou were cut off partway through this slide. The room has already heard this, so do "
                 f"not repeat it. Pick up from where it stops:\n{said}")
    if lost:
        text += ("\n\nA listener stopped you there. This is the thought you were in the middle of, which "
                 f"the room did not hear in full. Say that you are picking the slide back up, and finish it:\n{lost}")
    return text


def _asker(question: Question) -> str:
    return question.asker or "Someone in the room"


def answer(question: Question, asked_on: Slide | None, just_said: str, *, on_slide: Slide | None,
           slides: list[Slide], can_put_off: bool) -> str:
    """A question is being taken."""
    text = f"{_asker(question)} asks, in the chat: {question.text}"
    if asked_on is not None and (on_slide is None or asked_on.id != on_slide.id):
        text += f"\n\nIt was asked earlier, while the slide \"{asked_on.title}\" was showing"
        text += f", just after you said: \"{just_said}\"" if just_said else "."
    elif just_said:
        text += f"\n\nIt was asked just after you said: \"{just_said}\""
    text += "\n\nReply out loud, briefly (under 30 seconds of speech), then stop."
    later = [f"slide {i + 1} (\"{s.title}\")" for i, s in enumerate(slides)
             if on_slide is not None and s.position is not None and on_slide.position is not None
             and s.position > on_slide.position]
    if can_put_off and later:
        text += " The slides still to come are " + ", ".join(later) + "."
    elif can_put_off:
        text += " There are no slides still to come, so it can only be put off to the end."
    else:
        text += " It cannot be put off: answer it now, or decline it."
    return text


def come_back(question: Question, words_then: str, *, at_end: bool) -> str:
    """The lecture has reached the point a question was put off to."""
    where = ("The lecture has reached the end of its slides" if at_end
             else "You have just presented the slide you put this question off to")
    text = (f"Earlier, {_asker(question)} asked, in the chat: {question.text}\n\n"
            f"You put it off. {where}.")
    if words_then:
        text += f" What you said at the time was: \"{words_then}\""
    text += ("\n\nSay out loud that you are coming back to that question, and whose it was. If what you "
             "have just presented already answered it, say so in a sentence or two and do not go over it "
             "again. Otherwise answer it briefly (under 30 seconds of speech). It cannot be put off again.")
    return text


def redraw(code: str, error: str) -> str:
    """A diagram the lecturer drew during its turn failed on the page."""
    return ("The diagram you just drew could not be shown: the room saw an error where the picture should "
            f"be.\n\nThe error:\n{error}\n\nWhat you wrote:\n{code}\n\n"
            "Say in one short sentence that you will try that picture again, then draw a corrected "
            "diagram and stop. If you cannot see how to fix it, say you will carry on without it.")
