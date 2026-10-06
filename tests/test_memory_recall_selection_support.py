from core.context_retrieval import _distill_retrieved_hits, _recall_support_chunks, _query_overlap_terms

def capsule(q, *records):
    return _distill_retrieved_hits(q, [(r, 1.0) for r in records])[0]

def test_name_outweighs_numeric_catalogue():
    s = "Remember my parrot: my parrot's name is Pip.\n" + "\n".join(
        f"Parrot Supplies from Harbor Market include {n} premium products and {n + 12} accessories."
        for n in range(12, 24))
    assert "parrot's name is Pip" in capsule("What is my parrot's name?", s)

def test_location_outweighs_value_rich_advice():
    s = "Please remember I keep my spare compass inside the canvas pouch.\n" + "\n".join(
        f"Keep Harbour Outdoor equipment ready with {n} batteries and {n + 7} carrying straps."
        for n in range(10, 22))
    assert "spare compass inside the canvas pouch" in capsule("Where do I keep my spare compass?", s)

def test_supporting_duration_stays_with_subject():
    s = "Please remember how long my kiln repair took. Seven weeks passed before it worked again.\n" + "\n".join(
        f"Long Term Workshop plans include {n} annual inspections and {n + 10} tools."
        for n in range(11, 24))
    assert "Seven weeks passed" in capsule("How long did my kiln repair take?", s)

def test_support_retains_negative_assertion():
    s = "Please remember the duration of my stall closure. It did not last two months.\n" + "\n".join(
        f"Market Stall Supplies offers {n} products and a {n + 15} percent discount."
        for n in range(11, 24))
    assert "It did not last two months" in capsule("What was the duration of my stall closure?", s)

def test_support_does_not_cross_paragraph_boundary():
    anchor = "Remember how long my timber treatment took."
    chunks = _recall_support_chunks(anchor + "\n\nFreight delivery took nine weeks.", _query_overlap_terms("How long did my timber treatment take?"))
    assert chunks[0] == anchor

def test_support_does_not_cross_speaker_line():
    anchor = "USER: Remember how long my mosaic installation took."
    chunks = _recall_support_chunks(anchor + "\nASSISTANT: A generic project takes twelve months.", _query_overlap_terms("How long did my mosaic installation take?"))
    assert chunks[0] == anchor
