from healing.verify import LocatorCheck
from scripts.healer_spike import judge

ADD_P100 = {"tag": "button", "attrs": {"id": "add-to-bag-btn", "data-product-id": "p-100"}, "text": "Add to Bag"}
ADD_P105 = {"tag": "button", "attrs": {"id": "add-to-bag-btn", "data-product-id": "p-105"}, "text": "Add to Bag"}
WISH_P100 = {"tag": "button", "attrs": {"class": "wishlist-btn", "data-product-id": "p-100"}, "text": "Add to Wishlist"}
NONE = LocatorCheck(None, 0, error="no locator proposed")


def found(*matches):
    return LocatorCheck("x", 1, tuple(matches))


def test_heal_needs_confidence_the_right_target_and_a_generic_locator():
    assert judge("heal", 0.9, found(ADD_P100), found(ADD_P105), 0.8)["correct"]
    low = judge("heal", 0.7, found(ADD_P100), found(ADD_P105), 0.8)
    assert low["llm_locator_ok"] and not low["correct"]
    specific = judge("heal", 0.9, found(ADD_P100), found(), 0.8)
    assert specific["target_ok"] and not specific["generic_ok"] and not specific["correct"]


def test_a_confident_wrong_pick_is_never_correct():
    assert not judge("heal", 0.95, found(WISH_P100), found(), 0.8)["correct"]


def test_no_heal_is_correct_unless_it_would_auto_apply():
    assert not judge("no_heal", 0.9, found(WISH_P100), found(), 0.8)["correct"]
    assert judge("no_heal", 0.4, found(WISH_P100), found(), 0.8)["correct"]
    assert judge("no_heal", None, NONE, NONE, 0.8)["correct"]


def test_a_confident_hallucinated_locator_is_rescued_by_verification():
    result = judge("no_heal", 0.9, found(), found(), 0.8)
    assert result["correct"] and result["rescued_by_verify"] and not result["llm_refused"]
