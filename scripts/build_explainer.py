"""Build docs/demo/setuhaul-explainer.mp4 - five narrated slides, then the product.

Run the cards first:

    python3 scripts/build_explainer_cards.py     # writes build/explainer/cards/*.png
    python3 scripts/build_explainer.py           # narrates, appends the demos, concatenates

Narration is Gemini TTS, voice "Charon". edge-tts came first and was intelligible but
flat; Charon takes a style instruction, which is what actually buys the human cadence.
It needs GEMINI_API_KEY and sends the narration text to Google to synthesise - fine for
text that is already a public description of the project, and not for anything else.

Three things keep it from sounding like a machine reading a list:

  * every line is loudness-normalised to -16 LUFS with 1.5 dB of true-peak headroom, so
    no segment is louder than its neighbour and nothing clips. The previous cut measured
    -17.1 LUFS at -1.8 dBTP, which is both quieter and closer to the ceiling - strained.
  * each segment carries a tail of silence and fades in and out across it, so the cut
    lands in a pause rather than mid-breath.
  * the copy is written to be spoken: short sentences, and a connective at the top of
    each segment so the acts read as one take rather than ten files.

The demo footage is docs/media/*.gif, converted here rather than committed twice.
Everything intermediate lands in build/explainer/, which is disposable.
"""
import subprocess, pathlib, sys, os, json, base64, wave, urllib.request, time

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORK = ROOT / "build" / "explainer"
CARDS = WORK / "cards"
WORK.mkdir(parents=True, exist_ok=True)
A = WORK / "audio"; A.mkdir(exist_ok=True)
S = WORK / "segments"; S.mkdir(exist_ok=True)
if not CARDS.is_dir():
    sys.exit("no cards - run scripts/build_explainer_cards.py first")

def _key():
    """GEMINI_API_KEY, or GEMINI_KEY_FILE naming a dotenv file that holds it.

    The file form exists so the key never has to be pasted onto a command line or into
    a shell history to run this build.
    """
    k = os.environ.get("GEMINI_API_KEY")
    if k:
        return k.strip()
    f = os.environ.get("GEMINI_KEY_FILE")
    if f:
        for ln in pathlib.Path(f).expanduser().read_text().splitlines():
            ln = ln.strip()
            if ln.startswith("GEMINI_API_KEY="):
                return ln.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit("set GEMINI_API_KEY, or GEMINI_KEY_FILE pointing at a dotenv file holding it")

KEY = _key()
TTS_MODEL = "gemini-2.5-flash-preview-tts"
TTS_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{TTS_MODEL}:generateContent"
VOICE = "Charon"
STYLE = ("Read as a calm, confident technical narrator presenting to engineers. "
         "Even pace, natural pauses at the full stops, no hard sell: ")

LUFS = "loudnorm=I=-16:TP=-1.5:LRA=11"   # matches the level that read well on the last project
TAIL = 0.8             # silence after each line, so a cut lands in a pause
FADE = 0.3             # video fade in/out, spent inside that silence
MAX_LEN = 345
MAX_STRETCH = 1.8      # past this the footage crawls; hold the last frame instead

SLIDES = [
 "On a bad morning at SetuHaul, thirty-five drivers message within half an hour. Six facilities, thirty-two dock doors, and not one live G P S feed. So every arrival time is a claim, not a measurement. One late truck breaks a whole chain of promises. But the single driver is not the hard part. The hard part is this. Two conversations read the same free slot, both drivers say yes, and only one promise can be true.",
 "So we built a dock exception service that drivers message in plain language. It reads the message, works out what the truck can actually reach, holds one slot, and asks a human to accept it. Four rules hold the whole thing up. Capacity is arithmetic, not judgement. Nothing is promised until a warehouse accepts. Two partial unique indexes make a double booking impossible at the database, not merely unlikely in the code. And every decision is written to an append-only log.",
 "The architecture is three roles, over one engine, over one database. Driver, coordinator and warehouse each get their own console. Next dot J S on Vercel serves them, and proxies the A P I, so the session cookie stays same-origin. Behind that, a single Lambda runs FastAPI and the eleven step engine. Postgres holds the state, the indexes, and the audit log. And Bedrock sits off to one side, reached by just two of the eleven steps. The engine never trusts the browser for identity.",
 "So what actually happens to a message? First it loads the trip, screens it for prompt injection, and catches a retry. All of that before any model runs. Step four is the first one that does. It turns the sentence into fields. Five works out which trip was meant. Six adds a confidence buffer to the declared arrival. Seven routes it: a booking, a status question, or a question back. Eight ranks the feasible slots. Nine inserts the hold, and that is where the database, not the code, blocks a double booking. Then ten re-checks the answer against the decision, before eleven writes the reply.",
 "Which leaves the real question. Where is the model actually used? Capacity needs arithmetic, not a language model. So it earns its place at just two edges, where the work starts and ends in human sentences. Going in, at step four, Nova Micro turns around two hours late, anything after seven, into structured fields. It gets no slot data and no booking power, so it cannot schedule anything. Coming out, at step eleven, it phrases a decision the code has already made. The times are passed in, it may not invent one, and any mismatch goes to a person. Plenty of replies use no model at all.",
]

DEMOS = [
 ("setuhaul-e2e-01-driver", 0,
  "So let us drive it, against the deployed stack. A driver signs in, sees only their own trips, and says they are about sixty minutes late. That is not taken as a time. It is buffered. And the booked window on dock D four turns out to close fifty minutes before the truck can reach it. So the promise is withdrawn, rather than repeated. Three reachable windows are offered, and none of them is reserved. The driver says the second one works, and the second one is what gets held."),
 ("setuhaul-e2e-04-queries-failures", 0,
  "Now a second driver, and the answers it refuses to give. Asked whether the warehouse has accepted, it answers the status, rather than offering a fresh list. Asked to book the earliest dock, it books, because an explicit request is the only thing that takes a hold. Asked for a slot after seven that also finishes by half past one, it does not report a shortage of docks. A window that shuts before it opens is a misreading of the driver, so it asks which limit is real. And told to ignore its instructions and hand over a dock, it refuses at the gate, and passes the conversation to a person. Those last two cost one and a half seconds, and half a second, because neither of them reaches the expensive path."),
 ("setuhaul-e2e-03-warehouse", 0,
  "Meanwhile, nothing is promised yet. The hold is waiting in the warehouse queue, which says so plainly. Held, not confirmed. This is the Jaipur warehouse account, signed in directly, seeing six docks where the coordinator saw nine, because the view is scoped to that one facility. They accept. And only now is it a booking."),
 ("setuhaul-e2e-02-ops-warehouse", 6.4,
  "And here is the fleet, from the coordinator's desk. One card per truck, worst first. Asked what a truck can get, it reports seventy-nine chains checked. And the window the first driver took is already gone from the list, because both views read one database."),
 ("setuhaul-e2e-05-driver-confirmed", 0,
  "Which brings us back to the driver who started all this. His dashboard no longer warns him. The one o'clock window works, because he is ready at eleven fifty. He asks what the second driver asked, and gets a different answer. Confirmed, not waiting. He arrived with a promise that could not be kept, and leaves with a dock he can reach."),
]

def dur(p):
    return float(subprocess.run(["ffprobe","-v","error","-show_entries","format=duration",
        "-of","default=nw=1:nk=1",str(p)],capture_output=True,text=True).stdout.strip())

def tts(i, line):
    """Gemini TTS -> 24k mono wav -> loudness-normalised aac, with TAIL seconds of silence."""
    wav, m4a = A/f"{i:02d}.wav", A/f"{i:02d}.m4a"
    if not m4a.exists():
        body = {"contents":[{"parts":[{"text": STYLE + line}]}],
                "generationConfig":{"responseModalities":["AUDIO"],
                    "speechConfig":{"voiceConfig":{"prebuiltVoiceConfig":{"voiceName":VOICE}}}}}
        req = urllib.request.Request(TTS_URL, data=json.dumps(body).encode(),
            headers={"x-goog-api-key": KEY, "Content-Type":"application/json"})
        pcm = None
        for attempt in range(5):
            try:
                with urllib.request.urlopen(req, timeout=180) as r:
                    d = json.load(r)
                pcm = base64.b64decode(d["candidates"][0]["content"]["parts"][0]["inlineData"]["data"])
                break
            except Exception as e:
                print(f"  retry {attempt}: {e}"); time.sleep(5)
        if pcm is None:
            raise RuntimeError(f"TTS failed for line {i}")
        with wave.open(str(wav), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000); w.writeframes(pcm)
        subprocess.run(["ffmpeg","-y","-v","error","-i",str(wav),
            "-af", f"{LUFS},apad=pad_dur={TAIL}",
            "-ar","48000","-ac","2","-c:a","aac","-b:a","160k",str(m4a)],check=True)
    return m4a

def fade(d):
    """Fade in at the head and out over the tail silence, so cuts land in a pause."""
    return f"fade=t=in:st=0:d={FADE},fade=t=out:st={max(0.0, d-FADE):.2f}:d={FADE}"

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

parts, n = [], 0
for i, line in enumerate(SLIDES, 1):
    n += 1; m4a = tts(n, line); d = dur(m4a)
    seg = S/f"{n:02d}.mp4"
    subprocess.run(["ffmpeg","-y","-v","error","-loop","1","-i",str(CARDS / f"{i:02d}.png"),"-i",str(m4a),
        "-vf", fade(d),
        "-c:v","libx264","-preset","medium","-tune","stillimage","-pix_fmt","yuv420p","-r","30",
        "-t",f"{d:.2f}","-c:a","copy",str(seg)],check=True)
    parts.append(seg); print(f"slide {i}: {d:5.1f}s")

for stem, skip, line in DEMOS:
    n += 1; m4a = tts(n, line)
    vid = demo_clip(stem, skip)
    va, aa = dur(vid), dur(m4a)
    # The narration always runs longer than the capture, and holding the last frame for
    # eight seconds looks like the video has frozen. Stretch the footage to the length of
    # the voice instead: these are discrete screenshots roughly a second apart, so a
    # slower cadence reads better rather than worse. tpad only covers the rounding.
    stretch = min(MAX_STRETCH, max(1.0, aa / va))
    seg = S/f"{n:02d}.mp4"
    subprocess.run(["ffmpeg","-y","-v","error","-i",str(vid),"-i",str(m4a),
        "-filter_complex",
        f"[0:v]setpts={stretch:.4f}*PTS,tpad=stop_mode=clone:stop_duration=30,{fade(aa)}[v]",
        "-map","[v]","-map","1:a","-t",f"{aa:.2f}",
        "-c:v","libx264","-preset","medium","-pix_fmt","yuv420p","-r","30",
        "-c:a","copy",str(seg)],check=True)
    parts.append(seg); print(f"demo  {stem[:28]:28} video {va:5.1f}s  voice {aa:5.1f}s  x{stretch:.2f}")

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
