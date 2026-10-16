"""Live provider adapters using only urllib. Secrets never leave memory."""
import json
import os
import re
import shutil
import urllib.error
import urllib.parse
import urllib.request
from .core import ROOT, StudioError


def require(key):
    value = os.environ.get(key, "").strip()
    if not value:
        raise StudioError(f"Missing {key}. Add it in Replit Secrets or your environment, then restart the app.")
    return value


def settings():
    specs = [
        ("Story intelligence", [] if os.environ.get("ANTHROPIC_API_KEY") else ["OPENAI_API_KEY", "LLM_MODEL"]),
        ("Firecrawl research", ["FIRECRAWL_API_KEY"]),
        ("ElevenLabs narration", ["ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID"]),
        ("Pexels footage", ["PEXELS_API_KEY"]),
    ]
    return {"providers":[{"name":name, "ready":not (missing := [k for k in keys if not os.environ.get(k)]),
                          "missing":missing} for name, keys in specs],
            "ffmpeg":bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))}


def request(url, data=None, headers=None, binary=False, label="Provider"):
    headers = dict(headers or {})
    headers["User-Agent"] = "FacelessYouTubeOS/1.0"
    if data is not None:
        data = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers), timeout=180) as response:
            content = response.read()
            return content if binary else json.loads(content)
    except urllib.error.HTTPError as e:
        # Never persist provider responses, URLs, headers or exception text.
        if e.code in (401, 403):
            raise StudioError(f"{label} rejected access. Check its API key, permissions and plan.") from None
        if e.code == 429:
            raise StudioError(f"{label} reached a rate or credit limit. Retry after checking your account.") from None
        raise StudioError(f"{label} could not complete the request (HTTP {e.code}). Check the provider account and retry.") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise StudioError(f"{label} is unreachable or timed out. Completed files are kept; retry when available.") from None
    except (ValueError, KeyError, TypeError):
        raise StudioError(f"{label} returned an unreadable response. No approval was granted.") from None


def parse_json(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        obj = json.loads(text)
        if not isinstance(obj, dict):
            raise ValueError
        return obj
    except (ValueError, TypeError):
        raise StudioError("The AI returned invalid structured data. Retry or edit the draft manually.") from None


class LiveProviders:
    def llm(self, prompt_name, payload):
        prompt = (ROOT / "prompts" / (prompt_name + ".txt")).read_text(encoding="utf-8")
        text = json.dumps(payload, ensure_ascii=False)
        if os.environ.get("ANTHROPIC_API_KEY"):
            result = request("https://api.anthropic.com/v1/messages",
                {"model":os.environ.get("LLM_MODEL", "claude-sonnet-4-5"), "max_tokens":12000,
                 "system":prompt, "messages":[{"role":"user", "content":text}]},
                {"x-api-key":require("ANTHROPIC_API_KEY"), "anthropic-version":"2023-06-01"}, label="Anthropic")
            try:
                return parse_json("".join(x.get("text", "") for x in result["content"] if x.get("type") == "text"))
            except (KeyError, TypeError):
                raise StudioError("Anthropic returned an incomplete draft. Retry or edit manually.") from None
        key, model = require("OPENAI_API_KEY"), require("LLM_MODEL")
        result = request("https://api.openai.com/v1/chat/completions",
            {"model":model, "messages":[{"role":"system", "content":prompt}, {"role":"user", "content":text}],
             "response_format":{"type":"json_object"}},
            {"Authorization":"Bearer " + key}, label="OpenAI")
        try:
            return parse_json(result["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError):
            raise StudioError("OpenAI returned an incomplete draft. Retry or edit manually.") from None

    def search(self, question):
        result = request("https://api.firecrawl.dev/v2/search",
            {"query":question, "limit":3, "sources":["web"]},
            {"Authorization":"Bearer " + require("FIRECRAWL_API_KEY")}, label="Firecrawl")
        data = result.get("data", {})
        return data.get("web", []) if isinstance(data, dict) else data

    def read(self, url):
        result = request("https://api.firecrawl.dev/v2/scrape", {"url":url, "formats":["markdown"]},
                         {"Authorization":"Bearer " + require("FIRECRAWL_API_KEY")}, label="Firecrawl")
        data = result.get("data") or {}
        metadata = data.get("metadata") or {}
        text = data.get("markdown", "")
        if not text:
            raise StudioError("Firecrawl could not read one of the sources. Try another public source.")
        return {"url":url, "title":metadata.get("title", url), "text":text,
                "publication_date":metadata.get("publishedTime", metadata.get("publishedDate", "")),
                "origin":"firecrawl"}

    def voice_identity(self):
        return require("ELEVENLABS_VOICE_ID")

    def speech(self, text):
        voice = urllib.parse.quote(self.voice_identity(), safe="")
        return request(f"https://api.elevenlabs.io/v1/text-to-speech/{voice}?output_format=mp3_44100_128",
            {"text":text, "model_id":"eleven_multilingual_v2",
             "voice_settings":{"stability":0.65, "similarity_boost":0.75, "style":0.15, "speed":1.0}},
            {"xi-api-key":require("ELEVENLABS_API_KEY"), "Accept":"audio/mpeg"},
            binary=True, label="ElevenLabs")

    def videos(self, query, page=1):
        params = urllib.parse.urlencode({"query":query, "orientation":"landscape", "per_page":40, "page":page})
        return request("https://api.pexels.com/videos/search?" + params,
                       headers={"Authorization":require("PEXELS_API_KEY")}, label="Pexels").get("videos", [])

    def download(self, url, path):
        host = urllib.parse.urlparse(url).hostname or ""
        if urllib.parse.urlparse(url).scheme != "https" or not (host.endswith(".pexels.com") or host.endswith(".pexels.io")):
            raise StudioError("Pexels returned a clip outside its approved download hosts.")
        temp = path.with_suffix(".part")
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with urllib.request.urlopen(url, timeout=180) as response, temp.open("wb") as output:
                shutil.copyfileobj(response, output)
            os.replace(temp, path)
        except (OSError, urllib.error.URLError, TimeoutError):
            temp.unlink(missing_ok=True)
            raise StudioError("A footage download failed. Finished clips are saved; retry to resume.") from None
