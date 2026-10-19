# Experience Descriptions: Optiver Software Engineer Intern (Summer 2027)

Copy-paste text for application forms, derived from `ideal_candidate_realistic_t.tex`. Every number and date matches the resume.

## Acme Labs, Software Engineer (Feb. 2026 to Jun. 2026)

Form fields: Acme Labs | Software Engineer | Remote | Feb. 2026 | Jun. 2026

Short (under 600 characters):
```
I owned the search engine for a social platform end to end: crawler, index, ranking, and the query service. I rewrote the indexing pipeline in Python with an async I/O core so it could process 6.7M documents on a single machine, and built latency tooling that non-engineers on the team used to catch regressions before release.
```

Long (under 1500 characters):
```
Acme Labs is a social platform where search is the main way people find communities. I owned the search engine end to end, from the crawler through the index and ranking to the query service. The indexing pipeline had become the bottleneck, so I rewrote it in Python around an async I/O core and a batched write path, which let it process 6.7M documents on a single machine within the nightly window.

I also built the latency tooling the team used to catch regressions: a replay harness that ran recorded queries against a candidate build and reported p50 and p99 per query class. People outside engineering used it before every release, which is where I learned to design measurement tools for readers who are not engineers.
```

## Latency Replay Harness (Jan. 2026)

Form fields: Personal project | Latency Replay Harness | College Town, IL | Jan. 2026 | Jan. 2026

Short (under 600 characters):
```
A C++ harness that replays recorded query traffic against a service and reports p50 and p99 latency per query class, with a deterministic seed so two runs on the same build are comparable. Measured on a 6-core machine under WSL2, it drains 815K messages per second single-threaded.
```

Long (under 1500 characters):
```
I built this harness because comparing two builds by eye was not defensible. It replays recorded query traffic against a service, tags each query with a class, and reports p50 and p99 latency per class with a deterministic seed so two runs on the same build are directly comparable.

The hot path is a single-producer, single-consumer ring buffer with cache-line padding. Measured on a 6-core machine under WSL2 with g++ 11 at -O2, it drains 815K messages per second single-threaded. The README records the raw output and the case where the design loses: bursty traffic with many small query classes, where the per-class histograms dominate the cost.
```

## Notes

If the form allows only three entries, use Acme Labs, the Latency Replay Harness, and the open source work, in that order. For "why this company": the posting emphasizes measurement discipline and owning a system end to end, which is exactly how the Acme Labs work is described above.
