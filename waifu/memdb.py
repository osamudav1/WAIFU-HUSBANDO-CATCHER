"""
waifu/memdb.py — Async in-memory MongoDB-compatible storage.

Supports:
  find_one / find / insert_one / update_one / update_many /
  delete_one / delete_many / count_documents / find_one_and_update /
  aggregate (basic: $match $sort $limit $skip $project $group $count)
  create_index / drop_index (no-ops)

Data is lost on restart (temporary / local memory only).
"""
from __future__ import annotations

import copy
import re
import uuid
from typing import Any


# ── helpers ──────────────────────────────────────────────────────────────────

def _new_id() -> str:
    return str(uuid.uuid4())


def _get_field(doc: dict, key: str) -> Any:
    """Support dot-notation field access."""
    parts = key.split(".")
    val = doc
    for part in parts:
        if not isinstance(val, dict):
            return None
        val = val.get(part)
    return val


def _set_field(doc: dict, key: str, value: Any) -> None:
    parts = key.split(".")
    d = doc
    for part in parts[:-1]:
        d = d.setdefault(part, {})
    d[parts[-1]] = value


def _match(doc: dict, filt: dict) -> bool:
    """Evaluate a MongoDB-style filter against a document."""
    for key, cond in filt.items():
        if key == "$or":
            if not any(_match(doc, sub) for sub in cond):
                return False
            continue
        if key == "$and":
            if not all(_match(doc, sub) for sub in cond):
                return False
            continue
        if key == "$nor":
            if any(_match(doc, sub) for sub in cond):
                return False
            continue

        val = _get_field(doc, key)

        if isinstance(cond, dict) and any(k.startswith("$") for k in cond):
            for op, operand in cond.items():
                if op == "$eq"  and val != operand:                return False
                if op == "$ne"  and val == operand:                return False
                if op == "$gt"  and (val is None or val <= operand): return False
                if op == "$gte" and (val is None or val <  operand): return False
                if op == "$lt"  and (val is None or val >= operand): return False
                if op == "$lte" and (val is None or val >  operand): return False
                if op == "$in"  and val not in operand:            return False
                if op == "$nin" and val in operand:                return False
                if op == "$exists":
                    exists = val is not None
                    if operand and not exists:   return False
                    if not operand and exists:   return False
                if op == "$regex":
                    if not isinstance(val, str): return False
                    flags = re.IGNORECASE if cond.get("$options", "") == "i" else 0
                    if not re.search(operand, val, flags): return False
                if op == "$elemMatch":
                    if not isinstance(val, list): return False
                    if not any(_match(e if isinstance(e, dict) else {"v": e}, operand)
                               for e in val): return False
                if op == "$size":
                    if not isinstance(val, list) or len(val) != operand: return False
        else:
            if isinstance(cond, dict):
                if not isinstance(val, dict) or not _match(val, cond):
                    return False
            elif isinstance(val, list):
                if cond not in val:
                    return False
            else:
                if val != cond:
                    return False
    return True


def _apply_update(doc: dict, update: dict) -> dict:
    doc = copy.deepcopy(doc)
    for op, fields in update.items():
        if op == "$set":
            for k, v in fields.items():
                _set_field(doc, k, v)

        elif op == "$unset":
            for k in (fields if isinstance(fields, list) else fields.keys()):
                parts = k.split(".")
                d = doc
                for part in parts[:-1]:
                    d = d.get(part, {}) if isinstance(d, dict) else {}
                if isinstance(d, dict):
                    d.pop(parts[-1], None)

        elif op == "$inc":
            for k, v in fields.items():
                old = _get_field(doc, k) or 0
                _set_field(doc, k, old + v)

        elif op == "$push":
            for k, v in fields.items():
                lst = _get_field(doc, k)
                if not isinstance(lst, list):
                    lst = []
                    _set_field(doc, k, lst)
                if isinstance(v, dict) and "$each" in v:
                    lst.extend(v["$each"])
                else:
                    lst.append(v)

        elif op == "$pull":
            for k, v in fields.items():
                lst = _get_field(doc, k)
                if not isinstance(lst, list):
                    continue
                if isinstance(v, dict):
                    new_lst = [x for x in lst
                               if not _match(x if isinstance(x, dict) else {}, v)]
                else:
                    new_lst = [x for x in lst if x != v]
                _set_field(doc, k, new_lst)

        elif op == "$addToSet":
            for k, v in fields.items():
                lst = _get_field(doc, k)
                if not isinstance(lst, list):
                    lst = []
                    _set_field(doc, k, lst)
                if v not in lst:
                    lst.append(v)

        elif op == "$pop":
            for k, v in fields.items():
                lst = _get_field(doc, k)
                if isinstance(lst, list) and lst:
                    if v == 1:
                        lst.pop()
                    else:
                        lst.pop(0)
    return doc


# ── result stubs ──────────────────────────────────────────────────────────────

class InsertOneResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id

class InsertManyResult:
    def __init__(self, inserted_ids):
        self.inserted_ids = inserted_ids

class UpdateResult:
    def __init__(self, matched_count=0, modified_count=0, upserted_id=None):
        self.matched_count  = matched_count
        self.modified_count = modified_count
        self.upserted_id    = upserted_id

class DeleteResult:
    def __init__(self, deleted_count=0):
        self.deleted_count = deleted_count


# ── cursor ────────────────────────────────────────────────────────────────────

class MemCursor:
    def __init__(self, docs: list[dict]):
        self._docs      = docs
        self._sort_spec: list[tuple] = []
        self._limit_n:  int | None   = None
        self._skip_n:   int          = 0

    def sort(self, key_or_list, direction=1):
        if isinstance(key_or_list, list):
            self._sort_spec = key_or_list
        else:
            self._sort_spec = [(key_or_list, direction)]
        return self

    def limit(self, n: int):
        self._limit_n = n
        return self

    def skip(self, n: int):
        self._skip_n = n
        return self

    def _resolve(self) -> list[dict]:
        docs = list(self._docs)
        for key, direction in reversed(self._sort_spec):
            reverse = direction == -1
            docs.sort(key=lambda d: (_get_field(d, key) is None,
                                      _get_field(d, key)),
                      reverse=reverse)
        docs = docs[self._skip_n:]
        if self._limit_n is not None:
            docs = docs[:self._limit_n]
        return docs

    def __aiter__(self):
        self._iter_list = iter(self._resolve())
        return self

    async def __anext__(self):
        try:
            return next(self._iter_list)
        except StopIteration:
            raise StopAsyncIteration

    async def to_list(self, length: int | None = None) -> list[dict]:
        docs = self._resolve()
        return docs[:length] if length is not None else docs


# ── aggregate pipeline ────────────────────────────────────────────────────────

def _resolve_expr(expr, doc: dict) -> Any:
    if isinstance(expr, str) and expr.startswith("$"):
        return _get_field(doc, expr[1:])
    if isinstance(expr, dict):
        for op, arg in expr.items():
            if op == "$sum":
                if isinstance(arg, list):
                    return sum(_resolve_expr(a, doc) or 0 for a in arg)
                return _resolve_expr(arg, doc) or 0
            if op == "$avg":
                vals = [_resolve_expr(a, doc) for a in (arg if isinstance(arg, list) else [arg])]
                vals = [v for v in vals if v is not None]
                return sum(vals) / len(vals) if vals else None
            if op == "$first": return _resolve_expr(arg, doc)
            if op == "$last":  return _resolve_expr(arg, doc)
            if op == "$max":
                vals = [_resolve_expr(a, doc) for a in (arg if isinstance(arg, list) else [arg])]
                return max((v for v in vals if v is not None), default=None)
            if op == "$min":
                vals = [_resolve_expr(a, doc) for a in (arg if isinstance(arg, list) else [arg])]
                return min((v for v in vals if v is not None), default=None)
            if op == "$size":
                v = _resolve_expr(arg, doc)
                return len(v) if isinstance(v, list) else 0
            if op == "$ifNull":
                v = _resolve_expr(arg[0], doc)
                return v if v is not None else _resolve_expr(arg[1], doc)
            if op == "$cond":
                cond_val = _resolve_expr(arg[0] if isinstance(arg, list) else arg.get("if"), doc)
                then_br  = arg[1] if isinstance(arg, list) else arg.get("then")
                else_br  = arg[2] if isinstance(arg, list) else arg.get("else")
                return _resolve_expr(then_br if cond_val else else_br, doc)
    return expr


def _run_pipeline(docs: list[dict], pipeline: list[dict]) -> list[dict]:
    result = list(docs)
    for stage in pipeline:
        for op, arg in stage.items():
            if op == "$match":
                result = [d for d in result if _match(d, arg)]

            elif op == "$sort":
                for key in reversed(list(arg.keys())):
                    reverse = arg[key] == -1
                    result.sort(key=lambda d: (_get_field(d, key) is None,
                                               _get_field(d, key)),
                                reverse=reverse)

            elif op == "$limit":
                result = result[:arg]

            elif op == "$skip":
                result = result[arg:]

            elif op == "$project":
                new_result = []
                for doc in result:
                    new_doc = {}
                    include_id = arg.get("_id", 1)
                    if include_id:
                        new_doc["_id"] = doc.get("_id")
                    for field, spec in arg.items():
                        if field == "_id":
                            continue
                        if spec == 0:
                            continue
                        elif spec == 1:
                            val = _get_field(doc, field)
                            if val is not None:
                                _set_field(new_doc, field, val)
                        else:
                            _set_field(new_doc, field, _resolve_expr(spec, doc))
                    new_result.append(new_doc)
                result = new_result

            elif op == "$group":
                groups: dict[Any, dict] = {}
                id_expr = arg["_id"]
                for doc in result:
                    if isinstance(id_expr, str) and id_expr.startswith("$"):
                        gid = _get_field(doc, id_expr[1:])
                    elif isinstance(id_expr, dict):
                        gid = tuple(
                            (_resolve_expr(v, doc)) for v in id_expr.values()
                        )
                    else:
                        gid = id_expr

                    key = str(gid)
                    if key not in groups:
                        groups[key] = {"_id": gid}
                        for agg_field, agg_expr in arg.items():
                            if agg_field == "_id": continue
                            op2 = list(agg_expr.keys())[0]
                            if op2 in ("$sum", "$avg", "$max", "$min"):
                                groups[key][agg_field] = [] if op2 in ("$avg",) else 0
                            else:
                                groups[key][agg_field] = None

                    for agg_field, agg_expr in arg.items():
                        if agg_field == "_id": continue
                        op2, sub = list(agg_expr.items())[0]
                        val = _resolve_expr(sub, doc)
                        if op2 == "$sum":
                            groups[key][agg_field] = (groups[key][agg_field] or 0) + (val or 0)
                        elif op2 == "$avg":
                            if val is not None:
                                groups[key].setdefault(f"__avg_{agg_field}", []).append(val)
                        elif op2 == "$max":
                            cur = groups[key][agg_field]
                            if val is not None and (cur is None or val > cur):
                                groups[key][agg_field] = val
                        elif op2 == "$min":
                            cur = groups[key][agg_field]
                            if val is not None and (cur is None or val < cur):
                                groups[key][agg_field] = val
                        elif op2 == "$first":
                            if groups[key][agg_field] is None:
                                groups[key][agg_field] = val
                        elif op2 == "$last":
                            groups[key][agg_field] = val
                        elif op2 == "$push":
                            if not isinstance(groups[key].get(agg_field), list):
                                groups[key][agg_field] = []
                            groups[key][agg_field].append(val)
                        elif op2 == "$addToSet":
                            if not isinstance(groups[key].get(agg_field), list):
                                groups[key][agg_field] = []
                            if val not in groups[key][agg_field]:
                                groups[key][agg_field].append(val)

                # finalize averages
                for gd in groups.values():
                    for k in list(gd.keys()):
                        if k.startswith("__avg_"):
                            field = k[6:]
                            vals = gd.pop(k)
                            gd[field] = sum(vals) / len(vals) if vals else None

                result = list(groups.values())

            elif op == "$count":
                result = [{arg: len(result)}]

            elif op == "$addFields" or op == "$set":
                new_result = []
                for doc in result:
                    doc = copy.deepcopy(doc)
                    for field, expr in arg.items():
                        _set_field(doc, field, _resolve_expr(expr, doc))
                    new_result.append(doc)
                result = new_result

            elif op == "$unwind":
                path = arg if isinstance(arg, str) else arg.get("path", "")
                field = path.lstrip("$")
                new_result = []
                for doc in result:
                    items = _get_field(doc, field)
                    if isinstance(items, list):
                        for item in items:
                            new_doc = copy.deepcopy(doc)
                            _set_field(new_doc, field, item)
                            new_result.append(new_doc)
                    elif items is not None:
                        new_result.append(doc)
                result = new_result

    return result


# ── collection ────────────────────────────────────────────────────────────────

class MemCollection:
    def __init__(self, name: str):
        self.name   = name
        self._store: dict[str, dict] = {}

    def _all(self) -> list[dict]:
        return [copy.deepcopy(d) for d in self._store.values()]

    async def find_one(self, filt: dict = None, *args, **kwargs) -> dict | None:
        filt = filt or {}
        for doc in self._store.values():
            if _match(doc, filt):
                return copy.deepcopy(doc)
        return None

    def find(self, filt: dict = None, *args, **kwargs) -> MemCursor:
        filt = filt or {}
        matched = [copy.deepcopy(d) for d in self._store.values() if _match(d, filt)]
        return MemCursor(matched)

    async def insert_one(self, document: dict) -> InsertOneResult:
        doc = copy.deepcopy(document)
        if "_id" not in doc:
            doc["_id"] = _new_id()
        self._store[str(doc["_id"])] = doc
        return InsertOneResult(doc["_id"])

    async def insert_many(self, documents: list) -> "InsertManyResult":
        ids = []
        for document in documents:
            doc = copy.deepcopy(document)
            if "_id" not in doc:
                doc["_id"] = _new_id()
            self._store[str(doc["_id"])] = doc
            ids.append(doc["_id"])
        return InsertManyResult(ids)

    async def update_one(self, filt: dict, update: dict,
                         upsert: bool = False) -> UpdateResult:
        for key, doc in self._store.items():
            if _match(doc, filt):
                self._store[key] = _apply_update(doc, update)
                return UpdateResult(1, 1)
        if upsert:
            new_doc: dict = {}
            for k, v in filt.items():
                if not k.startswith("$"):
                    _set_field(new_doc, k, v)
            new_doc = _apply_update(new_doc, update)
            if "_id" not in new_doc:
                new_doc["_id"] = _new_id()
            self._store[str(new_doc["_id"])] = new_doc
            return UpdateResult(0, 0, upserted_id=new_doc["_id"])
        return UpdateResult(0, 0)

    async def update_many(self, filt: dict, update: dict) -> UpdateResult:
        matched = modified = 0
        for key, doc in list(self._store.items()):
            if _match(doc, filt):
                self._store[key] = _apply_update(doc, update)
                matched += 1
                modified += 1
        return UpdateResult(matched, modified)

    async def delete_one(self, filt: dict) -> DeleteResult:
        for key, doc in list(self._store.items()):
            if _match(doc, filt):
                del self._store[key]
                return DeleteResult(1)
        return DeleteResult(0)

    async def delete_many(self, filt: dict) -> DeleteResult:
        to_del = [k for k, d in self._store.items() if _match(d, filt)]
        for k in to_del:
            del self._store[k]
        return DeleteResult(len(to_del))

    async def count_documents(self, filt: dict = None) -> int:
        filt = filt or {}
        return sum(1 for d in self._store.values() if _match(d, filt))

    async def find_one_and_update(self, filt: dict, update: dict,
                                   upsert: bool = False,
                                   return_document=None) -> dict | None:
        for key, doc in list(self._store.items()):
            if _match(doc, filt):
                old = copy.deepcopy(doc)
                self._store[key] = _apply_update(doc, update)
                return old
        if upsert:
            await self.update_one(filt, update, upsert=True)
        return None

    def aggregate(self, pipeline: list[dict]) -> MemCursor:
        result = _run_pipeline(self._all(), pipeline)
        return MemCursor(result)

    async def create_index(self, *args, **kwargs):
        pass

    async def drop_index(self, *args, **kwargs):
        pass

    async def drop(self):
        self._store.clear()

    async def estimated_document_count(self) -> int:
        return len(self._store)


# ── database ──────────────────────────────────────────────────────────────────

class MemDatabase:
    """In-memory database — collections created on first access."""

    def __init__(self):
        self._cols: dict[str, MemCollection] = {}

    def _col(self, name: str) -> MemCollection:
        if name not in self._cols:
            self._cols[name] = MemCollection(name)
        return self._cols[name]

    def __getitem__(self, name: str) -> MemCollection:
        return self._col(name)

    def __getattr__(self, name: str) -> MemCollection:
        if name.startswith("_"):
            raise AttributeError(name)
        return self._col(name)

    def get_collection(self, name: str) -> MemCollection:
        return self._col(name)


# ── FallbackCollection ────────────────────────────────────────────────────────

import logging as _logging
_FLOG = _logging.getLogger("waifu.memdb")

_QUOTA_KEYWORDS = (
    "quota", "exceeded", "storage limit", "free tier",
    "too many", "out of space", "disk", "errmsg", "full",
    "8000", "code: 8000",
)


def _is_quota_err(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(k in msg for k in _QUOTA_KEYWORDS)


class FallbackCollection:
    """
    Wraps a Motor (MongoDB) collection with an in-memory fallback.

    Normal mode  → all operations go to MongoDB.
    Fallback mode → triggered when any WRITE fails with a quota/storage error.
                    Writes go to in-memory; reads still try MongoDB first.
    """

    def __init__(self, motor_col, name: str):
        self._db   = motor_col
        self._mem  = MemCollection(name)
        self._name = name
        self._fallback = False   # True once quota exceeded

    def _warn_switch(self, exc: Exception) -> None:
        self._fallback = True
        _FLOG.warning(
            "⚠️  MongoDB quota exceeded on '%s' — switching to in-memory fallback. "
            "Data added now is temporary (lost on restart). Error: %s",
            self._name, exc,
        )

    # ── reads (try mongo first, then mem) ─────────────────────────────────────

    async def find_one(self, filt=None, *args, **kwargs):
        try:
            result = await self._db.find_one(filt or {}, *args, **kwargs)
            if result is not None:
                return result
            # also check mem (data written there during fallback)
            return await self._mem.find_one(filt or {})
        except Exception:
            return await self._mem.find_one(filt or {})

    def find(self, filt=None, *args, **kwargs):
        # Return a cursor that merges both sources
        return _MergedCursor(self._db, self._mem, filt or {})

    async def count_documents(self, filt=None) -> int:
        filt = filt or {}
        try:
            n = await self._db.count_documents(filt)
        except Exception:
            n = 0
        n += await self._mem.count_documents(filt)
        return n

    def aggregate(self, pipeline):
        return self._mem.aggregate(pipeline)   # mem only (lightweight stats)

    # ── writes ────────────────────────────────────────────────────────────────

    async def insert_one(self, document):
        if self._fallback:
            return await self._mem.insert_one(document)
        try:
            return await self._db.insert_one(document)
        except Exception as exc:
            if _is_quota_err(exc):
                self._warn_switch(exc)
                return await self._mem.insert_one(document)
            raise

    async def insert_many(self, documents):
        if self._fallback:
            return await self._mem.insert_many(documents)
        try:
            return await self._db.insert_many(documents)
        except Exception as exc:
            if _is_quota_err(exc):
                self._warn_switch(exc)
                return await self._mem.insert_many(documents)
            raise

    async def update_one(self, filt, update, upsert=False):
        if self._fallback:
            return await self._mem.update_one(filt, update, upsert=upsert)
        try:
            return await self._db.update_one(filt, update, upsert=upsert)
        except Exception as exc:
            if _is_quota_err(exc):
                self._warn_switch(exc)
                return await self._mem.update_one(filt, update, upsert=upsert)
            raise

    async def update_many(self, filt, update):
        if self._fallback:
            return await self._mem.update_many(filt, update)
        try:
            return await self._db.update_many(filt, update)
        except Exception as exc:
            if _is_quota_err(exc):
                self._warn_switch(exc)
                return await self._mem.update_many(filt, update)
            raise

    async def delete_one(self, filt):
        if self._fallback:
            return await self._mem.delete_one(filt)
        try:
            return await self._db.delete_one(filt)
        except Exception as exc:
            if _is_quota_err(exc):
                self._warn_switch(exc)
                return await self._mem.delete_one(filt)
            raise

    async def delete_many(self, filt):
        if self._fallback:
            return await self._mem.delete_many(filt)
        try:
            return await self._db.delete_many(filt)
        except Exception as exc:
            if _is_quota_err(exc):
                self._warn_switch(exc)
                return await self._mem.delete_many(filt)
            raise

    async def find_one_and_update(self, filt, update, upsert=False, **kw):
        if self._fallback:
            return await self._mem.find_one_and_update(filt, update, upsert=upsert)
        try:
            return await self._db.find_one_and_update(filt, update, upsert=upsert, **kw)
        except Exception as exc:
            if _is_quota_err(exc):
                self._warn_switch(exc)
                return await self._mem.find_one_and_update(filt, update, upsert=upsert)
            raise

    async def create_index(self, *args, **kwargs):
        try:
            await self._db.create_index(*args, **kwargs)
        except Exception:
            pass

    async def drop_index(self, *args, **kwargs):
        try:
            await self._db.drop_index(*args, **kwargs)
        except Exception:
            pass

    async def estimated_document_count(self):
        try:
            return await self._db.estimated_document_count()
        except Exception:
            return await self._mem.estimated_document_count()


class _MergedCursor:
    """Cursor that merges results from MongoDB + in-memory fallback."""

    def __init__(self, motor_col, mem_col, filt):
        self._db  = motor_col
        self._mem = mem_col
        self._filt = filt
        self._sort_spec = []
        self._limit_n = None
        self._skip_n  = 0

    def sort(self, key_or_list, direction=1):
        if isinstance(key_or_list, list):
            self._sort_spec = key_or_list
        else:
            self._sort_spec = [(key_or_list, direction)]
        return self

    def limit(self, n):
        self._limit_n = n
        return self

    def skip(self, n):
        self._skip_n = n
        return self

    async def to_list(self, length=None):
        docs = []
        try:
            cur = self._db.find(self._filt)
            if self._sort_spec:
                cur = cur.sort(self._sort_spec)
            docs = await cur.to_list(length=length or 10_000)
        except Exception:
            pass
        mem_docs = await self._mem.find(self._filt).to_list()
        # merge (no duplicates by _id)
        seen = {str(d.get("_id")) for d in docs}
        for d in mem_docs:
            if str(d.get("_id")) not in seen:
                docs.append(d)
        docs = docs[self._skip_n:]
        if self._limit_n:
            docs = docs[:self._limit_n]
        if length:
            docs = docs[:length]
        return docs

    def __aiter__(self):
        self._iter_coro = self.to_list()
        self._iter_list = None
        return self

    async def __anext__(self):
        if self._iter_list is None:
            self._iter_list = iter(await self._iter_coro)
        try:
            return next(self._iter_list)
        except StopIteration:
            raise StopAsyncIteration
