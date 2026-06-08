import dotenv from 'dotenv';

dotenv.config();

export const config = {
  // Поддержка обоих токенов для обратной совместимости
  // Приоритет: XUI_BOT_TOKEN > AWG_BOT_TOKEN > TELEGRAM_BOT_TOKEN (legacy)
  telegramToken: process.env.XUI_BOT_TOKEN || process.env.AWG_BOT_TOKEN || process.env.TELEGRAM_BOT_TOKEN,
  outputDir: './output',
  // Admin IDs - только эти пользователи могут генерировать AWG конфиги
  adminIds: process.env.ADMIN_IDS ? process.env.ADMIN_IDS.split(',').map(id => parseInt(id.trim())) : []
};

if (!config.telegramToken) {
  console.error('Error: XUI_BOT_TOKEN or AWG_BOT_TOKEN is not set in .env file');
  process.exit(1);
}

// Made with Bob
