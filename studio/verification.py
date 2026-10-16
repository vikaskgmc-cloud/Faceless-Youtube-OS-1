"""Conservative deterministic evidence checks. No model participates here."""
import html
import re
import unicodedata
from decimal import Decimal

NUMBER = re.compile(r"(?<![\w.])[-+]?(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\.\d+)(?:[eE][-+]?\d+)?(?!\d|\.\d)")
MEANING = {
    "double", "doubled", "doubling", "triple", "tripled", "tripling", "quadrupled",
    "million", "millions", "billion", "billions", "trillion", "trillions",
    "thousand", "thousands", "most", "all", "only", "first", "last", "record",
    "largest", "smallest", "biggest", "fastest", "slowest", "highest", "lowest",
    "never", "always", "every", "none", "half", "halved", "majority",
    "increase", "increased", "decrease", "decreased", "more", "less", "over", "under",
    "not", "no", "without", "exactly", "approximately", "before", "after",
}
WORD_NUMBERS = {"ten":10, "eleven":11, "twelve":12, "thirteen":13, "fourteen":14,
                "fifteen":15, "sixteen":16, "seventeen":17, "eighteen":18,
                "nineteen":19, "twenty":20, "thirty":30, "forty":40, "fifty":50,
                "sixty":60, "seventy":70, "eighty":80, "ninety":90, "hundred":100}
SECTIONS = ["hook", "promise", "setup", "discovery", "escalation", "big reveal",
            "consequence", "next curiosity"]
SUSPENSE = ["but here's the thing", "what happened next", "little did they know",
            "everything changed", "you won't believe", "the truth is"]


def normalized(text):
    text = unicodedata.normalize("NFKC", html.unescape(str(text))).lower()
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    return re.sub(r"[\s\"'‘’“”`*_#~\\]", "", text)


def numbers(text):
    result = set()
    for match in NUMBER.finditer(str(text)):
        token = match.group().replace(",", "")
        canonical = str(Decimal(token).normalize())
        result.add(canonical)
        suffix = re.match(r"\s*(%|percent\b|trillions?\b|billions?\b|millions?\b|thousands?\b|bn\b|mn\b)", str(text)[match.end():], re.I)
        if suffix:
            unit = suffix[1].lower().rstrip("s")
            unit = {"%":"percent", "bn":"billion", "mn":"million"}.get(unit,unit)
            result.add(canonical + " " + unit)
    # Word figures are kept as distinct phrases; do not infer arithmetic.
    for word in re.findall(r"\b[a-z]+\b", str(text).lower()):
        if word in WORD_NUMBERS:
            result.add("word:" + word)
    return result


def evidence_issues(claim, quote, page):
    issues = []
    if len(normalized(quote)) < 20:
        issues.append("The quote must contain at least 20 meaningful characters.")
    if not normalized(quote) or normalized(quote) not in normalized(page):
        issues.append("The exact quote was not found in the saved page text.")
    missing = numbers(claim) - numbers(quote)
    if missing:
        issues.append("A figure in the claim is missing from its quote.")
    cw = set(re.findall(r"\b[a-z]+\b", claim.lower()))
    qw = set(re.findall(r"\b[a-z]+\b", quote.lower()))
    if (cw & MEANING) - qw:
        issues.append("Meaning-changing words are not supported by the quote: " +
                      ", ".join(sorted((cw & MEANING) - qw)) + ".")
    return issues


def metric_key(claim):
    explicit = claim.get("metric", "").strip().lower()
    text = explicit or NUMBER.sub("", claim["claim"].lower())
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def detect_conflicts(claims):
    for claim in claims:
        if claim["status"] == "conflicting":
            claim["status"] = claim.get("base_status", "needs_checking")
        claim["issues"] = [x for x in claim.get("issues", []) if not x.startswith("Conflicting metric")]
    groups = {}
    for claim in claims:
        if claim["status"] == "verified" and numbers(claim["claim"]):
            # Compare both explicit metric and number-stripped sentence.
            keys = {metric_key(claim), re.sub(r"\W+", " ", NUMBER.sub("", claim["claim"].lower())).strip()}
            for key in keys:
                groups.setdefault(key, []).append(claim)
    for key, group in groups.items():
        if len({tuple(sorted(numbers(c["claim"]))) for c in group}) > 1:
            for c in group:
                c["base_status"] = "verified"
                c["status"] = "conflicting"
                c["issues"].append("Conflicting metric: resolve or reject the contradictory evidence.")


def fact_check(script, claims, next_idea=""):
    blocking, warnings = [], []
    scenes = script.get("scenes", [])
    lookup = {c["id"]: c for c in claims}
    if not scenes:
        blocking.append("The script has no scenes.")
    found, total = set(), 0
    for scene in scenes:
        sid = scene.get("id", "?")
        text = scene.get("text", "")
        section = scene.get("section", "").lower().replace("_", " ")
        found.add(section)
        words = len(text.split())
        total += words
        allowed = set()
        ids = scene.get("claim_ids", [])
        if not ids:
            warnings.append(f"Scene {sid} has no cited claim.")
        for cid in ids:
            c = lookup.get(cid)
            if not c:
                blocking.append(f"Scene {sid} cites unknown claim {cid}.")
            elif c["status"] != "verified":
                blocking.append(f"Scene {sid} cites {cid}, which is not verified.")
            else:
                allowed |= numbers(c["claim"])
        figures = set()
        scene_numbers = numbers(text)
        for m in NUMBER.finditer(text):
            canonical = str(Decimal(m.group().replace(",", "")).normalize())
            qualified = {n for n in scene_numbers if n.startswith(canonical + " ")}
            scaled = any(not n.endswith(" percent") for n in qualified)
            if "." in m.group() or abs(Decimal(m.group().replace(",", ""))) >= 10 or scaled:
                figures |= numbers(m.group()) | qualified
        figures |= {n for n in scene_numbers if n.startswith("word:")}
        if figures - allowed:
            blocking.append(f"Scene {sid} contains a figure not supported by its own cited claims.")
        if section == "hook":
            if words > 55:
                warnings.append("The hook is longer than 55 words.")
            if not text.rstrip().endswith("?"):
                warnings.append("The hook should end on a question.")
        query_words = len(scene.get("footage_query", "").split())
        if not 2 <= query_words <= 5:
            warnings.append(f"Scene {sid} needs a stock footage phrase of 2 to 5 words.")
    for required in ("hook", "big reveal", "next curiosity"):
        if required not in found:
            warnings.append(f"Missing {required} section.")
    full = "\n".join(s.get("text", "") for s in scenes).lower()
    if re.search(r"\b(subscribe|smash.{0,12}like|hit.{0,12}bell)\b", full):
        warnings.append("Remove subscribe or engagement begging.")
    for phrase in SUSPENSE:
        if full.count(phrase) > 1:
            warnings.append(f"Overused suspense phrase: {phrase}.")
    if next_idea and scenes:
        last_line = scenes[-1].get("text", "").strip().splitlines()[-1:]
        if not last_line or normalized(next_idea) not in normalized(last_line[0]):
            blocking.append("The final line must tease the project's next idea (include its exact wording).")
    return {"blocking": list(dict.fromkeys(blocking)), "warnings": list(dict.fromkeys(warnings)),
            "words": total, "estimated_minutes": round(total / 150, 2), "passed": not blocking}


def packaging_issues(title, thumbnail, claims):
    issues = []
    if not title.strip() or len(title) >= 70:
        issues.append("Choose a title shorter than 70 characters.")
    if title.isupper():
        issues.append("The title must not be all caps.")
    if not 1 <= len(thumbnail.get("text", "").split()) <= 4:
        issues.append("Thumbnail text must have 1 to 4 words.")
    if not thumbnail.get("object", "").strip() or not thumbnail.get("emotion", "").strip():
        issues.append("The thumbnail needs one object and an emotion or contrast.")
    supported = set()
    for c in claims:
        if c["status"] == "verified":
            supported |= numbers(c["claim"])
    for label, text in (("Title", title), ("Thumbnail", " ".join(thumbnail.values()))):
        if numbers(text) - supported:
            issues.append(f"{label} contains an unsupported figure.")
    return issues
