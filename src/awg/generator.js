/**
 * AWG 2.0 Parameter Generator
 * Direct JS port of cascade-0.9.2/internal/awgparams/generator.go
 * (which itself mirrors AwgParamGenerator.js / Vadim-Khristenko/AmneziaWG-Architect)
 *
 * Generates the full set of AWG 2.0 obfuscation parameters:
 *   Jc, Jmin, Jmax, S1, S2, S3, S4, H1-H4 (ranges), I1-I5 (CPS packets)
 *
 * Supported CPS profiles for I1:
 *   quic_initial     — QUIC Initial (RFC 9000, Long Header 0xC0-0xC3)
 *   quic_0rtt        — QUIC 0-RTT (Long Header 0xD0-0xD3)
 *   tls_client_hello — TLS 1.3 ClientHello
 *   dtls             — DTLS 1.2 ClientHello
 *   http3            — HTTP/3 over QUIC
 *   sip              — SIP REGISTER request
 *   wireguard_noise  — WireGuard Noise_IK handshake initiation
 *   dns_query        — DNS A/AAAA query (RFC 1035)
 *   tls_to_quic      — composite: TLS ClientHello → QUIC Initial
 *   quic_burst       — composite: QUIC Initial → QUIC 0-RTT → HTTP/3
 *   random           — pick one of the non-composite profiles at random
 */

import { randomBytes } from 'crypto';

// ── Browser Fingerprint (BFP) ──────────────────────────────────────────────────
// Packet size ranges [min, max] per browser per protocol.
// Key: "qi"=QUIC Initial, "q0"=QUIC 0-RTT, "h3"=HTTP/3,
//      "tls"=TLS 1.3, "nx"=WireGuard Noise_IK, "dtls"=DTLS 1.2

const BFP = {
  chrome:         { qi:[1250,1250], q0:[1250,1350], h3:[1250,1350], tls:[512,800],  nx:[1200,1250], dtls:[1100,1200] },
  firefox:        { qi:[1200,1252], q0:[1200,1300], h3:[1200,1350], tls:[512,700],  nx:[1200,1250], dtls:[1050,1200] },
  safari:         { qi:[1250,1252], q0:[1250,1300], h3:[1250,1350], tls:[512,750],  nx:[1200,1250], dtls:[1100,1200] },
  edge:           { qi:[1250,1250], q0:[1250,1350], h3:[1250,1350], tls:[512,800],  nx:[1200,1250], dtls:[1100,1200] },
  yandex_desktop: { qi:[1250,1250], q0:[1250,1350], h3:[1350,1350], tls:[512,800],  nx:[1200,1250], dtls:[1100,1200] },
  yandex_mobile:  { qi:[1232,1232], q0:[1250,1350], h3:[1350,1350], tls:[512,800],  nx:[1200,1250], dtls:[1100,1200] },
};

// ── Host pools ─────────────────────────────────────────────────────────────────

const HOST_POOLS = {
  quic_initial: [
    'yandex.net','yastatic.net','storage.yandexcloud.net','cloud.yandex.ru',
    'vk.com','mycdn.me','vk-cdn.net','ok.ru','mail.ru','avito.ru',
    'ozon.ru','wildberries.ru','kinopoisk.ru','sber.ru','tbank.ru',
    'github.com','objects.githubusercontent.com','cdn.jsdelivr.net',
    'steamstatic.com','steamcontent.com','wikipedia.org',
    'gcore.com','bunny.net','fastly.net','a248.e.akamai.net',
    'cloudfront.net','microsoft.com','icloud.com','apple.com',
    'hetzner.com','ovhcloud.com','tencentcs.com','alicdn.com',
  ],
  quic_0rtt: [
    'yandex.net','yastatic.net','vk.com','ok.ru','mail.ru',
    'avito.ru','ozon.ru','wildberries.ru','sber.ru','tbank.ru',
    'github.com','microsoft.com','apple.com','icloud.com',
    'gcore.com','fastly.net','akamaiedge.net','cloudfront.net',
  ],
  tls_client_hello: [
    'yandex.ru','yandex.net','yastatic.net','vk.com','ok.ru',
    'mail.ru','avito.ru','ozon.ru','wildberries.ru','kinopoisk.ru',
    'sber.ru','sberbank.ru','tbank.ru','vtb.ru','alfabank.ru',
    'github.com','gitlab.com','microsoft.com','office.com',
    'apple.com','icloud.com','steamcontent.com','wikipedia.org',
    'gcore.com','bunny.net','fastly.net','akamaiedge.net',
    'cloudfront.net','hetzner.com','ovhcloud.com',
  ],
  dtls: [
    'stun.yandex.net','stun1.l.google.com','stun2.l.google.com',
    'stun.cloudflare.com','stun.nextcloud.com','stun.sipnet.ru',
    'stun.services.mozilla.com','stun.voip.eutelia.it',
    'stun.ekiga.net','stunserver.stunprotocol.org',
    'stun.1und1.de','stun.t-online.de','stun.hetzner.de',
    'global.stun.twilio.com','stun.sip.us','stun.counterpath.net',
  ],
  sip: [
    'sip.beeline.ru','sip.megafon.ru','sip.mts.ru',
    'sipnet.ru','sip.zadarma.com','sip.onlinepbx.ru',
    'sip2.zadarma.com','registrar.sip.net','sip.bicom.com',
    'sip.antisip.com','proxy01.sipphone.com',
  ],
  dns_query: [
    'yandex.ru','google.com','cloudflare.com','microsoft.com','apple.com',
    'wikipedia.org','mail.ru','vk.com','amazon.com','baidu.com','youtube.com',
  ],
};

// ── Exported profiles list (for UI) ───────────────────────────────────────────

export const AWG_PROFILES = [
  { id: 'random',           label: 'Random' },
  { id: 'quic_initial',     label: 'QUIC Initial' },
  { id: 'quic_0rtt',        label: 'QUIC 0-RTT' },
  { id: 'tls_client_hello', label: 'TLS 1.3' },
  { id: 'dtls',             label: 'DTLS 1.2' },
  { id: 'http3',            label: 'HTTP/3' },
  { id: 'sip',              label: 'SIP' },
  { id: 'wireguard_noise',  label: 'Noise_IK (WireGuard)' },
  { id: 'dns_query',        label: 'DNS Query (RFC 1035)' },
  { id: 'tls_to_quic',      label: 'TLS→QUIC (composite)' },
  { id: 'quic_burst',       label: 'QUIC Burst (composite)' },
];

// ── Public API ─────────────────────────────────────────────────────────────────

/**
 * Generate a complete set of AWG 2.0 obfuscation parameters.
 *
 * @param {Object} [opts]
 * @param {string} [opts.profile='random']    CPS profile id
 * @param {string} [opts.intensity='medium']  'low' | 'medium' | 'high'
 * @param {string} [opts.host='']             Custom SNI host (empty = pick from pool)
 * @param {string} [opts.browser='']          BFP browser name, or '' for no BFP
 * @param {number} [opts.iterCount=0]         Retry counter — increases intensity
 * @param {number} [opts.jc=6]               Base Jc value (1-10)
 * @returns {{
 *   Jc,Jmin,Jmax,S1,S2,S3,S4,
 *   H1,H2,H3,H4,
 *   I1,I2,I3,I4,I5,
 *   profile: string
 * }}
 */
export function generate(opts = {}) {
  const profile   = opts.profile   || 'random';
  const intensity = opts.intensity || 'medium';
  const host      = opts.host      || '';
  const browser   = opts.browser   || '';
  const iterCount = opts.iterCount || 0;
  const baseJc    = opts.jc        || 6;

  const ivMap = { low: 1, medium: 2, high: 3 };
  let iv = ivMap[intensity] || 2;
  if (iterCount > 3) iv++;
  const boost = iterCount * 5;

  // H1-H4 — non-overlapping ranges across uint32 space
  const H1 = rRange(100_000_000);
  const H2 = rRange(1_200_000_000);
  const H3 = rRange(2_400_000_000);
  const H4 = rRange(3_600_000_000);

  // S1-S4
  let S1 = Math.min(64, rnd(15, 32) + boost);
  let S2 = Math.min(64, rnd(15, 32) + boost);
  if (S2 === S1 + 56) S2++; // критичное ограничение: S1+56 ≠ S2
  const S3 = Math.min(64, rnd(8, 24) + boost);
  const S4 = Math.min(32, rnd(6, 18) + boost);

  // Jc / Jmin / Jmax
  const jcExtra = intensity === 'high' ? 2 : 0;
  const Jc   = Math.max(3, Math.min(10, baseJc + jcExtra));
  const Jmin = 64 + boost * 2;
  const Jmax = Math.min(1280, 256 + iv * 150 + boost * 10);

  // Resolve profile
  let resolvedProfile = profile;
  if (resolvedProfile === 'random') {
    const pool = [
      'quic_initial','quic_0rtt','tls_client_hello',
      'dtls','http3','sip','wireguard_noise','dns_query',
    ];
    resolvedProfile = pool[rnd(0, pool.length - 1)];
  }

  const I1 = genI1(resolvedProfile, iv, host, browser);
  const I2 = mkEntropy(1, iv);
  const I3 = mkEntropy(2, iv);
  const I4 = mkEntropy(3, iv);
  const I5 = mkEntropy(4, iv);

  return { Jc, Jmin, Jmax, S1, S2, S3, S4, H1, H2, H3, H4, I1, I2, I3, I4, I5, profile: resolvedProfile };
}

// ── Low-level helpers ──────────────────────────────────────────────────────────

/** Random integer in [a, b] inclusive. */
function rnd(a, b) {
  if (b <= a) return a;
  return a + Math.floor(Math.random() * (b - a + 1));
}

/** n random bytes as lowercase hex string. Uses crypto.randomBytes for entropy. */
function rh(n) {
  if (n <= 0) return '';
  return randomBytes(n).toString('hex');
}

/**
 * Format value as hex padded to byteLen bytes (byteLen*2 hex chars).
 * Mirrors Go hexPad(value, byteLen).
 */
function hexPad(value, byteLen) {
  let h = value.toString(16);
  while (h.length < byteLen * 2) h = '0' + h;
  if (h.length > byteLen * 2) h = h.slice(h.length - byteLen * 2);
  return h;
}

/** Ensure hex string has even length (required for valid hex). */
function assertEvenHex(h) {
  if (h.length % 2 !== 0) h += '0';
  return h;
}

/** Generate H-range string "start-end" with base offset. Mirrors Go rRange(base). */
function rRange(base) {
  const s = base + rnd(0, 500_000);
  return `${s}-${s + rnd(1_000, 50_000)}`;
}

/** Pick a random host from the pool for profile, or return customHost. */
function getHost(poolKey, customHost) {
  if (customHost) return customHost;
  const pool = HOST_POOLS[poolKey] || HOST_POOLS.tls_client_hello;
  return pool[rnd(0, pool.length - 1)];
}

/**
 * Return a random value within BFP range for (browser, key),
 * or fallback if browser unknown or key not applicable.
 */
function bfpRc(browser, key, fallback) {
  const tbl = BFP[browser];
  if (!tbl || !tbl[key]) return fallback;
  const [lo, hi] = tbl[key];
  return rnd(lo, hi);
}

// ── CPS packet generators ──────────────────────────────────────────────────────

function mkQUICi(iv, host, browser) {
  const dcid     = rnd(8, 20);
  const scid     = rnd(0, 20);
  const tokenLen = rnd(0, 1) === 1 ? rnd(8, 32) : 0;
  const sniRc    = Math.min(host.length + rnd(0, 6), 64);
  const rLen     = Math.min(rnd(20, 80) * iv, 500);

  const h = assertEvenHex(
    hexPad(0xc0 | rnd(0, 3), 1) +
    '00000001' +
    hexPad(dcid, 1) + rh(dcid) +
    hexPad(scid, 1) + rh(scid) +
    hexPad(tokenLen, 1) + rh(tokenLen) +
    rh(4)
  );
  return `<b 0x${h}><rc ${bfpRc(browser, 'qi', sniRc)}><t><r ${rLen}>`;
}

function mkQUIC0(iv, host, browser) {
  const dcid       = rnd(8, 20);
  const scid       = rnd(0, 20);
  const ticketHint = Math.min(host.length + rnd(4, 16), 48);
  const rLen       = Math.min(rnd(30, 120) * iv, 600);

  const h = assertEvenHex(
    hexPad(0xd0 | rnd(0, 3), 1) +
    '00000001' +
    hexPad(dcid, 1) + rh(dcid) +
    hexPad(scid, 1) + rh(scid) +
    rh(4)
  );
  return `<b 0x${h}><t><r ${rLen}><rc ${bfpRc(browser, 'q0', ticketHint)}>`;
}

function mkTLS(iv, host, browser) {
  const recLen = rnd(300, 550);
  const hsLen  = recLen - rnd(4, 9);
  const sniExt = 2 + 2 + 2 + 1 + 2 + host.length;
  const sniRc  = Math.min(sniExt, 64);
  const rLen   = Math.min(rnd(20, 60) * iv, 300);

  const h = assertEvenHex(
    '160301' +
    hexPad(recLen, 2) +
    '01' +
    hexPad(hsLen, 3) +
    '0303' +
    rh(32)
  );
  return `<b 0x${h}><rc ${bfpRc(browser, 'tls', sniRc)}><r ${rLen}><t>`;
}

function mkNoise(iv, browser) {
  const rLen  = Math.min(rnd(10, 40) * iv, 200);
  const rcLen = rnd(4, 12);
  return `<b 0x01000000${rh(4)}><b 0x${rh(32)}><b 0x${rh(48)}><b 0x${rh(28)}><r ${rLen}><t><rc ${bfpRc(browser, 'nx', rcLen)}>`;
}

function mkDTLS(iv, host, browser) {
  const fragLen = rnd(100, 300);
  const sniRc   = Math.min(host.length + rnd(2, 8), 60);
  const epoch   = rnd(0, 255);
  const rLen    = Math.min(rnd(15, 50) * iv, 250);

  const h = assertEvenHex(
    '16' +
    'fefd' +
    hexPad(epoch, 2) +
    rh(6) +
    hexPad(fragLen, 2) +
    '01' +
    rh(6) +
    'fefd0000' +
    rh(4) +
    rh(32)
  );
  return `<b 0x${h}><rc ${bfpRc(browser, 'dtls', sniRc)}><t><r ${rLen}>`;
}

function mkHTTP3(iv, host, browser) {
  const ptypes = [0xc0, 0xc1, 0xc2, 0xc3, 0xe0, 0xe1, 0xe2];
  const dcid   = rnd(8, 20);
  const scid   = rnd(0, 20);
  const sniLen = Math.min(host.length + 9 + rnd(0, 6), 64);
  const rLen   = Math.min(rnd(30, 100) * iv, 500);

  const h = assertEvenHex(
    hexPad(ptypes[rnd(0, ptypes.length - 1)], 1) +
    '00000001' +
    hexPad(dcid, 1) + rh(dcid) +
    hexPad(scid, 1) + rh(scid) +
    rh(4)
  );
  return `<b 0x${h}><rc ${bfpRc(browser, 'h3', sniLen)}><r ${rLen}><t>`;
}

function mkSIP(iv, host) {
  const hostHex = Buffer.from(host).toString('hex');
  const h = assertEvenHex(
    '524547495354455220736970' + // "REGISTER sip"
    '3a' +                       // ":"
    hostHex +
    '20' +                       // " "
    rh(4)
  );
  const rcVal = Math.min(host.length + rnd(8, 24) * iv, 150);
  const rLen  = Math.min(rnd(5, 30) * iv, 120);
  return `<b 0x${h}><rc ${rcVal}><t><r ${rLen}>`;
}

/** Encode hostname into DNS label format (RFC 1035 §3.1). */
function encodeDNSName(name) {
  let result = '';
  for (const label of name.split('.')) {
    if (!label) continue;
    result += hexPad(label.length, 1);
    result += Buffer.from(label).toString('hex');
  }
  result += '00'; // root label
  return result;
}

function mkDNS(host) {
  const subs = ['www', 'mail', 'api', 'cdn', 'static', 'img', 'm', 'ns1'];
  const queryName = rnd(0, 1) === 1 ? `${subs[rnd(0, subs.length - 1)]}.${host}` : host;
  const qtype = rnd(0, 1) === 1 ? '001c' : '0001'; // AAAA or A

  const h = assertEvenHex(
    rh(2) +           // Transaction ID
    '0100' +          // Flags: standard query, recursion desired
    '0001' +          // QDCOUNT: 1
    '000000000000' +  // ANCOUNT/NSCOUNT/ARCOUNT: 0
    encodeDNSName(queryName) +
    qtype +
    '0001'            // QCLASS: IN
  );
  return `<b 0x${h}>`;
}

function mkTLStoQUIC(iv, host, browser) {
  return mkTLS(iv, getHost('tls_client_hello', host), browser) +
         mkQUICi(iv, getHost('quic_initial', host), browser);
}

function mkQUICBurst(iv, host, browser) {
  return mkQUICi(iv, getHost('quic_initial', host), browser) +
         mkQUIC0(iv, getHost('quic_0rtt', host), browser) +
         mkHTTP3(iv, getHost('quic_initial', host), browser);
}

/**
 * Generate entropy packet for I2-I5.
 * <c> tag excluded — causes issues with some AWG clients.
 */
function mkEntropy(idx, iv) {
  const rLen  = Math.min(rnd(10, 40) * iv, 300);
  const rcLen = rnd(4, 12);
  const rdLen = rnd(4, 8);

  const b  = iv >= 2 ? `<b 0x${rh(rnd(4, 8 * iv))}>` : '';
  const r  = `<r ${rLen}>`;
  const t  = '<t>';
  const rc = `<rc ${rcLen}>`;
  const rd = `<rd ${rdLen}>`;

  const patterns = [
    b + r + t + rc + rd,
    t + b + r + rc + rd,
    rc + b + r + t + rd,
    t + r + rc + b + rd,
    r + rc + b + t + rd,
  ];

  const res = patterns[(idx + rnd(0, 4)) % patterns.length];
  return res || '<r 10>';
}

function genI1(profile, iv, host, browser) {
  switch (profile) {
    case 'quic_initial':     return mkQUICi(iv, getHost('quic_initial', host), browser);
    case 'quic_0rtt':        return mkQUIC0(iv, getHost('quic_0rtt', host), browser);
    case 'tls_client_hello': return mkTLS(iv, getHost('tls_client_hello', host), browser);
    case 'wireguard_noise':  return mkNoise(iv, browser);
    case 'dtls':             return mkDTLS(iv, getHost('dtls', host), browser);
    case 'http3':            return mkHTTP3(iv, getHost('quic_initial', host), browser);
    case 'sip':              return mkSIP(iv, getHost('sip', host));
    case 'dns_query':        return mkDNS(getHost('dns_query', host));
    case 'tls_to_quic':      return mkTLStoQUIC(iv, host, browser);
    case 'quic_burst':       return mkQUICBurst(iv, host, browser);
    default:                 return mkQUICi(iv, getHost('quic_initial', host), browser);
  }
}
