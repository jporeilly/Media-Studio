import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { SlidersHorizontal } from "lucide-react";
import { api, errorMessage } from "../../api/client";
import { useAuth } from "../../context/AuthContext";
import {
  STUDIO_QUERY_KEY,
  changedSettings,
  inRange,
  useStudioSettings,
  useVoices,
  withCurrent,
  tokenState,
  type Option,
  type StudioChanges,
  type StudioPayload,
  type StudioSettings,
} from "../../lib/studioSettings";
import { PRIVACIES } from "../../lib/vimeo";
import { Button, Card, ErrorBox, Field, Input, Select, Spinner } from "../ui";

const VIMEO_TOKEN_HINT =
  "A personal access token from developer.vimeo.com (My apps › your app › Generate an access token) with the scopes public private upload edit video_files. Stored in the studio's config and never shown again; MEDIA_STUDIO_VIMEO_TOKEN in the server's environment overrides it. Unlisted and Private videos need Vimeo Plus or higher.";

function Options({ options }: { options: Option[] }) {
  return <>{options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}</>;
}

/** "Studio": the narration, transcription and output defaults every job starts from. Admins edit them; everyone else reads them. */
export function StudioCard() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const qc = useQueryClient();
  const studio = useStudioSettings();
  const edge = useVoices("edge_tts");
  const kokoro = useVoices("kokoro");
  const [draft, setDraft] = useState<StudioSettings | null>(null);
  // The Vimeo token is write-only: typed here, sent once, never read back (null = untouched; "" = clear it).
  const [vimeoToken, setVimeoToken] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const form = draft ?? studio.data?.settings ?? null;
  const options = studio.data?.options ?? null;
  const settingChanges: StudioChanges = form && studio.data ? changedSettings(studio.data.settings, form) : {};
  const tokenChange = vimeoToken !== null && (vimeoToken !== "" || !!form?.vimeo_token_set);
  const changes: StudioChanges = tokenChange ? { ...settingChanges, vimeo_token: vimeoToken ?? "" } : settingChanges;
  const dirty = Object.keys(changes).length > 0;
  const pauseOk = !!form && !!options && inRange(form.transition_pause, options.transition_pause);
  const volumeOk = !!form && !!options && inRange(form.music_volume, options.music_volume);
  const durationOk = !!form && !!options && inRange(form.transition_duration, options.transition_duration);
  const opacityOk = !!form && !!options && inRange(form.watermark_opacity, options.watermark_opacity);
  const textOk = !!form && !!form.ollama_model.trim() && !!form.output_folder.trim();
  const valid = pauseOk && volumeOk && durationOk && opacityOk && textOk;

  const save = useMutation({
    mutationFn: (body: StudioChanges) => api.put<StudioPayload>("/api/settings/studio", body),
    onSuccess: (r) => {
      qc.setQueryData(STUDIO_QUERY_KEY, r);
      qc.invalidateQueries({ queryKey: STUDIO_QUERY_KEY });
      // The default provider changed, or a voice list may have (the Kokoro model landing).
      qc.invalidateQueries({ queryKey: ["voices"] });
      setDraft(null);
      setVimeoToken(null);
      setSaved(true);
    },
    onError: () => setSaved(false),
  });
  const update = (patch: Partial<StudioSettings>) => {
    if (!form) return;
    setSaved(false);
    setDraft({ ...form, ...patch });
  };

  const readOnly = !isAdmin;
  const noteOf = (list: Option[] | undefined, value: string) => list?.find((o) => o.value === value)?.note;
  const pause = options?.transition_pause;
  const volume = options?.music_volume;
  const duration = options?.transition_duration;
  const opacity = options?.watermark_opacity;
  // The server's list of privacies, else the page's own (a backend older than this card).
  const privacyOptions = options?.vimeo_privacy?.options ?? PRIVACIES;

  return (
    <Card title="Studio" subtitle="Narration, transcription and output defaults for everyone.">
      {studio.isLoading && <Spinner label="Loading the studio settings…" />}
      {studio.isError && <ErrorBox message={errorMessage(studio.error)} />}
      {form && options && pause && volume && duration && opacity && (
        <div style={{ display: "grid", gap: 14 }}>
          <div className="os-form-grid">
            <Field label="Narration provider" hint={noteOf(options.tts_provider.options, form.tts_provider)}>
              <Select value={form.tts_provider} disabled={readOnly} onChange={(e) => update({ tts_provider: e.target.value })}>
                <Options options={options.tts_provider.options} />
              </Select>
            </Field>
            <Field label="Whisper model" hint={noteOf(options.whisper_model.options, form.whisper_model)}>
              <Select value={form.whisper_model} disabled={readOnly} onChange={(e) => update({ whisper_model: e.target.value })}>
                <Options options={options.whisper_model.options} />
              </Select>
            </Field>

            <Field label="Default Edge voice" hint={edge.isLoading ? "Loading the Edge voices…" : "Used when a job names no voice."}>
              <Select value={form.edge_tts_voice} disabled={readOnly} onChange={(e) => update({ edge_tts_voice: e.target.value })}>
                <Options options={withCurrent(edge.data?.voices ?? [], form.edge_tts_voice, edge.isLoading)} />
              </Select>
            </Field>
            <div />

            <Field label="Kokoro voice" hint={kokoro.isLoading ? "Loading the Kokoro voices…" : "Used when a Kokoro job names no voice."}>
              <Select value={form.kokoro_voice} disabled={readOnly} onChange={(e) => update({ kokoro_voice: e.target.value })}>
                <Options options={withCurrent(kokoro.data?.voices ?? [], form.kokoro_voice, kokoro.isLoading)} />
              </Select>
            </Field>
            <Field label="Kokoro language" hint="The language Kokoro reads the notes in.">
              <Select value={form.kokoro_lang} disabled={readOnly} onChange={(e) => update({ kokoro_lang: e.target.value })}>
                <Options options={options.kokoro_lang.options} />
              </Select>
            </Field>

            <Field label="Ollama model" hint="The local Ollama model that translates re-voiced narration (for example gemma3:12b).">
              <Input value={form.ollama_model} disabled={readOnly} onChange={(e) => update({ ollama_model: e.target.value })} aria-invalid={!form.ollama_model.trim()} />
            </Field>
            <Field label="Output folder" hint="Not used by this edition yet — renders are saved with their project. An absolute path on the server; it is created if missing and must be writable.">
              <Input value={form.output_folder} disabled={readOnly} onChange={(e) => update({ output_folder: e.target.value })} aria-invalid={!form.output_folder.trim()} />
            </Field>
            <Field label="Courses folder" hint="The folder Walkthroughs › Import workshop… reads: one folder per course, each with a course.json. An existing folder on the server; read, and written only by Export to course while the box below is ticked. Leave it empty to turn the import off.">
              <Input value={form.courses_folder ?? ""} disabled={readOnly} placeholder="(not set)" onChange={(e) => update({ courses_folder: e.target.value })} data-courses-folder="" />
            </Field>
            <Field label="Export to course" hint="Ticked, a walkthrough's owner (or an administrator) may write its figures into its course workshop, inside that course's own folder. Unticked, Export to course previews and offers a zip to copy in by hand. Keep it off for a published course until its readers have a Content Manager from 0.7.42, which draws the pins (an older one shows the pin's text, not the pin).">
              <label className="os-row" style={{ gap: 8, alignItems: "center" }}>
                <input type="checkbox" checked={!!form.courses_export} disabled={readOnly} onChange={(e) => update({ courses_export: e.target.checked })} data-courses-export="" />
                <span>Allow export into the Courses folder</span>
              </label>
            </Field>

            <Field label="Vimeo access token" hint={VIMEO_TOKEN_HINT}>
              <div style={{ display: "grid", gap: 6 }}>
                <Input
                  type="password"
                  autoComplete="off"
                  value={vimeoToken ?? ""}
                  disabled={readOnly}
                  placeholder={form.vimeo_token_set ? "(set — paste a new token to replace it)" : "(not set)"}
                  onChange={(e) => { setSaved(false); setVimeoToken(e.target.value); }}
                  data-vimeo-token=""
                />
                <span className="os-muted os-small" data-vimeo-token-state="">{tokenState(form, vimeoToken)}</span>
                {isAdmin && form.vimeo_token_set && vimeoToken !== "" && (
                  <div>
                    <Button size="sm" onClick={() => { setSaved(false); setVimeoToken(""); }} data-vimeo-token-clear="">Clear the token</Button>
                  </div>
                )}
              </div>
            </Field>
            <Field label="Vimeo privacy" hint={noteOf(privacyOptions, form.vimeo_privacy ?? "unlisted") ?? "The privacy a published video starts with; the Publish dialog can change it per video."}>
              <Select value={form.vimeo_privacy ?? "unlisted"} disabled={readOnly} onChange={(e) => update({ vimeo_privacy: e.target.value })} data-vimeo-privacy="">
                <Options options={privacyOptions} />
              </Select>
            </Field>

            <Field label="Transition pause" hint={`Seconds of silence between slides (${pause.min} to ${pause.max}).`}>
              <Input
                type="number"
                min={pause.min}
                max={pause.max}
                step={pause.step}
                value={form.transition_pause}
                disabled={readOnly}
                onChange={(e) => update({ transition_pause: Number(e.target.value) })}
                aria-invalid={!pauseOk}
              />
            </Field>
            <Field label="Music volume" hint={`Background music level, ${volume.min} (silent) to ${volume.max} (full).`}>
              <Input
                type="number"
                min={volume.min}
                max={volume.max}
                step={volume.step}
                value={form.music_volume}
                disabled={readOnly}
                onChange={(e) => update({ music_volume: Number(e.target.value) })}
                aria-invalid={!volumeOk}
              />
            </Field>

            <Field label="Slide transition" hint="The default effect between slides; each render can change it under More options.">
              <Select value={form.slide_transition} disabled={readOnly} onChange={(e) => update({ slide_transition: e.target.value })}>
                <Options options={options.slide_transition.options} />
              </Select>
            </Field>
            <Field label="Transition duration" hint={`Seconds the effect lasts (${duration.min} to ${duration.max}).`}>
              <Input
                type="number"
                min={duration.min}
                max={duration.max}
                step={duration.step}
                value={form.transition_duration}
                disabled={readOnly}
                onChange={(e) => update({ transition_duration: Number(e.target.value) })}
                aria-invalid={!durationOk}
              />
            </Field>

            <Field label="Watermark text" hint="Brand text drawn over every render; leave it empty for none. Each render can change it.">
              <Input value={form.watermark_text} disabled={readOnly} placeholder="e.g. Company name" onChange={(e) => update({ watermark_text: e.target.value })} />
            </Field>
            <Field label="Watermark position">
              <Select value={form.watermark_position} disabled={readOnly} onChange={(e) => update({ watermark_position: e.target.value })}>
                <Options options={options.watermark_position.options} />
              </Select>
            </Field>
            <Field label="Watermark opacity" hint={`${opacity.min} (faint) to ${opacity.max} (solid).`}>
              <Input
                type="number"
                min={opacity.min}
                max={opacity.max}
                step={opacity.step}
                value={form.watermark_opacity}
                disabled={readOnly}
                onChange={(e) => update({ watermark_opacity: Number(e.target.value) })}
                aria-invalid={!opacityOk}
              />
            </Field>
          </div>

          {edge.data?.error && <ErrorBox message={edge.data.error} />}
          {edge.data?.notice && <div className="os-muted os-small">{edge.data.notice}</div>}
          {kokoro.data?.error && <ErrorBox message={kokoro.data.error} />}
          {kokoro.data?.notice && <div className="os-muted os-small">{kokoro.data.notice}</div>}
          {!pauseOk && <ErrorBox message={`The transition pause must be between ${pause.min} and ${pause.max} seconds.`} />}
          {!volumeOk && <ErrorBox message={`The music volume must be between ${volume.min} and ${volume.max}.`} />}
          {!durationOk && <ErrorBox message={`The transition duration must be between ${duration.min} and ${duration.max} seconds.`} />}
          {!opacityOk && <ErrorBox message={`The watermark opacity must be between ${opacity.min} and ${opacity.max}.`} />}
          {!textOk && <ErrorBox message="The Ollama model and the output folder cannot be empty." />}
          {save.isError && <ErrorBox message={errorMessage(save.error)} />}
          {saved && <div style={{ color: "var(--good)", fontWeight: 500 }}>Settings saved — they apply to the next job.</div>}

          {isAdmin ? (
            <div>
              <Button
                variant="primary"
                icon={<SlidersHorizontal size={16} />}
                disabled={!dirty || !valid || save.isPending}
                onClick={() => save.mutate(changes)}
              >
                {save.isPending ? "Saving…" : "Save settings"}
              </Button>
            </div>
          ) : (
            <div className="os-muted os-small">Only admins can change these settings.</div>
          )}
        </div>
      )}
    </Card>
  );
}
