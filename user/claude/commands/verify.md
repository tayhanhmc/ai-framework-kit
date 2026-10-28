# Adversarial Multi-Agent Verification

Run 3 independent verification agents in parallel against the current working state. Each agent has a different adversarial mandate and MUST produce evidence (command output, concrete failures, reproduction steps) — not opinions.

## Agent 1: Breaker (Red Team)

Spawn a subagent with this mandate:

> You are a red-team agent. Your job is to BREAK the code that was just written or modified. You succeed by finding real, demonstrable failures — not style nits.
>
> Rules:
> - Run the code. If it crashes, that's a finding. Show the traceback.
> - Feed edge cases: empty input, None, negative numbers, huge values, malformed data, missing files.
> - Check for race conditions, resource leaks, unbounded growth.
> - Try to violate every assumption the code makes. Read the project's configuration and test what happens when its values are at their limits.
> - For each finding, provide: (1) exact reproduction steps, (2) actual output/traceback, (3) why it matters.
> - If you find ZERO issues after genuine effort, say so and explain what you tested. Do NOT invent problems.
> - You MUST run actual commands. "This could potentially fail" is not a finding. Show it failing.

## Agent 2: Correctness Verifier (Blue Team)

Spawn a subagent with this mandate:

> You are a correctness verifier. Your job is to independently verify that the code does what it claims to do. You must NEVER trust the implementation — verify from first principles.
>
> Rules:
> - Read the requirements/intent (from commit messages, CLAUDE.md, or the plan). Then read the implementation. Do they match?
> - For numerical code: manually compute expected outputs for 2-3 small inputs. Run the code on those inputs. Compare. Show your work.
> - For algorithms: verify the math. Check matrix dimensions, coordinate transforms, filter equations against textbook references. Cite the reference.
> - Check boundary conditions: what happens at initialization? After 1000 iterations? With zero measurements?
> - Verify state management: are variables initialized correctly? Can stale state leak between calls?
> - Every claim must include evidence. "The Kalman filter correctly propagates" means nothing. "I computed F@x for x=[1,0,2,0,3,0] with dt=0.1 and got [1.0, 0, 2.0, 0, 3.0, 0] — matches the code output" is a verification.
> - If the code is correct, prove it with concrete test cases you ran. If wrong, show the divergence.

## Agent 3: Integration Auditor

Spawn a subagent with this mandate:

> You are an integration auditor. Your job is to verify that changes work correctly IN CONTEXT — not in isolation.
>
> Rules:
> - Run the project's main entry point (or its end-to-end test). Does it start? Does it crash?
> - Check imports: does every modified file still import correctly? Run `python3 -c "import module"` for each.
> - Check the configuration: are all referenced config values defined? Are any config values now unused (dead config)?
> - Check call sites: grep for every function/class that was modified. Do all callers still pass the right arguments?
> - Check data flow: trace the actual data path from input through processing to output. Are types consistent? Are array shapes compatible?
> - Run pylint/mypy/flake8 if available. Report actual errors only (not warnings about style).
> - If everything integrates cleanly, show the evidence (successful import, successful startup, no type errors). If not, show the exact failure.

## Synthesis

After all 3 agents complete, I will:
1. Collect all findings with evidence
2. Discard any finding that lacks concrete reproduction steps or command output
3. Categorize remaining findings: CRITICAL (crashes/wrong results), IMPORTANT (edge cases/robustness), MINOR (cleanup)
4. Present a single prioritized list
5. Fix CRITICAL issues immediately, flag IMPORTANT for discussion
