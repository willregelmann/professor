---
name: preparing-a-deck
description: How to prepare a Professor deck that a lecturer can give well - slides with speaker notes, built with the professor tools (describe, write_slide, arrange). Use when asked to prepare, write or revise a lecture, a talk, a deck or slides on a codebase or a subject, when running /lecture, or when improving a deck after it has been given.
---

# Preparing a deck

A deck is what a lecturer brings to a subject: slides in the order they'd be given, each with speaker notes. Another agent gives it out loud, live, and takes questions. Your job is to hand that lecturer everything it needs.

## The one thing to get right

**The lecturer cannot see your sources.** It may run on another machine, with no access to the repository, the paper or this conversation. Whatever it needs to explain a slide, or to answer the obvious questions about it, has to be in the deck. Notes that say "see `server.py`" are useless to it.

## How to go about it

1. **Decide who it's for.** The same subject is a different lecture for a newcomer and for a maintainer. If the user hasn't said, ask once, or say what you assumed.
2. **Learn the subject first.** For a codebase, read it: entry points, the data model, the public surface, the tests. Check every claim against the source before it goes on a slide.
3. **`describe`** the deck: subject, audience, minutes, and background.
4. **`write_slide`** for each slide, in order.
5. **`deck`** to read back the main line. Check the order tells one story and the planned seconds fit the minutes. Use **`arrange`** to reorder or to set a slide aside.

## What goes where

| Field | What it's for |
|---|---|
| `title` | The point of the slide, in a few words |
| `notes` | What the lecturer should cover. Never shown, never read out |
| `text` | A few short lines to look at. Not sentences to read along with |
| `diagram` | A Mermaid diagram, when a picture really helps |
| `code` | A short excerpt, about a dozen lines at most, with `code_language` |
| `math` | One equation in LaTeX |
| `sources` | Where the slide's claims come from: `src/server.py:42`, a paper, a URL |
| `seconds` | Roughly how long to speak. 30 to 60 suits most slides |
| `background` (on the deck) | Facts, names and limits that hold across every slide |

**One slide makes one point.** If the notes cover two ideas, make two slides.

## Notes, not a script

Notes say what to cover. The lecturer finds the words, fresh, each time. Don't write narration, and don't write "Welcome everyone" or "On this slide we see".

Good notes carry the substance: the facts, the numbers, the names, the reason something is the way it is, the one example worth giving, and the mistake a newcomer usually makes. Write them as plain statements.

> **Weak:** "Explain how questions are handled in the lecture loop."
>
> **Strong:** "A question is marked heard the moment it arrives, before any agent sees it. It's taken at the end of the current slide, or straight away if a listener has stopped the lecturer. Every question ends answered, put off or declined; the core settles it even if the lecturer forgets. The reason: an agent that forgets a tool call must not be able to drop a question."

**Size the notes to the slide's time.** A lecturer speaks about 2.5 words a second, so a 40-second slide is about 100 words out loud. It is told to keep to that, so it can only say a fraction of long notes, and it chooses which fraction. Make that choice for it:

- Start the notes with the points that must be said, in a sentence or two each. Together they should fit the slide's `seconds`.
- Then a line that says `For questions:` and, under it, the supporting detail: the numbers, the edge cases, the history. The lecturer draws on this when someone asks, and doesn't try to say it all.
- If the points that must be said don't fit the time, the slide is two slides.

Put a claim's limits in the notes too. If you aren't sure of something, say so in the notes ("not verified: ...") so the lecturer says so out loud instead of asserting it.

## Background

`background` is for what no single slide's notes carry but questions will reach for: what the project is and isn't, who the people and components are, what's out of scope, what the lecturer shouldn't claim. The lecturer is told to stay inside the notes and the background and to say "I don't know" beyond them. A thin background gives a lecturer that can't answer much.

## What's shown

- **Diagrams:** keep them small, about twelve nodes or fewer. Prefer `flowchart LR`, `flowchart TD` or `sequenceDiagram`. In a flowchart, put any label with spaces or punctuation in double quotes: `A["api server (port 8642)"]`. In a sequence diagram use no quotes. No styling, no `classDef`, no click handlers.
- **The check:** `write_slide` draws the diagram and the equation before saving. If either fails, nothing is saved and the error comes back. Fix it and write the slide again. Don't drop the picture just to get past the check unless the slide is fine without it.
- **Code:** the smallest excerpt that makes the point, copied exactly, with its source.
- **Sources:** every slide that makes a claim about the code or a paper names where it comes from. Never cite a source you didn't draw the claim from.

## Revising after a lecture

A deck improves between lectures. `lecture` shows which slides were covered and which were skipped. `questions` shows what the listeners asked, on which slide, and what the lecturer said. A question that keeps coming up on the same slide means its notes are missing something: revise the slide with `write_slide` (only the fields you pass change), or add a slide. The main line changes only when you change it.

**Corrections.** When a listener catches a mistake, the lecturer keeps a correction against the slide. `deck` shows how many are open on each slide, and `slide` shows what they say. Deal with every open one, on purpose:

- Check it against the source. A listener, or the lecturer, can be wrong too.
- If it's right, fix the slide with `write_slide`, then `correct` with the correction's number and `settle: "applied"`.
- If the slide was right after all, `correct` with `settle: "set_aside"` and `why`.

You can also keep a correction yourself with `correct`, giving the slide and what's wrong, when you notice something you can't fix yet. A correction is never deleted.

Slides made up during a lecture are listed under `beside`, each with the question it was made for. If one earned its place, put it on the main line with `arrange`, and give it proper notes with `write_slide` first.

## If the tools aren't there

If the professor tools aren't available, the plugin's runtime isn't installed. Tell the user to run the plugin's `bin/professor install`, and don't try to write a deck some other way.
