from transformers import AutoTokenizer

t = AutoTokenizer.from_pretrained('microsoft/mdeberta-v3-base')
t.save_pretrained('trained_models/mdeberta_finetuned/', legacy_format=True)