import fs from 'fs';
import path from 'path';
import { config } from './config.js';

class Logger {
  constructor() {
    this.logDir = null;
    this.initialized = false;
  }

  initialize() {
    if (!this.initialized) {
      // Используем /app/logs если существует (Docker volume), иначе ./output/logs
      const dockerLogDir = '/app/logs';
      if (fs.existsSync('/app')) {
        this.logDir = dockerLogDir;
      } else {
        this.logDir = path.join(config.outputDir, 'logs');
      }
      this.ensureLogDir();
      this.initialized = true;
    }
  }

  ensureLogDir() {
    if (!fs.existsSync(this.logDir)) {
      fs.mkdirSync(this.logDir, { recursive: true });
    }
  }

  getLogFilePath() {
    this.initialize();
    const date = new Date();
    const dateStr = date.toISOString().split('T')[0]; // YYYY-MM-DD
    return path.join(this.logDir, `awgbot_${dateStr}.log`);
  }

  formatMessage(level, message, data = null) {
    this.initialize();
    const timestamp = new Date().toISOString();
    let logMessage = `[${timestamp}] [${level}] ${message}`;
    
    if (data) {
      if (data instanceof Error) {
        logMessage += `\n  Error: ${data.message}\n  Stack: ${data.stack}`;
      } else if (typeof data === 'object') {
        logMessage += `\n  Data: ${JSON.stringify(data, null, 2)}`;
      } else {
        logMessage += `\n  Data: ${data}`;
      }
    }
    
    return logMessage + '\n';
  }

  writeLog(level, message, data = null) {
    const logMessage = this.formatMessage(level, message, data);
    const logFile = this.getLogFilePath();
    
    // Write to file
    fs.appendFileSync(logFile, logMessage, 'utf8');
    
    // Also write to console
    console.log(logMessage.trim());
  }

  info(message, data = null) {
    this.writeLog('INFO', message, data);
  }

  warn(message, data = null) {
    this.writeLog('WARN', message, data);
  }

  error(message, data = null) {
    this.writeLog('ERROR', message, data);
  }

  debug(message, data = null) {
    this.writeLog('DEBUG', message, data);
  }
}

export const logger = new Logger();

// Made with Bob