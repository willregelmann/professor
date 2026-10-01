# Professor: build plan

How we build what [`invariants/`](invariants/INTENT.md) describes. The invariants say *what* must hold. This plan says *how* and *in what order*. If the two disagree, the invariants win, and this plan gets fixed.

## Context

Professor grows out of `~/presenter` on `ha-pi.local`, a proof of concept built with Wren in September 2026: one aiohttp server and one page. It narrates a three-slide deck live from speaker notes, speaks sentence by sentence while the reply is still being written, draws inline Mermaid diagrams in step with the speech, and answers typed questions in the same session. It works, but it has several gaps:
- the deck is a static file, read once at start-up
- the lecturer can draw a diagram but can't make a slide
- the lecture only moves forward
- a diagram that fails to draw is shown to the room and never reported to the lecturer
- questions wait for the next break, and nothing records what became of them
- only one host (Hermes), with Wren's persona written into the server

The prototype isn't in git, and the Pi holds the only copy.

## Principles

1. **Fallibility is a side effect, not a flaw.** Default to what a human lecturer does: notes, not a script. Deviate only when the pressure that shaped the habit doesn't apply AND a measurement shows the deviation helps. Accountability comes from what the lecture keeps: its path, its questions, its corrections.
2. **Nothing ships without a measurement.** Every stage has exit criteria checked by the harness. The number that matters most is the time from a turn starting to the first word heard.
3. **Fail where the room can see it.** A lecture that can't go on, a slide that won't draw and a sentence that can't be spoken all show up in the [presentation](invariants/interfaces/PRESENTATION.md).
4. **The core settles what the agent forgets.** Whatever an invariant promises, the core guarantees even when the agent doesn't call the tool. A question nobody settled is still settled.

## Architecture

### Packages
- **`professor` core:** a Python library that holds all the lecture logic: the store, the lecture loop, the splitter that separates what's said from what's drawn, and the question states. Nothing else writes to the store.
- **Server (`professor serve`):** one aiohttp process. It serves the page, holds a websocket to each listener, runs the lecture loop, and serves the [agent tools](invariants/interfaces/AGENT.md) over MCP.
- **Page:** one HTML page carrying both the [presentation](invariants/interfaces/PRESENTATION.md) and the [Q&A](invariants/interfaces/QA.md). Mermaid for diagrams, KaTeX for equations, a highlighter for code, all served locally.
- **Host adapters:** one small module per [host](invariants/interfaces/HOST.md), each with the same four jobs (below).
- **Claude Code plugin:** the MCP server entry, a skill for preparing a deck, a skill for lecturing, and a `/lecture` command. The Agent SDK loads the same plugin.

### Storage
One SQLite file, e.g. `~/.professor/professor.db`, owned by the server.

| Primitive | Storage |
|---|---|
| [Deck](invariants/primitives/DECK.md) | `decks`: id, subject, audience, intended length, created_at |
| [Slide](invariants/primitives/SLIDE.md) | `slides`: id, deck_id, position on the main line (null when beside it), anchor slide and question it was made for (when made up), origin (prepared / made up). `slide_revisions`: slide_id, what's shown, notes, sources, whether it could be shown, created_at. Only ever added to; the latest is current |
| [Lecture](invariants/primitives/LECTURE.md) | `lectures`: id, deck_id, host, host session handle, state, place, detour stack. `visits`: lecture_id, slide revision, arrived_at, how it got there, covered or skipped. `utterances`: lecture_id, visit, sentence, spoken or cut off, ts. Only ever added to |
| [Question](invariants/primitives/QUESTION.md) | `questions`: id, lecture_id, asker, text, the visit and utterance it was asked at, state (heard / being answered / put off / answered / declined), put off until, answer utterances, slide made for it. `corrections`: slide_id, question_id, text, applied / set aside / open |

The invariants only require a lecture to keep its path. This plan also keeps the words (`utterances`), because the page already receives every sentence for captions, the Q&A has to show answers in writing, and a different lecturer resuming needs more than a list of slide numbers.

### The lecture loop
The server owns the loop. The agent is asked for one turn at a time.

1. **A moment arrives:** a slide is reached, a question is taken, a listener stops the lecturer, or a lecture is resumed.
2. **The core builds the prompt** from the slide's notes, what it shows, and what the lecture has kept. The first turn in a host session carries the standing instructions.
3. **The adapter starts a turn** and hands back text as it's produced.
4. **The splitter cuts the text** into sentences and drawn blocks, in order. Each sentence goes to the speech engine and then to the page with its audio. Each drawn block goes to the page at its place in the queue.
5. **Tool calls arrive during the turn** over MCP (`go`, `put_off`, `write_slide`…) and change the lecture's state.
6. **The turn ends.** The core decides what's next: a waiting question, a move the lecturer asked for, or the next slide on the main line after a short pause.

Rules the core enforces without the agent's help:
- A question is marked *heard* the moment it's received, before any agent sees it.
- A question turn that ends without `put_off` or `decline` is marked *answered*, with what was said.
- A put-off question is raised again when the lecture reaches the slide it was put off to, and at the end of the main line.
- A drawn block that fails on the page is reported back, and the lecturer gets one more turn to redraw it or carry on without it.
- A stop from a listener cancels the turn, drops the audio not yet played, and records the cut-off sentences.

### Speaking and drawing
Carried over from the prototype: plain spoken prose with a fenced block for anything to be drawn. Any model that can write text can do it. The standing instructions are the prototype's preamble with Wren and Ash taken out.

### Speech
`edge-tts`, as in the prototype, behind a one-function seam (`text → audio`). It needs the network and is an unofficial client of a Microsoft service, so a local engine is the first alternative to try (see Deferred). In Stage 0 it returned no audio for 3 ordinary sentences out of roughly 650, so the core retries a sentence once before showing it as a failure.

### Checking a slide can be shown
[Prepare](invariants/capabilities/PREPARE.md) has to know a diagram draws before any listener sees it, and there may be no page open when a deck is prepared. `write_slide` therefore checks through a headless render. The cheapest thing that works on the Pi is an open question (below).

### Host adapters
Each adapter does four things: start a turn, yield text as it's produced, cancel a turn, and hold a session handle.

| Host | Turn and speech | Session | Status |
|---|---|---|---|
| Hermes | Streaming chat completions on the gateway | `X-Hermes-Session-Id` | Proven in the prototype |
| Claude Agent SDK | A query with partial messages turned on | The SDK's session resume | Speech measured in Stage 0; resume untested |
| Tools only (e.g. a Claude Code session) | The agent calls `say` with a passage and `listen` to wait for the next moment | The host's own | Speech measured in Stage 0 through the SDK; a real Claude Code session and a long `listen` are untested |

`say` and `listen` belong to the tools-only adapter. They aren't part of the agent's tool set, and the other adapters never expose them.

### Lessons carried over from the prototype
- **Empty audio deadlocks the player.** The speech engine can return nothing without raising, and Chrome fires no event for an empty clip. The server never sends one; the page keeps a watchdog.
- **Short openers stall the stream.** A sentence under 20 characters is merged with the next.
- **Partial fences get spoken.** A trailing partial fence is held back until it's known to be speech or a block.
- **A failed sentence isn't a failed turn.** Treating it as one let a waiting question start an overlapping answer.
- **Order is one queue.** Audio and drawings share a single ordered queue on the page, so a picture lands where the speech introduced it.
- **Keys stay on the server.** The gateway key is read at run time and never sent to the page.

## Stages

### 0. Bring the prototype home, harness and baseline
- **Rescue:** copy `~/presenter` from the Pi into `prototype/` and commit it unchanged.
- **Harness:** the three driver scripts become a headless listener that plays the page's role and records, per turn: time to first audio, total time, the order of sentences and drawings, and every warning.
- **Fake host:** an adapter that replays scripted text at a set pace, so the core can be tested without a model.
- **Baseline:** time to first audio on Hermes, for a slide turn and a question turn, N runs each.
- **Spikes:** the same measurement through the Agent SDK, and through `say` / `listen` in a Claude Code session.
- **Exit:** the baseline is recorded, and there's a number for each of the three hosts. If tools-only is too slow to follow, that's written down and its fallback is designed around it.
- **Result (2026-10-01, `bench/results.jsonl`, `python -m bench.report`):** exit met. Median seconds to first audio, 5 sessions each, all on `claude-opus-5-5` at medium effort:

  | Host | First slide | Question | Later slide |
  |---|---|---|---|
  | Hermes (Wren, through the prototype on the Pi) | 4.4 | 4.3 | 6.3 |
  | Claude Agent SDK, streamed | 2.6 | 2.8 | 2.1 |
  | Tools only (`say` / `listen`, through the SDK) | 3.0 | 5.0 | 2.9 |

  - No host left the room in silence after the first word: every one wrote faster than the speech played.
  - Tools-only is fast enough to follow. The agent wrote several `say` calls in one reply and each ran as soon as it was written. Telling it to put one sentence in each call made it slower, not faster.
  - Hermes is slower to start and says more for the same notes (about 61s of speech for a 35s slide, against 37s). That's the agent and its gateway, not the adapter.
  - Not yet measured: tools-only in a real Claude Code session with an external tool server, and a `listen` that waits minutes.

### 1. Core, store and presentation
- **Core:** the store, the lecture loop, the splitter, [Deliver](invariants/capabilities/DELIVER.md).
- **Page:** slide, voice, captions, place and state.
- **Import:** the prototype's `deck.json` becomes a stored deck.
- **Exit, against the fake host:**
  - a three-slide deck plays end to end and every drawing lands between the right two sentences;
  - an empty audio clip, a broken diagram and an unreachable host each show up on the page, and none stalls the lecture;
  - the page is never silent without a state that explains it.

### 2. Hermes and Q&A
- **Hermes adapter**, lifted from the prototype.
- **[Answer](invariants/capabilities/ANSWER.md):** the five question states, `put_off` and `decline`, put-off questions raised again, stop.
- **Q&A:** the chat, with states and written answers.
- **Exit:**
  - Wren gives the imported deck, with time to first audio no worse than the baseline;
  - across N runs with scripted questions, every question ends answered, put off or declined, and no put-off question is still open when the lecture ends;
  - a stop is heard within one sentence.

### 3. Agent tools and Prepare
- **MCP:** the read tools and the prepare tools, served by the server, with a stdio entry for hosts that only load local commands.
- **[Prepare](invariants/capabilities/PREPARE.md):** `describe`, `write_slide` with the show check, `arrange`.
- **Plugin:** the preparing skill and `/lecture`.
- **Exit:**
  - Claude Code prepares a deck from a repository;
  - Wren gives that deck from the Pi with no access to the repository, and `../fuzzy-assertions` (`test:run`) judges that nothing was invented beyond the notes;
  - no slide reaches a lecture that failed its show check.

### 4. Navigate and made-up slides
- **[Navigate](invariants/capabilities/NAVIGATE.md):** `go`, nested detours, covered and skipped, the place shown on the page.
- **Made-up slides:** a slide written during a lecture is kept beside the main line with its question.
- **Exit:**
  - a scripted lecture with a detour inside a detour returns to the right slide each time;
  - every move that isn't "next" is announced;
  - the deck's main line is unchanged after any lecture.

### 5. The other hosts
- **Adapters:** Claude Agent SDK and tools-only.
- **Exit:** the same deck and the same scripted questions give the same question states, the same path and the same kept record in all three hosts. Speed may differ, and the page shows it.

### 6. Resume and improving a deck
- **[Resume](invariants/capabilities/RESUME.md):** across a pause, a server restart and a change of host, with a recap sized to the gap.
- **Corrections:** `correct`, and Prepare applying or setting them aside.
- **Exit:**
  - a lecture started by Wren is resumed through the Agent SDK at the right slide, with its put-off questions still open;
  - behaviour judges pass on "recaps after a long gap", "doesn't recap after a short one" and "doesn't claim to remember what wasn't kept".

### Deferred
- Spoken questions
- A local speech engine, so a lecture doesn't depend on the network
- Letting the lecturer decide mid-sentence when to take a question. Until then a question is taken at the end of the current slide, or straight away after a stop.
- Listeners on other networks, and anything beyond trusting the local network
- Exporting a deck to another format
- More than one lecture running at once

## Open questions
1. **Latency budget:** how long can a listener wait for the first word before a lecture stops feeling live? Stage 0 measured 2 to 6 seconds depending on the host; the threshold is a judgment.
2. **Show check:** what does the headless render? Candidates: Mermaid's command-line tool (brings a browser, heavy on the Pi), a small Node script using Mermaid's parser alone (catches syntax, not layout), or asking an open page when there is one.
3. **Mermaid:** the open-source renderer, as in the prototype, or the hosted service? The plan assumes the renderer.
4. **Where the server runs:** beside the lecturer's host, or beside the listeners? Preparing "explain this codebase" wants the repository; Wren lives on the Pi.
5. **Who advances:** at the end of a slide the core moves on after a short pause. Should a single listener be able to turn that off and step through by hand?
6. **Keeping the words:** `utterances` goes beyond what the invariants ask. Keep it, or keep only the path and the answers?
7. **Checking a point landed:** what makes the check worth the interruption? Candidates: after a slide that drew questions last time, before a slide that builds on it, or never unprompted.
8. **Naming:** the tool names, the command, and the on-disk layout.
