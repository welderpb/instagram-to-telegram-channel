"""Create an instaloader session file from a logged-in Firefox profile.

Usage: python tools/firefox_session.py <path to cookies.sqlite>
Username comes from IG_USERNAME (env or .env). Writes ./session-<IG_USERNAME>.
Skips test_login(), which is broken in instaloader 4.16 (instaloader #2792).
"""
import os
import shutil
import sqlite3
import sys
import tempfile

import instaloader
from dotenv import load_dotenv

load_dotenv()
user = os.getenv("IG_USERNAME")
if len(sys.argv) != 2 or not user:
    sys.exit(__doc__)

with tempfile.TemporaryDirectory() as tmp:
    db = os.path.join(tmp, "cookies.sqlite")
    shutil.copy(sys.argv[1], db)  # Firefox keeps the DB locked while running
    con = sqlite3.connect(db)
    cookies = dict(con.execute(
        "select name, value from moz_cookies where host like '%instagram.com'"))
    con.close()
if "sessionid" not in cookies:
    sys.exit("No sessionid cookie: log in to Instagram in this Firefox profile first.")

L = instaloader.Instaloader()
L.context._session.cookies.update(cookies)
L.context.username = user
filename = f"session-{user}"
L.save_session_to_file(filename)
os.chmod(filename, 0o600)
print(f"Saved {filename}")
