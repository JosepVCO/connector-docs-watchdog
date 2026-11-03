# Connector Docs Watchdog report — 2026-11-03

**Data origin: DEMO** — bundled sample files, no network access. All sample content is synthetic and illustrative. This is not a monitoring result for any real vendor.

> **DEMONSTRATION MODE — not AI output.** Findings come from the deterministic
> rule engine (`deterministic-rules`) over bundled sample data, to show the
> workflow offline without credentials.

**Run status: COMPLETE.**

_Mode: deterministic (no AI)_
_Sources: 2 of 2 checked and fully processed_

## Source provenance
| Source | Outcome | URL | HTTP | Fetched at (UTC) | Extracted | Fingerprint |
|---|---|---|---|---|---|---|
| Sample Platform A (synthetic) / `changelog` | changed | demo_data/stage2/platform-a-changelog.md (bundled file) | – | 2026-11-03T16:25:28+00:00 | 964 chars | `adc80b8f7625` |
| Example Ads API (synthetic) / `changelog` | changed | demo_data/stage2/example-changelog.md (bundled file) | – | 2026-11-03T16:25:28+00:00 | 659 chars | `f2ed15308d3c` |

_The fingerprint is the SHA-256 of the extracted text (first 12 hex digits), so two runs can be compared without storing page content._

## Run summary
- Sample Platform A (synthetic)/changelog: changed, 1 finding(s)
- Example Ads API (synthetic)/changelog: changed, 1 finding(s)

## Deprecations (2)
_Features announced or flagged for retirement._

### [HIGH] Example Ads API (synthetic) — deprecation: Reporting
- **Provider / product:** Example Ads API (synthetic) · Reporting
- **Detected:** 2026-11-03
- **Status:** Confirmed by source text · confidence 60% · classifier `deterministic-rules`
- **Deadline:** 2027-03-31 — in 148 days
- **Description:** Deprecation language ('deprecated') in newly added documentation text.
- **Potential impact:** Keyword-based detection. Not determined: the evidence shows the change but not which integrations use it. Check the affected area against your connector's usage.
- **Recommended action:** Review the vendor notice and check whether any connector uses the affected feature.
- **Source:** https://demo.invalid/example-changelog.md (`changelog`)
- **New state:**
  > ### Reporting
  > The `legacy_report` endpoint is deprecated and will be removed on 2027-03-31. Use `campaign_stats` instead.
  > The `campaign_stats` endpoint now returns a new optional `video_views` metric.
  > Documentation navigation was reorganized again to improve readability.
- **Evidence:**
  > The `legacy_report` endpoint is deprecated and will be removed on 2027-03-31. Use `campaign_stats` instead.

### [HIGH] Sample Platform A (synthetic) — deprecation: Legacy audience lists deprecation
- **Provider / product:** Sample Platform A (synthetic) · Legacy audience lists deprecation
- **Detected:** 2026-11-03
- **Status:** Confirmed by source text · confidence 60% · classifier `deterministic-rules`
- **Deadline:** 2027-09-30 — in 331 days
- **Description:** Deprecation language ('deprecation') in newly added documentation text.
- **Potential impact:** Keyword-based detection. Not determined: the evidence shows the change but not which integrations use it. Check the affected area against your connector's usage.
- **Recommended action:** Review the vendor notice and check whether any connector uses the affected feature.
- **Source:** https://demo.invalid/platform-a-changelog.md (`changelog`)
- **New state:**
  > #### Legacy audience lists deprecation
  > *Applies to version 4.0 and later. The feature is switched off for every version on September 30, 2027.*
  > Legacy audience lists can no longer be created or edited. Build audiences with the new audience builder instead; the migration guide lists the steps.
  > The following endpoints are affected:
  > * `POST /{account-id}/audience_lists`
  > * `PUT /{list-id}`
- **Evidence:**
  > #### Legacy audience lists deprecation
  > *Applies to version 4.0 and later. The feature is switched off for every version on September 30, 2027.*

---
_**How to read severity and confidence.** They come from fixed keyword rules (for example: a stated date raises severity; confidence is a constant per rule), not from an analysis of your integration. They say how explicit the vendor wording is, not how much a particular integration is affected._

_Evidence quotes show that the wording exists in the retrieved change. They do not prove that the classification or the stated impact is correct; confirm against the source before acting._

