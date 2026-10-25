import unittest
from unittest.mock import Mock
from scripts.go_live import managed_postgres_recovery as mod

class Cursor:
    def __init__(self, columns, indexes, constraints):
        self.rows=[columns,indexes,constraints]
        self.i=0
    def __enter__(self): return self
    def __exit__(self,*a): return False
    def execute(self,*args): pass
    def fetchall(self):
        data=self.rows[self.i];self.i+=1
        return data

class SchemaCatalogTests(unittest.TestCase):
    def fixture(self,drop_index=False,drop_primary=False):
        cols=[(t,"id","integer","NO") for t in mod.CRITICAL_TABLES]
        idx=[(t,t+"_pkey","CREATE UNIQUE INDEX") for t in mod.CRITICAL_TABLES
             if not(drop_index and t==mod.CRITICAL_TABLES[0])]
        cons=[(t,t+"_pkey","PRIMARY KEY") for t in mod.CRITICAL_TABLES
              if not(drop_primary and t==mod.CRITICAL_TABLES[0])]
        conn=Mock()
        conn.cursor.return_value=Cursor(cols,idx,cons)
        return conn
    def test_catalog_digest_contains_no_row_data(self):
        digest, counts=mod.schema_catalog(self.fixture())
        self.assertEqual(len(digest),64)
        self.assertEqual(counts["columns"],len(mod.CRITICAL_TABLES))
    def test_missing_required_index_refused(self):
        with self.assertRaisesRegex(RuntimeError,"index"):
            mod.schema_catalog(self.fixture(drop_index=True))
    def test_missing_primary_key_refused(self):
        with self.assertRaisesRegex(RuntimeError,"primary key"):
            mod.schema_catalog(self.fixture(drop_primary=True))
    def test_structural_catalog_not_mutated(self):
        conn=self.fixture()
        mod.schema_catalog(conn)
        queries=[str(call.args[0]) for call in conn.cursor.return_value.execute.call_args_list]
        self.assertTrue(all(q.startswith("SET TRANSACTION READ ONLY") or q.startswith("SELECT") for q in queries))

if __name__=="__main__":
    unittest.main()
