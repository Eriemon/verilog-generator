"""Run the local confidence gate for the Erie Verilog generator skill."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = SKILL_ROOT.parent
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from runtime.verilog_generator.config import load_settings, path_setting, skill_dependency_settings  # noqa: E402

LEGACY_TERMS = (
    "H" + "LS",
    "h" + "ls",
    "V" + "itis",
    "v" + "itis",
    "System" + "Verilog",
    "system" + "verilog",
    "." + "sv",
    "ap_" + "uint",
    "#pragma " + "H" + "LS",
    "verilog_" + "h" + "ls" + "_adapter",
    "h" + "ls" + "_generator",
)
ABSOLUTE_PATH_PATTERN = re.compile(
    r"(?<![A-Za-z])[A-Za-z]:[\\/]|"
    + "F"
    + r":/|"
    + "G"
    + r":/|"
    + "C"
    + r":/|"
    + "Users"
    + r"\\|"
    + "Work"
    + "Space"
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the Erie Verilog generator skill locally.")
    parser.add_argument("--settings", type=Path, default=SKILL_ROOT / "config" / "defaults.json")
    parser.add_argument("--with-remote", action="store_true", help="Also run the remote confidence gate.")
    args = parser.parse_args(argv)

    settings_path = args.settings if args.settings.is_absolute() else (Path.cwd() / args.settings).resolve()
    settings = load_settings(settings_path)
    smoke_dir = path_setting(settings, "smoke_dir")
    cleanup_residuals(settings)

    run([sys.executable, str(path_setting(settings, "quick_validate")), str(SKILL_ROOT)], cwd=PROJECT_ROOT)
    verify_dependency_schema(settings)
    verify_markdown_ascii()
    run([sys.executable, "-m", "compileall", "-q", "runtime", "integration", "smoke", "scripts"], cwd=SKILL_ROOT)
    run([sys.executable, "smoke/run_smoke.py", "--settings", str(settings_path)], cwd=SKILL_ROOT)
    run_cli_gate(settings, smoke_dir)
    verify_legacy_terms(settings)
    verify_hardcoded_paths()
    cleanup_residuals(settings)
    verify_no_residuals(settings)

    if args.with_remote:
        run([sys.executable, "scripts/remote_validate_verilog_skill.py", "--settings", str(settings_path)], cwd=SKILL_ROOT)
        cleanup_residuals(settings)
        verify_no_residuals(settings)

    print("Erie Verilog generator local confidence gate passed.")
    return 0


def run_cli_gate(settings: dict, smoke_dir: Path) -> None:
    example_spec = path_setting(settings, "example_spec")
    cli_dir = smoke_dir / "cli"
    workflow_dir = smoke_dir / "workflow"
    remove_inside_skill(smoke_dir)
    run([sys.executable, "-m", "runtime.verilog_generator", "scaffold", "--name", "erie_adapter", "--out", str(cli_dir / "spec.json")], cwd=SKILL_ROOT)
    run([sys.executable, "-m", "runtime.verilog_generator", "prompt", "--spec", str(example_spec), "--out", str(cli_dir / "prompt.md")], cwd=SKILL_ROOT)
    run(
        [
            sys.executable,
            "-m",
            "runtime.verilog_generator",
            "run-workflow",
            "--spec",
            str(example_spec),
            "--out-dir",
            str(workflow_dir),
            "--model-provider",
            "mock",
            "--no-external",
        ],
        cwd=SKILL_ROOT,
    )
    run(
        [
            sys.executable,
            "-m",
            "runtime.verilog_generator",
            "validate",
            "--spec",
            str(example_spec),
            "--path",
            str(workflow_dir / "attempt-001" / "rtl" / "generated"),
            "--no-external",
        ],
        cwd=SKILL_ROOT,
    )


def verify_markdown_ascii() -> None:
    violations: list[str] = []
    for path in iter_skill_files():
        if path.suffix.lower() != ".md":
            continue
        rel = path.relative_to(SKILL_ROOT).as_posix()
        if rel in {"README.md", "README-CN.md"}:
            continue
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if any(ord(char) > 127 for char in line):
                violations.append(f"{rel}:{line_number}")
    if violations:
        raise AssertionError("Markdown files must be ASCII-only for install safety: " + ", ".join(sorted(violations)))


def verify_legacy_terms(settings: dict) -> None:
    allowlist = set(settings.get("validation", {}).get("legacy_term_allowlist", []))
    violations: list[str] = []
    for path in iter_skill_files():
        rel = path.relative_to(SKILL_ROOT).as_posix()
        text = path.read_text(encoding="utf-8", errors="ignore")
        if rel in allowlist:
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            if _contains_legacy_term(line) and not _allowed_dependency_term_line(rel, line):
                violations.append(f"{rel}:{line_number}")
    if violations:
        raise AssertionError("Legacy generation terms found outside allowlist: " + ", ".join(sorted(violations)))


def _allowed_dependency_term_line(rel: str, line: str) -> bool:
    if rel in {"README.md", "README-CN.md"}:
        return "does not generate " + "H" + "LS" in line or "不生成 " + "H" + "LS" in line
    if rel == "config/defaults.json":
        return any(marker in line for marker in ("fpga-agent-skills", "Vivado/Vitis", "vitis-hls-synthesis", '"skill": "vitis-', '"source_path": "vitis-'))
    if rel == "SKILL.md":
        return "dependency" in line.lower() or "route to the installed FPGA" in line
    if rel == "references/configuration.md":
        return any(marker in line for marker in ("dependency", "provides", "recommended groups", "required groups", "Vivado/Vitis", "vitis-hls-synthesis"))
    if rel == "scripts/validate_verilog_skill.py":
        return "FPGA-Agent-skills dependency" in line or "vitis-hls-synthesis" in line
    if rel == "smoke/run_smoke.py":
        return "vitis-hls-synthesis" in line or "Vivado/Vitis" in line
    return False


def _contains_legacy_term(line: str) -> bool:
    for term in LEGACY_TERMS:
        if term == "." + "sv":
            if re.search(r"(?<![A-Za-z0-9_])\." + "sv" + r"(?![A-Za-z0-9_])", line):
                return True
            continue
        if term in line:
            return True
    return False


def verify_dependency_schema(settings: dict) -> None:
    dependencies = skill_dependency_settings(settings)
    required_urls = {item["url"] for item in dependencies["required"]}
    recommended_urls = {item["url"] for item in dependencies["recommended"]}
    if required_urls != {
        "https://github.com/Eriemon/remote-ssh.git",
        "https://github.com/adeleempurpled290/FPGA-Agent-skills.git",
    }:
        raise AssertionError(f"Unexpected required dependency URLs: {sorted(required_urls)}")
    if recommended_urls != {
        "https://github.com/obra/superpowers.git",
        "https://github.com/muratcankoylan/Agent-Skills-for-Context-Engineering.git",
    }:
        raise AssertionError(f"Unexpected recommended dependency URLs: {sorted(recommended_urls)}")
    fpga = next(item for item in dependencies["required"] if item["id"] == "fpga-agent-skills")
    if len(fpga["skills"]) != 8:
        raise AssertionError("FPGA-Agent-skills dependency must include all 8 Vivado/Vitis skills.")


def verify_hardcoded_paths() -> None:
    allowed = {
        "config/defaults.json",
        "references/configuration.md",
    }
    violations: list[str] = []
    for path in iter_skill_files():
        rel = path.relative_to(SKILL_ROOT).as_posix()
        if rel in allowed:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if ABSOLUTE_PATH_PATTERN.search(text):
            violations.append(rel)
    if violations:
        raise AssertionError("Hardcoded absolute paths found outside config/docs: " + ", ".join(sorted(violations)))


def verify_no_residuals(settings: dict) -> None:
    residuals: list[str] = []
    names = set(settings.get("validation", {}).get("forbidden_residuals", []))
    for path in SKILL_ROOT.rglob("*"):
        if path.name in names or any(part in names for part in path.parts):
            residuals.append(path.relative_to(SKILL_ROOT).as_posix())
    if residuals:
        raise AssertionError("Residual validation artifacts remain: " + ", ".join(sorted(residuals)))


def cleanup_residuals(settings: dict) -> None:
    remove_inside_skill(path_setting(settings, "smoke_dir"))
    remove_inside_skill(SKILL_ROOT / "workflow-state.json")
    for path in sorted(SKILL_ROOT.rglob("__pycache__"), reverse=True):
        remove_inside_skill(path)


def remove_inside_skill(path: Path) -> None:
    try:
        resolved = path.resolve()
    except FileNotFoundError:
        return
    if not resolved.exists():
        return
    try:
        resolved.relative_to(SKILL_ROOT.resolve())
    except ValueError as exc:
        raise AssertionError(f"Refusing to remove outside skill root: {resolved}") from exc
    if resolved.is_dir():
        shutil.rmtree(resolved)
    else:
        resolved.unlink()


def iter_skill_files() -> list[Path]:
    ignored_parts = {".git", "__pycache__", "_smoke_runs", "reports"}
    files: list[Path] = []
    for path in SKILL_ROOT.rglob("*"):
        if not path.is_file():
            continue
        rel_parts = set(path.relative_to(SKILL_ROOT).parts)
        if rel_parts & ignored_parts:
            continue
        if path.suffix.lower() in {".pyc", ".pyo"}:
            continue
        files.append(path)
    return files


def run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    printable = " ".join(str(item) for item in command)
    print(f"[run] {printable}")
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    result = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
        env=env,
    )
    if result.stdout:
        print(result.stdout.rstrip())
    if result.stderr:
        print(result.stderr.rstrip(), file=sys.stderr)
    if result.returncode != 0:
        raise SystemExit(result.returncode)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
