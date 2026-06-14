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

export class RouteBot {
  constructor() {
    this.bot = new TelegramBot(config.telegramToken, { polling: true });
    // Anti-flood: max 5 requests per minute
    this.antiFlood = new AntiFlood(10, 60000);
    this.antiFlood.startCleanup();
    // AWG Manager
    this.awgManager = new AWGManager();
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
      logger.info(`/start command received from chat ${chatId}, user ${userId}`);
      
      // Only admins can use the bot
      if (!this.isAdmin(userId)) {
        // Check anti-flood for non-admins
        const limitCheck = this.antiFlood.checkLimit(userId);
        if (!limitCheck.allowed) {
          logger.warn(`Anti-flood triggered for non-admin user ${userId}, remaining time: ${limitCheck.remainingTime}s`);
        }
        logger.warn(`Non-admin user ${userId} tried to use /start`);
        return; // Silently ignore for non-admins
      }
      
      let welcomeMessage = `
🚀 *Отправьте*
\`\`\`
rutube.ru
ozon.ru
google.com
\`\`\`
/start - Начало работы
/admin - Панель администратора`;
      
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
      
      // Получаем статистику
      let statsMessage = '';
      try {
        statsMessage = await this.showAwgStats(chatId);
      } catch (error) {
        statsMessage = '❌ Ошибка при получении статистики\n\n';
      }
      
      const keyboard = {
        inline_keyboard: [
          [
            { text: 'V1', callback_data: 'awg_select_v1' },
            { text: 'V2', callback_data: 'awg_select_v2' }
          ]
        ]
      };

      this.bot.sendMessage(
        chatId,
        `🔐 *Панель администратора*\n\n${statsMessage}Выберите действие:`,
        { parse_mode: 'Markdown', reply_markup: keyboard }
      );
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
        
        if (data === 'admin_menu') {
          // Получаем статистику
          let statsMessage = '';
          try {
            statsMessage = await this.showAwgStats(chatId);
          } catch (error) {
            statsMessage = '❌ Ошибка при получении статистики\n\n';
          }
          
          const keyboard = {
            inline_keyboard: [
              [
                { text: 'V1', callback_data: 'awg_select_v1' },
                { text: 'V2', callback_data: 'awg_select_v2' }
              ]
            ]
          };
          
          this.bot.sendMessage(
            chatId,
            `🔐 *Панель администратора*\n\n${statsMessage}Выберите действие:`,
            { parse_mode: 'Markdown', reply_markup: keyboard }
          );
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
        
        if (data.startsWith('awg_select_')) {
          const version = data.replace('awg_select_', '');
          await this.showClientSelectionMenu(chatId, version);
        } else if (data.startsWith('awg_gen_next_')) {
          const version = data.replace('awg_gen_next_', '');
          await this.requestVpsLabel(chatId, version);
        } else if (data.startsWith('awg_gen_by_number_')) {
          const version = data.replace('awg_gen_by_number_', '');
          await this.requestIpNumber(chatId, version);
        } else if (data === 'awg_gen_v1') {
          await this.requestVpsLabel(chatId, 'v1');
        } else if (data === 'awg_gen_v2') {
          await this.requestVpsLabel(chatId, 'v2');
        } else if (data === 'awg_stats') {
          await this.showAwgStats(chatId);
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
    });

    // Handle document uploads (.bat and .conf files)
    this.bot.on('document', async (msg) => {
      const chatId = msg.chat.id;
      const userId = msg.from.id;
      const document = msg.document;

      // Check if user is admin
      if (!this.isAdmin(userId)) {
        // Check anti-flood for non-admins
        const limitCheck = this.antiFlood.checkLimit(userId);
        if (!limitCheck.allowed) {
          logger.warn(`Anti-flood triggered for non-admin user ${userId}, remaining time: ${limitCheck.remainingTime}s`);
        }
        logger.warn(`Non-admin user ${userId} tried to upload document: ${document.file_name}`);
        return; // Silently ignore for non-admins
      }

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
          const userId = msg.from.id;
          
          // Check if user is admin
          if (!this.isAdmin(userId)) {
            // Check anti-flood for non-admins
            const limitCheck = this.antiFlood.checkLimit(userId);
            if (!limitCheck.allowed) {
              logger.warn(`Anti-flood triggered for non-admin user ${userId}, remaining time: ${limitCheck.remainingTime}s`);
            }
            logger.warn(`Non-admin user ${userId} tried to use unknown command: ${msg.text}`);
            return; // Silently ignore for non-admins
          }
          
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

      // Check if user is admin
      if (!this.isAdmin(userId)) {
        // Если разрешены DNS запросы для обычных пользователей
        if (config.allowUserDnsQueries) {
          // Проверяем, является ли текст валидным доменом
          if (this.isValidDomainQuery(text)) {
            // Разрешаем обработку домена БЕЗ промежуточных сообщений
            await this.processDomains(chatId, text, false); // false = тихий режим
            return;
          }
        }
        
        // Для всех остальных случаев - безшумно игнорируем
        const limitCheck = this.antiFlood.checkLimit(userId);
        if (!limitCheck.allowed) {
          logger.warn(`Anti-flood triggered for non-admin user ${userId}, remaining time: ${limitCheck.remainingTime}s`);
        }
        logger.warn(`Non-admin user ${userId} sent non-domain message: ${text.substring(0, 50)}...`);
        return; // Silently ignore for non-admins
      }

      // Check if user is in VPS label input mode
      const vpsSession = this.vpsLabelSessions.get(userId);
      if (vpsSession && vpsSession.waitingForLabel) {
        await this.handleVpsLabelInput(chatId, userId, text, vpsSession.version, vpsSession.mode);
        return;
      }

      // Check if user is in IP number input mode
      if (vpsSession && vpsSession.waitingForIpNumber) {
        await this.handleIpNumberInput(chatId, userId, text, vpsSession.version);
        return;
      }

      await this.processDomains(chatId, text);
    });

    // Error handling
    this.bot.on('polling_error', (error) => {
      logger.error('Polling error:', error);
    });
  }

  async processDomains(chatId, text, verbose = true) {
    try {
      logger.info(`Processing domains request from chat ${chatId} (verbose: ${verbose})`);
      
      // Check anti-flood
      const userId = chatId;
      const limitCheck = this.antiFlood.checkLimit(userId);
      
      if (!limitCheck.allowed) {
        logger.warn(`Anti-flood triggered for chat ${chatId}, remaining time: ${limitCheck.remainingTime}s`);
        if (verbose) {
          this.bot.sendMessage(
            chatId,
            `⏳ Слишком много запросов. Подождите ${limitCheck.remainingTime} секунд.`
          );
        }
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
        if (verbose) {
          this.bot.sendMessage(
            chatId,
            `❌ Неправильный формат:\n${invalidDomains.join('\n')}\n\n` +
            `/start - Начало работы`,
            { parse_mode: 'Markdown' }
          );
        }
        return;
      }

      // Send processing message only for verbose mode (admins)
      let processingMsg;
      if (verbose) {
        processingMsg = await this.bot.sendMessage(
          chatId,
          `⏳ Обрабатываю ${domains.length} домен(ов)...`
        );
      }

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
        if (verbose && processingMsg) {
          this.bot.editMessageText(
            '❌ Не удалось разрешить ни один домен. Проверьте правильность ввода.',
            { chat_id: chatId, message_id: processingMsg.message_id }
          );
        }
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

      // Delete processing message only if it was sent (verbose mode)
      if (verbose && processingMsg) {
        await this.bot.deleteMessage(chatId, processingMsg.message_id);
      }

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
      if (verbose) {
        this.bot.sendMessage(
          chatId,
          `❌ Произошла ошибка при обработке: ${error.message}`
        );
      }
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
  async showClientSelectionMenu(chatId, version) {
    try {
      logger.info(`Showing client selection menu for ${version} in chat ${chatId}`);
      
      const processingMsg = await this.bot.sendMessage(chatId, '⏳ Загружаю список клиентов...');

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
          `❌ Контейнер версии ${version} не найден`,
          { parse_mode: 'Markdown' }
        );
        return;
      }

      // Get clients with status
      const clients = await this.awgManager.getClientsWithStatus(container.name, version);

      await this.bot.deleteMessage(chatId, processingMsg.message_id);

      // Build message
      let message = `📋 *Клиенты ${version.toUpperCase()}*\n\n`;
      
      if (clients.length === 0) {
        message += 'Нет клиентов\n\n';
      } else {
        clients.forEach((client, index) => {
          const status = client.active ? '✅ активен' : '❌ неактивен';
          message += `${index + 1}. \`${client.ip}\` - ${status}\n`;
        });
        message += '\n';
      }

      // Build keyboard
      const keyboard = {
        inline_keyboard: [
          [
            { text: '📋 Подробнее', callback_data: `awg_clients_${version}` }
          ],
          [
            { text: '➕ Сформировать следующий', callback_data: `awg_gen_next_${version}` }
          ],
          [
            { text: '🔢 Сформировать по номеру', callback_data: `awg_gen_by_number_${version}` }
          ],
          [
            { text: '🔙 Назад', callback_data: 'admin_menu' }
          ]
        ]
      };

      this.bot.sendMessage(chatId, message, {
        parse_mode: 'Markdown',
        reply_markup: keyboard
      });

    } catch (error) {
      logger.error(`Error showing client selection menu for chat ${chatId}:`, error);
      this.bot.sendMessage(chatId, `❌ Ошибка: ${error.message}`);
    }
  }

  async requestVpsLabel(chatId, version) {
    try {
      logger.info(`Requesting VPS label for ${version} from chat ${chatId}`);
      
      // Сохраняем сессию
      this.vpsLabelSessions.set(chatId, {
        waitingForLabel: true,
        version: version,
        mode: 'next'
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

  async requestIpNumber(chatId, version) {
    try {
      logger.info(`Requesting IP number for ${version} from chat ${chatId}`);
      
      // Сохраняем сессию
      this.vpsLabelSessions.set(chatId, {
        waitingForIpNumber: true,
        version: version
      });
      
      await this.bot.sendMessage(
        chatId,
        `🔢 *Введите номер IP адреса*\n\n` +
        `Введите число от 2 до 254\n` +
        `⚠️ IP \`10.8.1.1\` зарезервирован для сервера\n\n` +
        `Например: \`8\` для IP \`10.8.1.8\`\n\n` +
        `Если клиент с этим IP уже существует - будет отправлена существующая конфигурация.\n` +
        `Если нет - будет создана новая.`,
        { parse_mode: 'Markdown' }
      );
    } catch (error) {
      logger.error(`Error requesting IP number for chat ${chatId}:`, error);
      this.bot.sendMessage(chatId, `❌ Ошибка: ${error.message}`);
    }
  }

  async handleVpsLabelInput(chatId, userId, label, version, mode = 'next') {
    try {
      // Получаем сессию для проверки ipNumber
      const session = this.vpsLabelSessions.get(userId);
      const ipNumber = session ? session.ipNumber : null;
      
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
      
      logger.info(`VPS label accepted: ${cleanLabel} for ${version} from chat ${chatId}, mode: ${mode}, ipNumber: ${ipNumber}`);
      
      // Генерируем конфигурацию в зависимости от режима
      if (mode === 'by_number' && ipNumber) {
        await this.generateAwgConfigByNumber(chatId, version, ipNumber, cleanLabel);
      } else {
        await this.generateAwgConfig(chatId, version, cleanLabel);
      }
      
    } catch (error) {
      logger.error(`Error handling VPS label input for chat ${chatId}:`, error);
      this.bot.sendMessage(chatId, `❌ Ошибка: ${error.message}`);
    }
  }

  async handleIpNumberInput(chatId, userId, text, version) {
    try {
      // Очищаем сессию
      this.vpsLabelSessions.delete(userId);
      
      // Валидация номера
      const ipNumber = parseInt(text.trim());
      
      if (isNaN(ipNumber) || ipNumber < 1 || ipNumber > 254) {
        await this.bot.sendMessage(
          chatId,
          `❌ Некорректный номер. Введите число от 1 до 254.\n\n` +
          `Попробуйте снова через /admin → Конфигурации`
        );
        return;
      }
      
      // Запрет использования IP 10.8.1.1 (адрес сервера)
      if (ipNumber === 1) {
        await this.bot.sendMessage(
          chatId,
          `❌ IP адрес 10.8.1.1 зарезервирован для сервера.\n\n` +
          `Используйте номера от 2 до 254.\n` +
          `Попробуйте снова через /admin → Конфигурации`
        );
        return;
      }
      
      logger.info(`IP number accepted: ${ipNumber} for ${version} from chat ${chatId}`);
      
      // Запрашиваем метку VPS
      this.vpsLabelSessions.set(chatId, {
        waitingForLabel: true,
        version: version,
        mode: 'by_number',
        ipNumber: ipNumber
      });
      
      await this.bot.sendMessage(
        chatId,
        `📝 *Введите метку сервера*\n\n` +
        `Например: \`XYZ\`, \`SERVER1\`, \`VPS-NY\`\n\n` +
        `Эта метка будет добавлена к имени файла конфигурации для IP \`10.8.1.${ipNumber}\`\n` +
        `Пример: \`XYZ_AWGv${version === 'v1' ? '1' : '2'}_10_8_1_${ipNumber}.conf\``,
        { parse_mode: 'Markdown' }
      );
      
    } catch (error) {
      logger.error(`Error handling IP number input for chat ${chatId}:`, error);
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

      // Show client selection menu again
      await this.showClientSelectionMenu(chatId, version);

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

  async generateAwgConfigByNumber(chatId, version, ipNumber, vpsLabel = null) {
    try {
      logger.info(`Generating ${version} config by number ${ipNumber} for chat ${chatId}`);
      
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
        `⏳ Генерирую конфигурацию ${version.toUpperCase()} для IP 10.8.1.${ipNumber}...\n` +
        `Это может занять несколько секунд...`
      );

      // Generate config by number
      const result = await this.awgManager.generateClientConfigByNumber(version, ipNumber, vpsLabel);

      // Delete processing message
      await this.bot.deleteMessage(chatId, processingMsg.message_id);

      // Send config file
      await this.bot.sendDocument(chatId, result.filepath);
      
      // Send info message
      let infoMessage = `✅ Конфигурация для \`${result.ip}\` `;
      if (result.isNew) {
        infoMessage += `создана`;
      } else {
        infoMessage += `восстановлена (IP уже существовал)`;
      }
      
      this.bot.sendMessage(chatId, infoMessage, { parse_mode: 'Markdown' });
      logger.info(`Sent ${version} config to chat ${chatId}: ${result.filename}`);

      // Show client selection menu again
      await this.showClientSelectionMenu(chatId, version);

    } catch (error) {
      logger.error(`Error generating ${version} config by number for chat ${chatId}:`, error);
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

      // Создаем карту найденных контейнеров по версиям
      const statsMap = new Map();
      stats.forEach(container => {
        statsMap.set(container.version, container);
      });

      let statsMessage = '📊 *Серверы*\n\n';
      
      // Показываем статус для обеих версий
      const versions = ['v1', 'v2'];
      
      for (const version of versions) {
        const container = statsMap.get(version);
        
        if (container) {
          // Получаем количество активных клиентов
          let activeClients = 0;
          if (container.running) {
            try {
              const clientsWithStatus = await this.awgManager.getClientsWithStatus(
                container.name,
                version
              );
              activeClients = clientsWithStatus.filter(c => c.active).length;
            } catch (error) {
              logger.warn(`Failed to get active clients for ${container.name}`);
            }
          }
          
          // Контейнер найден - показываем реальный статус
          statsMessage += `*AWG ${version.toUpperCase()}:*\n`;
          statsMessage += `${container.running ? '✅ Запущен' : '⚠️ Остановлен'}\n`;
          statsMessage += `📦 Контейнер: \`${container.name}\`\n`;
          statsMessage += `👥 Клиентов: ${container.clients}\n`;
          statsMessage += `🔌 Порт: ${container.port}\n`;
          statsMessage += `👤 Активных: ${activeClients}\n\n`;
        } else {
          // Контейнер не найден
          statsMessage += `*AWG ${version.toUpperCase()}:*\n`;
          statsMessage += `❌ Не установлен\n\n`;
        }
      }

      return statsMessage;

    } catch (error) {
      logger.error(`Error showing stats for chat ${chatId}:`, error);
      throw error;
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
          `📋 *Подробнее Клиенты ${version.toUpperCase()}*\n\n❌ Контейнер версии ${version} не найден`,
          { parse_mode: 'Markdown' }
        );
        return;
      }

      const clients = await this.awgManager.getClients(container.name);

      // Получаем статистику клиентов (последнее соединение, трафик)
      const clientsStats = await this.getClientsStats(container.name, version);

      await this.bot.deleteMessage(chatId, processingMsg.message_id);

      if (clients.length === 0) {
        this.bot.sendMessage(
          chatId,
          `📋 *Подробнее Клиенты ${version.toUpperCase()}*\n\n📦 Контейнер: \`${container.name}\`\n\nНет активных клиентов`,
          { parse_mode: 'Markdown' }
        );
        return;
      }

      let clientsMessage = `📋 *Подробнее Клиенты ${version.toUpperCase()}*\n\n`;
      clientsMessage += `📦 Контейнер: \`${container.name}\`\n`;
      clientsMessage += `Всего: ${clients.length}\n\n`;
      
      // Создаём кнопки для каждого клиента
      const keyboard = {
        inline_keyboard: []
      };
      
      clients.forEach((ip, index) => {
        const stats = clientsStats[ip] || {};
        const lastSeen = stats.lastHandshake || 'никогда';
        const transfer = stats.transfer || 'нет данных';
        
        clientsMessage += `${index + 1}. \`${ip}\`\n`;
        clientsMessage += `   └ 🕐 Последнее соединение: ${lastSeen}\n`;
        if (transfer !== 'нет данных') {
          clientsMessage += `   └ 📊 Трафик: ${transfer}\n`;
        }
        clientsMessage += `\n`;
        
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

      // Добавляем кнопку "Назад"
      keyboard.inline_keyboard.push([
        { text: '🔙 Назад', callback_data: `awg_select_${version}` }
      ]);

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

  async getClientsStats(containerName, version) {
    try {
      const interfaceName = version === 'v2' ? 'awg0' : 'wg0';
      
      // Получаем статистику через wg show
      const { stdout } = await execAsync(`docker exec ${containerName} wg show ${interfaceName}`);
      
      const stats = {};
      const lines = stdout.split('\n');
      let currentPeer = null;
      
      for (const line of lines) {
        // Ищем публичный ключ пира
        if (line.startsWith('peer:')) {
          currentPeer = {};
        } else if (currentPeer && line.includes('allowed ips:')) {
          // Извлекаем IP адрес
          const ipMatch = line.match(/allowed ips:\s*([0-9.]+)\/32/);
          if (ipMatch) {
            currentPeer.ip = ipMatch[1];
          }
        } else if (currentPeer && line.includes('latest handshake:')) {
          // Извлекаем время последнего handshake
          const timeMatch = line.match(/latest handshake:\s*(.+)/);
          if (timeMatch) {
            currentPeer.lastHandshake = this.formatHandshakeTime(timeMatch[1].trim());
          }
        } else if (currentPeer && line.includes('transfer:')) {
          // Извлекаем информацию о трафике
          const transferMatch = line.match(/transfer:\s*([^,]+),\s*(.+)/);
          if (transferMatch) {
            currentPeer.transfer = `↓${transferMatch[1].trim()} ↑${transferMatch[2].trim()}`;
          }
          
          // Сохраняем статистику для этого пира
          if (currentPeer.ip) {
            stats[currentPeer.ip] = currentPeer;
          }
          currentPeer = null;
        }
      }
      
      return stats;
    } catch (error) {
      logger.error(`Error getting clients stats:`, error);
      return {};
    }
  }

  formatHandshakeTime(timeStr) {
    // Если "1 minute ago" или подобное
    if (timeStr.includes('ago')) {
      return timeStr;
    }
    
    // Если это timestamp или другой формат
    try {
      const seconds = parseInt(timeStr);
      if (!isNaN(seconds)) {
        if (seconds < 60) {
          return `${seconds} сек назад`;
        } else if (seconds < 3600) {
          return `${Math.floor(seconds / 60)} мин назад`;
        } else if (seconds < 86400) {
          return `${Math.floor(seconds / 3600)} ч назад`;
        } else {
          return `${Math.floor(seconds / 86400)} дн назад`;
        }
      }
    } catch (e) {
      // ignore
    }
    
    return timeStr;
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
      
      // Show client selection menu again
      await this.showClientSelectionMenu(chatId, version);
      
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
        // Разбиваем конфигурацию на секции
        const sections = currentConfig.split(/(?=\[Peer\])/);
        
        // Фильтруем секции, удаляя ту, которая содержит нужный IP
        const filteredSections = sections.filter(section => {
          // Если это не секция [Peer], оставляем её
          if (!section.trim().startsWith('[Peer]')) {
            return true;
          }
          // Проверяем, содержит ли секция нужный IP
          const allowedIPsMatch = section.match(/AllowedIPs\s*=\s*([^\n]+)/);
          if (allowedIPsMatch) {
            const allowedIP = allowedIPsMatch[1].trim();
            // Удаляем только секцию с точным совпадением IP
            return allowedIP !== `${ip}/32`;
          }
          return true;
        });
        
        const newConfig = filteredSections.join('');
        
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
        
        // Создаем кнопки для быстрого перехода к клиентам
        const keyboard = {
          inline_keyboard: [
            [
              { text: '📋 AWG V1', callback_data: 'awg_clients_v1' },
              { text: '📋 AWG V2', callback_data: 'awg_clients_v2' }
            ]
          ]
        };
        
        this.bot.sendMessage(
          chatId,
          `✅ Клиент \`${ip}\` успешно удалён из ${version.toUpperCase()}\n\n` +
          `IP адрес освобождён и может быть использован для нового клиента`,
          {
            parse_mode: 'Markdown',
            reply_markup: keyboard
          }
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

  /**
   * Проверка, является ли текст валидным доменным запросом
   * @param {string} text - Текст для проверки
   * @returns {boolean}
   */
  isValidDomainQuery(text) {
    if (!text || typeof text !== 'string') {
      return false;
    }

    const trimmedText = text.trim();
    
    // Проверяем, что текст не пустой
    if (trimmedText === '') {
      return false;
    }

    // Разбиваем на строки (поддержка множественных доменов)
    const lines = trimmedText.split('\n').map(line => line.trim()).filter(line => line.length > 0);
    
    if (lines.length === 0) {
      return false;
    }

    // Регулярное выражение для проверки домена
    const domainRegex = /^(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$/;
    
    // Проверяем каждую строку
    for (const line of lines) {
      const cleanDomain = line
        .replace(/^https?:\/\//, '')
        .replace(/^www\./, '')
        .split('/')[0]
        .split(':')[0];
      
      if (!domainRegex.test(cleanDomain)) {
        return false;
      }
    }
    
    return true;
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
