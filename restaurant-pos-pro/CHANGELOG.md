# Changelog — Restaurant POS Pro

## V2.1 — November 4, 2026: Restaurant Branding & Theme Studio

- Added admin-only branding studio under Restaurant settings with six theme swatches and live preview.
- Added branch-scoped saved themes, display name, tagline, and validated restaurant logo upload/remove.
- Added safe PNG re-encoding with 2 MB upload maximum and 640 px resized output, no user SVG execution.
- Applied restaurant logos across POS navigation, main-branch login, receipts, KOT tickets, QR printouts and guest ordering pages.
- Added stylesheet modules `static/themes.css`, `static/guest-themes.css`, and browser code `static/branding.js`.
- Logged branding updates and logo changes into the audit trail.
- Added logo assets to the database backup workflow.
- Added 3 integration tests covering permissions, persistence, image validation and branch isolation.
- Original 13 backend integration checks remain passing.

# Changelog — Three-Phase POS Update

## V2.0 — November 2026

### Phase 1
- Preserved the original POS register/order/payment workflow.
- Added cashier opening/closing shifts and cash drawer variance.
- Added administrator-only partial/full refund entries with reason and optional full-return restocking.
- Added table transfer and the redesigned multi-zone visual floor.
- Added browser-print KOT tickets and live kitchen updates with Server-Sent Events.
- Added audit records for orders, payments, staff, shifts, inventory, refunds and branch changes.

### Phase 2
- Added ingredient catalog, recipes, purchasing, stock movements and ingredient-level checkout validation.
- Added supplier and expense tracking, customer CRM and a points ledger.
- Added recipe cost snapshots, cost recovery for restocked refunds, advanced financial analytics and CSV exports.

### Phase 3
- Added branch-scoped orders/menus/tables, multi-branch switching and independent demo branch creation.
- Added public guest/table QR menus, takeaway and delivery online ordering (pay later), driver/status board.
- Added a local/offline browser draft and manual idempotent submission workflow.

### Testing
- Retains and passes the seven original core integration tests.
- Adds and passes six advanced integration tests.
- JavaScript client syntax validated using Node.js.
- Desktop/mobile client rendering checked using an isolated mock-data browser harness (no horizontal overflow or JS errors).
- Older starter SQLite DB migration smoke test passed (`PRAGMA integrity_check = ok`).

### Known release limits
- No payment gateway or card terminal integration.
- Offline drafts are **not** an offline transaction-processing engine or automatic full synchronization.
- Local/UTC reporting and restaurant tax/fiscal compliance require regional configuration.
- SQLite suitable for pilot-scale single-server usage; high-scale multi-site deployment requires centralized infrastructure and further engineering.
