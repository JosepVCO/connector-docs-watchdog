> # CONTROLLED SIMULATION — NOT A NEW VENDOR ANNOUNCEMENT.
>
> This report is produced offline from the synthetic Meta Ads and LinkedIn Marketing fixtures. The stored
> baseline is a copy of each fixture with one section removed (the deprecation notice), so the
> deterministic engine rediscovers it. The text and the deadlines are invented for illustration; they are
> not vendor statements. It shows what a detected deprecation looks like, not a change observed over time.
> The unmodified live result is in [`live-report-2026-11-03.md`](live-report-2026-11-03.md).
> Everything below the line is the unedited program output, apart from the data-origin line.

---

# Connector Docs Watchdog report — 2026-11-03

**Data origin: SIMULATION** — synthetic pages from `tests/fixtures/synthetic/`, no network access. The text is invented; it follows the structure of the real vendor pages but is not their content.

**Run status: COMPLETE.**

_Mode: deterministic (no AI)_
_Sources: 2 of 2 checked and fully processed_

## Source provenance
| Source | Outcome | URL | HTTP | Fetched at (UTC) | Extracted | Fingerprint |
|---|---|---|---|---|---|---|
| Meta Ads / `versioned-changes-v25` | changed | tests/fixtures/synthetic/meta-ads__versioned-changes-v25.html (bundled file) | – | 2026-11-03T16:24:53+00:00 | 1,019 chars | `9a295afaa4c1` |
| LinkedIn Marketing / `recent-changes` | changed | tests/fixtures/synthetic/linkedin-marketing__recent-changes.html (bundled file) | – | 2026-11-03T16:24:53+00:00 | 2,038 chars | `29bdccfad6b3` |

_The fingerprint is the SHA-256 of the extracted text (first 12 hex digits), so two runs can be compared without storing page content._

## Run summary
- Meta Ads/versioned-changes-v25: changed, 1 finding(s)
- LinkedIn Marketing/recent-changes: changed, 1 finding(s)

## Deprecations (2)
_Features announced or flagged for retirement._

### [HIGH] Meta Ads — deprecation: Bundle Campaigns and Bundle App Campaigns deprecation
- **Provider / product:** Meta Ads · Bundle Campaigns and Bundle App Campaigns deprecation
- **Detected:** 2026-11-03
- **Status:** Confirmed by source text · confidence 60% · classifier `deterministic-rules`
- **Deadline:** 2027-06-30 — in 239 days
- **Description:** Deprecation language ('deprecation') in newly added documentation text.
- **Potential impact:** Keyword-based detection. Not determined: the evidence shows the change but not which integrations use it. Check the affected area against your connector's usage.
- **Recommended action:** Review the vendor notice and check whether any connector uses the affected feature.
- **Source:** https://developers.facebook.com/documentation/ads-commerce/marketing-api/marketing-api-changelog/version25.0.md (`versioned-changes-v25`)
- **New state:**
  > #### Bundle Campaigns and Bundle App Campaigns deprecation
  > *Effective now for v25.0 and, from June 30, 2027, for every earlier version.*
  > Bundle campaigns can no longer be created, copied or edited through the API ([details](https://example.invalid/docs/bundle-campaigns)).
  > Existing bundle campaigns need to be rebuilt; the [migration guide](https://example.invalid/docs/migrate) lists the steps.
  > Affected endpoints:
  > * [`POST /{ad-account-id}/campaigns`](https://example.invalid/ref/campaigns)
  > * [`POST /{campaign-id}/copies`](https://example.invalid/ref/copies)
- **Evidence:**
  > #### Bundle Campaigns and Bundle App Campaigns deprecation
  > *Effective now for v25.0 and, from June 30, 2027, for every earlier version.*

### [HIGH] LinkedIn Marketing — deprecation: **Deprecation Notice:** The Platform Version 202701 (January 2027 release) will 
- **Provider / product:** LinkedIn Marketing · **Deprecation Notice:** The Platform Version 202701 (January 2027 release) will 
- **Detected:** 2026-11-03
- **Status:** Confirmed by source text · confidence 60% · classifier `deterministic-rules`
- **Deadline:** 2027-12-15 — in 407 days
- **Description:** Deprecation language ('deprecation') in newly added documentation text.
- **Potential impact:** Keyword-based detection. Not determined: the evidence shows the change but not which integrations use it. Check the affected area against your connector's usage.
- **Recommended action:** Review the vendor notice and check whether any connector uses the affected feature.
- **Source:** https://learn.microsoft.com/en-us/linkedin/marketing/integrations/recent-changes (`recent-changes`)
- **New state:**
  > Warning
  > **Deprecation Notice:** The Platform Version 202701 (January 2027 release) will be sunset on December 15, 2027. Move to a newer API version before then so your integration keeps working. The migration guide lists the supported versions.
  > Questions about the migration can be sent to developer support.
- **Evidence:**
  > **Deprecation Notice:** The Platform Version 202701 (January 2027 release) will be sunset on December 15, 2027. Move to a newer API version before then so your integration keeps working. The migration guide lists the supported versions.

---
_**How to read severity and confidence.** They come from fixed keyword rules (for example: a stated date raises severity; confidence is a constant per rule), not from an analysis of your integration. They say how explicit the vendor wording is, not how much a particular integration is affected._

_Evidence quotes show that the wording exists in the retrieved change. They do not prove that the classification or the stated impact is correct; confirm against the source before acting._

