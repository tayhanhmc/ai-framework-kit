# Cross-Verification Between Two Agents

Spawn two agents that independently solve the same problem, then compare their outputs. Disagreement reveals bugs or ambiguity.

## Usage
Provide the task as the argument: /cross-verify "Compute the expected pose for a tag at position [0, 0, 0.5]m with 15-degree rotation"

## Agent A: Solver

> Solve this task independently: $ARGUMENTS
>
> Show all your work. Write and run code to compute the answer. Print intermediate values. Do NOT look at any existing implementation — solve from first principles using library docs and math.
>
> Output your final answer in a structured format (JSON or clearly labeled values).

## Agent B: Solver (different approach)

> Solve this task independently: $ARGUMENTS
>
> Show all your work. You MUST use a DIFFERENT method than the obvious approach. If the obvious way is matrix multiplication, use quaternions. If the obvious way is OpenCV's solvePnP, use a manual DLT. If the obvious way is a loop, use vectorized NumPy.
>
> Output your final answer in the same structured format.

## Comparison

After both complete:
1. Compare outputs numerically (within tolerance for floating point)
2. If they agree: high confidence the answer is correct
3. If they disagree: investigate which is wrong (or if the problem is ambiguous)
4. Use disagreements to find bugs in the actual codebase implementation
