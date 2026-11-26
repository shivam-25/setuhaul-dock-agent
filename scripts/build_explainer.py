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
 "On a bad morning at SetuHaul, thirty-five drivers message inside half an hour. Six facilities, thirty-two dock doors, and no live G P S. Every late truck needs another slot, and no slot can be given away twice.",
 "The whole design rests on one rule. The model proposes; the database decides. Eleven steps run per message, and exactly two touch a language model: reading the driver's text, and writing the reply. Whether a slot is feasible, who gets it, and the booking itself are never a model's call.",
 "Ten predicates decide feasibility, all in code. I checked them against a separately written S Q L oracle, which reported precision of one and recall of one across seventy-two cases. It also caught nothing, because I had written the same wrong rule twice. An oracle proves agreement, not correctness.",
 "What is scarce is not throughput. At peak this is one message a minute. It is a compatible dock at a given hour. So twenty threads reach for one dock door, ten times over. Every run: one booking, nineteen drivers offered the next slot, zero errors.",
 "Five defects survived a passing test suite and died the moment I used the product. A window that was open, but unreachable. A default that locked out fourteen of fifteen drivers. Three endpoints that answered about any driver's load. A driver saying the second one works, and getting the first one booked. And a replay cache keyed on the day, answering four P M with the ten A M slots.",
 "It is deployed: Lambda scoped to Nova only, the interface on Vercel, the state in Postgres. A booking turn through the public U R L takes four point four seconds, and three point four of those are distance, because a policy forbids Lambda in the database's region. A prompt injection is refused in one millisecond, before any model runs.",
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
 ("setuhaul-e2e-01-driver",
  "Here is the product, driven against the deployed stack. A driver signs in, sees only their own trips, and says they are about sixty minutes late. That is not taken as a time. It is buffered, and the booked window on dock D four turns out to close fifty minutes before the truck can reach it, so the promise is withdrawn rather than repeated. Three reachable windows are offered, and none of them is reserved. The driver says the second one works, and the second one is what gets held."),
 ("setuhaul-e2e-02-ops-warehouse",
  "Nothing is promised yet. The hold sits in the warehouse queue, which says so in as many words: held, not confirmed. The warehouse accepts, and only now is it a booking. The coordinator can ask what any truck can actually get, and the slot the driver just took is already gone from that list. One append-only log carries the whole chain, each line numbered by the step that made it.")
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
    # The narration always runs longer than the capture, and holding the last frame for
    # eight seconds looks like the video has frozen. Stretch the footage to the length of
    # the voice instead: these are discrete screenshots roughly a second apart, so a
    # slower cadence reads better rather than worse. tpad only covers the rounding.
    total = max(va, aa) + 0.5
    stretch = max(1.0, aa / va)
    seg = S/f"{n:02d}.mp4"
    subprocess.run(["ffmpeg","-y","-v","error","-i",str(vid),"-i",str(m4a),
        "-filter_complex",
        f"[0:v]setpts={stretch:.4f}*PTS,tpad=stop_mode=clone:stop_duration=30[v];[1:a]apad[a]",
        "-map","[v]","-map","[a]","-t",f"{total:.2f}",
        "-c:v","libx264","-preset","medium","-pix_fmt","yuv420p","-r","30",
        "-c:a","aac","-b:a","160k",str(seg)],check=True)
    parts.append(seg); print(f"demo  {stem[:28]:28} video {va:5.1f}s  voice {aa:5.1f}s  x{stretch:.2f}  -> {total:5.1f}s")

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
