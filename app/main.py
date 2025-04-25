import os
import base64
import nacl.signing
import nacl.encoding
from datetime import datetime

from fastapi import FastAPI, Request, Form, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
import httpx
from dotenv import load_dotenv

from database import init_db, save_message, get_all_messages

load_dotenv()
init_db()

TELNYX_API_KEY = os.getenv("TELNYX_API_KEY")
TELNYX_MESSAGING_PROFILE_ID = os.getenv("TELNYX_MESSAGING_PROFILE_ID")
TELNYX_FROM_NUMBER = os.getenv("TELNYX_FROM_NUMBER")
TELNYX_PUBLIC_KEY = os.getenv("TELNYX_PUBLIC_KEY")

APP_USERNAME = os.getenv("APP_USERNAME")
APP_PASSWORD = os.getenv("APP_PASSWORD")
SESSION_SECRET = os.getenv("SESSION_SECRET", "supersecret_session_key")
REFRESH_INTERVAL_SECONDS = int(os.getenv("REFRESH_INTERVAL_SECONDS", "10"))

app = FastAPI()
security = HTTPBasic()
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET)

def authenticate(credentials: HTTPBasicCredentials = Depends(security)):
    if credentials.username != APP_USERNAME or credentials.password != APP_PASSWORD:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )

def verify_telnyx_signature(signature: str, timestamp: str, body: bytes):
    if not TELNYX_PUBLIC_KEY:
        raise Exception("TELNYX_PUBLIC_KEY is not set.")

    message = timestamp.encode() + body
    verify_key = nacl.signing.VerifyKey(TELNYX_PUBLIC_KEY, encoder=nacl.encoding.Base64Encoder)

    try:
        verify_key.verify(message, base64.b64decode(signature))
        return True
    except Exception:
        return False

@app.get("/", response_class=HTMLResponse)
async def read_messages(request: Request, credentials: HTTPBasicCredentials = Depends(authenticate)):
    messages = get_all_messages()
    flash = request.session.pop('flash', None)
    return templates.TemplateResponse("index.html", {"request": request, "messages": messages, "flash": flash, "refresh_interval": REFRESH_INTERVAL_SECONDS})

@app.get("/send", response_class=HTMLResponse)
async def send_form(request: Request, credentials: HTTPBasicCredentials = Depends(authenticate)):
    return templates.TemplateResponse("send.html", {"request": request})

@app.post("/send-sms")
async def send_sms(request: Request, to: str = Form(...), message: str = Form(...), credentials: HTTPBasicCredentials = Depends(authenticate)):
    url = "https://api.telnyx.com/v2/messages"
    headers = {
        "Authorization": f"Bearer {TELNYX_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "from": TELNYX_FROM_NUMBER,
        "to": to,
        "text": message,
        "messaging_profile_id": TELNYX_MESSAGING_PROFILE_ID
    }

    error_detail = None  # ✅ Always define it early!

    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=payload, headers=headers)

    if response.status_code in [200, 202]:
        save_message("outgoing", TELNYX_FROM_NUMBER, to, message, datetime.utcnow().isoformat(), status="sent")
        request.session['flash'] = {"type": "success", "message": "Message sent successfully!"}
    else:
        try:
            error_detail = response.json().get('errors', [{}])[0].get('detail', response.text)
        except Exception:
            error_detail = response.text or "Unknown send error"

        save_message(
            "outgoing",
            TELNYX_FROM_NUMBER,
            to,
            message,
            datetime.utcnow().isoformat(),
            status="failed",
            error_message=error_detail
        )
        request.session['flash'] = {"type": "danger", "message": f"Failed to send message: {error_detail}"}

    return RedirectResponse("/", status_code=303)

@app.post("/webhook")
async def webhook(request: Request):
    signature = request.headers.get("telnyx-signature-ed25519")
    timestamp = request.headers.get("telnyx-timestamp")

    if not signature or not timestamp:
        raise HTTPException(status_code=400, detail="Missing Telnyx signature headers")

    body = await request.body()

    if not verify_telnyx_signature(signature, timestamp, body):
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    data = await request.json()
    payload = data.get("data", {}).get("payload", {})

    from_number = payload.get("from", {}).get("phone_number")
    to_number = payload.get("to", {}).get("phone_number")
    body_text = payload.get("text")

    if from_number and to_number and body_text:
        save_message("incoming", from_number, to_number, body_text, datetime.utcnow().isoformat())

    return {"message": "Webhook received securely"}

@app.get("/messages")
async def api_messages(credentials: HTTPBasicCredentials = Depends(authenticate)):
    messages = get_all_messages()
    return JSONResponse(content={"messages": messages})

@app.get("/resend/{message_id}")
async def resend_message(request: Request, message_id: int, credentials: HTTPBasicCredentials = Depends(authenticate)):
    messages = get_all_messages()
    message = next((m for m in messages if m[0] == message_id and m[1] == "outgoing"), None)

    if not message:
        request.session['flash'] = {"type": "danger", "message": "Message not found or not eligible for resend."}
        return RedirectResponse("/", status_code=303)

    to_number = message[3]
    body_text = message[4]

    url = "https://api.telnyx.com/v2/messages"
    headers = {
        "Authorization": f"Bearer {TELNYX_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "from": TELNYX_FROM_NUMBER,
        "to": to_number,
        "text": body_text,
        "messaging_profile_id": TELNYX_MESSAGING_PROFILE_ID
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=payload, headers=headers)

    if response.status_code in [200, 202]:
        request.session['flash'] = {"type": "success", "message": "Resent message successfully!"}
    else:
        try:
            error_detail = response.json().get('errors', [{}])[0].get('detail', response.text)
        except Exception:
            error_detail = response.text

        request.session['flash'] = {"type": "danger", "message": f"Failed to resend message: {error_detail}"}

    return RedirectResponse("/", status_code=303)
