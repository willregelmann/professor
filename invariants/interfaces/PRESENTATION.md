# Presentation

What the listeners see and hear: the [slide](../primitives/SLIDE.md) the [lecture](../primitives/LECTURE.md) has reached, and the lecturer's voice talking them through it.

## What it carries

| | |
|---|---|
| The slide | Whatever the current slide shows: text, a diagram, code, an equation, and its sources |
| The voice | The lecturer's words, spoken as they're made |
| The words | The same words, readable |
| The place | How far through the main line the lecture is, and whether this is a detour |
| The state | What the lecturer is doing right now: speaking, thinking, listening, paused, or unable to go on |

## Invariants

- The presentation shows exactly one slide, the one the lecture is at, and it's the same for everyone.
- **Voice and slide stay together.** Something drawn appears when the speech reaches it.
- Only speech is spoken, and only what's meant to be seen is shown. Marks meant for the screen are never read out, and never left lying in the words.
- Everything said can also be read. A listener with the sound off misses nothing that was said.
- **Silence is never ambiguous.** The listeners can always tell whether the lecturer is thinking, listening, paused or gone.
- Failures are shown where the room can see them: a slide that wouldn't draw, a sentence that couldn't be spoken, a lecturer that can't be reached.
- A listener can pause the voice and start it again at any moment, and nothing is lost by pausing.
- Someone who arrives late sees the lecture as it is now: the current slide, the place and the state.
- The presentation decides nothing. Everything it shows and says comes from the lecture.
