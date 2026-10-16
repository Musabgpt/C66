import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from . import addons as e

class ExtensionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name); self.ctx=SimpleNamespace(root=str(self.root),after_change=lambda p:None)
    def test_catalog_has_only_real_builtins_ready(self):
        data=json.loads(e.catalog(self.ctx)[0]); self.assertTrue(any(x['id']=='dns_inspector' and x['status']=='ready' for x in data)); self.assertTrue(any(x['id']=='netlify' and x['status']=='external' for x in data))
    def test_diff_is_reviewable(self):
        diff,_=e.git_diff_patch(self.ctx,'index.html','old\n','new\n'); self.assertIn('--- a/index.html',diff); self.assertIn('+new',diff)
    def test_static_site_requires_index_and_writes_files(self):
        msg,meta=e.static_site_build(self.ctx,'site',{'index.html':'<h1>مرحبا</h1>','style.css':'body{}'}); self.assertTrue((self.root/'site/index.html').is_file()); self.assertEqual(meta['files'],2)
        with self.assertRaises(e.tools.ToolError): e.static_site_build(self.ctx,'missing',{'style.css':'body{}'})
    def test_dns_response_is_parsed_without_external_credentials(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self,*a): return False
            def read(self,n): return b'{"Status":0,"Answer":[{"name":"example.org","type":1,"TTL":60,"data":"192.0.2.1"}]}'
        with patch.object(e.urllib.request,'urlopen',return_value=Response()):
            text,meta=e.dns_lookup(self.ctx,'example.org','A'); self.assertIn('192.0.2.1',text); self.assertEqual(meta['answers'],1)

if __name__=='__main__': unittest.main()
