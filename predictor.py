"""Card 4 - Prediction engine.

Plain Python, no web code. Two jobs:
  1. estimate_air():     score -> estimated All India Rank (linear interpolation)
  2. classify_colleges(): estimated AIR -> Safe / Moderate / Reach lists
Data comes only from the supplied files in backend/data/.
"""
import json
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"

CATEGORIES = [("UR", "General (UR)"), ("EWS", "EWS"), ("OBC", "OBC"), ("SC", "SC"), ("ST", "ST")]

# From the score-to-rank file's own notes: below 144 not qualified (UR/EWS),
# reserved categories qualify from 113.
QUALIFYING_SCORE = {"UR": 144, "EWS": 144, "OBC": 113, "SC": 113, "ST": 113}

SAFE_PCT, REACH_PCT = 85, 115   # thresholds as % of the college closing rank
# Every matching college is returned (most competitive first). The website shows the first 10 and a "Show all" button.

DISCLAIMER = (
    "These are indicative estimates based on a supplied, approximate 2025 dataset "
    "(not official NTA or MCC data). Actual ranks, cutoffs and seat availability vary "
    "every year. This is not counselling advice and not a guarantee of admission."
)


def load_anchors(path=None):
    """Return [(score, air), ...] sorted by score, lowest first."""
    path = path or DATA_DIR / "neet_2025_score_to_rank.json"
    points = json.loads(Path(path).read_text())["points"]
    return sorted((p["score"], p["air"]) for p in points)


def load_colleges(path=None):
    path = path or DATA_DIR / "neet_2025_college_cutoffs.json"
    return json.loads(Path(path).read_text())["colleges"]


ANCHORS = load_anchors()
COLLEGES = load_colleges()


def interpolate_air(score, anchors):
    """Straight-line estimate between the two anchors that surround `score`."""
    for (s0, a0), (s1, a1) in zip(anchors, anchors[1:]):
        if s0 <= score <= s1:
            return a0 + (a1 - a0) * (score - s0) / (s1 - s0)
    raise ValueError("score outside anchor range")


def estimate_air(score, category, anchors=None):
    """Return (status, air, note). status is 'ok', 'not_qualified' or 'out_of_range'.

    We never invent ranks outside the supplied data:
      - below the qualifying mark  -> not_qualified
      - qualified but below the lowest anchor (113-143 for reserved) -> out_of_range
      - above the top anchor (686) -> AIR 1, with a note that it was capped
    """
    anchors = anchors or ANCHORS
    if score < QUALIFYING_SCORE[category]:
        return "not_qualified", None, (
            f"A score of {score} is below the {QUALIFYING_SCORE[category]} qualifying mark "
            f"for {category} in the supplied data, so no rank or colleges are shown."
        )
    lowest, highest = anchors[0][0], anchors[-1][0]
    if score < lowest:
        return "out_of_range", None, (
            f"Our dataset has no rank data below a score of {lowest}, so we cannot "
            "estimate a rank for your score without guessing."
        )
    if score > highest:
        return "ok", 1, f"Scores above {highest} are shown as AIR 1 because the data stops there."
    return "ok", int(interpolate_air(score, anchors) + 0.5), None


def classify_colleges(air, category, home_state, colleges=None):
    """Sort colleges into safe / moderate / reach using whole-number maths
    (no float rounding surprises at the 85% / 100% / 115% lines)."""
    colleges = COLLEGES if colleges is None else colleges
    groups = {"safe": [], "moderate": [], "reach": []}
    for c in colleges:
        closing = c["closing_rank_2025"][category]
        if air * 100 <= SAFE_PCT * closing:
            key = "safe"
        elif air <= closing:
            key = "moderate"
        elif air * 100 <= REACH_PCT * closing:
            key = "reach"
        else:
            continue  # beyond 115%: excluded
        groups[key].append({
            "id": c["id"], "college": c["college"], "city": c["city"], "state": c["state"],
            "type": c["type"], "closing_rank": closing,
            "in_home_state": c["state"] == home_state,
        })
    out = {}
    for key, rows in groups.items():
        rows.sort(key=lambda r: (r["closing_rank"], r["college"], r["city"]))  # most competitive first
        out[key] = {"total": len(rows), "colleges": rows}
    return out


def _explanation(score, air, category, groups):
    s, m, r = (groups[k]["total"] for k in ("safe", "moderate", "reach"))
    text = (
        f"Your score of {score} maps to an estimated AIR of about {air:,}. We compare that rank with each "
        f"college's 2025 closing rank for {category}: Safe means your AIR is within 85% of the closing rank, "
        f"Moderate means up to 100%, and Reach means up to 115%. "
    )
    if s + m + r == 0:
        return text + ("At this estimated rank, none of the 50 government colleges in our dataset fall within "
                       "115% of their closing rank for your category. Consider state-quota counselling for your home "
                       "state, which this tool does not cover, and talk to a counsellor about your options.")
    return text + f"We found {s} Safe, {m} Moderate and {r} Reach colleges."


def predict(score, category, home_state, anchors=None, colleges=None):
    status, air, note = estimate_air(score, category, anchors)
    result = {"status": status, "score": score, "category": category, "home_state": home_state,
              "estimated_air": air, "note": note, "disclaimer": DISCLAIMER}
    if status != "ok":
        result["groups"] = None
        result["explanation"] = note
        return result
    groups = classify_colleges(air, category, home_state, colleges)
    result["groups"] = groups
    result["explanation"] = _explanation(score, air, category, groups)
    return result


def summarize(result):
    """The teaser version of a prediction: rank, note and COUNTS per group, but no college names.
    Used by /api/estimate so the results page can say "34 matching colleges" before a lead is captured."""
    out = {k: v for k, v in result.items() if k not in ("groups", "explanation")}
    out["counts"] = None if result["groups"] is None else {k: g["total"] for k, g in result["groups"].items()}
    return out
