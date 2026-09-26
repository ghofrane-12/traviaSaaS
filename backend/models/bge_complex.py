import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer
import os

BASE_DIR = os.getenv("MODELS_DIR", "trained_models")

MODEL_DIR     = os.path.join(BASE_DIR, "bge-m3-iscomplex")
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"


cpx_tokenizer = AutoTokenizer.from_pretrained(MODEL_DIR, local_files_only=True)
cpx_model     = AutoModelForSequenceClassification.from_pretrained(MODEL_DIR, local_files_only=True)
cpx_model.to(DEVICE).eval()

def predict_complexity(text):
    inputs = cpx_tokenizer(text, return_tensors="pt",
                           truncation=True, max_length=128, padding=True).to(DEVICE)
    with torch.no_grad():
        logits = cpx_model(**inputs).logits
    probs   = torch.softmax(logits, dim=-1)[0]
    pred_id = probs.argmax().item()
    return {
        "is_complex": bool(pred_id),
        "label":      "Complex" if pred_id else "Simple",
        "confidence": round(probs[pred_id].item(), 4)
    }
