# Evaluating context changes

Use after material changes to routing, workflows or the model used for product work.
Script tests prove detector behavior; they do not prove better agent outcomes.

## Procedure

1. Select a small set of real, repeatable tasks with known acceptance criteria. Include
   an ordinary fix needing no special docs, a non-obvious domain constraint, a decision
   whose rationale is absent from code, stale context, and a multi-session handoff.
2. Compare minimal instructions, the current framework and the proposed change. Keep
   the model, tool permissions, code revision, dependencies and task prompt constant;
   change only the context treatment. Use disposable checkouts and no external writes.
3. Run repeated trials in independent sessions and reset task state between trials.
   Prevent access to other trials' patches, transcripts and learned memory. Start with
   a few trials per task; treat small differences as inconclusive. Fit runs to the
   authorized model budget; if live runs are unavailable, record that limitation.
4. Grade the final code or artifact against acceptance checks independently of the
   agent's self-report. Read traces to check whether the agent found the relevant
   constraint, followed stale guidance, or loaded irrelevant documents.
5. Record one row per trial: task, treatment, model/version, success, tokens, elapsed
   time, unnecessary questions, stale-context mistakes, and evidence location. Include
   failed and blocked trials. Keep raw evidence in the existing analysis/task folders.
6. Retain extra context when it prevents consequential failures or improves measured
   outcomes enough to justify its cost. Prune or narrow ineffective rules; preserve
   rare critical constraints unless their risk is covered elsewhere.

The framework kit's eval folder implements this procedure (it stays with the kit, not in
the framework). Its default mode checks every fixture without a model: each unmodified
task fails its hidden acceptance tests, the reference solution passes, seeded boundary
and stale-context violations are caught, and the runtime fixture leaves no server or
bound port. Its live mode runs bounded `claude -p` trials per task, treatment (minimal,
framework, or one proposed change at a time) and trial in fresh checkouts, grades them
independently of the agent's report, and records one JSONL row per trial; its summary
calls a difference inconclusive unless it is significant with at least five trials per
arm. Hidden tests remain readable from the trial's shell, so it is a measuring aid, not
a sealed benchmark.

No task-specific benchmark result should become a general rule without checking its
scope. Do not claim an improvement from lint, coverage or instruction-loading probes.
