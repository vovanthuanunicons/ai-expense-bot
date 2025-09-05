# main.py — FastAPI + Polling (không cần webhook)
import os, time, threading, requests
from fastapi import FastAPI, Request

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]            # ENV trên Render
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "dev")     # vẫn giữ, phòng khi dùng webhook
ALLOWED = set(x.strip() for x in os.getenv("ALLOWED_CHAT_IDS", "").split(",") if x.strip())

API = f"https://api.telegram.org/bot{BOT_TOKEN}"

app = FastAPI()

@app.get("/healthz")
def healthz():
    return {"status": "ok"}

# (Giữ webhook để sau này muốn quay lại thì dùng được, nhưng hiện tại ta chạy polling)
@app.post(f"/telegram/webhook/{WEBHOOK_SECRET}")
async def telegram_webhook(request: Request):
    update = await request.json()
    print("WEBHOOK UPDATE:", update)
    handle_update(update)
    return {"ok": True}

def send_message(chat_id: str, text: str):
    try:
        r = requests.post(f"{API}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=10)
        if r.status_code != 200:
            print("sendMessage failed:", r.text)
    except Exception as e:
        print("sendMessage error:", e)

def handle_update(update: dict):
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return
    chat_id = str(msg["chat"]["id"])
    text = (msg.get("text") or "").strip()

    # chặn nếu cấu hình ALLOWED_CHAT_IDS
    if ALLOWED and chat_id not in ALLOWED:
        print(f"Blocked chat_id {chat_id}")
        return

    if text.startswith("/start") or text.lower() == "help":
        send_message(chat_id, "Xin chào! Bot đã chạy (Polling). Gõ: ăn trưa 25k #food")
    else:
        # Tối thiểu: echo lại. (Sau khi chạy được, ta sẽ nối Google Sheet.)
        send_message(chat_id, f"Đã nhận: {text}")

def polling_loop():
    offset = None
    while True:
        try:
            r = requests.get(f"{API}/getUpdates", params={"timeout": 50, "offset": offset}, timeout=60)
            data = r.json()
            for upd in data.get("result", []):
                offset = upd["update_id"] + 1
                print("POLL UPDATE:", upd)
                handle_update(upd)
        except Exception as e:
            print("Polling error:", e)
            time.sleep(2)

@app.on_event("startup")
def on_startup():
    # Chạy polling song song web (nhớ dùng --workers 1)
    threading.Thread(target=polling_loop, daemon=True).start()
    print("Polling started.")
