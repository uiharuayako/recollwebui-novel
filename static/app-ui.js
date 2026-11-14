/* Progressive enhancement; search and navigation remain usable without JavaScript. */
(() => {
  'use strict';
  const reader = document.getElementById('reader-app');
  const main = document.querySelector('main') || document.getElementById('settings-box');
  if (main && !main.id) main.id = 'main-content';
  if (main && !main.hasAttribute('tabindex')) main.tabIndex = -1;
  const skip = document.querySelector('.app-skip-link');
  if (skip && main) skip.href = '#' + main.id;
  const query = document.getElementById('library-query');
  const menuSelectors = '.app-user-menu[open], .app-result-more[open], .app-export[open], .app-search-help[open]';
  const closeMenus = (except, restore) => document.querySelectorAll(menuSelectors).forEach(menu => {
    if (menu.contains(except)) return;
    menu.open = false;
    if (restore) menu.querySelector('summary')?.focus();
  });
  document.addEventListener('click', event => closeMenus(event.target, false));
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') closeMenus(null, true);
    if (reader || !query || event.defaultPrevented || event.isComposing || event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.target.closest('input, textarea, select, button, summary, [contenteditable="true"], [role="textbox"], dialog')) return;
    if (event.key === '/') {
      event.preventDefault();
      query.focus();
      query.select();
    }
  });
  // Avoid a mobile keyboard on arrival and do not steal focus from result links.
  if (query && (matchMedia('(max-width: 700px)').matches || document.getElementById('results'))) {
    query.removeAttribute('autofocus');
    if (document.activeElement === query) query.blur();
  }
  const form = document.getElementById('library-search');
  const submit = form?.querySelector('[type="submit"]');
  const restoreSearch = () => {
    if (!submit) return;
    submit.disabled = false;
    submit.innerHTML = '搜索文库<span aria-hidden="true"> →</span>';
    form.removeAttribute('aria-busy');
  };
  form?.addEventListener('submit', () => {
    submit.disabled = true;
    submit.textContent = '正在搜索…';
    form.setAttribute('aria-busy', 'true');
  });
  window.addEventListener('pageshow', restoreSearch);
  document.querySelectorAll('form[data-recommendation-form]').forEach(recommendationForm => {
    recommendationForm.addEventListener('submit', async event => {
      const account = window.RecollAccount;
      if (!account) return;
      event.preventDefault();
      const button = recommendationForm.querySelector('button[type="submit"]');
      if (button.disabled) return;
      const remove = button.getAttribute('aria-pressed') === 'true';
      const action = remove ? 'remove' : 'add';
      const values = new FormData(recommendationForm);
      const container = recommendationForm.closest('.app-recommendation-actions, .search-result-links');
      let status = container.querySelector('.app-recommendation-status');
      if (!status) {
        status = document.createElement('p');
        status.className = 'app-recommendation-status';
        status.setAttribute('role', 'status');
        container.appendChild(status);
      }
      button.disabled = true;
      recommendationForm.setAttribute('aria-busy', 'true');
      status.textContent = '正在保存…';
      status.dataset.error = 'false';
      try {
        const response = await account.fetch('/api/recommendations/' + action, {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({document_id: values.get('document_id'), handle: values.get('handle'), source_query: values.get('source_query') || ''}),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.message || data.error || '搜索推荐未保存，请稍后重试。');
        const saved = data.saved;
        button.setAttribute('aria-pressed', String(saved));
        button.textContent = saved ? '移出搜索推荐' : '加入搜索推荐';
        recommendationForm.action = account.url('/recommendations/' + (saved ? 'remove' : 'add'));
        status.textContent = saved ? '已加入搜索推荐，回首页可以看到。' : '已移出搜索推荐。';
      } catch (error) {
        status.textContent = error.message || '搜索推荐未保存，请稍后重试。';
        status.dataset.error = 'true';
      } finally {
        button.disabled = false;
        recommendationForm.removeAttribute('aria-busy');
      }
    });
  });
})();
