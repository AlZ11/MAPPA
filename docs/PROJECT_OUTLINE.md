# MAPPA 2.0 — project outline

## One-line summary

The 2021 ACSIMA study judged health apps on what developers publish about them. MAPPA 2.0
checks whether that is true, by comparing the privacy policy and Play Store label with
what the app's code and network traffic show, then scores each app against an updated
ACSIMA checklist with the evidence attached.

## Why it's needed (from the original paper)

- Reviewers were asked to compare "disclosures regarding privacy and security practices
  (i.e., privacy policy) to actual app behaviour" (§3.5), but were only allowed published
  sources: the app UI, the store listing, the developer website. Nothing let them see
  data flows.
- 2 apps, 6 reviewers, ~148–164 minutes per app, 61–64% agreement (§4.2).
- Reviewers struggled to separate "not satisfied" (0) from "unknown" (−1) (Table A3).
- The MAPPA section (§6) is empty.
- Australian privacy rules changed: automated-decision transparency (APP 1.7–1.9) and
  the Children's Online Privacy Code both take effect by 10 December 2026.

## Research questions

- **RQ1** How often do Australian health apps' four evidence sources contradict each
  other, and which contradictions are most common?
- **RQ2** Can an automated, evidence-backed assessment match human ACSIMA scoring at a
  fraction of the time?
- **RQ3** How did apps respond to the 10 December 2026 automated-decision disclosure
  obligation? (before/after snapshots)

## Architecture: nine components

```
1 Collector ──► 2 Policy reader ─┐
            ├─► 3 Label reader  ─┤
            ├─► 4 Code scanner  ─┼─► 6 Evidence store ─► 7 Consistency engine ─► 8 ACSIMA 2.0 scorer ─► 9 Reports
            └─► 5 Traffic (stretch)┘
```

| # | Component | Input | Output | Phase |
|---|---|---|---|---|
| 1 | Collector | App list | Dated, hashed raw blobs: store metadata, policy, Data Safety page, APK | Weeks 1–2 |
| 2 | Policy reader | Policy text | Structured claims (data type, action, recipient, purpose, condition) + exact quote | Weeks 2–4 |
| 3 | Label reader | Data Safety page | Structured label facts in the same taxonomy | Weeks 1–2 |
| 4 | Code scanner | APK | Permissions, tracker SDKs, cleartext/network-security config | Weeks 4–6 |
| 5 | Traffic observer | APK on test device | Contacted domains → companies; identifiers in payloads | Weeks 8–10 (stretch) |
| 6 | Evidence store | Outputs of 2–5 | One evidence row per finding, same columns for all sources | Weeks 4–6 |
| 7 | Consistency engine | Evidence rows | Ranked contradiction flags | Weeks 4–6 |
| 8 | ACSIMA 2.0 scorer | Evidence + flags | 0 / 0.5 / 1 / −1 per criterion, with evidence and method | Weeks 6–8 |
| 9 | Reports | Scores | Per-app report card; researcher table + CSV | Weeks 10–12 |

### Component notes

**2 Policy reader.** Split the policy into paragraphs. An LLM fills a fixed schema per
paragraph (allowed values only). Every extracted claim must include a verbatim quote;
code checks the quote exists in the source text (fuzzy match on whitespace only) and
drops claims that fail. Temperature 0; run 3 times on the eval set to measure stability.
Keep a cheap baseline (small open model or the original BERT approach) to report
accuracy vs cost.

**4 Code scanner.** `androguard` for manifest permissions and package names; match
package prefixes against the Exodus Privacy tracker list; MobSF as a cross-check.
Findings are labelled "capability", never "observed behaviour".

**5 Traffic observer.** Emulator or rooted test phone + `mitmproxy` with a system CA;
Frida only where needed to disable certificate pinning. Scripted 5-minute pre-login
session per app. Gate: if fewer than half of the top-50 apps give usable traffic by
week 9, stop and report it as a limitation.

**7 Consistency rules (severity order).**
1. Critical — observed but undeclared: code or traffic shows data going to a party that
   neither the policy nor the label mentions.
2. High — label hides what the policy admits.
3. Medium — the policy contradicts itself.
4. Low — declared but not observed (may just be an untriggered feature).

**8 Scoring methods.** Each criterion is scored by one of: `rule` (from evidence rows),
`llm_with_quote`, or `not_assessable` (−1 with reason). Same scale as the 2021 human
study so scores are directly comparable.

## Shared taxonomy

Use Google Play Data Safety's categories as the common vocabulary for every source.
Verify the list against Google's current help page before coding.

| Category | Data types |
|---|---|
| Location | Approximate location; Precise location |
| Personal info | Name; Email address; User IDs; Address; Phone number; Race and ethnicity; Political or religious beliefs; Sexual orientation; Other info |
| Financial info | User payment info; Purchase history; Credit score; Other financial info |
| Health and fitness | Health info; Fitness info |
| Messages | Emails; SMS or MMS; Other in-app messages |
| Photos and videos | Photos; Videos |
| Audio | Voice or sound recordings; Music files; Other audio files |
| Files and docs | Files and docs |
| Calendar | Calendar events |
| Contacts | Contacts |
| App activity | App interactions; In-app search history; Installed apps; Other user-generated content; Other actions |
| Web browsing | Web browsing history |
| App info and performance | Crash logs; Diagnostics; Other app performance data |
| Device or other IDs | Device or other IDs |

Purposes: App functionality; Analytics; Developer communications; Advertising or
marketing; Fraud prevention, security, and compliance; Personalization; Account
management.

Actions: `collected`, `shared` (plus `sold` for policy claims).

## Evidence row (target shape, built from week 4)

| Field | Meaning |
|---|---|
| snapshot_id, app_id | Which app, which snapshot |
| source | policy / label / code / traffic |
| category, data_type | From the taxonomy above (or `unmapped` + raw string) |
| action | collected / shared / sold / capability / observed |
| recipient | first_party / named company / unspecified third party |
| purpose | From purposes list, may be null |
| evidence_blob, evidence_locator | Raw blob hash + quote / selector / request id |
| method | parser / llm / static / dynamic, with version |
| created_at | UTC |

## ACSIMA 2.0 changes

Keep the 50 original criteria; the consumer list (23) remains the main comparison set.

| Criterion | Change | Check |
|---|---|---|
| C51 Automated decisions disclosed | New | If the app has AI features that score, triage or recommend, the policy names the kinds of personal info used and decisions made (APP 1.7–1.9) |
| C52 AI provider named | New | If content goes to an external AI provider, the policy names it and says whether data trains models |
| C53 Label matches policy | New | Consistency rule 2 |
| C54 No undeclared data flows | New | Consistency rule 1 |
| C55 No data sent before consent | New | Traffic, pre-login capture |
| C49 Protects minor users | Split into checkable parts | Against the Children's Online Privacy Code (lock wording once registered) |
| C32 Encryption | Made testable | Label "encrypted in transit" + no cleartext allowed in network config |
| C47 Plain language | Made measurable | Readability score + LLM judgement with quotes |
| C9 Data declared | Made granular | Per data category, not yes/no |

## Evaluation

| Question | Method | Metric |
|---|---|---|
| Policy reader accuracy | Two annotators label ~30 policies | Precision / recall / F1 per category; Cohen's kappa between annotators |
| Are contradiction flags real? | Hand-check ~100 random flags | Share correct, per severity |
| Matches human scoring? | 3 people score ~10 apps (incl. Calm, MyFitnessPal) | Krippendorff's alpha: system vs humans compared with humans vs humans |
| Faster / cheaper? | Time + API cost per app | vs ~148–164 min per app (2021) |
| Value of each layer | Findings with policy only, + label, + code, + traffic | Extra confirmed findings per layer |
| Market picture | Run on the full sample | % with undeclared flows; top trackers; % of AI-feature apps without ADM disclosure |

Human-subject parts need CSIRO ethics approval before they start.

## Timeline (assumes start Mon 5 Oct 2026, ~12 weeks)

| Weeks | Dates | Work | Gate |
|---|---|---|---|
| 0 | this week | Scope agreed in writing; CSIRO OK for APK/traffic analysis; ethics application started; AndroZoo key requested | — |
| 1–2 | 5–18 Oct | Collector + label reader | Snapshot 1 of ~1,000 apps frozen and backed up |
| 2–4 | 12 Oct–1 Nov | Policy reader; hand-label 30 policies | F1 measured |
| 4–6 | 26 Oct–15 Nov | Code scanner, evidence store, consistency engine | 100 flags hand-checked |
| 6–8 | 9–29 Nov | ACSIMA 2.0 scorer; human comparison study | Agreement + time numbers |
| 8–10 | 23 Nov–13 Dec | Traffic observer, top 50 apps | Week-9 go / no-go |
| 10–12 | 7–27 Dec | Scale run, thin UI, write-up | MAPPA section + extension paper draft |
| — | Dec / Jan | Snapshot 2 after 10 Dec (date agreed with supervisor) | Before/after comparison |

Cut order if behind: UI → traffic observer → sample size (down to ~300).
Never cut the evaluation.

## Out of scope

iOS apps; logged-in app behaviour; paid features; legal conclusions about compliance
(the tool reports evidence and inconsistencies, not legal findings).
