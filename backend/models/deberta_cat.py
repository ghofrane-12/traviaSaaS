import os
import joblib
import torch
import torch.nn as nn
import torch.nn.functional as F
from transformers import AutoTokenizer, AutoModel, AutoConfig

DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")

# ── Lazy loading ──────────────────────────────────────────────
_tokenizer    = None
_model_temp   = None
_cat_encoder  = None
_lang_encoder = None

class MultiTaskDeberta(nn.Module):
    def __init__(self, model_name, num_cat, num_lang):
        super().__init__()
        config         = AutoConfig.from_pretrained(model_name, local_files_only=True)
        self.backbone  = AutoModel.from_config(config)
        hidden         = self.backbone.config.hidden_size
        self.dropout   = nn.Dropout(0.2)
        self.cat_head  = nn.Sequential(
            nn.Linear(hidden, hidden), nn.GELU(),
            nn.Dropout(0.3), nn.Linear(hidden, num_cat)
        )
        self.lang_head = nn.Linear(hidden, num_lang)

    def forward(self, input_ids, attention_mask):
        outputs     = self.backbone(input_ids=input_ids, attention_mask=attention_mask)
        last_hidden = outputs.last_hidden_state
        mask        = attention_mask.unsqueeze(-1).expand(last_hidden.size()).float()
        sum_emb     = torch.sum(last_hidden * mask, dim=1)
        sum_mask    = mask.sum(dim=1).clamp(min=1e-9)
        pooled      = self.dropout(sum_emb / sum_mask)
        return self.cat_head(pooled), self.lang_head(pooled)

def _get_model_dir(model_name: str, default_base: str = "trained_models") -> str:
    """Retourne le meilleur chemin disponible pour un modèle."""
    tmp_path = f"/tmp/models_cache/{model_name}"
    if os.path.exists(tmp_path):
        return tmp_path
    base_dir = os.getenv("MODELS_DIR", default_base)
    return os.path.join(base_dir, model_name)

def _load_deberta():
    global _tokenizer, _model_temp, _cat_encoder, _lang_encoder
    if _model_temp is None:
        import time, logging
        logger = logging.getLogger("chat_logger")
        model_dir = _get_model_dir("mdeberta_finetuned")
        t0 = time.time()
        logger.info(f"[DeBERTa] 🔄 Chargement depuis {model_dir}...")
        _tokenizer    = AutoTokenizer.from_pretrained(model_dir, local_files_only=True)
        _cat_encoder  = joblib.load(os.path.join(model_dir, "category_encoder.pkl"))
        _lang_encoder = joblib.load(os.path.join(model_dir, "language_encoder.pkl"))
        _model_temp   = MultiTaskDeberta(
            model_name=model_dir,
            num_cat=len(_cat_encoder.classes_),
            num_lang=len(_lang_encoder.classes_),
        ).to(DEVICE).float()
        state_dict = torch.load(
            os.path.join(model_dir, "model_weights.pt"),
            map_location=DEVICE, weights_only=False
        )
        _model_temp.load_state_dict(state_dict, strict=False)
        _model_temp.eval()
        logger.info(f"[DeBERTa] ✅ Chargé en {time.time()-t0:.2f}s sur {DEVICE}")
    return _tokenizer, _model_temp, _cat_encoder, _lang_encoder


def predict_deberta(text: str) -> dict:
    tokenizer, model, cat_encoder, lang_encoder = _load_deberta()

    encoding = tokenizer(
        text,
        return_tensors="pt",
        truncation=True,
        padding=True,
        max_length=192
    ).to(DEVICE)

    with torch.no_grad():
        cat_logits, lang_logits = model(
            input_ids=encoding["input_ids"],
            attention_mask=encoding["attention_mask"]
        )

    cat_probs  = F.softmax(cat_logits,  dim=1)
    lang_probs = F.softmax(lang_logits, dim=1)
    cat_pred   = torch.argmax(cat_probs,  dim=1).item()
    lang_pred  = torch.argmax(lang_probs, dim=1).item()

    return {
        "category":              cat_encoder.inverse_transform([cat_pred])[0],
        "language":              lang_encoder.inverse_transform([lang_pred])[0],
        "category_confidence":   float(cat_probs.max()),
        "language_confidence":   float(lang_probs.max()),
    }