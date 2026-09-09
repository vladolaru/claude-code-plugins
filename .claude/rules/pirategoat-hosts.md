---
paths:
  - "plugins/pirategoat-tools/scripts/hosts/**"
  - "plugins/pirategoat-tools/scripts/containment.py"
---

# Host discovery

`scripts/hosts/chain.py`'s docstring describes the resolver chain and what each resolver may read; repo-boundary checks live only in `scripts/containment.py` (see `plugins/pirategoat-tools/AGENTS.md` § Rules, Containment). Tests: `pytest plugins/pirategoat-tools/tests/hosts/ -q`.
