"""Run on your machine: python -m unittest discover -s tests -p test_customer_access.py"""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from pilot import store
from pilot.exports import safe_cell
from pilot.benchmark import score


class CustomerAccessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"CUSTOMER_DATA_ROOT": self.temp.name})
        self.env.start(); store.initialize()
        self.a = store.Access("a", "a"*32, time.time()+1000)
        self.b = store.Access("b", "b"*32, time.time()+1000)
        with store.db() as c:
            for access in (self.a, self.b):
                c.execute("INSERT INTO workspaces VALUES(?,?)", (access.workspace, access.principal))
                c.execute("INSERT INTO members VALUES(?,?)", (access.principal, access.workspace))
            c.execute("INSERT INTO documents(id,workspace,name,sha,status,created) VALUES(?,?,?,?,?,?)", ("c"*32,self.a.workspace,"private.pdf","hash","ready",time.time()))
            c.execute("INSERT INTO jobs(id,workspace,principal,kind,payload,status,stage,result,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)", ("d"*32,self.a.workspace,"a","verify","{}","complete","done",json.dumps({"results":[{"verdict":"SUPPORTED"}]}),time.time(),time.time()))

    def tearDown(self):
        self.env.stop(); self.temp.cleanup()

    def test_other_workspace_cannot_open_review(self):
        with self.assertRaises(PermissionError): store.get_job(self.b,"d"*32)

    def test_other_workspace_cannot_download_document(self):
        with self.assertRaises(PermissionError): store.document_bytes(self.b,"c"*32)

    def test_other_workspace_cannot_delete_document(self):
        with self.assertRaises(PermissionError): store.request_delete(self.b,"c"*32)

    def test_other_workspace_cannot_submit_foreign_source(self):
        with self.assertRaises(PermissionError): store.submit_review(self.b,"review","claim",["c"*32])

    def test_membership_revocation_takes_effect(self):
        with store.db() as c: c.execute("DELETE FROM members WHERE principal='a'")
        with self.assertRaises(PermissionError): store.get_job(self.a,"d"*32)

    def test_expired_session_denied(self):
        expired=store.Access("a",self.a.workspace,time.time()-1)
        with self.assertRaises(PermissionError): store.jobs(expired)

    def test_path_traversal_denied(self):
        with self.assertRaises(PermissionError): store.file_path(self.a.workspace,"../../outside")
        with self.assertRaises(ValueError): store.uid("../other")

    def test_stale_review_decision_rejected(self):
        store.decide(self.a,"d"*32,0,"reviewed","checked")
        with self.assertRaises(ValueError): store.decide(self.a,"d"*32,0,"incorrect_evidence","stale")
        self.assertEqual(store.decisions(self.a,"d"*32)[0]["decision"],"reviewed")

    def test_csv_formula_neutralized(self):
        self.assertEqual(safe_cell("=1+1"),"'=1+1")
        self.assertTrue(safe_cell(" @SUM(1)").startswith("'"))

    def test_failures_not_scored_as_unknown(self):
        report=score([{"status":"complete","gold":"CONTRADICTED","predicted":"SUPPORTED"},
                      {"status":"failed","gold":"UNKNOWN","predicted":"UNKNOWN"}])
        self.assertEqual(report["completed"],1)
        self.assertEqual(report["accuracy"],0)
        self.assertEqual(report["false_supported_count"],1)
        self.assertIsNone(report["observed_cost_usd"])


if __name__ == "__main__": unittest.main()
