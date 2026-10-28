// Run with NODE_PATH pointing to a directory containing jsdom@26.1.0.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { JSDOM } = require('jsdom');
const fs = require('node:fs');
const path = require('node:path');
const script = fs.readFileSync(path.join(__dirname, '../BrowserResources/TranslatePage.js'), 'utf8');
const whole = { x: 0, y: 0, width: 1, height: 1 };

function page(html) {
  const dom = new JSDOM(html, { runScripts: 'outside-only', pretendToBeVisual: true });
  const w = dom.window;
  Object.defineProperties(w, { innerWidth: { value: 400 }, innerHeight: { value: 800 } });
  const rect = element => {
    const top = Number(element?.dataset?.top ?? 80);
    return { left: 20, top, width: 160, height: 20, right: 180, bottom: top + 20 };
  };
  w.Range.prototype.getBoundingClientRect = function () { return rect(this.startContainer.parentElement); };
  w.Element.prototype.getBoundingClientRect = function () { return rect(this); };
  w.getComputedStyle = () => ({ display: 'block', visibility: 'visible', opacity: '1' });
  w.eval(script);
  return { w, document: w.document, api: w.__pdtTranslator, close: () => w.close() };
}

test('replaces only text, preserves button handlers, links and whitespace; restores original', () => {
  const p = page('<button>  加入购物车 </button><a href="/product">商品详情</a>');
  let clicked = 0;
  const button = p.document.querySelector('button');
  button.addEventListener('click', () => clicked++);
  const batch = p.api.collect(whole);
  assert.equal(batch.items.length, 2);
  assert.equal(p.api.apply(batch.documentID, batch.items.map(x => ({ ...x, source: x.text, target: 'Переведено' }))), 2);
  assert.equal(button.textContent, '  Переведено ');
  button.click();
  assert.equal(clicked, 1);
  assert.equal(p.document.querySelector('a').getAttribute('href'), '/product');
  assert.equal(p.api.collect(whole).items.length, 0);
  p.api.restore();
  assert.equal(button.textContent, '  加入购物车 ');
  p.close();
});

test('rejects stale document/source and detached nodes; respects website text changes', () => {
  const p = page('<button>购买商品</button><span>新商品</span>');
  const batch = p.api.collect(whole);
  const entry = { id: batch.items[0].id, source: batch.items[0].text, target: 'Купить' };
  assert.equal(p.api.apply('another-document', [entry]), 0);
  const node = p.document.querySelector('button').firstChild;
  node.nodeValue = '已售罄';
  assert.equal(p.api.apply(batch.documentID, [entry]), 0);
  const next = p.api.collect(whole);
  assert.equal(next.items[0].text, '已售罄');
  assert.equal(p.api.apply(next.documentID, [{ id: next.items[0].id, source: '已售罄', target: 'Продано' }]), 1);
  node.nodeValue = '已经下架';
  p.api.restore();
  assert.equal(node.nodeValue, '已经下架');
  p.document.querySelector('span').remove();
  assert.equal(p.api.apply(batch.documentID, [{ id: batch.items[1].id, source: '新商品', target: 'Новинка' }]), 0);
  p.close();
});

test('excludes form fields, editable content, scripts, SVG and opted-out content', () => {
  const p = page('<p>普通商品</p><input value="秘密内容"><textarea>个人地址</textarea>' +
    '<select><option>账户信息</option></select><div contenteditable>私人信息</div>' +
    '<script>const value="秘密"</script><svg><text>图中文字</text></svg><div translate="no">无需翻译</div>');
  const batch = p.api.collect(whole);
  assert.deepEqual(Array.from(batch.items, x => x.text), ['普通商品']);
  const info = p.api.snapshotInfo();
  assert.equal(info.privateRects.length, 4);
  assert.equal(JSON.stringify(info).includes('秘密'), false);
  p.close();
});

test('honors screen region and batch budget; discovers dynamically inserted text', () => {
  const p = page('<p data-top="80">上半部分</p><p data-top="600">下半部分</p>');
  assert.deepEqual(Array.from(p.api.collect({ x: 0, y: 0, width: 1, height: .5 }).items, x => x.text), ['上半部分']);
  assert.deepEqual(Array.from(p.api.collect({ x: 0, y: .5, width: 1, height: .5 }).items, x => x.text), ['下半部分']);
  p.document.body.insertAdjacentHTML('beforeend', '<p>更多商品</p>');
  assert.equal(p.api.collect(whole).items.length, 3);
  assert.equal(p.api.collect(whole, 1).items.length, 1);
  p.close();
});
