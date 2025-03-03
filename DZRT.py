import sqlite3
import requests
import asyncio
import datetime
from bs4 import BeautifulSoup
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, CallbackContext

# Bot credentials
BOT_TOKEN = "7802865789:AAFnLWxYYEXfUcw-uq0nVLXluxXQ48QuGyg"
ADMIN_IDS = ["1218778557", "2028523343"]
URL = "https://www.dzrt.com/ar-sa/products"
HEADERS = {"User-Agent": "Mozilla/5.0"}

# Initialize bot
bot = Bot(token=BOT_TOKEN)

# Database setup
def init_db():
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        authenticated BOOLEAN DEFAULT FALSE
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS keys (
        key TEXT PRIMARY KEY,
        expiry_date TEXT
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS products (
        name TEXT PRIMARY KEY,
        status TEXT
    )
    """)
    conn.commit()
    conn.close()

init_db()

# Generate keys (admin only)
async def generate_key(update: Update, context: CallbackContext):
    if str(update.effective_user.id) not in ADMIN_IDS:
        await update.message.reply_text("❌ You are not authorized.")
        return

    args = context.args
    if len(args) != 1 or not args[0].isdigit():
        await update.message.reply_text("Usage: /generate_key <days>")
        return

    days = int(args[0])
    expiry_date = (datetime.datetime.now() + datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    key = f"KEY-{datetime.datetime.now().timestamp()}"

    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO keys (key, expiry_date) VALUES (?, ?)", (key, expiry_date))
    conn.commit()
    conn.close()

    await update.message.reply_text(f"✅ Generated Key: `{key}` (Valid for {days} days)", parse_mode="Markdown")

# Authenticate user with a key
async def authenticate(update: Update, context: CallbackContext):
    args = context.args
    if len(args) != 1:
        await update.message.reply_text("Usage: /auth <key>")
        return

    key = args[0]
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute("SELECT expiry_date FROM keys WHERE key = ?", (key,))
    row = cursor.fetchone()

    if row:
        expiry_date = datetime.datetime.strptime(row[0], "%Y-%m-%d")
        if expiry_date >= datetime.datetime.now():
            cursor.execute("INSERT OR IGNORE INTO users (user_id, authenticated) VALUES (?, TRUE)", (update.effective_user.id,))
            conn.commit()
            await update.message.reply_text("✅ تم تفعيل اشتراكك وستتلقى اشعارات بحالة المنتجات")
        else:
            await update.message.reply_text("❌ انتهت صلاحية المفتاح")
    else:
        await update.message.reply_text("❌ مفتاح غير صالح")

    conn.close()

# Fetch current product statuses as a dictionary
def fetch_products_status_dict():
    response = requests.get(URL, headers=HEADERS)
    if response.status_code != 200:
        return {}
    
    soup = BeautifulSoup(response.text, "html.parser")
    products = soup.find_all("div", class_="relative bg-white px-2.5 pb-3 pt-6")

    status_dict = {}
    for product in products:
        name_tag = product.find("span", attrs={"title": True})
        product_name = name_tag["title"].strip() if name_tag else "Unknown Product"
        sold_out = product.find("span", class_="bg-custom-orange-700")
        limited = product.find("span", class_="bg-custom-fuchsia-800")
        status = "❌ غير متوفر" if sold_out else "⚠️ متوفر لفترة محدودة" if limited else "✅ متوفر"
        status_dict[product_name] = status
    return status_dict

# Format product statuses for display
def format_products_message():
    statuses = fetch_products_status_dict()
    if not statuses:
        return "⚠️ Failed to fetch the website."
    
    message_lines = []
    for product_name, status in statuses.items():
        message_lines.append(f"📦 {product_name} - {status}")
    return "\n".join(message_lines)

# List products (only for authenticated users)
async def list_products(update: Update, context: CallbackContext):
    query = update.callback_query
    user_id = query.from_user.id

    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute("SELECT authenticated FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()

    if not row or not row[0]:
        await query.message.reply_text("❌ انت غير مشترك")
        return

    message = "🛍️ *النكهات المتوفرة:*\n\n" + format_products_message()
    await query.message.reply_text(message, parse_mode="Markdown")

async def HowToSub(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.message.reply_text("قم بادخال المفتاح بالصيغة الاتية\n /auth KEY-XXXXXXXXXX.XXXXX \n \n لا تملك اشتراك؟ \n تواصل مع @Dooood97")

# Button handler (only one button now)
async def button_handler(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()  # Acknowledge button press

    if query.data == "list_products":
        await list_products(update, context)
    if query.data == "HowTo":
        await HowToSub(update, context)

# Start command (menu with one button)
async def start(update: Update, context: CallbackContext):
    keyboard = [
        [InlineKeyboardButton("طريقة الأشتراك؟", callback_data="HowTo")],
        [InlineKeyboardButton("📜 عرض المنتجات", callback_data="list_products")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("🔹 مرحبًا! اختر خيارًا:", reply_markup=reply_markup)

# Background job: Check stock every minute and notify changes
async def check_stock_update(context: CallbackContext):
    new_statuses = fetch_products_status_dict()
    if not new_statuses:
        return

    changes = []
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    for product_name, new_status in new_statuses.items():
        cursor.execute("SELECT status FROM products WHERE name = ?", (product_name,))
        row = cursor.fetchone()
        if row is None:
            cursor.execute("INSERT INTO products (name, status) VALUES (?, ?)", (product_name, new_status))
            changes.append(f"📦 {product_name} - تم إضافته بحالة: {new_status}")
        else:
            old_status = row[0]
            if new_status != old_status:
                changes.append(f"📦 {product_name} - تغيرت الحالة من {old_status} إلى {new_status}")
                cursor.execute("UPDATE products SET status = ? WHERE name = ?", (new_status, product_name))
    conn.commit()
    conn.close()

    # Notify all authenticated users if there are any changes
    if changes:
        message = "🔄 *تحديث المخزون:*\n" + "\n".join(changes)
        conn = sqlite3.connect("bot_data.db")
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users WHERE authenticated = TRUE")
        users = cursor.fetchall()
        conn.close()
        for (user_id,) in users:
            try:
                await context.bot.send_message(chat_id=user_id, text=message, parse_mode="Markdown")
            except Exception as e:
                print(f"Failed to send message to {user_id}: {e}")

# Main function
app = Application.builder().token(BOT_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("generate_key", generate_key))
app.add_handler(CommandHandler("auth", authenticate))
app.add_handler(CallbackQueryHandler(button_handler))

# Schedule the stock checking job to run every minute (first run after 10 seconds)
job_queue = app.job_queue
job_queue.run_repeating(check_stock_update, interval=60, first=10)
print("DONEEEEEEEEEEEEEEEEEEEEEEEE")
app.run_polling()
