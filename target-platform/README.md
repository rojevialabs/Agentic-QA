# ShopLite — AutoPilot-QA target platform

A deliberately minimal e-commerce app made of 5 independent services (separate processes, separate in-memory stores). It exists only to be tested by AutoPilot-QA. **Not production code.**

## Run

```powershell
npm install
npm run start:all                                           # all 5 services, no Docker
$env:CATALOG_BUTTON_VARIANT='v2'; npm run start:all         # seeded UI change
docker compose up --build                                   # 5 services + MongoDB
$env:CATALOG_BUTTON_VARIANT='v2'; docker compose up -d catalog-service   # redeploy catalog only
npm test
```

Always browse via `http://localhost:...` (not `127.0.0.1`). The shared session cookie `apqa_sid` is per-host.

## Services

| Service | Port | Pages | API |
|---|---|---|---|
| auth | 4001 | `/login.html`, `/register.html` | `POST /api/register`, `POST /api/login`, `GET /api/me` |
| catalog | 4002 | `/catalog.html?q=` | `GET /api/products?q=`, `GET /api/products/:id` |
| cart | 4003 | `/cart.html` | `GET /api/cart`, `POST /api/cart/add`, `DELETE /api/cart/:itemId`, `DELETE /api/cart` |
| order | 4004 | `/checkout.html` | `POST /api/orders`, `GET /api/orders`, `GET /api/orders/:id`, `POST /api/orders/:id/paid` |
| payment | 4005 | `/payment.html?orderId=&amount=` | `POST /api/payments`, `GET /api/payments/:id` |

Every service: `POST /api/__reset` restores its seed state (test-only).

Checkout flow: catalog → cart → checkout (`POST /api/orders`, status `pending_payment`, cart cleared) → payment page → `POST /api/payments` → payment-service calls `POST /api/orders/:id/paid`.

## Page elements (stable ids)

| Page | Elements |
|---|---|
| login / register | `#email`, `#password`, `#login-btn` / `#register-btn`, `#message` (`data-state=success\|error`) |
| catalog | `#search-input`, `#search-btn`, `#result-count`, `#product-list`, `.product-card[data-product-id]`, `.product-name`, `.product-price`, `.product-stock`, add button (below), `#cart-message`, `#cart-count`, `#no-results` |
| cart | `.cart-item[data-item-id]`, `.item-name`, `.item-qty`, `.item-price`, `.remove-btn`, `#cart-total`, `#empty-cart`, `#checkout-btn` |
| checkout | `#ship-name`, `#ship-address`, `#ship-city`, `#ship-zip`, `#place-order-btn`, `#checkout-total`, `#checkout-message` |
| payment | `#order-id`, `#amount`, `#card-number`, `#expiry`, `#pay-btn`, `#payment-status` (`Payment approved` / `Payment declined: <reason>`) |

### Seeded UI change — `CATALOG_BUTTON_VARIANT`

| Variant | Button (one per `.product-card`, always with `data-product-id`) |
|---|---|
| `v1` (default) | `<button id="add-to-cart-btn">Add to Cart</button>` |
| `v2` | `<button id="add-to-bag-btn">Add to Bag</button>` |

The id repeats on every card, so always scope the locator to a `.product-card`. Out-of-stock products render the button `disabled`.

## UI text

Exact visible labels and messages; there is nothing else (for example, no CVV field and no "Continue" step).

| Page | Controls (visible labels) | Messages shown |
|---|---|---|
| login | Email, Password, button 'Log in' | 'Logged in' · 'Invalid email or password' (wrong or empty credentials) |
| register | Email, Password, button 'Register' | 'Account created' · 'Please enter a valid email' · 'Password must be at least 8 characters' · 'An account with this email already exists' |
| catalog | search box and button 'Search' (matches words in product names and descriptions, not ids); per product: name, price, 'In stock' / 'Out of stock', button 'Add to Cart' (disabled when out of stock) | 'Added <product name> to cart' · 'Could not add <product name> to cart' · '<n> products' · 'No products found' |
| cart | per row: name, quantity, price, button 'Remove'; 'Total: $<amount>'; button 'Proceed to Checkout' (disabled when the cart is empty) | 'Your cart is empty' |
| checkout | 'Order total: $<amount>'; fields Full name, Address, City, Postcode; button 'Place Order' | 'Please fill in all shipping fields' · 'Your cart is empty' · on success the browser goes straight to the payment page |
| payment | 'Order <id> — amount due $<amount>'; fields Card number, Expiry (MM/YY); button 'Pay' | 'Payment approved' · 'Payment declined: card expired' · 'Payment declined: invalid card number' · 'Payment declined: invalid expiry date' |

The only way to reach checkout is: add a product in the catalog → open the cart → 'Proceed to Checkout'.

Where things live: 'Add to Cart' buttons exist **only on the catalog page**. To add a second product, open the catalog page again
first. The cart page can only list rows, remove them ('Remove'), show the total and offer 'Proceed to Checkout'; it cannot add products.

## Test data

| Kind | Values |
|---|---|
| User | `demo@shop.test` / `demo1234` |
| Products | p-100 Wireless Headphones $59.99 (12) · p-101 Mechanical Keyboard $89.50 (5) · p-102 USB-C Charger $24.00 (30) · p-103 Running Shoes $74.95 (8) · **p-104 Coffee Grinder $45.00 (0, out of stock)** · p-105 Yoga Mat $19.99 (20) |
| Cart qty | integer 1–10 per line (boundary), and ≤ stock |
| Valid card | `4111111111111111`, expiry `12/30` |
| Expired card | `4111111111111111`, expiry `01/20` → `Payment declined: card expired` |
| Card rules | 16 digits (spaces allowed), expiry `MM/YY`, valid through the end of that month |
| Shipping | name, address, city, zip: all required |
