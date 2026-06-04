import { exec } from 'child_process';
import { promisify } from 'util';
import fs from 'fs';
import path from 'path';
import { config } from './config.js';
import { logger } from './logger.js';

const execAsync = promisify(exec);

/**
 * AWG Manager - управление AmneziaWG контейнерами
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
    
    // Получаем внешний IP сервера
    try {
      const { stdout } = await execAsync('curl -s ifconfig.me');
      this.serverIP = stdout.trim();
      logger.info(`Server IP: ${this.serverIP}`);
    } catch (error) {
      logger.error('Failed to get server IP:', error);
      this.serverIP = '0.0.0.0';
    }

    // Ищем запущенные контейнеры AWG
    try {
      const { stdout } = await execAsync('docker ps --filter "name=amnezia" --format "{{.Names}}"');
      const containerNames = stdout.trim().split('\n').filter(name => name);
      
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
    // Ищем конфиг файл
    const possibleConfigs = ['awg0.conf', 'wg0.conf'];
    let configPath = null;
    let configContent = null;

    for (const confFile of possibleConfigs) {
      try {
        const { stdout } = await execAsync(
          `docker exec ${containerName} cat /opt/amnezia/awg/${confFile}`
        );
        configPath = `/opt/amnezia/awg/${confFile}`;
        configContent = stdout;
        break;
      } catch (error) {
        continue;
      }
    }

    if (!configContent) {
      throw new Error('Config file not found');
    }

    // Парсим конфиг
    const parsedConfig = this.parseAwgConfig(configContent);
    
    // Определяем версию
    const version = this.detectVersion(parsedConfig);
    
    // Получаем порт
    const portMatch = configContent.match(/ListenPort\s*=\s*(\d+)/);
    const port = portMatch ? portMatch[1] : '51820';

    // Получаем ключи из файлов
    let serverPublicKey, presharedKey;
    
    try {
      const { stdout: pubKey } = await execAsync(
        `docker exec ${containerName} cat /opt/amnezia/awg/wireguard_server_public_key.key`
      );
      serverPublicKey = pubKey.trim();
    } catch (error) {
      logger.warn(`Failed to read public key for ${containerName}`);
    }

    try {
      const { stdout: psk } = await execAsync(
        `docker exec ${containerName} cat /opt/amnezia/awg/wireguard_psk.key`
      );
      presharedKey = psk.trim();
    } catch (error) {
      logger.warn(`Failed to read PSK for ${containerName}`);
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
      
      const match = trimmed.match(/^([^=]+)=(.*)$/);
      if (match && currentSection) {
        const key = match[1].trim();
        const value = match[2].trim();
        
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
   * Определить версию AWG
   */
  detectVersion(parsedConfig) {
    // v2 имеет параметры S3, S4 или H-параметры с диапазонами
    if (parsedConfig.interface.S3 || parsedConfig.interface.S4) {
      return 'v2';
    }
    
    if (parsedConfig.interface.H1 && parsedConfig.interface.H1.includes('-')) {
      return 'v2';
    }
    
    return 'v1';
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
   */
  async checkContainer(containerName) {
    try {
      const { stdout } = await execAsync(`docker ps --filter "name=${containerName}" --format "{{.Status}}"`);
      return stdout.includes('Up');
    } catch (error) {
      logger.error(`Error checking container ${containerName}:`, error);
      return false;
    }
  }

  /**
   * Получить следующий свободный IP
   */
  async getNextIP(container) {
    try {
      const { stdout } = await execAsync(
        `docker exec ${container.name} cat ${container.configPath}`
      );

      // Найти все IP из AllowedIPs
      const ipMatches = stdout.matchAll(/AllowedIPs\s*=\s*(\d+\.\d+\.\d+\.\d+)\/32/g);
      const ips = Array.from(ipMatches, m => m[1]);

      if (ips.length === 0) {
        return '10.8.1.1';
      }

      // Найти максимальный последний октет
      const lastOctets = ips.map(ip => parseInt(ip.split('.')[3]));
      const maxOctet = Math.max(...lastOctets);

      if (maxOctet >= 254) {
        throw new Error('No free IPs in pool (10.8.1.1-254)');
      }

      return `10.8.1.${maxOctet + 1}`;
    } catch (error) {
      logger.error(`Error getting next IP for ${container.name}:`, error);
      throw error;
    }
  }

  /**
   * Сгенерировать пару ключей WireGuard
   */
  async generateKeys() {
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
        
        if (!this.availableContainers.length) {
          throw new Error('No AWG containers available and wg not installed on host');
        }
        
        const container = this.availableContainers[0];
        const { stdout: privateKey } = await execAsync(
          `docker exec ${container.name} wg genkey`
        );
        const privKey = privateKey.trim();
        
        const { stdout: publicKey } = await execAsync(
          `docker exec ${container.name} sh -c "echo '${privKey}' | wg pubkey"`
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
   */
  async addPeer(container, publicKey, ip) {
    const peerConfig = `
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
      await execAsync(`docker restart ${container.name}`);

      logger.info(`Added peer to ${container.name}: ${ip} (${publicKey})`);
      return true;
    } catch (error) {
      logger.error(`Error adding peer to ${container.name}:`, error);
      throw error;
    }
  }

  /**
   * Создать клиентский конфиг
   */
  createClientConfig(container, privateKey, ip) {
    const params = container.params;
    let configContent = `[Interface]
Address = ${ip}/32
DNS = 1.1.1.1, 1.0.0.1
PrivateKey = ${privateKey}
`;

    // Добавляем параметры обфускации если есть
    if (params.Jc) configContent += `Jc = ${params.Jc}\n`;
    if (params.Jmin) configContent += `Jmin = ${params.Jmin}\n`;
    if (params.Jmax) configContent += `Jmax = ${params.Jmax}\n`;
    if (params.S1) configContent += `S1 = ${params.S1}\n`;
    if (params.S2) configContent += `S2 = ${params.S2}\n`;
    
    // Для v1 добавляем только S1, S2 и фиксированные H
    if (container.version === 'v1') {
      if (params.H1) configContent += `H1 = ${params.H1}\n`;
      if (params.H2) configContent += `H2 = ${params.H2}\n`;
      if (params.H3) configContent += `H3 = ${params.H3}\n`;
      if (params.H4) configContent += `H4 = ${params.H4}\n`;
    }
    // Для v2 добавляем S3, S4 и диапазоны H
    else if (container.version === 'v2') {
      if (params.S3) configContent += `S3 = ${params.S3}\n`;
      if (params.S4) configContent += `S4 = ${params.S4}\n`;
      if (params.H1) configContent += `H1 = ${params.H1}\n`;
      if (params.H2) configContent += `H2 = ${params.H2}\n`;
      if (params.H3) configContent += `H3 = ${params.H3}\n`;
      if (params.H4) configContent += `H4 = ${params.H4}\n`;
      
      // Добавляем I параметры для маскировки
      // Если есть в серверном конфиге - используем их, иначе дефолтные
      const defaultI1 = '<b 0x084481800001000300000000077469636b65747306776964676574096b696e6f706f69736b0272750000010001c00c0005000100000039001806776964676574077469636b6574730679616e646578c025c0390005000100000039002b1765787465726e616c2d7469636b6574732d776964676574066166697368610679616e646578036e657400c05d000100010000001c000457fafe25>';
      
      configContent += `I1 = ${params.I1 || defaultI1}\n`;
      configContent += `I2 = ${params.I2 || ''}\n`;
      configContent += `I3 = ${params.I3 || ''}\n`;
      configContent += `I4 = ${params.I4 || ''}\n`;
      configContent += `I5 = ${params.I5 || ''}\n`;
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
  async generateClientConfig(version = null, vpsLabel = null) {
    // Инициализируем если еще не сделали
    if (!this.initialized) {
      await this.initialize();
    }

    // Получаем контейнер
    const container = this.getContainer(version);
    logger.info(`Generating ${container.version} config using ${container.name}${vpsLabel ? ` with label: ${vpsLabel}` : ''}`);

    // Проверяем контейнер
    const isRunning = await this.checkContainer(container.name);
    if (!isRunning) {
      throw new Error(`Container ${container.name} is not running`);
    }

    // Генерируем ключи
    const keys = await this.generateKeys();

    // Получаем следующий свободный IP
    const ip = await this.getNextIP(container);

    // Добавляем пира на сервер
    await this.addPeer(container, keys.publicKey, ip);

    // Создаем клиентский конфиг
    const configContent = this.createClientConfig(container, keys.privateKey, ip);

    // Сохраняем конфиг в файл с меткой VPS если указана
    let filename;
    if (vpsLabel) {
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
      containerName: container.name
    };
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
        const isRunning = await this.checkContainer(container.name);
        
        let clients = 0;
        if (isRunning) {
          try {
            const { stdout } = await execAsync(
              `docker exec ${container.name} grep -c "\\[Peer\\]" ${container.configPath} || echo 0`
            );
            clients = parseInt(stdout.trim()) || 0;
          } catch (error) {
            logger.warn(`Failed to count clients for ${container.name}`);
          }
        }

        stats.push({
          name: container.name,
          version: container.version,
          port: container.port,
          endpoint: container.endpoint,
          running: isRunning,
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
      if (vpsLabel) {
        filename = `${vpsLabel}_AWG${container.version}_${clientIP.replace(/\./g, '_')}_RESENT.conf`;
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
   * Получить список доступных версий
   */
  getAvailableVersions() {
    return this.availableContainers.map(c => ({
      version: c.version,
      name: c.name,
      port: c.port
    }));
  }
}

// Made with Bob