# Faceless YouTube OS

**The world behind the headlines.** A human-controlled documentary production line. **This app NEVER uploads or publishes.** A person uploads manually and records the YouTube URL.

## Run

Python 3.11+ and `ffmpeg`/`ffprobe` with libx264 and libass. No pip/npm dependencies.

```sh
python3 main.py                          # dashboard, port 5000; or press Run in Replit
python3 -m unittest discover -s tests -v  # offline fictional providers + real HD/4K renders
```

Create an investigation (4K default), enter and confirm its topic score, and select the topic. Autopilot runs machine steps **only up to the next human gate**: research, script, packaging, then manual-upload confirmation. Prompts are editable in `prompts/`. `API.md` documents the web interface.

## Credentials

Add these in **Replit Secrets** or environment variables, then restart. Never paste keys into the dashboard, prompts or project files.

| Capability | Required configuration |
| --- | --- |
| Research planning, story, titles | `ANTHROPIC_API_KEY` (default model `claude-sonnet-4-5`, optional `LLM_MODEL` override), **or** `OPENAI_API_KEY` **and** `LLM_MODEL` |
| Search and page text | `FIRECRAWL_API_KEY` |
| Fixed channel narration | `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID` |
| Footage | `PEXELS_API_KEY` |

Adapters use direct HTTPS through Python's `urllib`; account connections do not automatically populate these variables. Settings reports missing names, never values. Manual editors work without paid providers. Live steps fail explicitly when unconfigured. Review provider credit limits, commercial-use plans and content licenses.

## Editorial safeguards

- Statuses only advance one step: IDEA → SELECTED → RESEARCHING → RESEARCH_COMPLETE → SCRIPTING → SCRIPT_REVIEW → APPROVED → VOICE → VISUALS → EDITING → QA → THUMBNAIL → UPLOAD_READY → PUBLISHED → ANALYZING.
- Topic formula: curiosity 25 + audience 20 + story 20 + visuals 10 + evergreen 10 + monetization 10 − difficulty 0–5. **Maximum is 95**, on the nominal 100-point scale. Gate requires human-confirmed 75+, or a written override.
- Research plans 8–12 questions and reads Firecrawl page text. The AI cites **saved source indices**, never supplies URLs. Code checks exact normalized quotes (20+ meaningful characters), figures and meaning-changing words. Contradictory metrics are conflicting. Human verification needs a note. Editing **any** claim withdraws research and dependent approvals.
- Scripts cite only verified claim IDs. Deterministic fact-check blocks unknown/unverified citations and unsupported figures ≥10 or any decimal within each scene's own claims; warns on absent citations, structure, long hooks, engagement begging and repeated suspense. Every save/import retains a revision. A next idea must appear in the last line.
- **Quote-backed is not guaranteed true.** Code cannot judge all source quality or misleading paraphrases. A person must read sources, qualifications and the final story. Reapprovals restore gates without moving statuses backward.
- Title and thumbnail numbers must be supported; titles are under 70 characters and not all caps. Thumbnail text is 1–4 words. Output is a **thumbnail concept**, not a finished thumbnail image.

## Media and delivery

Dry-run shows ≤5000-character voice chunks without an API call. Successful chunks are checksummed and reused on retries with the same channel voice/settings. An ambiguous network failure before audio reaches disk cannot guarantee avoiding a provider charge.

Pexels footage uses distinct video IDs with photographer/link credits. HD picks 1280–1920px widths; 4K picks 2560–3840 or falls back to the widest file. Below 3840×2160 is reported as upscaled, not native 4K.

Exports are **1920×1080 or 3840×2160 H.264** with drift pans, soft navy fades and navy fallback cards. Shot lengths are 2–6s when scene duration permits (sub-2s scenes necessarily have shorter shots). Scene/SRT timing is **estimated by narration word share**, not audio alignment. Optional burned captions and rights-confirmed music upload (≤50MB) with ducking. Audio normalization targets **−14 LUFS**, true-peak −1 dBTP.

Required QA checks current narration/cut, duration match within 1.5s, script approval, a fresh fact-check and credits; footage coverage and native-4K are advisory. QA reruns at packaging/manual-publication gates. Download the video, narration, estimated SRT, credits and JSON/text upload packages (title, description draft, sources, Pexels credit). Create the final thumbnail and upload manually.

## Analytics

After recording the upload URL, enter CTR and average percentage viewed (0–100). Optional CSV uses strictly increasing **seconds**:

```csv
time,percent
0,100
30,72
60,65
```

Recommendations compare to the channel's **own median of at least 3 earlier manually published videos**; equality counts as high. Retention at 30s is interpolated only inside the supplied range. Steepest drop uses percentage points/second; biggest rise uses percentage-point gain. Intervals map to estimated scenes by midpoint.

## Storage and operating limits

Each `projects/<id>/project.json` is readable, atomically saved JSON, with evidence, revisions and approvals. Its `voice/`, `media/`, `render/` and `uploads/` folders are ignored by Git. Source, prompts and tests stay in the connected repository. Back up **whole project folders**, including media.

This is a **private single-operator workspace tool, without user authentication**. Keep the workspace/preview private. Do not expose publicly before adding access control. Cross-origin mutation checks and output allowlists are not a login system. Local files are not durable autoscaling deployment storage.

One production job runs at a time. Progress is in memory; after a restart, rerun to resume saved stages, voice chunks and shot caches. Long 4K renders need substantial CPU, disk and time. Tests use temporary fictional data and synthesized media only, never paid APIs. Human editorial review and finishing remain necessary; no app can guarantee enterprise production quality or YouTube popularity.