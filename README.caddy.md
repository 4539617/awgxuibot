# Caddy + StreamVault — HTTPS реверс-прокси для Cascade

Caddy стоит перед Cascade и обеспечивает:
- **HTTPS + HTTP/3 (QUIC)** на порту 443
- **Сайт-заглушка StreamVault** на `/` — выглядит как личный видеосервер
- **Скрытый путь** `/ADMIN_PATH/` для доступа к Cascade UI
- **Заголовки безопасности** (HSTS, no-referrer, X-Frame-Options, …)
- **TLS-сертификат** для голого IP через acme.sh (shortlived, 6 дней, автообновление)

## Быстрый старт

### 1. Убедиться, что Cascade запущен

```bash
docker compose -f docker-compose.cascade.yml up -d
docker compose -f docker-compose.cascade.yml ps
```

### 2. Настроить окружение

```bash
cp caddy/.env.example caddy/.env
nano caddy/.env
# Установить ADMIN_PATH — длинная случайная строка
# Сгенерировать: openssl rand -hex 12
# CASCADE_PORT=51821 (должен совпадать с PORT в docker-compose.cascade.yml)
```

### 3. Выдать TLS-сертификат (первый запуск)

```bash
# Порт 80 должен быть доступен из интернета во время выдачи сертификата.
# Скрипт использует acme.sh standalone mode — Caddy ещё не запущен, это нормально.
sudo bash caddy/scripts/acme-install.sh 85.204.18.253 your@email.com
```

Скрипт автоматически:
1. Устанавливает acme.sh
2. Выдаёт shortlived-сертификат (6 дней) для IP
3. Устанавливает сертификат в `/etc/ssl/cascade/`
4. Запускает Caddy (`docker compose -f docker-compose.caddy.yml up -d`)
5. Переключает acme.sh на webroot-режим для последующих обновлений

### 4. Если сертификат уже есть — просто запустить Caddy

```bash
docker compose -f docker-compose.caddy.yml up -d --build
```

### 5. Добавить видео-заглушку (опционально, но желательно)

```bash
# Вариант A — скачать Big Buck Bunny (royalty-free, ~60MB)
curl -L "https://download.blender.org/demo/movies/BBB/bbb_sunflower_1080p_30fps_normal.mp4" \
     -o caddy/www/video/decoy.mp4

# Вариант B — сгенерировать через ffmpeg (noise clip, ~5MB)
ffmpeg -f lavfi -i color=c=black:s=1280x720:r=25 \
       -f lavfi -i anoisesrc=r=44100 \
       -t 60 -c:v libx264 -c:a aac \
       caddy/www/video/decoy.mp4
```

Без видеофайла сайт работает — в hero-блоке отображается canvas-шум + спиннер буферизации.

### 6. Закрыть прямой доступ к Cascade

После запуска Caddy весь трафик должен идти через него.
Прямой доступ к Cascade (порт 51821) нужно заблокировать:

```bash
iptables-nft -A INPUT ! -i lo -p tcp --dport 51821 -j DROP
```

## Доступ

| URL | Что открывается |
|-----|-----------------|
| `https://85.204.18.253/` | Сайт-заглушка StreamVault |
| `https://85.204.18.253/<ADMIN_PATH>/` | Cascade Web UI |
| `http://85.204.18.253/` | Редирект на HTTPS |

## Структура файлов

```
caddy/
├── Caddyfile              # Конфиг Caddy (читается из контейнера)
├── Dockerfile             # FROM caddy:alpine
├── .env.example           # Шаблон окружения (ADMIN_PATH, CASCADE_PORT)
├── .env                   # Реальный конфиг (НЕ в git, скопировать из .env.example)
├── scripts/
│   └── acme-install.sh    # Скрипт первоначальной выдачи TLS-сертификата
└── www/
    ├── index.html         # Сайт-заглушка StreamVault
    ├── 404.html           # Кастомная страница 404
    └── video/
        ├── README.txt     # Инструкция по добавлению видео
        └── decoy.mp4      # Видеофайл (НЕ в git, добавить вручную)

docker-compose.caddy.yml   # Docker Compose для Caddy
```

## Обновление сертификата

acme.sh устанавливает cronjob автоматически.
После каждого обновления выполняется `docker restart cascade-caddy` (задаётся в `--reloadcmd`).

Проверить:
```bash
crontab -l | grep acme
~/.acme.sh/acme.sh --list
```

## Безопасность

- `ADMIN_PATH` — это security through obscurity. Дополнительно включите TOTP 2FA в Cascade: Settings → Users
- `Referrer-Policy: no-referrer` — скрытый путь не утекает через заголовок Referer
- Каскад-порт (51821) **не должен** быть доступен из интернета (см. шаг 6)
- TLS-сертификат обновляется автоматически каждые 5 дней через acme.sh cron
