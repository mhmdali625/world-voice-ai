"""
Simulates the Android client against a running backend in
offline voice mode (dev_offline_tts.ps1). No API quota.

Does exactly what mobile/Services/speechPlayer.ts does:
  1. POST /tts/jobs
  2. poll GET /tts/jobs/{id} every 350 ms
  3. open the audio stream only when ready_to_start is true
  4. play it with a model of ExoPlayer buffering
     (start at 1.0 s buffered, 2.0 s after a stall (media3 defaults))
then repeats with slow generation, then a replay.

Usage: .venv\\Scripts\\python test_phone_flow.py [base_url]
"""

import sys
import time

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
BPS = 48000


def play(client: httpx.Client, job: dict, label: str) -> None:
    t0 = time.perf_counter()
    polls = 0
    while True:
        status = client.get(BASE + job["status_path"]).json()
        polls += 1
        if status["error"]:
            print(f"[{label}] job failed: {status['error']}")
            return
        if status["ready_to_start"] or status["done"]:
            break
        time.sleep(0.35)
    opened = time.perf_counter() - t0

    arrivals, total = [], 0
    with client.stream("GET", BASE + job["audio_path"]) as response:
        response.raise_for_status()
        for chunk in response.iter_bytes():
            total += len(chunk)
            arrivals.append((time.perf_counter() - t0, max(0, total - 44)))

    audio = arrivals[-1][1] / BPS

    def buffered(t: float) -> float:
        got = 0
        for at, size in arrivals:
            if at <= t:
                got = size
        return got / BPS

    t, played, playing, first, stalls, stalled = opened, 0.0, False, None, 0, 0.0
    threshold, step = 1.0, 0.01
    while played < audio - 1e-6 and t < 400:
        ahead = buffered(t) - played
        if not playing:
            if ahead >= min(threshold, audio - played) - 1e-9:
                playing = True
                first = first if first is not None else t
            elif first is not None:
                stalled += step
        elif ahead <= 1e-9:
            playing, stalls, threshold = False, stalls + 1, 2.0
        else:
            played += min(step, ahead)
        t += step

    final = client.get(BASE + job["status_path"]).json()
    print(
        f"[{label}] status polls before start: {polls} | player opened {opened:.2f} s | "
        f"first sound {first:.2f} s | interruptions {stalls} ({stalled:.2f} s) | "
        f"finished {t:.2f} s | audio {audio:.2f} s | "
        f"rate x{final['generation_rate']} | server stream start {final['stream_started_s']} s"
    )


def main() -> None:
    with httpx.Client(timeout=200) as client:
        settings = client.get(BASE + "/dev/tts-test")
        if settings.status_code != 200:
            sys.exit("Backend is not in offline test mode (run dev_offline_tts.ps1).")
        print("test settings:", settings.json())

        # Four sentences (> 120 chars) so WV_TTS_SPLIT can take effect.
        body = {
            "text": (
                "في قدامك لابتوب مفتوح عالطاولة، والشاشة شغّالة وعليها صفحة كتابة. "
                "حدّ اللابتوب عاليمين في كباية قهوة وتلفون أسود. "
                "ورا الطاولة في شبّاك والضو جايي منّو، فالصورة واضحة منيحة. "
                "ما في شي مكتوب واضح بقدر إقراه من هالبعد."
            ),
            "language": "ar-LB",
        }

        client.post(BASE + "/dev/tts-test", params={"rate": 1.5})
        normal = client.post(BASE + "/tts/jobs", json=body).json()
        play(client, normal, "normal x1.5")

        client.post(BASE + "/dev/tts-test", params={"rate": 0.63})
        slow = client.post(BASE + "/tts/jobs", json=body).json()
        play(client, slow, "slow x0.63")

        play(client, slow, "replay (cached)")

        print("quota endpoint:", client.get(BASE + "/tts/quota").json())


if __name__ == "__main__":
    main()
