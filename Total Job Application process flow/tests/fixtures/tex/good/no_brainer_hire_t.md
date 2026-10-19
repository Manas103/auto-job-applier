# No-Brainer Hire: Optiver Software Engineer Intern (Summer 2027, Chicago)

What makes a candidate an automatic yes for this specific role, and what to do about the parts that are not on the resume.

---

## Read the Posting Correctly Before Anything Else

This is a trading firm, so every applicant assumes the same thing: nanoseconds, lock-free data structures, kernel bypass, C++ template wizardry. That profile is not wrong, but it is aimed at the Optiver of a decade ago and it is not what the published evidence describes.

### 1. Optiver has said out loud that latency is no longer the moat

From Optiver engineering coverage: *"Even ultra-low latency is no longer a competitive moat in itself"* and *"Optiver invests substantially more in building better models than lowering latencies."* Their technology page leads with an *"AI-first mindset in how we build, test, deploy, and run."* They have shipped an internal **AI gateway** and an **MCP hosting platform** so agents can reach internal systems.

The JD says the same thing in recruiting language: *"Optiver is building with cutting edge AI technology in the most competitive, data rich landscapes in the world."*

### 2. Most Optiver engineering is not on the hot path

About **950 engineers** out of ~2,200 employees, with **30 to 40 percent on platform teams**, flagged in the source as unusually high versus a typical tech company's 15 to 20 percent. Optiver executes 10M+ trades daily across 100 exchanges, processes petabytes daily, and prices 1M+ instruments. Most of the engineering is the machinery around the trade, not the trade itself.

### 3. The published intern projects are tooling, data, and testing infrastructure

Optiver publishes what interns actually built. This is the most useful page on their site:

| Office | Intern project |
|---|---|
| Chicago | Custom binary file format for exchange message processing (C, Python C extensions, Pandas); order-of-magnitude pipeline efficiency gain |
| Chicago | Financial instrument prioritization system for FPGA |
| Chicago | Redesigned software-to-FPGA communication interface; 88% less software wait time |
| Austin | Auto-trader testing framework |
| Amsterdam | Datacenter network simulator that prevented deployment incidents |
| Sydney | Alerting framework letting traders and researchers write custom validations in pricing models |
| Shanghai | Raw market data format support |
| Mumbai | Real-time liquidity visibility across currencies, stocks, options, ETFs, futures |

**Almost none of it is trading strategy.** It is capture formats, test frameworks, simulators, alerting, and visibility tooling built for traders and researchers to use. Chicago hosted **68 summer interns** across tech, trading, and research. Interns are expected to push to production and ship releases as early as **weeks 2 to 3**.

### 4. The online assessment is the real filter, and it is an aptitude test

This is the fact that should reorganize the entire preparation plan. The Optiver technology OA runs **3 to 4 hours** in four parts:

| Section | Format |
|---|---|
| **NumberLogic** | Number sequences, ~25 minutes, escalating to fractions, large numbers, alternating rules |
| **Beat The Odds** | ~30 probability questions, **90 seconds each**, 30 minutes total, no pauses |
| **Zap-N** | Up to 9 cognitive/neuro games, 2 to 15 minutes each, ~60 minutes total |
| **Coding** | Standard coding plus CS fundamentals multiple choice |

**Sections carry negative marking.** Wrong answers cost points, so blind guessing is worse than skipping.

Two consequences almost nobody plans for:
- **Most of this is not LeetCode.** Grinding graph problems will not move the NumberLogic or Beat The Odds score at all.
- **There is an 8-month lockout.** The JD states that anyone who has completed an OA or interviewed for any Optiver technology graduate or internship role at **any Optiver location** in the past 8 months cannot reapply. One unprepared attempt burns the cycle. Do not take this OA cold.

### 5. "Why Optiver" is scored, and the wrong answer is "I'm interested in finance"

Interview guides converge that the recruiter screen rejects candidates whose stated interest is in trading or finance generically, rather than in **building high-performance infrastructure**. The JD's own phrasing is *"curious about how tech powers the financial industry."* Tech powering finance. Not finance.

---

## The Immediate-Yes Checklist

A candidate is an automatic yes when all of these are true:

- [ ] **Eligible on the narrow window.** Graduating between **December 2027 and June 2028**, junior standing or higher. This is a hard gate.
- [ ] **Clears the OA.** Nothing else on this list matters if this fails.
- [ ] **Deep C++ or Python fluency, defensible at the internals level.** Not "familiar with." Interviewers probe memory management, threading, the GIL, and performance tradeoffs.
- [ ] **One system owned end to end**, from design through deployment and monitoring, with the candidate able to name what broke and how they found out.
- [ ] **One thing built that a non-engineer used.** This is the sleeper requirement and it maps to almost every published Optiver intern project.
- [ ] **Evidence of measurement discipline.** A real benchmark, a real profile, a real before-and-after. Numbers that came from measuring rather than estimating.
- [ ] **A specific, infrastructure-flavored answer to "Why Optiver."**
- [ ] **Can explain a technical project to a non-technical person**, on demand, in the interview.

---

## Table Stakes vs. Differentiators

### Table stakes (missing any of these ends it)

| Requirement | Evidence it is table stakes |
|---|---|
| C++, Python, or Java at real depth | Named in the JD; interviewers probe language internals |
| CS fundamentals: OS, concurrency, memory model, cache locality, networking | The onsite has a no-coding theory round on exactly this |
| Clean, bug-free implementation under time pressure | Guides note the emphasis is on clean solutions over algorithmic tricks; DSA is medium difficulty and implementation-focused |
| TCP vs UDP in a trading context | Explicitly listed as an onsite topic |
| Ability to reason about latency, throughput, memory with back-of-envelope math | Listed as a system design expectation |
| Graduation in the Dec 2027 to Jun 2028 window | Hard eligibility gate in the JD |

### Differentiators (these are what get the offer)

| Differentiator | Why it works here specifically |
|---|---|
| **Tooling built for non-engineers to configure themselves** | Matches the archetype of Optiver's real intern projects almost exactly |
| **A data capture or replay format with real numbers** | Mirrors the actual Chicago intern project; Optiver contributed a nanosecond-precision Postgres `timestamp9` type, which tells you how much they care |
| **Reproducible, replay-based benchmarking** | Optiver runs CI on bare metal because performance tests need predictable compute profiles |
| **ML or research infrastructure** | Optiver names ML infrastructure as a core technology pillar and has shipped an AI gateway |
| **Upstream open-source contributions** | Optiver contributes upstream itself: `timestamp9`, `optiver-asyncpg`, `vulcan-py`, `opti-napalm`, a WAL-based NOTIFY/LISTEN mechanism |
| **A market-fluency credential** | Trading competition placement, or a market making case study you can actually explain |
| **Rust** | Documented as emerging at Optiver for research tooling and service orchestration; almost no applicant mentions it |

---

## The Four Highest-Leverage Projects to Build

Ordered by return per hour for this role specifically.

### 1. A validation and alerting framework that non-engineers configure (highest priority)

Build a service that lets someone who is not an engineer define guardrails on a live system's output, in a small expression language or config format, and get alerted with the triggering input attached when one fires.

**Why it is first:** it is a near-direct analogue of a published Optiver intern project (the Sydney alerting framework for pricing models), and it proves the rarest thing on a student resume, which is that you have built a product for another human rather than a script for yourself.

**Do not skip the boring parts.** The alert has to carry the input snapshot. The expression language has to reject bad input with a useful error. Those details are what make it real in a project dive.

### 2. A tick capture format with a Python extension over a C++ layout

Design a column-oriented binary format for high-rate message capture with nanosecond timestamps, and expose it to Python with a zero-copy extension. Report storage ratio and query time before and after.

**Why:** this is the actual Chicago intern project. It also sits exactly on the C++/Python boundary where a lot of real Optiver engineering happens, and that boundary is much less crowded than the matching-engine space.

### 3. A market data decoder and full-depth order book

Parse a multicast feed format into full-depth books at high message rates. ITCH-style public specifications are freely available.

**Why:** table stakes for any trading-firm application, and it is the shared vocabulary of every technical conversation you will have in this process. Build it, but do not expect it to differentiate you.

### 4. A lock-free matching engine

Price-time priority, atomics, ring buffer, p99 measured properly.

**Why last:** every serious applicant has one. It proves depth and it will be discussed, but it is the price of entry rather than the reason you get hired. Building it first and stopping there is the most common mistake in this applicant pool.

---

## Coursework and Certifications

**Coursework priority, highest first:**
1. Operating Systems (concurrency primitives, memory model, scheduling)
2. Computer Networks (TCP vs UDP, multicast, the actual stack)
3. Computer Architecture (cache hierarchy, data layout, why alignment matters)
4. Databases (Postgres and Kafka are in the stack; "data rich landscapes" is not filler)
5. Probability and Statistics (**this one is underrated**: it maps directly to the Beat The Odds section)
6. Parallel or Distributed Computing

**Certifications: skip entirely.** There is no certification that Optiver values, and no evidence anywhere in the research that certifications feature in this funnel. Time spent on one is time not spent on probability drills.

**The exception worth the time:** a market microstructure or algorithmic trading course, if your school offers one. If it does not, read a market making primer well enough to explain adverse selection and inventory risk in plain English. That is enough to be credible without pretending to be a trader.

---

## Gap Analysis for Alex

| Gap | Closable? | What to do |
|---|---|---|
| **OA aptitude, especially probability under 90-second pressure** | **Yes, and this is the top priority by a wide margin** | Drill timed probability. Practice number sequences. Take a full timed mock before touching the real thing. The 8-month lockout means one attempt per cycle. |
| **No trading-firm internship** | **No** for Summer 2027 | Acme Labs reframes honestly as end-to-end platform ownership, which is what Optiver's culture actually names |
| **No exchange-side experience** | **No** | Market data decoder plus capture pipeline covers the consumption half credibly |
| **Not at a Chicago-pipeline school** | **No** | Attend an Optiver virtual event; apply early in the cycle. Neither is worth much, but they are free. |
| **No market-fluency credential** | **Yes, in one season. This is the largest closable gap.** | Enter a trading competition. The UChicago Trading Competition is national and open to teams from other schools; there are others. A placement is a resume line and, more usefully, a story. |
| **No algorithmic trading coursework at SU** | **Partially** | Self-study plus a competition substitutes for the line |
| **Tooling for non-engineers** | **Already closed** | Acme Labs replay harness and ranking dashboard |
| **End-to-end ownership** | **Already closed** | Five-month Acme Labs rotation through deployment and monitoring |
| **ML and research infrastructure** | **Closed and won** | PyTorch and Triton contributions are more real than most student projects |
| **Measurement rigor** | **Closed** | The cache-measurement paper is a black-box measurement result, which is the most Optiver-shaped thing in the portfolio |
| **GPA 3.50** | **No** | Low weight in this funnel. The OA dominates. |

---

## The One Thing Most Applicants Get Wrong Here

**They prepare for the wrong test.**

Every applicant to a Chicago prop shop grinds LeetCode and builds a matching engine. Both are useful. Neither touches three of the four sections of the assessment that actually decides whether a human ever reads the resume. Number sequences, 90-second probability questions, and cognitive games are not improved by graph algorithms, and negative marking punishes the guess-everything strategy that works on ordinary online assessments.

The second version of the same mistake happens at the recruiter screen. Candidates who spent six months on trading-systems projects answer "why Optiver" with enthusiasm about markets, and get rejected for it, because the documented rejection pattern is exactly that: generic interest in finance rather than specific interest in building high-performance infrastructure.

**The correct preparation split for this role is roughly:** timed probability and sequences first, C++ or Python internals and CS fundamentals second, project depth third, LeetCode fourth. That is close to the inverse of what most applicants do.

---

## Interview Prep Angles

### 1. Drill the OA sections separately, and treat negative marking as a strategy problem

Beat The Odds gives 90 seconds per question across roughly 30 questions with no pause. That is a pacing problem as much as a math problem. Practice with a timer, and practice **deciding to skip**, because a wrong answer costs more than a blank one. NumberLogic escalates into fractions and alternating rules; the skill is pattern-recognition speed, and it is trainable.

### 2. Have the "Why Optiver" answer written and specific

The answer should be about infrastructure, and it should cite something real. Usable material, all from Optiver's own published record: the platform-team ratio, the no-QA build-and-own model, hundreds of production changes daily, running CI on bare metal because performance tests need predictable compute profiles, contributing `timestamp9` to Postgres, the shift from latency to models. Any one of those, connected to something Alex has actually built, is a better answer than 95 percent of the pool will give.

### 3. Prepare the project dive at two levels for every project

Interviewers ask for both the technical explanation and the non-technical one. For each of the three projects, be able to answer: what broke, how you found out, what you measured, what you would do differently. For the matching engine specifically, expect pressure on how p99 was measured, not just what it was.

### 4. Rehearse the CS fundamentals theory round out loud

The onsite includes a no-coding round on concurrency primitives, memory models, cache locality, OS internals, data layout, and TCP versus UDP in trading contexts. This is a talking round, not a coding round, and it rewards people who have explained these things before. The Acme Labs crawler (thread pools, connection handling) and the matching engine (atomics, memory ordering, false sharing) are both good anchors.

### 5. Be ready for low-level design

Reported prompts include order management systems, task schedulers, and register systems, scored on class responsibilities, encapsulation, maintainability, and edge cases rather than cleverness. Practice writing a clean, small, well-factored system in 45 minutes without over-engineering it.

### 6. Know enough market structure to hold a conversation, and no more

Be able to explain what a market maker does, what a bid-ask spread is, what adverse selection means, and why inventory risk matters. Do not pretend to be a trader. The JD asks for curiosity about how tech powers the industry, so the credible posture is a strong engineer who has taken the time to understand the business, which is exactly what Alex is.

### 7. Two logistics notes worth planning around

- **The 8-month lockout is firm-wide, not office-wide.** It applies to any Optiver technology graduate or internship role at any location. The Austin posting for the same role is explicitly cross-linked in the JD, so applying to both is not a way around it. Choose the office deliberately.
- **Optiver supports US immigration sponsorship for this role**, per the JD. Unlike Praxis and GE Appliances, work authorization is not a filter here and does not need to be addressed on the resume.

---

## Realistic Assessment

Alex is a stronger candidate for Optiver than the raw credentials suggest, and weaker than the resume can express, for the same reason: this is the one role in the project where the document is not the binding constraint.

**Where he is genuinely strong.** The three axes Optiver's own intern projects actually live on are tooling that non-engineers use, high-throughput data pipeline work, and end-to-end ownership through deployment. He has real evidence for all three, plus production open-source contributions in ML infrastructure that most applicants to this role cannot match, plus a solo-authored black-box measurement result that reads as a culture match rather than a curiosity.

**Where he is genuinely behind.** No trading-firm internship, no school pipeline into Chicago prop shops, and no market-fluency credential. The first two are unclosable for this cycle. The third is closable in one season and is the highest-return item on the list after OA preparation.

**What actually decides this.** A 3-to-4-hour aptitude assessment with negative marking, taken once, with an 8-month lockout on a retry. Everything in this folder gets the resume past the screen. The OA is the part that has to be trained for, and it should be treated as the main event rather than a formality.

---

## Sources

1. Optiver, Software Engineer Intern (Summer 2027 - Chicago) job posting: https://www.optiver.com/join-us/jobs/technology/chicago/software-engineer-intern-summer-2027-chicago/
2. The Pragmatic Engineer, "Software engineering at a proprietary trading company: Optiver": https://newsletter.pragmaticengineer.com/p/optiver
3. Optiver Technology overview: https://www.optiver.com/what-we-do/technology/
4. Optiver Chicago office: https://www.optiver.com/join-us/locations/chicago/
5. Optiver, "Turning ideas into reality: Chicago summer tech interns": https://optiver.com/working-at-optiver/career-hub/turning-ideas-into-reality-chicago-summer-tech-interns/
6. Optiver, "Learning by building: Inside Optiver's Software Engineering Internship": https://optiver.com/working-at-optiver/career-hub/learning-by-building-inside-optivers-software-engineering-internship/
7. Optiver, Technology Internships: https://www.optiver.com/join-us/students/internships/technology/
8. Optiver, "Designing low latency C++ systems" (David Gross, CppCon): https://optiver.com/working-at-optiver/career-hub/designing-low-latency-cpp-systems/
9. TechPrep, "Optiver's Interview Process (2026)": https://www.techprep.app/blog/optiver-interview-process
10. LinkJob, "My 2026 Optiver HackerRank Questions & Firsthand Experience" (OA section structure and negative marking): https://www.linkjob.ai/interview-questions/optiver-hackerrank-questions/
11. InterviewQuery, Optiver Software Engineer Interview Guide 2026: https://www.interviewquery.com/interview-guides/optiver-software-engineer
12. Glassdoor, Optiver Software Engineer Intern interview questions: https://www.glassdoor.com/Interview/Optiver-Software-Engineer-Intern-Interview-Questions-EI_IE243355.0,7_KO8,32.htm
13. UChicago Midwest Trading Competition: https://midwesttrading.uchicago.edu/
