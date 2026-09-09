---
paths:
  - "plugins/pirategoat-tools/scripts/hosts/**"
  - "plugins/pirategoat-tools/scripts/containment.py"
---

# Host discovery

`scripts/hosts/chain.py`'s docstring describes the resolver chain and what each resolver may read; `scripts/containment.py` is the only place a repo-boundary check may live (`tests/test_containment_contract.py` fails on any inline `commonpath`, `is_relative_to`, or `commonprefix`). Tests: `pytest plugins/pirategoat-tools/tests/hosts/ -q`.
