"""Tests for tracing and metrics collection."""

import pytest

from core.observability import MetricsCollector, Trace


@pytest.mark.asyncio
class TestTrace:
    async def test_basic_span(self):
        trace = Trace()
        async with trace.span("test_op") as s:
            pass

        assert len(trace.spans) == 1
        assert trace.spans[0].name == "test_op"
        assert trace.spans[0].status == "ok"
        assert trace.spans[0].duration_ms is not None

    async def test_nested_spans(self):
        trace = Trace()
        async with trace.span("outer") as outer:
            async with trace.span("inner") as inner:
                pass

        assert len(trace.spans) == 2
        assert trace.spans[1].parent_id == trace.spans[0].span_id

    async def test_error_span(self):
        trace = Trace()
        with pytest.raises(ValueError):
            async with trace.span("failing"):
                raise ValueError("boom")

        assert trace.spans[0].status == "error"
        assert "boom" in trace.spans[0].metadata.get("error", "")

    async def test_trace_to_dict(self):
        trace = Trace()
        async with trace.span("op"):
            pass

        d = trace.to_dict()
        assert "trace_id" in d
        assert len(d["spans"]) == 1


@pytest.mark.asyncio
class TestMetricsCollector:
    async def test_increment(self):
        mc = MetricsCollector()
        await mc.increment("calls")
        await mc.increment("calls")
        await mc.increment("calls", 3)

        summary = await mc.get_summary()
        assert summary["counters"]["calls"] == 5

    async def test_histogram(self):
        mc = MetricsCollector()
        for v in [10, 20, 30, 40, 50]:
            await mc.histogram("latency", v)

        summary = await mc.get_summary()
        h = summary["histograms"]["latency"]
        assert h["count"] == 5
        assert h["min"] == 10
        assert h["max"] == 50
        assert h["avg"] == 30.0

    async def test_reset(self):
        mc = MetricsCollector()
        await mc.increment("x")
        await mc.reset()
        summary = await mc.get_summary()
        assert summary["counters"] == {}

    async def test_flush(self, tmp_path):
        import core.observability as obs
        original = obs.METRICS_PATH
        obs.METRICS_PATH = tmp_path / "metrics.json"
        try:
            mc = MetricsCollector()
            await mc.increment("test", 1)
            await mc.flush()
            assert obs.METRICS_PATH.exists()
        finally:
            obs.METRICS_PATH = original
