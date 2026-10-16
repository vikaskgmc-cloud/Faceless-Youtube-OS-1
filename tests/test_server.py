import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from urllib.parse import urlencode
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from studio import server
from studio.core import Store


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class DashboardHTTPTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.environment_patch=patch.dict("os.environ", {
            "STUDIO_ACCESS_PASSWORD":"test-only-studio-password-123",
            "SESSION_SECRET":"test-only-session-signing-secret-1234567890",
        })
        cls.environment_patch.start()
        cls.store_patch=patch.object(server,"STORE",Store(cls.temp.name))
        cls.store_patch.start()
        cls.http=ThreadingHTTPServer(("127.0.0.1",0),server.Handler)
        cls.thread=threading.Thread(target=cls.http.serve_forever,daemon=True)
        cls.thread.start()
        cls.url=f"http://127.0.0.1:{cls.http.server_port}"
        cls.opener=urllib.request.build_opener(NoRedirect)

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.store_patch.stop()
        cls.environment_patch.stop()
        cls.temp.cleanup()

    def call(self,path,data=None,headers=None,authenticated=True):
        h=dict(headers or {})
        if authenticated and "Cookie" not in h:
            h["Cookie"]=self.auth_cookie
        if data is not None:
            h.update({"Content-Type":"application/json","X-Studio-Request":"1"})
        req=urllib.request.Request(self.url+path,data=json.dumps(data).encode() if data is not None else None,headers=h)
        try:
            with self.opener.open(req) as res:
                return res.status,res.read(),res.headers
        except urllib.error.HTTPError as e:
            return e.code,e.read(),e.headers

    @classmethod
    def setUp(cls):
        cls.auth_cookie = cls._login_cookie()

    @classmethod
    def _login_cookie(cls):
        request=urllib.request.Request(
            cls.url+"/auth/login",
            data=urlencode({"password":"test-only-studio-password-123"}).encode(),
            headers={"Content-Type":"application/x-www-form-urlencoded","Origin":cls.url},
        )
        try:
            with cls.opener.open(request) as response:
                return response.headers["Set-Cookie"].split(";",1)[0]
        except urllib.error.HTTPError as response:
            if response.code != 303:
                raise AssertionError(f"Test login failed: {response.code}") from response
            return response.headers["Set-Cookie"].split(";",1)[0]

    def test_dashboard_and_project_crud_and_safe_errors(self):
        code,html,_=self.call("/")
        self.assertEqual(code,200)
        self.assertIn(b"app.js",html)
        code,body,_=self.call("/api/projects",{"topic":"Fictional human workflow","pillar":"business"})
        self.assertEqual(code,201)
        project=json.loads(body)
        self.assertEqual(project["quality"],"4K")
        pid=project["id"]
        code,body,_=self.call(f"/api/projects/{pid}/gate",{"gate":"script"})
        self.assertEqual(code,400)
        self.assertNotIn(b"Traceback",body)
        code,body,_=self.call(f"/api/projects/{pid}/dry_run",{})
        self.assertEqual(json.loads(body)["chunks"],[])
        code,_,_=self.call(f"/files/{pid}/../../.replit")
        self.assertEqual(code,404)
        code,body,_=self.call("/api/settings")
        self.assertEqual(code,200)
        self.assertNotIn(b"SESSION_SECRET",body)

    def test_login_and_authenticated_workflow(self):
        request=urllib.request.Request(
            self.url+"/auth/login",
            data=urlencode({"password":"test-only-studio-password-123"}).encode(),
            headers={"Content-Type":"application/x-www-form-urlencoded","Origin":self.url},
        )
        response=None
        try:
            response=self.opener.open(request)
        except urllib.error.HTTPError as error:
            if error.code != 303:
                raise
            response=error
        with response:
            self.assertEqual(response.status,303)
            cookie=response.headers["Set-Cookie"].split(";",1)[0]
            self.assertIn("HttpOnly",response.headers["Set-Cookie"])
            self.assertIn("Secure",response.headers["Set-Cookie"])
        code,html,_=self.call("/",headers={"Cookie":cookie})
        self.assertEqual(code,200)
        self.assertIn(b"Sign out",html)
        code,body,_=self.call("/api/projects",{"topic":"Authenticated human workflow","pillar":"business"},headers={"Cookie":cookie})
        self.assertEqual(code,201)
        project=json.loads(body)
        pid=project["id"]
        code,body,_=self.call(f"/api/projects/{pid}/score",{
            "curiosity":20,"audience":18,"story":18,"visuals":8,
            "evergreen":8,"monetization":8,"difficulty":2,"confirmed":True,
        },headers={"Cookie":cookie})
        self.assertEqual(code,200)
        self.assertTrue(json.loads(body)["score"]["confirmed"])
        code,body,_=self.call(f"/api/projects/{pid}/gate",{"gate":"topic"},headers={"Cookie":cookie})
        self.assertEqual(code,200)
        self.assertEqual(json.loads(body)["status"],"SELECTED")
        code,body,_=self.call(f"/api/projects/{pid}/dry_run",{},headers={"Cookie":cookie})
        self.assertEqual(code,200)
        self.assertEqual(json.loads(body)["chunks"],[])
        code,body,_=self.call(f"/api/projects/{pid}/gate",{"gate":"research"},headers={"Cookie":cookie})
        self.assertEqual(code,400)
        self.assertIn(b"Run research",body)

    def test_logout_clears_session_cookie(self):
        code,_,headers=self.call("/auth/logout",{})
        self.assertEqual(code,303)
        self.assertEqual(headers.get("Location"),"/login")
        self.assertIn("Max-Age=0",headers.get("Set-Cookie",""))

    def test_unauthenticated_requests_cannot_read_download_or_spend_credits(self):
        topics_before=[x["topic"] for x in server.STORE.list()]
        p=server.STORE.create({"topic":"Private fixture","pillar":"technology"})
        topics_after_fixture=[x["topic"] for x in server.STORE.list()]
        folder=server.STORE.folder(p["id"])
        (folder/"voice").mkdir()
        (folder/"voice/private.wav").write_bytes(b"provider output")
        p["artifacts"]["narration"]={"path":"voice/private.wav"}
        server.STORE.save(p)
        server.JOBS.pop(p["id"],None)

        for path in ("/", "/settings", "/analytics", "/app.js"):
            code,_,headers=self.call(path,authenticated=False)
            self.assertEqual(code,303)
            self.assertEqual(headers.get("Location"),"/login")
        for path in ("/api/overview","/api/settings",f"/api/projects/{p['id']}",
                     f"/api/projects/{p['id']}/export-script",
                     f"/files/{p['id']}/voice/private.wav"):
            code,body,_=self.call(path,authenticated=False)
            self.assertEqual(code,401,path)
            self.assertNotIn(b"Private fixture",body)
        code,_,_=self.call("/api/overview",{"bad":"payload"},{"Cookie":"studio_session=invalid."})
        self.assertEqual(code,401)

        with patch.object(server,"start_job") as start_job, patch.object(server,"mutate") as mutate:
            code,_,_=self.call("/api/projects",{"topic":"Unauthorized create","pillar":"business"},authenticated=False)
            self.assertEqual(code,401)
            for action in ("suggest_score","autopilot","run","rerun"):
                code,_,_=self.call(f"/api/projects/{p['id']}/{action}",{},authenticated=False)
                self.assertEqual(code,401,action)
            start_job.assert_not_called()
            mutate.assert_not_called()
        self.assertNotIn(p["id"],server.JOBS)
        self.assertEqual([x["topic"] for x in server.STORE.list()],topics_after_fixture)
        self.assertEqual(len(topics_after_fixture),len(topics_before)+1)

    def test_authentication_fails_closed_when_credentials_are_missing(self):
        with patch.dict("os.environ", {}, clear=True):
            code,body,_=self.call("/api/overview",authenticated=False)
            self.assertEqual(code,401)
            self.assertNotIn(b"projects",body)
            code,body,_=self.call("/login",authenticated=False)
            self.assertEqual(code,503)
            self.assertIn(b"STUDIO_ACCESS_PASSWORD",body)

    def test_cross_origin_mutations_are_blocked(self):
        code,body,_=self.call("/api/projects",{"topic":"Fictional","pillar":"business"},{"Origin":"https://malicious.invalid"})
        self.assertEqual(code,400)
        self.assertIn(b"Cross-site",body)

    def test_range_download_and_stale_outputs_not_accessible(self):
        p=server.STORE.create({"topic":"Fictional range test","pillar":"technology"})
        folder=server.STORE.folder(p["id"])
        (folder/"voice").mkdir()
        (folder/"voice/narration.wav").write_bytes(b"abcdefghij")
        p["artifacts"]["narration"]={"path":"voice/narration.wav"}
        server.STORE.save(p)
        code,body,headers=self.call(f"/files/{p['id']}/voice/narration.wav",headers={"Range":"bytes=2-5"})
        self.assertEqual((code,body),(206,b"cdef"))
        self.assertEqual(headers["Content-Range"],"bytes 2-5/10")
        p["artifacts"]={}
        server.STORE.save(p)
        code,_,_=self.call(f"/files/{p['id']}/voice/narration.wav")
        self.assertEqual(code,404)


if __name__=="__main__":
    unittest.main()
