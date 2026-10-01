# Deliver

Gives a [slide](../primitives/SLIDE.md): shows it, and talks the listeners through it out loud. The notes say what to cover. How to say it this time, to these listeners, after everything that has already been said, is the lecturer's judgment.

## Invariants

- **The words are made fresh.** They come from the slide's notes and the [lecture](../primitives/LECTURE.md) so far, never from a stored script.
- Delivering doesn't repeat what the lecture has already established, and it can lean on it ("as we saw a moment ago").
- **What's seen and what's heard stay together.** Something drawn appears at the point where the speech reaches it, not before and not after.
- Only speech is spoken. Nothing meant for the screen is ever read out.
- The listeners aren't left in unexplained silence. When the lecturer needs a moment, they can tell it's thinking.
- The lecturer says when it isn't sure of something, at the moment it says it.
- Delivering a slide ends when its point is made. Between slides there's always a moment when a [question](../primitives/QUESTION.md) can be taken.
- A listener can stop the lecturer mid-sentence. The thought that was cut off isn't lost, and the lecturer can pick it up again.
- The lecturer checks whether a point landed when it judges the check is worth the interruption, not after every slide.
- One sentence that can't be spoken doesn't end the slide. It's shown as a failure and the rest carries on.
