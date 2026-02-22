# Factory Design Patterns

## Overview

Factory patterns encapsulate object creation logic so client code depends on interfaces, not concrete classes. **"New is Glue"** -- any direct `new` call couples you to that specific type.

Three forms with increasing flexibility:

| Pattern | Structure | When to Use | Complexity |
|---------|-----------|-------------|------------|
| **Factory Method** | Static method in interface hierarchy | Simple scenarios, few implementations | Low |
| **Factory Class** | Separate static factory class | Most production code, clear separation | Medium |
| **Abstract Factory** | Factory interface + implementations | Swappable strategies, testing, families | High |

## The Problem

```php
// BAD: Client glued to concrete class
class CheckoutController {
    public function processPayment(): void {
        $processor = new StripePaymentProcessor(); // tight coupling
        $processor->process($this->order);
    }
}
```

Programming to an interface alone does not solve this -- you still call `new` on a concrete class somewhere.

## Factory Method

Static method within the interface hierarchy that returns interface references:

```php
interface PaymentProcessor {
    public static function acquire(string $type): self;
    public function process(Order $order): ProcessingResult;
}
class StripeProcessor implements PaymentProcessor {
    public static function acquire(string $type): PaymentProcessor {
        return match($type) {
            'stripe' => new self(getenv('STRIPE_API_KEY')),
            'paypal' => new PayPalProcessor(getenv('PAYPAL_CLIENT_ID')),
            default  => throw new InvalidArgumentException("Unknown: {$type}"),
        };
    }
    public function process(Order $order): ProcessingResult { /* ... */ }
}
// Client: $processor = PaymentProcessor::acquire($type);
```

Pro: Simple, no extra classes. Con: Mixes interface and creation concerns.

## Factory Class

Separate class dedicated to creation logic:

```php
class PaymentProcessorFactory {
    public static function create(string $type, array $config = []): PaymentProcessor {
        return match($type) {
            'stripe' => new StripeProcessor(
                $config['api_key'] ?? getenv('STRIPE_API_KEY'),
                $config['webhook_secret'] ?? getenv('STRIPE_WEBHOOK_SECRET')),
            'paypal' => new PayPalProcessor(
                $config['client_id'] ?? getenv('PAYPAL_CLIENT_ID'),
                $config['client_secret'] ?? getenv('PAYPAL_CLIENT_SECRET'),
                $config['sandbox'] ?? false),
            default => throw new InvalidArgumentException("Unknown: {$type}"),
        };
    }
}
// Usage: $processor = PaymentProcessorFactory::create($order->getPaymentMethod());
```

### WC_Payment_Gateway Factory (WordPress/WooCommerce)

```php
class WC_Gateway_Factory {
    public static function acquire(WC_Payment_Gateway $gateway): PaymentProcessor {
        $settings = $gateway->settings;
        return match($gateway->id) {
            'stripe' => new StripeProcessor(
                $settings['api_key'], $settings['webhook_secret']
            ),
            'paypal' => new PayPalProcessor(
                $settings['client_id'], $settings['client_secret'],
                $gateway->testmode === 'yes'
            ),
            default => throw new InvalidArgumentException("Unsupported: {$gateway->id}"),
        };
    }
}
```

### Extensible Plugin-Based Factory (WordPress)

```php
class WP_Payment_Processor_Factory implements ProcessorFactory {
    private static array $registered = [];

    public static function register(string $type, callable $creator): void {
        self::$registered[$type] = $creator;
    }
    public function acquire(string $type, array $config = []): PaymentProcessor {
        if (!isset(self::$registered[$type]))
            throw new InvalidArgumentException("Unknown: {$type}");
        return (self::$registered[$type])($config);
    }
}

// Third-party plugins register via hooks
add_action('plugins_loaded', fn() =>
    WP_Payment_Processor_Factory::register('custom', fn($c) => new CustomProcessor($c))
, 20);
```

## Abstract Factory

Factory itself implements an interface -- swap entire creation strategies at runtime:

```php
interface ProcessorFactory {
    public function acquire(string $type, array $config = []): PaymentProcessor;
    public function supports(string $type): bool;
}
class ProductionProcessorFactory implements ProcessorFactory { /* real instances */ }
class TestProcessorFactory implements ProcessorFactory { /* mock instances */ }

// Client depends on factory interface -- no knowledge of concrete types
class CheckoutController {
    public function __construct(private ProcessorFactory $factory) {}
    public function processPayment(Order $order): void {
        $this->factory->acquire($order->getPaymentMethod())->process($order);
    }
}
```

Use when you need swappable creation strategies (prod/test), consistent object families, or third-party creation logic.

## React Component Factory (TypeScript)

React factories dynamically select components at render time. Key difference from PHP: they return JSX elements, the registry is a plain object, and the "client" is a rendering component.

```typescript
interface FieldProps {
    name: string; label: string; value: unknown;
    onChange: (name: string, value: unknown) => void;
}

// Registry: maps type -> component (extensible by third-party code)
const fieldRegistry: Record<string, React.ComponentType<any>> = {
    text: TextField, email: TextField, select: SelectField,
};

function createField(config: FieldConfig, value: unknown, onChange: FieldProps['onChange']) {
    const Component = fieldRegistry[config.type];
    if (!Component) throw new Error(`Unknown field type: ${config.type}`);
    return <Component key={config.name} {...config} value={value} onChange={onChange} />;
}

// Data-driven rendering -- fields from config, not hardcoded JSX
const DynamicForm: React.FC<{ fields: FieldConfig[] }> = ({ fields }) => {
    const [values, setValues] = React.useState<Record<string, unknown>>({});
    const handleChange = (name: string, value: unknown) =>
        setValues(prev => ({ ...prev, [name]: value }));
    return <form>{fields.map(f => createField(f, values[f.name], handleChange))}</form>;
};
```

## Common Mistakes

- **Factory knows too much** -- factory does creation + persistence + logging. Keep it to creation only; other concerns belong in services.
- **Hardcoded types** -- `ProcessorFactory::create('stripe')` couples client to type. Derive from context: `$order->getPaymentMethod()` or inject an Abstract Factory.
- **Memory leaks** -- cached singleton instances never released. Use `WeakReference` (PHP 7.4+) when caching factory-produced instances.

## Decision Tree

```
Need to create objects without exposing concrete types?
+-- Yes -> Use a factory pattern
|   +-- Creation simple, bundled with interface? -> Factory Method
|   +-- Need clear separation interface/creation? -> Factory Class
|   +-- Need swappable strategies (prod/test)?    -> Abstract Factory
+-- No  -> Direct `new` is fine
```
