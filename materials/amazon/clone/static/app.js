(() => {
  'use strict';

  const BEST_SELLERS_PATH = '/Best-Sellers-External-Solid-State-Drives/zgbs/pc/3015429011';
  const BEST_SELLERS_ROOT = '/Best-Sellers/zgbs';
  const PRODUCT_PATH = '/SAMSUNG-Portable-SSD-1TB-MU-PC1T0T/dp/B0874XN4D8';
  const MOBILE_PRODUCT_PATH = '/gp/aw/d/B0874XN4D8';
  const CART_PATH = '/gp/cart/view.html';
  const TARGET_ASIN = 'B0874XN4D8';

  const fallbackProducts = [
    {
      rank: 1,
      asin: 'B08HN37XC1',
      title: 'SANDISK 2TB Extreme Portable SSD (Old Model) - Up to 1050MB/s, USB-C, USB 3.2 Gen 2, IP65 Water and Dust Resistance',
      short_title: 'SANDISK 2TB Extreme Portable SSD',
      brand: 'SanDisk',
      department: 'Computers',
      category: 'Data Storage',
      rating: 4.6,
      reviews: 91118,
      price: 269.75,
      old_price: 299.99,
      bought: '10K+ bought in past month',
      capacity: '2 TB',
      color: 'Black',
      interface: 'USB 3.2 Gen 2',
      connectivity: 'USB-C',
      bullets: ['Up to 1050MB/s', 'IP65 water and dust resistance'],
      sprite_index: 0,
    },
    {
      rank: 2,
      asin: TARGET_ASIN,
      title: 'Samsung T7 Portable SSD, 1TB External Solid State Drive, Speeds Up to 1,050MB/s, USB 3.2 Gen 2, Reliable Storage for Gaming, Students, Professionals, MU-PC1T0T/AM, Gray',
      short_title: 'Samsung T7 Portable SSD, 1TB External Solid State Drive',
      brand: 'Samsung',
      department: 'Computers',
      category: 'Data Storage',
      rating: 4.7,
      reviews: 38068,
      price: 219.99,
      old_price: 274.99,
      bought: '5K+ bought in past month',
      capacity: '1 TB',
      color: 'Titan Gray',
      interface: 'USB 3.0',
      connectivity: 'USB',
      bullets: [
        'MADE FOR THE MAKERS: Create, explore, and store with fast, durable portable storage.',
        'SHARE IDEAS IN A FLASH: PCIe NVMe technology supports read and write speeds up to 1,050/1,000 MB/s.',
        'ALWAYS MAKE THE SAVE: Compact design with capacity for working files, photographs, and game data.',
        'ADAPTS TO EVERY NEED: Broad compatibility across computers, phones, cameras, and consoles.',
        'HI RESOLUTION VIDEO RECORDING: Record high-resolution video directly to portable storage on supported devices.',
      ],
      sprite_index: 1,
    },
    {
      rank: 3,
      asin: 'B0CHFSWM2P',
      title: 'Samsung T9 Portable SSD 1TB, USB 3.2 Gen 2x2 External Solid State Drive, up to 2,000MB/s',
      short_title: 'Samsung T9 Portable SSD 1TB',
      brand: 'Samsung',
      department: 'Computers',
      category: 'Data Storage',
      rating: 4.6,
      reviews: 2888,
      price: 249,
      old_price: 289.99,
      bought: '2K+ bought in past month',
      capacity: '1 TB',
      color: 'Black',
      interface: 'USB 3.2 Gen 2x2',
      connectivity: 'USB-C',
      bullets: ['Up to 2,000MB/s', 'Dynamic thermal guard'],
      sprite_index: 2,
    },
    {
      rank: 4,
      asin: 'B0C5JQ68FY',
      title: 'SANDISK 1TB Portable SSD - Up to 800MB/s, USB-C, USB 3.2 Gen 2',
      short_title: 'SANDISK 1TB Portable SSD',
      brand: 'SanDisk',
      department: 'Computers',
      category: 'Data Storage',
      rating: 4.6,
      reviews: 13477,
      price: 139.77,
      old_price: 159.99,
      bought: '3K+ bought in past month',
      capacity: '1 TB',
      color: 'Black',
      interface: 'USB 3.2 Gen 2',
      connectivity: 'USB-C',
      bullets: ['Up to 800MB/s', 'Compact portable design'],
      sprite_index: 3,
    },
    {
      rank: 5,
      asin: 'B0BGKXX9TK',
      title: 'SSK Portable SSD 500GB External Solid State Drive, up to 1050MB/s USB-C',
      short_title: 'SSK Portable SSD 500GB',
      brand: 'SSK',
      department: 'Computers',
      category: 'Data Storage',
      rating: 4.5,
      reviews: 4560,
      price: 78.62,
      old_price: 89.99,
      bought: '1K+ bought in past month',
      capacity: '500 GB',
      color: 'Black',
      interface: 'USB 3.2 Gen 2',
      connectivity: 'USB-C',
      bullets: ['Up to 1050MB/s', 'Phone and computer compatible'],
      sprite_index: 4,
    },
    {
      rank: 6,
      asin: 'B08GV9M64L',
      title: 'SANDISK 1TB Extreme PRO Portable SSD - Up to 2000MB/s, USB-C, IP65',
      short_title: 'SANDISK 1TB Extreme PRO Portable SSD',
      brand: 'SanDisk',
      department: 'Computers',
      category: 'Data Storage',
      rating: 4.5,
      reviews: 9874,
      price: 183.45,
      old_price: 229.99,
      bought: '1K+ bought in past month',
      capacity: '1 TB',
      color: 'Black',
      interface: 'USB 3.2 Gen 2x2',
      connectivity: 'USB-C',
      bullets: ['Up to 2000MB/s', 'Forged aluminum chassis'],
      sprite_index: 5,
    },
  ];

  const fallbackData = {
    session: { delivery_label: 'New York 10001', currency: 'USD', signed_in: false },
    products: fallbackProducts,
    cart: { items: [], total_quantity: 0, subtotal: 0 },
    discovery: { best_sellers_viewed: false, product_views: [] },
    saved_for_later: [],
    wishlist: [],
    recent_views: [],
    search_history: [],
  };

  const state = {
    ...fallbackData,
    siteCatalog: { departments: [], trendingSearches: [], homeModules: [], bestSellerRails: [], products: [] },
    localCart: readLocalCart(),
    searchUi: null,
    apiError: '',
    usingFallback: true,
    busy: false,
    toastTimer: 0,
  };

  const header = document.getElementById('site-header');
  const main = document.getElementById('main-content');
  const footer = document.getElementById('site-footer');
  const toast = document.getElementById('toast');
  const boundaryDialog = document.getElementById('boundary-dialog');
  const menuDialog = document.getElementById('menu-dialog');
  const filterDialog = document.getElementById('filter-dialog');

  function escapeHtml(value) {
    return String(value ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#039;');
  }

  function safeNumber(value, fallback = 0) {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
  }

  function readLocalCart() {
    try {
      const parsed = JSON.parse(window.localStorage.getItem('amazon-local-cart') || '{}');
      return parsed && typeof parsed === 'object' ? parsed : {};
    } catch {
      return {};
    }
  }

  function writeLocalCart() {
    window.localStorage.setItem('amazon-local-cart', JSON.stringify(state.localCart));
  }

  function priceParts(value) {
    return safeNumber(value).toFixed(2).split('.');
  }

  function priceMarkup(value, className = 'price') {
    const [whole, cents] = priceParts(value);
    return `<span class='${className}'><span aria-hidden='true'>$</span>${whole}<sup>${cents}</sup><span class='sr-only'> dollars</span></span>`;
  }

  function money(value) {
    return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(safeNumber(value));
  }

  function reviewCount(value) {
    return safeNumber(value).toLocaleString('en-US');
  }

  function catalogProducts() {
    return state.siteCatalog.products.map((product) => ({ ...product, source: 'marketplace' }));
  }

  function ssdProducts() {
    return state.products.map((product) => ({ ...product, source: 'ssd' }));
  }

  function allProducts() {
    const merged = new Map();
    [...catalogProducts(), ...ssdProducts()].forEach((product) => merged.set(product.asin, product));
    return [...merged.values()];
  }

  function productByAsin(asin) {
    return allProducts().find((product) => product.asin === asin);
  }

  function slugify(value) {
    return String(value || 'Amazon-product')
      .replace(/[^a-zA-Z0-9]+/g, '-')
      .replace(/^-|-$/g, '')
      .slice(0, 72) || 'Amazon-product';
  }

  function productHref(product) {
    if (product.asin === TARGET_ASIN) return PRODUCT_PATH;
    return `/${escapeHtml(product.slug || slugify(product.short_title || product.title))}/dp/${encodeURIComponent(product.asin)}`;
  }

  function productImageMarkup(product, className = '', label = '') {
    const alt = escapeHtml(label || product.short_title || product.title);
    if (product.source === 'marketplace') {
      const index = Math.min(11, Math.max(0, safeNumber(product.sprite_index)));
      return `<div class='marketplace-image marketplace-${index} ${className}' role='img' aria-label='${alt}'></div>`;
    }
    const index = Math.min(5, Math.max(0, safeNumber(product.sprite_index)));
    return `<div class='sprite-image sprite-${index} ${className}' role='img' aria-label='${alt}'></div>`;
  }

  function spriteMarkup(product, className, label) {
    return productImageMarkup({ ...product, source: 'ssd' }, className, label);
  }

  function ratingMarkup(product) {
    return `
      <div class='rating-line' aria-label='${safeNumber(product.rating).toFixed(1)} out of 5 stars, ${reviewCount(product.reviews)} ratings'>
        <span class='rating-value'>${safeNumber(product.rating).toFixed(1)}</span>
        <span class='stars' aria-hidden='true'>★★★★★</span>
        <a class='review-count' href='${productHref(product)}#reviews'>(${reviewCount(product.reviews)})</a>
      </div>`;
  }

  function createIcons() {
    if (window.lucide && typeof window.lucide.createIcons === 'function') {
      window.lucide.createIcons({ attrs: { 'stroke-width': 2 } });
    }
  }

  function currentQuery() {
    return new URLSearchParams(window.location.search).get('k') || '';
  }

  function currentDepartment() {
    return new URLSearchParams(window.location.search).get('i') || 'all';
  }

  function localCartQuantity() {
    return Object.values(state.localCart).reduce((total, quantity) => total + safeNumber(quantity), 0);
  }

  function departmentOptions(selected) {
    const options = [['all', 'All'], ...(state.siteCatalog.departments || []).map((department) => [department.slug || slugify(department.name).toLowerCase(), department.name])];
    return options.map(([value, label]) => `<option value='${value}'${selected === value ? ' selected' : ''}>${label}</option>`).join('');
  }

  function searchFormMarkup(kind, query, department) {
    const mobile = kind === 'mobile';
    return `
      <form class='${mobile ? 'mobile-search' : 'nav-search'}' action='/s' method='get' role='search' data-search-form>
        ${mobile ? '' : `<label class='sr-only' for='desktop-department'>Search in</label><select id='desktop-department' name='i' aria-label='Choose a department'>${departmentOptions(department)}</select>`}
        <label class='sr-only' for='${kind}-search'>Search Amazon</label>
        <input id='${kind}-search' name='k' value='${escapeHtml(query)}' type='search' maxlength='160' required placeholder='${mobile && window.location.pathname.startsWith('/gp/goldbox') ? 'Search in Deals' : 'Search Amazon'}' autocomplete='off' aria-autocomplete='list' aria-expanded='false'>
        ${mobile ? `<input type='hidden' name='i' value='${escapeHtml(department)}'>` : ''}
        <button type='submit' title='Search' aria-label='Search'><i data-lucide='search' aria-hidden='true'></i></button>
        <div class='autocomplete-panel' role='listbox' aria-label='Search suggestions'></div>
      </form>`;
  }

  function renderHeader() {
    const count = Math.max(0, safeNumber(state.cart?.total_quantity)) + localCartQuantity();
    const delivery = escapeHtml(state.session?.delivery_label || 'New York 10001');
    const query = currentQuery();
    const department = currentDepartment();
    const path = window.location.pathname;
    const mobileDelivery = `Delivering to ${delivery} - Update location`;
    const navLinks = [
      ['/s?k=health&i=all', 'Health AI'],
      ['/s?k=amazon+basics&i=all', 'Amazon Basics'],
      [BEST_SELLERS_ROOT, 'Best Sellers'],
      ['/gp/goldbox/', "Today's Deals"],
      ['/s?k=new+releases&i=all', 'New Releases'],
      ['/s?k=books&i=books', 'Books'],
      ['/s?k=groceries&i=all', 'Groceries'],
      ['/hz/wishlist/ls', 'Gift Cards'],
      ['/account?view=sell', 'Sell'],
      ['/s?k=fashion&i=fashion', 'Fashion'],
    ];
    header.innerHTML = `
      <div class='desktop-nav'>
        <div class='nav-belt'>
          <a class='amazon-logo nav-box' href='/' aria-label='Amazon home'>amazon</a>
          <a class='nav-box nav-location' href='/local-boundary?kind=delivery' data-preference='delivery'>
            <i data-lucide='map-pin' aria-hidden='true'></i>
            <span><span class='nav-line-1'>Delivering to ${delivery}</span><span class='nav-line-2'>Update location</span></span>
          </a>
          ${searchFormMarkup('desktop', query, department)}
          <a class='nav-box nav-language' href='/local-boundary?kind=language' data-preference='language' title='Choose language'><span class='flag-us' aria-hidden='true'></span> EN</a>
          <div class='account-wrap'>
            <a class='nav-box account-trigger' href='/account'><span><span class='nav-line-1'>Hello, sign in</span><span class='nav-line-2'>Account &amp; Lists <span aria-hidden='true'>▾</span></span></span></a>
            <div class='signin-flyout' aria-label='Account and lists'>
              <a class='amazon-button amazon-button-primary' href='/account'>Sign in</a>
              <p>New customer? <a href='/account?mode=register'>Start here.</a></p>
              <div class='flyout-columns'>
                <div><strong>Your Lists</strong><a href='/hz/wishlist/ls'>Create a List</a><a href='/hz/wishlist/ls'>Find a List or Registry</a></div>
                <div><strong>Your Account</strong><a href='/account'>Account</a><a href='/account/orders'>Orders</a><a href='/hz/wishlist/ls'>Recommendations</a></div>
              </div>
            </div>
          </div>
          <a class='nav-box' href='/account/orders'><span><span class='nav-line-1'>Returns</span><span class='nav-line-2'>&amp; Orders</span></span></a>
          <a class='nav-box nav-cart' href='${CART_PATH}' aria-label='Cart with ${count} items'>
            <i data-lucide='shopping-cart' aria-hidden='true'></i><span class='cart-count'>${count}</span><span>Cart</span>
          </a>
        </div>
        <nav class='nav-main' aria-label='Primary navigation'>
          <button class='all-menu' type='button' data-open-menu><i data-lucide='menu' aria-hidden='true'></i> All</button>
          ${navLinks.map(([href, label]) => `<a href='${href}'${path === href ? " aria-current='page'" : ''}>${label}</a>`).join('')}
        </nav>
      </div>
      <div class='mobile-nav'>
        <div class='mobile-top'>
          <button class='icon-button' type='button' data-open-menu title='Open menu' aria-label='Open menu'><i data-lucide='menu' aria-hidden='true'></i></button>
          <a class='amazon-logo' href='/' aria-label='Amazon home'>amazon</a>
          <a class='mobile-signin' href='/account'>Sign in ›</a>
          <a class='icon-button' href='/account' title='Account' aria-label='Account'><i data-lucide='user-round' aria-hidden='true'></i></a>
          <a class='mobile-cart-link' href='${CART_PATH}' title='Cart' aria-label='Cart with ${count} items'><i data-lucide='shopping-cart' aria-hidden='true'></i><span class='cart-count'>${count}</span></a>
        </div>
        ${searchFormMarkup('mobile', query, department)}
        <a class='mobile-location' href='/local-boundary?kind=delivery' data-preference='delivery'><i data-lucide='map-pin' aria-hidden='true'></i> ${mobileDelivery} <span aria-hidden='true'>⌄</span></a>
      </div>`;
    createIcons();
  }

  function renderDrawer() {
    const departments = state.siteCatalog.departments.length ? state.siteCatalog.departments : [
      { name: 'Electronics', href: '/s?k=electronics&i=electronics', children: ['Computers & Accessories', 'Headphones'] },
      { name: 'Home, Garden & Tools', href: '/s?k=home&i=home', children: ['Kitchen & Dining', 'Home decor'] },
    ];
    menuDialog.innerHTML = `
      <div class='menu-heading'>
        <h2 id='menu-title'><i data-lucide='circle-user-round' aria-hidden='true'></i> Hello, sign in</h2>
        <button class='icon-button menu-close' type='button' data-close-menu title='Close menu' aria-label='Close menu'><i data-lucide='x' aria-hidden='true'></i></button>
      </div>
      <nav class='drawer-nav' aria-label='All departments'>
        <section><h3>Trending</h3><a href='/Best-Sellers/zgbs'>Best Sellers</a><a href='/gp/goldbox/'>Today's Deals</a><a href='/s?k=new+releases'>New Releases</a></section>
        <section><h3>Shop by Department</h3>
          ${departments.map((department) => `
            <details>
              <summary><a href='${department.href}'>${escapeHtml(department.name)}</a><i data-lucide='chevron-right' aria-hidden='true'></i></summary>
              <div class='drawer-children'>${department.children.map((child) => `<a href='/s?k=${encodeURIComponent(child)}&i=all'>${escapeHtml(child)}</a>`).join('')}</div>
            </details>`).join('')}
          <a href='/s?k=all+departments'>See all</a>
        </section>
        <section><h3>Help &amp; Settings</h3><a href='/account'>Your Account</a><a href='/local-boundary?kind=language'>English</a><a href='/account?view=help'>Customer Service</a></section>
      </nav>`;
    createIcons();
  }

  function renderFooter() {
    footer.innerHTML = `
      <button class='back-to-top' type='button' data-back-to-top>Back to top</button>
      <div class='footer-links'>
        <section class='footer-column'><h2>Get to Know Us</h2><a href='/account?view=careers'>Careers</a><a href='/account?view=newsletter'>Amazon Newsletter</a><a href='/account?view=about'>About Amazon</a><a href='/account?view=accessibility'>Accessibility</a></section>
        <section class='footer-column'><h2>Make Money with Us</h2><a href='/account?view=sell'>Sell on Amazon</a><a href='/account?view=supply'>Supply to Amazon</a><a href='/account?view=affiliate'>Become an Affiliate</a><a href='/account?view=advertise'>Advertise Your Products</a></section>
        <section class='footer-column'><h2>Amazon Payment Products</h2><a href='/checkout/payment'>Amazon Visa</a><a href='/checkout/payment'>Amazon Store Card</a><a href='/checkout/payment'>Shop with Points</a><a href='/hz/wishlist/ls'>Gift Cards</a></section>
        <section class='footer-column'><h2>Let Us Help You</h2><a href='/account'>Your Account</a><a href='/account/orders'>Your Orders</a><a href='/account?view=shipping'>Shipping Rates &amp; Policies</a><a href='/account?view=returns'>Returns &amp; Replacements</a></section>
      </div>
      <div class='footer-base'><span>English</span><span>United States</span></div>`;
  }

  function errorNotice() {
    if (!state.apiError) return '';
    return `<div class='error-strip' role='alert'><i data-lucide='triangle-alert' aria-hidden='true'></i><span>Some current store information could not be loaded. Showing available local catalog information.</span><button type='button' data-retry>Retry</button></div>`;
  }

  function setMain(content) {
    main.innerHTML = `${errorNotice()}${content}`;
    createIcons();
  }

  function renderLoading(label = 'Loading') {
    document.title = `${label} - Amazon.com`;
    setMain(`<section class='route-loading' aria-label='${escapeHtml(label)}'><div class='skeleton skeleton-hero'></div><div class='skeleton-row'><div class='skeleton skeleton-panel'></div><div class='skeleton skeleton-panel'></div><div class='skeleton skeleton-panel'></div></div><span class='sr-only'>${escapeHtml(label)}</span></section>`);
  }

  function compactCard(product, options = {}) {
    if (!product) return '';
    const rank = options.rank ? `<span class='rank-ribbon'>#${options.rank}</span>` : '';
    const deal = options.deal && product.deal ? `<div class='deal-line'><span>${escapeHtml(product.deal)}</span><strong>Limited time deal</strong></div>` : '';
    const quickAdd = options.quickAdd ? `<button class='quick-add icon-button' type='button' data-quick-add='${escapeHtml(product.asin)}' title='Add to cart' aria-label='Add ${escapeHtml(product.short_title || product.title)} to cart'><i data-lucide='plus' aria-hidden='true'></i></button>` : '';
    return `
      <article class='compact-card' data-asin='${escapeHtml(product.asin)}'>
        ${rank}
        <a class='compact-image-link' href='${productHref(product)}'>${productImageMarkup(product, 'compact-image')}</a>
        ${quickAdd}
        ${deal}
        <a class='compact-title' href='${productHref(product)}'>${escapeHtml(product.short_title || product.title)}</a>
        ${options.rating === false ? '' : ratingMarkup(product)}
        <div class='compact-price'>${priceMarkup(product.price)} ${product.old_price ? `<span class='old-price'>${money(product.old_price)}</span>` : ''}</div>
        ${product.prime ? "<span class='prime-mark'>prime</span>" : ''}
      </article>`;
  }

  function productRail(title, products, options = {}) {
    return `
      <section class='product-rail'>
        <div class='section-heading'><h2>${escapeHtml(title)}</h2>${options.href ? `<a href='${options.href}'>See more</a>` : ''}</div>
        <div class='rail-scroller'>${products.filter(Boolean).map((product, index) => compactCard(product, { rank: options.ranked ? index + 1 : 0, deal: options.deal, quickAdd: options.quickAdd })).join('')}</div>
      </section>`;
  }

  function homeModuleMarkup(module) {
    const products = module.asins.map(productByAsin).filter(Boolean).slice(0, 4);
    return `
      <section class='home-module'>
        <h2>${escapeHtml(module.title)}</h2>
        <div class='home-module-grid'>
          ${products.map((product) => `<a href='${productHref(product)}'><span>${productImageMarkup(product, 'home-module-image')}</span><small>${escapeHtml(product.category || product.short_title)}</small></a>`).join('')}
        </div>
        <a class='module-link' href='${module.href}'>Explore more</a>
      </section>`;
  }

  function renderHome() {
    document.title = 'Amazon.com. Spend less. Smile more.';
    const modules = state.siteCatalog.homeModules;
    const catalog = catalogProducts();
    const recent = (state.recent_views || [])
      .map((entry) => productByAsin(entry.asin) || (entry.product ? { ...entry.product, source: 'marketplace' } : null))
      .filter(Boolean);
    setMain(`
      <section class='home-page'>
        <div class='market-hero'>
          <div class='market-hero-copy'><h1>Everyday finds for every room</h1><p>Discover popular picks across home, electronics, fashion, beauty, toys, and books.</p><a href='/Best-Sellers/zgbs'>Shop Best Sellers</a></div>
          <div class='market-hero-products' aria-hidden='true'>${catalog.slice(0, 4).map((product) => productImageMarkup(product, 'hero-product-image')).join('')}</div>
        </div>
        <div class='home-site-content'>
          <div class='home-module-row'>${modules.map(homeModuleMarkup).join('')}</div>
          ${recent.length ? productRail('Keep shopping for', recent, { href: '/hz/history' }) : ''}
          ${productRail('Popular in electronics', catalog.filter((product) => product.department === 'Electronics').concat(ssdProducts().slice(0, 3)), { href: '/s?k=electronics&i=electronics' })}
          ${productRail('Home refresh favorites', catalog.filter((product) => product.department === 'Home & Kitchen'), { href: '/s?k=home&i=home' })}
          <div class='home-module-row secondary-modules'>
            ${homeModuleMarkup({ title: 'Toys for creative play', asins: ['B0D5BLOCKS1', '0241341657', 'B0D1LAMP01', 'B0D4BOTTLE'], href: '/s?k=toys&i=toys' })}
            ${homeModuleMarkup({ title: 'Beauty and self care', asins: ['B0D3SERUM1', 'B0D4TMBLR1', 'B0D2THROW1', 'B0D7EARBDS'], href: '/s?k=beauty&i=beauty' })}
            ${homeModuleMarkup({ title: 'Books and ideas', asins: ['0241341657', 'B0D2BCKPCK', 'B0D1LAMP01', 'B0D5BLOCKS1'], href: '/s?k=books&i=books' })}
            ${homeModuleMarkup({ title: 'Ready for the day', asins: ['B0D8RUNSHO', 'B0D2BCKPCK', 'B0D4BOTTLE', 'B0D9HEADPH'], href: '/s?k=fashion&i=fashion' })}
          </div>
          ${productRail('Frequently repurchased essentials', catalog.slice().reverse().slice(0, 8), { href: '/s?k=everyday+essentials' })}
        </div>
      </section>`);
  }

  function rankedProductMarkup(product) {
    const normalized = { ...product, source: 'ssd' };
    return `
      <article class='ranked-product' data-asin='${escapeHtml(product.asin)}'>
        <span class='rank-ribbon'>#${safeNumber(product.rank)}</span>
        <a class='ranked-image-link' href='${productHref(normalized)}' aria-label='View ${escapeHtml(product.short_title || product.title)}'>${spriteMarkup(product, 'ranked-image')}</a>
        <div class='ranked-meta'>
          <a class='ranked-title' href='${productHref(normalized)}'>${escapeHtml(product.title)}</a>
          ${ratingMarkup(normalized)}
          <p class='ranked-bought'>${escapeHtml(product.bought || '')}</p>
          <div class='price-line'>${priceMarkup(product.price)}<span class='old-price'>${money(product.old_price)}</span></div>
        </div>
      </article>`;
  }

  function renderBestSellers() {
    document.title = 'Amazon Best Sellers: Best External Solid State Drives';
    const products = Array.isArray(state.products) ? state.products : [];
    setMain(`
      <nav class='store-subnav ssd-subnav' aria-label='Computers store'><a href='/Computers-Accessories/b/?node=541966'>Computers</a><a href='/s?k=laptops&i=computers'>Laptops</a><a href='/s?k=desktops&i=computers'>Desktops</a><a href='/s?k=monitors&i=computers'>Monitors</a><a href='/s?k=tablets&i=computers'>Tablets</a><a href='/s?k=computer+accessories&i=computers'>Computer Accessories</a><a href='/s?k=pc+components&i=computers'>PC Components</a><a href='/gp/goldbox/'>Deals</a></nav>
      <section class='best-page ssd-best-page'>
        <div class='best-hero'><h1>Amazon Best Sellers</h1><p>Our most popular products based on sales. Updated frequently.</p></div>
        <div class='mobile-category-bar'>External Solid State Drives <i data-lucide='chevron-down' aria-hidden='true'></i></div>
        <nav class='mobile-best-tabs' aria-label='Best Sellers views'><a class='active' href='${BEST_SELLERS_PATH}'>Best Sellers</a><a href='/s?k=new+external+ssd'>New Releases</a></nav>
        <div class='best-layout'>
          <aside class='category-sidebar' aria-label='Category hierarchy'><ul><li>‹ Any Department</li><li class='depth-1'>‹ Computers &amp; Accessories</li><li class='depth-2'>Data Storage</li><li class='depth-3'>Crypto Hardware Wallets</li><li class='depth-3'>External Hard Drives</li><li class='depth-3 current'>External Solid State Drives</li><li class='depth-3'>External Zip Drives</li><li class='depth-3'>Floppy &amp; Tape Drives</li><li class='depth-3'>Internal Hard Drives</li><li class='depth-3'>Internal Solid State Drives</li><li class='depth-3'>Network Attached Storage</li><li class='depth-3'>Tape Libraries</li><li class='depth-3'>USB Flash Drives</li></ul></aside>
          <section class='ranked-section'><h2>Best Sellers in External Solid State Drives</h2>
            ${products.length ? `<div class='ranked-grid'>${products.map(rankedProductMarkup).join('')}</div>` : `<div class='no-results'><h2>No Best Sellers are available right now.</h2><button class='amazon-button' type='button' data-retry>Try again</button></div>`}
          </section>
        </div>
      </section>`);
  }

  function renderBestSellersRoot() {
    document.title = 'Amazon Best Sellers';
    const rails = state.siteCatalog.bestSellerRails;
    const categories = state.siteCatalog.departments.map((department) => department.name);
    setMain(`
      <nav class='local-tabs' aria-label='Best Sellers navigation'><a class='active' href='/Best-Sellers/zgbs'>Best Sellers</a><a href='/s?k=new+releases'>New Releases</a></nav>
      <section class='root-best-page'>
        <header><h1>Amazon Best Sellers</h1><p>Our most popular products based on sales. Updated frequently.</p></header>
        <div class='root-best-layout'>
          <aside class='all-departments' aria-label='Any Department'><h2>Any Department</h2>${categories.map((category) => `<a href='/s?k=${encodeURIComponent(category)}'>${escapeHtml(category)}</a>`).join('')}<a href='${BEST_SELLERS_PATH}'>Computers &amp; Accessories</a></aside>
          <div class='best-rails'>${rails.map((rail) => productRail(rail.title, rail.asins.map(productByAsin), { ranked: true, href: '/s?k=' + encodeURIComponent(rail.title.replace('Best Sellers in ', '')) })).join('')}</div>
        </div>
      </section>`);
  }

  function computerProductCard(product) {
    const normalized = { ...product, source: 'ssd' };
    return `<article class='computer-card'><a href='${productHref(normalized)}'>${productImageMarkup(normalized, 'computer-image')}</a><a href='${productHref(normalized)}'>${escapeHtml(product.short_title || product.title)}</a>${ratingMarkup(normalized)}${priceMarkup(product.price)}<small>FREE delivery ${escapeHtml(state.session.delivery_label)}</small></article>`;
  }

  function renderComputers() {
    document.title = 'Computers, Tablets, & Accessories - Amazon.com';
    const catalog = catalogProducts();
    const electronics = catalog.filter((product) => product.department === 'Electronics');
    setMain(`
      <nav class='store-subnav' aria-label='Computers store'><a href='/Computers-Accessories/b/?node=541966'>Computers</a><a href='/s?k=laptops&i=computers'>Laptops</a><a href='/s?k=desktops&i=computers'>Desktops</a><a href='/s?k=monitors&i=computers'>Monitors</a><a href='/s?k=tablets&i=computers'>Tablets</a><a href='/s?k=computer+accessories&i=computers'>Computer Accessories</a><a href='/gp/goldbox/'>Deals</a></nav>
      <section class='computers-page'>
        <aside class='computer-sidebar'><h2>Shop by Store</h2>${['Handpicked Electronics', 'Laptops', 'Desktops', 'PC Gaming', 'Monitors', 'Tablets', 'Computer Accessories', 'Networking', 'Computer Components', 'Drives & Storage'].map((item) => `<a href='/s?k=${encodeURIComponent(item)}&i=computers'>${item}</a>`).join('')}<h2>Best Sellers</h2><a href='${BEST_SELLERS_PATH}'>Drives &amp; Storage Products</a></aside>
        <div class='computer-content'>
          <a class='mobile-store-back' href='/s?k=electronics'>‹ Electronics</a>
          <h1 class='mobile-store-title'>Computers, Tablets, &amp; Accessories</h1>
          <div class='computers-banner'>Computers &amp; Accessories</div>
          <section class='brand-store-rail'><h2>Shop SanDisk</h2><div class='computer-scroller'>${state.products.slice(0, 6).map(computerProductCard).join('')}</div></section>
          <section class='featured-computer-shops'><h2>Featured shops</h2><div class='featured-shop-grid'><a href='/s?k=laptops&i=computers'>${productImageMarkup(productByAsin('B0D2BCKPCK'), 'featured-shop-image')}<strong>Laptops &amp; everyday carry</strong></a><a href='/s?k=headphones&i=electronics'>${productImageMarkup(electronics[0], 'featured-shop-image')}<strong>Audio accessories</strong></a><a href='${BEST_SELLERS_PATH}'>${productImageMarkup({ ...state.products[1], source: 'ssd' }, 'featured-shop-image')}<strong>Portable data storage</strong></a></div></section>
          ${productRail('Top picks for your setup', electronics.concat(catalog.filter((product) => product.category === 'Lighting')), { href: '/s?k=computer+setup&i=computers' })}
        </div>
      </section>`);
  }

  function filterMarkup(context = 'search') {
    const ui = state.searchUi || { filters: {} };
    const filters = ui.filters || {};
    const departmentFilters = [['all', 'All'], ['computers', 'Computers & Accessories'], ...(state.siteCatalog.departments || []).map((department) => [department.slug || slugify(department.name).toLowerCase(), department.name])];
    return `
      <div class='filter-section'><h2>Department</h2>
        ${departmentFilters.map(([value, label]) => `<label><input type='radio' name='${context}-department' value='${escapeHtml(value)}' data-search-filter='department'${(filters.department || 'all') === value ? ' checked' : ''}> ${escapeHtml(label)}</label>`).join('')}
      </div>
      <div class='filter-section'><h2>Eligible for Free Shipping</h2><label><input type='checkbox' data-search-filter='prime'${filters.prime ? ' checked' : ''}> Prime &amp; FREE Shipping</label></div>
      <div class='filter-section'><h2>Customer Reviews</h2><label><input type='checkbox' data-search-filter='rating'${filters.rating ? ' checked' : ''}> <span class='stars'>★★★★☆</span> &amp; Up</label></div>
      <div class='filter-section'><h2>Price</h2>
        ${[['all', 'All prices'], ['under25', 'Under $25'], ['25to50', '$25 to $50'], ['50to100', '$50 to $100'], ['100plus', '$100 & above']].map(([value, label]) => `<label><input type='radio' name='${context}-price' value='${value}' data-search-filter='price'${(filters.price || 'all') === value ? ' checked' : ''}> ${label}</label>`).join('')}
      </div>`;
  }

  function renderDeals() {
    document.title = "Today's Deals - Amazon.com";
    const products = catalogProducts().filter((product) => product.deal);
    setMain(`
      <nav class='deals-subnav' aria-label='Deals navigation'><a href='/gp/goldbox/'>Today's Deals</a><a href='/gp/goldbox/?view=coupons'>Coupons</a><a href='/gp/goldbox/?view=renewed'>Renewed Deals</a><a href='/gp/goldbox/?view=outlet'>Outlet</a><a href='/gp/goldbox/?view=resale'>Amazon Resale</a></nav>
      <section class='deals-page'>
        <div class='deal-chips' aria-label='Deal categories'><button type='button'><i data-lucide='chevron-left'></i></button>${["Lightning deals", "Customers' Most-Loved", 'Outlet', 'Lowest Price in 365 Days', 'Premium Brands', 'Summer Favorites', 'Beauty', 'Fashion', 'Home'].map((label) => `<a href='/gp/goldbox/?category=${encodeURIComponent(label)}'>${label}</a>`).join('')}<button type='button'><i data-lucide='chevron-right'></i></button><button class='mobile-filter-button' type='button' data-open-filters>Filters</button></div>
        <div class='deals-layout'>
          <aside class='deals-filters'><h2>Department</h2>${['All', 'Amazon Devices & Accessories', 'Appliances', 'Arts, Crafts & Sewing', 'Beauty & Personal Care'].map((label, index) => `<label><input type='radio' name='deal-department'${index === 0 ? ' checked' : ''}> ${label}</label>`).join('')}<h2>Brands</h2>${['SoundPoint', 'Harbor House', 'KitchenWorks', 'Trail & Tide'].map((label) => `<label><input type='checkbox'> ${label}</label>`).join('')}<h2>Customer Reviews</h2><span class='stars'>★★★★☆</span> &amp; up</aside>
          <section class='deals-grid' aria-label="Today's Deals">${products.map((product) => compactCard(product, { deal: true, quickAdd: true })).join('')}</section>
        </div>
      </section>`);
  }

  function searchHaystack(product) {
    return [product.title, product.short_title, product.brand, product.department, product.category, ...(product.bullets || [])].join(' ').toLowerCase();
  }

  function matchDepartment(product, department) {
    if (!department || department === 'all') return true;
    const normalized = department.toLowerCase();
    return [product.department, product.category].some((value) => String(value || '').toLowerCase().includes(normalized));
  }

  function applySearchFilters(products, filters) {
    return products.filter((product) => {
      if (!matchDepartment(product, filters.department)) return false;
      if (filters.prime && !product.prime && product.source !== 'ssd') return false;
      if (filters.rating && safeNumber(product.rating) < 4) return false;
      const price = safeNumber(product.price);
      if (filters.price === 'under25' && price >= 25) return false;
      if (filters.price === '25to50' && (price < 25 || price > 50)) return false;
      if (filters.price === '50to100' && (price < 50 || price > 100)) return false;
      if (filters.price === '100plus' && price < 100) return false;
      return true;
    });
  }

  function sortProducts(products, sort) {
    const sorted = [...products];
    if (sort === 'price-asc') sorted.sort((a, b) => a.price - b.price);
    else if (sort === 'price-desc') sorted.sort((a, b) => b.price - a.price);
    else if (sort === 'rating') sorted.sort((a, b) => b.rating - a.rating || b.reviews - a.reviews);
    else sorted.sort((a, b) => b.reviews - a.reviews);
    return sorted;
  }

  function searchResultMarkup(product) {
    return `
      <article class='search-result' data-asin='${escapeHtml(product.asin)}'>
        <a href='${productHref(product)}'>${productImageMarkup(product, 'search-result-image')}</a>
        <div class='search-result-copy'>
          <h2><a href='${productHref(product)}'>${escapeHtml(product.title)}</a></h2>
          ${ratingMarkup(product)}
          <p class='ranked-bought'>${escapeHtml(product.bought || '')}</p>
          ${priceMarkup(product.price)}
          ${product.prime || product.source === 'ssd' ? "<span class='prime-mark'>prime</span>" : ''}
          <p>FREE delivery to ${escapeHtml(state.session.delivery_label)}</p>
          <button class='amazon-button amazon-button-primary search-quick-add' type='button' data-quick-add='${escapeHtml(product.asin)}'>Add to cart</button>
        </div>
      </article>`;
  }

  function renderSearchFromState() {
    const ui = state.searchUi;
    const cleaned = ui.query.trim();
    const filtered = sortProducts(applySearchFilters(ui.baseProducts, ui.filters), ui.sort);
    const pageSize = 16;
    const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
    ui.page = Math.min(Math.max(1, ui.page), totalPages);
    const visible = filtered.slice((ui.page - 1) * pageSize, ui.page * pageSize);
    const countLabel = `${filtered.length} ${filtered.length === 1 ? 'result' : 'results'}`;
    document.title = cleaned ? `Amazon.com : ${cleaned}` : 'Amazon.com Search';
    setMain(`
      <section class='search-page'>
        <header class='search-heading'>
          <div><span>1-${Math.min(filtered.length, ui.page * pageSize)} of ${countLabel} for</span><h1>“${escapeHtml(cleaned)}”</h1></div>
          <label>Sort by:
            <select data-search-sort><option value='featured'${ui.sort === 'featured' ? ' selected' : ''}>Featured</option><option value='price-asc'${ui.sort === 'price-asc' ? ' selected' : ''}>Price: Low to High</option><option value='price-desc'${ui.sort === 'price-desc' ? ' selected' : ''}>Price: High to Low</option><option value='rating'${ui.sort === 'rating' ? ' selected' : ''}>Avg. Customer Review</option></select>
          </label>
        </header>
        <div class='mobile-search-chips'><button type='button' data-open-filters><i data-lucide='sliders-horizontal'></i></button><button type='button' data-chip-filter='prime'>Prime</button><button type='button' data-chip-filter='rating'>★★★★ &amp; Up</button><button type='button' data-chip-filter='price'>Price</button><button type='button' data-chip-filter='department'>Department</button></div>
        ${ui.corrected ? `<div class='correction-line'>Showing results for <a href='/s?k=${encodeURIComponent(ui.corrected)}'><strong>${escapeHtml(ui.corrected)}</strong></a><br><small>Search instead for <a href='/s?k=${encodeURIComponent(cleaned)}'>${escapeHtml(cleaned)}</a></small></div>` : ''}
        <div class='search-layout'>
          <aside class='search-filters' aria-label='Search filters'>${filterMarkup('desktop')}</aside>
          <section class='search-results-column' aria-label='Search results'>
            ${!cleaned ? `<div class='no-results'><h2>Enter a search term</h2><p>Use the search box to find products.</p></div>` : visible.length ? visible.map(searchResultMarkup).join('') : `<div class='no-results'><h2>No results for “${escapeHtml(cleaned)}”</h2><p>Try checking your spelling or use more general terms.</p><a href='/Best-Sellers/zgbs'>Browse Best Sellers</a></div>`}
            ${filtered.length > pageSize ? `<nav class='pagination' aria-label='Search results pages'><button type='button' data-search-page='${ui.page - 1}'${ui.page === 1 ? ' disabled' : ''}><i data-lucide='chevron-left'></i> Previous</button>${Array.from({ length: totalPages }, (_, index) => index + 1).filter((page) => page === 1 || page === totalPages || Math.abs(page - ui.page) <= 2).map((page, index, pages) => `${index > 0 && page - pages[index - 1] > 1 ? '<span aria-hidden="true">…</span>' : ''}<button type='button' data-search-page='${page}'${ui.page === page ? " aria-current='page'" : ''}>${page}</button>`).join('')}<button type='button' data-search-page='${ui.page + 1}'${ui.page === totalPages ? ' disabled' : ''}>Next <i data-lucide='chevron-right'></i></button></nav>` : ''}
          </section>
        </div>
      </section>`);
    if (filterDialog) {
      filterDialog.querySelector('.filter-dialog-content').innerHTML = filterMarkup('mobile');
      createIcons();
    }
  }

  function typoCorrection(query) {
    const corrections = {
      'headphons': 'headphones',
      'wireles earbuds': 'wireless earbuds',
      'ear buds': 'wireless earbuds',
      'portble ssd': 'portable ssd',
      'water botle': 'water bottle',
    };
    return corrections[query.toLowerCase()] || '';
  }

  async function renderSearchRoute() {
    const query = currentQuery().trim().slice(0, 160);
    if (!query) {
      state.searchUi = { query: '', corrected: '', baseProducts: [], filters: { department: currentDepartment(), price: 'all', prime: false, rating: false }, sort: 'featured', page: 1 };
      renderSearchFromState();
      return;
    }
    renderLoading('Searching Amazon');
    const correction = typoCorrection(query);
    const effectiveQuery = correction || query;
    const terms = effectiveQuery.toLowerCase().split(/\s+/).filter(Boolean);
    const localMatches = allProducts().filter((product) => terms.every((term) => searchHaystack(product).includes(term)));
    let apiProducts = [];
    let searchError = '';
    try {
      const response = await fetch(`/api/search?k=${encodeURIComponent(effectiveQuery)}`, { headers: { Accept: 'application/json' }, cache: 'no-store' });
      if (!response.ok) throw new Error(`Search returned ${response.status}`);
      const payload = await response.json();
      apiProducts = Array.isArray(payload.products) ? payload.products.map((product) => {
        const catalogProduct = state.siteCatalog.products.find((candidate) => candidate.asin === product.asin);
        return catalogProduct
          ? { ...catalogProduct, ...product, source: 'marketplace' }
          : { ...product, source: 'ssd', department: 'Computers', category: 'Data Storage', brand: product.title.split(' ')[0] };
      }) : [];
    } catch (error) {
      searchError = error instanceof Error ? error.message : 'Search failed';
    }
    const merged = new Map();
    [...localMatches, ...apiProducts].forEach((product) => merged.set(product.asin, product));
    state.searchUi = {
      query,
      corrected: correction,
      baseProducts: [...merged.values()],
      filters: { department: currentDepartment(), price: 'all', prime: false, rating: false },
      sort: 'featured',
      page: 1,
    };
    if (searchError && !state.apiError) state.apiError = searchError;
    renderSearchFromState();
  }

  function purchaseForm(product, mobile = false) {
    const action = mobile ? '/cart/add-to-cart/ref=mw_dp_buy_crt' : '/gp/product/handle-buy-box/ref=dp_start-bbf_1_glance';
    return `
      <form class='purchase-form ${mobile ? 'mobile-purchase' : 'desktop-purchase'}' method='post' action='${action}' data-add-form>
        <input type='hidden' name='ASIN' value='${escapeHtml(product.asin)}'>
        <label>Quantity:
          <select class='quantity-select' name='quantity' aria-label='Quantity'><option value='1'>1</option><option value='2'>2</option><option value='3'>3</option></select>
        </label>
        <button class='amazon-button amazon-button-primary' type='submit' name='submit.add-to-cart' value='Add to Cart'>Add to cart</button>
        <a class='amazon-button amazon-button-orange' href='/buy-now?asin=${encodeURIComponent(product.asin)}'>Buy Now</a>
      </form>`;
  }

  function reviewSection(product) {
    const histogram = [76, 15, 5, 2, 2];
    return `
      <section class='reviews-section' id='reviews'>
        <div class='rating-summary'><h2>Customer reviews</h2><div class='rating-large'><span class='stars'>★★★★★</span> ${safeNumber(product.rating).toFixed(1)} out of 5</div><p>${reviewCount(product.reviews)} global ratings</p>
          <div class='rating-histogram'>${histogram.map((value, index) => `<span>${5 - index} star</span><div><i style='width:${value}%'></i></div><a href='#reviews'>${value}%</a>`).join('')}</div>
        </div>
        <div class='review-cards'><h2>Top reviews</h2>
          <article><strong>Exactly what I needed</strong><span class='stars'>★★★★★</span><small>Reviewed in the United States</small><p>The product arrived well packed and has been dependable in everyday use. Setup was simple and the size is convenient.</p></article>
          <article><strong>Good quality and easy to use</strong><span class='stars'>★★★★★</span><small>Reviewed in the United States</small><p>Feels thoughtfully made and matches the description. I would choose it again for the same use.</p></article>
        </div>
      </section>`;
  }

  function relatedFor(product) {
    const sameDepartment = allProducts().filter((candidate) => candidate.asin !== product.asin && (candidate.department === product.department || candidate.category === product.category));
    return sameDepartment.concat(allProducts().filter((candidate) => candidate.asin !== product.asin && !sameDepartment.some((item) => item.asin === candidate.asin))).slice(0, 8);
  }

  function renderProduct() {
    const product = state.products.find((item) => item.asin === TARGET_ASIN);
    if (!product) {
      renderNotFound();
      return;
    }
    const normalized = { ...product, source: 'ssd' };
    document.title = `${product.title} - Amazon.com`;
    const discount = product.old_price > product.price ? Math.round((1 - safeNumber(product.price) / safeNumber(product.old_price, 1)) * 100) : 0;
    setMain(`
      <article class='product-page t7-product-page'>
        <nav class='breadcrumbs' aria-label='Breadcrumb'>Electronics › Computers &amp; Accessories › Data Storage › External Solid State Drives</nav>
        <div class='product-layout'>
          <section class='product-gallery' aria-label='Product images'>
            <div class='thumbnail-list' aria-label='Image thumbnails'>
              <button class='thumbnail selected' type='button' data-gallery-state='main' title='Main product image' aria-label='Main product image'>${spriteMarkup(product, '')}</button>
              <button class='thumbnail thumbnail-placeholder' type='button' data-gallery-state='side' title='Side view' aria-label='Side view'>2</button>
              <button class='thumbnail thumbnail-placeholder' type='button' data-gallery-state='scale' title='Size view' aria-label='Size view'>3</button>
              <button class='thumbnail thumbnail-placeholder' type='button' data-gallery-state='detail' title='Features' aria-label='Features'>4</button>
              <button class='thumbnail thumbnail-placeholder' type='button' data-gallery-state='video' title='Videos' aria-label='Product videos'><i data-lucide='play' aria-hidden='true'></i></button>
            </div>
            ${spriteMarkup(product, 'main-product-image gallery-main', `${product.short_title}, ${product.color}`)}
          </section>
          <section class='product-summary'>
            <a class='brand-link' href='/s?k=samsung+portable+ssd'>Visit the Samsung Store</a>
            <h1>${escapeHtml(product.title)}</h1>
            ${ratingMarkup(normalized)}
            <span class='choice-badge'>Amazon's <em>Choice</em></span>
            <p class='recent-sales'><strong>${escapeHtml(product.bought || '5K+ bought in past month').split(' bought')[0]} bought</strong> in past month</p>
          </section>
          <section class='product-details'>
            <div class='variant-block'><p>Color: <strong data-variant-value>Titan Gray</strong></p><div class='variant-options' aria-label='Color'><button class='variant-option selected' type='button' data-variant='Titan Gray'>Titan Gray</button><button class='variant-option' type='button' data-variant='Blue'>Blue</button><button class='variant-option' type='button' data-variant='Red'>Red</button></div></div>
            <div class='variant-block'><p>Memory Storage Capacity: <strong data-variant-value>1 TB</strong></p><div class='variant-options' aria-label='Memory Storage Capacity'><button class='variant-option' type='button' data-variant='500 GB'>500 GB</button><button class='variant-option selected' type='button' data-variant='1 TB'>1 TB</button><button class='variant-option' type='button' data-variant='2 TB'>2 TB</button></div></div>
            <div class='product-price-block'>${discount ? `<span class='discount'>-${discount}%</span>` : ''}${priceMarkup(product.price, 'product-price')}<p class='list-price'>List Price: <del>${money(product.old_price)}</del></p><a href='/local-boundary?kind=returns'>FREE Returns</a></div>
            <dl class='fact-table'><dt>Digital Storage Capacity</dt><dd>${escapeHtml(product.capacity)}</dd><dt>Hard Disk Interface</dt><dd>${escapeHtml(product.interface)}</dd><dt>Connectivity Technology</dt><dd>${escapeHtml(product.connectivity)}</dd><dt>Brand</dt><dd>Samsung</dd></dl>
            <section class='about-item'><h2>About this item</h2><ul>${(product.bullets || []).map((bullet) => `<li>${escapeHtml(bullet)}</li>`).join('')}</ul></section>
          </section>
          <aside class='buy-box' aria-label='Purchase options'>
            <div class='buy-price'>${priceMarkup(product.price, 'buy-price-value')}</div>
            <div class='delivery-copy'><i data-lucide='map-pin' aria-hidden='true'></i><span>FREE delivery to ${escapeHtml(state.session.delivery_label)}. See details</span></div>
            <div class='stock'>In Stock</div>
            ${purchaseForm(product, false)}
            ${purchaseForm(product, true)}
            <div class='buy-box-facts'><span>Ships from</span><span>Amazon.com</span><span>Sold by</span><span>Amazon.com</span><span>Returns</span><span>30-day refund/replacement</span></div>
            <div class='buy-box-divider'></div>
            <a class='amazon-button' href='/hz/wishlist/ls?asin=${encodeURIComponent(product.asin)}'>Add to List</a>
            <div class='other-offer'><strong>Other sellers on Amazon</strong><span>New from ${money(product.price + 8.25)}</span></div>
          </aside>
        </div>
        ${productRail('Products related to this item', relatedFor(normalized), { href: '/s?k=portable+storage' })}
        ${reviewSection(normalized)}
      </article>`);
  }

  function variantMarkup(product) {
    const variants = product.variants && typeof product.variants === 'object' ? Object.entries(product.variants) : [];
    return variants.map(([label, values]) => `
      <div class='variant-block'><p>${escapeHtml(label)}: <strong data-variant-value>${escapeHtml(values[0])}</strong></p><div class='variant-options' aria-label='${escapeHtml(label)}'>${values.map((value, index) => `<button class='variant-option${index === 0 ? ' selected' : ''}' type='button' data-variant='${escapeHtml(value)}'>${escapeHtml(value)}</button>`).join('')}</div></div>`).join('');
  }

  function specsMarkup(product) {
    const entries = product.specs && typeof product.specs === 'object' ? Object.entries(product.specs) : [];
    return entries.map(([key, value]) => `<dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd>`).join('');
  }

  function renderGenericProduct(product) {
    document.title = `${product.title} - Amazon.com`;
    const discount = product.old_price > product.price ? Math.round((1 - product.price / product.old_price) * 100) : 0;
    setMain(`
      <article class='generic-pdp'>
        <nav class='breadcrumbs' aria-label='Breadcrumb'>${escapeHtml(product.department)} › ${escapeHtml(product.category)} › ${escapeHtml(product.brand)}</nav>
        <div class='generic-pdp-layout'>
          <section class='generic-gallery' aria-label='Product images'>
            <div class='generic-thumbnails'><button class='thumbnail selected' type='button' data-gallery-state='main' aria-label='Main image'>${productImageMarkup(product, '')}</button><button class='thumbnail' type='button' data-gallery-state='detail' aria-label='Product detail'><i data-lucide='scan-search'></i></button><button class='thumbnail' type='button' data-gallery-state='scale' aria-label='Product scale'><i data-lucide='ruler'></i></button></div>
            <div class='generic-main-wrap'>${productImageMarkup(product, 'generic-main-image gallery-main')}</div>
          </section>
          <section class='generic-summary'>
            <a class='brand-link' href='/s?k=${encodeURIComponent(product.brand)}'>Visit the ${escapeHtml(product.brand)} Store</a>
            <h1>${escapeHtml(product.title)}</h1>
            ${ratingMarkup(product)}
            <span class='choice-badge'>Amazon's <em>Choice</em></span>
            <p class='recent-sales'>${escapeHtml(product.bought)}</p>
          </section>
          <section class='generic-details'>
            <div class='product-price-block'><span class='discount'>-${discount}%</span>${priceMarkup(product.price, 'product-price')}<p class='list-price'>List Price: <del>${money(product.old_price)}</del></p><a href='/local-boundary?kind=returns'>FREE Returns</a></div>
            ${variantMarkup(product)}
          </section>
          <section class='generic-information'>
            <dl class='fact-table'>${specsMarkup(product)}</dl>
            <section class='about-item'><h2>About this item</h2><ul>${product.bullets.map((bullet) => `<li>${escapeHtml(bullet)}</li>`).join('')}</ul></section>
          </section>
          <aside class='buy-box generic-buy-box' aria-label='Purchase options'>
            <div class='buy-price'>${priceMarkup(product.price, 'buy-price-value')}</div>
            <div class='delivery-copy'><i data-lucide='map-pin'></i><span>FREE delivery to ${escapeHtml(state.session.delivery_label)}</span></div>
            <strong class='stock'>${escapeHtml(product.availability)}</strong>
            <label>Quantity: <select class='quantity-select' data-generic-quantity><option value='1'>1</option><option value='2'>2</option><option value='3'>3</option></select></label>
            <button class='amazon-button amazon-button-primary' type='button' data-quick-add='${escapeHtml(product.asin)}'>Add to cart</button>
            <button class='amazon-button amazon-button-orange' type='button' data-boundary='buy-now'>Buy Now</button>
            <div class='buy-box-facts'><span>Ships from</span><span>Amazon.com</span><span>Sold by</span><span>${escapeHtml(product.brand)}</span><span>Returns</span><span>30-day refund/replacement</span></div>
            <button class='amazon-button' type='button' data-list-add='${escapeHtml(product.asin)}'>Add to List</button>
            <div class='other-offer'><strong>Other sellers on Amazon</strong><span>New from ${money(product.price + 4.5)}</span><span>Used from ${money(product.price * 0.82)}</span></div>
          </aside>
        </div>
        <section class='pdp-specifications'><h2>Product information</h2><dl class='fact-table'>${specsMarkup(product)}</dl></section>
        ${productRail('Customers who viewed this item also viewed', relatedFor(product), { href: '/s?k=' + encodeURIComponent(product.category) })}
        ${reviewSection(product)}
      </article>`);
  }

  function cartItemProduct(item) {
    return item.product || productByAsin(item.asin) || { asin: item.asin, title: 'Amazon item', short_title: 'Amazon item', price: 0, source: 'marketplace', sprite_index: 0 };
  }

  function cartItemMarkup(item) {
    const product = cartItemProduct(item);
    const quantity = Math.min(3, Math.max(1, safeNumber(item.quantity, 1)));
    const local = Boolean(item.local);
    return `
      <article class='cart-item' data-cart-item='${escapeHtml(item.asin)}'>
        <a href='${productHref(product)}'>${productImageMarkup(product, 'cart-item-image')}</a>
        <div><a class='cart-item-title' href='${productHref(product)}'>${escapeHtml(product.title)}</a><p class='cart-stock'>In Stock</p><span class='sr-only'>Quantity</span>
          <div class='cart-item-actions'>
            <select class='cart-quantity' ${local ? 'data-local-cart-quantity' : 'data-cart-quantity'}='${escapeHtml(item.asin)}' aria-label='Quantity for ${escapeHtml(product.short_title || product.title)}'>${[1, 2, 3].map((value) => `<option value='${value}'${quantity === value ? ' selected' : ''}>Qty: ${value}</option>`).join('')}</select>
            <button class='text-action' type='button' ${local ? 'data-local-remove' : 'data-remove'}='${escapeHtml(item.asin)}'>Delete</button>
            ${local ? '' : `<button class='text-action' type='button' data-save='${escapeHtml(item.asin)}'>Save for later</button>`}
          </div>
        </div>
        <strong class='cart-item-price'>${money(product.price * quantity)}</strong>
      </article>`;
  }

  function savedItemMarkup(item) {
    const product = item.product || productByAsin(item.asin) || item;
    return `<article class='saved-item'>${productImageMarkup(product, '')}<div><a href='${productHref(product)}'>${escapeHtml(product.title || product.short_title)}</a><br>${priceMarkup(product.price)}<br><button class='amazon-button' type='button' data-move-to-cart='${escapeHtml(product.asin)}'>Move to Cart</button></div></article>`;
  }

  function localCartItems() {
    const backendAsins = new Set((state.cart?.items || []).map((item) => item.asin));
    return Object.entries(state.localCart)
      .filter(([asin, quantity]) => safeNumber(quantity) > 0 && !backendAsins.has(asin))
      .map(([asin, quantity]) => ({ asin, quantity, product: productByAsin(asin), local: true }))
      .filter((item) => item.product);
  }

  function renderCart() {
    document.title = 'Amazon.com Shopping Cart';
    const serverItems = Array.isArray(state.cart?.items) ? state.cart.items : [];
    const localItems = localCartItems();
    const items = serverItems.concat(localItems);
    const saved = Array.isArray(state.saved_for_later) ? state.saved_for_later : [];
    const totalQuantity = serverItems.reduce((total, item) => total + safeNumber(item.quantity), 0) + localItems.reduce((total, item) => total + safeNumber(item.quantity), 0);
    const subtotal = serverItems.reduce((total, item) => total + safeNumber(item.subtotal, safeNumber(item.product?.price) * safeNumber(item.quantity)), 0) + localItems.reduce((total, item) => total + safeNumber(item.product.price) * safeNumber(item.quantity), 0);
    const empty = items.length === 0;
    setMain(`
      <section class='cart-page'>
        <div class='cart-layout ${empty ? 'empty-cart-layout' : ''}'>
          <section class='cart-main'>
            ${empty ? `<div class='empty-cart-content'><img class='empty-cart-image' src='/static/assets/empty-cart.png' alt='Empty shopping cart'><div class='empty-cart-copy'><h1>Your Amazon Cart is empty</h1><a href='/gp/goldbox/'>Shop today's deals</a><div class='empty-actions'><a class='amazon-button amazon-button-primary' href='/account'>Sign in to your account</a><a class='amazon-button' href='/account?mode=register'>Sign up now</a></div></div></div>` : `<div class='cart-header'><h1>Shopping Cart</h1><span>Price</span></div>${items.map(cartItemMarkup).join('')}<div class='cart-subtotal-line'>Subtotal (${totalQuantity} ${totalQuantity === 1 ? 'item' : 'items'}): <strong>${money(subtotal)}</strong></div>`}
          </section>
          ${empty ? '' : `<aside class='cart-summary'><p>Subtotal (${totalQuantity} ${totalQuantity === 1 ? 'item' : 'items'}): <strong>${money(subtotal)}</strong></p><a class='amazon-button amazon-button-primary desktop-cart-primary' href='/checkout'>Proceed to checkout</a><a class='amazon-button amazon-button-primary mobile-cart-primary' href='/checkout'>Proceed to checkout</a></aside>`}
        </div>
        ${saved.length ? `<section class='saved-section'><h2>Saved for later</h2>${saved.map(savedItemMarkup).join('')}</section>` : `<section class='saved-section' aria-label='Saved for later'></section>`}
        <p class='cart-disclaimer'>The price and availability of items are subject to change. The Cart is a temporary place to store a list of your items and reflects each item's most recent price.</p>
      </section>`);
  }

  function renderLists() {
    document.title = 'Your Lists - Amazon.com';
    const pendingAsin = new URLSearchParams(window.location.search).get('asin');
    const pending = pendingAsin ? productByAsin(pendingAsin) : null;
    const wishlist = (state.wishlist || [])
      .map((entry) => ({
        ...entry,
        product: productByAsin(entry.asin) || (entry.product ? { ...entry.product, source: 'marketplace' } : null),
      }))
      .filter((entry) => entry.product);
    setMain(`
      <nav class='list-subnav' aria-label='Lists and registries'><a href='/hz/wishlist/ls'>Your Lists</a><a href='/hz/wishlist/ls?view=friends'>Your Friends</a><a href='/hz/wishlist/ls?view=registry'>Gift Finder</a><a href='/hz/wishlist/ls?view=baby'>Baby Registry</a><a href='/hz/wishlist/ls?view=wedding'>Wedding Registry</a></nav>
      <section class='lists-page'>
        ${wishlist.length ? `
          <div class='wishlist-shell'>
            <aside class='wishlist-sidebar'><h1>Your Lists</h1><button class='amazon-button amazon-button-primary' type='button' data-boundary='list'>Create a List</button><a class='active' href='/hz/wishlist/ls'>Shopping List <small>${wishlist.length} ${wishlist.length === 1 ? 'item' : 'items'}</small></a></aside>
            <section class='wishlist-content'>
              <header><div><h2>Shopping List</h2><p><i data-lucide='lock' aria-hidden='true'></i> Private · Local to this browser session</p></div><button class='icon-button' type='button' data-boundary='list' title='List settings' aria-label='List settings'><i data-lucide='more-horizontal'></i></button></header>
              ${pending && !wishlist.some((entry) => entry.asin === pending.asin) ? `<div class='pending-list-item'>${productImageMarkup(pending, 'pending-list-image')}<div><strong>${escapeHtml(pending.short_title)}</strong><button class='amazon-button amazon-button-primary' type='button' data-list-add='${escapeHtml(pending.asin)}'>Add to this List</button></div></div>` : ''}
              <div class='wishlist-grid'>${wishlist.map((entry) => `<article class='wishlist-item'>
                <a href='${productHref(entry.product)}'>${productImageMarkup(entry.product, 'wishlist-item-image')}</a>
                <div><a class='wishlist-item-title' href='${productHref(entry.product)}'>${escapeHtml(entry.product.title)}</a>${ratingMarkup(entry.product)}${priceMarkup(entry.product.price)}<p class='cart-stock'>In Stock</p></div>
                <div class='wishlist-item-actions'><button class='amazon-button amazon-button-primary' type='button' data-quick-add='${escapeHtml(entry.asin)}'>Add to Cart</button><button class='text-action' type='button' data-list-remove='${escapeHtml(entry.asin)}'>Delete</button></div>
              </article>`).join('')}</div>
            </section>
          </div>` : `
          <div class='lists-hero'><div class='lists-hero-products'>${catalogProducts().slice(1, 5).map((product) => productImageMarkup(product, 'lists-hero-image')).join('')}</div><h1>Lists <span>&amp; Registries</span></h1><p>for all your shopping needs</p><button class='amazon-button amazon-button-primary' type='button' data-boundary='list'>Sign In</button></div>
          ${pending ? `<div class='pending-list-item'>${productImageMarkup(pending, 'pending-list-image')}<div><strong>${escapeHtml(pending.short_title)}</strong><p>Save this item in a list local to your current shopping session.</p><button class='amazon-button amazon-button-primary' type='button' data-list-add='${escapeHtml(pending.asin)}'>Add to List</button></div></div>` : ''}
          <div class='list-type-grid'><article><i data-lucide='list'></i><h2>Shopping List</h2><p>Add items you want to shop for.</p></article><article><i data-lucide='gift'></i><h2>Wish List</h2><p>Keep gift ideas together in this session.</p></article></div>
          <section class='list-benefits'><h2>Lists</h2><div><article><i data-lucide='package-check'></i><strong>Stay organized</strong><p>Save your items and ideas in one convenient location</p></article><article><i data-lucide='users'></i><strong>Share controls stop safely</strong><p>Public sharing requires a real account and remains unavailable</p></article><article><i data-lucide='badge-percent'></i><strong>Save money</strong><p>Check deals and price drops on your saved items</p></article></div></section>`}
      </section>`);
  }

  function renderHistory() {
    document.title = 'Your Browsing History - Amazon.com';
    const recent = (state.recent_views || [])
      .map((entry) => productByAsin(entry.asin) || (entry.product ? { ...entry.product, source: 'marketplace' } : null))
      .filter(Boolean);
    setMain(`
      <nav class='local-tabs' aria-label='Browsing history navigation'><a class='active' href='/hz/history'>Browsing History</a><a href='/hz/wishlist/ls'>Your Lists</a><a href='/s?k=recommended'>Recommendations</a></nav>
      <section class='history-page'>
        <header><h1>Your Browsing History</h1><p>Products viewed in this local shopping session.</p></header>
        ${recent.length ? `<div class='history-grid'>${recent.map((product) => `<article class='history-item'><a href='${productHref(product)}'>${productImageMarkup(product, 'history-item-image')}</a><a href='${productHref(product)}'>${escapeHtml(product.title)}</a>${ratingMarkup(product)}${priceMarkup(product.price)}<button class='amazon-button amazon-button-primary' type='button' data-quick-add='${escapeHtml(product.asin)}'>Add to Cart</button></article>`).join('')}</div>` : `<div class='history-empty'><i data-lucide='history' aria-hidden='true'></i><h2>Your browsing history is empty.</h2><p>Products you view will appear here.</p><a class='amazon-button amazon-button-primary' href='/Best-Sellers/zgbs'>Explore Best Sellers</a></div>`}
      </section>`);
  }

  function accountCard(icon, title, copy, href) {
    const boundary = href.includes('kind=account') ? " data-boundary='account'" : '';
    return `<a class='account-card' href='${href}'${boundary}><span class='account-card-icon' aria-hidden='true'><i data-lucide='${icon}'></i></span><span><strong>${title}</strong><small>${copy}</small></span></a>`;
  }

  function renderAccountPage() {
    document.title = 'Your Account';
    const primary = [
      ['package-search', 'Your Orders', 'Track, return, cancel an order, download invoice or buy again', '/account/orders'],
      ['shield-check', 'Login & security', 'Edit login, name, and mobile number', '/local-boundary?kind=account'],
      ['badge-check', 'Prime', 'Manage your membership, view benefits, and payment settings', '/local-boundary?kind=service'],
      ['house', 'Your Addresses', 'Edit, remove or set default address', '/local-boundary?kind=delivery'],
      ['briefcase-business', 'Your business account', 'Sign up to save with business-exclusive pricing and delivery options', '/local-boundary?kind=service'],
      ['gift', 'Gift cards', 'View balance or redeem a card, and purchase a new Gift Card', '/local-boundary?kind=service'],
      ['wallet-cards', 'Your Payments', 'View all transactions, manage payment methods and settings', '/local-boundary?kind=payment'],
      ['users-round', 'Your Amazon Family', 'Manage profiles, sharing, and permissions in one place', '/local-boundary?kind=service'],
      ['tablet-smartphone', 'Digital Services and Device Support', 'Troubleshoot device issues, manage or cancel digital subscriptions', '/local-boundary?kind=service'],
      ['list-checks', 'Your Lists', 'View, modify, and share your lists, or create new ones', '/hz/wishlist/ls'],
      ['headset', 'Customer Service', 'Browse self service options, help articles or contact us', '/account?view=help'],
      ['mail', 'Your Messages', 'View or respond to messages from Amazon, Sellers and Buyers', '/local-boundary?kind=service'],
    ];
    setMain(`
      <section class='account-page'>
        <h1>Your Account</h1>
        <div class='account-card-grid'>${primary.map((item) => accountCard(...item)).join('')}</div>
        <div class='account-link-grid'>
          <section><h2>Ordering and shopping preferences</h2><a href='/local-boundary?kind=delivery'>Your Addresses</a><a href='/local-boundary?kind=payment'>Your Payments</a><a href='/hz/wishlist/ls'>Your Lists</a><a href='/hz/history'>Your browsing history</a></section>
          <section><h2>Digital content and devices</h2><a href='/local-boundary?kind=service'>Manage digital content</a><a href='/local-boundary?kind=service'>Digital delivery settings</a><a href='/local-boundary?kind=service'>Apps and devices</a></section>
          <section><h2>Memberships and subscriptions</h2><a href='/local-boundary?kind=service'>Prime membership</a><a href='/local-boundary?kind=service'>Subscriptions</a><a href='/local-boundary?kind=service'>Membership settings</a></section>
        </div>
      </section>`);
  }

  function renderSafeBoundaryPage(kind) {
    document.title = 'Your Orders - Amazon.com';
    setMain(`
      <section class='safe-page'>
        <a class='amazon-logo safe-logo' href='/'>amazon</a>
        <div class='safe-panel'>
          <i data-lucide='package-search'></i>
          <h1>Your Orders</h1>
          <p>Sign in would be required to view order history. No order or account data is collected here.</p>
          <button class='amazon-button amazon-button-primary' type='button' data-boundary='${kind}'>Sign in to view orders</button>
          <a href='/'>Return to shopping</a>
        </div>
      </section>`);
  }

  function renderNotFound() {
    document.title = 'Page Not Found - Amazon.com';
    setMain(`<section class='not-found'><div class='not-found-mark' aria-hidden='true'>404</div><div><h1>Sorry, we couldn't find that page.</h1><p>Try searching or go back to the <a href='/'>Amazon home page</a>.</p></div></section>`);
  }

  function boundaryKind(pathname) {
    if (pathname.startsWith('/checkout/payment')) return 'payment';
    if (pathname.startsWith('/buy-now')) return 'buy-now';
    if (pathname.startsWith('/local-boundary')) return new URLSearchParams(window.location.search).get('kind') || 'service';
    return '';
  }

  function boundaryCopy(kind) {
    const copy = {
      account: ['Account action unavailable', 'Sign-in, registration, and account changes are outside this public shopping preview.'],
      orders: ['Orders action unavailable', 'Order history requires an account and is not available here.'],
      list: ['Sign in required', 'Saving account lists is not enabled here. Your cart is unchanged.'],
      checkout: ['Checkout stops here', 'Checkout, delivery details, and order placement are not enabled. Your cart remains available.'],
      payment: ['Payment action unavailable', 'Payment methods and transactions are not enabled.'],
      'buy-now': ['Buy Now stops here', 'Buy Now and order placement are not enabled. Use Add to cart to update your cart.'],
      delivery: ['Delivery location is fixed', `This store uses ${state.session.delivery_label} for consistent availability and pricing.`],
      language: ['Language selection unavailable', 'This storefront is presented in English for the current session.'],
      returns: ['Returns information only', 'Returns and replacements cannot be started from this storefront.'],
      service: ['This action stops here', 'This service is outside the available shopping flow.'],
    };
    return copy[kind] || copy.service;
  }

  async function openBoundary(kind) {
    const [title, message] = boundaryCopy(kind);
    document.getElementById('boundary-title').textContent = title;
    document.getElementById('boundary-message').textContent = message;
    if (!boundaryDialog.open) boundaryDialog.showModal();
    createIcons();
    try {
      const response = await fetch('/api/boundary', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ kind: kind === 'orders' ? 'account' : kind }) });
      await response.json();
    } catch {
      // The local visible boundary remains effective without its optional audit call.
    }
  }

  async function savePreference(kind) {
    try {
      const response = await fetch('/api/session/preferences', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ kind }) });
      await response.json();
    } catch {
      // Preference APIs are optional; the local boundary remains the fallback.
    }
  }

  async function renderRoute() {
    const path = window.location.pathname;
    const kind = boundaryKind(path);
    if (kind) {
      if (kind === 'checkout' || kind === 'payment') renderCart();
      else if (kind === 'buy-now') {
        const product = productByAsin(new URLSearchParams(window.location.search).get('asin')) || productByAsin(TARGET_ASIN);
        product.asin === TARGET_ASIN ? renderProduct() : renderGenericProduct(product);
      } else renderHome();
      window.setTimeout(() => openBoundary(kind), 0);
      return;
    }
    if (path === '/') renderHome();
    else if (path === BEST_SELLERS_PATH) renderBestSellers();
    else if (path === BEST_SELLERS_ROOT || path === `${BEST_SELLERS_ROOT}/`) renderBestSellersRoot();
    else if (path === PRODUCT_PATH || path === MOBILE_PRODUCT_PATH) renderProduct();
    else if (/\/dp\/[A-Z0-9]{10,11}\/?$/.test(path)) {
      const asin = path.match(/\/dp\/([A-Z0-9]{10,11})\/?$/)?.[1];
      const product = productByAsin(asin);
      if (!product) renderNotFound();
      else if (asin === TARGET_ASIN) renderProduct();
      else renderGenericProduct(product);
    } else if (path === CART_PATH) renderCart();
    else if (path === '/s') await renderSearchRoute();
    else if (path === '/gp/goldbox' || path === '/gp/goldbox/') renderDeals();
    else if (path === '/b' || /\/b\/?$/.test(path)) renderComputers();
    else if (path.startsWith('/hz/wishlist')) renderLists();
    else if (path === '/hz/history') renderHistory();
    else if (path === '/account') renderAccountPage();
    else if (path === '/account/orders') renderSafeBoundaryPage('orders');
    else renderNotFound();
  }

  function normalizeBootstrap(payload) {
    if (!payload || typeof payload !== 'object') throw new Error('Store data is invalid');
    return {
      session: payload.session && typeof payload.session === 'object' ? payload.session : fallbackData.session,
      products: Array.isArray(payload.products) ? payload.products : [],
      cart: payload.cart && typeof payload.cart === 'object' ? payload.cart : fallbackData.cart,
      discovery: payload.discovery && typeof payload.discovery === 'object' ? payload.discovery : fallbackData.discovery,
      saved_for_later: Array.isArray(payload.saved_for_later) ? payload.saved_for_later : [],
      wishlist: Array.isArray(payload.wishlist) ? payload.wishlist : [],
      recent_views: Array.isArray(payload.recent_views) ? payload.recent_views : [],
      search_history: Array.isArray(payload.search_history) ? payload.search_history : [],
    };
  }

  async function loadSiteCatalog() {
    try {
      const response = await fetch('/static/site-catalog.json', { headers: { Accept: 'application/json' }, cache: 'no-store' });
      if (!response.ok) throw new Error(`Catalog returned ${response.status}`);
      const payload = await response.json();
      if (!payload || !Array.isArray(payload.products)) throw new Error('Catalog is invalid');
      state.siteCatalog = payload;
    } catch (error) {
      state.apiError = error instanceof Error ? error.message : 'Catalog could not be loaded';
    }
  }

  async function loadBootstrap(showLoading = false) {
    if (showLoading) renderLoading('Refreshing store');
    try {
      const response = await fetch('/api/bootstrap', { headers: { Accept: 'application/json' }, cache: 'no-store' });
      if (!response.ok) throw new Error(`Store data returned ${response.status}`);
      Object.assign(state, normalizeBootstrap(await response.json()), { usingFallback: false });
    } catch (error) {
      Object.assign(state, fallbackData, { apiError: error instanceof Error ? error.message : 'Store data could not be loaded', usingFallback: true });
    }
  }

  function showToast(message, isError = false) {
    window.clearTimeout(state.toastTimer);
    toast.textContent = message;
    toast.classList.toggle('error', isError);
    toast.classList.add('show');
    state.toastTimer = window.setTimeout(() => toast.classList.remove('show'), 3200);
  }

  function setBusy(busy) {
    state.busy = busy;
    document.querySelectorAll('[data-cart-quantity], [data-remove], [data-save], [data-move-to-cart]').forEach((control) => {
      control.disabled = busy;
    });
  }

  async function cartMutation(url, options, successMessage) {
    if (state.busy) return;
    setBusy(true);
    try {
      const response = await fetch(url, { ...options, headers: { Accept: 'application/json', ...(options.headers || {}) } });
      if (!response.ok) {
        let message = 'The cart could not be updated.';
        try {
          const payload = await response.json();
          if (payload.error) message = payload.error;
        } catch {
          // Keep the stable fallback message.
        }
        throw new Error(message);
      }
      await response.json();
      await loadBootstrap(false);
      renderHeader();
      renderCart();
      showToast(successMessage);
    } catch (error) {
      showToast(error instanceof Error ? error.message : 'The cart could not be updated.', true);
    } finally {
      setBusy(false);
    }
  }

  async function quickAdd(asin, quantity = 1) {
    const product = productByAsin(asin);
    if (!product) return;
    const safeQuantity = Math.min(3, Math.max(1, safeNumber(quantity, 1)));
    try {
      const response = await fetch('/api/cart/add', { method: 'POST', headers: { Accept: 'application/json', 'Content-Type': 'application/json' }, body: JSON.stringify({ asin, quantity: safeQuantity }) });
      if (!response.ok) throw new Error('Local fallback');
      await response.json();
      await loadBootstrap(false);
    } catch {
      state.localCart[asin] = Math.min(3, safeNumber(state.localCart[asin]) + safeQuantity);
      writeLocalCart();
    }
    renderHeader();
    showToast(`Added ${safeQuantity} to Cart`);
  }

  async function addToList(asin) {
    try {
      const response = await fetch('/api/list', { method: 'POST', headers: { Accept: 'application/json', 'Content-Type': 'application/json' }, body: JSON.stringify({ asin }) });
      if (!response.ok) throw new Error('List sign-in required');
      await response.json();
      await loadBootstrap(false);
      if (window.location.pathname.startsWith('/hz/wishlist')) renderLists();
      showToast('Added to List');
    } catch {
      openBoundary('list');
    }
  }

  async function removeFromList(asin) {
    try {
      const response = await fetch(`/api/list/${encodeURIComponent(asin)}`, { method: 'DELETE', headers: { Accept: 'application/json' } });
      if (!response.ok) throw new Error('List could not be updated');
      await response.json();
      await loadBootstrap(false);
      renderLists();
      showToast('Deleted from List');
    } catch (error) {
      showToast(error instanceof Error ? error.message : 'List could not be updated.', true);
    }
  }

  function closeBoundary() {
    if (boundaryDialog.open) boundaryDialog.close();
    const kind = boundaryKind(window.location.pathname);
    if (!kind) return;
    const destination = kind === 'payment' ? CART_PATH : kind === 'buy-now' ? PRODUCT_PATH : '/';
    window.location.assign(destination);
  }

  function recentSearches() {
    try {
      const values = JSON.parse(window.localStorage.getItem('amazon-recent-searches') || '[]');
      return Array.isArray(values) ? values.slice(0, 4) : [];
    } catch {
      return [];
    }
  }

  function rememberSearch(query) {
    const cleaned = String(query || '').trim();
    if (!cleaned) return;
    const next = [cleaned, ...recentSearches().filter((value) => value.toLowerCase() !== cleaned.toLowerCase())].slice(0, 5);
    window.localStorage.setItem('amazon-recent-searches', JSON.stringify(next));
  }

  function localSuggestions(query) {
    const cleaned = query.trim().toLowerCase();
    const results = [];
    recentSearches().filter((value) => !cleaned || value.toLowerCase().includes(cleaned)).forEach((label) => results.push({ label, type: 'recent', href: `/s?k=${encodeURIComponent(label)}` }));
    (state.siteCatalog.trendingSearches || []).filter((value) => !cleaned || value.includes(cleaned)).slice(0, cleaned ? 3 : 5).forEach((label) => results.push({ label, type: 'trending', href: `/s?k=${encodeURIComponent(label)}` }));
    if (cleaned) {
      allProducts().filter((product) => searchHaystack(product).includes(cleaned)).slice(0, 4).forEach((product) => results.push({ label: product.short_title || product.title, type: 'product', href: productHref(product), product }));
      if (!results.some((item) => item.label.toLowerCase() === cleaned)) results.unshift({ label: query.trim(), type: 'search', href: `/s?k=${encodeURIComponent(query.trim())}` });
    }
    return results.slice(0, 8);
  }

  function showSuggestions(input, suggestions) {
    const form = input.closest('[data-search-form]');
    const panel = form?.querySelector('.autocomplete-panel');
    if (!panel) return;
    panel.innerHTML = suggestions.map((suggestion, index) => `
      <a href='${suggestion.href}' role='option' data-suggestion-index='${index}' data-suggestion-label='${escapeHtml(suggestion.label)}'>
        <i data-lucide='${suggestion.type === 'recent' ? 'history' : suggestion.type === 'product' ? 'package' : suggestion.type === 'trending' ? 'trending-up' : 'search'}' aria-hidden='true'></i>
        ${suggestion.product ? productImageMarkup(suggestion.product, 'suggestion-image') : ''}
        <span>${escapeHtml(suggestion.label)}</span>
        <small>${suggestion.type === 'product' ? escapeHtml(suggestion.product.category) : suggestion.type}</small>
      </a>`).join('');
    panel.classList.toggle('open', suggestions.length > 0);
    input.setAttribute('aria-expanded', suggestions.length > 0 ? 'true' : 'false');
    input.dataset.activeSuggestion = '-1';
    createIcons();
  }

  async function refreshSuggestions(input) {
    const query = input.value.slice(0, 80);
    let suggestions = localSuggestions(query);
    showSuggestions(input, suggestions);
    try {
      const response = await fetch(`/api/suggestions?q=${encodeURIComponent(query)}`, { headers: { Accept: 'application/json' } });
      if (!response.ok) return;
      const payload = await response.json();
      const remote = Array.isArray(payload.suggestions) ? payload.suggestions : [];
      remote.forEach((label) => {
        if (typeof label === 'string' && !suggestions.some((item) => item.label === label)) suggestions.push({ label, type: 'search', href: `/s?k=${encodeURIComponent(label)}` });
      });
      showSuggestions(input, suggestions.slice(0, 8));
    } catch {
      // Local suggestions are the expected fallback.
    }
  }

  function closeSuggestions(exceptForm = null) {
    document.querySelectorAll('.autocomplete-panel.open').forEach((panel) => {
      if (exceptForm && panel.closest('form') === exceptForm) return;
      panel.classList.remove('open');
      panel.closest('form')?.querySelector('input[type=search]')?.setAttribute('aria-expanded', 'false');
    });
  }

  function updateSearchFilter(control) {
    if (!state.searchUi) return;
    const type = control.dataset.searchFilter;
    if (type === 'department' || type === 'price') state.searchUi.filters[type] = control.value;
    else state.searchUi.filters[type] = control.checked;
    state.searchUi.page = 1;
    renderSearchFromState();
  }

  document.addEventListener('click', (event) => {
    const suggestion = event.target.closest('[data-suggestion-index]');
    if (suggestion) rememberSearch(suggestion.dataset.suggestionLabel);
    const target = event.target.closest('button, a');
    if (!target) {
      closeSuggestions();
      return;
    }
    if (target.matches('[data-open-menu]')) {
      if (!menuDialog.open) menuDialog.showModal();
      closeSuggestions();
      createIcons();
    } else if (target.matches('[data-close-menu]')) {
      menuDialog.close();
    } else if (target.matches('[data-close-dialog]')) {
      closeBoundary();
    } else if (target.matches('[data-back-to-top]')) {
      window.scrollTo({ top: 0, behavior: 'smooth' });
    } else if (target.matches('[data-retry]')) {
      Promise.all([loadSiteCatalog(), loadBootstrap(true)]).then(() => {
        renderHeader();
        renderDrawer();
        renderRoute();
      });
    } else if (target.matches('[data-remove]')) {
      cartMutation(`/api/cart/${encodeURIComponent(target.dataset.remove)}`, { method: 'DELETE' }, 'Removed from Cart');
    } else if (target.matches('[data-save]')) {
      cartMutation(`/api/cart/${encodeURIComponent(target.dataset.save)}/save-for-later`, { method: 'POST' }, 'Saved for later');
    } else if (target.matches('[data-move-to-cart]')) {
      cartMutation(`/api/cart/${encodeURIComponent(target.dataset.moveToCart)}/move-to-cart`, { method: 'POST' }, 'Moved to Cart');
    } else if (target.matches('[data-local-remove]')) {
      delete state.localCart[target.dataset.localRemove];
      writeLocalCart();
      renderHeader();
      renderCart();
      showToast('Removed from Cart');
    } else if (target.matches('[data-variant]')) {
      const group = target.closest('.variant-options');
      group.querySelectorAll('.variant-option').forEach((button) => button.classList.toggle('selected', button === target));
      const label = group.closest('.variant-block')?.querySelector('[data-variant-value]');
      if (label) label.textContent = target.dataset.variant;
    } else if (target.matches('[data-gallery-state]')) {
      const gallery = target.closest('.product-gallery, .generic-gallery');
      gallery?.querySelectorAll('[data-gallery-state]').forEach((button) => button.classList.toggle('selected', button === target));
      const image = gallery?.querySelector('.gallery-main');
      if (image) image.dataset.gallery = target.dataset.galleryState;
    } else if (target.matches('[data-quick-add]')) {
      const quantity = target.closest('.generic-buy-box')?.querySelector('[data-generic-quantity]')?.value || 1;
      quickAdd(target.dataset.quickAdd, quantity);
    } else if (target.matches('[data-list-add]')) {
      addToList(target.dataset.listAdd);
    } else if (target.matches('[data-list-remove]')) {
      removeFromList(target.dataset.listRemove);
    } else if (target.matches('[data-boundary]')) {
      openBoundary(target.dataset.boundary);
    } else if (target.matches('[data-open-filters]')) {
      if (filterDialog && !filterDialog.open) filterDialog.showModal();
    } else if (target.matches('[data-close-filters]')) {
      if (filterDialog?.open) filterDialog.close();
    } else if (target.matches('[data-search-page]')) {
      state.searchUi.page = safeNumber(target.dataset.searchPage, 1);
      renderSearchFromState();
      window.scrollTo({ top: 100, behavior: 'smooth' });
    } else if (target.matches('[data-chip-filter]')) {
      const type = target.dataset.chipFilter;
      if (type === 'prime' || type === 'rating') state.searchUi.filters[type] = !state.searchUi.filters[type];
      else if (filterDialog && !filterDialog.open) filterDialog.showModal();
      if (type === 'prime' || type === 'rating') renderSearchFromState();
    } else if (target.matches('[data-preference]')) {
      savePreference(target.dataset.preference);
    } else if (!target.closest('[data-search-form]')) {
      closeSuggestions();
    }
  });

  document.addEventListener('change', (event) => {
    const searchFilter = event.target.closest('[data-search-filter]');
    if (searchFilter) {
      updateSearchFilter(searchFilter);
      return;
    }
    const sort = event.target.closest('[data-search-sort]');
    if (sort) {
      state.searchUi.sort = sort.value;
      state.searchUi.page = 1;
      renderSearchFromState();
      return;
    }
    const localSelect = event.target.closest('[data-local-cart-quantity]');
    if (localSelect) {
      state.localCart[localSelect.dataset.localCartQuantity] = safeNumber(localSelect.value, 1);
      writeLocalCart();
      renderHeader();
      renderCart();
      showToast('Cart updated');
      return;
    }
    const select = event.target.closest('[data-cart-quantity]');
    if (!select) return;
    const quantity = Number(select.value);
    if (![1, 2, 3].includes(quantity)) {
      showToast('Choose a quantity from 1 to 3.', true);
      renderCart();
      return;
    }
    cartMutation(`/api/cart/${encodeURIComponent(select.dataset.cartQuantity)}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ quantity }) }, 'Cart updated');
  });

  document.addEventListener('input', (event) => {
    const input = event.target.closest('[data-search-form] input[type=search]');
    if (input) refreshSuggestions(input);
  });

  document.addEventListener('focusin', (event) => {
    const input = event.target.closest('[data-search-form] input[type=search]');
    if (input) {
      closeSuggestions(input.closest('form'));
      refreshSuggestions(input);
    }
  });

  document.addEventListener('keydown', (event) => {
    const input = event.target.closest('[data-search-form] input[type=search]');
    if (!input) return;
    const panel = input.closest('form').querySelector('.autocomplete-panel');
    const options = [...panel.querySelectorAll('[role=option]')];
    if (event.key === 'Escape') {
      panel.classList.remove('open');
      input.setAttribute('aria-expanded', 'false');
      return;
    }
    if (!['ArrowDown', 'ArrowUp', 'Enter'].includes(event.key) || !panel.classList.contains('open')) return;
    let active = safeNumber(input.dataset.activeSuggestion, -1);
    if (event.key === 'ArrowDown') active = (active + 1) % options.length;
    if (event.key === 'ArrowUp') active = (active - 1 + options.length) % options.length;
    if (event.key === 'Enter' && active >= 0) {
      event.preventDefault();
      rememberSearch(options[active].dataset.suggestionLabel);
      window.location.assign(options[active].href);
      return;
    }
    if (event.key !== 'Enter') {
      event.preventDefault();
      options.forEach((option, index) => option.classList.toggle('active', index === active));
      input.dataset.activeSuggestion = String(active);
      options[active]?.scrollIntoView({ block: 'nearest' });
    }
  });

  document.addEventListener('submit', (event) => {
    const searchForm = event.target.closest('[data-search-form]');
    if (searchForm) {
      rememberSearch(new FormData(searchForm).get('k'));
      return;
    }
    const form = event.target.closest('[data-add-form]');
    if (!form) return;
    const quantity = Number(new FormData(form).get('quantity'));
    if (![1, 2, 3].includes(quantity)) {
      event.preventDefault();
      showToast('Choose a quantity from 1 to 3.', true);
      return;
    }
    if (quantity !== 2 || !state.discovery.best_sellers_viewed || !state.discovery.product_views?.includes(TARGET_ASIN)) {
      event.preventDefault();
      quickAdd(TARGET_ASIN, quantity);
      return;
    }
    window.sessionStorage.setItem('amazon-add-pending', JSON.stringify({ asin: TARGET_ASIN, quantity, at: Date.now() }));
  });

  boundaryDialog.addEventListener('cancel', (event) => {
    event.preventDefault();
    closeBoundary();
  });

  menuDialog.addEventListener('click', (event) => {
    if (event.target === menuDialog) menuDialog.close();
  });

  if (filterDialog) {
    filterDialog.addEventListener('click', (event) => {
      if (event.target === filterDialog) filterDialog.close();
    });
  }

  async function init() {
    // Authentication, checkout, and order pages are rendered by the commerce
    // adapter.  Re-running the catalogue renderer here would replace their
    // forms with the historical external-boundary placeholder.
    if (document.documentElement.dataset.serverOwned === 'true') {
      createIcons();
      return;
    }
    renderHeader();
    renderFooter();
    renderDrawer();
    await Promise.all([loadSiteCatalog(), loadBootstrap(false)]);
    renderHeader();
    renderDrawer();
    await renderRoute();
    const pendingRaw = window.sessionStorage.getItem('amazon-add-pending');
    if (pendingRaw) {
      try {
        const pending = JSON.parse(pendingRaw);
        const inCart = state.cart.items?.some((item) => item.asin === pending.asin && safeNumber(item.quantity) >= safeNumber(pending.quantity));
        if (inCart) {
          showToast(`Added ${pending.quantity} to Cart`);
          window.sessionStorage.removeItem('amazon-add-pending');
        } else if (Date.now() - safeNumber(pending.at) > 120000) {
          window.sessionStorage.removeItem('amazon-add-pending');
        }
      } catch {
        window.sessionStorage.removeItem('amazon-add-pending');
      }
    }
  }

  init();
})();
