# LONGEVITY.md — what to track, what to ignore, and what the numbers mean

Fork-owned. Context for using this platform's data to set long-term healthspan
goals, and for the Grafana dashboard built on top of it
(`openwearables-longevity`, provisioned from the homelab repo).

Written because the hard part is not collecting the data — this platform already
holds most of the relevant signals — but knowing **which numbers carry evidence,
which are vendor decoration, and by how much each device's reading is offset from
the population data you would benchmark against.** Getting that wrong produces a
dashboard that looks authoritative and quietly points the wrong way.

Sourced from a literature review conducted 2026-08-29. Claims flagged
**[unverified]** were not read back to primary source; treat them as provisional.

---

## 1. The five signals worth setting goals on

Everything else on the dashboard is context, not a target.

| Signal | Evidence | Target | Where it comes from |
|---|---|---|---|
| **VO₂max** | Strongest wearable-derived mortality predictor. Low vs elite fitness carries a larger hazard than smoking, diabetes or CAD (Mandsager 2018, n=122,007). No upper plateau — elite still beat high. | **45–48 Garmin-estimated**, and lose **<7%/decade** | `vo2_max` series |
| **Sleep regularity (SRI)** | UK Biobank n=60,977 accelerometry: most- vs least-regular quintile ~30% lower all-cause mortality. **Beat sleep duration** — adding duration did not improve model fit. Replicated independently (n=88,975). | Bed/wake SD **<60 min**; SRI trend upward | derived from `event_record` sleep intervals |
| **Steps** | 7,000 vs 2,000/day → all-cause HR 0.53; inflection ~5,000–7,000. The 10,000 figure "originates from a marketing campaign in Japan". | **8,000–10,000/day** | `steps` series |
| **VILPA** (vigorous bursts) | ~3.4 min/day → 22–28% lower all-cause mortality in 25,241 non-exercisers. 1 min vigorous ≈ 4.1 min moderate ≈ 52.6 min light — far from the 1:2 guideline ratio. | **3–5 min/day** | derived from `heart_rate` above a threshold |
| **Grip strength** | Per 5 kg lower, all-cause HR 1.16. "A stronger predictor of all-cause and cardiovascular mortality than systolic blood pressure" (PURE, n=139,691). | **≥50 kg dominant hand at 45** | ⚠️ **not collected** — needs a ~$50 dynamometer, quarterly |

**RHR is a sixth, but only as a derivative.** The level's causal story was
overturned by its own field: a 2023 Mendelian-randomisation study (n=835,465)
found no genetic association, and SIGNIFY lowered HR by exactly 10 bpm in 19,102
patients with **no** mortality benefit. What survives is *change* — ARIC found
HR 1.12 per +5 bpm of temporal change, beating baseline RHR. Goal: **90-day
slope < +0.8 bpm/year**, and don't chase very low (J-shape for AF).

---

## 2. Calibration offsets — the part that silently corrupts benchmarking

Every one of these is a case where a device number and a published percentile
are **not the same measurement**. Joining them without an offset is the most
likely way to draw a confident wrong conclusion.

| Comparison | Offset | Consequence if ignored |
|---|---|---|
| Garmin VO₂max vs FRIEND percentiles | Garmin **over**states; FRIEND is measured CPET | You place yourself several percentile bands too high |
| Wearable RHR vs NHANES | NHANES is a **seated daytime pulse, 5–15 bpm higher** | Your sleep-derived RHR looks artificially excellent |
| Overnight ring HRV vs KORA 5-min supine norms | **~2×** difference purely from method | Population position is meaningless |
| Hip-worn NHANES steps vs wrist consumer steps | NHANES 2005–06 uncensored 9,676/day vs 6,540 censored | ~50% swing from the counting convention alone |
| Treadmill vs cycle VO₂max | Cycle runs **10–15% below** | Mixing modes invents a decline |
| Oura Gen3 vs Gen4 HRV | ~1.5 ms against the same ECG | **A device swap is a hard series break** |

Two practical rules follow. **Never merge Garmin and Ultrahuman into one
series** for the same metric — store `device_model` and provider as schema
dimensions and keep them separate. And use published percentiles for rough
positioning only; for anything you intend to act on, trend against **your own
baseline**.

---

## 3. What to ignore, and why

These appear on wearable dashboards and carry no outcome evidence. They are
listed so nobody re-adds them believing they were overlooked.

- **Garmin Body Battery** — zero independent validation studies exist.
- **Garmin Stress Score** — one n=60 study: correlates with HR and RMSSD
  (unsurprising, it is derived from them) but had "marginal predictive value for
  subjective stress", and plain heart rate outperformed it.
- **Readiness / Recovery scores** (Oura, WHOOP, Garmin Training Readiness) — no
  study validates any composite against health outcomes. They are undisclosed
  weighted blends of RHR, HRV, sleep and prior load, with **silent version
  changes that break a longitudinal series without notice**. Store the raw
  inputs and compute your own if you want one; never trend a vendor score across
  years.
- **"Fitness age" / "cardio age" / wearable "biological age"** — a presentation
  layer. Garmin's is repackaged VO₂max percentile and carries no information
  beyond the VO₂max it is computed from. Ultrahuman's Cardio Age paper is
  authored entirely by Ultrahuman employees, is retrospective on their own
  customers, has no CPET comparison and no outcome data, and reports 82.6% of
  users scoring "younger" than chronological age — a marketing property, not a
  measurement property.
- **Sleep stage minutes** — deep/REM are the least trustworthy numbers a ring or
  watch produces. Across seven devices, sensitivity to *sleep* was ≥0.93 but
  **specificity for wake was 0.18–0.54**, and Garmin devices "ranked last on most
  performance metrics… often more extreme than actigraphy". Four-stage Cohen's
  κ ≈ 0.47–0.49. **Trust total sleep time and sleep timing; never set a goal on
  deep-sleep minutes or REM %.** The dashboard's hypnogram and stage panels are
  deliberately labelled display-only.
- **Wearable SpO₂** — fēnix 6 scored **CCC 0.10** and returned data for only 59%
  of requested measurements. Skin-tone bias in reflectance PPG is real and
  documented (every pigmentation group breached the FDA 3% threshold in one
  meta-analysis; dark skin carried the largest *positive* bias, which is the
  dangerous direction). Use as an ordinal within-person signal at most.
- **"Zone 2 hours"** — **no study has linked time-in-zone-2 to mortality or any
  hard outcome.** The claim is mechanistic reasoning plus extrapolation from
  elite-athlete volume. The one large RCT (Generation 100, n=1,567, five years)
  was null. Track VO₂max and vigorous minutes instead.
- **Chronotype** — definite-evening vs definite-morning HR 1.10, from a single
  self-report question, heavily confounded. Use objective sleep timing instead.

---

## 4. Gaps in *this* deployment

Found by inspecting the live database, not assumed.

**No lab chemistry exists in the schema at all.** `cholesterol`, `ldl`, `hdl`,
`triglycerides`, `hba1c`, `crp`, `apob`, `ferritin`, `creatinine`, `albumin` —
all absent from `SeriesType`. The only `insulin` hit is `insulin_delivery`, a
pump metric. Vitals *are* modelled (`blood_glucose`, `blood_pressure_*`,
`forced_vital_capacity`), so the gap is specifically lab analytes.

This directly gates the interesting maths: **PhenoAge needs albumin, creatinine,
glucose, CRP, lymphocyte %, MCV, RDW, ALP and WBC — of which this platform can
currently store exactly one.**

**`personal_record` is empty** — no `birth_date`, no `sex`. That blocks every
age/sex percentile comparison and any HRmax-derived threshold. Populating those
two fields is the cheapest unlock available.

**Suggested shape for labs** (LOINC as the key so "Ferritin"/"FERRITIN"/"Serum
ferritin" collapse to `2276-4`):

```sql
lab_result(user_id, collected_at, loinc_code, analyte_name,
           value, ucum_unit, ref_low, ref_high, lab_name, source_document)
```

Store **`collected_at`, not report time** — that is the join key to wearable
data and they differ by days. Always keep the raw PDF. RCPA/SPIA harmonised
reference intervals with LOINC codes are published on the NCTS and the Digital
Health Implementer Hub — that is the Australian reference-range table, done
properly.

⚠️ **Unit trap:** glucose and cholesterol are **mmol/L in Australia** vs mg/dL in
the US, and µg vs mg is a 1000× error that looks plausible. Normalise
explicitly and store what was actually recorded.

---

## 5. PhenoAge — implementable today, once labs exist

Closed-form, no reference dataset, and **its native units are SI — i.e. exactly
what an Australian pathology report prints.** Only one conversion: CRP mg/L ÷ 10.

```
xb = -19.90667
   + (-0.03359355  * albumin_g_per_L)
   + ( 0.009506491 * creatinine_umol_per_L)
   + ( 0.1953192   * glucose_mmol_per_L)        # fasting
   + ( 0.09536762  * ln(CRP_mg_per_dL))         # = CRP_mg_per_L / 10
   + (-0.01199984  * lymphocyte_percent)
   + ( 0.02676401  * MCV_fL)
   + ( 0.3306156   * RDW_percent)
   + ( 0.001868778 * ALP_U_per_L)
   + ( 0.05542406  * WBC_1000_per_uL)
   + ( 0.08035356  * age_years)

M = 1 - exp(-1.51714 * exp(xb) / 0.0076927)
PhenoAge = 141.50225 + ln(-0.0055305 * ln(1 - M)) / 0.090165
```

**Regression test (verified by running it):** albumin 45, creatinine 80, glucose
5.0, CRP 1.0 mg/L, lymphocytes 30%, MCV 90, RDW 13, ALP 70, WBC 6.0, age 50
→ `xb = -9.073`, `M = 0.0224`, **PhenoAge ≈ 41.8**.

**Guard:** feeding US conventional units (4.5 / 0.9 / 90) drives `M → 1` and the
final `ln` throws. Keep that as an assertion — it fails loudly rather than
returning a plausible wrong age. Getting the CRP conversion wrong alone inflates
`xb` by 0.2196 ≈ **+2.4 years**.

Two clinical gates: glucose must be **fasting**, and CRP is an acute-phase
reactant — **refuse or flag when hs-CRP > ~10 mg/L**. Published *PhenoAgeAccel*
is a within-sample residual and is not reproducible for n=1; use plain
`PhenoAge − age` and label it as such.

Source: Liu Z et al., *PLoS Med* 2018;15(12):e1002718 — **cite the 2019
correction for the equation**. Do not confuse with Levine ME et al., *Aging*
2018, which is the DNAm clock and needs a methylation array.

⚠️ A known-bad reference (`github.com/199-mcp/mcp-phenoage-clock`) documents US
conventional units against these SI coefficients. Do not copy its unit table.

---

## 6. Getting clinical data in — Australia

**My Health Record is now the only real source, and the bottleneck.** The
*Sharing by Default* amendment commenced **1 July 2026**: pathology and imaging
must be uploaded, enforced by Medicare withholding and civil penalties, most
visible immediately. Crucially ADHA mandates **CDA conformance level 3A/3B**, so
reports carry machine-readable coded content rather than only a PDF.

But: there is **no `Observation` and no `DiagnosticReport`**. The path is
`DocumentReference` → `Binary/{id}` → base64 CDA ZIP → `CDA_ROOT.XML` + PDF. And
**GP consultation notes never appear** — there is no consult-note document type,
only a Shared Health Summary the GP writes when they choose to.

**First experiment, before building anything:** pull one pathology CDA and check
whether the per-analyte `Result Group` is populated (it is `0..*`, optional). If
yes, you get structured analyte rows with values, units, reference ranges and
normal flags. If no, you get panel metadata plus a PDF. **That single answer
decides whether ingest is a clean XML mapper or an LLM-plus-review pipeline.**

Ranked routes:

1. **MHR via myGov → download → parse the CDA.** Start here.
2. **APP 12 request to your GP practice** for the native Best Practice /
   MedicalDirector export — best shot at structured GP data *and* the visit
   records MHR lacks. Privacy Act 1988 APP 12.5: access "in the manner
   requested… if reasonable and practicable"; OAIC guidance says ≤30 days. Note
   there is **no right to a named machine-readable format** — a scanned PDF is
   compliance.
3. **APP 12 to the pathology provider** for full history — PDFs, but a complete
   backfill in one request.
4. MHR FHIR Gateway as a registered Portal Operator — the only true API, but
   requires courier-posted certified ID, Deloitte-run conformance testing and an
   App Distribution Channel. There is no personal-use tier; a GitHub search
   returns zero open-source MHR clients.

**Every Australian pathology provider offers patients nothing** — no portal, no
export, no API (Sullivan Nicolaides, QML, Australian Clinical Labs, 4Cyte all
verified; ACL and 4Cyte now explicitly redirect patients to MHR). That is the
deliberate national design.

⚠️ **QML self-request posts results by mail and will not email them**, and the
GP gets no copy — so it likely never reaches MHR. Cheapest, worst for a
pipeline.

**Design ingest for CDA XML parsing with PDF/OCR fallback, not a FHIR client.**
The US playbook does not port: Australia has no information-blocking rule and no
patient-access API mandate, AU Core is trial-use and system-to-system, and CDR
does not cover health.

---

## 7. The market, briefly

**No consumer longevity platform has a public API.** Function, Superpower,
InsideTracker, Marek, Biograph, Human Longevity, Neko, Bioniq — the industry
standard for "data portability" is a PDF. None operate in Australia. And the
data flows the wrong way: they *ingest* Garmin/Oura/Whoop and emit nothing, so
connecting this platform to them exports your data into their silo.

Locally, **Everlab** has a Brisbane site (A$299 baseline → ~A$2,999 annual) but
no wearable integration or documented export. Self-request pathology is legal in
Australia but carries **no Medicare rebate**.

Two things worth pursuing:

- **Ultrahuman Partner API** documents *Blood Test Reports* endpoints alongside
  daily metrics, this fork already ingests Ultrahuman, and Blood Vision is
  landing in Australia. Partner access is discretionary and slow — start the
  clock early. Their Vision Cloud parses any provider's lab PDF free, so it can
  be tested with a Sullivan Nicolaides report before committing.
- **TruDiagnostic** is the only biological-age test that exports raw data (CpG
  betas, IDATs on request) and ships to Australia. Buy **one**, then recompute
  locally forever with `pyaging` or `biolearn`. But read §8 first.

**Fasten Health is archived** (verified: `archived: true`, July 2026) and never
supported a single Australian provider. Do not plan around it.

**No open-source project correlates wearable timeseries against lab biomarkers
as a first-class feature.** That gap is exactly where this platform sits.

---

## 8. Where the science is genuinely contested

State these plainly anywhere biological age is displayed.

- **Epigenetic clock replicates of the same sample deviate 3–9 years** across
  six major clocks. PCA-based clocks fix this; most consumer ones are not.
- **Biological reliability is a separate and worse problem**, and is
  uncorrelated with technical reliability (r = 0.017). Most clocks fall to
  ICC 0.4–0.7 across meals, stress and pollution — **DunedinPACE and GrimAge in
  the "poor" range**. DunedinPACE's often-quoted ICC 0.96 is a *technical
  replicate* figure, not week-to-week stability.
- **Effect size is modest**: adding GrimAge v2 to conventional risk factors moved
  10-year mortality AUC 0.851 → 0.865.
- **No epigenetic clock is FDA-qualified** as a surrogate endpoint.
- **Pace of aging is not computable from bloods.** Both the original Dunedin
  measure (needs ≥3 repeats over ~12 years plus spirometry, VO₂max, periodontal
  attachment and telomere length) and DunedinPACE (needs a methylation matrix)
  are out of reach without an array.

---

## 9. Flagged unverified

Not read back to primary source during the review:

- FRIEND **2022** per-percentile values (paywalled; the 2015 table is verified,
  and the 2022 update states standards are 1.5–4.6 mL/kg/min *lower*, so the
  2015 numbers flatter slightly).
- That Garmin's exposed HRV metric is specifically RMSSD.
- That Garmin's VO₂max categories are Cooper-derived.
- Wearable respiratory-rate agreement of ±1 breath/min (conventionally cited).
- The 1965 Yamasa/manpo-kei attribution for the 10,000-step origin — the
  defensible form is only "originates from a marketing campaign in Japan".

---

## 10. The dashboard

`openwearables-longevity`, provisioned as a ConfigMap from the homelab repo
(`k8s/manifests/addons/kube-prometheus-stack/dashboard-openwearables-longevity.yaml`),
reading this database through a read-only `grafana_ro` Postgres datasource.

Nineteen panels over six rows. Targets live in panel thresholds and SQL
denominators and are **meant to be edited**; each panel description carries its
evidence grade and calibration caveat.

Two query traps are baked into the SQL and must survive any edit:

- **`is_daily_total`** — steps and energy exist as *both* intraday samples and
  provider daily totals. `SUM(value)` across both roughly doubles the count.
- **Two providers per night** — Garmin and Ultrahuman both record sleep, so an
  unfiltered query returns two rows a night and averages land between two
  devices. Hence the `$sleep_provider` / `$activity_provider` variables.

And two derived metrics are **own implementations**, not the published measures:

- **SRI** uses 5-minute epochs over sleep-session intervals. Published
  implementations disagree markedly on identical data — two analyses of one
  cohort reported medians of 81 vs 60 — so **track its trend, never compare it
  to a published cut-point.**
- **Vigorous minutes** counts *distinct minutes* with any HR sample above
  `$vigorous_hr`. Counting samples instead over-reads by ~50× during workouts,
  because the `fix-garmin-connect-activity-hr-samples` patch stores per-second
  samples alongside 2-minute daily ones. It also includes deliberate exercise,
  whereas VILPA is specifically *incidental* activity in non-exercisers.
