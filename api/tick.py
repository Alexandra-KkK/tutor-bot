"""Будильник: раз в минуту сюда стучится внешний сервис, и бот рассылает напоминания."""
import json
import logging
import os
import sys
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.basicConfig(level=logging.INFO)

from bot.serverless import run, run_tick, secret


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
    def _go(self):
        expected = secret("CRON_SECRET")
        if expected and query(self).get("key") != expected:
            answer(self, 403, {"ok": False, "error": "forbidden"})
            return
        try:
            run(run_tick())
        except Exception:
            logging.exception("Ошибка в проверке напоминаний")
            answer(self, 500, {"ok": False})
            return
        answer(self, 200, {"ok": True})

    def do_GET(self):
        self._go()

    def do_POST(self):
        self._go()

    def log_message(self, *args):
        pass
