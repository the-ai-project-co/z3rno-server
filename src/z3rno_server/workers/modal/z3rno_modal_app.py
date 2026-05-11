"""Phase F slice 6 — Modal app scaffold.

Run ``modal deploy z3rno_modal_app.py`` after installing modal and
authenticating. Each ``@app.function`` here is the Modal-side
counterpart to a Celery task; ``ModalBackend`` looks them up by name
and ``.spawn(...)``s them.

This file is *not* imported by the server; it's a deploy-time
artifact. Keeping it under ``workers/modal/`` rather than at repo
root so it ships in the wheel and operators can run
``modal deploy $(python -c 'import z3rno_server.workers.modal as m; print(m.__file__)')``.
"""


from __future__ import annotations

try:
    import modal  # type: ignore[import-not-found]
except ImportError as exc:  # pragma: no cover — scaffold not invoked in tests
    raise SystemExit(
        "modal is not installed. Run `pip install modal` and "
        "`modal token new` before deploying."
    ) from exc


image = modal.Image.debian_slim().pip_install("z3rno-server")
app = modal.App(name="z3rno", image=image)


@app.function(timeout=900)
def forge_distill(**payload: object) -> object:
    """Bridge to the existing ``z3rno.forge_distill`` Celery task body.

    Operators wire their LLM provider env vars (``OPENAI_API_KEY``,
    ``LLM_MODEL``, etc.) as Modal Secrets — see the Phase F operator
    reference for the full secret bindings.
    """
    from z3rno_server.workers.forge import (  # type: ignore[attr-defined]
        run_forge_distill,
    )

    return run_forge_distill(**payload)


@app.function(timeout=600)
def ingest_run(**payload: object) -> object:
    from z3rno_server.workers.ingest import (  # type: ignore[attr-defined]
        run_ingest,
    )

    return run_ingest(**payload)


@app.function(timeout=1800)
def refine_run(**payload: object) -> object:
    from z3rno_server.workers.refine import (  # type: ignore[attr-defined]
        run_refine,
    )

    return run_refine(**payload)
