"""Сюда Telegram присылает каждое сообщение боту."""
import json
import logging
import os
import sys
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.basicConfig(level=logging.INFO)

from bot.serverless import handle_update, run, secret


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
    def do_POST(self):
        expected = secret("WEBHOOK_SECRET")
        if expected and self.headers.get("X-Telegram-Bot-Api-Secret-Token") != expected:
            answer(self, 403, {"ok": False, "error": "forbidden"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            run(handle_update(json.loads(raw) if raw else {}))
        except Exception:
            # Telegram всё равно получает ok: иначе он будет слать это же сообщение снова и снова
            logging.exception("Не удалось обработать сообщение")
        answer(self, 200, {"ok": True})

    def do_GET(self):
        answer(self, 200, {"ok": True, "hint": "сюда пишет только Telegram"})

    def log_message(self, *args):
        pass
