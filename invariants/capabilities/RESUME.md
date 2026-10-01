# Resume

Picks up a [lecture](../primitives/LECTURE.md) that stopped, whether it was paused on purpose or cut off, and whether that was a minute ago or a month.

## Invariants

- A lecture resumes where it stopped, with its path and its [questions](../primitives/QUESTION.md) intact. Questions that were put off are still open.
- Resuming begins with a recap sized to the gap. A minute needs none. A month needs "last time, we got as far as…".
- **Resuming works from what the lecture kept, and doesn't pretend to remember more.** When the lecturer isn't sure what was covered, it asks.
- A lecture cut off mid-thought resumes at the start of that thought, never mid-sentence.
- A lecture that stopped without being paused resumes the same way as one that was.
- A different lecturer can resume a lecture. What a lecture knows about itself is kept with the lecture, not in the head of whoever gave it.
- If the [deck](../primitives/DECK.md) changed in the meantime, the lecturer says what changed that matters. If the slide it stopped on is gone, it resumes at the nearest point that's left and says so.
- Resuming isn't starting over. Giving the deck again from the beginning is a new lecture.
