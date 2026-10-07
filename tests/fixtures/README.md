# Real pages for parser tests

Parsers are validated only against real pages saved here by hand. Synthetic pages
(`tests/synthetic/`) test the plumbing, not the parsers. Until these files exist, the
Google Play and Data Safety parsers are **unvalidated**.

## How to save a page

Google Play pages carry their data in script blocks in the page source, so save the
**source** as served:

1. Open `view-source:<url>` in Chrome (put `view-source:` in front of the address).
2. Save with Ctrl+S (Cmd+S on a Mac) as "Webpage, HTML only".

Then save the same page a second time as rendered (Ctrl+S, "Webpage, Complete"; keep
only the `.html` file), with `.rendered.html` in the name. Fetching uses the rendered
version by default, and the pair shows whether the label data survives rendering
(decides `datasafety_fetcher`).

Add one line per file to `SOURCES.md` here: URL, date saved (UTC), and what case it is.

## Pages needed

| File | URL | Case |
|---|---|---|
| `google_play/search_meditation.html` | `https://play.google.com/store/search?q=meditation&c=apps&hl=en&gl=AU` | search results; also shows the real number of results per search |
| `google_play/details_com.calm.android.html` | `https://play.google.com/store/apps/details?id=com.calm.android&hl=en&gl=AU` | listing; verifies the seed package name |
| `google_play/details_com.myfitnesspal.android.html` | `https://play.google.com/store/apps/details?id=com.myfitnesspal.android&hl=en&gl=AU` | listing; verifies the seed package name |
| `datasafety/no_data_collected.html` (+ `.rendered.html`) | an app showing "No data collected" | headline |
| `datasafety/no_data_shared.html` (+ `.rendered.html`) | an app showing "No data shared with third parties" | headline |
| `datasafety/many_categories.html` (+ `.rendered.html`) | a large app (e.g. MyFitnessPal) | many categories and purposes |
| `datasafety/optional.html` (+ `.rendered.html`) | an app with an "Optional" data type | optional flag |
| `datasafety/not_provided.html` (+ `.rendered.html`) | an app whose developer gave no information | `not_provided` |
| `datasafety/all_practices.html` (+ `.rendered.html`) | an app listing all security practices | practices |
| `datasafety/removed_app.html` | a removed or nonexistent app ID | 404 |
| `datasafety/odd_layout.html` (+ `.rendered.html`) | anything that looks different | layout variation |

For each Data Safety page, also write down what the page says (data types per section,
optional markers, purposes, practices) in `datasafety/<name>.expected.md`. The fixture
tests compare the parser's output with that note.
