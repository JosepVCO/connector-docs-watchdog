<html><body><pre># Sample Platform A changelog (SYNTHETIC demo data: illustrative text, not a vendor page)

## Version 4.0

Released March 3, 2027.

### Audiences

#### Legacy audience lists deprecation

*Applies to version 4.0 and later. The feature is switched off for every version on September 30, 2027.*

Legacy audience lists can no longer be created or edited. Build audiences with the new audience builder instead; the migration guide lists the steps.

The following endpoints are affected:

* `POST /{account-id}/audience_lists`
* `PUT /{list-id}`

### Reporting

#### Daily breakdown

The reporting endpoints accept a `breakdown=day` parameter that returns one row per day.

### Campaigns

#### Budget field is optional for lifetime budgets

Requests that set a lifetime budget no longer need to send `daily_budget`.

## Available versions

| Version | Date Introduced | Available Until |
| --- | --- | --- |
| 4.0 | March 3, 2027 | TBD |
| 3.0 | September 8, 2026 | September 15, 2027 |
</pre></body></html>
