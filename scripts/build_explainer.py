"""Build docs/demo/setuhaul-explainer.mp4 - six narrated slides, then the product.

The previous explainer was assembled by hand and the commands lived in a shell history,
which is why this is a script. Run the cards first:

    python3 scripts/build_explainer_cards.py     # writes build/explainer/cards/*.png
    python3 scripts/build_explainer.py           # narrates, appends the demos, concatenates

Narration is macOS `say`; the demo footage is docs/media/*.gif, converted here rather than
committed twice. Everything intermediate lands in build/explainer/, which is disposable.

Total must stay under three minutes - the script prints the figure and says which side of
the line it is on.
"""
import subprocess, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORK = ROOT / "build" / "explainer"
CARDS = WORK / "cards"
WORK.mkdir(parents=True, exist_ok=True)
A = WORK / "audio"; A.mkdir(exist_ok=True)
S = WORK / "segments"; S.mkdir(exist_ok=True)
if not CARDS.is_dir():
    sys.exit("no cards - run scripts/build_explainer_cards.py first")
VOICE, RATE = "Rishi", 172

SLIDES = [
 "On a bad morning at SetuHaul, thirty-five drivers message inside half an hour. Six facilities, thirty-two dock doors, and not one live G P S feed. Every late truck needs another slot, and no slot can be given away twice.",
 "The whole design rests on one rule. The model proposes; the database decides. Eleven steps run for every message, and exactly two of them touch a language model: reading the driver's text, and writing the reply. Whether a slot is feasible, which driver gets it, and the booking itself, are never a model's call.",
 "Ten predicates decide feasibility, all of them in code. I checked them against a S Q L oracle written separately, and it reported precision of one and recall of one across seventy-two cases. It also caught nothing, because I had written the same wrong rule twice. An oracle proves agreement, not correctness.",
 "What is scarce here is not throughput. At peak this is one message a minute. It is a compatible dock at a given hour. So twenty threads reach for one dock door, ten times over. Every single run: one booking, nineteen drivers told immediately and offered the next slot, zero errors.",
 "Five defects survived a passing test suite and died the moment I used the product. A window that was open, but that the truck could not reach. A default that locked out fourteen of fifteen drivers. Three endpoints that would answer about any driver's load. A driver saying the second one works, and getting the first one booked. And a replay cache keyed on the day, answering a four P M question with the ten A M slots.",
 "It is deployed. Lambda behind a role scoped to Nova only, the interface on Vercel, the state in Postgres. A booking turn through the public U R L takes four point four seconds, and three point four of those are distance: an organisation policy forbids Lambda in the database's own region. A prompt injection is refused in one millisecond, before any model runs.",
]

def demo_clip(stem):
    """GIF -> 1280x720 h264, letterboxed on the deck's ink rather than stretched."""
    out = WORK / f"{stem}.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(ROOT / "docs/media" / f"{stem}.gif"),
        "-vf", "scale=1280:720:force_original_aspect_ratio=decrease:flags=lanczos,"
               "pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=0x111318,fps=30",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)], check=True)
    return out

DEMOS = [
 ("setuhaul-01-driver-journey",
  "Here is the product. A driver signs in, sees only their own trips, and says they are about sixty minutes late. That is not taken as a time. It is buffered, and the booked window on dock D four turns out to close fifty minutes before the truck can reach it, so the promise is withdrawn rather than repeated. Three reachable windows are offered instead. The driver says the second one works, and the second one is what gets held."),
 ("setuhaul-02-operations",
  "And the same morning from the dock office. Every truck today, grouped one card to a truck, worst first. The coordinator can ask what any truck can actually get, and see the slots that qualify alongside the rule that refused the rest. Underneath it, the append-only audit log: every decision, numbered by the step that made it."),
]

def dur(p):
    return float(subprocess.run(["ffprobe","-v","error","-show_entries","format=duration",
        "-of","default=nw=1:nk=1",str(p)],capture_output=True,text=True).stdout.strip())

def tts(i, line):
    aiff, m4a = A/f"{i:02d}.aiff", A/f"{i:02d}.m4a"
    subprocess.run(["say","-v",VOICE,"-r",str(RATE),"-o",str(aiff),line],check=True)
    subprocess.run(["ffmpeg","-y","-v","error","-i",str(aiff),"-c:a","aac","-b:a","160k",str(m4a)],check=True)
    return m4a

parts, n = [], 0
for i, line in enumerate(SLIDES, 1):
    n += 1; m4a = tts(n, line); d = dur(m4a) + 0.7
    seg = S/f"{n:02d}.mp4"
    subprocess.run(["ffmpeg","-y","-v","error","-loop","1","-i",str(CARDS / f"{i:02d}.png"),"-i",str(m4a),
        "-c:v","libx264","-preset","medium","-tune","stillimage","-pix_fmt","yuv420p","-r","30",
        "-t",f"{d:.2f}","-af","apad=pad_dur=0.7","-c:a","aac","-b:a","160k",str(seg)],check=True)
    parts.append(seg); print(f"slide {i}: {d:5.1f}s")

for stem, line in DEMOS:
    n += 1; m4a = tts(n, line)
    vid = demo_clip(stem)
    va, aa = dur(vid), dur(m4a)
    # The footage plays end to end, and the voice is padded with silence to match it. If
    # the voice runs long, the last frame is held rather than the demo being cut short.
    total = max(va, aa) + 0.5
    seg = S/f"{n:02d}.mp4"
    subprocess.run(["ffmpeg","-y","-v","error","-i",str(vid),"-i",str(m4a),
        "-filter_complex",f"[0:v]tpad=stop_mode=clone:stop_duration=30[v];[1:a]apad[a]",
        "-map","[v]","-map","[a]","-t",f"{total:.2f}",
        "-c:v","libx264","-preset","medium","-pix_fmt","yuv420p","-r","30",
        "-c:a","aac","-b:a","160k",str(seg)],check=True)
    parts.append(seg); print(f"demo  {stem[:28]:28} video {va:5.1f}s  voice {aa:5.1f}s  -> {total:5.1f}s")

with open(WORK / "concat.txt", "w") as fh:
    for seg in parts:
        fh.write(f"file '{seg}'\n")

OUT = ROOT / "docs" / "demo" / "setuhaul-explainer.mp4"
subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
    "-i", str(WORK / "concat.txt"),
    "-c:v", "libx264", "-preset", "medium", "-pix_fmt", "yuv420p", "-r", "30",
    "-movflags", "+faststart", "-c:a", "aac", "-b:a", "160k", str(OUT)], check=True)
t = dur(OUT)
print(f"\n{OUT.relative_to(ROOT)}")
print(f"TOTAL {int(t//60)}m{t%60:04.1f}s   {'UNDER 3:00' if t < 180 else 'OVER 3:00 - trim a line'}")
sys.exit(0 if t < 180 else 1)
