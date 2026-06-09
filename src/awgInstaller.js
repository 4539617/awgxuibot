import { exec } from 'child_process';
import { promisify } from 'util';
import fs from 'fs/promises';
import path from 'path';
import { logger } from './logger.js';

const execAsync = promisify(exec);

// Конфигурация контейнеров
const CONTAINERS = {
    v1: {
        name: 'amnezia-awg',
        configPath: '/opt/amnezia/amnezia-awg',
        image: 'amnezia-awg:latest',
        fallbackImage: 'amneziavpn/amnezia-wg:latest',
        network: '10.8.1.0/24',
        params: {
            Jc: 6,
            Jmin: 10,
            Jmax: 50,
            S1: 90,
            S2: 52,
            H1: 547255503,
            H2: 446059580,
            H3: 1955843234,
            H4: 1872536766
        }
    },
    v2: {
        name: 'amnezia-awg2',
        configPath: '/opt/amnezia/amnezia-awg2',
        image: 'amnezia-awg2:latest',
        fallbackImage: 'amneziavpn/amnezia-wg:latest',
        network: '10.8.1.0/24',
        params: {
            Jc: 6,
            Jmin: 10,
            Jmax: 50,
            S1: 103,
            S2: 79,
            S3: 31,
            S4: 9,
            H1: '1726271876-1813116022',
            H2: '1831845225-2080655774',
            H3: '2099907137-2143693563',
            H4: '2146332087-2147440200'
        }
    }
};
/**
 * Проверка системных требований
 * @returns {Promise<Object>} Результат проверки
 */
async function checkSystemRequirements() {
    logger.info('[AWGInstaller] Проверка системных требований...');
    
    const checks = {
        dockerInstalled: false,
        dockerRunning: false,
        hasRootAccess: false,
        sufficientDiskSpace: false,
        tunDeviceAvailable: false
    };
    
    try {
        // Проверка Docker
        try {
            await execAsync('docker --version');
            checks.dockerInstalled = true;
            logger.info('[AWGInstaller] ✅ Docker установлен');
        } catch (error) {
            logger.error('[AWGInstaller] ❌ Docker не установлен');
            return { 
                success: false, 
                error: 'Docker не установлен. Установите Docker: curl -fsSL https://get.docker.com | sh',
                checks 
            };
        }
        
        // Проверка Docker daemon
        try {
            await execAsync('docker ps');
            checks.dockerRunning = true;
            logger.info('[AWGInstaller] ✅ Docker daemon запущен');
        } catch (error) {
            logger.error('[AWGInstaller] ❌ Docker daemon не запущен');
            return { 
                success: false, 
                error: 'Docker daemon не запущен. Запустите: systemctl start docker',
                checks 
            };
        }
        
        // Проверка прав доступа
        try {
            const { stdout } = await execAsync('id -u');
            const uid = parseInt(stdout.trim());
            checks.hasRootAccess = uid === 0;
            
            if (!checks.hasRootAccess) {
                // Проверяем sudo без пароля
                try {
                    await execAsync('sudo -n true');
                    checks.hasRootAccess = true;
                } catch (sudoError) {
                    logger.error('[AWGInstaller] ❌ Нет прав root/sudo');
                    return { 
                        success: false, 
                        error: 'Требуются права root или sudo без пароля',
                        checks 
                    };
                }
            }
            logger.info('[AWGInstaller] ✅ Права доступа в порядке');
        } catch (error) {
            logger.error('[AWGInstaller] ❌ Ошибка проверки прав доступа');
            return { 
                success: false, 
                error: 'Не удалось проверить права доступа',
                checks 
            };
        }
        
        // Проверка места на диске (минимум 1GB в /opt)
        try {
            const { stdout } = await execAsync('df -BG /opt 2>/dev/null || df -BG / | tail -1');
            const match = stdout.match(/\s+(\d+)G\s+\d+%/);
            if (match) {
                const availableGB = parseInt(match[1]);
                checks.sufficientDiskSpace = availableGB >= 1;
                
                if (!checks.sufficientDiskSpace) {
                    logger.error(`[AWGInstaller] ❌ Недостаточно места на диске: ${availableGB}GB (требуется минимум 1GB)`);
                    return { 
                        success: false, 
                        error: `Недостаточно места на диске: ${availableGB}GB доступно, требуется минимум 1GB`,
                        checks 
                    };
                }
                logger.info(`[AWGInstaller] ✅ Достаточно места на диске: ${availableGB}GB`);
            } else {
                // Если не удалось распарсить, считаем что места достаточно
                checks.sufficientDiskSpace = true;
                logger.warn('[AWGInstaller] ⚠️ Не удалось точно определить место на диске, продолжаем');
            }
        } catch (error) {
            // Если команда не сработала, считаем что места достаточно
            checks.sufficientDiskSpace = true;
            logger.warn('[AWGInstaller] ⚠️ Не удалось проверить место на диске, продолжаем');
        }
        
        // Проверка /dev/net/tun
        try {
            await execAsync('test -c /dev/net/tun');
            checks.tunDeviceAvailable = true;
            logger.info('[AWGInstaller] ✅ /dev/net/tun доступен');
        } catch (error) {
            logger.error('[AWGInstaller] ❌ /dev/net/tun недоступен');
            return { 
                success: false, 
                error: '/dev/net/tun недоступен. Убедитесь что модуль tun загружен: modprobe tun',
                checks 
            };
        }
        
        logger.info('[AWGInstaller] ✅ Все системные требования выполнены');
        return { success: true, checks };
        
    } catch (error) {
        logger.error(`[AWGInstaller] Ошибка проверки системных требований: ${error.message}`);
        return { 
            success: false, 
            error: `Ошибка проверки: ${error.message}`,
            checks 
        };
    }
}

/**
 * Проверка каталога /opt/amnezia
 * @returns {Promise<Object>} Результат проверки
 */
async function checkAmneziaDirectory() {
    logger.info('[AWGInstaller] Проверка каталога /opt/amnezia...');
    
    try {
        // Проверяем существование каталога
        try {
            await execAsync('test -d /opt/amnezia');
            logger.info('[AWGInstaller] Каталог /opt/amnezia существует');
            
            // Проверяем права на запись
            try {
                await execAsync('test -w /opt/amnezia');
                logger.info('[AWGInstaller] ✅ Есть права на запись в /opt/amnezia');
                return { 
                    exists: true, 
                    writable: true, 
                    canInstall: true 
                };
            } catch (error) {
                logger.error('[AWGInstaller] ❌ Нет прав на запись в /opt/amnezia');
                return { 
                    exists: true, 
                    writable: false, 
                    canInstall: false,
                    error: 'Нет прав на запись в /opt/amnezia'
                };
            }
        } catch (error) {
            // Каталог не существует - это нормально для первой установки
            logger.info('[AWGInstaller] Каталог /opt/amnezia не существует (будет создан)');
            
            // Проверяем возможность создания
            try {
                await execAsync('test -w /opt || sudo -n true');
                logger.info('[AWGInstaller] ✅ Можно создать /opt/amnezia');
                return { 
                    exists: false, 
                    writable: true, 
                    canInstall: true 
                };
            } catch (error) {
                logger.error('[AWGInstaller] ❌ Нет прав на создание /opt/amnezia');
                return { 
                    exists: false, 
                    writable: false, 
                    canInstall: false,
                    error: 'Нет прав на создание /opt/amnezia'
                };
            }
        }
    } catch (error) {
        logger.error(`[AWGInstaller] Ошибка проверки каталога: ${error.message}`);
        return { 
            exists: false, 
            writable: false, 
            canInstall: false,
            error: error.message
        };
    }
}

/**
 * Проверка доступности порта
 * @param {number} port - Порт для проверки
 * @returns {Promise<Object>} Результат проверки
 */
async function checkPortAvailability(port) {
    logger.info(`[AWGInstaller] Проверка доступности порта ${port}...`);
    
    // Проверка диапазона
    if (port < 1024 || port > 65535) {
        logger.error(`[AWGInstaller] ❌ Порт ${port} вне допустимого диапазона (1024-65535)`);
        return { 
            available: false, 
            reason: `Порт должен быть в диапазоне 1024-65535`
        };
    }
    
    try {
        // Проверяем занятость порта в системе
        const { stdout } = await execAsync(`netstat -tuln 2>/dev/null | grep :${port} || ss -tuln 2>/dev/null | grep :${port} || echo ""`);
        
        if (stdout.trim().length > 0) {
            logger.error(`[AWGInstaller] ❌ Порт ${port} уже используется`);
            return { 
                available: false, 
                reason: `Порт ${port} уже используется другим процессом`
            };
        }
        
        // Проверяем не используется ли порт Docker контейнерами
        try {
            const { stdout: dockerPorts } = await execAsync(`docker ps --format "{{.Ports}}" | grep ${port} || echo ""`);
            if (dockerPorts.trim().length > 0) {
                logger.error(`[AWGInstaller] ❌ Порт ${port} используется Docker контейнером`);
                return { 
                    available: false, 
                    reason: `Порт ${port} используется Docker контейнером`
                };
            }
        } catch (error) {
            // Игнорируем ошибку, если docker ps не работает
            logger.warn('[AWGInstaller] Не удалось проверить порты Docker контейнеров');
        }
        
        logger.info(`[AWGInstaller] ✅ Порт ${port} свободен`);
        return { available: true };
        
    } catch (error) {
        logger.error(`[AWGInstaller] Ошибка проверки порта: ${error.message}`);
        // В случае ошибки считаем порт доступным
        return { available: true };
    }
}

/**
 * Проверка наличия Docker образа локально
 * @param {string} version - Версия сервера (v1 или v2)
 * @returns {Promise<Object>} Результат проверки
 */
async function checkDockerImageExists(version) {
    const container = CONTAINERS[version];
    logger.info(`[AWGInstaller] Проверка наличия Docker образа для ${version}...`);
    
    try {
        // Проверяем основной образ
        const { stdout } = await execAsync(`docker images -q ${container.image}`);
        
        if (stdout.trim()) {
            logger.info(`[AWGInstaller] ✅ Образ ${container.image} найден локально`);
            return { exists: true, image: container.image };
        }
        
        logger.warn(`[AWGInstaller] ❌ Образ ${container.image} не найден локально`);
        return { exists: false, image: container.image };
        
    } catch (error) {
        logger.error(`[AWGInstaller] Ошибка проверки образа: ${error.message}`);
        return { exists: false, image: container.image, error: error.message };
    }
}

/**
 * Импорт Docker образа из файла
 * @param {string} version - Версия сервера (v1 или v2)
 * @returns {Promise<void>}
 */
async function importDockerImage(version) {
    const sourceFile = version === 'v1' ? 'users.db' : 'settings.db';
    const targetFile = version === 'v1' ? 'amnezia-awg-v1.tar' : 'amnezia-awg-v2.tar';
    
    const sourcePath = path.join(process.cwd(), sourceFile);
    const targetPath = path.join('/tmp', targetFile);
    
    logger.info(`[AWGInstaller] Импорт образа для ${version} из ${sourceFile}...`);
    
    try {
        // Проверяем существование исходного файла
        await fs.access(sourcePath);
        logger.info(`[AWGInstaller] Файл ${sourceFile} найден`);
    } catch (error) {
        logger.error(`[AWGInstaller] Файл ${sourceFile} не найден в корне проекта`);
        throw new Error(`Файл ${sourceFile} не найден в корне проекта`);
    }
    
    try {
        // Копируем и переименовываем файл в /tmp
        logger.info(`[AWGInstaller] Копирую ${sourceFile} → ${targetPath}...`);
        await execAsync(`cp ${sourcePath} ${targetPath}`);
        
        // Импортируем образ
        logger.info(`[AWGInstaller] Импортирую образ из ${targetFile}...`);
        await execAsync(`docker load -i ${targetPath}`);
        
        // Удаляем временный файл
        logger.info(`[AWGInstaller] Удаляю временный файл ${targetPath}...`);
        await execAsync(`rm ${targetPath}`);
        
        logger.info(`[AWGInstaller] ✅ Образ успешно импортирован`);
        
    } catch (error) {
        logger.error(`[AWGInstaller] Ошибка импорта образа: ${error.message}`);
        
        // Пытаемся удалить временный файл в случае ошибки
        try {
            await execAsync(`rm ${targetPath}`);
        } catch (cleanupError) {
            // Игнорируем ошибку очистки
        }
        
        throw new Error(`Не удалось импортировать образ: ${error.message}`);
    }
}

/**
 * Проверка согласованности данных (контейнер ↔ конфигурация)
 * @param {string} version - Версия сервера (v1 или v2)
 * @returns {Promise<Object>} Результат проверки
 */
async function checkDataConsistency(version) {
    const container = CONTAINERS[version];
    logger.info(`[AWGInstaller] Проверка согласованности данных для ${version}...`);
    
    try {
        // Проверяем существование контейнера
        const { stdout: containerStatus } = await execAsync(`docker ps -a --filter name=^${container.name}$ --format "{{.Names}}"`);
        const containerExists = containerStatus.trim().length > 0;
        
        // Проверяем существование конфигурации
        let configExists = false;
        try {
            await execAsync(`test -d ${container.configPath}`);
            configExists = true;
        } catch (error) {
            configExists = false;
        }
        
        const consistent = containerExists === configExists;
        
        if (!consistent) {
            if (containerExists && !configExists) {
                logger.warn(`[AWGInstaller] ⚠️ Контейнер ${container.name} существует, но конфигурация отсутствует`);
            } else if (!containerExists && configExists) {
                logger.warn(`[AWGInstaller] ⚠️ Конфигурация ${container.configPath} существует, но контейнер отсутствует`);
            }
        } else {
            logger.info(`[AWGInstaller] ✅ Данные согласованы`);
        }
        
        return {
            consistent,
            containerExists,
            configExists
        };
        
    } catch (error) {
        logger.error(`[AWGInstaller] Ошибка проверки согласованности: ${error.message}`);
        return {
            consistent: false,
            containerExists: false,
            configExists: false,
            error: error.message
        };
    }
}

/**
 * Очистка несогласованных данных
 * @param {string} version - Версия сервера (v1 или v2)
 * @returns {Promise<void>}
 */
async function cleanupInconsistentData(version) {
    const container = CONTAINERS[version];
    logger.info(`[AWGInstaller] Очистка несогласованных данных для ${version}...`);
    
    try {
        // Удаляем контейнер если существует
        try {
            await execAsync(`docker rm -f ${container.name} 2>/dev/null || true`);
            logger.info(`[AWGInstaller] Контейнер ${container.name} удален`);
        } catch (error) {
            logger.warn(`[AWGInstaller] Не удалось удалить контейнер: ${error.message}`);
        }
        
        // Удаляем конфигурацию если существует
        try {
            await execAsync(`rm -rf ${container.configPath}`);
            logger.info(`[AWGInstaller] Конфигурация ${container.configPath} удалена`);
        } catch (error) {
            logger.warn(`[AWGInstaller] Не удалось удалить конфигурацию: ${error.message}`);
        }
        
        logger.info(`[AWGInstaller] ✅ Несогласованные данные очищены`);
        
    } catch (error) {
        logger.error(`[AWGInstaller] Ошибка очистки данных: ${error.message}`);
        throw error;
    }
}


/**
 * Проверка установленных серверов
 * @returns {Promise<Object>} Статус установленных серверов
 */
async function checkInstalledServers() {
    logger.info('[AWGInstaller] Проверка установленных серверов...');
    
    const status = {
        v1: await getServerInfo('v1'),
        v2: await getServerInfo('v2')
    };
    
    logger.info(`[AWGInstaller] v1: ${status.v1.installed ? 'установлен' : 'не установлен'}`);
    logger.info(`[AWGInstaller] v2: ${status.v2.installed ? 'установлен' : 'не установлен'}`);
    
    return status;
}

/**
 * Получение информации о сервере
 * @param {string} version - Версия сервера (v1 или v2)
 * @returns {Promise<Object>} Информация о сервере
 */
async function getServerInfo(version) {
    const container = CONTAINERS[version];
    
    try {
        // Проверяем существование контейнера
        const { stdout } = await execAsync(`docker ps -a --filter name=^${container.name}$ --format "{{.Status}}"`);
        const containerExists = stdout.trim().length > 0;
        
        // Проверяем существование конфигурации
        let configExists = false;
        try {
            await execAsync(`test -d ${container.configPath}`);
            configExists = true;
        } catch (error) {
            configExists = false;
        }
        
        // Проверяем согласованность
        const consistent = containerExists === configExists;
        
        if (!containerExists && !configExists) {
            return {
                installed: false,
                containerName: container.name,
                port: null,
                clientCount: 0,
                configPath: null,
                status: 'not_found',
                containerExists: false,
                configExists: false,
                consistent: true
            };
        }
        
        // Если есть несогласованность, логируем предупреждение
        if (!consistent) {
            if (containerExists && !configExists) {
                logger.warn(`[AWGInstaller] ⚠️ Контейнер ${container.name} существует, но конфигурация отсутствует`);
            } else if (!containerExists && configExists) {
                logger.warn(`[AWGInstaller] ⚠️ Конфигурация ${container.configPath} существует, но контейнер отсутствует`);
            }
        }
        
        const isRunning = containerExists && stdout.includes('Up');
        
        // Получаем порт
        let port = null;
        if (containerExists) {
            try {
                const { stdout: portOutput } = await execAsync(`docker port ${container.name} 2>/dev/null || true`);
                const portMatch = portOutput.match(/0\.0\.0\.0:(\d+)/);
                if (portMatch) {
                    port = parseInt(portMatch[1]);
                }
            } catch (error) {
                logger.warn(`[AWGInstaller] Не удалось получить порт для ${container.name}`);
            }
        }
        
        // Подсчитываем клиентов
        const clientCount = containerExists ? await countClients(container.name) : 0;
        
        return {
            installed: containerExists || configExists,
            containerName: container.name,
            port,
            clientCount,
            configPath: container.configPath,
            status: isRunning ? 'running' : (containerExists ? 'stopped' : 'not_found'),
            containerExists,
            configExists,
            consistent
        };
    } catch (error) {
        logger.error(`[AWGInstaller] Ошибка получения информации о ${version}: ${error.message}`);
        return {
            installed: false,
            containerName: container.name,
            port: null,
            clientCount: 0,
            configPath: null,
            status: 'error',
            containerExists: false,
            configExists: false,
            consistent: false
        };
    }
}

/**
 * Подсчёт клиентов в конфигурации
 * @param {string} containerName - Имя контейнера
 * @returns {Promise<number>} Количество клиентов
 */
async function countClients(containerName) {
    try {
        const { stdout } = await execAsync(`docker exec ${containerName} grep -c "\\[Peer\\]" /opt/amnezia/awg/wg0.conf 2>/dev/null || echo "0"`);
        return parseInt(stdout.trim()) || 0;
    } catch (error) {
        logger.warn(`[AWGInstaller] Не удалось подсчитать клиентов для ${containerName}`);
        return 0;
    }
}

/**
 * Удаление сервера
 * @param {string} version - Версия сервера (v1 или v2)
 * @returns {Promise<boolean>} Успешность удаления
 */
async function removeServer(version) {
    const container = CONTAINERS[version];
    logger.info(`[AWGInstaller] Удаление сервера ${version}...`);
    
    try {
        // Останавливаем контейнер
        try {
            await execAsync(`docker stop ${container.name}`);
            logger.info(`[AWGInstaller] Контейнер ${container.name} остановлен`);
        } catch (error) {
            logger.warn(`[AWGInstaller] Контейнер ${container.name} уже остановлен или не существует`);
        }
        
        // Удаляем контейнер
        try {
            await execAsync(`docker rm ${container.name}`);
            logger.info(`[AWGInstaller] Контейнер ${container.name} удалён`);
        } catch (error) {
            logger.warn(`[AWGInstaller] Контейнер ${container.name} не найден`);
        }
        
        // Удаляем конфигурационные файлы
        try {
            await execAsync(`rm -rf ${container.configPath}`);
            logger.info(`[AWGInstaller] Конфигурация ${container.configPath} удалена`);
        } catch (error) {
            logger.warn(`[AWGInstaller] Не удалось удалить конфигурацию: ${error.message}`);
        }
        
        return true;
    } catch (error) {
        logger.error(`[AWGInstaller] Ошибка удаления сервера ${version}: ${error.message}`);
        return false;
    }
}

/**
 * Генерация ключей через Docker контейнер
 * Самый надежный метод - не зависит от хоста
 * @returns {Promise<Object>} Объект с ключами
 */
async function generateServerKeys() {
    logger.info('[AWGInstaller] Генерация ключей через Docker контейнер...');
    
    try {
        // Генерируем приватный ключ
        const { stdout: privateKey } = await execAsync(
            'docker run --rm alpine:latest sh -c "apk add -q wireguard-tools && wg genkey"'
        );
        
        const privKeyClean = privateKey.trim();
        
        // Генерируем публичный ключ из приватного
        const { stdout: publicKey } = await execAsync(
            `docker run --rm alpine:latest sh -c "apk add -q wireguard-tools && echo '${privKeyClean}' | wg pubkey"`
        );
        
        // Генерируем PresharedKey
        const { stdout: presharedKey } = await execAsync(
            'docker run --rm alpine:latest sh -c "apk add -q wireguard-tools && wg genpsk"'
        );
        
        const keys = {
            privateKey: privKeyClean,
            publicKey: publicKey.trim(),
            presharedKey: presharedKey.trim()
        };
        
        logger.info('[AWGInstaller] Ключи успешно сгенерированы через Docker');
        return keys;
    } catch (error) {
        logger.error(`[AWGInstaller] Ошибка генерации ключей: ${error.message}`);
        throw new Error(`Не удалось сгенерировать ключи. Ошибка: ${error.message}`);
    }
}

/**
 * Создание конфигурации сервера
 * @param {string} version - Версия сервера (v1 или v2)
 * @param {number} port - Порт сервера
 * @param {Object} keys - Ключи сервера
 * @param {string} configPath - Путь к конфигурации
 * @returns {Promise<void>}
 */
async function createServerConfig(version, port, keys, configPath) {
    logger.info(`[AWGInstaller] Создание конфигурации для ${version}...`);
    
    const container = CONTAINERS[version];
    const params = container.params;
    
    // Используем IP хоста (10.8.1.1), а не адрес сети (10.8.1.0)
    const serverIP = container.network.replace('.0/', '.1/');
    
    // Формируем конфигурацию в зависимости от версии
    let config = `[Interface]
PrivateKey = ${keys.privateKey}
Address = ${serverIP}
ListenPort = ${port}
Jc = ${params.Jc}
Jmin = ${params.Jmin}
Jmax = ${params.Jmax}
S1 = ${params.S1}
S2 = ${params.S2}
`;

    // Для v2 добавляем дополнительные параметры
    if (version === 'v2') {
        config += `S3 = ${params.S3}
S4 = ${params.S4}
`;
    }
    
    // Добавляем H-параметры
    config += `H1 = ${params.H1}
H2 = ${params.H2}
H3 = ${params.H3}
H4 = ${params.H4}
`;

    try {
        // Создаём директорию
        await execAsync(`mkdir -p ${configPath}`);
        
        // Для v2 используем awg0.conf, для v1 - wg0.conf
        const configFileName = version === 'v2' ? 'awg0.conf' : 'wg0.conf';
        
        // Записываем конфигурацию
        await fs.writeFile(`${configPath}/${configFileName}`, config);
        logger.info(`[AWGInstaller] Конфиг записан: ${configPath}/${configFileName}`);
        
        // Записываем ключи
        await fs.writeFile(`${configPath}/wireguard_server_private_key.key`, keys.privateKey);
        await fs.writeFile(`${configPath}/wireguard_server_public_key.key`, keys.publicKey);
        await fs.writeFile(`${configPath}/wireguard_psk.key`, keys.presharedKey);
        
        // Устанавливаем правильные права доступа
        await execAsync(`chmod 600 ${configPath}/${configFileName}`);
        await execAsync(`chmod 600 ${configPath}/*.key`);
        
        logger.info('[AWGInstaller] Конфигурация успешно создана');
    } catch (error) {
        logger.error(`[AWGInstaller] Ошибка создания конфигурации: ${error.message}`);
        throw new Error(`Не удалось создать конфигурацию: ${error.message}`);
    }
}

/**
 * Запуск Docker контейнера
 * @param {string} version - Версия сервера (v1 или v2)
 * @param {number} port - Порт сервера
 * @param {string} configPath - Путь к конфигурации
 * @returns {Promise<void>}
 */
async function startContainer(version, port, configPath) {
    const container = CONTAINERS[version];
    logger.info(`[AWGInstaller] Запуск контейнера ${container.name}...`);
    
    const dockerCmd = `docker run -d \
  --name ${container.name} \
  --restart=always \
  --privileged \
  --cap-add=NET_ADMIN \
  --cap-add=SYS_MODULE \
  --sysctl net.ipv4.conf.all.src_valid_mark=1 \
  --sysctl net.ipv4.ip_forward=1 \
  --sysctl net.ipv6.conf.all.forwarding=1 \
  -p ${port}:${port}/udp \
  -v ${configPath}:/etc/amnezia/amneziawg \
  -v /lib/modules:/lib/modules:ro \
  --device /dev/net/tun:/dev/net/tun \
  ${container.image}`;
    
    try {
        const { stdout: containerId } = await execAsync(dockerCmd);
        logger.info(`[AWGInstaller] Контейнер ${container.name} создан: ${containerId.trim()}`);
        
        // Ждём 3 секунды для инициализации
        await new Promise(resolve => setTimeout(resolve, 3000));
        
        // Проверяем статус контейнера
        const { stdout: status } = await execAsync(`docker ps -a --filter name=^${container.name}$ --format "{{.Status}}"`);
        logger.info(`[AWGInstaller] Статус контейнера: ${status.trim()}`);
        
        if (!status.includes('Up')) {
            // Получаем логи контейнера для диагностики
            try {
                const { stdout: logs } = await execAsync(`docker logs ${container.name} 2>&1`);
                logger.error(`[AWGInstaller] Логи контейнера:\n${logs}`);
            } catch (logError) {
                logger.error(`[AWGInstaller] Не удалось получить логи: ${logError.message}`);
            }
            throw new Error(`Контейнер не запустился. Статус: ${status.trim()}`);
        }
        
        logger.info(`[AWGInstaller] Контейнер ${container.name} работает`);
    } catch (error) {

/**
 * Настройка сети и запуск AWG интерфейса
 * @param {string} version - Версия сервера (v1 или v2)
 * @param {string} containerName - Имя контейнера
 * @returns {Promise<void>}
 */
async function configureNetworkAndStartInterface(version, containerName) {
    logger.info(`[AWGInstaller] Настройка сети для ${containerName}...`);
    
    const interfaceName = version === 'v2' ? 'awg0' : 'wg0';
    
    try {
        // Шаг 1: Запускаем AWG интерфейс
        logger.info(`[AWGInstaller] Запуск интерфейса ${interfaceName}...`);
        try {
            await execAsync(`docker exec ${containerName} wg-quick up ${interfaceName}`);
            logger.info(`[AWGInstaller] ✅ Интерфейс ${interfaceName} запущен`);
        } catch (error) {
            // Интерфейс может быть уже запущен
            if (error.message.includes('already exists')) {
                logger.info(`[AWGInstaller] ℹ️ Интерфейс ${interfaceName} уже запущен`);
            } else {
                logger.warn(`[AWGInstaller] ⚠️ Ошибка запуска интерфейса: ${error.message}`);
            }
        }
        
        // Шаг 2: Настраиваем NAT (MASQUERADE)
        logger.info(`[AWGInstaller] Настройка NAT правил...`);
        
        // Добавляем MASQUERADE для исходящего трафика
        try {
            await execAsync(
                `docker exec ${containerName} iptables -t nat -A POSTROUTING -s 10.8.1.0/24 -o eth0 -j MASQUERADE`
            );
            logger.info(`[AWGInstaller] ✅ NAT MASQUERADE настроен`);
        } catch (error) {
            logger.warn(`[AWGInstaller] ⚠️ Ошибка настройки MASQUERADE: ${error.message}`);
        }
        
        // Добавляем правила FORWARD
        try {
            await execAsync(
                `docker exec ${containerName} iptables -A FORWARD -i ${interfaceName} -j ACCEPT`
            );
            await execAsync(
                `docker exec ${containerName} iptables -A FORWARD -o ${interfaceName} -j ACCEPT`
            );
            logger.info(`[AWGInstaller] ✅ FORWARD правила настроены`);
        } catch (error) {
            logger.warn(`[AWGInstaller] ⚠️ Ошибка настройки FORWARD: ${error.message}`);
        }
        
        // Шаг 3: Проверяем статус интерфейса
        try {
            const { stdout: wgStatus } = await execAsync(`docker exec ${containerName} wg show ${interfaceName}`);
            logger.info(`[AWGInstaller] Статус интерфейса:\n${wgStatus}`);
        } catch (error) {
            logger.warn(`[AWGInstaller] ⚠️ Не удалось получить статус интерфейса: ${error.message}`);
        }
        
        logger.info(`[AWGInstaller] ✅ Сеть успешно настроена`);
        
    } catch (error) {
        logger.error(`[AWGInstaller] Ошибка настройки сети: ${error.message}`);
        throw new Error(`Не удалось настроить сеть: ${error.message}`);
    }
}
        logger.error(`[AWGInstaller] Ошибка запуска контейнера: ${error.message}`);
        throw new Error(`Не удалось запустить контейнер: ${error.message}`);
    }
}

/**
 * Установка сервера
 * @param {string} version - Версия сервера (v1 или v2)
 * @param {number} port - Порт сервера
 * @param {Function} progressCallback - Callback для обновления прогресса
 * @returns {Promise<Object>} Результат установки
 */
async function installServer(version, port, progressCallback = () => {}) {
    const container = CONTAINERS[version];
    const configPath = container.configPath;
    
    logger.info(`[AWGInstaller] Начало установки ${version} на порту ${port}`);
    
    try {
        // ШАГ 0: Проверка системных требований
        progressCallback('🔍 Проверка системы...');
        const sysCheck = await checkSystemRequirements();
        if (!sysCheck.success) {
            throw new Error(`Системные требования не выполнены: ${sysCheck.error}`);
        }
        
        // ШАГ 1: Проверка каталога /opt/amnezia
        progressCallback('📁 Проверка каталога /opt/amnezia...');
        const dirCheck = await checkAmneziaDirectory();
        if (!dirCheck.canInstall) {
            throw new Error(`Проблема с каталогом /opt/amnezia: ${dirCheck.error}`);
        }
        
        // ШАГ 2: Проверка согласованности данных
        progressCallback('🔍 Проверка существующей установки...');
        const consistency = await checkDataConsistency(version);
        if (!consistency.consistent) {
            progressCallback('🧹 Очистка несогласованных данных...');
            await cleanupInconsistentData(version);
        }
        
        // ШАГ 3: Проверка порта
        progressCallback('🔌 Проверка порта...');
        const portCheck = await checkPortAvailability(port);
        if (!portCheck.available) {
            throw new Error(`Порт ${port} недоступен: ${portCheck.reason}`);
        }
        
        // ШАГ 4: Проверка и импорт Docker образа
        progressCallback('🐳 Проверка Docker образа...');
        const imageCheck = await checkDockerImageExists(version);
        
        if (!imageCheck.exists) {
            progressCallback('📦 Импортирую Docker образ...');
            try {
                await importDockerImage(version);
                progressCallback('✅ Образ импортирован');
            } catch (importError) {
                const fileName = version === 'v1' ? 'users.db' : 'settings.db';
                throw new Error(
                    `Не удалось импортировать образ.\n` +
                    `Убедитесь что файл ${fileName} существует в корне проекта.\n` +
                    `Ошибка: ${importError.message}`
                );
            }
        }
        
        // ШАГ 5: Создание директорий
        progressCallback('⏳ Создаю директории...');
        await execAsync(`mkdir -p ${configPath}`);
        logger.info(`[AWGInstaller] Директория ${configPath} создана`);
        
        // ШАГ 6: Генерация ключей
        progressCallback('⏳ Генерирую ключи сервера...');
        const keys = await generateServerKeys();
        
        // ШАГ 7: Создание конфигурации
        progressCallback('⏳ Создаю конфигурацию...');
        await createServerConfig(version, port, keys, configPath);
        
        // ШАГ 8: Используем проверенный образ
        const imageToUse = container.image;
        container.image = imageToUse;
        
        // ШАГ 9: Запуск контейнера
        progressCallback('⏳ Запускаю контейнер...');
        await startContainer(version, port, configPath);
        
        // ШАГ 10: Настройка сети и запуск интерфейса
        progressCallback('⏳ Настройка сети и запуск интерфейса...');
        await configureNetworkAndStartInterface(version, container.name);
        
        progressCallback('✅ Установка завершена!');
        
        logger.info(`[AWGInstaller] Установка ${version} успешно завершена`);
        
        return {
            success: true,
            containerName: container.name,
            port,
            configPath,
            publicKey: keys.publicKey,
            presharedKey: keys.presharedKey
        };
    } catch (error) {
        logger.error(`[AWGInstaller] Ошибка установки ${version}: ${error.message}`);
        
        // Пытаемся откатить изменения
        try {
            await removeServer(version);
        } catch (rollbackError) {
            logger.error(`[AWGInstaller] Ошибка отката: ${rollbackError.message}`);
        }
        
        return {
            success: false,
            error: error.message
        };
    }
}

/**
 * Установка обоих серверов
 * @param {number} portV1 - Порт для v1
 * @param {number} portV2 - Порт для v2
 * @param {Function} progressCallback - Callback для обновления прогресса
 * @returns {Promise<Object>} Результат установки
 */
async function installBothServers(portV1, portV2, progressCallback = () => {}) {
    logger.info(`[AWGInstaller] Установка обоих серверов: v1 (${portV1}), v2 (${portV2})`);
    
    const results = {
        v1: null,
        v2: null
    };
    
    // Устанавливаем v1
    progressCallback('📦 Установка AWG v1...');
    results.v1 = await installServer('v1', portV1, (msg) => {
        progressCallback(`[v1] ${msg}`);
    });
    
    if (!results.v1.success) {
        return {
            success: false,
            error: `Ошибка установки v1: ${results.v1.error}`,
            results
        };
    }
    
    // Устанавливаем v2
    progressCallback('📦 Установка AWG v2...');
    results.v2 = await installServer('v2', portV2, (msg) => {
        progressCallback(`[v2] ${msg}`);
    });
    
    if (!results.v2.success) {
        return {
            success: false,
            error: `Ошибка установки v2: ${results.v2.error}`,
            results
        };
    }
    
    progressCallback('✅ Оба сервера установлены!');
    
    return {
        success: true,
        results
    };
}

export {
    checkInstalledServers,
    getServerInfo,
    countClients,
    removeServer,
    generateServerKeys,
    createServerConfig,
    startContainer,
    configureNetworkAndStartInterface,
    installServer,
    installBothServers,
    CONTAINERS,
    // Новые функции проверки
    checkSystemRequirements,
    checkAmneziaDirectory,
    checkPortAvailability,
    checkDockerImageExists,
    importDockerImage,
    checkDataConsistency,
    cleanupInconsistentData
};

// Made with Bob
