import hashlib
import logging
import os

from core import audit_logger
from core.provider_invocation_gateway import seal_direct_provider_invocation

logger = logging.getLogger(__name__)

# State cache so we only load the pipeline once per execution context
_JUDGE_PIPELINE = None
_LLM_LOAD_ATTEMPTED = False

def _init_llm_judge():
    global _JUDGE_PIPELINE, _LLM_LOAD_ATTEMPTED
    if _LLM_LOAD_ATTEMPTED:
        return _JUDGE_PIPELINE

    _LLM_LOAD_ATTEMPTED = True
    if os.environ.get("VOOL_ENABLE_LOCAL_SEMANTIC_MODEL") != "1":
        logger.info("Semantic Judge local model not enabled; using deterministic fallback.")
        return None
    try:
        # Phase 30: Semantic Consensus (LLM-as-a-judge)
        # Use cached local weights only. If the model is not already present, we fall back immediately.
        from transformers import AutoModelForSequenceClassification, AutoTokenizer, TextClassificationPipeline

        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        model_name = "cross-encoder/stsb-distilroberta-base"
        tokenizer = AutoTokenizer.from_pretrained(model_name, local_files_only=True)
        model = AutoModelForSequenceClassification.from_pretrained(model_name, local_files_only=True)
        _JUDGE_PIPELINE = TextClassificationPipeline(
            model=model,
            tokenizer=tokenizer,
            device=-1,
        )
        audit_logger.log("semantic_judge_initialized", target_id="llm", target_type="consensus", details={})
    except ImportError:
        logger.warning("transformers not installed, Semantic Judge disabled.")
    except Exception as e:
        logger.warning(f"Failed to load Semantic Judge LLM: {e}")

    return _JUDGE_PIPELINE

def evaluate_semantic_agreement(summary_a: str, summary_b: str) -> float:
    """
    Evaluates the semantic similarity of two AI responses.
    Returns 1.0 for identical semantics, 0.0 for complete disagreement.
    If the LLM pipeline is not available, falls back to token intersection.
    """
    if not summary_a or not summary_b:
        return 0.5

    judge = _init_llm_judge()
    if judge:
        try:
            # Cross-encoder formats inputs as pairs
            payload = {
                "inputs": {
                    "text": summary_a,
                    "text_pair": summary_b,
                }
            }
            permit = seal_direct_provider_invocation(
                provider_id="transformers:semantic-judge",
                model_id="cross-encoder/stsb-distilroberta-base",
                operation="semantic_similarity",
                payload=payload,
                request_id=(
                    "semantic-judge-"
                    + hashlib.sha256(
                        f"{summary_a}\0{summary_b}".encode()
                    ).hexdigest()
                ),
            )
            result = judge(permit.consume()["inputs"])
            if isinstance(result, list):
                result = result[0]
            # stsb outputs scores typically between 0 and 1
            score = float(result.get("score", 0.5))
            return max(0.0, min(1.0, score))
        except Exception as e:
            logger.warning(f"LLM semantic evaluation failed, falling back: {e}")

    # Fallback tokenizer algorithm (Legacy Phase 2 behaviour)
    def _tokenize(text: str) -> set[str]:
        return {t for t in "".join(ch if ch.isalnum() else " " for ch in text.lower()).split() if len(t) > 2}

    ta = _tokenize(summary_a)
    tb = _tokenize(summary_b)
    if not ta or not tb:
        return 0.5
    overlap = len(ta & tb)
    union = len(ta | tb)
    return overlap / max(1, union)
