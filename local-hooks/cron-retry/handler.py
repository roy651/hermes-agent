"""cron-retry hook — retry a cron job once on TimeoutError (execute step).

Add-on replacement for our core ``_process_job`` retry. At gateway:startup we wrap
``cron.scheduler.run_job`` (the execute step that can raise ``TimeoutError``). Because
``run_one_job`` calls ``run_job`` by module-global name, patching the global makes the
retry apply without touching core. save/deliver/mark live downstream in ``run_one_job``,
so they still happen exactly once. Scheduled jobs run in the gateway, so this covers the
real path (manual ``cron run`` in the CLI won't retry — fine, those are watched live).
Fails safe.
"""


def _with_retry(fn):
    def wrapped(job, *args, **kwargs):
        try:
            return fn(job, *args, **kwargs)
        except TimeoutError:
            name = job.get("name", job.get("id", "?")) if isinstance(job, dict) else "?"
            print(f"[cron-retry] '{name}' timed out on first attempt — retrying once", flush=True)
            return fn(job, *args, **kwargs)
    wrapped._retry_wrapped = True
    return wrapped


async def handle(event_type: str, context: dict) -> None:
    if event_type == "gateway:startup":
        _patch()


def _patch() -> None:
    try:
        import cron.scheduler as sched
        if getattr(sched.run_job, "_retry_wrapped", False):
            return
        sched.run_job = _with_retry(sched.run_job)
        print("[cron-retry] patched run_job (retry once on TimeoutError)", flush=True)
    except Exception as e:
        print(f"[cron-retry] FAILED to patch run_job: {e}", flush=True)


if __name__ == "__main__":  # self-test
    calls = {"n": 0}

    def fake(job):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("simulated")
        return (True, "out", "resp", None)

    wrapped = _with_retry(fake)
    result = wrapped({"name": "test-job"})
    assert calls["n"] == 2, "should retry exactly once"
    assert result == (True, "out", "resp", None), "should return second attempt's result"

    calls["n"] = 0

    def ok(job):
        calls["n"] += 1
        return "fine"

    assert _with_retry(ok)({"name": "x"}) == "fine" and calls["n"] == 1, "no retry when no timeout"
    print("[self-test OK] retries once on timeout; passes through otherwise")
