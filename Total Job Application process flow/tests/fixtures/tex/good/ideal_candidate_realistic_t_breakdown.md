# Realistic Ideal Candidate Breakdown: Optiver Software Engineer Intern (Summer 2027, Chicago)

**Candidate:** Alex Doe, State University, B.S. Computer Science, GPA 3.50
**Files:**
- `ideal_candidate_realistic_optiver.tex` (with the arXiv preprint) **<- recommended**
- `ideal_candidate_realistic_optiver_nopapers.tex` (without the preprint)

Two versions were built at Alex's request. The final section of this document explains what actually differs between them and which one to send.

---

## The Strategic Frame: This Is Not the DRW Resume

DRW and Optiver are both Chicago proprietary trading firms hiring Summer 2027 software engineering interns. The instinct is to send the same resume. That instinct is wrong, and the reason is documented rather than inferred.

**DRW's posting selected for the hot path.** The resume built for it led with a market data decoder and a lock-free matching engine, and the open-source contributions were framed as the primary systems-engineering signal.

**Optiver has publicly moved.** Optiver's own engineering writeups state that *"even ultra-low latency is no longer a competitive moat in itself"* and that the firm *"invests substantially more in building better models than lowering latencies."* Their technology page leads with an **"AI-first mindset in how we build, test, deploy, and run."* Thirty to forty percent of Optiver's roughly 950 engineers work on **platform** teams, which their own source flags as unusually high.

And most concretely: Optiver publishes what its interns build, and the list is dominated by **tooling, capture formats, test frameworks, simulators, and alerting systems built for traders and researchers**, not trading strategy. The Chicago intern projects on record are a custom binary file format for exchange message processing (C, Python C extensions, Pandas) and an FPGA instrument prioritization system.

So the Optiver resume keeps the systems proof but **reorders and reframes around platform and tooling ownership**. Three changes carry that.

---

## Change 1: The Acme Labs Reframe, "Owned" and "Tooling Non-Engineers Actually Used"

This is the eighth distinct framing of the Acme Labs internship in this project, and it is built from two Optiver-specific words.

### Bullet 1: the word is "owned"

> "Owned a search platform end to end across a 5-month rotation, from a multithreaded C++ crawler ingesting 2M+ documents over concurrent TCP connections through an inverted index and a query service holding sub-50ms p99 for 100+ concurrent users, **taking every change from design through deployment and monitoring**."

Optiver's engineering culture has a name for this and uses it constantly: **"build and own."** Engineers are described as treating projects with CEO-level ownership, responsible for design, build, testing, deployment, monitoring, and support. Optiver has **no dedicated QA team**, specifically so that work is never thrown over a wall.

The trailing clause is written to echo that list back almost verbatim. It is not decoration. It converts a five-month internship into evidence for the exact cultural property Optiver says it hires on, and it is entirely true: he built, deployed, and operated the thing.

The DRW version of this bullet split the crawler and the query service into two separate bullets emphasizing throughput and latency numbers. Here they are compressed into one so that the second bullet can do work no other resume in this project has asked it to do.

### Bullet 2: the tooling bullet, which is the most important line on this resume

> "Built the tooling non-engineers actually used: a query-replay harness that reproduced any bad result from captured traffic, and a dashboard letting non-technical stakeholders retune ranking weights without a code change; presented the system to company leadership while working on a multilingual team."

Compare this against Optiver's published intern projects:

| Optiver intern project (real) | This bullet |
|---|---|
| Sydney: alerting framework letting traders and researchers write **custom validations** inside pricing models | Dashboard letting non-technical stakeholders **retune ranking weights without a code change** |
| Chicago: capture format for exchange messages, enabling high-quality data capture | Query-replay harness reproducing any bad result **from captured traffic** |
| Amsterdam: datacenter network simulator adopted by multiple teams | Tooling adopted by non-engineers on the team |

The match is close enough to be the argument of the resume. Optiver's SWE interns build things that traders and researchers use. Alex has already built things non-engineers used. That is a rarer signal among sophomore and junior applicants than another matching engine.

The 3D Three.js interface is **not** named here. It was the right call at Praxis, Roblox, and Axon for demo and product-sense reasons. At Optiver it would read as decorative, and the space is better spent on the replay harness. What survives from that work is the fact underneath it: he built the interface non-technical people used and presented it to leadership.

**"While working on a multilingual team"** is retained deliberately. The JD offers *"the opportunity to work alongside best-in-class professionals from over 40 different countries,"* and Optiver runs 11 offices across Amsterdam, Chicago, Austin, New York, London, Sydney, Shanghai, Hong Kong, Singapore, Taipei, and Mumbai. Having already delivered inside a foreign-language engineering team is a concrete answer to a soft claim.

---

## Change 2: The Generative AI Lab as a Serving Path and a Feature Pipeline

> "Built the real-time inference path for a speech-scoring model in an English-learning application, streaming audio over WebSockets within a 300ms budget, plus the feature pipeline turning 100K+ raw interaction events into the 40+ signals the model consumed."

This is a **single bullet doing two jobs**, and both jobs are Optiver jobs.

**"Real-time inference path ... within a 300ms budget"** is model serving under a latency constraint. Optiver's signals layer is described as market data collection, pricing algorithms, and ML pipelines, and their Chicago quantitative researchers describe *"building models that learn from huge streams of market data and make decisions at ultra-low latency."* A student who has put a model behind a hard latency budget has done the small version of that.

**"The feature pipeline turning 100K+ raw interaction events into the 40+ signals the model consumed"** is the part that matters more and is usually underplayed. Optiver's published Chicago and Amsterdam intern work is data-pipeline work. Framing the lab as *"I built the pipeline that fed the model"* rather than *"I trained a model"* is the correct emphasis for a platform-heavy engineering org.

**Vision Lab is excluded**, consistent with DRW, ByteDance, Roblox, and Axon. The neoantigen GAN is genuinely impressive computational work, but the biology domain is opaque to a trading-firm interviewer and would cost a line that the market data project uses better. Per the project constraints, Vision is for HPC, research-engineering, and biotech roles.

---

## Change 3: Project Order, Which Is an Argument

The three projects are ordered deliberately, and the order is the opposite of what a trading-firm applicant instinctively chooses.

### 1. Market Data Decoder and Tick Capture Pipeline (C++, Python, Feb 2026)

This is the DRW market data project **extended**. DRW's version stopped at decoding and order book reconstruction. Here it gains a second half:

> "...plus a column-oriented capture format with nanosecond timestamps that cut replay storage 9x and made any session reproducible bit for bit."

That extension is targeted directly at the real Chicago intern project (custom binary format for exchange message processing, order-of-magnitude efficiency gain) and at Optiver's contributed Postgres **timestamp9** nanosecond-precision type, which tells you how much they care about time resolution in stored data.

**"Made any session reproducible bit for bit"** is the phrase to defend in interview. Optiver runs CI on bare metal with custom hardware because performance measurement requires predictable compute profiles, and their writeups note that at their timescales measurement noise itself becomes the problem. Reproducible replay is the foundation of testing anything in that world.

Dated **Feb. 2026**, matching the DRW resume's date for the same project family. The two resumes stay internally consistent, which matters if both firms ever compare notes or if the same interviewer sees both.

### 2. Model Validation and Alerting Framework (Python, Kafka, PostgreSQL, Jun 2026)

**This is the new project built specifically for Optiver, and it is the highest-leverage single item on this resume.**

> "Built a service letting non-engineers define guardrails on a live model's output in a small expression language, evaluating 200+ rules per second against a streaming feed and routing each breach to an alert with the triggering input snapshot attached."

It is a near-direct analogue of a real published Optiver intern project (the Sydney alerting framework letting traders and researchers create custom validations within pricing models). Kafka and PostgreSQL are both documented parts of Optiver's infrastructure.

Three details are chosen to survive a project dive:
- **"A small expression language"** proves he built a product for other people rather than a script for himself. That is the whole point.
- **"200+ rules per second against a streaming feed"** is a defensible, unglamorous number. It is not a superlative and nobody will call it inflated.
- **"With the triggering input snapshot attached"** is an operational-maturity detail. It says he has actually been paged and knows that an alert without context is useless.

### 3. Lock-Free Order Matching Engine (C++, Dec 2025)

Identical to the DRW resume, same date, same numbers, deliberately unchanged and deliberately placed **last**.

Every serious applicant to a Chicago prop shop has a matching engine. At DRW it led, because DRW's posting selected for the hot path. At Optiver it anchors: it proves the systems depth is real (atomics, ring buffers, price-time priority, p99 under 1 microsecond) while the ordering says *"I know this is table stakes, and here is what I built on top of it."*

That ordering is itself a signal of having read the company rather than the industry.

---

## Change 4: Open Source Becomes a Domain Credential, Not Just a Systems Credential

Across this project the PyTorch and Triton block has been framed three ways: as an ML-infrastructure credential (ByteDance), as a systems credential (DRW, SpaceX, Praxis), and as a professional-practice credential (GE Appliances, Axon).

**At Optiver it is all three at once, and this is the role where it is worth the most.**

- **Systems:** production C++ in a dispatch layer, plus a compiler correctness bug in MLIR static analysis. That is real low-level engineering, which is what the onsite CS fundamentals round probes.
- **ML infrastructure:** Optiver's technology page names *"Machine Learning infrastructure: large-scale compute and ML workflows for systematic trading"* as a core pillar, and the firm has launched an AI gateway and an MCP hosting platform. PyTorch and Triton are literally the substrate of that pillar.
- **Professional practice:** Optiver maintains its own open-source contributions (the Postgres `timestamp9` type, `optiver-asyncpg`, `vulcan-py`, `opti-napalm`, a NOTIFY/LISTEN mechanism built on the write-ahead log). This is a firm that contributes upstream. A candidate who has landed code in PyTorch core speaks that language.

The **full technical variant** is used here (ATen, MLIR AxisInfo, FileCheck, BFloat16/Half, 900+ downstream projects), not the plain variant used for GE Appliances and Axon. The reader is a trading-firm engineer who will recognize every term, and stripping the jargon would throw away the credential.

---

## Change 5: The Education Line Answers the Eligibility Question in Zero Extra Vertical Space

> `Coursework: ... \hfill \textbf{Graduating May 2028; available May -- Aug. 2027}`

The JD carries a **hard, narrow eligibility gate**: *"On track to graduate between December 2027 to June 2028, with junior standing or higher."* May 2028 falls inside it. A recruiter screening for that constraint should not have to do arithmetic from the education date range.

This is the fifth consecutive realistic resume to use the `\hfill` trick on a coursework bullet, and the fifth time it answers a different requirement (Praxis: citizenship; GE: co-op availability and sponsorship; Roblox: on-site availability; Axon: 12-week availability; Optiver: graduation window). It costs zero lines.

**Coursework order changed from DRW.** Operating Systems and Computer Networks still lead, because the onsite CS fundamentals round covers OS internals, concurrency, and TCP versus UDP in trading contexts. **Databases was added**, which DRW's version omitted, because Postgres and Kafka are documented parts of Optiver's stack and the JD's "data rich landscapes" framing is not incidental.

**Skills changed from DRW too:** Rust was added (documented as emerging at Optiver for research tooling and service orchestration), Kafka and PostgreSQL were added, and `perf` was added alongside GDB. "Low-Latency Data Pipelines" and "Performance Profiling" replaced DRW's "OOP & Design Patterns."

---

## The Two Versions: What Actually Differs, and Which to Send

Alex asked for one resume with the arXiv paper and one without. Building both exposed a concrete tradeoff worth stating plainly.

### The mechanical difference

At one page, the paper is not free. Adding the `Independent Research` subheading costs about three lines, so the open-source block had to be **compressed from two bullets to one merged bullet** to keep the page. The nopapers version keeps the ATen bullet and the Triton bullet separate and fully detailed.

| | `_nopapers.tex` | `.tex` (with paper) |
|---|---|---|
| Section title | Open Source | Open Source & Research |
| PyTorch / Triton | **2 bullets**, full detail | **1 merged bullet**, compressed |
| arXiv preprint | absent | 1 bullet |
| Page count | 1 | 1 |

So the real question is not "does the paper add value." It is **"is the paper worth more than the second open-source bullet."**

### The recommendation: send the version with the paper

The cache-measurement paper is not, for this audience, an AI paper. **It is a black-box performance-measurement result**, and that is the most Optiver-flavored thing Alex has done.

Read the bullet as a trading-firm engineer would:

> "Recovered the internal cache-measurement block sizes of 4 key-value stores from black-box timing telemetry alone, correcting a widely cited figure by 8x and tracing one provider's run-to-run non-determinism to unpinned load balancing across replicas."

Strip the LLM context and what remains is: *he inferred a closed system's internal structure purely from timing observations, found the published number was wrong by 8x, and root-caused run-to-run variance to a load-balancing artifact.*

That is exactly the skill set of an engineer at a firm that:
- runs CI on bare metal because *"performance tests require predictable compute profiles"*
- built a custom observability layer over GitHub Actions to capture system-level metrics like queue times
- states that at its timescales *"measurement noise becomes a challenge"*
- has launched an **AI gateway** and an **MCP hosting platform**, meaning someone there is right now measuring exactly this behavior in production

The paper also does something the second open-source bullet cannot: it is **solo-authored independent work**, which speaks to the JD's *"entrepreneurial mindset"* and *"drive to solve tough problems"* far more directly than a second upstream patch does. And it gives the interviewer an obvious, distinctive question to open with, which is worth a great deal in a process where the deep project dive is a scored round.

The cost is real but small. Merging the open-source bullets loses some of the Triton specificity, but the merged bullet still names MLIR AxisInfo, the FileCheck test, and the ATen dispatch work. Nothing load-bearing disappears.

### When to send the nopapers version instead

Two cases, both narrow:

1. **If an application portal or a referrer asks for a strictly engineering-focused resume**, or the reader is known to be an ATS filtering on keywords rather than a human. The nopapers version is denser in hard technical terms per square inch.
2. **If Alex is not prepared to defend the paper in detail.** Anything on a resume at a firm that runs a deep project dive is an attack surface. If he cannot explain the measurement methodology, the confounders he ruled out, and why the widely cited figure was wrong, the paper becomes a liability rather than an asset. That is a preparation question, not a document question, and the answer should be to prepare rather than to cut.

The nopapers version is also the better base for **DRW-style hot-path roles** where ML infrastructure is irrelevant, so it is worth keeping regardless.

---

## Full Gap Analysis vs. the Full Ideal

| Gap | Status | Compensation on this resume |
|---|---|---|
| No trading-firm internship | **Unclosable for Summer 2027** | Acme Labs reframed as end-to-end platform ownership; three trading-domain projects |
| No exchange-side experience | **Unclosable** | Market data decoder plus capture pipeline covers the feed-consumption half |
| Not at a Chicago-area or Optiver-pipeline school | **Unclosable** | Nothing on the resume fixes this. The OA score does. |
| No undergraduate algorithmic trading coursework | **Unclosable at SU** | Self-study; a trading competition placement would substitute (see no-brainer doc) |
| No market-fluency credential | **Closable in one season** | Currently absent. This is the largest closable gap. |
| GPA 3.50 vs 3.94 | **Unclosable** | Low weight here. The OA dominates the funnel. |
| Tooling built for non-engineers | **Already closed** | Acme Labs bullet 2; validation framework project |
| End-to-end ownership through deployment | **Already closed** | Acme Labs bullet 1, written in Optiver's own vocabulary |
| High-throughput C++ data pipeline | **Already closed** | Crawler at 2M+ documents; capture pipeline |
| Low-latency systems depth | **Closed via projects** | Matching engine, ring buffers, p99 under 1 microsecond |
| ML and research infrastructure | **Closed and arguably won** | PyTorch and Triton are more real than any student project on the full ideal |
| Measurement rigor | **Closed via the paper** | The single strongest argument for the with-paper version |

---

## Structural Notes

**This is the first role in the project to ship two realistic variants of the same resume.** The variants are not "long and short." They encode a specific tradeoff (a solo research credential versus a second upstream contribution bullet) that only became visible once both were laid out at one page.

**This is the second trading-firm role, and the first to deliberately contradict the first one.** The DRW resume and the Optiver resume share two projects and the same underlying person, and disagree on emphasis, ordering, and what the open-source work is for. Both are honest. That divergence is the methodology working: the JD was read for what Optiver selects for in 2027, not for what trading firms selected for in general.

**The binding constraint here is not the resume.** Every other role in this project has been a document problem. Optiver's funnel puts a 3-to-4-hour aptitude assessment with negative marking between the application and any human, and there is an 8-month lockout on reapplying after taking it. The no-brainer document treats that as the main event, because it is.

---

## Sources

1. Optiver, Software Engineer Intern (Summer 2027 - Chicago) job posting: https://www.optiver.com/join-us/jobs/technology/chicago/software-engineer-intern-summer-2027-chicago/
2. The Pragmatic Engineer, "Software engineering at a proprietary trading company: Optiver": https://newsletter.pragmaticengineer.com/p/optiver
3. Optiver Technology overview: https://www.optiver.com/what-we-do/technology/
4. Optiver Chicago office: https://www.optiver.com/join-us/locations/chicago/
5. Optiver, "Turning ideas into reality: Chicago summer tech interns": https://optiver.com/working-at-optiver/career-hub/turning-ideas-into-reality-chicago-summer-tech-interns/
6. Optiver, "Learning by building: Inside Optiver's Software Engineering Internship": https://optiver.com/working-at-optiver/career-hub/learning-by-building-inside-optivers-software-engineering-internship/
7. Optiver, "Designing low latency C++ systems" (David Gross, CppCon): https://optiver.com/working-at-optiver/career-hub/designing-low-latency-cpp-systems/
8. TechPrep, "Optiver's Interview Process (2026)": https://www.techprep.app/blog/optiver-interview-process
9. LinkJob, "My 2026 Optiver HackerRank Questions & Firsthand Experience": https://www.linkjob.ai/interview-questions/optiver-hackerrank-questions/
10. InterviewQuery, Optiver Software Engineer Interview Guide 2026: https://www.interviewquery.com/interview-guides/optiver-software-engineer
