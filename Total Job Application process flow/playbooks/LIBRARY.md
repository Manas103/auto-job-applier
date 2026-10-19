# LIBRARY: compact index of existing repos

Generated from `playbooks/PORTFOLIO.md`; do not edit by hand. One line per repo: what it is, then [stack].
Use it to shortlist reuse and extend candidates for a project slot, then search PORTFOLIO.md for each
candidate's full row (measured numbers, disclosures) before writing a bullet. Numbers are deliberately
omitted here: always take them from the full row.

- order-matching-engine: Price-time-priority limit order book with a lock-free ingest queue and a replay harness [C++20, CMake] (extended: (Sep. 2026) with a market-data publisher; keep the extension month when it...)
- market-data-decoder: Binary market-data feed decoder with sequence-gap recovery and a pcap replay tool [C++17, CMake]
- inference-batching-server: Continuous-batching inference server for small language models with KV-cache reuse [Python, PyTorch, FastAPI]
- churn-prediction-pipeline: End-to-end churn model: feature pipeline, gradient boosting, calibration, model card [Python, pandas, scikit-learn, dbt]
- telemetry-ground-station: Telemetry ingest and alerting service for simulated spacecraft packets [Rust, Tokio, Grafana]
- flight-software-test-harness: Hardware-in-the-loop style test harness with fault injection for a simulated flight computer [C, Python, pytest]
- feature-flag-service: Multi-tenant feature-flag service with percentage rollouts and an audit log [Go, PostgreSQL, Redis]
- log-anomaly-detector: Sequence model flagging anomalous service logs with a labeled evaluation set [Python, PyTorch]
