"""
Build a multilingual speech-recognition test set.

  * Offline Windows voices (David, Zira) for English: no API.
  * ONE Gemini TTS request (Fola) for natural Lebanese, French,
    English and mixed-language phrases, split at the pauses.

Output: backend/test_audio/asr/<name>.wav + expected.tsv
(name <TAB> expected language <TAB> expected transcript)

Usage: .venv\\Scripts\\python make_asr_testset.py [--no-gemini]
"""

import asyncio
import subprocess
import sys
import wave
from pathlib import Path

import generate_voice_clips as clips
import tts_service as tts

OUT = Path(__file__).resolve().parent / "test_audio" / "asr"

# (name, language, text)
SAPI_PHRASES = [
    ("en_user_david", "en", "Describe what is in front of me, identify the important objects, and explain what I can use them for."),
    ("en_user_zira", "en", "Describe what is in front of me, identify the important objects, and explain what I can use them for."),
    ("en_short_david", "en", "What is this?"),
    ("en_read_zira", "en", "Can you read the text on this paper for me?"),
]

# Every language and code-switching combination required,
# including the user's four real examples verbatim.
FOLA_PHRASES = [
    ("en_fola", "en", "Describe what is in front of me, identify the important objects, and explain what I can use them for."),
    ("fr_fola", "fr", "Décris ce qu'il y a devant moi, et dis-moi s'il y a du texte à lire."),
    ("fr_short_fola", "fr", "Qu'est-ce que c'est ?"),
    ("lb_q_fola", "ar", "شو هيدا اللي عاليمين؟ وفي شي مكتوب عالورقة؟"),
    ("mix_user1", "ar+en", "خبرني شو في عالـ table حد اللابتوب."),
    ("mix_user2", "en+ar", "Can you tell me شو مكتوب عالباب؟"),
    ("mix_user3", "ar+en", "شوف إذا الـ charger موصول بالـ laptop."),
    ("mix_user4", "ar+en+fr", "فيك تقرالي شو مكتوب عالـ document en français?"),
    ("mix_lb_fr", "ar+fr", "شو في قدامي؟ Est-ce qu'il y a quelque chose d'écrit ?"),
    ("mix_en_fr", "en+fr", "Can you read this label, s'il vous plaît, and tell me the price?"),
    ("mix_tri", "fr+en+ar", "Bonjour, can you tell me شو في عالطاولة؟"),
]

# Generated with the separate flash-tts model (its own daily
# quota), so the flash-lite quota used by the app is untouched.
FOLA_TEST_MODEL = "gemini-3.8-flash-tts"


def sapi(name: str, text: str, voice: str, rate: int = 16000) -> None:
    path = OUT / f"{name}.wav"
    script = (
        "Add-Type -AssemblyName System.Speech;"
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        f"$s.SelectVoice('Microsoft {voice} Desktop');"
        f"$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo({rate}, "
        "[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, "
        "[System.Speech.AudioFormat.AudioChannel]::Mono);"
        f"$s.SetOutputToWaveFile('{path}', $fmt);"
        f"$s.Speak(\"{text}\"); $s.Dispose()"
    )
    powershell = Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
    subprocess.run([str(powershell), "-NoProfile", "-Command", script], check=True)
    print("wrote", path.name)


async def fola() -> list[tuple[str, str, str]]:
    script = "\n\n".join(text for _, _, text in FOLA_PHRASES)
    pcm = bytearray()
    async for piece in tts._stream_pcm(script, "und", False, FOLA_TEST_MODEL):  # noqa: SLF001 - ONE request
        pcm.extend(piece)
    pcm = bytes(pcm)
    clips.write_wav(OUT / "_fola_all.wav", pcm)      # keep the raw take
    await tts.close_http_client()
    pieces = clips.split_on_pauses(pcm, len(FOLA_PHRASES))
    if pieces is None:
        clips.write_wav(OUT / "_unsplit_fola.wav", pcm)
        print("Could not split the Fola audio reliably; saved _unsplit_fola.wav")
        return []
    written = []
    for (name, language, text), piece in zip(FOLA_PHRASES, pieces):
        clips.write_wav(OUT / f"{name}.wav", piece)
        print(f"wrote {name}.wav ({len(piece) / tts.BYTES_PER_SECOND:.2f} s)")
        written.append((name, language, text))
    return written


def read_pcm(path: Path) -> tuple[bytes, int]:
    with wave.open(str(path), "rb") as file:
        return file.readframes(file.getnframes()), file.getframerate()


def offline_mixes() -> list[tuple[str, str, str]]:
    """
    Code-switching samples without any API: a real Fola
    Lebanese phrase (cut at a pause from the saved recording)
    followed by an English phrase from the offline voice.
    """
    fola_pcm, rate = read_pcm(OUT.parent / "fola_sample_ar-LB.wav")
    pieces = clips.split_on_pauses(fola_pcm, 6)
    if not pieces:
        return []
    lebanese = pieces[0]                     # "في قدامك لابتوب مفتوح عالطاولة،"
    clips.write_wav(OUT / "lb_short_fola.wav", lebanese)

    english_text = "and tell me if there is any text on the screen."
    sapi("_en_tail", english_text, "David", rate)
    english, _ = read_pcm(OUT / "_en_tail.wav")
    (OUT / "_en_tail.wav").unlink()

    gap = b"\x00\x00" * int(rate * 0.25)
    clips.write_wav(OUT / "mix_lb_en.wav", lebanese + gap + english)

    # More code-switching orders (real Fola Lebanese + English).
    beside = pieces[2]   # "حدّ اللابتوب عاليمين في كباية قهوة وتلفون أسود."
    sapi("_en_head", "Can you tell me", "Zira", rate)
    head, _ = read_pcm(OUT / "_en_head.wav")
    (OUT / "_en_head.wav").unlink()
    clips.write_wav(OUT / "mix_en_lb.wav", head + gap + beside)

    sapi("_en_mid", "and is the charger connected,", "David", rate)
    mid, _ = read_pcm(OUT / "_en_mid.wav")
    (OUT / "_en_mid.wav").unlink()
    clips.write_wav(OUT / "mix_lb_en_lb.wav", lebanese + gap + mid + gap + beside)

    print("wrote lb_short_fola, mix_lb_en, mix_en_lb, mix_lb_en_lb")
    return [
        ("lb_short_fola", "ar", "في قدامك لابتوب مفتوح عالطاولة"),
        ("mix_lb_en", "ar+en", "في قدامك لابتوب مفتوح عالطاولة، and tell me if there is any text on the screen."),
        ("mix_en_lb", "en+ar", "Can you tell me حدّ اللابتوب عاليمين في كباية قهوة وتلفون أسود"),
        ("mix_lb_en_lb", "ar+en+ar", "في قدامك لابتوب مفتوح عالطاولة، and is the charger connected, حدّ اللابتوب عاليمين في كباية قهوة وتلفون أسود"),
    ]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for name, language, text in SAPI_PHRASES:
        sapi(name, text, "Zira" if "zira" in name else "David")
        rows.append((name, language, text))

    rows += offline_mixes()
    if "--no-gemini" not in sys.argv:
        rows += asyncio.run(fola())

    # Existing natural Lebanese answer recording.
    rows.append((
        "lb_answer_fola", "ar",
        "في قدامك لابتوب مفتوح عالطاولة، والشاشة شغّالة وعليها صفحة كتابة. "
        "حدّ اللابتوب عاليمين في كباية قهوة وتلفون أسود. ورا الطاولة في شبّاك "
        "والضو جايي منّو، فالصورة واضحة منيحة. ما في شي مكتوب واضح بقدر إقراه من هالبعد.",
    ))
    source = OUT.parent / "fola_sample_ar-LB.wav"
    with wave.open(str(source), "rb") as src, wave.open(str(OUT / "lb_answer_fola.wav"), "wb") as dst:
        dst.setparams(src.getparams())
        dst.writeframes(src.readframes(src.getnframes()))

    (OUT / "expected.tsv").write_text(
        "\n".join("\t".join(row) for row in rows) + "\n", encoding="utf-8"
    )
    print(f"\n{len(rows)} test recordings in {OUT}")


if __name__ == "__main__":
    main()
