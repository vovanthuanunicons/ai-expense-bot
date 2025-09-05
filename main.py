# main.py — Bot Telegram quản lý chi tiêu (FastAPI + Google Sheets)
import os, json, re, datetime, requests
from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
import gspread
from google.oauth2.service_account import Credentials

app = FastAPI()

# ====== Cấu hình từ biến môi trường ======
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_CHAT_IDS = set(x.strip() for x in os.getenv("ALLOWED_CHAT_IDS", "").split(",") if x.strip())
GOOGLE_SHEET_KEY = os.getenv("GOOGLE_SHEET_KEY")
GOOGLE_CREDS_JSON = os.getenv("GOOGLE_CREDS_JSON")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "secret")

# Tên tab trong Sheet (khớp với file bạn đã tạo)
SHEET_TAB_NAME = os.getenv("SHEET_TAB_NAME", "Chitieu")
CONFIG_TAB_NAME = os.getenv("CONFIG_TAB_NAME", "Config")

# ====== Kết nối Google Sheets ======
if not GOOGLE_CREDS_JSON or not GOOGLE_CREDS_JSON.strip().startswith("{"):
    raise RuntimeError("Chưa cấu hình GOOGLE_CREDS_JSON (nội dung JSON service account).")

creds_info = json.loads(GOOGLE_CREDS_JSON)
scopes = ["https://www.googleapis.com/auth/spreadsheets"]
credentials = Credentials.from_service_account_info(creds_info, scopes=scopes)
gc = gspread.authorize(credentials)

wb = gc.open_by_key(GOOGLE_SHEET_KEY)
try:
    sheet = wb.worksheet(SHEET_TAB_NAME)
except:
    sheet = wb.sheet1  # fallback: tab đầu tiên
config_sheet = wb.worksheet(CONFIG_TAB_NAME)

# ====== Kiểu dữ liệu cho Telegram update ======
class TelegramUpdate(BaseModel):
    update_id: int | None = None
    message: dict | None = None
    edited_message: dict | None = None

# ====== Helper: gửi tin nhắn về Telegram ======
def send_message(chat_id: str, text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print("send_message error:", e)

# ====== Parse tin nhắn chi tiêu ======
def parse_expense(text: str):
    """
    Ví dụ: 'ăn trưa 75k #food' hoặc 'mua sách 120000 #education'
    Trả về: (amount:int|None, category:str, note:str)
    """
    amount = None
    category = "khác"
    note = text.strip()

    m = re.search(r'(\d[\d\.]*)\s*(k|nghìn|ngàn|vnđ|vnd)?', text, re.IGNORECASE)
    if m:
        raw = m.group(1).replace('.', '')
        unit = (m.group(2) or '').lower()
        try:
            amt = float(raw)
            if unit in ['k', 'nghìn', 'ngàn']:
                amt *= 1000
            amount = int(amt)
        except:
            pass

    m2 = re.search(r'#([a-z0-9_\-]+)', text, re.IGNORECASE)
    if m2:
        category = m2.group(1).lower()

    return amount, category, note

# ====== Ghi dòng vào Sheet ======
def append_row(row):
    # row = [Ngày, Số tiền, Nhóm, Ghi Chú, ChatId]
    sheet.append_row(row, value_input_option="USER_ENTERED")

# ====== Hạn mức tháng từ tab Config ======
def get_monthly_limit():
    try:
        value = config_sheet.acell("B1").value
        return int(str(value).replace(",", "").strip())
    except:
        return 9000000  # mặc định

# ====== Tính tổng theo kỳ ======
def iter_records():
    # Kỳ vọng header: Ngày | Số tiền | Nhóm | Ghi Chú | ChatId
    return sheet.get_all_records()

def is_same_week(d1: datetime.datetime, d2: datetime.datetime):
    return d1.isocalendar().week == d2.isocalendar().week and d1.year == d2.year

def is_same_month(d1: datetime.datetime, d2: datetime.datetime):
    return d1.month == d2.month and d1.year == d2.year

def is_same_quarter(d1: datetime.datetime, d2: datetime.datetime):
    return (d1.month - 1)//3 == (d2.month - 1)//3 and d1.year == d2.year

def sum_period(chat_id: str, period: str):
    now = datetime.datetime.now()
    total = 0
    for r in iter_records():
        try:
            date = datetime.datetime.strptime(r["Ngày"], "%Y-%m-%d %H:%M:%S")
            cond = (
                is_same_week(date, now) if period == "tuan" else
                is_same_quarter(date, now) if period == "quy" else
                is_same_month(date, now)
            )
            if cond and str(r.get("ChatId", "")) == str(chat_id):
                total += int(str(r["Số tiền"]).replace(",", ""))
        except:
            continue
    return total

# ====== Routes ======
@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/")
def root():
    return {"status": "ok"}

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post(f"/telegram/webhook/{WEBHOOK_SECRET}")
async def telegram_webhook(update: TelegramUpdate, request: Request):
    body = await request.json()
    msg = body.get("message") or body.get("edited_message")
    if not msg:
        return {"ok": True}

    chat_id = str(msg["chat"]["id"])
    if ALLOWED_CHAT_IDS and chat_id not in ALLOWED_CHAT_IDS:
        raise HTTPException(status_code=403, detail="Không được phép")

    text = (msg.get("text") or "").strip()
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Lệnh /start hoặc help
    if text.lower().startswith("/start") or text.lower() == "help":
        send_message(chat_id,
            "Xin chào! Gõ ví dụ: 'ăn trưa 75k #food'\n"
            "- Báo cáo: 'baocao tuan|thang|quy'\n"
            "- Đổi hạn mức: 'hanmuc 9500000'\n"
            f"- Hạn mức hiện tại: {get_monthly_limit():,}đ"
        )
        return {"ok": True}

    # Lệnh hanmuc
    if text.lower().startswith("hanmuc"):
        m = re.search(r'(\d[\d\.]*)', text)
        if m:
            new_limit = int(m.group(1).replace(".", ""))
            config_sheet.update("B1", str(new_limit))
            send_message(chat_id, f"✅ Đã cập nhật hạn mức tháng: {new_limit:,}đ")
        else:
            send_message(chat_id, "❌ Không tìm thấy số. Dùng: hanmuc 9500000")
        return {"ok": True}

    # Lệnh baocao
    if text.lower().startswith("baocao"):
        period = "thang"
        if "tuan" in text.lower():
            period = "tuan"
        elif "quy" in text.lower():
            period = "quy"
        total = sum_period(chat_id, period)
        limit = get_monthly_limit()
        extra = f"\nHạn mức tháng: {limit:,}đ" if period == "thang" else ""
        send_message(chat_id, f"📊 Tổng chi {period} này: {total:,}đ{extra}")
        return {"ok": True}

    # Mặc định: coi là ghi chi tiêu
    amount, category, note = parse_expense(text)
    if amount is None:
        send_message(chat_id, "❌ Mình chưa thấy số tiền. Ví dụ: 'cà phê 35k #drink'")
        return {"ok": True, "note": "no-amount"}

    row = [now_str, amount, category, note, chat_id]
    append_row(row)

    # Cảnh báo hạn mức
    month_total = sum_period(chat_id, "thang")
    limit = get_monthly_limit()
    warn = ""
    if month_total > limit:
        warn = f"\n⚠️ ĐÃ VƯỢT hạn mức {limit:,}đ trong tháng!"

    send_message(chat_id, f"✅ Đã ghi: {amount:,}đ #{category}{warn}")
    return {"ok": True, "saved": row}
