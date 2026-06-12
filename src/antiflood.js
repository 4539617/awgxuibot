/**
 * Anti-flood protection for bot
 * Limits number of requests per user per time window
 */
export class AntiFlood {
  constructor(maxRequests = 5, windowMs = 60000) {
    this.maxRequests = maxRequests; // Maximum requests per window
    this.windowMs = windowMs; // Time window in milliseconds
    this.userRequests = new Map(); // Map of userId -> array of timestamps
  }

  /**
   * Check if user is allowed to make a request
   * @param {number} userId - Telegram user ID
   * @returns {Object} - { allowed: boolean, remainingTime?: number }
   */
  checkLimit(userId) {
    const now = Date.now();
    
    // Get user's request history
    if (!this.userRequests.has(userId)) {
      this.userRequests.set(userId, []);
    }
    
    const requests = this.userRequests.get(userId);
    
    // Remove old requests outside the time window
    const validRequests = requests.filter(timestamp => now - timestamp < this.windowMs);
    this.userRequests.set(userId, validRequests);
    
    // Check if user exceeded the limit
    if (validRequests.length >= this.maxRequests) {
      const oldestRequest = Math.min(...validRequests);
      const remainingTime = Math.ceil((this.windowMs - (now - oldestRequest)) / 1000);
      return {
        allowed: false,
        remainingTime: remainingTime
      };
    }
    
    // Add current request
    validRequests.push(now);
    this.userRequests.set(userId, validRequests);
    
    return { allowed: true };
  }

  /**
   * Get user's remaining requests
   * @param {number} userId - Telegram user ID
   * @returns {number} - Number of remaining requests
   */
  getRemainingRequests(userId) {
    if (!this.userRequests.has(userId)) {
      return this.maxRequests;
    }
    
    const now = Date.now();
    const requests = this.userRequests.get(userId);
    const validRequests = requests.filter(timestamp => now - timestamp < this.windowMs);
    
    return Math.max(0, this.maxRequests - validRequests.length);
  }

  /**
   * Clear old entries periodically to prevent memory leak
   */
  startCleanup() {
    setInterval(() => {
      const now = Date.now();
      for (const [userId, requests] of this.userRequests.entries()) {
        const validRequests = requests.filter(timestamp => now - timestamp < this.windowMs);
        if (validRequests.length === 0) {
          this.userRequests.delete(userId);
        } else {
          this.userRequests.set(userId, validRequests);
        }
      }
    }, this.windowMs);
  }
}
