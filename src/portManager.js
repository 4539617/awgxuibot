import { exec } from 'child_process';
import { promisify } from 'util';
import { logger } from './logger.js';

const execAsync = promisify(exec);

/**
 * Генерация случайного свободного порта
 * @param {number} minPort - Минимальный порт (по умолчанию 30000)
 * @param {number} maxPort - Максимальный порт (по умолчанию 65000)
 * @param {number} maxAttempts - Максимальное количество попыток (по умолчанию 10)
 * @returns {Promise<number|null>} Свободный порт или null если не найден
 */
async function generateRandomPort(minPort = 30000, maxPort = 65000, maxAttempts = 10) {
    logger.info('[PortManager] Генерация случайного порта...');
    
    for (let attempt = 1; attempt <= maxAttempts; attempt++) {
        const port = Math.floor(Math.random() * (maxPort - minPort + 1)) + minPort;
        logger.info(`[PortManager] Попытка ${attempt}/${maxAttempts}: проверка порта ${port}`);
        
        const inUse = await isPortInUse(port);
        
        if (!inUse) {
            logger.info(`[PortManager] Найден свободный порт: ${port}`);
            return port;
        }
        
        logger.info(`[PortManager] Порт ${port} занят, пробуем другой...`);
    }
    
    logger.error(`[PortManager] Не удалось найти свободный порт после ${maxAttempts} попыток`);
    return null;
}

/**
 * Проверка, занят ли порт
 * @param {number} port - Порт для проверки
 * @returns {Promise<boolean>} true если порт занят, false если свободен
 */
async function isPortInUse(port) {
    try {
        // Проверяем через Docker
        const dockerCheck = await checkDockerPorts(port);
        if (dockerCheck) {
            logger.info(`[PortManager] Порт ${port} занят Docker контейнером`);
            return true;
        }
        
        // Проверяем через netstat (для Linux)
        try {
            const { stdout } = await execAsync(`netstat -tuln | grep :${port} || true`);
            if (stdout.trim()) {
                logger.info(`[PortManager] Порт ${port} занят системным процессом`);
                return true;
            }
        } catch (error) {
            // netstat может не быть установлен, пробуем ss
            try {
                const { stdout } = await execAsync(`ss -tuln | grep :${port} || true`);
                if (stdout.trim()) {
                    logger.info(`[PortManager] Порт ${port} занят системным процессом (ss)`);
                    return true;
                }
            } catch (ssError) {
                logger.warn(`[PortManager] Не удалось проверить порт через netstat/ss: ${ssError.message}`);
            }
        }
        
        logger.info(`[PortManager] Порт ${port} свободен`);
        return false;
    } catch (error) {
        logger.error(`[PortManager] Ошибка проверки порта ${port}: ${error.message}`);
        // В случае ошибки считаем порт занятым для безопасности
        return true;
    }
}

/**
 * Проверка портов, используемых Docker контейнерами
 * @param {number} port - Порт для проверки
 * @returns {Promise<boolean>} true если порт используется Docker
 */
async function checkDockerPorts(port) {
    try {
        const { stdout } = await execAsync('docker ps --format "{{.Ports}}"');
        const ports = stdout.split('\n').filter(line => line.trim());
        
        for (const portLine of ports) {
            // Формат: 0.0.0.0:49656->49656/udp
            const match = portLine.match(/0\.0\.0\.0:(\d+)->/);
            if (match && parseInt(match[1]) === port) {
                return true;
            }
        }
        
        return false;
    } catch (error) {
        logger.error(`[PortManager] Ошибка проверки Docker портов: ${error.message}`);
        return false;
    }
}

/**
 * Валидация введённого порта
 * @param {number|string} port - Порт для валидации
 * @returns {{valid: boolean, error: string|null}} Результат валидации
 */
function validatePort(port) {
    // Проверка на число
    const portNum = parseInt(port);
    
    if (isNaN(portNum)) {
        return {
            valid: false,
            error: 'Порт должен быть числом'
        };
    }
    
    // Проверка диапазона
    if (portNum < 1024 || portNum > 65535) {
        return {
            valid: false,
            error: 'Порт должен быть в диапазоне 1024-65535'
        };
    }
    
    return {
        valid: true,
        error: null
    };
}

/**
 * Получение списка занятых портов из Docker
 * @returns {Promise<number[]>} Массив занятых портов
 */
async function getUsedPorts() {
    try {
        const { stdout } = await execAsync('docker ps --format "{{.Ports}}"');
        const ports = [];
        const lines = stdout.split('\n').filter(line => line.trim());
        
        for (const line of lines) {
            // Извлекаем все порты из строки
            const matches = line.matchAll(/0\.0\.0\.0:(\d+)->/g);
            for (const match of matches) {
                ports.push(parseInt(match[1]));
            }
        }
        
        logger.info(`[PortManager] Найдено занятых портов: ${ports.length}`);
        return ports;
    } catch (error) {
        logger.error(`[PortManager] Ошибка получения списка портов: ${error.message}`);
        return [];
    }
}

/**
 * Получение следующего свободного порта в диапазоне
 * @param {number} startPort - Начальный порт
 * @param {number} endPort - Конечный порт
 * @returns {Promise<number|null>} Свободный порт или null
 */
async function getNextFreePort(startPort = 30000, endPort = 65000) {
    logger.info(`[PortManager] Поиск свободного порта в диапазоне ${startPort}-${endPort}`);
    
    const usedPorts = await getUsedPorts();
    
    for (let port = startPort; port <= endPort; port++) {
        if (!usedPorts.includes(port)) {
            const inUse = await isPortInUse(port);
            if (!inUse) {
                logger.info(`[PortManager] Найден свободный порт: ${port}`);
                return port;
            }
        }
    }
    
    logger.error(`[PortManager] Не найдено свободных портов в диапазоне ${startPort}-${endPort}`);
    return null;
}

export {
    generateRandomPort,
    isPortInUse,
    validatePort,
    getUsedPorts,
    getNextFreePort,
    checkDockerPorts
};
