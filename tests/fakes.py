"""Explicitly fictional providers. Never enabled in the running dashboard."""
import io
import math
import wave
from studio import media
from studio.verification import SECTIONS

FACTS = [
    ("The fictional Aster mill has 12 copper turbines.", "aster turbine count"),
    ("The fictional Brindle port has 24 silver cranes.", "brindle crane count"),
    ("The fictional Cobalt train has 36 green carriages.", "cobalt carriage count"),
    ("The fictional Dapple vault has 48 paper doors.", "dapple door count"),
    ("The fictional Ember bridge has 60 wooden arches.", "ember arch count"),
    ("The fictional Fable station has 72 violet platforms.", "fable platform count"),
    ("The fictional Grove engine has 84 amber cylinders.", "grove cylinder count"),
    ("The fictional Hazel tower has 96 blue windows.", "hazel window count"),
]


def wav_bytes(seconds=0.2):
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        # Non-silent synthetic tone to exercise loudness and ducking.
        import struct
        wav.writeframes(b"".join(struct.pack("<h", int(1200*math.sin(i*2*math.pi*220/16000)))
                                 for i in range(max(1, int(seconds*16000)))))
    return out.getvalue()


class FakeProviders:
    def __init__(self):
        self.speech_calls = 0
        self.fail_speech_at = None
        self.ai_calls = []

    def llm(self, prompt, payload):
        self.ai_calls.append((prompt, payload))
        if prompt == "research_plan":
            return {"questions":[f"What powers fictional mechanism {i}?" for i in range(8)]}
        if prompt == "extract_claims":
            return {"claims":[{"claim":fact, "quote":fact, "metric":metric, "source_index":i}
                               for i, (fact,metric) in enumerate(FACTS)]}
        if prompt == "script":
            claims = payload["claims"]
            scenes = []
            for i, section in enumerate(SECTIONS):
                c = claims[i % len(claims)]
                text = c["claim"] + (" Why does this matter?" if i == 0 else "")
                if section == "next curiosity" and payload["next_idea"]:
                    text += "\nNext, we investigate " + payload["next_idea"] + "."
                scenes.append({"id":f"s{i+1}", "section":section, "text":text,
                               "claim_ids":[c["id"]], "footage_query":f"fictional factory machine"})
            return {"scenes":scenes}
        if prompt == "titles":
            return {"titles":[{"family":f,"title":t} for f,t in zip(
                ["curiosity","mystery","contrarian","scale","question"],
                ["Inside the Aster mill","The hidden mill","A different kind of mill","A world of copper","What powers Aster?"])],
                "thumbnail":{"object":"copper turbine","emotion":"small versus immense","text":"Hidden Power"},
                "description":"An explicitly fictional documentary for offline testing."}
        if prompt == "score":
            return dict(curiosity=25, audience=20, story=20, visuals=10, evergreen=10, monetization=10, difficulty=0)
        raise AssertionError(prompt)

    def search(self, question):
        i = int(question.split()[-1].rstrip("?"))
        return [{"url":f"https://fictional.invalid/source-{i}"}]

    def read(self, url):
        i = int(url.rsplit("-",1)[-1])
        return {"url":url,"title":f"Fictional source {i}","text":FACTS[i][0],
                "publication_date":"2026-01-01", "origin":"fake"}

    def voice_identity(self):
        return "fictional-test-voice-not-a-secret"

    def speech(self, text):
        self.speech_calls += 1
        if self.speech_calls == self.fail_speech_at:
            from studio.core import StudioError
            raise StudioError("Fictional provider interrupted.")
        return wav_bytes(max(0.2, len(text.split())*0.07))

    def videos(self, query, page=1):
        return [{"id":i, "url":f"https://fictional.invalid/clip/{i}", "user":{"name":"Fictional Photographer"},
                 "video_files":[{"id":i, "width":1280, "height":720, "file_type":"video/mp4",
                                "link":f"https://fictional.invalid/file/{i}"}]} for i in range(1,41)]

    def download(self, url, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        media.ffmpeg(["-f","lavfi","-i","color=c=teal:s=320x180:r=24:d=0.5","-an",
                      "-c:v","libx264","-threads","1","-pix_fmt","yuv420p",path])
