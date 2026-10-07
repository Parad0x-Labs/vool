from core.context_retrieval import _distill_retrieved_hits, _recall_query_hits


def test_pet_answer_not_buried_by_carpet_catalogue():
    fact = "Please remember my pet's name is Bramble."
    noise = "\n".join(f"Carpet Catalogue Name Directory lists {n} products from Market House." for n in range(11, 24))
    text,_ = _distill_retrieved_hits("What is my pet's name?", [(fact + "\n" + noise, 1.0)])
    assert "pet's name is Bramble" in text

def test_car_answer_not_buried_by_cartography():
    fact = "Remember my car's nickname is Meteor."
    noise = "\n".join(f"Cartography Nickname Atlas from Survey House shows {n} locations." for n in range(11, 24))
    text,_ = _distill_retrieved_hits("What is my car's nickname?", [(fact + "\n" + noise, 1.0)])
    assert "car's nickname is Meteor" in text

def test_inflections_and_hyphen_compounds_remain_relevant():
    assert _recall_query_hits("tool storage", {"tools"}) == {"tools"}
    assert _recall_query_hits("hydrogen-alpha filter", {"alpha"}) == {"alpha"}
    assert not _recall_query_hits("carpet cartography depart", {"pet", "car", "art"})
