@echo off
echo Arret Cloud Run...
call gcloud run services update backend --region=europe-west1 --min-instances=0 --project=travel-saas-pfe

echo Arret Cloud SQL...
call gcloud sql instances patch travel-ai-db --activation-policy=NEVER --project=travel-saas-pfe

echo Environnement arrete !
pause