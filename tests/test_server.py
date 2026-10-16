import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from studio import server
from studio.core import Store


class DashboardHTTPTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.store_patch=patch.object(server,"STORE",Store(cls.temp.name))
        cls.store_patch.start()
        cls.http=ThreadingHTTPServer(("127.0.0.1",0),server.Handler)
        cls.thread=threading.Thread(target=cls.http.serve_forever,daemon=True)
        cls.thread.start()
        cls.url=f"http://127.0.0.1:{cls.http.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.store_patch.stop()
        cls.temp.cleanup()

    def call(self,path,data=None,headers=None):
        h=dict(headers or {})
        if data is not None:
            h.update({"Content-Type":"application/json","X-Studio-Request":"1"})
        req=urllib.request.Request(self.url+path,data=json.dumps(data).encode() if data is not None else None,headers=h)
        try:
            with urllib.request.urlopen(req) as res:
                return res.status,res.read(),res.headers
        except urllib.error.HTTPError as e:
            return e.code,e.read(),e.headers

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
