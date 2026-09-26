"""import os
import pickle
import numpy as np
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity
from scipy.special import softmax

MODEL_PATH = "C:/Users/MSI/OneDrive/Bureau/test_projet/backend/trained_models/bge-m3-finetuned-v2"

# Charger le modèle
model = SentenceTransformer(MODEL_PATH)
model.max_seq_length = 256

# Charger les données
with open(os.path.join(MODEL_PATH, "data.pkl"), "rb") as f:
    data = pickle.load(f)

centroids = data["centroids"]
subcat_names = data["subcat_names"]
sub_to_cat = data["sub_to_cat"]
train_embeddings = data["train_embeddings"]
train_texts = data["train_texts"]
train_df = data["train_df"]

centroid_matrix = np.vstack([centroids[s] for s in subcat_names])

# Hyperparams
TEMPERATURE = 0.7
CENTROID_THRESHOLD = 0.3
TOP_K = 15

def predict_bge(query):
    q = query.strip().lower()
    q_emb = model.encode([q], normalize_embeddings=True)
    sims = cosine_similarity(q_emb, centroid_matrix)[0]
    probs = softmax(sims / TEMPERATURE)
    top_idx = sims.argsort()[::-1][:TOP_K]
    top_subcats = [subcat_names[i] for i in top_idx]
    top_probs = probs[top_idx]

    mask = train_df["sub_cat"].isin(top_subcats)
    candidate_embeddings = train_embeddings[mask.values]
    candidate_labels = train_df[mask]["sub_cat"].values
    candidate_sims = cosine_similarity(q_emb, candidate_embeddings)[0]

    final_scores = []
    for emb_sim, label in zip(candidate_sims, candidate_labels):
        idx = top_subcats.index(label)
        score = 0.9 * emb_sim + 0.1 * top_probs[idx]
        final_scores.append(score)
    final_scores = np.array(final_scores)

    best_idx = final_scores.argmax()
    best_subcat = candidate_labels[best_idx]
    best_score = float(final_scores[best_idx])

    if best_score < CENTROID_THRESHOLD:
        return "Unknown", "Unknown", best_score

    cat = sub_to_cat.get(best_subcat, "Unknown")
    return cat, best_subcat, best_score"""



