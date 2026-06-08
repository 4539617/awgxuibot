import TelegramBot from 'node-telegram-bot-api';
import { exec } from 'child_process';
import { promisify } from 'util';
import { config } from './config.js';
import { resolveDomain, resolveMultipleDomains } from './dnsResolver.js';
import {
  generateBatchFile,
  saveBatchFile,
  generateFilename,
  generateMultipleDomainsFilename
} from './fileGenerator.js';
import { AntiFlood } from './antiflood.js';

const execAsync = promisify(exec);
import { processBatFile } from './batFileProcessor.js';
import { processAwgConfig } from './awgConverter.js';
import { AWGManager } from './awgManager.js';
import { logger } from './logger.js';
import fs from 'fs';
import path from 'path';
import * as awgInstaller from './awgInstaller.js';
import * as portManager from './portManager.js';

export class RouteBot {
  constructor() {
    this.bot = new TelegramBot(config.telegramToken, { polling: true });
    // Anti-flood: max 5 requests per minute
    this.antiFlood = new AntiFlood(10, 60000);
    this.antiFlood.startCleanup();
    // AWG Manager
    this.awgManager = new AWGManager();
    // Install sessions storage
    this.installSessions = new Map();
    // VPS label sessions storage
    this.vpsLabelSessions = new Map();
    this.setupHandlers();
    logger.info('RouteBot initialized');
  }

  setupHandlers() {
    // Start command
    this.bot.onText(/\/start/, (msg) => {
      const chatId = msg.chat.id;
      const userId = msg.from.id;
      logger.info(`/start command received from chat ${chatId}`);
      
      let welcomeMessage = `
🚀 *Отправьте*
\`\`\`
rutube.ru
ozon.ru
google.com
\`\`\`
/start - Начало работы`;

      // Add admin command for administrators
      if (this.isAdmin(userId)) {
        welcomeMessage += `\n/admin - Панель администратора`;
      }
      
      this.bot.sendMessage(chatId, welcomeMessage, { parse_mode: 'Markdown' });
    });

    // Admin command - show admin menu (only for admins)
    this.bot.onText(/\/admin/, async (msg) => {
      const chatId = msg.chat.id;
      const userId = msg.from.id;
      
      logger.info(`/admin command received from chat ${chatId}`);
      
      // Check if user is admin
      if (!this.isAdmin(userId)) {
        logger.warn(`Unauthorized /admin command from user ${userId}`);
        return; // Silently ignore for non-admins
      }
      
      const keyboard = {
        inline_keyboard: [
          [
            { text: '🔧 Конфигурации', callback_data: 'admin_config' },
            { text: '⚙️ Установка', callback_data: 'admin_install' }
          ],
          [
            { text: '📊 Статистика', callback_data: 'admin_stats' },
            { text: '📋 Клиенты', callback_data: 'admin_clients' }
          ]
        ]
      };

      this.bot.sendMessage(
        chatId,
        '🔐 *Панель администратора*\n\nВыберите действие:',
        { parse_mode: 'Markdown', reply_markup: keyboard }
      );
    });

    // Stats command (admin only)
    this.bot.onText(/\/awgstats/, async (msg) => {
      const chatId = msg.chat.id;
      
      // Check if user is admin
      if (!this.isAdmin(chatId)) {
        logger.warn(`Unauthorized /awgstats command from chat ${chatId}`);
        return; // Silently ignore for non-admins
      }
      
      await this.showAwgStats(chatId);
    });

    // Handle callback queries for Admin, AWG and Install
    this.bot.on('callback_query', async (query) => {
      const chatId = query.message.chat.id;
      const userId = query.from.id;
      const data = query.data;

      // Admin menu callbacks
      if (data.startsWith('admin_')) {
        await this.bot.answerCallbackQuery(query.id);
        
        // Verify admin access
        if (!this.isAdmin(userId)) {
          logger.warn(`Unauthorized admin callback from user ${userId}`);
          return;
        }
        
        if (data === 'admin_config') {
          await this.showConfigMenu(chatId);
        } else if (data === 'admin_install') {
          await this.showInstallMenu(chatId, userId);
        } else if (data === 'admin_stats') {
          await this.showAwgStats(chatId);
        } else if (data === 'admin_clients') {
          await this.showAwgClients(chatId);
        }
      }
      // AWG config generation callbacks
      else if (data.startsWith('awg_')) {
        await this.bot.answerCallbackQuery(query.id);
        
        // Verify admin access for AWG operations
        if (!this.isAdmin(userId)) {
          logger.warn(`Unauthorized AWG callback from user ${userId}`);
          return;
        }
        
        if (data === 'awg_gen_v1') {
          await this.requestVpsLabel(chatId, 'v1');
        } else if (data === 'awg_gen_v2') {
          await this.requestVpsLabel(chatId, 'v2');
        } else if (data === 'awg_stats') {
          await this.showAwgStats(chatId);
        } else if (data === 'awg_clients') {
          await this.showAwgClients(chatId);
        } else if (data.startsWith('awg_clients_')) {
          const version = data.replace('awg_clients_', '');
          await this.showAwgClientsList(chatId, version);
        }
      }
      // Resend config callbacks
      else if (data.startsWith('resend_')) {
        await this.bot.answerCallbackQuery(query.id);
        
        // Verify admin access
        if (!this.isAdmin(userId)) {
          logger.warn(`Unauthorized resend callback from user ${userId}`);
          return;
        }
        
        // Parse: resend_v1_10.8.1.1
        const parts = data.split('_');
        const version = parts[1];
        const ip = parts.slice(2).join('.');
        
        await this.resendClientConfig(chatId, version, ip);
      }
      // Delete client callbacks
      else if (data.startsWith('delete_')) {
        await this.bot.answerCallbackQuery(query.id);
        
        // Verify admin access
        if (!this.isAdmin(userId)) {
          logger.warn(`Unauthorized delete callback from user ${userId}`);
          return;
        }
        
        // Parse: delete_v1_10.8.1.1
        const parts = data.split('_');
        const version = parts[1];
        const ip = parts.slice(2).join('.');
        
        await this.deleteClientConfig(chatId, version, ip);
      }
      // Confirm delete callbacks
      else if (data.startsWith('confirm_delete_')) {
        await this.bot.answerCallbackQuery(query.id);
        
        // Verify admin access
        if (!this.isAdmin(userId)) {
          logger.warn(`Unauthorized confirm delete callback from user ${userId}`);
          return;
        }
        
        // Parse: confirm_delete_v1_10.8.1.1
        const parts = data.replace('confirm_delete_', '').split('_');
        const version = parts[0];
        const ip = parts.slice(1).join('.');
        
        await this.confirmDeleteClient(chatId, version, ip);
      }
      // Install callbacks
      else if (data.startsWith('install_')) {
        await this.bot.answerCallbackQuery(query.id);
        
        // Verify admin access for install operations
        if (!this.isAdmin(userId)) {
          logger.warn(`Unauthorized install callback from user ${userId}`);
          return;
        }
        
        await this.handleInstallCallback(query, userId, chatId, data);
      }
    });

    // Handle document uploads (.bat and .conf files)
    this.bot.on('document', async (msg) => {
      const chatId = msg.chat.id;
      const document = msg.document;

      logger.info(`Document received from chat ${chatId}: ${document.file_name}`);

      const fileName = document.file_name ? document.file_name.toLowerCase() : '';
      
      // Check file type
      if (fileName.endsWith('.bat')) {
        await this.processBatFile(chatId, document);
      } else if (fileName.endsWith('.conf')) {
        await this.processAwgConfig(chatId, document);
      } else {
        logger.warn(`Invalid file type received from chat ${chatId}: ${document.file_name}`);
        this.bot.sendMessage(
          chatId,
          '❌ Пожалуйста, отправьте файл с расширением .bat или .conf'
        );
      }
    });

    // Handle text messages (domains and port input)
    this.bot.on('message', async (msg) => {
      // Skip if it's a document
      if (msg.document) {
        return;
      }

      // Skip commands
      if (msg.text && msg.text.startsWith('/')) {
        // Show start message for unknown commands
        if (!msg.text.match(/^\/(start|help|admin|awgstats)$/)) {
          const chatId = msg.chat.id;
          const welcomeMessage = `/start - Начало работы`;
          this.bot.sendMessage(chatId, welcomeMessage, { parse_mode: 'Markdown' });
        }
        return;
      }

      const chatId = msg.chat.id;
      const userId = msg.from.id;
      const text = msg.text;

      if (!text || text.trim() === '') {
        return;
      }

      // Check if user is in port selection mode
      const session = this.installSessions.get(userId);
      if (session && session.step === 'port_selection') {
        await this.handlePortInput(chatId, userId, text);
        return;
      }

      // Check if user is in VPS label input mode
      const vpsSession = this.vpsLabelSessions.get(userId);
      if (vpsSession && vpsSession.waitingForLabel) {
        await this.handleVpsLabelInput(chatId, userId, text, vpsSession.version);
        return;
      }

      await this.processDomains(chatId, text);
    });

    // Error handling
    this.bot.on('polling_error', (error) => {
      logger.error('Polling error:', error);
    });
  }

  async processDomains(chatId, text) {
    try {
      logger.info(`Processing domains request from chat ${chatId}`);
      
      // Check anti-flood
      const userId = chatId;
      const limitCheck = this.antiFlood.checkLimit(userId);
      
      if (!limitCheck.allowed) {
        logger.warn(`Anti-flood triggered for chat ${chatId}, remaining time: ${limitCheck.remainingTime}s`);
        this.bot.sendMessage(
          chatId,
          `⏳ Слишком много запросов. Подождите ${limitCheck.remainingTime} секунд.`
        );
        return;
      }

      // Parse domains from text
      const domains = text
        .split('\n')
        .map(line => line.trim())
        .filter(line => line.length > 0);

      if (domains.length === 0) {
        logger.warn(`No domains found in request from chat ${chatId}`);
        this.bot.sendMessage(chatId, '❌ Не найдено доменов для обработки.');
        return;
      }

      logger.info(`Parsed ${domains.length} domain(s) from chat ${chatId}: ${domains.join(', ')}`);

      // Validate domains
      const domainRegex = /^(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$/;
      const invalidDomains = domains.filter(domain => {
        const cleanDomain = domain
          .replace(/^https?:\/\//, '')
          .replace(/^www\./, '')
          .split('/')[0]
          .split(':')[0];
        return !domainRegex.test(cleanDomain);
      });

      if (invalidDomains.length > 0) {
        logger.warn(`Invalid domains from chat ${chatId}: ${invalidDomains.join(', ')}`);
        this.bot.sendMessage(
          chatId,
          `❌ Неправильный формат:\n${invalidDomains.join('\n')}\n\n` +
          `/start - Начало работы`,
          { parse_mode: 'Markdown' }
        );
        return;
      }

      // Send processing message
      const processingMsg = await this.bot.sendMessage(
        chatId, 
        `⏳ Обрабатываю ${domains.length} домен(ов)...`
      );

      // Resolve domains
      logger.info(`Starting DNS resolution for ${domains.length} domain(s) from chat ${chatId}`);
      let domainsMap;
      if (domains.length === 1) {
        const addresses = await resolveDomain(domains[0]);
        domainsMap = new Map([[domains[0], addresses]]);
      } else {
        domainsMap = await resolveMultipleDomains(domains);
      }

      // Check if any domains were resolved
      if (domainsMap.size === 0) {
        logger.warn(`No domains resolved for chat ${chatId}`);
        this.bot.editMessageText(
          '❌ Не удалось разрешить ни один домен. Проверьте правильность ввода.',
          { chat_id: chatId, message_id: processingMsg.message_id }
        );
        return;
      }

      // Generate statistics
      let totalIPs = 0;
      for (const addresses of domainsMap.values()) {
        totalIPs += addresses.ipv4.length;
      }

      // Generate batch file
      const filename = domains.length === 1 
        ? generateFilename(domains[0])
        : generateMultipleDomainsFilename(domains);

      const content = generateBatchFile(domainsMap, filename);
      const filepath = saveBatchFile(content, filename);

      logger.info(`Generated batch file for chat ${chatId}: ${filename}, Total IPs: ${totalIPs}`);

      // Delete processing message
      await this.bot.deleteMessage(chatId, processingMsg.message_id);

      // Send file without caption
      await this.bot.sendDocument(chatId, filepath);
      logger.info(`Sent batch file to chat ${chatId}`);

      // Send domains list (max 5) with statistics
      const domainsList = Array.from(domainsMap.keys());
      const displayDomains = domainsList.slice(0, 5);
      let domainsMessage = '🌐 Домены:\n';
      
      displayDomains.forEach(domain => {
        const ipCount = domainsMap.get(domain).ipv4.length;
        domainsMessage += `• ${domain} (${ipCount} IP)\n`;
      });
      
      if (domainsList.length > 5) {
        domainsMessage += `\n... и еще ${domainsList.length - 5}`;
      }

      // Add statistics
      domainsMessage += `\n📊 Статистика:\n`;
      domainsMessage += `Всего доменов: ${domainsMap.size}\n`;
      domainsMessage += `Всего IP адресов: ${totalIPs}`;

      this.bot.sendMessage(chatId, domainsMessage);

    } catch (error) {
      logger.error(`Error processing domains for chat ${chatId}:`, error);
      this.bot.sendMessage(
        chatId,
        `❌ Произошла ошибка при обработке: ${error.message}`
      );
    }
  }

  async processBatFile(chatId, document) {
    try {
      logger.info(`Processing .bat file from chat ${chatId}: ${document.file_name}`);
      
      // Check anti-flood
      const userId = chatId;
      const limitCheck = this.antiFlood.checkLimit(userId);
      
      if (!limitCheck.allowed) {
        logger.warn(`Anti-flood triggered for .bat file from chat ${chatId}, remaining time: ${limitCheck.remainingTime}s`);
        this.bot.sendMessage(
          chatId,
          `⏳ Слишком много запросов. Подождите ${limitCheck.remainingTime} секунд.`
        );
        return;
      }

      // Send processing message
      const processingMsg = await this.bot.sendMessage(
        chatId,
        `⏳ Обрабатываю файл ${document.file_name}...`
      );

      // Download file
      logger.info(`Downloading file ${document.file_name} from chat ${chatId}`);
      const fileLink = await this.bot.getFileLink(document.file_id);
      const response = await fetch(fileLink);
      const buffer = await response.arrayBuffer();
      
      // Save to temp file
      const tempDir = path.join(config.outputDir, 'temp');
      if (!fs.existsSync(tempDir)) {
        fs.mkdirSync(tempDir, { recursive: true });
      }
      
      const tempFilePath = path.join(tempDir, `temp_${Date.now()}_${document.file_name}`);
      fs.writeFileSync(tempFilePath, Buffer.from(buffer));
      logger.info(`Saved temp file: ${tempFilePath}`);

      // Process the file
      logger.info(`Starting .bat file processing for chat ${chatId}`);
      const result = await processBatFile(tempFilePath, document.file_name);

      // Delete temp file
      fs.unlinkSync(tempFilePath);
      logger.info(`Deleted temp file: ${tempFilePath}`);

      // Delete processing message
      await this.bot.deleteMessage(chatId, processingMsg.message_id);

      // Send processed file
      await this.bot.sendDocument(chatId, result.outputPath);
      logger.info(`Sent processed file to chat ${chatId}: ${result.outputPath}`);

      // Send statistics
      const statsMessage = `
📊 *Статистика обработки:*

📝 Всего маршрутов: ${result.stats.totalRoutes}

*До обработки:*
✅ С комментариями: ${result.stats.initialWithComments}
❌ Без комментариев: ${result.stats.initialWithoutComments}

*После обработки:*
➕ Добавлено комментариев: ${result.stats.commentsAdded}
🎯 Итого с комментариями: ${result.stats.finalWithComments}
⚠️ Осталось без комментариев: ${result.stats.finalWithoutComments}
      `;

      this.bot.sendMessage(chatId, statsMessage, { parse_mode: 'Markdown' });
      logger.info(`Bat file processing completed for chat ${chatId}. Stats: ${JSON.stringify(result.stats)}`);

    } catch (error) {
      logger.error(`Error processing .bat file for chat ${chatId}:`, error);
      this.bot.sendMessage(
        chatId,
        `❌ Произошла ошибка при обработке файла: ${error.message}`
      );
    }
  }

  async processAwgConfig(chatId, document) {
    try {
      logger.info(`Processing .conf file from chat ${chatId}: ${document.file_name}`);
      
      // Check anti-flood
      const userId = chatId;
      const limitCheck = this.antiFlood.checkLimit(userId);
      
      if (!limitCheck.allowed) {
        logger.warn(`Anti-flood triggered for .conf file from chat ${chatId}, remaining time: ${limitCheck.remainingTime}s`);
        this.bot.sendMessage(
          chatId,
          `⏳ Слишком много запросов. Подождите ${limitCheck.remainingTime} секунд.`
        );
        return;
      }

      // Send processing message
      const processingMsg = await this.bot.sendMessage(
        chatId,
        `⏳ Обрабатываю конфигурацию ${document.file_name}...`
      );

      // Download file
      logger.info(`Downloading file ${document.file_name} from chat ${chatId}`);
      const fileLink = await this.bot.getFileLink(document.file_id);
      const response = await fetch(fileLink);
      const buffer = await response.arrayBuffer();
      
      // Save to temp file
      const tempDir = path.join(config.outputDir, 'temp');
      if (!fs.existsSync(tempDir)) {
        fs.mkdirSync(tempDir, { recursive: true });
      }
      
      const tempFilePath = path.join(tempDir, `temp_${Date.now()}_${document.file_name}`);
      fs.writeFileSync(tempFilePath, Buffer.from(buffer));
      logger.info(`Saved temp file: ${tempFilePath}`);

      // Process the file
      logger.info(`Starting config processing for chat ${chatId}`);
      const result = await processAwgConfig(tempFilePath, document.file_name);

      // Delete temp file
      fs.unlinkSync(tempFilePath);
      logger.info(`Deleted temp file: ${tempFilePath}`);

      // Delete processing message
      await this.bot.deleteMessage(chatId, processingMsg.message_id);

      // Send processed file
      await this.bot.sendDocument(chatId, result.outputPath);
      logger.info(`Sent processed config to chat ${chatId}: ${result.outputPath}`);

      // Send information message
      let infoMessage = '';
      
      if (result.converted) {
        infoMessage = `
✅ *Конвертация завершена!*

📋 Исходная версия v${result.version}
🔄 Конвертировано v1
        `;
      } else {
        infoMessage = `
✅ *Проверка завершена!*

📋 Версия v${result.version}
ℹ️ Конфигурация уже в формате v1, конвертация не требуется
        `;
      }

      this.bot.sendMessage(chatId, infoMessage, { parse_mode: 'Markdown' });
      logger.info(`Config processing completed for chat ${chatId}. Version: ${result.version}, Converted: ${result.converted}`);

    } catch (error) {
      logger.error(`Error processing .conf file for chat ${chatId}:`, error);
      this.bot.sendMessage(
        chatId,
        `❌ Произошла ошибка при обработке конфигурации: ${error.message}`
      );
    }
  }
  async showConfigMenu(chatId) {
    try {
      logger.info(`Showing config menu for chat ${chatId}`);
      
      const keyboard = {
        inline_keyboard: [
          [
            { text: 'v1', callback_data: 'awg_gen_v1' },
            { text: 'v2', callback_data: 'awg_gen_v2' }
          ]
        ]
      };

      this.bot.sendMessage(
        chatId,
        '🔧 *Конфигурации *\n\nВыберите версию:',
        { parse_mode: 'Markdown', reply_markup: keyboard }
      );
    } catch (error) {
      logger.error(`Error showing config menu for chat ${chatId}:`, error);
      this.bot.sendMessage(chatId, `❌ Ошибка: ${error.message}`);
    }
  }

  async requestVpsLabel(chatId, version) {
    try {
      logger.info(`Requesting VPS label for ${version} from chat ${chatId}`);
      
      // Сохраняем сессию
      this.vpsLabelSessions.set(chatId, {
        waitingForLabel: true,
        version: version
      });
      
      await this.bot.sendMessage(
        chatId,
        `📝 *Введите метку сервера*\n\n` +
        `Например: \`XYZ\`, \`SERVER1\`, \`VPS-NY\`\n\n` +
        `Эта метка будет добавлена к имени файла конфигурации.\n` +
        `Пример: \`XYZ_AWGv1_10_8_1_1.conf\``,
        { parse_mode: 'Markdown' }
      );
    } catch (error) {
      logger.error(`Error requesting VPS label for chat ${chatId}:`, error);
      this.bot.sendMessage(chatId, `❌ Ошибка: ${error.message}`);
    }
  }

  async handleVpsLabelInput(chatId, userId, label, version) {
    try {
      // Очищаем сессию
      this.vpsLabelSessions.delete(userId);
      
      // Валидация метки
      const cleanLabel = label.trim().toUpperCase().replace(/[^A-Z0-9_-]/g, '');
      
      if (!cleanLabel || cleanLabel.length === 0) {
        await this.bot.sendMessage(
          chatId,
          `❌ Некорректная метка. Используйте только буквы, цифры, дефис и подчеркивание.\n\n` +
          `Попробуйте снова через /admin → Конфигурации`
        );
        return;
      }
      
      if (cleanLabel.length > 20) {
        await this.bot.sendMessage(
          chatId,
          `❌ Метка слишком длинная (максимум 20 символов).\n\n` +
          `Попробуйте снова через /admin → Конфигурации`
        );
        return;
      }
      
      logger.info(`VPS label accepted: ${cleanLabel} for ${version} from chat ${chatId}`);
      
      // Генерируем конфигурацию с меткой
      await this.generateAwgConfig(chatId, version, cleanLabel);
      
    } catch (error) {
      logger.error(`Error handling VPS label input for chat ${chatId}:`, error);
      this.bot.sendMessage(chatId, `❌ Ошибка: ${error.message}`);
    }
  }

  async generateAwgConfig(chatId, version, vpsLabel = null) {
    try {
      logger.info(`Generating ${version} config for chat ${chatId}`);
      
      // Check anti-flood
      const userId = chatId;
      const limitCheck = this.antiFlood.checkLimit(userId);
      
      if (!limitCheck.allowed) {
        logger.warn(`Anti-flood triggered for generation from chat ${chatId}`);
        this.bot.sendMessage(
          chatId,
          `⏳ Слишком много запросов. Подождите ${limitCheck.remainingTime} секунд.`
        );
        return;
      }

      // Send processing message
      const processingMsg = await this.bot.sendMessage(
        chatId,
        `⏳ Генерирую конфигурацию ${version.toUpperCase()}...\n` +
        `Это может занять несколько секунд...`
      );

      // Generate config
      const result = await this.awgManager.generateClientConfig(version, vpsLabel);

      // Delete processing message
      await this.bot.deleteMessage(chatId, processingMsg.message_id);

      // Send config file
      await this.bot.sendDocument(chatId, result.filepath);
      logger.info(`Sent ${version} config to chat ${chatId}: ${result.filename}`);

    } catch (error) {
      logger.error(`Error generating ${version} config for chat ${chatId}:`, error);
      this.bot.sendMessage(
        chatId,
        `❌ Ошибка при генерации конфигурации: ${error.message}\n\n` +
        `Убедитесь, что:\n` +
        `• Docker-контейнер запущен\n`
      );
    }
  }

  async showAwgStats(chatId) {
    try {
      logger.info(`Showing stats for chat ${chatId}`);

      const processingMsg = await this.bot.sendMessage(chatId, '⏳ Получаю статистику...');

      const stats = await this.awgManager.getStats();

      await this.bot.deleteMessage(chatId, processingMsg.message_id);

      if (stats.length === 0) {
        this.bot.sendMessage(
          chatId,
          `📊 *Статистика серверов*\n\n❌ Контейнеры AWG не найдены.\n\nУбедитесь, что контейнеры запущены\n\`\``,
          { parse_mode: 'Markdown' }
        );
        return;
      }

      let statsMessage = '📊 *Статистика серверов*\n\n';
      
      for (const container of stats) {
        const versionLabel = container.version === 'v1' ? 'v1' : 'v2';
        statsMessage += `*${versionLabel}:*\n`;
        statsMessage += `${container.running ? '✅ Работает' : '❌ Не работает'}\n`;
        statsMessage += `📦 Контейнер: \`${container.name}\`\n`;
        statsMessage += `👥 Клиентов: ${container.clients}\n`;
        statsMessage += `🔌 Порт: ${container.port}\n\n`;
      }

      this.bot.sendMessage(chatId, statsMessage, { parse_mode: 'Markdown' });

    } catch (error) {
      logger.error(`Error showing stats for chat ${chatId}:`, error);
      this.bot.sendMessage(
        chatId,
        `❌ Ошибка при получении статистики: ${error.message}`
      );
    }
  }

  async showAwgClients(chatId) {
    try {

      const keyboard = {
        inline_keyboard: [
          [
            { text: '🚀 Клиенты v1', callback_data: 'awg_clients_v1' },
            { text: '🚀 Клиенты v2', callback_data: 'awg_clients_v2' }
          ]
        ]
      };

      this.bot.sendMessage(
        chatId,
        '📋 *Список клиентов*\n\nВыберите версию:',
        { parse_mode: 'Markdown', reply_markup: keyboard }
      );

    } catch (error) {
      logger.error(`Error showing clients menu for chat ${chatId}:`, error);
      this.bot.sendMessage(
        chatId,
        `❌ Ошибка: ${error.message}`
      );
    }
  }

  async showAwgClientsList(chatId, version) {
    try {
      logger.info(`Showing ${version} clients list for chat ${chatId}`);

      const processingMsg = await this.bot.sendMessage(chatId, '⏳ Получаю список клиентов...');

      // Initialize AWG manager if needed
      if (!this.awgManager.initialized) {
        await this.awgManager.initialize();
      }

      // Find container by version
      const container = this.awgManager.availableContainers.find(c => c.version === version);
      
      if (!container) {
        await this.bot.deleteMessage(chatId, processingMsg.message_id);
        this.bot.sendMessage(
          chatId,
          `📋 *Клиенты ${version.toUpperCase()}*\n\n❌ Контейнер версии ${version} не найден`,
          { parse_mode: 'Markdown' }
        );
        return;
      }

      const clients = await this.awgManager.getClients(container.name);

      await this.bot.deleteMessage(chatId, processingMsg.message_id);

      if (clients.length === 0) {
        this.bot.sendMessage(
          chatId,
          `📋 *Клиенты ${version.toUpperCase()}*\n\n📦 Контейнер: \`${container.name}\`\n\nНет активных клиентов`,
          { parse_mode: 'Markdown' }
        );
        return;
      }

      let clientsMessage = `📋 *Клиенты ${version.toUpperCase()}*\n\n`;
      clientsMessage += `📦 Контейнер: \`${container.name}\`\n`;
      clientsMessage += `Всего: ${clients.length}\n\n`;
      
      // Создаём кнопки для каждого клиента
      const keyboard = {
        inline_keyboard: []
      };
      
      clients.forEach((ip, index) => {
        clientsMessage += `${index + 1}. \`${ip}\`\n`;
        
        // Добавляем кнопки для каждого IP
        keyboard.inline_keyboard.push([
          {
            text: `📤 ${ip}`,
            callback_data: `resend_${version}_${ip}`
          },
          {
            text: `🗑️ Удалить ${ip}`,
            callback_data: `delete_${version}_${ip}`
          }
        ]);
      });

      this.bot.sendMessage(chatId, clientsMessage, {
        parse_mode: 'Markdown',
        reply_markup: keyboard
      });

    } catch (error) {
      logger.error(`Error showing ${version} clients list for chat ${chatId}:`, error);
      this.bot.sendMessage(
        chatId,
        `❌ Ошибка при получении списка клиентов: ${error.message}`
      );
    }
  }

  async resendClientConfig(chatId, version, ip) {
    try {
      logger.info(`Resending config for ${ip} (${version}) to chat ${chatId}`);
      
      const processingMsg = await this.bot.sendMessage(
        chatId,
        `⏳ Восстанавливаю конфигурацию для \`${ip}\`...`,
        { parse_mode: 'Markdown' }
      );
      
      // Initialize AWG manager if needed
      if (!this.awgManager.initialized) {
        await this.awgManager.initialize();
      }
      
      // Find container by version
      const container = this.awgManager.availableContainers.find(c => c.version === version);
      
      if (!container) {
        await this.bot.deleteMessage(chatId, processingMsg.message_id);
        this.bot.sendMessage(
          chatId,
          `❌ Контейнер версии ${version} не найден`
        );
        return;
      }
      
      // Regenerate config
      const result = await this.awgManager.regenerateClientConfig(container.name, ip);
      
      // Delete processing message
      await this.bot.deleteMessage(chatId, processingMsg.message_id);
      
      // Send config file
      await this.bot.sendDocument(chatId, result.filepath);
      logger.info(`Resent config to chat ${chatId}: ${result.filename}`);
      
      this.bot.sendMessage(
        chatId,
        `✅ Конфигурация для \`${ip}\` отправлена повторно`,
        { parse_mode: 'Markdown' }
      );
      
    } catch (error) {
      logger.error(`Error resending config for ${ip}:`, error);
      this.bot.sendMessage(
        chatId,
        `❌ Ошибка при восстановлении конфигурации:\n${error.message}\n\n` +
        `Возможные причины:\n` +
        `• Оригинальный файл конфигурации был удалён\n` +
        `• Клиент был удалён с сервера\n\n` +
        `Попробуйте создать новую конфигурацию через /admin → Конфигурации`
      );
    }
  }

  async deleteClientConfig(chatId, version, ip) {
    try {
      logger.info(`Delete request for ${ip} (${version}) from chat ${chatId}`);
      
      // Запрашиваем подтверждение
      const keyboard = {
        inline_keyboard: [
          [
            { text: '✅ Да, удалить', callback_data: `confirm_delete_${version}_${ip}` },
            { text: '❌ Отмена', callback_data: `awg_clients_${version}` }
          ]
        ]
      };
      
      this.bot.sendMessage(
        chatId,
        `⚠️ *Подтверждение удаления*\n\n` +
        `Вы уверены что хотите удалить клиента \`${ip}\` из ${version.toUpperCase()}?\n\n` +
        `Это действие:\n` +
        `• Удалит клиента из конфигурации сервера\n` +
        `• Перезапустит контейнер\n` +
        `• Сохранённый файл конфигурации останется`,
        {
          parse_mode: 'Markdown',
          reply_markup: keyboard
        }
      );
      
    } catch (error) {
      logger.error(`Error requesting delete confirmation for ${ip}:`, error);
      this.bot.sendMessage(
        chatId,
        `❌ Ошибка при запросе подтверждения: ${error.message}`
      );
    }
  }

  async confirmDeleteClient(chatId, version, ip) {
    try {
      logger.info(`Confirming delete for ${ip} (${version}) from chat ${chatId}`);
      
      const processingMsg = await this.bot.sendMessage(
        chatId,
        `⏳ Удаляю клиента \`${ip}\`...`,
        { parse_mode: 'Markdown' }
      );
      
      // Initialize AWG manager if needed
      if (!this.awgManager.initialized) {
        await this.awgManager.initialize();
      }
      
      // Find container by version
      const container = this.awgManager.availableContainers.find(c => c.version === version);
      
      if (!container) {
        await this.bot.deleteMessage(chatId, processingMsg.message_id);
        this.bot.sendMessage(
          chatId,
          `❌ Контейнер версии ${version} не найден`
        );
        return;
      }
      
      // Delete peer from server config
      const configPath = container.configPath;
      const configFile = version === 'v2' ? 'awg0.conf' : 'wg0.conf';
      
      // Remove peer section for this IP
      await this.bot.deleteMessage(chatId, processingMsg.message_id);
      
      try {
        // Read current config
        const { stdout: currentConfig } = await execAsync(
          `docker exec ${container.name} cat ${configPath}`
        );
        
        // Remove peer section for this IP
        const peerRegex = new RegExp(
          `\\[Peer\\][\\s\\S]*?AllowedIPs\\s*=\\s*${ip.replace(/\./g, '\\.')}\\/32[\\s\\S]*?(?=\\[Peer\\]|$)`,
          'g'
        );
        
        const newConfig = currentConfig.replace(peerRegex, '');
        
        // Write new config
        const tempFile = `/tmp/awg_config_${Date.now()}.conf`;
        await execAsync(`echo '${newConfig.replace(/'/g, "'\\''")}' > ${tempFile}`);
        await execAsync(`docker cp ${tempFile} ${container.name}:${configPath}`);
        await execAsync(`rm ${tempFile}`);
        
        // Restart WireGuard interface
        const configName = configFile.replace('.conf', '');
        const fullConfigPath = `/etc/amnezia/amneziawg/${configFile}`;
        await execAsync(`docker exec ${container.name} wg-quick down ${fullConfigPath} || true`);
        await execAsync(`docker exec ${container.name} wg-quick up ${fullConfigPath}`);
        
        logger.info(`Successfully deleted client ${ip} from ${container.name}`);
        
        this.bot.sendMessage(
          chatId,
          `✅ Клиент \`${ip}\` успешно удалён из ${version.toUpperCase()}\n\n` +
          `IP адрес освобождён и может быть использован для нового клиента`,
          { parse_mode: 'Markdown' }
        );
        
      } catch (error) {
        logger.error(`Error deleting client ${ip}:`, error);
        this.bot.sendMessage(
          chatId,
          `❌ Ошибка при удалении клиента:\n${error.message}\n\n` +
          `Попробуйте удалить вручную через:\n` +
          `\`docker exec ${container.name} wg set wg0 peer <PUBLIC_KEY> remove\``
        );
      }
      
    } catch (error) {
      logger.error(`Error in confirmDeleteClient for ${ip}:`, error);
      this.bot.sendMessage(chatId, `❌ Ошибка: ${error.message}`);
    }
  }

  // ==================== INSTALL METHODS ====================

  async showInstallMenu(chatId, userId) {
    try {
      logger.info(`Showing install menu for chat ${chatId}`);
      
      const processingMsg = await this.bot.sendMessage(chatId, '🔍 Проверяю установленные серверы...');
      
      const status = await awgInstaller.checkInstalledServers();
      
      await this.bot.deleteMessage(chatId, processingMsg.message_id);
      
      const message = this.formatInstallStatus(status);
      const keyboard = this.createInstallKeyboard(status);
      
      this.bot.sendMessage(chatId, message, {
        parse_mode: 'Markdown',
        reply_markup: { inline_keyboard: keyboard }
      });
      
    } catch (error) {
      logger.error(`Error showing install menu for chat ${chatId}:`, error);
      this.bot.sendMessage(chatId, `❌ Ошибка: ${error.message}`);
    }
  }

  formatInstallStatus(status) {
    let message = '📊 *Статус серверов:*\n\n';
    
    message += status.v1.installed 
      ? `✅ v1 установлен (порт: ${status.v1.port})\n`
      : '❌ v1 не установлен\n';
      
    message += status.v2.installed
      ? `✅ v2 установлен (порт: ${status.v2.port})\n`
      : '❌ v2 не установлен\n';
    
    if (status.v1.installed || status.v2.installed) {
      message += '\n*Что делать?*';
    } else {
      message += '\n*Выберите версию для установки:*';
    }
    
    return message;
  }

  createInstallKeyboard(status) {
    const keyboard = [];
    
    // Если ничего не установлено
    if (!status.v1.installed && !status.v2.installed) {
      keyboard.push([
        { text: 'v1', callback_data: 'install_v1' },
        { text: 'v2', callback_data: 'install_v2' },
        { text: 'v1&v2', callback_data: 'install_both' }
      ]);
    }
    
    // Если установлен только v1
    if (status.v1.installed && !status.v2.installed) {
      keyboard.push([
        { text: '🔄 Переуст. v1', callback_data: 'install_reinstall_v1' },
        { text: '➕ Установить v2', callback_data: 'install_v2' }
      ]);
    }
    
    // Если установлен только v2
    if (!status.v1.installed && status.v2.installed) {
      keyboard.push([
        { text: '➕ Установить v1', callback_data: 'install_v1' },
        { text: '🔄 Переуст. v2', callback_data: 'install_reinstall_v2' }
      ]);
    }
    
    // Если оба установлены
    if (status.v1.installed && status.v2.installed) {
      keyboard.push([
        { text: '🔄 Переуст. v1', callback_data: 'install_reinstall_v1' },
        { text: '🔄 Переуст. v2', callback_data: 'install_reinstall_v2' }
      ]);
      keyboard.push([
        { text: '🔄 Переустановить оба', callback_data: 'install_reinstall_both' }
      ]);
      keyboard.push([
        { text: '🗑️ Удалить v1', callback_data: 'install_delete_v1' },
        { text: '🗑️ Удалить v2', callback_data: 'install_delete_v2' }
      ]);
      keyboard.push([
        { text: '🗑️ Удалить оба', callback_data: 'install_delete_both' }
      ]);
    }
    
    keyboard.push([{ text: '❌ Отмена', callback_data: 'install_cancel' }]);
    
    return keyboard;
  }

  async handleInstallCallback(query, userId, chatId, data) {
    try {
      const messageId = query.message.message_id;
      
      // install_v1, install_v2, install_both
      if (data === 'install_v1' || data === 'install_v2' || data === 'install_both') {
        const version = data.replace('install_', '');
        this.installSessions.set(userId, { version, step: 'port_selection' });
        
        const keyboard = [[{ text: '🎲 Случайный порт', callback_data: 'install_port_random' }]];
        
        await this.bot.editMessageText(
          '📝 *Настройка порта*\n\nВведите порт (1024-65535) или нажмите кнопку:',
          {
            chat_id: chatId,
            message_id: messageId,
            parse_mode: 'Markdown',
            reply_markup: { inline_keyboard: keyboard }
          }
        );
      }
      // install_reinstall_v1, install_reinstall_v2
      else if (data.startsWith('install_reinstall_')) {
        const version = data.replace('install_reinstall_', '');
        
        if (version === 'both') {
          await this.showReinstallBothConfirmation(chatId, messageId);
        } else {
          await this.showReinstallConfirmation(chatId, messageId, version);
        }
      }
      // install_confirm_reinstall_v1, install_confirm_reinstall_v2
      else if (data.startsWith('install_confirm_reinstall_')) {
        const version = data.replace('install_confirm_reinstall_', '');
        
        await this.bot.editMessageText('⏳ Удаляю старый сервер...', {
          chat_id: chatId,
          message_id: messageId
        });
        
        await awgInstaller.removeServer(version);
        
        this.installSessions.set(userId, { version, step: 'port_selection' });
        
        const keyboard = [[{ text: '🎲 Случайный порт', callback_data: 'install_port_random' }]];
        
        await this.bot.editMessageText(
          '📝 *Настройка порта*\n\nВведите порт (1024-65535) или нажмите кнопку:',
          {
            chat_id: chatId,
            message_id: messageId,
            parse_mode: 'Markdown',
            reply_markup: { inline_keyboard: keyboard }
          }
        );
      }
      // install_port_random
      else if (data === 'install_port_random') {
        const session = this.installSessions.get(userId);
        
        if (!session) {
          await this.bot.editMessageText('❌ Сессия истекла. Начните заново с /install', {
            chat_id: chatId,
            message_id: messageId
          });
          return;
        }
        
        await this.bot.editMessageText('⏳ Генерирую случайный порт...', {
          chat_id: chatId,
          message_id: messageId
        });
        
        const port = await portManager.generateRandomPort();
        
        if (!port) {
          await this.bot.editMessageText(
            '❌ Не удалось найти свободный порт.\n\nПопробуйте ввести порт вручную или повторите попытку.',
            {
              chat_id: chatId,
              message_id: messageId,
              reply_markup: {
                inline_keyboard: [[{ text: '🎲 Попробовать снова', callback_data: 'install_port_random' }]]
              }
            }
          );
          return;
        }
        
        await this.startInstallation(chatId, messageId, userId, session.version, port);
      }
      // install_confirm_reinstall_both_final
      else if (data === 'install_confirm_reinstall_both_final') {
        await this.bot.editMessageText('⏳ Удаляю серверы...', {
          chat_id: chatId,
          message_id: messageId
        });
        
        await awgInstaller.removeServer('v1');
        await awgInstaller.removeServer('v2');
        
        this.installSessions.set(userId, { version: 'both', step: 'port_selection' });
        
        const keyboard = [[{ text: '🎲 Случайный порт', callback_data: 'install_port_random' }]];
        
        await this.bot.editMessageText(
          '📝 *Настройка порта для v1*\n\nВведите порт (1024-65535) или нажмите кнопку:',
          {
            chat_id: chatId,
            message_id: messageId,
            parse_mode: 'Markdown',
            reply_markup: { inline_keyboard: keyboard }
          }
        );
      }
      // install_delete_v1, install_delete_v2, install_delete_both
      else if (data.startsWith('install_delete_')) {
        const version = data.replace('install_delete_', '');
        
        if (version === 'both') {
          await this.showDeleteBothConfirmation(chatId, messageId);
        } else {
          await this.showDeleteConfirmation(chatId, messageId, version);
        }
      }
      // install_confirm_delete_v1, install_confirm_delete_v2
      else if (data.startsWith('install_confirm_delete_')) {
        const version = data.replace('install_confirm_delete_', '');
        
        await this.bot.editMessageText('⏳ Удаляю сервер...', {
          chat_id: chatId,
          message_id: messageId
        });
        
        const result = await awgInstaller.removeServer(version);
        
        if (result) {
          await this.bot.editMessageText(
            `✅ Сервер ${version} успешно удалён!\n\n` +
            `• Контейнер остановлен и удалён\n` +
            `• Конфигурация удалена`,
            {
              chat_id: chatId,
              message_id: messageId
            }
          );
        } else {
          await this.bot.editMessageText(
            `❌ Ошибка при удалении сервера ${version}`,
            {
              chat_id: chatId,
              message_id: messageId
            }
          );
        }
      }
      // install_confirm_delete_both_final
      else if (data === 'install_confirm_delete_both_final') {
        await this.bot.editMessageText('⏳ Удаляю серверы...', {
          chat_id: chatId,
          message_id: messageId
        });
        
        const result1 = await awgInstaller.removeServer('v1');
        const result2 = await awgInstaller.removeServer('v2');
        
        if (result1 && result2) {
          await this.bot.editMessageText(
            `✅ Оба сервера успешно удалены!\n\n` +
            `• v1: контейнер и конфигурация удалены\n` +
            `• v2: контейнер и конфигурация удалены`,
            {
              chat_id: chatId,
              message_id: messageId
            }
          );
        } else {
          await this.bot.editMessageText(
            `⚠️ Удаление завершено с ошибками\n\n` +
            `• v1: ${result1 ? '✅ удалён' : '❌ ошибка'}\n` +
            `• v2: ${result2 ? '✅ удалён' : '❌ ошибка'}`,
            {
              chat_id: chatId,
              message_id: messageId
            }
          );
        }
      }
      // install_cancel
      else if (data === 'install_cancel') {
        this.installSessions.delete(userId);
        await this.bot.editMessageText('❌ Установка отменена', {
          chat_id: chatId,
          message_id: messageId
        });
      }
      
    } catch (error) {
      logger.error(`Error handling install callback for chat ${chatId}:`, error);
      this.bot.sendMessage(chatId, `❌ Ошибка: ${error.message}`);
    }
  }

  async showReinstallConfirmation(chatId, messageId, version) {
    try {
      const info = await awgInstaller.getServerInfo(version);
      
      const message = `⚠️ *Внимание!*\n\nВы собираетесь удалить ${version}:\n• Контейнер: \`${info.containerName}\`\n• Порт: ${info.port}\n• Клиентов: ${info.clientCount}\n\n❗ Все клиенты потеряют доступ!\n\nПродолжить?`;
      
      const keyboard = [
        [
          { text: '✅ Да, удалить', callback_data: `install_confirm_reinstall_${version}` },
          { text: '❌ Отмена', callback_data: 'install_cancel' }
        ]
      ];
      
      await this.bot.editMessageText(message, {
        chat_id: chatId,
        message_id: messageId,
        parse_mode: 'Markdown',
        reply_markup: { inline_keyboard: keyboard }
      });
      
    } catch (error) {
      logger.error(`Error showing reinstall confirmation:`, error);
      throw error;
    }
  }

  async showReinstallBothConfirmation(chatId, messageId) {
    try {
      const v1Info = await awgInstaller.getServerInfo('v1');
      const v2Info = await awgInstaller.getServerInfo('v2');
      
      const totalClients = v1Info.clientCount + v2Info.clientCount;
      
      const message = `⚠️ *Внимание!*\n\nВы собираетесь удалить ОБА сервера:\n\n*v1:*\n• Контейнер: \`${v1Info.containerName}\`\n• Порт: ${v1Info.port}\n• Клиентов: ${v1Info.clientCount}\n\n*v2:*\n• Контейнер: \`${v2Info.containerName}\`\n• Порт: ${v2Info.port}\n• Клиентов: ${v2Info.clientCount}\n\n❗ Всего ${totalClients} клиентов потеряют доступ!\n\nПродолжить?`;
      
      const keyboard = [
        [
          { text: '✅ Да, удалить оба', callback_data: 'install_confirm_reinstall_both_final' },
          { text: '❌ Отмена', callback_data: 'install_cancel' }
        ]
      ];
      
      await this.bot.editMessageText(message, {
        chat_id: chatId,
        message_id: messageId,
        parse_mode: 'Markdown',
        reply_markup: { inline_keyboard: keyboard }
      });
      
    } catch (error) {
      logger.error(`Error showing reinstall both confirmation:`, error);
      throw error;
    }
  }

  async showDeleteConfirmation(chatId, messageId, version) {
    try {
      const info = await awgInstaller.getServerInfo(version);
      
      const message = `⚠️ *Внимание!*\n\nВы собираетесь удалить AWG ${version}:\n• Контейнер: \`${info.containerName}\`\n• Порт: ${info.port}\n• Клиентов: ${info.clientCount}\n\n❗ Все клиенты потеряют доступ!\n❗ Сервер будет полностью удалён!\n\nПродолжить?`;
      
      const keyboard = [
        [
          { text: '✅ Да, удалить', callback_data: `install_confirm_delete_${version}` },
          { text: '❌ Отмена', callback_data: 'install_cancel' }
        ]
      ];
      
      await this.bot.editMessageText(message, {
        chat_id: chatId,
        message_id: messageId,
        parse_mode: 'Markdown',
        reply_markup: { inline_keyboard: keyboard }
      });
      
    } catch (error) {
      logger.error(`Error showing delete confirmation:`, error);
      throw error;
    }
  }

  async showDeleteBothConfirmation(chatId, messageId) {
    try {
      const v1Info = await awgInstaller.getServerInfo('v1');
      const v2Info = await awgInstaller.getServerInfo('v2');
      
      const totalClients = v1Info.clientCount + v2Info.clientCount;
      
      const message = `⚠️ *ВНИМАНИЕ!*\n\nВы собираетесь ПОЛНОСТЬЮ УДАЛИТЬ оба сервера:\n\n*v1:*\n• Контейнер: \`${v1Info.containerName}\`\n• Порт: ${v1Info.port}\n• Клиентов: ${v1Info.clientCount}\n\n*v2:*\n• Контейнер: \`${v2Info.containerName}\`\n• Порт: ${v2Info.port}\n• Клиентов: ${v2Info.clientCount}\n\n❗ Всего ${totalClients} клиентов потеряют доступ!\n❗ Серверы будут полностью удалены!\n\nПродолжить?`;
      
      const keyboard = [
        [
          { text: '✅ Да, удалить оба', callback_data: 'install_confirm_delete_both_final' },
          { text: '❌ Отмена', callback_data: 'install_cancel' }
        ]
      ];
      
      await this.bot.editMessageText(message, {
        chat_id: chatId,
        message_id: messageId,
        parse_mode: 'Markdown',
        reply_markup: { inline_keyboard: keyboard }
      });
      
    } catch (error) {
      logger.error(`Error showing delete both confirmation:`, error);
      throw error;
    }
  }

  async handlePortInput(chatId, userId, text) {
    try {
      const session = this.installSessions.get(userId);
      
      if (!session) {
        return;
      }
      
      const port = parseInt(text);
      
      // Валидация порта
      const validation = portManager.validatePort(port);
      
      if (!validation.valid) {
        this.bot.sendMessage(chatId, `❌ ${validation.error}`);
        return;
      }
      
      // Проверка занятости
      const inUse = await portManager.isPortInUse(port);
      
      if (inUse) {
        this.bot.sendMessage(chatId, '❌ Порт уже занят! Попробуйте другой.');
        return;
      }
      
      // Начинаем установку
      const progressMsg = await this.bot.sendMessage(chatId, '🚀 Начинаю установку...');
      await this.startInstallation(chatId, progressMsg.message_id, userId, session.version, port);
      
    } catch (error) {
      logger.error(`Error handling port input for chat ${chatId}:`, error);
      this.bot.sendMessage(chatId, `❌ Ошибка: ${error.message}`);
    }
  }

  async startInstallation(chatId, messageId, userId, version, port) {
    try {
      const session = this.installSessions.get(userId);
      if (session) {
        session.step = 'installing';
      }
      
      logger.info(`Starting installation of ${version} on port ${port} for user ${userId}`);
      
      // Callback для обновления прогресса
      const progressCallback = (text) => {
        this.bot.editMessageText(text, {
          chat_id: chatId,
          message_id: messageId
        }).catch(err => logger.warn(`Failed to update progress: ${err.message}`));
      };
      
      let result;
      
      if (version === 'both') {
        // Для установки обоих серверов нужно два порта
        progressCallback('⏳ Генерирую второй порт для v2...');
        const port2 = await portManager.generateRandomPort();
        if (!port2) {
          await this.bot.editMessageText(
            '❌ Не удалось найти второй свободный порт для v2.\n\n' +
            'Попробуйте установить серверы по отдельности.',
            {
              chat_id: chatId,
              message_id: messageId
            }
          );
          this.installSessions.delete(userId);
          return;
        }
        
        progressCallback(`✅ Порты выбраны:\n• v1: ${port}\n• v2: ${port2}\n\n🚀 Начинаю установку...`);
        result = await awgInstaller.installBothServers(port, port2, progressCallback);
      } else {
        result = await awgInstaller.installServer(version, port, progressCallback);
      }
      
      if (result.success) {
        let message = '';
        
        if (version === 'both') {
          message = `✅ *Установка завершена!*\n\n*v1:*\n• Порт: ${result.results.v1.port}\n• Контейнер: \`${result.results.v1.containerName}\`\n• Конфиг: \`${result.results.v1.configPath}\`\n\n*v2:*\n• Порт: ${result.results.v2.port}\n• Контейнер: \`${result.results.v2.containerName}\`\n• Конфиг: \`${result.results.v2.configPath}\`\n\nТеперь можно создавать клиентов через /admin`;
        } else {
          message = `✅ *Установка ${version} завершена!*\n\n📋 *Детали:*\n• Версия: ${version}\n• Порт: ${port}\n• Контейнер: \`${result.containerName}\`\n• Конфиг: \`${result.configPath}\`\n• Клиентов: 0\n\nТеперь можно создавать клиентов через /admin`;
        }
        
        await this.bot.editMessageText(message, {
          chat_id: chatId,
          message_id: messageId,
          parse_mode: 'Markdown'
        });
      } else {
        await this.bot.editMessageText(`❌ *Ошибка установки:*\n\n${result.error}`, {
          chat_id: chatId,
          message_id: messageId,
          parse_mode: 'Markdown'
        });
      }
      
      // Очищаем сессию
      this.installSessions.delete(userId);
      
    } catch (error) {
      logger.error(`Error during installation for chat ${chatId}:`, error);
      await this.bot.editMessageText(`❌ Ошибка установки: ${error.message}`, {
        chat_id: chatId,
        message_id: messageId
      });
      this.installSessions.delete(userId);
    }
  }

  /**
   * Проверка, является ли пользователь администратором
   */
  isAdmin(userId) {
    if (config.adminIds.length === 0) {
      logger.warn('No admin IDs configured! AWG features will be unavailable.');
      return false;
    }
    return config.adminIds.includes(userId);
  }

  start() {
    logger.info('Bot started successfully!');
    if (config.adminIds.length > 0) {
      logger.info(`Admin IDs: ${config.adminIds.join(', ')}`);
    } else {
      logger.warn('No admin IDs configured! features will be unavailable.');
    }
    logger.info('Waiting for messages...');
  }
}

// Made with Bob
