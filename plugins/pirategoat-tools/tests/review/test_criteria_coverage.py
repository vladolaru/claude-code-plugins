"""Criteria-coverage suite — every registry triage criterion is an executable contract.

Why this exists: three post-1.107.0 review rounds each found dispatch false
negatives that were already written down in `agent_registry.json` as
`triage_criteria` bullets — "CSS/SCSS affecting focus indicators" (no check
covered it), "speak() calls" (no token), "public function signature changes"
(api-contract had no checks). The criteria were prose; the keywords/checks
were the executable subset; nothing verified the subset spans the prose.

This suite closes that loop:

1. **One minimal probe per criterion.** For every conditional agent, every
   `triage_criteria` bullet has at least one probe — the smallest realistic
   diff satisfying that criterion — that MUST dispatch through the real
   pipeline (`decide_agent_dispatch` + real registry), i.e. through domain
   gating, explicit applicability gates, and conservative fallback routing.
2. **Completeness is enforced.** A meta-test asserts the probed criterion
   strings exactly match the registry's — adding or rewording a criterion
   without a probe fails CI instead of failing in review three weeks later.

Probes default to a handful of lines so they describe one criterion with as
little incidental signal as possible. Size-based criteria (large PRs,
substantial additions) size their probes accordingly.

When a probe fails: either give the agent a backing signal (keyword /
triage check — prefer structural checks for structural criteria) or reword
the criterion to match what the machinery can honestly promise. Never
weaken the probe to pass.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
TESTS_DIR = Path(__file__).resolve().parent.parent  # review/ -> tests/
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"

_spec = importlib.util.spec_from_file_location(
    "plan_review_dispatch_cov", str(SCRIPTS_DIR / "review" / "plan_dispatch.py")
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

load_registry = _mod.load_registry
decide_agent_dispatch = _mod.decide_agent_dispatch
build_domain_counts = _mod.build_domain_counts


@pytest.fixture(scope="module")
def agents():
    return load_registry()["agents"]


# ---------------------------------------------------------------------------
# Probe helper
# ---------------------------------------------------------------------------

def probe(
    criterion,
    files,
    stats=None,
    commits="",
    diff="",
    pr="",
    repository="",
    added_files=(),
    deleted_files=(),
    renamed_files=(),
):
    """Build a probe: the smallest realistic change satisfying `criterion`.

    stats: {file: (added, removed)} — defaults to (5, 2) per file so the
    probe stays minimal and criterion-specific.
    """
    if stats is None:
        stats = {f: (5, 2) for f in files}
    diffstat = {
        "added": sum(a for a, _ in stats.values()),
        "removed": sum(r for _, r in stats.values()),
        "added_files": list(added_files),
        "deleted_files": list(deleted_files),
        "renamed_files": list(renamed_files),
        "file_stats": {f: {"added": a, "removed": r} for f, (a, r) in stats.items()},
    }
    return {
        "criterion": criterion,
        "files": files,
        "commits": commits,
        "diff": diff,
        "pr": pr,
        "repository": repository,
        "diffstat": diffstat,
    }


# ---------------------------------------------------------------------------
# The probes — one or more per criterion, keyed by agent.
# Criterion strings must match agent_registry.json VERBATIM (the meta-test
# enforces set equality, so a registry edit forces a probe update here).
# ---------------------------------------------------------------------------

CRITERIA_PROBES = {
    "a11y-reviewer": [
        probe(
            "Markup emission added or removed in ANY language that renders UI — JSX/TSX components, PHP echoing HTML, template files (labels, fieldsets, ARIA attributes, interactive elements)",
            ["includes/admin/class-wc-admin-settings.php"],
            diff='+<label for="woocommerce_currency">Currency</label>',
        ),
        probe(
            "Markup emission added or removed in ANY language that renders UI — JSX/TSX components, PHP echoing HTML, template files (labels, fieldsets, ARIA attributes, interactive elements)",
            ["templates/checkout/form.twig"],
            diff='+<label for="email">{{ label }}</label>',
        ),
        probe(
            "Markup emission added or removed in ANY language that renders UI — JSX/TSX components, PHP echoing HTML, template files (labels, fieldsets, ARIA attributes, interactive elements)",
            ["includes/admin/class-wc-settings-page.php"],
            diff="+\t\tsubmit_button( __( 'Save changes', 'woocommerce' ) );",
        ),
        probe(
            "ARIA attributes or focus management code",
            ["src/components/Modal.tsx"],
            diff='+ <div aria-modal="true" role="dialog">',
        ),
        probe(
            "New UI components or significant visual changes",
            ["src/components/DatePicker.tsx"],
            stats={"src/components/DatePicker.tsx": (30, 0)},
            added_files=["src/components/DatePicker.tsx"],
            diff="+ <button onClick={open}>Pick date</button>",
        ),
        probe(
            "CSS/SCSS affecting visibility, focus indicators, or contrast",
            ["src/styles/focus.scss"],
            diff="+ outline: 2px solid var(--focus-ring);",
        ),
        probe(
            "Screen reader announcements: speak() calls, aria-live regions, live region wiring",
            ["src/store/notices.ts"],
            diff="+ speak( message, 'polite' );",
        ),
        probe(
            "Screen reader announcements: speak() calls, aria-live regions, live region wiring",
            ["src/components/StatusRegion.tsx"],
            diff="+ liveRegion.setAttribute('aria-live', 'polite');",
        ),
        probe(
            "Focus management in hooks or utilities (programmatic .focus(), focus restoration logic)",
            ["src/hooks/useFocusReturn.ts"],
            diff="+ previousElement.focus();",
        ),
        probe(
            "Commits mentioning accessibility, a11y, keyboard, screen reader, ARIA",
            ["includes/class-renderer.php"],
            commits="improve keyboard accessibility of settings rows",
            diff="+ $rows = $this->sort( $rows );",
        ),
    ],
    "api-contract-reviewer": [
        probe(
            "REST API endpoint additions or modifications",
            ["includes/rest/class-orders-controller.php"],
            diff="+ register_rest_route( 'wc/v4', '/orders', array( 'callback' => $cb ) );",
        ),
        probe(
            "Hook/filter argument or return type changes",
            ["includes/class-wc-order.php"],
            diff=(
                "-return apply_filters( 'wc_order_total', $total );\n"
                "+return apply_filters( 'wc_order_total', $total, $order );"
            ),
        ),
        probe(
            "Response schema or DTO class modifications",
            ["src/Responses/OrderResponse.php"],
            diff=(
                "-    public function get_total() {\n"
                "+    public function get_total( bool $with_tax = true ) {"
            ),
        ),
        probe(
            "Response schema or DTO class modifications",
            ["includes/rest/class-orders-controller.php"],
            diff="+ $schema['properties']['refund_total'] = array( 'type' => 'number' );",
        ),
        probe(
            "Response schema or DTO class modifications",
            ["src/DTO/OrderStatus.php"],
            diff=(
                "-    public string $status;\n"
                "+    public ?string $status;"
            ),
        ),
        probe(
            "Response schema or DTO class modifications",
            ["src/dto/OrderStatus.ts"],
            diff=(
                "-  status: string;\n"
                "+  status?: string;"
            ),
        ),
        probe(
            "Public function signature changes (parameters, return types)",
            ["src/PaymentGatewayInterface.php"],
            diff=(
                "-    public function process( $order ) {\n"
                "+    public function process( $order, $currency ) {"
            ),
        ),
        probe(
            "Public function signature changes (parameters, return types)",
            ["internal/store/orders.go"],
            diff=(
                "-func ExportedName(ctx context.Context) (string, error) {\n"
                "-\treturn lookup(ctx)\n"
                "-}"
            ),
        ),
        probe(
            "Database migration files (schema contract)",
            ["db/migrations/20260716_add_orders_table.sql"],
            diff="+CREATE TABLE wp_wc_orders ( id BIGINT UNSIGNED );",
        ),
        probe(
            "Commits mentioning API, endpoint, contract, breaking, deprecate, backwards",
            ["includes/class-api.php"],
            commits="deprecate legacy checkout endpoint",
            diff="+ // moved to v4",
        ),
    ],
    "architecture-reviewer": [
        probe(
            "New classes, interfaces, or abstract types added",
            ["src/Payments/PaymentRouter.php"],
            diff="+class PaymentRouter {",
        ),
        probe(
            "Files spanning 3+ architectural layers",
            [
                "src/api/OrdersController.php",
                "src/services/OrderService.php",
                "src/repositories/OrderRepository.php",
            ],
            diff="+ $order = $this->resolve( $id );",
        ),
        probe(
            "Commits mentioning architecture, refactor, restructure, decouple, extract",
            ["src/Checkout.php"],
            commits="refactor order pipeline into stages",
            diff="+ $stages = $this->build_stages();",
        ),
        probe(
            "Large PRs (20+ files or 500+ lines)",
            ["src/Checkout.php"],
            stats={"src/Checkout.php": (520, 30)},
            diff="+ $stages = $this->build_stages();",
        ),
        probe(
            "Large PRs (20+ files or 500+ lines)",
            [f"src/orders/step_{i}.php" for i in range(21)],
            stats={f"src/orders/step_{i}.php": (2, 1) for i in range(21)},
            diff="+ $x = 1;",
        ),
        probe(
            "New modules or packages introduced",
            ["src/Inventory/StockSync.php"],
            commits="introduce inventory package for stock sync",
            added_files=["src/Inventory/StockSync.php"],
            diff="+class StockSync {",
        ),
        probe(
            "New modules or packages introduced",
            ["scripts/util/parsing.py"],
            commits="add parsing helpers",
            added_files=["scripts/util/parsing.py"],
            diff="+def parse_header(raw):\n+    return raw.strip()",
        ),
    ],
    "code-clarity-reviewer": [
        probe(
            "New functions, methods, or classes, or modifications to signatures and docblocks",
            ["includes/class-wc-totals.php"],
            diff="+    public function calculate_totals( $order ) {",
        ),
        probe(
            "Renamed symbols (functions, classes, variables)",
            ["src/utils/format.ts"],
            commits="rename ambiguous helper to formatOrderTotal",
            diff="+ const formatOrderTotal = (v) => v;",
        ),
        probe(
            "Renamed symbols (functions, classes, variables)",
            ["includes/class-wc-totals.php"],
            diff=(
                "-    public function calc( $order ) {\n"
                "+    public function calculate_totals( $order ) {"
            ),
        ),
        probe(
            "Renamed symbols (functions, classes, variables)",
            ["src/util/format.js"],
            diff=(
                "-  const tmp = items.filter(active);\n"
                "-  return tmp.length;\n"
                "+  const filteredItems = items.filter(active);\n"
                "+  return filteredItems.length;"
            ),
        ),
        probe(
            "Modified or added docblocks, JSDoc, or PHPDoc comments",
            ["includes/class-wc-order.php"],
            diff="+ * @param int $order_id Order identifier.",
        ),
        probe(
            "New files introducing public API surface",
            ["src/api/version.ts"],
            added_files=["src/api/version.ts"],
            diff="+export const API_VERSION = 'v2';",
        ),
        probe(
            "New files introducing public API surface",
            ["src/Export/csv-exporter.php"],
            added_files=["src/Export/csv-exporter.php"],
            diff="+function wc_export_orders_csv( $args ) {",
        ),
        probe(
            "Function signature changes (parameters, return types)",
            ["includes/class-wc-cart.php"],
            diff=(
                "-    public function add_fee( $name, $amount ) {\n"
                "+    public function add_fee( $name, $amount, $taxable = false ) {"
            ),
        ),
        probe(
            "Inline comments added or modified in changed code",
            ["includes/class-wc-totals.php"],
            diff="+ $total = $subtotal; // shipping is added later by recalculate()",
        ),
    ],
    "concurrency-reviewer": [
        probe(
            "Async/await or Promise patterns in JavaScript",
            ["src/checkout/submit.ts"],
            diff="+ await processPayment( order );",
        ),
        probe(
            "Async/await or Promise patterns in JavaScript",
            ["src/checkout/retry.ts"],
            diff="+ return new Promise((resolve, reject) => attempt(resolve, reject));",
        ),
        probe(
            "Database transaction blocks or direct query sequences",
            ["includes/class-orders-store.php"],
            diff="+ $wpdb->query( 'START TRANSACTION' );",
        ),
        probe(
            "Database transaction blocks or direct query sequences",
            ["includes/class-stock-store.php"],
            diff=(
                "+ $wpdb->query( $reserve_sql );\n"
                "+ $wpdb->query( $decrement_sql );"
            ),
        ),
        probe(
            "Background job or queue handler code",
            ["includes/class-webhook-dispatcher.php"],
            diff="+ $this->queue->push( $job );",
        ),
        probe(
            "Background job or queue handler code",
            ["includes/class-stock-jobs.php"],
            diff="+ as_enqueue_async_action( 'wc_reserve_stock', array( $order_id ) );",
        ),
        probe(
            "WordPress cron or scheduled event handlers",
            ["includes/class-stock-sync.php"],
            diff="+ wp_schedule_event( time(), 'hourly', 'wc_sync_stock' );",
        ),
        probe(
            "Cache read-write sequences (transients, object cache)",
            ["includes/class-rates.php"],
            diff="+ set_transient( 'wc_rates', $rates, HOUR_IN_SECONDS );",
        ),
        probe(
            "Cache read-write sequences (transients, object cache)",
            ["includes/class-session-store.php"],
            diff="+ wp_cache_set( $key, $value, 'wc-session' );",
        ),
        probe(
            "Order or payment processing flows (idempotency, duplicate suppression, capture races)",
            ["includes/class-payment-capture.php"],
            diff="+ $idempotency_key = $order->get_id() . ':capture';",
        ),
        probe(
            "Order or payment processing flows (idempotency, duplicate suppression, capture races)",
            ["includes/class-payment-flow.php"],
            diff="+ $result = $gateway->capture( $intent_id );",
        ),
        probe(
            "Commits mentioning async, concurrent, race, transaction, lock, queue",
            ["includes/class-webhooks.php"],
            commits="fix race condition in webhook delivery",
            diff="+ $delivered = $this->mark_delivered( $id );",
        ),
    ],
    "data-flow-privacy-reviewer": [
        probe(
            "Code handling personal identifiers (emails, addresses, IPs, usernames)",
            ["src/checkout/logger.ts"],
            diff="+ logger.info({ customerEmail });",
        ),
        probe(
            "Code handling personal identifiers (emails, addresses, IPs, usernames)",
            ["includes/class-rate-limiter.php"],
            diff="+ $key = 'rl_' . md5( $_SERVER['REMOTE_ADDR'] );",
        ),
        probe(
            "Logging or monitoring additions (error_log, WC_Logger, Sentry, New Relic)",
            ["includes/class-checkout.php"],
            diff="+ $logger = wc_get_logger(); $logger->info( wp_json_encode( $payload ) );",
        ),
        probe(
            "Logging or monitoring additions (error_log, WC_Logger, Sentry, New Relic)",
            ["src/monitoring/errors.ts"],
            diff="+ Sentry.captureException(err, { extra: context });",
        ),
        probe(
            "API responses or serialization carrying user-identifiable data",
            ["includes/rest/class-orders-controller.php"],
            diff="+ $response['billing_address'] = $order->get_billing_address();",
        ),
        probe(
            "Database schema additions storing personal data",
            ["db/migrations/20260716_add_emails.sql"],
            diff="+ALTER TABLE wp_users ADD COLUMN customer_email VARCHAR(255);",
        ),
        probe(
            "Payment or financial data processing",
            ["includes/class-gateway.php"],
            diff="+ $intent = $gateway->capture( $card_number_token );",
        ),
        probe(
            "Payment or financial data processing",
            ["includes/class-order-handler.php"],
            diff="+ $result = $this->process_payment( $order->get_total(), $token );",
        ),
        probe(
            "Data export, import, or migration handlers",
            ["includes/class-exporter.php"],
            diff="+function export_user_data( $user_id ) {",
        ),
        probe(
            "GDPR/privacy-related erasure or consent handlers",
            ["includes/class-privacy.php"],
            diff="+ register_privacy_erasers( 'woocommerce', $erasers );",
        ),
    ],
    "dead-code-reviewer": [
        probe(
            "Files deleted or renamed",
            ["includes/class-cart.php"],
            deleted_files=["includes/legacy-cart.php"],
            diff="+ // superseded by Cart",
        ),
        probe(
            "Significant code removal (removed > added)",
            ["includes/class-cart.php"],
            stats={"includes/class-cart.php": (2, 40)},
            diff="- legacy_recalculate();",
        ),
        probe(
            "Refactoring commits (extract, move, rename, consolidate, remove, delete)",
            ["includes/class-shipping.php"],
            commits="extract shipping calculator into service",
            diff="+ $this->calculator->run( $package );",
        ),
        probe(
            "Import/require statements added or removed",
            ["src/utils/debounce.ts"],
            diff="-import { debounce } from 'lodash';",
        ),
        probe(
            "New files replacing or superseding existing ones",
            ["src/OrderService.php"],
            added_files=["src/OrderService.php"],
            deleted_files=["includes/class-order-helper.php"],
            diff="+class OrderService {",
        ),
        probe(
            "New files replacing or superseding existing ones",
            ["src/parser_v2.py"],
            added_files=["src/parser_v2.py"],
            diff="+def parse(raw):\n+    return raw.strip()",
        ),
    ],
    "devils-advocate-reviewer": [
        # min_added_lines=50 — probes must be substantial by design.
        probe(
            "New classes, interfaces, or module-level abstractions introduced",
            ["src/Cache/OrderCacheAdapter.php"],
            stats={"src/Cache/OrderCacheAdapter.php": (60, 0)},
            added_files=["src/Cache/OrderCacheAdapter.php"],
            diff="+class OrderCacheAdapter {",
        ),
        probe(
            "New infrastructure components (database tables, caches, queues, background jobs)",
            ["src/Webhooks/Delivery.php"],
            stats={"src/Webhooks/Delivery.php": (60, 0)},
            commits="introduce redis queue for webhook delivery",
            diff="+ $this->redis->rpush( self::QUEUE, $payload );",
        ),
        probe(
            "Compatibility shims or adapter layers added",
            ["src/Compat/LegacyGatewayShim.php"],
            stats={"src/Compat/LegacyGatewayShim.php": (60, 0)},
            commits="add compat shim for legacy gateways",
            diff="+class LegacyGatewayShim {",
        ),
        probe(
            "Workarounds for upstream limitations",
            ["src/Sync/BatchRunner.php"],
            stats={"src/Sync/BatchRunner.php": (60, 0)},
            commits="workaround for upstream cron double-fire limitation",
            diff="+ if ( $this->already_ran( $tick ) ) { return; }",
        ),
        probe(
            "Substantial new logic (50+ added lines in non-test files)",
            ["src/Pricing/Engine.php"],
            stats={"src/Pricing/Engine.php": (60, 0)},
            diff="+ $price = $this->apply_rules( $base, $rules );",
        ),
    ],
    "docs-drift-reviewer": [
        probe(
            "New, renamed, or removed public functions, classes, or endpoints",
            ["internal/store/orders.go"],
            diff=(
                "-func ExportedName(ctx context.Context) (string, error) {\n"
                "-\treturn lookup(ctx)\n"
                "-}"
            ),
        ),
        probe(
            "New, renamed, or removed public functions, classes, or endpoints",
            ["internal/store/orders.go"],
            diff=(
                "+func ExportedName(ctx context.Context) (string, error) {\n"
                "+\treturn lookup(ctx)\n"
                "+}"
            ),
        ),
        probe(
            "New, renamed, or removed public functions, classes, or endpoints",
            ["src/orders/api.py"],
            diff="+def get_order(order_id):\n+    return _load(order_id)",
        ),
        probe(
            "New, renamed, or removed public functions, classes, or endpoints",
            ["src/orders/parse.ts"],
            diff="+export function parseOrderId( raw: string ): number {",
        ),
        probe(
            "Configuration option additions or removals",
            ["includes/class-install.php"],
            diff="+ add_option( 'wc_enable_new_checkout', 'no' );",
        ),
        probe(
            "Configuration option additions or removals",
            ["src/config/defaults.js"],
            diff="+  maxUploadSize: 1024,",
        ),
        probe(
            "Behavioral changes flagged in commits (deprecations, renames, migrations, restructuring)",
            ["includes/class-webhooks.php"],
            commits="migrate legacy webhook settings to the new store",
            diff="+ $store->put( $settings );",
        ),
        probe(
            "File renames or directory restructuring",
            ["docs/getting-started.md"],
            renamed_files=["docs/getting-started.md"],
            diff="+# Getting started",
        ),
        probe(
            "Hook signature changes or new hooks added",
            ["includes/class-checkout.php"],
            diff="+do_action( 'wc_after_checkout_processed', $order );",
        ),
    ],
    "ecosystem-integration-reviewer": [
        probe(
            "Diff touches PHP WordPress/WooCommerce integration surfaces. Explicit hook/registration APIs are strong signals, but upstream subclass/callback signature changes may still need review without those keywords.",
            ["src/OrdersController.php"],
            diff=(
                "+class OrdersController extends WC_REST_Orders_Controller {\n"
                "+    public function prepare_item_for_response( $object, $request ) {}\n"
                "+}"
            ),
        ),
    ],
    "history-insights-reviewer": [
        # Its value comes from the touched file's git history, so every
        # in-domain modification is relevant under conservative dispatch.
        probe(
            "PR modifies existing code that has meaningful git history (prior fixes, enhancements, refactors)",
            ["includes/class-wc-cart.php"],
            diff="+ $this->calculate_totals();",
        ),
        probe(
            "Changed files touch areas with known past issues or multiple prior contributors",
            ["includes/class-wc-checkout.php"],
            diff="+ $this->validate_posted_data( $data );",
        ),
        probe(
            "PR refactors or restructures code where the team may have learned lessons from earlier attempts",
            ["includes/class-wc-session.php"],
            commits="restructure session persistence",
            diff="+ $this->save_data();",
        ),
    ],
    "performance-reviewer": [
        # Split from one compound bullet: each clause needs its OWN signal —
        # the compound form was probed only via $wpdb, so session.query() and
        # requests.get() changes silently skipped.
        probe(
            "Database query construction or ORM usage",
            ["src/reports/store.py"],
            diff="+ rows = session.query(Order).filter_by(status='paid').all()",
        ),
        probe(
            "Database query construction or ORM usage",
            ["src/Orders/OrderRepository.php"],
            diff="+        $orders = Order::where( 'status', 'open' )->get();",
        ),
        probe(
            "Database query construction or ORM usage",
            ["src/reports/rollup.py"],
            diff='+    cursor.execute("SELECT id, total FROM orders")',
        ),
        probe(
            "Database query construction or ORM usage",
            ["src/reports/rollup.py"],
            diff=(
                '     rows = cursor.execute("""\n'
                "         SELECT id, total FROM orders\n"
                "-        ORDER BY created_at DESC\n"
                "+        ORDER BY created_at DESC, id DESC"
            ),
        ),
        probe(
            "Database query construction or ORM usage",
            ["migrations/0042_order_index.sql"],
            diff="+CREATE INDEX idx_orders_created ON orders (created_at);",
        ),
        probe(
            "Database query construction or ORM usage",
            ["src/reports/rollup.py"],
            diff="+        JOIN order_items ON order_items.order_id = orders.id",
        ),
        probe(
            "Database query construction or ORM usage",
            ["src/reports/rollup.py"],
            diff="+        WHERE status = 'open' AND total > 100",
        ),
        probe(
            "HTTP/API client calls",
            ["src/sync/client.py"],
            diff="+ response = requests.get(api_url, timeout=10)",
        ),
        probe(
            "HTTP/API client calls",
            ["internal/sync/client.go"],
            diff="+\tresp, err := http.Get(url)",
        ),
        probe(
            "Data fetching hooks and loaders",
            ["src/orders/useOrders.ts"],
            diff="+ const orders = await fetchOrders({ page });",
        ),
        probe(
            "Pagination and bulk operations",
            ["includes/class-stock.php"],
            diff="+ function bulk_update_stock( $ids ) {",
        ),
        probe(
            "Pagination and bulk operations",
            ["includes/class-orders-table.php"],
            diff="+ $orders = wc_get_orders( array( 'posts_per_page' => -1 ) );",
        ),
        probe(
            "Unbounded list rendering and collection iteration",
            ["src/components/OrderList.tsx"],
            diff=(
                "+      {orders.map((order) => (\n"
                "+        <li key={order.id}>{order.title}</li>\n"
                "+      ))}"
            ),
        ),
        probe(
            "Unbounded list rendering and collection iteration",
            ["src/components/OrderList.tsx"],
            diff=(
                "+  for (const order of orders) {\n"
                "+    rows.push(renderRow(order));\n"
                "+  }"
            ),
        ),
        probe(
            "Asset loading, lazy loading, code splitting",
            ["includes/class-assets.php"],
            diff="+ wp_enqueue_script( 'wc-admin', $url, array(), $ver, true );",
        ),
        probe(
            "Asset loading, lazy loading, code splitting",
            ["src/components/index.ts"],
            diff="+ const AnalyticsPanel = lazy(() => import('./AnalyticsPanel'));",
        ),
        probe(
            "Caching logic (transients, object cache, memoization)",
            ["includes/class-reports.php"],
            diff="+ set_transient( 'wc_report_cache', $data, DAY_IN_SECONDS );",
        ),
        probe(
            "Caching logic (transients, object cache, memoization)",
            ["src/orders/useTotals.ts"],
            diff="+ const totals = useMemo(() => computeTotals(rows), [rows]);",
        ),
        probe(
            "Commits mentioning performance, optimize, cache, query, load time",
            ["includes/class-orders-query.php"],
            commits="optimize slow order lookup query",
            diff="+ $ids = $this->lookup( $args );",
        ),
    ],
    "reference-integrity-reviewer": [
        probe(
            "Plugin or package registry declarations (slug, name, type mappings)",
            ["src/PaymentGateways/registry.json"],
            diff='+  "slug": "woocommerce-payments",',
        ),
        probe(
            "Asset path constructions (plugins_url, wp_enqueue_script/style, icon paths, image references)",
            ["includes/class-admin-assets.php"],
            diff="+ wp_enqueue_style( 'wc-admin', plugins_url( 'assets/admin.css', __FILE__ ) );",
        ),
        # NOTE: probes deliberately avoid other reference-integrity keywords
        # ('payment', 'gateway', ...) so each criterion is proven by ITS
        # backing signal, not by accidental co-occurrence.
        probe(
            "URL literals in configuration arrays or constants (docs links, API endpoints, CDN references)",
            ["includes/class-links-config.php"],
            diff="+ 'docs_link' => 'https://example.com/document/shipping/',",
        ),
        probe(
            "External constant or class references in data/config arrays",
            ["includes/class-handlers-config.php"],
            diff="+ 'handler' => \\Vendor\\Shipping\\LabelPrinter::class,",
        ),
        probe(
            "New dependency declarations (composer.json require, package.json dependencies)",
            ["composer.json"],
            diff='+    "vendor/payments-sdk": "^2.0",',
        ),
        probe(
            "New dependency declarations (composer.json require, package.json dependencies)",
            ["package.json"],
            diff='+    "lodash": "^4.17.21",',
        ),
        probe(
            "Hook or filter names referencing external plugins",
            ["includes/class-compat.php"],
            diff="+ add_action( 'elementor/init', $cb );",
        ),
        probe(
            "Enum values mapping to external standards (country codes, currency codes, status codes)",
            ["includes/class-currencies.php"],
            diff="+ 'currency' => 'EUR',",
        ),
    ],
    "reliability-reviewer": [
        probe(
            "Database migrations or schema changes",
            ["db/migrations/20260716_add_orders_table.sql"],
            diff="+CREATE TABLE wp_wc_orders ( id BIGINT UNSIGNED );",
        ),
        probe(
            "External service integrations or API client changes",
            ["internal/sync/client.go"],
            diff="+\tresp, err := http.Get(url)",
        ),
        probe(
            "External service integrations or API client changes",
            ["src/Clients/TaxServiceClient.php"],
            diff="+ $client = new ExternalTaxClient( $config );",
        ),
        probe(
            "External service integrations or API client changes",
            ["includes/class-tracker.php"],
            diff="+ $response = wp_remote_post( $url, array( 'body' => $payload ) );",
        ),
        probe(
            "Error handling or retry logic modifications",
            ["src/Webhooks/Delivery.php"],
            diff="+ return $this->retry( $callback, 3 );",
        ),
        probe(
            "Error handling or retry logic modifications",
            ["includes/class-importer.php"],
            diff="+ } catch ( Exception $e ) { $failed[] = $row; }",
        ),
        probe(
            "Feature flag or kill-switch changes",
            ["includes/class-features.php"],
            diff="+ if ( FeatureFlags::enabled( 'new_checkout' ) ) {",
        ),
        probe(
            "Feature flag or kill-switch changes",
            ["src/checkout/init.php"],
            diff="+ if ( ! Features::is_enabled( 'new_checkout' ) ) {",
        ),
        probe(
            "Deployment configuration or infrastructure changes",
            [".github/workflows/deploy.yml"],
            diff="+      - run: ./bin/deploy production",
        ),
        probe(
            "Deployment configuration or infrastructure changes",
            ["infra/main.tf"],
            diff='+resource "aws_sqs_queue" "webhooks" {}',
        ),
        probe(
            "Background job or queue processing changes",
            ["includes/class-jobs.php"],
            diff="+ $this->queue->dispatch( $job );",
        ),
        probe(
            "Caching layer modifications",
            ["includes/class-object-cache.php"],
            diff="+ wp_cache_set( $key, $value, 'wc-orders' );",
        ),
    ],
    "security-reviewer": [
        # One-line regressions are this domain's norm; every in-domain probe
        # must dispatch regardless of deterministic vocabulary.
        probe(
            "New or modified endpoints accepting external input",
            ["includes/rest/class-orders-controller.php"],
            diff="+ register_rest_route( 'wc/v4', '/orders', array( 'callback' => $cb ) );",
        ),
        probe(
            "Code processing user-supplied data (form fields, query params, request bodies, file uploads)",
            ["includes/class-form-handler.php"],
            diff="+ echo $_GET['name'];",
        ),
        probe(
            "Database operations (reads, writes, raw queries)",
            ["includes/class-orders-store.php"],
            diff='+ $wpdb->query( "DELETE FROM {$table} WHERE id = {$id}" );',
        ),
        probe(
            "Dynamic content rendered to output",
            ["includes/class-renderer.php"],
            diff="+ echo $description;",
        ),
        probe(
            "Auth, authorization, or session management changes",
            ["includes/class-session.php"],
            diff="+ if ( current_user_can( 'manage_woocommerce' ) ) {",
        ),
        probe(
            "File system operations with user-influenced paths",
            ["includes/class-downloads.php"],
            diff="+ readfile( $base . $_GET['file'] );",
        ),
        probe(
            "Third-party API or webhook integrations",
            ["includes/class-webhooks.php"],
            diff="+ wp_remote_post( $url, array( 'body' => $payload ) );",
        ),
        probe(
            "Cryptographic or secret/token handling",
            ["includes/class-api-keys.php"],
            diff="+ $signature = hash_hmac( 'sha256', $payload, $secret );",
        ),
        probe(
            "Commits introducing new entry points or data processing",
            ["includes/class-ajax.php"],
            commits="add ajax handler for coupon lookup",
            diff="+ add_action( 'wp_ajax_wc_lookup_coupon', $cb );",
        ),
        probe(
            "CI/CD configuration changes (workflow files, pipeline configs)",
            [".github/workflows/ci.yml"],
            diff="+      - uses: actions/checkout@v4",
        ),
        probe(
            "Infrastructure-as-code changes (Terraform, Helm, Docker)",
            ["infra/main.tf"],
            diff='+resource "aws_s3_bucket" "exports" {}',
        ),
    ],
    "toolchain-reviewer": [
        probe(
            "Package manager config changes (pnpm-workspace.yaml, .npmrc, package.json engines/scripts)",
            [".npmrc"],
            diff="+registry=https://registry.example.com/",
        ),
        probe(
            "Build tool config changes (webpack, vite, esbuild, turbo, nx)",
            ["webpack.config.js"],
            diff="+ mode: 'production',",
        ),
        probe(
            "Build tool config changes (webpack, vite, esbuild, turbo, nx)",
            ["nx.json"],
            diff='+ "defaultBase": "main",',
        ),
        probe(
            "Linter or formatter config changes (ESLint, Prettier, PHPCS, PHPStan)",
            [".eslintrc.json"],
            diff='+ "no-console": "error",',
        ),
        probe(
            "Linter or formatter config changes (ESLint, Prettier, PHPCS, PHPStan)",
            [".stylelintrc"],
            diff='+ "selector-max-specificity": "0,3,0",',
        ),
        probe(
            "TypeScript or Babel config changes (tsconfig.json, babel.config.*)",
            ["tsconfig.json"],
            diff='+ "strict": true,',
        ),
        probe(
            "CI/CD pipeline changes (GitHub Actions workflows, GitLab CI)",
            [".github/workflows/ci.yml"],
            diff="+      - run: pnpm test",
        ),
        probe(
            "Version constraint changes (.nvmrc, engines, Docker base images)",
            [".nvmrc"],
            diff="+22.11.0",
        ),
        probe(
            "Dependency management changes (renovate, dependabot configs)",
            ["renovate.json"],
            diff='+ "rangeStrategy": "bump",',
        ),
        probe(
            "Tool version upgrades or migrations",
            ["package.json"],
            commits="upgrade webpack to v6",
            diff='+    "webpack": "^6.0.0",',
        ),
        probe(
            "Dev environment config changes (wp-env, Docker Compose)",
            [".wp-env.json"],
            diff='+ "phpVersion": "8.3",',
        ),
        probe(
            "Supply chain security settings (allowBuilds, lockfile config, strictDepBuilds)",
            ["pnpm-workspace.yaml"],
            diff="+allowBuilds: []",
        ),
    ],
    "woo-regression-reviewer": [
        probe(
            "Diff belongs to WooCommerce core or a WooCommerce extension (WooPayments, AutomateWoo, etc.)",
            ["includes/class-renderer.php"],
            diff="+ $rows = $this->sort( $rows );",
            repository="https://github.com/woocommerce/woocommerce.git\nwoocommerce",
        ),
        probe(
            "PHP changes touching hooks, scheduled actions, meta/options, templates, interfaces, or validators in a WooCommerce codebase",
            ["includes/class-wc-order.php"],
            diff="+ update_post_meta( $order_id, '_wc_capture_flag', $value );",
        ),
    ],
    "wp-architecture-reviewer": [
        # Split from one compound bullet — the options/transients/REST branch
        # had no signal of its own (probed only via add_filter).
        probe(
            "PHP registering or consuming hooks and filters",
            ["includes/class-currency.php"],
            diff="+ add_filter( 'woocommerce_currency', $cb );",
        ),
        probe(
            "WordPress options, transients, or REST API usage",
            ["includes/class-features.php"],
            diff="+ $flag = get_option( 'wc_new_flag', 'no' );",
        ),
        probe(
            "WooCommerce-specific files",
            ["plugins/woocommerce/includes/class-wc-cart.php"],
            diff="+ $this->calculate_totals();",
        ),
        probe(
            # WC core checked out directly: paths are ROOT-relative — the
            # monorepo prefix must not be the only carrier of the signal.
            "WooCommerce-specific files",
            ["includes/class-wc-cart.php"],
            diff="+\t\t$this->calculate_totals();",
        ),
        probe(
            "Admin menus, settings pages, or custom post types",
            ["includes/class-post-types.php"],
            diff="+ register_post_type( 'wc_booking', $args );",
        ),
        probe(
            "Admin menus, settings pages, or custom post types",
            ["includes/class-settings-page.php"],
            diff="+ add_settings_field( 'wc_flag', $label, $cb, 'wc_settings' );",
        ),
        probe(
            "Admin menus, settings pages, or custom post types",
            ["includes/admin/class-tools-page.php"],
            diff="+ add_submenu_page( 'woocommerce', $title, $title, 'manage_options', 'wc-tools', $cb );",
        ),
        probe(
            "Commits mentioning hooks, filters, backwards compatibility, deprecation, i18n",
            ["includes/class-emails.php"],
            commits="preserve backwards compatibility for email hooks",
            diff="+ $mailer = $this->mailer();",
        ),
        probe(
            "Plugin bootstrap or activation/deactivation files",
            ["woocommerce.php"],
            diff="+ register_activation_hook( __FILE__, 'wc_install' );",
        ),
    ],
}

_FLAT = [
    (agent, i, p)
    for agent, probes in sorted(CRITERIA_PROBES.items())
    for i, p in enumerate(probes)
]


class TestCriteriaProbesDispatch:
    """Every criterion probe must dispatch through the real pipeline."""

    @pytest.mark.parametrize(
        "agent_name,idx,p",
        _FLAT,
        ids=[f"{a}:{i}:{p['criterion'][:50]}" for a, i, p in _FLAT],
    )
    def test_probe_dispatches(self, agents, agent_name, idx, p):
        config = agents[agent_name]
        status, reason = decide_agent_dispatch(
            agent_name,
            config,
            build_domain_counts(p["files"]),
            clean_files=p["files"],
            commit_messages=p["commits"],
            diffstat=p["diffstat"],
            pr_text=p["pr"],
            diff_text=p["diff"],
            repository_text=p["repository"],
        )
        assert status == "DISPATCH", (
            f"{agent_name} must dispatch for its criterion "
            f"{p['criterion']!r} — got {status} ({reason}). Give the agent a "
            f"backing signal (keyword / triage check) or reword the "
            f"criterion; never weaken the probe."
        )


class TestProbeNeutrality:
    """A probe must prove its criterion from the CHANGE ITSELF — diff,
    files, diffstat — unless the criterion is explicitly about commit/PR
    text. Without this, a clause can pass its coverage probe via a keyword
    smuggled into the probe's commit message while staying silently
    unsignaled for real diffs with neutral text (the round-8 class: eleven
    clauses skipped on small diffs despite every bullet having a passing
    probe)."""

    _TEXT_ORIENTED_MARKERS = ("commit", "pr ", " pr", "flagged in")

    @pytest.mark.parametrize(
        "agent_name,idx,p",
        _FLAT,
        ids=[f"{a}:{i}:{p['criterion'][:50]}" for a, i, p in _FLAT],
    )
    def test_probe_dispatches_without_commit_or_pr_text(
        self, agents, agent_name, idx, p
    ):
        crit = p["criterion"].lower()
        if any(marker in crit for marker in self._TEXT_ORIENTED_MARKERS):
            pytest.skip("criterion is explicitly about commit/PR text")
        config = agents[agent_name]
        status, reason = decide_agent_dispatch(
            agent_name,
            config,
            build_domain_counts(p["files"]),
            clean_files=p["files"],
            commit_messages="",
            diffstat=p["diffstat"],
            pr_text="",
            diff_text=p["diff"],
            repository_text=p["repository"],
        )
        assert status == "DISPATCH", (
            f"{agent_name}'s probe for {p['criterion']!r} only dispatches "
            f"via its commit/PR text — got {status} ({reason}) with neutral "
            f"text. Move the signal into the probe's diff/files/diffstat "
            f"(and back it with a diff-capable keyword or check), or reword "
            f"the criterion to say it is commit/PR-text-based."
        )


# ---------------------------------------------------------------------------
# Language matrix — language-GENERIC criteria probed per scoped language.
#
# The class of bug this prevents: a structural detector written for one
# language's surface syntax (PHP `public function`, JS `import`) silently
# gating a domain that spans ~30 languages. Review rounds kept finding the
# same hole one language at a time (Java signatures, Go func, Rust fn,
# Kotlin fun...). Each language family with distinct syntax gets a probe;
# a detector that only knows PHP fails here for every other language.
# ---------------------------------------------------------------------------

# Family → file extensions with representative positive probes. These tables
# document recognition; they do not claim that detector silence exhaustively
# excludes every criterion-relevant form in a language.
_MATRIX_FAMILY_EXTENSIONS = {
    "php": ["php", "phtml"],
    "typescript": ["js", "mjs", "cjs", "jsx", "ts", "tsx"],
    "python": ["py"],
    "ruby": ["rb"],
    "go": ["go"],
    "rust": ["rs"],
    "java": ["java"],
    "kotlin": ["kt", "kts"],
    "csharp": ["cs"],
    "swift": ["swift"],
    "scala": ["scala"],
}

_SIGNATURE_CHANGES = {
    "php": ("src/Cart.php",
            "-    public function add_fee( $n ) {\n+    public function add_fee( $n, $t ) {"),
    # Families may list MULTIPLE forms — one probe per distinct syntax shape.
    # TypeScript earned four the hard way: function declarations were covered
    # while interface members, class methods, and arrow functions silently
    # gated (the extension was "covered" on partial proof).
    "typescript": [
        ("src/orders/parse.ts",
         "-export function parse(raw: string): number {\n+export function parse(raw: string, strict: boolean): number {"),
        ("src/orders/shapes.ts",
         "-  getName(key: string): string;\n+  getName(key: string, loc: Locale): string;"),
        ("src/orders/OrderStore.ts",
         "-  async getName(key: string): Promise<string> {\n+  async getName(key: string, loc: Locale): Promise<string> {"),
        ("src/orders/format.ts",
         "-export const getName = (key: string): string =>\n+export const getName = (key: string, loc: Locale): string =>"),
        ("src/orders/normalize.js",
         "-const normalize = value => value.trim();\n+const normalize = raw => raw.trim().toLowerCase();"),
    ],
    "python": ("src/orders/parse.py",
               "-def parse(raw):\n+def parse(raw, strict=False):"),
    "ruby": ("app/models/order.rb",
             "-def total_for(order)\n+def total_for(order, currency)"),
    # Go carries two forms: func declarations and interface members —
    # interfaces are declared name-first (`type X interface {`), so member
    # changes need the type-body tracker with the Go opener (round-10 miss).
    "go": [
        ("internal/store/orders.go",
         "-func (s *Store) Name(ctx context.Context) error {\n+func (s *Store) Name(ctx context.Context, k string) error {"),
        ("internal/store/contract.go",
         " type OrderStore interface {\n"
         "-\tGetName(ctx context.Context, key string) (string, error)\n"
         "+\tGetName(ctx context.Context, key string, loc Locale) (string, error)\n"
         " }"),
    ],
    "rust": ("src/store/orders.rs",
             "-pub fn name(&self) -> String {\n+pub fn name(&self, key: &str) -> String {"),
    # Java carries two forms: modifier-prefixed and package-private —
    # methods with NO access modifier are legal Java and were silently
    # gated while the extension claimed coverage (round-9 miss).
    "java": [
        ("src/main/java/OrderStore.java",
         "-public String getName(String key) {\n+public String getName(String key, Locale l) {"),
        ("src/main/java/OrderMapper.java",
         "-OrderStatus resolveStatus(Order order) {\n+OrderStatus resolveStatus(Order order, Locale l) {"),
    ],
    "kotlin": ("src/main/kotlin/OrderStore.kt",
               "-fun name(key: String): String {\n+fun name(key: String, loc: Locale): String {"),
    "csharp": ("src/Orders/OrderStore.cs",
               "-public async Task<string> GetName(string key) {\n+public async Task<string> GetName(string key, Locale l) {"),
    "swift": ("Sources/Store/SessionStore.swift",
              "-func name(for key: String) -> String {\n+func name(for key: String, locale: Locale) -> String {"),
    "scala": ("src/main/scala/OrderStore.scala",
              "-def name(key: String): String = {\n+def name(key: String, locale: Locale): String = {"),
}

_TYPE_DECLARATIONS = {
    "php": ("src/Payments/OrderRouter.php", "+class OrderRouter {"),
    "typescript": ("src/orders/shapes.ts", "+export interface OrderShape {"),
    "python": ("src/orders/router.py", "+class OrderRouter:"),
    "ruby": ("app/models/order_router.rb", "+class OrderRouter"),
    "go": ("internal/store/orders.go", "+type OrderStore struct {"),
    "rust": ("src/store/orders.rs", "+pub struct OrderStore {"),
    "java": ("src/main/java/OrderRouter.java", "+public class OrderRouter {"),
    "kotlin": ("src/main/kotlin/Order.kt", "+data class Order(val id: Int)"),
    "csharp": ("src/Orders/OrderRouter.cs", "+public sealed class OrderRouter {"),
    "swift": ("Sources/Store/SessionStore.swift", "+actor SessionStore {"),
    "scala": ("src/main/scala/Order.scala", "+case class Order(id: Int)"),
}

_IMPORT_CHANGES = {
    "php": ("src/Orders/Router.php", "+use Vendor\\Orders\\Router;"),
    "typescript": ("src/utils/debounce.ts", "-import { debounce } from 'lodash';"),
    # Python and Rust carry two import forms each — the multiline block
    # member lines have no import token (round-11 miss).
    "python": [
        ("src/orders/export.py", "+from collections import OrderedDict"),
        ("src/orders/status.py",
         " from orders.types import (\n+    OrderStatus,\n )"),
    ],
    "ruby": ("app/models/order.rb", "-require 'json'"),
    "go": ("internal/store/orders.go", '+import "context"'),
    "rust": [
        ("src/store/orders.rs", "+use crate::orders::Router;"),
        ("src/store/routes.rs",
         " use crate::orders::{\n+    Router,\n };"),
    ],
    "java": ("src/main/java/OrderStore.java", "+import com.acme.orders.Router;"),
    "kotlin": ("src/main/kotlin/OrderStore.kt", "+import com.acme.orders.Router"),
    "csharp": ("src/Orders/OrderStore.cs", "+using System.Text.Json;"),
    "swift": ("Sources/Store/SessionStore.swift", "+import Foundation"),
    "scala": ("src/main/scala/OrderStore.scala", "+import com.acme.orders.Router"),
}

# REST/route registration surfaces per ecosystem. api-contract's "REST API
# endpoint additions or modifications" criterion is language-generic, but
# its detector rested on register_rest_route alone — a FastAPI decorator or
# an Express route carried no signal (round-10 miss: representative generic
# forms were incorrectly treated as exhaustive semantic coverage).
_ENDPOINT_REGISTRATIONS = {
    "php": [
        ("includes/rest/class-orders-controller.php",
         "+ register_rest_route( 'wc/v4', '/orders', array( 'callback' => $cb ) );"),
        ("routes/web.php",
         "+Route::get('/orders', [OrderController::class, 'index']);"),
    ],
    "typescript": [
        ("src/api/routes.ts", "+router.get('/orders/:id', getOrder);"),
        ("src/api/preflight.ts", "+router.options('/orders', preflight);"),
    ],
    "python": ("src/api/orders.py",
               '+@router.get("/orders/{order_id}")'),
    "ruby": ("config/routes.rb",
             "+  get '/orders/:id', to: 'orders#show'"),
    "go": [
        ("internal/api/routes.go",
         '+\thttp.HandleFunc("/orders", listOrders)'),
        ("internal/api/mux.go",
         '+\tr.HandleFunc("/orders", listOrders).Methods("GET")'),
    ],
    "rust": ("src/api/routes.rs",
             '+        .route("/orders", get(list_orders))'),
    "java": ("src/main/java/OrderController.java",
             '+    @GetMapping("/orders/{id}")'),
    "kotlin": ("src/main/kotlin/OrderRoutes.kt",
               '+    get("/orders/{id}") {'),
    "csharp": ("src/Orders/OrdersController.cs",
               '+    [HttpGet("orders/{id}")]'),
    "swift": ("Sources/App/routes.swift",
              '+    app.get("orders", ":id") { req in'),
    "scala": ("src/main/scala/OrderRoutes.scala",
              '+    case GET -> Root / "orders" / id =>'),
}


# (agent, criterion the matrix backs, probe map)

_LANGUAGE_MATRIX = [
    ("code-clarity-reviewer", "Function signature changes (parameters, return types)", _SIGNATURE_CHANGES),
    ("api-contract-reviewer", "Public function signature changes (parameters, return types)", _SIGNATURE_CHANGES),
    ("architecture-reviewer", "New classes, interfaces, or abstract types added", _TYPE_DECLARATIONS),
    ("dead-code-reviewer", "Import/require statements added or removed", _IMPORT_CHANGES),
    ("api-contract-reviewer", "REST API endpoint additions or modifications", _ENDPOINT_REGISTRATIONS),
]

def _forms(entry):
    """A family entry is one (file, diff) form or a list of forms."""
    return entry if isinstance(entry, list) else [entry]


_MATRIX_FLAT = [
    (agent, criterion, f"{lang}-{i}" if len(_forms(entry)) > 1 else lang, filepath, diff)
    for agent, criterion, tbl in _LANGUAGE_MATRIX
    for lang, entry in sorted(tbl.items())
    for i, (filepath, diff) in enumerate(_forms(entry))
]


class TestLanguageMatrix:
    """Language-generic criteria must dispatch in EVERY language their
    domain scopes — a detector that only recognizes PHP/JS syntax fails
    here for Go/Rust/Java/Kotlin/C# instead of failing in review."""

    @pytest.mark.parametrize(
        "agent_name,criterion,lang,filepath,diff",
        _MATRIX_FLAT,
        ids=[f"{a.split('-reviewer')[0]}:{lang}" for a, _, lang, _, _ in _MATRIX_FLAT],
    )
    def test_criterion_dispatches_in_language(
        self, agents, agent_name, criterion, lang, filepath, diff
    ):
        config = agents[agent_name]
        assert criterion in config.get("triage_criteria", []), (
            f"matrix anchor criterion drifted for {agent_name}: {criterion!r}"
        )
        p = probe(criterion, [filepath], diff=diff)
        status, reason = decide_agent_dispatch(
            agent_name,
            config,
            build_domain_counts(p["files"]),
            clean_files=p["files"],
            commit_messages="",
            diffstat=p["diffstat"],
            pr_text="",
            diff_text=p["diff"],
            repository_text="",
        )
        assert status == "DISPATCH", (
            f"{agent_name} criterion {criterion!r} has no backing signal for "
            f"{lang} — got {status} ({reason}). The domain scopes this "
            f"language; the detector must recognize its syntax."
        )


class TestDetectorSilenceConservatism:
    """Partial detector silence never gates a scoped programming language."""

    def _small(self, filepath):
        return {
            "added": 3, "removed": 2,
            "deleted_files": [], "renamed_files": [], "added_files": [],
            "file_stats": {filepath: {"added": 3, "removed": 2}},
        }

    def test_c_signature_change_dispatches_clarity(self, agents):
        status, reason = decide_agent_dispatch(
            "code-clarity-reviewer", agents["code-clarity-reviewer"],
            build_domain_counts(["src/parser.c"]),
            clean_files=["src/parser.c"],
            commit_messages="tidy parser",
            diffstat=self._small("src/parser.c"),
            pr_text="",
            diff_text="-int get_name(char *key) {\n+int get_name(char *key, int locale) {",
            repository_text="",
        )
        assert status == "DISPATCH", reason

    def test_cpp_include_change_dispatches_dead_code(self, agents):
        status, reason = decide_agent_dispatch(
            "dead-code-reviewer", agents["dead-code-reviewer"],
            build_domain_counts(["src/parser.cpp"]),
            clean_files=["src/parser.cpp"],
            commit_messages="tidy parser",
            diffstat=self._small("src/parser.cpp"),
            pr_text="",
            diff_text='-#include "legacy_parser.h"',
            repository_text="",
        )
        assert status == "DISPATCH", reason

    def test_representative_language_coverage_does_not_authorize_skip(self, agents):
        status, reason = decide_agent_dispatch(
            "concurrency-reviewer", agents["concurrency-reviewer"],
            build_domain_counts(["src/utils/format.py"]),
            clean_files=["src/utils/format.py"],
            commit_messages="tidy formatter",
            diffstat=self._small("src/utils/format.py"),
            pr_text="",
            diff_text="+ return value.strip()",
            repository_text="",
        )
        assert status == "DISPATCH", reason

    def test_mixed_language_files_dispatch_conservatively(self, agents):
        files = ["src/utils/format.py", "src/native/parser.c"]
        status, reason = decide_agent_dispatch(
            "concurrency-reviewer", agents["concurrency-reviewer"],
            build_domain_counts(files),
            clean_files=files,
            commit_messages="tidy formatting",
            diffstat={
                "added": 6, "removed": 2,
                "deleted_files": [], "renamed_files": [], "added_files": [],
                "file_stats": {
                    "src/utils/format.py": {"added": 3, "removed": 1},
                    "src/native/parser.c": {"added": 3, "removed": 1},
                },
            },
            pr_text="",
            diff_text="+ trim(value);",
            repository_text="",
        )
        assert status == "DISPATCH", reason

    def test_keyword_required_gate_holds_for_uncovered_languages(self):
        """An explicit membership gate still requires its configured keyword."""
        config = {
            "dispatch_class": "conditional",
            "domain": "security",
            "triage_keywords": ["auth", "token"],
            "require_triage_keyword_match": True,
        }
        status, reason = _mod.triage_conditional_agent(
            "synthetic-gated-reviewer", config,
            ["src/native/parser.c"],
            "tidy parser",
            {
                "added": 3, "removed": 1,
                "deleted_files": [], "renamed_files": [], "added_files": [],
                "file_stats": {"src/native/parser.c": {"added": 3, "removed": 1}},
            },
            diff_text="+ trim(value);",
        )
        assert status == "SKIPPED_TRIAGE", reason

    def test_every_matrix_family_probes_every_table(self):
        """Each family has representative positive probes in every table."""
        for tbl_name, tbl in (
            ("signatures", _SIGNATURE_CHANGES),
            ("types", _TYPE_DECLARATIONS),
            ("imports", _IMPORT_CHANGES),
            ("endpoints", _ENDPOINT_REGISTRATIONS),
        ):
            assert set(tbl) == set(_MATRIX_FAMILY_EXTENSIONS), (
                f"{tbl_name} table families out of sync with extensions map"
            )


class TestCriteriaCoverageComplete:
    """Every registry criterion has a probe; every probe maps to a real
    criterion. Adding or rewording a criterion without a probe fails here."""

    def test_probed_criteria_match_registry_exactly(self, agents):
        problems = []
        for agent_name, config in sorted(agents.items()):
            if config.get("dispatch_class") != "conditional":
                continue
            registry_criteria = set(config.get("triage_criteria", []))
            probed = {p["criterion"] for p in CRITERIA_PROBES.get(agent_name, [])}
            missing = registry_criteria - probed
            stale = probed - registry_criteria
            if missing:
                problems.append(f"{agent_name}: criteria without probes: {sorted(missing)}")
            if stale:
                problems.append(f"{agent_name}: probes for nonexistent criteria: {sorted(stale)}")
        assert not problems, (
            "Criteria/probe drift — every triage_criteria bullet needs a "
            "probe in CRITERIA_PROBES (and probes must quote criteria "
            "verbatim):\n" + "\n".join(problems)
        )

    def test_all_conditional_agents_have_probe_entries(self, agents):
        conditional = {
            name for name, cfg in agents.items()
            if cfg.get("dispatch_class") == "conditional"
        }
        assert conditional == set(CRITERIA_PROBES), (
            f"missing: {conditional - set(CRITERIA_PROBES)}, "
            f"stale: {set(CRITERIA_PROBES) - conditional}"
        )
