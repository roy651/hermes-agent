"""voice-transcript hook — show the STT transcript prominently for the user.

Add-on replacement for the gateway/run.py core change. At gateway:startup we wrap
``GatewayRunner._enrich_message_with_transcription`` and, when its result carries the
agent-facing voice marker, prepend a visible "Voice Message Transcript" block. Voice
messages only ever flow through the gateway, so this hook covers the real path.
Fails safe: if the marker isn't found or anything errors, the original text is returned.
"""
import re

_MARKER_RE = re.compile(
    r'\[The user sent a voice message~ Here\'s what they said: "(.*?)"\]',
    re.DOTALL,
)


def _inject(text):
    if not isinstance(text, str):
        return text
    m = _MARKER_RE.search(text)
    if not m:
        return text
    prominent = f"**Voice Message Transcript:**\n\n{m.group(1)}\n\n---\n\n"
    if prominent in text:
        return text
    return prominent + text


async def handle(event_type: str, context: dict) -> None:
    if event_type == "gateway:startup":
        _patch()


def _patch() -> None:
    try:
        from gateway.run import GatewayRunner
        orig = GatewayRunner._enrich_message_with_transcription
        if getattr(orig, "_voice_prominent_wrapped", False):
            return

        async def wrapped(self, *args, **kwargs):
            result = await orig(self, *args, **kwargs)
            try:
                if isinstance(result, tuple) and result and isinstance(result[0], str):
                    return (_inject(result[0]),) + tuple(result[1:])
            except Exception as e:
                print(f"[voice-transcript] inject failed: {e}", flush=True)
            return result

        wrapped._voice_prominent_wrapped = True
        GatewayRunner._enrich_message_with_transcription = wrapped
        print("[voice-transcript] patched _enrich_message_with_transcription", flush=True)
    except Exception as e:
        print(f"[voice-transcript] FAILED to patch: {e}", flush=True)


if __name__ == "__main__":  # self-test
    sample = 'foo\n\n[The user sent a voice message~ Here\'s what they said: "hello world"]'
    out = _inject(sample)
    print(out)
    assert out.startswith("**Voice Message Transcript:**"), "prominent block must lead"
    assert "hello world" in out
    print("\n[self-test OK]")
