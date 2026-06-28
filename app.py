import os
import asyncio
import collections
import shutil
import uuid
import subprocess
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.request import HTTPXRequest

BOT_TOKEN = os.getenv("BOT_TOKEN")
extraction_queue = collections.deque()
bot_state = {"is_processing": False}

async def run_cmd(cmd):
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = await proc.communicate()
    return stdout.decode().strip(), stderr.decode().strip()

async def process_queue(context):
    bot_state["is_processing"] = True
    while extraction_queue:
        user_id, link = extraction_queue.popleft()
        msg = await context.bot.send_message(user_id, "🔍 Scanning folder...")
        
        # 1. Folder ki list nikalo
        stdout, _ = await run_cmd(["megals", link])
        files = [f for f in stdout.split('\n') if f.strip()]
        
        await msg.edit_text(f"✅ {len(files)} files found. Starting download...")
        
        for i, file_path in enumerate(files, 1):
            temp_dir = f"./temp_{uuid.uuid4().hex}"
            os.makedirs(temp_dir, exist_ok=True)
            
            await msg.edit_text(f"⬇️ Downloading ({i}/{len(files)}):\n{os.path.basename(file_path)}")
            await run_cmd(["megatools", "dl", "--path", temp_dir, file_path])
            
            # File mil gayi
            file_name = os.listdir(temp_dir)[0]
            path = os.path.join(temp_dir, file_name)
            
            # 2. Upload / Split Logic
            await msg.edit_text(f"📤 Sending: {file_name}")
            if os.path.getsize(path) > 49 * 1024 * 1024:
                # Video split
                if file_name.lower().endswith(('.mp4', '.mkv')):
                    out_path = os.path.join(temp_dir, "split")
                    os.makedirs(out_path, exist_ok=True)
                    await run_cmd(["ffmpeg", "-i", path, "-c", "copy", "-map", "0", "-segment_time", "90", "-f", "segment", f"{out_path}/p%03d.mp4"])
                    for p in sorted(os.listdir(out_path)):
                        await context.bot.send_video(user_id, open(f"{out_path}/{p}", 'rb'))
                else:
                    # Generic split
                    with open(path, 'rb') as f:
                        part = 1
                        while chunk := f.read(49 * 1024 * 1024):
                            p_name = f"{path}.part{part}"
                            with open(p_name, 'wb') as out: out.write(chunk)
                            await context.bot.send_document(user_id, open(p_name, 'rb'))
                            os.remove(p_name); part += 1
            else:
                if file_name.lower().endswith(('.mp4', '.mkv')):
                    await context.bot.send_video(user_id, open(path, 'rb'))
                else:
                    await context.bot.send_document(user_id, open(path, 'rb'))
            
            shutil.rmtree(temp_dir)
        await context.bot.send_message(user_id, "🎉 Task Finished!")
    bot_state["is_processing"] = False

async def handle_message(update, context):
    link = update.message.text
    extraction_queue.append((update.effective_user.id, link))
    if not bot_state["is_processing"]:
        asyncio.create_task(process_queue(context))
    await update.message.reply_text("⏳ Added to queue.")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT, handle_message))
    app.run_polling()

if __name__ == '__main__':
    main()
    
