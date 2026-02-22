# Facade Pattern

## The Core Problem

You need to interact with a complex subsystem that requires implementing multiple classes/interfaces, coordinating dozens of objects, and wading through massive documentation -- just to perform a simple task. Business logic gets buried under infrastructure code.

## What is Facade?

Facade bridges a complexity gap by providing a **simplified, bespoke interface** to a complex subsystem. You design the API you WISH you had, then implement delegation behind it.

**Facade vs Adapter:**

| Aspect | Facade | Adapter |
|--------|--------|---------|
| Problem | Complexity gap (many classes) | Communication gap (one class) |
| Scope | Coordinates MANY objects | Translates ONE interface |
| Design | You invent the interface you need | You match an existing target interface |

**Key power:** Subsystem developers cannot provide a Facade that meets YOUR needs. You create your own bespoke interface tailored precisely to your client application.

**There is no fixed UML structure.** Facade is defined by intent (simplification), not form. Each implementation is unique to its context.

## When to Use Facade

- Subsystem requires too many classes/interfaces for simple tasks
- Business logic is buried under infrastructure coordination
- You need to orchestrate interactions among many objects
- You want to isolate client code from subsystem changes (update Facade implementation only)
- Interface Segregation is violated -- interfaces have dozens of methods but you need only a few

### When NOT to Use Facade

- Simple one-to-one adaptation needed (use Adapter)
- Subsystem is already simple enough to use directly
- You need full subsystem access (Facade intentionally limits what is exposed)

## WordPress/PHP

### WC()->cart as a Facade

WordPress core already uses Facades extensively:

| Function | Facades over |
|----------|-------------|
| `wp_mail()` | PHPMailer configuration, headers, error handling |
| `wp_remote_get()` | HTTP transport selection, SSL, redirects, timeouts |
| `WC()->cart` | Cart session, item storage, totals calculation, coupons, tax |
| `get_option()` | Options table queries, autoload, caching |

```php
// Without facade: coordinating multiple WooCommerce subsystems
$session = WC()->session;
$cart_data = $session->get('cart', []);
$totals = new WC_Cart_Totals($cart);
$tax = WC_Tax::get_rates();
// ... dozens more lines of coordination

// With WC()->cart facade: one clean call
$total = WC()->cart->get_total();
```

### Custom Notification Facade

```php
// Design the interface YOUR client needs (not what subsystems provide)
interface OrderNotifier {
    public function notifyOrderConfirmed(int $order_id): bool;
    public function notifyOrderShipped(int $order_id, string $tracking): bool;
}

class WooCommerceOrderNotifier implements OrderNotifier {
    public function notifyOrderConfirmed(int $order_id): bool {
        // Coordinates: WC_Order, WC_Customer, WC_Email,
        // template loader, variable replacement, wp_mail()
        $order = wc_get_order($order_id);
        $customer = $order->get_user();
        $mailer = WC()->mailer();

        ob_start();
        wc_get_template('emails/customer-completed-order.php', [
            'order' => $order,
            'email_heading' => __('Order Confirmed', 'woocommerce'),
        ]);
        $message = $mailer->wrap_message(
            __('Order Confirmed', 'woocommerce'),
            ob_get_clean()
        );

        return wp_mail($customer->user_email, __('Order Confirmed', 'woocommerce'), $message, [
            'Content-Type: text/html; charset=UTF-8',
        ]);
    }

    public function notifyOrderShipped(int $order_id, string $tracking): bool {
        // Similar coordination hidden behind one method
        // ...
        return true;
    }
}

// Client code is clean -- focused on business logic
class OrderProcessor {
    public function __construct(private OrderNotifier $notifier) {}

    public function complete(int $order_id): void {
        // ... business logic ...
        $this->notifier->notifyOrderConfirmed($order_id);
    }
}
```

## JS/TS

### Module Re-export Facade

```typescript
// Facade pattern in JS/TS often appears as a module that re-exports
// a simplified interface over complex internals.

// Internal complex modules (the "subsystem")
// analytics/tracker.ts, analytics/session.ts, analytics/consent.ts, etc.

// analytics/index.ts -- the Facade
import { Tracker } from './tracker';
import { SessionManager } from './session';
import { ConsentManager } from './consent';
import { EventQueue } from './queue';

const tracker = new Tracker();
const session = new SessionManager();
const consent = new ConsentManager();
const queue = new EventQueue();

// Simplified API -- clients never touch internals
export function trackEvent(name: string, props?: Record<string, unknown>): void {
  if (!consent.hasConsent()) return;
  session.ensureActive();
  queue.enqueue({ name, props, sessionId: session.getId(), ts: Date.now() });
  queue.flush();
}

export function identify(userId: string): void {
  if (!consent.hasConsent()) return;
  session.ensureActive();
  tracker.setUserId(userId);
}

export function optOut(): void {
  consent.revoke();
  queue.clear();
  session.destroy();
}

// Client code -- no knowledge of Tracker, SessionManager, ConsentManager, EventQueue
// import { trackEvent } from '@/analytics';
// trackEvent('checkout_completed', { orderId: 123 });
```

## Common Mistakes

- **WRONG:** Leaking subsystem types through the Facade interface (`SubsystemRequest` as a parameter)
  **RIGHT:** Facade uses its own types; translate to/from subsystem types inside the implementation

- **WRONG:** Making the Facade too generic (`execute(array $config): array` -- what does this do?)
  **RIGHT:** Design specific, intention-revealing methods tailored to client needs

- **WRONG:** Bypassing the Facade in some places while using it in others
  **RIGHT:** Once you create a Facade, use it consistently; mixed access defeats the isolation benefit

- **WRONG:** Creating a Facade when the subsystem is already simple
  **RIGHT:** Only add Facade when there is a genuine complexity gap to bridge

## Relationships

- Facade vs **Adapter** -- Facade simplifies many-to-one; Adapter translates one-to-one
- Facade vs **Proxy** -- Facade simplifies a subsystem; Proxy controls access to a single object
- Facade vs **Mediator** -- Facade provides a unidirectional simplified interface; Mediator coordinates bidirectional communication
- Facade often uses **Factory** internally to construct subsystem objects
- If a Facade grows too large, split into multiple cohesive Facades (Interface Segregation Principle)
