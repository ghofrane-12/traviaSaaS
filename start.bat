@echo off
echo Demarrage Cloud SQL...
call gcloud sql instances patch travel-ai-db --activation-policy=ALWAYS --project=travel-saas-pfe

echo Attente que la DB soit prete...
:wait_loop
timeout /t 15 /nobreak >nul
for /f %%i in ('gcloud sql instances describe travel-ai-db --format="value(state)" --project=travel-saas-pfe') do set STATE=%%i
echo Etat: %STATE%
if not "%STATE%"=="RUNNABLE" goto wait_loop

echo Cloud SQL RUNNABLE - attente supplementaire 60 secondes...
timeout /t 60 /nobreak

echo Demarrage Cloud Run...
call gcloud run services update backend --region=europe-west1 --min-instances=1 --project=travel-saas-pfe

echo Environnement pret !
pause