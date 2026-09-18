# Security Hotfix Notes — VGL POS prototype

Scope: close the remotely exploitable defects from the security audit with the
minimum possible change. No new runtime dependencies, no file moves, no
restructuring. Each fix is independent and independently revertable.

Run everything from the project root. `pytest` is the only tool you may need
to install (`python -m pip install pytest`) — it is a dev tool and is
deliberately **not** added to `requirements.txt`.

---

## P0-1 — Unauthenticated `POST /api/demo/reset` (calls `db.drop_all()`) and `POST /api/demo/simulate_sale`

**Fix.** Both routes are registered only when `APP_ENV=demo`. In any other
environment the routes do not exist and the paths return Flask's default 404
(they are never advertised with a 403). Even in demo, every request must carry
an `X-Demo-Token` header that matches the `DEMO_TOKEN` env var
(constant-time comparison via `hmac.compare_digest`); a missing or wrong
token — or an unset `DEMO_TOKEN` — also returns 404. The `try/except` blocks
that returned `str(e)` to the client were removed; failures now flow through
the sanitised global handler (see P1-8).

**Files.** `app.py`

**Verify.**
```
curl -X POST localhost:5000/api/demo/reset            # 404 (no APP_ENV)
python -m pytest test_app.py -v -k p0_1
```

## P0-3 — Checkout accepted negative/zero quantities and unbounded discounts

**Fix.** `checkout()` validates the entire request before any DB mutation and
rejects with HTTP 400 + a specific message (never clamps):
- `quantity`: strict integer (bool rejected), `> 0`, `<= 10000` per line
- line count: 1–200; `items` must be a non-empty list of objects
- `discount`: number `>= 0` and `<=` the pre-discount cart total
- `amount_paid`: number `>= 0`
- `customer_id`: if supplied, must be an integer that exists
- stock is now checked across **all** lines of the same product, so duplicated
  lines can no longer drive stock negative

**Files.** `routes/pos.py`

**Verify.**
```
curl -X POST localhost:5000/api/checkout -H 'Content-Type: application/json' \
  -d '{"items":[{"product_id":1,"quantity":-5}],"amount_paid":0}'      # 400
curl -X POST localhost:5000/api/checkout -H 'Content-Type: application/json' \
  -d '{"items":[{"product_id":1,"quantity":1}],"discount":99999}'      # 400
python -m pytest test_app.py -v -k p0_3
```

## P0-4 — CGST/SGST computed pre-discount; invoice did not reconcile

**Fix.** The order discount is apportioned across lines in proportion to their
gross value using the **largest-remainder method** (floor each share to the
paisa, hand remaining paise to the largest fractional remainders), so the
parts sum exactly to the discount. Tax is then computed on the post-discount
line value. All arithmetic is `decimal.Decimal`; values are converted to
`float` only at column assignment (columns are still `Float`). Before commit
the route asserts `taxable + cgst + sgst == total` within ₹0.01 and raises if
not (nothing is written). The audit case ₹299 @ 18% − ₹50 now stores
211.02 + 18.99 + 18.99 = 249.00 against total 249.00.
Note: `SaleItem.subtotal` keeps its existing meaning (gross line value,
pre-discount) so receipts still show line prices with the discount as its own
row; reconciliation is enforced on the Sale header totals.

**Files.** `routes/pos.py`

**Verify.**
```
python -m pytest test_app.py -v -k p0_4
```

## P0-6 — Tests silently ran against (and dropped) the real `instance/pos.db`

**Fix.** `create_app()` now accepts an optional config dict/object applied
**before** `db.init_app()` (Flask-SQLAlchemy 3.x builds the engine inside
`init_app`, so later overrides were no-ops). `test_app.py` passes
`{'TESTING': True, 'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:'}`,
sanitises the environment (`DATABASE_URL`, rate limiter, `APP_ENV`…) before
importing `app`, and `setUp` raises loudly if `db.engine.url` does not contain
`:memory:`. With `TESTING` set, `create_app()` also skips its auto
`create_all()`/seed so tests own their schema and fixtures.

**Files.** `app.py`, `test_app.py`

**Verify.**
```
python -m pytest test_app.py -v -k p0_6     # and note instance/pos.db is untouched by the suite
```

## P0-8 — `escHtml()` only escaped apostrophes; stored XSS on every screen

**Fix.** A correct `escapeHtml()` (escapes `& < > " '`) now lives in
`static/js/pos.js` and is applied to **every** interpolation of
server-supplied data across all 9 templates (product/customer/sale fields,
category names/icons, payment labels, HSN codes, toast messages — toasts are
escaped centrally inside `showToast`). The old helper was renamed to
`escJsString` (backslash/quote escaping) and survives only where a JS string
literal is genuinely built inside an inline handler; there it is wrapped as
`escapeHtml(escJsString(...))` because the literal sits inside an HTML
attribute. `receipt.html` is server-rendered by Jinja with autoescaping and
needed no change; `settings.html` and `base.html` interpolate no server data.

**Files.** `static/js/pos.js`, `templates/pos.html`,
`templates/dashboard.html`, `templates/products.html`,
`templates/customers.html`, `templates/sales.html`, `templates/reports.html`

**Verify.**
```
python -m pytest test_app.py -v -k p0_8
# manual: create a product named <img src=x onerror=alert(1)> — it renders as text
```

## P3-1 — Cart total parsed out of the DOM broke on Indian digit grouping

**Fix.** The payable total is kept as a JavaScript Number in module state
(`cartTotal`), assigned in `updateTotals()` and used directly by the checkout
handler, the amount-received listener, and the quick-amount buttons. Money is
never round-tripped through the DOM. For 1234.00 / 100000.00 / 1234567.89 the
state value is exact by construction; the regression test additionally
documents that the removed parse read ₹1,00,000.00 as ₹100.00.

**Files.** `templates/pos.html`

**Verify.**
```
python -m pytest test_app.py -v -k p3_1
```

## P1-8 — Global error handler returned `str(e)` (SQL/schema leak)

**Fix.** Unhandled exceptions are logged server-side with the full traceback
under a `uuid4` request id; the client receives only
`{"error": "Internal server error", "request_id": "<uuid>"}` (HTTP 500).
`HTTPException`s still pass through unchanged.

**Files.** `app.py`

**Verify.**
```
python -m pytest test_app.py -v -k p1_8
```

## P1-10 — `CORS(app)` allowed every origin

**Fix.** Allowed origins come from the `CORS_ORIGINS` env var
(comma-separated). Unset/empty means same-origin only: the list is empty and
no `Access-Control-Allow-Origin` header is ever emitted.

**Files.** `app.py`

**Verify.**
```
python -m pytest test_app.py -v -k p1_10
```

## P1-11 — `SECRET_KEY` fell back to a hardcoded literal

**Fix.** If `APP_ENV=production` and `SECRET_KEY` is unset, `create_app()`
raises `RuntimeError` at startup and the process refuses to boot. Outside
production a random per-process key (`secrets.token_hex(32)`) is generated —
sessions do not survive restarts in dev, which is acceptable there.
The literal `tn-pos-secret-2024` is gone.

**Files.** `app.py`

**Verify.**
```
APP_ENV=production python -c "import app"     # must raise RuntimeError
python -m pytest test_app.py -v -k p1_11
```

## DELETE guard — `DELETE /api/sales/<id>` hard-deleted a financial record

**Fix.** The route now stamps a new nullable `sales.cancelled_at` column
instead of deleting; stock restoration stays. A second DELETE returns 400 and
does not restore stock twice. Cancelled sales disappear from the
`GET /api/sales` listing (so the front-end behaves as before) but remain in
the database. New databases get the column from the model; existing databases
run the documented, idempotent one-off script:

```
python scripts/add_cancelled_at_column.py         # uses DATABASE_URL, or instance/pos.db
```

Known limitation (unchanged behaviour, flagged for the rebuild): reports still
aggregate over all sales, including cancelled ones.

**Files.** `models.py`, `routes/sales.py`, `scripts/add_cancelled_at_column.py`

**Verify.**
```
python -m pytest test_app.py -v -k delete_guard
```

## Committed credentials — `ngrok.yml` and loaderio token file

**Fix.** Deleted `ngrok.yml` and `static/loaderio-557f663c7b4236720358c9fbda7b53f0.txt`
from the working tree; `.gitignore` now blocks `ngrok.yml` and
`loaderio-*.txt`. The dynamic `/loaderio-<token>` route in `app.py` is
untouched, so loader.io verification still works without a committed file.
If these files were ever pushed to a remote, rotate the ngrok authtoken and
loaderio token and purge them from git history — deleting them from the tree
does not remove them from old commits.

**Files.** deleted `ngrok.yml`, deleted `static/loaderio-*.txt`, `.gitignore`

**Verify.**
```
python -m pytest test_app.py -v -k credentials
```

## Hardcoded `BASE_URL` in `routes/sales.py`

**Fix.** The WhatsApp receipt link host is read from the `RECEIPT_BASE_URL`
env var; when unset it falls back to the host the request arrived on
(`request.url_root`), which is correct for local use. The railway.app literal
is gone.

**Files.** `routes/sales.py`

**Verify.**
```
python -m pytest test_app.py -v -k base_url
```

---

## Full gate

```
python -m pytest test_app.py -v                                        # 38 passed
APP_ENV=production python -c "import app"                              # RuntimeError
python app.py &                                                        # then:
curl -X POST localhost:5000/api/demo/reset                             # 404
curl -X POST localhost:5000/api/checkout -H 'Content-Type: application/json' \
  -d '{"items":[{"product_id":1,"quantity":-5}],"amount_paid":0}'      # 400
curl -X POST localhost:5000/api/checkout -H 'Content-Type: application/json' \
  -d '{"items":[{"product_id":1,"quantity":1}],"discount":99999}'      # 400
```

## Out of scope (unchanged, for the follow-up rebuild)

- There is still **no authentication or authorization** on the business API
  (products, customers, sales, reports). The audit items fixed here close the
  destructive/leaky endpoints, but the API remains open by design of the
  prototype and needs the full auth/permission layer from the target
  architecture.
- Money columns are still `Float` at the storage layer (calculations are now
  `Decimal`); the schema migration to `Numeric` belongs to the rebuild.
- Reports include cancelled sales (see DELETE guard note).
