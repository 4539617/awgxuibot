import fs from 'fs';
import path from 'path';
import { config } from './config.js';

/**
 * Clean up old files from output directory
 * @param {number} maxAgeHours - Maximum age of files in hours
 */
export function cleanupOldFiles(maxAgeHours = 24) {
  try {
    if (!fs.existsSync(config.outputDir)) {
      return;
    }

    const now = Date.now();
    const maxAge = maxAgeHours * 60 * 60 * 1000; // Convert hours to milliseconds

    const files = fs.readdirSync(config.outputDir);
    let deletedCount = 0;

    for (const file of files) {
      const filepath = path.join(config.outputDir, file);
      const stats = fs.statSync(filepath);

      // Check if file is older than maxAge
      if (now - stats.mtimeMs > maxAge) {
        fs.unlinkSync(filepath);
        deletedCount++;
        console.log(`Deleted old file: ${file}`);
      }
    }

    if (deletedCount > 0) {
      console.log(`Cleanup completed: ${deletedCount} file(s) deleted`);
    }
  } catch (error) {
    console.error('Error during cleanup:', error.message);
  }
}

/**
 * Start periodic cleanup
 * @param {number} intervalHours - Cleanup interval in hours
 * @param {number} maxAgeHours - Maximum age of files in hours
 */
export function startPeriodicCleanup(intervalHours = 6, maxAgeHours = 24) {
  // Run cleanup immediately
  cleanupOldFiles(maxAgeHours);

  // Schedule periodic cleanup
  const intervalMs = intervalHours * 60 * 60 * 1000;
  setInterval(() => {
    cleanupOldFiles(maxAgeHours);
  }, intervalMs);

  console.log(`Periodic cleanup started: every ${intervalHours} hours, deleting files older than ${maxAgeHours} hours`);
}

// Made with Bob
