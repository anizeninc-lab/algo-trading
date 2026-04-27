# get_token.py
# Run this every morning before starting the trading system.
# It opens a browser to log in to Upstox and saves your access token to .env
#
# Usage:
#   py -m uv run python get_token.py

import os
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests
from dotenv import load_dotenv, set_key

load_dotenv()

API_KEY = os.getenv("UPSTOX_API_KEY", "")
API_SECRET = os.getenv("UPSTOX_API_SECRET", "")
REDIRECT_URI = os.getenv("UPSTOX_REDIRECT_URI", "http://127.0.0.1:8080/callback")

auth_code = None


class CallbackHandler(BaseHTTPRequestHandler):
    """Listens for the OAuth callback and extracts the auth code."""

    def do_GET(self):
        global auth_code
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if "code" in params:
            auth_code = params["code"][0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(
                b"""
                <html><body style='font-family:sans-serif;text-align:center;padding:40px'>
                <h2 style='color:green'>Login successful!</h2>
                <p>Access token saved. You can close this tab.</p>
                </body></html>
            """
            )
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Missing auth code")

    def log_message(self, format, *args):
        pass  # Suppress server logs


def get_access_token(code: str) -> str:
    """Exchange auth code for access token."""
    url = "https://api.upstox.com/v2/login/authorization/token"
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    payload = {
        "code": code,
        "client_id": API_KEY,
        "client_secret": API_SECRET,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    }
    response = requests.post(url, headers=headers, data=payload)
    data = response.json()

    if "access_token" in data:
        return data["access_token"]
    else:
        raise RuntimeError(f"Token exchange failed: {data}")


def main():
    if not API_KEY or not API_SECRET:
        print("ERROR: UPSTOX_API_KEY or UPSTOX_API_SECRET not set in .env")
        return

    # Build the Upstox login URL
    auth_url = (
        f"https://api.upstox.com/v2/login/authorization/dialog"
        f"?client_id={API_KEY}"
        f"&redirect_uri={urllib.parse.quote(REDIRECT_URI)}"
        f"&response_type=code"
    )

    print("Opening Upstox login in your browser...")
    print("Log in with your Upstox credentials.")
    print("Waiting for callback on http://127.0.0.1:8080/callback ...")
    print("")

    # Open browser
    webbrowser.open(auth_url)

    # Start local server to catch the callback
    server = HTTPServer(("127.0.0.1", 8080), CallbackHandler)
    server.handle_request()  # Handle one request then stop

    if not auth_code:
        print("ERROR: No auth code received.")
        return

    print("Auth code received. Exchanging for access token...")

    token = get_access_token(auth_code)

    # Save token to .env
    set_key(".env", "UPSTOX_ACCESS_TOKEN", token)
    print("")
    print("Access token saved to .env successfully.")
    print("You are ready to run the trading system.")
    print("")
    print("Run this next:")
    print("  py -m uv run python main.py")


if __name__ == "__main__":
    main()
