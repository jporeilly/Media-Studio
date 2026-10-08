# Limits

Every fixed number the app enforces, in one table, with the line of code it is read from. A limit marked "bound on abuse" is far beyond ordinary use.

| What | Limit | Where |
| --- | --- | --- |
| Project upload | 2 GB per file | `api/routers/projects.py:28` |
| Accepted project files | `.pptx`, `.pdf`, `.mp4`, `.mov`, `.mkv`, `.avi`, `.webm`, `.m4v` | `services/projects.py:54-56` |
| Jobs | 2 worker threads for the studio; 1 job per project at a time | `services/jobs.py:60`, `services/jobs.py:8-15` |
| Job poll | every 1.2 s; stopped after 3 failed attempts in a row, at once on a 404; a page with no job in flight asks for its project's job (or the running update) every 5 s | `frontend/src/lib/jobs.ts:17`, `frontend/src/lib/jobs.ts:21`, `frontend/src/lib/jobs.ts:19` |
| Speaker notes | 20,000 characters per slide | `services/slides.py:54` |
| Alt text | 2,000 characters | `api/schemas.py:345` |
| Pause after slide | 0–30 s | `services/slides.py:55` |
| Notes undo history | 20 steps per slide | `core/project_manager.py:288` |
| Narration speed | 0.5–2 for a sentence's own speed, a deck render and a re-voice; the server refuses outside it | `services/narration.py:91-92`, `api/schemas.py:288`, `api/schemas.py:325` |
| Sentence offset | ±300 s | `services/narration.py:86` |
| Sentence voice id | 200 characters | `services/narration.py:93` |
| Offsets in one save | 5,000 sentences (bound on abuse) | `services/narration.py:90` |
| One-sentence preview | 1,000 characters spoken; 30 s wait | `services/narration.py:103`, `services/narration.py:112` |
| Speaking-rate measurement | 45 s wait | `services/narration.py:120` |
| SRT cue | at least 0.5 s | `services/narration.py:906` |
| Transition duration | 0.1–2 s | `services/studio_settings.py:74` |
| Pause between slides | 0–5 s | `services/studio_settings.py:72` |
| Music volume | 0–1 | `services/studio_settings.py:73` |
| Watermark opacity | 0.1–1 | `services/studio_settings.py:75` |
| Intro and outro card | 1–10 s; text 200 characters | `api/schemas.py:299`, `api/schemas.py:301`, `api/schemas.py:14` |
| Preview render | 15 s on the card; the API takes 0–120 s | `frontend/src/lib/generateOptions.ts:37`, `api/schemas.py:317` |
| GIF export | the first 30 s, 5 fps, 480 px wide | `services/processing.py:2042` |
| Frame rate | 2 fps static, 24 fps with a transition or an animated slide | `core/video_creator.py:38-39`, `core/video_creator.py:107` |
| Render parts | up to 8 slides and pauses at 1080p, 18 at 720p, 4 at 4K (never fewer than 4); each part at least 48 frames unless it is the whole video, so a part takes more slides only while it is shorter than that | `core/video_creator.py:1403-1405` |
| Render wait | each part 120 s plus 0.05 s a frame (more for a picture larger than 1080p); the sound 120 s plus half the video's length; the join 60 s plus a tenth | `core/video_creator.py:1411-1412`, `core/video_creator.py:2220`, `core/video_creator.py:2242` |
| Zoom In | from 1.3 times the slide's size | `core/video_creator.py:1386` |
| Chapter title from a slide's notes | 60 characters | `services/processing.py:1862` |
| Edit ranges per track | 5,000 (bound on abuse) | `services/edit.py:116` |
| Music clips per project | 200 | `services/edit.py:343` |
| Music clip length | at least 0.1 s | `services/edit.py:346` |
| Clip level | 0–1, linear | `services/edit.py:474` |
| Clip fades | 0 s or more each, together no longer than the clip | `services/edit.py:476-481` |
| New clip's fades | 1 s in, 2 s out, cut to fit | `frontend/src/lib/edit.ts:732-733` |
| Markers per project | 200 | `services/edit.py:565` |
| Marker name | 1–80 characters, no control characters | `services/edit.py:566`, `services/edit.py:632-638` |
| Music upload | 100 MB; mp3, wav, m4a, aac, ogg, flac; mono or stereo; name 120 characters | `services/music.py:95`, `services/music.py:82`, `services/music.py:105`, `services/music.py:108` |
| Music decode at upload | 600 s | `services/music.py:124` |
| Timeline undo | 50 edits, this visit only | `frontend/src/lib/edit.ts:51` |
| Timeline frame | 1/30 s | `frontend/src/lib/edit.ts:49` |
| Nudge | 0.05 s; 0.25 s with Shift | `frontend/src/components/project/timeline/types.ts:110-111` |
| Snapping | within 8 px | `frontend/src/components/project/NarrationTimeline.tsx:179` |
| Zoom | up to 200 px per second | `frontend/src/lib/edit.ts:42` |
| Re-voice speed-up | up to +30 % to fit; tempo squeeze past 1.15× the window, up to 2× | `services/processing.py:511`, `services/processing.py:453-454` |
| QA review pass | 20 slides, 12,000 characters of notes | `services/ai_slides.py:64-65` |
| Q&A document | 1–50 questions, 10 by default | `api/schemas.py:446` |
| Tone instruction | 2,000 characters; a tone name 40 | `api/schemas.py:404`, `api/schemas.py:403` |
| Ollama waits | 3 s probe; 120 s per slide; 180 s per QA pass; 60 s per translation request | `services/ai_slides.py:61-63`, `core/translator.py:84` |
| Model-paced pacing | an answer under 80 % of the note's length falls back to the rules | `services/ai_slides.py:125` |
| Password length | policy minimum 4–64 (default 12); never over 72 bytes | `api/passwords.py:24-25`, `api/passwords.py:43`, `api/passwords.py:23` |
| Session | 7 days | `api/store.py:26` |
| Audit read | 1–1,000 rows, 100 by default; the card offers 50, 100, 250, 1000 | `api/store.py:46-47`, `frontend/src/components/settings/AuditCard.tsx:27` |
| Audit purge | 1–3,650 days; the card offers 30, 90, 365 | `api/routers/admin.py:28`, `frontend/src/components/settings/AuditCard.tsx:29` |
| Update | `git fetch` 45 s; `git pull` 300 s; `pip install` 1,800 s; the page waits 240 s for the restart | `services/updater.py:80`, `services/updater.py:103`, `services/updater.py:113`, `frontend/src/pages/Settings.tsx:40` |
| Desktop splash | "taking longer" after 45 s; gives up after 240 s | `desktop/dist/index.html:196-197` |
| Screen recording | stopped at 2 hours; refused with under 2 GB free; a piece sent every 5 s; 30 frames a second asked for, 1–60 accepted; a 3-2-1 countdown | `services/recordings.py:79`, `services/recordings.py:81`, `frontend/src/lib/capture.ts:66`, `frontend/src/lib/capture.ts:82`, `services/recordings.py:96`, `frontend/src/lib/capture.ts:80` |
| Recording disk | a piece that would leave under 1 GB free is refused (507) and the recording stops, keeping what arrived; one recording may take 16 GB (413 past it), against about 7.4 GB for two hours at the recorder's 8 Mbit/s of picture and 192 kbit/s of sound | `services/recordings.py:84`, `services/recordings.py:89`, `services/recordings.py:339-348`, `frontend/src/lib/capture.ts:72-73` |
| Recording live | saved or discarded only by its recorder while it was heard from in the last 20 s; the recorder's heartbeat every 5 s | `services/recordings.py:102`, `services/recordings.py:214-218`, `frontend/src/lib/capture.ts:79` |
| Recording pieces | up to 8,000 per recording, 256 MB each, refused (413) as the body passes it; the recorder's timeslice 1–60 s | `services/recordings.py:92-93`, `api/routers/recordings.py:119-131`, `services/recordings.py:100` |
| Recording save | the conversion waits 300 s plus 1.5 s a second recorded, measured by the pieces held (5 s each) or the recorder's clock, whichever is longer; the last frame held at most that long | `services/recordings.py:106-107`, `services/recordings.py:512-518`, `services/recordings.py:477` |
| Still delay | none, 3, 5 or 10 s | `frontend/src/lib/capture.ts:81` |
| Capture rectangle | 1–32,000 pixels a side; the grab waits 20 s; up to 16 monitors frozen at once | `services/capture.py:36-37`, `services/capture.py:33`, `services/capture.py:100` |
| Capture and recording names | 120 characters | `services/captures.py:32`, `services/recordings.py:94` |
| Still added to a deck | letterboxed onto the deck's own background (the median colour of a band 1 % deep round its first slide image) at that image's size; white at 1920 × 1080 when no slide is rendered | `services/slides.py:593`, `services/slides.py:615`, `services/slides.py:587`, `services/slides.py:590` |
| Screen capture from | this computer only: freezing or reading the screen, taking a still, and starting or feeding a recording answer a loopback client with no proxy header, 403 to anyone else | `api/routers/capture.py:22`, `api/routers/capture.py:27`, `api/routers/capture.py:30-46`, `api/routers/capture.py:49-62` |
| Voice list caches | the Edge and Kokoro lists are kept 7 days | `utils/config.py:558`, `utils/config.py:521` |
| Kokoro model | about 340 MB, downloaded once | `services/studio_settings.py:48` |
| Whisper voice activity | silences of 500 ms or more skipped | `core/video_importer.py:322` |
| Document slug on the Docs page | 200 characters | `api/routers/docs.py:105` |

The constants behind the music limits are `MAX_MUSIC_BYTES`, `MAX_CHANNELS` and `MAX_NAME_CHARS`; behind the edit's, `MAX_CLIPS`, `MIN_CLIP_SECONDS`, `MAX_MARKERS` and `MAX_MARKER_NAME`; behind the narration's, `MAX_OFFSET_SECONDS`, `MIN_SPEED` and `MAX_SPEED`; behind the render's, `STATIC_FPS`, `TRANSITION_FPS`, `CHUNK_PIXELS`, `MIN_CHUNK_SEGMENTS`, `MIN_CHUNK_FRAMES`, `RENDER_BASE_SECONDS`, `RENDER_SECONDS_PER_FRAME` and `ZOOM_FROM`; behind the screen capture's, `MAX_RECORDING_SECONDS`, `MIN_FREE_BYTES`, `MIN_CHUNK_FREE_BYTES`, `MAX_RECORDING_BYTES`, `LIVE_WINDOW_SECONDS`, `MAX_CHUNKS`, `MAX_CHUNK_BYTES`, `TRANSCODE_BASE_SECONDS`, `TIMESLICE_MS`, `HEARTBEAT_MS`, `VIDEO_BITS_PER_SECOND`, `STILL_DELAYS`, `MAX_DIMENSION` and `MAX_MONITORS`.

## See also

- [The Music lane](../guides/music.md)
- [Markers and chapters](../guides/markers-and-chapters.md)
- [Studio settings](../admin/studio-settings.md)
- [Screen capture](../guides/capture.md)
