"""The `professor` command.

    professor import-deck prototype/deck.json --audience "a room of software engineers"
    professor decks
    professor serve --deck 1 --host fake
    professor serve --deck 1 --host hermes --hermes-url http://ha-pi.local:8642
    professor mcp
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from aiohttp import web

from professor import importers, mcp_server, store as store_module
from professor.hosts import hermes
from professor.hosts.fake import NotesReader
from professor.lecture import Lecture
from professor.server import make_app
from professor.store import Store


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="professor", description="Live lectures given by AI agents.")
    ap.add_argument("--db", type=Path, default=store_module.default_path(),
                    help="the store (default: %(default)s)")
    sub = ap.add_subparsers(dest="command", required=True)

    imp = sub.add_parser("import-deck", help="bring in a deck in the prototype's JSON format")
    imp.add_argument("path", type=Path)
    imp.add_argument("--audience", default="", help="who the deck is for")
    imp.add_argument("--background", default="", help="what the lecturer should stay inside")

    sub.add_parser("decks", help="list the decks in the store")
    sub.add_parser("mcp", help="serve the agent's tools over MCP on stdin and stdout")

    serve = sub.add_parser("serve", help="give a deck: serve the page and run the lecture")
    serve.add_argument("--deck", type=int, required=True)
    serve.add_argument("--host", choices=["fake", "hermes"], default="fake",
                       help="where the lecturer lives: a Hermes gateway, or a scripted fake that reads "
                            "the notes out")
    serve.add_argument("--hermes-url", default=os.environ.get("HERMES_URL", "http://127.0.0.1:8642"),
                       help="the Hermes gateway (default: %(default)s)")
    serve.add_argument("--hermes-env", type=Path, default=None,
                       help="the gateway's .env, for its key, unless HERMES_API_KEY is set "
                            "(default: ~/.hermes/.env)")
    serve.add_argument("--port", type=int, default=8770)
    serve.add_argument("--bind", default="0.0.0.0")

    args = ap.parse_args(argv)
    if args.command == "mcp":
        mcp_server.serve(args.db)
        return
    store = Store(args.db)

    if args.command == "import-deck":
        deck = importers.import_prototype_deck(store, args.path, audience=args.audience,
                                               background=args.background)
        slides = store.main_line(deck.id)
        print(f"deck {deck.id}: {deck.subject} ({len(slides)} slides)")
        for i, slide in enumerate(slides):
            if slide.show_error:
                print(f"  slide {i + 1} can't be shown as it stands. {slide.show_error}")
            elif (slide.diagram or slide.math) and not slide.show_checked:
                print(f"  slide {i + 1} wasn't checked: no browser was found to draw its diagram with.")
    elif args.command == "decks":
        for deck in store.decks():
            print(f"{deck.id}\t{len(store.main_line(deck.id))} slides\t{deck.subject}")
    elif args.command == "serve":
        try:
            deck = store.deck(args.deck)
        except KeyError as exc:
            sys.exit(f"{exc.args[0]}. Run `professor decks` to see what's there.")
        if not store.main_line(deck.id):
            sys.exit(f"deck {deck.id} has no slides on its main line.")

        if args.host == "hermes":
            try:
                host = hermes.HermesHost(args.hermes_url, hermes.find_key(args.hermes_env))
            except (OSError, ValueError) as exc:
                sys.exit(f"No key for the Hermes gateway: {exc}. Set HERMES_API_KEY or pass --hermes-env.")
        else:
            host = NotesReader()

        async def app() -> web.Application:
            lecture = Lecture(store, store.create_lecture(deck.id, args.host), host)
            return make_app(lecture)
        web.run_app(app(), host=args.bind, port=args.port,
                    print=lambda *_: print(f"\"{deck.subject}\" on http://localhost:{args.port}", flush=True))


if __name__ == "__main__":
    main()
