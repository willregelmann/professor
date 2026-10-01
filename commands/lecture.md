---
description: Prepare a lecture on a subject or on this codebase, and say how to give it
argument-hint: "[subject, or 'this codebase'] [for whom]"
---

Prepare a Professor deck on: $ARGUMENTS

If no subject was given, the subject is the codebase in the current directory.

1. Load the `preparing-a-deck` skill and follow it.
2. If the audience or the length isn't clear from the request, make a sensible assumption (ten minutes, for someone new to the subject) and say what you assumed. Don't stop to ask unless the subject itself is unclear.
3. Build the deck with the professor tools: `describe`, then `write_slide` for each slide, then `deck` to read it back.
4. Finish by telling the user:
   - the deck's number and its main line, as a short numbered list of slide titles;
   - anything you weren't sure of and marked as such in the notes;
   - how to give it: `${CLAUDE_PLUGIN_ROOT}/bin/professor serve --deck <number> --host hermes --hermes-url <gateway>` has a Hermes agent give it, and `--host fake` reads the notes out with no model behind it. The page is at http://localhost:8770.

Do not start the server yourself unless the user asks you to.
