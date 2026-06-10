"""Run the remote confidence gate through the erie-remote-ssh skill."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath
from typing import Any

SKILL_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = SKILL_ROOT.parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from runtime.verilog_generator.config import load_settings, remote_runtime_settings_relpath, remote_setting
from runtime.verilog_generator.remote_selection import load_remote_runtime_config, resolve_confirmed_remote_server
from runtime.verilog_generator.workspace import require_workspace_root

REMOTE_FIXTURES = (
    "comb_parity_mux",
    "pipeline_delay",
    "ready_valid_slice",
)
SIMULATOR_BACKENDS = ("xsim", "vcs_verdi", "iverilog")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate this skill on a configured remote SSH server.")
    parser.add_argument("--settings", type=Path, default=SKILL_ROOT / "config" / "defaults.json")
    parser.add_argument("--server", help="Override configured server id/name.")
    parser.add_argument("--keep-remote", action="store_true", help="Compatibility option; remote validation directories are kept by default.")
    parser.add_argument("--cleanup-remote", action="store_true", help="Delete the remote validation directory after the gate finishes.")
    parser.add_argument("--report-runs", action="store_true", help="List retained remote validation runs without staging a new run.")
    parser.add_argument("--max-runs", type=int, default=5, help="Maximum retained runs to include with --report-runs.")
    parser.add_argument("--toolchain-config", type=Path, help="Compatibility option for the local copy of .settings/verilog.remote.json.")
    parser.add_argument("--write-toolchain-selection", action="store_true", help="Write a confirmed remote toolchain choice to the project-local config and exit.")
    parser.add_argument("--simulator-backend", choices=SIMULATOR_BACKENDS, help="Confirmed simulator backend for --write-toolchain-selection.")
    parser.add_argument("--vivado-settings", help="Confirmed remote Vivado settings64.sh path for xsim.")
    args = parser.parse_args(argv)

    settings = load_settings(args.settings)
    try:
        helper = Path(remote_setting(settings, "helper"))
        remote_settings = Path(remote_setting(settings, "settings"))
        server_list = resolve_server_list_path(Path(remote_setting(settings, "server_list")))
        local_remote_runtime_config = resolve_local_remote_runtime_config(settings, args.toolchain_config)
    except ValueError as exc:
        parser.error(str(exc))
        raise AssertionError("unreachable") from exc
    server = resolve_server(settings, args.server, parser)
    timeout = int(settings.get("remote", {}).get("timeout_s", 120))
    remote_python = remote_setting(settings, "python")
    remote_root = require_remote_relative_path(remote_setting(settings, "remote_root"), "settings.remote.remote_root")
    remote_runtime_config = require_remote_relative_path(remote_setting(settings, "remote_runtime_config"), "settings.remote.remote_runtime_config")

    if args.write_toolchain_selection:
        selection = selection_from_args(args, parser)
        payload = build_remote_runtime_config_payload(selection)
        write_remote_runtime_config(local_remote_runtime_config, payload)
        ensure_local_prerequisites(helper, remote_settings, server_list)
        ensure_remote_prerequisites(helper, remote_settings, server_list, server)
        upload_remote_runtime_config(helper, remote_settings, server_list, server, payload, remote_runtime_config, timeout)
        print(f"remote_runtime_config_written: {remote_runtime_config}")
        print(json.dumps({"server": server, **payload["remote"]["toolchain"]}, indent=2, ensure_ascii=False))
        return 0

    if args.report_runs:
        ensure_local_prerequisites(helper, remote_settings, server_list)
        ensure_remote_read_prerequisites(helper, remote_settings, server_list, server)
        report = report_remote_runs(helper, remote_settings, server_list, server, remote_root, args.max_runs)
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    run_id = f"run-{time.strftime('%Y%m%dT%H%M%S')}"
    remote_parent = remote_join(remote_root, run_id)
    remote_skill = remote_join(remote_parent, "erie-verilog-generator")
    cleanup_remote = cleanup_remote_requested(args)

    print("\n".join(remote_location_lines(remote_parent, remote_skill, cleanup_remote)))

    ensure_local_prerequisites(helper, remote_settings, server_list)
    ensure_remote_prerequisites(helper, remote_settings, server_list, server)
    remote_runtime = download_remote_runtime_config(helper, remote_settings, server_list, server, remote_runtime_config)
    package_root = stage_package(helper, run_id)
    request_paths: list[Path] = []
    try:
        request_paths.append(request_and_run(helper, remote_settings, server_list, server, timeout, "request-mkdir", ["--path", remote_parent, "--reason", "prepare Verilog skill validation directory"]))
        request_paths.append(
            request_and_run(
                helper,
                remote_settings,
                server_list,
                server,
                timeout,
                "request-upload",
                [
                    "--local",
                    str(package_root / "erie-verilog-generator"),
                    "--remote",
                    remote_skill,
                    "--reason",
                    "upload Verilog skill validation package",
                    "--confirm-sensitive-local-upload",
                ],
                run_request_args=["--confirm-sensitive-local-upload"],
            )
        )
        command = remote_validation_command(
            remote_skill,
            remote_python,
            cleanup_outputs=cleanup_remote,
            toolchain_selection=remote_runtime["toolchain"],
            remote_runtime_config_path=remote_runtime_config,
        )
        request_paths.append(
            request_and_run(
                helper,
                remote_settings,
                server_list,
                server,
                timeout,
                "request-command",
                ["--reason", "run Verilog skill remote confidence gate", "--", "bash", "-lc", command],
            )
        )
    finally:
        if cleanup_remote:
            try:
                request_paths.append(request_and_run(helper, remote_settings, server_list, server, timeout, "request-delete", ["--path", remote_parent, "--recursive", "--reason", "cleanup Verilog skill validation directory"]))
            except Exception as exc:
                print(f"[remote-cleanup-warning] {exc}", file=sys.stderr)
        else:
            print(f"remote_retained: {remote_parent}")
            print(f"remote_skill_retained: {remote_skill}")
        cleanup_package(package_root)
        cleanup_requests(request_paths)
        cleanup_local_residuals()

    print("Erie Verilog generator remote confidence gate passed.")
    return 0


def cleanup_remote_requested(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "cleanup_remote", False))


def remote_location_lines(remote_parent: str, remote_skill: str, cleanup_remote: bool) -> list[str]:
    return [
        f"remote_parent: {remote_parent}",
        f"remote_skill: {remote_skill}",
        f"remote_cleanup_requested: {cleanup_remote}",
    ]


def resolve_server(settings: dict, arg_server: str | None, parser: argparse.ArgumentParser) -> str:
    if arg_server:
        return arg_server
    selection = resolve_server_from_selection(settings)
    if selection:
        return str(selection["server_id"])
    parser.error("Remote server is not confirmed. Pass --server after the user selects a target from erie-remote-ssh choices.")
    raise AssertionError("unreachable")


def resolve_server_from_selection(settings: dict) -> dict | None:
    return resolve_confirmed_remote_server(settings)


def resolve_local_remote_runtime_config(settings: dict, arg_path: Path | None = None) -> Path:
    if arg_path:
        return arg_path.expanduser().resolve()
    meta = settings.get("__verilog_settings_meta__", {})
    workspace_root = Path(str(meta.get("workspace_root"))) if isinstance(meta, dict) and meta.get("workspace_root") else None
    if workspace_root is None:
        workspace_root = require_workspace_root(purpose="local remote runtime config")
    return (workspace_root / remote_runtime_settings_relpath()).resolve()


def resolve_server_list_path(configured_path: Path) -> Path:
    configured = configured_path.expanduser().resolve()
    return configured


def selection_from_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> dict:
    backend = args.simulator_backend
    if not backend:
        parser.error("--write-toolchain-selection requires --simulator-backend.")
    selection = {
        "simulator_backend": backend,
        "confirmed_by_user": True,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if backend == "xsim":
        if not args.vivado_settings:
            parser.error("--simulator-backend xsim requires --vivado-settings.")
        selection["vivado_settings64"] = require_remote_absolute_file_path(args.vivado_settings, "--vivado-settings")
    elif args.vivado_settings:
        selection["vivado_settings64"] = require_remote_absolute_file_path(args.vivado_settings, "--vivado-settings")
    return selection


def build_remote_runtime_config_payload(selection: dict[str, str]) -> dict[str, Any]:
    payload = {
        "version": 1,
        "remote": {
            "toolchain": {
                "simulator_backend": selection["simulator_backend"],
            },
            "env": {},
            "tools": {},
        },
    }
    vivado_settings = selection.get("vivado_settings64")
    if isinstance(vivado_settings, str) and vivado_settings.strip():
        payload["remote"]["toolchain"]["vivado_settings64"] = vivado_settings.strip()
    return payload


def write_remote_runtime_config(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def ensure_local_prerequisites(helper: Path, settings: Path, server_list: Path) -> None:
    for path, label in ((helper, "erie-remote-ssh helper"), (settings, "erie-remote-ssh settings"), (server_list, "server list")):
        if not path.exists():
            raise FileNotFoundError(f"Missing {label}: {path}")
    if not helper.is_file():
        raise ValueError(f"Remote helper is not a file: {helper}")


def ensure_remote_prerequisites(helper: Path, settings: Path, server_list: Path, server: str) -> None:
    base = helper_base(helper, settings, server_list)
    run_helper(helper, ["discover", *base, "--json"])
    run_helper(helper, ["list", *base])
    run_helper(helper, ["check", *base, "--server", server])
    run_helper(helper, ["scan-software", *base, "--server", server])
    run_helper(helper, ["workspace-check", *base, "--server", server])


def ensure_remote_read_prerequisites(helper: Path, settings: Path, server_list: Path, server: str) -> None:
    base = helper_base(helper, settings, server_list)
    run_helper(helper, ["discover", *base, "--json"])
    run_helper(helper, ["list", *base])
    run_helper(helper, ["check", *base, "--server", server])
    run_helper(helper, ["workspace-check", *base, "--server", server])


def upload_remote_runtime_config(
    helper: Path,
    settings: Path,
    server_list: Path,
    server: str,
    payload: dict[str, Any],
    remote_runtime_config: str,
    timeout: int,
) -> None:
    temp_dir = helper.resolve().parents[1] / "reports" / "tmp" / "verilog-generator-runtime-upload"
    temp_dir.mkdir(parents=True, exist_ok=True)
    local_copy = temp_dir / "verilog.remote.json"
    write_remote_runtime_config(local_copy, payload)
    request_paths: list[Path] = []
    try:
        request_paths.append(
            request_and_run(
                helper,
                settings,
                server_list,
                server,
                timeout,
                "request-mkdir",
                ["--path", str(PurePosixPath(remote_runtime_config).parent), "--reason", "prepare remote Verilog runtime settings directory"],
            )
        )
        request_paths.append(
            request_and_run(
                helper,
                settings,
                server_list,
                server,
                timeout,
                "request-upload",
                [
                    "--local",
                    str(local_copy),
                    "--remote",
                    remote_runtime_config,
                    "--reason",
                    "write remote Verilog runtime settings",
                    "--confirm-sensitive-local-upload",
                ],
                run_request_args=["--confirm-sensitive-local-upload"],
            )
        )
    finally:
        cleanup_requests(request_paths)
        if local_copy.exists():
            local_copy.unlink()


def download_remote_runtime_config(
    helper: Path,
    settings: Path,
    server_list: Path,
    server: str,
    remote_runtime_config: str,
) -> dict[str, Any]:
    local_copy = helper.resolve().parents[1] / "reports" / "downloads" / "verilog.remote.download.json"
    local_copy.parent.mkdir(parents=True, exist_ok=True)
    base = helper_base(helper, settings, server_list)
    result = run_helper(
        helper,
        [
            "file-download",
            *base,
            "--server",
            server,
            "--remote",
            remote_runtime_config,
            "--local",
            str(local_copy),
        ],
        allow_failure=True,
        quiet_on_failure=True,
    )
    if result.returncode != 0 or not local_copy.exists():
        raise FileNotFoundError(
            f"Remote validation requires {remote_runtime_config} in the selected remote workdir before external validation can continue."
        )
    return load_remote_runtime_config(local_copy)


def stage_package(helper: Path, run_id: str) -> Path:
    remote_project = helper.resolve().parents[1]
    package_root = remote_project / "reports" / "tmp" / f"erie-verilog-generator-{run_id}"
    cleanup_package(package_root)
    target = package_root / "erie-verilog-generator"
    staged_skill = target / "skills" / "erie-verilog-generator"
    staged_smoke = target / "smoke"
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "_smoke_runs", "reports", "workflow-state.json")
    shutil.copytree(SKILL_ROOT, staged_skill, ignore=ignore)
    shutil.copytree(PROJECT_ROOT / "smoke", staged_smoke, ignore=ignore)
    # The remote confidence gate runs the copied skill from inside a temporary
    # package root that stands in for the original repository root. Add
    # lightweight workspace-root markers so project-local state resolution keeps
    # working even though the copied package does not include the source repo.
    (package_root / "AGENTS.md").write_text(
        "# Remote Validation Workspace\n\n"
        "This marker file is created only for remote confidence-gate staging so\n"
        "workspace-root discovery can resolve project-local state paths.\n",
        encoding="utf-8",
    )
    (target / "AGENTS.md").write_text(
        "# Remote Validation Packaged Workspace\n\n"
        "This marker file is created only for remote confidence-gate staging so\n"
        "workspace-root discovery can resolve project-local state paths from the\n"
        "uploaded package root.\n",
        encoding="utf-8",
    )
    return package_root


def cleanup_package(package_root: Path) -> None:
    if not package_root.exists():
        return
    resolved = package_root.resolve()
    if resolved.parent.name != "tmp" or not resolved.name.startswith("erie-verilog-generator-run-"):
        raise AssertionError(f"Refusing to remove unexpected package path: {package_root}")
    remove_tree_with_retries(package_root)


def remove_tree_with_retries(path: Path, *, attempts: int = 5, delay_s: float = 0.2) -> None:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except PermissionError as exc:
            last_error = exc
            time.sleep(delay_s * (attempt + 1))
    if last_error is not None:
        raise last_error


def request_and_run(
    helper: Path,
    settings: Path,
    server_list: Path,
    server: str,
    timeout: int,
    operation: str,
    operation_args: list[str],
    run_request_args: list[str] | None = None,
) -> Path:
    base = helper_base(helper, settings, server_list)
    create = run_helper(helper, [operation, *base, "--server", server, *operation_args])
    request_path = parse_request_path(create.stdout)
    extra_run_args = run_request_args or []
    run_helper(helper, ["run-request", *base, "--request", str(request_path), "--execute", "--timeout", str(timeout), *extra_run_args])
    return request_path


def helper_base(helper: Path, settings: Path, server_list: Path) -> list[str]:
    return ["--settings", str(settings), "--config", str(server_list)]


def run_helper(helper: Path, args: list[str], *, allow_failure: bool = False, quiet_on_failure: bool = False) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, "-X", "utf8", str(helper), *args]
    printable = " ".join(command)
    print(f"[remote] {printable}")
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    result = subprocess.run(command, text=True, encoding="utf-8", errors="replace", capture_output=True, check=False, env=env)
    failed_quietly = result.returncode != 0 and quiet_on_failure
    if result.stdout and not failed_quietly:
        print(result.stdout.rstrip())
    if result.stderr and not failed_quietly:
        print(result.stderr.rstrip(), file=sys.stderr)
    if result.returncode != 0 and not allow_failure:
        raise SystemExit(result.returncode)
    return result


def parse_request_path(output: str) -> Path:
    for line in output.splitlines():
        if line.startswith("request:"):
            return Path(line.split(":", 1)[1].strip())
    raise AssertionError("erie-remote-ssh did not print a request path.")


def cleanup_requests(paths: list[Path]) -> None:
    for path in paths:
        try:
            if path.exists():
                path.unlink()
        except OSError as exc:
            print(f"[request-cleanup-warning] {path}: {exc}", file=sys.stderr)


def cleanup_local_residuals() -> None:
    for path in sorted(SKILL_ROOT.rglob("__pycache__"), reverse=True):
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)


def report_remote_runs(
    helper: Path,
    settings: Path,
    server_list: Path,
    server: str,
    remote_root: str,
    max_runs: int,
) -> dict:
    if max_runs < 1:
        raise ValueError("--max-runs must be at least 1.")
    base = helper_base(helper, settings, server_list)
    root_listing = run_helper(helper, ["file-list", *base, "--server", server, "--path", remote_root], allow_failure=True, quiet_on_failure=True)
    if root_listing.returncode != 0:
        return {"remote_root": remote_root, "runs": [], "status": "missing_or_unreadable"}
    data = parse_json_output(root_listing.stdout)
    entries = data.get("entries", []) if isinstance(data, dict) else []
    run_names = sorted(
        item["name"]
        for item in entries
        if isinstance(item, dict) and item.get("type") == "dir" and str(item.get("name", "")).startswith("run-")
    )
    selected = list(reversed(run_names[-max_runs:]))
    runs = [summarize_remote_run(helper, settings, server_list, server, remote_root, run_name) for run_name in selected]
    return {"remote_root": remote_root, "runs": runs, "status": "ok"}


def summarize_remote_run(
    helper: Path,
    settings: Path,
    server_list: Path,
    server: str,
    remote_root: str,
    run_name: str,
) -> dict:
    remote_skill = remote_join(remote_root, run_name, "erie-verilog-generator")
    execute_report = download_json_optional(
        helper,
        settings,
        server_list,
        server,
        remote_join(remote_skill, "_smoke_runs/remote_execute/attempt-001/validation.json"),
        remote_join("erie-verilog-generator-report", run_name, "remote_execute_validation.json"),
    )
    fixture_summary = download_json_optional(
        helper,
        settings,
        server_list,
        server,
        remote_join(remote_skill, "_smoke_runs/remote_fixtures/summary.json"),
        remote_join("erie-verilog-generator-report", run_name, "remote_fixture_summary.json"),
    )
    return {
        "run": run_name,
        "remote_skill": remote_skill,
        "remote_execute": summarize_validation_report(
            execute_report,
            rtl_path=remote_join(remote_skill, "_smoke_runs/remote_execute/attempt-001/rtl/generated/rtl/erie_adapter.v"),
            testbench_path=remote_join(remote_skill, "_smoke_runs/remote_execute/attempt-001/rtl/generated/tb/erie_adapter_tb.v"),
            validation_json=remote_join(remote_skill, "_smoke_runs/remote_execute/attempt-001/validation.json"),
        ),
        "fixtures": summarize_fixture_report(fixture_summary),
    }


def download_json_optional(
    helper: Path,
    settings: Path,
    server_list: Path,
    server: str,
    remote_path: str,
    local_path: str,
) -> dict | None:
    base = helper_base(helper, settings, server_list)
    result = run_helper(
        helper,
        ["file-download", *base, "--server", server, "--remote", remote_path, "--local", local_path],
        allow_failure=True,
        quiet_on_failure=True,
    )
    if result.returncode != 0:
        return None
    downloaded = parse_download_path(result.stdout)
    if not downloaded.exists():
        return None
    return json.loads(downloaded.read_text(encoding="utf-8"))


def summarize_validation_report(
    report: dict | None,
    *,
    rtl_path: str | None = None,
    testbench_path: str | None = None,
    validation_json: str | None = None,
) -> dict:
    if not report:
        return {"available": False}
    outputs = sorted(output["path"] for output in report.get("spec_outputs", []) if isinstance(output, dict) and output.get("path"))
    summary = {
        "available": True,
        "ok": report.get("ok"),
        "selected_simulator_backend": report.get("metrics", {}).get("selected_simulator_backend"),
        "executed_tools": report.get("metrics", {}).get("executed_tools", []),
        "outputs": outputs,
    }
    if rtl_path:
        summary["rtl_path"] = rtl_path
    if testbench_path:
        summary["testbench_path"] = testbench_path
    if validation_json:
        summary["validation_json"] = validation_json
    return summary


def summarize_fixture_report(summary: dict | None) -> list[dict]:
    if not summary:
        return []
    fixtures = summary.get("fixtures", [])
    if not isinstance(fixtures, list):
        return []
    return [
        {
            "name": item.get("name"),
            "ok": item.get("ok"),
            "selected_simulator_backend": item.get("selected_simulator_backend"),
            "executed_tools": item.get("executed_tools", []),
            "rtl_path": item.get("rtl_path"),
            "testbench_path": item.get("testbench_path"),
            "validation_json": item.get("validation_json"),
        }
        for item in fixtures
        if isinstance(item, dict)
    ]


def parse_json_output(output: str) -> dict:
    start = output.find("{")
    end = output.rfind("}")
    if start < 0 or end < start:
        raise ValueError("No JSON object found in erie-remote-ssh output.")
    return json.loads(output[start : end + 1])


def parse_download_path(output: str) -> Path:
    for line in output.splitlines():
        if line.startswith("downloaded:"):
            return Path(line.split(":", 1)[1].strip())
    raise AssertionError("erie-remote-ssh did not print a downloaded path.")


def remote_validation_command(
    remote_skill: str,
    remote_python: str,
    *,
    cleanup_outputs: bool = False,
    toolchain_selection: dict | None = None,
    remote_runtime_config_path: str | None = None,
) -> str:
    py = sh_quote(remote_python)
    cleanup_snippet = remote_output_cleanup_snippet(cleanup_outputs)
    fixture_names = " ".join(REMOTE_FIXTURES)
    selected_vivado = ""
    selected_backend = ""
    if toolchain_selection:
        selected_vivado = str(toolchain_selection.get("vivado_settings64") or "")
        selected_backend = str(toolchain_selection.get("simulator_backend") or "")
    simulator_priority_snippet = simulator_priority_export_snippet(selected_backend)
    return f"""
set -eu
cd {sh_quote(remote_skill)}
export PYTHONPATH="skills/erie-verilog-generator${{PYTHONPATH:+:$PYTHONPATH}}"
{py} --version
{vivado_activation_snippet(selected_vivado, selected_backend, remote_runtime_config_path)}
{simulator_priority_snippet}
for tool in xvlog xelab xsim vcs verdi iverilog vvp yosys; do
  if command -v "$tool" >/dev/null 2>&1; then
    echo "$tool=present"
  else
    echo "$tool=missing"
  fi
done
if command -v xvlog >/dev/null 2>&1 && command -v xelab >/dev/null 2>&1 && command -v xsim >/dev/null 2>&1; then
  expected_sim_backend=xsim
elif command -v vcs >/dev/null 2>&1 && command -v verdi >/dev/null 2>&1; then
  expected_sim_backend=vcs_verdi
elif command -v iverilog >/dev/null 2>&1 && command -v vvp >/dev/null 2>&1; then
  expected_sim_backend=iverilog
else
  echo "No supported simulator backend is available on the remote server." >&2
  exit 1
fi
if command -v yosys >/dev/null 2>&1; then
  yosys_available=1
else
  yosys_available=0
fi
{py} -m compileall -q skills/erie-verilog-generator/runtime skills/erie-verilog-generator/integration skills/erie-verilog-generator/scripts smoke
{py} smoke/run_smoke.py --settings skills/erie-verilog-generator/config/defaults.json
{rtl_md_constraint_remote_snippet(remote_python)}
if [ -n "$configured_simulator_backend" ]; then
  export VERILOG_GENERATOR_SIMULATOR_PRIORITY="$configured_simulator_backend"
  expected_sim_backend="$configured_simulator_backend"
fi
{py} -m runtime.verilog_generator run-workflow --spec skills/erie-verilog-generator/assets/examples/rtl_erie_verilog_spec.json --out-dir _smoke_runs/remote_execute --model-provider mock --readiness execute --external-target local
{py} -m runtime.verilog_generator validate --spec skills/erie-verilog-generator/assets/examples/rtl_erie_verilog_spec.json --path _smoke_runs/remote_execute/attempt-001/rtl/generated --readiness execute --external-target local
EXPECTED_SIM_BACKEND="$expected_sim_backend" {py} - <<'PY'
import json
import os
from pathlib import Path
expected = os.environ["EXPECTED_SIM_BACKEND"]
validation = json.loads(Path("_smoke_runs/remote_execute/attempt-001/validation.json").read_text(encoding="utf-8"))
metrics = validation["metrics"]
assert metrics["selected_simulator_backend"] == expected, metrics
assert set(["xvlog", "xelab", "xsim"]).issubset(metrics["executed_tools"]) if expected == "xsim" else True, metrics
if expected == "iverilog":
    assert "xsim" in metrics["missing_preferred_backends"], metrics
    assert "vcs_verdi" in metrics["missing_preferred_backends"], metrics
PY
mkdir -p _smoke_runs/remote_fixtures
REMOTE_FIXTURES="{fixture_names}" EXPECTED_SIM_BACKEND="$expected_sim_backend" {py} - <<'PY'
import json
import os
import subprocess
import sys
from pathlib import Path

fixtures = os.environ["REMOTE_FIXTURES"].split()
expected = os.environ["EXPECTED_SIM_BACKEND"]
summary = {{"fixtures": []}}
for name in fixtures:
    spec = Path("skills/erie-verilog-generator/assets/examples/remote_fixtures") / name / "spec.json"
    generated = Path("skills/erie-verilog-generator/assets/examples/remote_fixtures") / name / "generated"
    report_json = Path("_smoke_runs/remote_fixtures") / name / "validation.json"
    report_json.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "runtime.verilog_generator",
        "validate",
        "--spec",
        str(spec),
        "--path",
        str(generated),
        "--readiness",
        "execute",
        "--external-target",
        "local",
        "--report-json",
        str(report_json),
    ]
    subprocess.run(command, check=True)
    report = json.loads(report_json.read_text(encoding="utf-8"))
    metrics = report["metrics"]
    assert report["ok"] is True, report
    assert metrics["selected_simulator_backend"] == expected, metrics
    if expected == "xsim":
        assert set(["xvlog", "xelab", "xsim"]).issubset(metrics["executed_tools"]), metrics
    outputs = report.get("spec_outputs", [])
    summary["fixtures"].append({{
        "name": name,
        "ok": report["ok"],
        "selected_simulator_backend": metrics["selected_simulator_backend"],
        "executed_tools": metrics["executed_tools"],
        "rtl_path": str(generated / "rtl" / (name + ".v")),
        "testbench_path": str(generated / "tb" / (name + "_tb.v")),
        "validation_json": str(report_json),
        "outputs": outputs,
    }})
Path("_smoke_runs/remote_fixtures/summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
PY
if [ "$yosys_available" -eq 1 ]; then
  {py} -m runtime.verilog_generator run-workflow --spec skills/erie-verilog-generator/assets/examples/rtl_erie_verilog_spec.json --out-dir _smoke_runs/remote_implement --model-provider mock --readiness implement --external-target local
  {py} - <<'PY'
import json
from pathlib import Path
result = json.loads(Path("_smoke_runs/remote_implement/workflow_result.json").read_text(encoding="utf-8"))
assert result["status"] == "passed", result
PY
else
  set +e
  {py} -m runtime.verilog_generator run-workflow --spec skills/erie-verilog-generator/assets/examples/rtl_erie_verilog_spec.json --out-dir _smoke_runs/remote_implement --model-provider mock --readiness implement --external-target local
  impl_status=$?
  set -e
  if [ "$impl_status" -eq 0 ]; then
    echo "Expected implement readiness to block when yosys is missing." >&2
    exit 1
  fi
  {py} - <<'PY'
import json
from pathlib import Path
result = json.loads(Path("_smoke_runs/remote_implement/workflow_result.json").read_text(encoding="utf-8"))
assert result["status"] == "blocked_toolchain", result
validation = json.loads(Path("_smoke_runs/remote_implement/attempt-001/validation.json").read_text(encoding="utf-8"))
assert any(item.get("tool") == "yosys" and item.get("source") == "toolchain_issue" for item in validation["issues"]), validation
PY
fi
{cleanup_snippet}
find . -type d -name __pycache__ -prune -exec rm -rf {{}} +
""".strip()


def rtl_md_constraint_remote_snippet(remote_python: str) -> str:
    py = sh_quote(remote_python)
    template = r"""
mkdir -p _smoke_runs/remote_rtl_md_constraints
__PY__ - <<'PY'
from pathlib import Path

from runtime.verilog_generator.prompt import render_prompt
from runtime.verilog_generator.rtl_md_constraints import load_rtl_md_constraints, summarize_constraints_for_prompt
from runtime.verilog_generator.static_lint import lint_generated_rtl


def spec(name="remote_rtl_md_constraints"):
    return {
        "name": name,
        "description": "Remote RTL Markdown constraint regression fixture.",
        "behavior": ["Register one input bit."],
        "constraints": [],
        "notes": [],
        "clock": {"name": "clk", "edge": "posedge"},
        "reset": {"name": "rst_n", "active": "low", "synchronous": False},
        "interfaces": {
            "ports": [
                {"name": "clk", "direction": "input", "width": 1, "role": "clock"},
                {"name": "rst_n", "direction": "input", "width": 1, "role": "reset"},
                {"name": "a", "direction": "input", "width": 4},
                {"name": "y", "direction": "output", "width": 1},
            ]
        },
        "outputs": [{"path": f"rtl/{name}.v", "kind": "source", "language": "verilog"}],
    }


catalog = load_rtl_md_constraints()
assert catalog["total_rules"] == 68, catalog
assert catalog["required_rules"] == 47, catalog
assert catalog["advisory_rules"] == 21, catalog
prompt = render_prompt(spec(), stage="rtl")
for marker in ("RTL Markdown constraints", "MUST_CASE_HAS_DEFAULT", "MUST_ASSIGN_WIDTH_MATCH", "REC_LITERAL_EXPLICIT_BASE_WIDTH"):
    assert marker in prompt, marker
summary = summarize_constraints_for_prompt(max_rules_per_group=3)
assert "MUST rules are blocking error constraints" in summary, summary
assert "REC rules are default warning-level preferences" in summary, summary

bad_dir = Path("_smoke_runs/remote_rtl_md_constraints/bad")
bad_dir.mkdir(parents=True, exist_ok=True)
(bad_dir / "bad_constraints.v").write_text(
    "\n".join(
        [
            "module bad_constraints(input wire clk, input wire rst_n, input wire [3:0] a, output reg y);",
            "wire gated_clk = clk & rst_n;",
            "initial y = 1'b0;",
            "always @(a || rst_n) begin",
            "  if (a == 4'bx) begin",
            "    y <= 1'b1;",
            "  end",
            "  case (a)",
            "    4'b0001: y = 1'b1;",
            "  endcase",
            "end",
            "for (i = start; i < LIMIT; i = i + 1) begin",
            "  y = y;",
            "end",
            "endmodule",
            "",
        ]
    ),
    encoding="utf-8",
)
codes = {issue.code for issue in lint_generated_rtl(spec("bad_constraints"), bad_dir)}
for expected in ("WIRE_INIT", "SIM_ONLY", "SENS_OR_SEPARATOR", "XZ_LITERAL", "CASE_DEFAULT", "COMB_NONBLOCKING_ASSIGN", "FOR_CONST_BOUNDS"):
    assert expected in codes, codes

good_dir = Path("_smoke_runs/remote_rtl_md_constraints/good")
good_dir.mkdir(parents=True, exist_ok=True)
(good_dir / "good_constraints.v").write_text(
    "\n".join(
        [
            "module good_constraints(input wire clk, input wire rst_n, input wire [3:0] a, output reg y);",
            "always @(posedge clk or negedge rst_n) begin",
            "  if (!rst_n) begin",
            "    y <= 1'b0;",
            "  end else begin",
            "    y <= a[0];",
            "  end",
            "end",
            "endmodule",
            "",
        ]
    ),
    encoding="utf-8",
)
assert lint_generated_rtl(spec("good_constraints"), good_dir) == []
PY
__PY__ -m runtime.verilog_generator eval-skill --evals skills/erie-verilog-generator/evals/evals.json --out _smoke_runs/remote_eval_skill.json --no-state
__PY__ - <<'PY'
import json
from pathlib import Path

report = json.loads(Path("_smoke_runs/remote_eval_skill.json").read_text(encoding="utf-8"))
summary = report["summary"]
assert summary["ok"] is True, summary
assert summary["case_count"] >= 30, summary
case = next((item for item in report["cases"] if item.get("id") == "rtl_md_constraints_gate"), None)
assert case and case.get("passed") is True, case
PY
""".strip()
    return template.replace("__PY__", py)


def remote_output_cleanup_snippet(cleanup_outputs: bool) -> str:
    if cleanup_outputs:
        return "rm -rf _smoke_runs workflow-state.json"
    return "echo 'remote_outputs_retained=_smoke_runs workflow-state.json'"


def simulator_priority_export_snippet(selected_backend: str) -> str:
    if not selected_backend:
        return "configured_simulator_backend=''\necho 'simulator_backend_selection=auto_priority'"
    return f"configured_simulator_backend={sh_quote(selected_backend)}\necho 'simulator_backend_selection={selected_backend}'"


def vivado_activation_snippet(selected_vivado: str = "", selected_backend: str = "", remote_runtime_config_path: str | None = None) -> str:
    config_hint = str(remote_runtime_config_path or remote_runtime_settings_relpath())
    if selected_backend and selected_backend != "xsim":
        return "echo 'vivado_settings=not_required_for_selected_backend'"
    return f"""
selected_vivado_settings={sh_quote(selected_vivado)}
toolchain_config_hint={sh_quote(config_hint)}
vivado_candidates_file="$(mktemp)"
for candidate in \
  "${{XILINX_VIVADO:-}}/settings64.sh" \
  "${{XILINX_VIVADO:-}}/../settings64.sh" \
  /tools/Xilinx/Vivado/*/settings64.sh \
  /tools/Xilinx/Vitis/*/settings64.sh \
  /opt/Xilinx/Vivado/*/settings64.sh; do
  if [ -f "$candidate" ]; then
    readlink -f "$candidate"
  fi
done | sort -u > "$vivado_candidates_file"
vivado_candidate_count="$(wc -l < "$vivado_candidates_file" | tr -d ' ')"
if [ -n "$selected_vivado_settings" ]; then
  if ! grep -Fx "$selected_vivado_settings" "$vivado_candidates_file" >/dev/null 2>&1; then
    echo "Configured Xilinx settings64.sh was not found on the remote server: $selected_vivado_settings" >&2
    echo "Available Xilinx toolchain choices:" >&2
    cat "$vivado_candidates_file" >&2
    rm -f "$vivado_candidates_file"
    exit 2
  fi
  echo "vivado_settings=$selected_vivado_settings"
  # shellcheck disable=SC1090
  . "$selected_vivado_settings"
elif [ "$vivado_candidate_count" -gt 1 ]; then
  echo "TOOLCHAIN_SELECTION_REQUIRED=1" >&2
  echo "Multiple Xilinx toolchain settings64.sh candidates were detected. Ask the user to choose one and persist it in: $toolchain_config_hint" >&2
  echo "Available Xilinx toolchain choices:" >&2
  cat "$vivado_candidates_file" >&2
  rm -f "$vivado_candidates_file"
  exit 2
elif ! command -v xvlog >/dev/null 2>&1 || ! command -v xelab >/dev/null 2>&1 || ! command -v xsim >/dev/null 2>&1; then
  if [ "$vivado_candidate_count" -eq 1 ]; then
    auto_vivado_settings="$(cat "$vivado_candidates_file")"
    echo "vivado_settings=$auto_vivado_settings"
    # shellcheck disable=SC1090
    . "$auto_vivado_settings"
  fi
fi
rm -f "$vivado_candidates_file"
""".strip()


def require_remote_relative_path(value: str, label: str) -> str:
    raw = value.strip()
    if not raw:
        raise ValueError(f"{label} must not be empty.")
    if "\\" in raw:
        raise ValueError(f"{label} must use POSIX separators.")
    path = PurePosixPath(raw)
    if path.is_absolute() or raw.startswith("~"):
        raise ValueError(f"{label} must be relative to the configured remote workdir.")
    parts = path.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"{label} must be a normalized relative path without parent traversal.")
    return path.as_posix()


def require_remote_absolute_file_path(value: str, label: str) -> str:
    raw = value.strip()
    if not raw:
        raise ValueError(f"{label} must not be empty.")
    if "\\" in raw:
        raise ValueError(f"{label} must use POSIX separators.")
    path = PurePosixPath(raw)
    if not path.is_absolute() or raw.startswith("~"):
        raise ValueError(f"{label} must be an absolute POSIX path on the remote server.")
    if any(part in {"", ".", ".."} for part in path.parts[1:]):
        raise ValueError(f"{label} must be normalized without parent traversal.")
    return path.as_posix()


def remote_join(*parts: str) -> str:
    normalized: list[str] = []
    for index, part in enumerate(parts):
        value = require_remote_relative_path(part, f"remote path part {index}")
        normalized.extend(PurePosixPath(value).parts)
    return PurePosixPath(*normalized).as_posix()


def sh_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


if __name__ == "__main__":
    raise SystemExit(main())
