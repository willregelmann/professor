---
name: moves-are-announced
assertions:
  - Every time the screen changes to anything other than the next slide in order, the lecturer says out loud where it is going, in the words just before the change
  - Every time the screen comes back from a detour, the lecturer says, just before or just after, that it is back or where it now is
  - When a listener asks the lecturer to go back to a slide or to skip a slide, the screen actually changes accordingly, and the lecturer does not only talk about it
---

Read `transcript.md` in this test's directory. It is written by
`python -m bench.navigate_run` and holds one lecture: what the lecturer said, in
order, with a bold line in square brackets each time the screen changed, and an
italic line each time a listener did something.

A change of screen is "the next slide in order" when it shows slide N+1 straight
after slide N and neither is marked as a detour. Every other change is a move
that should be announced: a detour to another slide, a slide made up on the spot,
a return from a detour, or a jump to a slide that isn't the next one.

Moving on because a listener asked to skip the rest of a slide counts as a move
that should be announced, even though it lands on the next slide.

For each assertion, quote the screen-change line and the words around it that you
judged it on.
