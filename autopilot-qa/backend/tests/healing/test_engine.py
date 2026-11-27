import json

import pytest

from healing.engine import propose_heal
from healing.prompt import HealingFailure
from healing.verify import DomVerifier
from settings import BACKEND_DIR
from tests.fakes import FakeLLM

FIXTURES = BACKEND_DIR / "tests" / "fixtures" / "dom"
V2 = (FIXTURES / "catalog_v2.html").read_text(encoding="utf-8")
DECOY = (FIXTURES / "catalog_decoy.html").read_text(encoding="utf-8")
SEED = json.loads((BACKEND_DIR / "config" / "selector_map.v1.json").read_text(encoding="utf-8"))
FAILURE = HealingFailure(page_url="http://localhost:4002/catalog.html", broken_selector="#add-to-cart-btn",
                         description=SEED["services"]["catalog"]["elements"]["addToCartButton"]["description"],
                         scope_selector=".product-card", scope_text="Wireless Headphones")


def answer(locator, confidence, candidate=1, thought="The add button was renamed to Add to Bag."):
    action = json.dumps({"candidate": candidate, "new_locator": locator, "confidence": confidence, "basis": "b"})
    return [f"THOUGHT: {thought}\n", "ACTION: ", action[:20], action[20:]]


@pytest.fixture
async def verifier():
    async with DomVerifier() as v:
        yield v


async def heal(verifier, *replies, html=V2, sink=None):
    llm = FakeLLM(stream_replies=replies)
    decision = await propose_heal(llm, verifier, FAILURE, html, selector_key="catalog.addToCartButton", model="m",
                                  threshold=0.8, on_thought=sink)
    return decision, llm


async def test_a_correct_first_answer_is_verified_and_applied(verifier):
    seen = []

    async def sink(text):
        seen.append(text)

    decision, llm = await heal(verifier, answer("#add-to-bag-btn", 0.9), sink=sink)
    assert (decision.applied, decision.verified, decision.confidence, decision.rounds) == (True, True, 0.9, 1)
    assert (decision.new_locator, decision.candidate, decision.llm_confidence) == ("#add-to-bag-btn", 1, 0.9)
    assert "1 match in scope" in decision.verification
    assert seen == ["The add button was renamed to Add to Bag."] == decision.thoughts
    assert llm.calls[0]["model"] == "m"


async def test_proposing_the_failing_locator_gets_one_feedback_round(verifier):
    decision, llm = await heal(verifier, answer("#add-to-cart-btn", 0.9), answer("#add-to-bag-btn", 0.9))
    assert (decision.applied, decision.rounds) == (True, 2)
    assert "rejected: it is the failing locator itself" in llm.calls[1]["prompt"]


async def test_a_product_specific_locator_is_rejected_as_not_generic(verifier):
    decision, llm = await heal(verifier, answer("button[data-product-id='p-100']", 0.9),
                               answer(None, 0.1, candidate=None))
    assert "not generic" in llm.calls[1]["prompt"]
    assert (decision.applied, decision.new_locator, decision.rounds) == (False, None, 2)


async def test_no_candidate_means_no_heal(verifier):
    decision, llm = await heal(verifier, answer(None, 0.1, candidate=None))
    assert (decision.applied, decision.verified, decision.confidence, decision.llm_confidence) == (False, False, 0.0, 0.1)
    assert decision.rounds == 1 and len(llm.calls) == 1
    assert decision.verification == "the model found no candidate that does the same job"


async def test_a_verified_proposal_below_the_threshold_is_not_applied(verifier):
    decision, _ = await heal(verifier, answer("#add-to-bag-btn", 0.7))
    assert (decision.verified, decision.applied, decision.confidence) == (True, False, 0.7)


async def test_unparseable_answers_are_not_applied(verifier):
    decision, llm = await heal(verifier, ["I think it is the bag button."], ["Still no format."])
    assert (decision.applied, decision.rounds) == (False, 2)
    assert "missing ACTION" in decision.parse_errors
    assert "did not follow the THOUGHT/ACTION format" in llm.calls[1]["prompt"]


async def test_verification_cannot_detect_a_control_with_a_different_purpose(verifier):
    # Documents the limit of deterministic checks: a confident wrong-purpose pick is unique and generic.
    decision, _ = await heal(verifier, answer("button.wishlist-btn", 0.9), html=DECOY)
    assert (decision.verified, decision.applied) == (True, True)


async def test_the_action_record_has_the_c7_fields(verifier):
    decision, _ = await heal(verifier, answer("#add-to-bag-btn", 0.95))
    action = decision.as_action()
    for key in ["selector_key", "new_locator", "confidence", "llm_confidence", "basis", "verified", "verification",
                "applied", "threshold", "rounds", "model", "prompt_version"]:
        assert key in action
    assert "thoughts" not in action
