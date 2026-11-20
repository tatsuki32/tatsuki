# Cards (iPhone)

The phone side of the Desk Reader's flashcards. Review only, plus a field to type a word; the Mac fills its meaning after the
next sync. Works offline (the cards live in the phone's storage) and syncs through `/api/sync` with a passphrase.

- App: `public/index.html` (one file). Same FSRS code and data shapes as `desk/index.html`.
- Sync: `functions/api/sync.js` on Cloudflare Pages + KV (`CARDS`). The Mac side is `desk/cards_sync.py`, run by `server.py`
  every 2 minutes. Newer card wins; a day's review entries are joined.
- Setup, once: `bash cards/setup.sh` (KV namespace, passphrase in the keychain `tatsuki-desk-cards` and the Pages secret `SYNC_KEY`,
  build, deploy). Then on the Desk: Cards → Use on iPhone → open the link on the phone and add it to the Home Screen.
- Deploy again after a change: `python3 cards/build.py && (cd cards && npx wrangler pages deploy dist --project-name tatsuki-cards --branch main)`.

Only cards travel: the word, its meaning and the one sentence it was met in. A book's text never leaves the Mac.
