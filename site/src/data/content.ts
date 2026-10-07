/**
 * Page content, kept out of the markup.
 *
 * This is the answer to "a non-engineer has to edit raw HTML". Copy, pricing,
 * the pipeline stages and the FAQ all live here as typed data, so changing a
 * price or rewording an answer is a one-line edit with no markup touched and no
 * risk of breaking the layout.
 *
 * Honesty rules encoded as data, not as prose discipline:
 *  - `status: 'pre-traction'` drives the badge in the masthead and the footer.
 *  - The FAQ answer for production use MUST stay "No" until that is false.
 *    `tests/test_marketing_refusals_agent.py` asserts it.
 */

export const site = {
  name: 'ValidSim',
  domain: 'https://validsim.com',
  email: 'hello@validsim.com',
  github: 'https://github.com/validsim',
  status: 'pre-traction' as const,
  tagline: 'CI for robot foundation models',
} as const;

export const nav = [
  { href: '#blocked', label: 'Why BLOCK' },
  { href: '#pipeline', label: 'Pipeline' },
  { href: '#field', label: 'Field' },
  { href: '#pricing', label: 'Pricing' },
  { href: '#faq', label: 'FAQ' },
] as const;

/** The focal point. Real output from a real run — do not hand-edit the numbers. */
export const readout = {
  runId: 'vrun-ed0c150a',
  checkpoint: 'ckpt-gr00t-n1',
  composite: '72.7',
  threshold: '85.0',
  verdict: 'BLOCK' as const,
  command: 'validsim run --checkpoint ckpt-gr00t-n1 --episodes 1000 --adversarial 50',
  rows: [
    { label: 'Checkpoint', value: 'ckpt-gr00t-n1', tone: 'default' },
    { label: 'Success rate', value: '0.690', tone: 'block' },
    { label: 'Adversarial (n=50)', value: '0.340', tone: 'block' },
    { label: 'Safety', value: '77.7', tone: 'default' },
    { label: 'Robustness', value: 'not measured', tone: 'unmeasured' },
    { label: 'Gate', value: '85.0', tone: 'default' },
  ],
} as const;

/** Verbatim CLI transcript. Kept as lines so highlighting is applied per token. */
export const transcript = [
  { text: '$ validsim run --checkpoint ', tone: 'dim' },
  { text: 'ckpt-gr00t-n1', tone: 'key' },
  { text: ' --episodes ', tone: 'dim' },
  { text: '1000', tone: 'key' },
  { text: ' --adversarial ', tone: 'dim' },
  { text: '50', tone: 'key' },
  { text: '', tone: 'plain' },
  { text: 'ValidSim validation complete', tone: 'dim' },
  { text: '  Run ID:       vrun-ed0c150a', tone: 'dim' },
  { text: '  Task:         pick-place (1000 nominal + 50 adversarial)', tone: 'dim' },
  { text: '  Success rate: 69.0%', tone: 'block' },
  { text: '  Adversarial:  34.0% (50 episodes)', tone: 'block' },
  { text: '  Safety score: 77.7', tone: 'key' },
  { text: '  Robustness:   0.0 - not measured (1 randomization group)', tone: 'unmeasured' },
  { text: '  Composite:    72.7 (threshold 85.0)', tone: 'block' },
  { text: '  Decision:     BLOCK', tone: 'block' },
  { text: '    - adversarial success rate 34.0% is significantly below', tone: 'block' },
  { text: '      the 60% floor (17/50 adversarial episodes passed)', tone: 'block' },
  { text: '    - composite 72.71 is below the configured threshold 85.00', tone: 'block' },
  { text: '  Top failures:  emergency_stop=58, timeout=47, collision=47', tone: 'dim' },
] as const;

export const stages = [
  {
    no: 'FIG 0.3.1',
    title: 'Point at a checkpoint',
    body: 'A model artifact and a task. No pipeline to redesign, no data to migrate.',
  },
  {
    no: 'FIG 0.3.2',
    title: 'Run the episodes',
    body: 'Domain randomization across lighting, friction, mass and sensor conditions, plus adversarial scenarios in twelve categories.',
  },
  {
    no: 'FIG 0.3.3',
    title: 'Read the scorecard',
    body: 'Success with a bootstrap confidence interval, safety violations, robustness, and regression against your last shipped checkpoint.',
  },
  {
    no: 'FIG 0.3.4',
    title: 'Gate the deploy',
    body: 'The Action fails the pull request on BLOCK — and a component that could not be measured says so instead of scoring full marks.',
  },
] as const;

export const competitors = [
  { name: 'Formant, Viam', does: 'Fleet monitoring, teleoperation, fleet management', relation: 'post-deploy', tone: 'them' },
  { name: 'NVIDIA Isaac Sim', does: 'The simulation substrate itself', relation: 'platform', tone: 'them' },
  { name: 'W&B, MLflow', does: 'Experiment tracking for ML', relation: 'no physics', tone: 'them' },
  { name: 'UL, TÜV', does: 'Physical certification, over months', relation: 'partner', tone: 'part' },
  { name: 'In-house lab tools', does: 'Bespoke validation built by the big labs', relation: 'real threat', tone: 'us' },
] as const;

export const method = [
  {
    title: 'We found our own gate was wrong',
    body: 'Auditing the scoring engine, we found it would return APPROVE for a run in which every single simulation episode had failed. Thirty of the hundred composite points were being granted unconditionally.',
  },
  {
    title: 'We fixed it instead of shipping it',
    body: 'An unmeasured component now abstains from the score rather than contributing fabricated credit. That run scores 42.86 and blocks at any threshold. A flawless run still scores 100.',
  },
  {
    title: 'We only quote measured numbers',
    body: 'Every figure here came from running the tool. Where something is a target rather than a measurement — our pricing, our customer list — we label it as one.',
  },
] as const;

export const pricing = [
  { name: 'Developer', price: '$0', suffix: ' / mo', body: 'Researchers and open source. 10 runs a month.' },
  { name: 'Team', price: '$2k', suffix: ' / mo', body: 'Small labs, 2–10 engineers. Dashboard, Actions, support.' },
  { name: 'Pro', price: '$8k', suffix: ' / mo', body: 'Mid-size. Adversarial suites, regression timelines.' },
  { name: 'Enterprise', price: 'Custom', suffix: '', body: 'Fleet operators. Compliance reporting, HIL, SSO, SLA.' },
] as const;

export const faq = [
  {
    q: 'What does “not measured” mean on a scorecard?',
    a: 'A component that could not be measured is excluded from the composite’s denominator rather than given a default score. Robustness reads not measured when a run used fewer than two randomization groups; regression does the same when no baseline was supplied. This is deliberate. An earlier version scored both at 100, which meant 30 of the 100 composite points were awarded whether or not anything had actually been measured — and a run in which every episode failed could reach APPROVE.',
  },
  {
    q: 'Does ValidSim run on real robots?',
    a: 'No, and that is the point. Ten thousand scenarios cannot be run on one physical robot, so validation happens in simulation before anything reaches a fleet. The MVP ships a deterministic mock backend with an adapter for NVIDIA Isaac; the mock exists so the scoring contract is testable without GPU hardware.',
  },
  {
    q: 'Do I have to author my own simulation environment?',
    a: 'No. You point ValidSim at a model checkpoint and a task. It generates the domain randomization and the twelve adversarial scenario categories itself, so there is no scene to build. Pointing it at your own Isaac Lab environment is supported, just not required.',
  },
  {
    q: 'How does it fit into an existing pipeline?',
    a: 'As a CLI or a GitHub Action. The Action posts the scorecard on the pull request and fails the check on a BLOCK verdict, so an unvalidated model update cannot merge by default.',
  },
  {
    q: 'Is this in production anywhere?',
    a: 'No. ValidSim is pre-traction with no customers. The system is real and tested, and we publish the numbers it actually produces — including the runs where it blocks a model. We would rather show you a working tool with an honest scorecard than a customer list we cannot substantiate.',
  },
] as const;
