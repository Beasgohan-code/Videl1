/* Local dashboard equivalents of the Telegram widget tools. No user input is sent to a server. */
(() => {
  "use strict";

  function calculate(source) {
    const s = source.trim().replace(/×/g, "*").replace(/÷/g, "/").replace(/−/g, "-");
    if (!s || s.length > 56 || !/^[0-9+*/.()\-\s]+$/.test(s)) throw Error("Use numbers and +, −, ×, ÷ or parentheses (56 characters max).");
    let i = 0, nodes = 0;
    const space = () => { while (i < s.length && /\s/.test(s[i])) i++; };
    const checked = (n) => {
      if (!Number.isFinite(n) || Math.abs(n) > 1e15) throw Error("Result is out of range.");
      return n;
    };
    function atom(depth) {
      if (++nodes > 64 || depth > 16) throw Error("Expression is too complex.");
      space();
      if (s[i] === "+" || s[i] === "-") { const op = s[i++]; const v = atom(depth + 1); return checked(op === "-" ? -v : v); }
      if (s[i] === "(") { i++; const v = sum(depth + 1); space(); if (s[i++] !== ")") throw Error("Close the parentheses."); return v; }
      const m = s.slice(i).match(/^(?:\d+(?:\.\d*)?|\.\d+)/);
      if (!m) throw Error("Check the expression.");
      i += m[0].length; return checked(Number(m[0]));
    }
    function product(depth) {
      let v = atom(depth);
      for (;;) { space(); const op = s[i]; if (op !== "*" && op !== "/") break; i++; const r = atom(depth); if (op === "/" && r === 0) throw Error("Cannot divide by zero."); v = checked(op === "*" ? v * r : v / r); }
      return v;
    }
    function sum(depth) {
      let v = product(depth);
      for (;;) { space(); const op = s[i]; if (op !== "+" && op !== "-") break; i++; const r = product(depth); v = checked(op === "+" ? v + r : v - r); }
      return v;
    }
    const value = sum(0); space();
    if (i !== s.length) throw Error("Check the expression.");
    return String(Number(value.toPrecision(12)));
  }

  const units = {
    distance: { a: "km", b: "mi", forward: v => v * 0.6213711922, back: v => v / 0.6213711922 },
    weight: { a: "kg", b: "lb", forward: v => v * 2.2046226218, back: v => v / 2.2046226218 },
    temperature: { a: "°C", b: "°F", forward: v => v * 9 / 5 + 32, back: v => (v - 32) * 5 / 9 },
    length: { a: "cm", b: "in", forward: v => v / 2.54, back: v => v * 2.54 },
    volume: { a: "L", b: "US gal", forward: v => v / 3.785411784, back: v => v * 3.785411784 },
  };

  function mount(root) {
    root.innerHTML = `<div class="toolspace">
      <header class="tool-hero"><div><p class="tool-kicker"><span></span> VIDEL STUDIO / EVERYDAY TOOLS</p><h1>Small tools.<br><em>More room to think.</em></h1><p>Calculator, bill splitter, converter and counter—ready when you need them.</p></div><div class="tool-privacy"><span class="tool-privacy-icon" aria-hidden="true">⌁</span><div><strong>Private by design</strong><p>Runs in this browser. Nothing here is saved or synced to Telegram.</p></div></div></header>
      <div class="tool-section-heading"><div><span>YOUR TOOLKIT</span><h2>Make quick work of it.</h2></div><p>Changes reset when you leave this page.</p></div>
      <div class="tool-grid">
        <section class="tool-card tool-calc" aria-labelledby="tool-calc-title"><div class="tool-card-head"><div><span class="tool-index">01 / CALCULATE</span><h3 id="tool-calc-title">Calculator</h3><p>A little clarity for the everyday math.</p></div><span class="tool-icon" aria-hidden="true">÷</span></div>
          <form id="tool-calc-form"><label for="tool-expression">Expression</label><input id="tool-expression" maxlength="56" autocomplete="off" spellcheck="false" placeholder="e.g. (24 + 6) / 3" aria-describedby="tool-calc-hint"><p class="tool-hint" id="tool-calc-hint">Use +, −, ×, ÷ and parentheses.</p><div class="tool-result" aria-live="polite"><span>RESULT</span><strong id="tool-calc-result">—</strong></div><div class="tool-actions"><button class="tool-primary" type="submit">Calculate <span aria-hidden="true">↗</span></button><button id="tool-calc-copy" class="tool-quiet" type="button" disabled>Copy result</button><button id="tool-calc-clear" class="tool-quiet" type="button">Clear</button></div><p class="tool-message" id="tool-calc-message" role="status"></p></form></section>
        <section class="tool-card tool-split" aria-labelledby="tool-split-title"><div class="tool-card-head"><div><span class="tool-index">02 / SHARE</span><h3 id="tool-split-title">Split the bill</h3><p>Find an easy starting point for the table.</p></div><span class="tool-icon" aria-hidden="true">↗</span></div>
          <div class="tool-fields tool-fields-three"><div><label for="tool-bill">Bill amount</label><input id="tool-bill" type="number" inputmode="decimal" min="0" max="1000000000" step="any" placeholder="0.00"></div><div><label for="tool-tip">Tip %</label><input id="tool-tip" type="number" inputmode="decimal" min="0" max="100" step="any" value="10"></div><div><label for="tool-people">People</label><input id="tool-people" type="number" inputmode="numeric" min="1" max="100" step="1" value="1"></div></div><div class="tool-result" aria-live="polite"><span>PER PERSON</span><strong id="tool-split-result">—</strong><small id="tool-split-detail">Enter a bill to calculate.</small></div><p class="tool-hint">Estimates use floating-point math. Amounts round independently to 2 decimals; adjust any remainder manually.</p></section>
        <section class="tool-card tool-convert" aria-labelledby="tool-convert-title"><div class="tool-card-head"><div><span class="tool-index">03 / CONVERT</span><h3 id="tool-convert-title">Unit converter</h3><p>From one measure to another, instantly.</p></div><span class="tool-icon" aria-hidden="true">⇄</span></div>
          <div class="tool-fields tool-fields-two"><div><label for="tool-unit">Measure</label><select id="tool-unit"><option value="distance">Distance · km / mi</option><option value="weight">Weight · kg / lb</option><option value="temperature">Temperature · °C / °F</option><option value="length">Length · cm / in</option><option value="volume">Volume · L / US gal</option></select></div><div><label for="tool-unit-value" id="tool-unit-label">Value in km</label><input id="tool-unit-value" type="number" inputmode="decimal" step="any" placeholder="Enter a value"></div></div><div class="tool-result tool-convert-result" aria-live="polite"><div><span>CONVERTED TO <b id="tool-unit-target">mi</b></span><strong id="tool-unit-result">—</strong></div><button class="tool-swap" id="tool-unit-swap" type="button" aria-label="Swap conversion direction">⇄ <span>Swap</span></button></div><p class="tool-hint">Conversion is approximate; US gallons are used.</p></section>
        <section class="tool-card tool-counter" aria-labelledby="tool-counter-title"><div class="tool-card-head"><div><span class="tool-index">04 / COUNT</span><h3 id="tool-counter-title">Counter</h3><p>Keep track, one tap at a time.</p></div><span class="tool-icon" aria-hidden="true">#</span></div>
          <div class="tool-counter-value" id="tool-count" aria-live="polite">0</div><div class="tool-counter-controls"><button id="tool-minus" type="button" aria-label="Subtract one">−</button><button id="tool-reset" type="button">Reset</button><button id="tool-plus" type="button" aria-label="Add one">+</button></div><p class="tool-hint">Local to this page, from −999,999 to 999,999.</p></section>
      </div><footer class="tool-foot"><span>◈ &nbsp; VIDEL / EVERYDAY TOOLS</span><p>For the Telegram widget, open the bot in a private chat and send <code>/widget</code> or <code>/widget tools</code>. Dashboard values are separate.</p></footer>
    </div>`;
    const $ = id => root.querySelector("#" + id);
    let last = "", count = 0, reverse = false;
    const expression = $("tool-expression"), calcResult = $("tool-calc-result"), calcMessage = $("tool-calc-message"), copy = $("tool-calc-copy");
    $("tool-calc-form").addEventListener("submit", e => { e.preventDefault(); try { last = calculate(expression.value); calcResult.textContent = last; calcMessage.textContent = "Calculated locally."; copy.disabled = false; } catch (error) { last = ""; calcResult.textContent = "—"; calcMessage.textContent = error.message; copy.disabled = true; } });
    expression.addEventListener("input", () => { last = ""; copy.disabled = true; calcResult.textContent = "—"; calcMessage.textContent = ""; });
    $("tool-calc-clear").addEventListener("click", () => { expression.value = ""; expression.dispatchEvent(new Event("input")); expression.focus(); });
    copy.addEventListener("click", async () => { if (!last) return; try { if (!navigator.clipboard || !navigator.clipboard.writeText) throw Error(); await navigator.clipboard.writeText(last); if (root.isConnected) calcMessage.textContent = "Result copied."; } catch (_) { if (root.isConnected) calcMessage.textContent = "Copy unavailable. Select the result to copy it."; } });
    const bill = $("tool-bill"), tip = $("tool-tip"), people = $("tool-people");
    function split() {
      const b = Number(bill.value), t = Number(tip.value), n = Number(people.value);
      if (!bill.value || !tip.value || !people.value || !Number.isFinite(b) || b < 0 || b > 1e9 || !Number.isFinite(t) || t < 0 || t > 100 || !Number.isInteger(n) || n < 1 || n > 100) { $("tool-split-result").textContent = "—"; $("tool-split-detail").textContent = "Enter a valid bill, tip (0–100%) and 1–100 people."; return; }
      const total = b * (1 + t / 100); $("tool-split-result").textContent = (total / n).toFixed(2); $("tool-split-detail").textContent = "Total " + total.toFixed(2) + " · Tip " + (total - b).toFixed(2);
    }
    [bill, tip, people].forEach(el => el.addEventListener("input", split));
    const unit = $("tool-unit"), amount = $("tool-unit-value");
    function convert() {
      const u = units[unit.value]; if (!u) return;
      $("tool-unit-label").textContent = "Value in " + (reverse ? u.b : u.a);
      $("tool-unit-target").textContent = reverse ? u.a : u.b;
      const n = Number(amount.value), v = reverse ? u.back(n) : u.forward(n);
      $("tool-unit-result").textContent = amount.value && Number.isFinite(n) && Math.abs(n) <= 1e9 && Number.isFinite(v) ? String(Number(v.toFixed(4))) : "—";
    }
    unit.addEventListener("change", () => { reverse = false; convert(); }); amount.addEventListener("input", convert);
    $("tool-unit-swap").addEventListener("click", () => { reverse = !reverse; convert(); });
    function counter(delta) { count = delta === null ? 0 : Math.max(-999999, Math.min(999999, count + delta)); $("tool-count").textContent = String(count); }
    $("tool-minus").addEventListener("click", () => counter(-1)); $("tool-plus").addEventListener("click", () => counter(1)); $("tool-reset").addEventListener("click", () => counter(null));
  }
  window.VidelTools = Object.freeze({ mount });
})();
