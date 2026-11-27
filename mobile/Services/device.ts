import { File, Paths } from 'expo-file-system';

// ========================================
// DEVICE ID
// ========================================
//
// A stable, random id for this phone (no personal data). The backend
// keeps one live stream, frame memory and question history per device,
// so several phones can use the same server without seeing each
// other's memory. Stored in the app's document folder so memory still
// belongs to this phone after the app restarts or reconnects.

const ID_PATTERN = /^[A-Za-z0-9_-]{1,40}$/;

let cached: string | null = null;

export function getDeviceId(): string {
  if (cached) return cached;
  try {
    const file = new File(Paths.document, 'world-voice-device-id.txt');
    if (file.exists) {
      const saved = file.textSync().trim();
      if (ID_PATTERN.test(saved)) {
        cached = saved;
        return saved;
      }
    }
    const created = `phone-${Math.random().toString(36).slice(2, 10)}`;
    if (!file.exists) file.create();
    void file.write(created).catch(() => undefined);
    cached = created;
    return created;
  } catch {
    // Storage unavailable: still a consistent id for this app session.
    cached = `phone-${Math.random().toString(36).slice(2, 10)}`;
    return cached;
  }
}
