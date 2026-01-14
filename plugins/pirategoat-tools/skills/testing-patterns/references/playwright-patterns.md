# Playwright E2E Patterns

End-to-end testing patterns with Playwright, including WordPress and WooCommerce specific patterns.

## Quick Reference: Locators

| Locator | Priority | Example |
|---------|----------|---------|
| `getByRole` | Best | `page.getByRole('button', { name: 'Submit' })` |
| `getByLabel` | Good | `page.getByLabel('Email')` |
| `getByPlaceholder` | Good | `page.getByPlaceholder('Search...')` |
| `getByText` | Good | `page.getByText('Welcome')` |
| `getByTestId` | Acceptable | `page.getByTestId('submit-btn')` |
| CSS selector | Last resort | `page.locator('.my-class')` |
| XPath | Avoid | `page.locator('//button')` |

---

## Basic Test Structure

```typescript
import { test, expect } from '@playwright/test';

test.describe('Checkout Flow', () => {
    test.beforeEach(async ({ page }) => {
        await page.goto('/shop');
    });

    test('should complete checkout as guest', async ({ page }) => {
        // Add product to cart
        await page.getByRole('button', { name: 'Add to cart' }).first().click();

        // Go to checkout
        await page.getByRole('link', { name: 'Checkout' }).click();

        // Fill form
        await page.getByLabel('Email').fill('test@example.com');

        // Place order
        await page.getByRole('button', { name: 'Place order' }).click();

        // Verify success
        await expect(page.getByText('Order confirmed')).toBeVisible();
    });
});
```

---

## Page Object Model

Encapsulate page-specific logic in reusable classes.

### Page Object Structure

```typescript
// pages/checkout.page.ts
import { Page, Locator, expect } from '@playwright/test';

export class CheckoutPage {
    readonly page: Page;
    readonly emailInput: Locator;
    readonly firstNameInput: Locator;
    readonly lastNameInput: Locator;
    readonly placeOrderButton: Locator;

    constructor(page: Page) {
        this.page = page;
        this.emailInput = page.getByLabel('Email');
        this.firstNameInput = page.getByLabel('First name');
        this.lastNameInput = page.getByLabel('Last name');
        this.placeOrderButton = page.getByRole('button', { name: 'Place order' });
    }

    async goto() {
        await this.page.goto('/checkout');
    }

    async fillBillingDetails(details: BillingDetails) {
        await this.emailInput.fill(details.email);
        await this.firstNameInput.fill(details.firstName);
        await this.lastNameInput.fill(details.lastName);
    }

    async placeOrder() {
        await this.placeOrderButton.click();
    }

    async expectOrderConfirmation() {
        await expect(this.page.getByText('Order confirmed')).toBeVisible();
    }
}
```

### Using Page Objects

```typescript
import { test } from '@playwright/test';
import { CheckoutPage } from './pages/checkout.page';

test('should complete checkout', async ({ page }) => {
    const checkout = new CheckoutPage(page);

    await checkout.goto();
    await checkout.fillBillingDetails({
        email: 'test@example.com',
        firstName: 'John',
        lastName: 'Doe',
    });
    await checkout.placeOrder();
    await checkout.expectOrderConfirmation();
});
```

---

## Selectors

### Role-Based Selectors (Preferred)

```typescript
// Buttons
page.getByRole('button', { name: 'Submit' });
page.getByRole('button', { name: /submit/i });  // Case insensitive

// Links
page.getByRole('link', { name: 'Products' });

// Form inputs
page.getByRole('textbox', { name: 'Email' });
page.getByRole('checkbox', { name: 'Remember me' });
page.getByRole('combobox', { name: 'Country' });

// Headings
page.getByRole('heading', { name: 'Welcome', level: 1 });

// Other elements
page.getByRole('listitem');
page.getByRole('row');
page.getByRole('cell');
```

### Label-Based Selectors

```typescript
// Input by label
page.getByLabel('Email address');

// Input by placeholder
page.getByPlaceholder('Enter your email');
```

### Text-Based Selectors

```typescript
// Exact text
page.getByText('Welcome');

// Partial text
page.getByText('Welcome', { exact: false });

// Regex
page.getByText(/order #\d+/i);
```

### Test ID Selectors

When semantic selectors don't work, add `data-testid` attributes.

```html
<button data-testid="submit-order">Place Order</button>
```

```typescript
page.getByTestId('submit-order');
```

### CSS Selectors (Last Resort)

```typescript
// Class
page.locator('.product-card');

// ID
page.locator('#main-content');

// Attribute
page.locator('[data-product-id="123"]');

// Combining
page.locator('.cart-item:has-text("Product Name")');
```

---

## Waiting Strategies

### Auto-Waiting (Default)

Playwright auto-waits for elements to be actionable.

```typescript
// Automatically waits for button to be visible and enabled
await page.getByRole('button', { name: 'Submit' }).click();
```

### Explicit Waits

```typescript
// Wait for element to appear
await page.getByText('Loading complete').waitFor();

// Wait for element to disappear
await page.getByText('Loading...').waitFor({ state: 'hidden' });

// Wait for specific state
await page.getByRole('button').waitFor({ state: 'attached' });
```

### Network Waiting

```typescript
// Wait for navigation
await Promise.all([
    page.waitForNavigation(),
    page.getByRole('link', { name: 'Products' }).click(),
]);

// Wait for specific request
await Promise.all([
    page.waitForResponse(response =>
        response.url().includes('/api/orders') &&
        response.status() === 200
    ),
    page.getByRole('button', { name: 'Place order' }).click(),
]);

// Wait for network idle
await page.waitForLoadState('networkidle');
```

### Custom Waiting

```typescript
// Wait for condition
await expect(async () => {
    const count = await page.getByRole('listitem').count();
    expect(count).toBeGreaterThan(0);
}).toPass({ timeout: 5000 });
```

---

## Network Handling

### Intercepting Requests

```typescript
// Mock API response
await page.route('**/api/products', route => {
    route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
            { id: 1, name: 'Product 1', price: 100 },
            { id: 2, name: 'Product 2', price: 200 },
        ]),
    });
});
```

### Modifying Requests

```typescript
// Add headers
await page.route('**/api/**', route => {
    route.continue({
        headers: {
            ...route.request().headers(),
            'X-Test-Header': 'value',
        },
    });
});
```

### Blocking Requests

```typescript
// Block analytics
await page.route('**google-analytics.com**', route => route.abort());
await page.route('**facebook.com**', route => route.abort());
```

### Recording Requests

```typescript
const requests: Request[] = [];
page.on('request', request => {
    if (request.url().includes('/api/')) {
        requests.push(request);
    }
});

// After actions...
expect(requests.some(r => r.url().includes('/api/orders'))).toBe(true);
```

---

## WordPress/WooCommerce Patterns

### Admin Login

```typescript
export async function loginAsAdmin(page: Page) {
    await page.goto('/wp-admin');

    // Check if already logged in
    if (await page.getByRole('link', { name: 'Dashboard' }).isVisible()) {
        return;
    }

    await page.getByLabel('Username or Email Address').fill('admin');
    await page.getByLabel('Password').fill('password');
    await page.getByRole('button', { name: 'Log In' }).click();

    await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible();
}
```

### Product Helpers

```typescript
export async function addProductToCart(page: Page, productName: string) {
    await page.goto('/shop');

    const product = page.locator('.product').filter({ hasText: productName });
    await product.getByRole('button', { name: 'Add to cart' }).click();

    // Wait for cart update
    await expect(page.getByText('has been added to your cart')).toBeVisible();
}
```

### Checkout Helpers

```typescript
export async function fillBillingAddress(page: Page, address: BillingAddress) {
    await page.getByLabel('First name').fill(address.firstName);
    await page.getByLabel('Last name').fill(address.lastName);
    await page.getByLabel('Street address').first().fill(address.street);
    await page.getByLabel('Town / City').fill(address.city);
    await page.getByLabel('Postcode').fill(address.postcode);
    await page.getByLabel('Phone').fill(address.phone);
    await page.getByLabel('Email').fill(address.email);
}

export async function selectPaymentMethod(page: Page, method: string) {
    await page.getByLabel(method).check();
}
```

### Common Selectors

```typescript
// WooCommerce specific selectors
const selectors = {
    // Cart
    cartTotal: '.cart-contents .amount',
    cartCount: '.cart-contents .count',
    removeFromCart: '.remove_from_cart_button',

    // Checkout
    orderReview: '#order_review',
    placeOrderButton: '#place_order',
    paymentMethods: '.wc_payment_methods',

    // My Account
    myAccountNav: '.woocommerce-MyAccount-navigation',
    ordersTable: '.woocommerce-orders-table',

    // Products
    productCard: '.product',
    addToCartButton: '.add_to_cart_button',
    productPrice: '.price',
};
```

---

## Test Configuration

### playwright.config.ts

```typescript
import { defineConfig } from '@playwright/test';

export default defineConfig({
    testDir: './tests/e2e',
    timeout: 30000,
    retries: 2,
    workers: 4,

    use: {
        baseURL: 'http://localhost:8080',
        trace: 'on-first-retry',
        screenshot: 'only-on-failure',
        video: 'retain-on-failure',
    },

    projects: [
        {
            name: 'chromium',
            use: { browserName: 'chromium' },
        },
        {
            name: 'firefox',
            use: { browserName: 'firefox' },
        },
    ],
});
```

### Fixtures

```typescript
import { test as base } from '@playwright/test';
import { CheckoutPage } from './pages/checkout.page';

type Fixtures = {
    checkoutPage: CheckoutPage;
    loggedInPage: Page;
};

export const test = base.extend<Fixtures>({
    checkoutPage: async ({ page }, use) => {
        const checkoutPage = new CheckoutPage(page);
        await use(checkoutPage);
    },

    loggedInPage: async ({ page }, use) => {
        await loginAsAdmin(page);
        await use(page);
    },
});
```

---

## Best Practices Summary

| Do | Don't |
|----|-------|
| Use role-based selectors | Use CSS/XPath selectors |
| Use Page Object Model | Put selectors in tests |
| Let Playwright auto-wait | Add arbitrary sleeps |
| Test user journeys | Test every click |
| Mock flaky external services | Depend on third-party APIs |
| Use meaningful test names | Name tests `test1`, `test2` |
| Handle authentication in fixtures | Log in every test |
| Use `data-testid` as fallback | Make it first choice |
