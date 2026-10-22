# -*- coding: utf-8 -*-
"""
知识库 embedding 一致性测试。

核心主张：**换了 embedding 模型就绝不能再往同一个索引里写/查**。
旧实现完全不记模型，也不校验 —— 换模型后只会得到"看起来正常、其实错乱"的检索结果，
或者一个维度不匹配的裸异常。这里锁住三道闸门：
  1) meta.json 记录 {model, dim}
  2) 维度以**索引里的真实向量**为准（meta 丢了也拦得住）
  3) 模型名以 meta 为准（同维度不同模型，只有名字能发现）
以及查询/文档侧的前缀差异（bge 中文 v1.5 不加，v1.0 加）。

不联网、不加载真实 embedding 模型：_get_embedder / embed_signature / _encode 全部打桩。
"""
import unittest
from pathlib import Path
from unittest import mock

import kb



# ---- 共享状态守卫（见 _guards.py）----
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _guards import snapshot_shared_state as _snap, \
    assert_shared_state_unchanged as _assert_same

_SHARED_BEFORE = _snap()


def tearDownModule():
    _assert_same(_SHARED_BEFORE, __name__)

class TestQueryPrefix(unittest.TestCase):
    def test_bge_zh_v15_has_no_prefix(self):
        self.assertEqual(kb._query_prefix("BAAI/bge-small-zh-v1.5"), "")
        self.assertEqual(kb._query_prefix("/opt/models/bge-large-zh-v1.5"), "")

    def test_bge_zh_v10_uses_instruction(self):
        p = kb._query_prefix("/opt/models/bge-large-zh")
        self.assertIn("为这个句子生成表示", p)

    def test_e5_uses_query_prefix(self):
        self.assertEqual(kb._query_prefix("intfloat/multilingual-e5-small"), "query: ")

    def test_unknown_model_gets_no_prefix(self):
        """不认识的模型一律不加前缀 —— 宁可保持现状也不猜。"""
        self.assertEqual(kb._query_prefix("some/unknown-model"), "")


class TestEncodePrefix(unittest.TestCase):
    def _encode_with(self, model_ref, texts, is_query):
        embedder = mock.Mock()
        embedder.encode.return_value = mock.Mock(tolist=lambda: [[0.1, 0.2]])
        with mock.patch.object(kb, "_get_embedder", return_value=embedder), \
             mock.patch.object(kb, "_embedder_ref", model_ref):
            kb._encode(texts, is_query=is_query)
        return embedder.encode.call_args.args[0]

    def test_query_gets_prefix_but_document_does_not(self):
        doc_ref, q_ref = "BAAI/bge-small-zh-v1.5", "/m/bge-large-zh"
        self.assertEqual(self._encode_with(doc_ref, ["正文"], False), ["正文"])
        sent = self._encode_with(q_ref, ["问题"], True)
        self.assertEqual(len(sent), 1)
        self.assertTrue(sent[0].endswith("问题"))
        self.assertNotEqual(sent[0], "问题")

    def test_v15_query_unmodified(self):
        self.assertEqual(
            self._encode_with("BAAI/bge-small-zh-v1.5", ["问题"], True), ["问题"])


class TestEmbedSignature(unittest.TestCase):
    def _sig(self, model_ref, dim=512):
        embedder = mock.Mock()
        embedder.get_sentence_embedding_dimension.return_value = dim
        with mock.patch.object(kb, "_get_embedder", return_value=embedder), \
             mock.patch.object(kb, "_embedder_ref", model_ref):
            return kb.embed_signature()

    def test_local_path_and_hf_name_normalize_to_same_model(self):
        """本地路径与 HF 名必须得到同一个标识，否则换来源会被误判成换模型。"""
        a = self._sig("/opt/models/bge-small-zh-v1.5")
        b = self._sig("BAAI/bge-small-zh-v1.5")
        self.assertEqual(a, b)
        self.assertEqual(a, {"model": "bge-small-zh-v1.5", "dim": 512})

    def test_dim_read_failure_degrades_to_none(self):
        embedder = mock.Mock()
        embedder.get_sentence_embedding_dimension.side_effect = RuntimeError("nope")
        with mock.patch.object(kb, "_get_embedder", return_value=embedder), \
             mock.patch.object(kb, "_embedder_ref", "/m/bge-small-zh-v1.5"):
            self.assertIsNone(kb.embed_signature()["dim"])


class TestEmbeddingGate(unittest.TestCase):
    """check_embedding_consistency 是写入与查询共用的唯一闸门。"""

    def _collection(self, dim):
        c = mock.Mock()
        c.get.return_value = {"embeddings": [[0.0] * dim] if dim else []}
        return c

    def _check(self, now, meta, collection):
        with mock.patch.object(kb, "embed_signature", return_value=now):
            return kb.check_embedding_consistency("kb1", meta, collection)

    NOW = {"model": "bge-small-zh-v1.5", "dim": 512}

    def test_ok_when_everything_matches(self):
        meta = {"embedding": dict(self.NOW)}
        ok, why = self._check(self.NOW, meta, self._collection(512))
        self.assertTrue(ok)
        self.assertIsNone(why)

    def test_dim_mismatch_is_rejected(self):
        ok, why = self._check(self.NOW, {}, self._collection(768))
        self.assertFalse(ok)
        self.assertIn("768", why)
        self.assertIn("512", why)

    def test_dim_check_works_even_if_meta_lost(self):
        """meta.json 丢了也必须拦得住 —— 维度是从索引里的真实向量读的。"""
        ok, why = self._check(self.NOW, {}, self._collection(1024))
        self.assertFalse(ok)
        self.assertIn("1024", why)

    def test_same_dim_different_model_is_rejected(self):
        """512 维换 512 维：维度查不出来，只能靠记录的名字。"""
        meta = {"embedding": {"model": "bge-base-zh-v1.5", "dim": 512}}
        ok, why = self._check(self.NOW, meta, self._collection(512))
        self.assertFalse(ok)
        self.assertIn("bge-base-zh-v1.5", why)

    def test_empty_or_missing_collection_is_not_blocked(self):
        for coll in (None, self._collection(0)):
            ok, why = self._check(self.NOW, {}, coll)
            self.assertTrue(ok, why)

    def test_legacy_kb_without_record_is_not_blocked(self):
        """老库（改之前建的，没有 embedding 字段）不能被拦死 —— 只在下次写入时补记。"""
        ok, why = self._check(self.NOW, {}, self._collection(512))
        self.assertTrue(ok, why)

    def test_record_missing_dim_does_not_crash(self):
        ok, why = self._check({"model": "bge-small-zh-v1.5", "dim": None},
                              {"embedding": {"model": "x", "dim": None}}, None)
        self.assertFalse(ok)


class TestQueryKbGate(unittest.TestCase):
    def test_query_rejects_model_change_instead_of_returning_garbage(self):
        collection = mock.Mock()
        collection.get.return_value = {"embeddings": [[0.0] * 512]}
        client = mock.Mock()
        client.get_collection.return_value = collection
        meta = {"name": "kb1", "embedding": {"model": "bge-small-zh-v1.5", "dim": 512}}
        with mock.patch.object(kb, "get_kb", return_value=meta), \
             mock.patch.object(kb, "_get_chroma", return_value=client), \
             mock.patch.object(kb, "embed_signature",
                               return_value={"model": "bge-base-zh-v1.5", "dim": 512}):
            with self.assertRaises(ValueError) as cm:
                kb.query_kb("kb1", "问题")
        self.assertIn("bge-base-zh-v1.5", str(cm.exception))
        self.assertIn("bge-small-zh-v1.5", str(cm.exception))

    def test_query_missing_kb_still_raises_its_own_error(self):
        with mock.patch.object(kb, "get_kb", return_value=None):
            with self.assertRaises(ValueError) as cm:
                kb.query_kb("nope", "问题")
        self.assertIn("不存在", str(cm.exception))


class TestIngestGate(unittest.TestCase):
    def _add(self, meta, collection, signature):
        client = mock.Mock()
        client.get_collection.return_value = collection
        saved = {}
        with mock.patch.object(kb, "get_kb", return_value=meta), \
             mock.patch.object(kb, "_get_chroma", return_value=client), \
             mock.patch.object(kb, "_extract_file_text", return_value="正文" * 400), \
             mock.patch.object(kb, "_encode", return_value=[[0.0] * 512]), \
             mock.patch.object(kb, "embed_signature", return_value=signature), \
             mock.patch.object(kb, "_save_kb_meta",
                               side_effect=lambda n, m: saved.update(m)):
            kb.add_documents_to_kb("kb1", ["/tmp/x.txt"])
        return saved

    def _collection(self, dim):
        c = mock.Mock()
        c.get.return_value = {"embeddings": [[0.0] * dim] if dim else []}
        return c

    def test_ingest_records_signature(self):
        """首次写入把 {model, dim} 记进 meta —— 这是后续所有校验的依据。"""
        sig = {"model": "bge-small-zh-v1.5", "dim": 512}
        saved = self._add({"name": "kb1", "documents": [], "total_chunks": 0},
                          self._collection(0), sig)
        self.assertEqual(saved.get("embedding"), sig)

    def test_ingest_rejects_dim_change(self):
        sig = {"model": "bge-small-zh-v1.5", "dim": 512}
        with self.assertRaises(ValueError) as cm:
            self._add({"name": "kb1", "documents": [], "total_chunks": 0},
                      self._collection(768), sig)
        self.assertIn("768", str(cm.exception))
        self.assertIn("重建索引", str(cm.exception))


class TestQueryDiagnostics(unittest.TestCase):
    """
    **"0 条结果"必须能说清是为什么。**

    实测背景（2026-10-06）：中文 query 打英文语料，bge-small-zh-v1.5 给的是**负分**
    （-0.028），被 min_score 全部丢掉；旧实现只返回空列表，日志里看着像"这个库是空的"。
    """

    def _run(self, scores, query="hello", min_score=None, corpus_lang=None,
             wrapper=False, space="cosine"):
        """space="cosine" 时 distance 与 score 一一对应，便于表达既有断言；
        space="l2" 用来复现并锁住那个真 bug（score ≠ 1-dist）。"""
        n = len(scores)
        coll = mock.Mock()
        coll.configuration = {"hnsw": {"space": space}}
        coll.query.return_value = {
            "documents": [[("chunk %d" % i) for i in range(n)]],
            "metadatas": [[{"source": "s", "chunk_idx": i} for i in range(n)]],
            "distances": [[1 - s for s in scores]],      # Chroma 给的是 distance
        }
        coll.get.return_value = {
            "documents": {"en": ["english text " * 40],
                          "zh": ["中文语料内容 " * 40]}.get(corpus_lang, [])}
        client = mock.Mock()
        client.get_collection.return_value = coll
        meta = {"name": "kb1", "total_chunks": n}
        with mock.patch.object(kb, "get_kb", return_value=meta), \
             mock.patch.object(kb, "_get_chroma", return_value=client), \
             mock.patch.object(kb, "check_embedding_consistency", return_value=(True, None)), \
             mock.patch.object(kb, "_encode", return_value=[[0.0] * 512]):
            kb._corpus_lang_cache.clear()
            if wrapper:
                return kb.query_kb("kb1", query, top_k=max(n, 1), min_score=min_score)
            return kb.query_kb_detailed("kb1", query, top_k=max(n, 1), min_score=min_score)

    # ---- 正常路径 ----

    def test_partial_kept_has_no_hint(self):
        res, d = self._run([0.5, -0.05])
        self.assertEqual(len(res), 1)
        self.assertEqual((d["candidates"], d["kept"], d["dropped"]), (2, 1, 1))
        self.assertIsNone(d["hint"])

    def test_min_score_default_is_module_constant(self):
        _, d = self._run([0.5])
        self.assertEqual(d["min_score"], kb.DEFAULT_MIN_SCORE)

    def test_query_kb_wrapper_still_returns_plain_list(self):
        """旧调用方（lora_manager 注入 kb 上下文的两处）必须继续拿到 list。"""
        out = self._run([0.5], wrapper=True)
        self.assertIsInstance(out, list)
        self.assertEqual(len(out), 1)
        self.assertIn("text", out[0])

    # ---- 0 条结果：说清原因 ----

    def test_all_dropped_reports_counts_and_best_score(self):
        res, d = self._run([-0.028, -0.051, -0.069],
                           query="注意力机制解决了什么问题", corpus_lang="en")
        self.assertEqual(res, [])
        self.assertEqual((d["candidates"], d["kept"], d["dropped"]), (3, 0, 3))
        self.assertEqual(d["best_score"], -0.028)
        self.assertIn("3 个候选", d["hint"])
        self.assertIn("低于阈值", d["hint"])

    def test_language_mismatch_hint_is_actionable(self):
        """跨语言时的提示必须能照做，而不是一句"没搜到"。"""
        _, d = self._run([-0.028], query="注意力机制解决了什么问题", corpus_lang="en")
        self.assertTrue(d["lang_mismatch"])
        self.assertEqual((d["query_lang"], d["corpus_lang"]), ("zh", "en"))
        for token in ("中文", "英文", "query_template", "rebuild_kb_index", "min_score"):
            self.assertIn(token, d["hint"])
        # 不再宣称"跨语言必然出负分"——那是 distance 公式用错造成的假象（见坑 26）
        self.assertNotIn("负分是", d["hint"])

    def test_same_language_hint_points_elsewhere(self):
        _, d = self._run([-0.02], query="what is attention", corpus_lang="en")
        self.assertFalse(d["lang_mismatch"])
        self.assertIn("主题不相关", d["hint"])

    def test_unknown_language_is_stated_not_guessed(self):
        _, d = self._run([-0.02], query="what is attention", corpus_lang=None)
        self.assertEqual(d["corpus_lang"], "unknown")
        self.assertIn("无法判断", d["hint"])

    def test_empty_index_hint(self):
        res, d = self._run([])
        self.assertEqual(res, [])
        self.assertEqual(d["candidates"], 0)
        self.assertIsNone(d["best_score"])
        self.assertIn("没有任何向量", d["hint"])

    def test_diagnostics_do_not_break_retrieval_when_corpus_unreadable(self):
        """语料语言探测失败不能把检索搞崩 —— 诊断只是附加信息。"""
        n = 1
        coll = mock.Mock()
        coll.query.return_value = {"documents": [["t"]], "metadatas": [[{}]],
                                   "distances": [[1.05]]}
        coll.get.side_effect = RuntimeError("chroma 炸了")
        client = mock.Mock()
        client.get_collection.return_value = coll
        with mock.patch.object(kb, "get_kb", return_value={"name": "kb1", "total_chunks": n}), \
             mock.patch.object(kb, "_get_chroma", return_value=client), \
             mock.patch.object(kb, "check_embedding_consistency", return_value=(True, None)), \
             mock.patch.object(kb, "_encode", return_value=[[0.0] * 512]):
            kb._corpus_lang_cache.clear()
            res, d = kb.query_kb_detailed("kb1", "hello")
        self.assertEqual(d["corpus_lang"], "unknown")
        self.assertIsNotNone(d["hint"])

    # ---- 阈值旋钮 ----

    def test_min_score_override_lets_low_scores_through(self):
        """旋钮必须真的有效：默认丢掉，显式调低就放行（风险由调用方承担）。"""
        _, d1 = self._run([-0.05])
        self.assertEqual(d1["kept"], 0)
        res2, d2 = self._run([-0.05], min_score=-0.2)
        self.assertEqual(len(res2), 1)
        self.assertEqual(d2["min_score"], -0.2)
        self.assertIsNone(d2["hint"])


class TestDistanceConversion(unittest.TestCase):
    """
    **本次真正的 bug 就在这里。**

    集合是 Chroma 默认的 `hnsw:space=l2`，而归一化向量的 L2² = 2 - 2·cos
    ⇒ 真实余弦 = 1 - dist/2。旧代码一律用 `1 - dist`，在 l2 集合上算出来的是
    `2cos - 1`：真实余弦 0.49 被报成 -0.03，再被 min_score 过滤掉 ——
    检索明明命中了，却"看起来什么都没搜到"。
    """

    def test_l2_uses_half_distance(self):
        # 实测数据：dist=1.0284 → 真实余弦 0.4858
        self.assertAlmostEqual(kb._distance_to_score(1.0284, "l2"), 0.4858, places=4)
        self.assertAlmostEqual(kb._distance_to_score(0.6528, "l2"), 0.6736, places=4)

    def test_cosine_space_uses_full_distance(self):
        self.assertAlmostEqual(kb._distance_to_score(0.3, "cosine"), 0.7, places=6)

    def test_none_distance_passes_through(self):
        self.assertIsNone(kb._distance_to_score(None, "l2"))

    def test_space_detected_from_configuration(self):
        c = mock.Mock()
        c.configuration = {"hnsw": {"space": "l2"}}
        self.assertEqual(kb._collection_space(c), "l2")

    def test_space_detected_from_metadata_when_configuration_missing(self):
        c = mock.Mock()
        c.configuration = None
        c.metadata = {"hnsw:space": "cosine"}
        self.assertEqual(kb._collection_space(c), "cosine")

    def test_space_defaults_to_l2_when_unreadable(self):
        c = mock.Mock()
        c.configuration = None
        c.metadata = None
        self.assertEqual(kb._collection_space(c), "l2")


class TestL2Regression(unittest.TestCase):
    """把真 bug 锁成测试：l2 集合上的真实命中不能再被判为"没搜到"。"""

    def test_real_l2_hit_is_kept_not_dropped(self):
        # 实测真实数据：中文 query 打英文语料，dist=1.0284（真实余弦 0.4858）
        coll = mock.Mock()
        coll.configuration = {"hnsw": {"space": "l2"}}
        coll.query.return_value = {
            "documents": [["Attention mechanisms have become an integral part..."]],
            "metadatas": [[{"source": "paper.pdf", "chunk_idx": 10}]],
            "distances": [[1.0284]],
        }
        coll.get.return_value = {"documents": ["english text " * 40]}
        client = mock.Mock()
        client.get_collection.return_value = coll
        with mock.patch.object(kb, "get_kb",
                               return_value={"name": "kb1", "total_chunks": 1}), \
             mock.patch.object(kb, "_get_chroma", return_value=client), \
             mock.patch.object(kb, "check_embedding_consistency",
                               return_value=(True, None)), \
             mock.patch.object(kb, "_encode", return_value=[[0.0] * 512]):
            kb._corpus_lang_cache.clear()
            res, d = kb.query_kb_detailed("kb1", "注意力机制解决了什么问题", top_k=1)
        # 旧代码：score = 1-1.0284 = -0.0284 → 被 0.1 阈值丢掉，返回 []
        self.assertEqual(len(res), 1, "真实余弦 0.4858 的命中被丢掉了（公式又错了）")
        self.assertAlmostEqual(res[0]["score"], 0.4858, places=3)
        self.assertEqual(d["space"], "l2")
        self.assertEqual(d["kept"], 1)
        self.assertIsNone(d["hint"])

    def test_old_formula_would_have_dropped_it(self):
        """对照：旧公式下同一个 distance 会被判成负分并丢掉。"""
        self.assertLess(1 - 1.0284, kb.DEFAULT_MIN_SCORE)


class TestRebuildIndex(unittest.TestCase):
    """换 embedding 模型后重建索引：不需要原始文件，且失败不毁库。"""

    def _client(self, docs):
        old = mock.Mock()

        def _get(limit, offset, include):
            chunk = docs[offset:offset + limit]
            return {"ids": ["i%d" % i for i in range(offset, offset + len(chunk))],
                    "documents": chunk,
                    "metadatas": [{"chunk_idx": i} for i in range(offset, offset + len(chunk))]}

        old.get.side_effect = _get
        tmp = mock.Mock()
        client = mock.Mock()
        client.get_collection.return_value = old
        client.create_collection.return_value = tmp
        return client, old, tmp

    def test_rebuild_reencodes_and_swaps(self):
        docs = ["a", "b", "c"]
        client, old, tmp = self._client(docs)
        saved = {}
        with mock.patch.object(kb, "get_kb",
                               return_value={"name": "k", "total_chunks": 3}), \
             mock.patch.object(kb, "_get_chroma", return_value=client), \
             mock.patch.object(kb, "_encode",
                               side_effect=lambda ts, **kw: [[0.0] * 4 for _ in ts]), \
             mock.patch.object(kb, "embed_signature",
                               return_value={"model": "bge-m3", "dim": 1024}), \
             mock.patch.object(kb, "_save_kb_meta",
                               side_effect=lambda n, m: saved.update(m)):
            res = kb.rebuild_kb_index("k")
        self.assertEqual(res["chunks"], 3)
        self.assertEqual(res["embedding"]["model"], "bge-m3")
        client.delete_collection.assert_any_call(name="kb_k")
        tmp.modify.assert_called_once_with(name="kb_k")
        self.assertEqual(saved["embedding"], {"model": "bge-m3", "dim": 1024})
        self.assertIn("rebuilt_at", saved)

    def test_rebuild_keeps_old_index_when_encoding_fails(self):
        client, old, tmp = self._client(["a", "b"])
        with mock.patch.object(kb, "get_kb",
                               return_value={"name": "k", "total_chunks": 2}), \
             mock.patch.object(kb, "_get_chroma", return_value=client), \
             mock.patch.object(kb, "_encode", side_effect=RuntimeError("OOM")), \
             mock.patch.object(kb, "_save_kb_meta") as save_meta:
            with self.assertRaises(RuntimeError):
                kb.rebuild_kb_index("k")
        client.delete_collection.assert_not_called()      # 旧索引必须原封不动
        save_meta.assert_not_called()

    def test_rebuild_empty_index_raises(self):
        client, old, tmp = self._client([])
        with mock.patch.object(kb, "get_kb",
                               return_value={"name": "k", "total_chunks": 0}), \
             mock.patch.object(kb, "_get_chroma", return_value=client):
            with self.assertRaises(ValueError) as cm:
                kb.rebuild_kb_index("k")
        self.assertIn("没有任何向量", str(cm.exception))

    def test_rebuild_missing_kb_raises(self):
        with mock.patch.object(kb, "get_kb", return_value=None):
            with self.assertRaises(ValueError):
                kb.rebuild_kb_index("nope")


class TestConfigurableModel(unittest.TestCase):
    """换模型必须能靠配置完成，而不是改代码。"""

    def test_default_model(self):
        with mock.patch.dict(kb.os.environ, {}, clear=False):
            kb.os.environ.pop("KB_EMBED_MODEL", None)
            self.assertEqual(kb.embed_model_name(), kb.DEFAULT_EMBED_MODEL)

    def test_env_overrides_model(self):
        with mock.patch.dict(kb.os.environ, {"KB_EMBED_MODEL": "BAAI/bge-m3"}):
            self.assertEqual(kb.embed_model_name(), "BAAI/bge-m3")

    def test_blank_env_falls_back_to_default(self):
        with mock.patch.dict(kb.os.environ, {"KB_EMBED_MODEL": "   "}):
            self.assertEqual(kb.embed_model_name(), kb.DEFAULT_EMBED_MODEL)

    def test_candidates_include_home_models_dir(self):
        dirs = [str(d) for d in kb._candidate_dirs("BAAI/bge-m3")]
        self.assertTrue(any(d.endswith("models/bge-m3") for d in dirs), dirs)

    def test_absolute_path_is_a_candidate(self):
        dirs = [str(d) for d in kb._candidate_dirs("/opt/m/m3")]
        self.assertIn("/opt/m/m3", dirs)

    def test_resolve_prefers_existing_local_dir(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "config.json").write_text("{}", encoding="utf-8")
            (Path(td) / "pytorch_model.bin").write_text("", encoding="utf-8")
            with mock.patch.dict(kb.os.environ, {"KB_EMBED_MODEL": td}):
                self.assertEqual(kb._resolve_embed_model(), td)


class TestPassagePrefix(unittest.TestCase):
    """文档侧前缀和查询侧一样重要：e5 只给查询加前缀会让两侧不在同一空间。"""

    def test_bge_m3_no_prefix_either_side(self):
        self.assertEqual(kb._text_prefixes("/opt/models/bge-m3"), ("", ""))

    def test_bge_v15_no_prefix(self):
        self.assertEqual(kb._text_prefixes("BAAI/bge-small-zh-v1.5"), ("", ""))

    def test_bge_zh_v10_query_only(self):
        q, p = kb._text_prefixes("/m/bge-large-zh")
        self.assertTrue(q)
        self.assertEqual(p, "")

    def test_e5_prefixes_both_sides(self):
        self.assertEqual(kb._text_prefixes("intfloat/multilingual-e5-small"),
                         ("query: ", "passage: "))

    def test_encode_applies_passage_prefix_for_documents(self):
        embedder = mock.Mock()
        embedder.encode.return_value = mock.Mock(tolist=lambda: [[0.1]])
        with mock.patch.object(kb, "_get_embedder", return_value=embedder), \
             mock.patch.object(kb, "_embedder_ref", "intfloat/multilingual-e5-small"):
            kb._encode(["文档"], is_query=False)
            kb._encode(["问题"], is_query=True)
        self.assertEqual(embedder.encode.call_args_list[0].args[0], ["passage: 文档"])
        self.assertEqual(embedder.encode.call_args_list[1].args[0], ["query: 问题"])


if __name__ == "__main__":
    unittest.main()
