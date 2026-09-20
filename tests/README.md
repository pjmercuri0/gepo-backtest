# Production tests

These tests are intentionally separate from the root `test_*.py` research
runners. The root scripts perform long backtests at import time and are not
unit-test modules.

Run the deterministic suite with:

```bash
python3 -m unittest discover -s tests -v
```

When the development dependency is installed, the preferred command is:

```bash
python3 -m pytest
```
