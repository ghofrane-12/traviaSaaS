# models/xlmr_subcat.py

import json
import torch
from transformers import AutoTokenizer, AutoModelForSequenceClassification
import os


device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

# ── Lazy loading ──────────────────────────────────────────────
_tokenizer = None
_model     = None
_ID2LABEL  = None
_SUBCAT_TO_CAT = None
SUBCAT_TO_CAT: dict = {}
def _get_model_dir(model_name: str, default_base: str = "trained_models") -> str:
    """Retourne le meilleur chemin disponible pour un modèle."""
    tmp_path = f"/tmp/models_cache/{model_name}"
    if os.path.exists(tmp_path):
        return tmp_path
    base_dir = os.getenv("MODELS_DIR", default_base)
    return os.path.join(base_dir, model_name)


def _load_subcat():
    global _tokenizer, _model, _ID2LABEL, _SUBCAT_TO_CAT
    if _model is None:
        import time, logging
        logger = logging.getLogger("chat_logger")
        mappings_path = _get_model_dir("travel_model_32subcats_xlmr_large")
        model_path    = os.path.join(mappings_path, "mdeberta_subcategories")
        t0 = time.time()
        logger.info(f"[SubCat] 🔄 Chargement depuis {model_path}...")
        _tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True)
        _model     = AutoModelForSequenceClassification.from_pretrained(model_path, local_files_only=True)
        _model.to(device).eval()
        with open(f"{mappings_path}/subcat_id2label.json") as f:
            _ID2LABEL = {int(k): v for k, v in json.load(f).items()}
        with open(f"{mappings_path}/subcat_to_cat.json") as f:
            _SUBCAT_TO_CAT = json.load(f)
        SUBCAT_TO_CAT.update(_SUBCAT_TO_CAT)
        logger.info(f"[SubCat] ✅ Chargé en {time.time()-t0:.2f}s sur {device}")
    return _tokenizer, _model, _ID2LABEL, _SUBCAT_TO_CAT


def predict_subcat(text: str, top_k: int = 2) -> dict:
    tokenizer, model, ID2LABEL, SUBCAT_TO_CAT = _load_subcat()

    inputs = tokenizer(
        text,
        return_tensors="pt",
        truncation=True,
        padding=True,
        max_length=128,
    ).to(device)

    with torch.no_grad():
        probs = torch.softmax(model(**inputs).logits[0], dim=-1)

    topk_probs, topk_ids = torch.topk(probs, k=top_k)
    topk_probs = topk_probs.cpu().tolist()
    topk_ids   = topk_ids.cpu().tolist()

    top_label    = ID2LABEL.get(topk_ids[0], str(topk_ids[0]))
    top_category = SUBCAT_TO_CAT.get(top_label, "")

    return {
        "pred_id":    topk_ids[0],
        "label":      top_label,
        "category":   top_category,
        "confidence": topk_probs[0],
        "topk": [
            {
                "id":       i,
                "label":    ID2LABEL.get(i, str(i)),
                "category": SUBCAT_TO_CAT.get(ID2LABEL.get(i, ""), ""),
                "confidence": p,
            }
            for i, p in zip(topk_ids, topk_probs)
        ],
    }