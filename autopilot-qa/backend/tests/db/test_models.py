import uuid

import pytest
from bson import ObjectId
from pydantic import ValidationError

from db.models import BugReport, Message, Requirement, TestCase


def make_test_case(**overrides):
    fields = dict(run_id="run-1", requirement_id=str(ObjectId()), type="negative", title="expired card",
                  steps=["open payment page", "pay with 01/20"], expected_result="Payment declined: card expired")
    return TestCase(**(fields | overrides))


def test_message_defaults():
    m = Message(run_id="r", from_agent="orchestrator", to_agent="test_generator", task_id="t", type="request")
    uuid.UUID(m.message_id)
    assert m.payload == {}
    assert m.timestamp.tzinfo is not None
    assert m.timestamp.microsecond % 1000 == 0


@pytest.mark.parametrize("field,value", [("type", "shout"), ("from_agent", "intern")])
def test_message_rejects_values_outside_the_protocol(field, value):
    fields = dict(run_id="r", from_agent="orchestrator", to_agent="test_generator", task_id="t", type="thought")
    with pytest.raises(ValidationError):
        Message(**(fields | {field: value}))


def test_requirement_gets_a_run_id_and_pending_status():
    r = Requirement(text="User can log in")
    uuid.UUID(r.run_id)
    assert r.status == "pending"


def test_to_mongo_converts_refs_and_drops_empty_id():
    tc = make_test_case()
    doc = tc.to_mongo()
    assert "_id" not in doc
    assert doc["requirement_id"] == ObjectId(tc.requirement_id)
    assert doc["type"] == "negative"


def test_to_mongo_keeps_an_assigned_id_as_object_id():
    oid = ObjectId()
    assert make_test_case(id=str(oid)).to_mongo()["_id"] == oid


def test_from_mongo_exposes_object_ids_as_strings():
    oid, rid = ObjectId(), ObjectId()
    tc = TestCase.from_mongo(make_test_case().to_mongo() | {"_id": oid, "requirement_id": rid})
    assert tc.id == str(oid)
    assert tc.requirement_id == str(rid)


def test_test_case_type_is_constrained():
    with pytest.raises(ValidationError):
        make_test_case(type="smoke")


def test_bug_report_root_cause_is_constrained():
    with pytest.raises(ValidationError):
        BugReport(run_id="r", execution_id=str(ObjectId()), steps_to_reproduce=[], expected="a", actual="b",
                  root_cause="cosmic-rays", severity="low")
