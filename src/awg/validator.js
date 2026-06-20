/**
 * AWG Parameters Validator
 * Валидация параметров AmneziaWG v1 и v2
 * Основано на amneziawg-installer v5.16.1
 */

import { logger } from '../logger.js';

/**
 * Валидация Junk параметров (Jc, Jmin, Jmax)
 * @param {number} Jc - Количество junk-пакетов (1-128)
 * @param {number} Jmin - Минимальный размер junk (0-1280)
 * @param {number} Jmax - Максимальный размер junk (0-1280, >= Jmin)
 * @throws {Error} При невалидных значениях
 */
export function validateJunkParams(Jc, Jmin, Jmax) {
  // Jc: 1-128
  if (typeof Jc !== 'number' || Jc < 1 || Jc > 128) {
    throw new Error(`Jc должен быть числом 1-128, получено: ${Jc}`);
  }
  
  // Jmin: 0-1280
  if (typeof Jmin !== 'number' || Jmin < 0 || Jmin > 1280) {
    throw new Error(`Jmin должен быть числом 0-1280, получено: ${Jmin}`);
  }
  
  // Jmax: 0-1280 и >= Jmin
  if (typeof Jmax !== 'number' || Jmax < 0 || Jmax > 1280) {
    throw new Error(`Jmax должен быть числом 0-1280, получено: ${Jmax}`);
  }
  
  if (Jmax < Jmin) {
    throw new Error(`Jmax (${Jmax}) должен быть >= Jmin (${Jmin})`);
  }
  
  return true;
}

/**
 * Валидация S-параметров (padding)
 * @param {Object} interfaceConfig - Секция [Interface] конфига
 * @param {string} version - Версия AWG ('v1' или 'v2')
 * @throws {Error} При невалидных значениях
 */
export function validateSParams(interfaceConfig, version) {
  const { S1, S2, S3, S4 } = interfaceConfig;
  
  // S1: обязателен, 15-150
  if (!S1 || typeof S1 !== 'number') {
    throw new Error('S1 обязателен и должен быть числом');
  }
  if (S1 < 15 || S1 > 150) {
    throw new Error(`S1 должен быть 15-150, получено: ${S1}`);
  }
  
  // S2: обязателен, 15-150
  if (!S2 || typeof S2 !== 'number') {
    throw new Error('S2 обязателен и должен быть числом');
  }
  if (S2 < 15 || S2 > 150) {
    throw new Error(`S2 должен быть 15-150, получено: ${S2}`);
  }
  
  // КРИТИЧЕСКОЕ ОГРАНИЧЕНИЕ: S1 + 56 ≠ S2
  // Предотвращает одинаковый размер init и response сообщений
  if (S1 + 56 === S2) {
    throw new Error(
      `S1 + 56 не должно равняться S2 (${S1} + 56 = ${S1 + 56}, S2 = ${S2}). ` +
      `Это критическое ограничение AWG протокола.`
    );
  }
  
  // S3, S4: только для v2
  if (version === 'v2') {
    if (S3 !== undefined) {
      if (typeof S3 !== 'number' || S3 < 8 || S3 > 55) {
        throw new Error(`S3 должен быть 8-55, получено: ${S3}`);
      }
    }
    if (S4 !== undefined) {
      if (typeof S4 !== 'number' || S4 < 4 || S4 > 27) {
        throw new Error(`S4 должен быть 4-27, получено: ${S4}`);
      }
    }
  } else if (version === 'v1') {
    // v1 не должен иметь S3, S4
    if (S3 !== undefined || S4 !== undefined) {
      throw new Error('S3 и S4 не поддерживаются в AWG v1');
    }
  }
  
  return true;
}

/**
 * Парсинг H-диапазона из строки
 * @param {string} hValue - Значение H-параметра (например "1726271876-1813116022")
 * @returns {{start: number, end: number}|null} Диапазон или null если не диапазон
 */
function parseHRange(hValue) {
  if (!hValue || typeof hValue !== 'string') return null;
  
  if (!hValue.includes('-')) return null;
  
  const parts = hValue.split('-');
  if (parts.length !== 2) return null;
  
  const start = parseInt(parts[0], 10);
  const end = parseInt(parts[1], 10);
  
  if (isNaN(start) || isNaN(end)) return null;
  
  return { start, end };
}

/**
 * Валидация H-диапазонов (только для v2)
 * @param {Object} interfaceConfig - Секция [Interface] конфига
 * @throws {Error} При невалидных значениях
 */
export function validateHRanges(interfaceConfig) {
  const hParams = ['H1', 'H2', 'H3', 'H4'];
  const ranges = [];
  
  // Парсим все H-параметры
  for (const hName of hParams) {
    const hValue = interfaceConfig[hName];
    if (!hValue) continue;
    
    const range = parseHRange(hValue);
    if (range) {
      ranges.push({ ...range, name: hName });
    }
  }
  
  if (ranges.length === 0) {
    // Нет диапазонов - это v1 или некорректный конфиг
    return true;
  }
  
  // Проверка каждого диапазона
  for (const range of ranges) {
    // Нижняя граница >= 5 (1-4 зарезервированы WireGuard)
    if (range.start < 5) {
      throw new Error(
        `${range.name}: нижняя граница должна быть >= 5 ` +
        `(1-4 зарезервированы WireGuard), получено: ${range.start}`
      );
    }
    
    // Ширина >= 1000
    const width = range.end - range.start;
    if (width < 1000) {
      throw new Error(
        `${range.name}: ширина диапазона должна быть >= 1000, ` +
        `получено: ${width} (${range.start}-${range.end})`
      );
    }
    
    // Верхняя граница <= 2^31-1 (совместимость с Windows-клиентом)
    const MAX_INT31 = 2147483647;
    if (range.end > MAX_INT31) {
      throw new Error(
        `${range.name}: верхняя граница должна быть <= ${MAX_INT31} ` +
        `(ограничение Windows-клиента), получено: ${range.end}`
      );
    }
    
    // start < end
    if (range.start >= range.end) {
      throw new Error(
        `${range.name}: начало диапазона должно быть < конца ` +
        `(${range.start} >= ${range.end})`
      );
    }
  }
  
  // Проверка непересечения диапазонов
  for (let i = 0; i < ranges.length; i++) {
    for (let j = i + 1; j < ranges.length; j++) {
      const r1 = ranges[i];
      const r2 = ranges[j];
      
      // Проверка пересечения: диапазоны НЕ пересекаются если
      // r1.end < r2.start ИЛИ r2.end < r1.start
      const overlaps = !(r1.end < r2.start || r2.end < r1.start);
      
      if (overlaps) {
        throw new Error(
          `Диапазоны ${r1.name} (${r1.start}-${r1.end}) и ` +
          `${r2.name} (${r2.start}-${r2.end}) пересекаются`
        );
      }
      
      // Проверка зазора (границы не должны касаться)
      if (r1.end === r2.start || r2.end === r1.start) {
        throw new Error(
          `Диапазоны ${r1.name} и ${r2.name} касаются границами ` +
          `(нужен зазор >= 1)`
        );
      }
    }
  }
  
  return true;
}

/**
 * Улучшенное определение версии AWG
 * @param {Object} config - Распарсенный конфиг
 * @returns {string} 'v1' или 'v2'
 */
export function detectAwgVersion(config) {
  const iface = config.interface || {};
  
  // v2 признак #1: наличие S3 или S4
  if (iface.S3 !== undefined || iface.S4 !== undefined) {
    return 'v2';
  }
  
  // v2 признак #2: H-параметры с диапазонами (содержат '-')
  const hParams = ['H1', 'H2', 'H3', 'H4'];
  for (const hName of hParams) {
    const hValue = iface[hName];
    if (hValue && typeof hValue === 'string' && hValue.includes('-')) {
      return 'v2';
    }
  }
  
  // v2 признак #3: наличие I-параметров (CPS мимикрия)
  const iParams = ['I1', 'I2', 'I3', 'I4', 'I5'];
  for (const iName of iParams) {
    if (iface[iName] !== undefined) {
      return 'v2';
    }
  }
  
  // По умолчанию v1
  return 'v1';
}

/**
 * Полная валидация AWG конфига
 * @param {Object} config - Распарсенный конфиг
 * @param {string} [expectedVersion] - Ожидаемая версия (опционально)
 * @returns {{valid: boolean, version: string, errors: string[]}}
 */
export function validateAwgConfig(config, expectedVersion = null) {
  const errors = [];
  const iface = config.interface || {};
  
  // Определяем версию
  const version = detectAwgVersion(config);
  
  // Проверка ожидаемой версии
  if (expectedVersion && version !== expectedVersion) {
    errors.push(
      `Ожидалась версия ${expectedVersion}, обнаружена ${version}`
    );
  }
  
  try {
    // Валидация Junk параметров (если есть)
    if (iface.Jc !== undefined && iface.Jmin !== undefined && iface.Jmax !== undefined) {
      validateJunkParams(
        parseInt(iface.Jc, 10),
        parseInt(iface.Jmin, 10),
        parseInt(iface.Jmax, 10)
      );
    }
  } catch (error) {
    errors.push(`Junk параметры: ${error.message}`);
  }
  
  try {
    // Валидация S-параметров
    const sParams = {
      S1: iface.S1 ? parseInt(iface.S1, 10) : undefined,
      S2: iface.S2 ? parseInt(iface.S2, 10) : undefined,
      S3: iface.S3 ? parseInt(iface.S3, 10) : undefined,
      S4: iface.S4 ? parseInt(iface.S4, 10) : undefined
    };
    validateSParams(sParams, version);
  } catch (error) {
    errors.push(`S параметры: ${error.message}`);
  }
  
  try {
    // Валидация H-диапазонов (для v2)
    if (version === 'v2') {
      validateHRanges(iface);
    }
  } catch (error) {
    errors.push(`H диапазоны: ${error.message}`);
  }
  
  const valid = errors.length === 0;
  
  if (valid) {
    logger.info(`✅ AWG ${version} конфиг валиден`);
  } else {
    logger.error(`❌ AWG ${version} конфиг невалиден:`);
    errors.forEach(err => logger.error(`   - ${err}`));
  }
  
  return { valid, version, errors };
}

/**
 * Конвертация H-параметра из v2 диапазона в v1 одно значение
 * @param {string} hValue - Значение H-параметра
 * @returns {string} Первое значение из диапазона или исходное значение
 */
export function convertHParameterV2toV1(hValue) {
  if (!hValue || typeof hValue !== 'string') {
    return hValue;
  }
  
  // v2: "1726271876-1813116022" → берем первое значение
  if (hValue.includes('-')) {
    return hValue.split('-')[0];
  }
  
  // v1: уже одно число, возвращаем как есть
  return hValue;
}

// Made with Bob
