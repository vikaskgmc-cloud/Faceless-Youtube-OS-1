import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from studio import analytics, media
from studio.core import (SCORE_LIMITS, STATUSES, Store, StudioError, add_claim, advance,
                         gate, hand_verify, research_valid, save_score, save_script, script_valid)
from studio.pipeline import Pipeline, upload_package
from studio.providers import LiveProviders, settings
from studio.verification import (detect_conflicts, evidence_issues, fact_check, numbers,
                                 packaging_issues)
from tests.fakes import FACTS, FakeProviders, wav_bytes


class ProjectTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.p = self.store.create({"topic":"The fictional Aster mill","pillar":"hidden mechanisms","quality":"HD","next_idea":"the paper engine"})
        self.fake = FakeProviders()
        self.pipeline = Pipeline(self.store, self.fake)

    def tearDown(self):
        self.temp.cleanup()

    def research(self):
        save_score(self.p, {**SCORE_LIMITS, "difficulty":0,"confirmed":True})
        gate(self.p, {"gate":"topic"})
        self.pipeline.autopilot(self.p)
        return self.p

    def approve_script(self):
        self.research()
        gate(self.p, {"gate":"research"})
        self.pipeline.autopilot(self.p)
        gate(self.p, {"gate":"script"})
        return self.p

    def test_score_really_max_95_and_topic_human_gate(self):
        self.assertRaises(StudioError, gate, self.p, {"gate":"topic"})
        save_score(self.p,{**SCORE_LIMITS,"difficulty":0})
        self.assertEqual(self.p["score"]["total"],95)
        self.assertRaises(StudioError,gate,self.p,{"gate":"topic"})
        save_score(self.p,{"override_reason":"Deliberate editorial exception."})
        gate(self.p,{"gate":"topic"})
        self.assertEqual(self.p["status"],"SELECTED")

    def test_no_status_skip_or_reverse(self):
        self.assertRaises(StudioError,advance,self.p,"SCRIPTING")
        self.assertRaises(StudioError,advance,self.p,"IDEA")
        self.assertEqual(self.p["status"],"IDEA")

    def test_autopilot_never_selects_or_approves(self):
        self.pipeline.autopilot(self.p)
        self.assertEqual(self.p["status"],"IDEA")
        self.research()
        self.assertEqual(self.p["status"],"RESEARCHING")
        self.assertEqual(len(self.p["claims"]),8)
        self.assertNotIn("research",self.p["approvals"])
        self.assertTrue(all(c["status"]=="verified" and c["verified_by"]=="code" for c in self.p["claims"]))
        old = copy.deepcopy(self.p["claims"])
        self.pipeline.autopilot(self.p)
        self.assertEqual(self.p["claims"],old)
        self.assertEqual(len(self.p["questions"]),8)
        gate(self.p,{"gate":"research"})
        self.pipeline.autopilot(self.p)
        self.assertEqual(self.p["status"],"SCRIPT_REVIEW")
        self.assertNotIn("script",self.p["approvals"])

    def test_research_needs_five_verified_claims(self):
        self.research()
        for c in self.p["claims"][:4]:
            hand_verify(self.p,{"id":c["id"],"status":"rejected","note":"Fictional test rejection"})
        self.assertRaises(StudioError,gate,self.p,{"gate":"research"})

    def test_edit_withdraws_research_script_and_downloads(self):
        self.approve_script()
        c = self.p["claims"][0]
        add_claim(self.p,{**c,"source_index":c["source_index"]})
        self.assertFalse(research_valid(self.p))
        self.assertFalse(script_valid(self.p))
        self.assertEqual(self.p["status"],"APPROVED")  # Never regress.
        self.assertEqual(self.p["artifacts"],{})
        gate(self.p,{"gate":"research"})
        gate(self.p,{"gate":"script"})
        self.assertEqual(self.p["status"],"APPROVED")

    def test_manual_verification_needs_note_and_cannot_hide_conflict(self):
        self.research()
        c = self.p["claims"][0]
        self.assertRaises(StudioError,hand_verify,self.p,{"id":c["id"],"status":"verified","note":" "})
        hand_verify(self.p,{"id":c["id"],"status":"verified","note":"Inspected the fictional original."})
        self.assertEqual(c["verified_by"],"human")

    def test_invalid_source_index_cannot_invent_url(self):
        self.research()
        self.assertRaises(StudioError, add_claim,self.p,{"claim":"Invented","quote":"Invented"*5,"source_index":99,"source_url":"https://invented.invalid"})
        self.assertRaises(StudioError, add_claim,self.p,{"claim":"Invented","quote":"Invented"*5,"source_index":-1})
        self.assertEqual(len(self.p["claims"]),8)

    def test_script_gate_rechecks_not_cached_flag(self):
        self.research()
        gate(self.p,{"gate":"research"})
        self.pipeline.autopilot(self.p)
        self.p["script"]["scenes"][0]["text"] += " There were 9999 workers."
        self.p["fact_check"] = {"passed":True}
        self.assertRaises(StudioError,gate,self.p,{"gate":"script"})

    def test_script_revisions_and_next_idea(self):
        self.approve_script()
        script = copy.deepcopy(self.p["script"])
        script["scenes"][-1]["text"] = "A different future."
        save_script(self.p,script)
        self.assertEqual(len(self.p["revisions"]),2)
        self.assertIn("next idea"," ".join(self.p["fact_check"]["blocking"]))
        self.assertNotIn("script",self.p["approvals"])
        self.assertNotEqual(self.p["revisions"][0]["script"],self.p["revisions"][1]["script"])

    def test_human_can_write_initial_script_without_paid_llm(self):
        from studio.server import mutate
        self.research()
        gate(self.p,{"gate":"research"})
        draft=self.fake.llm("script",{"claims":[{"id":c["id"],"claim":c["claim"]} for c in self.p["claims"]],
                                      "next_idea":self.p["next_idea"]})
        mutate(self.p,"script",draft)
        self.assertEqual(self.p["status"],"SCRIPT_REVIEW")
        gate(self.p,{"gate":"script"})
        self.assertTrue(script_valid(self.p))

    @unittest.skipUnless(media.shutil.which("ffmpeg"),"ffmpeg not installed")
    def test_whole_line_offline_real_ffmpeg_with_fake_providers(self):
        self.approve_script()
        folder = self.store.folder(self.p["id"])
        # Also exercise uploaded music ducking and caption burn-in in the full cut.
        path = folder/"uploads/music.wav"
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(wav_bytes(0.5))
        self.p["music"]={"path":"uploads/music.wav","filename":"fictional-tone.wav","rights_confirmed":True}
        self.p["burn_captions"]=True
        decision = self.pipeline.autopilot(self.p)
        self.assertEqual(self.p["status"],"THUMBNAIL")
        self.assertIn("Human decision",decision)
        self.assertTrue(self.p["qa"]["passed"])
        self.assertEqual(len(self.p["titles"]),5)
        self.assertEqual(len({v["pexels_id"] for v in self.p["footage"]}),len(self.p["footage"]))
        self.assertEqual(self.p["selected_title"],"")
        video = media.probe(folder/"render/documentary.mp4")
        stream = next(s for s in video["streams"] if s["codec_type"]=="video")
        self.assertEqual((stream["width"],stream["height"]),(1920,1080))
        self.assertEqual(stream["codec_name"],"h264")
        self.assertLessEqual(abs(video["duration"]-self.p["artifacts"]["narration"]["duration"]),1.5)
        self.assertTrue((folder/"render/captions-estimated.srt").exists())
        self.assertIn("Fictional Photographer",(folder/"media/credits.txt").read_text())
        self.p["selected_title"]=self.p["titles"][0]["title"]
        gate(self.p,{"gate":"packaging"})
        upload_package(self.p,folder)
        self.assertEqual(self.p["status"],"UPLOAD_READY")
        self.pipeline.autopilot(self.p)
        self.assertEqual(self.p["status"],"UPLOAD_READY")
        self.assertFalse(self.p["youtube_url"])
        self.assertRaises(StudioError,gate,self.p,{"gate":"published","youtube_url":"https://youtube.com"})
        gate(self.p,{"gate":"published","youtube_url":"https://youtu.be/abcdefghijk"})
        analytics.save_analytics(self.p,{"ctr":6,"average_percentage_viewed":50,
                                       "retention_csv":"time,percent\n0,100\n30,60\n45,65\n60,35"})
        self.assertEqual(self.p["status"],"ANALYZING")
        self.assertEqual([h["to"] for h in self.p["history"]],STATUSES[1:])
        self.store.save(self.p)
        loaded = self.store.get(self.p["id"])
        self.assertEqual(loaded["status"],"ANALYZING")
        self.assertIn("never uploads", json.dumps(json.loads((folder/"upload-package.json").read_text())))

    @unittest.skipUnless(media.shutil.which("ffmpeg"),"ffmpeg not installed")
    def test_failed_voice_resumes_without_paying_again(self):
        self.approve_script()
        scenes = self.p["script"]["scenes"]
        scenes[0]["text"] = ("A fictional machine moves quietly. " * 340)
        gate(self.p,{"gate":"script"})
        plan = media.chunk_plan(self.p)
        self.assertGreater(len(plan),2)
        self.fake.fail_speech_at = 2
        folder = self.store.folder(self.p["id"])
        self.assertRaises(StudioError,media.voice,self.p,folder,self.fake,lambda _:None)
        self.assertEqual(len(list((folder/"voice").glob("*.mp3"))),1)
        self.fake.fail_speech_at = None
        media.voice(self.p,folder,self.fake,lambda _:None)
        unique_chunks = len({c["checksum"] for c in plan})
        self.assertEqual(self.fake.speech_calls,unique_chunks+1) # exact repeated chunks also reuse successful audio
        first = next((folder/"voice").glob("*.mp3"))
        first.write_bytes(b"corrupted")
        media.voice(self.p,folder,self.fake,lambda _:None)
        self.assertEqual(self.fake.speech_calls,unique_chunks+2)

    @unittest.skipUnless(media.shutil.which("ffmpeg"),"ffmpeg not installed")
    def test_real_4k_h264_render_reports_upscaled_source(self):
        self.approve_script()
        self.p["next_idea"]=""
        save_script(self.p,{"scenes":[{"id":"s1","section":"hook","text":FACTS[0][0]+" Why?",
                    "claim_ids":[self.p["claims"][0]["id"]],"footage_query":"fictional copper turbine"}]})
        gate(self.p,{"gate":"script"})
        self.p["quality"]="4K"
        self.pipeline.step(self.p,"voice")
        self.pipeline.step(self.p,"footage")
        self.pipeline.step(self.p,"cut")
        self.pipeline.step(self.p,"qa")
        folder=self.store.folder(self.p["id"])
        stream=next(s for s in media.probe(folder/"render/documentary.mp4")["streams"] if s["codec_type"]=="video")
        self.assertEqual((stream["width"],stream["height"]),(3840,2160))
        self.assertEqual(stream["codec_name"],"h264")
        self.assertTrue(self.p["qa"]["passed"])
        self.assertGreater(self.p["qa"]["upscaled_clips"],0)
        self.assertFalse(next(c["passed"] for c in self.p["qa"]["advisory"] if "native 4K" in c["label"]))

    def test_upload_ready_invalidated_production_can_be_restored_without_regressing(self):
        self.approve_script()
        # Synthetic later-stage state for a cheap control-flow regression test; no media asserted here.
        for target in ["VOICE","VISUALS","EDITING","QA","THUMBNAIL","UPLOAD_READY"]:
            advance(self.p,target)
        called=[]
        def fake_step(p,step):
            called.append(step)
            p["artifacts"][{"voice":"narration","footage":"footage","cut":"video"}.get(step,step)]={}
        with patch.object(self.pipeline,"step",side_effect=fake_step):
            self.pipeline.autopilot(self.p)
        self.assertEqual(called,["voice","footage","cut","qa","titles"])
        self.assertEqual(self.p["status"],"UPLOAD_READY")


class VerificationTest(unittest.TestCase):
    def test_quotes_and_numbers_and_meaning(self):
        page = '**The** fictional Aster mill has 12 copper turbines, recorded in 2026.'
        quote = '"The fictional Aster mill has 12 copper turbines"'
        self.assertEqual(evidence_issues("Aster mill has 12 copper turbines.",quote,page),[])
        for claim, q in [("Aster has 20 turbines.",quote),("Aster doubled its 12 turbines.",quote),
                          ("Aster has 12 billion turbines.",quote),("Only Aster has 12 turbines.",quote),
                          ("Aster has 12 turbines.","A tiny quote"),("Aster has 12 turbines.","An invented quote about 12 turbines which is not in the page.")]:
            self.assertTrue(evidence_issues(claim,q,page))
        self.assertNotEqual(numbers("12"),numbers("120"))
        self.assertEqual(numbers("1,000"),numbers("1000"))

    def test_conflicting_numbers_mark_both_and_resolve(self):
        claims = [{"id":"a","claim":"The fictional mill has 12 turbines.","metric":"mill turbine count","status":"verified","issues":[]},
                  {"id":"b","claim":"The fictional mill has 24 turbines.","metric":"mill turbine count","status":"verified","issues":[]}]
        detect_conflicts(claims)
        self.assertEqual([c["status"] for c in claims],["conflicting","conflicting"])
        claims[1]["status"]="rejected"
        claims[1]["base_status"]="rejected"
        detect_conflicts(claims)
        self.assertEqual([c["status"] for c in claims],["verified","rejected"])

    def test_scene_scoped_figures_unknown_ids_and_warnings(self):
        claims = [{"id":"a","claim":"A fictional machine has 12 turbines.","status":"verified"},
                  {"id":"b","claim":"A fictional machine has 24 wheels.","status":"verified"}]
        def check(text,ids):
            return fact_check({"scenes":[{"id":"s1","section":"hook","text":text,"claim_ids":ids,"footage_query":"factory turbine"}]},claims)
        self.assertTrue(check("There are 12 turbines?",["a"])["passed"])
        self.assertFalse(check("There are 24 wheels?",["a"])["passed"])
        self.assertFalse(check("There are 1.2 wheels?",["a"])["passed"])
        self.assertFalse(check("There are .5 wheels?",["a"])["passed"])
        self.assertFalse(check("There are 12 billion turbines?",["a"])["passed"])
        self.assertFalse(check("There are 3 billion turbines?",["a"])["passed"])
        self.assertFalse(check("A question?",["unknown"])["passed"])
        claims[0]["status"]="needs_checking"
        self.assertFalse(check("A question?",["a"])["passed"])
        warnings = check(("word " * 56)+"subscribe. What happened next? What happened next?",[])["warnings"]
        self.assertTrue(any("55" in w for w in warnings))
        self.assertTrue(any("big reveal" in w for w in warnings))
        self.assertTrue(any("begging" in w for w in warnings))
        self.assertTrue(any("Overused" in w for w in warnings))

    def test_packaging_blocks_unsupported_figures_and_word_count(self):
        claims=[{"id":"a","claim":"The fictional mill has 12 turbines.","status":"verified"}]
        thumb={"object":"turbine","emotion":"small versus large","text":"Hidden Power"}
        self.assertFalse(packaging_issues("Inside the mill",thumb,claims))
        self.assertTrue(packaging_issues("The 999 turbine mill",thumb,claims))
        self.assertTrue(packaging_issues("The 12 billion turbine mill",thumb,claims))
        self.assertTrue(packaging_issues("The 12.5million turbine mill",thumb,claims))
        self.assertTrue(packaging_issues("ALL CAPS",thumb,claims))
        self.assertTrue(packaging_issues("Inside the mill",{**thumb,"text":"Too many words for this thumbnail"},claims))

    def test_chunking_no_loss_and_hard_max(self):
        text = ("One fictional sentence. " * 1000) + "\n\n" + ("second " * 1000)
        plan=media.chunks(text)
        self.assertTrue(all(0<len(x)<=5000 for x in plan))
        self.assertEqual(" ".join(text.split())," ".join(" ".join(plan).split()))

    def test_quality_selection_ranges_fallback(self):
        files=[{"width":w,"height":w//2,"link":"https://fictional.invalid","file_type":"video/mp4"} for w in [640,1280,1920,2560,3840,4096]]
        self.assertEqual(media.choose_file(files,"HD")["width"],1920)
        self.assertEqual(media.choose_file(files,"4K")["width"],3840)
        self.assertEqual(media.choose_file(files[:3],"4K")["width"],1920)
        self.assertIsNone(media.choose_file(files[:1],"HD"))

    def test_all_numbers_detected_even_at_end_of_sentence(self):
        self.assertTrue(numbers("The year was 2026."))
        self.assertIn("12.5",numbers("12.5million"))
        self.assertNotEqual(numbers("12 million"),numbers("12 billion"))

    def test_no_secrets_exposed_or_fake_fallback(self):
        with patch.dict(os.environ,{},clear=True):
            config=settings()
            self.assertTrue(all(not p["ready"] for p in config["providers"]))
            self.assertRaisesRegex(StudioError,"OPENAI_API_KEY",LiveProviders().llm,"score",{})


class AnalyticsTest(unittest.TestCase):
    def test_channel_medians_quadrants_and_retention(self):
        priors=[{"id":str(i),"published_at":f"2026-01-0{i+1}","analytics":{"ctr":v,"average_percentage_viewed":r}} for i,(v,r) in enumerate([(4,40),(6,60),(5,50)])]
        p={"id":"new","published_at":"2026-02-01","analytics":{"ctr":6,"average_percentage_viewed":60,
           "retention":analytics.parse_retention("time,percent\n0,100\n20,80\n40,60\n50,70\n60,30")},
           "timeline":[{"scene_id":"s1","section":"hook","start":0,"end":35},{"scene_id":"s2","section":"reveal","start":35,"end":65}]}
        result=analytics.report(p,priors)
        self.assertEqual(result["median_ctr"],5)
        self.assertEqual(result["median_average_percentage_viewed"],50)
        self.assertIn("Make more",result["comparison"])
        self.assertEqual(result["retention"]["at_30_seconds"],70)
        self.assertEqual(result["retention"]["steepest_drop"]["start"],50)
        self.assertEqual(result["retention"]["biggest_rise"]["scene_id"],"s2")
        for ctr,apv,expected in [(6,40,"script and pacing"),(4,60,"title and thumbnail"),(4,40,"Wrong concept")]:
            p["analytics"].update(ctr=ctr,average_percentage_viewed=apv)
            self.assertIn(expected,analytics.report(p,priors)["comparison"])
        self.assertIn("Need at least 3",analytics.report(p,priors[:2])["comparison"])

    def test_retention_must_be_finite_sorted(self):
        for text in ["time,percent\n0,100\n0,50","time,percent\nnan,50","time,percent\n10,101"]:
            self.assertRaises(StudioError,analytics.parse_retention,text)


if __name__=="__main__":
    unittest.main()
