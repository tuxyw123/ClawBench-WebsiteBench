(() => {
  const menuButton = document.querySelector('.menu-button');
  const menu = document.querySelector('#category-menu');
  if (menuButton && menu) {
    menuButton.addEventListener('click', () => {
      const open = menuButton.getAttribute('aria-expanded') === 'true';
      menuButton.setAttribute('aria-expanded', String(!open));
      menu.classList.toggle('category-nav--open', !open);
    });
  }

  document.querySelectorAll('[data-busy-form]').forEach((form) => {
    form.addEventListener('submit', () => {
      const button = form.querySelector('button[type="submit"]');
      if (!button || button.disabled) return;
      button.disabled = true;
      button.setAttribute('aria-busy', 'true');
      button.dataset.originalLabel = button.textContent;
      button.textContent = 'Working…';
    });
  });

  const checkout = document.querySelector('[data-checkout]');
  if (checkout) {
    const subtotal = Number(checkout.dataset.subtotal);
    const shippingNode = checkout.querySelector('[data-shipping-total]');
    const taxNode = checkout.querySelector('[data-tax-total]');
    const grandNode = checkout.querySelector('[data-grand-total]');
    const format = (cents) => `$${(cents / 100).toFixed(2)}`;
    const update = () => {
      const method = checkout.querySelector('input[name="shipping_method"]:checked')?.value;
      const shipping = method === 'express' ? 1499 : (subtotal >= 7500 ? 0 : 599);
      const tax = Math.floor((subtotal * 825 + 5000) / 10000);
      shippingNode.textContent = shipping ? format(shipping) : 'Free';
      taxNode.textContent = format(tax);
      grandNode.textContent = format(subtotal + shipping + tax);
    };
    checkout.querySelectorAll('input[name="shipping_method"]').forEach((input) => input.addEventListener('change', update));
    update();
  }
})();

