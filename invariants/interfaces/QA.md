# Q&A

The chat beside the [presentation](PRESENTATION.md): where the listeners talk back, and where they can see what became of what they said.

## What a question shows

| State | Meaning |
|---|---|
| Heard | The lecturer has it, and hasn't taken it yet |
| Being answered | The lecturer is answering it now |
| Put off | The lecturer said when it will come back to it |
| Answered | With what was said, and the slide that was made for it if there was one |
| Declined | With the reason the lecturer gave |

## Invariants

- **Anything written here is a [question](../primitives/QUESTION.md).** There are no commands and nothing to learn. A listener writes what they'd say to a person: "wait, why?", "slower", "go back to the diagram".
- The chat is always open, including while the lecturer is speaking.
- Every question shows its state from the moment it's sent, and the state is always one of the five above.
- [Answers](../capabilities/ANSWER.md) are given out loud in the presentation. The chat keeps them in writing, so an answer can be read back later.
- Everyone in the lecture sees every question and what became of it.
- A listener can say who they are. A question from someone who hasn't is from "someone", never from a guessed name.
- When the lecturer asks the room something, it appears here, and replies to it are given here.
- A question sent while the lecturer can't be reached is kept and shown as waiting. It's never lost and never shown as heard.
- The chat is the lecture's own record of its questions. A lecture that's [picked up later](../capabilities/RESUME.md) shows it again.
