# Ghost qualification report — campaign 2026-10b

Date: 2026-10-10 21:34 +03:00 · HEAD: 4ae95ceef6228aa45235454d03b764a715324b26 (master, clean tree)
Full suite (this run): `.venv/Scripts/python.exe -m pytest tests -q` → **"1403 passed in 100.20s (0:01:40)"**. After the meta-orchestrator's review fixes (rustc resolution, 30fdddc + 3878043): **1406 passed** at 3878043.

> Reviewed and corrected by the meta-orchestrator on 2026-10-10: five factual errors in the agent draft were fixed (rustc/tesseract/pdftotext presence, `recommend_format` test, `learn path` prerequisites, and an overclaimed L3 row for local models). Corrections are marked *(corrected)*.

Evidence levels (implementation.md §8): L1 contract/unit test · L2 integration/scenario test through a real entry point · L3 live qualification (real model/provider/device, recorded artifacts) · L4 personal usefulness over time · NONE. All claims below cite production path:line plus a named test, or a command actually run. Docs are context, not evidence. The only L3 evidence that exists today is `ghost bench` (`.ghost/bench/*/report.json`); Telegram, Huawei and DeskSentinel have no live evidence.

## S01 — Telegram ("I want to talk to you on Telegram")

| Bullet (short) | Level | Evidence | Remaining gap |
|---|---|---|---|
| Authenticated incoming messages reach the same task service as CLI/UI | L2 | `ghost_agent/openjarvis_bridge/telegram.py:248-288` routes to `assistant.ask` / `queue_sandbox_outcome` (OutcomeWorker); `tests/test_openjarvis_telegram.py::test_explicit_build_request_can_queue_sandbox_work`, `::test_build_queue_is_opt_in` | chat path is a read-only assistant receipt, not the CLI task queue (by design); not exercised by a live bot |
| Unpaired sender cannot operate the agent | L1 | `telegram.py:242-246` (`authorized`) + pairing `:24-33`; `tests/test_openjarvis_telegram.py::test_unpaired_sender_and_group_never_invoke_agent` | — |
| Retries do not repeat task creation | L1 | receipt-ledger dedup `telegram.py:252-260`; `tests/test_openjarvis_telegram.py::test_duplicate_receipt_survives_restart`, `::test_redelivered_message_with_uncertain_receipt_is_not_reprocessed` | — |
| Interruptions/restarts preserve continuity | L1 | `reconcile_receipts` `:178-204`, backoff `_monitor_transport` `:291-318`, poller lock `:371-374`; `tests/test_openjarvis_telegram.py::test_reconcile_marks_stuck_sending_receipt_uncertain_once`, `::test_transient_error_is_retried_with_growing_backoff` | no live reconnect-over-real-network run |
| Sending, reconnecting, rotation, rate limits, health | L1 (partial) | `check_token_rotation`/`token_fingerprint`/`token_health` `:52-115`; `tests/test_telegram_readiness.py::test_rotate_token_check_*`, `::test_health_check_next_step_progression`, `::test_status_output_never_leaks_token_value` | rate limiting is backoff-only; no explicit 429 handling |
| Owner performs only provider/account steps Ghost cannot | L1 | token held as fingerprint only; `live_check` `telegram.py:133-167`; `tests/test_telegram_readiness.py::test_no_network_call_when_token_absent` | real bot token is owner-supplied |
| Real success recorded only after live test; mock labeled | NONE | `live_check` code + runbook `docs/engineering/telegram-live-qualification.md`; mocked-200 test `tests/test_telegram_readiness.py::test_live_check_success_recorded_with_mocked_200`; **no live artifact** (no telegram-live-check.json) | owner supplies bot token and runs the live check |

## S02 — Huawei watch ("My watch is a Huawei")

| Bullet (short) | Level | Evidence | Remaining gap |
|---|---|---|---|
| Provider route records sources, checked date, prerequisites, limitations | L1 | `ghost_agent/huawei_route.py:112-127`; `tests/test_huawei_route.py::test_provider_route_records_metadata`, `tests/test_tf1_tf4_huawei.py::test_register_route_set_state_ingest_trend` | route metadata seeded manually, not auto-discovered |
| Revoked/unavailable access → honest state | L1 | `set_state` `huawei_route.py:129-143` (emits "not available" event); ingest refuses non-active `:196-197`; `tests/test_huawei_route.py::test_honest_revoked_state`, `::test_ingest_requires_active_route`, `tests/test_tf1_tf4_huawei.py::test_status_honest_not_configured` | — |
| Observations preserve units, timestamps, tz, freshness, provenance | L1 | `ingest` `huawei_route.py:192-235`, `fresh` `:243`; `tests/test_huawei_route.py::test_ingest_preserves_units_timezone_provenance`, `::test_default_unit_applied_when_missing` | — |
| Inactivity reminders need recent data; no data = unknown | L1 | `inactivity_status` `huawei_route.py:253-265`; `tests/test_huawei_route.py::test_inactivity_from_fresh_low_activity`, `::test_freshness_and_unknown_when_no_data` | — |
| Owner corrections (illness, travel, unworn, rest day) | L1 | `correct_context`/`active_corrections` `huawei_route.py:269-294`; `tests/test_huawei_route.py::test_owner_context_correction_shadows_data`, `tests/test_tf1_tf4_huawei.py::test_correction_appears_in_context` | — |
| Trends distinguish measured changes from hypotheses | L1 | `trend_summary` `huawei_route.py:297-315` + `supports_prediction=False` `:317-320`; `tests/test_huawei_route.py::test_trend_summary_no_prediction`, `::test_trend_summary_unknown_without_data` | — |
| Health data excluded from unauthorized endpoints/logs | L1 | default-deny allow-list + privacy event log `huawei_route.py:322-404`; `tests/test_huawei_route.py::test_privacy_blocks_unauthorized_endpoint`, `tests/test_health_privacy.py::test_guard_denies_raw_value_routes_by_default`, `tests/test_tf1_tf4_huawei.py::test_purge_all_and_event_privacy` | — |
| Live Huawei data | NONE | synthetic/manual only: default provenance `huawei_synthetic` `huawei_route.py:217`, injectable `ingest_fn` | owner needs Huawei developer credentials + user authorization |

## S03 — DeskSentinel (ESP32 pan/tilt)

| Bullet (short) | Level | Evidence | Remaining gap |
|---|---|---|---|
| Adapter based on inspected firmware/protocol evidence | NONE | `ghost_agent/integrations/desksentinel.py` is an explicit [MOCKUP] placeholder; `tests/test_mvp_health_and_adapters.py::test_desksentinel_get_status_is_mock`, `::test_desksentinel_set_target_raises_connection_error` | firmware/protocol do not exist yet; honest placeholder |
| Simulator validates limits, disconnect, cancellation | L1 (partial) | `SimulatedPanTilt` + `DeviceCommandGate` `ghost_agent/device_control.py:62-130`; `tests/test_tf3_device_control.py::test_refused_outside_limits`, `::test_refused_rate_too_high`, `::test_refused_disconnected`, `::test_disconnect_moves_to_safe`, `::test_emergency_stop_wins` | no wire-encoding validation, no mid-command cancellation test |
| Device identity and pairing | L1 | `ghost_agent/devices.py:13-14,52-102`; `tests/test_tf2_devices.py::test_pair_revoke_transitions`, `::test_cli_pair_revoke_status`; gate `tests/test_tf3_device_control.py::test_refused_not_paired` | — |
| Commands within limits; safe state on comm loss | L1 | `device_control.py:95-130`; `SimulatedPanTilt.disconnect` `:78-81`; `tests/test_tf3_device_control.py::test_success_emits_event_and_moves`, `::test_disconnect_moves_to_safe` | no real hardware; safe state unproven on actual device |
| Flashing distinct from routine control | NONE | only a firmware version string on the device record (`devices.py:28`); no flashing workflow | requires real firmware first |
| Focus interventions follow owner mode + immediate override | NONE | no owner-override mechanism in `device_control.py`/`devices.py`; emergency stop blocks but does not override | needs focus-signal capability and owner mode config |
| Another programmable device enters same workflow | L1 | generic registry (transports, capabilities, constraints, freshness) `devices.py:104-118`; CLI `ghost device register/pair/simulate`; `tests/test_tf2_devices.py` (6 tests) | demonstrated only via registry, not a second real device |

## S04 — Learn Rust / practise cyber

| Bullet (short) | Level | Evidence | Remaining gap |
|---|---|---|---|
| Exercise references objectives and prerequisite concepts | L1 (partial) | exercises generated per concept with cited refs `ghost_agent/study.py:84-117`; prerequisite-ordered paths via `LearningEngine.generate_path` (`ghost learn path`, cli.py ~4239) *(corrected)*; `tests/test_tg3_study.py::test_generation_links_source_refs_and_hides_answer` | generated exercises do not yet cite their prerequisite concepts |
| Rust compiles under recorded toolchain; wrong solutions fail | **L3** *(corrected)* | RustCompileOracle `ghost_agent/learner.py` (rustc probe, version recorded, rustup `~/.cargo/bin` fallback added in 30fdddc); `tests/test_learner.py::test_assess_wrong_solution_rejected`, `tests/test_rustc_resolution.py`; **live check on this host: rustc 1.92.0 compiled a correct submission and rejected a type-error submission** | — |
| CTF challenges solvable, reproducible, isolated, resettable | L1 | `ghost_agent/challenges.py:126-201` (create refuses starter that passes oracle; temp-dir oracle with timeout); reset `:178-190`; `tests/test_learner.py::test_reset_makes_ctf_solvable_again` | no real CTF platform integration |
| Solution/oracle separate from learner material | L1 | learner dir gets prompt+starter only; oracle/solution under `.ghost/learner/protected/<id>/` `challenges.py:19-23,161-176`; `tests/test_tg3_study.py::test_generation_links_source_refs_and_hides_answer` | — |
| Progress survives restarts; responds to answers/misconceptions | L1 | persistent `.ghost/learner/` JSON with atomic writes/locks `learner.py:71-124`; attempt→mastery/streak/interval `:178-213`; `tests/test_learner.py::test_mastery_tracking_and_spaced_interval`, `::test_feedback_driven_next_activity`, `::test_corrupt_state_fails_closed` | — |
| Format effectiveness evaluated; can switch/stop | L1 | FormatStats per-format counters; `recommend_format` flags unused formats `learner.py:392-474`; `tests/test_tk3_format_stats.py` *(corrected)* | not evaluated over real study sessions (L4) |
| Missing toolchain → concrete dependency plan, no silent install | L1 | writes `.ghost/learner/dependency_plan.md` and returns explicit failure `learner.py:336-371`; extractors raise ExtractionUnavailable, never install (`extractors.py:50-69`); `tests/test_learner.py::test_rust_oracle_missing_compiler`, `tests/test_extractors.py::test_extract_pdf_missing_tool_raises` | — |

## S05 — School lessons / study workspace

| Bullet (short) | Level | Evidence | Remaining gap |
|---|---|---|---|
| Source identity, page/section refs, version, extraction quality | L1 | `ghost_agent/knowledge_base.py:54-65` (DocChunk: source, sha256, version, superseded, section, page, extraction_quality); version bump on re-ingest `:189-199`; `tests/test_tg2_kbase.py::test_reingest_changed_source_supersedes_old_chunks`, `::test_markdown_section_capture`; `tests/test_extractors.py::test_extract_pdf_splits_pages` | — |
| Optional OCR adapter | L1/L2 | `ghost_agent/extractors.py:47-83` (pdftotext/tesseract optional, missing → ExtractionUnavailable); `tests/test_extractors.py::test_extract_image_ocr_quality`, `::test_ingest_image_goes_through_ocr`; real `pdftotext` integration test ran on this host (TP1) *(corrected: tesseract 5.5 and pdftotext are installed)* | no live OCR run on a real scanned lesson recorded |
| Answers/revision material cite supplied passages | L1 (partial) | generation grounded in retrieved chunks with `source_refs` `ghost_agent/study.py:88-91,159`; lesson citation `learner.py:281-284`; `tests/test_tg3_study.py::test_no_sources_yields_empty_refs_and_prompt_note` | no claim-level verbatim passage citation for exercise answers (research path has it; study path does not) |
| Unanswered questions and conflicts identified, not invented | L1 (partial) | context conflicts surfaced `cli.py:2070,4564-4576`; generator returns zero exercises on invalid model output `study.py:119-134` | no dedicated unanswered-question register in the study path |
| Retrieval, correction, replacement, deletion + derived-content invalidation | L1 | supersede on re-ingest `knowledge_base.py:181-199`; delete cascades to memory + emits kbase.source_deleted `:302-349`; `tests/test_tg2_kbase.py::test_delete_source_removes_chunks_and_invalidates_memory`, `::test_reingest_unchanged_source_is_noop` | — |
| Quiz answers inform next session | L1 | check → `learner.record_attempt` → `next_activity` picks lowest-mastery/due concept `study.py:164-190`, `learner.py:178-213`; `tests/test_learner.py::test_feedback_driven_next_activity`, `tests/test_tg3_study.py::test_exact_and_keyword_checks_record_attempts` | no quiz-session memory beyond spaced-repetition state |
| Test dates, quiet periods, study time influence scheduling | L1 (partial) | deadline-aware `next_activity` `learner.py:216-261`, CLI `cli.py:4331-4345`; `tests/test_tg4_deadlines.py::test_nearer_deadline_chosen_first` (+2) | quiet hours not coupled to the study scheduler (only checkins/overnight) |
| Uploaded text cannot grant permissions or alter supervisor | L1 | kbase content injected only as user-role messages `orchestrator.py:580-592`, `cli.py:3468`; system prompt built separately; retrieval capped (`MAX_RETRIEVE_CHARS` `knowledge_base.py:51`) | no explicit prompt-role-isolation test — indirect coverage only |

## S06 — Idle time and overnight initiative

| Bullet (short) | Level | Evidence | Remaining gap |
|---|---|---|---|
| Work selected for stated benefit, not idle alone | L1 | composite score gates auto-queue `ghost_agent/proactive.py:633`; `tests/test_proactive.py`, `tests/test_initiative.py` | owner-visible benefit statements not yet evaluated live over days (L4) |
| Job checkpoints survive interrupted sessions | L1/L2 | expired-lease pause + `worker.interrupted` event `ghost_agent/worker.py:68-80`; checkpoint validation `orchestrator.py:388`; `tests/test_job_leases.py::test_expired_lease_paused_at_startup_with_event`, `tests/test_owner_return_yield.py::test_resume_continues_from_checkpoint_after_yield` | — |
| Owner return pauses heavy work within measured deadline | L1 | `yield_on_owner_return` `ghost_agent/proactive.py:348`; worker maps yield→paused; `tests/test_owner_return_yield.py::test_unattended_job_yields_when_owner_returns`, `::test_worker_maps_yield_to_paused` | no mid-job activity sampling (idle sampled once per cycle) |
| Quiet hours suppress notifications; morning digest | L1 | `ghost_agent/overnight.py:38-53,172-261`; `ghost_agent/digest.py`; `tests/test_overnight.py::test_quiet_hours_skip_proactive_run_worker`, `::test_digest_written_inside_window`; `tests/test_digest.py` | — |
| Inference/network/compute/disk/concurrency limits enforced | L1 (partial) | admission via ResourceMonitor `ghost_agent/runtime.py:139-165`; inference budget `proactive.py:586-597`; one-worker lock `worker.py:61`; `tests/test_proactive_inference_budget.py` | no GPU/network/cost ceilings; concurrency fixed at 1 |
| Offline PC reported as unavailable | L1 (partial) | coverage gap "(PC asleep/offline or Ghost not running)" `overnight.py:256-260`; `tests/test_overnight.py::test_coverage_reports_gap_longer_than_3x_interval` | gap is reported, not measured live |
| Another enrolled device may execute eligible work later | NONE | not required for first release; no distributed executor | deferred by spec |

## S07 — Open-ended discovery from unfamiliar context

| Bullet (short) | Level | Evidence | Remaining gap |
|---|---|---|---|
| Pipeline processes new context without product-name conditionals | L1 (partial) | opportunity path is generic (`ghost_agent/proactive.py`, `autonomy.py`); code inspection finds no scenario-name conditionals | no test asserting absence of such conditionals |
| Recorded reasoning links idea to observations and goals | L1 | evidence_items/goal_ids/trigger on tick records `proactive.py:543-568`; `ghost why` `cli.py:3555`; `tests/test_proactive.py::test_tick_record_has_deterministic_evidence_goal_ids_trigger` | — |
| Contradictory preferences suppress inappropriate ideas | NONE | similarity suppression exists `proactive.py:161-181,613-620`; contradictory-preference suppression not found | needs preference-conflict modeling |
| Rejected ideas do not reappear unchanged | L1 | `find_suppressed` `proactive.py:147` + suppressed_by_similarity; `tests/test_proactive.py::test_rejected_idea_is_suppressed`, `::test_reworded_similar_rejected_idea_is_suppressed_and_not_queued` | — |
| Suitable existing tool reused | L1 | reuse_inventory `ghost_agent/capability_acquisition.py:299`; `tests/test_capability_acquisition.py::test_reuse_inventory_prefers_existing` | — |
| One targeted question while independent work continues | L1 | needs_input notification, one question per tick `proactive.py:655-660`; `tests/test_proactive.py::test_needs_input_with_question_notifies_once_across_ticks` | — |
| Worker does not receive evaluator's desired feature as hidden instruction | NONE | generic untrusted-context framing only (`proactive.py` system prompt; `protocol_benchmark.py:27` untrusted_instruction case) | no held-out evaluation performed |

## S08 — Maintenance and self-improvement

| Bullet (short) | Level | Evidence | Remaining gap |
|---|---|---|---|
| Real failure linked to proposed change | L1 | `SelfRepairProposal.linked_failure` `ghost_agent/self_repair.py:126`; `tests/test_self_repair_oj15.py::test_propose_records_benefit_and_linked_failure` | no live recurring-failure → candidate run recorded end to end |
| Evidence identifies candidate, environment, tests, results | L1 | environment_identity + evaluator digest binding `self_repair.py:77-114,338-406`; `tests/test_self_repair_environment.py`, `tests/test_self_repair.py::test_forged_evaluator_label_with_correct_digest_cannot_verify` | — |
| Candidate cannot modify evaluator/permissions/evidence approving itself | L1 | nonce + pre/post digest binding, protected roots `self_repair.py:214-238,257-269`; `tests/test_self_repair.py::test_candidate_mutation_during_evaluation_rejected`, `::test_hand_entered_evidence_cannot_verify` | — |
| Failed update returns to known working version | L1 | hash-verified backup restore `ghost_agent/promotion.py:196-243`; `tests/test_promotion.py::test_promotion_preview_apply_and_rollback`, `tests/test_self_repair_oj15.py::test_applied_then_rollback` | — |
| Rejected/unchanged candidates do not loop forever | L1 | rejected-diagnosis refusal `self_repair.py:199-203`; `tests/test_self_repair_oj15.py::test_endless_repair_loop_prevented_on_rejected` | — |
| Updating preserves jobs, memory, integration config | L1 (partial) | promotion excludes `.ghost` and touches only approved files `promotion.py:81` (scope-locked); `tests/test_promotion_policy.py::test_self_repair_protected_path_refused` | no explicit "jobs survive update" test |
| Core authority changes use separate controlled path | L1 | protected_paths policy + PROTECTED_PATH_MESSAGE `promotion.py:19-29`, `self_repair.py:486-488`; `tests/test_promotion_policy.py` | — |

## Initial acceptance (implementation.md section 8)

| Requirement | Status | Evidence |
|---|---|---|
| S01–S08 traced to implementation and evidence, live limits shown explicitly | met | this report; every scenario above carries a table with levels and gaps |
| At least one useful opportunity arises from context without the owner specifying the feature | partially met | test-level only: `tests/test_proactive.py::test_tick_queues_one_durable_sandbox_outcome`; no live multi-day proactive artifact |
| Existing upstream integration configured successfully + missing capability operational via general acquisition workflow | partially met | OpenJarvis pin checked at `ghost_agent/openjarvis_bridge/runtime.py:50-58` and `openjarvis_bridge/__init__.py:7-9` (commit a2eb7f0, version 1.0.4.dev250+ga2eb7f0); acquisition lifecycle `capability_acquisition.py`; `tests/test_openjarvis_integration.py`, `tests/test_capability_acquisition.py`. Live OpenJarvis inference IS measured (bench below). Second-capability acquisition demonstrated at test level only |
| A generated artifact passes meaningful independent checks | met (L3) | bench accepted_rate 0.875 = independent hidden tests passed on live model output; artifact `.ghost/bench/20261010_184845_09375/report.json`; test `tests/test_benchmark.py::test_accepted_when_hidden_test_passes` |
| Permitted overnight job survives interruption and yields useful result | partially met | test-level: `tests/test_owner_return_yield.py::test_resume_continues_from_checkpoint_after_yield`, `tests/test_scheduler_recovery.py::test_dead_worker_is_paused_without_replaying_handler`; no recorded live overnight run |
| Candidate improvement evaluated, activated, observed, rolled back | partially met | promotion observation window `promotion.py:119-127`; `tests/test_promotion_policy.py::test_promotion_status_moves_observing_to_operationally_verified`, `tests/test_self_repair_oj15.py::test_applied_then_rollback`; no live end-to-end run recorded |
| Owner can inspect, stop, correct, revoke, forget, override via working interfaces | met (L1/L2) | `ghost memory retract` `cli.py:3860`; `ghost emergency-stop` `cli.py:3148`; `ghost policy`; `ghost why` `cli.py:3555`; `tests/test_emergency_stop_holds.py`, `tests/test_tg1_memory.py`, `tests/test_memory_integrity.py` |
| Local model operation remains possible; weak-model limits reported honestly | partially met *(corrected)* | live jobs ran on local Ollama (gemma4:e4b) and their failures are recorded in `.ghost/bench/20261010_15*/` and `20261010_174206*/` outcome artifacts, but **no local-model benchmark run completed with a report** (runs were stopped for the timeout bug and to re-test fixes); README states small local models are not reliable |
| Applicable tests + integration acceptance pass, no fabricated completion | met | 1403 passed (this run); bench reports failure honestly (run 20261010_181845 scored 0.000 and is kept) |
| Every adopted component has exact upstream version + upgrade/recovery evidence | partially met | OpenJarvis pin recorded in bridge; recovery/upgrade evidence is docs-level (STATE.md), not test artifacts |
| New generic infrastructure has documented unmet upstream requirement | partially met | reuse decisions per subsystem in audit/ records; no single registry of "generic infra built here vs upstream gap" |

## Measured results

`ghost bench history` (run 2026-10-10 21:37, artifact dir `.ghost/bench/*/report.json`):

```
run_id                   model                  accepted  ghost   goals
20261010_192252_059375   zai-org/GLM-5.3-Flash  0.500     0.375   8
20261010_184845_09375    zai-org/GLM-5.3-Flash  0.875     0.500   8
20261010_182909_0625     zai-org/GLM-5.3-Flash  0.875     0.250   8
20261010_181845_10625    zai-org/GLM-5.3-Flash  0.000     0.000   8
```

(accepted = independent hidden acceptance tests; ghost = Ghost's own gate.)

What this establishes (L3): with a capable remote model via the pinned OpenJarvis route, 7 of 8 generated artifacts passed hidden acceptance tests in the best run; Ghost's own procedure completed about half of goals end to end; a provider-outage run is reported honestly as zeros. What it does NOT establish: task-completion rate on real personal tasks (bench goals are fixed small coding tasks), any L3 evidence for Telegram/Huawei/DeskSentinel, initiative usefulness over time (L4), or cost/latency budgets beyond per-run timing in report.json.

## Owner-only steps

- Provide a Telegram bot token and run the live qualification per `docs/engineering/telegram-live-qualification.md` (S01).
- Obtain Huawei developer credentials + user authorization, then register the route and connect live data (S02).
- Build/flash the DeskSentinel ESP32 firmware so the [MOCKUP] adapter can be replaced with inspected-protocol evidence; provide the hardware (S03).
- *(corrected)* None for toolchains: rustc 1.92.0 (rustup), pdftotext and tesseract 5.5 are installed and found. Optionally ingest real lesson PDFs/scans for a live S05 run.
- Decide update-policy thresholds and approve the first live promotion of a self-repair candidate (S08).
- Grant authority/credentials for any paid or remote inference budget beyond what was bench-tested, if desired (S06).

## Summary

Counts per level across all S01–S08 and section-8 rows (L1 partial = L1 with named gap):
- L1 (contract/unit, incl. partial): 34 rows
- L2 (integration through real entry point): 1 row (S01 message→task service)
- L3 (live qualification): 2 rows (bench accepted_rate; live rustc oracle check) *(corrected)*
- L4: 0
- NONE: 7 (S01 live test; S02 live data; S03 adapter-evidence, flashing, focus override; S07 contradictory-preference suppression; S07 hidden-instruction isolation — as marked in the tables above)

Top 5 remaining gaps agents could still build without external access:
1. S03: wire-encoding validation + mid-command cancellation in the pan/tilt simulator and command gate.
2. S03/S05: explicit prompt-role isolation test proving ingested text cannot alter system instructions; owner-override mode for device interventions.
3. S04/S07: model concept-prerequisite ordering for `ghost learn path`; direct test for `recommend_format`; contradictory-preference suppression in the opportunity pipeline; held-out hidden-instruction evaluation harness.
4. S08: a recorded live end-to-end self-repair → promotion → observe → rollback cycle on a real recurring failure.
5. S06: overnight-run live artifact proving an interrupted job resumed from checkpoint and produced a useful digest (currently test-level only).
