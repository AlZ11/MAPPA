# APP-350: what is actually in the data (Task 02a, R0)

Source: `APP-350_v1.1.zip` from usableprivacy.org, placed by hand in `data-public/raw/`
(SHA-256 `22e767a199c716c4ef741c91944641847abc9e06fb36d2f7048f23239ea100b5`). Everything
below comes from inspecting the zip, apart from section 6, which needs the paper. No
APP-350 text is reproduced here; the example in section 2 is made up.

> **Dataset:** APP-350 Corpus v1.1. **Cite:** S. Zimmeck, P. Story, D. Smullen,
> A. Ravichander, Z. Wang, J. Reidenberg, N. C. Russell, N. Sadeh. *MAPS: Scaling Privacy
> Compliance Analysis to a Million Apps.* PoPETs 2019. Licensed for research, teaching
> and scholarship only, in the spirit of CC BY-NC.

## 1. Files and layout

| Path in the zip | What it is |
|---|---|
| `APP-350_v1.1/annotations/policy_<id>.yml` | 350 YAML files, one per policy: the labelled text. **This is what we load.** |
| `APP-350_v1.1/original_documents/<id>.html` | 350 original HTML policies. Not needed: the YAML already holds the segmented text. |
| `APP-350_v1.1/README.md`, `LICENSE` | Format description, label definitions, licence, citation. |
| `APP-350_v1.1/documentation/` | Annotator instructions, inter-annotator agreement, how the data was made. |
| `APP-350_v1.1/features.yml` | The authors' hand-made keyword lists per practice, built from train + validation only (cite Story et al., PAL 2019). |
| `__MACOSX/`, `.DS_Store` | macOS archive junk; ignored. |

- 350 policies; IDs 1-350, unique.
- 15,507 segments ("loosely paragraphs"): 3 to 608 per policy, median 33.
  4,085 segments are labelled; the rest are, per the README, assumed to neither
  affirm nor deny any practice (usable as negatives).
- Segment length: about 46-50 words median, about 60 words mean; the longest is 2,805 words.
- Total text: about 3.0M characters (train), 1.0M (validation), 1.9M (test).
- 6 segments contain HTML-like `<...>` fragments; kept as text.

## 2. How segments and labels are stored

Top-level keys of every file: `policy_id`, `policy_name`, `policy_type`,
`contains_synthetic`, `segments`. Each segment has `segment_id`, `segment_text`,
`annotations` (a list of `{practice, modality}`) and `sentences`. For labelled segments,
`sentences` lists the segment's sentences, each with its own annotations. In every
labelled segment, the segment's labels equal the union of its sentences' labels (checked
on all 4,085).

Made-up example in the same shape:

```yaml
policy_id: 9999
policy_name: EXAMPLE
policy_type: TRAINING
contains_synthetic: false
segments:
- segment_id: 0
  segment_text: Welcome to our app.
  annotations: []
  sentences: []
- segment_id: 1
  segment_text: We collect your email address when you sign up. We do not share your
    location with advertisers.
  annotations:
  - modality: PERFORMED
    practice: Contact_E_Mail_Address_1stParty
  - modality: NOT_PERFORMED
    practice: Location_3rdParty
  sentences:
  - sentence_text: We collect your email address when you sign up.
    annotations:
    - modality: PERFORMED
      practice: Contact_E_Mail_Address_1stParty
  - sentence_text: We do not share your location with advertisers.
    annotations:
    - modality: NOT_PERFORMED
      practice: Location_3rdParty
```

## 3. Practice labels and modality

58 practice labels: 28 data types × 2 parties (`_1stParty`, `_3rdParty`), plus `SSO` and
`Facebook_SSO`, which have no party suffix. The README explains that for those two, data
always flows between the app developer and the sign-on provider.

Data types: `Contact`, `Contact_Address_Book`, `Contact_City`, `Contact_E_Mail_Address`,
`Contact_Password`, `Contact_Phone_Number`, `Contact_Postal_Address`, `Contact_ZIP`,
`Demographic`, `Demographic_Age`, `Demographic_Gender`, `Identifier`, `Identifier_Ad_ID`,
`Identifier_Cookie_or_similar_Tech`, `Identifier_Device_ID`, `Identifier_IMEI`,
`Identifier_IMSI`, `Identifier_IP_Address`, `Identifier_MAC`, `Identifier_Mobile_Carrier`,
`Identifier_SIM_Serial`, `Identifier_SSID_BSSID`, `Location`, `Location_Bluetooth`,
`Location_Cell_Tower`, `Location_GPS`, `Location_IP_Address`, `Location_WiFi`
(+ `SSO`, `Facebook_SSO`).

- **Party:** first party = the app's developer/publisher; third party = ad networks,
  analytics services and similar (not other users).
- **Modality:** `PERFORMED` (the practice is explicitly described as happening) or
  `NOT_PERFORMED` (explicitly described as not happening). There is no third value;
  "not mentioned" is the absence of any label.
- All practices are about *collection* from the user. Broad types (`Location`,
  `Identifier`, ...) are only used for explicit general statements, not as catch-alls.
- Counts: 10,201 segment labels, of which 8,191 performed and 2,010 not performed.
  2 segments label the same practice with both modalities; no segment repeats a label.

## 4. The split: official, included

**The official split is in the data**, as `policy_type` in every file: `TRAINING` 188,
`VALIDATION` 62, `TEST` 100. It matches the paper's 188 / 62 / 100, so no seeded split is
needed and results can use the paper's test set.

## 5. Synthetic data in train and validation (important)

The documentation says the authors created **synthetic negative examples**: they copied
policies and rewrote statements from positive to negative ("we allow third parties to
collect your location" became "we do not allow ..."), and added these to train and
validation only. Each file says so in `contains_synthetic`:

| Split | Policies | contains_synthetic = true | Practices with both "performed" and "not performed" (mean per policy) |
|---|---:|---:|---:|
| train | 188 | 106 | 7.4 (synthetic) / 0.3 (real) |
| validation | 62 | 36 | 8.8 (synthetic) / 0.2 (real) |
| test | 100 | 0 | 0.1 |

Consequences:
- Policy-level `conflict` answers in train/validation are mostly an artefact of the
  rewriting; in real policies (and in test) they are rare (13 in all of test).
- `no` answers are much rarer in test (65 across 20 practices) than in train/validation.
- Tuning on validation means tuning partly on synthetic text. The original authors did
  the same; validation results are reported both on all 62 policies and on the 26 real ones.

## 6. What the paper's published numbers measure (needs checking against the PDF)

The paper's hosts are blocked from the development environment, so this section relies
on the task brief and the dataset docs. **Alex: please verify against the PDF.**

- **Metrics:** per the brief, the paper's main table reports negative predictive value
  (NPV), specificity and negative F1, i.e. how well a classifier finds policies that do
  *not* describe a practice. Ordinary F1 must not be compared with those numbers.
- **Level:** the dataset's agreement figures are computed per policy ("whether the same
  set of annotations appears anywhere in the policy"), and the paper's use case is
  per-app compliance, so the published results are very likely per policy. To confirm.
- **Open questions for the PDF:** (a) whether "negative" means "not mentioned" only, or
  also includes `NOT_PERFORMED`; (b) whether results cover all 58 practices or a subset;
  (c) whether the test numbers are per practice or averaged.

Our evaluation computes, from the same per-policy confusion counts: precision, recall,
F1, NPV, specificity and negative F1. Only the last three are compared with the paper.

## 7. Label counts per practice

Paragraph counts are labelled segments. Policy answers use the brief's rule: `yes` = some
segment performed and none not-performed; `no` = only not-performed; `conflict` = both;
all other policies are `not_mentioned`.
\*Test positive = test policies whose answer is `yes` or `conflict` (the policy says
"performed" somewhere).

| Practice | Train paragraphs perf / not | Train policies yes / no / conflict | Val policies yes / no / conflict | Test policies yes / no / conflict | Test positive* | Evaluable (≥10) |
|---|---:|---:|---:|---:|---:|:---:|
| `Contact_1stParty` | 95 / 21 | 39 / 4 / 15 | 10 / 2 / 9 | 30 / 0 / 0 | 30 | yes |
| `Contact_3rdParty` | 13 / 4 | 6 / 1 / 2 | 1 / 1 / 0 | 8 / 2 / 0 | 8 | no |
| `Contact_Address_Book_1stParty` | 89 / 35 | 28 / 10 / 15 | 8 / 1 / 8 | 25 / 0 / 1 | 26 | yes |
| `Contact_Address_Book_3rdParty` | 8 / 1 | 3 / 0 / 1 | 0 / 0 / 0 | 2 / 0 / 0 | 2 | no |
| `Contact_City_1stParty` | 28 / 8 | 11 / 1 / 6 | 4 / 0 / 3 | 11 / 0 / 0 | 11 | yes |
| `Contact_City_3rdParty` | 4 / 0 | 3 / 0 / 0 | 0 / 0 / 0 | 2 / 1 / 0 | 2 | no |
| `Contact_E_Mail_Address_1stParty` | 458 / 150 | 56 / 19 / 72 | 19 / 6 / 27 | 77 / 6 / 3 | 80 | yes |
| `Contact_E_Mail_Address_3rdParty` | 56 / 23 | 20 / 7 / 10 | 6 / 8 / 2 | 13 / 9 / 0 | 13 | yes |
| `Contact_Password_1stParty` | 82 / 39 | 28 / 8 / 23 | 10 / 2 / 11 | 28 / 4 / 2 | 30 | yes |
| `Contact_Password_3rdParty` | 7 / 3 | 2 / 2 / 1 | 1 / 1 / 0 | 2 / 0 / 0 | 2 | no |
| `Contact_Phone_Number_1stParty` | 220 / 101 | 31 / 16 / 46 | 13 / 9 / 21 | 53 / 8 / 1 | 54 | yes |
| `Contact_Phone_Number_3rdParty` | 28 / 15 | 10 / 5 / 5 | 2 / 3 / 1 | 5 / 3 / 0 | 5 | no |
| `Contact_Postal_Address_1stParty` | 136 / 81 | 29 / 19 / 32 | 12 / 5 / 17 | 39 / 7 / 1 | 40 | yes |
| `Contact_Postal_Address_3rdParty` | 23 / 12 | 10 / 5 / 2 | 3 / 4 / 0 | 5 / 7 / 0 | 5 | no |
| `Contact_ZIP_1stParty` | 47 / 12 | 11 / 0 / 12 | 5 / 0 / 1 | 11 / 0 / 0 | 11 | yes |
| `Contact_ZIP_3rdParty` | 9 / 3 | 4 / 3 / 0 | 2 / 0 / 1 | 3 / 0 / 0 | 3 | no |
| `Demographic_1stParty` | 56 / 19 | 27 / 1 / 11 | 10 / 0 / 3 | 29 / 0 / 0 | 29 | yes |
| `Demographic_3rdParty` | 38 / 5 | 20 / 2 / 3 | 4 / 0 / 1 | 17 / 0 / 0 | 17 | yes |
| `Demographic_Age_1stParty` | 101 / 45 | 25 / 1 / 31 | 11 / 0 / 9 | 35 / 0 / 1 | 36 | yes |
| `Demographic_Age_3rdParty` | 24 / 4 | 13 / 2 / 1 | 2 / 0 / 0 | 10 / 1 / 0 | 10 | yes |
| `Demographic_Gender_1stParty` | 87 / 38 | 22 / 0 / 27 | 10 / 0 / 9 | 27 / 0 / 0 | 27 | yes |
| `Demographic_Gender_3rdParty` | 21 / 3 | 12 / 2 / 1 | 0 / 0 / 0 | 7 / 0 / 0 | 7 | no |
| `Facebook_SSO` | 86 / 23 | 30 / 3 / 20 | 4 / 0 / 11 | 32 / 0 / 0 | 32 | yes |
| `Identifier_1stParty` | 41 / 12 | 15 / 2 / 8 | 7 / 0 / 2 | 19 / 0 / 1 | 20 | yes |
| `Identifier_3rdParty` | 15 / 4 | 7 / 1 / 3 | 2 / 1 / 1 | 8 / 0 / 0 | 8 | no |
| `Identifier_Ad_ID_1stParty` | 70 / 22 | 18 / 1 / 15 | 6 / 1 / 5 | 19 / 0 / 0 | 19 | yes |
| `Identifier_Ad_ID_3rdParty` | 53 / 12 | 12 / 1 / 9 | 5 / 1 / 2 | 11 / 0 / 0 | 11 | yes |
| `Identifier_Cookie_or_similar_Tech_1stParty` | 398 / 47 | 90 / 5 / 28 | 33 / 0 / 11 | 63 / 0 / 0 | 63 | yes |
| `Identifier_Cookie_or_similar_Tech_3rdParty` | 253 / 35 | 59 / 0 / 26 | 20 / 0 / 11 | 52 / 0 / 0 | 52 | yes |
| `Identifier_Device_ID_1stParty` | 191 / 67 | 59 / 1 / 50 | 17 / 1 / 20 | 54 / 0 / 0 | 54 | yes |
| `Identifier_Device_ID_3rdParty` | 77 / 28 | 24 / 4 / 18 | 8 / 0 / 6 | 21 / 0 / 0 | 21 | yes |
| `Identifier_IMEI_1stParty` | 28 / 9 | 11 / 1 / 7 | 2 / 1 / 4 | 17 / 0 / 0 | 17 | yes |
| `Identifier_IMEI_3rdParty` | 8 / 6 | 3 / 1 / 3 | 0 / 0 / 1 | 4 / 0 / 0 | 4 | no |
| `Identifier_IMSI_1stParty` | 1 / 3 | 0 / 2 / 1 | 0 / 0 / 2 | 3 / 0 / 0 | 3 | no |
| `Identifier_IMSI_3rdParty` | 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 1 / 0 / 0 | 1 | no |
| `Identifier_IP_Address_1stParty` | 262 / 88 | 66 / 1 / 59 | 21 / 1 / 21 | 65 / 0 / 2 | 67 | yes |
| `Identifier_IP_Address_3rdParty` | 119 / 37 | 39 / 4 / 23 | 8 / 1 / 8 | 25 / 0 / 0 | 25 | yes |
| `Identifier_MAC_1stParty` | 50 / 15 | 22 / 1 / 10 | 5 / 1 / 4 | 19 / 0 / 0 | 19 | yes |
| `Identifier_MAC_3rdParty` | 21 / 11 | 11 / 3 / 5 | 1 / 0 / 1 | 6 / 0 / 0 | 6 | no |
| `Identifier_Mobile_Carrier_1stParty` | 45 / 9 | 19 / 0 / 5 | 7 / 0 / 4 | 21 / 0 / 0 | 21 | yes |
| `Identifier_Mobile_Carrier_3rdParty` | 10 / 3 | 4 / 1 / 2 | 1 / 0 / 0 | 3 / 0 / 0 | 3 | no |
| `Identifier_SIM_Serial_1stParty` | 8 / 3 | 3 / 1 / 2 | 1 / 0 / 3 | 8 / 0 / 0 | 8 | no |
| `Identifier_SIM_Serial_3rdParty` | 0 / 2 | 0 / 2 / 0 | 0 / 0 / 0 | 1 / 0 / 0 | 1 | no |
| `Identifier_SSID_BSSID_1stParty` | 2 / 5 | 1 / 4 / 1 | 0 / 0 / 0 | 5 / 0 / 0 | 5 | no |
| `Identifier_SSID_BSSID_3rdParty` | 2 / 0 | 1 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 | no |
| `Location_1stParty` | 344 / 123 | 52 / 5 / 66 | 26 / 1 / 23 | 57 / 7 / 1 | 58 | yes |
| `Location_3rdParty` | 96 / 35 | 21 / 6 / 18 | 10 / 2 / 4 | 23 / 1 / 0 | 23 | yes |
| `Location_Bluetooth_1stParty` | 56 / 22 | 19 / 7 / 10 | 5 / 3 / 5 | 11 / 1 / 0 | 11 | yes |
| `Location_Bluetooth_3rdParty` | 19 / 8 | 4 / 1 / 6 | 3 / 1 / 2 | 2 / 1 / 0 | 2 | no |
| `Location_Cell_Tower_1stParty` | 63 / 15 | 27 / 2 / 12 | 10 / 0 / 7 | 14 / 0 / 0 | 14 | yes |
| `Location_Cell_Tower_3rdParty` | 19 / 7 | 9 / 2 / 4 | 3 / 0 / 3 | 4 / 1 / 0 | 4 | no |
| `Location_GPS_1stParty` | 101 / 27 | 32 / 6 / 15 | 11 / 4 / 8 | 29 / 2 / 0 | 29 | yes |
| `Location_GPS_3rdParty` | 25 / 14 | 6 / 5 / 7 | 3 / 1 / 2 | 6 / 1 / 0 | 6 | no |
| `Location_IP_Address_1stParty` | 60 / 17 | 25 / 1 / 16 | 11 / 1 / 3 | 20 / 0 / 0 | 20 | yes |
| `Location_IP_Address_3rdParty` | 24 / 6 | 14 / 2 / 3 | 5 / 0 / 3 | 5 / 1 / 0 | 5 | no |
| `Location_WiFi_1stParty` | 88 / 25 | 31 / 6 / 13 | 6 / 4 / 7 | 12 / 1 / 0 | 12 | yes |
| `Location_WiFi_3rdParty` | 23 / 12 | 5 / 4 / 6 | 3 / 1 / 2 | 2 / 1 / 0 | 2 | no |
| `SSO` | 116 / 30 | 37 / 2 / 23 | 11 / 0 / 10 | 37 / 0 / 0 | 37 | yes |
| **Total** | 4404 / 1404 | 1186 / 196 / 810 | 388 / 68 / 319 | 1118 / 65 / 13 | 1131 | 35 of 58 |

**Too rare to evaluate reliably** (fewer than 10 positive test policies): 23 of 58
practices, almost all third-party fine-grained types (e.g. `Contact_City_3rdParty` with
2 positive test policies, `Identifier_SSID_BSSID_3rdParty` with 0), plus
`Identifier_IMSI_1stParty`, `Identifier_SIM_Serial_1stParty` and
`Identifier_SSID_BSSID_1stParty`. They are still predicted and listed, but left out of
the averages. 35 practices remain evaluable.

**Annotation reliability:** inter-annotator agreement (Krippendorff's alpha, per
policy) is below 0.67 ("doubtful") for 16 of 58 practices, e.g. `Contact_Password_3rdParty`
(-0.01), `Demographic_1stParty` (0.39), `Location_IP_Address_1stParty` (0.40). Errors
on these practices partly reflect disagreement among the experts themselves.
