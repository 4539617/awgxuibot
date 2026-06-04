# Unified VPN Bot

Объединенный Telegram бот для комплексного управления VPN инфраструктурой.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Docker](https://img.shields.io/badge/docker-%230db7ed.svg?style=flat&logo=docker&logoColor=white)](https://docker.com)

## 🎯 Возможности

### 🛡️ NetCrazyBot (AmneziaWG Management)
- Управление AmneziaWG серверами (v1 и v2)
- Автоматическая установка AWG контейнеров
- Генерация клиентских AWG конфигураций
- Статистика и мониторинг серверов
- DNS резолвинг доменов через множество DNS серверов
- Генерация routing файлов для Keenetic
- Управление портами и клиентами
- Админ-панель с детальной статистикой

### 👥 XUIBot (3x-ui Panel Management)
- Управление пользователями и системой доступа
- Создание VLESS ключей (постоянных и временных)
- Временные ключи с выбором срока (1ч, 1д, 3д, 7д, 30д)
- QR-коды для быстрого подключения
- Статистика трафика по каждому ключу
- Автоматическая очистка просроченных ключей
- Автодобавление пользователей с активными ключами
- Система блокировки и антифлуд защита
- Поддержка множества транспортов (TCP, xHTTP)
- Поддержка TLS и Reality безопасности

## 🚀 Быстрая Установка

### Автоматическая установка одной командой:
```bash
bash <(curl -fsSL https://raw.githubusercontent.com/your-repo/main/install.sh)
```

### Ручная установка:
```bash
# Клонирование репозитория
git clone https://github.com/your-repo/unified-vpn-bot.git
cd unified-vpn-bot

# Настройка конфигурации
cp .env.example .env
nano .env  # Заполните все необходимые параметры

# Запуск установщика
chmod +x install.sh
sudo ./install.sh
```

## 📋 Интерактивное Меню Установщика

После запуска `install.sh` вы увидите меню:

```
========================================
   Выберите действие:
========================================
1) Установить NetCrazyBot (AWG Management)
2) Установить XUIBot (3x-ui Management)
3) Установить оба бота
4) Показать статус
5) Показать логи
0) Выход
========================================
```

### Варианты установки:
- **Вариант 1**: Установить только NetCrazyBot для управления AWG
- **Вариант 2**: Установить только XUIBot для управления пользователями
- **Вариант 3**: Установить оба бота для полного функционала

## 🔧 Конфигурация

Все настройки находятся в файле `.env`. Пример конфигурации в `.env.example`.

### Обязательные параметры для NetCrazyBot:
```bash
TELEGRAM_BOT_TOKEN=your_netcrazy_bot_token_here
ADMIN_IDS=123456789
```

### Обязательные параметры для XUIBot:
```bash
BOT_TOKEN=your_xuibot_token_here
XUI_URL=https://localhost:12345/your-path
XUI_PASSWORD=your_password_here
SERVER_ADDRESS=your-domain.com
```

### Дополнительные настройки:
- **Transport**: `tcp`, `xhttp`
- **Security**: `tls`, `reality`
- **Limits**: Настройка лимитов трафика и времени
- **Logging**: Конфигурация логирования

## 📋 Команды Управления

### Просмотр логов:
```bash
# Логи NetCrazyBot
docker logs -f netcrazybot

# Логи XUIBot
docker logs -f xuibot

# Последние 50 строк
docker logs --tail=50 netcrazybot
docker logs --tail=50 xuibot
```

### Управление контейнерами:
```bash
# Перезапуск
docker restart netcrazybot
docker restart xuibot

# Остановка
docker stop netcrazybot xuibot

# Запуск
docker start netcrazybot xuibot

# Остановка всех сервисов
docker compose down

# Запуск всех сервисов
docker compose up -d

# Пересборка и запуск
docker compose up -d --build
```

### Проверка статуса:
```bash
# Статус контейнеров
docker ps --filter name=netcrazybot --filter name=xuibot

# Использование ресурсов
docker stats netcrazybot xuibot
```

## 🎮 Команды Telegram Ботов

### NetCrazyBot (AWG):
| Команда | Описание |
|---------|----------|
| `/start` | Начало работы |
| `/admin` | Панель администратора |

### XUIBot (3x-ui):
| Команда | Описание |
|---------|----------|
| `/start` | Начало работы и проверка доступа |
| `/new` | Создать постоянный ключ |
| `/tempkey` | Создать временный ключ |
| `/myclients` | Список ваших ключей |
| `/allclients` | Все ключи (только админ) |
| `/users` | Управление пользователями (только админ) |
| `/help` | Помощь |

## 🔄 Обновление

```bash
cd /opt/unified-vpn-bot  # или ваша директория
git pull
docker compose down
docker compose up -d --build
```

## 🐛 Решение Проблем

### Проблема: Контейнер не запускается
```bash
# Проверьте логи
docker logs netcrazybot
docker logs xuibot

# Проверьте конфигурацию
cat .env

# Пересоберите образы
docker compose build --no-cache
docker compose up -d
```

### Проблема: Бот не отвечает
```bash
# Проверьте статус
docker ps

# Перезапустите контейнер
docker restart netcrazybot  # или xuibot

# Проверьте логи на ошибки
docker logs --tail=100 netcrazybot
```

### Проблема: Ошибка доступа к 3x-ui
```bash
# Проверьте доступность панели
curl -k https://your-panel-url

# Проверьте права на БД
ls -la /etc/x-ui/x-ui.db

# Проверьте network_mode в docker-compose.yml
```

## 📊 Мониторинг

### Использование диска:
```bash
# Анализ дискового пространства
sudo ./disk_analyzer.sh
```

### Логи:
- NetCrazyBot: `./output/logs/`
- XUIBot: `./logs/`

### Резервное копирование:
```bash
# Бэкап конфигурации
cp .env .env.backup

# Бэкап базы данных XUIBot
cp ./data/bot_users.db ./data/bot_users.db.backup

# Бэкап AWG конфигов
tar -czf awg_backup.tar.gz ./output/
```

## 🏗️ Архитектура

```
┌─────────────────────────────────────────────────────────┐
│                    Telegram Bot API                      │
└────────────┬────────────────────────────┬────────────────┘
             │                            │
    ┌────────▼────────┐          ┌───────▼────────┐
    │  NetCrazyBot    │          │    XUIBot      │
    │   (Node.js)     │          │   (Python)     │
    │                 │          │                │
    │  Port: -        │          │  Port: host    │
    └────────┬────────┘          └───────┬────────┘
             │                            │
    ┌────────▼────────┐          ┌───────▼────────┐
    │  AWG Containers │          │  3x-ui Panel   │
    │  (amnezia-awg)  │          │  + SQLite DB   │
    └─────────────────┘          └────────────────┘
```

## 🤝 Вклад в Проект

Мы приветствуем вклад в развитие проекта! 

1. Fork репозитория
2. Создайте feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit изменения (`git commit -m 'Add some AmazingFeature'`)
4. Push в branch (`git push origin feature/AmazingFeature`)
5. Откройте Pull Request

## 📞 Поддержка

- **Issues**: [GitHub Issues](https://github.com/your-repo/issues)
- **Документация**: [Wiki](https://github.com/your-repo/wiki)
- **Telegram**: @your_support_channel

## 📝 Лицензия

MIT License - см. файл [LICENSE](LICENSE)

## 🙏 Благодарности

- [NetCrazyBot](https://github.com/4539617/netcrazebot) - Оригинальный AWG бот
- [XUIBot](https://github.com/4539617/xuibot) - Оригинальный 3x-ui бот
- [AmneziaVPN](https://github.com/amnezia-vpn) - AmneziaWG протокол
- [3x-ui](https://github.com/MHSanaei/3x-ui) - 3x-ui панель

---

**Made with ❤️ by Bob**
