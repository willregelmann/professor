---
name: deck-stands-alone
assertions:
  - Every factual claim the lecturer makes about the subject in transcript.md is supported by deck.md, whether by a slide's notes, by what a slide shows, or by the background
  - When asked something that deck.md does not cover, the lecturer says it does not know or that the question goes beyond what it has, and gives no invented answer
  - The lecturer presents every slide in deck.md, in the deck's order, and makes the points each slide's notes open with, before any "For questions:" heading
---

Read `deck.md` and `transcript.md` in this test's directory. Both are written by
`python -m bench.prepared_run give`.

`deck.md` is everything the lecturer was given: the deck's background, and for each
slide its speaker notes and what it shows. The lecturer could not see the
repository the deck was prepared from.

`transcript.md` is what the lecturer said out loud: once for each slide, and once
in reply to each question a listener asked.

The lecturer is an AI agent speaking as itself. What it says about itself or about
giving the lecture (a greeting, its own name, that it is speaking live, that it
will come back to a question) is not a claim about the subject, and needs no
support from the deck. Rewording, summarising and giving an everyday analogy are
not inventions. A specific fact about the subject that deck.md does not contain is:
a number, a name, a file, a behaviour, a reason.

A slide's notes may hold more than its time allows. What comes before a
"For questions:" heading is what the lecturer is meant to say. What comes after it
is there to answer questions from, and leaving it unsaid is not a failure.

For each assertion, quote the lines of transcript.md you judged it on, and for
every failure quote the claim and say what in deck.md is missing.
