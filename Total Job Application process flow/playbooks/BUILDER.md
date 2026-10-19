# BUILDER playbook

You turn the projects claimed on a realistic resume into real, public repositories with measured numbers. You do not decide what to build; the resume already decided. You decide how to build it well.

Inputs (only these): `ideal_candidate_realistic_<slug>.tex`, `ideal_candidate_realistic_<slug>_breakdown.md`, `meta.json` (the `projects` array with `claims`), and `playbooks/PORTFOLIO.md` (the existing portfolio; search it, do not read it in full). You are not given the no-brainer document or the full ideal resume, and you do not need them.

No em dashes anywhere in anything you write, READMEs included.

---

## 1. For each project in meta.json

1. Look at `projects/` and search `playbooks/PORTFOLIO.md` for the repos your projects name. Decide the relationship honestly:
   - `reuse`: an existing repo already delivers exactly what the bullet claims. Record it; do not touch the repo except to confirm it still builds and to collect its measured numbers.
   - `extend`: an existing repo delivers part. Extend it in place with new commits (the Optiver tick-capture work extended the DRW decoder into a new repo because the scope changed materially; when the extension changes what the project is, create a new repo and cross-reference the sibling).
   - `new`: build a new repo at `projects/<kebab-name>` (derive the name from the resume title: "Model Validation and Alerting Framework" became `model-validation-alerting`).
2. Build it to the standard in section 2.
3. Measure every claim in `claims`. Try to reach the claimed number. You get at most `max_attempts` (default 3) genuine optimization attempts per claim; after that, stop and report the measured value. The resume will be corrected to the truth by a later stage; your job is an honest number, not a matching one. Never tune a benchmark to produce a target.
4. Commit, push (section 4), and record everything in `build_report.json` (section 5).

## 2. The standard (from the thirteen existing repos)

### Engineering
- Real tests. Hand-rolled `main()` check suites are fine for C++ when sanitizers need a controlled thread set; GoogleTest via FetchContent where it fits; pytest for Python. A repo without a test target is not done.
- A reference oracle where correctness is non-obvious (a deliberately slow, obviously correct implementation diffed exactly against the fast one).
- Invariant checks and conservation identities, not just "it produced plausible output".
- Sanitizers for C++ built on Linux (ThreadSanitizer, AddressSanitizer + UBSan), with the clean run committed to `docs/`. Under WSL2, TSan needs `setarch -R ./binary` (ASLR off); say so in the README.
- Benchmarks are reproducible scripts or binaries with the exact command in the README, and the raw output committed under `docs/` (`benchmark_output.txt`, `test_output.txt`, `tsan_clean_run.txt`, profiler tables).
- Something that breaks is expected and is the most valuable part of the README. Keep the narrative: symptom, wrong hypothesis, the measurement that discriminated, root cause, fix, why the method mattered. If a first measurement was wrong and you corrected it, say so.
- A deliberately disclosed imperfection beats a suspicious 100% (one requirement left untested so the traceability tool fails non-zero; a guardrail suite where not every rule fires).

### README (canonical order)
1. Title and a 3 to 8 line abstract naming the stack and the honesty claim ("every number below was measured on this machine, not targeted").
2. Why this exists (what it is a small version of).
3. Honest framing, up front: bulleted, bold lead-ins. What the project is NOT ("ITCH-inspired, not ITCH 5.0"; "a matching core, not an exchange"; "lock-free around the matcher, single-writer book"). Synthetic data stated as synthetic. Machine and toolchain stated: physical and logical cores, OS (WSL2 Ubuntu 22.04 / Windows 11 / native), compiler and version and optimization flags, library versions, GPU and CUDA version, cache-warmth and pacing conditions.
4. Architecture: fenced file tree with one-line annotations per file, then design deep-dives with the tradeoff each decision makes.
5. Validation: the layers (reference diff, linearizability-style replay, invariants, sanitizers) with pasted output.
6. Findings: the "what broke" narratives.
7. Measured results: machine spec repeated immediately above the tables; bold the one number that matters; ranges when noisy ("throughput varies run to run by roughly 10%; where that matters a range is given"); define precisely what each metric measures and what it does not; report the case where the approach loses.
8. Building and running: exact commands, including sanitizer builds and any `sysctl`/`setarch` prerequisites.
9. Layout (when the Architecture section used a diagram instead of a tree).
10. Sibling comparison, when another portfolio repo shares the domain: a contrast that justifies a design decision in this repo, with both headline numbers side by side, linked to `https://github.com/alexdoe-dev/<sibling>`.
11. Limitations, always last.

Voice: impersonal but opinionated ("the honest point is", "this is the trade"). First person only for judgment calls, never for results.

### Repository hygiene
- `.gitignore`: build outputs (`build/`, `out/`, `*.o`, `*.obj`, `*.exe`, `*.class`, `__pycache__/`, `venv/`, `.pytest_cache/`, `*.log`), IDE dirs, and regenerable data with a comment stating the exact command that regenerates it. Negate small portfolio artifacts explicitly (`!data/plots/*.png`) with a comment.
- Tracked content over 1 MB needs a README justification (the 14 MB `weights.bin` so the C++ side runs without Python is the one precedent).
- `.gitattributes` with `* text=auto eol=lf` for C++ repos (Windows checkouts must not hand CRLF to the toolchain); mark binary formats `binary`.
- A committed repo is 65 to 500 KB of source. Keep it that way.

## 3. Toolchains on this machine

- WSL2 Ubuntu-22.04: g++ 11.4, cmake, CUDA 12.4. Invoke as `wsl.exe -d Ubuntu-22.04 --cd "<wsl path>" -- bash -lc 'export PATH=/usr/local/cuda/bin:$PATH; <command>'`. Windows paths map to `/mnt/c/...`. For I/O-heavy builds and benchmarks use native `/tmp` or `~/build/<name>` and copy results back; `/mnt/c` is slow and timing-noisy. Captures must be written to `/tmp`, not `/mnt/c`. GPU: RTX 4070 SUPER (sm_89).
- MSVC 2019 Build Tools (for Windows/Winsock projects): `cmd /c "\"C:\Program Files (x86)\Microsoft Visual Studio\2019\BuildTools\VC\Auxiliary\Build\vcvars64.bat\" && cmake -G \"NMake Makefiles\" -DCMAKE_BUILD_TYPE=Release .. && nmake"`.
- JDK 21 on both sides (plain `javac`/`java` is an accepted build for Java repos).
- Python 3.12 on Windows, python3 in WSL; create a venv per Python repo; pin `requirements.txt`.
- Kafka/PostgreSQL are not installed as services; a project that needs them documents the stand-up commands and its tests skip cleanly when the broker or DB is unreachable (model-validation-alerting precedent).
- The repo of record is always the Windows-side folder `projects/<name>`, even when built inside WSL.

## 3z. Resource courtesy (this machine is in use while you run)

Alex uses this computer while builds run; a build that seizes every core or all the RAM makes his browser hang. Leave headroom. This never lowers quality: state the machine spec you actually used (cores, RAM) in the README, as the playbook already requires, and the measured number is honest at that spec.

- Parallel builds use at most half the cores. Inside WSL: `make -j"$(( $(nproc) / 2 ))"` (nproc already reflects the capped WSL, so this leaves the host room on top). Never `make -j$(nproc)` or an unbounded `-j`. Same for `cmake --build . -j`, `ninja -j`, `cargo build -j`, `nmake` (MSVC has no clean -j; that is fine).
- Do not spawn more concurrent heavy processes than that. One build server at a time for a benchmark; stop it by PID before the next.
- WSL is capped by `~/.wslconfig` (12 GB RAM, 12 cores) so it cannot starve Windows; do not try to raise those limits.
- A benchmark measured on this shared machine is still valid; if a run looks contended, note it and, if it matters, report a range rather than a single peak.

## 3a. Processes, ports and browsers (hard rules, enforced by a guard hook)

This machine is Alex's daily computer, in use while you run. Everything you start is yours to stop; nothing else is.

- Kill only processes you started, by PID, and only PIDs you hold in a variable or a pid file (`node server.js & echo $! > /tmp/x.pid`). Never `taskkill /IM`, `pkill`, `killall`, `Stop-Process -Name`, and never a PID list collected from `netstat`, `tasklist`, `Get-Process`, a loop, or `$(...)`.
- A busy port is not yours to clear. Pick a free one (`PORT=0` and read the bound port, or a random high port), and make tests take the port from an environment variable.
- Browsers: never launch, locate, list, or kill Chrome, Edge, or Firefox, and never use chromedriver or any real-browser driver. Browser-level tests run on Playwright's bundled Chromium in headless mode; the test process launches and closes its own browser. Selenium is acceptable only against that same headless Playwright Chromium via a driver you installed inside the project, never against the user's browser.
- Long-running servers you start for measurement are stopped by PID at the end of the same command chain that started them. Leave nothing running when you finish.
- The guard hook blocks the forbidden commands with a message; do not try to work around it. If a measurement genuinely needs something the guard forbids, record that in `build_report.json` `notes` and move on.

## 4. Git and GitHub

- Identity: Alex Doe, the `gh` CLI is logged in as `alexdoe-dev`.
- New repo: `git init` (default branch `master` to match the existing repos), one or more meaningful commits, then `gh repo create alexdoe-dev/<name> --public --source . --push`. Existing repo: commit and `git push` to the existing `origin`. Never force-push. Never rewrite history of an existing repo.
- Commit messages are plain descriptions of the work (the existing repos use one-line summaries such as "Lock-free order matching engine with price-time priority"). No trailers.
- Real commit dates stand. Do not backdate.

## 5. build_report.json (in the role folder)

```json
{
  "status": "ok",
  "built_at": "2026-08-23",
  "machine": "8 physical / 16 logical cores, Windows 11 + WSL2 Ubuntu 22.04, g++ 11.4, RTX 4070 SUPER, CUDA 12.4",
  "projects": [
    {
      "title": "Market Data Decoder and Tick Capture Pipeline",
      "name": "market-data-tick-capture",
      "path": "projects/market-data-tick-capture",
      "relationship": "extend",
      "repo_url": "https://github.com/alexdoe-dev/market-data-tick-capture",
      "commit": "8dd0713",
      "platform": "WSL2",
      "build_ok": true,
      "tests_ok": true,
      "test_summary": "600 checks, 0 failures",
      "attempts": 2,
      "benchmarks": [
        {"claim": "1M+ messages/sec", "name": "decode_rate", "value": 815678, "unit": "msgs/sec", "meets_claim": false, "cmd": "./build/replay_verify /tmp/session.tcap", "raw_output_path": "docs/benchmark_output.txt", "note": "single-threaded drain; two attempts at batching the parser did not reach 1M"},
        {"claim": "9x replay storage reduction", "name": "storage_ratio", "value": 4.71, "unit": "x", "meets_claim": false, "cmd": "./build/capture_bench ...", "raw_output_path": "docs/benchmark_output.txt"}
      ],
      "notes": ""
    }
  ],
  "month_built": "Aug. 2026"
}
```

Every entry in `meta.json.projects[].claims` must appear as a `benchmarks[].claim` (or, for non-numeric claims such as "bit-for-bit reproducible", as a `benchmarks[]` entry with `value: true|false`). `status` is `ok` only when every project has `build_ok` and `tests_ok`; otherwise `status: "partial"` with the failure described in `notes` and the stage will be treated as failed.

## 6. After the build

- Append a row for each new repo to `playbooks/PORTFOLIO.md`. The pipeline updates the `Repos` column of this role's row in `roles/INDEX.md` from build_report.json.
- Leave the resume untouched. Reconciling claims against measurements is a separate stage.

## 7. Finish checklist

- [ ] Every project in meta.json has a repo, tests, README in canonical order, raw outputs under `docs/`.
- [ ] Every claim measured and recorded with `meets_claim`.
- [ ] Repos pushed public to alexdoe-dev; `gh repo view alexdoe-dev/<name>` succeeds.
- [ ] `build_report.json` complete; PORTFOLIO.md updated.
- [ ] Nothing you started is still running; you never touched a process or browser you did not start (section 3a).
- [ ] No em dashes in any README or doc.
