import ast
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

# Keyword arguments whose dict keys are Sentry tag / context names.
_KEY_KWARGS = {"tags", "context", "contexts"}
_SKIP_DIRS = {"tests", "migrations", "graphify-out", "media"}


def _source_files() -> list[Path]:
    root = Path(settings.BASE_DIR)
    return [
        path
        for path in root.rglob("*.py")
        if not _SKIP_DIRS & set(path.relative_to(root).parts[:-1])
        and not any(part.startswith(".") for part in path.relative_to(root).parts)
        and path.name != "manage.py"
    ]


def _bare_string_problems(path: Path) -> list[str]:
    """Where `path` passes a plain string as a Sentry tag/context name or `level`."""
    problems = []
    for node in ast.walk(ast.parse(path.read_text())):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg in _KEY_KWARGS and isinstance(kw.value, ast.Dict):
                for key in kw.value.keys:
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        problems.append(f"{path.name}:{key.lineno} bare string key {key.value!r}")
            if (
                kw.arg == "level"
                and isinstance(kw.value, ast.Constant)
                and isinstance(kw.value.value, str)
            ):
                problems.append(f"{path.name}:{kw.value.lineno} bare string level")
    return problems


class MonitoringConventionTests(SimpleTestCase):
    """The repo's "no magic strings" rule for Sentry (ruff has no rule for this): tag and
    context names come from `common.error_logging.constants`, never a bare string.
    """

    def test_no_bare_strings_as_sentry_tag_context_or_level(self) -> None:
        problems = [p for path in _source_files() for p in _bare_string_problems(path)]

        self.assertEqual(problems, [], "Use common.error_logging.constants instead of bare strings")

    def test_checker_catches_bare_strings(self) -> None:
        tmp = Path(settings.BASE_DIR) / "common" / "_tmp_bare.py"
        tmp.write_text('f(tags={"a": 1}, context={"b": 2}, level="warning")\n')
        try:
            problems = _bare_string_problems(tmp)
        finally:
            tmp.unlink()

        self.assertEqual(len(problems), 3)
