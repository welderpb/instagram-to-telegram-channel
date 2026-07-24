import os
import shutil
import logging
import sys
import asyncio
import traceback
from telegram import Update, InputMediaPhoto, InputMediaVideo
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters
import instaloader
from instaloader import Profile
from dotenv import load_dotenv

# --- CONFIGURATION ---
# Load environment variables from a .env file if it exists
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
IG_USERNAME = os.getenv("IG_USERNAME")
ALLOWED_USER_IDS_RAW = os.getenv("ALLOWED_USER_IDS", "")

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

logger = logging.getLogger(__name__)

# Safety check to ensure variables are loaded
if not BOT_TOKEN or not CHANNEL_ID:
    logger.error(f"❌ Error: BOT_TOKEN or CHANNEL_ID not found in environment variables.")
    logger.error(f"Please set them in your terminal or a .env file.")
    sys.exit(1)

# Parse allowed IDs into a set of integers for fast lookup
try:
    ALLOWED_IDS = {int(x.strip()) for x in ALLOWED_USER_IDS_RAW.split(",") if x.strip()}
except ValueError:
    logger.error("❌ Error: ALLOWED_USER_IDS must contain only numbers separated by commas.")
    sys.exit(1)

# --- INSTALOADER METADATA PATCH ---
# instaloader 4.15.2 (currently the latest PyPI release) fetches post metadata via
# doc_id "8845758582119845", which Instagram deprecated in mid-July 2026: it now
# answers {"data": null}, so Post.from_shortcode raises
# BadResponseException("Fetching Post metadata failed.") for every post/reel.
#
# The upstream fix (PR #2706, not yet released) switches to the PolarisPostRootQuery
# doc_id, sends an X-CSRFToken header, and normalizes the returned v1/iPhone-format
# payload into the legacy GraphQL node shape the rest of instaloader expects. We
# monkeypatch that fix in here rather than installing an unreleased third-party fork,
# to keep the dependency pinned (see CLAUDE.md) and avoid running untrusted code
# alongside the session cookies. Remove this block once a fixed instaloader ships.
import json
import urllib.parse
from instaloader.instaloadercontext import InstaloaderContext, copy_session
from instaloader.structures import Post
from instaloader.exceptions import (
    BadResponseException,
    ConnectionException,
    PostChangedException,
)


def _patched_doc_id_graphql_query(self, doc_id, variables, referer=None):
    csrf = next((c.value for c in self._session.cookies
                 if c.name == 'csrftoken' and c.value), None)
    if not csrf:
        self._session.get('https://www.instagram.com/', timeout=self.request_timeout)
        csrf = next((c.value for c in self._session.cookies
                     if c.name == 'csrftoken' and c.value), '')

    with copy_session(self._session, self.request_timeout) as tmpsession:
        tmpsession.headers.update(self._default_http_header(empty_session_only=True))
        del tmpsession.headers['Connection']
        del tmpsession.headers['Content-Length']
        tmpsession.headers['authority'] = 'www.instagram.com'
        tmpsession.headers['scheme'] = 'https'
        tmpsession.headers['accept'] = '*/*'
        tmpsession.headers['x-csrftoken'] = csrf
        if referer is not None:
            tmpsession.headers['referer'] = urllib.parse.quote(referer)

        variables_json = json.dumps(variables, separators=(',', ':'))

        resp_json = self.get_json('graphql/query',
                                  params={'variables': variables_json,
                                          'doc_id': doc_id,
                                          'server_timestamps': 'true'},
                                  session=tmpsession,
                                  use_post=True)
    if 'status' not in resp_json:
        self.error("GraphQL response did not contain a \"status\" field.")
    return resp_json


def _fetch_play_count_from_clips(context, user_id, shortcode):
    """Fetch play_count for a reel via the clips connection endpoint as a fallback."""
    try:
        resp = context.doc_id_graphql_query(
            "27234427476213202",
            {"data": {"include_feed_video": True, "page_size": 12,
                      "target_user_id": str(user_id)}},
        )
        edges = ((resp.get("data") or {})
                 .get("xdt_api__v1__clips__user__connection_v2") or {})
        for edge in edges.get("edges") or []:
            media = (edge.get("node") or {}).get("media") or {}
            if media.get("code") == shortcode:
                return media.get("play_count")
    except (ConnectionException, BadResponseException):
        pass
    return None


def _normalize_post_data(media, context):
    """Normalize a Polaris media item to a legacy-compatible node."""
    media_types = {1: "GraphImage", 2: "GraphVideo", 8: "GraphSidecar"}
    media_type = media.get("media_type")
    typename = media_types.get(media_type)
    if not typename:
        raise BadResponseException(f"Unknown media_type in metadata: {media_type}.")
    pic_json = media.copy()
    pic_json["shortcode"] = media["code"]
    pic_json["id"] = media["pk"]
    pic_json["__typename"] = typename
    pic_json["is_video"] = media_type == 2
    pic_json["taken_at_timestamp"] = media["taken_at"]
    pic_json["owner"] = {
        "id": media["user"]["pk"],
        "username": media["user"].get("username", ""),
        "full_name": media["user"].get("full_name", ""),
    }
    candidates = (media.get("image_versions2") or {}).get("candidates") or []
    pic_json["display_url"] = candidates[0]["url"] if candidates else None
    video_versions = media.get("video_versions") or []
    pic_json["video_url"] = video_versions[0]["url"] if video_versions else None
    pic_json["video_duration"] = media.get("video_duration")
    pic_json["video_view_count"] = media.get("view_count")
    pic_json["video_play_count"] = media.get("play_count")
    if media_type == 2 and pic_json["video_view_count"] is None:
        pic_json["video_play_count"] = _fetch_play_count_from_clips(
            context, media["user"]["pk"], media["code"]
        )
    caption = media.get("caption")
    caption_text = caption.get("text") if isinstance(caption, dict) else None
    pic_json["edge_media_to_caption"] = (
        {"edges": [{"node": {"text": caption_text}}]} if caption_text is not None
        else {"edges": []}
    )
    pic_json["edge_media_preview_like"] = {"count": media.get("like_count") or 0}
    pic_json["edge_media_to_parent_comment"] = {
        "count": media.get("comment_count") or 0,
        "edges": [],
    }
    if media.get("has_liked") is not None:
        pic_json["viewer_has_liked"] = media["has_liked"]
    carousel = media.get("carousel_media") or []
    if carousel:
        carousel_nodes = []
        for item in carousel:
            item_type = item.get("media_type", 1)
            node = {
                "shortcode": item.get("code", ""),
                "__typename": media_types.get(item_type, "GraphImage"),
                "is_video": item_type == 2,
            }
            item_candidates = (item.get("image_versions2") or {}).get("candidates") or []
            node["display_url"] = item_candidates[0]["url"] if item_candidates else ""
            item_videos = item.get("video_versions") or []
            node["video_url"] = item_videos[0]["url"] if item_videos else None
            if item.get("accessibility_caption") is not None:
                node["accessibility_caption"] = item["accessibility_caption"]
            carousel_nodes.append({"node": node})
        pic_json["edge_sidecar_to_children"] = {"edges": carousel_nodes}
    tagged = (media.get("usertags") or {}).get("in") or []
    if tagged:
        pic_json["edge_media_to_tagged_user"] = {
            "edges": [
                {"node": {"user": {"username": t["user"]["username"].lower()}}}
                for t in tagged
                if (t.get("user") or {}).get("username")
            ]
        }
    return pic_json


def _patched_obtain_metadata(self):
    if not self._full_metadata_dict:
        resp = self._context.doc_id_graphql_query(
            "27128499623469141",
            {
                "shortcode": self.shortcode,
                "__relay_internal__pv__PolarisAIGMMediaWebLabelEnabledrelayprovider": False,
            },
        )
        web_info = (resp.get("data") or {}).get("xdt_api__v1__media__shortcode__web_info") or {}
        items = web_info.get("items")
        if not items:
            raise BadResponseException("Fetching Post metadata failed.")
        self._full_metadata_dict = _normalize_post_data(items[0], self._context)
        if self.shortcode != self._full_metadata_dict['shortcode']:
            self._node.update(self._full_metadata_dict)
            raise PostChangedException


InstaloaderContext.doc_id_graphql_query = _patched_doc_id_graphql_query
Post._obtain_metadata = _patched_obtain_metadata
logger.info("Applied instaloader metadata patch (upstream PR #2706).")
# --- END INSTALOADER METADATA PATCH ---


# Shared Instaloader instance, created once at startup and reused across requests.
# Reusing one instance preserves its RateController history so downloads are
# spaced out sensibly, instead of hammering Instagram with a fresh session on
# every message (which increases the chance of being soft-blocked).
L: instaloader.Instaloader = None
# Guards the shared instance in case update handling ever becomes concurrent.
loader_lock = asyncio.Lock()


def build_loader() -> instaloader.Instaloader:
    """Create the Instaloader instance and load the session file once."""
    loader = instaloader.Instaloader(
        max_connection_attempts=3,
        download_pictures=True,
        download_videos=True,
        download_video_thumbnails=False,
        download_geotags=False,
        download_comments=False,
        save_metadata=False,
        compress_json=False
    )

    if IG_USERNAME:
        session_file = f"session-{IG_USERNAME}"
        if os.path.exists(session_file):
            try:
                logger.info(f"Attempting to load session from {session_file}...")
                loader.load_session_from_file(IG_USERNAME, filename=session_file)
                logger.info("✅ Session loaded successfully!")
            except Exception as e:
                logger.error(f"⚠️ Failed to load session: {e}")
        else:
            logger.warning(f"⚠️ Session file '{session_file}' not found. Running anonymously (risky).")

    try:
        test_username = loader.test_login()
        if test_username != IG_USERNAME:
            logger.info('Session expired or invalid. Please renew session file.')
        else:
            logger.info(f'Session is active for {IG_USERNAME}.')
    except Exception as e:
        logger.error(f"Session might be expired or invalid. Error: {e}")

    return loader


async def check_auth(update: Update):
    """Checks if the user is authorized."""
    user_id = update.effective_user.id
    if user_id not in ALLOWED_IDS:
        logger.warning(f"⛔ Unauthorized access attempt from ID: {user_id} ({update.effective_user.first_name})")
        await update.message.reply_text(f"⛔ You are not authorized to use this bot.\nYour ID: `{user_id}`", parse_mode='Markdown')
        return False
    return True

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update):
        return
    await update.message.reply_text(
        "👋 Hello! Send me an Instagram link, and I will repost it to the channel."
    )

async def handle_instagram_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Authorization Check
    if not await check_auth(update):
        return

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
        # Extract Shortcode
        shortcode = None
        if "/reel/" in url:
            shortcode = url.split("/reel/")[1].split("/")[0].split("?")[0]
        elif "/p/" in url:
            shortcode = url.split("/p/")[1].split("/")[0].split("?")[0]

        if not shortcode:
            await status_msg.edit_text("❌ Could not parse Instagram shortcode.")
            return

        # Serialize access to the shared Instaloader instance.
        async with loader_lock:
            post = instaloader.Post.from_shortcode(L.context, shortcode)
            logger.info("Downloading post...")
            L.download_post(post, target=download_folder)

        # --- CAPTION HANDLING ---
        original_caption = post.caption if post.caption else ""

        # Telegram Caption Limit is 1024 chars. We reserve ~100 chars for the footer.
        max_caption_length = 900
        if len(original_caption) > max_caption_length:
            original_caption = original_caption[:max_caption_length] + "..."

        # Construct final caption with original text + link
        final_caption = (
            f"{original_caption}\n\n"
            f"🔗 <a href='{url}'>Original Link</a>"
        )

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
                    await context.bot.send_photo(chat_id=CHANNEL_ID, photo=f, caption=final_caption, parse_mode='HTML')
                else:
                    await context.bot.send_video(chat_id=CHANNEL_ID, video=f, caption=final_caption, parse_mode='HTML')
        else:
            # Carousel handling
            media_group = []
            for index, file in enumerate(media_files):
                with open(file["path"], 'rb') as f:
                    file_content = f.read() 

                # Only attach caption to the first item
                media_caption = final_caption if index == 0 else None
                
                if file["type"] == "photo":
                    media_group.append(InputMediaPhoto(media=file_content, caption=media_caption, parse_mode='HTML'))
                else:
                    media_group.append(InputMediaVideo(media=file_content, caption=media_caption, parse_mode='HTML'))
            
            await context.bot.send_media_group(chat_id=CHANNEL_ID, media=media_group)

        await status_msg.edit_text("✅ Reposted!")

    except Exception as e:
        logger.error(f"Error: {e}\n{traceback.format_exc()}")
        await status_msg.edit_text(f"❌ Error: {str(e)}")
    
    finally:
        if os.path.exists(download_folder):
            shutil.rmtree(download_folder)

if __name__ == '__main__':
    # Build the shared Instaloader instance once, before polling starts.
    L = build_loader()

    application = ApplicationBuilder().token(BOT_TOKEN).build()
    
    application.add_handler(CommandHandler('start', start))
    instagram_filter = filters.TEXT & ~filters.COMMAND & filters.Regex(r"instagram\.com")
    application.add_handler(MessageHandler(instagram_filter, handle_instagram_link))
    
    logger.info(f"Bot started. Forwarding to channel: {CHANNEL_ID}")
    if IG_USERNAME:
        logger.info(f"configured to use Instagram account: {IG_USERNAME}")
    logger.info(f"Allowed User IDs: {ALLOWED_IDS}")

    application.run_polling()
