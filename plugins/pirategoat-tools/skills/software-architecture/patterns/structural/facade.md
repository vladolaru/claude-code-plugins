# Facade Pattern: Simplifying Complex Subsystems

**Source:** Synthesized from jhumelsine.github.io design patterns series

## The Core Problem

You need to interact with a complex subsystem that requires:
- Implementing multiple classes/interfaces
- Understanding dozens of methods across multiple interfaces
- Coordinating interactions among many objects
- Wading through thousands of pages of documentation

**Example scenario:** A middleware communication system requires implementing `Request`, `Response`, `Reader`, `Writer`, `Listener` classes, each with dozens of methods, just to send a simple client-server request.

## What is Facade?

**Facade bridges a complexity gap by providing a simplified interface to a complex subsystem.**

### The Key Insight

> _Facade is like a magic trick that's all showmanship. It doesn't involve a hidden gimmick—it's an illusion that focuses upon the Client Application's needs regardless of how many classes and objects are required to satisfy those needs._

### The Essential Difference

**Facade vs Adapter:**

| Aspect | Facade | Adapter |
|--------|--------|---------|
| **Purpose** | Bridges complexity gap | Bridges communication gap |
| **Scope** | One class interacts with MANY classes | One class interacts with ONE class |
| **Size** | Larger (coordinates multiple objects) | Smaller (simple translation) |
| **Intent** | Simplify interface for client | Make incompatible interfaces compatible |

**Both** solve alignment problems between dependencies and business logic needs, but at different scales.

## Structure

**There is no fixed structure with Facade.** It's all about **intent**, not form.

Key characteristics:
- No nifty UML class diagrams to memorize
- Each Facade implementation is unique to its context
- It's delegation to multiple classes/objects
- The specific delegates differ in every scenario

**Representational structure:**

```
┌─────────────────┐
│ Client          │
│ Application     │
└────────┬────────┘
         │ Simple Interface
         ▼
┌─────────────────┐
│ Facade          │
│ (YourFacade)    │
└────────┬────────┘
         │ Delegates to multiple objects
         ├──────────┬──────────┬──────────┐
         ▼          ▼          ▼          ▼
    ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐
    │Object A│ │Object B│ │Object C│ │Object D│
    └────────┘ └────────┘ └────────┘ └────────┘
```

## When to Use Facade

### Use Facade When:

1. **Complexity overwhelms** - A subsystem requires too many classes/interfaces for simple tasks
2. **Documentation is massive** - You face 1,000+ page documentation for basic operations
3. **Interface Segregation violated** - Interfaces have dozens of methods, but you need only a few
4. **Multiple objects must coordinate** - You need to orchestrate interactions among many objects
5. **Client code is cluttered** - Business logic is buried under infrastructure code

### Don't Use Facade When:

1. **Simple one-to-one adaptation** - Use Adapter instead
2. **No complexity to hide** - Direct usage is already simple
3. **You need full subsystem access** - Facade limits what's exposed

## The Power of Bespoke Facades

### Why You Create Your Own Facade

The developers of complex subsystems **cannot** provide a Facade that meets all client needs precisely because:
- They don't know what YOU need
- Different clients have different requirements
- No single interface can satisfy everyone

**This is actually the great power of Facade:**

> _Creating your own Facade is a bit of an illusion, but it is also the great power of this design pattern. You can design a bespoke Facade interface that meets your client application needs precisely._

### The Facade Design Process

1. **Start with Facade interface first** - Design the API you WISH you had
2. **Add methods client application needs** - Not what the subsystem provides
3. **Implement delegation later** - Figure out how to coordinate subsystem objects
4. **Maintain cohesion** - If Facade grows too large, split into multiple cohesive Facades

### Absorbing Dependency Changes

When dependency interfaces change:
- Update Facade implementation only
- Facade interface stays stable
- Client Application remains untouched
- Complexity isolated in one place

**This is why Facade is an Essential Design Pattern.**

## PHP Examples

### Example 1: WordPress Communication Manager Facade

**The Problem:** WordPress communication subsystem requires multiple classes for simple client-server communication.

**Complex subsystem (what you must implement):**

```php
<?php
// Required interfaces from the communication subsystem
interface Serializable {
    public function serialize(): string;
    public function unserialize(string $data): void;
}

interface Request extends Serializable {
    public function set_endpoint(string $endpoint): void;
    public function set_headers(array $headers): void;
    public function set_body(string $body): void;
    public function get_request_id(): string;
}

interface Response extends Serializable {
    public function get_status_code(): int;
    public function get_body(): string;
    public function get_headers(): array;
    public function is_success(): bool;
}

interface Reader {
    public function read(): Response;
    public function set_timeout(int $seconds): void;
    public function set_buffer_size(int $bytes): void;
    public function enable_compression(): void;
    // ... 20+ more methods for other communication styles
}

interface Writer {
    public function write(Request $request): void;
    public function flush(): void;
    public function set_chunk_size(int $bytes): void;
    // ... 20+ more methods for broadcast, pub/sub, etc.
}

interface Listener {
    public function on_response_received(Response $response): void;
    public function on_error(\Exception $e): void;
    public function on_timeout(): void;
    // ... 15+ more callback methods
}

// Your business objects
class PaymentRequest {
    public $order_id;
    public $amount;
    public $currency;
}

class PaymentResponse {
    public $success;
    public $transaction_id;
    public $message;
}
```

**Without Facade (complex client code):**

```php
<?php
class PaymentProcessor {
    private $request_impl;
    private $response_impl;
    private $reader;
    private $writer;
    private $listener;

    public function __construct() {
        // Must instantiate and configure all these objects
        $this->request_impl = new RequestImpl();
        $this->response_impl = new ResponseImpl();
        $this->reader = new ReaderImpl();
        $this->writer = new WriterImpl();
        $this->listener = new ListenerImpl();

        // Configure each one
        $this->reader->set_timeout(30);
        $this->reader->set_buffer_size(4096);
        $this->writer->set_chunk_size(1024);

        // Wire them together
        $this->reader->add_listener($this->listener);
        // ... dozens more configuration calls
    }

    public function process_payment(PaymentRequest $payment): PaymentResponse {
        // Business logic buried under infrastructure code
        $this->request_impl->set_endpoint('/api/payment');
        $this->request_impl->set_headers(['Content-Type' => 'application/json']);
        $this->request_impl->set_body(json_encode([
            'order_id' => $payment->order_id,
            'amount' => $payment->amount,
            'currency' => $payment->currency,
        ]));

        $this->writer->write($this->request_impl);
        $this->writer->flush();

        $raw_response = $this->reader->read();

        // Convert complex Response to business object
        $response = new PaymentResponse();
        $response->success = $raw_response->is_success();
        $body = json_decode($raw_response->get_body(), true);
        $response->transaction_id = $body['transaction_id'] ?? null;
        $response->message = $body['message'] ?? '';

        return $response;
    }
}
```

**With Facade (simple client code):**

```php
<?php
/**
 * Facade: Simplifies client-server communication
 */
interface ClientConnection {
    /**
     * Send request and receive response
     *
     * @param string $endpoint API endpoint
     * @param array $data Request data
     * @param array $options Optional configuration
     * @return array Response data
     * @throws CommunicationException On communication failure
     */
    public function send(string $endpoint, array $data, array $options = []): array;
}

/**
 * Implementation hides all complexity
 */
class ClientConnectionImpl implements ClientConnection {
    private $request_impl;
    private $response_impl;
    private $reader;
    private $writer;
    private $listener;

    public function __construct() {
        // All the complex setup happens here, once
        $this->request_impl = new RequestImpl();
        $this->response_impl = new ResponseImpl();
        $this->reader = new ReaderImpl();
        $this->writer = new WriterImpl();
        $this->listener = new ListenerImpl();

        // Default configuration
        $this->reader->set_timeout(30);
        $this->reader->set_buffer_size(4096);
        $this->writer->set_chunk_size(1024);
        $this->reader->add_listener($this->listener);
    }

    public function send(string $endpoint, array $data, array $options = []): array {
        // Coordinate all the objects behind the scenes
        $this->request_impl->set_endpoint($endpoint);
        $this->request_impl->set_headers($options['headers'] ?? ['Content-Type' => 'application/json']);
        $this->request_impl->set_body(json_encode($data));

        $this->writer->write($this->request_impl);
        $this->writer->flush();

        $raw_response = $this->reader->read();

        if (!$raw_response->is_success()) {
            throw new CommunicationException(
                'Request failed: ' . $raw_response->get_body(),
                $raw_response->get_status_code()
            );
        }

        return json_decode($raw_response->get_body(), true);
    }
}

/**
 * Client code is now clean and focused on business logic
 */
class PaymentProcessor {
    private $connection;

    public function __construct(ClientConnection $connection) {
        $this->connection = $connection;
    }

    public function process_payment(PaymentRequest $payment): PaymentResponse {
        // Business logic is clear and concise
        try {
            $result = $this->connection->send('/api/payment', [
                'order_id' => $payment->order_id,
                'amount' => $payment->amount,
                'currency' => $payment->currency,
            ]);

            $response = new PaymentResponse();
            $response->success = true;
            $response->transaction_id = $result['transaction_id'];
            $response->message = $result['message'];

            return $response;
        } catch (CommunicationException $e) {
            $response = new PaymentResponse();
            $response->success = false;
            $response->message = $e->getMessage();
            return $response;
        }
    }
}
```

**Benefits:**
- Business logic is clean and readable
- Complex coordination hidden in Facade
- Easy to test (mock `ClientConnection`)
- Infrastructure complexity isolated

### Example 2: WordPress Database Facade

**The Problem:** WordPress database layer (`wpdb`) has complex API with many methods, query building, escaping, and error handling scattered throughout code.

**Without Facade:**

```php
<?php
class OrderRepository {
    private $wpdb;

    public function __construct() {
        global $wpdb;
        $this->wpdb = $wpdb;
    }

    public function find_by_id(int $order_id): ?array {
        // SQL building scattered in business logic
        $table = $this->wpdb->prefix . 'orders';
        $query = $this->wpdb->prepare(
            "SELECT * FROM {$table} WHERE id = %d",
            $order_id
        );
        $result = $this->wpdb->get_row($query, ARRAY_A);

        if ($this->wpdb->last_error) {
            error_log("Database error: {$this->wpdb->last_error}");
            return null;
        }

        return $result ?: null;
    }

    public function find_by_customer(int $customer_id): array {
        $table = $this->wpdb->prefix . 'orders';
        $query = $this->wpdb->prepare(
            "SELECT * FROM {$table} WHERE customer_id = %d ORDER BY created_at DESC",
            $customer_id
        );
        $results = $this->wpdb->get_results($query, ARRAY_A);

        if ($this->wpdb->last_error) {
            error_log("Database error: {$this->wpdb->last_error}");
            return [];
        }

        return $results ?: [];
    }

    public function save(array $order): bool {
        $table = $this->wpdb->prefix . 'orders';

        if (isset($order['id'])) {
            // Update
            $id = $order['id'];
            unset($order['id']);

            $result = $this->wpdb->update(
                $table,
                $order,
                ['id' => $id],
                ['%s', '%d', '%s'], // Format strings
                ['%d']
            );

            if ($this->wpdb->last_error) {
                error_log("Database error: {$this->wpdb->last_error}");
                return false;
            }

            return $result !== false;
        } else {
            // Insert
            $result = $this->wpdb->insert(
                $table,
                $order,
                ['%s', '%d', '%s'] // Format strings
            );

            if ($this->wpdb->last_error) {
                error_log("Database error: {$this->wpdb->last_error}");
                return false;
            }

            $order['id'] = $this->wpdb->insert_id;
            return true;
        }
    }
}
```

**With Facade:**

```php
<?php
/**
 * Database Facade: Simplifies common database operations
 */
interface DatabaseFacade {
    public function find_one(string $table, array $where): ?array;
    public function find_many(string $table, array $where = [], array $order_by = []): array;
    public function insert(string $table, array $data): int;
    public function update(string $table, array $data, array $where): bool;
    public function delete(string $table, array $where): bool;
    public function query(string $sql, array $params = []): array;
}

class WordPressDatabase implements DatabaseFacade {
    private $wpdb;

    public function __construct() {
        global $wpdb;
        $this->wpdb = $wpdb;
    }

    public function find_one(string $table, array $where): ?array {
        $table_name = $this->wpdb->prefix . $table;
        $query = $this->build_select_query($table_name, $where);

        $result = $this->wpdb->get_row($query, ARRAY_A);
        $this->check_error();

        return $result ?: null;
    }

    public function find_many(string $table, array $where = [], array $order_by = []): array {
        $table_name = $this->wpdb->prefix . $table;
        $query = $this->build_select_query($table_name, $where, $order_by);

        $results = $this->wpdb->get_results($query, ARRAY_A);
        $this->check_error();

        return $results ?: [];
    }

    public function insert(string $table, array $data): int {
        $table_name = $this->wpdb->prefix . $table;
        $formats = $this->determine_formats($data);

        $result = $this->wpdb->insert($table_name, $data, $formats);
        $this->check_error();

        if ($result === false) {
            throw new DatabaseException("Failed to insert into {$table}");
        }

        return $this->wpdb->insert_id;
    }

    public function update(string $table, array $data, array $where): bool {
        $table_name = $this->wpdb->prefix . $table;
        $data_formats = $this->determine_formats($data);
        $where_formats = $this->determine_formats($where);

        $result = $this->wpdb->update($table_name, $data, $where, $data_formats, $where_formats);
        $this->check_error();

        return $result !== false;
    }

    public function delete(string $table, array $where): bool {
        $table_name = $this->wpdb->prefix . $table;
        $formats = $this->determine_formats($where);

        $result = $this->wpdb->delete($table_name, $where, $formats);
        $this->check_error();

        return $result !== false;
    }

    public function query(string $sql, array $params = []): array {
        if (!empty($params)) {
            $sql = $this->wpdb->prepare($sql, ...$params);
        }

        $results = $this->wpdb->get_results($sql, ARRAY_A);
        $this->check_error();

        return $results ?: [];
    }

    // Private helpers encapsulate complexity
    private function build_select_query(string $table, array $where = [], array $order_by = []): string {
        $query = "SELECT * FROM {$table}";

        if (!empty($where)) {
            $conditions = [];
            foreach ($where as $column => $value) {
                $conditions[] = $this->wpdb->prepare("{$column} = %s", $value);
            }
            $query .= ' WHERE ' . implode(' AND ', $conditions);
        }

        if (!empty($order_by)) {
            $order_clauses = [];
            foreach ($order_by as $column => $direction) {
                $direction = strtoupper($direction) === 'DESC' ? 'DESC' : 'ASC';
                $order_clauses[] = "{$column} {$direction}";
            }
            $query .= ' ORDER BY ' . implode(', ', $order_clauses);
        }

        return $query;
    }

    private function determine_formats(array $data): array {
        $formats = [];
        foreach ($data as $value) {
            if (is_int($value)) {
                $formats[] = '%d';
            } elseif (is_float($value)) {
                $formats[] = '%f';
            } else {
                $formats[] = '%s';
            }
        }
        return $formats;
    }

    private function check_error(): void {
        if ($this->wpdb->last_error) {
            throw new DatabaseException($this->wpdb->last_error);
        }
    }
}

/**
 * Client code is now clean and database-agnostic
 */
class OrderRepository {
    private $db;

    public function __construct(DatabaseFacade $db) {
        $this->db = $db;
    }

    public function find_by_id(int $order_id): ?array {
        return $this->db->find_one('orders', ['id' => $order_id]);
    }

    public function find_by_customer(int $customer_id): array {
        return $this->db->find_many(
            'orders',
            ['customer_id' => $customer_id],
            ['created_at' => 'DESC']
        );
    }

    public function save(array $order): bool {
        if (isset($order['id'])) {
            $id = $order['id'];
            unset($order['id']);
            return $this->db->update('orders', $order, ['id' => $id]);
        } else {
            $order['id'] = $this->db->insert('orders', $order);
            return true;
        }
    }

    public function delete(int $order_id): bool {
        return $this->db->delete('orders', ['id' => $order_id]);
    }
}
```

**Benefits:**
- Repository code is clean and focused on business logic
- Database complexity hidden in Facade
- Easy to test (mock `DatabaseFacade`)
- Could swap WordPress database for different implementation
- Error handling centralized
- Format string logic encapsulated

### Example 3: WooCommerce Email Notification Facade

**The Problem:** Sending email notifications in WooCommerce requires coordinating multiple classes: `WC_Email`, `WC_Order`, `WC_Customer`, template loading, variable replacement, etc.

**With Facade:**

```php
<?php
/**
 * Email Notification Facade
 */
interface EmailNotificationFacade {
    public function send_order_confirmation(int $order_id): bool;
    public function send_shipping_notification(int $order_id, string $tracking_number): bool;
    public function send_refund_notification(int $order_id, float $amount): bool;
    public function send_custom_notification(string $to, string $subject, string $template, array $data): bool;
}

class WooCommerceEmailNotifications implements EmailNotificationFacade {
    private $mailer;
    private $template_loader;
    private $order_repository;

    public function __construct() {
        // Initialize complex dependencies
        $this->mailer = WC()->mailer();
        $this->template_loader = new WC_Template_Loader();
        $this->order_repository = new WC_Order_Repository();
    }

    public function send_order_confirmation(int $order_id): bool {
        $order = $this->order_repository->get($order_id);
        if (!$order) {
            return false;
        }

        $customer = $order->get_customer();
        $items = $order->get_items();
        $total = $order->get_total();

        $template_data = [
            'order_id' => $order_id,
            'order_date' => $order->get_date_created()->format('Y-m-d'),
            'customer_name' => $customer->get_display_name(),
            'items' => $items,
            'total' => wc_price($total),
        ];

        return $this->send_email(
            $customer->get_email(),
            __('Order Confirmation', 'woocommerce'),
            'order-confirmation',
            $template_data
        );
    }

    public function send_shipping_notification(int $order_id, string $tracking_number): bool {
        $order = $this->order_repository->get($order_id);
        if (!$order) {
            return false;
        }

        $customer = $order->get_customer();

        $template_data = [
            'order_id' => $order_id,
            'customer_name' => $customer->get_display_name(),
            'tracking_number' => $tracking_number,
            'tracking_url' => $this->build_tracking_url($tracking_number),
        ];

        return $this->send_email(
            $customer->get_email(),
            __('Your order has shipped', 'woocommerce'),
            'shipping-notification',
            $template_data
        );
    }

    public function send_refund_notification(int $order_id, float $amount): bool {
        $order = $this->order_repository->get($order_id);
        if (!$order) {
            return false;
        }

        $customer = $order->get_customer();

        $template_data = [
            'order_id' => $order_id,
            'customer_name' => $customer->get_display_name(),
            'refund_amount' => wc_price($amount),
        ];

        return $this->send_email(
            $customer->get_email(),
            __('Refund Processed', 'woocommerce'),
            'refund-notification',
            $template_data
        );
    }

    public function send_custom_notification(string $to, string $subject, string $template, array $data): bool {
        return $this->send_email($to, $subject, $template, $data);
    }

    // Private helper that coordinates all the complexity
    private function send_email(string $to, string $subject, string $template_name, array $data): bool {
        try {
            // Load template
            $template_path = $this->template_loader->locate_template("emails/{$template_name}.php");
            if (!$template_path) {
                throw new EmailException("Template not found: {$template_name}");
            }

            // Render template with data
            ob_start();
            extract($data);
            include $template_path;
            $message = ob_get_clean();

            // Wrap in email template
            $message = $this->mailer->wrap_message($subject, $message);

            // Set headers
            $headers = [
                'Content-Type: text/html; charset=UTF-8',
                'From: ' . get_bloginfo('name') . ' <' . get_option('admin_email') . '>',
            ];

            // Send
            $result = wp_mail($to, $subject, $message, $headers);

            if (!$result) {
                throw new EmailException("Failed to send email to {$to}");
            }

            // Log success
            $this->log_email_sent($to, $subject);

            return true;

        } catch (EmailException $e) {
            error_log("Email error: {$e->getMessage()}");
            return false;
        }
    }

    private function build_tracking_url(string $tracking_number): string {
        // Complex tracking URL building logic
        $carrier = $this->detect_carrier($tracking_number);
        return $carrier->get_tracking_url($tracking_number);
    }

    private function detect_carrier(string $tracking_number): Carrier {
        // Carrier detection logic
        // ...
        return new USPSCarrier();
    }

    private function log_email_sent(string $to, string $subject): void {
        // Logging logic
        do_action('wc_email_sent', $to, $subject, time());
    }
}

/**
 * Client code is simple and expressive
 */
class OrderProcessor {
    private $email_facade;

    public function __construct(EmailNotificationFacade $email_facade) {
        $this->email_facade = $email_facade;
    }

    public function complete_order(int $order_id): void {
        // Business logic for completing order
        // ...

        // Clean, simple email notification
        $this->email_facade->send_order_confirmation($order_id);
    }

    public function ship_order(int $order_id, string $tracking_number): void {
        // Business logic for shipping
        // ...

        $this->email_facade->send_shipping_notification($order_id, $tracking_number);
    }

    public function refund_order(int $order_id, float $amount): void {
        // Business logic for refund
        // ...

        $this->email_facade->send_refund_notification($order_id, $amount);
    }
}
```

**Benefits:**
- Email sending is one simple method call
- Template loading, rendering, wrapping all hidden
- Carrier detection and tracking URL building encapsulated
- Easy to test business logic (mock email facade)
- Email complexity doesn't leak into business code

## Testing with Facades

Facades make testing easier by providing a clean seam for test doubles.

```php
<?php
/**
 * Test Double for ClientConnection Facade
 */
class MockClientConnection implements ClientConnection {
    private $responses = [];
    private $calls = [];

    public function set_response(string $endpoint, array $response): void {
        $this->responses[$endpoint] = $response;
    }

    public function send(string $endpoint, array $data, array $options = []): array {
        $this->calls[] = [
            'endpoint' => $endpoint,
            'data' => $data,
            'options' => $options,
        ];

        if (!isset($this->responses[$endpoint])) {
            throw new CommunicationException("No mock response set for {$endpoint}");
        }

        return $this->responses[$endpoint];
    }

    public function get_calls(): array {
        return $this->calls;
    }

    public function assert_called_with(string $endpoint, array $expected_data): void {
        foreach ($this->calls as $call) {
            if ($call['endpoint'] === $endpoint && $call['data'] === $expected_data) {
                return; // Found matching call
            }
        }

        throw new AssertionException("Expected call to {$endpoint} not found");
    }
}

/**
 * Test using the mock
 */
class PaymentProcessorTest extends WP_UnitTestCase {
    public function test_processes_payment_successfully(): void {
        // Arrange: Create mock Facade
        $mock_connection = new MockClientConnection();
        $mock_connection->set_response('/api/payment', [
            'transaction_id' => 'TXN-12345',
            'message' => 'Payment approved',
        ]);

        $processor = new PaymentProcessor($mock_connection);

        $payment_request = new PaymentRequest();
        $payment_request->order_id = 123;
        $payment_request->amount = 99.99;
        $payment_request->currency = 'USD';

        // Act
        $response = $processor->process_payment($payment_request);

        // Assert
        $this->assertTrue($response->success);
        $this->assertEquals('TXN-12345', $response->transaction_id);

        $mock_connection->assert_called_with('/api/payment', [
            'order_id' => 123,
            'amount' => 99.99,
            'currency' => 'USD',
        ]);
    }

    public function test_handles_communication_failure(): void {
        $mock_connection = new MockClientConnection();
        // Don't set a response - will throw exception

        $processor = new PaymentProcessor($mock_connection);

        $payment_request = new PaymentRequest();
        $payment_request->order_id = 123;
        $payment_request->amount = 99.99;
        $payment_request->currency = 'USD';

        $response = $processor->process_payment($payment_request);

        $this->assertFalse($response->success);
        $this->assertStringContainsString('No mock response', $response->message);
    }
}
```

## Design Guidelines

### 1. Start with the Interface You Want

Don't start with what the subsystem provides. Start with what the CLIENT needs.

```php
<?php
// Don't think: "The subsystem has Reader, Writer, Listener..."
// Think: "My client needs to send a request and get a response"

interface ClientConnection {
    public function send(Request $request): Response;
}
```

### 2. Keep Facade Methods Cohesive

If a Facade grows too large or loses cohesion, split it.

```php
<?php
// BAD: One giant Facade doing everything
interface MessagingFacade {
    public function send_client_server(Request $request): Response;
    public function broadcast_message(Message $message): void;
    public function subscribe_to_topic(string $topic, callable $callback): void;
    public function publish_to_queue(string $queue, $data): void;
    // Too many unrelated responsibilities
}

// GOOD: Split into cohesive Facades
interface ClientServerConnection {
    public function send(Request $request): Response;
}

interface BroadcastService {
    public function broadcast(Message $message): void;
}

interface PubSubService {
    public function subscribe(string $topic, callable $callback): void;
}

interface QueueService {
    public function publish(string $queue, $data): void;
}
```

### 3. Facade Can Evolve

Start simple. Add methods as needed. Refactor when it grows.

```php
<?php
// Version 1: Simple
interface OrderFacade {
    public function create_order(array $data): int;
}

// Version 2: Add more as needed
interface OrderFacade {
    public function create_order(array $data): int;
    public function cancel_order(int $order_id): bool;
}

// Version 3: Growing...
interface OrderFacade {
    public function create_order(array $data): int;
    public function cancel_order(int $order_id): bool;
    public function refund_order(int $order_id, float $amount): bool;
    public function ship_order(int $order_id, string $tracking): bool;
}

// Version 4: Time to split? Maybe...
interface OrderManagement {
    public function create_order(array $data): int;
    public function cancel_order(int $order_id): bool;
}

interface OrderFulfillment {
    public function ship_order(int $order_id, string $tracking): bool;
}

interface OrderRefunds {
    public function refund_order(int $order_id, float $amount): bool;
}
```

### 4. Isolate Subsystem Changes

When subsystem interfaces change, update Facade implementation only.

```php
<?php
// Subsystem changes from v1 to v2
// v1: Reader->read() returns string
// v2: Reader->read() returns Response object

class ClientConnectionImpl implements ClientConnection {
    public function send(Request $request): Response {
        // v1 implementation
        // $raw_data = $this->reader->read();
        // return $this->parse_response($raw_data);

        // v2 implementation (Facade interface unchanged!)
        return $this->reader->read();
    }
}

// Client code never knew about the change
// Facade absorbed the breaking change
```

## Common Mistakes

### Mistake 1: Creating Unnecessary Facades

**Don't create a Facade if the subsystem is already simple.**

```php
<?php
// BAD: Pointless Facade
interface LoggerFacade {
    public function log(string $message): void;
}

// The underlying logger is already simple!
class Logger {
    public function log(string $message): void {
        error_log($message);
    }
}

// Just use the logger directly
```

### Mistake 2: Leaking Subsystem Types

**Facade should use its own types, not expose subsystem types.**

```php
<?php
// BAD: Leaks subsystem types
interface ClientConnection {
    public function send(SubsystemRequest $request): SubsystemResponse;
    //                   ^^^^^^^^^^^^^^^^         ^^^^^^^^^^^^^^^^^^
    //                   Subsystem types leaked!
}

// GOOD: Uses own types
interface ClientConnection {
    public function send(Request $request): Response;
    //                   ^^^^^^^            ^^^^^^^^
    //                   Facade's own types
}
```

### Mistake 3: Making Facade Too Generic

**Facade should be tailored to specific client needs, not generic.**

```php
<?php
// BAD: Too generic (just a thin wrapper)
interface CommunicationFacade {
    public function execute(array $config): array;
    // What does this do? What's in $config?
}

// GOOD: Specific to client needs
interface ClientConnection {
    public function send_payment_request(
        string $endpoint,
        float $amount,
        string $currency
    ): PaymentResponse;
    // Clear what it does and what it needs
}
```

### Mistake 4: Not Using Facade Consistently

**Once you create a Facade, use it everywhere. Don't bypass it.**

```php
<?php
// BAD: Mixing Facade and direct subsystem access
class OrderProcessor {
    private $connection;
    private $reader; // Direct subsystem access!

    public function process_order($order) {
        // Sometimes uses Facade
        $response = $this->connection->send($request);

        // Sometimes bypasses Facade
        $raw_data = $this->reader->read(); // Don't do this!
    }
}

// GOOD: Only use Facade
class OrderProcessor {
    private $connection;

    public function process_order($order) {
        // Always use Facade
        $response = $this->connection->send($request);
    }
}
```

## Real-World Facades in WordPress/WooCommerce

### WordPress Already Uses Facades

WordPress core has several built-in facades:

1. **`wp_mail()`** - Facades PHPMailer complexity
2. **`wp_remote_get()`/`wp_remote_post()`** - Facades HTTP request complexity
3. **`wp_cache_*()` functions** - Facades object caching complexity
4. **`get_option()`/`update_option()`** - Facades options table complexity

**Example: `wp_mail()` is a Facade**

```php
<?php
// Without wp_mail() (direct PHPMailer usage)
$mail = new PHPMailer(true);
try {
    $mail->setFrom('from@example.com', 'From Name');
    $mail->addAddress('to@example.com', 'To Name');
    $mail->Subject = 'Subject';
    $mail->Body = 'Message body';
    $mail->CharSet = 'UTF-8';
    $mail->isHTML(true);
    $mail->send();
} catch (Exception $e) {
    error_log("Mailer Error: {$mail->ErrorInfo}");
}

// With wp_mail() Facade
wp_mail('to@example.com', 'Subject', 'Message body', [
    'From: From Name <from@example.com>',
    'Content-Type: text/html; charset=UTF-8'
]);
```

### When to Create Your Own Facade

Create a Facade when WordPress/WooCommerce subsystems are:
- Too complex for your specific use case
- Require coordinating multiple classes
- Don't provide the interface you need

## Summary: Key Takeaways

### The Essential Insight

> **Facade bridges a complexity gap by providing a bespoke interface tailored to client needs.**

### When to Use

- Subsystem requires implementing many classes for simple tasks
- Documentation is overwhelming (1,000+ pages)
- Business logic is buried under infrastructure code
- Need to coordinate multiple objects
- Want to isolate client from subsystem changes

### Design Process

1. **Start with interface client needs** (not what subsystem provides)
2. **Add methods as needed** (not all at once)
3. **Implement delegation** to coordinate subsystem objects
4. **Keep cohesive** (split if grows too large)
5. **Absorb changes** in implementation (keep interface stable)

### Benefits

- **Simplicity** - Client code is clean and readable
- **Isolation** - Subsystem complexity hidden
- **Flexibility** - Easy to change implementation
- **Testability** - Clean seam for test doubles
- **Stability** - Absorbs subsystem changes

### The Power

> _Creating your own Facade is the great power of this design pattern. You can design a bespoke Facade interface that meets your client application needs precisely._

### Why It's Essential

Facade is on the Essential Design Patterns list because:
- Almost every complex subsystem needs one
- You'll create them yourself (subsystem developers can't know your needs)
- They dramatically improve code readability
- They isolate complexity effectively
- They make testing practical

## Further Reading

- [Wikipedia: Facade Pattern](https://en.wikipedia.org/wiki/Facade_pattern)
- [Refactoring Guru: Facade](https://refactoring.guru/design-patterns/facade)
- [Source Making: Facade Pattern](https://sourcemaking.com/design_patterns/facade)
- Gang of Four: Design Patterns (Chapter on Facade)
- Clean Code: Design Patterns, Episode 33 (Robert C. Martin)
