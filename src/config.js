import dotenv from 'dotenv';

dotenv.config();

export const config = {
  telegramToken: process.env.TELEGRAM_BOT_TOKEN,
  outputDir: './output',
  // Admin IDs - только эти пользователи могут генерировать AWG конфиги
  adminIds: process.env.ADMIN_IDS ? process.env.ADMIN_IDS.split(',').map(id => parseInt(id.trim())) : []
};

if (!config.telegramToken) {
  console.error('Error: TELEGRAM_BOT_TOKEN is not set in .env file');
  process.exit(1);
}

// Made with Bob
