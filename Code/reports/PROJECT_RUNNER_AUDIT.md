# Project Runner Audit

## Interface and selection

`select_project_runner()` selects Django, SymPy, Astropy, scikit-learn, Sphinx,
Requests, Matplotlib, or generic pytest based on the checked-out project. Every
runner implements environment preparation, selector normalization, health
checking, visible-check compilation, real execution, and infrastructure
classification.

## Native commands

- Django: `python tests/runtests.py <django-test-label>`.
- SymPy: resolves file/module/bare test-function selectors and invokes
  `python bin/test <file> -k <function>` when the project runner exists.
- Pytest-family projects: `python -m pytest -q <normalized-selector>` using the
  selected base checkout environment.

No production transition calls the old generic paired pytest helper. Target
Recovery and patch evaluation both use the selected `ProjectRunner`.

## Isolation

Every execution creates a unique writable run directory and sets:

```text
HOME
XDG_CACHE_HOME
MPLCONFIGDIR
TMPDIR
PYTHONHASHSEED=0
PYTHONDONTWRITEBYTECODE=1
```

Trial execution puts the trial repository first on `PYTHONPATH` and removes the
baseline root. Commands use argument tuples, `shell=False`, captured output, and
per-check timeouts.

## Health and classification

Health states are `HEALTHY`, `DEPENDENCY_MISSING`, `COLLECTION_BROKEN`,
`INVALID_SELECTOR`, `UNSUPPORTED_RUNTIME`, and
`EXTERNAL_SERVICE_REQUIRED`. Invalid environment/selector/unsupported results
are not target payoff. Baseline results are cached by base commit, environment
hash, and check ID.

## Tests and results

`tests/unit/test_project_runners.py` performs real child-process execution for:

- Django label conversion and `tests/runtests.py` command;
- SymPy bare `test_bell` resolution and `bin/test` command;
- generic pytest in isolated writable directories;
- missing dependency and invalid selector classification;
- unwritable HOME/MPLCONFIGDIR handling;
- baseline health-cache reuse.

Together with worktree tests: `10 passed in 7.51s`. The complete suite result is
`140 passed in 288.45s`.

## Remaining environment limits

The runner does not install arbitrary dependencies or start required databases,
networks, or services. Those conditions are reported as environment frontiers
and cannot instruct the Generator to modify project code.
