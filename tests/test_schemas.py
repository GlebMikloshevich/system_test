"""IngoreadDocument.fields accepts three shapes; all normalize to list[IngoreadField]."""

from ingoread_test.integration.schemas import IngoreadDocument, IngoreadFileResult


def test_file_result_coerces_single_dict_to_list():
    """A service returning one document as a dict (not a list) is wrapped."""
    r = IngoreadFileResult.model_validate(
        {"filename": "f.pdf", "result": {"label": "x", "fields": {"vin": "ABC"}}}
    )
    assert len(r.result) == 1
    assert r.result[0].label == "x"
    assert r.result[0].fields["vin"][0].text == "ABC"


def test_file_result_none_result_is_empty():
    r = IngoreadFileResult.model_validate({"filename": "f.pdf", "result": None})
    assert r.result == []


def test_bare_scalar_field_wraps_as_text():
    doc = IngoreadDocument.model_validate({"label": "x", "fields": {"field1": "answer"}})
    assert len(doc.fields["field1"]) == 1
    assert doc.fields["field1"][0].text == "answer"


def test_single_dict_field_wraps_in_list():
    doc = IngoreadDocument.model_validate({"label": "x", "fields": {"field2": {"text": "true"}}})
    assert len(doc.fields["field2"]) == 1
    assert doc.fields["field2"][0].text == "true"


def test_list_field_passes_through():
    doc = IngoreadDocument.model_validate(
        {
            "label": "x",
            "fields": {"field3": [{"text": "a"}, {"text": "b"}]},
        }
    )
    assert [f.text for f in doc.fields["field3"]] == ["a", "b"]


def test_mixed_shapes_in_same_response():
    doc = IngoreadDocument.model_validate(
        {
            "label": "x",
            "fields": {
                "field1": "answer",
                "field2": {"text": "true"},
                "field3": [{"text": "x"}],
                "field4": 194.4,  # numeric scalar
                "field5": False,  # bool scalar
            },
        }
    )
    assert doc.fields["field1"][0].text == "answer"
    assert doc.fields["field2"][0].text == "true"
    assert doc.fields["field3"][0].text == "x"
    assert doc.fields["field4"][0].text == "194.4"
    assert doc.fields["field5"][0].text == "false"
