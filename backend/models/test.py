


from bge_subcat import predict_bge


query = "je veux réserver un hotel à djerba"
cat_bge, subcat, score = predict_bge(query)
print(f"Catégorie BGE: {cat_bge}, Sous-catégorie: {subcat}, Score: {score}")

