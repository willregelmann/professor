# Professor

Live lectures given by AI agents: a lecture that listens.

All the way through a lecture, Professor keeps one question open: **did that land?** Read [`invariants/INTENT.md`](invariants/INTENT.md) for why, [`invariants/`](invariants/) for what must always hold, and [`PLAN.md`](PLAN.md) for how it's being built.

**Status:** Stage 3. An agent can prepare a deck with tools: slides with speaker notes, diagrams, code and equations, each checked to draw before it's saved. A Hermes agent can give a deck out loud and take questions from a chat: it answers them, puts them off to a later slide and comes back to them, or declines them. A listener can pause the lecture or stop the lecturer mid-sentence. Moving around a deck and making up slides mid-lecture is Stage 4.

## Try it

```
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/professor import-deck prototype/deck.json --audience "a room of software engineers"
.venv/bin/professor serve --deck 1 --host fake
```

Open http://localhost:8770 and press Start. The `fake` host has no model behind it: it reads each slide's notes out and can't answer questions. Decks and lectures are kept in `~/.professor/professor.db`.

## Claude Code

```
/plugin marketplace add /path/to/professor
/plugin install professor@professor
```

Then install the runtime once with the plugin's `bin/professor install`, which creates `~/.professor/venv`. After that:

```
/lecture this codebase, for an engineer who is new to it
```

prepares a deck and says how to give it. The plugin adds seven tools: `deck`, `slide`, `lecture` and `questions` to read, and `describe`, `write_slide` and `arrange` to prepare. `write_slide` draws a slide's diagram and equation in headless Chrome or Chromium before saving; a slide that fails comes back to be fixed and isn't saved. Any other MCP host can use the same tools by running `bin/professor mcp`.

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
.venv/bin/python -m bench.prepared_run prepare --db bench/data/prepared.db   # Claude Code prepares a deck
.venv/bin/python -m bench.report                        # time to first audio, by host
```

Set `PROFESSOR_PYTHON=$PWD/.venv/bin/python` to run the plugin from a checkout. `prototype/` is the proof of concept this grew out of, kept unchanged.
