# Limitations and Safety Boundaries

- Scope is publicly observable, first-party daily-use retail semantics rather
  than one benchmark journey. The local catalog is representative, not a full
  production feed, but storefront, department/category/deal discovery, search,
  Best Sellers, generic and task products, lists, history, cart, recovery, and
  responsive states are connected end to end. Pixel identity is not claimed.
- Ads, sponsored placements, tracking, telemetry, production personalization,
  challenge artwork, and third-party services are omitted. First-party related
  items and ordinary recommendation rails remain as deterministic local data.
- Account creation, production login, identity, addresses, real orders, returns,
  delivery changes, payment, Buy Now, checkout, and order placement are not
  connected. Visible controls stop at conspicuous same-origin no-effect dialogs
  and request no real account, address, password, or payment information.
- Cart, saved-for-later, list, recent-view, and search-history state are local
  SQLite records. They survive refresh and same-database restart, but never
  create a production cart, account, or order.
- `static/assets/ssd-sprite.png` is an original generated approximation rather
  than the copyrighted source product photography. It preserves catalog
  silhouette, crop, background, and visual weight, but visible material and
  image details differ. `empty-cart.png` similarly replaces source artwork.
- Anonymous source responses varied by source availability and the frozen Gate
  1 run exposed Germany/EUR despite requesting a New York baseline. The local
  fixture is normalized to `New York 10001` and `USD`; location and
  currency controls do not perform geolocation or market switching.
- The public mobile Best Sellers source shell exposed its heading and tabs but
  did not hydrate ranked cards under the GET-only capture boundary. The local
  mobile page keeps the observed shell and presents the desktop-observed public
  ranking as a one-column list. This task-completion inference is explicit.
- The ordinary product URL intermittently returned Amazon public protection
  content. Complete public desktop product HTML and the mobile `/gp/aw/d/`
  response were captured separately with JavaScript disabled. The clone neither
  reproduces nor bypasses source protection.
- The expanded public observations are a 2026-07-18 snapshot at
  `2026-07-18T06:08:28.771923+00:00`. Rankings, prices, ratings, review counts,
  copy, availability, layout, and source protection behavior may later change.
- Raw source HTML and screenshots remained in caller-owned temporary storage
  and are not committed. The repository retains only redacted machine
  observations, hashes, selected semantics, and locally authored replacements.
- Runtime resources are bundled and same-origin. The accepted verifier observed
  zero external runtime requests, zero request failures, zero page errors, zero
  unexpected HTTP errors, and zero unexpected console errors.
- Current anonymous source capture covered 100 page/viewport states and 1,700
  responses, but the homepage returned a public `202` protection state and some
  source hydration/telemetry failed under the enforced GET-only boundary.
  Source-protected or unobserved details are documented as inferred rather than
  claimed as direct observations.
- The 200-product authored catalog reuses twelve local marketplace sprite cells.
  Titles, brands, departments, categories, prices, variants, and ASINs are
  deterministic, but every synthetic product does not have unique artwork.
- Gate 3 direct-visual metrics apply only to 24 equal-size states with usable
  frozen source paint. Forty-four states are structural diagnostics and 32 are
  explicitly unavailable because the source was protected, expected-error, or
  near-uniform. Passing these gates establishes reproducible layout similarity,
  not full pixel identity or production-catalog equivalence.
- Gate 4 BrowserUse interaction comparison passed all five declared paired
  trajectories and received explicit human approval on `2026-07-18`, but it is
  a scoped behavioral sample rather than exhaustive coverage of Amazon.
- The live anonymous source resolved to Los Angeles 90060 while the clone keeps
  the frozen New York 10001/USD fixture. Changing the source region would
  require a write and was therefore not attempted. Live ranking prices also
  drifted after the frozen snapshot; the clone preserves the task fixture.
- Under the enforced GET-only boundary, the live mobile ranking route exposed
  its shell but no ranked cards. The clone mobile ranking continues to use the
  public desktop-observed order. A desktop ranking click also occasionally
  produced a transient empty PDP, so the Gate 4 runner performed one direct,
  read-only product GET retry before inspecting controls.
