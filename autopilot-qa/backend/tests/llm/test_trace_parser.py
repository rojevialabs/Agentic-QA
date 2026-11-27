from llm.trace_parser import Action, ParseError, Thought, ThoughtActionParser, parse

ACTION_JSON = '{"new_locator": "#add-to-bag-btn", "confidence": 0.91, "basis": "same data-product-id, text Add to Bag"}'
ACTION_DATA = {"new_locator": "#add-to-bag-btn", "confidence": 0.91, "basis": "same data-product-id, text Add to Bag"}
RESPONSE = f"THOUGHT: The add-to-cart id is gone but a button with the same data-product-id says Add to Bag.\nACTION: {ACTION_JSON}"


def feed_chunks(chunks):
    parser = ThoughtActionParser()
    events = []
    for chunk in chunks:
        events += parser.feed(chunk)
    return events + parser.finish()


def test_parses_a_complete_response():
    assert parse(RESPONSE) == [
        Thought("The add-to-cart id is gone but a button with the same data-product-id says Add to Bag."),
        Action(ACTION_DATA),
    ]


def test_character_by_character_stream_gives_the_same_events():
    assert feed_chunks(list(RESPONSE)) == parse(RESPONSE)


def test_thought_is_emitted_as_soon_as_its_newline_arrives():
    parser = ThoughtActionParser()
    assert parser.feed("THOUGHT: first idea") == []
    assert parser.feed("\nTHOUGHT: sec") == [Thought("first idea")]
    assert parser.feed("ond idea\n") == [Thought("second idea")]
    assert parser.feed("ACTION: {\"new_locator\": \"#x\",") == []
    assert parser.feed(" \"confidence\": 0.5, \"basis\": \"b\"}") == [Action({"new_locator": "#x", "confidence": 0.5, "basis": "b"})]
    assert parser.done


def test_markers_split_across_chunks():
    assert feed_chunks(["THOU", "GHT: x\nACT", "ION: {\"a\":", " 1}"]) == [Thought("x"), Action({"a": 1})]


def test_continuation_lines_and_blank_lines():
    text = "THOUGHT: line one\n\n  line two  \nACTION: {\"a\": 1}"
    assert parse(text) == [Thought("line one"), Thought("line two"), Action({"a": 1})]


def test_markdown_and_lowercase_markers():
    text = "**Thought:** the id changed\n**ACTION:** {\"a\": 2}"
    assert parse(text) == [Thought("the id changed"), Action({"a": 2})]
    assert parse("thought: t\naction: {\"a\": 3}") == [Thought("t"), Action({"a": 3})]


def test_json_in_a_code_fence_spanning_lines_with_trailing_prose():
    text = 'THOUGHT: t\nACTION:\n```json\n{\n  "new_locator": "#b",\n  "confidence": 0.8\n}\n```\nHope this helps!'
    assert parse(text) == [Thought("t"), Action({"new_locator": "#b", "confidence": 0.8})]


def test_braces_and_escaped_quotes_inside_json_strings():
    text = 'THOUGHT: t\nACTION: {"basis": "text has } and \\" quote {", "confidence": 0.7} trailing }'
    assert parse(text) == [Thought("t"), Action({"basis": 'text has } and " quote {', "confidence": 0.7})]


def test_action_on_the_same_line_as_the_thought():
    assert parse('THOUGHT: renamed button. ACTION: {"a": 1}') == [Thought("renamed button."), Action({"a": 1})]


def test_lowercase_action_word_inside_a_thought_is_not_a_marker():
    text = 'THOUGHT: the next action: click the bag button\nACTION: {"a": 1}'
    assert parse(text) == [Thought("the next action: click the bag button"), Action({"a": 1})]


def test_preamble_before_the_first_marker_is_ignored():
    assert parse('Sure! Here is my answer.\nTHOUGHT: t\nACTION: {"a": 1}') == [Thought("t"), Action({"a": 1})]


def test_missing_action_flushes_the_last_thought_and_reports():
    assert parse("THOUGHT: I am not sure") == [Thought("I am not sure"), ParseError("", "missing ACTION")]


def test_invalid_json_is_reported():
    [thought, error] = parse("THOUGHT: t\nACTION: {new_locator: '#x'}")
    assert thought == Thought("t")
    assert isinstance(error, ParseError)
    assert error.reason.startswith("invalid ACTION JSON")
    assert error.raw == "{new_locator: '#x'}"


def test_incomplete_json_at_finish_is_reported():
    assert parse('THOUGHT: t\nACTION: {"a": 1') == [Thought("t"), ParseError(' {"a": 1', "incomplete ACTION JSON")]


def test_input_after_the_action_is_ignored():
    parser = ThoughtActionParser()
    parser.feed('THOUGHT: t\nACTION: {"a": 1}')
    assert parser.feed("\nTHOUGHT: late") == []
    assert parser.finish() == []
