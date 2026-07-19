# Northstar Market -- Public Product Requirements

Document version: `1.0.0`  
Protocol version: `websitebench.site.v1`  
Product family: `white-label-commerce-v1`

Northstar Market is a synthetic online shop. A candidate implementation is
judged by browser-visible behavior and persisted business state, not by its
framework, source layout, database schema, or private API design.

## 1. Product scope

The site contains 48 deterministic synthetic products in eight categories.
It supports responsive browsing, search, category filters, sorting, product
details, a persistent cart, account registration and verification, login,
password reset, simulated checkout, order history, inventory, and order
cancellation.

The following are intentionally out of scope: real payment processing, real
email delivery, shipping integrations, refunds, returns, coupons, product
reviews, seller/admin experiences, and anonymous checkout.

## 2. Public pages and navigation

The canonical browser paths are:

| Path | Observable purpose |
| --- | --- |
| `/` | Home page, category navigation, featured products |
| `/search` | Search results, filters, sorting, pagination |
| `/products/{slug}` | Product detail and add-to-cart controls |
| `/cart` | Cart contents, quantity controls, totals, checkout entry |
| `/register` | Registration form and verification-sent state |
| `/verify?token=...` | One-time email verification |
| `/login` | Login form; accepts a `next` query parameter |
| `/forgot-password` | Password-reset request form |
| `/reset-password?token=...` | One-time password reset form |
| `/checkout` | Address, shipping, and test-payment form |
| `/checkout/success/{order_number}` | Order success summary |
| `/account/orders` | Signed-in user's order history |
| `/account/orders/{order_number}` | Signed-in user's order detail |

The header is present on all normal pages and provides a home link, product
search, account entry, and cart entry. Desktop and mobile layouts may differ,
but all functionality remains reachable by keyboard and through accessible
names. The primary responsive breakpoints are 390 x 844 and 1440 x 1000.

## 3. Catalog, search, and product details

- Product titles, descriptions, brands, categories, prices, images, rating
  summaries, and inventory are provided by the active fixture.
- Search is case-insensitive and matches product title, brand, description,
  category name, and tags.
- Empty search displays all products. No-match search displays an explicit
  empty state and a way to clear the search.
- A category filter can be combined with search.
- Sort choices are `Featured`, `Price: Low to High`, `Price: High to Low`, and
  `Customer Rating`.
- Results paginate at 12 products per page. Changing search, filter, or sort
  returns to page 1. Invalid page numbers resolve to the nearest valid page.
- The URL query string represents the active search state so refresh and Back
  preserve it.
- A product with zero inventory is marked `Out of stock` and cannot be added.
- Product money is rendered from integer cents with two decimal places.

## 4. Cart

- Anonymous visitors may add products to a server-persisted guest cart.
- A stable first-party device cookie identifies the guest cart. Refreshing,
  navigating, or reopening the site with the same cookie preserves it.
- Quantity for one product is limited by both current inventory and a hard cap
  of 5 units.
- Quantity controls provide increment, decrement, direct quantity selection,
  and remove. Setting quantity to zero removes the line.
- Adding the same product again increases its quantity up to the applicable
  cap and displays feedback if the requested amount cannot be added.
- Cart subtotal is the sum of current unit price times quantity. A cart does
  not reserve stock.
- On login, guest and account carts merge by product. Quantities are summed,
  then capped at `min(current inventory, 5)`. The guest cart is cleared after
  a successful merge. This operation is safe to retry and must not merge twice.
- Logging out does not delete the account cart.
- Starting checkout while signed out redirects to `/login` and returns to
  checkout after successful login.

## 5. Registration and email verification

Email addresses are normalized by trimming surrounding whitespace and applying
Unicode-aware lowercase before uniqueness and rate-limit checks.

The registration form requires:

- a syntactically valid email address;
- a password of at least 10 characters containing an uppercase letter, a
  lowercase letter, and a digit;
- a matching password confirmation.

Invalid client input does not consume the registration rate limit. An accepted
registration request creates or refreshes an unverified account, sends a link
to the local test mailbox, and shows a verification-sent state. It starts a
five-minute rate-limit window for both the normalized email and the current
device. During that window, another otherwise valid registration request that
matches either key is rejected with HTTP 429 semantics and the visible message
`Please wait before trying to register again.`

The registration window uses the benchmark's controlled clock. At exactly five
minutes after the accepted request, a new request is allowed.

Verification links:

- expire 30 minutes after issue;
- can be used once;
- verify only their associated account;
- are valid while `now <= expires_at` and invalid after that instant;
- display an explicit invalid/expired/already-used state when rejected.

An existing verified email is never replaced by registration. The response
must not expose whether a verified account exists.

## 6. Login, sessions, and logout

- Only verified accounts can log in.
- Invalid email/password and unknown-account attempts show the same generic
  error: `Email or password is incorrect.`
- An unverified account shows `Verify your email before signing in.` and a link
  back to registration.
- A successful login creates a server-side session, merges the guest cart, and
  redirects to the safe same-origin `next` path or home.
- Sessions expire 24 hours after creation using the controlled clock. They are
  valid while `now <= expires_at`.
- Refresh and normal server restart preserve non-expired sessions.
- Logout invalidates the current session and returns to home.
- External, protocol-relative, or malformed `next` values must not produce an
  open redirect.

## 7. Forgot and reset password

Submitting `/forgot-password` always shows the same generic success message:
`If an account exists, a reset link has been sent.` This prevents account
enumeration. A verified matching account receives a link in the local test
mailbox.

Reset links:

- expire 60 minutes after issue;
- can be used once;
- are valid while `now <= expires_at`;
- enforce the same password policy as registration;
- invalidate all existing sessions for that user after a successful reset.

Reusing, tampering with, or opening an expired link displays an explicit
invalid/expired/already-used state and does not change the password.

## 8. Checkout

Checkout requires a verified, signed-in user and a non-empty cart. It collects:

- full name;
- address line 1 and optional address line 2;
- city, two-letter US state, and five-digit ZIP code;
- shipping method;
- test card number, expiration, and CVV.

Shipping options are:

- `Standard` -- $5.99, or free when merchandise subtotal is at least $75.00;
- `Express` -- $14.99.

Tax is 8.25% of merchandise subtotal, rounded to the nearest cent using
half-up rounding. Shipping is not taxed. Every monetary calculation uses
integer cents.

The local test-payment values are:

- `4242 4242 4242 4242` -- success;
- `4000 0000 0000 0002` -- decline with `Your test payment was declined.`

Any other card number is invalid. Expiration must be a non-expired `MM/YY`
value relative to the controlled clock; CVV must contain three digits. The
application must never persist a full card number or CVV. It may persist only
the successful test card's last four digits.

The final place-order action is idempotent. Retrying the same checkout request
with the same idempotency key returns the original order and never charges,
decrements inventory, or clears a cart twice.

Order placement is one atomic transaction:

1. re-read every cart line and product inventory;
2. if any requested quantity is unavailable, create no order, decrement no
   inventory, preserve the cart, and identify affected lines to the user;
3. if payment is declined, create no order, decrement no inventory, and
   preserve the cart;
4. on success, create one immutable order snapshot, decrement all inventory,
   and clear the purchased account cart.

## 9. Orders and cancellation

- Order history and details persist across refresh, login, and service restart.
- Users can access only their own orders. A missing order and another user's
  order both render the same not-found response with HTTP 404 semantics.
- Cross-account order access therefore never reveals whether the order exists.
- A placed order has status `Placed`.
- It may be cancelled while `now <= placed_at + 30 minutes`.
- Successful cancellation changes status to `Cancelled`, records the
  cancellation time, and atomically restores all inventory exactly once.
- Repeating cancellation is safe and does not restock again.
- After the 30-minute boundary, cancellation is rejected with
  `The cancellation window has closed.` and neither order nor inventory changes.

## 10. Controlled time

Reference and candidate environments receive an initial UTC timestamp through
the reset contract. All session, registration, verification, reset, payment
expiration, order, and cancellation decisions use that controlled clock rather
than wall-clock time. The evaluator advances it through the private benchmark
admin port. Browser UI must never expose the admin token or admin controls.

## 11. Local test mailbox

Email is delivered only to the benchmark-provided local mailbox service. The
task supplies a browser-visible mailbox URL. Verification and reset messages
contain same-origin links to the active site. No external SMTP or email API is
permitted or required.

## 12. Accessibility and interaction feedback

- Interactive controls have visible labels or accessible names.
- Forms associate errors with their fields and preserve non-secret input after
  validation errors.
- Keyboard focus is visible. Dialogs, if used, trap focus and close with Escape.
- Loading actions disable duplicate submission and expose a busy state.
- Success, empty, and error states are textual, not color-only.
- Product images have meaningful alternative text.

## 13. Persistence and security boundaries

Users, carts, sessions, tokens, orders, idempotency records, and inventory are
stored by a real persistent backend under `/data`. A process restart must not
reset them. Only the authenticated benchmark reset endpoint may rebuild state.
Passwords are stored with a salted adaptive password hash. Tokens are stored as
non-reversible digests. Secrets and payment data are excluded from the
normalized benchmark state endpoint.
