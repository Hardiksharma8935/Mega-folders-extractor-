import os
import asyncio
import collections
from mega import Mega
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID")) if os.getenv("ADMIN_ID") else None

approved_users = set([ADMIN_ID]) if ADMIN_ID else set()
pending_approvals = {}
extraction_queue = collections.deque()
is_processing = False

mega = Mega()
m = mega.login()

async def process_queue(context: ContextTypes.DEFAULT_TYPE):
    global is_processing
    if is_processing or not extraction_queue:
        return

    is_processing = True
    user_id, mega_link, update = extraction_queue.popleft()
    await update.message.reply_text("⏳ Aapka number aa gaya hai! Extraction start ho raha hai...")
    await extract_mega_folder(mega_link, update, context)
    is_processing = False
    asyncio.create_task(process_queue(context))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id in approved_users:
        await update.message.reply_text(f"👋 Hello {user.first_name}! Main taiyar hoon. Mujhe MEGA folder ka link bhejo.")
    else:
        pending_approvals[user.id] = update
        await update.message.reply_text("🔒 Aapko is bot ko use karne ki permission nahi hai. Admin ko approval request bhej di gayi hai.")
        if ADMIN_ID:
            await context.bot.send_message(chat_id=ADMIN_ID, text=f"👤 **New User Request:**\nName: {user.first_name}\nID: `{user.id}`\n\nApprove: `/approve {user.id}`")

async def approve_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        user_id = int(context.args[0])
        approved_users.add(user_id)
        await update.message.reply_text(f"✅ User {user_id} approved.")
        if user_id in pending_approvals:
            await context.bot.send_message(chat_id=user_id, text="🎉 Admin ne approve kar diya!")
            del pending_approvals[user_id]
    except:
        await update.message.reply_text("❌ Format: `/approve USER_ID`")

async def handle_message(update: Update, update_context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in approved_users: return
    text = update.message.text
    if "mega.nz" in text:
        extraction_queue.append((user_id, text, update))
        if is_processing or len(extraction_queue) > 1:
            await update.message.reply_text(f"⏳ Queue Position #{len(extraction_queue) - 1}")
        else:
            asyncio.create_task(process_queue(update_context))
    else:
        await update.message.reply_text("⚠️ Valid MEGA link bhejo.")

async def extract_mega_folder(url: str, update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_msg = await update.message.reply_text("🔍 Files check ho rahi hain...")
    try:
        loop = asyncio.get_running_loop()
        files = await loop.run_in_executor(None, m.get_files_from_link, url)
        media_exts = ('.mp4', '.mkv', '.avi', '.mov', '.mp3', '.jpg', '.jpeg', '.png')
        media_files = [f for f in files.values() if str(f['name']).lower().endswith(media_exts)]
        
        if not media_files:
            await status_msg.edit_text("❌ No media files found.")
            return

        await status_msg.edit_text(f"📊 Total Files: {len(media_files)}. Extracting...")
        for index, file in enumerate(media_files, start=1):
            try:
                file_path = await loop.run_in_executor(None, m.download_file_by_link, url, file)
                if os.path.exists(file_path):
                    with open(file_path, 'rb') as doc:
                        await update.message.reply_document(document=doc, filename=file['name'])
                    os.remove(file_path)
            except: continue
        await update.message.reply_text("🏁 Complete!")
    except Exception as e:
        await status_msg.edit_text(f"❌ Error: {str(e)}")

def main():
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("approve", approve_user))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Bot is alive on Render...")
    application.run_polling()

if __name__ == '__main__':
    main()
