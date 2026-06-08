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
        
        if (!stdout.trim()) {
            return {
                installed: false,
                containerName: container.name,
                port: null,
                clientCount: 0,
                configPath: null,
                status: 'not_found'
            };
        }
        
        const isRunning = stdout.includes('Up');
        
        // Получаем порт
        let port = null;
        try {
            const { stdout: portOutput } = await execAsync(`docker port ${container.name} 2>/dev/null || true`);
            const portMatch = portOutput.match(/0\.0\.0\.0:(\d+)/);
            if (portMatch) {
                port = parseInt(portMatch[1]);
            }
        } catch (error) {
            logger.warn(`[AWGInstaller] Не удалось получить порт для ${container.name}`);
        }
        
        // Подсчитываем клиентов
        const clientCount = await countClients(container.name);
        
        return {
            installed: true,
            containerName: container.name,
            port,
            clientCount,
            configPath: container.configPath,
            status: isRunning ? 'running' : 'stopped'
        };
    } catch (error) {
        logger.error(`[AWGInstaller] Ошибка получения информации о ${version}: ${error.message}`);
        return {
            installed: false,
            containerName: container.name,
            port: null,
            clientCount: 0,
            configPath: null,
            status: 'error'
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
    
    // Формируем конфигурацию в зависимости от версии
    let config = `[Interface]
PrivateKey = ${keys.privateKey}
Address = ${container.network}
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
        // Шаг 1: Создание директорий
        progressCallback('⏳ Создаю директории...');
        await execAsync(`mkdir -p ${configPath}`);
        logger.info(`[AWGInstaller] Директория ${configPath} создана`);
        
        // Шаг 2: Генерация ключей
        progressCallback('⏳ Генерирую ключи сервера...');
        const keys = await generateServerKeys();
        
        // Шаг 3: Создание конфигурации
        progressCallback('⏳ Создаю конфигурацию...');
        await createServerConfig(version, port, keys, configPath);
        
        // Шаг 4: Получение образа с fallback
        progressCallback('⏳ Проверяю образ Docker...');
        let imageToUse = container.image;
        
        try {
            // Пробуем публичный образ
            const { stdout } = await execAsync(`docker images -q ${container.image}`);
            if (stdout.trim()) {
                logger.info(`[AWGInstaller] Публичный образ ${container.image} найден локально`);
            } else {
                logger.info(`[AWGInstaller] Скачиваю публичный образ ${container.image}...`);
                await execAsync(`docker pull ${container.image}`);
                logger.info(`[AWGInstaller] Публичный образ ${container.image} скачан`);
            }
        } catch (error) {
            // Fallback на локальный образ
            logger.warn(`[AWGInstaller] Не удалось получить публичный образ: ${error.message}`);
            logger.info(`[AWGInstaller] Пробую локальный образ ${container.fallbackImage}...`);
            
            try {
                const { stdout } = await execAsync(`docker images -q ${container.fallbackImage}`);
                if (stdout.trim()) {
                    imageToUse = container.fallbackImage;
                    logger.info(`[AWGInstaller] Локальный образ ${container.fallbackImage} найден`);
                } else {
                    throw new Error(`Локальный образ ${container.fallbackImage} не найден`);
                }
            } catch (fallbackError) {
                throw new Error(
                    `Не удалось получить образ.\n` +
                    `Публичный образ: ${error.message}\n` +
                    `Локальный образ: ${fallbackError.message}\n\n` +
                    `Убедитесь что:\n` +
                    `1. Есть доступ к Docker Hub для ${container.image}\n` +
                    `2. Или создан локальный образ ${container.fallbackImage}`
                );
            }
        }
        
        // Обновляем образ для использования
        container.image = imageToUse;
        
        // Шаг 5: Запуск контейнера
        progressCallback('⏳ Запускаю контейнер...');
        await startContainer(version, port, configPath);
        
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
    installServer,
    installBothServers,
    CONTAINERS
};

// Made with Bob
