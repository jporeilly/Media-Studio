import { useQuery } from "@tanstack/react-query";
import { api, qs } from "../api/client";

/**
 * Settings › Studio (services/studio_settings.py): the narration, transcription and output defaults shared by
 * everyone. The transition, pause and watermark values are the defaults a generate job starts from; the
 * Generate card prefills them and each render may change them (lib/generateOptions.ts).
 */
export interface StudioSettings {
  tts_provider: string;
  edge_tts_voice: string;
  kokoro_voice: string;
  kokoro_lang: string;
  whisper_model: string;
  ollama_model: string;
  output_folder: string;
  transition_pause: number;
  music_volume: number;
  slide_transition: string;
  transition_duration: number;
  watermark_text: string;
  watermark_position: string;
  watermark_opacity: number;
}

/** One selectable value, with the note the form shows as its hint. */
export interface Option {
  value: string;
  label: string;
  note?: string;
}

export interface Range {
  min: number;
  max: number;
  step: number;
  unit?: string;
}

/** What the form renders from (services.studio_settings.describe). */
export interface StudioOptions {
  tts_provider: { options: Option[] };
  kokoro_lang: { options: Option[] };
  whisper_model: { options: Option[] };
  transition_pause: Range;
  music_volume: Range;
  slide_transition: { options: Option[] };
  transition_duration: Range;
  watermark_position: { options: Option[] };
  watermark_opacity: Range;
}

/** What GET/PUT /api/settings/studio return. */
export interface StudioPayload {
  settings: StudioSettings;
  options: StudioOptions;
}

export const STUDIO_QUERY_KEY = ["studio-settings"];

/**
 * True for a real /api/settings/studio body. A backend older than this route answers the path with
 * the SPA's index.html (HTTP 200), which the client would otherwise carry around as "data".
 */
export function isStudioPayload(data: unknown): data is StudioPayload {
  if (!data || typeof data !== "object") return false;
  const d = data as Record<string, unknown>;
  return !!d.settings && typeof d.settings === "object" && !!d.options && typeof d.options === "object";
}

/** The studio settings. Any signed-in user may read them: the project pages preselect the narration from here. */
export function useStudioSettings() {
  return useQuery({
    queryKey: STUDIO_QUERY_KEY,
    queryFn: async () => {
      const data = await api.get<unknown>("/api/settings/studio");
      if (!isStudioPayload(data)) {
        throw new Error("The backend did not return the studio settings — it may be running an older version; restart it and reload.");
      }
      return data;
    },
    staleTime: 60_000,
  });
}

/** One voice as GET /api/voices lists it (api/routers/media.py). */
export interface Voice {
  voice_id: string;
  name: string;
  locale: string;
  gender: string | null;
}

/** GET /api/voices?provider=…: never a 500 — an unavailable provider comes back as an empty list plus `error`. */
export interface VoicesPayload {
  provider: string;
  voices: Voice[];
  error: string | null;
  notice: string | null;
}

/** The voices of one provider. Fetched once per provider (the lists change rarely; the Studio card refreshes them on save). */
export function useVoices(provider: string, enabled = true) {
  return useQuery({
    queryKey: ["voices", provider],
    queryFn: () => api.get<VoicesPayload>(`/api/voices${qs({ provider })}`),
    enabled: enabled && !!provider,
    staleTime: Infinity,
  });
}

/** The keys of `draft` whose values differ from `saved`: the partial body a PUT sends. */
export function changedSettings(saved: StudioSettings, draft: StudioSettings): Partial<StudioSettings> {
  const out: Record<string, unknown> = {};
  for (const key of Object.keys(draft) as (keyof StudioSettings)[]) {
    if (draft[key] !== saved[key]) out[key] = draft[key];
  }
  return out as Partial<StudioSettings>;
}

/** The configured default voice for a provider (Settings › Studio). */
export function defaultVoiceFor(settings: StudioSettings | undefined, provider: string): string {
  if (!settings) return "";
  return provider === "kokoro" ? settings.kokoro_voice : settings.edge_tts_voice;
}

/**
 * The voice to preselect: `preferred` (the studio default) when the list carries it, else the first
 * en-US voice, else the first voice. "" for an empty list — the server then uses the studio default.
 */
export function pickVoice(voices: Voice[], preferred: string): string {
  if (!voices.length) return "";
  if (preferred && voices.some((v) => v.voice_id === preferred)) return preferred;
  return (voices.find((v) => v.locale === "en-US") ?? voices[0]).voice_id;
}

/**
 * Options for a voice Select that keep the current value selectable even when the list does not
 * carry it, so a Save cannot silently drop a configured voice. The label says only what is known:
 * "(loading…)" while the list is still on its way, the bare id when no list arrived at all (the
 * provider was offline), and "(not in the list)" once a real list is here without it.
 */
export function withCurrent(voices: Voice[], current: string, loading = false): Option[] {
  const options: Option[] = voices.map((v) => ({ value: v.voice_id, label: v.name }));
  if (current && !voices.some((v) => v.voice_id === current)) {
    const label = loading ? `${current} (loading…)` : voices.length ? `${current} (not in the list)` : current;
    options.unshift({ value: current, label });
  }
  return options;
}

/** A finite number within the range's bounds. */
export function inRange(value: number, range: Range): boolean {
  return Number.isFinite(value) && value >= range.min && value <= range.max;
}
