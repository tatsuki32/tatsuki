"""A fake AnkiConnect for tests: the actions desk/anki.py uses, on an in-memory collection. Never touches a real Anki.

  python3 desk/tests/fake_ankiconnect.py [port]     serves on 127.0.0.1:<port> (default 8799) for a test Desk with ANKI_URL

It starts with one old deck "Esperanto" holding the reviewed note "esti" (Basic), like a person's real collection.
"""
import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeAnki:
    def __init__(self):
        self.lock = threading.Lock()
        self.models = {"Basic": ["Front", "Back"]}
        self.decks = {"Default", "Esperanto"}
        self.notes, self.cards, self.media, self.calls = {}, {}, {}, []
        self.next = 1000
        self._add("Basic", "Esperanto", {"Front": "esti", "Back": "to be"}, reps=3, due=True)

    def _add(self, model, deck, fields, reps=0, due=False):
        self.next += 1
        nid, cid = self.next, self.next + 500000
        names = self.models[model]
        self.notes[nid] = {"noteId": nid, "modelName": model, "tags": [], "cards": [cid],
                           "fields": {k: {"value": fields.get(k, ""), "order": i} for i, k in enumerate(names)}}
        self.cards[cid] = {"cardId": cid, "note": nid, "deckName": deck, "reps": reps, "due": due}
        return nid

    def handle(self, action, p):
        self.calls.append(action)
        if action == "version":
            return 6
        if action == "modelNames":
            return list(self.models)
        if action == "createModel":
            self.models[p["modelName"]] = list(p["inOrderFields"])
            return {"name": p["modelName"]}
        if action == "deckNames":
            return sorted(self.decks)
        if action == "createDeck":
            self.decks.add(p["deck"])
            return 1
        if action == "findNotes":
            own = re.search(r'"DeskId:((?:[^"\\]|\\.)*)"', p["query"])
            if own:
                want = own.group(1).replace('\\', '')
                return [nid for nid, n in self.notes.items() if n["fields"].get("DeskId", {}).get("value") == want]
            words = [w.replace('\\', '') for w in re.findall(r'"((?:[^"\\]|\\.)*)"', p["query"])]
            return [nid for nid, n in self.notes.items()
                    if any(w.lower() in f["value"].lower() for w in words for f in n["fields"].values())]
        if action == "notesInfo":
            return [json.loads(json.dumps(self.notes[n])) for n in p["notes"] if n in self.notes]
        if action == "cardsInfo":
            return [dict(self.cards[c]) for c in p["cards"] if c in self.cards]
        if action == "addNote":
            n = p["note"]
            if n["modelName"] not in self.models:
                raise ValueError("model was not found")
            if n["deckName"] not in self.decks:
                raise ValueError("deck was not found")
            first = self.models[n["modelName"]][0]
            if any(x["fields"].get(first, {}).get("value") == n["fields"].get(first) for x in self.notes.values()):
                raise ValueError("cannot create note because it is a duplicate")
            for a in n.get("audio") or []:
                self.media[a["filename"]] = a.get("data", "")
                for f in a.get("fields") or []:
                    n["fields"][f] = n["fields"].get(f, "") + f"[sound:{a['filename']}]"
            return self._add(n["modelName"], n["deckName"], n["fields"])
        if action == "updateNoteFields":
            note = self.notes[p["note"]["id"]]
            for k, v in p["note"]["fields"].items():
                note["fields"][k]["value"] = v
            return None
        if action == "findCards":
            if "is:due" in p["query"]:
                return [c for c, x in self.cards.items() if x["due"]]
            return list(self.cards)
        raise ValueError("unsupported action")

    def respond(self, body):
        try:
            req = json.loads(body)
            with self.lock:
                return {"result": self.handle(req["action"], req.get("params") or {}), "error": None}
        except Exception as e:
            return {"result": None, "error": str(e)}


def serve(port=8799, fake=None):
    fake = fake or FakeAnki()

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            out = json.dumps(fake.respond(self.rfile.read(int(self.headers.get("Content-Length") or 0)))).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, fake


if __name__ == "__main__":
    srv, _ = serve(int(sys.argv[1]) if len(sys.argv) > 1 else 8799)
    print("fake AnkiConnect on", srv.server_address, flush=True)
    threading.Event().wait()
