#!/usr/bin/env bash
# Установка бота на чистый сервер Ubuntu одной командой.
# Запуск: curl -fsSL https://raw.githubusercontent.com/<логин>/<репозиторий>/main/deploy/install.sh | sudo bash -s <логин>/<репозиторий>
set -euo pipefail

REPO="${1:-}"
DIR=/opt/tutor-bot
if [ -z "$REPO" ]; then
  echo "Укажите репозиторий: ... | sudo bash -s логин/репозиторий"; exit 1
fi

# На дешёвых серверах памяти мало (512 МБ), поэтому сначала добавляем файл подкачки
if ! swapon --show | grep -q . && [ ! -f /swapfile ]; then
  echo "==> Добавляю файл подкачки"
  fallocate -l 1G /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=1024 status=none
  chmod 600 /swapfile
  mkswap -q /swapfile
  swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo "/swapfile none swap sw 0 0" >>/etc/fstab
fi

echo "==> Устанавливаю Python и git"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git ca-certificates curl python3 python3-venv >/dev/null

echo "==> Скачиваю бота"
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" pull -q
else
  git clone -q "https://github.com/$REPO.git" "$DIR"
fi
cd "$DIR"
mkdir -p data

echo "==> Ставлю библиотеки"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

if [ ! -f .env ]; then
  echo
  read -rp "Вставьте токен бота от @BotFather: " TOKEN </dev/tty
  read -rp "Ваш Telegram ID от @userinfobot (можно оставить пустым): " ADMIN </dev/tty
  cp .env.example .env
  sed -i "s|^BOT_TOKEN=.*|BOT_TOKEN=$TOKEN|" .env
  sed -i "s|^ADMIN_IDS=.*|ADMIN_IDS=$ADMIN|" .env
  chmod 600 .env
fi

echo "==> Запускаю как службу"
cp deploy/tutor-bot.service /etc/systemd/system/tutor-bot.service
systemctl daemon-reload
systemctl enable -q --now tutor-bot
systemctl restart tutor-bot
sleep 3
systemctl is-active --quiet tutor-bot && echo "Бот работает." || { echo "Бот не запустился, журнал:"; journalctl -u tutor-bot -n 20 --no-pager; exit 1; }
echo
echo "Готово! Откройте своего бота в Telegram и нажмите «Начать»."
echo "Обновить бота потом: cd $DIR && git pull && .venv/bin/pip install -q -r requirements.txt && systemctl restart tutor-bot"
echo "Посмотреть журнал: journalctl -u tutor-bot -f -n 50"
