// ========================================
// BACKEND CONFIGURATION (single source)
// ========================================
//
// Previously the laptop IP was hardcoded in three files
// (192.168.0.103). When the router gave the laptop a new
// address (.111) every request failed.
//
// The URL now comes from mobile/.env:
//
//   EXPO_PUBLIC_BACKEND_URL=http://192.168.0.111:8000
//
// For the demo, the most reliable option is USB:
//
//   adb reverse tcp:8000 tcp:8000
//   EXPO_PUBLIC_BACKEND_URL=http://localhost:8000
//
// which works regardless of Wi-Fi or IP changes.
// Expo inlines EXPO_PUBLIC_* values when Metro bundles
// the app, so restart Metro after editing .env
// (no Android rebuild needed).

const DEFAULT_BACKEND_URL = 'http://192.168.0.111:8000';

const ENV_BACKEND_URL = (
  process.env.EXPO_PUBLIC_BACKEND_URL ||
  DEFAULT_BACKEND_URL
).replace(/\/+$/, '');

// Live binding: every request reads the current value, so a server
// address typed in the app (Services/settings.ts) applies at once.
// eslint-disable-next-line import/no-mutable-exports
export let BACKEND_URL = ENV_BACKEND_URL;

/** Overrides the server address ('' = back to mobile/.env). */
export function setBackendUrl(url: string): void {
  BACKEND_URL = (url || ENV_BACKEND_URL).replace(/\/+$/, '');
}

export function defaultBackendUrl(): string {
  return ENV_BACKEND_URL;
}

// Optional shared secret, matching WV_ACCESS_TOKEN in
// backend/.env. Empty = disabled.
// Can also be typed in the app (Services/settings.ts).
// eslint-disable-next-line import/no-mutable-exports
let ACCESS_TOKEN =
  process.env.EXPO_PUBLIC_WV_TOKEN || '';

export function setAccessToken(token: string): void {
  ACCESS_TOKEN = token.trim() || process.env.EXPO_PUBLIC_WV_TOKEN || '';
}

export function authHeaders(): Record<string, string> {
  return ACCESS_TOKEN
    ? { 'X-WV-Token': ACCESS_TOKEN }
    : {};
}


// ========================================
// TIMEOUTS
// ========================================

export const ANALYZE_TIMEOUT_MS = 60000;

export const TRANSCRIBE_TIMEOUT_MS = 30000;

// Speech streaming uses an *idle* timeout: it only
// aborts if NO data arrives for this long. A total
// timeout would cut off long answers on a slow day.
export const SPEECH_IDLE_TIMEOUT_MS = 30000;


// ========================================
// HTTP ERROR WITH STATUS
// ========================================

export class BackendError extends Error {

  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = 'BackendError';
    this.status = status;
  }
}

export async function readErrorDetail(
  response: Response | { text(): Promise<string> }
): Promise<string> {

  try {
    const text = await response.text();

    try {
      const parsed = JSON.parse(text);

      if (parsed && typeof parsed.detail === 'string') {
        return parsed.detail;
      }
    } catch {
      // Not JSON.
    }

    return text.slice(0, 200);

  } catch {
    return '';
  }
}

export function isNetworkError(error: unknown): boolean {
  return (
    !(error instanceof BackendError) &&
    error instanceof Error &&
    /network|failed to fetch|abort|timed? ?out/i.test(
      error.message
    )
  );
}
