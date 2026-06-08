# Unified Bot

Объединенный Telegram бот для управления сервисами.

## 🚀 Установка


```bash
git clone https://github.com/4539617/awgxuibot.git /opt/awgxuibot
cd /opt/awgxuibot
bash install.sh
```

## 🔄 Перезапуск

```bash
docker restart netcrazybot xuibot
```

или

```bash
docker compose restart
```

## 📋 Логи

```bash
docker logs netcrazybot
docker logs xuibot
```

## 🛑 Остановка

```bash
docker stop netcrazybot xuibot
```

## ▶️ Запуск

```bash
docker start netcrazybot xuibot
```

## ▶️ Перезапуск контейнера xuibot

```bash
docker compose down xuibot
docker compose build --no-cache xuibot
docker compose up -d xuibot
docker logs -f xuibot
```

## ▶️ Перезапуск контейнера awgbot

```bash
docker compose down awgbot
docker compose build --no-cache awgbot
docker compose up -d awgbot
docker logs -f awgbot
```