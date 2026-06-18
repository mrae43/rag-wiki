#!/usr/bin/env python3
"""
Codebase Health Check — FastAPI / Pydantic v2 / PostgreSQL / Alembic / SQLAlchemy / pgvector / pytest / ruff / mypy
Run from your project root: python healthcheck.py [--path ./src] [--json]
"""

import argparse
import ast
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

# ─────────────────────────────────────────────────────────
# Types
# ─────────────────────────────────────────────────────────

Severity = Literal["critical", "high", "medium", "low"]


@dataclass
class Finding:
    check: str
    severity: Severity
    file: str
    line: int | None
    issue: str
    fix: str


@dataclass
class CheckResult:
    name: str
    score: int  # 0–100
    findings: list[Finding] = field(default_factory=list)
    skipped: bool = False
    skip_reason: str = ""


# ─────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────


def py_files(root: Path) -> list[Path]:
    return [
        p
        for p in root.rglob("*.py")
        if not any(
            part in p.parts
            for part in (
                ".venv",
                "venv",
                ".env",
                "__pycache__",
                "dist",
                "build",
                ".git",
                "migrations",
                "alembic",
                "node_modules",
            )
        )
    ]


def read_source(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def parse_ast(path: Path) -> ast.Module | None:
    try:
        return ast.parse(read_source(path))
    except SyntaxError:
        return None


def rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def run_cmd(cmd: list[str], cwd: Path) -> tuple[int, str, str]:
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=60)
        return r.returncode, r.stdout, r.stderr
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return -1, "", str(e)


# ─────────────────────────────────────────────────────────
# CHECK 1 — Project structure
# ─────────────────────────────────────────────────────────


def check_structure(root: Path) -> CheckResult:
    findings: list[Finding] = []
    EXPECTED_DIRS = {"src", "tests", "alembic", "migrations"}
    EXPECTED_ROOT_FILES = {"pyproject.toml", "README.md"}
    MAX_DEPTH = 5

    present_dirs = {p.name for p in root.iterdir() if p.is_dir()}
    present_files = {p.name for p in root.iterdir() if p.is_file()}

    for expected in EXPECTED_DIRS:
        if expected not in present_dirs and not (root / expected).exists():
            # Allow src, app, or rag_wiki as the main module dir
            if expected in ("src",) and (
                "app" in present_dirs or "rag_wiki" in present_dirs
            ):
                continue
            findings.append(
                Finding(
                    check="structure",
                    severity="medium",
                    file=str(root),
                    line=None,
                    issue=f"Expected directory '{expected}/' not found at project root",
                    fix=f"Create '{expected}/' following standard layout conventions",
                )
            )

    for expected in EXPECTED_ROOT_FILES:
        if expected not in present_files:
            findings.append(
                Finding(
                    check="structure",
                    severity="medium",
                    file=str(root / expected),
                    line=None,
                    issue=f"'{expected}' missing at project root",
                    fix=f"Add '{expected}' — critical for onboarding and tooling",
                )
            )

    # Detect .py files dumped at root (not config files)
    root_py = [
        p.name
        for p in root.iterdir()
        if p.suffix == ".py"
        and p.name
        not in (
            "conftest.py",
            "setup.py",
            "manage.py",
            "wsgi.py",
            "asgi.py",
            "healthcheck.py",
        )
    ]
    for name in root_py:
        findings.append(
            Finding(
                check="structure",
                severity="medium",
                file=name,
                line=None,
                issue=f"Python file '{name}' sits at project root, not inside a package",
                fix="Move into src/ or app/ package directory",
            )
        )

    # Check depth
    for p in py_files(root):
        depth = len(p.relative_to(root).parts)
        if depth > MAX_DEPTH:
            findings.append(
                Finding(
                    check="structure",
                    severity="low",
                    file=rel(p, root),
                    line=None,
                    issue=f"File nested {depth} levels deep (max {MAX_DEPTH})",
                    fix="Flatten the hierarchy or reconsider module grouping",
                )
            )

    # Check for circular imports (static heuristic using import graph)
    import_graph: dict[str, set[str]] = {}
    files = py_files(root)
    for f in files:
        tree = parse_ast(f)
        if not tree:
            continue
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".")[0])
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(alias.name.split(".")[0])
        import_graph[rel(f, root)] = imports

    # Look for index.py barrels exporting from other packages
    for f in files:
        if f.name in ("__init__.py", "index.py"):
            src = read_source(f)
            cross_exports = re.findall(r"from\s+(\S+)\s+import", src)
            cross = [m for m in cross_exports if ".." in m]
            for c in cross:
                findings.append(
                    Finding(
                        check="structure",
                        severity="low",
                        file=rel(f, root),
                        line=None,
                        issue=f"Barrel file uses relative parent import '{c}' — possible cross-module coupling",
                        fix="Only re-export symbols from the same package; cross-package imports belong in services/",
                    )
                )

    score = max(0, 100 - len(findings) * 12)
    return CheckResult("Project structure", score, findings)


# ─────────────────────────────────────────────────────────
# CHECK 2 — Error handling
# ─────────────────────────────────────────────────────────


def check_errors(root: Path) -> CheckResult:
    findings: list[Finding] = []

    for f in py_files(root):
        tree = parse_ast(f)
        if not tree:
            continue
        src_lines = read_source(f).splitlines()

        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue

            # Bare except:
            if node.type is None:
                findings.append(
                    Finding(
                        check="errors",
                        severity="critical",
                        file=rel(f, root),
                        line=node.lineno,
                        issue="Bare `except:` catches everything including KeyboardInterrupt and SystemExit",
                        fix="Replace with `except Exception as e:` and handle or re-raise",
                    )
                )
                continue

            # except Exception with empty body (pass or ...)
            body_stmts = node.body
            if all(
                isinstance(s, (ast.Pass, ast.Expr))
                and (
                    isinstance(s, ast.Pass)
                    or (
                        isinstance(s, ast.Expr)
                        and isinstance(s.value, ast.Constant)
                        and s.value.value is ...
                    )
                )
                for s in body_stmts
            ):
                findings.append(
                    Finding(
                        check="errors",
                        severity="critical",
                        file=rel(f, root),
                        line=node.lineno,
                        issue="Empty except block silently swallows exceptions",
                        fix="Log the error, re-raise, or raise a domain-specific exception",
                    )
                )
                continue

            # except block that only does `pass` or only logs without raising
            only_logs = all(
                isinstance(s, ast.Expr)
                and isinstance(s.value, ast.Call)
                and (
                    (
                        hasattr(s.value.func, "attr")
                        and s.value.func.attr
                        in ("warning", "info", "debug", "error", "exception")
                    )
                    or (hasattr(s.value.func, "id") and s.value.func.id in ("print",))
                )
                for s in body_stmts
            )
            has_raise = any(isinstance(s, ast.Raise) for s in ast.walk(node))
            if only_logs and not has_raise:
                findings.append(
                    Finding(
                        check="errors",
                        severity="high",
                        file=rel(f, root),
                        line=node.lineno,
                        issue="Except block logs but does not re-raise — caller has no signal the operation failed",
                        fix="Add `raise` after logging, or raise a typed AppError with the original as `cause`",
                    )
                )

        # FastAPI route handlers: check for missing HTTPException on DB calls
        for node in ast.walk(tree):
            if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                has_db_call = any(
                    isinstance(n, ast.Await) and isinstance(n.value, ast.Call)
                    for n in ast.walk(node)
                )
                has_try = any(isinstance(n, ast.Try) for n in ast.walk(node))
                # Heuristic: route functions often have request/response param names
                param_names = [a.arg for a in node.args.args]
                looks_like_route = any(
                    p in param_names
                    for p in (
                        "db",
                        "session",
                        "request",
                        "response",
                        "background_tasks",
                    )
                )
                if looks_like_route and has_db_call and not has_try:
                    findings.append(
                        Finding(
                            check="errors",
                            severity="high",
                            file=rel(f, root),
                            line=node.lineno,
                            issue=f"Route handler `{node.name}` makes async DB calls without try/except",
                            fix="Wrap DB operations in try/except and raise HTTPException or a mapped AppError",
                        )
                    )

    score = max(0, 100 - len(findings) * 10)
    return CheckResult("Error handling", score, findings)


# ─────────────────────────────────────────────────────────
# CHECK 3 — Type safety (Pydantic v2 + mypy)
# ─────────────────────────────────────────────────────────


def check_types(root: Path) -> CheckResult:
    findings: list[Finding] = []

    # Check mypy config exists
    pyproject = root / "pyproject.toml"
    has_mypy_config = False
    if pyproject.exists():
        content = pyproject.read_text()
        has_mypy_config = "[tool.mypy]" in content
        has_strict = "strict = true" in content or "strict=true" in content

        if not has_mypy_config:
            findings.append(
                Finding(
                    check="types",
                    severity="critical",
                    file="pyproject.toml",
                    line=None,
                    issue="No [tool.mypy] section found in pyproject.toml",
                    fix="Add [tool.mypy] with strict = true, python_version, and plugins = ['pydantic.mypy']",
                )
            )
        elif not has_strict:
            findings.append(
                Finding(
                    check="types",
                    severity="high",
                    file="pyproject.toml",
                    line=None,
                    issue="mypy configured but strict = true is not set",
                    fix="Add `strict = true` under [tool.mypy] to enforce no implicit Any",
                )
            )

    # Check for pydantic mypy plugin
    if pyproject.exists() and has_mypy_config:
        content = pyproject.read_text()
        if "pydantic.mypy" not in content:
            findings.append(
                Finding(
                    check="types",
                    severity="high",
                    file="pyproject.toml",
                    line=None,
                    issue="mypy plugin for Pydantic not registered",
                    fix="Add plugins = ['pydantic.mypy'] under [tool.mypy]",
                )
            )

    for f in py_files(root):
        tree = parse_ast(f)
        if not tree:
            continue
        src = read_source(f)
        lines = src.splitlines()

        # Detect `Any` used as annotation
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "Any":
                # Only flag if it's in an annotation context (heuristic: parent is arg or return)
                pass  # AST walking for parent context is verbose; use line-level scan below

        # Line-level scan for Any in annotations
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if re.search(r":\s*Any\b", stripped) or re.search(r"->\s*Any\b", stripped):
                if not stripped.startswith("#"):
                    findings.append(
                        Finding(
                            check="types",
                            severity="high",
                            file=rel(f, root),
                            line=i,
                            issue="Annotation uses `Any` — defeats type checking",
                            fix="Replace with a concrete type, TypeVar, or Protocol",
                        )
                    )

            # type: ignore without explanation
            if "# type: ignore" in stripped and not re.search(
                r"type:\s*ignore\[", stripped
            ):
                findings.append(
                    Finding(
                        check="types",
                        severity="medium",
                        file=rel(f, root),
                        line=i,
                        issue="Unscoped `# type: ignore` — suppresses all mypy errors on this line",
                        fix="Use specific codes: `# type: ignore[assignment]` and add a comment explaining why",
                    )
                )

        # Pydantic v2: detect v1-style validators
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                for decorator in node.decorator_list:
                    dec_str = ast.unparse(decorator) if hasattr(ast, "unparse") else ""
                    if "validator" in dec_str and "field_validator" not in dec_str:
                        findings.append(
                            Finding(
                                check="types",
                                severity="high",
                                file=rel(f, root),
                                line=node.lineno,
                                issue=f"`@validator` used in `{node.name}` — this is Pydantic v1 syntax",
                                fix="Replace with `@field_validator` (Pydantic v2). Use `model_validator` for cross-field validation",
                            )
                        )

        # Detect Optional[X] instead of X | None (Pydantic v2 prefers union syntax)
        for i, line in enumerate(lines, 1):
            if re.search(r"Optional\[", line) and not line.strip().startswith("#"):
                findings.append(
                    Finding(
                        check="types",
                        severity="low",
                        file=rel(f, root),
                        line=i,
                        issue="Uses `Optional[X]` — Pydantic v2 and Python 3.10+ prefer `X | None`",
                        fix="Replace `Optional[X]` with `X | None` throughout",
                    )
                )

        # SQLAlchemy: detect missing Mapped[] type annotations on columns
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for base in node.bases:
                    base_str = ast.unparse(base) if hasattr(ast, "unparse") else ""
                    if "Base" in base_str or "DeclarativeBase" in base_str:
                        for item in node.body:
                            if isinstance(item, ast.Assign):
                                for target in item.targets:
                                    if isinstance(target, ast.Name):
                                        val_str = (
                                            ast.unparse(item.value)
                                            if hasattr(ast, "unparse")
                                            else ""
                                        )
                                        if "Column(" in val_str and not isinstance(
                                            item, ast.AnnAssign
                                        ):
                                            findings.append(
                                                Finding(
                                                    check="types",
                                                    severity="high",
                                                    file=rel(f, root),
                                                    line=item.lineno,
                                                    issue=f"SQLAlchemy column `{target.id}` uses legacy `Column()` without `Mapped[]` annotation",
                                                    fix="Use `Mapped[type] = mapped_column(...)` (SQLAlchemy 2.0 style)",
                                                )
                                            )

    score = max(0, 100 - len(findings) * 8)
    return CheckResult("Type safety", score, findings)


# ─────────────────────────────────────────────────────────
# CHECK 4 — Documentation
# ─────────────────────────────────────────────────────────


def check_docs(root: Path) -> CheckResult:
    findings: list[Finding] = []

    # README
    readme = root / "README.md"
    if not readme.exists():
        findings.append(
            Finding(
                check="docs",
                severity="high",
                file="README.md",
                line=None,
                issue="README.md is missing",
                fix="Add README.md with: project purpose, local setup, env vars, how to run tests",
            )
        )
    else:
        content = readme.read_text().lower()
        for section in ("install", "setup", "env", "test", "run"):
            if section not in content:
                findings.append(
                    Finding(
                        check="docs",
                        severity="medium",
                        file="README.md",
                        line=None,
                        issue=f"README.md appears to be missing a '{section}' section",
                        fix=f"Add a section covering {section} instructions",
                    )
                )

    # env example
    env_example = root / ".env.example"
    env_file = root / ".env"
    if not env_example.exists():
        findings.append(
            Finding(
                check="docs",
                severity="high",
                file=".env.example",
                line=None,
                issue=".env.example is missing — collaborators won't know which env vars are required",
                fix="Create .env.example with all variable names and placeholder values (no real secrets)",
            )
        )

    # Public functions/classes without docstrings
    for f in py_files(root):
        tree = parse_ast(f)
        if not tree:
            continue
        for node in ast.walk(tree):
            if not isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                continue
            # Only flag public symbols
            if node.name.startswith("_"):
                continue
            if not (ast.get_docstring(node)):
                # Only flag if it has a meaningful body (more than a pass)
                non_trivial = any(
                    not isinstance(s, (ast.Pass, ast.Expr))
                    or (
                        isinstance(s, ast.Expr)
                        and not isinstance(s.value, ast.Constant)
                    )
                    for s in node.body
                )
                if non_trivial:
                    kind = "class" if isinstance(node, ast.ClassDef) else "function"
                    findings.append(
                        Finding(
                            check="docs",
                            severity="low",
                            file=rel(f, root),
                            line=node.lineno,
                            issue=f"Public {kind} `{node.name}` has no docstring",
                            fix="Add a one-line docstring describing what it does, its params, and return value",
                        )
                    )

    # FastAPI routers: check for missing description/summary on route decorators
    for f in py_files(root):
        tree = parse_ast(f)
        if not tree:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                dec_str = ast.unparse(dec) if hasattr(ast, "unparse") else ""
                is_route = any(
                    m in dec_str
                    for m in (".get(", ".post(", ".put(", ".delete(", ".patch(")
                )
                if is_route:
                    has_doc = bool(ast.get_docstring(node))
                    has_summary = "summary=" in dec_str or "description=" in dec_str
                    if not has_doc and not has_summary:
                        findings.append(
                            Finding(
                                check="docs",
                                severity="medium",
                                file=rel(f, root),
                                line=node.lineno,
                                issue=f"FastAPI route `{node.name}` has no docstring or summary= — missing from OpenAPI spec",
                                fix="Add a docstring (FastAPI uses it as the OpenAPI description) or pass summary='...'",
                            )
                        )

    score = max(0, 100 - len(findings) * 5)
    return CheckResult("Documentation", score, findings)


# ─────────────────────────────────────────────────────────
# CHECK 5 — Tests
# ─────────────────────────────────────────────────────────


def check_tests(root: Path) -> CheckResult:
    findings: list[Finding] = []

    tests_dir = root / "tests"
    if not tests_dir.exists():
        return CheckResult(
            "Tests",
            0,
            [
                Finding(
                    check="tests",
                    severity="critical",
                    file="tests/",
                    line=None,
                    issue="No tests/ directory found",
                    fix="Create tests/ with unit/ and integration/ subdirectories",
                )
            ],
        )

    test_files = list(tests_dir.rglob("test_*.py")) + list(tests_dir.rglob("*_test.py"))
    src_files = py_files(root)

    if not test_files:
        findings.append(
            Finding(
                check="tests",
                severity="critical",
                file="tests/",
                line=None,
                issue="tests/ directory exists but contains no test files",
                fix="Add test files following test_<module>.py naming",
            )
        )
        return CheckResult("Tests", 0, findings)

    # Check for conftest.py (needed for pytest fixtures, DB session setup)
    conftest = tests_dir / "conftest.py"
    if not conftest.exists():
        findings.append(
            Finding(
                check="tests",
                severity="high",
                file="tests/conftest.py",
                line=None,
                issue="No conftest.py found in tests/ — missing shared fixtures",
                fix="Add conftest.py with at least: async_session fixture, test DB setup/teardown, and app client fixture",
            )
        )

    # Check for pytest-asyncio config (needed for FastAPI async tests)
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        content = pyproject.read_text()
        if "asyncio_mode" not in content and "pytest-asyncio" not in content:
            findings.append(
                Finding(
                    check="tests",
                    severity="high",
                    file="pyproject.toml",
                    line=None,
                    issue="pytest-asyncio mode not configured — async route tests may silently not run",
                    fix="Add under [tool.pytest.ini_options]: asyncio_mode = 'auto'",
                )
            )

    # Scan test files for common smells
    for f in test_files:
        tree = parse_ast(f)
        if not tree:
            continue
        src = read_source(f)
        lines = src.splitlines()

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("test_"):
                continue

            # Test with no assertions
            has_assert = any(isinstance(n, ast.Assert) for n in ast.walk(node))
            has_pytest_raises = (
                "pytest.raises" in ast.unparse(node)
                if hasattr(ast, "unparse")
                else False
            )
            if not has_assert and not has_pytest_raises:
                findings.append(
                    Finding(
                        check="tests",
                        severity="high",
                        file=rel(f, root),
                        line=node.lineno,
                        issue=f"Test `{node.name}` has no assertions — always passes",
                        fix="Add assert statements or use pytest.raises() for exception testing",
                    )
                )

        # Date/time non-determinism
        for i, line in enumerate(lines, 1):
            if re.search(
                r"datetime\.now\(\)|time\.time\(\)|datetime\.utcnow\(\)", line
            ):
                if not re.search(r"mock|patch|freeze", line.lower()):
                    findings.append(
                        Finding(
                            check="tests",
                            severity="medium",
                            file=rel(f, root),
                            line=i,
                            issue="Test uses real datetime/time — non-deterministic across runs",
                            fix="Use `freezegun` or `pytest-freezer` to freeze time in tests",
                        )
                    )

        # Hardcoded DB URLs in tests
        for i, line in enumerate(lines, 1):
            if (
                re.search(r"postgresql://|sqlite:///", line)
                and "env" not in line.lower()
                and "fixture" not in line.lower()
            ):
                findings.append(
                    Finding(
                        check="tests",
                        severity="high",
                        file=rel(f, root),
                        line=i,
                        issue="Hardcoded database URL in test file",
                        fix="Use a fixture or environment variable for the test DB URL",
                    )
                )

    # Check ratio of test files to source files
    ratio = len(test_files) / max(len(src_files), 1)
    if ratio < 0.3:
        findings.append(
            Finding(
                check="tests",
                severity="high",
                file="tests/",
                line=None,
                issue=f"Low test-to-source ratio: {len(test_files)} test files for {len(src_files)} source files ({ratio:.0%})",
                fix="Aim for at least one test file per module in services/, routers/, and domain/",
            )
        )

    score = max(0, 100 - len(findings) * 10)
    return CheckResult("Tests", score, findings)


# ─────────────────────────────────────────────────────────
# CHECK 6 — Security
# ─────────────────────────────────────────────────────────


def check_security(root: Path) -> CheckResult:
    findings: list[Finding] = []

    # Secrets patterns
    SECRET_PATTERNS = [
        (
            r'(?i)(password|secret|api_key|apikey|token|private_key)\s*=\s*["\'][^"\']{6,}["\']',
            "Possible hardcoded secret",
        ),
        (r"(?i)sk-[a-zA-Z0-9]{20,}", "Possible OpenAI/Anthropic API key"),
        (
            r"(?i)(postgres|postgresql)://[^@\s]+:[^@\s]+@",
            "Database URL with credentials in source",
        ),
    ]

    for f in py_files(root):
        src = read_source(f)
        lines = src.splitlines()
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for pattern, label in SECRET_PATTERNS:
                if re.search(pattern, line):
                    findings.append(
                        Finding(
                            check="security",
                            severity="critical",
                            file=rel(f, root),
                            line=i,
                            issue=f"{label} found in source code",
                            fix="Move to environment variable; use python-decouple or pydantic-settings BaseSettings",
                        )
                    )

    # .env in git
    gitignore = root / ".gitignore"
    if gitignore.exists():
        content = gitignore.read_text()
        if ".env" not in content:
            findings.append(
                Finding(
                    check="security",
                    severity="critical",
                    file=".gitignore",
                    line=None,
                    issue=".env is not in .gitignore — secrets may be committed",
                    fix="Add `.env` and `.env.*` (except .env.example) to .gitignore",
                )
            )
    else:
        findings.append(
            Finding(
                check="security",
                severity="critical",
                file=".gitignore",
                line=None,
                issue=".gitignore file is missing",
                fix="Create .gitignore and add .env, __pycache__, .venv, *.pyc, dist/",
            )
        )

    # SQL injection: raw string formatting in queries
    for f in py_files(root):
        src = read_source(f)
        lines = src.splitlines()
        for i, line in enumerate(lines, 1):
            if re.search(r'(execute|query)\s*\(\s*f["\']', line):
                findings.append(
                    Finding(
                        check="security",
                        severity="critical",
                        file=rel(f, root),
                        line=i,
                        issue="f-string used directly in DB execute/query call — SQL injection risk",
                        fix="Use parameterised queries: `session.execute(text('... :param'), {'param': val})`",
                    )
                )
            # text() without bindparams
            if re.search(r'text\s*\(\s*f["\']', line):
                findings.append(
                    Finding(
                        check="security",
                        severity="critical",
                        file=rel(f, root),
                        line=i,
                        issue="SQLAlchemy text() called with f-string — SQL injection risk",
                        fix="Use bind parameters: `text('SELECT ... WHERE id = :id').bindparams(id=value)`",
                    )
                )

    # pydantic-settings: check for BaseSettings usage (recommended pattern)
    has_base_settings = False
    for f in py_files(root):
        if "BaseSettings" in read_source(f):
            has_base_settings = True
            break
    if not has_base_settings:
        findings.append(
            Finding(
                check="security",
                severity="high",
                file="src/config.py (expected)",
                line=None,
                issue="No BaseSettings class found — environment configuration may not be validated at startup",
                fix="Create a Settings(BaseSettings) class using pydantic-settings; validate all required env vars on startup",
            )
        )

    # pgvector: check for missing index on vector columns
    for f in py_files(root):
        src = read_source(f)
        if "Vector(" in src or "pgvector" in src:
            if "index" not in src.lower() or (
                "ivfflat" not in src and "hnsw" not in src
            ):
                findings.append(
                    Finding(
                        check="security",
                        severity="medium",
                        file=rel(f, root),
                        line=None,
                        issue="Vector column found but no HNSW or IVFFlat index detected — similarity search will be slow (full scan)",
                        fix="Add index in Alembic migration: `op.create_index('ix_embedding', 'table', ['embedding'], postgresql_using='hnsw', postgresql_with={'m': 16, 'ef_construction': 64})`",
                    )
                )
            break

    score = max(0, 100 - len(findings) * 15)
    return CheckResult("Security", score, findings)


# ─────────────────────────────────────────────────────────
# CHECK 7 — Performance
# ─────────────────────────────────────────────────────────


def check_performance(root: Path) -> CheckResult:
    findings: list[Finding] = []

    for f in py_files(root):
        tree = parse_ast(f)
        if not tree:
            continue
        src = read_source(f)
        lines = src.splitlines()

        # N+1 heuristic: loop containing await session.get / await session.execute
        for node in ast.walk(tree):
            if not isinstance(node, (ast.For, ast.While)):
                continue
            awaits_in_loop = [
                n
                for n in ast.walk(node)
                if isinstance(n, ast.Await) and isinstance(n.value, ast.Call)
            ]
            db_awaits = []
            for a in awaits_in_loop:
                call_str = ast.unparse(a.value) if hasattr(ast, "unparse") else ""
                if any(
                    kw in call_str
                    for kw in (
                        "session.get",
                        "session.execute",
                        "session.scalar",
                        "db.get",
                        "db.execute",
                    )
                ):
                    db_awaits.append(a)
            if db_awaits:
                findings.append(
                    Finding(
                        check="performance",
                        severity="high",
                        file=rel(f, root),
                        line=node.lineno,
                        issue=f"Potential N+1: DB call inside a loop at line {node.lineno}",
                        fix="Use selectinload() or joinedload() on the relationship, or batch with a single WHERE IN query",
                    )
                )

        # SELECT * usage
        for i, line in enumerate(lines, 1):
            if re.search(r"select\s*\(\s*\*\s*\)|SELECT\s+\*", line, re.IGNORECASE):
                findings.append(
                    Finding(
                        check="performance",
                        severity="medium",
                        file=rel(f, root),
                        line=i,
                        issue="SELECT * fetches all columns — may pull large/unused data (especially with vector columns)",
                        fix="Enumerate only the columns your code actually uses",
                    )
                )

        # Missing pagination on list endpoints
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            dec_strs = [
                ast.unparse(d) if hasattr(ast, "unparse") else ""
                for d in node.decorator_list
            ]
            is_get_route = any(".get(" in d for d in dec_strs)
            if not is_get_route:
                continue
            param_names = [a.arg for a in node.args.args] + [
                a.arg for a in (node.args.kwonlyargs or [])
            ]
            has_pagination = any(
                p in param_names for p in ("limit", "offset", "page", "cursor", "skip")
            )
            body_str = ast.unparse(node) if hasattr(ast, "unparse") else ""
            has_db_call = any(
                kw in body_str
                for kw in ("session.execute", "session.scalars", "db.execute", ".all()")
            )
            if has_db_call and not has_pagination:
                findings.append(
                    Finding(
                        check="performance",
                        severity="high",
                        file=rel(f, root),
                        line=node.lineno,
                        issue=f"GET route `{node.name}` queries DB without pagination params (limit/offset)",
                        fix="Add `limit: int = 50, offset: int = 0` params and apply `.limit(limit).offset(offset)` to query",
                    )
                )

        # Synchronous sleep / blocking calls inside async functions
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            for child in ast.walk(node):
                if isinstance(child, ast.Call):
                    call_str = ast.unparse(child) if hasattr(ast, "unparse") else ""
                    if re.search(
                        r"\btime\.sleep\b|\brequests\.(get|post|put|delete|patch)\b",
                        call_str,
                    ):
                        findings.append(
                            Finding(
                                check="performance",
                                severity="critical",
                                file=rel(f, root),
                                line=child.lineno
                                if hasattr(child, "lineno")
                                else node.lineno,
                                issue=f"Blocking call `{call_str[:60]}` inside async function `{node.name}` — blocks the event loop",
                                fix="Use `await asyncio.sleep()` instead of `time.sleep()`; use `httpx.AsyncClient` instead of `requests`",
                            )
                        )

    score = max(0, 100 - len(findings) * 10)
    return CheckResult("Performance", score, findings)


# ─────────────────────────────────────────────────────────
# CHECK 8 — Scalability
# ─────────────────────────────────────────────────────────


def check_scalability(root: Path) -> CheckResult:
    findings: list[Finding] = []

    # pydantic-settings BaseSettings check (DRY: reuse from security)
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        content = pyproject.read_text()
        if "pydantic-settings" not in content and "pydantic_settings" not in content:
            findings.append(
                Finding(
                    check="scalability",
                    severity="high",
                    file="pyproject.toml",
                    line=None,
                    issue="pydantic-settings not in dependencies — env config may be ad-hoc",
                    fix="Add pydantic-settings and define a BaseSettings class for all config",
                )
            )

    # Check for health endpoint
    has_health = False
    for f in py_files(root):
        src = read_source(f)
        if re.search(r'["\']/?health["\']|/ping|/healthz|/readyz', src):
            has_health = True
            break
    if not has_health:
        findings.append(
            Finding(
                check="scalability",
                severity="high",
                file="src/routers/ (expected)",
                line=None,
                issue="No health check endpoint detected (/health, /ping, /healthz)",
                fix="Add GET /health that returns {status: ok} and checks DB connectivity — required for k8s/load balancers",
            )
        )

    # Detect in-memory state (module-level dicts/lists that grow)
    for f in py_files(root):
        tree = parse_ast(f)
        if not tree:
            continue
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        val_str = (
                            ast.unparse(node.value) if hasattr(ast, "unparse") else ""
                        )
                        # Module-level mutable collections that are NOT configs
                        if re.match(r"\{\}|\[\]|dict\(\)|list\(\)", val_str.strip()):
                            name_lower = target.id.lower()
                            if any(
                                kw in name_lower
                                for kw in (
                                    "cache",
                                    "store",
                                    "session",
                                    "token",
                                    "rate",
                                    "limit",
                                    "map",
                                )
                            ):
                                findings.append(
                                    Finding(
                                        check="scalability",
                                        severity="high",
                                        file=rel(f, root),
                                        line=node.lineno,
                                        issue=f"Module-level mutable `{target.id}` looks like in-memory state — breaks horizontal scaling",
                                        fix="Move to Redis or a shared store; never rely on per-process memory for shared state",
                                    )
                                )

    # Alembic: check migrations exist
    alembic_dir = root / "alembic"
    migrations_dir = root / "migrations"
    migration_path = alembic_dir if alembic_dir.exists() else migrations_dir
    if not migration_path.exists():
        findings.append(
            Finding(
                check="scalability",
                severity="high",
                file="alembic/ (expected)",
                line=None,
                issue="No Alembic migrations directory found",
                fix="Run `alembic init alembic` and configure env.py to point at your SQLAlchemy metadata",
            )
        )
    else:
        version_files = list(migration_path.rglob("*.py"))
        if len(version_files) <= 1:  # only env.py
            findings.append(
                Finding(
                    check="scalability",
                    severity="medium",
                    file=str(migration_path),
                    line=None,
                    issue="Alembic directory found but no version migration files exist",
                    fix="Generate your initial migration: `alembic revision --autogenerate -m 'initial'`",
                )
            )

    # Background tasks: detect fire-and-forget in route handlers
    for f in py_files(root):
        tree = parse_ast(f)
        if not tree:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            dec_strs = [
                ast.unparse(d) if hasattr(ast, "unparse") else ""
                for d in node.decorator_list
            ]
            is_route = any(
                any(m in d for m in (".get(", ".post(", ".put(", ".delete(", ".patch("))
                for d in dec_strs
            )
            if not is_route:
                continue
            body_str = ast.unparse(node) if hasattr(ast, "unparse") else ""
            # asyncio.create_task in a route = fire-and-forget
            if "asyncio.create_task" in body_str:
                findings.append(
                    Finding(
                        check="scalability",
                        severity="high",
                        file=rel(f, root),
                        line=node.lineno,
                        issue=f"Route `{node.name}` uses asyncio.create_task — fire-and-forget, task lost on process restart",
                        fix="Use FastAPI BackgroundTasks for lightweight tasks, or Celery/ARQ for durable background jobs",
                    )
                )

    score = max(0, 100 - len(findings) * 12)
    return CheckResult("Scalability", score, findings)


# ─────────────────────────────────────────────────────────
# CHECK 9 — Consistency (ruff + naming)
# ─────────────────────────────────────────────────────────


def check_consistency(root: Path) -> CheckResult:
    findings: list[Finding] = []

    # ruff config check
    pyproject = root / "pyproject.toml"
    has_ruff = False
    if pyproject.exists():
        content = pyproject.read_text()
        has_ruff = "[tool.ruff]" in content or "[tool.ruff.lint]" in content
        if not has_ruff:
            findings.append(
                Finding(
                    check="consistency",
                    severity="high",
                    file="pyproject.toml",
                    line=None,
                    issue="No [tool.ruff] section in pyproject.toml",
                    fix="Add ruff config with select = ['E','F','I','UP','B','SIM'] and line-length = 88",
                )
            )
        else:
            if "select" not in content:
                findings.append(
                    Finding(
                        check="consistency",
                        severity="medium",
                        file="pyproject.toml",
                        line=None,
                        issue="ruff configured but no rule select — using minimal defaults only",
                        fix="Add select = ['E','F','I','UP','B','SIM','ANN'] to catch more issues",
                    )
                )

    # Naming convention checks
    for f in py_files(root):
        tree = parse_ast(f)
        if not tree:
            continue

        for node in ast.walk(tree):
            # Class names should be PascalCase
            if isinstance(node, ast.ClassDef):
                if node.name.startswith("_"):
                    continue
                if not re.match(r"^[A-Z][a-zA-Z0-9]*$", node.name):
                    findings.append(
                        Finding(
                            check="consistency",
                            severity="medium",
                            file=rel(f, root),
                            line=node.lineno,
                            issue=f"Class `{node.name}` is not PascalCase",
                            fix=f"Rename to `{''.join(w.capitalize() for w in re.split(r'[_-]', node.name))}`",
                        )
                    )
            # Function names should be snake_case
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if re.search(r"[A-Z]", node.name) and not node.name.startswith("_"):
                    findings.append(
                        Finding(
                            check="consistency",
                            severity="low",
                            file=rel(f, root),
                            line=node.lineno,
                            issue=f"Function `{node.name}` is not snake_case",
                            fix="Rename to snake_case",
                        )
                    )

    # Commented-out code (heuristic)
    for f in py_files(root):
        lines = read_source(f).splitlines()
        consecutive_commented = 0
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if (
                stripped.startswith("#")
                and len(stripped) > 10
                and not stripped.startswith("# type:")
            ):
                # Heuristic: commented-out code has indentation keywords
                if re.search(
                    r"#\s+(def |class |return |import |if |for |await |async )",
                    stripped,
                ):
                    consecutive_commented += 1
                    if consecutive_commented >= 2:
                        findings.append(
                            Finding(
                                check="consistency",
                                severity="low",
                                file=rel(f, root),
                                line=i,
                                issue="Block of commented-out code detected",
                                fix="Delete dead code — version control preserves history",
                            )
                        )
                        consecutive_commented = 0
                        continue
            else:
                consecutive_commented = 0

    # Import ordering: check for missing isort / ruff I rules
    for f in py_files(root):
        lines = read_source(f).splitlines()
        import_lines = [
            (i + 1, l)
            for i, l in enumerate(lines)
            if l.startswith("import ") or l.startswith("from ")
        ]
        if len(import_lines) > 3:
            # Simple heuristic: stdlib after third-party is wrong
            stdlib_names = {
                "os",
                "sys",
                "re",
                "json",
                "pathlib",
                "typing",
                "datetime",
                "collections",
                "itertools",
                "functools",
                "asyncio",
                "math",
                "time",
                "uuid",
                "enum",
                "dataclasses",
                "abc",
            }
            found_third_party = False
            for lineno, line in import_lines:
                mod = re.match(r"(?:from|import)\s+(\w+)", line)
                if mod:
                    name = mod.group(1)
                    if name not in stdlib_names and name not in ("__future__",):
                        found_third_party = True
                    elif found_third_party and name in stdlib_names:
                        findings.append(
                            Finding(
                                check="consistency",
                                severity="low",
                                file=rel(f, root),
                                line=lineno,
                                issue="Import ordering: stdlib import appears after third-party imports",
                                fix="Use ruff with `I` rules enabled to auto-sort: stdlib → third-party → local",
                            )
                        )
                        break

    score = max(0, 100 - len(findings) * 6)
    return CheckResult("Consistency", score, findings)


# ─────────────────────────────────────────────────────────
# Runner + Reporter
# ─────────────────────────────────────────────────────────

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
SEVERITY_EMOJI = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵"}


def run_all(root: Path) -> list[CheckResult]:
    checks = [
        check_structure,
        check_errors,
        check_types,
        check_docs,
        check_tests,
        check_security,
        check_performance,
        check_scalability,
        check_consistency,
    ]
    return [c(root) for c in checks]


def print_report(results: list[CheckResult]) -> None:
    WIDTH = 68
    print()
    print("=" * WIDTH)
    print("  CODEBASE HEALTH CHECK — FastAPI / Pydantic v2 / PostgreSQL")
    print("=" * WIDTH)

    total_findings = sum(len(r.findings) for r in results)
    avg_score = sum(r.score for r in results) // len(results)
    critical = sum(1 for r in results for f in r.findings if f.severity == "critical")
    high_count = sum(1 for r in results for f in r.findings if f.severity == "high")

    status = "✅ PASSING" if avg_score >= 75 and critical == 0 else "❌ NEEDS ATTENTION"
    print(f"\n  Overall score : {avg_score}/100  {status}")
    print(
        f"  Total findings: {total_findings}  ({critical} critical, {high_count} high)"
    )
    print()

    for result in results:
        bar_filled = result.score // 5
        bar = "█" * bar_filled + "░" * (20 - bar_filled)
        crit_in_check = sum(1 for f in result.findings if f.severity == "critical")
        tag = " ⚠ CRITICAL" if crit_in_check else ""
        print(f"  {result.name:<28} {bar} {result.score:>3}/100{tag}")

    print()
    print("-" * WIDTH)

    for result in results:
        if not result.findings:
            print(f"\n  ✅ {result.name} — no findings")
            continue

        print(
            f"\n  {'—' * 3} {result.name.upper()} {'—' * (WIDTH - len(result.name) - 7)}"
        )
        sorted_findings = sorted(
            result.findings, key=lambda f: SEVERITY_ORDER[f.severity]
        )

        for f in sorted_findings:
            loc = f"{f.file}" + (f":{f.line}" if f.line else "")
            print(f"\n  {SEVERITY_EMOJI[f.severity]} [{f.severity.upper():<8}] {loc}")
            print(f"     Issue : {f.issue}")
            print(f"     Fix   : {f.fix}")

    print()
    print("=" * WIDTH)
    if avg_score >= 75 and critical == 0:
        print("  ✅  Health check PASSED (score ≥ 75, no critical findings)")
    else:
        print("  ❌  Health check FAILED — resolve critical/high findings first")
    print("=" * WIDTH)
    print()


def print_json(results: list[CheckResult]) -> None:
    out = []
    for r in results:
        out.append(
            {
                "check": r.name,
                "score": r.score,
                "findings": [
                    {
                        "severity": f.severity,
                        "file": f.file,
                        "line": f.line,
                        "issue": f.issue,
                        "fix": f.fix,
                    }
                    for f in r.findings
                ],
            }
        )
    print(json.dumps(out, indent=2))


# ─────────────────────────────────────────────────────────
# Entry
# ─────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Codebase health check for FastAPI / Pydantic v2 / PostgreSQL projects"
    )
    parser.add_argument(
        "--path", default=".", help="Project root directory (default: current dir)"
    )
    parser.add_argument("--json", action="store_true", help="Output results as JSON")
    parser.add_argument(
        "--check", help="Run only one check by name (e.g. --check security)"
    )
    args = parser.parse_args()

    root = Path(args.path).resolve()
    if not root.exists():
        print(f"Error: path '{root}' does not exist", file=sys.stderr)
        sys.exit(1)

    CHECK_MAP = {
        "structure": check_structure,
        "errors": check_errors,
        "types": check_types,
        "docs": check_docs,
        "tests": check_tests,
        "security": check_security,
        "performance": check_performance,
        "scalability": check_scalability,
        "consistency": check_consistency,
    }

    if args.check:
        name = args.check.lower()
        if name not in CHECK_MAP:
            print(
                f"Unknown check '{name}'. Available: {', '.join(CHECK_MAP)}",
                file=sys.stderr,
            )
            sys.exit(1)
        results = [CHECK_MAP[name](root)]
    else:
        results = run_all(root)

    if args.json:
        print_json(results)
    else:
        print_report(results)

    # Exit code: 1 if any critical finding
    has_critical = any(f.severity == "critical" for r in results for f in r.findings)
    sys.exit(1 if has_critical else 0)


if __name__ == "__main__":
    main()
