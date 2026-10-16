"""Readable, atomic project records and strictly sequential human gates."""
import copy
import hashlib
import json
import math
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from .verification import detect_conflicts, evidence_issues, fact_check, packaging_issues

STATUSES = "IDEA SELECTED RESEARCHING RESEARCH_COMPLETE SCRIPTING SCRIPT_REVIEW APPROVED VOICE VISUALS EDITING QA THUMBNAIL UPLOAD_READY PUBLISHED ANALYZING".split()
PILLARS = ["business", "technology", "mega projects", "countries", "hidden mechanisms"]
SCORE_LIMITS = {"curiosity":25, "audience":20, "story":20, "visuals":10,
                "evergreen":10, "monetization":10, "difficulty":5}
ROOT = Path(__file__).resolve().parent.parent
LOCKS = {}
LOCK_GUARD = threading.Lock()


class StudioError(Exception):
    """Safe, deliberately authored message suitable for the dashboard."""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temp, path)


def project_lock(pid):
    with LOCK_GUARD:
        return LOCKS.setdefault(pid, threading.RLock())


class Store:
    def __init__(self, root=None):
        self.root = Path(root or ROOT / "projects")

    def folder(self, pid):
        if not re.fullmatch(r"[a-f0-9]{12}", pid):
            raise StudioError("Project not found.")
        return self.root / pid

    def get(self, pid):
        try:
            return json.loads((self.folder(pid) / "project.json").read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise StudioError("Project not found.") from None

    def save(self, p):
        p["updated_at"] = now()
        atomic_json(self.folder(p["id"]) / "project.json", p)
        return p

    def list(self):
        if not self.root.exists():
            return []
        return sorted([json.loads(f.read_text(encoding="utf-8")) for f in self.root.glob("*/project.json")],
                      key=lambda p: p["created_at"], reverse=True)

    def create(self, data):
        topic = str(data.get("topic", "")).strip()
        if not topic or len(topic) > 250:
            raise StudioError("Enter a topic of 1 to 250 characters.")
        if data.get("pillar") not in PILLARS:
            raise StudioError("Choose one of the five content pillars.")
        quality = data.get("quality", "4K")
        if quality not in ("4K", "HD"):
            raise StudioError("Choose HD or 4K.")
        p = dict(id=uuid.uuid4().hex[:12], topic=topic, pillar=data["pillar"],
                 series=str(data.get("series", ""))[:150], next_idea=str(data.get("next_idea", ""))[:250],
                 quality=quality, burn_captions=False, status="IDEA", created_at=now(), updated_at=now(),
                 score={**{k:0 for k in SCORE_LIMITS}, "total":0, "confirmed":False, "override_reason":""},
                 questions=[], sources=[], claims=[], script={"scenes":[]}, revisions=[],
                 approvals={}, history=[], footage=[], titles=[], thumbnail={}, selected_title="",
                 description="", youtube_url="", analytics={}, artifacts={}, approval_history=[])
        return self.save(p)


def advance(p, target, actor="machine"):
    current = STATUSES.index(p["status"])
    if current + 1 >= len(STATUSES) or STATUSES[current + 1] != target:
        raise StudioError("A project can only move to the next production status.")
    p["history"].append({"at":now(), "from":p["status"], "to":target, "actor":actor})
    p["status"] = target


def invalidate(p, research=False):
    keys = ["script", "packaging"] + (["research"] if research else [])
    for key in keys:
        if key in p["approvals"]:
            p["approval_history"].append({"gate":key, **p["approvals"].pop(key), "withdrawn_at":now()})
    # Preserve revisions and files; never present stale outputs as current downloads.
    p["artifacts"] = {}
    p["qa"] = {}
    p["titles"] = []


def save_score(p, data):
    if p["status"] != "IDEA":
        raise StudioError("Topic scoring is locked after selection.")
    score = {}
    for key, limit in SCORE_LIMITS.items():
        val = data.get(key, 0)
        if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val) or not 0 <= val <= limit:
            raise StudioError(f"{key.capitalize()} must be between 0 and {limit}.")
        score[key] = val
    score["total"] = sum(score[k] for k in SCORE_LIMITS if k != "difficulty") - score["difficulty"]
    score["confirmed"] = data.get("confirmed") is True
    score["override_reason"] = str(data.get("override_reason", "")).strip()
    p["score"] = score


def add_claim(p, data):
    if STATUSES.index(p["status"]) < 2:
        raise StudioError("Start research before adding claims.")
    try:
        index = int(data["source_index"])
        if index < 0:
            raise ValueError
        source = p["sources"][index]
    except (KeyError, ValueError, IndexError, TypeError):
        raise StudioError("Choose a saved source by its index number.") from None
    existing = next((c for c in p["claims"] if c["id"] == data.get("id")), None)
    if data.get("id") and not existing:
        raise StudioError("Claim not found.")
    claim, quote = str(data.get("claim", "")).strip(), str(data.get("quote", "")).strip()
    if not claim:
        raise StudioError("Enter the claim text.")
    issues = evidence_issues(claim, quote, source["text"])
    item = dict(id=existing["id"] if existing else "c-" + uuid.uuid4().hex[:8],
                claim=claim, quote=quote, source_index=index, source_url=source["url"],
                source_title=source["title"], publication_date=source.get("publication_date", ""),
                date_checked=now(), confidence=1.0 if not issues else 0.0,
                status="verified" if not issues else "needs_checking", verified_by="code" if not issues else "",
                metric=str(data.get("metric", ""))[:200], note="", issues=issues)
    invalidate(p, research=True)
    if existing:
        p.setdefault("claim_revisions", []).append(copy.deepcopy(existing))
        existing.clear()
        existing.update(item)
    else:
        p["claims"].append(item)
    detect_conflicts(p["claims"])
    return item


def hand_verify(p, data):
    c = next((c for c in p["claims"] if c["id"] == data.get("id")), None)
    if not c:
        raise StudioError("Claim not found.")
    note = str(data.get("note", "")).strip()
    if not note:
        raise StudioError("Write a verification note explaining what you checked.")
    if data.get("status") not in ("verified", "rejected", "needs_checking"):
        raise StudioError("Choose a valid claim status.")
    invalidate(p, research=True)
    p.setdefault("claim_revisions", []).append(copy.deepcopy(c))
    c.update(status=data["status"], base_status=data["status"], verified_by="human",
             date_checked=now(), note=note, confidence=1.0 if data["status"] == "verified" else 0.0)
    detect_conflicts(p["claims"])


def save_script(p, data, reason="Human edit"):
    if STATUSES.index(p["status"]) < STATUSES.index("SCRIPTING"):
        raise StudioError("Approve research and start scripting first.")
    scenes = data.get("scenes")
    if not isinstance(scenes, list) or len(scenes) > 300:
        raise StudioError("Import a script JSON object with a scenes list (up to 300 scenes).")
    cleaned, ids = [], set()
    for i, s in enumerate(scenes):
        if not isinstance(s, dict) or not isinstance(s.get("claim_ids", []), list):
            raise StudioError("Each scene needs text, section, claim_ids and footage_query.")
        sid = str(s.get("id") or ("s-" + uuid.uuid4().hex[:8]))
        if sid in ids:
            raise StudioError("Each scene needs a unique ID.")
        ids.add(sid)
        cleaned.append({"id":sid, "section":str(s.get("section", "")), "text":str(s.get("text", "")),
                        "claim_ids":[str(x) for x in s.get("claim_ids", [])],
                        "footage_query":str(s.get("footage_query", ""))})
    invalidate(p)
    p["script"] = {"scenes":cleaned}
    p["revisions"].append({"revision":len(p["revisions"])+1, "at":now(), "reason":reason,
                           "script":copy.deepcopy(p["script"])})
    p["fact_check"] = fact_check(p["script"], p["claims"], p["next_idea"])


def research_valid(p):
    a = p["approvals"].get("research")
    return bool(a and a["fingerprint"] == digest(p["claims"]) and
                sum(c["status"] == "verified" for c in p["claims"]) >= 5)


def script_valid(p):
    a = p["approvals"].get("script")
    return bool(research_valid(p) and a and a["fingerprint"] == digest(p["script"]) and
                fact_check(p["script"], p["claims"], p["next_idea"])["passed"])


def packaging_valid(p):
    a = p["approvals"].get("packaging")
    return bool(a and a["fingerprint"] == digest([p["selected_title"], p["thumbnail"], p["description"]])
                and not packaging_issues(p["selected_title"], p["thumbnail"], p["claims"]))


def gate(p, data):
    name = data.get("gate")
    status = p["status"]
    index = STATUSES.index(status)
    note = str(data.get("note", "")).strip()
    if name == "topic":
        if status != "IDEA":
            raise StudioError("The topic gate is only available at IDEA.")
        s = p["score"]
        if not ((s["confirmed"] and s["total"] >= 75) or s["override_reason"]):
            raise StudioError("Confirm a topic score of at least 75, or write an override reason.")
        p["approvals"]["topic"] = {"at":now(), "by":"human", "fingerprint":digest(s), "note":s["override_reason"]}
        advance(p, "SELECTED", "human")
    elif name == "research":
        if index < STATUSES.index("RESEARCHING"):
            raise StudioError("Run research before approving it.")
        if sum(c["status"] == "verified" for c in p["claims"]) < 5:
            raise StudioError("Research needs at least 5 verified claims.")
        p["approvals"]["research"] = {"at":now(), "by":"human", "fingerprint":digest(p["claims"]), "note":note}
        if status == "RESEARCHING":
            advance(p, "RESEARCH_COMPLETE", "human")
    elif name == "script":
        if index < STATUSES.index("SCRIPT_REVIEW"):
            raise StudioError("Generate or import a script before approving it.")
        if not research_valid(p):
            raise StudioError("Research approval was withdrawn. Review and approve the claims again.")
        p["fact_check"] = fact_check(p["script"], p["claims"], p["next_idea"])
        if not p["fact_check"]["passed"]:
            raise StudioError("Script approval blocked: " + " ".join(p["fact_check"]["blocking"]))
        p["approvals"]["script"] = {"at":now(), "by":"human", "fingerprint":digest(p["script"]), "note":note}
        if status == "SCRIPT_REVIEW":
            advance(p, "APPROVED", "human")
    elif name == "packaging":
        if index < STATUSES.index("THUMBNAIL"):
            raise StudioError("Finish production QA before approving the title and thumbnail.")
        if not script_valid(p):
            raise StudioError("Review and approve the current script first.")
        issues = packaging_issues(p["selected_title"], p["thumbnail"], p["claims"])
        if issues:
            raise StudioError(" ".join(issues))
        if not p.get("qa", {}).get("passed"):
            raise StudioError("Run QA and resolve required checks first.")
        p["approvals"]["packaging"] = {"at":now(), "by":"human",
            "fingerprint":digest([p["selected_title"], p["thumbnail"], p["description"]]), "note":note}
        if status == "THUMBNAIL":
            advance(p, "UPLOAD_READY", "human")
    elif name == "published":
        if status != "UPLOAD_READY":
            raise StudioError("The project must be upload ready first.")
        if not script_valid(p) or not packaging_valid(p) or not p.get("qa", {}).get("passed"):
            raise StudioError("Restore all editorial approvals and pass QA before recording an upload.")
        url = str(data.get("youtube_url", "")).strip()
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.username or parsed.hostname not in ("youtube.com", "www.youtube.com", "youtu.be"):
            raise StudioError("Paste a valid HTTPS YouTube video URL after uploading manually.")
        valid_id = (re.fullmatch(r"/[A-Za-z0-9_-]{11}", parsed.path) if parsed.hostname == "youtu.be"
                    else re.search(r"(?:[?&]v=|/(?:shorts|live)/)[A-Za-z0-9_-]{11}(?:[&#?]|$)", url))
        if not valid_id:
            raise StudioError("Paste the URL of the uploaded video, not a channel or homepage.")
        p["youtube_url"] = url
        p["published_at"] = now()
        p["approvals"]["published"] = {"at":now(), "by":"human", "note":"Uploaded manually; URL recorded by human."}
        advance(p, "PUBLISHED", "human")
    else:
        raise StudioError("Unknown approval gate.")


def next_action(p):
    s = p["status"]
    if s == "IDEA":
        return "Human decision: confirm a score of at least 75, or write an override reason, then select the topic."
    if STATUSES.index(s) >= 3 and not research_valid(p):
        return "Human decision: review the changed evidence and approve at least 5 verified claims again."
    if STATUSES.index(s) >= 6 and not script_valid(p):
        return "Human decision: review and approve the changed script after resolving all blocking fact-check issues."
    if s == "UPLOAD_READY" and (not packaging_valid(p) or not p.get("qa", {}).get("passed")):
        return "Human decision: restore any changed production outputs with autopilot, then review and approve the title and thumbnail again."
    return {
        "SELECTED":"Run autopilot to plan research, read sources and check claims.",
        "RESEARCHING":"Human decision: read the evidence, resolve conflicting or unchecked claims, then approve research.",
        "RESEARCH_COMPLETE":"Run autopilot to draft the story and run the deterministic fact-check.",
        "SCRIPTING":"Run autopilot to complete the story draft.",
        "SCRIPT_REVIEW":"Human decision: read the script and fact-check, revise if needed, then approve the script.",
        "APPROVED":"Run autopilot to create narration, footage, a rough cut, QA and title drafts.",
        "VOICE":"Run autopilot to finish narration and footage.",
        "VISUALS":"Run autopilot to finish footage and begin editing.",
        "EDITING":"Run autopilot to render the video and perform QA.",
        "QA":"Resolve required QA issues, then run autopilot to prepare title drafts.",
        "THUMBNAIL":"Human decision: choose a supported title, review the thumbnail concept, then approve the upload package.",
        "UPLOAD_READY":"Human decision: download the package, create the final thumbnail and upload to YouTube manually. Then paste the video URL.",
        "PUBLISHED":"Human decision: enter CTR, average percentage viewed, and optional retention CSV.",
        "ANALYZING":"Review your channel comparison and use the findings for the next topic.",
    }[s]
