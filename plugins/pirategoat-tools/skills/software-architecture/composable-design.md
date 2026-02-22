# Composable Design Patterns Reference

## Core Concept

Composable design uses **object composition** where behavior emerges from interaction of cohesive objects, not a single monolithic class. Same classes, different object compositions → different behaviors — not via `if`/`switch`/feature flags, but via composed object graphs.

**Self-Referential Delegation:** Concrete classes implement an interface AND delegate to that same interface, enabling arbitrarily deep/wide compositions.

**Computation vs Coordination** (keep separate):
- **Computation** — Code reuse via delegation (in class implementation)
- **Coordination** — Object assembly (in the Configurer)

## The Configurer

Critical pattern never featured by the GoF:
- Instantiates objects and assembles composition
- The ONLY element knowing concrete classes and application context
- Without it, pattern classes only have potential

```php
class ContentFilterConfigurer {
    public static function createStandardFilter(): ContentFilter {
        $f = new BaseContent();
        $f = new SanitizeDecorator( $f );
        $f = new ShortcodeDecorator( $f );
        return new AutopDecorator( $f );
    }
    public static function createAdminFilter(): ContentFilter {
        return new SanitizeDecorator( new BaseContent() );
    }
}
// Client only knows interface — no knowledge of composition depth
$filter = ContentFilterConfigurer::createStandardFilter();
```

## Composable Patterns (Least → Most Complex)

| Pattern | Purpose | Type |
|---------|---------|------|
| **Proxy** | Wrapper (defer creation, control access) | Structural |
| **Decorator** | Layer behaviors dynamically | Structural |
| **Chain of Responsibility** | Linked handler delegation | Behavioral |
| **Composite** | Tree of snippet behaviors | Structural |
| **Specification** | Combinable rule filters (AND/OR/NOT) | Behavioral |
| **Interpreter** | Grammar evaluation | Behavioral |

## WordPress Decorator: Content Filter Pipeline

```php
interface ContentFilter {
    public function filter( string $content ): string;
}
class BaseContent implements ContentFilter {
    public function filter( string $content ): string { return $content; }
}
class SanitizeDecorator implements ContentFilter {
    public function __construct( private ContentFilter $inner ) {}
    public function filter( string $content ): string {
        return wp_kses_post( $this->inner->filter( $content ) );
    }
}
class ShortcodeDecorator implements ContentFilter {
    public function __construct( private ContentFilter $inner ) {}
    public function filter( string $content ): string {
        return do_shortcode( $this->inner->filter( $content ) );
    }
}
add_filter( 'the_content', function( $content ) {
    return ContentFilterConfigurer::createStandardFilter()->filter( $content );
} );
```

## WordPress Chain of Responsibility: REST Validation

```php
interface RequestHandler {
    public function setNext( RequestHandler $h ): RequestHandler;
    public function handle( WP_REST_Request $req ): ?WP_Error;
}
abstract class AbstractRequestHandler implements RequestHandler {
    private ?RequestHandler $next = null;
    public function setNext( RequestHandler $h ): RequestHandler { $this->next = $h; return $h; }
    public function handle( WP_REST_Request $req ): ?WP_Error { return $this->next?->handle( $req ); }
}
class NonceValidationHandler extends AbstractRequestHandler {
    public function handle( WP_REST_Request $req ): ?WP_Error {
        if ( ! wp_verify_nonce( $req->get_header( 'X-WP-Nonce' ), 'wp_rest' ) )
            return new WP_Error( 'invalid_nonce', 'Invalid nonce', [ 'status' => 403 ] );
        return parent::handle( $req );
    }
}
class PermissionCheckHandler extends AbstractRequestHandler {
    public function __construct( private string $cap ) {}
    public function handle( WP_REST_Request $req ): ?WP_Error {
        if ( ! current_user_can( $this->cap ) )
            return new WP_Error( 'forbidden', 'No permission', [ 'status' => 403 ] );
        return parent::handle( $req );
    }
}
class RateLimitHandler extends AbstractRequestHandler {
    public function __construct( private int $max, private int $window ) {}
    public function handle( WP_REST_Request $req ): ?WP_Error {
        $key = 'rate_limit_' . get_current_user_id();
        $count = get_transient( $key ) ?: 0;
        if ( $count >= $this->max )
            return new WP_Error( 'rate_limit', 'Too many requests', [ 'status' => 429 ] );
        set_transient( $key, $count + 1, $this->window );
        return parent::handle( $req );
    }
}
// Configurer assembles chain
$nonce = new NonceValidationHandler();
$nonce->setNext( new PermissionCheckHandler( 'edit_posts' ) )
      ->setNext( new RateLimitHandler( 100, HOUR_IN_SECONDS ) );

register_rest_route( 'myplugin/v1', '/data', [
    'methods'  => 'POST',
    'callback' => 'myplugin_handle_request',
    'permission_callback' => fn( $req ) => $nonce->handle( $req ) === null,
] );
```

## WooCommerce Specification: Product Filtering

```php
abstract class AbstractSpecification implements Specification {
    abstract public function isSatisfiedBy( $candidate ): bool;
    public function and( Specification $o ): Specification { return new AndSpecification( $this, $o ); }
    public function or( Specification $o ): Specification  { return new OrSpecification( $this, $o ); }
    public function not(): Specification                    { return new NotSpecification( $this ); }
}
class InStockSpecification extends AbstractSpecification {
    public function isSatisfiedBy( $p ): bool { return $p->is_in_stock(); }
}
class PriceRangeSpecification extends AbstractSpecification {
    public function __construct( private float $min, private float $max ) {}
    public function isSatisfiedBy( $p ): bool {
        $price = (float) $p->get_price();
        return $price >= $this->min && $price <= $this->max;
    }
}
class CategorySpecification extends AbstractSpecification {
    public function __construct( private array $cats ) {}
    public function isSatisfiedBy( $p ): bool {
        $pc = wp_get_post_terms( $p->get_id(), 'product_cat', [ 'fields' => 'slugs' ] );
        return ! empty( array_intersect( $this->cats, $pc ) );
    }
}
// Different compositions yield different filters
$spec = ( new InStockSpecification() )->and( new PriceRangeSpecification( 10.0, 50.0 ) );
$spec = ( new OnSaleSpecification() )->or( new CategorySpecification( [ 'electronics' ] ) )
    ->and( new InStockSpecification() );
```

## How Patterns Compose Together

Patterns combine in a single system: Configurer creates Decorator wrapping Decorator wrapping Chain of Responsibility with Specification filters. Each layer is independent, testable, swappable. Client sees only the root interface.

## When to Use / Avoid

**Use:** Rule/policy behaviors varying by customer, runtime changes without redeployment, multiple configs of same building blocks, self-service customization.
**Avoid:** Single static behavior, performance-critical hot path, will never change, stable simple hierarchy.

## Trade-offs

| Benefit | Cost |
|---------|------|
| Runtime flexibility | Distributed behavior harder to trace |
| High code reuse | Many small classes |
| Testable (stateless, pure-function-like) | Not all compositions testable in advance |
| Customer-configurable | Untested compositions may emerge |
| Concurrent-safe (stateless) | Runtime overhead vs inheritance |
