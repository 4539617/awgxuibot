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
    // Message ID storage for editing instead of sending new messages
    this.lastMessageIds = new Map();
    this.setupHandlers();
    logger.info('RouteBot initialized');
  }

  setupHandlers() {
    // Start command
    this.bot.onText(/\/start/, async (msg) => {
      const chatId = msg.chat.id;
      const userId = msg.from.id;
      
      logger.info(`/start command received from chat ${chatId}, user ${userId}`);
      
      // Check if user is admin
      if (!this.isAdmin(userId)) {
        logger.warn(`Unauthorized /start command from user ${userId}`);
        return; // Silently ignore for non-admins
      }
      
      // Показываем главное меню
      await this.showMainMenu(chatId);
    });


    // Handle callback queries for Admin, AWG and Install
    this.bot.on('callback_query', async (query) => {
      const chatId = query.message.chat.id;
      const userId = query.from.id;
      const data = query.data;

      // Start menu callbacks
      if (data.startsWith('start_')) {
        await this.bot.answerCallbackQuery(query.id);
        
        // Verify admin access
        if (!this.isAdmin(userId)) {
          logger.warn(`Unauthorized start menu callback from user ${userId}`);
          return;
        }
        
        if (data === 'start_menu') {
          // Показываем главное меню
          await this.showMainMenu(chatId);
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
        
        // Очищаем сессию при любом AWG callback (отмена ожидания ввода)
        if (this.vpsLabelSessions.has(chatId)) {
          this.vpsLabelSessions.delete(chatId);
          logger.info(`Cleared VPS label session for chat ${chatId} due to new action`);
        }
        
        if (data.startsWith('awg_select_')) {
          const version = data.replace('awg_select_', '');
          await this.showClientSelectionMenu(chatId, version, false);
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
          await this.showAwgClientsList(chatId, version, false);
        }
      }
      // Refresh callbacks (для кнопок "Обновить")
      else if (data.startsWith('refresh_')) {
        // НЕ вызываем answerCallbackQuery здесь, так как это будет сделано в методах
        
        // Verify admin access
        if (!this.isAdmin(userId)) {
          logger.warn(`Unauthorized refresh callback from user ${userId}`);
          await this.bot.answerCallbackQuery(query.id);
          return;
        }
        
        if (data.startsWith('refresh_select_')) {
          const version = data.replace('refresh_select_', '');
          await this.showClientSelectionMenu(chatId, version, true, query.id);
        } else if (data.startsWith('refresh_clients_')) {
          const version = data.replace('refresh_clients_', '');
          await this.showAwgClientsList(chatId, version, true, query.id);
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
        if (!msg.text.match(/^\/(start|help|awgstats)$/)) {
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

      // Check if we have any IPs to generate
      if (totalIPs === 0) {
        logger.warn(`No IP addresses found for any domain from chat ${chatId}`);
        if (verbose && processingMsg) {
          this.bot.editMessageText(
            '❌ Домены найдены, но не удалось получить IP адреса. Возможно, домены недоступны или заблокированы.',
            { chat_id: chatId, message_id: processingMsg.message_id }
          );
        }
        return;
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

  /**
   * Отправить или отредактировать сообщение
   * Использует editMessageText если есть сохраненный message_id, иначе sendMessage
   */
  // Метод для отправки нового сообщения (каждый переход в новом окне)
  async sendNewMessage(chatId, text, options = {}) {
    const result = await this.bot.sendMessage(chatId, text, options);
    this.lastMessageIds.set(chatId, result.message_id);
    return result;
  }

  // Метод для обновления существующего сообщения (для кнопки "Обновить")
  async updateMessage(chatId, text, options = {}, callbackQueryId = null) {
    const lastMessageId = this.lastMessageIds.get(chatId);
    
    try {
      if (lastMessageId) {
        // Пытаемся отредактировать существующее сообщение
        const result = await this.bot.editMessageText(text, {
          chat_id: chatId,
          message_id: lastMessageId,
          ...options
        });
        // Успешно обновили в том же окне - уведомление НЕ показываем
        return { message_id: lastMessageId, isNewWindow: false };
      }
    } catch (error) {
      // Если не удалось отредактировать (сообщение слишком старое или удалено)
      logger.warn(`Failed to edit message ${lastMessageId} for chat ${chatId}, sending new one`);
      this.lastMessageIds.delete(chatId);
    }
    
    // Отправляем новое сообщение, если редактирование не удалось
    const result = await this.bot.sendMessage(chatId, text, options);
    this.lastMessageIds.set(chatId, result.message_id);
    
    // Показываем popup уведомление о новом окне если передан callbackQueryId
    if (callbackQueryId) {
      try {
        await this.bot.answerCallbackQuery(callbackQueryId, {
          text: '⚠️ Интерфейс еще не готов',
          show_alert: true
        });
      } catch (error) {
        logger.warn(`Failed to show popup notification: ${error.message}`);
      }
    }
    
    return { ...result, isNewWindow: true };
  }

  // Обратная совместимость - по умолчанию отправляем новое сообщение
  async sendOrEditMessage(chatId, text, options = {}) {
    return this.sendNewMessage(chatId, text, options);
  }

  async showClientSelectionMenu(chatId, version, shouldUpdate = false, callbackQueryId = null) {
    try {
      logger.info(`Showing client selection menu for ${version} in chat ${chatId}, shouldUpdate: ${shouldUpdate}`);
      
      // Initialize AWG manager if needed
      if (!this.awgManager.initialized) {
        await this.awgManager.initialize();
      }

      // Find container by version
      const container = this.awgManager.availableContainers.find(c => c.version === version);
      
      if (!container) {
        if (shouldUpdate) {
          await this.updateMessage(
            chatId,
            `❌ Контейнер версии ${version} не найден`,
            { parse_mode: 'Markdown' },
            callbackQueryId
          );
        } else {
          await this.sendNewMessage(
            chatId,
            `❌ Контейнер версии ${version} не найден`,
            { parse_mode: 'Markdown' }
          );
        }
        return;
      }

      // Get clients with status
      const clients = await this.awgManager.getClientsWithStatus(container.name, version);

      // Build message
      let message = `📋 *AWG ${version.toUpperCase()}*\n\n`;
      
      if (clients.length === 0) {
        message += 'Нет клиентов\n\n';
      } else {
        clients.forEach((client) => {
          if (client.active) {
            // Для активных показываем время последнего соединения
            message += `\`${client.ip}\` - ✅ ${client.lastHandshake || 'активен'}\n`;
          } else {
            // Для неактивных просто крестик
            message += `\`${client.ip}\` - ❌\n`;
          }
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
            { text: '🔄 Обновить', callback_data: `refresh_select_${version}` },
            { text: '🔙 Назад', callback_data: 'start_menu' }
          ]
        ]
      };

      // Выбираем метод отправки в зависимости от shouldUpdate
      if (shouldUpdate) {
        // При обновлении передаем callbackQueryId для показа popup при создании нового окна
        await this.updateMessage(chatId, message, {
          parse_mode: 'Markdown',
          reply_markup: keyboard
        }, callbackQueryId);
        
        // Если сообщение было успешно обновлено в том же окне, отвечаем на callback
        if (callbackQueryId) {
          try {
            await this.bot.answerCallbackQuery(callbackQueryId);
          } catch (error) {
            // Игнорируем ошибку, если уже ответили
          }
        }
      } else {
        // При первом открытии используем sendNewMessage
        await this.sendNewMessage(chatId, message, {
          parse_mode: 'Markdown',
          reply_markup: keyboard
        });
      }

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
      
      await this.sendOrEditMessage(
        chatId,
        `📝 *Введите метку сервера*\n\n` +
        `Например: \`XYZ\`, \`SERVER1\`, \`VPS-NY\`\n\n` +
        `Эта метка будет добавлена к имени файла конфигурации.\n` +
        `Пример: \`XYZ_AWGv1_10_8_1_1.conf\``,
        {
          parse_mode: 'Markdown',
          reply_markup: {
            inline_keyboard: [[
              { text: '🔙 Назад', callback_data: `awg_select_${version}` }
            ]]
          }
        }
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
      
      await this.sendOrEditMessage(
        chatId,
        `🔢 *Введите номер IP адреса*\n\n` +
        `Введите число от 2 до 254\n` +
        `⚠️ IP \`10.8.1.1\` зарезервирован для сервера\n\n` +
        `Например: \`8\` для IP \`10.8.1.8\`\n\n` +
        `Если клиент с этим IP уже существует - будет отправлена существующая конфигурация.\n` +
        `Если нет - будет создана новая.`,
        {
          parse_mode: 'Markdown',
          reply_markup: {
            inline_keyboard: [[
              { text: '🔙 Назад', callback_data: `awg_select_${version}` }
            ]]
          }
        }
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
          `Попробуйте снова через /start → Конфигурации`
        );
        return;
      }
      
      if (cleanLabel.length > 20) {
        await this.bot.sendMessage(
          chatId,
          `❌ Метка слишком длинная (максимум 20 символов).\n\n` +
          `Попробуйте снова через /start → Конфигурации`
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
          `Попробуйте снова через /start → Конфигурации`
        );
        return;
      }
      
      // Запрет использования IP 10.8.1.1 (адрес сервера)
      if (ipNumber === 1) {
        await this.bot.sendMessage(
          chatId,
          `❌ IP адрес 10.8.1.1 зарезервирован для сервера.\n\n` +
          `Используйте номера от 2 до 254.\n` +
          `Попробуйте снова через /start → Конфигурации`
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
      
      await this.sendOrEditMessage(
        chatId,
        `📝 *Введите метку сервера*\n\n` +
        `Например: \`VPS3\`, \`SERVER1\`\n\n` +
        `Эта метка будет добавлена к имени файла конфигурации для IP \`10.8.1.${ipNumber}\`\n` +
        `Пример: \`VPS3_AWGv${version === 'v1' ? '1' : '2'}_10_8_1_${ipNumber}.conf\``,
        {
          parse_mode: 'Markdown',
          reply_markup: {
            inline_keyboard: [[
              { text: '🔙 Назад', callback_data: `awg_select_${version}` }
            ]]
          }
        }
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

      // Send config file with status info
      await this.bot.sendDocument(chatId, result.filepath);
      
      // Send status message if health check was performed
      if (result.healthStatus) {
        const health = result.healthStatus;
        let statusMsg = `✅ Конфигурация создана: \`${result.ip}\`\n\n`;
        statusMsg += `📦 *Состояние сервера:*\n`;
        statusMsg += `├ Контейнер: ${health.containerRunning ? '✅' : '❌'}\n`;
        statusMsg += `├ Интерфейс: ${health.interfaceUp ? '✅' : '❌'}\n`;
        statusMsg += `└ WireGuard: ${health.interfaceReady ? '✅ Готов' : '⏳ Запускается'}\n`;
        
        if (health.interfaceReady) {
          statusMsg += `\n📊 Всего клиентов: ${health.peerCount}`;
        }
        
        if (!health.healthy) {
          statusMsg += `\n\n⚠️ Обнаружены проблемы, проверьте статус`;
        }
        
        await this.bot.sendMessage(chatId, statusMsg, { parse_mode: 'Markdown' });
      }
      
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

      // Send config file with status info
      await this.bot.sendDocument(chatId, result.filepath);
      
      // Send status message if health check was performed
      if (result.healthStatus) {
        const health = result.healthStatus;
        let statusMsg = result.isNew
          ? `✅ Новая конфигурация создана: \`${result.ip}\`\n\n`
          : `✅ Конфигурация восстановлена: \`${result.ip}\`\n\n`;
        
        statusMsg += `📦 *Состояние сервера:*\n`;
        statusMsg += `├ Контейнер: ${health.containerRunning ? '✅' : '❌'}\n`;
        statusMsg += `├ Интерфейс: ${health.interfaceUp ? '✅' : '❌'}\n`;
        statusMsg += `└ WireGuard: ${health.interfaceReady ? '✅ Готов' : '⏳ Запускается'}\n`;
        
        if (health.interfaceReady) {
          statusMsg += `\n📊 Всего клиентов: ${health.peerCount}`;
        }
        
        if (!health.healthy) {
          statusMsg += `\n\n⚠️ Обнаружены проблемы, проверьте статус`;
        }
        
        await this.bot.sendMessage(chatId, statusMsg, { parse_mode: 'Markdown' });
      }
      
      logger.info(`Sent ${version} config to chat ${chatId}: ${result.filename}`);

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
      logger.info(`Getting stats for chat ${chatId}`);

      const stats = await this.awgManager.getStats();

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
          
          // Определяем статус контейнера
          if (container.restarting) {
            statsMessage += `🔄 Перезапускается...\n`;
          } else if (container.running) {
            statsMessage += `✅ Запущен\n`;
          } else if (container.stopped) {
            statsMessage += `⚠️ Остановлен\n`;
          } else {
            statsMessage += `❓ Неизвестно\n`;
          }
          
          statsMessage += `📦 Контейнер: \`${container.name}\`\n`;
          statsMessage += `👥 Клиентов: ${container.clients}\n`;
          statsMessage += `🔌 Порт: ${container.port}\n`;
          
          // Показываем активных клиентов только если контейнер работает
          if (container.running) {
            statsMessage += `👤 Активных: ${activeClients}\n`;
          } else if (container.restarting) {
            statsMessage += `⏳ Ожидание запуска...\n`;
          }
          
          statsMessage += '\n';
        } else {
          // Контейнер не найден
          statsMessage += `*AWG ${version.toUpperCase()}:*\n`;
          statsMessage += `❌ Не установлен\n\n`;
        }
      }

      return statsMessage;

    } catch (error) {
      logger.error(`Error getting stats for chat ${chatId}:`, error);
      throw error;
    }
  }

  async showMainMenu(chatId) {
    try {
      logger.info(`Showing main menu for chat ${chatId}`);
      
      // Показываем сообщение о загрузке
      const loadingMsg = await this.bot.sendMessage(chatId, '⏳ Загружаю...', { parse_mode: 'Markdown' });
      
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
      
      // Удаляем сообщение о загрузке
      try {
        await this.bot.deleteMessage(chatId, loadingMsg.message_id);
      } catch (error) {
        logger.warn(`Failed to delete loading message: ${error.message}`);
      }
      
      // Отправляем главное меню
      await this.sendNewMessage(
        chatId,
        `🔐 *Панель администратора*\n\n${statsMessage}Выберите действие:`,
        { parse_mode: 'Markdown', reply_markup: keyboard }
      );
    } catch (error) {
      logger.error(`Error showing main menu for chat ${chatId}:`, error);
      this.bot.sendMessage(chatId, `❌ Ошибка: ${error.message}`);
    }
  }

  async showAwgClientsList(chatId, version, shouldUpdate = false, callbackQueryId = null) {
    try {
      logger.info(`Showing ${version} clients list for chat ${chatId}, shouldUpdate: ${shouldUpdate}`);

      // Initialize AWG manager if needed
      if (!this.awgManager.initialized) {
        await this.awgManager.initialize();
      }

      // Find container by version
      const container = this.awgManager.availableContainers.find(c => c.version === version);
      
      if (!container) {
        if (shouldUpdate) {
          await this.updateMessage(
            chatId,
            `📋 *Подробнее Клиенты ${version.toUpperCase()}*\n\n❌ Контейнер версии ${version} не найден`,
            { parse_mode: 'Markdown' },
            callbackQueryId
          );
        } else {
          await this.sendNewMessage(
            chatId,
            `📋 *Подробнее Клиенты ${version.toUpperCase()}*\n\n❌ Контейнер версии ${version} не найден`,
            { parse_mode: 'Markdown' }
          );
        }
        return;
      }

      // Проверяем статус контейнера
      const containerStatus = await this.awgManager.checkContainer(container.name);
      let containerStatusMessage = '';
      let serverStatusEmoji = '';
      
      if (containerStatus.restarting) {
        containerStatusMessage = '\n🔄 *Статус контейнера:* Перезапускается\n';
        serverStatusEmoji = '🔄';
      } else if (!containerStatus.running) {
        containerStatusMessage = '\n⚠️ *Статус контейнера:* Остановлен\n';
        serverStatusEmoji = '⚠️';
      } else {
        serverStatusEmoji = '✅';
      }

      const clients = await this.awgManager.getClients(container.name);

      // Проверяем статус WireGuard интерфейса только если контейнер работает
      const configFile = version === 'v2' ? 'awg0' : 'wg0';
      let interfaceStatus = 'unknown';
      let interfaceMessage = '';
      
      if (containerStatus.running) {
        try {
          await execAsync(`docker exec ${container.name} wg show ${configFile} 2>&1`);
          interfaceStatus = 'ready';
          interfaceMessage = '\n✅ *Статус интерфейса:* Работает\n';
        } catch (error) {
          const errorMsg = error.message || error.toString();
          
          if (errorMsg.includes('does not exist') || errorMsg.includes('No such device')) {
            interfaceStatus = 'starting';
            interfaceMessage = '\n⏳ *Статус интерфейса:* Запускается\n';
            serverStatusEmoji = '⏳';
          } else if (errorMsg.includes('Unable to access interface')) {
            interfaceStatus = 'error';
            interfaceMessage = '\n⚠️ *Статус интерфейса:* Ошибка\n';
            serverStatusEmoji = '⚠️';
          } else {
            interfaceStatus = 'unknown';
            interfaceMessage = '\n❓ *Статус интерфейса:* Неизвестно\n';
            serverStatusEmoji = '❓';
          }
        }
      }

      // Определяем доступность сервера
      const serverAvailable = (containerStatus.running && interfaceStatus === 'ready');
      
      // Получаем статистику клиентов только если сервер доступен
      let clientsStats = {};
      if (serverAvailable) {
        clientsStats = await this.getClientsStats(container.name, version);
      }

      if (clients.length === 0) {
        if (shouldUpdate) {
          await this.updateMessage(
            chatId,
            `📋 *Подробнее Клиенты ${version.toUpperCase()}*\n\n📦 Контейнер: \`${container.name}\`${containerStatusMessage}${interfaceMessage}\n\nНет активных клиентов`,
            { parse_mode: 'Markdown' },
            callbackQueryId
          );
        } else {
          await this.sendNewMessage(
            chatId,
            `📋 *Подробнее Клиенты ${version.toUpperCase()}*\n\n📦 Контейнер: \`${container.name}\`${containerStatusMessage}${interfaceMessage}\n\nНет активных клиентов`,
            { parse_mode: 'Markdown' }
          );
        }
        return;
      }

      let clientsMessage = `📋 *Подробнее Клиенты ${version.toUpperCase()}*\n\n`;
      
      // Новый формат статуса сервера
      clientsMessage += `📦 *Состояние сервера:*\n`;
      clientsMessage += `├ Контейнер: ${containerStatus.running ? '✅' : '❌'}\n`;
      clientsMessage += `├ Интерфейс: ${interfaceStatus === 'ready' ? '✅' : interfaceStatus === 'starting' ? '⏳' : '❌'}\n`;
      clientsMessage += `└ WireGuard: ${serverAvailable ? '✅ Готов' : interfaceStatus === 'starting' ? '⏳ Запускается' : '❌ Недоступен'}\n`;
      
      // Добавляем предупреждения если сервер недоступен
      if (!serverAvailable) {
        if (interfaceStatus === 'starting') {
          clientsMessage += `\n⏳ *Сервер перезапускается...*\n`;
          clientsMessage += `Статус клиентов будет доступен через несколько секунд\n`;
        } else if (interfaceStatus === 'error') {
          clientsMessage += `\n⚠️ *Требуется проверка сервера*\n`;
        } else if (!containerStatus.running) {
          clientsMessage += `\n⚠️ *Контейнер остановлен*\n`;
        }
      }
      
      clientsMessage += `\nВсего: ${clients.length}\n`;
      
      // Добавляем временную метку ТОЛЬКО когда сервер доступен
      // Это позволяет отслеживать состояние по поведению кнопки "Обновить":
      // - Новое окно = интерфейс не готов (текст не меняется без метки)
      // - Обновление в том же окне = интерфейс готов (текст меняется с меткой)
      if (serverAvailable) {
        const timestamp = new Date().toLocaleTimeString('ru-RU', {
          hour: '2-digit',
          minute: '2-digit',
          second: '2-digit'
        });
        clientsMessage += `🕐 Обновлено: ${timestamp}\n`;
      }
      
      clientsMessage += `\n`;
      
      // Создаём кнопки для каждого клиента
      const keyboard = {
        inline_keyboard: []
      };
      
      clients.forEach((ip, index) => {
        const stats = clientsStats[ip] || {};
        const lastSeen = stats.lastHandshake || '❌';
        const transfer = stats.transfer || 'нет данных';
        
        // Если сервер недоступен - показываем причину
        if (!serverAvailable) {
          if (interfaceStatus === 'starting') {
            clientsMessage += `${ip} ⏳ (интерфейс запускается)\n`;
          } else if (interfaceStatus === 'error') {
            clientsMessage += `${ip} ⚠️ (ошибка интерфейса)\n`;
          } else if (!containerStatus.running) {
            clientsMessage += `${ip} ⚠️ (контейнер остановлен)\n`;
          } else {
            clientsMessage += `${ip} ${serverStatusEmoji} (сервер недоступен)\n`;
          }
        } else if (lastSeen === '❌') {
          // Сервер доступен, но клиент неактивен
          clientsMessage += `${ip} ❌ (клиент неактивен)\n`;
        } else {
          // Сервер доступен и клиент активен - показываем детали
          clientsMessage += `${ip} ✅\n`;
          clientsMessage += `   └ 🕐 ${lastSeen}\n`;
          if (transfer !== 'нет данных') {
            clientsMessage += `   └ 📊 ${transfer}\n`;
          }
        }
        
        // Добавляем кнопки для каждого IP
        keyboard.inline_keyboard.push([
          {
            text: `📤 ${ip}`,
            callback_data: `resend_${version}_${ip}`
          },
          {
            text: `🗑️ ${ip}`,
            callback_data: `delete_${version}_${ip}`
          }
        ]);
      });

      // Добавляем кнопки "Обновить" и "Назад"
      keyboard.inline_keyboard.push([
        { text: '🔄 Обновить', callback_data: `refresh_clients_${version}` },
        { text: '🔙 Назад', callback_data: `awg_select_${version}` }
      ]);

      if (shouldUpdate) {
        // При обновлении передаем callbackQueryId для показа popup при создании нового окна
        await this.updateMessage(chatId, clientsMessage, {
          parse_mode: 'Markdown',
          reply_markup: keyboard
        }, callbackQueryId);
        
        // Если сообщение было успешно обновлено в том же окне, отвечаем на callback
        if (callbackQueryId) {
          try {
            await this.bot.answerCallbackQuery(callbackQueryId);
          } catch (error) {
            // Игнорируем ошибку, если уже ответили
          }
        }
      } else {
        // При первом открытии используем sendNewMessage
        await this.sendNewMessage(chatId, clientsMessage, {
          parse_mode: 'Markdown',
          reply_markup: keyboard
        });
      }

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
      
    } catch (error) {
      logger.error(`Error resending config for ${ip}:`, error);
      this.bot.sendMessage(
        chatId,
        `❌ Ошибка при восстановлении конфигурации:\n${error.message}\n\n` +
        `Возможные причины:\n` +
        `• Оригинальный файл конфигурации был удалён\n` +
        `• Клиент был удалён с сервера\n\n` +
        `Попробуйте создать новую конфигурацию через /start → Конфигурации`
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
            { text: '🔙 Назад', callback_data: `awg_clients_${version}` }
          ]
        ]
      };
      
      await this.sendOrEditMessage(
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
        
        logger.info(`Restarting WireGuard interface ${configName}...`);
        await execAsync(`docker exec ${container.name} wg-quick down ${fullConfigPath} || true`);
        await execAsync(`docker exec ${container.name} wg-quick up ${fullConfigPath}`);
        
        logger.info(`Successfully deleted client ${ip} from ${container.name}`);
        
        // Используем новую функцию полной проверки здоровья сервера
        logger.info(`Starting comprehensive health check...`);
        const healthStatus = await this.awgManager.checkServerHealthAfterChange(
          container.name,
          15,  // maxAttempts
          1000 // delayMs
        );
        
        // Создаем кнопку для перехода в главное меню
        const keyboard = {
          inline_keyboard: [
            [
              { text: '🏠 Главное меню', callback_data: 'start_menu' }
            ]
          ]
        };
        
        let statusMessage = `✅ Клиент \`${ip}\` успешно удалён из ${version.toUpperCase()}\n`;
        
        // Детальная информация о состоянии сервера
        statusMessage += `\n📦 *Состояние сервера:*\n`;
        statusMessage += `├ Контейнер: ${healthStatus.containerRunning ? '✅' : '❌'} ${healthStatus.containerRunning ? 'Работает' : 'Остановлен'}\n`;
        statusMessage += `├ Интерфейс: ${healthStatus.interfaceUp ? '✅' : '❌'} ${healthStatus.interfaceUp ? 'Поднят' : 'Не активен'}\n`;
        statusMessage += `├ WireGuard: ${healthStatus.interfaceReady ? '✅' : '⏳'} ${healthStatus.interfaceReady ? 'Готов' : 'Инициализация'}\n`;
        statusMessage += `└ Проверок: ${healthStatus.attempts}/15\n`;
        
        // Информация о клиентах
        if (healthStatus.interfaceReady) {
          statusMessage += `\n📊 *Клиенты:*\n`;
          if (healthStatus.peerCount > 0) {
            statusMessage += `└ Активных: ${healthStatus.peerCount}`;
          } else {
            statusMessage += `└ Клиентов не осталось`;
          }
        } else if (healthStatus.interfaceUp && !healthStatus.interfaceReady) {
          statusMessage += `\n⏳ *Интерфейс запускается...*\n`;
          statusMessage += `└ Статус клиентов будет доступен через несколько секунд`;
        }
        
        // Предупреждения
        if (healthStatus.warnings.length > 0) {
          statusMessage += `\n\n⚠️ *Предупреждения:*\n`;
          healthStatus.warnings.slice(0, 2).forEach(warning => {
            statusMessage += `└ ${warning}\n`;
          });
        }
        
        // Ошибки
        if (healthStatus.errors.length > 0) {
          statusMessage += `\n\n❌ *Ошибки:*\n`;
          healthStatus.errors.slice(0, 2).forEach(error => {
            statusMessage += `└ ${error}\n`;
          });
          statusMessage += `\n💡 Проверьте логи: \`docker logs ${container.name}\``;
        }
        
        // Общий статус
        if (!healthStatus.healthy) {
          statusMessage += `\n\n⚠️ Сервер требует внимания`;
        }
        
        this.bot.sendMessage(
          chatId,
          statusMessage,
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
