# Jest and Vitest Patterns

Testing patterns for JavaScript/TypeScript using Jest and Vitest.

## Quick Reference: Assertions

| Assertion | Use When |
|-----------|----------|
| `expect(x).toBe(y)` | Strict equality (===) |
| `expect(x).toEqual(y)` | Deep equality (objects, arrays) |
| `expect(x).toBeTruthy()` | Truthy value |
| `expect(x).toBeFalsy()` | Falsy value |
| `expect(x).toBeNull()` | Exactly null |
| `expect(x).toBeUndefined()` | Exactly undefined |
| `expect(x).toContain(item)` | Array/string contains |
| `expect(x).toHaveLength(n)` | Array/string length |
| `expect(x).toThrow()` | Function throws |
| `expect(x).toHaveBeenCalled()` | Mock was called |

---

## Basic Test Structure

```javascript
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
// or
import { describe, it, expect, beforeEach, afterEach } from '@jest/globals';

describe('OrderService', () => {
    let orderService;

    beforeEach(() => {
        orderService = new OrderService();
    });

    afterEach(() => {
        // Cleanup
    });

    it('should create an order with valid data', () => {
        const order = orderService.create({ items: [item] });

        expect(order).toBeDefined();
        expect(order.status).toBe('pending');
    });
});
```

---

## Test Organization

### Grouping with Describe

```javascript
describe('OrderService', () => {
    describe('create', () => {
        it('should create order with valid data', () => {});
        it('should throw when items is empty', () => {});
    });

    describe('calculateTotal', () => {
        it('should sum item prices', () => {});
        it('should include tax when enabled', () => {});
        it('should apply discounts', () => {});
    });
});
```

### Test Naming

```javascript
// Good: Describes behavior
it('should return empty array when cart has no items', () => {});
it('should calculate tax based on shipping address', () => {});
it('should throw ValidationError when email is invalid', () => {});

// Bad: Vague or implementation-focused
it('works', () => {});
it('handles data', () => {});
it('calls the function', () => {});
```

---

## Mocking

### Function Mocks

```javascript
// Jest
const callback = jest.fn();
callback.mockReturnValue(42);
callback.mockResolvedValue({ data: 'value' });
callback.mockRejectedValue(new Error('Failed'));

// Vitest
import { vi } from 'vitest';
const callback = vi.fn();
callback.mockReturnValue(42);
```

### Module Mocks

```javascript
// Jest - mock entire module
jest.mock('./api', () => ({
    fetchData: jest.fn().mockResolvedValue({ data: 'test' }),
}));

// Vitest
vi.mock('./api', () => ({
    fetchData: vi.fn().mockResolvedValue({ data: 'test' }),
}));
```

### Partial Mocks

```javascript
// Mock only specific exports
jest.mock('./utils', () => ({
    ...jest.requireActual('./utils'),
    expensiveOperation: jest.fn().mockReturnValue('mocked'),
}));
```

### Spies

```javascript
// Spy on existing method
const spy = jest.spyOn(object, 'method');
spy.mockReturnValue('mocked');

// Restore original
spy.mockRestore();
```

### Mock Verification

```javascript
it('should call payment gateway with correct amount', () => {
    const gateway = { charge: jest.fn().mockResolvedValue({ success: true }) };
    const service = new OrderService(gateway);

    service.processPayment(100);

    expect(gateway.charge).toHaveBeenCalledWith(100);
    expect(gateway.charge).toHaveBeenCalledTimes(1);
});
```

### Clearing Mocks

```javascript
beforeEach(() => {
    jest.clearAllMocks();  // Clear call history
    // or
    jest.resetAllMocks();  // Clear + reset return values
    // or
    jest.restoreAllMocks(); // Restore original implementations
});
```

---

## Async Testing

### Async/Await

```javascript
it('should fetch user data', async () => {
    const user = await userService.getUser(123);

    expect(user.name).toBe('John');
});
```

### Promise Returns

```javascript
it('should resolve with user data', () => {
    return userService.getUser(123).then(user => {
        expect(user.name).toBe('John');
    });
});
```

### Rejections

```javascript
it('should reject when user not found', async () => {
    await expect(userService.getUser(999))
        .rejects
        .toThrow('User not found');
});
```

### Timer Mocking

```javascript
// Jest
beforeEach(() => {
    jest.useFakeTimers();
});

afterEach(() => {
    jest.useRealTimers();
});

it('should call callback after delay', () => {
    const callback = jest.fn();

    delayedCall(callback, 1000);

    expect(callback).not.toHaveBeenCalled();

    jest.advanceTimersByTime(1000);

    expect(callback).toHaveBeenCalled();
});

// Vitest
import { vi } from 'vitest';
vi.useFakeTimers();
vi.advanceTimersByTime(1000);
```

---

## Snapshot Testing

### Basic Snapshots

```javascript
it('should render correctly', () => {
    const tree = renderer.create(<Component />).toJSON();
    expect(tree).toMatchSnapshot();
});
```

### Inline Snapshots

```javascript
it('should format date correctly', () => {
    const result = formatDate(new Date('2024-01-15'));
    expect(result).toMatchInlineSnapshot(`"January 15, 2024"`);
});
```

### When to Use Snapshots

**Good for:**
- Large, stable output (HTML, JSON responses)
- Regression testing UI components
- Verifying complex object structures

**Bad for:**
- Dynamic content (timestamps, random IDs)
- Frequently changing output
- When specific assertions are clearer

### Snapshot Best Practices

```javascript
// BAD: Snapshot of entire component with dynamic data
expect(render(<UserProfile user={user} />)).toMatchSnapshot();

// GOOD: Snapshot of static structure, assert dynamic data
it('should display user name', () => {
    const { getByText } = render(<UserProfile user={user} />);
    expect(getByText(user.name)).toBeInTheDocument();
});
```

---

## React Testing Library

### Queries

```javascript
import { render, screen } from '@testing-library/react';

it('should display welcome message', () => {
    render(<Welcome name="John" />);

    // Priority order: getByRole > getByLabelText > getByText > getByTestId
    expect(screen.getByRole('heading')).toHaveTextContent('Welcome, John');
    expect(screen.getByText(/welcome/i)).toBeInTheDocument();
});
```

### Query Priority

| Query | Use When |
|-------|----------|
| `getByRole` | Interactive elements (buttons, inputs) |
| `getByLabelText` | Form fields |
| `getByPlaceholderText` | Inputs with placeholder |
| `getByText` | Non-interactive text content |
| `getByAltText` | Images |
| `getByTestId` | Last resort when others don't work |

### User Events

```javascript
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

it('should submit form on button click', async () => {
    const user = userEvent.setup();
    const onSubmit = jest.fn();

    render(<Form onSubmit={onSubmit} />);

    await user.type(screen.getByLabelText('Email'), 'test@example.com');
    await user.click(screen.getByRole('button', { name: 'Submit' }));

    expect(onSubmit).toHaveBeenCalledWith({ email: 'test@example.com' });
});
```

### Async Queries

```javascript
it('should show loading then data', async () => {
    render(<DataLoader />);

    // Initially shows loading
    expect(screen.getByText('Loading...')).toBeInTheDocument();

    // Wait for data to appear
    expect(await screen.findByText('Data loaded')).toBeInTheDocument();

    // Loading is gone
    expect(screen.queryByText('Loading...')).not.toBeInTheDocument();
});
```

### Query Variants

| Variant | Returns | Use When |
|---------|---------|----------|
| `getBy` | Element or throws | Element must exist |
| `queryBy` | Element or null | Element might not exist |
| `findBy` | Promise | Element will appear (async) |
| `getAllBy` | Array or throws | Multiple elements |

---

## Testing Hooks

```javascript
import { renderHook, act } from '@testing-library/react';

it('should increment counter', () => {
    const { result } = renderHook(() => useCounter());

    expect(result.current.count).toBe(0);

    act(() => {
        result.current.increment();
    });

    expect(result.current.count).toBe(1);
});
```

---

## Coverage Configuration

### Jest

```javascript
// jest.config.js
module.exports = {
    collectCoverage: true,
    coverageDirectory: 'coverage',
    coverageReporters: ['text', 'lcov'],
    coverageThreshold: {
        global: {
            branches: 80,
            functions: 80,
            lines: 80,
            statements: 80,
        },
    },
};
```

### Vitest

```javascript
// vitest.config.js
export default {
    test: {
        coverage: {
            provider: 'v8',
            reporter: ['text', 'json', 'html'],
            thresholds: {
                lines: 80,
                branches: 80,
            },
        },
    },
};
```

---

## Best Practices Summary

| Do | Don't |
|----|-------|
| Use `getByRole` first | Default to `getByTestId` |
| Test user behavior | Test implementation details |
| Use `async/await` for async code | Use callbacks for async |
| Clear mocks in `beforeEach` | Let mocks leak between tests |
| Use `userEvent` over `fireEvent` | Simulate events manually |
| Use inline snapshots sparingly | Snapshot everything |
| Test error states | Only test happy paths |
