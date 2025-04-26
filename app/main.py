import os
import sqlite3
from datetime import datetime
from fastapi import FastAPI, Request, Form, HTTPException, Depends
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
import httpx
from starlette.status import HTTP_303_SEE_OTHER, HTTP_401_UNAUTHORIZED
from dotenv import load_dotenv

from database import init_db, save_message, DB_FILE

# Load environment variables
load_dotenv()

# Configurations from .env
APP_VERSION = os.getenv("APP_VERSION", "dev")  # fallback to 'dev' if missing
APP_USERNAME = os.getenv("APP_USERNAME")
APP_PASSWORD = os.getenv("APP_PASSWORD")
TELNYX_API_KEY = os.getenv("TELNYX_API_KEY")
TELNYX_MESSAGING_PROFILE_ID = os.getenv("TELNYX_MESSAGING_PROFILE_ID")
TELNYX_FROM_NUMBER = os.getenv("TELNYX_FROM_NUMBER")
TELNYX_PUBLIC_KEY = os.getenv("TELNYX_PUBLIC_KEY")
SESSION_SECRET = os.getenv("SESSION_SECRET")
REFRESH_INTERVAL_SECONDS = int(os.getenv("REFRESH_INTERVAL_SECONDS", "10"))

# Initialize App
app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET)

templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

# Initialize Database
init_db()

# --- Session Authentication Middleware ---
def get_current_user(request: Request):
    if not request.session.get('user'):
        request.session['flash'] = {
            "type": "warning",
            "message": "Session expired. Please log in again."
        }
        request.session['next_url'] = str(request.url.path)
        return RedirectResponse("/login", status_code=303)
    return request.session['user']

# --- Routes ---

@app.get("/login")
async def login_page(request: Request):
    flash = request.session.pop('flash', None)
    return templates.TemplateResponse("login.html", {"request": request, "flash": flash})

@app.post("/login")
async def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    if username == APP_USERNAME and password == APP_PASSWORD:
        request.session['user'] = username
        next_url = request.session.pop('next_url', '/')
        return RedirectResponse(next_url, status_code=HTTP_303_SEE_OTHER)
    else:
        request.session['flash'] = {"type": "danger", "message": "Invalid login"}
        return RedirectResponse("/login", status_code=HTTP_303_SEE_OTHER)

@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=HTTP_303_SEE_OTHER)

@app.get("/")
async def inbox(request: Request, user: str | RedirectResponse = Depends(get_current_user)):
    if isinstance(user, RedirectResponse):
        return user

    flash = request.session.pop('flash', None)
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM messages ORDER BY id DESC")
    messages = cursor.fetchall()
    conn.close()
    return templates.TemplateResponse("index.html", {"request": request, "messages": messages, "flash": flash, "refresh_interval": REFRESH_INTERVAL_SECONDS, "app_version": APP_VERSION})

@app.get("/send")
async def send_page(request: Request, user: str | RedirectResponse = Depends(get_current_user)):
    if isinstance(user, RedirectResponse):
        return user

    flash = request.session.pop('flash', None)
    return templates.TemplateResponse("send.html", {"request": request, "flash": flash, "app_version": APP_VERSION})

@app.post("/send-sms")
async def send_sms(request: Request, to: str = Form(...), message: str = Form(...), user: str | RedirectResponse = Depends(get_current_user)):
    if isinstance(user, RedirectResponse):
        return user

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

    error_detail = None
    cost = None

    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=payload, headers=headers)

    if response.status_code in [200, 202]:
        try:
            resp_json = response.json()
            cost = resp_json.get("data", {}).get("cost", None)
            if isinstance(cost, dict):
                cost = None
        except Exception:
            cost = None

        save_message(
            "outgoing",
            TELNYX_FROM_NUMBER,
            to,
            message,
            datetime.utcnow().isoformat(),
            status="sent",
            cost=cost
        )
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

    return RedirectResponse("/", status_code=HTTP_303_SEE_OTHER)

@app.post("/webhook")
async def webhook(request: Request):
    payload = await request.json()
    event_type = payload.get("data", {}).get("event_type")
    message_data = payload.get("data", {}).get("payload", {})

    if event_type == "message.received":
        from_number = message_data.get("from", {}).get("phone_number")
        to_number = message_data.get("to", [{}])[0].get("phone_number")
        text = message_data.get("text")
        timestamp = message_data.get("received_at", datetime.utcnow().isoformat())

        cost_info = message_data.get("cost", {})
        cost = cost_info.get("amount") if isinstance(cost_info, dict) else None

        save_message(
            direction="incoming",
            from_number=from_number,
            to_number=to_number,
            body=text,
            timestamp=timestamp,
            status="received",
            cost=cost
        )

    return {"status": "ok"}

@app.get("/messages")
async def get_messages(request: Request, user: str | RedirectResponse = Depends(get_current_user)):
    if isinstance(user, RedirectResponse):
        return user

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM messages ORDER BY id DESC")
    messages = cursor.fetchall()
    conn.close()
    return {"messages": messages}

@app.get("/resend/{message_id}")
async def resend_message(message_id: int, request: Request, user: str | RedirectResponse = Depends(get_current_user)):
    if isinstance(user, RedirectResponse):
        return user

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT to_number, body FROM messages WHERE id = ?", (message_id,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        request.session['flash'] = {"type": "danger", "message": "Message not found"}
        return RedirectResponse("/", status_code=HTTP_303_SEE_OTHER)

    to, body = row
    return await send_sms(request, to=to, message=body, user=user)
