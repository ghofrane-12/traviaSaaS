from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import PlainTextResponse,RedirectResponse
from core.config import settings
from core.database import get_pool
import httpx

router = APIRouter(tags=["Messenger"])

@router.get("/webhook")
async def verify_webhook(request: Request):
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")
    if mode == "subscribe" and token == settings.FB_VERIFY_TOKEN:
        return PlainTextResponse(challenge)
    raise HTTPException(status_code=403, detail="Token invalide")

@router.post("/webhook")
async def receive_messenger_message(request: Request):
    data = await request.json()
    for entry in data.get("entry", []):
        page_id = entry.get("id")
        for event in entry.get("messaging", []):
            sender_id = event["sender"]["id"]
            if "message" in event:
                await send_messenger_message(sender_id, page_id)
    return {"status": "ok"}

async def send_messenger_message(sender_id: str, page_id: str):
    pool = await get_pool()
    row = await pool.fetchrow(
        """SELECT tenant_id, name, facebook_page_token 
           FROM tenants 
           WHERE facebook_page_id = $1""",
        page_id
    )
    if not row:
        print(f"❌ Aucun tenant pour page_id {page_id}")
        return

    page_token = row["facebook_page_token"]
    if not page_token:
        print(f"❌ Token manquant pour page_id {page_id}")
        return

    tenant_id   = str(row["tenant_id"])
    agency_name = row["name"]
    chat_url    = f"https://travel-saas-pfe.web.app/chat?tenant={tenant_id}"

    payload = {
        "recipient": {"id": sender_id},
        "message": {
            "attachment": {
                "type": "template",
                "payload": {
                    "template_type": "button",
                    "text": f"Bonjour ! Bienvenue chez {agency_name} 🌍\nAccédez à notre chatbot de voyage.",
                    "buttons": [{
                        "type": "web_url",
                        "url": chat_url,
                        "title": f"Chatbot {agency_name}"
                    }]
                }
            }
        }
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://graph.facebook.com/v19.0/me/messages",
            json=payload,
            params={"access_token": page_token}  
        )
        if resp.status_code != 200:
            print(f"❌ Facebook API error: {resp.text}")


@router.get("/facebook/connect/{tenant_id}")
async def start_facebook_oauth(tenant_id: str):
    app_id = settings.FB_APP_ID.strip()
    redirect_uri = settings.FB_REDIRECT_URI.strip()
    
    oauth_url = (
    "https://www.facebook.com/dialog/oauth"
    f"?client_id={app_id}"
    f"&redirect_uri={redirect_uri}"
    f"&scope=pages_messaging,pages_manage_metadata,pages_show_list"
    f"&state={tenant_id}"
    f"&response_type=code"
    )
    return RedirectResponse(oauth_url)


@router.get("/facebook/callback")
async def facebook_oauth_callback(code: str, state: str):
    """
    Facebook redirige ici après que l'admin a autorisé l'app
    state = tenant_id
    """
    tenant_id = state

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://graph.facebook.com/v19.0/oauth/access_token",
               params={
                "client_id":     settings.FB_APP_ID.strip(),      
                "client_secret": settings.FB_APP_SECRET.strip(),  
                "redirect_uri":  settings.FB_REDIRECT_URI.strip(), 
                "code":          code,
            }
        )
        user_token = resp.json()["access_token"]

        pages_resp = await client.get(
            "https://graph.facebook.com/v19.0/me/accounts",
            params={"access_token": user_token}
        )
        pages = pages_resp.json().get("data", [])


    if not pages:
        return {"error": "Aucune Page trouvée pour cet admin"}

    page = pages[0]

    pool = await get_pool()
    await pool.execute(
        """UPDATE tenants 
           SET facebook_page_id    = $1,
               facebook_page_token = $2
           WHERE tenant_id = $3""",
        page["id"],
        page["access_token"],  
        tenant_id
    )

    print(f"✅ Agence {tenant_id} connectée → Page {page['name']} ({page['id']})")
    
    return RedirectResponse(
        f"https://travel-saas-pfe.web.app/admin?facebook=connected"
    )