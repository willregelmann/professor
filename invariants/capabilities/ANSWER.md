# Answer

Takes a [question](../primitives/QUESTION.md) and does something about it. Deciding when to take it, how much to say, and whether it deserves a new [slide](../primitives/SLIDE.md) takes judgment, so the lecturer makes that call. That something visible happens for every question is fixed.

## Invariants

- **Every question ends one of three ways:** answered, put off out loud, or declined out loud.
- The asker is shown that the question was heard straight away, before any answer.
- An answer is given out loud, to everyone, and everyone can tell which question is being answered.
- A question is answered in the light of the moment it was asked, even when it's taken later.
- Answers are short, and then the lecture goes back to where it was. An answer that needs a picture makes a slide, held to the same rules as any [prepared](PREPARE.md) one.
- **A question that was put off is come back to.** When the lecture reaches the point that was promised, the lecturer says so. Whatever is still open at the end is taken then.
- "Is that right?" is checked against the slide's sources when they can be reached. When they can't, the lecturer says it couldn't check.
- A question beyond what the lecturer knows gets "I don't know".
- A word about pace changes what happens next. "Slower" isn't answered, it's done.
- Questions waiting together are taken in the order they were asked, unless the lecturer says otherwise. Related ones can share an answer, and each is still marked as dealt with.
