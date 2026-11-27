import { useEffect, useState } from 'react';

// The dashboard runs on the laptop, next to the backend (loopback: no
// access code needed, and frame images are served to it only).
export const BACKEND_URL = 'http://127.0.0.1:8000';

/** GET a JSON endpoint every `ms` milliseconds; null while unavailable. */
export function usePoll<T>(path: string | null, ms: number, refresh = 0): { data: T | null; error: string | null } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!path) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const load = async () => {
      try {
        const response = await fetch(`${BACKEND_URL}${path}`, { cache: 'no-store' });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const json = (await response.json()) as T;
        if (active) {
          setData(json);
          setError(null);
        }
      } catch (e) {
        if (active) setError(e instanceof Error ? e.message : 'unavailable');
      }
      if (active) timer = setTimeout(() => { void load(); }, ms);
    };
    void load();
    return () => {
      active = false;
      if (timer) clearTimeout(timer);
    };
  }, [path, ms, refresh]);

  return { data, error };
}

export const deviceQuery = (device: string | null, first = false) =>
  device ? `${first ? '?' : '&'}device=${encodeURIComponent(device)}` : '';

export function clock(seconds: number): string {
  return new Date(seconds * 1000).toLocaleTimeString();
}

export function ago(seconds: number): string {
  if (seconds < 60) return `${Math.max(0, Math.round(seconds))} s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  return `${Math.round(seconds / 3600)} h ago`;
}

// ---------------- response types (only the fields used) ----------------

export type DeviceStatus = {
  device: string;
  session_id: string | null;
  live: boolean;
  stalled: boolean;
  fps: number | null;
  resolution: string | null;
  frames_received: number;
  viewers: number;
  last_frame_age_s: number | null;
};

export type StreamStatus = DeviceStatus & { devices: DeviceStatus[] };

export type HazardDetail = { label_en: string; position: string; in_path: boolean; certain: boolean };

export type MemoryFrame = {
  id: string;
  device: string;
  captured_at: number;
  age_s: number;
  status: string;
  keep_reason: string | null;
  enhanced: string;
  faces_blurred: number | null;
  image_url: string;
  caption: string;
  text: string;
  objects: string[];
  hazards: string[];
  score?: number | null;
  match?: string;
};

export type QaRow = {
  id: string;
  device: string;
  asked_at: number;
  question: string | null;
  question_heard: string | null;
  answer: string | null;
  answer_language: string | null;
  evidence: string | null;
  latency_s: number | null;
  first_word_s: number | null;
  frames_used: string | null;
};

export type Evidence = { id: string; label: string; image_url: string; text: string; age_s: number };

export function parseUsed(row: QaRow): { evidence: Evidence[]; model: string | null; current: boolean } {
  try {
    const parsed = JSON.parse(row.frames_used ?? '{}') as { evidence?: Evidence[]; model?: string; current?: boolean };
    return { evidence: parsed.evidence ?? [], model: parsed.model ?? null, current: Boolean(parsed.current) };
  } catch {
    return { evidence: [], model: null, current: false };
  }
}

export type ModelUse = { used_today: number; daily_limit: number; exhausted: boolean; available_again_in_s: number };

export type MemoryStats = {
  device: string | null;
  selection: Record<string, number>;
  analysis: Record<string, number | string | null>;
  store: { frames: number; pending: number | null; analyzed: number | null; failed: number | null;
    oldest: number | null; questions: number; devices: string[] };
  retention_min: number;
  gemini_budget: { used_last_minute: number; per_minute: number; day: string; user_daily_reserve: number;
    models: Record<string, ModelUse>; fallbacks: number; reused_analysis: number; rate_limited: number;
    daily_exhausted: number; background_paused_s: number };
  privacy: Record<string, number | null>;
  warnings: Record<string, number>;
  vision: { chain: string[]; usable_for_questions: string[]; last_model: Record<string, string>;
    answer_chain: { name: string; provider: string; usable: boolean }[];
    recent_fallbacks: { at: number; kind: string; model: string; reason: string }[] };
};

export type Warning = { id: string; at: number; device: string; hazard: string; position: string | null;
  certain: boolean | null; message_en: string; active: boolean };

export type TtsQuota = { next_answer_provider: string | null;
  providers: Record<string, { configured?: boolean; available?: boolean; resets_in_s?: number }> };

export type NetworkInfo = { addresses: { address: string; url: string; kind: string; recommended: boolean }[];
  access_code_required: boolean };
