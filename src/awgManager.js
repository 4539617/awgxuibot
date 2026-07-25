import { exec } from 'child_process';
import { promisify } from 'util';
import fs from 'fs';
import path from 'path';
import { config } from './config.js';
import { logger } from './logger.js';
import { detectAwgVersion, validateAwgConfig } from './awg/validator.js';
import { generate } from './awg/generator.js';

const execAsync = promisify(exec);

/**
 * AWG Manager - управление AWG контейнерами
 * Автоматически определяет доступные контейнеры и их параметры
 */
export class AWGManager {
  constructor() {
    this.availableContainers = [];
    this.serverIP = null;
    this.initialized = false;
  }

  /**
   * Инициализация - определение доступных контейнеров
   */
  async initialize() {
    if (this.initialized) return;
    
    logger.info('Initializing AWG Manager...');
    
    // Получаем внешний IP сервера (только IPv4)
    try {
      const { stdout } = await execAsync('curl -4 -s ifconfig.me');
      this.serverIP = stdout.trim();
      logger.info(`Server IP: ${this.serverIP}`);
    } catch (error) {
      logger.error('Failed to get server IP:', error);
      this.serverIP = '0.0.0.0';
    }

    // Ищем все контейнеры AWG (запущенные и остановленные)
    try {
      const { stdout } = await execAsync('docker ps -a --filter "name=amnezia" --format "{{.Names}}"');
      const containerNames = stdout.trim().split('\n').filter(name => name);
      
      logger.info(`Found containers: ${containerNames.join(', ') || 'none'}`);
      
      for (const containerName of containerNames) {
        try {
          const containerInfo = await this.detectContainerConfig(containerName);
          if (containerInfo) {
            this.availableContainers.push(containerInfo);
            logger.info(`Detected container: ${containerName} (${containerInfo.version})`);
          }
        } catch (error) {
          logger.warn(`Failed to detect config for ${containerName}:`, error.message);
        }
      }
      
      logger.info(`Found ${this.availableContainers.length} AWG container(s)`);
      this.initialized = true;
    } catch (error) {
      logger.error('Failed to detect AWG containers:', error);
    }
  }

  /**
   * Определить конфигурацию контейнера
   */
  async detectContainerConfig(containerName) {
    // Проверяем статус контейнера
    const containerStatus = await this.checkContainer(containerName);
    const isRunning = containerStatus.running;
    
    // Ищем конфиг файл в разных возможных путях
    const possiblePaths = [
      { dir: '/opt/amnezia/amneziawg', files: ['awg0.conf', 'wg0.conf'] },  // Новый путь
      { dir: '/opt/amnezia/awg', files: ['awg0.conf', 'wg0.conf'] },        // Старый путь (для совместимости)
      { dir: '/etc/amnezia/amneziawg', files: ['awg0.conf', 'wg0.conf'] }   // Альтернативный путь
    ];
    
    let configPath = null;
    let configContent = null;

    for (const pathInfo of possiblePaths) {
      for (const confFile of pathInfo.files) {
        try {
          const fullPath = `${pathInfo.dir}/${confFile}`;
          
          if (isRunning) {
            // Для запущенного контейнера используем docker exec
            const { stdout } = await execAsync(
              `docker exec ${containerName} cat ${fullPath}`
            );
            configContent = stdout;
          } else {
            // Для остановленного контейнера используем docker cp
            const tempFile = `/tmp/${containerName}_${confFile}`;
            try {
              await execAsync(`docker cp ${containerName}:${fullPath} ${tempFile} 2>&1`);
              const { stdout } = await execAsync(`cat ${tempFile}`);
              configContent = stdout;
              await execAsync(`rm -f ${tempFile}`);
            } catch (cpError) {
              logger.debug(`Failed to copy ${fullPath} from stopped container: ${cpError.message}`);
              continue;
            }
          }
          
          configPath = fullPath;
          logger.info(`Found config at ${fullPath} for ${containerName} (${isRunning ? 'running' : 'stopped'})`);
          break;
        } catch (error) {
          continue;
        }
      }
      if (configContent) break;
    }

    if (!configContent) {
      throw new Error('Config file not found in any known location');
    }

    // Парсим конфиг
    const parsedConfig = this.parseAwgConfig(configContent);
    
    // Определяем версию и валидируем
    const validation = validateAwgConfig(parsedConfig);
    const version = validation.version;
    
    if (!validation.valid) {
      logger.warn(`⚠️ Конфиг ${containerName} содержит ошибки валидации:`);
      validation.errors.forEach(err => logger.warn(`   - ${err}`));
    }
    
    // Получаем порт
    const portMatch = configContent.match(/ListenPort\s*=\s*(\d+)/);
    const port = portMatch ? portMatch[1] : '51820';

    // Получаем ключи из файлов
    let serverPublicKey, presharedKey;
    
    // Пробуем разные пути для ключей
    const keyPaths = [
      '/opt/amnezia/awg',
      '/opt/amnezia/amneziawg',
      '/etc/amnezia/amneziawg'
    ];
    
    for (const keyPath of keyPaths) {
      try {
        let pubKey;
        if (isRunning) {
          const { stdout } = await execAsync(
            `docker exec ${containerName} cat ${keyPath}/wireguard_server_public_key.key`
          );
          pubKey = stdout;
        } else {
          const tempFile = `/tmp/${containerName}_pubkey.key`;
          await execAsync(`docker cp ${containerName}:${keyPath}/wireguard_server_public_key.key ${tempFile}`);
          const { stdout } = await execAsync(`cat ${tempFile}`);
          pubKey = stdout;
          await execAsync(`rm -f ${tempFile}`);
        }
        serverPublicKey = pubKey.trim();
        logger.info(`Found public key at ${keyPath} for ${containerName}`);
        break;
      } catch (error) {
        continue;
      }
    }
    
    for (const keyPath of keyPaths) {
      try {
        let psk;
        if (isRunning) {
          const { stdout } = await execAsync(
            `docker exec ${containerName} cat ${keyPath}/wireguard_psk.key`
          );
          psk = stdout;
        } else {
          const tempFile = `/tmp/${containerName}_psk.key`;
          await execAsync(`docker cp ${containerName}:${keyPath}/wireguard_psk.key ${tempFile}`);
          const { stdout } = await execAsync(`cat ${tempFile}`);
          psk = stdout;
          await execAsync(`rm -f ${tempFile}`);
        }
        presharedKey = psk.trim();
        logger.info(`Found PSK at ${keyPath} for ${containerName}`);
        break;
      } catch (error) {
        continue;
      }
    }

    // Проверяем что ключи найдены
    if (!serverPublicKey || !presharedKey) {
      throw new Error(`Failed to read keys for ${containerName}. Public key: ${serverPublicKey ? 'found' : 'missing'}, PSK: ${presharedKey ? 'found' : 'missing'}`);
    }

    // КРИТИЧЕСКАЯ ПРОВЕРКА: Получаем актуальный PublicKey из запущенного интерфейса
    // Основано на amneziawg-installer v5.16.1
    // Только для запущенных контейнеров
    if (isRunning) {
      try {
        const interfaceName = configPath.includes('awg0') ? 'awg0' : 'wg0';
        // Для AWG v2 (awg0) используем бинарник awg, для WG v1 (wg0) — wg
        const wgBin = interfaceName === 'awg0' ? 'awg' : 'wg';
        const { stdout: wgShow } = await execAsync(
          `docker exec ${containerName} ${wgBin} show ${interfaceName} public-key 2>/dev/null || echo ""`
        );
        const actualPublicKey = wgShow.trim();
        
        if (actualPublicKey && actualPublicKey !== serverPublicKey) {
          logger.warn(`⚠️  PublicKey mismatch обнаружен для ${containerName}!`);
          logger.warn(`   Ключ из файла: ${serverPublicKey}`);
          logger.warn(`   Реальный ключ:  ${actualPublicKey}`);
          logger.info(`✅ Используем реальный ключ из запущенного интерфейса`);
          serverPublicKey = actualPublicKey;
        } else if (actualPublicKey) {
          logger.info(`✅ PublicKey проверен и совпадает: ${actualPublicKey.substring(0, 16)}...`);
        } else {
          logger.warn(`⚠️  Не удалось получить PublicKey из интерфейса, используем ключ из файла`);
        }
      } catch (error) {
        logger.warn(`⚠️  Ошибка проверки PublicKey из интерфейса: ${error.message}`);
        logger.warn(`   Используем ключ из файла (может быть устаревшим)`);
      }
    } else {
      logger.info(`ℹ️  Контейнер остановлен, используем ключ из файла`);
    }

    return {
      name: containerName,
      version,
      port,
      endpoint: `${this.serverIP}:${port}`,
      configPath,
      serverPublicKey,
      presharedKey,
      params: parsedConfig.interface
    };
  }

  /**
   * Парсинг AWG конфига
   */
  parseAwgConfig(content) {
    const lines = content.split('\n');
    const parsedConfig = {
      interface: {},
      peers: []
    };
    
    let currentSection = null;
    let currentPeer = null;
    
    for (const line of lines) {
      const trimmed = line.trim();
      
      if (!trimmed || trimmed.startsWith('#')) continue;
      
      if (trimmed === '[Interface]') {
        currentSection = 'interface';
        continue;
      } else if (trimmed === '[Peer]') {
        currentSection = 'peer';
        currentPeer = {};
        parsedConfig.peers.push(currentPeer);
        continue;
      }
      
      // Используем indexOf чтобы value мог содержать '=' (I-параметры, hex-строки)
      const eqIdx = trimmed.indexOf('=');
      if (eqIdx > 0 && currentSection) {
        const key = trimmed.slice(0, eqIdx).trim();
        const value = trimmed.slice(eqIdx + 1).trim();
        
        if (currentSection === 'interface') {
          parsedConfig.interface[key] = value;
        } else if (currentSection === 'peer' && currentPeer) {
          currentPeer[key] = value;
        }
      }
    }
    
    return parsedConfig;
  }

  /**
   * Определить версию AWG (используем функцию из validator)
   * @deprecated Используйте detectAwgVersion из awg/validator.js
   */
  detectVersion(parsedConfig) {
    return detectAwgVersion(parsedConfig);
  }

  /**
   * Получить контейнер по версии или первый доступный
   */
  getContainer(version = null) {
    if (!this.availableContainers.length) {
      throw new Error('No AWG containers available. Run initialize() first.');
    }

    if (version) {
      const container = this.availableContainers.find(c => c.version === version);
      if (!container) {
        throw new Error(`No container found for version ${version}`);
      }
      return container;
    }

    // Возвращаем первый доступный
    return this.availableContainers[0];
  }

  /**
   * Проверить доступность контейнера
   * Возвращает объект с детальным статусом
   */
  async checkContainer(containerName) {
    try {
      // Используем ^name$ для точного совпадения имени контейнера
      const { stdout } = await execAsync(`docker ps -a --filter "name=^${containerName}$" --format "{{.Status}}"`);
      const status = stdout.trim();
      
      // Если статус пустой, контейнер не найден
      if (!status) {
        logger.warn(`Container ${containerName} not found`);
        return {
          running: false,
          restarting: false,
          stopped: true,
          status: 'not found',
          available: false
        };
      }
      
      // Определяем состояние контейнера
      const isUp = status.includes('Up');
      const isRestarting = status.toLowerCase().includes('restarting');
      const isExited = status.toLowerCase().includes('exited');
      
      return {
        running: isUp && !isRestarting,
        restarting: isRestarting,
        stopped: isExited,
        status: status,
        available: isUp && !isRestarting
      };
    } catch (error) {
      logger.error(`Error checking container ${containerName}:`, error);
      return {
        running: false,
        restarting: false,
        stopped: true,
        status: 'unknown',
        available: false
      };
    }
  }

  /**
   * Получить следующий свободный IP
   */
  async getNextIP(container) {
    try {
      // Проверяем статус контейнера
      const containerStatus = await this.checkContainer(container.name);
      
      let configContent;
      if (containerStatus.available) {
        const { stdout } = await execAsync(
          `docker exec ${container.name} cat ${container.configPath}`
        );
        configContent = stdout;
      } else {
        // Для остановленного контейнера используем docker cp
        const tempFile = `/tmp/${container.name}_config_nextip.conf`;
        await execAsync(`docker cp ${container.name}:${container.configPath} ${tempFile}`);
        const { stdout } = await execAsync(`cat ${tempFile}`);
        configContent = stdout;
        await execAsync(`rm -f ${tempFile}`);
      }

      // Найти все IP из AllowedIPs
      const ipMatches = configContent.matchAll(/AllowedIPs\s*=\s*(\d+\.\d+\.\d+\.\d+)\/32/g);
      const ips = Array.from(ipMatches, m => m[1]);

      if (ips.length === 0) {
        // 10.8.1.1 зарезервирован для сервера, начинаем с .2
        return '10.8.1.2';
      }

      // Найти максимальный последний октет
      const lastOctets = ips.map(ip => parseInt(ip.split('.')[3]));
      const maxOctet = Math.max(...lastOctets);

      if (maxOctet >= 254) {
        throw new Error('No free IPs in pool (10.8.1.2-254)');
      }

      // Если следующий IP будет .1, пропускаем его
      const nextOctet = maxOctet + 1;
      if (nextOctet === 1) {
        return '10.8.1.2';
      }

      return `10.8.1.${nextOctet}`;
    } catch (error) {
      logger.error(`Error getting next IP for ${container.name}:`, error);
      throw error;
    }
  }

  /**
   * Получить список всех клиентов с их статусами (активен/неактивен)
   */
  async getClientsWithStatus(containerName, version) {
    try {
      const interfaceName = version === 'v2' ? 'awg0' : 'wg0';
      
      // Получаем список всех IP из конфигурации
      const container = this.availableContainers.find(c => c.name === containerName);
      if (!container) {
        throw new Error(`Container ${containerName} not found`);
      }

      // Проверяем статус контейнера
      const containerStatus = await this.checkContainer(containerName);
      
      let configContent;
      if (containerStatus.available) {
        const { stdout } = await execAsync(
          `docker exec ${containerName} cat ${container.configPath}`
        );
        configContent = stdout;
      } else {
        // Для остановленного контейнера используем docker cp
        const tempFile = `/tmp/${containerName}_config_status.conf`;
        await execAsync(`docker cp ${containerName}:${container.configPath} ${tempFile}`);
        const { stdout } = await execAsync(`cat ${tempFile}`);
        configContent = stdout;
        await execAsync(`rm -f ${tempFile}`);
      }

      // Извлекаем все IP из AllowedIPs
      const ipMatches = configContent.matchAll(/AllowedIPs\s*=\s*(\d+\.\d+\.\d+\.\d+)\/32/g);
      const allIPs = Array.from(ipMatches, m => m[1]);

      // Получаем статистику handshake через wg/awg show только если контейнер запущен
      const wgBin = interfaceName === 'awg0' ? 'awg' : 'wg';
      let wgShow = '';
      if (containerStatus.available) {
        const { stdout } = await execAsync(
          `docker exec ${containerName} ${wgBin} show ${interfaceName} 2>/dev/null || echo ""`
        );
        wgShow = stdout;
      }

      // Парсим wg show для получения информации о handshake
      const handshakeMap = new Map();
      const lines = wgShow.split('\n');
      let currentIP = null;

      for (const line of lines) {
        const ipMatch = line.match(/allowed ips:\s*([0-9.]+)\/32/);
        if (ipMatch) {
          currentIP = ipMatch[1];
          handshakeMap.set(currentIP, { hasHandshake: false, lastHandshake: null });
        } else if (currentIP && line.includes('latest handshake:')) {
          const timeMatch = line.match(/latest handshake:\s*(.+)/);
          if (timeMatch) {
            const handshakeTime = timeMatch[1].trim();
            // Если есть handshake (не пустой и не "0")
            if (handshakeTime && handshakeTime !== '0') {
              handshakeMap.get(currentIP).hasHandshake = true;
              handshakeMap.get(currentIP).lastHandshake = handshakeTime;
            }
          }
        }
      }

      // Формируем результат
      const clientsWithStatus = allIPs.map(ip => {
        const lastOctet = parseInt(ip.split('.')[3]);
        const handshakeInfo = handshakeMap.get(ip);
        
        return {
          ip,
          number: lastOctet,
          active: handshakeInfo ? handshakeInfo.hasHandshake : false,
          lastHandshake: handshakeInfo ? handshakeInfo.lastHandshake : null
        };
      });

      // Сортируем по номеру IP
      clientsWithStatus.sort((a, b) => a.number - b.number);

      return clientsWithStatus;
    } catch (error) {
      logger.error(`Error getting clients with status for ${containerName}:`, error);
      throw error;
    }
  }

  /**
   * Сгенерировать пару ключей WireGuard
   */
  async generateKeys(containerName = null) {
    try {
      // Пробуем использовать wg на хосте
      try {
        const { stdout: privateKey } = await execAsync('wg genkey');
        const privKey = privateKey.trim();
        const { stdout: publicKey } = await execAsync(`echo "${privKey}" | wg pubkey`);
        const pubKey = publicKey.trim();
        
        return {
          privateKey: privKey,
          publicKey: pubKey
        };
      } catch (hostError) {
        // Если на хосте нет wg, используем из контейнера
        logger.info('wg not found on host, using container...');
        
        // Если передан конкретный контейнер, используем его
        let targetContainer = containerName;
        
        // Если контейнер не указан, ищем любой доступный
        if (!targetContainer) {
          if (!this.availableContainers.length) {
            // Пробуем найти любой запущенный amnezia контейнер
            try {
              const { stdout } = await execAsync('docker ps --filter "name=amnezia" --format "{{.Names}}" | head -n 1');
              targetContainer = stdout.trim();
              if (!targetContainer) {
                throw new Error('No running AWG containers found');
              }
              logger.info(`Found running container: ${targetContainer}`);
            } catch (findError) {
              throw new Error('No AWG containers available and wg not installed on host');
            }
          } else {
            targetContainer = this.availableContainers[0].name;
          }
        }
        
        // Генерируем ключи через контейнер
        logger.info(`Generating keys using container: ${targetContainer}`);
        const { stdout: privateKey } = await execAsync(
          `docker exec ${targetContainer} wg genkey`
        );
        const privKey = privateKey.trim();
        
        const { stdout: publicKey } = await execAsync(
          `docker exec ${targetContainer} sh -c "echo '${privKey}' | wg pubkey"`
        );
        const pubKey = publicKey.trim();
        
        return {
          privateKey: privKey,
          publicKey: pubKey
        };
      }
    } catch (error) {
      logger.error('Error generating WireGuard keys:', error);
      throw new Error('Failed to generate keys. Install wireguard-tools or ensure AWG container is running.');
    }
  }

  /**
   * Добавить пира в конфиг контейнера
   * Теперь с проверкой здоровья после добавления
   */
  async addPeer(container, publicKey, ip, peerName = null) {
    // Формируем комментарий с именем пира и датой создания
    const timestamp = new Date().toISOString().replace('T', ' ').substring(0, 19);
    const comment = peerName
      ? `# Peer: ${peerName} | IP: ${ip} | Created: ${timestamp}`
      : `# IP: ${ip} | Created: ${timestamp}`;
    
    const peerConfig = `
${comment}
[Peer]
PublicKey = ${publicKey}
PresharedKey = ${container.presharedKey}
AllowedIPs = ${ip}/32
`;

    try {
      // Добавляем конфиг в файл
      await execAsync(
        `docker exec ${container.name} sh -c "echo '${peerConfig}' >> ${container.configPath}"`
      );

      // Перезапускаем контейнер
      logger.info(`Restarting container ${container.name} after adding peer...`);
      await execAsync(`docker restart ${container.name}`);

      logger.info(`Added peer to ${container.name}: ${ip} (${publicKey})`);
      
      // Проверяем здоровье сервера после перезапуска
      logger.info(`Checking server health after adding peer...`);
      const healthStatus = await this.checkServerHealthAfterChange(container.name, 15, 1000);
      
      if (!healthStatus.healthy) {
        logger.warn(`⚠️ Server health check shows issues after adding peer ${ip}`);
        logger.warn(`Container running: ${healthStatus.containerRunning}`);
        logger.warn(`Interface ready: ${healthStatus.interfaceReady}`);
      } else {
        logger.info(`✅ Server is healthy after adding peer ${ip}`);
      }
      
      return {
        success: true,
        healthStatus
      };
    } catch (error) {
      logger.error(`Error adding peer to ${container.name}:`, error);
      throw error;
    }
  }

  /**
   * Создать клиентский конфиг.
   *
   * Для AWG v2:
   *   H1-H4, S3/S4     — берём с сервера (ДОЛЖНЫ совпадать на обеих сторонах).
   *   Jc/Jmin/Jmax/S1/S2 — берём с сервера.
   *   I1-I5             — генерируем свежие через generator.js (CPS-мимикрия),
   *                       т.к. в серверном конфиге их обычно нет, и они не
   *                       должны совпадать — каждый клиент получает свой профиль.
   *
   * Для AWG v1: только Jc/Jmin/Jmax/S1/S2/H1-H4.
   */
  createClientConfig(container, privateKey, ip) {
    const params = container.params;
    // params хранит строки из парсера конфига; проверяем через has()
    const has = (v) => v !== undefined && v !== null && String(v).trim() !== '';

    let configContent = `[Interface]
Address = ${ip}/32
DNS = 1.1.1.1, 1.0.0.1
PrivateKey = ${privateKey}
`;

    // Junk параметры
    if (has(params.Jc)) configContent += `Jc = ${params.Jc}\n`;
    if (has(params.Jmin)) configContent += `Jmin = ${params.Jmin}\n`;
    if (has(params.Jmax)) configContent += `Jmax = ${params.Jmax}\n`;

    // S параметры
    if (has(params.S1)) configContent += `S1 = ${params.S1}\n`;
    if (has(params.S2)) configContent += `S2 = ${params.S2}\n`;

    if (container.version === 'v2') {
      // S3/S4 — с сервера
      if (has(params.S3)) configContent += `S3 = ${params.S3}\n`;
      if (has(params.S4)) configContent += `S4 = ${params.S4}\n`;

      // H1-H4 — ОБЯЗАТЕЛЬНО с сервера, клиент и сервер должны совпадать
      if (has(params.H1)) configContent += `H1 = ${params.H1}\n`;
      if (has(params.H2)) configContent += `H2 = ${params.H2}\n`;
      if (has(params.H3)) configContent += `H3 = ${params.H3}\n`;
      if (has(params.H4)) configContent += `H4 = ${params.H4}\n`;

      // I1-I5 — генерируем свежие (CPS-мимикрия трафика, уникальная на каждый конфиг)
      // Если на сервере есть I-параметры — используем их, иначе генерируем
      const hasServerI = has(params.I1) || has(params.I2);
      if (hasServerI) {
        // Серверные I-параметры присутствуют — берём как есть
        if (!params.removeI1 && has(params.I1)) configContent += `I1 = ${params.I1}\n`;
        if (has(params.I2)) configContent += `I2 = ${params.I2}\n`;
        if (has(params.I3)) configContent += `I3 = ${params.I3}\n`;
        if (has(params.I4)) configContent += `I4 = ${params.I4}\n`;
        if (has(params.I5)) configContent += `I5 = ${params.I5}\n`;
      } else {
        // Генерируем свежие I-параметры через полноценный генератор
        const fresh = generate({ profile: 'random', intensity: 'medium' });
        if (!params.removeI1) configContent += `I1 = ${fresh.I1}\n`;
        configContent += `I2 = ${fresh.I2}\n`;
        configContent += `I3 = ${fresh.I3}\n`;
        configContent += `I4 = ${fresh.I4}\n`;
        configContent += `I5 = ${fresh.I5}\n`;
        logger.debug(`Generated fresh CPS params for v2 client (profile: ${fresh.profile})`);
      }
    } else {
      // v1: фиксированные H-значения
      if (has(params.H1)) configContent += `H1 = ${params.H1}\n`;
      if (has(params.H2)) configContent += `H2 = ${params.H2}\n`;
      if (has(params.H3)) configContent += `H3 = ${params.H3}\n`;
      if (has(params.H4)) configContent += `H4 = ${params.H4}\n`;
    }

    configContent += `
[Peer]
PublicKey = ${container.serverPublicKey}
PresharedKey = ${container.presharedKey}
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = ${container.endpoint}
PersistentKeepalive = 25
`;

    return configContent;
  }

  /**
   * Сгенерировать новый клиентский конфиг
   */
  async generateClientConfig(version = null, vpsLabel = null, peerName = null) {
    // Инициализируем если еще не сделали
    if (!this.initialized) {
      await this.initialize();
    }

    // Получаем контейнер
    const container = this.getContainer(version);
    logger.info(`Generating ${container.version} config using ${container.name}${vpsLabel ? ` with label: ${vpsLabel}` : ''}${peerName ? ` for peer: ${peerName}` : ''}`);

    // Проверяем контейнер
    const containerStatus = await this.checkContainer(container.name);
    if (!containerStatus.available) {
      if (containerStatus.restarting) {
        throw new Error(`Container ${container.name} is restarting. Please wait...`);
      }
      throw new Error(`Container ${container.name} is not running`);
    }

    // Генерируем ключи, передаем имя контейнера
    const keys = await this.generateKeys(container.name);

    // Получаем следующий свободный IP
    const ip = await this.getNextIP(container);

    // Добавляем пира на сервер с именем (теперь возвращает healthStatus)
    const addPeerResult = await this.addPeer(container, keys.publicKey, ip, peerName);

    // Создаем клиентский конфиг
    const configContent = this.createClientConfig(container, keys.privateKey, ip);

    // Сохраняем конфиг в файл с меткой VPS если указана
    let filename;
    if (vpsLabel && vpsLabel.trim() !== '') {
      filename = `${vpsLabel}_AWG${container.version}_${ip.replace(/\./g, '_')}.conf`;
    } else {
      filename = `AWG${container.version}_${ip.replace(/\./g, '_')}.conf`;
    }
    
    const filepath = path.join(config.outputDir, filename);

    fs.writeFileSync(filepath, configContent, 'utf8');
    logger.info(`Saved config: ${filepath}`);

    return {
      filepath,
      filename,
      ip,
      publicKey: keys.publicKey,
      version: container.version,
      containerName: container.name,
      healthStatus: addPeerResult.healthStatus
    };
  }

  /**
   * Сгенерировать клиентский конфиг для конкретного IP номера
   * Если IP уже существует - вернуть существующий конфиг
   * Если нет - создать новый
   */
  async generateClientConfigByNumber(version, ipNumber, vpsLabel = null, peerName = null) {
    // Инициализируем если еще не сделали
    if (!this.initialized) {
      await this.initialize();
    }

    // Получаем контейнер
    const container = this.getContainer(version);
    const targetIP = `10.8.1.${ipNumber}`;
    
    logger.info(`Generating ${container.version} config for IP ${targetIP}${vpsLabel ? ` with label: ${vpsLabel}` : ''}${peerName ? ` with peer name: ${peerName}` : ''}`);

    // Проверяем контейнер
    const containerStatus = await this.checkContainer(container.name);
    if (!containerStatus.available) {
      if (containerStatus.restarting) {
        throw new Error(`Container ${container.name} is restarting. Please wait...`);
      }
      throw new Error(`Container ${container.name} is not running`);
    }

    // Проверяем, существует ли уже этот IP в конфигурации
    const { stdout: configContent } = await execAsync(
      `docker exec ${container.name} cat ${container.configPath}`
    );

    const ipExists = configContent.includes(`AllowedIPs = ${targetIP}/32`);

    if (ipExists) {
      // IP уже существует - восстанавливаем конфиг
      logger.info(`IP ${targetIP} already exists, regenerating config`);
      return await this.regenerateClientConfig(container.name, targetIP, vpsLabel);
    } else {
      // IP не существует - создаем новый
      logger.info(`IP ${targetIP} does not exist, creating new config`);
      
      // Генерируем ключи
      const keys = await this.generateKeys(container.name);

      // Добавляем пира на сервер с указанным IP и именем (теперь возвращает healthStatus)
      const addPeerResult = await this.addPeer(container, keys.publicKey, targetIP, peerName);

      // Создаем клиентский конфиг
      const configContent = this.createClientConfig(container, keys.privateKey, targetIP);

      // Сохраняем конфиг в файл с меткой VPS если указана
      let filename;
      if (vpsLabel && vpsLabel.trim() !== '') {
        filename = `${vpsLabel}_AWG${container.version}_${targetIP.replace(/\./g, '_')}.conf`;
      } else {
        filename = `AWG${container.version}_${targetIP.replace(/\./g, '_')}.conf`;
      }
      
      const filepath = path.join(config.outputDir, filename);

      fs.writeFileSync(filepath, configContent, 'utf8');
      logger.info(`Saved config: ${filepath}`);

      return {
        filepath,
        filename,
        ip: targetIP,
        publicKey: keys.publicKey,
        version: container.version,
        containerName: container.name,
        isNew: true,
        healthStatus: addPeerResult.healthStatus
      };
    }
  }

  /**
   * Получить статистику контейнеров
   */
  async getStats() {
    // Инициализируем если еще не сделали
    if (!this.initialized) {
      await this.initialize();
    }

    const stats = [];

    for (const container of this.availableContainers) {
      try {
        // Проверяем статус контейнера в реальном времени
        const containerStatus = await this.checkContainer(container.name);
        
        let clients = 0;
        let actualPort = container.port;
        
        if (containerStatus.available) {
          try {
            // Получаем количество клиентов
            const { stdout } = await execAsync(
              `docker exec ${container.name} grep -c "\\[Peer\\]" ${container.configPath} || echo 0`
            );
            clients = parseInt(stdout.trim()) || 0;
            
            // Проверяем актуальный порт из конфига
            try {
              const { stdout: configContent } = await execAsync(
                `docker exec ${container.name} cat ${container.configPath}`
              );
              const portMatch = configContent.match(/ListenPort\s*=\s*(\d+)/);
              if (portMatch) {
                actualPort = portMatch[1];
              }
            } catch (error) {
              logger.warn(`Failed to read port from config for ${container.name}`);
            }
          } catch (error) {
            logger.warn(`Failed to get details for ${container.name}: ${error.message}`);
          }
        }

        stats.push({
          name: container.name,
          version: container.version,
          port: actualPort,
          endpoint: `${this.serverIP}:${actualPort}`,
          running: containerStatus.running,
          restarting: containerStatus.restarting,
          stopped: containerStatus.stopped,
          status: containerStatus.status,
          clients
        });
      } catch (error) {
        logger.error(`Error getting stats for ${container.name}:`, error);
      }
    }

    return stats;
  }

  /**
   * Получить список клиентов
   */
  async getClients(containerName) {
    const container = this.availableContainers.find(c => c.name === containerName);
    if (!container) {
      throw new Error(`Container ${containerName} not found`);
    }
    
    // Проверяем статус контейнера перед попыткой чтения
    const containerStatus = await this.checkContainer(container.name);
    if (!containerStatus.available) {
      logger.info(`Container ${container.name} is not available, returning empty client list`);
      return [];
    }
    
    try {
      const { stdout } = await execAsync(
        `docker exec ${container.name} grep "AllowedIPs" ${container.configPath}`
      );

      const ips = [];
      const lines = stdout.split('\n');
      
      for (const line of lines) {
        const match = line.match(/AllowedIPs\s*=\s*(\d+\.\d+\.\d+\.\d+)\/32/);
        if (match) {
          ips.push(match[1]);
        }
      }

      return ips;
    } catch (error) {
      logger.error(`Error getting clients for ${container.name}:`, error);
      return [];
    }
  }

  /**
   * Получить имена пиров из комментариев в конфигурации
   */
  async getPeerNames(containerName) {
    const container = this.availableContainers.find(c => c.name === containerName);
    if (!container) {
      throw new Error(`Container ${containerName} not found`);
    }
    
    // Проверяем статус контейнера
    const containerStatus = await this.checkContainer(container.name);
    if (!containerStatus.available) {
      logger.info(`Container ${container.name} is not available, returning empty peer names`);
      return {};
    }
    
    try {
      // Читаем полную конфигурацию
      const { stdout: configContent } = await execAsync(
        `docker exec ${container.name} cat ${container.configPath}`
      );
      
      const peerNames = {};
      const lines = configContent.split('\n');
      
      let currentComment = null;
      
      for (let i = 0; i < lines.length; i++) {
        const line = lines[i].trim();
        
        // Ищем комментарий с именем пира
        // Формат: # Peer: <имя> | IP: <ip> | Created/Updated: <дата>
        const commentMatch = line.match(/^#\s*Peer:\s*(.+?)\s*\|\s*IP:\s*(\d+\.\d+\.\d+\.\d+)/);
        if (commentMatch) {
          const peerName = commentMatch[1].trim();
          const ip = commentMatch[2].trim();
          // Сохраняем комментарий для последующей связи с AllowedIPs
          currentComment = { name: peerName, ip: ip };
          continue;
        }
        
        // Также ищем старый формат без имени: # IP: <ip> | Created: <дата>
        const ipOnlyMatch = line.match(/^#\s*IP:\s*(\d+\.\d+\.\d+\.\d+)/);
        if (ipOnlyMatch) {
          const ip = ipOnlyMatch[1].trim();
          currentComment = { name: null, ip: ip };
          continue;
        }
        
        // Если нашли AllowedIPs и есть сохраненный комментарий
        const allowedIPsMatch = line.match(/^AllowedIPs\s*=\s*(\d+\.\d+\.\d+\.\d+)/);
        if (allowedIPsMatch && currentComment) {
          const actualIP = allowedIPsMatch[1].trim();
          // Всегда используем IP из AllowedIPs как правильный
          if (currentComment.name) {
            peerNames[actualIP] = currentComment.name;
          } else {
            peerNames[actualIP] = null;
          }
          currentComment = null;
        }
        
        // Сбрасываем комментарий при начале новой секции [Peer]
        if (line === '[Peer]') {
          // Не сбрасываем, чтобы связать с AllowedIPs
          continue;
        }
      }
      
      return peerNames;
    } catch (error) {
      logger.error(`Error getting peer names for ${container.name}:`, error);
      return {};
    }
  }

  /**
   * Переименовать пира (обновить комментарий в конфигурации)
   */
  async renamePeer(containerName, clientIP, newPeerName) {
    const container = this.availableContainers.find(c => c.name === containerName);
    if (!container) {
      throw new Error(`Container ${containerName} not found`);
    }
    
    logger.info(`Renaming peer ${clientIP} to "${newPeerName}" in ${containerName}`);
    
    try {
      // Получаем конфигурацию сервера из контейнера
      const { stdout: serverConfig } = await execAsync(
        `docker exec ${container.name} cat ${container.configPath}`
      );
      
      // Ищем секцию [Peer] для этого IP
      // Используем более точный regex, который не пересекает границы между peer секциями
      // [^\\[] означает "любой символ кроме [", что предотвращает захват следующей секции [Peer]
      const escapedIP = clientIP.replace(/\./g, '\\.');
      const peerRegex = new RegExp(
        `(#[^\\n]*\\n)?\\[Peer\\]\\n([^\\[]*)AllowedIPs\\s*=\\s*${escapedIP}\\/32`,
        'g'
      );
      
      const peerMatch = serverConfig.match(peerRegex);
      if (!peerMatch || peerMatch.length === 0) {
        throw new Error(`Client with IP ${clientIP} not found in server config`);
      }
      
      // Если найдено несколько совпадений, берем первое (не должно быть дубликатов IP)
      const oldPeerSection = peerMatch[0];
      
      // Извлекаем реальный IP из AllowedIPs (на случай если комментарий содержит неправильный IP)
      const allowedIPsMatch = oldPeerSection.match(/AllowedIPs\s*=\s*(\d+\.\d+\.\d+\.\d+)\/32/);
      const actualIP = allowedIPsMatch ? allowedIPsMatch[1] : clientIP;
      
      // Создаем новый комментарий с правильным IP из AllowedIPs
      const timestamp = new Date().toISOString().replace('T', ' ').substring(0, 19);
      const newComment = `# Peer: ${newPeerName} | IP: ${actualIP} | Updated: ${timestamp}`;
      
      // Удаляем все комментарии перед секцией [Peer]
      const peerSectionWithoutComment = oldPeerSection.replace(/^#[^\n]*\n/gm, '');
      
      // Создаем новую секцию с новым комментарием
      const newPeerSection = `${newComment}\n${peerSectionWithoutComment}`;
      
      // Заменяем в конфигурации
      const newConfig = serverConfig.replace(oldPeerSection, newPeerSection);
      
      // Сохраняем обновленную конфигурацию во временный файл
      const tempFile = `/tmp/${containerName}_rename_${Date.now()}.conf`;
      await execAsync(`echo '${newConfig.replace(/'/g, "'\\''")}' > ${tempFile}`);
      
      // Копируем обновленную конфигурацию в контейнер
      await execAsync(`docker cp ${tempFile} ${container.name}:${container.configPath}`);
      
      // Удаляем временный файл
      await execAsync(`rm -f ${tempFile}`);
      
      // Перезапускаем контейнер для применения изменений
      logger.info(`Restarting container ${container.name} after renaming peer...`);
      await execAsync(`docker restart ${container.name}`);
      
      logger.info(`Successfully renamed peer ${clientIP} to "${newPeerName}"`);
      
      // Проверяем здоровье сервера после перезапуска
      const healthStatus = await this.checkServerHealthAfterChange(container.name, 15, 1000);
      
      return {
        success: true,
        healthStatus
      };
      
    } catch (error) {
      logger.error(`Error renaming peer ${clientIP} in ${containerName}:`, error);
      throw error;
    }
  }

  /**
   * Восстановить конфигурацию клиента по IP
   */
  async regenerateClientConfig(containerName, clientIP, vpsLabel = null) {
    const container = this.availableContainers.find(c => c.name === containerName);
    if (!container) {
      throw new Error(`Container ${containerName} not found`);
    }
    
    logger.info(`Regenerating config for ${clientIP} from ${containerName}`);
    
    try {
      // Получаем конфигурацию сервера из контейнера
      const { stdout: serverConfig } = await execAsync(
        `docker exec ${container.name} cat ${container.configPath}`
      );
      
      // Ищем секцию [Peer] для этого IP
      const peerRegex = new RegExp(
        `\\[Peer\\][\\s\\S]*?AllowedIPs\\s*=\\s*${clientIP.replace(/\./g, '\\.')}\\/32[\\s\\S]*?(?=\\[Peer\\]|$)`,
        'g'
      );
      
      const peerMatch = serverConfig.match(peerRegex);
      if (!peerMatch || peerMatch.length === 0) {
        throw new Error(`Client with IP ${clientIP} not found in server config`);
      }
      
      const peerSection = peerMatch[0];
      
      // Извлекаем PublicKey и PresharedKey клиента
      const pubKeyMatch = peerSection.match(/PublicKey\s*=\s*(.+)/);
      const pskMatch = peerSection.match(/PresharedKey\s*=\s*(.+)/);
      
      if (!pubKeyMatch || !pskMatch) {
        throw new Error(`Failed to extract keys for ${clientIP}`);
      }
      
      const clientPublicKey = pubKeyMatch[1].trim();
      const presharedKey = pskMatch[1].trim();
      
      // Генерируем приватный ключ клиента из публичного невозможно,
      // поэтому нужно извлечь его из сохранённого конфига
      // Ищем файл конфигурации в output директории
      const outputDir = config.outputDir;
      const files = fs.readdirSync(outputDir);
      
      // Ищем файл с этим IP
      const ipPattern = clientIP.replace(/\./g, '_');
      const configFile = files.find(f => f.includes(ipPattern) && f.endsWith('.conf'));
      
      if (!configFile) {
        throw new Error(
          `Configuration file for ${clientIP} not found in ${outputDir}. ` +
          `Cannot regenerate without original private key. ` +
          `Please generate a new configuration instead.`
        );
      }
      
      // Читаем сохранённый конфиг
      const savedConfigPath = path.join(outputDir, configFile);
      const savedConfig = fs.readFileSync(savedConfigPath, 'utf8');
      
      // Извлекаем приватный ключ из сохранённого конфига
      const privKeyMatch = savedConfig.match(/PrivateKey\s*=\s*(.+)/);
      if (!privKeyMatch) {
        throw new Error(`Private key not found in saved config ${configFile}`);
      }
      
      const clientPrivateKey = privKeyMatch[1].trim();
      
      // Создаём клиентский конфиг
      const configContent = this.createClientConfig(container, clientPrivateKey, clientIP);
      
      // Сохраняем конфиг в файл с меткой VPS если указана
      let filename;
      if (vpsLabel && vpsLabel.trim() !== '') {
        filename = `${vpsLabel.toUpperCase()}_AWG${container.version}_${clientIP.replace(/\./g, '_')}_RESENT.conf`;
      } else {
        filename = `AWG${container.version}_${clientIP.replace(/\./g, '_')}_RESENT.conf`;
      }
      
      const filepath = path.join(outputDir, filename);
      fs.writeFileSync(filepath, configContent, 'utf8');
      
      logger.info(`Regenerated config saved: ${filepath}`);
      
      return {
        filepath,
        filename,
        ip: clientIP,
        publicKey: clientPublicKey,
        version: container.version,
        containerName: container.name
      };
      
    } catch (error) {
      logger.error(`Error regenerating config for ${clientIP}:`, error);
      throw error;
    }
  }

  /**
   * Полная проверка состояния сервера после изменений конфигурации
   * Возвращает детальную информацию о состоянии контейнера и интерфейса
   */
  async checkServerHealthAfterChange(containerName, maxAttempts = 15, delayMs = 1000) {
    logger.info(`Starting health check for ${containerName}...`);
    
    const container = this.availableContainers.find(c => c.name === containerName);
    if (!container) {
      throw new Error(`Container ${containerName} not found`);
    }
    
    const interfaceName = container.version === 'v2' ? 'awg0' : 'wg0';
    const healthStatus = {
      containerRunning: false,
      interfaceUp: false,
      interfaceReady: false,
      peerCount: 0,
      attempts: 0,
      errors: [],
      warnings: [],
      timestamp: new Date().toISOString()
    };
    
    for (let attempt = 1; attempt <= maxAttempts; attempt++) {
      healthStatus.attempts = attempt;
      
      try {
        // 1. Проверка состояния контейнера Docker
        const containerStatus = await this.checkContainer(containerName);
        healthStatus.containerRunning = containerStatus.running;
        
        if (!containerStatus.running) {
          healthStatus.warnings.push(`Container is not running (status: ${containerStatus.status})`);
          
          if (containerStatus.restarting) {
            logger.info(`Attempt ${attempt}/${maxAttempts}: Container is restarting...`);
            await new Promise(resolve => setTimeout(resolve, delayMs));
            continue;
          } else {
            healthStatus.errors.push('Container stopped unexpectedly');
            break;
          }
        }
        
        // 2. Проверка существования интерфейса WireGuard
        try {
          const { stdout: ifaceCheck } = await execAsync(
            `docker exec ${containerName} ip link show ${interfaceName} 2>&1`
          );
          
          if (ifaceCheck.includes('does not exist')) {
            healthStatus.warnings.push(`Interface ${interfaceName} does not exist yet`);
            logger.info(`Attempt ${attempt}/${maxAttempts}: Interface ${interfaceName} not ready...`);
            await new Promise(resolve => setTimeout(resolve, delayMs));
            continue;
          }
          
          healthStatus.interfaceUp = ifaceCheck.includes('state UP') || ifaceCheck.includes('UNKNOWN');
          
        } catch (error) {
          healthStatus.warnings.push(`Cannot check interface: ${error.message}`);
          logger.info(`Attempt ${attempt}/${maxAttempts}: Interface check failed...`);
          await new Promise(resolve => setTimeout(resolve, delayMs));
          continue;
        }
        
        // 3. Проверка состояния WireGuard через wg/awg show
        const wgBin = interfaceName === 'awg0' ? 'awg' : 'wg';
        try {
          const { stdout: wgOutput } = await execAsync(
            `docker exec ${containerName} ${wgBin} show ${interfaceName} 2>&1`
          );
          
          // Подсчитываем количество пиров
          const peerMatches = wgOutput.match(/peer:/g);
          healthStatus.peerCount = peerMatches ? peerMatches.length : 0;
          
          // Проверяем наличие listening port (признак готовности)
          const hasListeningPort = wgOutput.includes('listening port:');
          
          if (hasListeningPort) {
            healthStatus.interfaceReady = true;
            logger.info(`✅ Interface ${interfaceName} is ready with ${healthStatus.peerCount} peer(s)`);
            break; // Успешная проверка
          } else {
            healthStatus.warnings.push('Interface exists but no listening port yet');
            logger.info(`Attempt ${attempt}/${maxAttempts}: Interface starting...`);
          }
          
        } catch (error) {
          const errorMsg = error.message || error.toString();
          
          if (errorMsg.includes('does not exist') || errorMsg.includes('No such device')) {
            healthStatus.warnings.push(`Interface ${interfaceName} not created yet`);
            logger.info(`Attempt ${attempt}/${maxAttempts}: Waiting for interface...`);
          } else {
            healthStatus.errors.push(`WireGuard error: ${errorMsg}`);
            logger.error(`Interface error: ${errorMsg}`);
          }
        }
        
        // Ожидание перед следующей попыткой
        if (attempt < maxAttempts) {
          await new Promise(resolve => setTimeout(resolve, delayMs));
        }
        
      } catch (error) {
        healthStatus.errors.push(`Health check error: ${error.message}`);
        logger.error(`Health check attempt ${attempt} failed:`, error);
        
        if (attempt < maxAttempts) {
          await new Promise(resolve => setTimeout(resolve, delayMs));
        }
      }
    }
    
    // Финальная оценка состояния
    healthStatus.healthy = healthStatus.containerRunning &&
                          healthStatus.interfaceUp &&
                          healthStatus.interfaceReady;
    
    if (!healthStatus.healthy) {
      logger.warn(`⚠️ Health check completed with issues after ${healthStatus.attempts} attempts`);
      logger.warn(`Container running: ${healthStatus.containerRunning}`);
      logger.warn(`Interface up: ${healthStatus.interfaceUp}`);
      logger.warn(`Interface ready: ${healthStatus.interfaceReady}`);
    } else {
      logger.info(`✅ Health check passed: ${healthStatus.peerCount} peer(s) active`);
    }
    
    return healthStatus;
  }

  /**
   * Получить список доступных версий
   */
  getAvailableVersions() {
    return this.availableContainers.map(c => ({
      version: c.version,
      name: c.name,
      port: c.port
    }));
  }

  /**
   * Запустить контейнер AWG
   */
  async startContainer(version) {
    try {
      const containerName = version === 'v1' ? 'amnezia-awg' : 'amnezia-awg2';
      
      logger.info(`Starting container ${containerName}...`);
      
      // Используем checkContainer для точной проверки статуса
      const containerStatus = await this.checkContainer(containerName);
      
      if (containerStatus.status === 'not found') {
        throw new Error(`Container ${containerName} not found. AWG ${version} is not installed.`);
      }
      
      // Проверяем, запущен ли контейнер
      if (containerStatus.running) {
        logger.info(`Container ${containerName} is already running`);
        return {
          success: true,
          message: `AWG ${version} уже запущен`,
          alreadyRunning: true
        };
      }
      
      // Запускаем контейнер
      await execAsync(`docker start ${containerName}`);
      logger.info(`Container ${containerName} started successfully`);
      
      // Обновляем информацию о контейнерах
      await this.initialize();
      
      return {
        success: true,
        message: `AWG ${version} успешно запущен`,
        alreadyRunning: false
      };
    } catch (error) {
      logger.error(`Error starting container for ${version}:`, error);
      throw new Error(`Ошибка запуска AWG ${version}: ${error.message}`);
    }
  }

  /**
   * Остановить контейнер AWG
   */
  async stopContainer(version) {
    try {
      const containerName = version === 'v1' ? 'amnezia-awg' : 'amnezia-awg2';
      
      logger.info(`Stopping container ${containerName}...`);
      
      // Проверяем существование контейнера
      const { stdout: allContainers } = await execAsync('docker ps -a --format "{{.Names}}"');
      if (!allContainers.includes(containerName)) {
        throw new Error(`Container ${containerName} not found. AWG ${version} is not installed.`);
      }
      
      // Проверяем, запущен ли контейнер
      const { stdout: runningContainers } = await execAsync('docker ps --format "{{.Names}}"');
      if (!runningContainers.includes(containerName)) {
        logger.info(`Container ${containerName} is already stopped`);
        return {
          success: true,
          message: `AWG ${version} уже остановлен`,
          alreadyStopped: true
        };
      }
      
      // Останавливаем контейнер
      await execAsync(`docker stop ${containerName}`);
      logger.info(`Container ${containerName} stopped successfully`);
      
      // Обновляем информацию о контейнерах
      await this.initialize();
      
      return {
        success: true,
        message: `AWG ${version} успешно остановлен`,
        alreadyStopped: false
      };
    } catch (error) {
      logger.error(`Error stopping container for ${version}:`, error);
      throw new Error(`Ошибка остановки AWG ${version}: ${error.message}`);
    }
  }
}