"""Накладає voiceover/voiceover_full.wav на out/IOTPR12-demo.webm -> IOTPR12.mp4 (H.264 + AAC).

Запуск (venv з PyAV):  C:\\Users\\tplin\\voice-clone\\venv\\Scripts\\python.exe video\\mux_pr12.py
"""
import sys
from pathlib import Path

sys.path.insert(0, r"C:\Users\tplin\voice-clone")
import mux  # noqa: E402  (спільні функції кодування/змішування з попередніх робіт)

DIR = Path(__file__).resolve().parent
mux.DIR = DIR
mux.VIDEO = DIR / "out" / "IOTPR12-demo.webm"
mux.VOICE = DIR / "voiceover" / "voiceover_full.wav"

_orig_open = mux.av.open


def _open(path, *a, **kw):
    # mux.make_mp4 пише у DIR/IoTModul-demo_voiceover.mp4 — перенаправляємо на IOTPR12.mp4
    if str(path).endswith("IoTModul-demo_voiceover.mp4"):
        path = DIR / "IOTPR12.mp4"
    return _orig_open(str(path), *a, **kw)


mux.av.open = _open

if __name__ == "__main__":
    mux.make_mp4(mux.load_voice())
    print("mp4:", DIR / "IOTPR12.mp4")
