"""Minimal-edit helpers for `ai-review-rubric-v2-train` (training queues only).

The AI reviewer decides every edit per candidate after reading its stored
input and output. `Editor.outreach` applies those edits to the ORIGINAL
email: explicit (old -> new) replacements and whole-sentence cuts, plus a
few mechanical fixes that never change meaning (literal '\\n' escape text,
placeholder-only signature lines, 'My name is from GTMFlow' / 'My name is
[Your Name]' openers). It then inserts a demonstration label sentence before
the closing, unless the reviewer rewrote an existing label sentence instead.
Everything else in the original is kept verbatim. The review build re-checks
every target (v2 validator + rubric lints) and records how much of the
original it keeps (preservation_rouge_l).
"""
from __future__ import annotations

import copy
import re

from heldout_helpers import NEUTRAL_RELEVANCE, PERSON_NAME, Reviewer  # noqa: F401

RUBRIC_V2 = "ai-review-rubric-v2-train"
LABELS = [
    "Please note: GTMFlow is a portfolio demonstration, and this email is not a commercial offer.",
    "GTMFlow is a portfolio demonstration project, so this message is not a commercial offer.",
    "To be clear, this is a demonstration message from a portfolio project and not a commercial offer.",
    "This email is part of a portfolio demonstration and is not a commercial offer.",
]
CLOSINGS = ("Best regards", "Best,", "Warm regards", "Kind regards", "Regards", "Thanks", "Thank you",
            "Sincerely", "Looking forward", "Cheers")
MECHANICAL = [
    ("My name is [Your Name], and I represent GTMFlow.", "I'm writing on behalf of GTMFlow.", "placeholder_signature"),
    ("My name is [Your Name] from GTMFlow.", "I'm writing from GTMFlow.", "placeholder_signature"),
    ("My name is [Your Name] from GTMFlow,", "I'm writing from GTMFlow,", "placeholder_signature"),
    ("My name is [Your Name], and I am reaching out to you from GTMFlow.", "I'm reaching out from GTMFlow.", "placeholder_signature"),
    ("My name is from GTMFlow, and ", "I'm writing from GTMFlow, and ", "broken_signature"),
    ("My name is from GTMFlow.", "I'm writing from GTMFlow.", "broken_signature"),
]
_PLACEHOLDER_LINE = re.compile(r"^\s*\[[^\]]*\]\s*$")


def _sentences_cut(text: str, needle: str) -> str:
    """Remove every sentence (within a line) that contains `needle`."""
    out_lines, hit = [], False
    for line in text.split("\n"):
        parts = re.split(r"(?<=[.!?])\s+", line)
        kept = [s for s in parts if needle not in s]
        hit |= len(kept) != len(parts)
        out_lines.append(" ".join(kept))
    if not hit:
        raise ValueError(f"cut: {needle!r} not found")
    return "\n".join(out_lines)


def _tidy(text: str) -> str:
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r" {2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


class Editor(Reviewer):
    def __init__(self, queue: str = "train-v2"):
        super().__init__(queue, rubric=RUBRIC_V2)

    def outreach(self, pos, *, reason, issues, replace=(), cut=(), label=None, relabel=None, subject=None,
                 call_note=None, caps=None, facts=None, support="partially_supported", writing=3,
                 missing="acceptable", uncertain=None):
        """replace: [(old, new)] applied to the body (old must occur).
        cut: substrings whose whole sentence is deleted.
        label: index into LABELS or a string, inserted before the closing.
        relabel: (old, new) -- rewrite an existing partial label sentence
        instead of inserting one."""
        orig = self.contents[pos]
        content = copy.deepcopy(orig)
        body = orig["email_body"]
        issues = list(issues)
        if "\\n" in body:
            body = body.replace("\\n", "\n")
            issues.append("literal_escape_sequences")
        for old, new, issue in MECHANICAL:
            if old in body:
                body = body.replace(old, new)
                issues.append(issue)
        for old, new in replace:
            if old not in body:
                raise ValueError(f"#{pos}: replace target not found: {old!r}")
            body = body.replace(old, new)
        for needle in cut:
            body = _sentences_cut(body, needle)
        lines = body.split("\n")
        if any(_PLACEHOLDER_LINE.match(l) for l in lines):
            lines = [l for l in lines if not _PLACEHOLDER_LINE.match(l)]
            issues.append("placeholder_signature")
        body = "\n".join(lines)
        if relabel:
            if relabel[0] not in body:
                raise ValueError(f"#{pos}: relabel target not found")
            body = body.replace(*relabel)
        else:
            text = LABELS[label] if isinstance(label, int) else label
            if text is None:
                raise ValueError(f"#{pos}: outreach needs a label or relabel")
            body = _insert_before_closing(body, text)
        body = _tidy(body)
        last = body.rstrip().split("\n")[-1].strip().rstrip(",").lower()
        if body.rstrip().endswith(",") or last in [c.rstrip(",").lower() for c in CLOSINGS]:
            body = body.rstrip() + "\nGTMFlow (demonstration)"
        content["email_body"] = body
        if subject is not None:
            content["subject"] = subject
        if call_note is not None:
            content["call_note"] = call_note
        if caps is not None:
            content["capabilities_used"] = caps
        if facts is not None:
            content["lead_facts_used"] = facts
        fields = {k: v for k, v in content.items() if v != orig.get(k)}
        return self.correct(pos, fields, reason, sorted(set(issues)), support=support, writing=writing,
                            missing=missing, uncertain=uncertain)

    def preview(self, positions=None):
        for r in self.reviews:
            if positions and r["position"] not in positions:
                continue
            f = r.get("corrected_fields", {})
            if "email_body" in f:
                print(f"--- #{r['position']} {f.get('subject', '')}\n{f['email_body']}")


def _insert_before_closing(body: str, label: str) -> str:
    """Insert before the closing block: the earliest closing line after which
    only closings and short signature lines follow."""
    lines = body.split("\n")
    best = None
    for i in range(len(lines) - 1, -1, -1):
        line = lines[i].strip()
        if line.lower().startswith(tuple(c.lower() for c in CLOSINGS)):
            best = i
        elif line and len(line) > 40:
            break
    if best is not None:
        return "\n".join(lines[:best]).rstrip() + "\n\n" + label + "\n\n" + "\n".join(lines[best:])
    return body.rstrip() + "\n\n" + label
