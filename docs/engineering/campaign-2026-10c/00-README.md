# Campaign 2026-10c: Ghost Evolve, a personal agent that measurably improves itself while you sleep

Started 2026-10-22 00:30 by the meta-orchestrator (Claude). The goal is the spec's
core promise (implementation.md §1.1, self-evolution as a *measurable loop*),
built so the improvement is judged by evidence the agent cannot edit.

## Why this is novel

Each piece below exists in the research literature, but for cloud-scale
coding agents, not for a personal agent that runs on the owner's PC under the
owner's standing policy:

- **Reflective strategy evolution** ([GEPA](https://arxiv.org/pdf/2507.19457), ICLR 2026): an LLM reads
  execution traces and proposes prompt edits; a per-instance Pareto front avoids local optima.
- **Archive-based self-improvement** ([Darwin Gödel Machine](https://arxiv.org/pdf/2505.22954)): keep every
  variant; pick parents by performance *and* novelty; validate every change empirically.
- **Gated skill libraries** ([CODESKILL](https://deeplearn.org/arxiv/825195/codeskill:-learning-self-evolving-skills-for-coding-agents),
  [Solve-and-Reproduce](https://arxiv.org/pdf/2608.28638), [SkillGLoW](https://huggingface.co/papers/2609.02217)):
  curated skills help a lot, naive self-written skills hurt, so admission must be gated.

Ghost Evolve combines them with three properties none of those systems has:

1. **The mutable thing is data, not code.** The *strategy genome* holds prompts, phase policy and
   repair tactics. Evolution can never touch the evaluator, hidden tests, policy or authority code.
   Self-modification stays inside a safe envelope by construction.
2. **The judge is held-out and hidden.** Train goals drive mutation. A separate *human-written,
   held-out* goal set, whose hidden tests the agent never sees, is the only promotion gate.
   Promotion also requires the owner's `improve` policy mode.
3. **The curriculum is personal.** An *examiner* writes practice tasks from the owner's own goals,
   concepts and recent failures. A task counts only if a reference solution passes its hidden test
   **and** a deliberately broken mutant fails it, so self-made tests must be meaningful.

## Components and owners

| ID | Component | Owner |
|---|---|---|
| E1 | Strategy genome: orchestrator strategy as versioned data; the default reproduces today's behaviour exactly | meta (Claude) |
| E2 | Benchmark v2: per-goal isolated roots, parallel runs, train/held-out split, starter projects (bug-fix tasks), `--strategy` | meta |
| E3 | Evolution engine `ghost evolve`: archive, reflective mutation, Pareto selection, held-out gated promotion, daemon hook | meta |
| E4 | Gated skill library `ghost skills` | Hermes track |
| E5 | Examiner curriculum `ghost practice` | Hermes track |
| E6 | Inference profiles (direct OpenAI-compatible client), goals CLI, `ghost home init` | Hermes track |

Track decision sheets: `tracks/E4.md`, `tracks/E5.md`, `tracks/E6.md`. The Hermes procedure is
`../campaign-2026-10b/01-PHASE-B-BRIEF.md` (tracks/progress paths substituted to this folder).
