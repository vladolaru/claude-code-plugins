# Hexagonal Architecture (Ports and Adapters)

## Overview

Hexagonal Architecture decouples business logic from external dependencies through pluggable design. Business logic interacts with externals through abstract **ports** (interfaces), fulfilled by concrete **adapters**.

**Core rule:** Business logic has NO dependency on anything outside its boundary. All arrows point inward toward ports.

It is a **pattern of design patterns**: Strategy (ports), Adapter (translates to externals), Facade (coordinates multiple externals), DI (configurer wires), Composite (multiple adapters), Decorator (chains behavior).

## When to Use Hexagonal Architecture

**Use when:**
- Delaying dependency decisions (databases, frameworks) until more info available
- External dependencies likely to change or vendor lock-in is a concern
- Testability priority — biz logic tested in isolation with test doubles
- Domain-Driven Design — keeping domain pure
- Monolith-to-microservices migration or dual reads/writes
- Parallel development — teams work independently once ports stabilize
- Multiple platforms (REST, CLI, GraphQL) sharing same business logic

**Do NOT use when:**
- Simple CRUD with minimal business logic
- Prototype/POC where speed > architecture
- Stable unchanging dependencies
- Small scripts/utilities

## Structure

```
External Frameworks & Drivers (DB, Web, Messaging)
        ▲ delegates to
Adapters (Framework/Driving, Dependency/Driven, Configurers)
        ▲ implements
Ports — Interfaces/Contracts (Driver inbound, Driven outbound)
        ▲ depends on
Business Logic (Use Cases, Entities, Domain Rules)
```

**Ports:** Pure stable elements. Driver ports declare what biz logic CAN do (called by externals). Driven ports declare what biz logic NEEDS (called from biz logic). Designed for business needs, NOT dictated by external APIs.

**Adapters:** Framework adapter (driving/left) translates external→domain→biz logic→domain→external. Dependency adapter (driven/right) translates domain↔external. NO business logic in adapters. Use Facade when port needs multiple externals.

**Configurer:** Creates/wires all objects. Only element knowing concrete classes. Different per environment (prod, test, staging).

**Dependency rules:** No cycles (DAG). Concrete classes never depend on other concrete classes. Dependency flows toward stability (concrete→abstract). Ports stable, adapters flexible.

## WordPress Plugin Mapping

```
my-plugin/src/
├── Domain/                ← Inner hexagon
│   ├── Order.php          ← Entity
│   ├── PlaceOrderInterface.php   ← Driver Port
│   └── PersistOrderInterface.php ← Driven Port
├── Application/           ← Use Cases
│   └── PlaceOrder.php     ← Implements driver port
├── Infrastructure/        ← Outer hexagon (Adapters)
│   ├── REST/OrderController.php       ← Framework Adapter
│   ├── Persistence/WPDBOrderRepo.php  ← Dependency Adapter
│   └── Email/WPMailAdapter.php        ← Dependency Adapter
└── Bootstrap.php          ← Configurer
```

## PHP Implementation

```php
// --- PORTS ---
interface PlaceOrderInterface {
    public function execute( Order $order ): OrderPlacementDetails;
}
interface PersistOrderInterface {
    public function save( Order $order ): void;
    public function findById( OrderId $id ): ?Order;
}

// --- BUSINESS LOGIC (depends only on ports) ---
class PlaceOrder implements PlaceOrderInterface {
    public function __construct(
        private PersistOrderInterface $repo,
        private CheckInventoryInterface $inventory,
        private SendEmailInterface $email
    ) {}
    public function execute( Order $order ): OrderPlacementDetails {
        if ( ! $this->inventory->isAvailable( $order->getItems() ) )
            throw new OrderValidationException( 'Items not available' );
        $this->repo->save( $order );
        $this->inventory->reserve( $order->getItems() );
        $this->email->send( $order->getCustomer()->getEmail(), 'Confirmation', '...' );
        return new OrderPlacementDetails( $order->getId(), $order->estimateDeliveryDate() );
    }
}

// --- FRAMEWORK ADAPTER (driving) ---
class OrderController {
    public function __construct( private PlaceOrderInterface $placeOrder ) {}
    public function placeOrder( WP_REST_Request $request ): WP_REST_Response {
        try {
            $order = $this->buildOrderFromRequest( $request );   // HTTP → Domain
            $details = $this->placeOrder->execute( $order );      // Delegate
            return new WP_REST_Response( [                        // Domain → HTTP
                'orderId' => (string) $details->getOrderId(),
            ], 201 );
        } catch ( OrderValidationException $e ) {
            return new WP_REST_Response( [ 'error' => $e->getMessage() ], 400 );
        }
    }
}

// --- DEPENDENCY ADAPTER (driven) ---
class WPDBOrderRepository implements PersistOrderInterface {
    public function __construct( private \wpdb $wpdb ) {}
    public function save( Order $order ): void {
        try {
            $this->wpdb->insert( $this->wpdb->prefix . 'orders', [
                'id' => (string) $order->getId(),
                'customer_id' => (string) $order->getCustomer()->getId(),
                'total' => $order->getTotal()->amount(),
            ] );
        } catch ( \Exception $e ) {
            throw new PersistenceException( 'Unable to persist order', 0, $e );
        }
    }
    public function findById( OrderId $id ): ?Order {
        $row = $this->wpdb->get_row( $this->wpdb->prepare(
            "SELECT * FROM {$this->wpdb->prefix}orders WHERE id = %s", (string) $id
        ), ARRAY_A );
        return $row ? $this->buildOrderFromData( $row ) : null;
    }
}

// --- CONFIGURERS ---
class ProductionConfigurer {
    public function configure(): PlaceOrderInterface {
        global $wpdb;
        return new PlaceOrder(
            new WPDBOrderRepository( $wpdb ),
            new InventoryServiceAdapter( INVENTORY_API_URL ),
            new WPMailAdapter()
        );
    }
}
class TestConfigurer {
    public function configure(): PlaceOrderInterface {
        return new PlaceOrder( new InMemoryOrderRepo(), new FakeInventory(), new FakeEmail() );
    }
}
```

## TypeScript Port/Adapter Example

```typescript
// Ports
interface PlaceOrder {
  execute(order: Order): Promise<OrderPlacementDetails>;
}
interface OrderRepository {
  save(order: Order): Promise<void>;
  findById(id: string): Promise<Order | null>;
}
interface PaymentGateway {
  charge(amount: Money, method: PaymentMethod): Promise<PaymentResult>;
}

// Business logic
class PlaceOrderUseCase implements PlaceOrder {
  constructor(
    private readonly orderRepo: OrderRepository,
    private readonly payment: PaymentGateway,
  ) {}
  async execute(order: Order): Promise<OrderPlacementDetails> {
    const result = await this.payment.charge(order.total, order.paymentMethod);
    if (!result.success) throw new PaymentFailedError(result.error);
    await this.orderRepo.save(order);
    return { orderId: order.id, estimatedDelivery: order.estimateDelivery() };
  }
}

// Driven adapter
class PostgresOrderRepository implements OrderRepository {
  constructor(private readonly pool: Pool) {}
  async save(order: Order): Promise<void> {
    await this.pool.query(
      'INSERT INTO orders (id, customer_id, total) VALUES ($1, $2, $3)',
      [order.id, order.customerId, order.total.amount],
    );
  }
  async findById(id: string): Promise<Order | null> {
    const { rows } = await this.pool.query('SELECT * FROM orders WHERE id = $1', [id]);
    return rows[0] ? this.toDomain(rows[0]) : null;
  }
}

class StripePaymentAdapter implements PaymentGateway {
  constructor(private readonly stripe: Stripe) {}
  async charge(amount: Money, method: PaymentMethod): Promise<PaymentResult> {
    try {
      const intent = await this.stripe.paymentIntents.create({
        amount: amount.toCents(), currency: amount.currency,
        payment_method: method.token, confirm: true,
      });
      return { success: true, transactionId: intent.id };
    } catch (err) {
      return { success: false, error: (err as Error).message };
    }
  }
}

// Configurers
const configureProduction = (): PlaceOrder => new PlaceOrderUseCase(
  new PostgresOrderRepository(new Pool({ connectionString: process.env.DATABASE_URL })),
  new StripePaymentAdapter(new Stripe(process.env.STRIPE_SECRET_KEY!)),
);
const configureTest = (): PlaceOrder => new PlaceOrderUseCase(
  new InMemoryOrderRepository(), new FakePaymentGateway(),
);
```

## Common Mistakes

| Mistake | Wrong | Right |
|---------|-------|-------|
| Biz logic in adapter | Adapter sets status based on order total | Move rule to biz logic layer |
| Leaky abstractions | Port exposes `executeQuery(sql)` | Port uses domain types only |
| Port mirrors external API | `insertIntoOrdersTable(data)` | `save(Order): void` |
| Adapter-to-adapter coupling | Controller depends on DB adapter | Controller depends on biz logic port |
| Primitive obsession | `execute(int, array, string): array` | `execute(Order): OrderPlacementDetails` |
| Framework in biz logic | Use case imports `Symfony\Request` | Adapter translates framework↔domain |
| No configurer | `new DbRepo(new Connection())` scattered | Centralize in Configurer/DI container |

## Expanding the Architecture

**Breadth:** Multiple framework adapters (REST, GraphQL, CLI) → same driver port → same biz logic → multiple driven ports → multiple dependency adapters.

**Depth:** `PlaceOrder → IHandlePlacedOrders → Adapter → IManageInventory → ManageInventory → IPersist → DB`

**Composite (add behavior without touching biz logic):**
```php
$composite = new HandleStuffViaComposite();
$composite->add( new PersistStuffViaDB() );
$composite->add( new AuditLogViaService() );
$manageStuff = new ManageStuff( $composite ); // Biz logic unchanged
```

**Dispatcher for migration:** Phase 1: DB only → Phase 2: dual writes (DB+cloud, reads prefer cloud) → Phase 3: cloud only. Business logic unchanged throughout.
