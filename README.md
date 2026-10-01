# Professor

Live lectures given by AI agents: a lecture that listens.

All the way through a lecture, Professor keeps one question open: **did that land?** Read [`invariants/INTENT.md`](invariants/INTENT.md) for why, [`invariants/`](invariants/) for what must always hold, and [`PLAN.md`](PLAN.md) for how it's being built.

**Status:** Stage 2. A Hermes agent can give a stored deck out loud, with slides, captions and diagrams, and take questions from a chat: it answers them, puts them off to a later slide and comes back to them, or declines them. A listener can pause the lecture or stop the lecturer mid-sentence. Preparing a deck with agent tools is Stage 3, so for now decks are imported.

## Try it

```
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/professor import-deck prototype/deck.json --audience "a room of software engineers"
.venv/bin/professor serve --deck 1 --host fake
```

Open http://localhost:8770 and press Start. The `fake` host has no model behind it: it reads each slide's notes out and can't answer questions. Decks and lectures are kept in `~/.professor/professor.db`.

## Hermes Agent

Point Professor at a Hermes gateway and the agent behind it gives the lecture:

```
.venv/bin/professor serve --deck 1 --host hermes --hermes-url http://ha-pi.local:8642
```

The gateway's key is read from `HERMES_API_KEY`, or from `API_SERVER_KEY` in `~/.hermes/.env` when Professor runs on the same machine as the gateway (`--hermes-env` names another file). It stays on the server and is never sent to a listener.

## Development

```
.venv/bin/python -m pytest                              # unit tests, against a scripted lecturer
.venv/bin/python -m bench.lecture_run --host fake       # a scripted listener sits through a lecture
.venv/bin/python -m bench.report                        # time to first audio, by host
```

`prototype/` is the proof of concept this grew out of, kept unchanged.
