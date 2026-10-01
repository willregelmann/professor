# Navigate

Moves a [lecture](../primitives/LECTURE.md) through its [deck](../primitives/DECK.md): on to the next slide, back to an earlier one, off the main line for a detour, and back again.

## Invariants

- A lecture is always at exactly one [slide](../primitives/SLIDE.md), and it's the one everyone is looking at.
- The lecturer can move the lecture, and so can a listener. Every move is visible to everyone.
- A move that isn't simply "next" is said out loud. The lecturer never skips or jumps silently.
- **A detour remembers where it left.** Detours can happen inside detours, and the way back is never lost.
- Coming back to a slide that has already been given isn't giving it again. The lecturer returns to it knowing what was said there.
- The lecture knows which slides it has covered and which it skipped.
- The listeners can always tell where they are: how far through the main line, and whether this is a detour.
- Moving through a deck never changes its order.
- Reaching the end of the main line doesn't end the lecture. It opens the floor. A lecture ends when the lecturer or the listeners end it, and that is said out loud.
