"""Re-fit the demo segments to their narration without re-synthesising the voice.

The Gemini free tier is a daily quota, and this build exhausted it part-way through a
re-render. What survived is every demo segment with its audio already inside it, so the
footage can be re-timed from the audio that is already on disk.

Why it needs re-timing: Charon reads slower than the voice the timings were tuned for,
so three of the five demo clips needed more than the 1.8x stretch cap and held their
last frame instead - eight seconds of it on the final act, which reads as a frozen
video rather than a pause. These captures are discrete screenshots about a second
apart, so stretching to 2.5x just shows each shot for two and a half seconds, which is
readable. That is strictly better than a freeze.

    python3 scripts/refit_demo_segments.py      # rewrites segments, then concatenates
"""
import subprocess, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORK = ROOT / "build" / "explainer"
S = WORK / "segments"

# segment number -> the clip it was built from, and the head-skip used originally
DEMOS = {6: ("setuhaul-e2e-01-driver", 0.0), 7: ("setuhaul-e2e-04-queries-failures", 0.0),
         8: ("setuhaul-e2e-03-warehouse", 0.0), 9: ("setuhaul-e2e-02-ops-warehouse", 6.4),
         10: ("setuhaul-e2e-05-driver-confirmed", 0.0)}
FADE = 0.3

def dur(p):
    return float(subprocess.run(["ffprobe","-v","error","-show_entries","format=duration",
        "-of","default=nw=1:nk=1",str(p)],capture_output=True,text=True).stdout.strip())

def fade(d):
    return f"fade=t=in:st=0:d={FADE},fade=t=out:st={max(0.0, d-FADE):.2f}:d={FADE}"

for n, (stem, skip) in DEMOS.items():
    seg, vid = S/f"{n:02d}.mp4", WORK/f"{stem}.mp4"
    if not seg.exists() or not vid.exists():
        sys.exit(f"missing {seg if not seg.exists() else vid}")
    aud = WORK/f"refit-{n:02d}.m4a"
    subprocess.run(["ffmpeg","-y","-v","error","-i",str(seg),"-vn","-c:a","copy",str(aud)],check=True)
    va, aa = dur(vid), dur(aud)
    stretch = max(1.0, aa / va)          # no cap: a slow cadence beats a frozen frame
    out = S/f"{n:02d}.mp4"
    tmp = S/f"{n:02d}.refit.mp4"
    subprocess.run(["ffmpeg","-y","-v","error","-i",str(vid),"-i",str(aud),
        "-filter_complex",
        f"[0:v]setpts={stretch:.4f}*PTS,tpad=stop_mode=clone:stop_duration=5,{fade(aa)}[v]",
        "-map","[v]","-map","1:a","-t",f"{aa:.2f}",
        "-c:v","libx264","-preset","medium","-pix_fmt","yuv420p","-r","30",
        "-c:a","copy",str(tmp)],check=True)
    tmp.replace(out)
    print(f"demo {n:02d} {stem[:30]:30} video {va:5.1f}s  voice {aa:5.1f}s  x{stretch:.2f}  (was capped at 1.80)")

parts = [S/f"{i:02d}.mp4" for i in range(1, 11)]
with open(WORK/"concat.txt","w") as fh:
    for p in parts:
        fh.write(f"file '{p}'\n")
OUT = ROOT/"docs"/"demo"/"setuhaul-explainer.mp4"
subprocess.run(["ffmpeg","-y","-v","error","-f","concat","-safe","0","-i",str(WORK/"concat.txt"),
    "-c:v","libx264","-preset","medium","-pix_fmt","yuv420p","-r","30",
    "-movflags","+faststart","-c:a","aac","-b:a","160k",str(OUT)],check=True)
t = dur(OUT)
print(f"\n{OUT.relative_to(ROOT)}\nTOTAL {int(t//60)}m{t%60:04.1f}s")
