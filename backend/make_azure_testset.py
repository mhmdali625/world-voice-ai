"""
Multilingual recognition test set generated with Azure Speech (no Gemini quota).

  * Pure French:   native voices fr-FR-HenriNeural / fr-FR-DeniseNeural
  * Pure Lebanese: ar-LB-RamiNeural / ar-LB-LaylaNeural (Lebanese words such
                   as قدامك, كباية, هلّق, شوي, منيحة, عاليمين)
  * Code-switching: ONE multilingual speaker switching language inside the
                   sentence with SSML <lang>, as a real person would.

Output: test_audio/azure_asr/<name>.wav + expected.tsv
(name <TAB> languages <TAB> expected text <TAB> key words that must be kept)

Usage: .venv\\Scripts\\python make_azure_testset.py
Uses ~16 Azure requests (free F0 tier).
"""

import asyncio
import wave
from pathlib import Path
from xml.sax.saxutils import escape

import httpx

import config

OUT = Path(__file__).resolve().parent / "test_audio" / "azure_asr"
URL = f"https://{config.AZURE_SPEECH_REGION}.tts.speech.microsoft.com/cognitiveservices/v1"
MULTI = "en-US-AndrewMultilingualNeural"
MULTI_F = "en-US-AvaMultilingualNeural"


def single(voice: str, locale: str, text: str) -> str:
    return (f"<speak version='1.0' xml:lang='{locale}'><voice name='{voice}'>"
            f"{escape(text)}</voice></speak>")


def mixed(voice: str, segments: list[tuple[str, str]]) -> str:
    body = "".join(f"<lang xml:lang='{loc}'>{escape(txt)}</lang>" for loc, txt in segments)
    return (f"<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='en-US'>"
            f"<voice name='{voice}'>{body}</voice></speak>")


AR, EN, FR = "ar-LB", "en-US", "fr-FR"

# (name, languages, ssml, expected text, key words that must survive recognition)
CASES = [
    # ---- pure French ----
    ("fr_henri", "fr", single("fr-FR-HenriNeural", FR,
        "Décris ce qu'il y a devant moi, et dis-moi s'il y a du texte à lire."),
     "Décris ce qu'il y a devant moi, et dis-moi s'il y a du texte à lire.", "devant|texte|lire"),
    ("fr_denise_short", "fr", single("fr-FR-DeniseNeural", FR, "Qu'est-ce que c'est ?"),
     "Qu'est-ce que c'est ?", "qu'est-ce|c'est"),
    ("fr_denise_read", "fr", single("fr-FR-DeniseNeural", FR,
        "Peux-tu me lire l'étiquette sur cette bouteille, s'il te plaît ?"),
     "Peux-tu me lire l'étiquette sur cette bouteille, s'il te plaît ?", "étiquette|bouteille"),

    # ---- pure Lebanese (Lebanese words, not only loanwords) ----
    ("lb_rami_words", "ar", single("ar-LB-RamiNeural", AR,
        "خبرني شو في قدامك. في كباية قهوة عاليمين، وهلّق الضو منيح شوي."),
     "خبرني شو في قدامك. في كباية قهوة عاليمين، وهلّق الضو منيح شوي.", "قدامك|كباية|عاليمين|هلق|شوي"),
    ("lb_layla_words", "ar", single("ar-LB-LaylaNeural", AR,
        "شو هيدا اللي حدّ الكباية؟ فيك تقرّب الكاميرا شوي؟"),
     "شو هيدا اللي حدّ الكباية؟ فيك تقرّب الكاميرا شوي؟", "هيدا|الكباية|تقرب|شوي"),

    # ---- Arabic + English (user's examples) ----
    ("mix_ar_en_user1", "ar+en", mixed(MULTI, [(AR, "خبرني شو في عالـ "), (EN, "table"), (AR, " حد اللابتوب.")]),
     "خبرني شو في عالـ table حد اللابتوب.", "table|خبرني"),
    ("mix_en_ar_user2", "en+ar", mixed(MULTI, [(EN, "Can you tell me "), (AR, "شو مكتوب عالباب؟")]),
     "Can you tell me شو مكتوب عالباب؟", "tell|مكتوب|الباب"),
    ("mix_ar_en_user3", "ar+en", mixed(MULTI_F, [(AR, "شوف إذا الـ "), (EN, "charger"), (AR, " موصول بالـ "), (EN, "laptop.")]),
     "شوف إذا الـ charger موصول بالـ laptop.", "charger|laptop|موصول"),

    # ---- Arabic + French ----
    ("mix_ar_fr_user4", "ar+fr", mixed(MULTI, [(AR, "فيك تقرالي شو مكتوب عالـ "), (EN, "document "), (FR, "en français ?")]),
     "فيك تقرالي شو مكتوب عالـ document en français?", "document|français|مكتوب"),
    ("mix_ar_fr", "ar+fr", mixed(MULTI_F, [(AR, "شو في قدامي؟ "), (FR, "Est-ce qu'il y a quelque chose d'écrit ?")]),
     "شو في قدامي؟ Est-ce qu'il y a quelque chose d'écrit ?", "قدامي|écrit"),
    ("mix_fr_ar", "fr+ar", mixed(MULTI, [(FR, "Dis-moi, "), (AR, "شو لون الكباية؟")]),
     "Dis-moi, شو لون الكباية؟", "dis-moi|الكباية"),

    # ---- English + French ----
    ("mix_en_fr", "en+fr", mixed(MULTI, [(EN, "Can you read this label, "), (FR, "s'il vous plaît, "), (EN, "and tell me the price?")]),
     "Can you read this label, s'il vous plaît, and tell me the price?", "label|plaît|price"),
    ("mix_fr_en", "fr+en", mixed(MULTI_F, [(FR, "Est-ce que le "), (EN, "charger "), (FR, "est branché ?")]),
     "Est-ce que le charger est branché ?", "charger|branché"),

    # ---- all three ----
    ("mix_tri_1", "fr+en+ar", mixed(MULTI, [(FR, "Bonjour, "), (EN, "can you tell me "), (AR, "شو في عالطاولة؟")]),
     "Bonjour, can you tell me شو في عالطاولة؟", "bonjour|tell|الطاولة"),
    ("mix_tri_2", "ar+en+fr", mixed(MULTI_F, [(AR, "خبرني إذا الـ "), (EN, "screen "), (AR, "مكتوب عليها شي "), (FR, "en français.")]),
     "خبرني إذا الـ screen مكتوب عليها شي en français.", "screen|français|مكتوب"),
]


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    headers = {
        "Ocp-Apim-Subscription-Key": config.AZURE_SPEECH_KEY,
        "Content-Type": "application/ssml+xml",
        "X-Microsoft-OutputFormat": "raw-16khz-16bit-mono-pcm",
        "User-Agent": "WorldVoiceAI",
    }
    rows = []
    async with httpx.AsyncClient(timeout=30) as client:
        for name, languages, ssml, text, keys in CASES:
            r = await client.post(URL, headers=headers, content=ssml.encode("utf-8"))
            if r.status_code != 200:
                print(f"FAILED {name}: HTTP {r.status_code} {r.text[:120]}")
                continue
            with wave.open(str(OUT / f"{name}.wav"), "wb") as f:
                f.setnchannels(1)
                f.setsampwidth(2)
                f.setframerate(16000)
                f.writeframes(r.content)
            rows.append("\t".join((name, languages, text, keys)))
            print(f"wrote {name}.wav ({len(r.content) / 32000:.1f} s) [{languages}]")
            await asyncio.sleep(3.2)      # Azure F0: 20 requests per minute
    (OUT / "expected.tsv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    print(f"\n{len(rows)}/{len(CASES)} recordings in {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
