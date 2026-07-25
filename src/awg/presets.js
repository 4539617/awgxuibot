/**
 * AWG Operator Presets
 * Готовые наборы параметров для обхода DPI различных операторов
 * Основано на amneziawg-installer v5.16.1 и полевых данных
 *
 * Каждый пресет задаёт Jc/S1/S2 под конкретного оператора.
 * Полные v2-параметры (H1-H4, S3/S4, I1-I5) генерируются через generator.js.
 */

import { generate } from './generator.js';

/**
 * Пресеты параметров обфускации для различных операторов.
 * Поля Jc, Jmin, Jmax, S1, S2 — переопределяют значения генератора.
 * removeI1: true — удалить I1 из клиентского конфига (особенность Мегафона).
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
    Jc: 3,
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
    removeI1: true
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

// ── Helpers ────────────────────────────────────────────────────────────────────

function randomInt(min, max) {
  return Math.floor(Math.random() * (max - min + 1)) + min;
}

// ── Public API ─────────────────────────────────────────────────────────────────

/**
 * Применить пресет оператора и вернуть полный набор AWG-параметров.
 *
 * Для AWG v2: берём базу из generator.generate(), затем поверх накладываем
 * Jc/Jmin/Jmax/S1/S2 из пресета оператора (проверенные полевые значения).
 * H1-H4, S3/S4, I1-I5 всегда свежегенерируемые.
 *
 * Для AWG v1 (legacy): возвращаем только Jc/Jmin/Jmax/S1/S2 без v2-полей.
 *
 * @param {string} presetName - Ключ пресета из AWG_PRESETS
 * @param {'v1'|'v2'} [version='v2'] - Версия AWG
 * @param {Object} [genOpts] - Опции генератора (profile, intensity, host, browser)
 * @returns {Object} Параметры для клиентского конфига
 */
export function applyPreset(presetName, version = 'v2', genOpts = {}) {
  const preset = AWG_PRESETS[presetName];
  if (!preset) {
    throw new Error(`Неизвестный пресет: ${presetName}. Доступные: ${Object.keys(AWG_PRESETS).join(', ')}`);
  }

  // Вычисляем Jc/Jmin/Jmax/S1/S2 из пресета
  const jc = typeof preset.Jc === 'number'
    ? preset.Jc
    : randomInt(preset.Jc.min, preset.Jc.max);

  let jmin;
  if (typeof preset.Jmin === 'number') {
    jmin = preset.Jmin;
  } else {
    jmin = randomInt(preset.Jmin.min, preset.Jmin.max);
  }

  let jmax;
  if (typeof preset.Jmax === 'number') {
    jmax = preset.Jmax;
  } else if (preset.JmaxOffset) {
    const offset = preset.JmaxOffset.random
      ? randomInt(preset.JmaxOffset.min, preset.JmaxOffset.max)
      : preset.JmaxOffset.min;
    jmax = jmin + offset;
  } else {
    jmax = jmin + 200;
  }

  let s1;
  if (typeof preset.S1 === 'number') {
    s1 = preset.S1;
  } else {
    s1 = randomInt(preset.S1.min, preset.S1.max);
  }

  let s2;
  if (typeof preset.S2 === 'number') {
    s2 = preset.S2;
  } else {
    let attempts = 0;
    do {
      s2 = randomInt(preset.S2.min, preset.S2.max);
      attempts++;
      if (preset.S2.avoidS1Plus56 && s2 === s1 + 56) continue;
      break;
    } while (attempts < 100);
    if (attempts >= 100) {
      throw new Error('Не удалось сгенерировать S2 с соблюдением ограничения S1 + 56 ≠ S2');
    }
  }

  if (version === 'v1') {
    // v1: только базовые параметры
    return {
      Jc: jc, Jmin: jmin, Jmax: jmax, S1: s1, S2: s2,
      removeI1: preset.removeI1 || false,
      presetName,
      presetDescription: preset.description,
    };
  }

  // v2: генерируем полный набор, перекрываем Jc/S-значения пресетом оператора
  const base = generate({
    profile:   genOpts.profile   || 'random',
    intensity: genOpts.intensity || 'medium',
    host:      genOpts.host      || '',
    browser:   genOpts.browser   || '',
    iterCount: genOpts.iterCount || 0,
    jc,
  });

  return {
    ...base,
    Jc: jc,
    Jmin: jmin,
    Jmax: jmax,
    S1: s1,
    S2: s2,
    removeI1: preset.removeI1 || false,
    presetName,
    presetDescription: preset.description,
  };
}

/**
 * Получить список доступных пресетов для UI.
 * @returns {Array<{key: string, name: string, description: string}>}
 */
export function getAvailablePresets() {
  return Object.entries(AWG_PRESETS).map(([key, preset]) => ({
    key,
    name: preset.name,
    description: preset.description,
  }));
}

/**
 * Получить информацию о пресете.
 * @param {string} presetName
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
      removeI1: preset.removeI1,
    },
  };
}

/**
 * Проверка, является ли пресет мобильным (для мобильных операторов).
 * @param {string} presetName
 * @returns {boolean}
 */
export function isMobilePreset(presetName) {
  return ['mobile', 'tele2', 'yota', 'megafon', 'tattelekom'].includes(presetName);
}
