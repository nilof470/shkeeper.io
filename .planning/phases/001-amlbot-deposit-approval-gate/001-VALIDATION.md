---
phase: 001
slug: amlbot-deposit-approval-gate
status: draft
nyquist_compliant: true
wave_0_complete: false
created: 2026-05-03
---

# Phase 001 - Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | Python stdlib `unittest` |
| **Config file** | none |
| **Quick run command** | `python -m compileall shkeeper` |
| **Full suite command** | `python -m unittest discover -s tests` |
| **Estimated runtime** | ~60 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python -m compileall shkeeper`
- **After every plan wave:** Run `python -m unittest discover -s tests`
- **Before `$gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 60 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 001-01-01 | 01 | 1 | SPEC-09 | T-01-01 | Provider evidence persists without exposing secrets | unit | `python -m unittest discover -s /Users/test/PycharmProjects/aml-shkeeper/tests` | W0 | pending |
| 001-01-02 | 01 | 1 | SPEC-14 | T-01-02 | Duplicate checks are idempotent | unit | `python -m unittest discover -s /Users/test/PycharmProjects/aml-shkeeper/tests` | W0 | pending |
| 001-02-01 | 02 | 1 | SPEC-03 | T-02-01 | Transaction state and AML state are separated | unit | `python -m unittest discover -s tests` | W0 | pending |
| 001-02-02 | 02 | 1 | SPEC-11 | T-02-02 | Skips cannot fake zero-risk AML scores | unit | `python -m unittest discover -s tests` | W0 | pending |
| 001-02-03 | 02 | 1 | SPEC-13 | T-02-03 | Missing mappings fail closed | unit | `python -m unittest discover -s tests` | W0 | pending |
| 001-03-01 | 03 | 2 | SPEC-05 | T-03-01 | Callbacks are delayed while AML pending | integration | `python -m unittest discover -s tests` | W0 | pending |
| 001-03-02 | 03 | 2 | SPEC-06 | T-03-02 | Trigger callback carries AML decision evidence | integration | `python -m unittest discover -s tests` | W0 | pending |
| 001-04-01 | 04 | 3 | SPEC-15 | T-04-01 | Docs state grither-pay owns credit/manual review | doc test | `python -m unittest discover -s tests` | W0 | pending |

---

## Wave 0 Requirements

- [ ] `tests/` exists in SHKeeper.
- [ ] `/Users/test/PycharmProjects/aml-shkeeper/tests/` exists in `aml-shkeeper`.
- [ ] `python -m unittest discover -s tests` exits 0 in SHKeeper after implementation.
- [ ] `python -m unittest discover -s /Users/test/PycharmProjects/aml-shkeeper/tests` exits 0 after sidecar implementation.

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Live AMLBot credential validity | SPEC-01 | Requires production AMLBot credentials and billing account | In staging, configure AMLBot secrets and run one known clean tx through the sidecar. |
| grither-pay admin manual review UX | SPEC-15 | Out of scope for this repository | Verify grither-pay routes `manual_review` callbacks outside this phase. |

---

## Validation Sign-Off

- [x] All tasks have automated verify commands or Wave 0 dependencies.
- [x] Sampling continuity: no 3 consecutive tasks without automated verify.
- [x] Wave 0 covers all missing references.
- [x] No watch-mode flags.
- [x] Feedback latency target < 60s.
- [x] `nyquist_compliant: true` set in frontmatter.

**Approval:** pending
