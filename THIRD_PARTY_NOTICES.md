# Third-party notices

The MIT License in [`LICENSE`](LICENSE) covers the original code and documentation written for this project.
It does **not** cover the material below, which belongs to other parties and stays under their terms. This
file records what the repository contains from third parties, what the pages said about their rights when I
checked (2026-11-03), and how each item is handled. It is a record of what I found, not legal advice.

## Vendor documentation

| Vendor | What is in this repository | What I found | Treatment |
|---|---|---|---|
| **Google** (Google Ads API) | `tests/fixtures/excerpts/google-ads__release-notes.html` and `google-ads__sunset-dates.html`: excerpts of the pages at <https://developers.google.com/google-ads/api/docs/release-notes> and `.../sunset-dates` | Each page states: "Except as otherwise noted, the content of this page is licensed under the Creative Commons Attribution 4.0 License, and code samples are licensed under the Apache 2.0 License." | Real text, reduced to the sections the tests need. Content is used under **CC BY 4.0** (<https://creativecommons.org/licenses/by/4.0/>). Attribution, source URL, capture date, the SHA-256 of the original response and a statement of the changes are in each file's header and in `tests/fixtures/MANIFEST.json`. No code samples are included, so Apache 2.0 material is not reproduced. Not endorsed by Google. |
| **Microsoft / LinkedIn** (LinkedIn Marketing API documentation on Microsoft Learn) | `tests/fixtures/synthetic/linkedin-marketing__recent-changes.html`: **invented** text on the layout of the real page. The only vendor wording in it is two short interface sentences ("Access to this page requires authorization. ...") kept so the `ignore_patterns` rule can be tested. | The Microsoft Learn terms of use (<https://learn.microsoft.com/en-us/legal/termsofuse>) restrict copying, reproducing and redistributing content from the services without written consent, except for personal non-commercial use. The page names a source repository (`MicrosoftDocs/linkedin-api-docs`) that was not publicly reachable. I found no open license for the documentation text. | Rights unclear, so the fixture is synthetic. |
| **Meta** (Marketing API documentation) | `tests/fixtures/synthetic/meta-ads__*.html`, `tests/fixtures/meta_v25_changelog.md.html`: **invented** text in the Markdown-in-HTML wrapper structure Meta's pages use. No Meta documentation text. | Meta's Platform Terms (<https://developers.facebook.com/terms/>) treat the documentation as "Developer Docs" with requirements attached. The captured pages contain no license text. I found no grant to redistribute the documentation. | Rights unclear, so the fixtures are synthetic. |

The demonstration data in `src/docs_watchdog/demo_data/` is also entirely synthetic and does not reproduce any
vendor's wording.

## Quotations in example reports

`docs/examples/simulated-detection-2026-11-03.md` is program output over the synthetic Meta and LinkedIn
fixtures, so every quoted passage is invented text. `docs/examples/live-report-2026-11-03.md` contains URLs,
status codes and fingerprints only, with no quoted vendor text.

## Trademarks

Meta, Facebook, Google, Google Ads, LinkedIn, Microsoft and Pinterest are trademarks of their respective
owners. This project is independent and is not affiliated with, sponsored by or endorsed by any of them. The
names are used only to identify the documentation being monitored.

## Software dependencies

Dependencies are installed separately from PyPI (see `pyproject.toml` and `constraints.txt`). They are not
copied into this repository and keep their own licenses; check each package's metadata for the terms.
