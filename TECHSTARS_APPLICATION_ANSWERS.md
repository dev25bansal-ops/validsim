# Techstars Founder Catalyst — Application Answers

**Startup:** ValidSim
**One-liner:** GitHub Actions for robots — continuous validation, regression testing and safety scoring for robot foundation models before they touch the real world.

---

## Before you paste anything: three answers only you can give

The form asks for facts I cannot infer, and getting them wrong is worse than a
blank field. Confirm these first.

| # | Question | What I found | What you must confirm |
|---|---|---|---|
| 1 | **Is your company incorporated?** | Your vault lists Delaware C-Corp as an **unchecked Week 1–2 task** (`vault/08 - Team & Legal/Corporate Structure.md` §Week-1 checklist, and the NVIDIA Inception eligibility table shows `[ ]`). | If you've filed, say **Yes** and give the jurisdiction + date. If not, answer **"No, my company is not yet incorporated."** The form explicitly says this is fine. |
| 2 | **What year was your company founded?** | Not recorded anywhere in the vault. | Your call. If the code started in 2026, use **2026**. |
| 3 | **Country of incorporation** | Plan is **Delaware, USA**. | Only answer if #1 is Yes. |

> If you're not incorporated, that is genuinely not a problem — the form says
> so, and many successful Techstars founders applied at idea stage.

---

## Field-by-field answers

### 1. Startup name
```
ValidSim
```

### 2. Startup URL
```
https://github.com/<your-org>/SIM-TO-REAL
```
**Or leave blank if you have no live site.** A placeholder URL is worse than
none — reviewers click it. If nothing is deployed yet, leave it empty.

### 3. What year was your company founded? *
```
2026
```

### 4. What problem are you looking to solve? * (max 500 chars)

Robotics foundation models ship weekly, but almost nobody verifies a model
update before deploying it to a real fleet. A bad checkpoint reaches hundreds
of robots, and the failure surfaces as a safety incident rather than a bug —
while the team that caused it is already training the next version.

There is no continuous validation for robot models the way there is for
software. CI tells a web team a change didn't break the build. A robotics team
has nothing comparable: no regression suite, no safety gate, no answer to
"is this checkpoint better or worse than the one we shipped?"

```
Robotics foundation models ship weekly, but almost nobody verifies a model
update before deploying it to a real fleet. A bad checkpoint reaches hundreds of
robots, and the failure surfaces as a safety incident rather than a bug — while
the team that caused it is already training the next version.

There is no continuous validation for robot models the way there is for
software. CI tells a web team a change didn't break the build. A robotics team
has nothing comparable: no regression suite, no safety gate, no answer to "is
this checkpoint better or worse than the one we shipped?"
```

### 5. What is your company going to make to solve this problem? * (max 500 chars)

ValidSim is a validation gate that runs before a robot model reaches a fleet.
You point it at a checkpoint; it runs the model through thousands of simulated
episodes under domain randomization and adversarial scenarios, then returns a
scorecard: task success, safety violations, robustness across conditions, and
regressions against your last shipped model.

It ships as a GitHub Action and a CLI, so a robotics team gets the same
"did this break anything?" signal they expect from software CI. The output is a
signed, comparable scorecard — the artifact an insurer or a safety reviewer
asks for and that teams currently rebuild by hand.

```
ValidSim is a validation gate that runs before a robot model reaches a fleet.
Point it at a checkpoint and it runs the model through thousands of simulated
episodes under domain randomization and adversarial scenarios, then returns a
scorecard: task success, safety violations, robustness across conditions, and
regressions against your last shipped model.

It ships as a GitHub Action and a CLI, so a robotics team gets the same "did
this break anything?" signal they expect from software CI. The output is a
signed, comparable scorecard — the artifact an insurer or a safety reviewer
asks for, which teams currently rebuild by hand.
```

### 6. Is your company incorporated? *
```
No, my company is not yet incorporated.
```
*(or `Yes, Delaware, United States` if you've filed)*

### 7. Which vertical networks are relevant to your company? (select up to five)

This routes you to the right regional programs and mentors. For a robotics
devtools company, pick from what's actually offered in the dropdown:

| Priority | Network | Why |
|---|---|---|
| 1 | **NVIDIA Inception** | Most relevant. Robotics compute, and you already have a drafted application gated on incorporation. |
| 2 | **Enterprise** | Your Phase 3 buyers are fleet operators and manufacturers. |
| 3 | **Industrial Automation / Logistics** | Amazon Robotics, DHL, GXO are named Phase 3 targets. |
| 4 | **Manufacturing** | BMW, Mercedes, Toyota, Foxconn, Samsung. |
| 5 | **Artificial Intelligence / SaaS** | Fallback if the dropdown uses software categories. |

**Do not** select Healthcare, Fintech, Consumer, or Climate unless you have a
named target in that space. A precise selection reads as focused; a scattershot
one reads as unfocused. NVIDIA Inception + Enterprise is the honest answer.

---

## Questions the form will ask next, pre-empted

These appear later in the Techstars form. Answer them the same way: concretely,
without overclaiming.

### Traction
Your vault records **zero LOIs, zero pilots, zero revenue** — the GTM plan
targets 2–3 signed LOIs and your own `YC Application Answers.md` marks them
`[verify]`. Don't claim customers.

Answer honestly: the working system is real and tested; the discovery
conversations are just beginning. Techstars funds pre-traction startups — an
honest "we have a working product and are now talking to customers" is a
stronger answer than an implied customer list you can't produce on a call.

### "What have you achieved so far?"
Lead with what's **measured, not projected**:
- A working, tested validation system (a real number — run `python -m pytest tests/`)
- A full scoring engine: safety, robustness, regression, adversarial scenarios
- A test suite with CI gating at a 90% coverage floor
- CLI + API + dashboard + GitHub Actions integration

**Do not** claim GPU timings, Isaac Sim runs, or accuracy figures. Your own
docs warn against exactly this: the current engine is a deterministic mock,
and quoting unmeasured performance is the fastest way to lose credibility in
due diligence.

### "Why you?"
Two founders, AI/ML, building for a problem they can see because it's the
interface between their own skills. Note the honest gap rather than hiding it:
no formal robotics academic credibility yet — an advisory board is post-funding.

---

## The honest framing that works best here

Techstars' stated thesis is **customer discovery, not capital**. Your vault
says you have "superb infrastructure for discovery — a written script, named
targets, a decision log template — **but no discovery has actually happened**."

So apply as a team **de-risking a market assumption**, not a company needing
money. The strongest true sentence you can write is:

> "We built the validation system first, and now we're using it on ourselves —
> finding that our own deploy gate could approve a run where every simulation
> episode failed. We're fixing that, and the same discipline is what we're
> bringing to robot fleets."

That's falsifiable, specific, and demonstrates exactly the engineering judgment
a technical founder is judged on. It's far stronger than any market-size number.

---

## Three things to fix before you submit

1. **Run 3–5 real discovery calls.** Highest impact per hour available to you,
   and the weakest dimension in your profile. Even informal conversations with
   robotics engineers change what you write in the next section.
2. **Don't claim traction you don't have.** No LOIs, no pilots, no revenue —
   your own docs say so. Techstars is fine with pre-traction; diligence is not
   fine with a padded answer.
3. **Have a live URL or leave it blank.** A dead link is worse than no link.
