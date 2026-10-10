"""Drive a full interview from the terminal, acting exactly like the browser does over REST + WebSocket.

    python scripts/fake_frontend.py                      # auto-answers with canned transcripts
    python scripts/fake_frontend.py --type               # you type each answer
    python scripts/fake_frontend.py --interview "Quick Demo" --answer-seconds 15
    python scripts/fake_frontend.py --end-after 2        # sends end_session after 2 answers

Pair it with scripts/mock_vitals.py (Data) to see stress, coach flags, and adaptations. Useful for
Brain, Data, and Sensing to test the backend without the React app.
"""

import argparse
import asyncio
import json
import sys

import httpx
from websockets.asyncio.client import connect

CANNED = [
    "It's going really well. We've been building an interview coach that reads your heart rate from the webcam.",
    "I built a FastAPI backend for a hackathon project. The part I'm proudest of is the real-time pipeline: "
    "readings come in every second, get scored against a personal baseline, and stream to a live chart over "
    "WebSockets. I learned a lot about keeping latency low.",
    "Um, I'm not sure.",
    "First I'd check whether the slowdown is everywhere or one endpoint, then look at recent deploys, database "
    "query plans, and connection pool saturation. I'd add timing around each layer to find where the time goes.",
    "I'd shard users across chat servers, keep connections on WebSockets, put messages in a durable queue, and "
    "store history in a partitioned database so reads stay fast.",
    "My biggest failure was missing a launch deadline because I didn't flag a risk early. Now I raise blockers the day I see them.",
]


def fmt(event: dict) -> str:
    t, d = event["type"], event.get("data", {})
    if t == "vitals":
        return f"   vitals  HR {d.get('heart_rate')}  stress {d.get('stress')}"
    if t == "question":
        return f"\n>> Q {d['index']}/{d['total']} [{d['kind']}] {d['text']}\n   audio: {d.get('audio_url')}"
    if t == "coach_flag":
        return f"   !! COACH ({d['level']}): {d['message']}"
    return f"   {t}: {json.dumps(d)}"


async def run(args) -> None:
    base = args.api.rstrip("/")
    ws_base = base.replace("http", "ws", 1)
    async with httpx.AsyncClient(base_url=base, timeout=30) as http:
        r = await http.post("/api/sessions", json={
            "user_name": args.name, "interview_type": args.interview, "role_title": args.role})
        r.raise_for_status()
        sid = r.json()["session_id"]
        print(f"session {sid}")

        async with connect(f"{ws_base}/ws/{sid}") as sock:
            (await http.post(f"/api/sessions/{sid}/start")).raise_for_status()
            answers = 0

            async for raw in sock:
                event = json.loads(raw)
                if event["type"] != "vitals" or args.show_vitals:
                    print(fmt(event))

                if event["type"] == "question":
                    d = event["data"]
                    await asyncio.sleep(args.speak_seconds)   # "audio playing"
                    await sock.send(json.dumps({"type": "answer_started", "data": {"question_id": d["question_id"]}}))
                    if args.type:
                        text = await asyncio.to_thread(input, "   your answer> ")
                    else:
                        await asyncio.sleep(args.answer_seconds)
                        text = CANNED[answers % len(CANNED)]
                        print(f"   (answer) {text}")
                    await sock.send(json.dumps({"type": "answer_done",
                                                "data": {"question_id": d["question_id"], "transcript": text}}))
                    answers += 1
                    if args.end_after and answers >= args.end_after:
                        print("   (sending end_session)")
                        await sock.send(json.dumps({"type": "end_session", "data": {}}))

                elif event["type"] == "session_ended":
                    report = (await http.get(f"/api/sessions/{sid}/report")).json()
                    print("\n===== REPORT =====")
                    print(json.dumps(report, indent=2))
                    return


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--name", default="Test User")
    ap.add_argument("--interview", default="Quick Demo")
    ap.add_argument("--role", default="Software Engineer")
    ap.add_argument("--type", action="store_true", help="type answers instead of canned ones")
    ap.add_argument("--speak-seconds", type=float, default=2.0, help="pretend interviewer audio length")
    ap.add_argument("--answer-seconds", type=float, default=8.0, help="how long each canned answer takes")
    ap.add_argument("--end-after", type=int, default=0, help="send end_session after N answers")
    ap.add_argument("--show-vitals", action="store_true")
    try:
        asyncio.run(run(ap.parse_args()))
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
