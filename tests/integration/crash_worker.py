"""A worker in its own process, so a test can SIGKILL it.

A dead process cannot close its ZooKeeper session. ZooKeeper only learns of the
death when the session times out, and that path is what the test has to exercise.

Usage: crash_worker.py <worker_id> <zk_hosts> <session_timeout>
"""
from __future__ import annotations

import sys
import threading
from dataclasses import replace

from warehouse_common import zk
from warehouse_common.rpc import scheduler_pb2
from warehouse_common.settings import Settings
from warehouse_common.worker import TaskWorker


class StubScheduler:
    def GetTask(self, request, timeout=None):
        return scheduler_pb2.GetTaskResponse(
            found=True, task_id=request.task_id, order_id="o1", task_type="PICK", zone="A", attempt=1
        )


class NullProducer:
    def send(self, *args, **kwargs) -> None:
        pass


def main() -> None:
    settings = replace(
        Settings.from_env("crash-worker"),
        worker_id=sys.argv[1],
        zk_hosts=sys.argv[2],
        zk_session_timeout=float(sys.argv[3]),
    )
    client = zk.connect(settings)
    never = threading.Event()
    TaskWorker(
        settings,
        client,
        capability="picking",
        zones=[],
        process=lambda task: never.wait(),
        scheduler=StubScheduler(),
        producer=NullProducer(),
    ).start()
    print("ready", flush=True)
    never.wait()


if __name__ == "__main__":
    main()
