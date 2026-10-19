"""Сюда Telegram присылает каждое сообщение боту."""
import html
import json
import logging
import os
import sys
import urllib.request
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


def chat_of(payload: dict):
    """Куда писать, если обработка упала: из обычного сообщения или из нажатия кнопки."""
    for holder in (payload.get("message"), payload.get("edited_message"),
                   (payload.get("callback_query") or {}).get("message")):
        chat_id = ((holder or {}).get("chat") or {}).get("id")
        if chat_id:
            return chat_id
    return None


def tell_user_it_broke(payload: dict, error: BaseException) -> None:
    """Молчащий бот хуже сломанного: пусть человек увидит, что запрос не прошёл.

    Шлём напрямую через HTTP, без aiogram и без базы, потому что упасть могли как раз они.
    """
    chat_id = chat_of(payload)
    token = secret("BOT_TOKEN")
    if not chat_id or not token:
        return
    note = f"{type(error).__name__}: {error}"[:300]
    body = json.dumps({
        "chat_id": chat_id,
        "text": "Что-то сломалось на моей стороне, сообщение не обработалось.\n\n"
                f"<code>{html.escape(note)}</code>\n\nПокажите это разработчику.",
        "parse_mode": "HTML",
    }).encode()
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=body, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(request, timeout=10).read()
    except Exception:
        logging.exception("Не удалось даже сообщить об ошибке")



class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        expected = secret("WEBHOOK_SECRET")
        if expected and self.headers.get("X-Telegram-Bot-Api-Secret-Token") != expected:
            answer(self, 403, {"ok": False, "error": "forbidden"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        payload = {}
        try:
            payload = json.loads(raw) if raw else {}
            run(handle_update(payload))
        except Exception as error:
            # Telegram всё равно получает ok: иначе он будет слать это же сообщение снова и снова
            logging.exception("Не удалось обработать сообщение")
            tell_user_it_broke(payload, error)
        answer(self, 200, {"ok": True})

    def do_GET(self):
        answer(self, 200, {"ok": True, "hint": "сюда пишет только Telegram"})

    def log_message(self, *args):
        pass
