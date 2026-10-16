"""Machine steps stop at human gates. No upload or YouTube API exists."""
import json
from . import media
from .core import (STATUSES, SCORE_LIMITS, StudioError, add_claim, advance, digest, gate,
                   next_action, packaging_valid, research_valid, save_score, save_script, script_valid)
from .providers import LiveProviders
from .verification import packaging_issues

FAMILIES = {"curiosity", "mystery", "contrarian", "scale", "question"}


class Pipeline:
    def __init__(self, store, provider=None, progress=None):
        self.store = store
        self.provider = provider or LiveProviders()
        self.progress = progress or (lambda message: None)

    def research(self, p):
        if p["status"] == "SELECTED":
            advance(p, "RESEARCHING")
            self.store.save(p)
        if p["status"] != "RESEARCHING":
            raise StudioError("Research runs after topic selection, before human research approval.")
        if p.get("research_machine_complete"):
            return
        if not p["questions"]:
            self.progress("Planning 8 to 12 research questions.")
            data = self.provider.llm("research_plan", {"topic":p["topic"], "pillar":p["pillar"]})
            questions = data.get("questions", [])
            if not isinstance(questions, list) or not 8 <= len(questions) <= 12 or not all(isinstance(q,str) and q.strip() for q in questions):
                raise StudioError("The research planner must return 8 to 12 questions. Retry to generate a valid plan.")
            p["questions"] = questions
            self.store.save(p)
        seen = {s["url"] for s in p["sources"]}
        completed = p.setdefault("searched_questions", [])
        for question in p["questions"]:
            if question in completed:
                continue
            self.progress("Searching and reading: " + question[:100])
            results = self.provider.search(question)
            for result in results[:3]:
                url = result.get("url", "")
                if not url.startswith(("https://", "http://")) or url in seen:
                    continue
                source = self.provider.read(url)
                p["sources"].append(source)
                seen.add(url)
                self.store.save(p)
            completed.append(question)
            self.store.save(p)
        if not p["sources"]:
            raise StudioError("No readable research sources were found. Add a source or try more specific questions.")
        self.progress("Extracting exact quotes. Code—not the AI—will decide verification.")
        # Keep source indices stable. Truncation limits model context only; verifier checks full saved text.
        payload = {"topic":p["topic"], "sources":[{"source_index":i, "title":s["title"], "text":s["text"][:18000]}
                                              for i,s in enumerate(p["sources"])]}
        data = self.provider.llm("extract_claims", payload)
        claims = data.get("claims", [])
        if not isinstance(claims, list) or not claims:
            raise StudioError("No structured claims were extracted. Retry or add evidence manually.")
        for item in claims:
            if not isinstance(item, dict):
                continue
            # Source is selected solely from the saved indexed list, never an AI supplied URL/status.
            try:
                if type(item.get("source_index")) is not int:
                    raise StudioError("Invalid source index.")
                if any(c["claim"] == item.get("claim") and c["source_index"] == item["source_index"] for c in p["claims"]):
                    continue
                add_claim(p, item)
                self.store.save(p)
            except StudioError:
                p.setdefault("research_notices", []).append("An AI claim with an invalid source index or empty text was discarded.")
        p["research_machine_complete"] = True
        self.store.save(p)

    def script(self, p):
        if not research_valid(p):
            raise StudioError("A human must approve at least 5 verified claims before scripting.")
        if p["status"] == "RESEARCH_COMPLETE":
            advance(p, "SCRIPTING")
            self.store.save(p)
        if STATUSES.index(p["status"]) < STATUSES.index("SCRIPTING"):
            raise StudioError("Finish and approve research first.")
        self.progress("Drafting the story from verified claims only.")
        draft = self.provider.llm("script", {"topic":p["topic"], "next_idea":p["next_idea"],
                      "claims":[{"id":c["id"], "claim":c["claim"]} for c in p["claims"] if c["status"] == "verified"]})
        save_script(p, draft, "AI draft; deterministic fact-check run")
        if p["status"] == "SCRIPTING":
            advance(p, "SCRIPT_REVIEW")
        self.store.save(p)

    def titles(self, p):
        if not script_valid(p) or not p.get("qa", {}).get("passed"):
            raise StudioError("Finish required production QA before generating title drafts.")
        self.progress("Preparing five title families and a thumbnail concept.")
        data = self.provider.llm("titles", {"topic":p["topic"], "script":p["script"],
                             "claims":[c["claim"] for c in p["claims"] if c["status"] == "verified"]})
        titles, thumbnail = data.get("titles", []), data.get("thumbnail", {})
        if not isinstance(titles, list) or len(titles) != 5 or {t.get("family") for t in titles if isinstance(t,dict)} != FAMILIES:
            raise StudioError("The title draft must include exactly one title from each of the five families. Retry or enter a title manually.")
        if not isinstance(thumbnail, dict) or not all(isinstance(v,str) for v in thumbnail.values()):
            raise StudioError("The thumbnail draft is invalid. Retry or enter a concept manually.")
        for title in titles:
            issues = packaging_issues(str(title.get("title", "")), thumbnail, p["claims"])
            if issues:
                raise StudioError("Title draft failed checks: " + " ".join(issues))
        p["titles"] = titles
        p["thumbnail"] = thumbnail
        p["description"] = str(data.get("description", ""))
        p["selected_title"] = ""  # A human chooses; never auto-select an AI title.
        p["approvals"].pop("packaging", None)
        p["artifacts"].pop("package", None)
        self.store.save(p)

    def step(self, p, step):
        folder = self.store.folder(p["id"])
        if step == "research":
            self.research(p)
        elif step == "script":
            self.script(p)
        elif step in ("voice", "footage", "cut", "qa", "titles"):
            if not script_valid(p):
                raise StudioError("Human approval of the current research and script is required.")
            if step == "voice":
                if p["status"] == "APPROVED":
                    advance(p, "VOICE")
                    self.store.save(p)
                if STATUSES.index(p["status"]) < STATUSES.index("VOICE"):
                    raise StudioError("Approve the script before generating voice.")
                self.invalidate_production(p, ("footage", "video", "captions", "package", "package_text"))
                media.voice(p, folder, self.provider, self.progress)
                if p["status"] == "VOICE":
                    advance(p, "VISUALS")
            elif step == "footage":
                if STATUSES.index(p["status"]) < STATUSES.index("VISUALS"):
                    raise StudioError("Generate narration before selecting footage.")
                self.invalidate_production(p, ("video", "captions", "package", "package_text"))
                media.footage(p, folder, self.provider, self.progress, self.store.save)
                if p["status"] == "VISUALS":
                    advance(p, "EDITING")
            elif step == "cut":
                if STATUSES.index(p["status"]) < STATUSES.index("EDITING"):
                    raise StudioError("Complete the footage step before editing.")
                self.invalidate_production(p, ("video", "package", "package_text"))
                self.store.save(p)
                media.cut(p, folder, self.progress)
                if p["status"] == "EDITING":
                    advance(p, "QA")
            elif step == "qa":
                if STATUSES.index(p["status"]) < STATUSES.index("QA"):
                    raise StudioError("Render the rough cut before QA.")
                result = media.qa(p, folder)
                self.store.save(p)
                if not result["passed"]:
                    raise StudioError("QA needs attention: " + "; ".join(c["label"] for c in result["required"] if not c["passed"]) + ".")
                if p["status"] == "QA":
                    advance(p, "THUMBNAIL")
            else:
                if STATUSES.index(p["status"]) < STATUSES.index("THUMBNAIL"):
                    raise StudioError("Complete QA before preparing titles.")
                self.titles(p)
        else:
            raise StudioError("Unknown machine step.")
        self.store.save(p)

    def invalidate_production(self, p, keys):
        for key in keys:
            p["artifacts"].pop(key, None)
        p["qa"] = {}
        p["approvals"].pop("packaging", None)
        self.store.save(p)

    def autopilot(self, p):
        # A gate is a stop, never an automatic approval.
        if p["status"] in ("IDEA", "PUBLISHED", "ANALYZING"):
            return next_action(p)
        if p["status"] == "UPLOAD_READY" and packaging_valid(p) and p["artifacts"].get("video"):
            return next_action(p)
        if p["status"] in ("SELECTED", "RESEARCHING"):
            self.step(p, "research")
            return next_action(p)
        if not research_valid(p):
            return next_action(p)
        if p["status"] in ("RESEARCH_COMPLETE", "SCRIPTING"):
            self.step(p, "script")
            return next_action(p)
        if p["status"] == "SCRIPT_REVIEW" or not script_valid(p):
            return next_action(p)
        if "narration" not in p["artifacts"] or p["status"] in ("APPROVED", "VOICE"):
            self.step(p, "voice")
        if "footage" not in p["artifacts"] or p["status"] == "VISUALS":
            self.step(p, "footage")
        if "video" not in p["artifacts"] or p["status"] == "EDITING":
            self.step(p, "cut")
        self.step(p, "qa")
        if not p["titles"]:
            self.step(p, "titles")
        return next_action(p)


def upload_package(p, folder):
    if not packaging_valid(p):
        raise StudioError("The human must approve the current title and thumbnail first.")
    sources = list(dict.fromkeys(c["source_url"] for c in p["claims"] if c["status"] == "verified"))
    credits_path = folder / "media/credits.txt"
    credits = credits_path.read_text(encoding="utf-8") if credits_path.exists() else ""
    package = {"title":p["selected_title"], "description_draft":p["description"],
               "channel_promise":"The world behind the headlines", "sources":sources,
               "pexels_credit_line":"Video footage provided by Pexels — https://www.pexels.com",
               "clip_credits":credits, "thumbnail_concept":p["thumbnail"],
               "upload_instructions":"Create the final thumbnail. Upload the video and captions manually on YouTube. This app never uploads or publishes anything.",
               "captions_note":"SRT timings are estimated by word share; manually review.",
               "music":p.get("music", {}).get("filename", "None")}
    path = folder / "upload-package.json"
    path.write_text(json.dumps(package, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    text = p["selected_title"] + "\n\n" + p["description"] + "\n\nSources\n" + "\n".join(sources) + "\n\n" + credits
    (folder / "upload-package.txt").write_text(text, encoding="utf-8")
    p["artifacts"]["package"] = {"path":"upload-package.json"}
    p["artifacts"]["package_text"] = {"path":"upload-package.txt"}
