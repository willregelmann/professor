# Host

The runtime the [agent](AGENT.md) lives in: Hermes Agent, the Claude Agent SDK, a Claude Code session, or anything else that can hold a conversation with a model and give it tools. The host runs the agent. Professor runs the [lecture](../primitives/LECTURE.md). Every host is served by the same core through a thin adapter.

## What an adapter does

| Need | Hermes | Claude Agent SDK | A host with tools and nothing else |
|---|---|---|---|
| Give the agent its tools | Tool server | Tool server | Tool server |
| Start a turn when the lecture needs one: a slide is reached, a question arrives, a listener stops the lecturer | A request to the gateway | A new turn in the session | The agent waits on a tool that returns when something happens |
| Carry the agent's speech as it's produced | The gateway's streamed reply | The session's streamed reply | A tool the agent speaks through, a passage at a time |
| Keep the conversation going between turns | The gateway's session | The SDK's session | The host's own |

## Invariants

- One core, many adapters. Adapters translate between the host and Professor and contain no lecture logic. A behaviour that differs by host is a bug.
- The tools are the same in every host.
- **A host that can't do something gets a visible fallback, never a silent gap.** A host that can't carry speech as it's produced still lectures, and the listeners can tell the lecturer is composing.
- **The host's session isn't the lecture's memory.** What a lecture knows about itself is kept with the lecture. An agent in another host can pick it up and is given what was kept.
- Preparing needs only the tools. Any host that can use them can prepare a [deck](../primitives/DECK.md).
- Host quirks are absorbed by the adapter, never by the core.
- A host that can't be reached is shown to the listeners as a lecturer that can't go on.
- Nothing the host keeps secret reaches the listeners.
