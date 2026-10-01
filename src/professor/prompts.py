"""What the lecturer is told: the standing instructions, the same for every deck,
and the prompt for each moment.

The standing instructions are the prototype's preamble with Wren and Ash taken
out. Who the listeners are and what the lecturer should stay inside come from
the deck.
"""
from __future__ import annotations

from professor.store import Deck, Slide, Utterance

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
    "say that plainly rather than inventing detail."
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
    said = " ".join(u.text for u in already_said if u.kind == "said" and not u.failed)
    if said:
        text += ("\n\nYou were cut off partway through this slide. The room has already heard this, so do "
                 f"not repeat it. Pick up from where it stops:\n{said}")
    return text


def redraw(code: str, error: str) -> str:
    """A diagram the lecturer drew during its turn failed on the page."""
    return ("The diagram you just drew could not be shown: the room saw an error where the picture should "
            f"be.\n\nThe error:\n{error}\n\nWhat you wrote:\n{code}\n\n"
            "Say in one short sentence that you will try that picture again, then draw a corrected "
            "diagram and stop. If you cannot see how to fix it, say you will carry on without it.")
