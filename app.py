import os
import asyncio
import collections
import shutil
import uuid
import subprocess
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telegram.request import HTTPXRequest

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID")) if os.getenv("ADMIN_ID") else None

approved_users = set([ADMIN_ID]) if ADMIN_ID else set()
all_users = set() 
extraction_queue = collections.deque()

bot_state = {
    "is_cancelled": False,
    "is_processing": False
}

# --- Anti-Ban: Auto Delete Message Task ---
async def auto_delete_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, delay: int = 600):
    """10 minute (600 seconds) baad Telegram se message automatically delete kar dega"""
    await asyncio.sleep(delay)
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass # Agar user ne pehle hi delete kar diya ho toh ignore karo

def format_mega_url(url: str) -> str:
    url = url.strip()
    if '/folder/' in url and '#' in url:
        parts = url.split('/folder/')[1].split('#')
        return f"https://mega.nz/#F!{parts[0]}!{parts[1]}"
    return url

async def run_cmd(cmd):
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = await proc.communicate()
    return stdout.decode().strip(), stderr.decode().strip()

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    msg = " ".join(context.args)
    if not msg: return
    for user_id in all_users:
        try: await context.bot.send_message(chat_id=user_id, text=f"📢 {msg}")
        except: continue

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in approved_users: return
    if not bot_state["is_processing"]: return
    bot_state["is_cancelled"] = True 
    await update.message.reply_text("🛑 Process ko rokne ki request bhej di gayi hai...")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    all_users.add(user_id)
    
    # STEALTH MODE: Agar user approved nahi hai, toh chup raho. Reply mat karo.
    if user_id not in approved_users:
        # Admin ko alert bhej sakte hain silently
        if ADMIN_ID:
            keyboard = [[InlineKeyboardButton("✅ Approve", callback_data=f"approve_{user_id}")]]
            await context.bot.send_message(ADMIN_ID, f"👤 Naya User Aaya: {update.effective_user.first_name}\nID: `{user_id}`", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    # Sirf approved users ko reply jayega
    sent_msg = await update.message.reply_text(f"👋 Hello {update.effective_user.first_name}! Send MEGA link.")
    # Welcome message ko bhi 2 min baad delete kar do safai ke liye
    asyncio.create_task(auto_delete_message(context, user_id, sent_msg.message_id, 120))

async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if update.effective_user.id != ADMIN_ID: return
    data = query.data
    if data.startswith("approve_"):
        user_id = int(data.split("_")[1])
        approved_users.add(user_id)
        await query.edit_message_text(text=f"✅ User `{user_id}` approved!")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # STEALTH MODE: Unapproved messages ko silently ignore karo
    if user_id not in approved_users: return
    
    text = update.message.text.strip()
    if "mega.nz" in text:
        extraction_queue.append((user_id, text))
        if not bot_state["is_processing"]: 
            asyncio.create_task(process_queue(context)) 
        else: 
            msg = await update.message.reply_text(f"⏳ Queue position: {len(extraction_queue)}.")
            asyncio.create_task(auto_delete_message(context, user_id, msg.message_id, 30))
    else:
        msg = await update.message.reply_text("⚠️ Valid MEGA link bhejo.")
        asyncio.create_task(auto_delete_message(context, user_id, msg.message_id, 30))

async def process_queue(context: ContextTypes.DEFAULT_TYPE):
    bot_state["is_processing"] = True
    
    while extraction_queue:
        bot_state["is_cancelled"] = False
        user_id, mega_link = extraction_queue.popleft()
        
        try:
            status_msg = await context.bot.send_message(chat_id=user_id, text="🔍 Link scan kar raha hoon...")
            clean_url = format_mega_url(mega_link)
            
            # --- PHASE 1: FOLDER SCAN (One-by-One Method for 90GB+ Support) ---
            stdout, stderr = await run_cmd(["megals", clean_url])
            files = [f for f in stdout.split('\n') if f.strip()]
            
            if not files:
                # Agar direct file link hai
                files = [clean_url]
            
            await status_msg.edit_text(f"✅ {len(files)} items found. Downloading and Uploading one by one...")

            for i, file_path in enumerate(files, 1):
                if bot_state["is_cancelled"]: break
                
                await status_msg.edit_text(f"⬇️ Downloading ({i}/{len(files)})...")
                task_id = str(uuid.uuid4())
                download_dir = f"./downloads/{task_id}"
                os.makedirs(download_dir, exist_ok=True)
                
                # Download single file
                await run_cmd(["megatools", "dl", "--path", download_dir, file_path])
                
                downloaded_files = os.listdir(download_dir)
                if not downloaded_files:
                    shutil.rmtree(download_dir, ignore_errors=True)
                    continue
                    
                actual_file_name = downloaded_files[0]
                full_file_path = os.path.join(download_dir, actual_file_name)
                file_size_mb = os.path.getsize(full_file_path) / (1024 * 1024)
                
                # --- PHASE 2: UPLOAD & AUTO-DELETE ---
                await status_msg.edit_text(f"📤 Uploading: `{actual_file_name}`\n(Auto-delete in 10 mins)")
                
                # Split logic for > 50MB files to bypass Telegram limits
                if file_size_mb > 49.5:
                    with open(full_file_path, 'rb') as f:
                        part_num = 1
                        while True:
                            if bot_state["is_cancelled"]: break
                            chunk = f.read(49 * 1024 * 1024)
                            if not chunk: break
                            
                            part_name = f"{full_file_path}.{part_num:03d}"
                            with open(part_name, 'wb') as p: p.write(chunk)
                            
                            try:
                                sent_doc = await context.bot.send_document(chat_id=user_id, document=open(part_name, 'rb'), read_timeout=180, write_timeout=180)
                                # 10 MINUTE AUTO-DELETE
                                asyncio.create_task(auto_delete_message(context, user_id, sent_doc.message_id, 600))
                            except Exception as e:
                                pass
                                
                            os.remove(part_name)
                            part_num += 1
                else:
                    # Upload standard file (<50MB)
                    try:
                        sent_doc = await context.bot.send_document(chat_id=user_id, document=open(full_file_path, 'rb'), read_timeout=180, write_timeout=180)
                        # 10 MINUTE AUTO-DELETE
                        asyncio.create_task(auto_delete_message(context, user_id, sent_doc.message_id, 600))
                    except Exception as e:
                        pass
                
                # Server se file turant delete taaki storage full na ho
                shutil.rmtree(download_dir, ignore_errors=True)
                await asyncio.sleep(1) # Prevent flood waits

            if bot_state["is_cancelled"]:
                await status_msg.edit_text("🛑 Process Cancelled.")
            else:
                await status_msg.edit_text("✅ All files processed!\n(Files will automatically disappear from chat in 10 mins for safety)")
            
            # Status message ko bhi 1 minute baad uda do
            asyncio.create_task(auto_delete_message(context, user_id, status_msg.message_id, 60))
                
        except Exception as e:
            pass # Stealth mode: Don't spam errors
            
    bot_state["is_processing"] = False 

def main():
    request = HTTPXRequest(connect_timeout=60.0, read_timeout=300.0, write_timeout=300.0)
    application = Application.builder().token(BOT_TOKEN).request(request).concurrent_updates(True).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(CommandHandler("broadcast", broadcast))
    application.add_handler(CallbackQueryHandler(button_click))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("Bot is running in STEALTH & AUTO-DELETE Mode...")
    application.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
    
