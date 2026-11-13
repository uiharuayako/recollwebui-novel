import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const source = await readFile(new URL('../static/reader/settings.js', import.meta.url), 'utf8');
const { normalizeReaderSettings } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

test('reader settings reject arbitrary CSS values and constrain layout dimensions', () => {
  const values = normalizeReaderSettings({ reader_theme: 'url(evil)', reader_font_family: 'custom-css',
    reader_content_width: 50000, reader_line_height: 1, reader_indent: true, reader_paragraph_spacing: -10 });
  assert.equal(values.reader_theme, 'sepia');
  assert.equal(values.reader_font_family, 'serif');
  assert.equal(values.reader_content_width, 1040);
  assert.equal(values.reader_line_height, 140);
  assert.equal(values.reader_paragraph_spacing, 0);
  assert.equal(values.reader_indent, 1);
});

test('missing settings preserve defaults while supported preferences survive reload', () => {
  const values = normalizeReaderSettings({ font_size: 24, reader_theme: 'dark', reader_line_height: 195,
    reader_text_align: 'justify', reader_preserve_publisher: 1 });
  assert.equal(values.reader_theme, 'dark');
  assert.equal(values.reader_line_height, 195);
  assert.equal(values.reader_text_align, 'justify');
  assert.equal(values.reader_content_width, 760);
  assert.equal(values.reader_preserve_publisher, 1);
});
