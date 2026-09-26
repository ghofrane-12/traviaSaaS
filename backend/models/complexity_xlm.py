# models/complexity_xlm.py
import torch
import os
from transformers import AutoModelForSequenceClassification, AutoTokenizer

DEVICE    = "cuda" if torch.cuda.is_available() else "cpu"
THRESHOLD = 0.40

# ── Lazy loading ──────────────────────────────────────────────
_cpx_tokenizer = None
_cpx_model     = None
def _get_model_dir(model_name: str, default_base: str = "trained_models") -> str:
    """Retourne le meilleur chemin disponible pour un modèle."""
    tmp_path = f"/tmp/models_cache/{model_name}"
    if os.path.exists(tmp_path):
        return tmp_path
    base_dir = os.getenv("MODELS_DIR", default_base)
    return os.path.join(base_dir, model_name)


def _load_complexity():
    global _cpx_tokenizer, _cpx_model
    if _cpx_model is None:
        import time, logging
        logger = logging.getLogger("chat_logger")
        model_dir = _get_model_dir("xlm-roberta-base-is-complex")
        t0 = time.time()
        logger.info(f"[Complexity] 🔄 Chargement depuis {model_dir}...")
        _cpx_tokenizer = AutoTokenizer.from_pretrained(model_dir, local_files_only=True)
        _cpx_model     = AutoModelForSequenceClassification.from_pretrained(model_dir, local_files_only=True)
        _cpx_model.to(DEVICE).eval()
        logger.info(f"[Complexity] ✅ Chargé en {time.time()-t0:.2f}s sur {DEVICE}")
    return _cpx_tokenizer, _cpx_model


def predict_complexity(text: str) -> dict:
    tokenizer, model = _load_complexity()

    inputs = tokenizer(
        text,
        return_tensors="pt",
        truncation=True,
        max_length=128,
        padding=True
    ).to(DEVICE)

    with torch.no_grad():
        logits = model(**inputs).logits

    probs        = torch.softmax(logits, dim=-1)[0]
    prob_complex = probs[1].item()

    is_complex = prob_complex >= THRESHOLD
    confidence = prob_complex if is_complex else probs[0].item()

    return {
        "is_complex":   bool(is_complex),
        "label":        "COMPLEX_MULTI_INTENT" if is_complex else "SIMPLE",
        "confidence":   round(confidence, 4),
        "prob_complex": round(prob_complex, 4),
        "source":       "model",
    }