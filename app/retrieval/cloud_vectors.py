"""Small, workspace-scoped pgvector collection for the existing hybrid retriever."""
from __future__ import annotations

import json
import math
import os

from pilot import cloud_db


def vector_literal(values, dimensions=768):
    values = list(values)
    if (len(values) != dimensions or dimensions != 768
            or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in values)
            or not any(values)):
        raise cloud_db.CloudStorageError("Embedding values or dimensions are incompatible with the saved cloud index.")
    return json.dumps(values,separators=(",",":"),allow_nan=False)


class Collection:
    def __init__(self, name, model, dimensions, *, workspace=None):
        from pilot.store import uid
        self.workspace = uid(workspace or os.getenv("CUSTOMER_WORKSPACE_ID", ""))
        self.name, self.model, self.dimensions = name, model, dimensions
        if dimensions != 768:
            raise cloud_db.CloudStorageError("The supplied Supabase schema uses 768 dimensions. Preserve the original embedding settings.")
        with cloud_db.db() as c:
            for saved in c.execute("SELECT model,dimensions FROM vector_collections WHERE workspace=?",(self.workspace,)).fetchall():
                if saved["model"] != model or saved["dimensions"] != dimensions:
                    raise cloud_db.CloudStorageError("The workspace already has different embedding settings. Restore the original configuration; no index was rebuilt.")
            c.execute("INSERT INTO vector_collections(workspace,name,model,dimensions) VALUES(?,?,?,?) ON CONFLICT DO NOTHING", (self.workspace,name,model,dimensions))
            row = c.execute("SELECT model,dimensions FROM vector_collections WHERE workspace=? AND name=?", (self.workspace,name)).fetchone()
            if not row or row["model"] != model or row["dimensions"] != dimensions:
                raise cloud_db.CloudStorageError("The cloud collection uses different embedding settings. Restore its original model and dimensions.")

    def _scope(self, where=None, ids=None):
        sql, args = "workspace=? AND collection=?", [self.workspace,self.name]
        if where is not None:
            if set(where) != {"document_id"}:
                raise ValueError("Cloud retrieval requires an explicit document filter.")
            from pilot.store import uid
            sql += " AND document_id=?"; args.append(uid(where["document_id"]))
        if ids is not None:
            ids = list(ids)
            if not ids: return sql+" AND FALSE", args
            sql += " AND id IN ("+",".join("?" for _ in ids)+")"; args.extend(ids)
        return sql,args

    def get(self, *, where=None, ids=None, include=None, limit=None, offset=0):
        sql,args = self._scope(where,ids)
        fields = {"documents","metadatas"} if include is None else set(include)
        columns = "id" + (",document" if "documents" in fields else "") + (",metadata" if "metadatas" in fields else "") + (",embedding::text AS embedding" if "embeddings" in fields else "")
        query = "SELECT "+columns+" FROM vectors WHERE "+sql+" ORDER BY id"
        if limit is not None:
            query += " LIMIT ? OFFSET ?"; args.extend([max(0,int(limit)),max(0,int(offset))])
        with cloud_db.db() as c:
            rows = c.execute(query,args).fetchall()
        result = {"ids":[r["id"] for r in rows]}
        for field,column in (("documents","document"),("metadatas","metadata"),("embeddings","embedding")):
            if field in fields:
                result[field] = [json.loads(r[column]) if field != "documents" else r[column] for r in rows]
        return result

    def upsert(self, *, ids, embeddings, documents, metadatas):
        if not len(ids) == len(embeddings) == len(documents) == len(metadatas):
            raise ValueError("Embedding batch lengths do not match.")
        from pilot.store import uid
        prepared = [(self.workspace,self.name,cid,uid(meta["document_id"]),vector_literal(vector,self.dimensions),text,json.dumps(meta,allow_nan=False))
                    for cid,vector,text,meta in zip(ids,embeddings,documents,metadatas)]
        with cloud_db.db() as c:
            self._authorize_writer(c)
            for values in prepared:
                c.execute("INSERT INTO vectors(workspace,collection,id,document_id,embedding,document,metadata) VALUES(?,?,?,?,?::extensions.vector,?,?) "
                          "ON CONFLICT(workspace,collection,id) DO UPDATE SET document_id=excluded.document_id,embedding=excluded.embedding,document=excluded.document,metadata=excluded.metadata", values)

    def query(self, *, query_embeddings, where, n_results, include=None):
        if not isinstance(where,dict) or set(where) != {"document_id"}:
            raise ValueError("Cloud retrieval requires an explicit document filter.")
        if len(query_embeddings) != 1 or not 1 <= int(n_results) <= 1000:
            raise ValueError("Use one query vector and between 1 and 1,000 results.")
        sql,args = self._scope(where)
        vector = vector_literal(query_embeddings[0],self.dimensions)
        with cloud_db.db() as c:
            rows = c.execute("SELECT id,metadata,document,embedding <=> ?::extensions.vector AS distance FROM vectors WHERE "+sql+" ORDER BY distance,id LIMIT ?", [vector,*args,int(n_results)]).fetchall()
        result = {"ids":[[r["id"] for r in rows]]}
        for field,column in (("metadatas","metadata"),("documents","document"),("distances","distance")):
            if field in (include or []):
                result[field] = [[json.loads(r[column]) if field=="metadatas" else r[column] for r in rows]]
        return result

    def delete(self, *, ids=None, where=None):
        if ids is None and where is None:
            raise ValueError("A deletion must specify source IDs or a document.")
        sql,args = self._scope(where,ids)
        with cloud_db.db() as c:
            self._authorize_writer(c)
            c.execute("DELETE FROM vectors WHERE "+sql,args)

    def _authorize_writer(self,c):
        from pilot.store import uid
        job,token=uid(os.getenv("CUSTOMER_JOB_ID","")),uid(os.getenv("CUSTOMER_JOB_TOKEN",""))
        if not c.execute("SELECT 1 FROM jobs j JOIN members m ON m.workspace=j.workspace AND m.principal=j.principal "
                         "WHERE j.id=? AND j.workspace=? AND j.run_token=? AND j.status='running' FOR UPDATE OF j",
                         (job,self.workspace,token)).fetchone():
            raise cloud_db.CloudStorageError("The request lost processing ownership or workspace access. No vectors were changed.",code="job_ownership_changed")
