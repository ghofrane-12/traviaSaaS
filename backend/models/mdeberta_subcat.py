# models/mdeberta_subcat.py

import json
import torch
from transformers import AutoTokenizer, AutoModelForSequenceClassification
import os

BASE_DIR = os.getenv("MODELS_DIR", "trained_models")
MAPPINGS_PATH = os.path.join(BASE_DIR, "travel_model_30subcats")
MODEL_PATH    = os.path.join(MAPPINGS_PATH, "mdeberta_subcategories")
tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH, local_files_only=True)
model     = AutoModelForSequenceClassification.from_pretrained(MODEL_PATH, local_files_only=True)

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
model.to(device)
model.eval()

# ── Mappings ──────────────────────────────────────────────────────────────────
with open(f"{MAPPINGS_PATH}/subcat_id2label.json") as f:
    ID2LABEL: dict[int, str] = {int(k): v for k, v in json.load(f).items()}

with open(f"{MAPPINGS_PATH}/subcat_to_cat.json") as f:
    SUBCAT_TO_CAT: dict[str, str] = json.load(f)


def predict_subcat(text: str, top_k: int = 2) -> dict:
    """
    PURE INFERENCE — retourne labels texte directement.

    Retourne :
    {
        "pred_id":    int,
        "label":      str,    ex: "Flight Booking"
        "category":   str,    ex: "Reservation"
        "confidence": float,
        "topk": [
            {"id": int, "label": str, "category": str, "confidence": float},
            ...
        ]
    }
    """
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
                "id":         i,
                "label":      ID2LABEL.get(i, str(i)),
                "category":   SUBCAT_TO_CAT.get(ID2LABEL.get(i, ""), ""),
                "confidence": p,
            }
            for i, p in zip(topk_ids, topk_probs)
        ],
    }