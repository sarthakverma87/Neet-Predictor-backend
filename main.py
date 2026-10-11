"""Card 3 - FastAPI backend. Run:  uvicorn main:app --reload --port 8000"""
import os
import re
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

import database
import predictor

# States and union territories offered in the dropdown. The dataset only has colleges in 24 of them;
# the rest are valid choices but will never get an "In your state" badge.
STATES = [
    "Andaman & Nicobar Islands", "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chandigarh",
    "Chhattisgarh", "Dadra & Nagar Haveli and Daman & Diu", "Delhi", "Goa", "Gujarat", "Haryana",
    "Himachal Pradesh", "Jammu & Kashmir", "Jharkhand", "Karnataka", "Kerala", "Ladakh", "Lakshadweep",
    "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram", "Nagaland", "Odisha", "Puducherry",
    "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand",
    "West Bengal",
]

app = FastAPI(title="NEET College Predictor")

origins = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "http://localhost:5173").split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST"], allow_headers=["*"])

database.init_db()


class EstimateRequest(BaseModel):
    """Just the three NEET details from Page 1. Nothing personal."""
    score: int = Field(ge=0, le=720)
    category: Literal["UR", "EWS", "OBC", "SC", "ST"]
    home_state: str

    @field_validator("home_state")
    @classmethod
    def check_state(cls, v):
        if v not in STATES:
            raise ValueError("Please choose your home state.")
        return v


class PredictRequest(EstimateRequest):
    """The three NEET details plus the contact details and tracking tags from Page 2."""
    name: str
    mobile: str
    utm_source: Optional[str] = Field(default=None, max_length=100)
    utm_medium: Optional[str] = Field(default=None, max_length=100)
    utm_campaign: Optional[str] = Field(default=None, max_length=100)
    device: Optional[str] = Field(default=None, max_length=20)
    is_test: bool = False

    @field_validator("name")
    @classmethod
    def check_name(cls, v):
        v = " ".join(v.split())
        if not 2 <= len(v) <= 60:
            raise ValueError("Please enter your name (2-60 characters).")
        return v

    @field_validator("mobile")
    @classmethod
    def check_mobile(cls, v):
        v = v.strip()
        if not re.fullmatch(r"[6-9]\d{9}", v):
            raise ValueError("Enter a valid 10-digit Indian mobile number.")
        return v


class EventRequest(BaseModel):
    """One funnel/analytics event. Only non-identifying fields: never a name or phone number."""
    event: Literal["page_view", "form_start", "score_submit", "form_submit", "result_view",
                   "cta_click", "share_click"]
    utm_source: Optional[str] = Field(default=None, max_length=100)
    utm_medium: Optional[str] = Field(default=None, max_length=100)
    utm_campaign: Optional[str] = Field(default=None, max_length=100)
    device: Optional[str] = Field(default=None, max_length=20)
    score_band: Optional[str] = Field(default=None, max_length=20)
    category: Optional[str] = Field(default=None, max_length=10)
    state: Optional[str] = Field(default=None, max_length=60)
    safe_count: Optional[int] = Field(default=None, ge=0, le=100000)
    moderate_count: Optional[int] = Field(default=None, ge=0, le=100000)
    reach_count: Optional[int] = Field(default=None, ge=0, le=100000)
    cta_type: Optional[str] = Field(default=None, max_length=50)
    is_test: bool = False


def score_band(score):
    """Same buckets as scoreBand() in frontend/src/analytics.js."""
    if score >= 650: return "650+"
    if score >= 600: return "600-649"
    if score >= 550: return "550-599"
    if score >= 500: return "500-549"
    if score >= 400: return "400-499"
    return "below-400"


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/metadata")
def metadata():
    return {
        "categories": [{"code": c, "label": l} for c, l in predictor.CATEGORIES],
        "states": STATES,
        "score_min": 0,
        "score_max": 720,
    }


@app.post("/api/estimate")
def estimate(req: EstimateRequest):
    """Teaser for Page 2: estimated rank and how many colleges fall in each group. No lead is saved and
    no college names are returned. The full list only comes from /api/predict, after a lead is saved."""
    return predictor.summarize(predictor.predict(req.score, req.category, req.home_state))


@app.post("/api/shared-results")
def shared_results(req: EstimateRequest):
    """Full results for a link a student shared from their results page (score + category + state only).
    No lead is saved: the person opening the link has not given any details."""
    return predictor.predict(req.score, req.category, req.home_state)


@app.post("/api/predict")
def predict(req: PredictRequest):
    # 1. Calculate (pure function, cannot fail on valid input)
    result = predictor.predict(req.score, req.category, req.home_state)
    groups = result.get("groups") or {}
    counts = {k: g["total"] for k, g in groups.items()}  # empty when the score has no rank estimate
    # 2. Save the lead FIRST. If this fails, the student gets an error and NO results.
    try:
        repeat = database.save_lead(
            req.name, req.mobile, req.score, req.category, req.home_state, result["estimated_air"],
            req.utm_source, req.utm_medium, req.utm_campaign, req.is_test,
            device=req.device, score_band=score_band(req.score),
            safe_count=counts.get("safe"), moderate_count=counts.get("moderate"), reach_count=counts.get("reach"))
    except Exception:
        raise HTTPException(status_code=500, detail="We could not save your details. Please try again.")
    # 3. Only now return results
    result["repeat_submission"] = repeat
    return result


@app.post("/api/event")
def record_event(req: EventRequest):
    """Funnel tracking. Write-only, like leads: there is no endpoint that reads events back.
    A failure here must never affect the student, so the front end ignores the response."""
    try:
        database.save_event(**req.model_dump())
    except Exception:
        raise HTTPException(status_code=500, detail="Event not saved.")
    return {"ok": True}
