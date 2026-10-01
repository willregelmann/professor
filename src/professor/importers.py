"""Bringing decks in from elsewhere."""
from __future__ import annotations

import json
from pathlib import Path

from professor import showcheck
from professor.store import Deck, Store


def import_prototype_deck(store: Store, path: Path | str, *, audience: str = "", background: str = "",
                          check=showcheck.check) -> Deck:
    """A deck in the prototype's format: a title and slides with a title, notes,
    a length in seconds and sometimes a diagram.

    The prototype kept who the listeners were and what the lecturer should stay
    inside in its server. Here they belong to the deck, so they're passed in.
    """
    data = json.loads(Path(path).read_text())
    slides = data["slides"]
    seconds = sum(s.get("seconds", 45) for s in slides)
    deck = store.create_deck(data["title"], audience=audience, background=background,
                             minutes=max(1, round(seconds / 60)))
    for s in slides:
        # Imported slides are drawn in a check like any others. One that fails is
        # still brought in, marked as unable to be shown, so it can be fixed.
        result = check(diagram=s.get("diagram", ""))
        store.add_slide(deck.id, title=s["title"], notes=s["notes"], diagram=s.get("diagram", ""),
                        seconds=s.get("seconds"), show_checked=result.ok,
                        show_error=result.error if result.checked else None)
    return deck
