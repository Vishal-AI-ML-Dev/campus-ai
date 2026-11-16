"""Job-queue routing tests (Batch 5).

These verify the ``jobs.enqueue`` decision logic WITHOUT needing a live Redis:
  * no queue configured -> fall back to FastAPI BackgroundTasks (current behaviour)
  * queue available      -> push to the queue, do NOT run in-process
  * enqueue blows up      -> fall back to BackgroundTasks (never lose the job)

The end-to-end skill/project flows are already covered by the existing suite,
which runs in the in-process fallback (REDIS_URL is forced empty in conftest).
"""

import jobs


class _FakeBackgroundTasks:
    """Records add_task calls the way FastAPI's BackgroundTasks would receive them."""

    def __init__(self):
        self.calls = []

    def add_task(self, func, *args, **kwargs):
        self.calls.append((func, args, kwargs))


class _FakeQueue:
    """Minimal stand-in for rq.Queue capturing enqueue() options."""

    def __init__(self):
        self.enqueued = []

    def enqueue(self, func, args=(), kwargs=None, **options):
        self.enqueued.append((func, args, kwargs or {}, options))


def _sample(_value):  # a plain importable target for enqueue
    return None


def test_enqueue_falls_back_to_background_tasks_without_redis(monkeypatch):
    jobs._reset()
    monkeypatch.setattr(jobs.settings, "REDIS_URL", "", raising=False)

    bg = _FakeBackgroundTasks()
    jobs.enqueue(bg, _sample, 42)

    assert bg.calls == [(_sample, (42,), {})]
    jobs._reset()


def test_enqueue_uses_queue_when_available(monkeypatch):
    queue = _FakeQueue()
    monkeypatch.setattr(jobs, "get_queue", lambda: queue)

    bg = _FakeBackgroundTasks()
    jobs.enqueue(bg, _sample, 7)

    # Routed to the durable queue, NOT the in-process fallback.
    assert bg.calls == []
    assert len(queue.enqueued) == 1
    func, args, kwargs, options = queue.enqueued[0]
    assert func is _sample
    assert args == (7,)
    assert kwargs == {}
    assert options["job_timeout"] == jobs.JOB_TIMEOUT


def test_enqueue_falls_back_when_queue_raises(monkeypatch):
    class _BoomQueue:
        def enqueue(self, *a, **k):
            raise RuntimeError("redis down")

    monkeypatch.setattr(jobs, "get_queue", lambda: _BoomQueue())

    bg = _FakeBackgroundTasks()
    jobs.enqueue(bg, _sample, 99)

    # Broker hiccup must not lose the job - it runs in-process instead.
    assert bg.calls == [(_sample, (99,), {})]
