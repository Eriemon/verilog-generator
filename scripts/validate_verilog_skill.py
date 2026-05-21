"""Run the local confidence gate for the Erie Verilog generator skill."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = SKILL_ROOT.parent
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from runtime.verilog_generator.config import fpga_developer_routing_settings, load_settings, path_setting, skill_dependency_settings  # noqa: E402
from runtime.verilog_generator.remote_selection import resolve_confirmed_remote_server  # noqa: E402
from runtime.verilog_generator import __version__  # noqa: E402

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
REF_DEPENDENCY_PATTERN = re.compile(r"(?<![A-Za-z0-9_])ref[\\/]")
SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9-]+$")
SKILL_DESCRIPTION_WORKFLOW_TERMS = (
    "requirements ->",
    "codegen plan",
    "run-workflow",
    "prompt --spec",
    "resume",
)
PATTERN_NAMES = ("Tool Wrapper", "Generator", "Reviewer", "Inversion", "Pipeline")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the Erie Verilog generator skill locally.")
    parser.add_argument("--settings", type=Path, default=SKILL_ROOT / "config" / "defaults.json")
    parser.add_argument("--with-remote", action="store_true", help="Also run the remote confidence gate.")
    parser.add_argument("--remote-server", help="Explicit remote server id for the remote confidence gate.")
    args = parser.parse_args(argv)

    settings_path = args.settings if args.settings.is_absolute() else (Path.cwd() / args.settings).resolve()
    settings = load_settings(settings_path)
    smoke_root = path_setting(settings, "smoke_dir")
    smoke_dir = smoke_root / f"validate-{os.getpid()}-{int(time.time())}"
    cleanup_residuals(settings, smoke_dir)

    run([sys.executable, str(path_setting(settings, "quick_validate")), str(SKILL_ROOT)], cwd=PROJECT_ROOT)
    verify_dependency_schema(settings)
    verify_markdown_ascii()
    verify_skill_standards()
    run([sys.executable, "-m", "compileall", "-q", "runtime", "integration", "smoke", "scripts"], cwd=SKILL_ROOT)
    run([sys.executable, "smoke/run_smoke.py", "--settings", str(settings_path)], cwd=SKILL_ROOT)
    run_cli_gate(settings, smoke_dir)
    effectiveness_report = smoke_dir / "skill-effectiveness.json"
    run([sys.executable, "-m", "runtime.verilog_generator", "eval-skill", "--evals", str(SKILL_ROOT / "evals" / "evals.json"), "--out", str(effectiveness_report)], cwd=SKILL_ROOT)
    verify_skill_effectiveness(effectiveness_report)
    verify_legacy_terms(settings)
    verify_hardcoded_paths()
    verify_no_ref_dependencies()
    cleanup_residuals(settings, smoke_dir)
    verify_no_residuals(settings, smoke_dir)

    if args.with_remote:
        remote_server = args.remote_server or resolve_remote_server(settings)
        if not remote_server:
            raise AssertionError("Remote validation requires --remote-server or a confirmed project-local remote server selection.")
        run(build_remote_validation_command(settings_path, remote_server), cwd=SKILL_ROOT)
        remote_runs_result = run(
            build_remote_validation_command(settings_path, remote_server, report_runs=True),
            cwd=SKILL_ROOT,
        )
        remote_runs_report = parse_json_object(remote_runs_result.stdout)
        remote_runs_path = smoke_dir / "remote-runs.json"
        remote_runs_path.parent.mkdir(parents=True, exist_ok=True)
        remote_runs_path.write_text(json.dumps(remote_runs_report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        run(
            [
                sys.executable,
                "-m",
                "runtime.verilog_generator",
                "eval-skill",
                "--evals",
                str(SKILL_ROOT / "evals" / "evals.json"),
                "--out",
                str(effectiveness_report),
                "--remote-runs-json",
                str(remote_runs_path),
            ],
            cwd=SKILL_ROOT,
        )
        verify_skill_effectiveness(effectiveness_report)
        cleanup_residuals(settings, smoke_dir)
        verify_no_residuals(settings, smoke_dir)

    print("Erie Verilog generator local confidence gate passed.")
    return 0


def run_cli_gate(settings: dict, smoke_dir: Path) -> None:
    example_spec = path_setting(settings, "example_spec")
    use_case_examples_dir = path_setting(settings, "use_case_examples_dir")
    cli_dir = smoke_dir / "cli"
    workflow_dir = smoke_dir / "workflow"
    canonical_report = cli_dir / "validation-report.json"
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
            "--report-json",
            str(canonical_report),
        ],
        cwd=SKILL_ROOT,
    )
    canonical_payload = json.loads(canonical_report.read_text(encoding="utf-8"))
    if canonical_payload.get("warnings") != 0:
        raise AssertionError(f"Canonical validate emitted warnings: {canonical_payload}")
    for example_spec_path in sorted(use_case_examples_dir.glob("*.json")):
        family = example_spec_path.stem
        family_dir = smoke_dir / "cli-use-case" / family
        family_report = family_dir / "validation-report.json"
        run(
            [
                sys.executable,
                "-m",
                "runtime.verilog_generator",
                "prompt",
                "--spec",
                str(example_spec_path),
                "--out",
                str(family_dir / "prompt.md"),
            ],
            cwd=SKILL_ROOT,
        )
        prompt_text = (family_dir / "prompt.md").read_text(encoding="utf-8")
        if "## Use-case template" not in prompt_text or family not in prompt_text:
            raise AssertionError(f"Prompt missing use-case template section for {family}.")
        run(
            [
                sys.executable,
                "-m",
                "runtime.verilog_generator",
                "run-workflow",
                "--spec",
                str(example_spec_path),
                "--out-dir",
                str(family_dir / "workflow"),
                "--model-provider",
                "mock",
                "--no-external",
            ],
            cwd=SKILL_ROOT,
        )
        workflow_result = json.loads((family_dir / "workflow" / "workflow_result.json").read_text(encoding="utf-8"))
        attempt = workflow_result["attempts"][-1]
        requirements_path = SKILL_ROOT / attempt["stage_outputs"]["requirements"]["artifact_path"]
        plan_path = SKILL_ROOT / attempt["stage_outputs"]["codegen_plan"]["artifact_path"]
        requirements = json.loads(requirements_path.read_text(encoding="utf-8"))
        if requirements.get("selected_use_case_template_id") != family:
            raise AssertionError(f"Requirements did not preserve use-case template id for {family}: {requirements}")
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        if plan.get("selected_use_case_template_id") != family:
            raise AssertionError(f"Codegen plan did not preserve use-case template id for {family}: {plan}")
        generated_dir = SKILL_ROOT / attempt["artifact_dir"]
        run(
            [
                sys.executable,
                "-m",
                "runtime.verilog_generator",
                "validate",
                "--spec",
                str(example_spec_path),
                "--path",
                str(generated_dir),
                "--no-external",
                "--report-json",
                str(family_report),
            ],
            cwd=SKILL_ROOT,
        )
        payload = json.loads(family_report.read_text(encoding="utf-8"))
        if payload.get("warnings") != 0:
            raise AssertionError(f"Validate emitted warnings for {family}: {payload}")


def build_remote_validation_command(
    settings_path: Path,
    remote_server: str | None,
    *,
    report_runs: bool = False,
) -> list[str]:
    command = [sys.executable, "scripts/remote_validate_verilog_skill.py", "--settings", str(settings_path)]
    if remote_server:
        command.extend(["--server", remote_server])
    if report_runs:
        command.extend(["--report-runs", "--max-runs", "1"])
    return command


def resolve_remote_server(settings: dict) -> str | None:
    selection = resolve_confirmed_remote_server(settings)
    if not selection:
        return None
    return str(selection["server_id"])


def verify_skill_effectiveness(report_path: Path) -> None:
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    summary = payload.get("summary", {})
    if summary.get("ok") is not True:
        raise AssertionError(f"Skill-effectiveness gate failed: {summary}")


def parse_json_object(output: str) -> dict:
    starts = [index for index, char in enumerate(output) if char == "{"]
    for start in reversed(starts):
        candidate = output[start:].strip()
        if not candidate:
            continue
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise ValueError("No JSON object found in command output.")


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


def verify_skill_standards() -> None:
    skill_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    if not skill_text.startswith("---\n"):
        raise AssertionError("SKILL.md must start with YAML frontmatter.")
    try:
        _, frontmatter, _body = skill_text.split("---", 2)
    except ValueError as exc:
        raise AssertionError("SKILL.md frontmatter is malformed.") from exc
    if len(frontmatter) > 1024:
        raise AssertionError("SKILL.md frontmatter must stay within 1024 characters.")
    fields = [
        line.split(":", 1)[0].strip()
        for line in frontmatter.splitlines()
        if line.strip() and not line.startswith(" ") and ":" in line
    ]
    if fields != ["name", "description"]:
        raise AssertionError(f"SKILL.md frontmatter fields must be exactly name/description, got {fields}.")
    name_match = re.search(r"^name:\s*([^\n]+)$", frontmatter, flags=re.MULTILINE)
    description_match = re.search(r"description:\s*>-\s*\n((?:\s{2}.+\n?)*)", skill_text)
    if not name_match or not description_match:
        raise AssertionError("SKILL.md frontmatter must define both name and folded description.")
    skill_name = name_match.group(1).strip()
    description = " ".join(line.strip() for line in description_match.group(1).splitlines()).strip()
    if not SKILL_NAME_PATTERN.fullmatch(skill_name):
        raise AssertionError(f"SKILL.md name must use lowercase letters, numbers, and hyphens only: {skill_name!r}.")
    if not description.startswith("Use when"):
        raise AssertionError("SKILL.md description must start with 'Use when'.")
    if len(description) > 500:
        raise AssertionError(f"SKILL.md description must stay within 500 characters, got {len(description)}.")
    lowered = description.lower()
    for term in SKILL_DESCRIPTION_WORKFLOW_TERMS:
        if term in lowered:
            raise AssertionError(f"SKILL.md description must describe trigger conditions only, not workflow term {term!r}.")

    load_lines = [line.strip() for line in skill_text.splitlines() if line.strip().startswith("- Load ")]
    if not load_lines:
        raise AssertionError("SKILL.md must expose progressive-disclosure Load rules for supporting resources.")
    missing_resources: list[str] = []
    for line in load_lines:
        match = re.search(r"`([^`]+)`", line)
        if not match:
            continue
        resource = match.group(1)
        if not (SKILL_ROOT / resource).exists():
            missing_resources.append(resource)
    if missing_resources:
        raise AssertionError("SKILL.md Load rules reference missing resources: " + ", ".join(sorted(set(missing_resources))))

    standards_path = SKILL_ROOT / "references" / "skill-standards.md"
    if not standards_path.exists():
        raise AssertionError("references/skill-standards.md is required.")
    standards_text = standards_path.read_text(encoding="utf-8")
    standards_lower = standards_text.lower()
    for marker in PATTERN_NAMES:
        if marker not in standards_text:
            raise AssertionError(f"references/skill-standards.md must mention {marker!r}.")
    for marker in ("progressive disclosure", "pass-rate delta", "with and without the skill"):
        if marker not in standards_lower:
            raise AssertionError(f"references/skill-standards.md must mention {marker!r}.")

    goals_text = (SKILL_ROOT / "ENGINEERING_DESIGN_GOALS.md").read_text(encoding="utf-8")
    for marker in PATTERN_NAMES:
        if marker not in goals_text:
            raise AssertionError(f"ENGINEERING_DESIGN_GOALS.md must preserve the {marker} pattern.")

    required_eval_paths = [
        SKILL_ROOT / "runtime" / "verilog_generator" / "evaluation.py",
        SKILL_ROOT / "runtime" / "verilog_generator" / "eval_suite.py",
    ]
    missing_eval = [path.relative_to(SKILL_ROOT).as_posix() for path in required_eval_paths if not path.exists()]
    if missing_eval:
        raise AssertionError("Skill evaluation assets are missing: " + ", ".join(missing_eval))


def verify_legacy_terms(settings: dict) -> None:
    allowlist = set(settings.get("validation", {}).get("legacy_term_allowlist", []))
    violations: list[str] = []
    for path in iter_skill_files():
        rel = path.relative_to(SKILL_ROOT).as_posix()
        if rel in {"README.md", "README-CN.md"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if rel in allowlist:
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            if any(term in line for term in LEGACY_TERMS) and not _allowed_dependency_term_line(rel, line):
                violations.append(f"{rel}:{line_number}")
    if violations:
        raise AssertionError("Legacy generation terms found outside allowlist: " + ", ".join(sorted(violations)))


def _allowed_dependency_term_line(rel: str, line: str) -> bool:
    if rel == "config/defaults.json":
        return any(marker in line for marker in ("fpga-agent-skills", "Vivado/Vitis", "vitis-hls-synthesis", "vitis-developer", '"skill": "vitis-', '"source_path": "vitis-'))
    if rel == "SKILL.md":
        return "dependency" in line.lower() or "route to the installed FPGA" in line or "developer routing" in line.lower()
    if rel == "references/configuration.md":
        return any(marker in line for marker in ("dependency", "provides", "recommended groups", "required groups", "Vivado/Vitis", "Vitis/*/settings64.sh", "vitis-hls-synthesis", "vitis-developer", "developer routing"))
    if rel == "scripts/validate_verilog_skill.py":
        return any(marker in line for marker in ("FPGA-Agent-skills dependency", "vitis-hls-synthesis", "vitis-developer", "VCS+Verdi", "/tools/Xilinx/Vitis/*/settings64.sh", "simulator_backend"))
    if rel == "scripts/manage_skill_dependencies.py":
        return any(marker in line for marker in ("FPGA-Agent", "Vivado/Vitis", "vitis-developer", "vitis-hls-synthesis", '"vivado-'))
    if rel == "scripts/remote_validate_verilog_skill.py":
        return any(marker in line for marker in ("/tools/Xilinx/Vitis/*/settings64.sh", "selected_backend", "simulator_backend"))
    if rel == "smoke/run_smoke.py":
        return any(marker in line for marker in ("vitis-hls-synthesis", "vitis-developer", "vitis_command", "/tools/Xilinx/Vitis/2022.2/settings64.sh", "/tools/Xilinx/Vitis/*/settings64.sh", "Configured Xilinx settings64.sh", "Multiple Xilinx toolchain settings64.sh candidates"))
    if rel == "smoke/dependency_gates.py":
        return any(marker in line for marker in ("FPGA-Agent", "Vivado/Vitis", "vitis-developer", "vitis-hls-synthesis", '"vivado-', "AMD-Xilinx", "PangoMicro"))
    if rel == "smoke/toolchain_gates.py":
        return any(marker in line for marker in ("vitis-hls-synthesis", "vitis-developer", "vitis_command", "/tools/Xilinx/Vitis/2022.2/settings64.sh", "/tools/Xilinx/Vitis/*/settings64.sh", "Configured Xilinx settings64.sh", "Multiple Xilinx toolchain settings64.sh candidates", "simulator_backend"))
    return False


def verify_dependency_schema(settings: dict) -> None:
    dependencies = skill_dependency_settings(settings)
    routing = fpga_developer_routing_settings(settings)
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
    if routing["selection_policy"] != "ask_on_first_fpga_workflow":
        raise AssertionError("FPGA developer routing must ask on first FPGA workflow.")
    if routing["fpga_agent_required_when_developer_present"] is not False:
        raise AssertionError("FPGA-Agent-skills must not be required when a developer skill is installed.")
    if routing["vendors"]["amd_xilinx"]["skills"] != ["vivado-developer", "vitis-developer"]:
        raise AssertionError("AMD-Xilinx developer routing must recognize vivado-developer and vitis-developer.")
    if routing["vendors"]["pangomicro"]["skills"] != ["pds-developer"]:
        raise AssertionError("PangoMicro developer routing must recognize pds-developer.")


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


def verify_no_ref_dependencies() -> None:
    violations: list[str] = []
    candidate_release = PROJECT_ROOT / "dist" / f"erie-verilog-generator-v{__version__}"
    active_paths = [
        PROJECT_ROOT / "AGENTS.md",
        PROJECT_ROOT / "docs" / "development" / "DEVELOPMENT.md",
        PROJECT_ROOT / "docs" / "handoff" / "HANDOFF.md",
        PROJECT_ROOT / "docs" / "git_manager" / "CHANGELOG.md",
        PROJECT_ROOT / "docs" / "dir_manager" / "planned_structure.json",
        SKILL_ROOT / "SKILL.md",
        SKILL_ROOT / "smoke" / "run_smoke.py",
    ]
    active_paths.extend(sorted((SKILL_ROOT / "references").glob("*")))
    active_paths.extend(sorted((SKILL_ROOT / "scripts").glob("*")))
    for path in active_paths:
        if not path.exists() or not path.is_file():
            continue
        if REF_DEPENDENCY_PATTERN.search(path.read_text(encoding="utf-8", errors="ignore")):
            violations.append(_project_relative(path))

    if candidate_release.exists():
        for path in candidate_release.rglob("*"):
            if not path.is_file():
                continue
            if "__pycache__" in path.parts or path.suffix.lower() in {".pyc", ".pyo"}:
                continue
            if REF_DEPENDENCY_PATTERN.search(path.read_text(encoding="utf-8", errors="ignore")):
                violations.append(_project_relative(path))

    if violations:
        raise AssertionError("External temporary reference directory dependencies remain in active skill or candidate release files: " + ", ".join(sorted(violations)))


def verify_no_residuals(settings: dict, smoke_dir: Path) -> None:
    residuals: list[str] = []
    names = set(settings.get("validation", {}).get("forbidden_residuals", []))
    smoke_root = path_setting(settings, "smoke_dir").resolve()
    if smoke_dir.exists():
        residuals.append(smoke_dir.relative_to(SKILL_ROOT).as_posix())
    for path in SKILL_ROOT.rglob("*"):
        try:
            if path.resolve() == smoke_root:
                continue
        except FileNotFoundError:
            continue
        if path.name in names or any(part in names for part in path.parts):
            residuals.append(path.relative_to(SKILL_ROOT).as_posix())
    if residuals:
        raise AssertionError("Residual validation artifacts remain: " + ", ".join(sorted(residuals)))


def cleanup_residuals(settings: dict, smoke_dir: Path) -> None:
    remove_inside_skill(smoke_dir)
    smoke_root = path_setting(settings, "smoke_dir")
    if smoke_root.exists() and smoke_root.is_dir():
        remove_inside_skill(smoke_root)
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
        _remove_tree_with_retry(resolved)
    else:
        resolved.unlink()


def _remove_tree_with_retry(path: Path, *, attempts: int = 5, delay_s: float = 0.1) -> None:
    last_error: OSError | None = None
    for _ in range(attempts):
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except OSError as exc:
            last_error = exc
            if not path.exists():
                return
            time.sleep(delay_s)
    if last_error is not None:
        raise last_error


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


def _project_relative(path: Path) -> str:
    try:
        return path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)


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
