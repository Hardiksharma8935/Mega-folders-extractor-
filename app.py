import os
import asyncio
import collections
import shutil
import uuid
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telegram.request import HTTPXRequest

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID")) if os.getenv("ADMIN_ID") else None

approved_users = set([ADMIN_ID]) if ADMIN_ID else set()
all_users = set() 
extraction_queue = collections.deque()

bot_state = {"process": None, "is_cancelled": False, "is_processing": False}

def format_mega_url(url: str) -> str:
    url = url.strip()
    if '/folder/' in url and '#' in url:
        parts = url.split('/folder/')[1].split('#')
        return f"https://mega.nz/#F!{parts[0]}!{parts[1]}"
    return url

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_state["is_cancelled"] = True 
    if bot_state["process"]:
        try: bot_state["process"].kill()
        except: pass
    await update.message.reply_text("🛑 Cancel signal sent.")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    all_users.add(user.id)
    if user.id in approved_users:
        await update.message.reply_text("👋 Link bhejo, main ready hoon.")
    else:
        await update.message.reply_text("🔒 Not approved.")
        if ADMIN_ID:
            keyboard = [[InlineKeyboardButton("✅ Approve", callback_data=f"approve_{user.id}")]]
            await context.bot.send_message(ADMIN_ID, f"User: {user.id}", reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in approved_users: return
    text = update.message.text.strip()
    if "mega.nz" in text:
        extraction_queue.append((update.effective_user.id, text, update))
        if not bot_state["is_processing"]: 
            asyncio.create_task(process_queue(context))
        else:
            await update.message.reply_text("⏳ Queue mein add kar diya gaya hai.")

async def process_queue(context: ContextTypes.DEFAULT_TYPE):
    bot_state["is_processing"] = True
    while extraction_queue:
        user_id, mega_link, update = extraction_queue.popleft()
        try:
            status_msg = await context.bot.send_message(user_id, "⏳ Starting...")
            task_id = str(uuid.uuid4())
            download_dir = f"./downloads/{task_id}"
            os.makedirs(download_dir, exist_ok=True)
            
            # Download
            proc = await asyncio.create_subprocess_exec("megatools", "dl", "--path", download_dir, format_mega_url(mega_link))
            bot_state["process"] = proc
            await proc.communicate()
            
            if proc.returncode == 0:
                for root, _, files in os.walk(download_dir):
                    for file in files:
                        if bot_state["is_cancelled"]: break
                        path = os.path.join(root, file)
                        size = os.path.getsize(path) / (1024 * 1024)
                        
                        if size > 49:
                            part_num = 1
                            with open(path, 'rb') as f:
                                while chunk := f.read(49 * 1024 * 1024):
                                    part = f"{path}.{part_num:03d}"
                                    with open(part, 'wb') as p: p.write(chunk)
                                    await context.bot.send_document(user_id, open(part, 'rb'), read_timeout=300, write_timeout=300)
                                    os.remove(part); part_num += 1
                        else:
                            await context.bot.send_document(user_id, open(path, 'rb'), read_timeout=300, write_timeout=300)
            else:
                await context.bot.send_message(user_id, "❌ Download failed.")
        except Exception as e:
            await context.bot.send_message(user_id, f"Error: {str(e)}")
        finally:
            shutil.rmtree(download_dir, ignore_errors=True)
    bot_state["is_processing"] = False

def main():
    while True: # Infinite loop for 24x7 uptime
        try:
            print("Bot starting...")
            request = HTTPXRequest(connect_timeout=60, read_timeout=300, write_timeout=300)
            app = Application.builder().token(BOT_TOKEN).request(request).build()
            
            app.add_handler(CommandHandler("start", start))
            app.add_handler(CommandHandler("cancel", cancel))
            app.add_handler(MessageHandler(filters.TEXT, handle_message))
            app.add_handler(CallbackQueryHandler(lambda u, c: (approved_users.add(int(u.callback_query.data.split('_')[1])), u.callback_query.edit_message_text("Approved!"))))
            
            app.run_polling()
        except Exception as e:
            print(f"Restarting in 5s due to: {e}")
            asyncio.sleep(5)

if __name__ == '__main__':
    main()
    
