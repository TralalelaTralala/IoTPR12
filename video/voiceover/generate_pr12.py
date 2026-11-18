"""Озвучка тексту ПР12 клонованим голосом (OmniVoice).

python generate.py                       # усе: еталон + всі фрази + повна доріжка
python generate.py --only 3 7            # перегенерувати лише фрази №3 і №7
python generate.py --ref-start 12.5      # взяти еталон з 12.5 с запису (за замовчуванням — авто)
"""
import argparse
import json
import re
import shutil
from pathlib import Path

import av
import numpy as np
import soundfile as sf
import torch

SR = 24000
ROOT = Path(r"C:/Users/tplin/Documents/Codex/2026-10-23/new-chat/IOTPR12/video/voiceover")
TEXT = ROOT / "tekst_tts.md"
TOTAL = 177  # довжина відео IOTPR12-demo, с
GAP = 0.25  # мінімальна пауза перед наступною фразою, с
MAX_SPEED = 1.25  # максимальне прискорення фрази, щоб влізти в таймкод


def load_audio(path, sr=SR):
    with av.open(str(path)) as f:
        res = av.AudioResampler(format="flt", layout="mono", rate=sr)
        chunks = [r.to_ndarray().ravel() for fr in f.decode(audio=0) for r in res.resample(fr)]
    return np.concatenate(chunks)


def pick_reference(wav, length=9.0):
    """Найгучніше суцільне мовлення: вікно з найбільшою часткою «голосних» кадрів."""
    hop = int(0.05 * SR)
    rms = np.sqrt(np.mean(wav[: len(wav) // hop * hop].reshape(-1, hop) ** 2, axis=1))
    voiced = rms > max(np.percentile(rms, 30) * 3, 1e-3)
    n = int(length / 0.05)
    if len(voiced) <= n:
        return 0.0
    score = np.convolve(voiced.astype(float), np.ones(n), "valid")
    # початок і кінець вікна мають припадати на тишу, щоб не різати слово
    edge = ~voiced[: len(score)] & ~voiced[n - 1 : n - 1 + len(score)]
    score = score + edge * n * 0.1
    return float(np.argmax(score) * 0.05)


def parse_text():
    segs = []
    for line in TEXT.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\*\*\[(\d+):(\d+)\]\*\*\s*(.+)$", line.strip())
        if m:
            segs.append((int(m[1]) * 60 + int(m[2]), m[3].strip()))
    return segs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", type=int, nargs="*", help="номери фраз (з 1)")
    ap.add_argument("--ref-start", type=float, help="початок еталону в записі, с")
    ap.add_argument("--ref-len", type=float, default=9.0)
    ap.add_argument("--recording", type=Path, default=ROOT / "recording.wav", help="шлях до вихідного запису для створення еталону")
    ap.add_argument("--steps", type=int, default=32)
    args = ap.parse_args()

    segdir = ROOT / "segments"
    segdir.mkdir(parents=True, exist_ok=True)

    ref_path = ROOT / "reference.wav"
    if args.ref_start is not None or not ref_path.exists():
        rec = load_audio(args.recording)
        start = args.ref_start if args.ref_start is not None else pick_reference(rec, args.ref_len)
        clip = rec[int(start * SR) : int((start + args.ref_len) * SR)]
        clip = clip / (np.abs(clip).max() + 1e-9) * 0.9
        sf.write(ref_path, clip, SR)
        print(f"Еталон: {start:.2f}–{start + args.ref_len:.2f} с запису -> {ref_path}")

    from omnivoice import OmniVoice

    model = OmniVoice.from_pretrained("k2-fsa/OmniVoice", device_map="cuda:0", dtype=torch.float16)

    # текст еталону розпізнає Whisper; можна виправити вручну в reference.txt
    ref_txt = ROOT / "reference.txt"
    if args.ref_start is not None or not ref_txt.exists():
        model.load_asr_model()
        ref_text = None
    else:
        ref_text = ref_txt.read_text(encoding="utf-8").strip()
    ref_wav, _ = sf.read(ref_path, dtype="float32")
    prompt = model.create_voice_clone_prompt((torch.from_numpy(ref_wav)[None], SR), ref_text=ref_text)
    ref_txt.write_text(prompt.ref_text, encoding="utf-8")
    print(f"Текст еталону: {prompt.ref_text}")

    segs = parse_text()
    todo = set(args.only) if args.only else set(range(1, len(segs) + 1))
    name = lambda i, t: segdir / f"{i:02d}_{t // 60}m{t % 60:02d}s.wav"
    kw = lambda text: dict(text=text, voice_clone_prompt=prompt, language="uk", num_step=args.steps)

    # 1) природний темп для кожної фрази (кеш, щоб розклад враховував і незмінені фрази)
    cache_path = segdir / "natural.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    natural = {}
    for i, (t, text) in enumerate(segs, 1):
        nat = segdir / f"nat_{i:02d}.wav"
        if i in todo or text not in cache or not nat.exists():
            audio = model.generate(**kw(text))[0]
            sf.write(nat, audio, SR)
            cache[text] = len(audio) / SR
        natural[i] = cache[text]
    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")

    # 2) розклад: фраза стартує на таймкоді або одразу після попередньої;
    #    якщо не влазить до наступного таймкоду — прискорюємо, але не більше MAX_SPEED
    plan, prev_end = [], -GAP
    for i, (t, _) in enumerate(segs, 1):
        start = max(t, prev_end + GAP)
        room = (segs[i][0] if i < len(segs) else TOTAL) - start - GAP
        dur = natural[i] if natural[i] <= room else max(room, natural[i] / MAX_SPEED)
        plan.append((start, dur))
        prev_end = start + dur

    # 3) фінальні файли: природний темп або перегенерація під потрібну тривалість
    for i, ((t, text), (start, dur)) in enumerate(zip(segs, plan), 1):
        out = name(i, t)
        if i in todo or not out.exists():
            if dur < natural[i] - 0.05:
                audio = model.generate(**kw(text), duration=dur, postprocess_output=False)[0]
                sf.write(out, audio, SR)
            else:
                shutil.copy(segdir / f"nat_{i:02d}.wav", out)
        shift = f"  зсув +{start - t:.1f} с" if start - t > 0.05 else ""
        print(f"[{i:02d}/{len(segs)}] {t // 60}:{t % 60:02d}  {dur:5.1f} с (х{natural[i] / dur:.2f}){shift}")

    # повна доріжка під відео: кожна фраза стоїть на своєму місці за розкладом
    full = np.zeros(TOTAL * SR, dtype=np.float32)
    for i, ((t, _), (start, _)) in enumerate(zip(segs, plan), 1):
        a, _ = sf.read(name(i, t), dtype="float32")
        s = int(start * SR)
        a = a[: max(0, len(full) - s)]
        full[s : s + len(a)] += a
    sf.write(ROOT / "voiceover_full.wav", full, SR)
    print(f"Готово: {ROOT / 'voiceover_full.wav'}")


if __name__ == "__main__":
    main()
