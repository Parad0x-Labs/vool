import json
from types import SimpleNamespace

from core import context_retrieval as cr
from core import embedding_service as es


def test_nomic_wire_tasks_and_space_identity(monkeypatch):
    payloads=[]
    monkeypatch.setattr(es,"_best_embed_model",lambda:"nomic-embed-text")
    monkeypatch.setattr(es,"_neural_down_until",0.0)
    def seal(**kw):
        payloads.append(kw["payload"])
        return SimpleNamespace(consume=lambda:kw["payload"])
    monkeypatch.setattr(es,"seal_direct_provider_invocation",seal)
    class Response:
        def __enter__(self):return self
        def __exit__(self,*a):return None
        def read(self):
            return json.dumps({"embeddings":[[float(i%17) for i in range(768)]]}).encode()
    monkeypatch.setattr(es.urllib.request,"urlopen",lambda *a,**k:Response())
    _,document_backend=es.embed_stamped("A factual document.")
    with es.embedding_query():
        query,query_backend=es.embed_stamped("A factual question?")
    es.embed_stamped("Another document.")
    assert [p["input"][0] for p in payloads] == [
        "search_document: A factual document.",
        "search_query: A factual question?",
        "search_document: Another document."]
    assert document_backend == query_backend == "ollama:nomic-embed-text#retrieval-mrl384-v1"
    assert len(query)==384
    assert abs(sum(x*x for x in query)-1)<1e-9

def test_question_mask_has_no_letter_debris_and_preserves_offsets():
    source="Do cranes fly?\nRemember the code is VERA-261."
    view=cr._recall_assertion_view(source)
    assert len(view)==len(source)
    assert view.index("VERA-261")==source.index("VERA-261")
    assert view.splitlines()[0].strip()==""
    assert "Remember the code is VERA-261." in view

def test_camel_boundaries_are_not_arbitrary_substrings():
    assert cr._recall_query_hits("my SkyPrinter",{"printer"})=={"printer"}
    assert cr._recall_query_hits("a catalogue application",{"cat"})==set()

def test_question_tail_is_not_complete_fact_evidence():
    fact="- relevant context: I now have 26 labels since restarting my specimen collection."
    query="How many labels do I have since restarting my specimen collection?"
    assert cr._capsule_fact_body(fact).lower() not in query.lower()
    assert not cr._content_covered_excluding_query(cr._capsule_fact_body(fact),query.lower(),query,1.0)

def test_similarity_floor_is_vector_space_specific():
    from core.vool_memory import VoolMemory
    assert VoolMemory.semantic_floor("ollama:nomic-embed-text#retrieval-mrl384-v1") == 0.5
    assert VoolMemory.semantic_floor("ollama:nomic-embed-text#p384") == 0.55
    assert VoolMemory.semantic_floor("hash-bow:384") == 0.55

def test_short_named_question_keeps_reference_and_owning_date():
    source="Session date: 2025-02-04\nWhat mount fits my Fujifilm X100V?"
    view=cr._recall_assertion_view(source)
    assert "Mentioned in a question: my Fujifilm X100V." in view
    assert "What mount fits" not in view
    other="Session date: 2025-08-09\nI bought a new tripod."
    result,_=cr._distill_retrieved_hits(
        "Which Fujifilm camera did I mention?", [(source,0.9),(other,0.8)])
    assert "Fujifilm X100V" in result
    line=next(line for line in result.splitlines() if "Fujifilm X100V" in line)
    assert "2025-02-04" in line
    assert "2025-08-09" not in line

def test_completed_event_admission_does_not_admit_event_questions():
    assert cr._score_importance("I recently attended the astronomy lecture on March 8th.") >= 0.35
    assert cr._score_importance("Did I recently attend the astronomy lecture on March 8th?") < 0.35

def test_semantic_recall_keeps_short_compound_assertion():
    text="Session date: 2025-03-19\nI've been using tarragon and dill in my cooking. I've harvested yellow peppers from my allotment."
    result,_=cr._distill_retrieved_hits(
        "What dinner should I make with homegrown ingredients?", [(text,0.9)],
        semantic_record_indices={0})
    assert "tarragon and dill" in result and "yellow peppers" in result
    assert "2025-03-19" in result

def test_token_length_rejection_splits_without_losing_original_weights(monkeypatch):
    accepted=[]
    def backend(chunks,model,timeout=15):
        if any(len(c)>500 for c in chunks):
            raise es._EmbeddingInputTooLong("model token ceiling")
        accepted.extend(chunks)
        return [[1.0,0.0] if "LEFT" in c else [0.0,1.0] for c in chunks]
    monkeypatch.setattr(es,"_ollama_embed_batch",backend)
    source=("LEFT "*200)+("RIGHT "*200)
    vectors=es._embed_chunks_resilient([source,"tail"],"nomic-embed-text")
    assert len(vectors)==2
    assert "".join(accepted)==source+"tail"
    assert vectors[0][0]>0 and vectors[0][1]>0
    assert vectors[1]==[0.0,1.0]
