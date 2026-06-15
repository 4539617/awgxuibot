#!/bin/bash

# ============================================
# Script: disk_analyzer.sh
# Description: Анализ дискового пространства и рекомендации по очистке
# Author: System Admin
# Version: 1.0
# ============================================

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color
BOLD='\033[1m'

# Конфигурация
THRESHOLD_PERCENT=80  # Порог тревоги в процентах
LOG_FILE="/var/log/disk_cleanup.log"
REPORT_FILE="/tmp/disk_report_$(date +%Y%m%d_%H%M%S).txt"

# Создаем директорию для логов если нет
mkdir -p "$(dirname $LOG_FILE)"

# Функция логирования
log() {
    echo -e "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

# Функция вывода заголовка
print_header() {
    echo -e "\n${BLUE}${BOLD}═══════════════════════════════════════════════════════════${NC}"
    echo -e "${CYAN}${BOLD}  $1${NC}"
    echo -e "${BLUE}${BOLD}═══════════════════════════════════════════════════════════${NC}\n"
}

# Функция вывода предупреждения
print_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

# Функция вывода успеха
print_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

# Функция вывода ошибки
print_error() {
    echo -e "${RED}❌ $1${NC}"
}

# Функция вывода информации
print_info() {
    echo -e "${CYAN}ℹ️  $1${NC}"
}

# Функция форматирования размера
format_size() {
    local size=$1
    if [ $size -ge 1073741824 ]; then
        echo "$(echo "scale=2; $size/1073741824" | bc) GB"
    elif [ $size -ge 1048576 ]; then
        echo "$(echo "scale=2; $size/1048576" | bc) MB"
    elif [ $size -ge 1024 ]; then
        echo "$(echo "scale=2; $size/1024" | bc) KB"
    else
        echo "${size} B"
    fi
}

# Функция анализа диска
analyze_disk() {
    print_header "АНАЛИЗ ДИСКОВОГО ПРОСТРАНСТВА"
    
    # Общая информация
    echo -e "${BOLD}📊 Общая статистика:${NC}"
    df -h / | awk 'NR==2 {printf "   ├─ Всего: %s\n   ├─ Использовано: %s (%.1f%%)\n   └─ Свободно: %s\n", $2, $3, $5, $4}'
    
    USED_PERCENT=$(df / | awk 'NR==2 {print $5}' | sed 's/%//')
    if [ $USED_PERCENT -gt $THRESHOLD_PERCENT ]; then
        echo -e "\n${RED}${BOLD}⚠️  ВНИМАНИЕ! Диск заполнен на ${USED_PERCENT}% (порог: ${THRESHOLD_PERCENT}%)${NC}\n"
    fi
    
    # Топ-10 самых больших папок в корне
    echo -e "\n${BOLD}📁 Топ-10 самых больших папок в корне:${NC}"
    du -sh /* 2>/dev/null | sort -hr | head -10 | nl -w2 -s'. '
    
    # Топ-20 самых больших файлов
    echo -e "\n${BOLD}📄 Топ-20 самых больших файлов (>100MB):${NC}"
    find / -type f -size +100M 2>/dev/null -exec du -h {} \; | sort -hr | head -20 | nl -w2 -s'. '
    
    # Анализ inode
    echo -e "\n${BOLD}🔢 Использование inode:${NC}"
    df -i / | awk 'NR==2 {printf "   ├─ Всего: %s\n   ├─ Использовано: %s (%.1f%%)\n   └─ Свободно: %s\n", $2, $3, $5, $4}'
    
    # Удаленные но открытые файлы
    echo -e "\n${BOLD}🗑️  Удаленные но открытые файлы (освободятся после перезапуска процессов):${NC}"
    DELETED_FILES=$(lsof +L1 2>/dev/null | grep -v "^COMMAND" | awk '{print $1, $2, $7, $9}' | head -10)
    if [ -n "$DELETED_FILES" ]; then
        echo "$DELETED_FILES" | nl -w2 -s'. '
        TOTAL_SIZE=$(lsof +L1 2>/dev/null | awk '{sum+=$7} END {print sum}')
        echo -e "   ${YELLOW}Общий размер: $(format_size $TOTAL_SIZE)${NC}"
    else
        echo "   Нет удаленных открытых файлов"
    fi
}

# Функция анализа Docker
analyze_docker() {
    print_header "АНАЛИЗ DOCKER"
    
    if ! command -v docker &> /dev/null; then
        print_warning "Docker не установлен"
        return
    fi
    
    # Проверка доступности Docker
    if ! docker info &>/dev/null; then
        print_error "Docker демон не запущен"
        return
    fi
    
    # Общая статистика Docker
    echo -e "${BOLD}🐳 Статистика Docker:${NC}"
    docker system df | tail -n +2 | while read line; do
        echo "   $line"
    done
    
    # Размер директории Docker
    DOCKER_SIZE=$(du -sh /var/lib/docker 2>/dev/null | cut -f1)
    echo -e "\n   📁 Размер директории Docker: ${DOCKER_SIZE}"
    
    # Неиспользуемые образы
    echo -e "\n${BOLD}📦 Неиспользуемые образы (>100MB):${NC}"
    docker images --filter "dangling=true" --format "table {{.Repository}}\t{{.Tag}}\t{{.Size}}" | tail -n +2 | head -10
    
    # Остановленные контейнеры
    STOPPED_CONTAINERS=$(docker ps -a --filter "status=exited" --format "table {{.Names}}\t{{.Image}}\t{{.Size}}" | tail -n +2)
    if [ -n "$STOPPED_CONTAINERS" ]; then
        echo -e "\n${BOLD}🛑 Остановленные контейнеры:${NC}"
        echo "$STOPPED_CONTAINERS" | nl -w2 -s'. '
    fi
    
    # Размер логов контейнеров
    echo -e "\n${BOLD}📋 Логи контейнеров:${NC}"
    for container in $(docker ps -aq 2>/dev/null); do
        LOG_PATH=$(docker inspect --format='{{.LogPath}}' $container 2>/dev/null)
        if [ -f "$LOG_PATH" ]; then
            LOG_SIZE=$(du -h "$LOG_PATH" 2>/dev/null | cut -f1)
            CONTAINER_NAME=$(docker inspect --format='{{.Name}}' $container 2>/dev/null | sed 's/\///')
            echo "   ├─ $CONTAINER_NAME: $LOG_SIZE"
        fi
    done
}

# Функция анализа логов
analyze_logs() {
    print_header "АНАЛИЗ ЛОГОВ"
    
    # Системные логи
    echo -e "${BOLD}📝 Системные логи (/var/log):${NC}"
    du -sh /var/log/* 2>/dev/null | sort -hr | head -10 | nl -w2 -s'. '
    
    # Логи journald
    JOURNAL_SIZE=$(journalctl --disk-usage 2>/dev/null | awk '{print $7, $8}')
    echo -e "\n   📊 Логи systemd-journal: ${JOURNAL_SIZE}"
    
    # Старые логи
    OLD_LOGS=$(find /var/log -name "*.log" -mtime +30 2>/dev/null | wc -l)
    OLD_GZ_LOGS=$(find /var/log -name "*.gz" -mtime +30 2>/dev/null | wc -l)
    echo -e "   📁 Логов старше 30 дней: ${OLD_LOGS} шт."
    echo -e "   📁 Сжатых логов старше 30 дней: ${OLD_GZ_LOGS} шт."
}

# Функция анализа кешей
analyze_caches() {
    print_header "АНАЛИЗ КЕШЕЙ"
    
    # Apt кеш
    APT_CACHE=$(du -sh /var/cache/apt/archives 2>/dev/null | cut -f1)
    echo -e "${BOLD}📦 Apt кеш:${NC} ${APT_CACHE}"
    
    # Pip кеш
    if [ -d ~/.cache/pip ]; then
        PIP_CACHE=$(du -sh ~/.cache/pip 2>/dev/null | cut -f1)
        echo -e "🐍 Pip кеш: ${PIP_CACHE}"
    fi
    
    # Snap кеш
    if [ -d /var/lib/snapd/cache ]; then
        SNAP_CACHE=$(du -sh /var/lib/snapd/cache 2>/dev/null | cut -f1)
        echo -e "📦 Snap кеш: ${SNAP_CACHE}"
    fi
    
    # Общий кеш
    TOTAL_CACHE=$(du -sh /var/cache 2>/dev/null | cut -f1)
    echo -e "\n   📊 Общий размер кеша: ${TOTAL_CACHE}"
}

# Функция анализа баз данных
analyze_databases() {
    print_header "АНАЛИЗ БАЗ ДАННЫХ"
    
    # PostgreSQL в Docker
    if docker ps --format '{{.Names}}' | grep -q "postgres"; then
        echo -e "${BOLD}🐘 PostgreSQL (Docker):${NC}"
        docker exec $(docker ps -q --filter "name=postgres") du -sh /var/lib/postgresql/data 2>/dev/null | awk '{print "   Размер: " $1}'
    fi
    
    # PostgreSQL локальный
    if [ -d /var/lib/postgresql ]; then
        PSQL_SIZE=$(du -sh /var/lib/postgresql 2>/dev/null | cut -f1)
        echo -e "\n🐘 PostgreSQL (локальный): ${PSQL_SIZE}"
    fi
    
    # MySQL/MariaDB
    if [ -d /var/lib/mysql ]; then
        MYSQL_SIZE=$(du -sh /var/lib/mysql 2>/dev/null | cut -f1)
        echo -e "🐬 MySQL/MariaDB: ${MYSQL_SIZE}"
    fi
}

# Функция анализа оперативной памяти
analyze_memory() {
    print_header "АНАЛИЗ ОПЕРАТИВНОЙ ПАМЯТИ"
    
    # Общая информация о памяти
    echo -e "${BOLD}💾 Общая статистика памяти:${NC}"
    free -h | awk 'NR==2 {printf "   ├─ Всего: %s\n   ├─ Использовано: %s\n   ├─ Свободно: %s\n   └─ Доступно: %s\n", $2, $3, $4, $7}'
    
    # Процент использования
    MEM_PERCENT=$(free | awk 'NR==2 {printf "%.1f", ($3/$2)*100}')
    echo -e "\n   📊 Использование: ${MEM_PERCENT}%"
    
    if (( $(echo "$MEM_PERCENT > 80" | bc -l) )); then
        echo -e "   ${RED}${BOLD}⚠️  ВНИМАНИЕ! Высокое использование памяти!${NC}"
    fi
    
    # Топ-20 процессов по использованию памяти
    echo -e "\n${BOLD}🔝 Топ-20 процессов по использованию памяти:${NC}"
    ps aux --sort=-%mem | awk 'NR<=21 {printf "%2d. %-10s %6s %6s %s\n", NR-1, $1, $4"%", $6/1024"MB", $11}' | tail -n +2
    
    # Топ-10 процессов по RSS (реальная память)
    echo -e "\n${BOLD}📊 Топ-10 процессов по реальной памяти (RSS):${NC}"
    ps -eo pid,user,rss,comm --sort=-rss | head -11 | tail -n +2 | awk '{printf "%2d. PID: %-7s User: %-10s RSS: %6.2f MB  %s\n", NR, $1, $2, $3/1024, $4}'
    
    # Информация о swap
    echo -e "\n${BOLD}💿 Swap память:${NC}"
    free -h | awk 'NR==3 {printf "   ├─ Всего: %s\n   ├─ Использовано: %s\n   └─ Свободно: %s\n", $2, $3, $4}'
    
    SWAP_PERCENT=$(free | awk 'NR==3 {if($2>0) printf "%.1f", ($3/$2)*100; else print "0"}')
    echo -e "   📊 Использование swap: ${SWAP_PERCENT}%"
    
    if (( $(echo "$SWAP_PERCENT > 50" | bc -l) )); then
        echo -e "   ${YELLOW}⚠️  Высокое использование swap - возможна нехватка RAM${NC}"
    fi
    
    # Процессы использующие swap
    if [ -d /proc ]; then
        echo -e "\n${BOLD}💿 Топ-10 процессов использующих swap:${NC}"
        for dir in /proc/*/; do
            pid=$(basename "$dir")
            if [[ "$pid" =~ ^[0-9]+$ ]]; then
                if [ -f "/proc/$pid/status" ]; then
                    swap=$(grep VmSwap /proc/$pid/status 2>/dev/null | awk '{print $2}')
                    if [ -n "$swap" ] && [ "$swap" -gt 0 ]; then
                        comm=$(cat /proc/$pid/comm 2>/dev/null)
                        echo "$swap $pid $comm"
                    fi
                fi
            fi
        done | sort -rn | head -10 | awk '{printf "%2d. PID: %-7s Swap: %6.2f MB  %s\n", NR, $2, $1/1024, $3}'
    fi
    
    # Анализ кеша и буферов
    echo -e "\n${BOLD}📦 Кеш и буферы:${NC}"
    free -h | awk 'NR==2 {printf "   ├─ Буферы: %s\n   └─ Кеш: %s\n", $6, $7}'
    
    # Docker контейнеры и память
    if command -v docker &> /dev/null && docker info &>/dev/null; then
        echo -e "\n${BOLD}🐳 Использование памяти Docker контейнерами:${NC}"
        docker stats --no-stream --format "table {{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}" 2>/dev/null | tail -n +2 | head -10 | nl -w2 -s'. '
    fi
    
    # Рекомендации по памяти
    echo -e "\n${BOLD}💡 Рекомендации:${NC}"
    if (( $(echo "$MEM_PERCENT > 80" | bc -l) )); then
        echo -e "   ${YELLOW}• Рассмотрите возможность увеличения RAM${NC}"
        echo -e "   ${YELLOW}• Остановите неиспользуемые сервисы${NC}"
        echo -e "   ${YELLOW}• Проверьте процессы с высоким потреблением памяти${NC}"
    fi
    
    if (( $(echo "$SWAP_PERCENT > 50" | bc -l) )); then
        echo -e "   ${YELLOW}• Высокое использование swap замедляет систему${NC}"
        echo -e "   ${YELLOW}• Освободите RAM или увеличьте размер swap${NC}"
    fi
    
    # Очистка кеша (только информация)
    echo -e "\n${CYAN}ℹ️  Для очистки кеша памяти (требует root):${NC}"
    echo -e "   sync && echo 3 > /proc/sys/vm/drop_caches"
}

# Функция рекомендаций
show_recommendations() {
    print_header "РЕКОМЕНДАЦИИ ПО ОЧИСТКЕ"
    
    local recommendations=()
    
    # Проверка Docker
    if command -v docker &> /dev/null && docker info &>/dev/null; then
        recommendations+=("${GREEN}1. Очистка Docker:${NC}
   docker system prune -a -f --volumes
   docker builder prune -a -f
   ⚠️  Внимание: удалит все неиспользуемые образы, контейнеры и тома")
    fi
    
    # Проверка логов
    JOURNAL_SIZE=$(journalctl --disk-usage 2>/dev/null | awk '{print $3}')
    if [ -n "$JOURNAL_SIZE" ] && [ $JOURNAL_SIZE -gt 500000000 ]; then
        recommendations+=("${GREEN}2. Очистка логов journald:${NC}
   journalctl --vacuum-size=200M
   journalctl --vacuum-time=30d")
    fi
    
    # Проверка apt кеша
    if [ -d /var/cache/apt/archives ] && [ $(du -s /var/cache/apt/archives 2>/dev/null | cut -f1) -gt 1048576 ]; then
        recommendations+=("${GREEN}3. Очистка apt кеша:${NC}
   apt clean
   apt autoremove -y
   rm -rf /var/lib/apt/lists/*")
    fi
    
    # Проверка старых логов
    OLD_LOGS=$(find /var/log -name "*.log" -mtime +30 2>/dev/null | wc -l)
    if [ $OLD_LOGS -gt 10 ]; then
        recommendations+=("${GREEN}4. Удаление старых логов:${NC}
   find /var/log -name \"*.log\" -mtime +30 -delete
   find /var/log -name \"*.gz\" -mtime +30 -delete
   find /var/log -name \"*.1\" -delete")
    fi
    
    # Проверка tmp
    TMP_SIZE=$(du -s /tmp 2>/dev/null | cut -f1)
    if [ $TMP_SIZE -gt 1048576 ]; then
        recommendations+=("${GREEN}5. Очистка временных файлов:${NC}
   rm -rf /tmp/*
   rm -rf /var/tmp/*")
    fi
    
    # Проверка кешей пользователей
    if [ -d ~/.cache ] && [ $(du -s ~/.cache 2>/dev/null | cut -f1) -gt 1048576 ]; then
        recommendations+=("${GREEN}6. Очистка кеша пользователя:${NC}
   rm -rf ~/.cache/*
   rm -rf ~/.npm/_cacache
   rm -rf ~/.cargo/registry/cache")
    fi
    
    # Удаленные файлы
    DELETED_FILES=$(lsof +L1 2>/dev/null | wc -l)
    if [ $DELETED_FILES -gt 5 ]; then
        recommendations+=("${YELLOW}7. Перезапуск процессов с удаленными файлами:${NC}
   systemctl restart <service_name>
   # Или полная перезагрузка сервера: reboot")
    fi
    
    # Вывод рекомендаций
    for rec in "${recommendations[@]}"; do
        echo -e "$rec\n"
    done
    
    if [ ${#recommendations[@]} -eq 0 ]; then
        print_success "Система в хорошем состоянии! Очистка не требуется."
    fi
}

# Функция безопасной очистки
safe_cleanup() {
    print_header "БЕЗОПАСНАЯ ОЧИСТКА"
    
    echo -e "${YELLOW}${BOLD}ВНИМАНИЕ! Эта очистка безопасна и не удалит важные данные.${NC}\n"
    read -p "Выполнить безопасную очистку? (y/n): " -n 1 -r
    echo
    
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        print_info "Очистка отменена"
        return
    fi
    
    log "Начало безопасной очистки"
    
    # 1. Очистка Docker
    if command -v docker &> /dev/null && docker info &>/dev/null; then
        print_info "Очистка Docker..."
        docker system prune -a -f --volumes 2>/dev/null
        docker builder prune -a -f 2>/dev/null
        print_success "Docker очищен"
        log "Очищен Docker"
    fi
    
    # 2. Очистка логов
    print_info "Очистка логов..."
    journalctl --vacuum-size=200M 2>/dev/null
    find /var/log -name "*.log" -type f -exec truncate -s 0 {} \; 2>/dev/null
    find /var/log -name "*.gz" -delete 2>/dev/null
    find /var/log -name "*.1" -delete 2>/dev/null
    find /var/log -name "*.old" -delete 2>/dev/null
    print_success "Логи очищены"
    log "Очищены логи"
    
    # 3. Очистка apt
    print_info "Очистка apt кеша..."
    apt clean 2>/dev/null
    apt autoremove -y 2>/dev/null
    rm -rf /var/lib/apt/lists/* 2>/dev/null
    print_success "Apt кеш очищен"
    log "Очищен apt кеш"
    
    # 4. Очистка временных файлов
    print_info "Очистка временных файлов..."
    rm -rf /tmp/* 2>/dev/null
    rm -rf /var/tmp/* 2>/dev/null
    print_success "Временные файлы удалены"
    log "Удалены временные файлы"
    
    # 5. Очистка кешей
    print_info "Очистка кешей..."
    rm -rf ~/.cache/* 2>/dev/null
    rm -rf /var/cache/apt/archives/*.deb 2>/dev/null
    print_success "Кеши очищены"
    log "Очищены кеши"
    
    print_success "Очистка завершена!"
    
    # Показываем результат
    echo -e "\n${BOLD}Результат после очистки:${NC}"
    df -h / | awk 'NR==2 {printf "   Использовано: %s (%s)\n   Свободно: %s\n", $3, $5, $4}'
}

# Функция генерации отчета
generate_report() {
    print_header "ГЕНЕРАЦИЯ ОТЧЕТА"
    
    {
        echo "============================================"
        echo "ОТЧЕТ О ДИСКОВОМ ПРОСТРАНСТВЕ"
        echo "Дата: $(date)"
        echo "Хост: $(hostname)"
        echo "============================================"
        echo ""
        
        echo "=== ИСПОЛЬЗОВАНИЕ ДИСКА ==="
        df -h
        echo ""
        
        echo "=== ТОП-20 ПАПОК ==="
        du -sh /* 2>/dev/null | sort -hr | head -20
        echo ""
        
        echo "=== DOCKER СТАТИСТИКА ==="
        docker system df 2>/dev/null
        echo ""
        
        echo "=== ТОП-20 БОЛЬШИХ ФАЙЛОВ ==="
        find / -type f -size +100M 2>/dev/null -exec ls -lh {} \; | sort -k5 -hr | head -20
        echo ""
        
        echo "=== ЛОГИ ==="
        du -sh /var/log/* 2>/dev/null | sort -hr | head -10
        echo ""
        
        echo "=== УДАЛЕННЫЕ ФАЙЛЫ ==="
        lsof +L1 2>/dev/null
        echo ""
        
    } > "$REPORT_FILE"
    
    print_success "Отчет сохранен: $REPORT_FILE"
    echo -e "\nПросмотреть отчет: cat $REPORT_FILE"
}

# Функция меню
show_menu() {
    clear
    echo -e "${CYAN}${BOLD}╔══════════════════════════════════════════════════════════╗"
    echo -e "║            ДИСК АНАЛИЗАТОР И ОЧИСТКА СИСТЕМЫ             ║"
    echo -e "╚══════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "${GREEN}1.${NC} Полный анализ диска"
    echo -e "${GREEN}2.${NC} Анализ оперативной памяти"
    echo -e "${GREEN}3.${NC} Анализ Docker"
    echo -e "${GREEN}4.${NC} Анализ логов"
    echo -e "${GREEN}5.${NC} Анализ кешей"
    echo -e "${GREEN}6.${NC} Анализ баз данных"
    echo -e "${GREEN}7.${NC} Показать рекомендации"
    echo -e "${YELLOW}8.${NC} Безопасная очистка (автоматическая)"
    echo -e "${BLUE}9.${NC} Сгенерировать полный отчет"
    echo -e "${RED}0.${NC} Выход"
    echo ""
    echo -e "${CYAN}═══════════════════════════════════════════════════════════${NC}"
    echo -n "Выберите действие [0-9]: "
}

# Функция основного цикла
main() {
    # Проверка прав
    if [ "$EUID" -ne 0 ]; then 
        print_error "Скрипт должен запускаться с правами root (sudo)"
        exit 1
    fi
    
    while true; do
        show_menu
        read choice
        case $choice in
            1) analyze_disk ;;
            2) analyze_memory ;;
            3) analyze_docker ;;
            4) analyze_logs ;;
            5) analyze_caches ;;
            6) analyze_databases ;;
            7) show_recommendations ;;
            8) safe_cleanup ;;
            9) generate_report ;;
            0)
                echo -e "\n${GREEN}До свидания!${NC}"
                exit 0
                ;;
            *)
                print_error "Неверный выбор"
                sleep 1
                ;;
        esac
        echo -e "\n${CYAN}Нажмите Enter для продолжения...${NC}"
        read
    done
}

# Запуск
main