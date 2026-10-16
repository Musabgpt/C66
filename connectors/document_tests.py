import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from . import documents as d

class DocumentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name); self.changed=[]
        self.ctx=SimpleNamespace(root=str(self.root),session=SimpleNamespace(dirs=[]),before_change=lambda p:None,after_change=self.changed.append)
    def test_arabic_documents_roundtrip(self):
        for extension in ('md','html','txt'):
            d.create(self.ctx,'notes.'+extension,'# مرحبا\nهذا اختبار')
            text,meta=d.read(self.ctx,'notes.'+extension)
            self.assertIn('مرحبا',text);self.assertTrue(meta['document'])
    def test_archive_roundtrip(self):
        d.create(self.ctx,'nested/notes.md','مرحبا')
        d.pack(self.ctx,'files.zip',['nested'])
        d.extract(self.ctx,'files.zip','unpacked')
        self.assertEqual((self.root/'unpacked/nested/notes.md').read_text(),'مرحبا')
    def test_bad_archive_rejects_everything_before_writing(self):
        for name in ('../outside.txt','/outside.txt','C:/outside.txt','a/../../outside.txt','..\\outside.txt'):
            with self.subTest(name=name):
                with zipfile.ZipFile(self.root/'bad.zip','w') as z:z.writestr('safe.txt','ok');z.writestr(name,'bad')
                with self.assertRaises(d.tools.ToolError): d.extract(self.ctx,'bad.zip','out')
                self.assertFalse((self.root/'out').exists())
    def test_zip_symlink_rejected(self):
        item=zipfile.ZipInfo('link');item.create_system=3;item.external_attr=0o120777<<16
        with zipfile.ZipFile(self.root/'bad.zip','w') as z:z.writestr(item,'/tmp')
        with self.assertRaises(d.tools.ToolError):d.extract(self.ctx,'bad.zip','out')
    def test_symlink_cannot_write_outside_workspace(self):
        with tempfile.TemporaryDirectory() as outside:
            (self.root/'link').symlink_to(outside,target_is_directory=True)
            with self.assertRaises(d.tools.ToolError):d.create(self.ctx,'link/notes.txt','bad')
            self.assertFalse((Path(outside)/'notes.txt').exists())
    def test_pdf_read_and_native_creation_contract(self):
        from pypdf import PdfWriter
        pdf=PdfWriter();pdf.add_blank_page(200,200)
        with (self.root/'scan.pdf').open('wb') as f:pdf.write(f)
        text,meta=d.read(self.ctx,'scan.pdf',limit=1)
        self.assertIn('OCR',text);self.assertEqual(meta['pages'],1)
        def render(action,**args):
            self.assertEqual(action,'document_pdf');self.assertEqual(args['content'],'مرحبا')
            (self.root/'arabic.pdf').write_bytes(b'%PDF-1.4\n');return {'ok':True}
        with patch.object(d.phone,'available',return_value=True),patch.object(d.phone,'call',side_effect=render):
            d.create(self.ctx,'arabic.pdf','مرحبا')
        self.assertIn(str(self.root/'arabic.pdf'),self.changed)
    def test_failed_download_preserves_existing_file(self):
        d.create(self.ctx,'notes.txt','original')
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def read(self,n):raise OSError('interrupted')
        with patch.object(d.urllib.request,'build_opener') as opener:
            opener.return_value.open.return_value=Response()
            with self.assertRaises(OSError):d.download(self.ctx,'https://example.org/notes.txt','notes.txt')
        self.assertEqual((self.root/'notes.txt').read_text(),'original')
