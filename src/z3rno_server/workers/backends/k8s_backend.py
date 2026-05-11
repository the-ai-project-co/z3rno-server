"""K8s Jobs backend — submit one Job per task.

Designed for self-hosted clusters where Argo / KEDA / Tekton are
already in the picture and a fresh Pod-per-task model is cheaper
than running a permanent Celery pool.

Rendering: ``options.template`` overrides the default Job template
embedded below. Operators usually point this at a
``ConfigMap``-mounted file so changes don't require a server image
rebuild. The payload is passed through as the ``Z3RNO_TASK_PAYLOAD``
env var (JSON-encoded); the entrypoint inside the image is
responsible for decoding and dispatching.

The ``kubernetes`` Python client is lazy-imported so a celery deploy
doesn't pull it in.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any

from z3rno_server.workers.backends.base import (
    DispatchResult,
    JobBackend,
    JobBackendError,
)

DEFAULT_JOB_TEMPLATE: dict[str, Any] = {
    "apiVersion": "batch/v1",
    "kind": "Job",
    "metadata": {
        "name": None,  # filled in per dispatch
        "labels": {"app.kubernetes.io/name": "z3rno", "app.kubernetes.io/component": "task"},
    },
    "spec": {
        "backoffLimit": 2,
        "ttlSecondsAfterFinished": 3600,
        "template": {
            "metadata": {"labels": {"app.kubernetes.io/name": "z3rno"}},
            "spec": {
                "restartPolicy": "Never",
                "containers": [
                    {
                        "name": "task",
                        "image": "ghcr.io/the-ai-project-co/z3rno-server:latest",
                        "command": ["z3rno-task-runner"],
                        "env": [],
                    }
                ],
            },
        },
    },
}


class K8sJobsBackend(JobBackend):
    name = "k8s_jobs"

    def __init__(
        self,
        *,
        namespace: str = "z3rno",
        job_template: dict[str, Any] | None = None,
        image: str | None = None,
    ) -> None:
        self.namespace = namespace
        # Deep-ish copy via json roundtrip is fine — templates are small.
        self.job_template = json.loads(json.dumps(job_template or DEFAULT_JOB_TEMPLATE))
        if image:
            self.job_template["spec"]["template"]["spec"]["containers"][0]["image"] = image

    def _client(self) -> Any:
        try:
            from kubernetes import (  # type: ignore[import-not-found]
                client,
                config,
            )
        except ImportError as exc:
            raise JobBackendError(
                "K8s backend requested but the `kubernetes` package is not "
                "installed. `pip install kubernetes`."
            ) from exc
        try:
            # In-cluster config first; fall back to kubeconfig for
            # operator-local dispatches.
            try:
                config.load_incluster_config()
            except Exception:
                config.load_kube_config()
        except Exception as exc:
            raise JobBackendError(f"K8s config load failed: {exc}") from exc
        return client.BatchV1Api()

    def _render(self, task_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        manifest: dict[str, Any] = json.loads(json.dumps(self.job_template))
        job_name = f"z3rno-{task_name.replace('.', '-').replace('_', '-')}-{uuid.uuid4().hex[:8]}"
        manifest["metadata"]["name"] = job_name
        manifest["metadata"].setdefault("labels", {})["z3rno.task"] = task_name
        env = manifest["spec"]["template"]["spec"]["containers"][0].setdefault("env", [])
        env.append({"name": "Z3RNO_TASK_NAME", "value": task_name})
        env.append({"name": "Z3RNO_TASK_PAYLOAD", "value": json.dumps(payload)})
        env.append({"name": "Z3RNO_TASK_DISPATCH_TS", "value": str(int(time.time()))})
        return manifest

    def enqueue(
        self,
        *,
        task_name: str,
        payload: dict[str, Any],
        options: dict[str, Any] | None = None,
    ) -> DispatchResult:
        api = self._client()
        manifest = self._render(task_name, payload)
        try:
            api.create_namespaced_job(
                namespace=(options or {}).get("namespace", self.namespace),
                body=manifest,
            )
        except Exception as exc:
            raise JobBackendError(
                f"K8s Job submit failed for {task_name}: {exc}"
            ) from exc
        return DispatchResult(
            job_id=manifest["metadata"]["name"],
            backend=self.name,
            extra={
                "namespace": (options or {}).get("namespace", self.namespace),
                "image": os.environ.get("Z3RNO_TASK_IMAGE", ""),
            },
        )
