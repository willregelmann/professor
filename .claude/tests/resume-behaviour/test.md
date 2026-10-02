---
name: resume-behaviour
assertions:
  - In resumed-after-a-long-gap.md the lecturer begins with a recap of what was covered before the lecture stopped, in the manner of "last time, we got as far as…", before it carries on with the slides
  - In resumed-after-a-short-gap.md the lecturer does not recap what was covered and does not greet the room again; it carries straight on with the slide the lecture stopped in
  - In resumed-after-a-long-gap.md, when asked to repeat a story that first-part.md does not contain, the lecturer says that its record of the lecture does not have it, and neither tells such a story nor claims to remember one
  - In neither resumed file is the first slide presented again
---

Read the three files in this test's directory. They are written by
`python -m bench.resume_run`.

`first-part.md` is everything a lecture kept of its first part, which one
lecturer gave before the lecture stopped partway through its second slide.

`resumed-after-a-long-gap.md` and `resumed-after-a-short-gap.md` are what a
different lecturer said when it picked that same lecture up: in the first as if a
month had passed, in the second a few seconds after it stopped. Each starts from
the same stopped lecture, so the two do not follow one another.

The second lecturer was given only the record in `first-part.md`. It has no
memory of its own of the first part.

Mentioning a question that was put off earlier, or coming back to it, is not a
recap. Saying in passing which slide it is on is not a recap either. A recap is a
summary of what was covered before the stop.

For each assertion, quote the lines you judged it on.
