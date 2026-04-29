# Contributing

## Ground Rules

1. **Zero external dependencies.** Every import must resolve to Python stdlib or the `omx` package itself. No exceptions.
2. **Python 3.12+.** Use modern features: `tomllib`, `StrEnum`, `match/case`, `X | Y` union syntax.
3. **Tests use stdlib `unittest`.** No pytest, no test framework dependencies.
4. **Google-style docstrings** on all public functions and classes.

## Development Setup

```bash
git clone <repo-url> omx-py
cd omx-py
# No install needed. Just set PYTHONPATH:
export PYTHONPATH=src  # or $env:PYTHONPATH="src" on PowerShell
python -m omx --version
```

## Running Tests

```bash
PYTHONPATH=src python -m unittest discover -s tests/unit -v
```

## Adding a Module

1. Create the module under `src/omx/<module>/`
2. Add an `__init__.py` with a module docstring
3. Add tests under `tests/unit/test_<module>.py`
4. Run the full test suite to check for regressions

## Code Style

- Type hints on all public function signatures
- `from __future__ import annotations` at the top of files using forward references
- `pathlib.Path` instead of string paths
- `dataclasses` for structured data, with `to_dict()`/`from_dict()` for serialization
- `enum.StrEnum` for string-valued enumerations
- No classes where a function suffices

## Docstring Format

```python
def function_name(arg1: str, arg2: int = 0) -> bool:
    """One-line summary of what the function does.

    Longer description if the function is non-trivial.

    Args:
        arg1: Description of first argument.
        arg2: Description with default noted if non-obvious.

    Returns:
        What the function returns.

    Raises:
        ValueError: When and why this is raised.
    """
```

## Dependency Audit

Before committing, verify no external imports leaked in:

```bash
grep -rn "^import\|^from" src/omx/ \
  | grep -v "__future__\|omx\.\|json\|os\|sys\|re\|signal\|time\|uuid\|shutil" \
  | grep -v "subprocess\|threading\|tempfile\|random\|argparse\|pathlib\|typing" \
  | grep -v "dataclasses\|enum\|datetime\|tomllib\|fcntl\|msvcrt\|ctypes\|urllib"
```

## File Organization

- One concept per file. Don't put unrelated functions together.
- Keep files under ~300 lines. Split if growing larger.
- Tests mirror source structure: `src/omx/core/engine.py` → `tests/unit/test_core_engine.py`
