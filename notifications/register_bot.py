"""Run from the deployed worker directory, using the operator's Wrangler login."""
import argparse
import getpass
import json
import re
import secrets
import shutil
import subprocess
from urllib.error import URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("provider", choices=("telegram", "max"))
    parser.add_argument("origin", help="Public workers.dev origin")
    args = parser.parse_args()
    origin = args.origin.rstrip("/")
    if not re.fullmatch(r"https://article-downloader-notify\.[a-z0-9-]+\.workers\.dev", origin):
        parser.error("Use the deployed article-downloader-notify workers.dev address")
    npx = shutil.which("npx.cmd") or shutil.which("npx")
    if not npx:
        parser.error("Install Node.js and npm first")
    token = getpass.getpass("Bot token: ").strip()
    if not token or any(c.isspace() for c in token):
        parser.error("Empty or invalid token")
    if args.provider == "telegram" and not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token):
        parser.error("Invalid Telegram bot token")
    secret = secrets.token_urlsafe(32)
    prefix = args.provider.upper()
    keys = {prefix + "_TOKEN": token, prefix + "_WEBHOOK_SECRET": secret}
    subprocess.run([npx, "wrangler@4", "secret", "bulk"], input=json.dumps(keys), text=True, check=True)
    headers = {"Content-Type": "application/json"}
    data = {"url": origin + "/hook/" + args.provider}
    if args.provider == "telegram":
        endpoint = "https://api.telegram.org/bot" + token + "/setWebhook"
        data.update(secret_token=secret, allowed_updates=["message"])
    else:
        endpoint = "https://platform-api2.max.ru/subscriptions"
        headers["Authorization"] = token
        data.update(secret=secret, update_types=["message_created", "bot_started"])
    try:
        req = Request(endpoint, headers=headers, data=json.dumps(data).encode())
        with build_opener(NoRedirect()).open(req, timeout=20) as resp:
            result = json.load(resp)
        if not result.get("ok" if args.provider == "telegram" else "success"):
            raise ValueError()
    except (URLError, ValueError, OSError):
        parser.exit(1, "Webhook registration failed. Check the bot credentials and retry.\n")
    print("Webhook registered. Open the bot privately and send /start.")


if __name__ == "__main__":
    main()
