import pytest

from patent_hunter.scorers.json_extract import extract_json_array


def test_clean_array():
    out = extract_json_array('[{"a":1}, {"a":2}]')
    assert out == [{"a": 1}, {"a": 2}]


def test_with_markdown_fence():
    text = "```json\n[{\"score\": 7}]\n```"
    assert extract_json_array(text) == [{"score": 7}]


def test_with_prose_preface():
    text = 'Sure! Here is the JSON:\n[{"score": 8}]\nThanks.'
    assert extract_json_array(text) == [{"score": 8}]


def test_handles_nested_brackets_and_strings():
    text = 'noise [{"k": "has ] bracket", "n": [1,2,3]}] tail'
    out = extract_json_array(text)
    assert out == [{"k": "has ] bracket", "n": [1, 2, 3]}]


def test_rejects_when_no_array():
    with pytest.raises(ValueError):
        extract_json_array("nothing useful here")


def test_rejects_when_top_level_object():
    with pytest.raises(ValueError):
        extract_json_array('{"score": 7}')


def test_rejects_empty():
    with pytest.raises(ValueError):
        extract_json_array("")
