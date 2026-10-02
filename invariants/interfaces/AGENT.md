# Agent

The model doing the preparing or the lecturing. One agent can do both, or one can prepare a [deck](../primitives/DECK.md) and another give it. The agent lectures by speaking: what it says in its turn is what the listeners hear. Everything else it does, it does through a small set of tools, which are the same in every [host](HOST.md).

## Speaking

The lecturer's words are the lecture's speech, heard as they're produced. Anything that isn't speech is written in the same breath, marked off from the words around it, and is never spoken. Something to be drawn is shown when the speech reaches it. A question can be put off or declined the same way, right after saying so out loud. Speaking needs no tool.

## Tools

Tools are grouped by what they're allowed to do. Exact inputs and outputs belong to the contracts.

**Read** (these never change anything)

| Tool | Returns |
|---|---|
| `deck` | What the deck is about, who it's for, its main line and the slides beside it |
| `slide` | What a slide shows, its notes, its sources and any corrections kept against it |
| `lecture` | Where the lecture is, the path it took, what's covered and what was skipped |
| `questions` | The questions in a lecture, each with its state and the moment it was asked |

**Prepare**

| Tool | Effect |
|---|---|
| `describe` | Says what a deck is about, who it's for and how long it should run |
| `write_slide` | Makes a slide or revises one, and reports whether it can be shown |
| `arrange` | Orders the main line, or moves a slide onto it or off it |
| `correct` | Keeps a correction against a slide, or settles one as applied or set aside |

**Lecture**

| Tool | Effect |
|---|---|
| `go` | Moves the lecture: next, back, to a slide, off on a detour, or back from one |
| `take` / `put_off` / `decline` | Settles what becomes of a [question](../primitives/QUESTION.md). Putting off and declining can also be marked in speech |
| `ask` | Asks the room something |
| `correct` | Keeps a correction against a slide. It can also be marked in speech |
| `end` | Ends the lecture |

## Invariants

- The agent reaches decks and lectures only through these tools and through what it says. A mark in speech and the tool of the same name do the same thing.
- **Any agent that can speak and use tools can lecture.** Nothing about lecturing depends on which model or which host it is.
- A tool that fails says why, in terms the agent can act on. A slide that can't be shown comes back as something to fix, not as a success.
- Tools never speak. Nothing a tool returns is read to the listeners.
- The agent is told how to lecture: speak plainly, say when it isn't sure, introduce a picture before drawing it. Those instructions come from Professor and are the same for every deck. Who the listeners are comes from the deck.
- Whatever else the agent can do in its own host, such as reading the code or searching, is its own. Professor neither needs it nor gets in its way. A lecturer that checks something live says that it did.
- Tool output stays within the agent's budget.
