import pytest

from patent_hunter.scorers.json_extract import extract_json_array, score_result_kwargs


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


def test_score_result_kwargs_normalizes_new_fields():
    obj = {
        "patent_id": "A",
        "score": 99,
        "consumer_viable": "yes",
        "short_title_ja": "🔌 配線留め",
        "summary_ja": "径違いのケーブルを工具なしで固定する。",
        "opportunity_ja": "月検索 6.4 万・既存品は外れ不満",
        "next_action_steps_ja": ["Onshape で 30 分", "Etsy で $8", "STL 販売"],
        "failure_reasons_ja": ["価格競争で埋もれる"],
        "failure_mitigations_ja": ["用途を絞って比較画像を出す"],
        "confidence_score": 101,
        "confidence_bom": "-5",
        "confidence_amazon_gap": "70",
        "diy_friendly": "true",
        "diy_print_minutes": 5,
        "diy_material_cost_jpy": 9999,
        "diy_required_extras": "M3ネジ x 1",
        "diy_score": 12,
    }

    out = score_result_kwargs(obj, "fallback", "raw")

    assert out["score"] == 10
    assert out["consumer_viable"] is True
    assert out["short_title_ja"] == "🔌 配線留め"
    assert out["next_action_steps_ja"] == [
        "Onshape で 30 分",
        "Etsy で $8",
        "STL 販売",
    ]
    assert out["failure_reasons_ja"] == ["価格競争で埋もれる"]
    assert out["failure_mitigations_ja"] == ["用途を絞って比較画像を出す"]
    assert out["confidence_score"] == 100
    assert out["confidence_bom"] == 0
    assert out["confidence_amazon_gap"] == 70
    assert out["diy_friendly"] is True
    assert out["diy_print_minutes"] == 10
    assert out["diy_material_cost_jpy"] == 2000
    assert out["diy_required_extras"] == ["M3ネジ x 1"]
    assert out["diy_score"] == 10
