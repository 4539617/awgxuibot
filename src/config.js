import dotenv from 'dotenv';

dotenv.config();

export const config = {
  // Поддержка обоих токенов для обратной совместимости
  // Приоритет: AWG_BOT_TOKEN > TELEGRAM_BOT_TOKEN (legacy)
  // awgbot должен использовать AWG_BOT_TOKEN, а не XUI_BOT_TOKEN!
  telegramToken: process.env.AWG_BOT_TOKEN || process.env.TELEGRAM_BOT_TOKEN,
  outputDir: './output',
  // Admin IDs - только эти пользователи могут генерировать AWG конфиги
  adminIds: process.env.ADMIN_IDS ? process.env.ADMIN_IDS.split(',').map(id => parseInt(id.trim())) : [],
  // Режим работы: если true, то токен не обязателен (для standalone генерации конфигов)
  standaloneMode: process.env.STANDALONE_MODE === 'true',
  // Разрешить обычным пользователям делать DNS запросы (по умолчанию false)
  allowUserDnsQueries: process.env.ALLOW_USER_DNS_QUERIES === 'true'
};

// Проверка токена только если не standalone режим
if (!config.standaloneMode && !config.telegramToken) {
  console.error('Error: XUI_BOT_TOKEN or AWG_BOT_TOKEN is not set in .env file');
  console.error('Hint: For standalone config generation, set STANDALONE_MODE=true');
  process.exit(1);
}
