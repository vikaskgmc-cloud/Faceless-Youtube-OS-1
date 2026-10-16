"""Channel-relative comparisons, never generic industry benchmarks."""
import csv
import io
import math
from .core import StudioError, advance, now


def percentage(value, name):
    try:
        number = float(value)
    except (ValueError, TypeError):
        raise StudioError(f"Enter {name} as a percentage between 0 and 100.") from None
    if not math.isfinite(number) or not 0 <= number <= 100:
        raise StudioError(f"{name} must be between 0 and 100.")
    return number


def parse_retention(text):
    try:
        rows = list(csv.DictReader(io.StringIO(text.strip())))
        if not rows or not {"time", "percent"}.issubset(rows[0]):
            raise StudioError("Retention CSV needs the headers time,percent, with time in seconds.")
        points = []
        for row in rows:
            time = float(row["time"])
            if not math.isfinite(time) or time < 0:
                raise ValueError
            percent = percentage(row["percent"], "Retention")
            if points and time <= points[-1]["time"]:
                raise StudioError("Retention CSV times must be in increasing order, without duplicates.")
            points.append({"time":time, "percent":percent})
        return points
    except (ValueError, KeyError, TypeError, csv.Error):
        raise StudioError("Retention CSV must contain numeric time in seconds and percent values.") from None


def save_analytics(p, data):
    if p["status"] not in ("PUBLISHED", "ANALYZING"):
        raise StudioError("Record the manually uploaded YouTube URL before entering analytics.")
    metrics = {"ctr":percentage(data.get("ctr"), "CTR"),
               "average_percentage_viewed":percentage(data.get("average_percentage_viewed"), "Average percentage viewed"),
               "recorded_at":now(), "retention":p.get("analytics", {}).get("retention", [])}
    if str(data.get("retention_csv", "")).strip():
        metrics["retention"] = parse_retention(data["retention_csv"])
    p["analytics"] = metrics
    if p["status"] == "PUBLISHED":
        advance(p, "ANALYZING", "human")


def median(values):
    values = sorted(values)
    mid = len(values) // 2
    return values[mid] if len(values) % 2 else (values[mid-1]+values[mid])/2


def scene_at(p, time):
    for scene in p.get("timeline", []):
        if scene["start"] <= time < scene["end"]:
            return {"scene_id":scene["scene_id"], "section":scene["section"]}
    return {"scene_id":None, "section":"Outside the estimated scene timeline"}


def retention_report(p):
    points = p.get("analytics", {}).get("retention", [])
    if not points:
        return None
    at30 = None
    for point in points:
        if point["time"] == 30:
            at30 = point["percent"]
    for a, b in zip(points, points[1:]):
        if a["time"] < 30 < b["time"]:
            at30 = a["percent"] + (b["percent"]-a["percent"]) * (30-a["time"])/(b["time"]-a["time"])
    intervals = [{"start":a["time"], "end":b["time"], "change":b["percent"]-a["percent"],
                  "percentage_points_per_second":(b["percent"]-a["percent"])/(b["time"]-a["time"]),
                  **scene_at(p, (a["time"]+b["time"])/2)}
                 for a,b in zip(points, points[1:])]
    drops = [i for i in intervals if i["change"] < 0]
    rises = [i for i in intervals if i["change"] > 0]
    return {"at_30_seconds":at30, "at_30_scene":scene_at(p,30),
            "steepest_drop":min(drops, key=lambda x:x["percentage_points_per_second"], default=None),
            "biggest_rise":max(rises, key=lambda x:x["change"], default=None),
            "scene_timing":"Estimated by narration word share; intervals are mapped by their midpoint."}


def report(p, projects):
    metrics = p.get("analytics", {})
    if "ctr" not in metrics:
        return {"comparison":"Record CTR and average percentage viewed after manually uploading.", "baseline_count":0, "retention":retention_report(p)}
    # Earlier by manual publication time, not creation time or analytics entry order.
    prior = [x for x in projects if x["id"] != p["id"] and x.get("published_at", "~") < p.get("published_at", "")
             and "ctr" in x.get("analytics", {}) and "average_percentage_viewed" in x.get("analytics", {})]
    if len(prior) < 3:
        return {"comparison":f"Need at least 3 earlier videos with analytics; {len(prior)} available. No recommendation yet.",
                "baseline_count":len(prior), "retention":retention_report(p)}
    ctr = median([x["analytics"]["ctr"] for x in prior])
    apv = median([x["analytics"]["average_percentage_viewed"] for x in prior])
    high_ctr, high_retention = metrics["ctr"] >= ctr, metrics["average_percentage_viewed"] >= apv
    recommendation = {
        (True,True):"Make more like this.",
        (True,False):"Fix the script and pacing.",
        (False,True):"Fix the title and thumbnail.",
        (False,False):"Wrong concept: reconsider the topic and audience fit.",
    }[high_ctr, high_retention]
    return {"comparison":recommendation, "baseline_count":len(prior), "median_ctr":ctr,
            "median_average_percentage_viewed":apv, "high_ctr":high_ctr, "high_retention":high_retention,
            "tie_rule":"Equal to the median counts as high.", "retention":retention_report(p)}
