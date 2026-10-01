import pytest

from professor.splitter import Draw, Say, Splitter


def split(text: str, chunk: int) -> list:
    s, out = Splitter(), []
    for i in range(0, len(text), chunk):
        out += s.feed(text[i:i + chunk])
    return out + s.close()


TALK = ("Hello. This is the first real sentence of the talk. Here is how that flows.\n"
        "```mermaid\nflowchart LR\n  A[\"one\"] --> B[\"two\"]\n```\n"
        "As you can see, one leads to two. That is all")

EXPECTED = [
    Say("Hello. This is the first real sentence of the talk."),
    Say("Here is how that flows."),
    Draw("flowchart LR\n  A[\"one\"] --> B[\"two\"]"),
    Say("As you can see, one leads to two."),
    Say("That is all"),
]


@pytest.mark.parametrize("chunk", [1, 2, 3, 5, 7, 12, 40, 10_000])
def test_same_result_however_the_text_is_chunked(chunk):
    assert split(TALK, chunk) == EXPECTED


def test_a_sentence_is_emitted_before_the_text_ends():
    s = Splitter()
    assert s.feed("This is a complete sentence already. And this one is still be") == [
        Say("This is a complete sentence already.")]


def test_a_short_opener_is_merged_not_held_back():
    s = Splitter()
    assert s.feed("Hello. ") == []
    assert s.feed("I am the lecturer today. ") == [Say("Hello. I am the lecturer today.")]


def test_a_partial_fence_is_never_spoken():
    s = Splitter()
    out = s.feed("Let me draw that for you now. ``")
    out += s.feed("`merm")
    assert out == [Say("Let me draw that for you now.")]
    assert s.feed("aid\ngraph TD\n A-->B\n```") == [Draw("graph TD\n A-->B")]


def test_speech_before_a_block_goes_first_even_without_a_full_stop():
    assert split("Here it is\n```mermaid\ngraph TD\n A-->B\n```", 4) == [
        Say("Here it is"), Draw("graph TD\n A-->B")]


def test_an_unterminated_block_is_drawn_not_spoken():
    assert split("Watch this closely, everyone. ```mermaid\ngraph TD\n A-->B", 6) == [
        Say("Watch this closely, everyone."), Draw("graph TD\n A-->B")]


def test_backticks_are_not_read_aloud():
    assert split("The tool is called `tell_partner` and it posts.", 5) == [
        Say("The tool is called tell_partner and it posts.")]


def test_an_empty_block_draws_nothing():
    assert split("Nothing to see here, I am afraid. ```mermaid\n```", 3) == [
        Say("Nothing to see here, I am afraid.")]


def test_a_mark_is_acted_on_never_spoken():
    from professor.splitter import Mark
    text = "Good question. I will come to that when we reach the third slide.\n```later\n3\n```\n"
    for chunk in (1, 4, 9, 1000):
        assert split(text, chunk) == [
            Say("Good question. I will come to that when we reach the third slide."), Mark("later", "3")]


def test_a_block_of_a_kind_nobody_knows_is_still_not_spoken():
    from professor.splitter import Mark
    assert split("Here is some code for you all.\n```python\nprint('hi')\n```\nThat was the code, as promised.", 5) == [
        Say("Here is some code for you all."), Mark("python", "print('hi')"),
        Say("That was the code, as promised.")]


def test_a_mark_with_nothing_in_it_still_counts():
    from professor.splitter import Mark
    assert split("I would rather not answer that one.\n```decline\n```", 3) == [
        Say("I would rather not answer that one."), Mark("decline", "")]


def test_what_has_not_made_a_sentence_yet_can_be_read_back():
    s = Splitter()
    s.feed("This sentence is complete and long. And this one was cut off bef")
    assert s.unsaid() == "And this one was cut off bef"
