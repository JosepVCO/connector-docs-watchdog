<html><body><pre># Example Ads API changelog (SYNTHETIC demo data)

## v3.3 — released October 15, 2026

### Reporting

The `legacy_report` endpoint is deprecated and will be removed on 2027-03-31. Use `campaign_stats` instead.
The `campaign_stats` endpoint now returns a new optional `video_views` metric.
Documentation navigation was reorganized again to improve readability.

## v3.2 — released January 10, 2026

### Reporting

The `campaign_stats` endpoint now accepts a `breakdown` parameter for daily aggregation.
Documentation navigation was reorganized to make endpoints easier to find.

### Authentication

OAuth access tokens issued after this release are valid for 60 days.
</pre></body></html>
