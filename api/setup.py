"""Разовая настройка: сказать Telegram, куда слать сообщения."""
import json
import logging
import os
import sys
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.basicConfig(level=logging.INFO)

from bot.serverless import run, secret, setup_webhook


def answer(request, code: int, payload: dict) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode()
    request.send_response(code)
    request.send_header("Content-Type", "application/json; charset=utf-8")
    request.send_header("Content-Length", str(len(data)))
    request.end_headers()
    request.wfile.write(data)


def query(request) -> dict:
    return {k: v[0] for k, v in parse_qs(urlparse(request.path).query).items()}



class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        expected = secret("CRON_SECRET")
        params = query(self)
        if expected and params.get("key") != expected:
            answer(self, 403, {"ok": False, "error": "forbidden"})
            return
        base = params.get("url") or os.getenv("VERCEL_PROJECT_PRODUCTION_URL") or self.headers.get("Host", "")
        if not base.startswith("http"):
            base = f"https://{base}"
        try:
            answer(self, 200, run(setup_webhook(base)))
        except Exception as e:
            logging.exception("Не удалось настроить webhook")
            answer(self, 500, {"ok": False, "error": str(e)})

    def log_message(self, *args):
        pass
