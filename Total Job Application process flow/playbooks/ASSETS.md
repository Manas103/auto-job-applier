# ASSETS: Alex Doe, fixed constraints and asset inventory

EXAMPLE FILE. Alex Doe is a fictional applicant. Replace every fact below with your own before running the pipeline; every stage treats this file as the single source of truth about the applicant. When a fact changes (a new internship, a new repo, a paper), edit this file and nothing else.

Writing rule for this file and everything derived from it: no em dashes anywhere. Use commas, periods, or restructure.

## 1. Fixed facts (identical on every resume)

| Field | Value |
|---|---|
| Name | Alex Doe |
| Email | alex.doe@example.com |
| Phone | +1-555-010-0100 |
| LinkedIn | linkedin.com/in/alex-doe |
| School | State University, College Town, IL |
| Degree | Bachelor of Science in Computer Science, GPA 3.50, Aug. 2024 to May 2028 |
| Work authorization | U.S. permanent resident (green card). Authorized to work without sponsorship. Not a U.S. citizen; roles that require U.S. citizenship are screened out. A posting that admits permanent residents alongside citizens is fine. |
| Location | U.S. roles only (including U.S. remote). |
| Target | 2027 internships and co-ops: Summer 2027 (May to Aug. 2027), Fall 2027, Spring 2027, and Winter 2027 co-ops. The availability line states the posting's own term. |

Consequences of the authorization fact (edit to match yours):
- Roles that require U.S. citizenship are out of scope. Never write "U.S. Citizen" on a resume.
- When a posting cares about authorization, the honest logistics tag is: `Authorized to work in the U.S. without sponsorship`.
- ITAR "U.S. person" requirements are satisfied by permanent residency.

## 2. Experience assets

### 2a. Acme Labs, Austin, TX. Software Engineer Intern, Feb. 2026 to Jun. 2026

The internship has ended. Never write "Present". Never change the dates.

Underlying truth (example): built an internal search service end to end and presented it to leadership. Components that exist and can be claimed individually or together:
- Crawler and ingestion pipeline over concurrent HTTP connections, 1M+ documents ingested
- Inverted index plus vector retrieval, hybrid lexical and embedding ranking
- Query service with a measured p99 latency target and a load test that proves it
- A regression test suite and a dashboard for non-technical stakeholders

The description is deliberately flexible: reframe around whichever slice matches the target role, as long as every underlying fact stays true.

### 2b. State University, Generative AI Lab. Undergraduate Research Assistant, Jan. 2025 to May 2025

Example: evaluated retrieval-augmented generation pipelines; wrote the evaluation harness and reported accuracy against a fixed benchmark.

### 2c. State University, Vision Lab. Undergraduate Research Assistant, Jan. 2025 to May 2025

Example: data labeling tooling and a small image-classification baseline. Include only when the role rewards it; the include or exclude decision is argued in each breakdown.

## 3. Open source assets

### 3a. OpenProjectA (Example Foundation). Open Source Contributor, Feb. 2025 to Present

Example: merged pull requests fixing a numerical edge case and adding tests.

### 3b. OpenProjectB (Example Foundation). Open Source Contributor, May 2026 to Present

Example: a documented performance fix with a benchmark.

Space trick: both may be merged into one subheading "OpenProjectA and OpenProjectB" when space is tight.

## 4. Research assets

None in this example. List papers or preprints here with their exact titles and dates; real artifacts keep their real dates on every resume.

## 5. Base-resume projects

See `playbooks/PORTFOLIO.md` for the repos that exist. Never claim a project that is not built.

## 6. Education line patterns

`State University, College Town, IL`, `Bachelor of Science in Computer Science; GPA: 3.50`, `Aug. 2024 -- May 2028`. The coursework line is chosen per role from courses actually taken.

## 7. Skills line rules

Every skill listed must be used in an experience, project or open-source item on the same resume.

## 8. Project portfolio (moved)

The project table lives in `playbooks/PORTFOLIO.md`; `playbooks/LIBRARY.md` is a generated one-line-per-repo index of it.
