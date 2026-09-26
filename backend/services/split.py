import json
import re
from core.config import settings
import httpx


def llm_split(query: str):
    prompt = f"""
    Tu es un assistant spécialisé en voyage.
    Analyse la requête suivante et détermine si elle contient plusieurs intentions.

    - Si elle contient UNE SEULE intention : retourne-la telle quelle dans une liste.
    - Si elle contient PLUSIEURS intentions : découpe-la en segments clairs,
    chaque segment représentant UNE intention utilisateur.

    IMPORTANT :
    - Une requête avec une destination ET des dates pour UN SEUL service = UNE SEULE intention.
    - Garde les phrases EXACTEMENT dans la langue d'origine (ne traduis pas, ne reformule pas).
    - Retourne uniquement une liste JSON de chaînes de texte.
    - Pas de balises Markdown, pas d'explications.

    Exemples :
    Input: "n7eb nrizervi vol l-Istanbul w nchouf les procédures mta3 el visa"
    Output: ["n7eb nrizervi vol l-Istanbul", "nchouf les procédures mta3 el visa"]

    Input: "réserver un vol et hôtel"
    Output: ["réserver un vol", "réserver un hôtel"]

    Input: "annuler mon vol"
    Output: ["annuler mon vol"]

    Input: "{query}"
    Output:
    """

    response = httpx.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": "gpt-4o-mini",
            "max_tokens": 256,
            "temperature": 0.1,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=15,
    )
    response.raise_for_status()

    text = response.json()["choices"][0]["message"]["content"].strip()

    text = re.sub(r"^```json", "", text)
    text = re.sub(r"```$", "", text)
    text = text.strip()

    try:
        segments = json.loads(text)
    except:
        segments = [seg.strip() for seg in text.split("\n") if seg.strip()]

    return segments