"""Runs the deployment scripts against stub commands, so they can be tested without Google Cloud.

Each stub is a small executable placed first on PATH. It appends its arguments to a
log, then prints whatever the test told it to. The tests assert on that log: which
commands ran, with what arguments, and in what order.
"""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SUBMIT_FLINK = ROOT / "infra" / "runtime" / "dataproc" / "submit-flink.sh"
FLINK_ENV = "gs://dataproc/dataproc/flink.env"


class Sandbox:
    def __init__(self, tmp_path: Path) -> None:
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        self.log = tmp_path / "calls.log"
        self.log.touch()
        self.tmp_path = tmp_path

    def stub(self, name: str, script: str = "") -> None:
        """script is bash, with the arguments in $@ and $* as usual."""
        path = self.bin / name
        path.write_text(f'#!/usr/bin/env bash\necho "{name} $*" >> "{self.log}"\n{script}\n')
        path.chmod(path.stat().st_mode | stat.S_IEXEC)

    def run(self, command: list[str], **env) -> subprocess.CompletedProcess:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            env={**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}", **env},
            cwd=ROOT,
        )

    @property
    def calls(self) -> list[str]:
        return self.log.read_text().splitlines()

    def index_of(self, fragment: str) -> int:
        return next(i for i, call in enumerate(self.calls) if fragment in call)


@pytest.fixture
def sandbox(tmp_path) -> Sandbox:
    return Sandbox(tmp_path)


# ---- submit-flink.sh: what the Dataproc job runs ---------------------------------


def stub_flink_env(sandbox: Sandbox, jar_exists: bool = True) -> None:
    env_file = sandbox.tmp_path / "flink.env"
    env_file.write_text(
        "JAR_URI=gs://artifacts/flink/analytics-flink.jar\n"
        "KAFKA_BOOTSTRAP=bootstrap.warehouse.asia-south1.managedkafka.demo.cloud.goog:9092\n"
        "CHECKPOINT_DIR=gs://dataproc/flink-checkpoints\n"
    )
    # The script fetches flink.env, then the JAR. Only the first needs real content.
    sandbox.stub(
        "gcloud",
        f'[[ "$*" == *flink.env* ]] && cp "{env_file}" /tmp/flink.env && exit 0\n'
        f'[[ "$*" == *analytics-flink.jar* ]] && exit {0 if jar_exists else 1}\n'
        "exit 0",
    )


def stub_yarn(sandbox: Sandbox, *applications: str) -> None:
    """Each application is "id name", listed as `yarn application -list` prints them."""
    rows = "".join(f"{app.split()[0]}\\t{app.split()[1]}\\tApache Flink\\troot\\tdefault\\tRUNNING\\n" for app in applications)
    sandbox.stub("yarn", f'[[ "$*" == *-list* ]] && printf "{rows}"; exit 0')


def test_first_run_submits_the_job_with_google_sign_in(sandbox):
    stub_flink_env(sandbox)
    stub_yarn(sandbox)
    sandbox.stub("flink")

    result = sandbox.run(["bash", str(SUBMIT_FLINK), FLINK_ENV])

    assert result.returncode == 0, result.stderr
    assert not any("application -kill" in call for call in sandbox.calls)
    submitted = next(call for call in sandbox.calls if call.startswith("flink run"))
    assert "-m yarn-cluster" in submitted and "-ynm warehouse-analytics" in submitted
    assert "--bootstrap bootstrap.warehouse.asia-south1.managedkafka.demo.cloud.goog:9092" in submitted
    assert "--kafka-auth google" in submitted
    assert "--checkpoint-dir gs://dataproc/flink-checkpoints" in submitted


def test_the_taskmanager_fits_the_two_vcores_yarn_offers(sandbox):
    # The JobManager takes 1 vcore. A TaskManager asking 1 per slot would need 2 more.
    stub_flink_env(sandbox)
    stub_yarn(sandbox)
    sandbox.stub("flink")

    sandbox.run(["bash", str(SUBMIT_FLINK), FLINK_ENV])

    submitted = next(call for call in sandbox.calls if call.startswith("flink run"))
    assert "-yD yarn.containers.vcores=1" in submitted


def test_redeploy_kills_the_running_job_first(sandbox):
    stub_flink_env(sandbox)
    stub_yarn(sandbox, "application_17_0003 warehouse-analytics")
    sandbox.stub("flink")

    result = sandbox.run(["bash", str(SUBMIT_FLINK), FLINK_ENV])

    assert result.returncode == 0, result.stderr
    assert sandbox.index_of("application -kill application_17_0003") < sandbox.index_of("flink run")


def test_other_yarn_applications_are_left_alone(sandbox):
    stub_flink_env(sandbox)
    stub_yarn(sandbox, "application_17_0009 spark-shell")
    sandbox.stub("flink")

    sandbox.run(["bash", str(SUBMIT_FLINK), FLINK_ENV])

    assert not any("application -kill" in call for call in sandbox.calls)


def test_fails_loudly_if_the_jar_was_never_uploaded(sandbox):
    stub_flink_env(sandbox, jar_exists=False)
    stub_yarn(sandbox)
    sandbox.stub("flink")

    result = sandbox.run(["bash", str(SUBMIT_FLINK), FLINK_ENV])

    assert result.returncode == 1
    assert "make flink-jar" in result.stderr
    assert not any(call.startswith("flink run") for call in sandbox.calls)


# ---- down.sh: a wrong order here leaves resources billing ---------------------


def stub_cloud(sandbox: Sandbox, cluster_exists: bool = True) -> None:
    sandbox.stub("gcloud", f'[[ "$*" == *"clusters describe"* ]] && exit {0 if cluster_exists else 1}; exit 0')
    sandbox.stub("terraform")
    sandbox.stub("sleep")


def test_down_removes_kubernetes_resources_before_terraform_destroy(sandbox):
    stub_cloud(sandbox)
    sandbox.stub("kubectl")

    result = sandbox.run(["bash", "scripts/down.sh"])

    assert result.returncode == 0, result.stderr
    assert sandbox.index_of("delete application root") < sandbox.index_of("destroy")


def test_down_waits_until_the_load_balancer_and_disks_are_gone(sandbox):
    stub_cloud(sandbox)
    counter = sandbox.tmp_path / "polls"
    counter.write_text("0")
    # The ingress lingers for three polls, as a load balancer does while it is removed.
    sandbox.stub(
        "kubectl",
        f"""
if [[ "$*" == *"get ingress"* ]]; then
  n=$(cat "{counter}"); echo $((n + 1)) > "{counter}"
  [ "$n" -lt 3 ] && echo ingress/warehouse
fi
exit 0
""",
    )

    sandbox.run(["bash", "scripts/down.sh"])

    assert counter.read_text().strip() == "4"
    last_poll = max(i for i, call in enumerate(sandbox.calls) if "get ingress" in call)
    assert last_poll < sandbox.index_of("destroy")


def test_down_still_destroys_when_the_cluster_is_already_gone(sandbox):
    stub_cloud(sandbox, cluster_exists=False)
    sandbox.stub("kubectl")

    result = sandbox.run(["bash", "scripts/down.sh"])

    assert result.returncode == 0, result.stderr
    assert not any(call.startswith("kubectl") for call in sandbox.calls)
    assert any("destroy" in call for call in sandbox.calls)


def test_down_reports_anything_left_billing(sandbox):
    stub_cloud(sandbox)
    sandbox.stub("kubectl")

    sandbox.run(["bash", "scripts/down.sh"])

    assert any("forwarding-rules list" in call for call in sandbox.calls)
    assert any("disks list" in call for call in sandbox.calls)
    assert sandbox.index_of("destroy") < sandbox.index_of("disks list")


# ---- up.sh and preflight.sh ------------------------------------------------------


def stub_quota(sandbox: Sandbox, regional_free: int = 100, global_free: int = 32, n2_free: int = 200) -> None:
    regional = (
        '{"quotas": [{"metric": "CPUS", "limit": 100, "usage": %d}, {"metric": "N2_CPUS", "limit": 200, "usage": %d},'
        ' {"metric": "E2_CPUS", "limit": 0, "usage": 0}]}' % (100 - regional_free, 200 - n2_free)
    )
    project = '{"quotas": [{"metric": "CPUS_ALL_REGIONS", "limit": 32, "usage": %d}]}' % (32 - global_free)
    sandbox.stub(
        "gcloud",
        f"[[ \"$*\" == *\"regions describe\"* ]] && echo '{regional}' && exit 0\n"
        f"[[ \"$*\" == *\"project-info describe\"* ]] && echo '{project}' && exit 0\n"
        '[[ "$*" == *"get-value project"* ]] && echo demo && exit 0\n'
        "exit 0",
    )


def test_up_prints_the_public_url(sandbox):
    stub_quota(sandbox)
    sandbox.stub(
        "terraform",
        '[[ "$*" == *"output -raw cluster_name"* ]] && echo -n warehouse\n'
        '[[ "$*" == *"output -raw cluster_zone"* ]] && echo -n asia-south1-a\n'
        "exit 0",
    )
    sandbox.stub("kubectl", 'echo -n "34.120.0.10"')

    result = sandbox.run(["bash", "scripts/up.sh"])

    assert result.returncode == 0, result.stderr
    assert "http://34.120.0.10" in result.stdout
    assert sandbox.index_of("apply") < sandbox.index_of("get-credentials warehouse --zone asia-south1-a")


def stub_up_cluster(sandbox: Sandbox, prometheus: bool) -> None:
    stub_quota(sandbox)
    sandbox.stub("terraform")
    sandbox.stub("sleep")
    statefulset = "echo statefulset.apps/prometheus-0" if prometheus else "exit 0"
    sandbox.stub(
        "kubectl",
        '[[ "$*" == *"get ingress"* ]] && echo -n "34.120.0.10" && exit 0\n'
        f'[[ "$*" == *"get statefulset"* ]] && {{ {statefulset}; exit 0; }}\n'
        "exit 0",
    )


def test_up_restarts_the_prometheus_operator_if_prometheus_never_appears(sandbox):
    stub_up_cluster(sandbox, prometheus=False)

    result = sandbox.run(["bash", "scripts/up.sh"])

    assert result.returncode == 0, result.stderr
    assert "Restarting its operator" in result.stdout
    assert sandbox.index_of("delete pod -l app=kube-prometheus-stack-operator") > 0
    assert "http://34.120.0.10" in result.stdout


def test_up_leaves_a_running_prometheus_alone(sandbox):
    stub_up_cluster(sandbox, prometheus=True)

    result = sandbox.run(["bash", "scripts/up.sh"])

    assert result.returncode == 0, result.stderr
    assert not any("delete pod" in call for call in sandbox.calls)


def test_up_explains_itself_if_no_load_balancer_appears(sandbox):
    stub_quota(sandbox)
    sandbox.stub("terraform")
    sandbox.stub("kubectl")
    sandbox.stub("sleep")

    result = sandbox.run(["bash", "scripts/up.sh"])

    assert result.returncode == 1
    assert "kubectl -n argocd get applications" in result.stderr


def test_up_creates_nothing_when_the_vcpu_quota_is_short(sandbox):
    # Other VMs in the project already use 26 of its 32 vCPUs.
    stub_quota(sandbox, global_free=6)
    sandbox.stub("terraform")

    result = sandbox.run(["bash", "scripts/up.sh"])

    assert result.returncode == 1
    assert "Not enough vCPUs" in result.stderr
    assert not any(call.startswith("terraform") for call in sandbox.calls)


def test_preflight_reports_all_three_quotas(sandbox):
    stub_quota(sandbox)

    result = sandbox.run(["bash", "scripts/preflight.sh"])

    assert result.returncode == 0, result.stderr
    assert "100 in asia-south1, 200 of N2 there, 32 across the project" in result.stdout


def test_preflight_catches_a_machine_family_with_no_quota(sandbox):
    # This project really has 0 E2 vCPUs in asia-south1, though 100 vCPUs in all.
    stub_quota(sandbox)

    result = sandbox.run(["bash", "scripts/preflight.sh"], MACHINE_FAMILY="E2")

    assert result.returncode == 1
    assert "0 of E2 there" in result.stdout


# ---- chaos.sh: the demo must hit the right target ------------------------------


def stub_cluster(sandbox: Sandbox) -> None:
    sandbox.stub(
        "kubectl",
        r"""
args="$*"
if [[ "$args" == *"get pods -l app.kubernetes.io/name=job-scheduler"* ]]; then
  printf 'pod/job-scheduler-a\npod/job-scheduler-b\n'
elif [[ "$args" == *"get pods -l app.kubernetes.io/name=picking"* ]]; then
  printf 'pod/picking-a\npod/picking-b\n'
elif [[ "$args" == *"exec job-scheduler-a"* ]]; then
  echo '{"replica":"a","leading":false,"epoch":-1}'
elif [[ "$args" == *"exec job-scheduler-b"* ]]; then
  echo '{"replica":"b","leading":true,"epoch":4}'
elif [[ "$args" == *"exec zookeeper-1"* ]]; then
  echo leader
elif [[ "$args" == *"exec zookeeper-"* ]]; then
  echo follower
fi
exit 0
""",
    )


def test_kill_leader_targets_the_actual_leader(sandbox):
    stub_cluster(sandbox)

    result = sandbox.run(["bash", "scripts/chaos.sh", "kill-leader"])

    assert result.returncode == 0, result.stderr
    assert any("delete pod job-scheduler-b --grace-period=0 --force" in call for call in sandbox.calls)
    assert not any("delete pod job-scheduler-a" in call for call in sandbox.calls)


def test_kill_zookeeper_targets_the_ensemble_leader(sandbox):
    stub_cluster(sandbox)

    sandbox.run(["bash", "scripts/chaos.sh", "kill-zookeeper"])

    assert any("delete pod zookeeper-1" in call for call in sandbox.calls)


def test_kill_picker_skips_graceful_shutdown_like_a_real_crash(sandbox):
    stub_cluster(sandbox)

    sandbox.run(["bash", "scripts/chaos.sh", "kill-picker"])

    assert any("delete pod picking-a --grace-period=0 --force" in call for call in sandbox.calls)


def test_hang_posts_to_the_chaos_route_inside_the_pod(sandbox):
    stub_cluster(sandbox)

    sandbox.run(["bash", "scripts/chaos.sh", "hang-picker"])

    call = next(call for call in sandbox.calls if "exec picking-a" in call)
    assert "http://localhost:8080/chaos/hang POST" in call


def test_unknown_command_shows_usage(sandbox):
    stub_cluster(sandbox)

    result = sandbox.run(["bash", "scripts/chaos.sh", "nonsense"])

    assert result.returncode == 1
    assert "kill-leader" in result.stdout


# ---- shell syntax --------------------------------------------------------------


@pytest.mark.parametrize(
    "script",
    sorted(str(p) for p in (ROOT / "scripts").glob("*.sh")) + sorted(str(p) for p in (ROOT / "infra").rglob("*.sh")),
)
def test_every_shell_script_parses(script):
    assert subprocess.run(["bash", "-n", script], capture_output=True).returncode == 0
