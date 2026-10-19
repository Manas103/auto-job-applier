# Full Ideal Candidate Breakdown: Optiver Software Engineer Intern (Summer 2027, Chicago)

**Candidate:** Mira Kaplan, University of Chicago, B.S. Computer Science, GPA 3.94, graduating June 2028
**File:** `ideal_candidate_full_optiver.tex`

---

## First: What This Role Actually Is

This is the second trading-firm role in the project, after DRW. The temptation is to refill the DRW template: nanosecond order books, kernel bypass, lock-free everything, UIUC. That would be a mistake, and the research says so clearly. Five facts decode this posting.

### 1. Optiver has publicly stated that latency alone is no longer its moat

From the Pragmatic Engineer's deep dive on Optiver engineering: *"Even ultra-low latency is no longer a competitive moat in itself"* and *"Optiver invests substantially more in building better models than lowering latencies."* Optiver's own technology page leads with **"an AI-first mindset in how we build, test, deploy, and run."* The company has launched an internal **AI gateway** and an **MCP hosting platform** so agents can reach internal systems.

The JD reflects this and the DRW posting did not: *"Optiver is building with cutting edge AI technology in the most competitive, data rich landscapes in the world."*

A resume built entirely out of sub-microsecond matching engines answers the Optiver of 2018. The ideal 2027 candidate proves systems depth **and** shows they can build the layer above it.

### 2. Roughly 30 to 40 percent of Optiver engineering is platform work

Optiver has about **950 engineers** out of ~2,200 employees, and 30 to 40 percent of engineering sits on platform teams, which the source explicitly flags as unusually high (typical tech companies run 15 to 20 percent). The odds that a given SWE intern lands on a hot-path trading team are therefore well under half.

### 3. The published intern projects are almost all tooling, data, and testing infrastructure

This is the single most useful research finding for this role. Optiver publishes what its interns actually built:

| Office | Project |
|---|---|
| Chicago | Custom binary file format optimizing exchange message processing (C, Python C extensions, Pandas); order-of-magnitude pipeline efficiency gain |
| Chicago | Financial instrument prioritization system for FPGA |
| Austin | Auto-trader testing framework |
| Amsterdam | Datacenter network simulator that prevented deployment incidents |
| Sydney | Alerting framework letting traders and researchers write custom validations inside pricing models |
| Shanghai | Raw market data format support |
| Mumbai | Real-time liquidity visibility system across currencies, stocks, options, ETFs, futures |

Read the list again. Almost none of it is trading strategy or hot-path execution. It is **capture formats, testing harnesses, simulators, alerting frameworks, and visibility tooling built for traders and researchers to use**. The Chicago office hosted 68 summer interns across tech, trading, and research, and tech interns are expected to push to production, shipping releases as early as weeks 2 and 3.

The full ideal resume is designed against this list, not against a fantasy of what a trading intern does.

### 4. The online assessment, not the resume, is the real filter

The Optiver technology OA runs 3 to 4 hours in four parts: **NumberLogic** (number sequences, ~25 minutes), **Beat The Odds** (~30 probability questions at 90 seconds each), **Zap-N** (up to 9 cognitive games, ~60 minutes), and a coding section. Sections carry **negative marking**.

This is an aptitude gate, not a knowledge gate. No resume line improves an OA score. What the resume must do is be strong enough to get sent the OA and strong enough to survive the deep project dive afterward. That changes what the resume is optimizing for: it needs **depth that survives interrogation**, not breadth that impresses a skimmer.

### 5. "Why Optiver" is a scored question, and generic finance interest is a documented rejection point

Guides on the process converge that the recruiter screen rejects candidates whose interest is in *trading* or *finance* generically rather than in **building high-performance infrastructure**. The JD backs this: the closing bullets ask for someone *"curious about how tech powers the financial industry"* and *"comfortable working with developers, traders and business teams."* The word order matters. Tech powering finance, not finance.

---

## School: University of Chicago

### Why not UIUC again

UIUC was the correct answer for DRW and is a genuine Optiver feeder. It is deliberately not reused. The project's rule is that the school choice must be re-derived from the specific role, and reusing it would hide the fact that Optiver and DRW select for different things.

### The Chicago-area pipeline

Optiver's Chicago office recruits from **Northwestern, University of Chicago, UIUC, and Illinois Institute of Technology**. Optiver also runs dedicated recruitment event pages for **University of Michigan** and **Carnegie Mellon**. With UIUC used for DRW and CMU used for ByteDance, the realistic field is UChicago, Northwestern, and Michigan.

### Why UChicago wins for this specific role

**It is the only school where the market-fluency and engineering-depth requirements are satisfiable in the same degree.** UChicago CS undergraduates can take Booth courses during Summer Quarter, and the Financial Mathematics program's *Quantitative Portfolio Management and Algorithmic Trading* course is explicitly open to undergraduates. That is why the coursework line names it. A Michigan CS student can be an excellent engineer; they cannot easily take a graduate-level algorithmic trading course as a junior.

**It sits in the city the job is in.** Optiver's US headquarters is at 130 East Randolph, Chicago, in One Prudential Plaza, roughly 100,000 square feet with room for close to 600 staff, up from about 400. Chicago has been US HQ since 2003, two years after the office opened, and is the center of the US options business and technology team. A UChicago student can attend Optiver's Chicago events in person, which is a real and unglamorous advantage.

**It hosts the competition that supplies this resume's differentiator.** See below.

### Why not Northwestern

Northwestern is a legitimate feeder and is closer to a conventional engineering school than UChicago. It loses on one point: it has no undergraduate-accessible equivalent of the Booth and Financial Mathematics coursework, so the market-fluency signal would have to come entirely from extracurriculars. UChicago gets it from both.

### Why the graduation date is June 2028

The JD requires graduation **between December 2027 and June 2028**. UChicago runs on quarters and graduates in June, which puts this candidate at the exact final day of the eligible window. It is a small thing that demonstrates the resume was read against the actual constraint rather than assumed.

---

## Experience

### IMC Trading, Software Engineer Intern, Options Trading Technology (Chicago, Jun 2026 to Aug 2026)

**Why IMC and not Citadel Securities or Jump.** Jump was used for DRW. Citadel Securities is the obvious prestige answer, and obvious is not the same as best-fit. IMC is the closest structural analogue to Optiver in existence: a Dutch-founded options market maker with a major Chicago presence and the same business model of providing liquidity in listed derivatives. A candidate coming from IMC has already worked inside the exact problem shape Optiver works in, which is a sharper signal than a bigger name from a different business model.

**Bullet 1, the validation framework.** This is deliberately modeled on the archetype of Optiver's own published intern projects, specifically the Sydney *"alerting framework for pricing models"* that let traders and researchers write custom validations. Building it at a competitor is the strongest possible proof that this candidate will be productive on day one. The details chosen carry specific weight:
- *"a small expression language"* shows the candidate built a **product for other people**, not a script for themselves.
- *"shipped to production in week three"* matches Optiver's stated expectation that interns ship in weeks 2 to 3.
- *"adopted by two desks"* is trader adoption, which is the currency at Optiver.

**Bullet 2, the latency work.** Systems depth still has to be proven, so this bullet does the hot-path work. Two choices matter. First, the fix is a **concurrency design change** (shared mutex to per-instrument single-producer ring), which is exactly the CS fundamentals territory the onsite theory round covers: concurrency primitives, memory models, cache locality. Second, it is *"measured against a replayed 90-minute captured session rather than synthetic load."* Optiver runs CI on bare metal with custom hardware precisely because performance measurement is hard there, and their own writeups note that at sub-nanosecond scales measurement noise becomes the challenge. Showing you know the difference between a real benchmark and a fake one is a differentiator at this firm specifically.

### Cboe Global Markets, Software Engineer Intern, Market Data (Chicago, Jun 2025 to Aug 2025)

**Why an exchange for the sophomore summer.** This is the design decision that makes this profile better than a two-prop-shop profile. Optiver Chicago is *"the center of the US options business."* Cboe is where US options trade and is headquartered in Chicago. A candidate who has worked on the exchange side and the market-maker side understands the full loop: how a feed is published and how it is consumed. In an interview, that produces answers nobody else can give.

**Bullet 1** is the capture and replay harness, chosen because Optiver's Chicago intern project list contains a nearly identical problem (custom binary format for exchange message processing) and because *"replaying any window bit for bit"* is the foundation of every serious trading-systems test strategy.

**Bullet 2** is deliberately unglamorous: turning a recurring manual incident into an automated check, done *"with the market operations team."* Optiver has **no dedicated QA team** and runs a build-and-own culture where engineers own design, build, test, deploy, monitor, and support. This bullet proves the candidate does the boring ownership work and talks to non-engineers, which is what the JD's "collaborative teammate comfortable working with developers, traders and business teams" is actually asking about.

---

## Projects

Three projects, each covering a different axis Optiver scores on.

### 1. Column-Oriented Binary Format for Tick Data Capture (C++, Python C API, Feb 2026)

This is the closest analogue on the resume to a real Optiver Chicago intern project: Sabrina's custom binary file format work using C, Python C extensions, and Pandas, which improved pipeline efficiency by an order of magnitude. Optiver processes **petabytes of data daily** and prices **1M+ financial instruments**.

The **Python C API** detail is the point. Optiver's stack is C++ for the fast path and Python for research and tooling, and the boundary between them is where a lot of real Optiver engineering happens. A candidate who has written a zero-copy Python extension over a C++ data layout has done the actual job. "11x storage, 40 minutes to under 3" is the kind of concrete before-and-after that survives a project dive.

### 2. Distributed Backtest Scheduler (Rust, Python, Kubernetes, Apr 2026)

This covers the research-infrastructure axis, which is where Optiver says its investment is going. Optiver's technology page names *"research infrastructure enabling fast, reproducible research cycles"* as a core capability, and Rust is documented as emerging at Optiver in research tooling and service orchestration. Kubernetes is in the stack.

**Deterministic replay and content-addressed result caching** are the two words that matter. They say the candidate thinks about reproducibility, which is the central problem in research infrastructure and the thing that distinguishes it from ordinary batch processing.

### 3. Lock-Free Limit Order Book and Matching Engine (C++20, Sep 2025)

The table-stakes trading systems project. Every serious candidate for this role has one. It is placed **third**, not first, which is itself the argument of this resume: at Optiver in 2027, the matching engine is the price of entry, not the differentiator.

*"Cache-aligned intrusive book"* and *"single-producer ring buffer"* are chosen over generic "lock-free data structures" because they name specific design decisions an interviewer can push on, and the onsite explicitly tests cache locality and data layout.

---

## Leadership and Involvement: UChicago Trading Competition, Case Author and Technology Lead

This is the entry that makes the resume hard to beat, and it is the reason UChicago was chosen.

The **UChicago Trading Competition** is real and is described as among the nation's premier university algorithmic trading competitions, running cases on market making, options trading, and time series analysis, with sponsor firms sending engineers and recruiters. The 14th annual edition ran April 10 to 11, 2026.

Most strong candidates **compete** in it. This candidate **built it**: wrote the market making case and built the simulated exchange that runs it, matching 300+ competitor algorithms over two days, and coordinated engineers from four sponsoring prop firms as case reviewers.

Why this is the right differentiator for Optiver specifically, item by item against the JD's "Who you are" list:

| JD requirement | What this entry proves |
|---|---|
| "Driven by an entrepreneurial mindset" | Built and ran a thing that did not exist, at scale, with external sponsors |
| "Curious about how tech powers the financial industry" | Had to understand market making well enough to author a case on it |
| "Collaborative teammate comfortable working with developers, traders and business teams" | Coordinated professional prop-firm engineers as reviewers |
| "Passionate for writing clean code, well-architected systems" | A simulated exchange matching 300+ algorithms live has to actually work |
| "Strong engineering instincts" | Building the exchange is a real distributed matching problem |

It also does something no project can: it demonstrates the candidate can **explain trading concepts to other people**, which is the skill the JD's collaboration bullet and the interview's "explain your project in technical and non-technical terms" question are both probing.

Like the Virginia Tech Rescue Squad entry on the Axon full ideal, this is a credential that cannot be manufactured in a weekend. That is precisely why it belongs on a full ideal.

---

## Skills

| Line | Rationale |
|---|---|
| **Languages:** C++ (17/20), Python, Java, Rust, C#, SQL | Exactly Optiver's documented stack. C++ for low latency, Python for modeling and tooling, Rust emerging in research tooling and orchestration, C# for trader-facing GUIs. Java is on the list because the JD names it. |
| **Systems:** Lock-Free Concurrency, Cache-Aware Data Layout, UDP Multicast & TCP, Low-Latency Data Pipelines, Distributed Systems | Maps to the onsite CS fundamentals round: concurrency primitives, memory models and cache locality, TCP vs UDP in trading contexts, data layout. |
| **Tools:** Linux, GDB, perf, CMake, Kafka, PostgreSQL, Kubernetes, Docker, Git | Kafka, Postgres, and Kubernetes are documented parts of Optiver's infrastructure. Custom Linux kernels are in use, so Linux fluency is assumed. `perf` is there because measurement is the culture. |

**Deliberately excluded:** VHDL and SystemVerilog. Optiver uses them, but FPGA development is a **separate posting** (Software Engineer, Hardware Development). Claiming FPGA skills on a general SWE intern application signals the candidate did not read which role they applied to. Databricks was also excluded as too niche to spend a word on.

---

## Gap Analysis: Full Ideal vs. Alex

| Signal | Full ideal | Alex | Closable? |
|---|---|---|---|
| School with in-city Optiver pipeline | UChicago | SU | **No.** Compensate with the OA score and depth. |
| Undergraduate algorithmic trading coursework | Booth / FinMath course | Not offered at SU | **No**, but self-study plus a competition placement substitutes. |
| Trading-firm internship | IMC options tech | Acme Labs (search platform) | **No** for Summer 2027. Acme Labs reframes well as end-to-end platform ownership. |
| Exchange-side internship | Cboe market data | None | **No.** |
| Tooling built for non-engineers | Validation framework at IMC | **Yes**, the Acme Labs replay harness and ranking dashboard | **Already closed.** |
| End-to-end ownership through deployment | Explicit at IMC | **Yes**, Acme Labs 5-month rotation | **Already closed.** |
| High-throughput data pipeline in C++ | Tick capture format | **Yes**, crawler at 2M+ documents | **Already closed.** |
| Low-latency C++ systems depth | Matching engine, ring buffers | **Closable**, project already built | **Closed via projects.** |
| Research and ML infrastructure | Backtest scheduler | **Yes**, and stronger: PyTorch and Triton contributions are real production ML infrastructure | **Closed, and arguably won.** |
| Measurement rigor as a stated value | Replay-based benchmarking | **Yes**, the cache-measurement paper is a black-box measurement result | **Closed via the paper.** |
| Market-fluency credential | Authored a trading competition case | None | **Closable in one season.** See the no-brainer doc. |
| GPA | 3.94 | 3.50 | **No**, and it does not matter much here. The OA dominates. |

**The shape of this gap is unusual and worth naming.** On DRW, the gap was systems depth and prestige. Here, most of what separates Alex from the full ideal is **credential access** (school, trading internship, in-city presence), while the substantive engineering axes that Optiver's own intern projects live on (tooling for non-engineers, data pipelines, end-to-end ownership, ML infrastructure, measurement rigor) are already closed or won outright.

The binding constraint for Alex at Optiver is not the resume. It is the online assessment.

---

## Sources

1. https://example.com/only-one
