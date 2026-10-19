# RECONCILE playbook

You bring the realistic resume and its documents back into agreement with what was actually built and measured. Truth flows one way: from `build_report.json` into the resume. You never touch a repository, and you never raise a claim above its measurement.

Inputs: the role folder (`ideal_candidate_realistic_<slug>.tex` and any variants, both breakdowns, `no_brainer_hire_<slug>.md`, `experience_descriptions_<slug>.md`, `meta.json`) and `build_report.json`.

No em dashes anywhere.

---

## 1. For every benchmark entry in build_report.json

- `meets_claim: true`: leave the bullet's number as is. If the measured value is materially higher than the claim and the bullet would be stronger and still defensible with the measured value, you may raise it to the measured value (never above it), and say so in the report.
- `meets_claim: false`: rewrite the bullet so the number is the measured one. Keep the bullet's shape, technique, and verb; change only the quantity and any qualifier that the measurement requires ("1M+ messages/sec" becomes "815K messages/sec"; "cut replay storage 9x" becomes "cut replay storage 4.7x"). Never round up. If the measurement came with a scope ("single-threaded drain", "warm cache", "synthetic feed"), and the bullet would be misleading without it, add the scope in as few words as possible.
- Non-numeric claims (`value: false`, e.g. "bit-for-bit reproducible" that was not achieved): remove or reword the claim so it is true.
- A claim the builder could not measure at all: reword the bullet so it makes no claim that cannot be defended, and flag it in the report.

## 2. Dates

Set each built project's month on the resume to `build_report.month_built` (the real build month). This is true even when the build month is the current month: a pushed repository with commits is a real artifact, and the verifier accepts `month_built` from `build_report.json` as a legitimate current-month date. Never back-date a built project to satisfy the date rule. Keep the projects in an order that still makes chronological sense; if the order changed, update the breakdown's "why this order" argument. Reused repos keep the month recorded in the portfolio table for them.

## 3. Documents

- Update the realistic breakdown wherever it justified the old number or the old date.
- Update the no-brainer document if it quoted the old number.
- Update `experience_descriptions_<slug>.md` wherever it states a number or a date you changed: the Short and Long blocks and the `Form fields` line must match the reconciled resume exactly (limits: 600 and 1500 characters).
- Update `meta.json` `claims` to the reconciled values and add `"reconciled": true`.
- Apply the same edits to every variant `.tex` (for example `_nopapers`).

## 4. Recompile and verify

Recompile every `.tex` in the folder twice with pdflatex; each must be exactly one page with zero Overfull/Underfull box warnings. A shorter number can change line breaks; fix any warning you introduce.

## 5. reconciliation.md

Write `reconciliation.md` in the role folder:

```
# Reconciliation: <Company> <Role>

| File | Location | Before | After | Reason |
|---|---|---|---|---|
| ideal_candidate_realistic_optiver.tex | Projects, bullet 1 | 1M+ messages/sec | 815K messages/sec | measured decode_rate 815,678 msgs/sec (docs/benchmark_output.txt) |
| ... | ... | ... | ... | ... |

Unmeasured or removed claims: ...
Date changes: ...
Variants updated: ...
```

Include every change, including "no change needed" rows for claims that were met, so the table is a complete audit of the resume against the build.

## 6. Finish checklist

- [ ] No resume number exceeds its measurement.
- [ ] Project months equal the real build month.
- [ ] Breakdown, no-brainer, meta.json, and all variants updated consistently.
- [ ] Every `.tex` recompiled: one page, no box warnings.
- [ ] `reconciliation.md` complete.
