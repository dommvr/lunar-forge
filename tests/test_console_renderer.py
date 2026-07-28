from lunar_forge.events import EventFactory, EventType
from lunar_forge.ui.console_renderer import ConsoleRenderer
from lunar_forge.ui.renderers import Renderer


def _factory():
    return EventFactory(
        session_id="session_renderer",
        turn_id="turn_renderer",
        environment={},
        timestamp_factory=lambda: "2026-07-28T12:00:00Z",
    )


def test_console_renderer_handles_status_tool_final_and_usage_events():
    factory = _factory()
    events = (
        factory.create(
            EventType.STATUS_UPDATED,
            {"message": "Inspecting project..."},
        ),
        factory.create(
            EventType.TOOL_STARTED,
            {"tool_name": "read_file"},
        ),
        factory.create(
            EventType.TOOL_FINISHED,
            {"tool_name": "read_file", "ok": True},
        ),
        factory.create(
            EventType.ASSISTANT_MESSAGE_COMPLETED,
            {"text": "Inspection complete."},
        ),
        factory.create(
            EventType.MODEL_USAGE,
            {
                "reasoning_effort": "high",
                "model_calls": 1,
                "exact_calls": 1,
                "estimated_calls": 0,
                "input_tokens": 20,
                "output_tokens": 5,
                "total_tokens": 25,
            },
        ),
    )

    renderer = ConsoleRenderer()
    output = renderer.consume(events)

    assert isinstance(renderer, Renderer)
    assert "Inspecting project..." in output
    assert "Tool: read_file (started)" in output
    assert "Tool: read_file (completed)" in output
    assert "Inspection complete." in output
    assert "Model usage:" in output
    assert "- Reasoning effort: high" in output
    assert "- Total tokens: 25" in output


def test_one_shot_console_renderer_only_returns_final_message():
    factory = _factory()
    output = ConsoleRenderer.one_shot().consume(
        (
            factory.create(
                EventType.STATUS_UPDATED,
                {"message": "Working..."},
            ),
            factory.create(
                EventType.TOOL_STARTED,
                {"tool_name": "read_file"},
            ),
            factory.create(
                EventType.ASSISTANT_MESSAGE_COMPLETED,
                {"text": "Current one-shot answer."},
            ),
            factory.create(
                EventType.MODEL_USAGE,
                {
                    "reasoning_effort": "medium",
                    "total_tokens": 10,
                },
            ),
        )
    )

    assert output == "Current one-shot answer."


def test_console_renderer_does_not_duplicate_streamed_completion():
    factory = _factory()
    output = ConsoleRenderer.one_shot().consume(
        (
            factory.create(
                EventType.ASSISTANT_MESSAGE_DELTA,
                {"delta": "Hello"},
            ),
            factory.create(
                EventType.ASSISTANT_MESSAGE_DELTA,
                {"delta": " world"},
            ),
            factory.create(
                EventType.ASSISTANT_MESSAGE_COMPLETED,
                {"text": "Hello world"},
            ),
        )
    )

    assert output == "Hello world"
