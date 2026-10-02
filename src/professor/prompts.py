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
    "say \"I don't know\". If you find you WERE wrong, or that a slide is, say so out loud and then "
    "write a block that starts with a line ```correct, holds in one or two sentences what was wrong "
    "and what is right, and ends with a line ```. It is kept against the slide for whoever revises "
    "the deck.\n"
    "Or put it off: if a later slide covers it, say out loud that you will come to it and when, and "
    "then write a block that starts with a line ```later, holds the number of that slide (or the word "
    "end), and ends with a line ```. You will be handed the question again when the lecture gets there.\n"
    "Or decline it: if it is not something you should answer here, say so out loud and why, and then "
    "write a block that starts with a line ```decline, holds the reason in one short sentence, and ends "
    "with a line ```.\n"
    "These blocks are never spoken. If you write neither, the question counts as answered. When a "
    "question is really a request about pace, such as \"slower\" or \"less detail\", do not discuss it: "
    "say \"of course\" in a few words and do it from then on.\n\n"
    "MOVING AROUND. The next slide comes up by itself when you finish one; you never need to ask for "
    "it. You can also change what is on the screen, with a block that starts with a line ```go, holds "
    "one line, and ends with a line ```. That line is one of:\n"
    "a slide's number, to show that slide while you talk about it;\n"
    "return, to go back to where you were;\n"
    "next, to leave the slide you are on unfinished and move on, when the room asks you to skip it;\n"
    "from and a number (for example: from 2), to carry the lecture on from that slide.\n"
    "Showing a slide this way is a detour, and so is a slide or diagram you make while answering. "
    "ALWAYS say out loud where you are going before the block (\"let me go back to the diagram on "
    "slide two\"). End every detour yourself: when you have finished with it, say that you are going "
    "back (\"back to slide three\") and then write the return block. Never move silently. "
    "When the room asks you to go back to something or to skip something, do it; do not just talk "
    "about it.\n\n"
    "A SLIDE OF YOUR OWN. When an answer deserves something to look at, you can make a slide on the "
    "spot. Say that you are putting something on the screen, then write a block that starts with a "
    "line ```slide and ends with a line ```. Inside it, write a line \"title: \" with a short title, "
    "then any of \"text:\", \"diagram:\" (Mermaid, by the rules above) and \"code:\", each on its own "
    "line and followed by its content. The slide is shown as a detour and kept with the deck. A plain "
    "```mermaid block while you are answering a question does the same, with the question as its title."
)


WORDS_PER_S = 2.5  # about how fast the speech engine talks


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


def present(deck: Deck, slide: Slide, index: int, n: int, already_said: list[Utterance], *,
            before: str = "") -> str:
    """The lecture has reached a slide. `before` is what this lecture said the
    last time it gave this slide, if it has been here already."""
    seconds = _seconds(deck, slide, n)
    # A length in words holds a lecturer to time far better than one in seconds does.
    length = (f", in about {seconds} seconds of speech, which is at most about {round(seconds * WORDS_PER_S, -1):.0f} "
              "words; the notes hold more than there is time to say, so make the points they open with and "
              "keep anything under \"For questions\" for when someone asks" if seconds else "")
    text = (f"Present slide {index + 1} of {n}, \"{slide.title}\". Speaker notes (cover these in your own "
            f"words{length}, then stop):\n{slide.notes}")
    if slide.text:
        text += f"\n\nThe slide shows this text. Talk about it; do not read it out:\n{slide.text}"
    if slide.code:
        text += f"\n\nThe slide shows this code. Talk the room through it; do not read it out:\n{slide.code}"
    if slide.math and not slide.show_error:
        text += ("\n\nThe slide shows this equation, properly typeset. Say what it means and what each part "
                 f"is; do not read the symbols out one by one:\n{slide.math}")
    if (slide.diagram or slide.math) and slide.show_error:
        text += ("\n\nThis slide was meant to show a diagram or an equation, but it could not be drawn, so "
                 "the room cannot see it. Say so briefly and describe it in words instead. Do not draw "
                 "another:\n" + (slide.diagram or slide.math))
    elif slide.diagram:
        text += ("\n\nThis diagram is ALREADY on screen for this slide; walk the room through it and do "
                 f"not draw another:\n{slide.diagram}")
    if slide.sources:
        text += "\n\nThe slide cites: " + "; ".join(slide.sources)
    if before:
        text += ("\n\nYou have ALREADY given this slide once in this lecture, and the room has come back to "
                 "it. Do not give it again from the top. Say that you are back on it, then go over it "
                 f"more briefly, or differently, in the light of why you came back. What you said then:\n{before}")
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
           slides: list[Slide], can_put_off: bool, stopped_after: str | None = None) -> str:
    """A question is being taken. `stopped_after` is the last thing the room heard
    of the current slide, when a listener stopped the lecturer partway through it."""
    text = ""
    if stopped_after is not None:
        heard = (f"The last thing the room heard was: \"{stopped_after}\"" if stopped_after
                 else "The room heard nothing of it.")
        text = ("A listener stopped you partway through this slide. You are NOT finished with it. "
                f"{heard} Whatever you wrote after that was never heard.\n\n")
    text += f"{_asker(question)} asks, in the chat: {question.text}"
    if asked_on is not None and (on_slide is None or asked_on.id != on_slide.id):
        text += f"\n\nIt was asked earlier, while the slide \"{asked_on.title}\" was showing"
        text += f", just after you said: \"{just_said}\"" if just_said else "."
    elif just_said:
        text += f"\n\nIt was asked just after you said: \"{just_said}\""
    text += "\n\nReply out loud, briefly (under 30 seconds of speech, so at most about 75 words), then stop."
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
             "again. Otherwise answer it briefly (under 30 seconds of speech, so at most about 75 words). It cannot be put off again.")
    return text


def ago(seconds: float) -> str:
    """A gap in words: "about 20 seconds", "about 3 days"."""
    for size, name in ((86400, "day"), (3600, "hour"), (60, "minute")):
        if seconds >= size:
            n = round(seconds / size)
            return f"about {n} {name}{'' if n == 1 else 's'}"
    n = max(1, round(seconds))
    return f"about {n} second{'' if n == 1 else 's'}"


def _clip(text: str, words: int) -> str:
    parts = text.split()
    return text if len(parts) <= words else " ".join(parts[:words]) + " …"


def resume(gap_s: float, *, given: list[tuple[str, str]], questions: list[tuple[Question, str]],
           changes: list[str], stopped_in: str | None) -> str:
    """A lecture that stopped is being picked up. The lecturer may not be the one
    who gave the first part, so it's handed what the lecture kept, and nothing else."""
    record = "\n".join(f"- Slide \"{title}\": {_clip(said, 90) or '(nothing was said)'}" for title, said in given)
    asked = "\n".join(
        f"- {_asker(q)} asked: \"{q.text}\" ({q.state.replace('_', ' ')})" + (f" You said: {_clip(reply, 60)}" if reply else "")
        for q, reply in questions)
    text = (f"RESUMING. This lecture was started earlier and stopped {ago(gap_s)} ago. You are picking it up "
            "now. You may or may not be the lecturer who gave the first part. Either way, the record below is "
            "ALL that is known of it. Work from the record, and never claim to remember anything it does not "
            "show. If the room asks about something earlier that the record does not show, say that your "
            "record of the lecture does not have it, and ask them to remind you if it matters.\n\n"
            f"THE RECORD SO FAR.\n{record or '- Nothing had been said yet.'}")
    if asked:
        text += f"\nQuestions so far:\n{asked}"
    if changes:
        text += ("\n\nTHE DECK HAS CHANGED since the lecture stopped. Tell the room what changed, in a "
                 "sentence:\n" + "\n".join(f"- {c}" for c in changes))
    if gap_s < 120:
        text += ("\n\nThe gap was short. Do NOT recap and do not greet the room again. Carry straight on as "
                 "if you had paused for breath.")
    elif gap_s < 3600:
        text += ("\n\nBegin with ONE sentence saying that you are picking the lecture back up and where. Do "
                 "not recap what was covered.")
    else:
        text += ("\n\nIt has been a while, so begin with a recap sized to the gap: \"last time, we got as far "
                 "as…\", in two to four sentences drawn from the record, naming what was covered and any "
                 "question still open. Then carry on.")
    if stopped_in:
        text += (f"\n\nThe lecture stopped while you were in the middle of a slide. The last thing on the "
                 f"record is: \"{stopped_in}\" It may have been cut off partway, so start again from the "
                 "beginning of that thought, never mid-sentence.")
    return text


BACK_AT_THE_END = ("The lecture had already reached the end of its slides when it stopped. Say that you are "
                   "back and that the floor is open for questions. Do not present a slide.")


def after_pause(gap_s: float) -> str:
    """The lecture was paused for a good while and has been started again."""
    return (f"The lecture was paused for {ago(gap_s)} just now. Begin by picking the thread back up in one "
            "sentence, saying where you had got to.")


def close_detour(index: int, title: str) -> str:
    """The lecturer's turn ended with a detour still on the screen."""
    return ("Your turn ended with a detour still on the screen. The room must hear where the screen is going "
            f"before it goes. In one short sentence, say that you are going back to slide {index + 1}, "
            f"\"{title}\". Say nothing else.")


def redraw(code: str, error: str) -> str:
    """A diagram the lecturer drew during its turn failed on the page."""
    return ("The diagram you just drew could not be shown: the room saw an error where the picture should "
            f"be.\n\nThe error:\n{error}\n\nWhat you wrote:\n{code}\n\n"
            "Say in one short sentence that you will try that picture again, then draw a corrected "
            "diagram and stop. If you cannot see how to fix it, say you will carry on without it.")
