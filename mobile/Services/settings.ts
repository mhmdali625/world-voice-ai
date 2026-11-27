import { File, Paths } from 'expo-file-system';

import { setAccessToken, setBackendUrl } from './config';

// ========================================
// USER SETTINGS (saved on the phone)
// ========================================
//
// autoStart  : opening the app starts the Live Assistant by itself
//              (camera -> streaming -> hands-free listening), so a
//              blind user does not have to find any button.
// serverUrl  : the laptop's address, changeable in the app when the
//              network changes (home Wi-Fi, phone hotspot) - no
//              rebuild and no Metro restart needed. Empty = the
//              address from mobile/.env.

export type Settings = {
  autoStart: boolean;
  serverUrl: string;
  /** Matches WV_ACCESS_TOKEN on the laptop (empty = none / from mobile/.env). */
  token: string;
};

const DEFAULTS: Settings = { autoStart: false, serverUrl: '', token: '' };
const FILE_NAME = 'world-voice-settings.json';

let cached: Settings | null = null;

function settingsFile(): File {
  return new File(Paths.document, FILE_NAME);
}

export function loadSettings(): Settings {
  if (cached) return cached;
  let value: Settings = { ...DEFAULTS };
  try {
    const file = settingsFile();
    if (file.exists) {
      const parsed = JSON.parse(file.textSync()) as Partial<Settings>;
      value = {
        autoStart: parsed.autoStart === true,
        serverUrl: typeof parsed.serverUrl === 'string' ? parsed.serverUrl : '',
        token: typeof parsed.token === 'string' ? parsed.token : '',
      };
    }
  } catch {
    // Unreadable settings: defaults.
  }
  cached = value;
  if (value.serverUrl) setBackendUrl(value.serverUrl);
  if (value.token) setAccessToken(value.token);
  return value;
}

export function saveSettings(update: Partial<Settings>): Settings {
  const next: Settings = { ...loadSettings(), ...update };
  cached = next;
  setBackendUrl(next.serverUrl);
  setAccessToken(next.token);
  try {
    const file = settingsFile();
    if (!file.exists) file.create();
    void file.write(JSON.stringify(next)).catch(() => undefined);
  } catch {
    // Kept in memory for this session.
  }
  return next;
}

/** Accepts "192.168.1.20", "192.168.1.20:8000" or a full http:// URL. */
export function normalizeServerUrl(input: string): string {
  const value = input.trim();
  if (!value) return '';
  const withScheme = /^https?:\/\//i.test(value) ? value : `http://${value}`;
  const withPort = /:\d+(\/|$)/.test(withScheme.replace(/^https?:\/\//i, '')) ? withScheme : `${withScheme}:8000`;
  return withPort.replace(/\/+$/, '');
}
