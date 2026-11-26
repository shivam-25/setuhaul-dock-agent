"""Build docs/demo/setuhaul-explainer.mp4 - six narrated slides, then the product.

The previous explainer was assembled by hand and the commands lived in a shell history,
which is why this is a script. Run the cards first:

    python3 scripts/build_explainer_cards.py     # writes build/explainer/cards/*.png
    python3 scripts/build_explainer.py           # narrates, appends the demos, concatenates

Narration is edge-tts, a neural voice. macOS `say` was used first and sounded like a
railway announcement; the compact voices are the only ones installed by default and the
good ones are a separate download. edge-tts needs no key and no account, at the cost of
sending the narration text to Microsoft to synthesise - which is fine for text that is
already a public description of the project, and would not be for anything else.

The demo footage is docs/media/*.gif, converted here rather than committed twice.
Everything intermediate lands in build/explainer/, which is disposable.
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
VOICE, RATE = "en-IN-PrabhatNeural", "+22%"  # Indian English; the default pace runs ~30% slower than `say`
MAX_LEN = 330          # five minutes was the target; the deck now has to explain the
                       # architecture, the data flow and where the model sits, and the
                       # last six seconds were not worth taking out of any of them
MAX_STRETCH = 1.8      # past this the footage crawls; hold the last frame instead

SLIDES = [
 "On a bad morning at SetuHaul, thirty-five drivers message inside half an hour. Six facilities, thirty-two dock doors, and not one live G P S feed, so every arrival time is a claim rather than a measurement. One late truck breaks a chain of promises at once. But the hard part is not the single driver. It is that two conversations read the same free slot, both drivers say yes, and only one promise can be true.",
 "So we built a dock exception service that drivers message in plain language. It reads the message, works out what the truck can actually reach, holds one slot, and asks a human to accept it. Four rules hold it up. Capacity is arithmetic, not judgement. Nothing is promised until a warehouse accepts. Two partial unique indexes make a double booking impossible at the database rather than merely unlikely in the code. And every decision is written to an append-only log.",
 "The architecture is three roles over one engine and one database. Driver, coordinator and warehouse each get their own console, served by Next dot j s on Vercel, which also proxies the A P I so the session cookie stays same-origin. Behind it, one Lambda runs FastAPI and the eleven step engine, and Postgres holds the state, the indexes and the audit log. Bedrock sits off to the side, reached by only two of the eleven steps. The engine never trusts the browser for identity.",
 "Here is what happens to a message. It loads the trip, screens for prompt injection, and catches a retry before any model runs. Step four is the first one that does, turning the sentence into fields. Five resolves which trip was meant; six adds a confidence buffer to the declared arrival. Seven routes it: a booking, a status question, or a question back. Eight ranks the feasible slots. Nine inserts the hold, and that is where the database, not the code, blocks a double booking. Ten re-checks the answer against the decision before eleven writes the reply.",
 "Which leaves where the model is actually used. Capacity needs arithmetic, not a language model, so it earns its place only at the two edges where work starts and ends in human sentences. Going in, at step four, Nova micro turns around two hours late, anything after seven, into structured fields. It gets no slot data and no booking power, so it cannot schedule anything. Coming out, at step eleven, it phrases a decision the code already made. The times are passed in, it may not invent one, and a mismatch goes to a person. Many replies use no model at all.",
]

def demo_clip(stem, skip=0.0):
    """GIF -> 1280x720 h264, letterboxed on the deck's ink rather than stretched.

    `skip` drops the opening seconds. The coordinator capture also contains an Accept,
    which the warehouse act now covers properly, and showing the same booking accepted
    twice reads as a mistake rather than as two roles.
    """
    out = WORK / f"{stem}.mp4"
    pre = ["-ss", f"{skip}"] if skip else []
    subprocess.run(["ffmpeg", "-y", "-v", "error", *pre,
        "-i", str(ROOT / "docs/media" / f"{stem}.gif"),
        "-vf", "scale=1280:720:force_original_aspect_ratio=decrease:flags=lanczos,"
               "pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=0x111318,fps=30",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)], check=True)
    return out

DEMOS = [
 ("setuhaul-e2e-01-driver", 0, 
  "Here is the product, driven against the deployed stack. A driver signs in, sees only their own trips, and says they are about sixty minutes late. That is not taken as a time. It is buffered, and the booked window on dock D four turns out to close fifty minutes before the truck can reach it, so the promise is withdrawn rather than repeated. Three reachable windows are offered, and none of them is reserved. The driver says the second one works, and the second one is what gets held."),
 ("setuhaul-e2e-04-queries-failures", 0,
  "A second driver, and the answers it refuses to give. Asked whether the warehouse has accepted, it answers the status rather than offering a fresh list. Asked to book the earliest dock, it books, because an explicit request is the only thing that takes a hold. Asked for a slot after seven that also finishes by half one, it does not report a shortage of docks: a window that shuts before it opens is a misreading of the driver, so it asks which limit is real. And told to ignore its instructions and hand over a dock, it refuses at the gate and passes the conversation to a person. Those last two cost one and a half seconds and half a second, because neither of them reaches the expensive path."),
 ("setuhaul-e2e-03-warehouse", 0,
  "Nothing is promised yet. The hold waits in the warehouse queue, which says so plainly: held, not confirmed. This is the Jaipur warehouse, signed in as herself, seeing six docks where the coordinator saw nine, because her view is scoped to her facility. She accepts, and only now is it a booking."),
 ("setuhaul-e2e-02-ops-warehouse", 6.4,
  "And the fleet, from the coordinator's desk. One card per truck, worst first. Asked what a truck can get, it reports seventy-nine chains checked, and the window the first driver took is already gone from the list, because both views read one database."),
 ("setuhaul-e2e-05-driver-confirmed", 0,
  "And back to the driver who started it. His dashboard no longer warns him: the one o'clock window works, because he is ready at eleven fifty. He asks what the second driver asked and gets a different answer. Confirmed, not waiting. He arrived with a promise that could not be kept, and leaves with a dock he can reach.")
]

def dur(p):
    return float(subprocess.run(["ffprobe","-v","error","-show_entries","format=duration",
        "-of","default=nw=1:nk=1",str(p)],capture_output=True,text=True).stdout.strip())

def tts(i, line):
    aiff, m4a = A/f"{i:02d}.mp3", A/f"{i:02d}.m4a"
    subprocess.run([sys.executable,"-m","edge_tts","--voice",VOICE,"--rate",RATE,
                    "--text",line,"--write-media",str(aiff)],check=True)
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

for stem, skip, line in DEMOS:
    n += 1; m4a = tts(n, line)
    vid = demo_clip(stem, skip)
    va, aa = dur(vid), dur(m4a)
    # The narration always runs longer than the capture, and holding the last frame for
    # eight seconds looks like the video has frozen. Stretch the footage to the length of
    # the voice instead: these are discrete screenshots roughly a second apart, so a
    # slower cadence reads better rather than worse. tpad only covers the rounding.
    total = max(va, aa) + 0.5
    stretch = min(MAX_STRETCH, max(1.0, aa / va))
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
print(f"TOTAL {int(t//60)}m{t%60:04.1f}s   "
      f"{'under ' if t < MAX_LEN else 'OVER '}{MAX_LEN//60}:{MAX_LEN%60:02d}"
      f"{'' if t < MAX_LEN else ' - trim a line'}")
sys.exit(0 if t < MAX_LEN else 1)
