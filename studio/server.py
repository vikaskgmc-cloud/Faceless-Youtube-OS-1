"""Small same-origin HTTP dashboard. All mutations are explicit JSON POSTs."""
import base64
import binascii
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import threading
import time
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from . import analytics, media
from .core import (PILLARS, ROOT, SCORE_LIMITS, STATUSES, Store, StudioError, add_claim,
                   advance, digest, gate, hand_verify, invalidate, next_action, now, project_lock,
                   research_valid, save_score, save_script)
from .pipeline import Pipeline, upload_package
from .providers import LiveProviders, settings
from .verification import fact_check, packaging_issues

STORE = Store()
JOBS = {}
JOBS_LOCK = threading.Lock()
MACHINE_LOCK = threading.Lock()  # CPU/memory: one media/API production job at a time.
SESSION_COOKIE = "studio_session"
SESSION_MAX_AGE = 12 * 60 * 60


def files_for(p):
    files = []
    for kind, artifact in p["artifacts"].items():
        path = artifact.get("path")
        if path and (STORE.folder(p["id"]) / path).is_file():
            files.append({"name":path.split("/")[-1], "kind":kind,
                          "url":f"/files/{p['id']}/{path}"})
    return files


def enriched(p, projects=None):
    p["fact_check"] = fact_check(p["script"], p["claims"], p["next_idea"])
    p["next_action"] = next_action(p)
    p["analytics_report"] = analytics.report(p, projects if projects is not None else STORE.list())
    p["files"] = files_for(p)
    p["job"] = dict(JOBS[p["id"]]) if p["id"] in JOBS else None
    p["packaging_issues"] = packaging_issues(p["selected_title"], p["thumbnail"], p["claims"])
    return p


def start_job(pid, step=None):
    with JOBS_LOCK:
        if any(j.get("running") for j in JOBS.values()):
            raise StudioError("Another production job is running. Wait for it to finish before starting another.")
        JOBS[pid] = {"running":True, "message":"Starting production step.", "error":None}
    def progress(message):
        with JOBS_LOCK:
            JOBS[pid]["message"] = message
    def work():
        with MACHINE_LOCK, project_lock(pid):
            try:
                p = STORE.get(pid)
                pipeline = Pipeline(STORE, progress=progress)
                if step:
                    pipeline.step(p, step)
                    result = next_action(p)
                else:
                    result = pipeline.autopilot(p)
                STORE.save(p)
                with JOBS_LOCK:
                    JOBS[pid].update(running=False, message=result, error=None)
            except StudioError as e:
                with JOBS_LOCK:
                    JOBS[pid].update(running=False, error=str(e), message=str(e))
            except Exception:
                # Deliberately no traceback, response body, or exception text in any log.
                message = "This step could not finish. Saved work is kept. Check your input, provider configuration and available disk space, then retry."
                with JOBS_LOCK:
                    JOBS[pid].update(running=False, error=message, message=message)
    threading.Thread(target=work, daemon=True).start()
    return {"running":True, "message":"Production job started. It will stop at the next human gate."}


def mutate(p, action, data):
    if action == "score":
        save_score(p, data)
    elif action == "gate":
        # Refresh QA immediately at packaging/publication gates, not just the cached flag.
        if data.get("gate") in ("packaging", "published"):
            media.qa(p, STORE.folder(p["id"]))
        gate(p, data)
        if data.get("gate") == "packaging":
            upload_package(p, STORE.folder(p["id"]))
    elif action == "source":
        if STATUSES.index(p["status"]) < 2:
            raise StudioError("Start research before adding source evidence.")
        url = str(data.get("url", "")).strip()
        if urlparse(url).scheme not in ("http", "https") or not urlparse(url).hostname:
            raise StudioError("Enter the public source page's full HTTP or HTTPS URL.")
        text = str(data.get("text", "")).strip()
        if not text:
            raise StudioError("Paste the source page text so quotes can be checked against it.")
        p["sources"].append({"url":url, "title":str(data.get("title", "")) or url, "text":text,
                              "publication_date":str(data.get("publication_date", "")), "origin":"human-supplied page text"})
        invalidate(p, research=True)
    elif action == "claim":
        add_claim(p, data)
    elif action == "verify_claim":
        hand_verify(p, data)
    elif action in ("script", "import_script"):
        if action == "import_script":
            try:
                data = json.loads(data.get("text", ""))
                if not isinstance(data, dict):
                    raise ValueError
            except (ValueError, TypeError):
                raise StudioError("Import a JSON script with a scenes list, using the exported format.") from None
        if p["status"] == "RESEARCH_COMPLETE":
            if not research_valid(p):
                raise StudioError("Review and approve the current research before writing the script.")
            advance(p, "SCRIPTING", "human")
        save_script(p, data, "Imported human revision" if action == "import_script" else "Human edit")
        if p["status"] == "SCRIPTING":
            advance(p, "SCRIPT_REVIEW", "human")
    elif action == "settings":
        if "quality" in data and data["quality"] not in ("HD", "4K"):
            raise StudioError("Choose HD or 4K output.")
        if "burn_captions" in data and type(data["burn_captions"]) is not bool:
            raise StudioError("Choose whether to burn captions.")
        if "next_idea" in data and str(data["next_idea"]) != p["next_idea"]:
            p["next_idea"] = str(data["next_idea"])[:250]
            invalidate(p)
        if (data.get("quality", p["quality"]) != p["quality"] or
                data.get("burn_captions", p["burn_captions"]) != p["burn_captions"]):
            for key in ("footage", "video", "captions", "credits", "package", "package_text"):
                p["artifacts"].pop(key, None)
            p["qa"] = {}
            p["approvals"].pop("packaging", None)
        p["quality"] = data.get("quality", p["quality"])
        p["burn_captions"] = data.get("burn_captions", p["burn_captions"])
    elif action == "packaging":
        thumbnail = data.get("thumbnail", {})
        if not isinstance(thumbnail, dict):
            raise StudioError("Enter a thumbnail object, emotion and text.")
        p["selected_title"] = str(data.get("selected_title", "")).strip()
        p["thumbnail"] = {k:str(thumbnail.get(k, "")) for k in ("object", "emotion", "text")}
        p["description"] = str(data.get("description", ""))
        p["approvals"].pop("packaging", None)
        for key in ("package", "package_text"):
            p["artifacts"].pop(key, None)
    elif action == "analytics":
        analytics.save_analytics(p, data)
    elif action == "music":
        if data.get("rights_confirmed") is not True:
            raise StudioError("Confirm you have royalty-free or other appropriate rights to use this music.")
        filename = os.path.basename(str(data.get("filename", "")))
        extension = os.path.splitext(filename)[1].lower()
        if extension not in (".wav", ".mp3", ".m4a", ".ogg", ".flac"):
            raise StudioError("Upload a WAV, MP3, M4A, OGG or FLAC music file.")
        try:
            content = base64.b64decode(data.get("data", ""), validate=True)
        except (ValueError, binascii.Error):
            raise StudioError("The music upload was unreadable. Select the audio file again.") from None
        if not content or len(content) > 50 * 1024 * 1024:
            raise StudioError("Music files must be non-empty and no larger than 50 MB.")
        path = STORE.folder(p["id"]) / "uploads" / ("music" + extension)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name("candidate" + extension)
        temp.write_bytes(content)
        try:
            info = media.probe(temp)
            if not any(s.get("codec_type") == "audio" for s in info["streams"]):
                raise StudioError("That file has no audio track.")
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)
        p["music"] = {"path":f"uploads/music{extension}", "filename":filename,
                      "rights_confirmed":True, "uploaded_at":now()}
        for key in ("video", "package", "package_text"):
            p["artifacts"].pop(key, None)
        p["qa"] = {}
        p["approvals"].pop("packaging", None)
    elif action == "suggest_score":
        if p["status"] != "IDEA":
            raise StudioError("Score suggestions are only available before selecting the topic.")
        draft = LiveProviders().llm("score", {"topic":p["topic"], "pillar":p["pillar"]})
        temporary = {"status":"IDEA"}
        save_score(temporary, draft)
        p["score_suggestion"] = {**temporary["score"], "confirmed":False, "rationale":str(draft.get("rationale", ""))}
    else:
        raise StudioError("Unknown action.")


class Handler(BaseHTTPRequestHandler):
    server_version = "FacelessStudio"

    def log_message(self, *args):
        pass  # Never log request URLs, bodies or credentials.

    def respond(self, value, status=200):
        content = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)

    def respond_html(self, content, status=200):
        body = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def configured_credentials():
        password = os.environ.get("STUDIO_ACCESS_PASSWORD", "")
        secret = os.environ.get("SESSION_SECRET", "")
        if len(password) < 16 or len(secret) < 32:
            return None
        return password, secret

    @staticmethod
    def session_signature(expiry, password, secret):
        key = hmac.new(secret.encode(), b"faceless-studio-session:" + password.encode(), hashlib.sha256).digest()
        return hmac.new(key, str(expiry).encode(), hashlib.sha256).hexdigest()

    def is_authorized(self):
        credentials = self.configured_credentials()
        if credentials is None:
            return False
        password, secret = credentials
        try:
            cookies = SimpleCookie(self.headers.get("Cookie", ""))
            token = cookies[SESSION_COOKIE].value
            expiry_text, signature = token.split(".", 1)
            expiry = int(expiry_text)
        except (CookieError, KeyError, ValueError, TypeError):
            return False
        if expiry <= int(time.time()) or expiry > int(time.time()) + SESSION_MAX_AGE + 60:
            return False
        expected = self.session_signature(expiry, password, secret)
        try:
            return secrets.compare_digest(signature.encode("ascii"), expected.encode("ascii"))
        except UnicodeEncodeError:
            return False

    def login_page(self, status=200, error=""):
        message = f'<p role="alert" style="color:#a33">{error}</p>' if error else ""
        content = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sign in · Documentary Studio</title><style>
*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#f3f1e8;color:#202522;font:16px system-ui,sans-serif}}
main{{width:min(440px,calc(100% - 32px));padding:36px;background:#fff;border:1px solid #deddd5;border-radius:12px;box-shadow:0 12px 40px #20252212}}
h1{{font-size:28px;margin:0 0 8px}}p{{line-height:1.5;color:#535950}}label{{display:block;margin:24px 0 8px;font-weight:600}}
input{{width:100%;padding:12px;border:1px solid #a9ada4;border-radius:6px;font:inherit}}button{{margin-top:18px;width:100%;padding:12px;border:0;border-radius:6px;background:#253c39;color:white;font:inherit;font-weight:700;cursor:pointer}}
</style></head><body><main><h1>Documentary Studio</h1><p>Sign in to access your private production workspace.</p>{message}
<form method="post" action="/auth/login"><label for="password">Studio password</label><input id="password" name="password" type="password" autocomplete="current-password" required autofocus><button type="submit">Sign in</button></form></main></body></html>"""
        self.respond_html(content, status)

    def unauthorized(self):
        path = urlparse(self.path).path
        if path.startswith(("/api/", "/files/")):
            return self.respond({"error":"Sign in to access the studio."}, 401)
        self.send_response(303)
        self.send_header("Location", "/login")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def same_origin(self):
        origin = self.headers.get("Origin")
        return not origin or urlparse(origin).netloc == self.headers.get("Host")

    def login(self):
        if not self.same_origin():
            return self.login_page(403, "Cross-site sign-in is not allowed.")
        credentials = self.configured_credentials()
        if credentials is None:
            return self.login_page(503, "Studio access is not configured. Set STUDIO_ACCESS_PASSWORD (at least 16 characters) and SESSION_SECRET (at least 32 characters) in Replit Secrets, then restart.")
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 4096:
                raise ValueError
            form = parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
            supplied = form.get("password", [""])[0]
        except (ValueError, UnicodeError):
            return self.login_page(400, "Enter a valid studio password.")
        password, secret = credentials
        if not secrets.compare_digest(supplied.encode("utf-8"), password.encode("utf-8")):
            return self.login_page(401, "The studio password is incorrect.")
        expiry = int(time.time()) + SESSION_MAX_AGE
        signature = self.session_signature(expiry, password, secret)
        token = f"{expiry}.{signature}"
        self.send_response(303)
        self.send_header("Location", "/")
        self.send_header("Set-Cookie", f"{SESSION_COOKIE}={token}; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age={SESSION_MAX_AGE}")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        try:
            if urlparse(self.path).path == "/login":
                if self.configured_credentials() is None:
                    return self.login_page(503, "Studio access is not configured. Set STUDIO_ACCESS_PASSWORD (at least 16 characters) and SESSION_SECRET (at least 32 characters) in Replit Secrets, then restart.")
                return self.login_page()
            if not self.is_authorized():
                return self.unauthorized()
            self.get_route()
        except StudioError as e:
            self.respond({"error":str(e)}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self.respond({"error":"This page could not load. Check that your project files are readable and try again."}, 500)

    def get_route(self):
        path = unquote(urlparse(self.path).path)
        if path == "/api/settings":
            return self.respond(settings())
        if path == "/api/overview":
            projects = STORE.list()
            return self.respond({"projects":[enriched(p, projects) for p in projects], "statuses":STATUSES,
                                 "pillars":PILLARS, "settings":settings(), "jobs":dict(JOBS)})
        match = re.fullmatch(r"/api/projects/([a-f0-9]{12})(/export-script)?", path)
        if match:
            p = STORE.get(match[1])
            if match[2]:
                content = json.dumps(p["script"], indent=2, ensure_ascii=False).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Disposition", 'attachment; filename="script.json"')
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return
            return self.respond(enriched(p))
        match = re.fullmatch(r"/files/([a-f0-9]{12})/(.+)", path)
        if match:
            p = STORE.get(match[1])
            relative = match[2]
            allowed = {a.get("path") for a in p["artifacts"].values()}
            if relative not in allowed:
                raise StudioError("This output is missing or stale. Restore approvals and rerun production to create current downloads.")
            folder = STORE.folder(p["id"]).resolve()
            file = (folder / relative).resolve()
            if folder not in file.parents:
                raise StudioError("File not found.")
            return self.send_file(file, download=file.suffix in (".json", ".txt", ".srt"))
        if path.startswith(("/api/", "/files/")):
            raise StudioError("Page not found.")
        static = {"app.js", "style.css", "favicon.svg"}
        if path.lstrip("/") in static:
            return self.send_file(ROOT / "web" / path.lstrip("/"))
        return self.send_file(ROOT / "web/index.html")

    def send_file(self, path, download=False):
        if not path.is_file():
            raise StudioError("File not found.")
        size = path.stat().st_size
        start, end = 0, size-1
        range_header = self.headers.get("Range")
        if range_header:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header)
            if not match or not any(match.groups()):
                self.send_error(416)
                return
            if not match[1]:
                start = max(0, size-int(match[2]))
            else:
                start = int(match[1])
                end = min(end, int(match[2])) if match[2] else end
            if start > end or start >= size:
                self.send_error(416)
                return
        self.send_response(206 if range_header else 200)
        self.send_header("Content-Type", mimetypes.guess_type(str(path))[0] or "application/octet-stream")
        self.send_header("Content-Length", str(end-start+1))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        if range_header:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        if download:
            self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
        self.end_headers()
        with path.open("rb") as f:
            f.seek(start)
            remaining = end-start+1
            while remaining > 0:
                block = f.read(min(1024*1024, remaining))
                if not block:
                    break
                self.wfile.write(block)
                remaining -= len(block)

    def do_POST(self):
        try:
            path = unquote(urlparse(self.path).path)
            if path == "/auth/login":
                return self.login()
            if not self.is_authorized():
                return self.unauthorized()
            if path == "/auth/logout":
                if not self.same_origin():
                    return self.respond({"error":"Cross-site changes are not allowed."}, 403)
                self.send_response(303)
                self.send_header("Location", "/login")
                self.send_header("Set-Cookie", f"{SESSION_COOKIE}=; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=0")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", "0")
                return self.end_headers()
            if self.headers.get("X-Studio-Request") != "1":
                raise StudioError("Use the studio dashboard to make changes.")
            if not self.same_origin():
                raise StudioError("Cross-site changes are not allowed.")
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 72 * 1024 * 1024:
                raise StudioError("The request is empty or too large.")
            try:
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError
            except (ValueError, UnicodeError):
                raise StudioError("Send a valid JSON form.") from None
            if path == "/api/projects":
                return self.respond(enriched(STORE.create(data)), 201)
            match = re.fullmatch(r"/api/projects/([a-f0-9]{12})/([a-z_]+)", path)
            if not match:
                raise StudioError("Action not found.")
            pid, action = match.groups()
            # Read-only dry-run needs no API credential and may be used while idle at any stage.
            if JOBS.get(pid, {}).get("running"):
                raise StudioError("This project is busy. Wait for the current step before editing.")
            if action in ("autopilot", "run", "rerun"):
                STORE.get(pid)
                return self.respond(start_job(pid, str(data.get("step", "")) if action == "run" else None), 202)
            with project_lock(pid):
                if JOBS.get(pid, {}).get("running"):
                    raise StudioError("This project is busy. Wait for the current step before editing.")
                p = STORE.get(pid)
                if action == "dry_run":
                    plan = media.chunk_plan(p)
                    return self.respond({"chunks":plan, "total_characters":sum(c["characters"] for c in plan)})
                mutate(p, action, data)
                STORE.save(p)
                return self.respond(enriched(p))
        except StudioError as e:
            self.respond({"error":str(e)}, 400)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self.respond({"error":"This action could not finish. Check the form and provider configuration, then try again. Saved work has been kept."}, 500)


def main():
    print("Faceless YouTube OS is available on port 5000. Manual uploads only.", flush=True)
    server = ThreadingHTTPServer(("0.0.0.0", 5000), Handler)
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main()
