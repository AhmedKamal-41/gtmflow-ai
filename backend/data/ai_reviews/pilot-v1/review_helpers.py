"""Formatting helpers for AI-review decision files (not a reviewer).

Each decision is made per candidate by the AI reviewer; these helpers only
format a corrected demonstration outreach the same way the human
corrections of #4 and #6 did, and write batch files pinned to the exact
output id and content hash that was reviewed.
"""
import json
from datetime import datetime, timezone

CAP_TEXT = {
    "cap-3": "drafts outreach from supplied company facts",
    "cap-4": "a person approves, rejects or corrects each draft",
}
ALL_UNKNOWN_KEYS = None  # unknowns_acknowledged is kept from the original output


def outreach(name, descriptor, facts_used, place=None, subject=None):
    """A corrected demonstration outreach: restates only record facts,
    labels the demonstration, invents no needs or contacts."""
    where = f" in {place}" if place else ""
    body = (
        "Hi there,\n\n"
        f"The supplied company record lists {name} as {descriptor}{where}.\n\n"
        "GTMFlow is a portfolio demonstration that drafts outreach from supplied company facts "
        "for a person to review before any use. This message illustrates that workflow and is "
        "not a commercial offer.\n\n"
        "Would you be interested in seeing how the workflow prepares a draft for human review?\n\n"
        "Best regards,\nGTMFlow (demonstration)"
    )
    return {
        "subject": subject or f"GTMFlow workflow demonstration for {name}",
        "email_body": body,
        "lead_facts_used": facts_used,
        "capabilities_used": ["cap-3", "cap-4"],
        "claims_used": [],
        "call_note": (
            "Demonstration draft only; not a commercial offer. It restates the record's company facts. "
            "Buying intent, budget, current problems and tools are unknown, and no contact person is on file."
        ),
        "confidence": "medium",
    }


def write_batch(path, reviews):
    stamp = datetime.now(timezone.utc).isoformat()
    for r in reviews:
        r.setdefault("reviewed_at", stamp)
    with open(path, "w") as f:
        json.dump({"reviewer": "ai:claude-opus-5-5", "rubric": "ai-review-rubric-v1", "reviews": reviews}, f, indent=1)
    print(f"wrote {path}: {len(reviews)} reviews")
