# Personal sites on tany.im

Personal things go on short subdomains of tany.im (the company site stays at `tany.im`).

| Subdomain | What | Source |
|---|---|---|
| `tatsuki.tany.im` | Portfolio / profile (was `tatsuki3284.pages.dev`) | outside the repo, see `sites.json` |

Naming rule: one short English word per thing (`photo.tany.im`, `notes.tany.im` …).
Already used: `go` (short links), `sift` (Sift Daily). Pick a word that is not in this table.

## Add a new one
1. `sites/<name>/wrangler.toml` (copy `tatsuki/`, change `name` and `pattern`).
2. Add it to `sites.json` (`src` = the folder with index.html; personal text stays outside the repo).
3. `bash sites/deploy.sh <name>` — the DNS record for the subdomain is made automatically.
