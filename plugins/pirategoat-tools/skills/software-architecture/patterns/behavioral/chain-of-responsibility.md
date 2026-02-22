# Chain of Responsibility Pattern

## Quick Reference

| Aspect | Detail |
|--------|--------|
| Intent | Delegate a request through a linked chain of handlers until one can process it |
| When to Use | Multiple potential handlers, only one should process; handler selection at runtime |
| Key Benefit | Decouples sender from receiver; dynamically configurable handler chains |

## When to Use

- **Multiple handlers** can process a request, but only one should
- **Handler selection** determined at runtime, not hardcoded
- **Resource optimization** -- check cheap handlers before expensive ones (cache -> DB -> API)
- **Request sender** shouldn't know which handler processes it
- **Handler set** can change dynamically per environment or configuration

Common use cases: caching layers, authentication/authorization, exception handling, HTTP middleware, payment gateway fallbacks, WordPress hook/filter priority chains, image size selection.

## When NOT to Use

- **Every handler must execute** -- use Decorator or Composite instead
- **Handler order doesn't matter** -- use Strategy or handler collection
- **Single handler always known** -- use direct invocation
- **Simple if/else sufficient** -- don't over-engineer
- **Performance critical** -- chain traversal adds overhead

## WordPress/PHP

```php
interface RequestHandler {
    public function handleRequest(Request $request): Response;
}

/**
 * Template Method approach: delegation logic centralized and final.
 * Concrete handlers only implement canHandle() and processRequest().
 */
abstract class DelegatingRequestHandler implements RequestHandler {
    private RequestHandler $nextHandler;

    public function __construct(RequestHandler $nextHandler) {
        $this->nextHandler = $nextHandler;
    }

    final public function handleRequest(Request $request): Response {
        if ($this->canHandle($request)) {
            return $this->processRequest($request);
        }
        return $this->nextHandler->handleRequest($request);
    }

    abstract protected function canHandle(Request $request): bool;
    abstract protected function processRequest(Request $request): Response;
}

// Anchor: default behavior when no handler processes request
class AnchoringRequestHandler implements RequestHandler {
    public function handleRequest(Request $request): Response {
        throw new UnhandledRequestException($request);
    }
}

// Concrete handler example
class CacheHandler extends DelegatingRequestHandler {
    private array $cache = [];

    protected function canHandle(Request $request): bool {
        return isset($this->cache[$request->getKey()]);
    }

    protected function processRequest(Request $request): Response {
        return $this->cache[$request->getKey()];
    }
}

// Configurer: assembles chain from end to beginning
class HandlerConfigurer {
    public function create(array $config): RequestHandler {
        $anchor = new AnchoringRequestHandler();
        $chain = $anchor;

        if ($config['api_enabled'] ?? false) {
            $chain = new ApiHandler($chain);
        }
        if ($config['db_enabled'] ?? false) {
            $chain = new DatabaseHandler($chain);
        }
        // Cache always first (cheapest)
        $chain = new CacheHandler($chain);

        return $chain;
    }
}

// Client: no knowledge of chain composition
$handler = (new HandlerConfigurer())->create($config);
$response = $handler->handleRequest($request);
```

**WordPress hook system as CoR:** Filters apply callbacks in priority order. Each callback transforms the value and passes it along. Rewrite rules try URL patterns sequentially until one matches. Authentication tries different auth methods in order.

## Common Mistakes

- **WRONG:** Duplicating delegation logic in every concrete handler (GoF approach)
  **RIGHT:** Use Template Method -- centralize delegation in abstract class with `final`

- **WRONG:** Nullable next handler reference (null pointer at chain end)
  **RIGHT:** Always terminate with an anchor handler that provides default behavior

- **WRONG:** Concrete handlers aware they're in a chain (calling `parent::handleRequest()`)
  **RIGHT:** Concrete handlers only answer: "Can I handle this?" and "How do I handle this?"

- **WRONG:** No anchor handler -- chain ends with exception or null
  **RIGHT:** Explicit anchor: throw, return default, or log depending on requirements

## Relationships

- **Decorator** -- nearly identical structure but different behavior. CoR stops at first match; Decorator always traverses entire chain adding cumulative behavior.
- **Strategy** -- CoR extends Strategy. Each handler is a strategy; CoR chains them for sequential attempt.
- **Template Method** -- used inside the abstract handler to centralize and protect delegation logic
- **Adapter** -- often used within handlers to integrate external systems (DB, web service, API)
- **Observer** -- used for cache invalidation and keeping chain elements synchronized
- **Composite** -- similar tree structure, but Composite is for part-whole hierarchies
