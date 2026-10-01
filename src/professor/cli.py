"""The `professor` command.

    professor import-deck prototype/deck.json --audience "a room of software engineers"
    professor decks
    professor serve --deck 1 --host fake
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from aiohttp import web

from professor import importers, store as store_module
from professor.hosts.fake import FakeHost
from professor.lecture import Lecture
from professor.server import make_app
from professor.store import Store


def _fake_host(store: Store, deck_id: int) -> FakeHost:
    """A lecturer with no model behind it: it reads each slide's notes out as
    written, at about the pace a model writes."""
    return FakeHost([s.notes for s in store.main_line(deck_id)], chars_per_s=250)


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

    serve = sub.add_parser("serve", help="give a deck: serve the page and run the lecture")
    serve.add_argument("--deck", type=int, required=True)
    serve.add_argument("--host", choices=["fake"], default="fake",
                       help="where the lecturer lives (only the scripted fake so far)")
    serve.add_argument("--port", type=int, default=8770)
    serve.add_argument("--bind", default="0.0.0.0")

    args = ap.parse_args(argv)
    store = Store(args.db)

    if args.command == "import-deck":
        deck = importers.import_prototype_deck(store, args.path, audience=args.audience,
                                               background=args.background)
        print(f"deck {deck.id}: {deck.subject} ({len(store.main_line(deck.id))} slides)")
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

        async def app() -> web.Application:
            lecture = Lecture(store, store.create_lecture(deck.id, args.host), _fake_host(store, deck.id))
            return make_app(lecture)
        web.run_app(app(), host=args.bind, port=args.port,
                    print=lambda *_: print(f"\"{deck.subject}\" on http://localhost:{args.port}", flush=True))


if __name__ == "__main__":
    main()
