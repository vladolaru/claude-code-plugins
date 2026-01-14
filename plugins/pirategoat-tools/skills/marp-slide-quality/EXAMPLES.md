# Marp Slide Quality - Fix Examples

Real-world examples of analyzing and fixing common slide quality issues.

## Example 1: Analyzing a Presentation

**User request**: "Check the quality of my presentation"

**Step 1: Initial analysis**
```bash
uvx --from git+https://github.com/nibzard/slidegauge slidegauge presentation.md --text
```

**Output Example**:
```
Slide 1 (✓ 100) • no issues
Slide 2 (✓ 85) • content/too_long(15)
Slide 3 (❌ 65) • bullets/too_many(10), content/too_long(15), lines/too_many(10)
...
SUMMARY: avg=88.2 • passing=24/25 • threshold=70
```

**Response to user**:
> Your presentation scores 88.2/100 overall - pretty good! However, Slide 3 is failing (65/100) with multiple issues. Let me show you the details and suggest fixes.

**Step 2: Get details on failing slides**
```bash
uvx --from git+https://github.com/nibzard/slidegauge slidegauge presentation.md --json | jq '.slides[] | select(.score < 70)'
```

**Step 3: Present findings and suggest fixes**

## Example 2: Too Many Bullets

**Before** (Score: 65):
```markdown
# Hyperparameters Overview

**Learning Rate:**
- How much to update weights each step
- Most important hyperparameter
- Too high: unstable training
- Too low: slow learning or no improvement

**Batch Size:**
- Number of examples per update
- Larger: more stable, but slower
- Smaller: faster iterations, but noisier

**LoRA Rank:**
- Size of adapter matrices
- Default: 32, can go up to 128+
```

**Issues**:
- 9 bullets > max 6 (-10)
- 370 chars > 350 (-15)
- 16 lines > 15 (-10)

**After** (Split into 2 slides - Score: 100 each):

**Slide 1**:
```markdown
# Learning Rate & Batch Size

**Learning Rate:**
- How much to update weights each step
- Most important hyperparameter
- Too high: unstable training
- Too low: slow learning

**Batch Size:**
- Number of examples per update
- Larger: more stable, slower
- Smaller: faster, noisier
```

**Slide 2**:
```markdown
# LoRA Configuration

**LoRA Rank:**
- Size of adapter matrices
- Controls capacity of fine-tuning
- Default: 32
- Can increase up to 128+ for complex tasks

**Trade-off:**
Higher rank = more capacity but slower training
```

## Example 3: Code Too Long

**Before** (Score: 77):
```markdown
# Reward Functions

**Simple example (GSM8K math):**

```python
def compute_reward(question: str, answer: str) -> float:
    # Extract numerical answer
    predicted = extract_number(answer)
    correct = extract_number(ground_truth)

    # Base reward
    reward = 1.0 if predicted == correct else 0.0

    # Bonus for formatting
    if has_boxed_answer(answer):
        reward += 0.1

    return reward
```
```

**Issues**:
- 13 lines of code > 10 (-8)
- 32 chars content < 50 (-5)
- 19 total lines > 15 (-10)

**After** (Score: 95):
```markdown
# Reward Functions

Reward functions determine training signal quality.

**Example: Math problems (GSM8K)**

```python
def compute_reward(question: str, answer: str) -> float:
    predicted = extract_number(answer)
    correct = extract_number(ground_truth)

    reward = 1.0 if predicted == correct else 0.0
    if has_boxed_answer(answer):
        reward += 0.1
    return reward
```

Returns 1.0 for correct answers, 0.0 otherwise, with formatting bonus.
```

**Changes**:
- Added introductory context (fixes content/too_short)
- Removed some comments from code
- Condensed code formatting
- Added explanatory line after code

## Example 4: Title Too Long

**Before** (Score: 67):
```markdown
# Concrete Example: Math RL Environment

[20 lines of code...]
```

**Issues**:
- Title: 37 chars > 35 (-10)
- Code: 20 lines > 10 (-8)
- Total: 26 lines > 15 (-10)
- Content: 49 chars < 50 (-5)

**After** (Split into 2 slides):

**Slide 1** (Score: 100):
```markdown
# Math RL Environment

Example implementation for math problem solving.

The `MathEnv` class wraps math problems and provides:
- Question formatting with answer box requirement
- Format validation (checks for `\boxed{}`)
- Answer checking with symbolic math grading
```

**Slide 2** (Score: 95):
```markdown
# Math RL Environment: Code

```python
class MathEnv(ProblemEnv):
    def get_question(self) -> str:
        return self.problem + " Answer in \\boxed{}"

    def check_format(self, sample: str) -> bool:
        return has_boxed_answer(sample)

    def check_answer(self, sample: str) -> bool:
        answer = extract_boxed(sample)
        return safe_grade(answer, self.answer)
```

See full implementation in cookbook/envs/math_env.py
```

## Example 5: Content Too Short

**Before** (Score: 95):
```markdown
# What is Tinker?

**Tinker is a hosted API for fine-tuning**
```

**Issues**:
- Content: 38 chars < 50 (-5)

**After** (Score: 100):
```markdown
# What is Tinker?

**Tinker is a hosted API for fine-tuning large language models**

You send training requests to Tinker's service, and it handles distributed GPU compute.
```

## Example 6: Splitting Dense Slides

**Before** (Score: 75):
```markdown
# Training Data Requirements

**Supervised Learning:**
- Prompt/completion pairs
- Quality matters more than quantity
- Typical: 1K-100K examples
- Format: conversational messages or raw text

**Reinforcement Learning:**
- Just prompts (model generates completions)
- Reward function or preference data
- More data usually needed (10K-1M prompts)
- Can generate multiple completions per prompt
```

**Issues**:
- 8 bullets > 6 (-10)
- 353 chars > 350 (-15)

**After** (2 slides):

**Slide 1**:
```markdown
# Training Data: Supervised Learning

**What you need:**
- Prompt/completion pairs
- Quality over quantity
- Typical: 1K-100K examples

**Format:**
- Conversational messages
- Raw text completions
- Instruction-following pairs
```

**Slide 2**:
```markdown
# Training Data: Reinforcement Learning

**What you need:**
- Prompts only (no completions)
- Reward function or preference data
- More data: 10K-1M prompts

**Strategy:**
Generate multiple completions per prompt to compare and learn from
```

## Example 7: Complete Workflow

**User**: "My slides need improvement, can you check them?"

**Step 1**: Run initial analysis
```bash
uvx --from git+https://github.com/nibzard/slidegauge slidegauge slides.md --text
```

**Step 2**: Report findings
> I analyzed your 85 slides. Overall score: 93.5/100
>
> **Good news**: 83/85 slides are passing (≥70)
>
> **Needs attention**:
> - Slide 16 (65/100): Too many bullets, content, and lines
> - Slide 68 (67/100): Long title, excessive code
>
> Would you like me to fix these issues?

**Step 3**: Get user approval, then fix

**Step 4**: Validate improvements
```bash
uvx --from git+https://github.com/nibzard/slidegauge slidegauge slides.md --text
```

**Step 5**: Report results
> Fixed! New score: 96.2/100
> - All slides now passing
> - Split slide 16 into 2 focused slides
> - Simplified slide 68 code example

## Tips for Common Scenarios

### When to split vs condense

**Split when**:
- Slide covers 2+ distinct topics
- Bullets can be naturally grouped
- Code example is inherently complex

**Condense when**:
- Content is repetitive
- Bullets can be merged
- Examples can be simplified

### Preserving user intent

**Don't**:
- Change technical accuracy
- Remove important details
- Alter code behavior

**Do**:
- Improve formatting
- Split for clarity
- Add context where needed

### Getting approval

**For minor fixes** (typos, formatting):
- Just fix and report

**For major changes** (splitting, removing content):
- Show before/after
- Explain rationale
- Get user approval first
