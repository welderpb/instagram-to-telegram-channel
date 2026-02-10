import os
import shutil
import logging
import sys
from telegram import Update, InputMediaPhoto, InputMediaVideo
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters
import instaloader
from dotenv import load_dotenv

# --- CONFIGURATION ---
# Load environment variables from a .env file if it exists
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")

# Safety check to ensure variables are loaded
if not BOT_TOKEN or not CHANNEL_ID:
    print("❌ Error: BOT_TOKEN or CHANNEL_ID not found in environment variables.")
    print("Please set them in your terminal or a .env file.")
    sys.exit(1)

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Hello! Send me an Instagram link, and I will repost it to the channel."
    )

async def handle_instagram_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    user_first_name = update.effective_user.first_name
    
    if "instagram.com" not in url:
        await update.message.reply_text("❌ That doesn't look like an Instagram link.")
        return

    status_msg = await update.message.reply_text("⏳ Downloading content...")
    
    # Create temp directory using message ID
    download_folder = f"temp_{update.message.message_id}"
    
    try:
        # --- DOWNLOAD LOGIC ---
        L = instaloader.Instaloader(
            download_pictures=True,
            download_videos=True, 
            download_video_thumbnails=False,
            download_geotags=False, 
            download_comments=False,
            save_metadata=False,
            compress_json=False
        )

        # Extract Shortcode
        shortcode = None
        if "/reel/" in url:
            shortcode = url.split("/reel/")[1].split("/")[0].split("?")[0]
        elif "/p/" in url:
            shortcode = url.split("/p/")[1].split("/")[0].split("?")[0]
        
        if not shortcode:
            await status_msg.edit_text("❌ Could not parse Instagram shortcode.")
            return

        post = instaloader.Post.from_shortcode(L.context, shortcode)
        L.download_post(post, target=download_folder)

        # --- UPLOAD LOGIC ---
        await status_msg.edit_text("📤 Uploading to channel...")

        media_files = []
        for filename in sorted(os.listdir(download_folder)):
            filepath = os.path.join(download_folder, filename)
            if filename.endswith(".jpg"):
                media_files.append({"type": "photo", "path": filepath})
            elif filename.endswith(".mp4"):
                media_files.append({"type": "video", "path": filepath})

        caption = f"📱 <b>New Post from Instagram</b>\n\nShared by: {user_first_name}\n🔗 <a href='{url}'>Original Link</a>"

        if not media_files:
            await status_msg.edit_text("❌ No media found to upload.")
            return

        if len(media_files) == 1:
            file = media_files[0]
            with open(file["path"], 'rb') as f:
                if file["type"] == "photo":
                    await context.bot.send_photo(chat_id=CHANNEL_ID, photo=f, caption=caption, parse_mode='HTML')
                else:
                    await context.bot.send_video(chat_id=CHANNEL_ID, video=f, caption=caption, parse_mode='HTML')
        else:
            # Carousel handling
            media_group = []
            for index, file in enumerate(media_files):
                with open(file["path"], 'rb') as f:
                    file_content = f.read() 
                media_caption = caption if index == 0 else None
                
                if file["type"] == "photo":
                    media_group.append(InputMediaPhoto(media=file_content, caption=media_caption, parse_mode='HTML'))
                else:
                    media_group.append(InputMediaVideo(media=file_content, caption=media_caption, parse_mode='HTML'))
            
            await context.bot.send_media_group(chat_id=CHANNEL_ID, media=media_group)

        await status_msg.edit_text("✅ Reposted!")

    except Exception as e:
        logging.error(f"Error: {e}")
        await status_msg.edit_text(f"❌ Error: {str(e)}")
    
    finally:
        if os.path.exists(download_folder):
            shutil.rmtree(download_folder)

if __name__ == '__main__':
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    
    application.add_handler(CommandHandler('start', start))
    instagram_filter = filters.TEXT & ~filters.COMMAND & filters.Regex(r"instagram\.com")
    application.add_handler(MessageHandler(instagram_filter, handle_instagram_link))
    
    print(f"Bot started. Forwarding to channel: {CHANNEL_ID}")
    application.run_polling()
