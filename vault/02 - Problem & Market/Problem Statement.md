---
tags:
  - problem
  - market
status: complete
created: 2026-09-18
area: "02 - Problem & Market"
---

# 🩸 Problem Statement

> [!warning] Verify before external use — 2026-09-21
> Statements such as “no productized pipeline,” “every lab,” and the three-zero framing are market hypotheses, not facts established by this repository. Validate with current discovery interviews and sourced competitive research.

## The core problem

> [!important]
> **Robot foundation models and robot behaviors now update as frequently as software, but there is no equivalent of continuous integration for machines that can cause physical harm when a policy update misfires.**

The industry has open simulators — NVIDIA Isaac Sim in particular — but **no productized pipeline** that takes a new model checkpoint plus a task library and returns a defensible safety-and-success scorecard ([[Vision & Positioning]]).

## Current state of robot validation

Every robotics lab and fleet operator today validates model updates with:

- **Hand-built test scenes** in simulation (hours to days of engineering per scene)
- **Small teleoperation trials** ($30–80/hr per operator, poor generalization, cannot cover dangerous edge cases)
- **Judgment calls** by senior engineers ("looks good, ship it")
- **Ad-hoc spreadsheets** tracking pass/fail
- **Zero** standardized regression detection
- **Zero** safety scoring methodology
- **Zero** audit trail for insurers or regulators

The "three zeros" are the product spec: regression detection, safety scoring, audit trail ([[Module Specs]]).

## Physical ≠ digital: why software CI/CD doesn't port directly

In software, a bad deploy rolls back. In robotics, a bad deploy:
- drops a **$50K** component,
- damages a production line (downtime at **$10K–$100K/hr**),
- or injures a human coworker (liability, [[Compliance]]).

Therefore validation must be **massively parallel** (you cannot run 10,000 scenarios on one physical robot), **physics-accurate** (a game engine isn't enough), and must produce **audit-ready evidence** (insurers and regulators will demand it). This is the unique insight quoted in [[YC Application]].

## Who feels this pain right now

| Tier | Who | When they buy |
|---|---|---|
| **Tier 1** | Robot foundation model companies (~50–100 globally in 2026: Physical Intelligence $5.6B, Skild AI $14–15B, FieldAI, 1X, Figure, Apptronik, Agility, Boston Dynamics) | **Immediate** |
| **Tier 2** | Humanoid OEMs & integrators (~200–500 by 2028: Tesla Optimus, Unitree, UBTECH, Sanctuary AI) | Year 1–2 |
| **Tier 3** | Fleet operators (1,000+ by 2029: Amazon Robotics, GXO, DHL, BMW, Mercedes, Toyota, Foxconn, Samsung) | Year 2–3 |
| **Tier 4** | Insurers, regulators, compliance bodies (Munich Re, Swiss Re, Lloyd's, UL, TÜV, BSI, OSHA) | Year 3+ — mandatory spend |

Full detail: [[Buyer Tiers]]. Costs per pain point: [[Pain Quantified]].

## The buyer's internal monologue

> *"I just fine-tuned our VLA model for the new bin-picking task. Success rate in our test scene looks good — 94%. But last time we deployed a 'good' model, it failed on the night shift because the lighting was different. And the one before that broke the gripper on a deformable object we hadn't tested. My CTO wants to deploy to 200 robots next month. My insurance broker wants a safety report. I have 3 test scenes and a prayer. I need a system that runs 10,000 scenarios, catches regressions, generates a safety scorecard, and tells me — with statistical confidence — whether this checkpoint is safe to deploy."*

> [!quote] Discovery question that surfaces this monologue verbatim
> **"How do you validate a model update before deploying it to your fleet?"** — ask this in every call ([[YC Countdown]], [[Go-to-Market]]).

## Why the timing forces converge here

Five forces (capital, shipping cadence, deployments, open sim, compliance) → [[Why Now (2026)]]. Market value of the gap → [[Market Sizing (TAM SAM SOM)]].

Links: [[Home]] · [[Pain Quantified]] · [[Buyer Tiers]] · [[Product Vision]] · [[Risk Register]]
