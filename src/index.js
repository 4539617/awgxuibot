import { RouteBot } from './bot.js';
import { startPeriodicCleanup } from './fileCleanup.js';

// Start periodic cleanup (every 6 hours, delete files older than 24 hours)
startPeriodicCleanup(6, 24);

// Create and start the bot
const bot = new RouteBot();
bot.start();
