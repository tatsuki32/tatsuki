"""メール整理: nothing is stopped without a press, links in doubtful mail are never touched, and nothing here sends mail. Gmail is faked."""
import base64
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "desk"))
import gmail_clean as gc

GOOD_AUTH = "mx.google.com; dkim=pass header.i=@news.example.com; spf=pass; dmarc=pass header.from=news.example.com"
SIG = "v=1; d=news.example.com; h=From:To:\r\n List-Unsubscribe:List-Unsubscribe-Post; b=x"


def msg(sender, labels=("CATEGORY_PROMOTIONS", "INBOX"), unsub="<https://news.example.com/u?k=1>", post="List-Unsubscribe=One-Click",
        auth=GOOD_AUTH, sig=SIG, subject="セールのお知らせ", at=1793000000000):
    headers = [("From", sender), ("Subject", subject)]
    if unsub:
        headers.append(("List-Unsubscribe", unsub))
    if post:
        headers.append(("List-Unsubscribe-Post", post))
    if auth:
        headers.append(("Authentication-Results", auth))
    if sig:
        headers.append(("DKIM-Signature", sig))
    return {"internalDate": str(at), "labelIds": list(labels), "payload": {"headers": [{"name": k, "value": v} for k, v in headers]}}


class Digest(unittest.TestCase):
    def test_groups_by_sender_and_counts(self):
        out = gc.digest([msg("Shop <A@News.example.com>"), msg("Shop <a@news.example.com>", at=1793000000001), msg("Other <b@other.example.com>")])
        self.assertEqual(out["a@news.example.com"]["count"], 2)
        self.assertEqual(len(out), 2)

    def test_a_person_without_list_headers_is_not_a_candidate(self):
        self.assertEqual(gc.digest([msg("Friend <f@example.com>", labels=("INBOX",), unsub="", post="")]), {})

    def test_bank_and_starred_are_guarded(self):
        out = gc.digest([msg("銀行 <info@bank.example.com>"), msg("Shop <s@example.com>", labels=("INBOX", "STARRED"))])
        self.assertTrue(out["info@bank.example.com"]["protect"])
        self.assertTrue(out["s@example.com"]["protect"])

    def test_account_notices_are_guarded(self):
        for sender, subject in (("Google <noreply-accounts@google.com>", "Security"), ("Netflix <info@account.netflix.example.com>", "x"),
                                ("Amazon <no-reply@amazon.com.au>", "x"), ("Facebook <notification@facebookmail.example.com>", "x"),
                                ("求人 <cs@jobs.example.com>", "ご応募を受け付けました")):
            out = list(gc.digest([msg(sender, subject=subject)]).values())[0]
            self.assertTrue(out["protect"], sender)

    def test_trust_needs_google_verdict_and_signed_header(self):
        def t(**k):
            return list(gc.digest([msg("S <s@example.com>", **k)]).values())[0]["trusted"]
        self.assertTrue(t())
        self.assertFalse(t(auth="mx.evil.example; dkim=pass; dmarc=pass"))
        self.assertFalse(t(auth="mx.google.com; dkim=pass; dmarc=fail"))
        self.assertFalse(t(sig="v=1; h=From:To; b=x"))


class SafeUrl(unittest.TestCase):
    def test_only_public_https(self):
        with mock.patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))]):
            self.assertTrue(gc.safe_url("https://example.com/u"))
            self.assertFalse(gc.safe_url("http://example.com/u"))
            self.assertFalse(gc.safe_url("https://user:pw@example.com/u"))
        for ip in ("127.0.0.1", "192.168.1.1", "169.254.169.254", "10.0.0.1"):
            with mock.patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", (ip, 443))]):
                self.assertFalse(gc.safe_url("https://localhost.example/u"), ip)


class RateLimit(unittest.TestCase):
    def _error(self, code, message):
        import io, urllib.error
        return urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(json.dumps({"error": {"message": message}}).encode()))

    def test_reading_too_fast_is_retried_but_a_real_refusal_is_not(self):
        ok = mock.MagicMock()
        ok.__enter__.return_value.read.return_value = b'{"ok": 1}'
        with mock.patch.object(gc, "access_token", return_value="t"), mock.patch("time.sleep"):
            with mock.patch("urllib.request.urlopen", side_effect=[self._error(403, "User-rate limit exceeded"), ok]) as u:
                self.assertEqual(gc.gmail("GET", "/x"), {"ok": 1})
                self.assertEqual(u.call_count, 2)
            with mock.patch("urllib.request.urlopen", side_effect=[self._error(403, "Insufficient Permission")]) as u:
                with self.assertRaises(gc.GmailError):
                    gc.gmail("GET", "/x")
                self.assertEqual(u.call_count, 1)


class Act(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        for target, value in (("DIR", self.dir.name), ("connected", lambda: True)):
            p = mock.patch.object(gc, target, value)
            p.start()
            self.addCleanup(p.stop)
        self.calls = []
        p = mock.patch.object(gc, "gmail", side_effect=self.fake)
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(gc, "one_click", return_value=True)
        self.click = p.start()
        self.addCleanup(p.stop)

    def fake(self, method, path, body=None, tries=4):
        self.calls.append((method, path, body))
        if path == "/settings/filters" and method == "POST":
            return {"id": "F1"}
        if path.startswith("/messages?"):
            return {"messages": [{"id": "m1"}, {"id": "m2"}]}
        return {}

    def load(self, *messages):
        items = gc.digest(list(messages))
        gc._save("senders.json", {"at": 1, "messages": len(messages), "items": items})
        return list(items.values())[0]["id"]

    def test_stop_unsubscribes_and_makes_a_filter_but_keeps_old_mail(self):
        sid = self.load(msg("Shop <s@example.com>"))
        status, body = gc.act(sid, "stop")
        self.assertEqual(status, 200)
        self.click.assert_called_once()
        self.assertEqual(self.calls, [("POST", "/settings/filters", {"criteria": {"from": "s@example.com"},
                                                                     "action": {"addLabelIds": ["TRASH"], "removeLabelIds": ["INBOX"]}})])
        self.assertEqual(body["done"][0]["unsub"], "done")
        self.assertEqual(body["candidates"], [])

    def test_doubtful_mail_gets_no_request_at_its_link(self):
        sid = self.load(msg("Shop <s@example.com>", auth="mx.google.com; dkim=fail; dmarc=fail"))
        status, body = gc.act(sid, "stop")
        self.assertEqual(status, 200)
        self.click.assert_not_called()
        self.assertEqual(body["done"][0]["unsub"], "none")
        self.assertNotIn("page", body["done"][0])

    def test_spam_never_touches_the_link(self):
        sid = self.load(msg("Shop <s@example.com>"))
        status, body = gc.act(sid, "spam")
        self.assertEqual(status, 200)
        self.click.assert_not_called()
        self.assertIn(("POST", "/messages/batchModify", {"ids": ["m1", "m2"], "addLabelIds": ["SPAM"], "removeLabelIds": ["INBOX"]}), self.calls)
        self.assertEqual(body["done"][0]["moved"], 2)

    def test_guarded_sender_needs_confirmation(self):
        sid = self.load(msg("銀行 <info@bank.example.com>"))
        self.assertEqual(gc.act(sid, "stop")[0], 409)
        self.click.assert_not_called()
        self.assertEqual(self.calls, [])
        self.assertEqual(gc.act(sid, "stop", True)[0], 200)

    def test_undo_removes_the_filter(self):
        sid = self.load(msg("Shop <s@example.com>"))
        gc.act(sid, "stop")
        status, body = gc.undo(sid)
        self.assertEqual(status, 200)
        self.assertEqual(self.calls[-1][:2], ("DELETE", "/settings/filters/F1"))
        self.assertEqual(len(body["candidates"]), 1)

    def test_view_hides_the_personal_link(self):
        sid = self.load(msg("Shop <s@example.com>"))
        self.assertNotIn("news.example.com/u", json.dumps(gc.view()))
        gc.act(sid, "keep")
        self.assertNotIn("news.example.com/u", json.dumps(gc.view()))

    def test_no_way_to_send_or_delete_for_good(self):
        with open(gc.__file__, encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn("gmail.send", src)
        self.assertNotIn("/messages/send", src)
        self.assertNotIn("batchDelete", src)


if __name__ == "__main__":
    unittest.main()
