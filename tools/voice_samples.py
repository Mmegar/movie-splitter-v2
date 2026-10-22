"""
Voice samples for the app's Settings > Narrator voice (docs/voices/<voice>.mp3, and <voice>-funny.mp3 for
Chatterbox's funny, intense delivery in that voice). Runs on GitHub (voice-samples workflow), which can reach
Microsoft's and Hugging Face's voice services. Samples are at 1x: the Narrator speed is added when parts are made.
"""
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("NARRATOR_TEMPO", "1")
import process  # noqa: E402  (binds story to process's helpers)
import story  # noqa: E402

LINE = ("Okay, so you're the new detective in a town where everybody lies. Great start. "
        "And the one witness who saw everything? He just bought a one-way ticket to anywhere. Totally normal.")


def voices():
    js = (HERE / "docs" / "js" / "views" / "settings.js").read_text(encoding="utf-8")
    block = js[js.index("export const VOICES"):]
    return re.findall(r"\['(en-[A-Z]{2}-\w+Neural)'", block[:block.index("];")])


def main():
    out = HERE / "docs" / "voices"
    out.mkdir(parents=True, exist_ok=True)
    story.TEMPO = 1.0
    made, failed = 0, []
    for v in voices():
        story.VOICE = v
        plain = out / f"{v}.mp3"
        if not plain.exists():
            try:
                story.speak_edge(LINE, plain)
                made += 1
            except Exception as e:
                failed.append(f"{v}: {e}")
                continue
        funny = out / f"{v}-funny.mp3"
        if not funny.exists():
            story._CHATTER["ref"] = None          # copy this voice, not the previous one
            try:
                story.speak_chatterbox(LINE, funny)
                made += 1
            except Exception as e:
                funny.unlink(missing_ok=True)
                failed.append(f"{v} (funny): {e}")
    print(f"Made {made} samples.")
    for f in failed:
        print("  failed:", f)


if __name__ == "__main__":
    main()
