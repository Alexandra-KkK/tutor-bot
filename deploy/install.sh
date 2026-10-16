#!/usr/bin/env bash
# Установка бота на чистый сервер Ubuntu одной командой.
# Запуск: curl -fsSL https://raw.githubusercontent.com/<логин>/<репозиторий>/main/deploy/install.sh | sudo bash -s <логин>/<репозиторий>
set -euo pipefail

REPO="${1:-}"
DIR=/opt/tutor-bot
if [ -z "$REPO" ]; then
  echo "Укажите репозиторий: ... | sudo bash -s логин/репозиторий"; exit 1
fi

echo "==> Устанавливаю Docker и git"
apt-get update -qq
apt-get install -y -qq git ca-certificates curl >/dev/null
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh >/dev/null
fi

echo "==> Скачиваю бота"
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" pull -q
else
  git clone -q "https://github.com/$REPO.git" "$DIR"
fi
cd "$DIR"

if [ ! -f .env ]; then
  echo
  read -rp "Вставьте токен бота от @BotFather: " TOKEN </dev/tty
  read -rp "Ваш Telegram ID от @userinfobot (можно оставить пустым): " ADMIN </dev/tty
  cp .env.example .env
  sed -i "s|^BOT_TOKEN=.*|BOT_TOKEN=$TOKEN|" .env
  sed -i "s|^ADMIN_IDS=.*|ADMIN_IDS=$ADMIN|" .env
  chmod 600 .env
fi

echo "==> Запускаю"
docker compose up -d --build
echo
echo "Готово! Откройте своего бота в Telegram и нажмите «Начать»."
echo "Обновить бота потом: cd $DIR && git pull && docker compose up -d --build"
echo "Посмотреть журнал: cd $DIR && docker compose logs -f --tail 50"
