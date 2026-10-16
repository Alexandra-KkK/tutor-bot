#!/bin/bash
# Запуск бота на macOS: двойной щелчок по этому файлу.
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null; then
  echo "Сначала установите Python с python.org"; read -r; exit 1
fi
[ -d .venv ] || python3 -m venv .venv
source .venv/bin/activate
echo "Устанавливаю нужные библиотеки..."
python -m pip install -q -r requirements.txt
echo
echo "Бот запускается. Не закрывайте это окно, пока хотите, чтобы бот работал."
python run.py
