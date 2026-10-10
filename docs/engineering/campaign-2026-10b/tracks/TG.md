# Track TG: personal memory controls, document lifecycle, study loop (R02, R10, S04, S05)

Audit: `audit/A2.md`. Branch `c2/TG`, worktree `C:/Users/chris/Documents/Ghost/ghost_c2_TG`.
Files this track owns: `ghost_agent/context_manager.py`,
`ghost_agent/knowledge_base.py`, `ghost_agent/learner.py`,
`ghost_agent/learning_engine.py`, new `ghost_agent/study.py`, `cli.py` (new
blocks only, plus the existing `memory`/`kbase`/`learn` handlers), and tests.

---

## TG1: memory record fields and owner controls (review: 2)

**Goal:** memory records carry R02's minimum fields, and the owner can list,
edit, export and set retention.

**Decided design:**

- Add these fields to `MemoryRecord`, with defaults so old records load:
  `subject: str = ""`, `sensitivity: str = "normal"`
  (`normal|personal|health|secret`, the same set as track TA's events),
  `last_verified_at: Optional[float] = None`.
- Extend `KINDS` with `"preference"`, `"hypothesis"` and `"feedback"`. Validate
  `sensitivity` in `_validate`.
- `ValidatedMemoryStore.edit(memory_id, *, content=None, subject=None, sensitivity=None, confirm_verified=False) -> MemoryRecord`.
  Editing content keeps provenance: store the previous content in a new
  `revisions: list[dict]` field (`{"content", "edited_at"}`, max 10).
  `confirm_verified` sets `last_verified_at = now`. Retracted or purged
  records can't be edited.
- Retention: `.ghost/memory_retention.json`
  `{"default_days": null, "by_kind": {"working": 7}}`. `active()` hides
  records older than their kind's retention; they aren't deleted. A
  `ValidatedMemoryStore.apply_retention() -> int` purges them through the
  **existing** purge path (so dependents are invalidated).
- CLI: extend the existing `ghost memory` group with `list [--kind K] [--json]`,
  `edit <id> [--content ..] [--subject ..] [--sensitivity ..] [--verified]`,
  `export --out PATH` (refuses to overwrite; omits `secret` records), and
  `retention set --kind K --days N` / `retention apply`.
- Health/secret sensitivity records must never be printed by `list` unless
  `--show-sensitive` is passed.

**Tests:** legacy load; edit keeps revisions; a retracted record can't be
edited; retention hides, then applies through purge; export omits secret;
list hides health without the flag.

---

## TG2: knowledge base document lifecycle (review: 2)

**Goal:** owner documents can be replaced and deleted, and derived content is
invalidated (R02, S05).

**Decided design:**

- Add to `DocChunk`: `version: int = 1`, `superseded: bool = False`,
  `section: str = ""`, `page: Optional[int] = None`,
  `extraction_quality: str = "text"` (`text|ocr|partial`).
- Re-ingesting the same `source` with a different file sha256 marks the old
  chunks `superseded=True` and stores the new ones with `version = old+1`.
  The same sha256 is a no-op.
- `retrieve` ignores superseded chunks.
- `OwnerKnowledgeBase.delete_source(source) -> int` removes all chunks of that
  source and emits `Event("kbase.source_deleted", {"source": ..., "chunks": n})`.
  It also invalidates **derived** items: any `ValidatedMemoryStore` record
  whose `supporting_artifacts` contains the source path gets `invalidate(...)`,
  and any LearnerStore lesson citing it (`lesson_id == source`) is listed in
  the event payload as `affected_lessons` (do not delete learner history).
- Sections: for `.md` files, `section` is the nearest preceding heading text
  for each chunk. For other files it stays empty. `page` stays None (no PDF
  support here).
- CLI: `ghost kbase delete <source>` and `ghost kbase sources` (lists the
  sources with versions and chunk counts).

**Tests:** re-ingesting a changed file supersedes the old chunks and retrieve
returns only new content; unchanged is a no-op; delete removes the chunks,
invalidates a dependent memory, and the event lists affected lessons;
markdown section capture.

---

## TG3: exercise generation linked to concepts

**Goal:** Ghost generates practice material tied to concepts and cited
sources, with protected answers (R10, S04, S05).

**Decided design:** new module `ghost_agent/study.py`:

- `Exercise` dataclass: `exercise_id, concept_id, prompt, hints: list[str]` (1–3,
  progressive), `answer_kind: "exact"|"rustc"|"keywords"`,
  `source_refs: list[str]` (kbase chunk ids), `created_at`.
- The **answer** is stored separately in `.ghost/learner/answers/<exercise_id>.json`
  (`{"expected": str}` or keywords), never in the learner-facing record.
- `ExerciseGenerator(root, model, chat_fn=chat)` with
  `generate(concept_id, count=3) -> list[Exercise]`: it retrieves up to 4
  kbase chunks for the concept name (`OwnerKnowledgeBase.retrieve`), asks the
  model for strict JSON `{"exercises": [{"prompt", "hints", "answer_kind", "expected"}]}`,
  validates (types, lengths: prompt ≤ 600, hint ≤ 240, expected ≤ 2000),
  drops invalid items, and sets `source_refs` to the retrieved chunk ids. If
  nothing was retrieved, `source_refs` is empty and the prompt says "no owner
  sources available". Model JSON is parsed defensively; never execute
  anything.
- `check(exercise_id, submission) -> tuple[bool, str]`: `exact` is a
  case-insensitive stripped compare; `keywords` passes if all expected
  keywords appear; `rustc` uses the existing `RustCompileOracle`. Every check
  records `LearnerStore.record_attempt(concept_id, correct, lesson_id=<first source_ref or "">, submission=...)`.
- `next_hint(exercise_id, wrong_attempts)` reveals hints progressively.
- CLI under `ghost learn`: `exercises <concept_id> [--count N] --model M`,
  `answer <exercise_id> <submission>`, and `hint <exercise_id>`.

**Tests** (scripted chat): generation links source_refs; answers live outside
the exercise record; exact/keyword checks record attempts; a progressive hint
sequence; invalid model JSON yields zero exercises without crashing.

---

## TG4: deadline-aware review scheduling

**Goal:** test dates influence what to study next (S05).

**Decided design:** a `LearnerStore.set_deadline(concept_ids: list[str], due_at: float, label: str)`
store in `.ghost/learner/deadlines.json`. `next_activity(now)`: among due
concepts, concepts linked to a deadline within 14 days come first, ordered by
nearest deadline, then lowest mastery. The existing behavior applies when
there are no deadlines. CLI: `ghost learn deadline <label> --date YYYY-MM-DD --concepts a,b`.

**Tests:** a concept with a nearer deadline is chosen first; past deadlines
are ignored; without deadlines the ordering is unchanged.
