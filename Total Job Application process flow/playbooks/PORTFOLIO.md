# PORTFOLIO: existing project repos

EXAMPLE FILE. These repos are fictional placeholders for the example applicant in `ASSETS.md`. Replace the table with your own repos. Search this file for the repos you need; do not read it in full. `playbooks/LIBRARY.md` is a generated one-line-per-repo index of it.

## Existing project portfolio (assets, not constraints)

Design-first rule: when building the realistic resume, design the role's projects from the JD and the no-brainer analysis BEFORE reading this table. Then compare. An existing repo may replace a designed project only if it is at least as strong for this reader as the design.

All repos live at `projects/<name>` in this workspace and at `https://github.com/<your-github-user>/<name>`. Numbers are measured, from each README.

| Repo | Stack / platform | What it is | Headline measured numbers |
|---|---|---|---|
| order-matching-engine | C++20, CMake | Price-time-priority limit order book with a lock-free ingest queue and a replay harness | 3M orders/sec single thread; p99 under 2us on the replay set |
| market-data-decoder | C++17, CMake | Binary market-data feed decoder with sequence-gap recovery and a pcap replay tool | 20M messages/sec decode; zero gaps on a 1h capture |
| inference-batching-server | Python, PyTorch, FastAPI | Continuous-batching inference server for small language models with KV-cache reuse | 3.1x throughput over naive batching at equal p95 latency |
| churn-prediction-pipeline | Python, pandas, scikit-learn, dbt | End-to-end churn model: feature pipeline, gradient boosting, calibration, model card | AUC 0.86 on a held-out month; calibration error 0.02 |
| telemetry-ground-station | Rust, Tokio, Grafana | Telemetry ingest and alerting service for simulated spacecraft packets | 250K packets/sec; alert latency p99 40ms |
| flight-software-test-harness | C, Python, pytest | Hardware-in-the-loop style test harness with fault injection for a simulated flight computer | 400 scenarios; 12 injected fault classes all detected |
| feature-flag-service | Go, PostgreSQL, Redis | Multi-tenant feature-flag service with percentage rollouts and an audit log | 50K evaluations/sec per node; p99 3ms |
| log-anomaly-detector | Python, PyTorch | Sequence model flagging anomalous service logs with a labeled evaluation set | F1 0.81 against a rules baseline of 0.62 |

`order-matching-engine` was extended (Sep. 2026) with a market-data publisher; keep the extension month when it is used.

Notes for stages:
- Reuse means the repo already demonstrates what the slot needs. Extend means a real, measured change to an existing repo. New means the builder creates and publishes a repo.
- Never quote a number that is not in the repo's README.
- Prefer one strong repo over two weak ones; space on a one-page resume is paid for by cutting something else.

## Per-repo notes (example)

- order-matching-engine: the replay harness is the strongest artifact; it reproduces any order sequence from a capture and checks the book against a reference model after every event. Disclosure: single-machine benchmark on a laptop, numbers are not exchange-grade.
- market-data-decoder: decodes a public sample feed; the gap-recovery path is tested by dropping packets at random. Disclosure: synthetic captures only.
- inference-batching-server: continuous batching with per-request KV-cache slots; the benchmark compares against static batching at the same hardware and model size. Disclosure: small models only, single GPU.
- churn-prediction-pipeline: features are built with dbt models over a public dataset; calibration is checked with reliability curves and reported in the model card. Disclosure: public data, no production traffic.
- telemetry-ground-station: async ingest with backpressure, alert rules as code, Grafana dashboards checked in. Disclosure: simulated packets.
- flight-software-test-harness: scenario files describe sensor inputs and expected outputs; fault injection covers stuck sensors, bit flips and timing jitter. Disclosure: simulated flight computer, no real hardware.
- feature-flag-service: percentage rollouts are deterministic per user id; the audit log is append-only. Disclosure: load test on one node.
- log-anomaly-detector: labeled evaluation set built by hand from public incident write-ups. Disclosure: small labeled set, F1 has wide error bars.

## How to fill this file

One table row per public repo: the repo name exactly as on GitHub, the stack, one sentence on what it is, and the headline numbers copied from its README. Add a per-repo note when a number needs a disclosure (synthetic data, single machine, small sample). Stages read the row and the note; anything not written here is treated as not true.
