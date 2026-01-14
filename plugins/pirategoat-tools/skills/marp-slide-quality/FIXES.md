# Quick Fix Guide

Fast reference for fixing common SlideGauge issues.

## Quick Lookup

| Issue | Deduction | Quick Fix |
|-------|-----------|-----------|
| `bullets/too_many` | -10 | Split slide or merge bullets |
| `content/too_long` | -15 | Remove ~N chars or split |
| `lines/too_many` | -10 | Condense or split |
| `title/too_long` | -10 | Shorten to ≤35 chars |
| `code/too_long` | -8 | Simplify or split |
| `content/too_short` | -5 | Add context |
| `title/required` | -30 | Add # heading |
| `accessibility/alt_required` | -20 | Add alt text |

## Fix Patterns

### Too Many Bullets (>6)

**Strategy 1: Group and split**
```markdown
<!-- BEFORE: 9 bullets -->
# Big Topic
- Point 1
- Point 2
- Point 3
- Point 4
- Point 5
- Point 6
- Point 7
- Point 8
- Point 9

<!-- AFTER: Split into 2 slides -->
# Big Topic: Part 1
- Point 1
- Point 2
- Point 3
- Point 4

---

# Big Topic: Part 2
- Point 5
- Point 6
- Point 7
- Point 8
- Point 9
```

**Strategy 2: Consolidate**
```markdown
<!-- BEFORE: 8 bullets -->
- Too high: unstable
- Too low: no learning
- Just right: good convergence

<!-- AFTER: 4 bullets -->
- Too high: unstable training
- Too low: no learning
```

### Content Too Long (>350 chars)

**Strategy 1: Remove fluff**
```markdown
<!-- BEFORE: 380 chars -->
In this section we will discuss the various different types of hyperparameters
that you might want to consider when you are fine-tuning your model...

<!-- AFTER: 280 chars -->
Key hyperparameters for fine-tuning:
- Learning rate
- Batch size
```

**Strategy 2: Split slide**
```markdown
<!-- BEFORE: One 400-char slide -->
# All About Training
[Lots of content about both SL and RL...]

<!-- AFTER: Two slides -->
# Supervised Learning
[SL content only]

---

# Reinforcement Learning
[RL content only]
```

### Too Many Lines (>15)

**Usually combined with other issues**:
1. Fix bullets/content first
2. Lines often resolve automatically
3. If not, remove blank lines or condense formatting

### Title Too Long (>35 chars)

```markdown
<!-- BEFORE: 47 chars -->
# A Comprehensive Introduction to Machine Learning Concepts

<!-- AFTER: 31 chars -->
# Intro to ML Concepts

<!-- Or use subtitle -->
# ML Concepts
## A Comprehensive Introduction
```

### Code Too Long (>10 lines)

**Strategy 1: Remove comments**
```python
# BEFORE: 13 lines
def compute_reward(question: str, answer: str) -> float:
    # Extract the numerical answer from the response
    predicted = extract_number(answer)
    # Get the ground truth answer
    correct = extract_number(ground_truth)

    # Compute base reward
    reward = 1.0 if predicted == correct else 0.0

    # Add bonus for proper formatting
    if has_boxed_answer(answer):
        reward += 0.1

    return reward

# AFTER: 7 lines
def compute_reward(question: str, answer: str) -> float:
    predicted = extract_number(answer)
    correct = extract_number(ground_truth)
    reward = 1.0 if predicted == correct else 0.0
    if has_boxed_answer(answer):
        reward += 0.1
    return reward
```

**Strategy 2: Show excerpt**
```python
# BEFORE: 20 lines of full implementation

# AFTER: Key parts only
class MathEnv(ProblemEnv):
    def check_answer(self, sample: str) -> bool:
        answer = extract_boxed(sample)
        return safe_grade(answer, self.answer)

# See full implementation in cookbook/envs/math_env.py
```

**Strategy 3: Split into multiple slides**
```markdown
# Slide 1: Interface
```python
class Env:
    def initial_observation(self) -> list[int]:
        """Return initial prompt tokens"""

    def step(self, action: int):
        """Returns: (observation, reward, done)"""
```

---

# Slide 2: Implementation Example
```python
class MathEnv(Env):
    def step(self, action: int):
        self.completion += [action]
        if is_complete(self.completion):
            reward = self.check_answer()
            return [], reward, True
        return [], 0.0, False
```
```

### Content Too Short (<50 chars)

**Add context before/after code**:
```markdown
<!-- BEFORE: 32 chars -->
# Simple Example

```python
result = model.predict(x)
```

<!-- AFTER: 85 chars -->
# Simple Example

Use the model to make predictions on new data:

```python
result = model.predict(x)
```

This returns probability scores for each class.
```

### Missing Alt Text

```markdown
<!-- BEFORE -->
![](diagram.png)

<!-- AFTER -->
![Architecture diagram showing client-server communication](diagram.png)
```

## Combined Issues

### Pattern: Too Many Bullets + Content Too Long + Too Many Lines

**Root cause**: Slide is trying to cover too much

**Fix**: Split into 2-3 focused slides

```markdown
<!-- BEFORE: Score 65 -->
# Everything About Training

**Supervised Learning:**
- Uses labeled data
- Trains on examples
- Optimizes cross-entropy
- Needs high-quality data

**Reinforcement Learning:**
- Uses rewards
- Explores environment
- Optimizes policy
- Needs reward function

**Best Practices:**
- Start small
- Monitor metrics
- Save checkpoints

<!-- AFTER: 3 slides, all score 100 -->
# Supervised Learning

- Uses labeled examples
- Optimizes cross-entropy loss
- Quality matters more than quantity

Key: (prompt, expected_output) pairs

---

# Reinforcement Learning

- Uses reward signals
- Optimizes policy for maximum reward
- Explores action space

Key: Reward function defines success

---

# Training Best Practices

- Start with small model/dataset
- Monitor loss and accuracy metrics
- Save checkpoints frequently
```

### Pattern: Code Too Long + Content Too Short + Too Many Lines

**Root cause**: All code, no context

**Fix**: Add explanatory text, simplify code

```markdown
<!-- BEFORE: Score 77 -->
# Implementation

```python
[20 lines of complex code...]
```

<!-- AFTER: Score 95 -->
# Implementation Overview

The training loop handles batching and gradient updates:

```python
for batch in dataset:
    loss = model.forward(batch)
    loss.backward()
    optimizer.step()
```

Key steps: forward pass, compute gradients, update weights.
```

## Decision Tree

```
Is slide failing (< 70)?
├─ Yes → Fix immediately
│  ├─ Multiple issues? → Consider splitting
│  └─ Single issue? → Apply targeted fix
└─ No → Is it < 80?
   ├─ Yes → Suggest improvements
   └─ No → Leave as-is unless user requests
```

## Split vs Fix Guidelines

**Split when**:
- Score < 65 with multiple issues
- Slide has 2+ distinct topics
- Fixes would require removing important content

**Fix when**:
- Score 65-79 with 1-2 issues
- Content is inherently related
- Simple edits (word reduction, merging bullets)

## Validation Checklist

After making fixes:
- [ ] Re-run SlideGauge
- [ ] Confirm score improved
- [ ] Check no new issues introduced
- [ ] Verify slide still makes sense
- [ ] Ensure user's message preserved
