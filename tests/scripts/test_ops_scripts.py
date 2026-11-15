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


# ---- recover.sh: carrying on after an interrupted make up --------------------------


def stub_recover(sandbox: Sandbox, state=(), gcloud=None, kubectl=None, lock="", sql_suffix="abc123") -> Path:
    """gcloud and kubectl map a fragment of the command line to what it prints.

    A value may be a list, printed one item per call, the last one repeating, to
    model a resource Google is still creating.
    """
    state_lines = "\\n".join(state)
    sandbox.stub(
        "terraform",
        f'[[ "$*" == *"state list"* ]] && printf "{state_lines}" && exit 0\n'
        f'[[ "$*" == *"state show"* ]] && echo \'  hex = "{sql_suffix}"\' && exit 0\n'
        "exit 0",
    )

    def responder(name: str, table: dict) -> None:
        lines = []
        for i, (fragment, output) in enumerate(table.items()):
            outputs = output if isinstance(output, list) else [output]
            counter = sandbox.tmp_path / f"{name}-{i}.count"
            choices = " ".join(f"'{o}'" for o in outputs)
            lines.append(
                f'if [[ "$*" == *"{fragment}"* ]]; then\n'
                f'  n=$(cat "{counter}" 2>/dev/null || echo 0); echo $((n+1)) > "{counter}"\n'
                f"  outs=({choices}); i=$(( n < ${{#outs[@]}} ? n : ${{#outs[@]}} - 1 ))\n"
                '  echo "${outs[$i]}"; exit 0\nfi'
            )
        # Like the real tools, looking up something that does not exist fails, and
        # anything else (a delete, get-credentials) succeeds.
        missing = '[[ "$*" == *describe* || "$*" == *" list"* || "$*" == *" get "* || "$*" == *"storage cat"* ]] && exit 1'
        sandbox.stub(name, "\n".join(lines) + f"\n{missing}\nexit 0")

    responder("gcloud", {"get-value project": "demo", "storage cat": lock, **(gcloud or {})})
    responder("kubectl", kubectl or {})
    sandbox.stub("sleep")
    return sandbox.tmp_path / "recovered_imports.tf"


def run_recover(sandbox: Sandbox, out: Path, *args: str) -> subprocess.CompletedProcess:
    return sandbox.run(["bash", "scripts/recover.sh", *args], RECOVERED_IMPORTS=str(out))


def test_recover_does_nothing_after_a_clean_run(sandbox):
    out = stub_recover(sandbox)

    result = run_recover(sandbox, out)

    assert result.returncode == 0, result.stderr
    assert "Nothing left behind" in result.stdout
    assert not out.exists()


def test_recover_adopts_a_cluster_created_after_the_connection_dropped(sandbox):
    out = stub_recover(
        sandbox,
        gcloud={
            "container clusters describe warehouse": "RUNNING",
            "node-pools describe default-pool": "default-pool",
        },
    )

    result = run_recover(sandbox, out)

    assert result.returncode == 0, result.stderr
    blocks = out.read_text()
    assert "to = google_container_cluster.this" in blocks
    assert 'id = "projects/demo/locations/asia-south1-a/clusters/warehouse"' in blocks
    # Terraform deletes this pool as the last step of creating a cluster.
    assert any("node-pools delete default-pool" in call for call in sandbox.calls)


def test_recover_leaves_resources_terraform_already_knows_alone(sandbox):
    out = stub_recover(
        sandbox,
        state=["google_container_cluster.this", "google_managed_kafka_cluster.this"],
        gcloud={"container clusters describe warehouse": "RUNNING", "managed-kafka clusters describe": "ACTIVE"},
    )

    result = run_recover(sandbox, out)

    assert result.returncode == 0, result.stderr
    assert not out.exists()
    assert not any("delete" in call for call in sandbox.calls)


def test_recover_waits_for_a_resource_google_is_still_creating(sandbox):
    out = stub_recover(sandbox, gcloud={"managed-kafka clusters describe": ["CREATING", "CREATING", "ACTIVE"]})

    result = run_recover(sandbox, out)

    assert result.returncode == 0, result.stderr
    assert "waiting: CREATING" in result.stderr
    assert sum(call.startswith("sleep") for call in sandbox.calls) == 2
    assert 'id = "projects/demo/locations/asia-south1/clusters/warehouse"' in out.read_text()


def test_recover_deletes_a_dataproc_cluster_terraform_cannot_import(sandbox):
    out = stub_recover(sandbox, gcloud={"dataproc clusters describe": "RUNNING"})

    result = run_recover(sandbox, out)

    assert result.returncode == 0, result.stderr
    assert any("dataproc clusters delete warehouse-analytics" in call for call in sandbox.calls)
    assert not out.exists()


def test_recover_finds_the_database_by_the_suffix_in_the_state(sandbox):
    out = stub_recover(
        sandbox,
        gcloud={
            "sql instances describe warehouse-abc123": "RUNNABLE",
            "sql databases describe warehouse": "warehouse",
            "sql users list": "warehouse",
        },
    )

    result = run_recover(sandbox, out)

    blocks = out.read_text()
    assert result.returncode == 0, result.stderr
    assert 'id = "projects/demo/instances/warehouse-abc123"' in blocks
    assert 'id = "projects/demo/instances/warehouse-abc123/databases/warehouse"' in blocks
    assert 'id = "demo/warehouse-abc123/warehouse"' in blocks


def test_recover_adopts_kubernetes_objects_and_helm_releases(sandbox):
    out = stub_recover(
        sandbox,
        state=["google_container_cluster.this"],
        gcloud={"container clusters describe warehouse": "RUNNING"},
        kubectl={"namespace argocd": "namespace/argocd", "name=argocd": "secret/sh.helm.release.v1.argocd.v1"},
    )

    result = run_recover(sandbox, out)

    blocks = out.read_text()
    assert result.returncode == 0, result.stderr
    assert 'to = kubernetes_namespace_v1.this["argocd"]\n  id = "argocd"' in blocks
    assert 'to = helm_release.argocd\n  id = "argocd/argocd"' in blocks
    assert "helm_release.root_application" not in blocks


def test_recover_releases_a_lock_left_by_this_computer(sandbox):
    who = f"{os.environ.get('USER') or subprocess.run(['id', '-un'], capture_output=True, text=True).stdout.strip()}"
    host = subprocess.run(["hostname"], capture_output=True, text=True).stdout.strip()
    lock = '{"ID": "1234-abcd", "Who": "%s@%s"}' % (who, host)
    out = stub_recover(sandbox, lock=lock)
    sandbox.stub("pgrep", "exit 1")

    result = run_recover(sandbox, out, "--lock-only")

    assert result.returncode == 0, result.stderr
    assert any("force-unlock -force 1234-abcd" in call for call in sandbox.calls)


def test_recover_will_not_release_someone_elses_lock(sandbox):
    out = stub_recover(sandbox, lock='{"ID": "1234-abcd", "Who": "aman@other-laptop"}')
    sandbox.stub("pgrep", "exit 1")

    result = run_recover(sandbox, out, "--lock-only")

    assert result.returncode == 1
    assert "locked by aman@other-laptop" in result.stderr
    assert not any("force-unlock" in call for call in sandbox.calls)


def test_up_adopts_leftovers_before_applying_and_cleans_up_after(sandbox):
    stub_up_cluster(sandbox, prometheus=True)
    out = sandbox.tmp_path / "recovered_imports.tf"

    result = sandbox.run(["bash", "scripts/up.sh"], RECOVERED_IMPORTS=str(out))

    assert result.returncode == 0, result.stderr
    assert sandbox.index_of("state list") < sandbox.index_of("-chdir=infra/runtime apply")
