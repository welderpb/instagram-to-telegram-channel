# Instagram to Telegram Repost Bot

A Gemini generated Python Telegram bot that automatically downloads Instagram Posts, Reels, and Carousels (Albums) and reposts them to a specified Telegram Channel.


## ✨ Features

* **Universal Support:** Handles single Images, Videos (Reels), and Carousels (Mixed Albums).
* **Auto-Repost:** Automatically forwards content to your configured Telegram Channel.
* **Caption Preservation:** Keeps the original Instagram caption (truncated to fit Telegram limits) and adds a link back to the source.
* **User Authorization:** Restricts bot usage to specific Telegram User IDs (security).
* **Instagram Login Support:** Uses session files to download content (avoids some 429/401 errors).
* **Dockerized:** Ready-to-run Dockerfile and GitHub Actions workflow included.

## 🛠 Prerequisites

1.  **Telegram Bot Token:** Get one from [@BotFather](https://t.me/BotFather).
2.  **Telegram Channel ID:** The username (e.g., `@mychannel`) or ID of the channel where posts will be sent. **The bot must be an Admin in this channel.**
3.  **Python 3.10+** (if running locally).

## 🚀 Installation & Setup

### 1. Clone the repository
```bash
git clone https://github.com/welderpb/instagram-to-telegram-channel.git
cd instagram-to-telegram-channel
```

### 2. Install dependencies

```bash
pip install -r requirements.txt

```

### 3. Configure Environment Variables

Create a file named `.env` in the root directory and fill in your details:

```env
BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz
CHANNEL_ID=@your_channel_username
ALLOWED_USER_IDS=123456789,987654321
IG_USERNAME=your_instagram_username

```

| Variable | Description |
| --- | --- |
| `BOT_TOKEN` | Your Telegram Bot API Token. |
| `CHANNEL_ID` | Channel Username (`@channel`) or ID (`-100...`). |
| `ALLOWED_USER_IDS` | Comma-separated list of Telegram User IDs allowed to use the bot. |
| `IG_USERNAME` | Your Instagram username (required for session login). |

---

## 🔑 Instagram Login (Session Setup)

To download content reliably (and avoid "Login Required" errors), you **must** create a session file locally.

1. Create a temporary script named `login_setup.py`:
```python
import instaloader

# Replace with your username
USERNAME = "your_instagram_username"

L = instaloader.Instaloader()
try:
    L.interactive_login(USERNAME) 
    L.save_session_to_file()
except Exception as e:
    print(e)

```


2. Run it: `python login_setup.py`
3. Enter your password when prompted.
4. This generates a file named `session-yourusername`. **Keep this file safe!**

---

## ▶️ Running the Bot

### Option A: Run Locally

Ensure your `.env` file and session file are in the project folder.

```bash
python insta_bot.py

```

### Option B: Run with Docker

1. **Build the image:**
```bash
docker build -t insta-bot .

```


2. **Run the container:**
*Map the session file into the container so the bot can use your login.*
```bash
docker run -d \
  --name insta-bot \
  --env-file .env \
  -v $(pwd)/session-yourusername:/app/session-yourusername \
  insta-bot

```



---

## ⚠️ Disclaimer & Limitations

* **Cloud Hosting:** Instagram aggressively blocks data center IP addresses (AWS, DigitalOcean, Heroku, etc.). This bot works best when hosted on a **residential IP** (e.g., a local Raspberry Pi or home server).
* **Terms of Service:** This tool is for educational purposes. Downloading content from Instagram may violate their Terms of Service. Use responsibly.

```

```
