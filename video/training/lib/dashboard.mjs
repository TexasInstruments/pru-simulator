// Things every tour does on the dashboard, and the selectors they share.

/** Open a file of the server's source/ list in the Assembly Editor (Open button, then the list entry). */
export async function openFile(r, name) {
  await r.click('#btn-file');
  const item = `#_src-menu div:text-is("${name}")`;
  await r.reveal(item, '#_src-menu');
  await r.click(item);
  await r.until(`document.querySelector('#asm-source').value.length > 0 && !document.getElementById('_src-menu')`,
    { max: 5, what: `${name} shown in the Assembly Editor` });
}

/** Text of an element, whitespace collapsed. */
export const text = (page, selector) => page.locator(selector).first().innerText().then((t) => t.replace(/\s+/g, ' ').trim());

/** A register's value as shown in the Registers panel, e.g. "0x00000001". */
export const reg = (page, n) => text(page, `#reg-val-${n}`);

/** The Cycles counter of the focus core. */
export const cycles = (page) => text(page, '#cnt-cycles').then((t) => parseInt(t.replace(/\D/g, ''), 10));
