#!/usr/bin/env python3
"""Stdlib unittest cho task_queue — hàng đợi tuần tự (intake -> clarify -> queue -> next/done).

Phủ phần cơ chế queue (không gọi mạng/agent):
  1. add/dedupe/list — enqueue, chặn trùng qid còn mở, sort theo priority rồi tuổi.
  2. Khoá TUẦN TỰ THEO LUỒNG (owner, mặc định task_resolver) — `next` lấy lock; `next`
     lần 2 cùng owner bị từ chối; owner KHÁC không bị chặn; `done` nhả lock; `release`
     cứu lock kẹt; helper try_claim/release_claim (dùng chung với task_resolver.py).
  3. answer — --accept-proposed gấp `proposed` thành brief, item sang ready;
     source != etask thì KHÔNG sync (không đụng mạng).
  4. requeue/remove — chỉ requeue từ failed/done.
No pip deps. Enrichment (context_pack/scout) KHÔNG test ở đây vì cần API/clone thật.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from argparse import Namespace

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(
    _HERE, "..", ".claude", "skills", "auto-dev", "tools")))
sys.path.insert(0, os.path.abspath(os.path.join(
    _HERE, "..", ".claude", "skills", "dev-automation", "tools")))

import task_queue  # noqa: E402


def ns(**kw):
    return Namespace(**kw)


def add(task, **kw):
    base = dict(source="manual", task=task, project=None, type=None,
                title=None, priority=2, ready=True)
    base.update(kw)
    return task_queue.cmd_add(ns(**base))


def nxt(owner=None):
    return task_queue.cmd_next(ns(owner=owner))


def done(qid, result="ok", note=None, owner=None):
    return task_queue.cmd_done(ns(qid=qid, result=result, note=note, owner=owner))


def answer(qid, answers_file=None, accept_proposed=False, no_sync=True):
    return task_queue.cmd_answer(ns(qid=qid, answers_file=answers_file,
                                    accept_proposed=accept_proposed, no_sync=no_sync))


class QueueBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tq_")
        os.environ["WORK_DIR"] = self.tmp

    def tearDown(self):
        os.environ.pop("WORK_DIR", None)
        shutil.rmtree(self.tmp, ignore_errors=True)


class AddListTest(QueueBase):
    def test_add_and_dedupe(self):
        out = add("t1")
        self.assertTrue(out["ok"])
        self.assertEqual(out["item"]["state"], "ready")
        dup = add("t1")
        self.assertTrue(dup["error"])
        self.assertIn("already queued", dup["message"])

    def test_closed_item_can_be_readded(self):
        add("t1")
        task_queue.cmd_next(ns())
        task_queue.cmd_done(ns(qid="manual-t1", result="ok", note=None))
        self.assertTrue(add("t1")["ok"])

    def test_list_sorted_by_priority_then_age(self):
        add("low", priority=3)
        add("high", priority=1)
        add("mid", priority=2)
        out = task_queue.cmd_list(ns(state=None))
        self.assertEqual([i["qid"] for i in out["items"]],
                         ["manual-high", "manual-mid", "manual-low"])

    def test_list_filters_state(self):
        add("a", ready=True)
        add("b", ready=False)
        out = task_queue.cmd_list(ns(state="needs_clarification"))
        self.assertEqual([i["qid"] for i in out["items"]], ["manual-b"])


class SerialLockTest(QueueBase):
    def test_next_claims_and_second_next_refused(self):
        add("t1")
        add("t2")
        first = task_queue.cmd_next(ns())
        self.assertTrue(first["ok"])
        self.assertEqual(first["item"]["qid"], "manual-t1")
        self.assertEqual(first["item"]["state"], "processing")
        second = task_queue.cmd_next(ns())
        self.assertTrue(second["error"])
        self.assertEqual(second["locked_by"]["qid"], "manual-t1")

    def test_done_releases_lock_and_next_proceeds(self):
        add("t1")
        add("t2")
        task_queue.cmd_next(ns())
        done = task_queue.cmd_done(ns(qid="manual-t1", result="ok", note="merged"))
        self.assertEqual(done["item"]["state"], "done")
        nxt = task_queue.cmd_next(ns())
        self.assertEqual(nxt["item"]["qid"], "manual-t2")

    def test_done_fail_marks_failed(self):
        add("t1")
        task_queue.cmd_next(ns())
        out = task_queue.cmd_done(ns(qid="manual-t1", result="fail", note="tests red"))
        self.assertEqual(out["item"]["state"], "failed")

    def test_release_returns_item_to_ready(self):
        add("t1")
        task_queue.cmd_next(ns())
        rel = task_queue.cmd_release(ns())
        self.assertTrue(rel["ok"])
        item = task_queue._load("manual-t1")
        self.assertEqual(item["state"], "ready")
        self.assertIsNone(task_queue._read_lock())

    def test_next_on_empty_queue(self):
        out = task_queue.cmd_next(ns())
        self.assertTrue(out["ok"])
        self.assertIsNone(out["item"])

    def test_needs_clarification_not_claimable(self):
        add("t1", ready=False)
        out = task_queue.cmd_next(ns())
        self.assertIsNone(out["item"])


class AnswerTest(QueueBase):
    def test_accept_proposed_builds_brief_and_readies(self):
        add("t1", ready=False, title="Fix export permission")
        item = task_queue._load("manual-t1")
        item["questions"] = [
            {"ask": "Scope?", "blocking": True,
             "proposed": "chỉ endpoint /export", "assumption": "toàn module"},
        ]
        task_queue._save(item)
        out = task_queue.cmd_answer(ns(qid="manual-t1", answers_file=None,
                                       accept_proposed=True))
        self.assertTrue(out["ok"])
        self.assertEqual(out["item"]["state"], "ready")
        with open(out["brief_path"], encoding="utf-8") as f:
            brief = f.read()
        self.assertIn("chỉ endpoint /export", brief)

    def test_answers_file_resolves(self):
        add("t1", ready=False, title="T")
        ans = os.path.join(self.tmp, "ans.json")
        with open(ans, "w", encoding="utf-8") as f:
            json.dump({"Scope?": "chỉ /export"}, f)
        out = task_queue.cmd_answer(ns(qid="manual-t1", answers_file=ans,
                                       accept_proposed=False))
        self.assertEqual(out["item"]["state"], "ready")

    def test_answer_requires_input(self):
        add("t1", ready=False)
        out = task_queue.cmd_answer(ns(qid="manual-t1", answers_file=None,
                                       accept_proposed=False))
        self.assertTrue(out["error"])


class FlowOwnerLockTest(QueueBase):
    def test_default_owner_is_task_resolver(self):
        add("t1")
        out = nxt()
        self.assertTrue(out["ok"])
        self.assertEqual(task_queue._read_lock("task_resolver")["qid"], "manual-t1")

    def test_other_owner_not_blocked(self):
        add("t1")
        add("t2")
        first = nxt(owner="task_resolver")
        self.assertTrue(first["ok"])
        # Luồng khác (vd người làm tay) vẫn lấy được task kế — không phải lock toàn cục.
        second = nxt(owner="manual")
        self.assertTrue(second["ok"])
        self.assertEqual(second["item"]["qid"], "manual-t2")
        # Nhưng cùng owner thì vẫn tuần tự.
        third = nxt(owner="manual")
        self.assertTrue(third["error"])

    def test_done_releases_the_items_own_owner(self):
        add("t1")
        nxt(owner="manual")
        out = done("manual-t1")           # không truyền owner -> lấy owner ghi trên item
        self.assertEqual(out["item"]["state"], "done")
        self.assertIsNone(task_queue._read_lock("manual"))

    def test_try_claim_release_helpers(self):
        # API mà task_resolver.py dùng trực tiếp.
        self.assertTrue(task_queue.try_claim("task_resolver", "tX"))
        self.assertFalse(task_queue.try_claim("task_resolver", "tY"))   # đang bận
        self.assertTrue(task_queue.try_claim("manual", "tZ"))           # owner khác OK
        self.assertFalse(task_queue.release_claim("task_resolver", qid="tY"))  # sai qid
        self.assertTrue(task_queue.release_claim("task_resolver", qid="tX"))
        self.assertTrue(task_queue.try_claim("task_resolver", "tY"))    # đã nhả -> claim lại được


class AnswerSyncTest(QueueBase):
    def test_manual_source_never_syncs(self):
        add("t1", ready=False, title="T")
        item = task_queue._load("manual-t1")
        item["questions"] = [{"ask": "Scope?", "blocking": True, "proposed": "x"}]
        task_queue._save(item)
        out = answer("manual-t1", accept_proposed=True, no_sync=False)
        self.assertTrue(out["ok"])
        self.assertIsNone(out.get("brief_synced"))  # source=manual -> không đụng eTask


class ThinGuardTest(unittest.TestCase):
    def _item(self, state="ready", **signals):
        return {"state": state, "signals": signals, "notes": []}

    def test_thin_and_bare_forces_clarification(self):
        it = self._item(thin_description=True, has_extra_context=False)
        task_queue._apply_thin_guard(it)
        self.assertEqual(it["state"], "needs_clarification")

    def test_thin_but_enriched_stays_ready(self):
        it = self._item(thin_description=True, has_extra_context=True)
        task_queue._apply_thin_guard(it)
        self.assertEqual(it["state"], "ready")

    def test_rich_description_untouched(self):
        it = self._item(thin_description=False, has_extra_context=False)
        task_queue._apply_thin_guard(it)
        self.assertEqual(it["state"], "ready")


class RequeueRemoveTest(QueueBase):
    def test_requeue_only_from_terminal(self):
        add("t1")
        blocked = task_queue.cmd_requeue(ns(qid="manual-t1"))
        self.assertTrue(blocked["error"])
        task_queue.cmd_next(ns())
        task_queue.cmd_done(ns(qid="manual-t1", result="fail", note=None))
        out = task_queue.cmd_requeue(ns(qid="manual-t1"))
        self.assertTrue(out["ok"])
        # add() không set clarify_verdict -> quay về needs_clarification (an toàn).
        self.assertEqual(out["item"]["state"], "needs_clarification")

    def test_remove(self):
        add("t1")
        out = task_queue.cmd_remove(ns(qid="manual-t1"))
        self.assertTrue(out["ok"])
        self.assertIsNone(task_queue._load("manual-t1"))


if __name__ == "__main__":
    unittest.main()
