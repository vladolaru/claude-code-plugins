# Hexagonal Architecture (Ports and Adapters)

## Overview

Hexagonal Architecture, also known as Ports and Adapters, is an architectural pattern that decouples business logic from external dependencies through pluggable design. Despite its name, it's not really about hexagons - the pattern focuses on design boundaries and constraints.

**Core Principle**: Business Domain implementation is sheltered in its own cocoon, protected from external dependencies. The business logic interacts with external systems through abstract ports (interfaces), which are fulfilled by concrete adapters.

**Creator**: Alistair Cockburn conceived this pattern in the 1990s to decouple software components.

**Why Hexagons?**: Cockburn chose hexagons to avoid the preconceived notions associated with rectangles (classes, layers, fixed access points). He wanted each side to represent a facet or access point. Any polygon would work - he chose hexagons because they were easiest to draw. The number of sides has no architectural significance.

### Names and Terminology

This design goes by multiple names:
- **Hexagonal Architecture** (original name)
- **Ports and Adapters** (Cockburn's preferred name after learning about design patterns)
- Similar patterns: Clean Architecture, Onion Architecture

All share the same core concept: pluggable design with business logic cocooned from external dependencies.

## When to Use Hexagonal Architecture

### Primary Use Cases

**1. Delaying Dependency Decisions**
- Allows you to defer choosing specific technologies (databases, frameworks, etc.)
- Maximizes the number of decisions NOT made early
- Provides more time and information to make correct decisions

**2. Enabling External Dependency Changes**
- Makes it feasible to change databases, frameworks, or external services
- Questions to ask: "Are you never going to change dependencies because you choose not to, or because you've become too tightly coupled to them?"
- Protects against vendor lock-in or vendors going out of business

**3. Improving Testability**
- Accommodates test doubles more easily
- Business logic can be unit tested in isolation
- No need for expensive integration tests for basic functionality

**4. Domain-Driven Design Support**
- Keeps domain logic pure and focused
- External concerns don't leak into business rules
- Supports ubiquitous language without technical pollution

**5. Distributed System Evolution**
- Facilitates moving from monolith to microservices
- Supports dual reads/writes during migrations
- Enables gradual rollout of architectural changes

### When NOT to Use

- **Simple CRUD applications** with minimal business logic
- **Prototype/proof-of-concept** projects where you're validating concepts quickly
- **Projects with stable, unchanging dependencies** where coupling is acceptable
- **Small scripts or utilities** where architectural overhead isn't justified

### Indicators You Need This Pattern

- Multiple external dependencies that might change
- Complex business rules that need to be tested in isolation
- Migration from one technology to another is planned or likely
- Team wants to work in parallel on different components
- Framework/vendor lock-in is a concern

## Structure

### The Three Layers

```
┌─────────────────────────────────────────────────────┐
│         External Frameworks & Drivers               │
│    (Database, Web, Messaging, File System, etc.)    │
└─────────────────────────────────────────────────────┘
                         ▲
                         │ delegates to
                         │
┌─────────────────────────────────────────────────────┐
│                    Adapters                         │
│  - Framework Adapters (left side - driving)         │
│  - Dependency Adapters (right side - driven)        │
│  - Configurers (object creation & assembly)         │
└─────────────────────────────────────────────────────┘
                         ▲
                         │ implements
                         │
┌─────────────────────────────────────────────────────┐
│                     Ports                           │
│         (Interfaces/Contracts)                      │
│  - Driver Business Logic Ports (inbound)            │
│  - Driven Dependency Ports (outbound)               │
└─────────────────────────────────────────────────────┘
                         ▲
                         │ depends on
                         │
┌─────────────────────────────────────────────────────┐
│               Business Logic                        │
│     (Use Cases, Entities, Domain Rules)             │
└─────────────────────────────────────────────────────┘
```

### The Hexagons

**Red Hexagon (Inner)**:
- Boundary around business logic and ports
- All arrows point INWARD
- Business logic has NO dependency or knowledge of anything outside this boundary
- Pure Stable/Fixed boundary

**Purple Hexagon (Outer)**:
- Encapsulates the entire design
- All arrows point OUTWARD
- Pure Unstable/Flexible boundary
- External dependencies mostly don't know the inner design exists

**Zone Between Hexagons**:
- Contains Adapters, Façades, and Configurers
- Unstable/Flexible - can be changed easily
- Like a DMZ that buffers business logic from external chaos
- Allows business logic to operate in any environment

### The Port (Interface/Contract)

**What It Is**:
- An interface that declares a contract
- Design element (not just an implementation detail)
- Cohesive set of methods following SRP and ISP

**Types of Ports**:

1. **Driver Business Logic Port** (Left Side - Inbound)
   - Interface declaring what the business logic can do
   - Called BY external frameworks (REST, CLI, GUI, etc.)
   - Frameworks DRIVE the business logic through these ports
   - Example: `IPlaceOrder`, `ICancelOrder`, `IManageStuff`

2. **Driven Dependency Port** (Right Side - Outbound)
   - Interface declaring what external behaviors business logic needs
   - Called FROM business logic TO external dependencies
   - Business logic DRIVES external dependencies through these ports
   - Example: `IPersistOrders`, `ISendEmail`, `IPublishEvents`

3. **Hybrid** (Both Sides)
   - Some systems both drive and are driven (messaging platforms)
   - Business logic produces events (driven side)
   - Business logic consumes events (driver side)

**Port Characteristics**:
- Pure Stable/Fixed elements (all arrows point inward)
- No dependencies on other design elements
- Should be designed for business logic needs, not dictated by external APIs
- Opportunity to "shake the Etch-A-Sketch" and design the contracts you need

**Naming Convention**:
- First-person declarative with `I` prefix: `IPlaceOrders`, `IPersistData`, `ISendNotifications`
- Alternative: Start with `For`: `ForPlacingOrders`, `ForUpdatingOrders`

### The Adapter

**What It Is**:
- Concrete class implementing a Port/Interface/Contract
- Translates between business logic contracts and external dependency APIs
- Contains all the "blood, sweat, and tears" of working with external APIs
- Segregated from business logic to reduce complexity

**Types of Adapters**:

1. **Framework Adapter** (Left Side - Driving)
   - Has all framework details (REST, GraphQL, CLI, etc.)
   - NO business logic
   - Delegates to Business Logic Contract
   - Example: Controller extending REST framework

2. **Dependency Adapter** (Right Side - Driven)
   - Implements Dependency Contract
   - Delegates to External Dependency
   - Translates domain types to/from external representations
   - Example: Database adapter, email service adapter

**Adapter Characteristics**:
- Unstable/Flexible elements
- Can be swapped out easily
- Only inward relationship is creation
- Responsibility limited to translation between contract and external API

**When to Use Façade Instead**:
- When contract requires behavior from MULTIPLE external dependencies
- Single external dependency insufficient to satisfy contract
- More complex than simple adapter
- Still should not contain business logic

### Business Logic

**What It Lives Inside**:
- The Red Hexagon
- Completely isolated from external dependencies
- Only depends on Ports/Interfaces/Contracts

**Characteristics**:
- Unstable/Flexible (surprisingly!)
- Only inward relationship is creation
- Can be updated or replaced without affecting rest of design
- Should be straightforward enough for business analysts to understand

**Contains**:
- Domain entities
- Use case implementations
- Business rules and validation
- Orchestration of domain operations

**Does NOT Contain**:
- Database queries
- REST/HTTP handling
- Messaging platform specifics
- Framework-specific code
- External API calls

### Configurer (Dependency Injection)

**What It Is**:
- Responsible for creating and configuring objects
- Assembles the dependency graph
- The only element that knows about all concrete classes

**Characteristics**:
- Pure Unstable/Flexible
- Nothing depends upon it
- Completely invisible to rest of design
- Often resides in `main()` or application startup
- Can use Factories for complex assembly

**Typical Structure**:
```java
FrameworkAdapter frameworkAdapter =
    new FrameworkAdapter(
        new BusinessLogic(
            new DependencyAdapter()
        )
    );
```

**Multiple Configurers**:
- Different configurers for different environments (Production, Test, Staging)
- Different configurers for different frameworks
- Easy to swap out without affecting other design elements

### Design Patterns Involved

Hexagonal Architecture is a **pattern of design patterns**:

1. **Strategy** - Ports/Interfaces/Contracts define behavioral contracts
2. **Adapter** - Translates between contracts and external APIs
3. **Façade** - Coordinates multiple external dependencies for single contract
4. **Dependency Injection** - Configurer creates and injects dependencies
5. **Factory** - May be used within Configurer for complex object creation
6. **Template Method** - Ports may use this in appropriate circumstances
7. **Composite** - Can manage multiple adapters for same interface

## Dependency and Knowledge Management

### Why Hexagonal Architecture Works

The pattern succeeds because of how it manages dependency and knowledge relationships. This is based on Bob Martin's component coupling principles applied at the class level.

### Core Definitions

**Dependency and Knowledge**:
- When A depends upon B, A has knowledge of B
- B has NO dependency upon or knowledge of A
- B doesn't even know it's in a relationship with A
- A takes all the risk - if B changes, A might need to change
- Relationships are one-way streets

**In Code Terms** (Java example):
A depends upon and has knowledge of B when:
- A implements B
- A extends B
- A creates an instance of B
- A references B as: field, local variable, parameter, collection

**Visual Representation**:
- UML class diagrams show dependencies via arrows
- Arrow direction = direction of dependency and knowledge
- Design is a Directional Graph (nodes = classes, edges = dependencies)

### The Three Principles

#### 1. Acyclic Dependencies Principle

**Rule**: Remove cycles from the dependency graph.

**Why**: Design should be a Directed Acyclic Graph (DAG), not just a Directed Graph.

**How to Fix Cycles**: Use Dependency Inversion Principle (DIP) to break them.

**In Hexagonal Architecture**: NO cycles exist. Start at any element and follow any path - you can never return to your starting position.

#### 2. Stable Dependencies Principle

**Core Concept**: Count inward arrows vs outward arrows to determine stability.

**Stable/Fixed Elements** (More inward arrows than outward):
- Immune to updates in the design
- Like utility classes (String, LinkedList)
- Well-defined, narrowly scoped behaviors
- Don't change often (or ever)
- Examples in design: Ports/Interfaces/Contracts, External Dependencies, Red Hexagon boundary

**Unstable/Flexible Elements** (More outward arrows than inward):
- Mostly invisible to other elements
- Can change without major impact on rest of design
- Examples in design: Configurers, Business Logic, Adapters, Purple Hexagon boundary

**Adults vs Teenagers**:
- Stable/Fixed = Adults (everyone depends on them, they depend on few)
- Unstable/Flexible = Teenagers (they depend on others, few depend on them)
- Adults need to be responsible and stable
- Teenagers can change at a moment's notice

**The Principle**: Dependency/knowledge should flow toward stability. Design elements should depend upon elements at least as stable as themselves.

**Why**: Don't build towers that are top-heavy and prone to collapse.

#### 3. Stable Abstractions Principle

**Rule**: Unstable/Flexible elements tend to be concrete, Stable/Fixed elements tend to be abstractions.

**Implication**: Following dependency arrows in good design, you'll see flow from concrete classes → through increasing stability → ending at abstractions.

**In Hexagonal Architecture**: Dependencies flow from concrete Adapters and Business Logic → through increasing stability → to abstract Ports/Interfaces.

### Event Horizons

Inspired by black holes - information cannot cross certain boundaries.

#### Pure Stable/Fixed Event Horizons

**Elements**: Ports/Interfaces/Contracts, Red Hexagon boundary

**Behavior**: Flipped event horizon
- ANY element can depend upon and know about these
- These elements have NO knowledge beyond their boundaries
- Don't even know they're part of a design
- Like observable stars - everyone can see them, but they see nothing

#### Pure Unstable/Flexible Event Horizons

**Elements**: Configurers, Purple Hexagon boundary

**Behavior**: True event horizon
- NO element can depend upon or know about these
- These elements have dependency and knowledge beyond their boundaries
- Other elements don't know if they're one class or a thousand
- Like black holes - they see everything, but nothing can see them

**Pseudo-Pure Elements**: Business Logic and Adapters are technically not pure (they have creation arrows pointing in), but they're "pure enough" since only the Configurer knows them.

### Class-to-Class Dependencies: There Are None

**Key Insight**: Concrete classes have NO dependencies or knowledge of other concrete classes.

**Why**:
- Business Logic and Adapters separated via Ports/Interfaces/Contracts
- All arrows point INTO the Ports/Interfaces
- Cannot traverse the Event Horizon to hop to other concrete classes
- Only Configurers know concrete classes (for creation purposes only)

**Benefits**:
- Once Ports stabilize, concrete classes can be implemented in parallel
- Different developers/teams can work simultaneously
- No stepping on each other's toes
- Updates to one implementation won't affect others

### Topological Sorting View

When you arrange Hexagonal Architecture by dependency flow:

```
Configurers → Business Logic → Ports/Interfaces → Adapters → External Dependencies
(Unstable/Flexible concrete) → (Stable/Fixed abstract) → (Unstable/Flexible concrete) → (Stable/Fixed external)
```

This perfectly adheres to Martin's principles:
- **Acyclic Dependencies** - No cycles
- **Stable Dependencies** - Elements depend upon more stable elements
- **Stable Abstractions** - Flow from concrete → abstractions

### Summary of Why It Works

1. **Business Logic cocooned** - Can execute in any environment with support of Adapters/Façades/Configurers
2. **Event Horizons hide details** - Implementation can be any number of classes without affecting rest of design
3. **Ports separate implementations** - Update in one class won't affect other classes
4. **Parallel development** - Once Ports stabilize, different teams can work independently

## Clean Architecture Relationship

### Core Similarities

Hexagonal Architecture and Clean Architecture share the same philosophy:
- Domain information cocooned within design
- External dependencies pushed to edges
- Domain doesn't depend on external dependencies
- Pluggable design through interfaces

### Key Differences

| Aspect | Hexagonal Architecture | Clean Architecture |
|--------|------------------------|-------------------|
| **Focus** | Structure of design | Semantics and behavior |
| **Level** | Syntax | Semantics |
| **Detail** | Basic architectural structure | More context and implementation detail |
| **Layers** | 2 layers (inside/outside) | 4 layers (Entities, Use Cases, Interface Adapters, Frameworks) |
| **Visualization** | Hexagons | Concentric circles |

**Relationship**: Clean Architecture conceptually extends Hexagonal Architecture. Hexagonal is about structure; Clean is about what goes in that structure.

### Clean Architecture Layers Mapped to Hexagonal

#### 1. Enterprise Business Rules (Innermost Layer)

**What**: Entities that encapsulate enterprise-wide business rules.

**Characteristics**:
- Can be used by many different applications in enterprise
- Domain-specific, transcend any specific feature
- Would exist even if the system didn't exist
- Pure Stable/Fixed (all arrows point inward)
- Examples: Customer, Order, Loan, PhoneNumber

**Hexagonal Mapping**: These aren't explicitly shown in basic Hexagonal diagrams but reside within the Red Hexagon.

#### 2. Application Business Rules

**What**: Use Cases/Interactors that orchestrate enterprise entities.

**Characteristics**:
- Fine-grained, scoped to single Use Case/User Story
- Examples: PlaceOrder, CancelOrder, WithdrawFunds, BookReservation
- Reference and create Entities

**Hexagonal Mapping**: The "Business Logic" in Hexagonal Architecture.

**Input/Output Boundary** (Clean Architecture detail):
```java
interface UseCase {
    ResponseModel execute(RequestModel requestModel);
}

// Concrete example
interface PlaceOrder {
    OrderPlacementDetails execute(Order order);
}
```

Where:
- **Input Boundary** - Interface the Use Case implements
- **Request Model** - Data object as arguments
- **Output Boundary** - Interface for response
- **Response Model** - Data object being returned

**Data Access Interface**: Same as Driven Dependency Port in Hexagonal.

#### 3. Interface Adapters

**What**: Layer that converts data between Use Cases and External systems.

**Elements**:

**Controller**:
- Aligns with Framework Adapter (left side - driving)
- First encounter point for inbound requests
- Adapts external types into Entity/Domain types
- Should be associated with something in Framework layer

**Presenter and ViewModel**:
- Takes Output/Response Model (domain objects)
- Builds ViewModel formatted for user presentation
- Example: Output Model (domain) → HTML ViewModel (web browser)
- Different Presenters for different consumption methods

**Data Access**:
- Aligns with Dependency Adapter (right side - driven)
- Same concept, different name

**Hexagonal Mapping**: The Adapter zone between Red and Purple hexagons.

#### 4. Frameworks and Drivers (Outermost Layer)

**What**: External tools and frameworks (database, web server, UI).

**Elements**:
- Framework that Controller extends
- View that renders ViewModel
- Database, message queues, file systems
- External APIs and services

**Hexagonal Mapping**: External Dependencies beyond Purple Hexagon.

**Dependency Direction Note**:
- Martin's diagram shows arrows pointing inward (incorrect)
- Should point outward (Adapters depend on External, not vice versa)
- Hexagonal Architecture correctly shows arrows pointing outward

### Data Type Consistency Across Layers

**Challenge**: External systems don't understand domain Entities.

**Examples**:
- Can't save a Customer Entity directly in database
- REST API won't have Entity objects, just field representations
- Different external systems may represent same data differently

**Adapter Responsibility**: Translate between Entity/Domain types and external representations.

**For Each New External System**: New Adapter manages that system's specific translation.

### Avoiding Leaky Abstractions

**Problem**: External dependency details leak through Ports into Business Logic.

**Examples of Leaks**:
- Database-specific error codes exposed in interface
- HTTP status codes leaked from REST adapter
- Vendor-specific types in contract definitions

**How to Prevent**:
- Port/Interface/Contract should be abstract and generic
- Adapter translates external specifics into abstract concepts
- Example: `DatabaseVendorError` → `UnableToPersistException`
- Log specific errors for diagnosis, but don't propagate them upward
- Focus on Port definitions BEFORE considering external dependencies

**Good Practice**:
- Error indicates: "couldn't persist" not "MySQL error 1062"
- Status conveys: "service unavailable" not "HTTP 503"
- Types are: domain objects, not vendor-specific constructs

### What Clean Architecture Adds

1. **Screaming Architecture** - First impression should be what app does, not how it's built
2. **Use Case focus** - Fine-grained business logic scoping
3. **Entity concept** - Enterprise-wide business rules
4. **Presenter/ViewModel** - Explicit presentation layer separation
5. **Decision deferral** - Architecture allows major decisions to be deferred
6. **Maximize decisions NOT made** - Good architecture minimizes early commitments

### Quotes from Bob Martin

> "A good architecture allows major decisions to be deferred!"

> "A good architecture maximizes the number of decisions NOT made."

On interface naming:
> "I don't want my users knowing that I'm handing them an interface. I just want them to know that it's a ShapeFactory."

## Implementation Guide

### Starting with Test-Driven Development

**Order of Implementation**:

1. **Start with Business Logic Contract** (one method suffices)
2. **Implement Business Logic using TDD**
3. **When business logic needs external behavior**, declare it in Driven Dependency Contract

**Test Setup**:
```
Test Case →
    Creates Driven Dependency Test Double →
    Injects into Business Logic object →
    Validates behavior via Driver Business Logic
```

**Benefits**:
- Business logic testing is trivial to set up
- No need for external dependencies during development
- Tests run fast
- Clear contract-first design

### Implementing Ports (PHP Examples)

#### Driver Business Logic Port (Inbound)

```php
<?php

namespace App\Domain\Order;

/**
 * Port for placing orders.
 *
 * This interface defines what the business logic can do.
 * External frameworks (REST, CLI, etc.) call through this port.
 */
interface PlaceOrderInterface
{
    /**
     * Place a new order.
     *
     * @param Order $order The order to place
     * @return OrderPlacementDetails Details about the placed order
     * @throws OrderValidationException If order is invalid
     * @throws OrderPlacementException If order cannot be placed
     */
    public function execute(Order $order): OrderPlacementDetails;
}
```

#### Driven Dependency Port (Outbound)

```php
<?php

namespace App\Domain\Order;

/**
 * Port for persisting orders.
 *
 * This interface defines what external behavior the business logic needs.
 * Business logic calls OUT through this port to external systems.
 */
interface PersistOrderInterface
{
    /**
     * Save an order.
     *
     * @param Order $order The order to persist
     * @throws PersistenceException If order cannot be saved
     */
    public function save(Order $order): void;

    /**
     * Find an order by ID.
     *
     * @param OrderId $id The order identifier
     * @return Order|null The order if found, null otherwise
     */
    public function findById(OrderId $id): ?Order;

    /**
     * Delete an order.
     *
     * @param OrderId $id The order identifier
     * @throws PersistenceException If order cannot be deleted
     */
    public function delete(OrderId $id): void;
}
```

### Implementing Business Logic (PHP)

```php
<?php

namespace App\Application\Order;

use App\Domain\Order\PlaceOrderInterface;
use App\Domain\Order\PersistOrderInterface;
use App\Domain\Order\Order;
use App\Domain\Order\OrderPlacementDetails;
use App\Domain\Inventory\CheckInventoryInterface;
use App\Domain\Email\SendEmailInterface;

/**
 * Use Case: Place an order.
 *
 * This is the business logic. It depends only on interfaces/contracts.
 * It has NO knowledge of databases, frameworks, or external services.
 */
class PlaceOrder implements PlaceOrderInterface
{
    private PersistOrderInterface $orderRepository;
    private CheckInventoryInterface $inventory;
    private SendEmailInterface $emailService;

    public function __construct(
        PersistOrderInterface $orderRepository,
        CheckInventoryInterface $inventory,
        SendEmailInterface $emailService
    ) {
        $this->orderRepository = $orderRepository;
        $this->inventory = $inventory;
        $this->emailService = $emailService;
    }

    public function execute(Order $order): OrderPlacementDetails
    {
        // Business logic - clear, focused, testable
        $this->validateOrder($order);

        if (!$this->inventory->isAvailable($order->getItems())) {
            throw new OrderValidationException('Items not available');
        }

        $this->orderRepository->save($order);
        $this->inventory->reserve($order->getItems());

        $this->emailService->send(
            $order->getCustomer()->getEmail(),
            'Order Confirmation',
            $this->buildConfirmationMessage($order)
        );

        return new OrderPlacementDetails(
            $order->getId(),
            $order->estimateDeliveryDate(),
            $order->getTrackingNumber()
        );
    }

    private function validateOrder(Order $order): void
    {
        if ($order->getItems()->isEmpty()) {
            throw new OrderValidationException('Order must have items');
        }

        if (!$order->getCustomer()->isVerified()) {
            throw new OrderValidationException('Customer must be verified');
        }
    }

    private function buildConfirmationMessage(Order $order): string
    {
        // Build email message...
        return "Your order #{$order->getId()} has been placed...";
    }
}
```

### Implementing Adapters (PHP)

#### Framework Adapter (Driving Side)

```php
<?php

namespace App\Infrastructure\Http\Controller;

use App\Domain\Order\PlaceOrderInterface;
use App\Domain\Order\Order;
use Symfony\Component\HttpFoundation\Request;
use Symfony\Component\HttpFoundation\JsonResponse;

/**
 * REST Controller - Framework Adapter (Driving side).
 *
 * This adapter:
 * - Handles HTTP/REST concerns
 * - Translates HTTP request → Domain objects
 * - Delegates to business logic
 * - Translates Domain response → HTTP response
 * - Contains NO business logic
 */
class OrderController
{
    private PlaceOrderInterface $placeOrder;

    public function __construct(PlaceOrderInterface $placeOrder)
    {
        $this->placeOrder = $placeOrder;
    }

    public function placeOrder(Request $request): JsonResponse
    {
        try {
            // Translate HTTP → Domain
            $order = $this->buildOrderFromRequest($request);

            // Delegate to business logic
            $details = $this->placeOrder->execute($order);

            // Translate Domain → HTTP
            return new JsonResponse([
                'orderId' => (string) $details->getOrderId(),
                'estimatedDelivery' => $details->getEstimatedDeliveryDate()->format('Y-m-d'),
                'trackingNumber' => $details->getTrackingNumber(),
            ], 201);

        } catch (OrderValidationException $e) {
            return new JsonResponse(['error' => $e->getMessage()], 400);
        } catch (OrderPlacementException $e) {
            return new JsonResponse(['error' => 'Unable to place order'], 500);
        }
    }

    private function buildOrderFromRequest(Request $request): Order
    {
        // Parse JSON, validate, create domain objects
        $data = json_decode($request->getContent(), true);

        return new Order(
            OrderId::generate(),
            new Customer($data['customerId']),
            $this->buildOrderItems($data['items']),
            new ShippingAddress($data['address'])
        );
    }

    private function buildOrderItems(array $itemsData): OrderItems
    {
        // Build OrderItems collection from array data...
    }
}
```

#### Dependency Adapter (Driven Side)

```php
<?php

namespace App\Infrastructure\Persistence;

use App\Domain\Order\PersistOrderInterface;
use App\Domain\Order\Order;
use App\Domain\Order\OrderId;
use Doctrine\DBAL\Connection;

/**
 * Database Adapter - Dependency Adapter (Driven side).
 *
 * This adapter:
 * - Implements the persistence contract
 * - Handles database-specific concerns
 * - Translates Domain objects ↔ Database rows
 * - Contains NO business logic
 */
class OrderDatabaseRepository implements PersistOrderInterface
{
    private Connection $connection;

    public function __construct(Connection $connection)
    {
        $this->connection = $connection;
    }

    public function save(Order $order): void
    {
        try {
            // Translate Domain → Database
            $data = [
                'id' => (string) $order->getId(),
                'customer_id' => (string) $order->getCustomer()->getId(),
                'status' => $order->getStatus()->value(),
                'total' => $order->getTotal()->amount(),
                'created_at' => $order->getCreatedAt()->format('Y-m-d H:i:s'),
            ];

            $this->connection->insert('orders', $data);

            // Save order items in separate table
            foreach ($order->getItems() as $item) {
                $this->saveOrderItem($order->getId(), $item);
            }

        } catch (\Doctrine\DBAL\Exception $e) {
            // Don't leak database-specific errors
            throw new PersistenceException(
                'Unable to persist order',
                0,
                $e // But keep original for logging
            );
        }
    }

    public function findById(OrderId $id): ?Order
    {
        $data = $this->connection->fetchAssociative(
            'SELECT * FROM orders WHERE id = ?',
            [(string) $id]
        );

        if (!$data) {
            return null;
        }

        // Translate Database → Domain
        return $this->buildOrderFromData($data);
    }

    public function delete(OrderId $id): void
    {
        try {
            $this->connection->delete('orders', ['id' => (string) $id]);
        } catch (\Doctrine\DBAL\Exception $e) {
            throw new PersistenceException('Unable to delete order', 0, $e);
        }
    }

    private function saveOrderItem(OrderId $orderId, OrderItem $item): void
    {
        // Save individual order item...
    }

    private function buildOrderFromData(array $data): Order
    {
        // Reconstruct domain object from database data...
    }
}
```

### Implementing Configurers (PHP)

```php
<?php

namespace App\Infrastructure\DependencyInjection;

use App\Application\Order\PlaceOrder;
use App\Domain\Order\PlaceOrderInterface;
use App\Infrastructure\Persistence\OrderDatabaseRepository;
use App\Infrastructure\Inventory\InventoryServiceAdapter;
use App\Infrastructure\Email\EmailServiceAdapter;
use Doctrine\DBAL\Connection;

/**
 * Production Configurer.
 *
 * This is the only class that knows all the concrete implementations.
 * It creates and wires everything together.
 */
class ProductionConfigurer
{
    private Connection $dbConnection;
    private array $config;

    public function __construct(Connection $dbConnection, array $config)
    {
        $this->dbConnection = $dbConnection;
        $this->config = $config;
    }

    public function configurePlaceOrder(): PlaceOrderInterface
    {
        // Create adapters
        $orderRepository = new OrderDatabaseRepository($this->dbConnection);

        $inventory = new InventoryServiceAdapter(
            $this->config['inventory_service_url']
        );

        $emailService = new EmailServiceAdapter(
            $this->config['email_service_api_key']
        );

        // Inject adapters into business logic
        return new PlaceOrder(
            $orderRepository,
            $inventory,
            $emailService
        );
    }
}

/**
 * Test Configurer.
 *
 * Same structure but with test doubles.
 */
class TestConfigurer
{
    public function configurePlaceOrder(): PlaceOrderInterface
    {
        return new PlaceOrder(
            new InMemoryOrderRepository(),
            new FakeInventoryService(),
            new FakeEmailService()
        );
    }
}
```

### Using Façade for Multiple Dependencies

When a contract needs behavior from multiple external systems:

```php
<?php

namespace App\Infrastructure\Persistence;

use App\Domain\Order\PersistOrderInterface;
use App\Domain\Order\Order;
use App\Domain\Order\OrderId;

/**
 * Façade that coordinates multiple external dependencies.
 *
 * This is needed when the persistence contract requires:
 * - Saving to database
 * - Caching the result
 * - Publishing domain event
 */
class OrderPersistenceFacade implements PersistOrderInterface
{
    private OrderDatabaseRepository $database;
    private OrderCacheAdapter $cache;
    private EventPublisherAdapter $eventPublisher;

    public function __construct(
        OrderDatabaseRepository $database,
        OrderCacheAdapter $cache,
        EventPublisherAdapter $eventPublisher
    ) {
        $this->database = $database;
        $this->cache = $cache;
        $this->eventPublisher = $eventPublisher;
    }

    public function save(Order $order): void
    {
        // Coordinate multiple external dependencies
        $this->database->save($order);
        $this->cache->put($order);
        $this->eventPublisher->publish(new OrderPlacedEvent($order));
    }

    public function findById(OrderId $id): ?Order
    {
        // Try cache first, fall back to database
        $order = $this->cache->get($id);

        if ($order === null) {
            $order = $this->database->findById($id);
            if ($order !== null) {
                $this->cache->put($order);
            }
        }

        return $order;
    }

    public function delete(OrderId $id): void
    {
        $this->database->delete($id);
        $this->cache->remove($id);
        $this->eventPublisher->publish(new OrderDeletedEvent($id));
    }
}
```

### Configuration with Dependency Injection Container

Most PHP frameworks provide DI containers. Here's Symfony example:

```yaml
# config/services.yaml
services:
    # Ports don't need configuration - they're interfaces

    # Business Logic
    App\Application\Order\PlaceOrder:
        arguments:
            $orderRepository: '@App\Domain\Order\PersistOrderInterface'
            $inventory: '@App\Domain\Inventory\CheckInventoryInterface'
            $emailService: '@App\Domain\Email\SendEmailInterface'

    # Adapters - Bind interfaces to concrete implementations
    App\Domain\Order\PersistOrderInterface:
        class: App\Infrastructure\Persistence\OrderDatabaseRepository
        arguments:
            $connection: '@doctrine.dbal.default_connection'

    App\Domain\Inventory\CheckInventoryInterface:
        class: App\Infrastructure\Inventory\InventoryServiceAdapter
        arguments:
            $serviceUrl: '%env(INVENTORY_SERVICE_URL)%'

    App\Domain\Email\SendEmailInterface:
        class: App\Infrastructure\Email\EmailServiceAdapter
        arguments:
            $apiKey: '%env(EMAIL_SERVICE_API_KEY)%'

    # Framework Adapters
    App\Infrastructure\Http\Controller\OrderController:
        arguments:
            $placeOrder: '@App\Application\Order\PlaceOrder'
        tags: ['controller.service_arguments']
```

## Adapter Flexibility Patterns

### Behavior vs Structure Variations

**Behavior Variations** (Requirements):
- Business domain REQUIRES these variations
- Must implement because behavior demands it
- Example: Must persist data AND send email

**Structure Variations** (Design Decisions):
- Developer CHOOSES these variations
- Improve modularity without changing behavior
- Example: Adding caching layer

### Expanding in Breadth

**Multiple Frameworks** (Driving Side):
```
Android Framework → ManageStuffFromAndroid ─┐
                                            ├→ IManageStuff → ManageStuff
REST Framework → ManageStuffFromRest ───────┘
```

**Multiple Dependencies** (Driven Side):
```
ManageStuff → IPersistStuff → PersistStuffViaDB → Database
           ↓
           → IPublishEvents → PublishEventsViaKafka → Kafka
           ↓
           → ISendEmail → SendEmailViaSparkPost → SparkPost
```

Each facet of the hexagon represents a port/adapter pair.

### Expanding in Depth

**Service Calling Service**:
```
PlaceOrdersFromREST → IPlaceOrders → PlaceOrders
                                      ↓
                      IHandlePlacedOrders → HandlePlacedOrdersViaManageInventory
                                            ↓
                      IManageInventory → ManageInventory → IPersistInventory → PersistInventoryViaDB
```

This creates a chain: Strategy → Adapter → Strategy → Adapter (repeating pattern like polymer chains).

### Using Composite for Structural Breadth

**Problem**: Need to add messaging without touching Red Hexagon.

**Solution**: Composite pattern manages multiple adapters.

```php
<?php

interface IHandleStuff {
    public function handle(Stuff $stuff): void;
}

class HandleStuffViaComposite implements IHandleStuff {
    private array $handlers = [];

    public function add(IHandleStuff $handler): void {
        $this->handlers[] = $handler;
    }

    public function handle(Stuff $stuff): void {
        foreach ($this->handlers as $handler) {
            $handler->handle($stuff);
        }
    }
}

// Configuration
$composite = new HandleStuffViaComposite();
$composite->add(new PersistStuffViaDB());
$composite->add(new NotifyStuffViaMessageService());

$manageStuff = new ManageStuff($composite);
```

**Benefits**:
- No change to business logic
- No change to existing adapters
- Can add/remove handlers dynamically
- Handles both breadth and depth (composites can contain composites)

### Dispatching Pattern for Migration

**Use Case**: Migrate from Database to Cloud Storage without downtime.

```php
<?php

class PersistDocumentsViaDispatching implements IPersistDocuments
{
    private FeatureFlags $featureFlags;
    private IPersistDocuments $database;
    private IPersistDocuments $cloudStorage;

    public function add(PersonId $personId, Document $document): void
    {
        // Dual writes during migration
        if ($this->featureFlags->isEnabled('CLOUD_STORAGE')) {
            $this->cloudStorage->add($personId, $document);
        }

        if ($this->featureFlags->isEnabled('DATABASE')) {
            $this->database->add($personId, $document);
        }
    }

    public function getByPersonId(PersonId $personId): ?Document
    {
        // Prefer cloud storage, fall back to database
        $document = $this->cloudStorage->getByPersonId($personId);

        if ($document !== null) {
            return $document;
        }

        $document = $this->database->getByPersonId($personId);

        if ($document !== null && !$this->featureFlags->isEnabled('DATABASE')) {
            $this->notifyInconsistency($personId);
        }

        return $document;
    }

    public function delete(PersonId $personId): void
    {
        // Delete from both during migration
        $this->database->delete($personId);
        $this->cloudStorage->delete($personId);
    }

    private function notifyInconsistency(PersonId $personId): void
    {
        // Log/alert that document found in DB when DB disabled
    }
}
```

**Migration Stages**:
1. Enable DATABASE flag (current behavior)
2. Enable CLOUD_STORAGE flag (dual writes active)
3. Run batch migration in background
4. Disable DATABASE flag (cloud storage only)
5. Remove dispatcher and database adapter

### Nested Hexagons (Purple within Purple)

**Key Insight**: All Pure Unstable/Flexible elements have Event Horizons. They can be represented as Purple Hexagons.

**Elements That Can Be Purple Hexagons**:
- Business Logic
- Adapters
- Configurers
- Composite adapters
- Façades

**Fractal Design**: Each Purple Hexagon could contain:
- Single class, OR
- Many classes, OR
- Another complete Red/Purple Hexagon system, OR
- Completely different internal design

**Benefits**:
- Encapsulation at multiple levels
- Separation of concerns
- Different teams can work on different hexagons
- Internal design choices are local decisions

**Example with Nested Configurer**:
```php
<?php

class PersistingConfigurer {
    public static function getDocumentPersister(): IPersistDocuments {
        return new PersistDocumentsViaDispatching(
            FeatureFlagAPI::getInstance(),
            new ManageDocumentsViaDB(),
            new ManageDocumentsViaCloudStorage()
        );
    }
}

class ApplicationConfigurer {
    public function configure(): IManageDocuments {
        return new ManageDocumentsFromREST(
            new ManageDocuments(
                PersistingConfigurer::getDocumentPersister()
            )
        );
    }
}
```

## Benefits

### Primary Benefits

1. **Delayed Technology Decisions**
   - Choose databases, frameworks later when you have more information
   - Not locked into early architectural decisions
   - Can evaluate options as they mature

2. **Technology Independence**
   - Business logic doesn't know about external technologies
   - Can switch databases, frameworks, messaging systems
   - Protects against vendor lock-in or discontinuation

3. **Superior Testability**
   - Business logic tested in complete isolation
   - Test doubles easily injected
   - Fast unit tests without external dependencies
   - Integration tests only for adapters

4. **Parallel Development**
   - Once ports stabilize, teams work independently
   - Business logic team separate from infrastructure team
   - No stepping on each other's code
   - Reduced merge conflicts

5. **Clear Separation of Concerns**
   - Business rules in one place
   - Technical details in another
   - Each element has single responsibility
   - Easy to reason about

6. **Maintainability**
   - Changes localized to specific adapters
   - Business logic updates don't affect infrastructure
   - Infrastructure updates don't affect business logic
   - Clear boundaries prevent accidental coupling

7. **Domain Focus**
   - Business logic screams what it does
   - Not polluted with technical concerns
   - Ubiquitous language remains pure
   - Domain experts can understand code

8. **Flexibility**
   - Swap adapters for different environments (prod, test, dev)
   - Add new frameworks without touching business logic
   - Evolve architecture incrementally
   - Support multiple platforms simultaneously

### Design-Level Benefits

1. **Adheres to SOLID Principles**
   - Single Responsibility: Each element has one reason to change
   - Open/Closed: Open for extension (new adapters), closed for modification
   - Liskov Substitution: Adapters are substitutable through interfaces
   - Interface Segregation: Ports are cohesive contracts
   - Dependency Inversion: Depend on abstractions, not concretions

2. **Manages Complexity**
   - Complex interactions segregated to adapters
   - Business logic remains simple
   - Technical complexity doesn't pollute domain

3. **Event Horizons Provide Encapsulation**
   - Pure Stable/Fixed elements hide nothing but known to all
   - Pure Unstable/Flexible elements hide everything
   - Information hiding at architectural level

4. **No Cycles in Dependency Graph**
   - Clean dependency flow
   - No circular dependencies
   - Easy to understand flow

5. **Stability Flows to Abstractions**
   - Concrete elements depend on stable abstractions
   - Inversions happen at right boundaries
   - Tower built on solid foundation, not inverted

### Business Benefits

1. **Faster Feature Development**
   - Parallel work streams
   - Less integration friction
   - Clear contracts reduce confusion

2. **Lower Technical Debt**
   - Clean boundaries prevent coupling
   - Easy to refactor within boundaries
   - External changes contained

3. **Reduced Risk**
   - Technology changes less risky
   - Vendor issues isolated
   - Migration paths always available

4. **Better Quality**
   - Comprehensive unit testing possible
   - Bugs contained to specific components
   - Easier to diagnose issues

5. **Team Scalability**
   - More developers can work simultaneously
   - Junior developers can work on adapters
   - Senior developers focus on domain

## Trade-offs and Costs

### When the Cost Outweighs Benefits

1. **Simple CRUD Applications**
   - Overhead not justified
   - Abstraction layers add no value
   - Direct database access is fine

2. **Prototypes and Spikes**
   - Speed more important than architecture
   - Trying to learn/validate something quickly
   - May throw away anyway

3. **Scripts and Utilities**
   - One-off tools
   - No evolution expected
   - Simplicity is key

4. **Stable, Unchanging Systems**
   - If nothing will ever change
   - No testing needed beyond integration
   - Team comfortable with tight coupling

### Costs and Challenges

1. **More Classes/Files**
   - More elements to navigate
   - Directory structure more complex
   - Can seem like overkill initially

2. **Indirection**
   - Harder to "grep and find" direct calls
   - Follow dependency injection to understand wiring
   - IDE support helps but learning curve exists

3. **Upfront Design**
   - Need to think about ports/contracts early
   - Requires domain understanding
   - May need refactoring as understanding grows

4. **Team Learning Curve**
   - Developers need to understand pattern
   - Requires discipline to maintain boundaries
   - Easy to accidentally violate principles

5. **Testing Discipline Required**
   - Need to write proper test doubles
   - Can't rely on integration tests only
   - Requires unit testing mindset

6. **Not a Silver Bullet**
   - Still need good domain design
   - Doesn't solve all architectural problems
   - Can be over-applied

### Mitigating the Costs

1. **Start Small**: Apply to one bounded context, not entire system
2. **Evolve Gradually**: Refactor legacy code incrementally
3. **IDE Support**: Use tools that help navigate abstractions
4. **Team Training**: Invest in pattern education
5. **Clear Guidelines**: Document when to use and when not to use
6. **Code Reviews**: Ensure boundaries are maintained

### When Is It Worth It?

**Worth It When**:
- External dependencies likely to change
- Multiple platforms/frameworks to support
- Complex business logic needs isolation
- Comprehensive testing is priority
- Long-term maintenance expected
- Multiple teams working in parallel

**Not Worth It When**:
- Application is simple and stable
- Speed of development most critical
- Team lacks experience with pattern
- Short-lived prototype or experiment
- Direct access to external systems is fine

## Common Mistakes

### 1. Business Logic Leaking Into Adapters

**Mistake**: Putting domain rules in adapters.

**Example**:
```php
// WRONG - Business logic in adapter
class OrderDatabaseRepository implements PersistOrderInterface {
    public function save(Order $order): void {
        // Business rule in adapter - WRONG!
        if ($order->getTotal() > 1000) {
            $order->setStatus('NEEDS_APPROVAL');
        }

        $this->db->insert('orders', $order->toArray());
    }
}
```

**Fix**: Move business logic to business logic layer.

```php
// CORRECT - Business logic in domain
class PlaceOrder implements PlaceOrderInterface {
    public function execute(Order $order): OrderPlacementDetails {
        if ($order->getTotal() > 1000) {
            $order->setStatus('NEEDS_APPROVAL');
        }

        $this->orderRepository->save($order);
        // ...
    }
}

// Adapter only handles persistence
class OrderDatabaseRepository implements PersistOrderInterface {
    public function save(Order $order): void {
        $this->db->insert('orders', $order->toArray());
    }
}
```

### 2. Letting External Dependency Details Leak

**Mistake**: Exposing external specifics through ports.

**Example**:
```php
// WRONG - Database details leak through interface
interface PersistOrderInterface {
    public function save(Order $order): void;
    public function findById(int $id): ?array; // Returns array like DB does
    public function executeQuery(string $sql): Result; // Exposes SQL
}
```

**Fix**: Keep interface abstract and domain-focused.

```php
// CORRECT - Abstract interface
interface PersistOrderInterface {
    public function save(Order $order): void;
    public function findById(OrderId $id): ?Order; // Returns domain object
    public function findByCustomer(CustomerId $id): OrderCollection;
}
```

### 3. Designing Ports Around External Dependencies

**Mistake**: Creating ports that mirror external API instead of domain needs.

**Example**:
```php
// WRONG - Port mirrors database structure
interface OrderStorageInterface {
    public function insertIntoOrdersTable(array $data): void;
    public function selectFromOrdersWhereId(int $id): array;
    public function updateOrdersSetStatusWhere(string $status, int $id): void;
}
```

**Fix**: Design ports for business needs first.

```php
// CORRECT - Port reflects domain operations
interface PersistOrderInterface {
    public function save(Order $order): void;
    public function findById(OrderId $id): ?Order;
    public function updateStatus(OrderId $id, OrderStatus $status): void;
}
```

### 4. Tight Coupling Between Adapters

**Mistake**: Adapters knowing about each other.

**Example**:
```php
// WRONG - Adapter depends on another adapter
class OrderController {
    private PlaceOrder $placeOrder;
    private OrderDatabaseRepository $database; // Direct dependency on adapter

    public function placeOrder(Request $request): Response {
        $order = $this->buildOrder($request);
        $details = $this->placeOrder->execute($order);

        // Using database adapter directly - WRONG
        $this->database->updateStatistics($order);

        return $this->buildResponse($details);
    }
}
```

**Fix**: All communication through business logic.

```php
// CORRECT - Only depends on business logic interface
class OrderController {
    private PlaceOrderInterface $placeOrder;

    public function placeOrder(Request $request): Response {
        $order = $this->buildOrder($request);
        $details = $this->placeOrder->execute($order); // Statistics updated here
        return $this->buildResponse($details);
    }
}
```

### 5. Using Primitive Types Instead of Domain Types

**Mistake**: Using strings, ints, arrays in contracts instead of domain objects.

**Example**:
```php
// WRONG - Primitive obsession
interface PlaceOrderInterface {
    public function execute(
        int $customerId,
        array $items, // array of what?
        string $address
    ): array; // array of what?
}
```

**Fix**: Use domain types throughout.

```php
// CORRECT - Domain types
interface PlaceOrderInterface {
    public function execute(Order $order): OrderPlacementDetails;
}
```

### 6. Not Inverting Framework Dependencies

**Mistake**: Business logic depending on framework.

**Example**:
```php
// WRONG - Business logic depends on Symfony
use Symfony\Component\HttpFoundation\Request;

class PlaceOrder {
    public function execute(Request $request): Response {
        // Business logic mixed with framework
    }
}
```

**Fix**: Framework adapts to business logic, not vice versa.

```php
// CORRECT - Framework adapter handles framework concerns
class OrderController {
    private PlaceOrderInterface $placeOrder;

    public function placeOrder(Request $request): Response {
        $order = $this->buildOrderFromRequest($request); // Translate framework → domain
        $details = $this->placeOrder->execute($order); // Pure domain operation
        return $this->buildResponseFromDetails($details); // Translate domain → framework
    }
}
```

### 7. Creating One Adapter for Multiple Concerns

**Mistake**: Adapter doing too much (violates SRP).

**Example**:
```php
// WRONG - One adapter does everything
class OrderAdapterForEverything implements PersistOrderInterface, ValidateOrderInterface, EmailOrderInterface {
    private Database $db;
    private EmailService $email;
    private ValidationService $validator;

    // Too many responsibilities in one adapter
}
```

**Fix**: One adapter per concern, use Composite if needed.

```php
// CORRECT - Separate adapters
class OrderDatabaseRepository implements PersistOrderInterface { }
class OrderValidator implements ValidateOrderInterface { }
class OrderEmailNotifier implements EmailOrderInterface { }
```

### 8. Not Using Configurers

**Mistake**: Manual object creation throughout codebase.

**Example**:
```php
// WRONG - Scattered object creation
class OrderController {
    public function placeOrder(Request $request): Response {
        $db = new OrderDatabaseRepository(new Connection());
        $inventory = new InventoryAdapter('http://api.example.com');
        $email = new EmailAdapter('api-key');
        $placeOrder = new PlaceOrder($db, $inventory, $email);

        // ...
    }
}
```

**Fix**: Centralize configuration in Configurer or DI container.

```php
// CORRECT - Configuration centralized
// In services.yaml or Configurer
$placeOrder = new PlaceOrder(
    $this->orderRepository,
    $this->inventoryService,
    $this->emailService
);
```

### 9. Testing Through Adapters

**Mistake**: Only writing integration tests, not unit tests.

**Example**:
```php
// WRONG - Test requires database
class PlaceOrderTest extends TestCase {
    public function test_place_order() {
        $db = new DatabaseConnection('test_db');
        $placeOrder = new PlaceOrder(new OrderDatabaseRepository($db));

        // Test hits database - slow, fragile
    }
}
```

**Fix**: Unit test with test doubles.

```php
// CORRECT - Unit test with test double
class PlaceOrderTest extends TestCase {
    public function test_place_order() {
        $mockRepository = $this->createMock(PersistOrderInterface::class);
        $placeOrder = new PlaceOrder($mockRepository);

        // Fast, isolated unit test
    }
}
```

### 10. Forgetting About the Configurer Layer

**Mistake**: No clear ownership of object creation and wiring.

**Fix**:
- Make Configurers explicit
- Document what creates what
- Ensure only Configurers know about all concrete classes
- Use DI containers properly

## Pattern Relationships

### Design Patterns Used in Hexagonal Architecture

1. **Strategy Pattern**
   - Ports/Interfaces define behavioral contracts
   - Different adapters = different strategies
   - Business logic algorithm varies by adapter injected

2. **Adapter Pattern**
   - Core pattern of the architecture
   - Translates between incompatible interfaces
   - Wraps external dependencies

3. **Façade Pattern**
   - Simplifies complex external subsystems
   - Presents unified interface to business logic
   - Coordinates multiple external dependencies

4. **Dependency Injection Pattern**
   - Configurer injects dependencies
   - Loose coupling between components
   - Different configurations for different environments

5. **Factory Pattern**
   - May be used within Configurer
   - Encapsulates complex object creation
   - Abstract Factory for family of related adapters

6. **Template Method Pattern**
   - Ports may use this in some circumstances
   - Define algorithm skeleton in interface
   - Let adapters fill in steps

7. **Composite Pattern**
   - Manage multiple adapters for single interface
   - Tree structure of adapters
   - Enables breadth and depth expansion

8. **Decorator Pattern**
   - Chain adapters to add behavior
   - Example: caching decorator around database adapter
   - Maintains same interface

9. **Chain of Responsibility**
   - Dispatcher pattern uses this
   - Try multiple adapters in sequence
   - Example: check cache, then database

### Architectural Patterns Related to Hexagonal

1. **Clean Architecture** (Bob Martin)
   - Same core concepts
   - More detailed layer specification
   - Focus on use cases and entities
   - Hexagonal is structure, Clean is semantics

2. **Onion Architecture** (Jeffrey Palermo)
   - Concentric layers
   - Dependencies point inward
   - Core domain at center
   - Very similar to Hexagonal

3. **Domain-Driven Design** (Eric Evans)
   - Hexagonal supports DDD
   - Keeps domain model pure
   - Bounded contexts map to hexagons
   - Anti-Corruption Layer = Adapter

4. **Microservices Architecture**
   - Each microservice can be a hexagon
   - Service-to-service calls via adapters
   - Enables service independence
   - Supports polyglot architecture

5. **Layered Architecture**
   - Traditional 3-tier architecture
   - Hexagonal improves it with dependency inversion
   - Adapters prevent layer penetration
   - Business logic doesn't depend on data layer

6. **Event-Driven Architecture**
   - Events as port/adapter pairs
   - Event publishers and consumers as adapters
   - Domain events originate in business logic
   - Messaging platforms as external dependencies

7. **Service-Oriented Architecture (SOA)**
   - Services communicate through well-defined interfaces
   - Adapters for service integration
   - Hexagonal at service level
   - Anti-corruption layers between services

8. **CQRS (Command Query Responsibility Segregation)**
   - Commands and Queries as separate ports
   - Read and write models as adapters
   - Business logic orchestrates both
   - Natural fit with Hexagonal

### Principles Supported by Hexagonal

1. **SOLID Principles**
   - Single Responsibility: Each adapter has one job
   - Open/Closed: Add adapters without modifying business logic
   - Liskov Substitution: Adapters substitutable through interfaces
   - Interface Segregation: Ports are cohesive contracts
   - Dependency Inversion: Core principle of architecture

2. **Separation of Concerns**
   - Business logic separate from technical details
   - Each layer has distinct responsibility
   - Adapters isolate external complexity

3. **Dependency Inversion Principle**
   - High-level modules don't depend on low-level
   - Both depend on abstractions
   - Abstractions don't depend on details

4. **Acyclic Dependencies Principle**
   - No circular dependencies in design
   - Clear directional flow
   - Graph is acyclic

5. **Stable Dependencies Principle**
   - Depend on things more stable than yourself
   - Flow from flexible to stable
   - Ports are stable, adapters are flexible

6. **Stable Abstractions Principle**
   - Unstable elements are concrete
   - Stable elements are abstract
   - Flow from concrete to abstract

7. **Tell, Don't Ask**
   - Business logic tells adapters what to do
   - Doesn't ask about external state
   - Command-oriented interfaces

8. **You Aren't Gonna Need It (YAGNI)**
   - Don't create ports/adapters until needed
   - Start simple, add complexity when justified
   - Adapters enable incremental complexity

## Real-World Examples

### Example 1: E-Commerce Order Processing

**Scenario**: Multi-channel e-commerce platform accepting orders from web, mobile, API.

**Hexagonal Structure**:

```
Driving Side (Inbound):
- Web Controller → REST Adapter → IPlaceOrder
- Mobile App Handler → GraphQL Adapter → IPlaceOrder
- B2B API Gateway → RPC Adapter → IPlaceOrder

Business Logic:
- PlaceOrder (Use Case)
  - Validates order
  - Checks inventory
  - Calculates pricing
  - Orchestrates fulfillment

Driven Side (Outbound):
- IPersistOrder → PostgreSQL Adapter → PostgreSQL DB
- ICheckInventory → Inventory Service Adapter → Inventory Microservice
- ICalculatePrice → Pricing Engine Adapter → Pricing Service
- IPublishOrderEvent → Kafka Adapter → Kafka Message Broker
- ISendConfirmation → SendGrid Adapter → SendGrid Email API
```

**Benefits Realized**:
- Added mobile channel without touching order logic
- Switched from MySQL to PostgreSQL transparently
- Added B2B API while preserving existing channels
- Comprehensive unit testing without external services
- Multiple teams worked on different adapters simultaneously

### Example 2: Document Management System Migration

**Scenario**: Migrate document storage from database BLOBs to cloud storage without downtime.

**Phase 1 - Database Only**:
```
IManageDocuments → ManageDocuments → IPersistDocuments → DatabaseAdapter → Database
```

**Phase 2 - Dual Writes (Migration Period)**:
```
IManageDocuments → ManageDocuments → IPersistDocuments → Dispatcher
                                                           ├→ DatabaseAdapter → Database
                                                           └→ CloudStorageAdapter → AWS S3
```

**Phase 3 - Cloud Only**:
```
IManageDocuments → ManageDocuments → IPersistDocuments → CloudStorageAdapter → AWS S3
```

**Dispatcher Logic**:
- Writes: Both database and cloud (feature flag controlled)
- Reads: Try cloud first, fall back to database
- Monitoring: Alert when document found in database but not cloud
- Cleanup: Remove database adapter after full migration

**Benefits**:
- Zero downtime migration
- Gradual rollout with feature flags
- Rollback capability at each stage
- Business logic unchanged throughout
- Monitoring caught edge cases

### Example 3: Multi-Tenant SaaS Payment Processing

**Scenario**: Different customers use different payment gateways.

**Structure**:
```
IProcessPayment → ProcessPayment → IChargeCard → PaymentGatewayRouter
                                                   ├→ StripeAdapter → Stripe
                                                   ├→ PayPalAdapter → PayPal
                                                   └→ BraintreeAdapter → Braintree
```

**Router Logic**:
```php
class PaymentGatewayRouter implements IChargeCard {
    private array $adapters;
    private TenantConfig $config;

    public function charge(Card $card, Money $amount): PaymentResult {
        $gateway = $this->config->getGatewayForTenant();
        $adapter = $this->adapters[$gateway];
        return $adapter->charge($card, $amount);
    }
}
```

**Benefits**:
- Each tenant uses preferred gateway
- Add new gateways without affecting existing
- A/B test different gateways
- Fallback if gateway down
- Billing optimized per tenant

### Example 4: Regulatory Compliance Audit Logging

**Scenario**: Add comprehensive audit logging across all operations.

**Before**:
```
IManageRecords → ManageRecords → IPersistRecords → DatabaseAdapter
```

**After** (Using Composite):
```
IManageRecords → ManageRecords → IPersistRecords → Composite
                                                     ├→ DatabaseAdapter → Database
                                                     └→ AuditLogAdapter → Audit Log Service
```

**Composite Implementation**:
```php
class RecordPersistenceComposite implements IPersistRecords {
    private array $persisters = [];

    public function add(IPersistRecords $persister): void {
        $this->persisters[] = $persister;
    }

    public function save(Record $record): void {
        foreach ($this->persisters as $persister) {
            $persister->save($record);
        }
    }
}
```

**Configuration**:
```php
$composite = new RecordPersistenceComposite();
$composite->add(new DatabaseAdapter($db));
$composite->add(new AuditLogAdapter($auditService));

$manageRecords = new ManageRecords($composite);
```

**Benefits**:
- Audit logging added without touching business logic
- Other adapters unaware of audit logging
- Can add more adapters (caching, replication) same way
- Enable/disable audit per environment

### Example 5: Internationalization and Localization

**Scenario**: Support multiple languages and regions.

**Structure**:
```
Framework → Controller → IGreetUser → GreetUser → ITranslate → TranslationAdapter
                                                                 ├→ English
                                                                 ├→ Spanish
                                                                 └→ French
```

**Adapter Selection**:
```php
class TranslationConfigurer {
    public function getTranslator(Locale $locale): ITranslate {
        return match($locale->getLanguage()) {
            'en' => new EnglishTranslationAdapter(),
            'es' => new SpanishTranslationAdapter(),
            'fr' => new FrenchTranslationAdapter(),
            default => new EnglishTranslationAdapter(),
        };
    }
}
```

**Benefits**:
- Language selected at configuration time
- Business logic language-agnostic
- Add languages without code changes
- Different translation services per language
- A/B test translation quality

### Example 6: Testing External Service Integration

**Scenario**: Third-party API with rate limits and costs.

**Production**:
```
IFetchWeather → FetchWeather → IWeatherProvider → OpenWeatherMapAdapter → OpenWeatherMap API
```

**Test**:
```
IFetchWeather → FetchWeather → IWeatherProvider → FakeWeatherProvider (Test Double)
```

**Local Development**:
```
IFetchWeather → FetchWeather → IWeatherProvider → MockWeatherProvider → Static JSON Files
```

**Benefits**:
- No API calls during tests (fast, free)
- Deterministic test data
- Test error scenarios (API down, malformed response)
- Developers work offline
- Same business logic in all environments

## Quotes and Further Reading

### Key Quotes

**Alistair Cockburn**:
> "If it's your decision, it's design; if not, it's a requirement."

> "For those who keep asking about #hexagonalarchitecture layers, here it is: There are only 2 layers: inside. outside."

> "The Hexagonal / Ports & Adapters pattern does not nest." (Note: Others argue it does nest via Purple Hexagons)

**Bob Martin**:
> "A good architecture allows major decisions to be deferred!"

> "A good architecture maximizes the number of decisions NOT made."

> "I don't want my users knowing that I'm handing them an interface. I just want them to know that it's a ShapeFactory."

> "The first thing one should notice with an architecture is what the application does and not how it's built."

**From the Blog Series**:
> "Is it a case that they are never going to change to those external dependencies because they choose not to, or that they never can change those dependencies because they've become too tightly coupled to them?"

> "The Business Logic is the main reason for the entire design, and it is mostly invisible!"

> "The only concrete classes that know about other concrete classes are Configurers."

> "Purple Hexagons can be elements within Purple Hexagons. They are self-referential too."

### Essential Reading

**Primary Sources**:
- Alistair Cockburn's Hexagonal Architecture page: https://alistair.cockburn.us/hexagonal-architecture/
- Alistair Cockburn's reference site: https://hexagonalarchitecture.org/
- Bob Martin's Clean Architecture blog: https://blog.cleancoder.com/uncle-bob/2012/08/13/the-clean-architecture.html
- Bob Martin's Screaming Architecture: https://blog.cleancoder.com/uncle-bob/2011/09/30/Screaming-Architecture.html

**Books**:
- **Clean Architecture** by Robert C. Martin (2017)
- **Get Your Hands Dirty on Clean Architecture** by Tom Hombergs
- **Implementing Domain-Driven Design** by Vaughn Vernon
- **Clean Code** by Robert C. Martin (for interface naming and principles)

**Online Articles**:
- Hexagonal Architecture on Wikipedia: https://en.wikipedia.org/wiki/Hexagonal_architecture_(software)
- DDD, Hexagonal, Onion, Clean, CQRS: https://herbertograca.com/2017/11/16/explicit-architecture-01-ddd-hexagonal-onion-clean-cqrs-how-i-put-it-all-together/
- A Color Coded Guide to Ports and Adapters: https://8thlight.com/insights/a-color-coded-guide-to-ports-and-adapters
- The Principles of OOD by Bob Martin: http://butunclebob.com/ArticleS.UncleBob.PrinciplesOfOod

**Video Presentations**:
- Alistair in the "Hexagone" (3-part series): https://www.youtube.com/watch?v=th4AgBcrEHA
- Hexagonal Architecture and Legacy Code by Jim Humelsine: https://www.youtube.com/watch?v=aayl6FysZ_U
- The Principles of Clean Architecture by Uncle Bob: https://www.youtube.com/watch?v=o_TH-Y78tt4
- ITkonekt 2019 Robert C. Martin - Clean Architecture and Design: https://www.youtube.com/watch?v=2dKZ-dWaCiU

**Design Pattern Resources**:
- Gang of Four Design Patterns
- Martin Fowler's Patterns of Enterprise Application Architecture
- Refactoring Guru: https://refactoring.guru/design-patterns

**Related Concepts**:
- SOLID Principles: https://en.wikipedia.org/wiki/SOLID
- Dependency Injection: https://en.wikipedia.org/wiki/Dependency_injection
- Domain-Driven Design
- Test-Driven Development
- Bounded Contexts
- Anti-Corruption Layer

### Community Resources

**GitHub**:
- Search "hexagonal architecture" for implementation examples
- Language-specific examples available in Java, C#, Python, PHP, JavaScript

**Blogs and Tutorials**:
- Hexagonal Me by Juan Manuel Garrido de Paz: https://jmgarridopaz.github.io/
- Hexagonal Architecture on tsh.io: https://tsh.io/blog/hexagonal-architecture/
- Happy Coders EU: https://www.happycoders.eu/software-craftsmanship/hexagonal-architecture/
- Organizing Layers Using Hexagonal Architecture, DDD, and Spring: https://www.baeldung.com/hexagonal-architecture-ddd-spring

**Practice and Examples**:
- Kata exercises for practicing hexagonal architecture
- Legacy code refactoring examples
- Step-by-step migration guides
- Framework-specific implementations (Symfony, Laravel, Spring Boot)

## Summary

Hexagonal Architecture (Ports and Adapters) is a powerful architectural pattern that:

1. **Isolates business logic** from external dependencies through abstract ports
2. **Uses adapters** to translate between business needs and external systems
3. **Manages dependencies** to flow toward stability and abstraction
4. **Enables flexibility** through pluggable design and loose coupling
5. **Supports testing** by making test doubles trivial to inject
6. **Facilitates parallel development** by separating concerns clearly
7. **Defers technology decisions** until more information is available
8. **Protects against vendor lock-in** by abstracting external dependencies

The pattern works because of its rigorous dependency and knowledge management:
- Pure Stable/Fixed elements (Ports) at the center
- Pure Unstable/Flexible elements (Adapters, Business Logic) at the edges
- Event Horizons that prevent inappropriate coupling
- Configurers that wire everything together without polluting the design

While it adds some complexity upfront, the benefits of maintainability, testability, and flexibility typically far outweigh the costs for any non-trivial application that expects to evolve over time.

The key to success is understanding that it's not about hexagons - it's about boundaries, constraints, and managing how knowledge flows through your system.
