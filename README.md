# SERVICE POS — Restaurant Management Platform (Three-Phase Edition)

An upgraded, working **FastAPI + SQLite restaurant POS reference implementation**. It builds on the previously delivered Service POS starter and now includes operational workflows from all three requested phases. It provides real database-backed functionality, not a static design. **This is a functional pilot/MVP, not a certified retail POS or turnkey, production-hardened multi-branch system.**

## Quick setup on Windows

1. Extract `restaurant-pos-pro-branding-themes-updated.zip` and open the `restaurant-pos-pro` folder.
2. Install **Python 3.10+** if it is not installed.
3. Double-click `run-windows.bat` (requires internet to install Python packages on first run).
4. Open `http://127.0.0.1:8000` in Chrome or Edge.
5. Create the first administrator account and set your restaurant name/currency. Change the seeded sample menu and prices before real use.

Or from a terminal:

```powershell
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

**Linux/macOS**: `chmod +x run-linux.sh && ./run-linux.sh`, or create a virtual environment, install dependencies and run `python -m uvicorn app:app --host 127.0.0.1 --port 8000`.

**Docker**: `docker compose up --build -d` runs the server on port 8000 and persists the SQLite database in a named Docker volume. Do not expose that port directly to the public internet without HTTPS, reverse proxy, and hardening.

## Upgrading from the earlier starter without losing orders

**Back up your original `data/restaurant.db` first.** Stop the old POS server. Copy the database from the previous `restaurant-pos-starter/data/restaurant.db` into the new project's `data/restaurant.db`, then start this upgraded project. Database upgrades are applied during server startup and add the new tables/columns without intentionally dropping prior orders, staff, menu, or payments. Verify your backup and run the included automated tests before relying on the upgrade. Never run both versions against one SQLite file simultaneously. 

You can make a consistent database backup using:

```bash
python tools/backup_db.py
```

It creates timestamped backups in `backups/` and checks database integrity. Move encrypted backup copies off the server and regularly test restores yourself. The `backups` directory and active DB are not included in the source ZIP.

## Restaurant logo & style presets (new)

From **Settings → Branding studio**, an administrator can customize the selected branch:

- **Upload, replace or remove logo** (PNG, JPG or WebP up to 2 MB). Images are validated, resized to fit inside 640×640 and re-encoded as safe PNG. Logos show in the sidebar, top navigation, login for the main branch, receipt printouts, kitchen/KOT tickets, printed QR table cards and customer QR/online ordering header.
- **Choose one of six complete theme styles**: Signature (coral), Midnight (dark/violet), Ivory (cream/copper), Ocean (blue), Forest (green) or Saffron (gold). Clicking a theme gives a *temporary live preview*. Click **Save branding & theme** to persist the selection for your branch. If you leave settings without saving, the saved theme is restored.
- Set optional **restaurant display name and short tagline**, independently for each branch. If left blank, the display name falls back to the original restaurant name in Business information.
- Other branches retain their own logos/themes; only administrators can modify branding. Theme changes do **not** alter any order, inventory, payment, or customer data. Branding edits are recorded in the audit trail.

The server stores theme/name preferences in the SQLite `branch_branding` table. Uploaded logo files are stored under **`data/branding/`** (or adjacent to your configured `POS_DATABASE`). **Back up and restore both the SQLite database AND the branding directory**. The bundled backup script now backs up logo assets next to its timestamped `.db` copy. Uploaded images are never placed in the publicly writable static directory.

The six styles are editable CSS stylesheets:

```text
static/themes.css          Staff dashboard / billing / management themes
static/guest-themes.css    Customer-facing QR / online ordering themes
static/branding.js         Branding settings, preview and upload interactions
branding.py                Branch branding API, image validation and storage
```

**Logo formats**: SVG uploads are intentionally disabled for security. Card receipts remain monochrome/print-friendly even when a dark website theme is selected. Pillow is installed automatically from `requirements.txt` on setup.

## Features and workflow

### Phase 1 — Complete Operational POS foundation

| Feature | Delivered behavior |
|---|---|
| Advanced register | Dine-in, takeaway and delivery; product search/category filters, quantity, notes, order discounts; add items to open tickets. |
| Payment and checkout | Cash, card-recorded-externally and other payments; split *tenders*; server-side total calculation; stock validation; transactional checkout. |
| Floor and tables | Zone-based visual table tiles; occupied/available/preparing indicators; open order on a table; transfer an unpaid table order. |
| Kitchen KDS | New → Preparing → Ready → Served; Server-Sent Events stream for updates across connected browser sessions. |
| KOT + receipts | Print-ready kitchen order tickets and browser-based 80mm receipt printing; table QR print. |
| Cashier shifts | Starting cash, open/close, cash receipts, cash expenses/refunds, expected cash and counted cash variance. Admin is exempt from compulsory cashier shift, cashier is not. |
| Refund permissions | Administrator-only partial/full **recorded** refunds with mandatory reason; optional product/ingredient restock on final full refund; cumulative refund capped at order total. |
| Audit trail | Staff-linked audit entries for order, kitchen, payment, refund, staff creation, menu/settings, stock, purchasing, branch and shift operations. |

### Phase 2 — Restaurant management

| Feature | Delivered behavior |
|---|---|
| Ingredients and recipes | Branch-level ingredients (g/ml/pcs) with milliunit precision; link ingredient quantity per recipe/menu item. |
| Ingredient stock | Stock consumption on paid orders in the checkout transaction. Insufficient ingredient stock blocks checkout. Consumption stored as snapshot for correct full-refund restocking. |
| Suppliers and purchasing | Supplier directory, receive ingredient quantities and update last purchase unit cost, stock movement history. |
| Wastage and expenses | Record ingredient waste and operating expenses; cash expenses reflected in cashier shift reconciliation. |
| Food costing | Per-order ingredient cost snapshot; approximate inventory valuation from last purchase costs. |
| Customers and loyalty | Contact directory with order counts/lifetime spending. Link customers at new-order creation; points earned upon paid checkout (1 per 100 whole currency units); point redemption on open linked orders (1 point = 1 currency unit). |
| Financial reports | Date range, gross/net sales, refunds, cost of goods, expenses, estimated contribution profit, top dishes, payment tender breakdown, trend and CSV order export. |

### Phase 3 — Smart restaurant platform foundation

| Feature | Delivered behavior / limit |
|---|---|
| QR ordering | Each table has a securely random QR URL; guest can add menu items and submit an unpaid kitchen ticket. Refuses a second active dine-in order at an occupied table. |
| Online store | Each branch has a shareable online ordering link at `/guest?store=...`; supports takeaway and delivery requests. **No online checkout is connected.** |
| Delivery board | Delivery job statuses: pending → assigned → picked up → delivered, plus failed; driver, address and phone maintained by staff. |
| Loyalty program | Points ledger, earning at checkout, redemption before payment, reversal of earned points on fully refunded sales. |
| Offline drafts | Cashier can **save local browser drafts** for manual review/submission when connected. Uses a unique `client_key` to prevent duplicate submissions. **Not offline payment processing, not automatic distributed synchronization, and not an offline-capable reloaded application.** |
| Multi-branch | Administrator can create branches, switch branches, clone independent menu items, and manage branch-scoped orders, inventory, tables, suppliers, customers, delivery, reports and staff. SQLite is still a pilot deployment choice. |

## QR and online ordering network setup

QR links must use a URL **reachable from customers' phones**. If the server runs only at `127.0.0.1`, guests will not be able to open the printed links. Set a public HTTPS domain (or a trusted private LAN URL in a controlled test) for QR code generation, e.g.:

```bash
# Example only; use the actual public domain you own and control:
export POS_PUBLIC_BASE_URL=https://pos.example.com
```

On Windows PowerShell: `$env:POS_PUBLIC_BASE_URL='https://pos.example.com'` before starting the server. Reprint the codes after changing this value. Tokens are high entropy, but **a QR token must be handled as a bearer capability**: anybody with the link can place orders to that table. Per-process throttling is included as a basic safeguard, but add gateway-level anti-spam protections before public use.

The public store creates **pay-later** delivery/takeaway orders. Confirm orders and payment independently. Never assume a guest has paid just because the ticket appears in the kitchen.

## Roles

**Administrator:** All modules in selected branch, branch creation/switching, refunds, staff management, exports and audit log.

**Cashier:** Orders, register, receipts, shifts, customer creation, expenses, delivery dispatch and limited reports. A shift must be opened before checkout.

**Kitchen:** Read kitchen orders, advance preparation status and print KOT. Cannot take payments, issue refunds, edit pricing or view financial reports.

Staff is assigned to the administrator's selected branch at creation. Admins can switch branches via the header dropdown.

## Validation

```bash
python -m unittest discover -s tests -v
python -m unittest discover -s tests_pro -v
python -m unittest discover -s tests_branding -v
node --check static/app.js
node --check static/pro.js
node --check static/guest.js
node --check static/branding.js
```

The first command tests original starter behavior. The second verifies migration/setup, QR links, inventory purchase, recipe consumption, cashier shift, loyalty, refunds/restocking, guest ordering, replay protection, profit and branch isolation. Node.js is optional; only needed for JS syntax checks, not to run the POS.

## Directory layout

```text
restaurant-pos-pro/
  app.py                Main FastAPI API (upgraded original)
  branding.py           Safe logo upload, theme preference APIs & branch branding table
  enhancements.py       Migrations + Phase 1/2/3 endpoints
  static/
    index.html          Staff application shell
    app.js              Register, KDS, original management UI
    pro.js              Extended management UI, branch switcher, offline drafts
    styles.css          POS dashboard styling
    themes.css          Six branch theme CSS styles
    branding.js         Branding studio and theme preview
    guest-themes.css    Public QR menu theme styles
    guest.html/js/css   Separate public ordering interface
  tests/                Original integration suite
  tests_pro/            Three-phase feature tests
  tests_branding/       New branding/permissions/integration tests
  tools/backup_db.py    Online SQLite backup utility
  data/                 SQLite database and logo assets (created at runtime)
  .env.example          Example environment settings
  run-windows.bat       Windows startup helper
  run-linux.sh          Linux/macOS startup helper
```

## Important operational / production limits

1. **Payment processing:** Card tenders and refunds are **manual records**. No gateway, bank/card terminal, digital wallet, PCI scope, automated settlement or card refund is integrated.
2. **Offline:** Drafts live in browser `localStorage` on the current device. They are not encrypted, cannot be guaranteed after browser storage is cleared, and cannot validate a table or price until sync. Avoid sensitive customer notes in drafts. Do not count drafts as sales or kitchen orders.
3. **Multi-branch infrastructure:** Branch data is isolated by the application, but **SQLite and a single server are not suitable for a busy, distributed, high-concurrency multi-restaurant rollout**. Migrate to PostgreSQL, add worker-safe broadcast and distributed rate limiting, central backups, tenant auth and synchronization tests before commercial release.
4. **Printer hardware:** Printing currently uses the browser print dialog and 80mm-friendly CSS. For automatic ESC/POS printer routing or receipt printers, implement/test drivers and hardware adapters separately.
5. **Finance/timezone:** Datetimes and report date filters currently use **UTC**; restaurant-local day boundaries, tax compliance, fiscal receipts, statutory invoice numbering and payroll accounting are not implemented. Global currency and tax settings are currently shared among branches. Profit is an **estimate** and depends on recipe coverage, purchase costs and recorded expenses.
6. **Security/availability:** Session cookies, server-side role permissions, CSRF checks, password hashing, basic login throttling, and audit are present, but production deployment still needs HTTPS, secure cookie setting, private database storage, ingress rules, backups and restoration rehearsals, external monitoring, distributed rate limiting, better admin permissions and security assessment.
7. **Public ordering:** Basic per-token rate limiting uses process memory; it does not protect a distributed deployment. Table QR codes are capability links, not a payment authentication method. Customers are not automatically notified by SMS/WhatsApp/email.
8. **Staff operations:** Additional enterprise needs such as separate kitchen stations, full split/merge check workflows, tax-inclusive modifiers, paid ticket changes, automatic table reservations, and end-of-day timezone reconciliation are future work.

**Do not expose your SQLite file, environment variables or backups through the static site.** Do not use development credentials in a public deployment. Replace test/sample menu with real products and prices and validate business processes before accepting actual customer orders.
"# Restaurant-pos-pro" 
