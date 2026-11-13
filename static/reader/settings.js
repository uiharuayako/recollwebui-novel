export const READER_DEFAULTS = Object.freeze({
  reader_theme: 'sepia', reader_font_family: 'serif', reader_line_height: 180,
  reader_paragraph_spacing: 12, reader_content_width: 760,
  reader_text_align: 'left', reader_indent: 0, reader_preserve_publisher: 0,
});

const choices = {
  reader_theme: ['light', 'sepia', 'dark', 'auto'],
  reader_font_family: ['serif', 'sans', 'system'], reader_text_align: ['left', 'justify'],
};
const ranges = { reader_line_height: [140, 220], reader_paragraph_spacing: [0, 30],
  reader_content_width: [480, 1040], reader_indent: [0, 1], reader_preserve_publisher: [0, 1] };

export function normalizeReaderSettings(input = {}) {
  const result = { ...READER_DEFAULTS };
  for (const [key, options] of Object.entries(choices)) {
    if (options.includes(input[key])) result[key] = input[key];
  }
  for (const [key, [min, max]] of Object.entries(ranges)) {
    const value = Number(input[key]);
    if (input[key] !== undefined && Number.isFinite(value)) result[key] = Math.max(min, Math.min(max, Math.round(value)));
  }
  return result;
}

export class ReaderSettings {
  constructor({ root, preferences, onBeforeChange = () => {}, onChange = () => {}, onSave = async () => {} }) {
    this.root = root;
    this.values = normalizeReaderSettings(preferences);
    this.onChange = onChange;
    this.onSave = onSave;
    this.panel = document.getElementById('reader-settings-panel');
    this.toggle = document.getElementById('reader-toggle-settings');
    this.pending = {};
    this.saveQueue = Promise.resolve();
    for (const key of Object.keys(READER_DEFAULTS)) {
      const control = document.getElementById(key.replaceAll('_', '-'));
      control?.addEventListener('change', () => {
        onBeforeChange();
        const value = control.type === 'checkbox' ? Number(control.checked)
          : key in ranges ? Number(control.value) : control.value;
        this.values = normalizeReaderSettings({ ...this.values, [key]: value });
        this.pending[key] = this.values[key];
        this.render();
        void this.onChange(key, this.values);
        clearTimeout(this.saveTimer);
        this.saveTimer = setTimeout(() => this.flush(), 250);
      });
      if (control?.type === 'range') control.addEventListener('input', () => this.renderOutput(key, control.value));
    }
    this.toggle?.addEventListener('click', () => this.setOpen(this.panel?.hidden !== false));
    document.getElementById('reader-close-settings')?.addEventListener('click', () => this.setOpen(false));
    document.getElementById('reader-settings-backdrop')?.addEventListener('click', () => this.setOpen(false));
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && this.panel?.hidden === false) { event.preventDefault(); this.setOpen(false); }
    });
    this.systemTheme = window.matchMedia('(prefers-color-scheme: dark)');
    this.systemTheme.addEventListener?.('change', () => {
      if (this.values.reader_theme === 'auto') { onBeforeChange(); this.render(); void this.onChange('reader_theme', this.values); }
    });
    this.render();
  }

  setOpen(open) {
    if (!this.panel) return;
    this.panel.hidden = !open;
    this.toggle?.setAttribute('aria-expanded', String(open));
    const backdrop = document.getElementById('reader-settings-backdrop');
    if (backdrop) backdrop.hidden = !open;
    if (open) this.panel.querySelector('button,select,input')?.focus();
    else this.toggle?.focus();
  }

  renderOutput(key, value) {
    const output = document.getElementById(key.replaceAll('_', '-') + '-value');
    const text = key === 'reader_line_height' ? (Number(value) / 100).toFixed(2) : `${value}px`;
    if (output) output.textContent = text;
    const control = document.getElementById(key.replaceAll('_', '-'));
    if (control?.type === 'range') control.setAttribute('aria-valuetext', key === 'reader_line_height' ? `${text} 倍行距` : text);
  }

  render() {
    this.root.dataset.theme = this.values.reader_theme;
    this.root.style.setProperty('--reader-content-width', `${this.values.reader_content_width}px`);
    for (const [key, value] of Object.entries(this.values)) {
      const control = document.getElementById(key.replaceAll('_', '-'));
      if (control?.type === 'checkbox') control.checked = !!value;
      else if (control) control.value = String(value);
      this.renderOutput(key, value);
    }
  }

  replacePreferences(preferences) {
    this.values = normalizeReaderSettings({ ...preferences, ...this.pending });
    this.render();
  }

  async flush(options = {}) {
    clearTimeout(this.saveTimer);
    const pending = this.pending;
    this.pending = {};
    if (!Object.keys(pending).length) return this.saveQueue;
    this.saveQueue = this.saveQueue.catch(() => {}).then(() => this.onSave(pending, options));
    return this.saveQueue;
  }

  colors() {
    const theme = this.values.reader_theme === 'auto' ? (this.systemTheme.matches ? 'dark' : 'light') : this.values.reader_theme;
    return theme === 'dark' ? { text: '#dbe3d9', muted: '#9aa89d', link: '#a2c7a8', background: '#1c211f' }
      : theme === 'light' ? { text: '#29342d', muted: '#6d776f', link: '#3e7154', background: '#fcfcfa' }
      : { text: '#36382f', muted: '#737568', link: '#48735b', background: '#f5f0e6' };
  }

  contentStyle(fontSize) {
    const p = this.values, color = this.colors();
    const families = { serif: '"Noto Serif CJK SC", "Source Han Serif SC", "Songti SC", "SimSun", Georgia, serif',
      sans: '"Noto Sans CJK SC", "Microsoft YaHei", "PingFang SC", Arial, sans-serif',
      system: 'system-ui, -apple-system, "Segoe UI", sans-serif' };
    const dark = p.reader_theme === 'dark' || (p.reader_theme === 'auto' && this.systemTheme.matches);
    const base = `html,body{background:transparent!important;color:${color.text}!important;color-scheme:${dark ? 'dark' : 'light'};}
      body{max-width:${p.reader_content_width}px!important;margin-left:auto!important;margin-right:auto!important;text-rendering:optimizeLegibility;}
      img,svg,video,canvas{max-width:100%!important;height:auto;}img{cursor:zoom-in;}
      a:any-link{color:${color.link}!important;text-decoration-thickness:1px;text-underline-offset:.18em;}
      pre{overflow-x:auto!important;max-width:100%!important;white-space:pre!important;word-break:normal!important;overflow-wrap:normal!important;padding:1em;border-radius:8px;background:${color.text}0a;}
      code{font-family:ui-monospace,"SFMono-Regular",Consolas,monospace;font-size:.88em;}
      pre code{white-space:pre!important;overflow-wrap:normal!important;word-break:normal!important;}
      table{display:block;overflow-x:auto!important;max-width:100%!important;border-collapse:collapse;}td,th{min-width:4em;padding:.4em .65em;}
      blockquote{border-left:3px solid ${color.muted}55;padding-left:1em;margin-left:0;}
      ::selection{background:#91adce66;}mark[data-reader-match]{background:#e9c46a;color:#252320;border-radius:2px;}
      [data-reader-destination]{animation:reader-destination 1.8s ease-out;}@keyframes reader-destination{from{background:#e9c46a99}to{background:transparent}}
      p[data-reader-txt-line]{display:block!important;white-space:pre-wrap;min-height:1lh;}
      .singlePage{padding-bottom:40px!important;}`;
    if (p.reader_preserve_publisher) return base;
    return base + `
      body{font-family:${families[p.reader_font_family]}!important;font-size:${fontSize}px!important;line-height:${p.reader_line_height / 100}!important;overflow-wrap:anywhere;}
      p,li,dd,blockquote{font-size:inherit!important;line-height:inherit!important;text-align:${p.reader_text_align}!important;margin-top:0!important;margin-bottom:${p.reader_paragraph_spacing}px!important;}
      p{text-indent:${p.reader_indent ? '2em' : '0'}!important;}li p,blockquote p,td p{text-indent:0!important;}
      h1,h2,h3,h4,h5,h6{font-family:inherit;line-height:1.4!important;margin:1.2em 0 .7em!important;text-wrap:pretty;}
      h1{font-size:1.65em!important;}h2{font-size:1.4em!important;}h3{font-size:1.2em!important;}
      body{letter-spacing:.015em;}pre,code,table{letter-spacing:normal;}ruby{ruby-position:over;}rt{font-size:.55em;}`;
  }
}
