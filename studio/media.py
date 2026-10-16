"""Resumable narration, footage selection, ffmpeg cuts and deterministic QA."""
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
from pathlib import Path
from .core import StudioError, atomic_json, digest, script_valid
from .verification import fact_check

FPS = 24


def ffmpeg(args, cwd=None):
    if not shutil.which("ffmpeg"):
        raise StudioError("ffmpeg is missing. Install ffmpeg to generate media.")
    try:
        result = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                                 "-threads", "2", "-filter_threads", "1", *map(str, args)],
                                cwd=cwd, capture_output=True, timeout=14400)
        if result.returncode:
            raise StudioError("ffmpeg could not process the media. Check that the audio/video files are valid and there is enough disk space.")
    except subprocess.TimeoutExpired:
        raise StudioError("The render exceeded four hours. Completed shots are kept; retry on a larger machine.") from None


def probe(path):
    if not Path(path).is_file():
        raise StudioError("A required media file is missing.")
    try:
        result = subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
                                capture_output=True, timeout=60)
        if result.returncode:
            raise ValueError
        data = json.loads(result.stdout)
        return {"duration":float(data["format"].get("duration", 0)), "streams":data["streams"]}
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
        raise StudioError("A media file could not be inspected. Check that it is a valid audio or video file.") from None


def chunks(text, limit=5000):
    """Prefer paragraph, then sentence, then word boundaries; never exceed limit."""
    pieces, current = [], ""
    paragraphs = re.split(r"\n\s*\n", text.strip())
    for paragraph in paragraphs:
        if not paragraph.strip():
            continue
        units = [paragraph] if len(paragraph) <= limit else re.split(r"(?<=[.!?])\s+", paragraph)
        for unit in units:
            if len(unit) > limit:
                words, unit = unit.split(), ""
                subunits, part = [], ""
                for word in words:
                    if len(word) > limit:
                        raise StudioError("A script word exceeds 5000 characters. Break it up before narration.")
                    if len(part) + len(word) + bool(part) > limit:
                        subunits.append(part)
                        part = word
                    else:
                        part += (" " if part else "") + word
                if part:
                    subunits.append(part)
            else:
                subunits = [unit]
            for piece in subunits:
                sep = "\n\n" if current else ""
                if len(current) + len(sep) + len(piece) > limit:
                    if current:
                        pieces.append(current)
                    current = piece
                else:
                    current += sep + piece
    if current:
        pieces.append(current)
    return pieces


def narration_text(p):
    return "\n\n".join(s["text"].strip() for s in p["script"]["scenes"] if s["text"].strip())


def chunk_plan(p):
    return [{"index":i+1, "characters":len(t), "checksum":hashlib.sha256(t.encode()).hexdigest(), "text":t}
            for i, t in enumerate(chunks(narration_text(p)))]


def voice(p, folder, provider, progress):
    if not script_valid(p):
        raise StudioError("Approve the current research and script before generating narration.")
    # Include the fixed channel voice and synthesis settings in the identity, but never store the secret itself.
    identity = digest([provider.voice_identity(), "eleven_multilingual_v2", 0.65, 0.75, 0.15, 1.0])
    directory = folder / "voice"
    directory.mkdir(parents=True, exist_ok=True)
    plan = chunk_plan(p)
    if not plan:
        raise StudioError("The script is empty.")
    paths = []
    for item in plan:
        key = digest([identity, item["checksum"]])
        path = directory / (key + ".mp3")
        manifest = directory / (key + ".json")
        reusable = False
        if path.exists() and manifest.exists():
            try:
                reusable = json.loads(manifest.read_text())["audio_checksum"] == hashlib.sha256(path.read_bytes()).hexdigest()
            except (ValueError, KeyError):
                pass
        if not reusable:
            progress(f"Narration chunk {item['index']} of {len(plan)}.")
            audio = provider.speech(item["text"])
            if not audio:
                raise StudioError("ElevenLabs returned an empty audio chunk.")
            temp = path.with_suffix(".part")
            temp.write_bytes(audio)
            os.replace(temp, path)
            atomic_json(manifest, {"text_checksum":item["checksum"], "audio_checksum":hashlib.sha256(audio).hexdigest()})
        else:
            progress(f"Reusing completed narration chunk {item['index']} of {len(plan)}.")
        paths.append(path)
    listing = directory / "concat.txt"
    listing.write_text("\n".join(f"file '{x.name}'" for x in paths), encoding="utf-8")
    output = directory / "narration.wav"
    ffmpeg(["-f", "concat", "-safe", "0", "-i", listing, "-ac", "1", "-ar", "48000",
            "-c:a", "pcm_s16le", output])
    duration = probe(output)["duration"]
    p["artifacts"]["narration"] = {"path":"voice/narration.wav", "script":digest(p["script"]),
                                  "duration":duration, "checksum":hashlib.sha256(output.read_bytes()).hexdigest()}
    p["voice_plan"] = [{k:v for k,v in x.items() if k != "text"} for x in plan]


def timeline(p, duration=None):
    duration = duration or p.get("artifacts", {}).get("narration", {}).get("duration", 0)
    scenes = p["script"]["scenes"]
    weights = [len(s["text"].split()) for s in scenes]
    total = sum(weights)
    if not total or duration <= 0:
        raise StudioError("Generate narration from a non-empty script before planning the edit.")
    start, result = 0.0, []
    # Quantize to frames, preserving final duration to within a single frame.
    ends = 0
    for s, weight in zip(scenes, weights):
        ends += weight
        end = round(duration * ends / total * FPS) / FPS
        result.append({"scene_id":s["id"], "section":s["section"], "start":start, "end":end, "duration":end-start})
        start = end
    return result


def choose_file(files, quality):
    options = [f for f in files if f.get("width") and f.get("height") and f["width"] > f["height"]
               and f.get("link") and f.get("file_type", "video/mp4") == "video/mp4"]
    lo, hi = (2560, 3840) if quality == "4K" else (1280, 1920)
    preferred = [f for f in options if lo <= f["width"] <= hi]
    return max(preferred or (options if quality == "4K" else []), key=lambda f:f["width"], default=None)


def footage(p, folder, provider, progress, save):
    if not script_valid(p) or "narration" not in p["artifacts"]:
        raise StudioError("Generate approved narration before searching for footage.")
    schedule = timeline(p)
    fingerprint = digest([p["script"], p["quality"]])
    if p.get("footage_fingerprint") != fingerprint:
        p["footage"] = []
        p["footage_fingerprint"] = fingerprint
    # Existing downloaded clips remain available for resume, but are never reused in a cut.
    used = {v["pexels_id"] for v in p["footage"]}
    for scene, timing in zip(p["script"]["scenes"], schedule):
        needed = max(1, math.ceil(timing["duration"] / 5))
        existing = [v for v in p["footage"] if v["scene_id"] == scene["id"] and (folder / v["path"]).exists()]
        if len(existing) >= needed:
            continue
        progress(f"Finding landscape footage for {scene['section']}.")
        for page in range(1, 4):
            for video in provider.videos(scene["footage_query"], page):
                if video["id"] in used:
                    continue
                file = choose_file(video.get("video_files", []), p["quality"])
                if not file:
                    continue
                relative = f"media/pexels-{video['id']}-{file.get('id', file['width'])}.mp4"
                path = folder / relative
                if not path.exists():
                    provider.download(file["link"], path)
                # Inspect actual downloaded dimensions, not just the catalog.
                streams = probe(path)["streams"]
                stream = next((s for s in streams if s.get("codec_type") == "video"), None)
                if not stream:
                    continue
                width, height = stream["width"], stream["height"]
                item = {"scene_id":scene["id"], "pexels_id":video["id"], "path":relative,
                        "width":width, "height":height, "upscaled":p["quality"] == "4K" and (width < 3840 or height < 2160),
                        "photographer":video.get("user", {}).get("name", "Pexels contributor"),
                        "url":video.get("url", "https://www.pexels.com")}
                p["footage"].append(item)
                used.add(video["id"])
                existing.append(item)
                write_credits(p, folder)
                save(p)
                if len(existing) >= needed:
                    break
            if len(existing) >= needed:
                break
    write_credits(p, folder)
    p["artifacts"]["footage"] = {"fingerprint":fingerprint}


def write_credits(p, folder):
    directory = folder / "media"
    directory.mkdir(parents=True, exist_ok=True)
    text = "Video footage provided by Pexels (https://www.pexels.com).\n\n"
    text += "\n".join(f"{v['scene_id']} — {v['photographer']} — {v['url']}" for v in p["footage"])
    (directory / "credits.txt").write_text(text + "\n", encoding="utf-8")
    p["artifacts"]["credits"] = {"path":"media/credits.txt"}


def srt_timestamp(seconds):
    millis = round(seconds * 1000)
    h, millis = divmod(millis, 3600000)
    m, millis = divmod(millis, 60000)
    s, millis = divmod(millis, 1000)
    return f"{h:02}:{m:02}:{s:02},{millis:03}"


def captions(p, folder, schedule):
    cues = []
    for scene, timing in zip(p["script"]["scenes"], schedule):
        words = scene["text"].split()
        for i in range(0, len(words), 9):
            start = timing["start"] + timing["duration"] * i / len(words)
            end = timing["start"] + timing["duration"] * min(i+9, len(words)) / len(words)
            cues.append(f"{len(cues)+1}\n{srt_timestamp(start)} --> {srt_timestamp(end)}\n{' '.join(words[i:i+9])}\n")
    path = folder / "render/captions-estimated.srt"
    path.write_text("\n".join(cues), encoding="utf-8")
    (folder / "render/captions-NOTE.txt").write_text(
        "ESTIMATED CAPTIONS: timing is based on narration word share, not forced alignment. Review before uploading.\n", encoding="utf-8")
    p["artifacts"]["captions"] = {"path":"render/captions-estimated.srt", "estimated":True}


def cut(p, folder, progress):
    if not script_valid(p):
        raise StudioError("Current script approval is required before rendering.")
    narration = p["artifacts"].get("narration")
    if not narration or narration["script"] != digest(p["script"]):
        raise StudioError("Generate narration for this script revision before rendering.")
    if "footage" not in p["artifacts"]:
        raise StudioError("Run the footage step first; scenes without available clips will use navy cards.")
    duration = probe(folder / narration["path"])["duration"]
    schedule = timeline(p, duration)
    p["timeline"] = schedule
    directory = folder / "render"
    directory.mkdir(parents=True, exist_ok=True)
    captions(p, folder, schedule)
    width, height = (3840, 2160) if p["quality"] == "4K" else (1920, 1080)
    # Version the render cache with all inputs, avoiding stale shots on revisions/settings changes.
    render_key = digest([p["script"], p["quality"], p["footage"], duration, "drift-fade-v1"])[:12]
    shots = []
    clip_index = {}
    for scene, timing in zip(p["script"]["scenes"], schedule):
        available = [v for v in p["footage"] if v["scene_id"] == scene["id"]]
        count = max(1, math.ceil(timing["duration"] / 6))
        # Balance equal shot lengths to keep 2–6 seconds except sub-2s scenes.
        shot_frames = max(1, round(timing["duration"] * FPS))
        count = min(count, max(1, shot_frames // (2*FPS)))
        base, extra = divmod(shot_frames, count)
        for j in range(count):
            frames = base + (j < extra)
            seconds = frames / FPS
            path = directory / f"shot-{render_key}-{len(shots):04}.mp4"
            if not path.exists():
                progress(f"Rendering shot {len(shots)+1} · {scene['section']} · {p['quality']}.")
                idx = clip_index.get(scene["id"], 0)
                clip = available[idx] if idx < len(available) else None
                clip_index[scene["id"]] = idx + 1
                if clip:
                    # Loop only within this single shot if a source is shorter than the shot.
                    inputs = ["-stream_loop", "-1", "-i", folder / clip["path"]]
                    vf = (f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                          f"crop={width}:{height},setsar=1,"
                          f"zoompan=z='1.02+0.02*on/{frames}':x='(iw-iw/zoom)*on/{frames}':"
                          f"y='(ih-ih/zoom)/2':d=1:s={width}x{height}:fps={FPS},")
                else:
                    inputs = ["-f", "lavfi", "-i", f"color=c=0x101c30:s={width}x{height}:r={FPS}"]
                    vf = ""
                vf += f"fade=t=in:st=0:d=0.16:color=0x101c30,fade=t=out:st={max(0,seconds-.16)}:d=0.16:color=0x101c30,format=yuv420p"
                ffmpeg([*inputs, "-vf", vf, "-an", "-frames:v", frames,
                        "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-threads", "2", path])
            shots.append(path)
    listing = directory / "shots.txt"
    listing.write_text("\n".join(f"file '{x.name}'" for x in shots), encoding="utf-8")
    silent = directory / "picture.mp4"
    ffmpeg(["-f", "concat", "-safe", "0", "-i", listing, "-c", "copy", silent])
    inputs = ["-i", silent, "-i", folder / narration["path"]]
    music = p.get("music")
    if music:
        inputs += ["-stream_loop", "-1", "-i", folder / music["path"]]
        filters = ("[1:a]asplit=2[voice][side];[2:a]volume=0.12[bed];"
                   "[bed][side]sidechaincompress=threshold=0.015:ratio=10:attack=20:release=500[duck];"
                   "[voice][duck]amix=inputs=2:duration=first:normalize=0,"
                   "loudnorm=I=-14:TP=-1:LRA=11[a]")
    else:
        filters = "[1:a]loudnorm=I=-14:TP=-1:LRA=11[a]"
    output = directory / "documentary.mp4"
    args = [*inputs, "-filter_complex_threads", "1", "-filter_complex", filters,
            "-map", "0:v:0", "-map", "[a]"]
    if p.get("burn_captions"):
        # Generated path only, never user-supplied shell/filter text.
        subtitle = str((directory / "captions-estimated.srt").resolve()).replace("'", r"\'").replace(":", r"\:")
        args += ["-vf", f"subtitles='{subtitle}'", "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-threads", "2"]
    else:
        args += ["-c:v", "copy"]
    ffmpeg([*args, "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-t", f"{duration:.6f}",
            "-movflags", "+faststart", output])
    p["artifacts"]["video"] = {"path":"render/documentary.mp4", "script":digest(p["script"]),
                               "quality":p["quality"], "duration":probe(output)["duration"]}


def qa(p, folder):
    required, advisory = [], []
    def check(label, passed, is_required=True):
        (required if is_required else advisory).append({"label":label, "passed":bool(passed)})
    narration, video = p["artifacts"].get("narration"), p["artifacts"].get("video")
    n_exists = bool(narration and (folder / narration["path"]).is_file())
    v_exists = bool(video and (folder / video["path"]).is_file())
    check("Narration exists for the current script", n_exists and narration.get("script") == digest(p["script"]))
    check("Finished cut exists for the current script and quality", v_exists and video.get("script") == digest(p["script"]) and video.get("quality") == p["quality"])
    match = False
    if n_exists and v_exists:
        match = abs(probe(folder / narration["path"])["duration"] - probe(folder / video["path"])["duration"]) <= 1.5
    check("Video and narration lengths match within 1.5 seconds", match)
    check("Current script approval is on record", script_valid(p))
    p["fact_check"] = fact_check(p["script"], p["claims"], p["next_idea"])
    check("Script still passes the deterministic fact-check", p["fact_check"]["passed"])
    check("Pexels credits file exists", (folder / "media/credits.txt").is_file() and "credits" in p["artifacts"])
    scene_ids = {c["scene_id"] for c in p["footage"] if (folder / c["path"]).is_file()}
    check("Every scene has footage (otherwise a navy card is used)",
          all(s["id"] in scene_ids for s in p["script"]["scenes"]), False)
    upscaled = 0
    for clip in p["footage"]:
        try:
            stream = next(s for s in probe(folder / clip["path"])["streams"] if s.get("codec_type") == "video")
            if stream["width"] < 3840 or stream["height"] < 2160:
                upscaled += 1
        except (StudioError, StopIteration, KeyError):
            upscaled += 1
    check("All source clips are native 4K" if p["quality"] == "4K" else "HD output selected (native 4K check not applicable)",
          p["quality"] != "4K" or upscaled == 0, False)
    check("Scene timing and captions are estimated by word share; review manually", False, False)
    p["qa"] = {"required":required, "advisory":advisory, "passed":all(c["passed"] for c in required),
               "upscaled_clips":upscaled if p["quality"] == "4K" else 0}
    return p["qa"]
