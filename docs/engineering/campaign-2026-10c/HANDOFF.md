# Campaign 2026-10c (Ghost Evolve) handoff, 2026-10-22 ~01:15

Merged on master:
- E1 strategy genome (7581a9c).
- E2a held-out split: 8 goals, 3 bug-fix starters, integrity proofs (a4ad3d6). Full suite 1437 passed.
- Track sheets E7, E8 (e55cce0).

Running on the Hermes kanban (GLM-5.3-Flash; dispatcher loop PID 23640, stop with STOP-DISPATCH):
- E4 skills t_255f4518 (c3/E4), E5 curriculum t_48b34747 (c3/E5), E6 profiles/goals/home t_9d862653 (c3/E6)
- E7 extra train goals t_37b31860 (c3/E7), E8 evolution view t_db32f79e (c3/E8)
Each one must be reviewed by the root before it merges to master.

Not started (root work, in order):
1. E2b benchmark.py v2: per-goal roots under run_root/<goal_id>; write starter_files and use
   workspace_mode="project" when present; parallel (ThreadPoolExecutor); split=train|heldout|all
   via benchmark_goals.all_goals(); strategy_genes/skills_root in the job contract; report fields
   split/strategy_digest/label; CLI flags --split/--parallel/--strategy. Keep tests/test_benchmark.py green.
2. E3 evolve.py + `ghost evolve`: archive, reflective GLM mutation from failure traces, Pareto
   selection on train, held-out gated promotion (owner `improve` mode).
3. Integrate E4-E8, then run evolution with GLM overnight, Kaggle open-model run, README/STATE.
