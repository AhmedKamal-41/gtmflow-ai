# Sample data

Demo lead lists for testing CSV upload, validation, deterministic lead scoring, AI summary/outreach generation, and Slack webhook pushes, every subsystem the backend exposes. Companies and contacts are **fictional**; this is synthetic demo data, not real prospects.

## Files

- `leads_sample.csv`, 10 leads (housing, healthcare, SaaS, retail, education) deliberately tuned so the deterministic scorer returns **2 Hot, 4 Warm, 4 Cold**. This same file is the one the README's curl example and the `/upload` page demo use.

### Hot / Warm / Cold tuning

The 100-point scorer combines signals from industry, title, company size, source, data completeness, and pain-point keywords (see [`../docs/scoring-model.md`](../docs/scoring-model.md)). The CSV is shaped so each band has clean representatives:

- **Hot (≥ 80):** Cascade Modular Homes (Housing + VP Operations + referral + tenant/maintenance pain), Northbridge Clinics (Healthcare + Practice Manager + webinar + patient/intake pain)
- **Warm (55–79):** Meridian Diagnostics, Bluepine Property Partners, Loopline Software, Quanta Analytics
- **Cold (0–54):** Vault Outfitters, Sunday Pantry Co, Brightline Academy Network, Latitude Learning

Numbers are not load-bearing in tests, the unit tests build their own CSVs, but they're the right shape for a 30-second demo: "2 Hot leads jump out of 10."

## Columns

`company_name` is required; everything else is optional. The first nine columns map to `Lead` fields directly; anything else (here: `notes`, `pain_points`) is preserved in the `cleaned_data` JSON column and fed back into the scoring engine for pain-point keyword matching.

| Column | Notes |
|--------|-------|
| `company_name` | Required. Company name. |
| `website` | Company domain or URL. |
| `industry` | One of: Housing, Healthcare, SaaS, Retail, Education. |
| `contact_name` | Full name of the primary contact. |
| `contact_email` | Contact email. |
| `contact_title` | Role / seniority signal. |
| `company_size` | Headcount signal (kept as string). |
| `location` | ISO-ish country or region label. |
| `source` | How the lead was acquired (e.g., `outbound`, `inbound`, `referral`, `webinar`, `event`). |
| `notes` | Free-text rationale, the scorer searches this for pain-point keywords. |
| `pain_points` | Comma-separated pain themes, also searched for keyword matches. |

## Why these extra columns matter

`notes` and `pain_points` are intentionally not part of the `Lead` schema. They live in `cleaned_data` to demonstrate two things at once:

1. **CSV ingestion preserves unknown columns**, `services/csv_ingestion.py` keeps them on each lead.
2. **The scorer can read `cleaned_data`**, pain-point keyword matching walks every text value in `cleaned_data` along with the standard lead fields, so free-text rationale from a sales op shows up in the score breakdown.

Real lead lists from sales ops rarely come pre-shaped; this is how GTMFlow AI keeps signal that would otherwise be lost.
