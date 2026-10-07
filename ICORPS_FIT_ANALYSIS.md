# ValidSim × University of Memphis NSF I-Corps — Fit Analysis

**Prepared:** 2026-09-25 · **Cohort:** Fall 2026 (starts Wed 2026-09-30) · **Source:** `https://www.memphis.edu/research/i-corps/`

> [!warning] Read this first
> The application form, deadline, review process, deliverables and selection rubric are **not published**. Everything below assesses the project against the **three pillars the program page actually states** — Customer Discovery, Business Model Canvas, Design Thinking. It is not a prediction of selection odds. Where the project's real state falls short, I say so plainly.

---

## 1. Three things you need to know immediately

### 1.1 You are eligible — this reverses my earlier read

The page states eligibility explicitly, and it is **broad**:

> "Faculty researchers · Graduate and undergraduate students · University staff · Community teams · **Unaffiliated entrepreneurs and innovators in the Mid-South region**"

I searched all 66 vault notes: the project has **no Memphis connection** — no university affiliation, no incubator membership, no regional presence. Under the published criteria that is **not a disqualifier**. It is a networking disadvantage, not an eligibility bar.

**One thing to confirm yourself:** "Mid-South region" is the only qualifier I cannot resolve. If the founders are not in the Memphis area, ask Dr. Cody Behles (`cbehles@memphis.edu`, 901-678-2648) before investing effort.

### 1.2 This is not a funding program — it is a customer-discovery program

The page promises **no grant funding, no stipend, no equity take**. It says participation "improves the likelihood of obtaining federal grants" and prepares teams for **SBIR/STTR funding potentially totaling $2M+** — a *future* path, not money provided.

**Consequence:** do not apply as a company needing capital. Apply as a team that needs to de-risk a market assumption. The curriculum is Customer Discovery → Business Model Canvas → Design Thinking. An application led by architecture, the composite-score formula, or the GitHub Action will read as the wrong shape entirely.

### 1.3 The cohort starts in 5 days

**Wednesday, 2026-09-30.** Five weeks, one 2-hour evening session weekly. No deadline is published, but the start date is binding — you have days, not weeks.

---

## 2. Fit against the three pillars

### 2.1 Pillar 1 — Customer Discovery · **your largest gap**

| Evidence | State |
|---|---|
| Documented customer interviews | **None.** No interview logs or transcripts anywhere in 66 vault notes. |
| Target customer definition | **Strong.** `Go-to-Market.md` names 5–10 specific labs (Physical Intelligence, Skild AI, 1X, Figure AI, Apptronik) with a written discovery question. |
| Discovery script | **Exists.** `Discovery Call Script.md` + a `Decision Log` template, both committed. |
| LOIs / pilots / revenue | **None verified.** Target is "2–3 signed LOIs"; zero documented. |

Read that honestly: you have built excellent **infrastructure for doing** discovery, but **zero completed discovery**. The 10-calls/week cadence in `Go-to-Market.md` is a plan, not a record. A panel assessing Customer Discovery will find: strong hypothesis, strong script, strong target list — **no interviews conducted**.

This is your binding constraint. It is also, helpfully, exactly what the program teaches — a genuine argument *for* applying.

### 2.2 Pillar 2 — Business Model Canvas · **~7 of 9 blocks substantively filled**

| Dimension | State |
|---|---|
| Customer Segments | Defined by tier (`Pricing Tiers.md`, `Buyer Tiers`) |
| Value Propositions | Defined (`Business Model.md`) |
| Channels | Defined — direct, NVIDIA Inception, conferences |
| Customer Relationships | Defined — developer-led |
| Revenue Streams | **Planning assumptions only** — `Unit Economics.md` explicitly warns these are "not measured unit economics" |
| Key Resources / Activities | Defined (`Technical Moat Features.md`) — strongest block |
| Key Partners | Defined |
| Cost Structure | **Unverified projections** — GPU cost, CAC, retention all flagged unverified |

Acceptable at this stage for a five-week program — pressuring these assumptions is precisely what the Business Model Canvas session exists to do. Bring them as **questions, not claims**.

### 2.3 Pillar 3 — Design Thinking · **your strongest pillar**

The problem definition is *derived from the code*, not asserted: a robotics lab's VLA policy update has no evidence-gated deploy path, and ValidSim is that gate. You can point at `validsim/engine/scorecard.py` and show the actual logic.

The counterexample I reproduced earlier — **every episode fails, `success_rate = 0.0`, and the run still APPROVES at threshold 60**, because 30 of 100 composite points are unconditional — is a *real* scoring defect you found yourself.

**Do not hide it in the application. Lead with it.** Fixing your own metric's failure mode is the strongest possible demonstration of design-thinking discipline, and it is falsifiable evidence rather than a claim.

### 2.4 SBIR/STTR positioning

The page frames the program around SBIR/STTR readiness. Relevant verified assets: a `SECURITY.md` with a working vulnerability-reporting route (issue 29), key-gated API routes, HMAC-signed webhooks, and a mature audit posture. That is a credible foundation for a commercialization narrative — though SBIR eligibility ultimately depends on the *PI's* institution and NSF's rules, which this program does not guarantee.

---

## 3. Readiness scorecard

| Pillar | Score | One-line reason |
|---|---|---|
| Customer Discovery | **2 / 5** | Hypothesis + script + targets exist; **zero interviews conducted** |
| Business Model Canvas | **3.5 / 5** | 7/9 blocks real; revenue and cost are projections |
| Design Thinking | **4 / 5** | Problem derived from working code; one self-found scoring defect |
| Engineering artifact | **5 / 5** | Suite green, ruff clean — genuinely unusual pre-seed depth. No test count is quoted here on purpose; run `python -m pytest tests/` and cite the totals it prints |
| Traction / validation | **1 / 5** | No LOI, no pilot, no revenue, no external user |
| University / regional fit | **2 / 5** | Eligible, but zero Memphis connection and no regional presence documented |

**Overall: a technically exceptional prototype with an unvalidated market.** That profile is precisely what a customer-discovery program is designed for — which makes this a good fit *if* you can be honest about the gap.

---

## 4. What to do in the next 5 days

**Ranked by impact per hour available:**

1. **Conduct 3–5 real customer-discovery interviews — today, before applying.** Even informal calls to robotics researchers count. This converts your single weakest dimension from 2/5 to 3/5, and it is the one thing a panel will look for. Log them in `Decision Log` format.
2. **Fix the composite-score defect** (`success_rate = 0.0` still approving). Then state it plainly in the application: *"We found that our own gate could approve a run where every episode failed. Here is the fix."* That is a stronger signal than any metric you could quote.
3. **Write the one-page canvas** with the 7 real blocks filled and Revenue/Cost explicitly marked as assumptions to test. Do not present projections as findings.
4. **Apply framed as discovery, not capital.** "We have a working gate and need to test whether deploy-gating is the right product boundary" — not "we need funding."
5. **Confirm the regional eligibility question** with Dr. Behles if the founders are outside the Mid-South.

---

## 5. Honest risks

- **Five days is very short** for both applying and conducting discovery interviews. Sequence matters: interviews first, because they change what you write.
- **The Mid-South qualifier is the one hard gate I could not verify.** Resolve it first; everything else is moot if you are ineligible.
- **A strong engineering artifact can hurt you.** Programs like this are skeptical of technology-first pitches. Lead with the problem and the interviews, not the scorecard formula.
- **Do not quote unverified performance.** Your own `MVP Success Metrics.md` carries an explicit warning against claiming 4×A100 timing, >95% accuracy, or 100% uptime before measurement. I-Corps panels are trained to probe exactly this. The engineering artifact is the same kind of risk: cite the coverage `TOTAL` and ruff status that `python -m pytest tests/` prints on the day you submit, and name the command that produced them — not a remembered test count.
- **"NSF" in the name does not confer NSF certification.** The page does not confirm national-program affiliation, an NSF award number, or a node. Do not claim NSF endorsement in your application.

---

## 6. What I could not verify

- Application form questions, deadline, review process, required deliverables
- Whether participants receive national NSF I-Corps certification
- Whether "Mid-South region" is a hard geographic requirement
- Selection criteria or scoring rubric (none published)

Fetch the "Apply Now" link for the actual form, or contact `cbehles@memphis.edu`. If you paste the application text, I will map your assets against the real questions.
