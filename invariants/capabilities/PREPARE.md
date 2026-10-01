# Prepare

Builds or revises a [deck](../primitives/DECK.md) from a subject and whatever it can be learned from: a codebase, a paper, a conversation, what the agent already knows. Preparing is what happens before a lecture and between lectures. Making up a slide in the middle of one is the same act done quickly, and it's held to the same rules.

## Invariants

- Preparing produces [slides](../primitives/SLIDE.md) with notes, never narration.
- **Everything the lecturer will need goes into the deck.** Preparing assumes the lecturer won't be able to reach the sources.
- A claim taken from a source carries that source onto its slide.
- **What's prepared can be shown.** A diagram that doesn't draw is fixed or reported while preparing, not discovered in front of the listeners.
- A deck is prepared for someone. It says who it's for and roughly how long it's meant to run.
- Preparing is optional. A lecture can start from an empty deck.
- Whoever prepares a deck needn't be the one who gives it.
- Preparing can use what past [lectures](../primitives/LECTURE.md) kept: the questions that were asked and the corrections that were made. A correction kept against a slide is applied or set aside on purpose, never lost.
- Moving a made-up slide onto the main line is preparing, and happens only on purpose.
