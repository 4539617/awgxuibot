/**
 * AWG Operator Presets
 * Готовые наборы параметров для обхода DPI различных операторов
 * Основано на amneziawg-installer v5.16.1 и полевых данных
 */

/**
 * Пресеты параметров обфускации для различных операторов
 */
export const AWG_PRESETS = {
  default: {
    name: 'Стандартный',
    description: 'Домашний и проводной интернет, стандартные VPS',
    Jc: { min: 3, max: 6, random: true },
    Jmin: { min: 40, max: 89, random: true },
    JmaxOffset: { min: 50, max: 250, random: true }, // Jmax = Jmin + offset
    S1: { min: 15, max: 150, random: true },
    S2: { min: 15, max: 150, random: true, avoidS1Plus56: true }
  },
  
  mobile: {
    name: 'Мобильные сети (универсальный)',
    description: 'Tele2, Yota, Мегафон, Таттелеком - универсальный профиль',
    Jc: 3, // фиксированный
    Jmin: { min: 30, max: 50, random: true },
    JmaxOffset: { min: 20, max: 80, random: true },
    S1: 86,
    S2: 3
  },
  
  tele2: {
    name: 'Tele2',
    description: 'Tele2 - проверенная конфигурация',
    Jc: 7,
    Jmin: 50,
    Jmax: 1000,
    S1: 134,
    S2: 65
  },
  
  yota: {
    name: 'Yota',
    description: 'Yota - Москва и регионы',
    Jc: 4,
    Jmin: 40,
    Jmax: 70,
    S1: 86,
    S2: 3
  },
  
  megafon: {
    name: 'Мегафон',
    description: 'Мегафон - регионы (без I1 параметра)',
    Jc: 4,
    Jmin: 40,
    Jmax: 70,
    S1: 86,
    S2: 3,
    removeI1: true // Особенность Мегафона
  },
  
  beeline: {
    name: 'Билайн',
    description: 'Билайн - работает с дефолтными параметрами',
    Jc: { min: 3, max: 6, random: true },
    Jmin: { min: 40, max: 89, random: true },
    JmaxOffset: { min: 50, max: 250, random: true },
    S1: { min: 15, max: 150, random: true },
    S2: { min: 15, max: 150, random: true, avoidS1Plus56: true }
  },
  
  tattelekom: {
    name: 'Таттелеком / Летай',
    description: 'Таттелеком / Летай - Татарстан',
    Jc: 4,
    Jmin: 40,
    Jmax: 70,
    S1: 86,
    S2: 3
  }
};

/**
 * Генерация случайного числа в диапазоне
 * @param {number} min - Минимум (включительно)
 * @param {number} max - Максимум (включительно)
 * @returns {number}
 */
function randomInt(min, max) {
  return Math.floor(Math.random() * (max - min + 1)) + min;
}

/**
 * Применение пресета и генерация конкретных значений параметров
 * @param {string} presetName - Название пресета
 * @returns {Object} Сгенерированные параметры
 */
export function applyPreset(presetName) {
  const preset = AWG_PRESETS[presetName];
  
  if (!preset) {
    throw new Error(`Неизвестный пресет: ${presetName}. Доступные: ${Object.keys(AWG_PRESETS).join(', ')}`);
  }
  
  const params = {};
  
  // Jc
  if (typeof preset.Jc === 'number') {
    params.Jc = preset.Jc;
  } else if (preset.Jc.random) {
    params.Jc = randomInt(preset.Jc.min, preset.Jc.max);
  }
  
  // Jmin
  if (typeof preset.Jmin === 'number') {
    params.Jmin = preset.Jmin;
  } else if (preset.Jmin.random) {
    params.Jmin = randomInt(preset.Jmin.min, preset.Jmin.max);
  }
  
  // Jmax
  if (typeof preset.Jmax === 'number') {
    params.Jmax = preset.Jmax;
  } else if (preset.JmaxOffset) {
    // Jmax = Jmin + offset
    const offset = preset.JmaxOffset.random
      ? randomInt(preset.JmaxOffset.min, preset.JmaxOffset.max)
      : preset.JmaxOffset.min;
    params.Jmax = params.Jmin + offset;
  }
  
  // S1
  if (typeof preset.S1 === 'number') {
    params.S1 = preset.S1;
  } else if (preset.S1.random) {
    params.S1 = randomInt(preset.S1.min, preset.S1.max);
  }
  
  // S2
  if (typeof preset.S2 === 'number') {
    params.S2 = preset.S2;
  } else if (preset.S2.random) {
    let s2;
    let attempts = 0;
    const maxAttempts = 100;
    
    do {
      s2 = randomInt(preset.S2.min, preset.S2.max);
      attempts++;
      
      // Проверка критического ограничения: S1 + 56 ≠ S2
      if (preset.S2.avoidS1Plus56 && s2 === params.S1 + 56) {
        continue; // Генерируем заново
      }
      
      break;
    } while (attempts < maxAttempts);
    
    if (attempts >= maxAttempts) {
      throw new Error('Не удалось сгенерировать S2 с соблюдением ограничения S1 + 56 ≠ S2');
    }
    
    params.S2 = s2;
  }
  
  // Специальные флаги
  if (preset.removeI1) {
    params.removeI1 = true;
  }
  
  return {
    ...params,
    presetName,
    presetDescription: preset.description
  };
}

/**
 * Получить список доступных пресетов для UI
 * @returns {Array<{key: string, name: string, description: string}>}
 */
export function getAvailablePresets() {
  return Object.entries(AWG_PRESETS).map(([key, preset]) => ({
    key,
    name: preset.name,
    description: preset.description
  }));
}

/**
 * Получить информацию о пресете
 * @param {string} presetName - Название пресета
 * @returns {Object|null}
 */
export function getPresetInfo(presetName) {
  const preset = AWG_PRESETS[presetName];
  if (!preset) return null;
  
  return {
    name: preset.name,
    description: preset.description,
    parameters: {
      Jc: preset.Jc,
      Jmin: preset.Jmin,
      Jmax: preset.Jmax,
      JmaxOffset: preset.JmaxOffset,
      S1: preset.S1,
      S2: preset.S2,
      removeI1: preset.removeI1
    }
  };
}

/**
 * Проверка, является ли пресет мобильным (для мобильных операторов)
 * @param {string} presetName - Название пресета
 * @returns {boolean}
 */
export function isMobilePreset(presetName) {
  const mobilePresets = ['mobile', 'tele2', 'yota', 'megafon', 'tattelekom'];
  return mobilePresets.includes(presetName);
}

// Made with Bob
