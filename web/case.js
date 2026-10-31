/* מקרה בוחן: Wyckoff Accumulation על BTC/USDT (גרף 4 שעות, מאי 2023), נתונים אמיתיים.
   תשעה פרקים: הקשר, SC, AR, ST, Spring, כניסה, ניהול העסקה, אחרי היציאה, מעבדה. בכל פרק הגרף חושף עוד נרות, יש שאלות, לחיצות על הגרף ותרגול.
   הגרף נשאר משמאל לימין (LTR) והטקסט מימין לשמאל (RTL). */
(function () {
  "use strict";
  const NX = window.NX;
  const $ = (s, r) => (r || document).querySelector(s);
  const esc = NX.esc;
  const N0 = (v) => Math.round(v).toLocaleString("en-US");
  const N1 = (v) => Number(v).toLocaleString("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  const N2 = (v) => Number(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const HD = (t) => new Date(t * 1000).toLocaleDateString("he-IL", { day: "numeric", month: "long", timeZone: "UTC" });
  const HT = (t) => new Date(t * 1000).toLocaleString("he-IL", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "UTC" });
  const CK = "nx_case_wyckoff";
  const lget = (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch (e) { return d; } };
  const lset = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* private mode */ } };

  const C = { data: null, chart: null, cs: null, vs: null, draw: null, ch: 0, upto: 0, lines: [], answers: lget(CK, {}), replay: { i: 0, timer: null, done: false }, lab: null, entered: false, bars: [], mounted: false };

  // ------------------------------------------------------------------ chart
  const bar = (r) => ({ time: r[0], open: r[1], high: r[2], low: r[3], close: r[4] });
  const vbar = (r) => ({ time: r[0], value: r[5], color: r[4] >= r[1] ? "rgba(43,224,126,0.45)" : "rgba(255,74,74,0.45)" });
  const EVN = { sc: ["SC", "#ff4a4a"], ar: ["AR", "#5aa9ff"], st: ["ST", "#e8d44d"], spring: ["SPRING", "#2be07e"], sos: ["SOS", "#ffa028"], lps: ["LPS", "#c58bff"] };
  function initChart(el) {
    const LW = window.LightweightCharts, D = C.data;
    C.chart = LW.createChart(el, { autoSize: true, layout: { background: { type: "solid", color: "#050505" }, textColor: "#8b8b8b", fontFamily: "JetBrains Mono, Consolas, monospace", fontSize: 11 }, grid: { vertLines: { color: "#111" }, horzLines: { color: "#111" } },
      crosshair: { mode: LW.CrosshairMode.Normal, vertLine: { color: "#ffa028", labelBackgroundColor: "#ffa028" }, horzLine: { color: "#ffa028", labelBackgroundColor: "#ffa028" } }, rightPriceScale: { borderColor: "#262626" }, timeScale: { borderColor: "#262626", timeVisible: true, secondsVisible: false, rightOffset: 6 } });
    C.cs = C.chart.addCandlestickSeries({ upColor: "#2be07e", downColor: "#ff4a4a", borderVisible: false, wickUpColor: "#2be07e", wickDownColor: "#ff4a4a" });
    C.vs = C.chart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "vol", lastValueVisible: false, priceLineVisible: false });
    C.chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    C.chart.subscribeClick(onClick);
    C.draw = window.NXDraw.attach({ host: el.parentElement, chart: C.chart, series: C.cs, times: () => C.times, decimals: () => 0, name: "case" });
    C.draw.bindToolbar($("#cs-draw"));
  }
  function show(upto, opts = {}) {
    const D = C.data; C.upto = upto;
    C.cs.setData(D.candles.slice(0, upto + 1).map(bar)); C.vs.setData(D.candles.slice(0, upto + 1).map(vbar));
    C.lines.forEach((l) => { try { C.cs.removePriceLine(l); } catch (e) { /* gone */ } }); C.lines = [];
    (opts.lines || []).forEach(([p, col, title, dash]) => C.lines.push(C.cs.createPriceLine({ price: p, color: col, lineWidth: 1, lineStyle: dash ? 2 : 0, axisLabelVisible: true, title })));
    const M = [];
    (opts.events || []).forEach((k) => { const i = D.ev[k]; if (i != null && i <= upto) M.push({ time: D.candles[i][0], position: k === "sc" || k === "st" || k === "spring" || k === "lps" ? "belowBar" : "aboveBar", color: EVN[k][1], shape: k === "sc" || k === "st" || k === "spring" || k === "lps" ? "arrowUp" : "arrowDown", text: EVN[k][0] }); });
    (opts.marks || []).forEach((m) => { if (m.i <= upto) M.push({ time: D.candles[m.i][0], position: m.pos || "belowBar", color: m.color, shape: m.shape || "circle", text: m.text }); });
    M.sort((a, b) => a.time - b.time); C.cs.setMarkers(M);
    C.chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, upto - (opts.window || 120)), to: upto + 12 });
    if (C.draw) C.draw.paint();
  }
  let clickCb = null;
  function onClick(p) { if (!clickCb || !p.time) return; const D = C.data; let best = -1, bd = 1e12; for (let i = 0; i <= C.upto; i++) { const d = Math.abs(D.candles[i][0] - p.time); if (d < bd) { bd = d; best = i; } } clickCb(best); }

  // ------------------------------------------------------------------ chapters
  const yesno = (id, q, options, correct, why) => ({ id, q, options, correct, why });
  function chapters() {
    const D = C.data, S = D.stats, ev = D.ev, K = D.candles, sc = K[ev.sc], lowSC = sc[3], scPos = Math.round(((sc[4] - sc[3]) / (sc[2] - sc[3])) * 100);
    const rl = D.range_low, rh = D.range_high;
    const dHi = K[D.decline_high], dsc = K[ev.sc];
    return [
      { t: "פתיחה: איך קוראים Wyckoff", upto: D.decline_high + 25, ev: [], window: 90,
        body: () => `<p>זה מקרה בוחן <b>אמיתי</b>: BTC/USDT בגרף של 4 שעות, ${HD(K[D.decline_high][0])} עד ${HD(K[K.length - 1][0])} 2023. כל נר שתראו קרה בפועל.</p>
          <p>Wyckoff מלמד לקרוא את הגרף כמו סיפור של <b>קונים גדולים ("הכסף החכם")</b> שצוברים פוזיציה בשקט אחרי ירידה, לפני שהמחיר ממריא. המקרה הזה הוא <b>Accumulation</b> (צבירה) והמטרה: לדעת <b>מה לזהות</b>, <b>מתי להיכנס</b>, <b>איפה לטעות בזול</b> ו<b>מתי לצאת</b>.</p>
          <div class="cs-box q"><h4>❓ שלוש השאלות שחוזרות בכל שלב</h4><ol><li><b>איפה אני במבנה?</b> האם יש בכלל ירידה שנעצרת, טווח, או פריצה?</li><li><b>מי שולט?</b> מאמץ מול תוצאה: נפח גדול בלי תנועה = מישהו סופג את הצד השני.</li><li><b>איפה הרעיון שלי שגוי?</b> אם אין סטופ ברור מעבר למבנה, אין עסקה.</li></ol></div>
          <div class="cs-box tip"><h4>💡 איך משתמשים</h4><p>כל פרק חושף עוד נרות. נסו לענות לפני שלוחצים "הבא". יש שאלות, לחיצות על הגרף, מחשבון סיכון, ובסוף מעבדה שבה אפשר לבדוק כניסה בכל נר. אפשר גם לצייר על הגרף בכלי הציור.</p></div>` },
      { t: "1 · הקשר: ירידה שנעצרת?", upto: ev.sc - 12, ev: [], window: 200,
        body: () => `<h4>🔍 מה רואים על הגרף</h4><p>מהשיא ב-${HD(dHi[0])} (${N0(dHi[2])}$) המחיר ירד <b>${N1(S.decline_pct)}%</b> בתוך <b>${S.decline_bars} נרות</b> (כחודש). שיאים נמוכים יותר, שפלים נמוכים יותר, נרות אדומים ארוכים: ירידה אמיתית.</p>
          <p>Accumulation לא קורה "באמצע שום מקום". הוא בא <b>אחרי ירידה משמעותית</b>, כי רק אז יש מי שנאלץ למכור (פאניקה, סטופים, מרג'ין) ויש למי לקנות.</p>
          <div class="cs-box q"><h4>❓ שאלה 1</h4><div id="cs-quiz"></div></div>`,
        quiz: yesno("q1", "לפני שמחפשים Accumulation, איזה הקשר צריך להיות?", ["מגמת עלייה ארוכה שממשיכה", "ירידה משמעותית שמתחילה להיעצר", "טווח צר בלי מגמה כבר חודשים"], 1, "Accumulation בא אחרי ירידה: הקונים הגדולים מחכים לפאניקה כדי לקנות בזול. אחרי עלייה ארוכה מחפשים את הבבואה שלו, Distribution.") },
      { t: "2 · שלב A: ה-SC, הנר שעוצר את הדימום", upto: ev.sc + 2, ev: [], window: 110, click: "sc",
        body: () => `<h4>🔍 מה מחפשים</h4><p><b>Selling Climax (SC)</b>: נר רחב עם <b>נפח שיא</b> בסוף ירידה ארוכה. מי שנשאר מוכר בפאניקה, וגדולים קונים מולו.</p>
          <div class="cs-box q"><h4>🖱 משימה</h4><p>הנרות מוצגים עד שפל הירידה. <b>לחצו על הנר</b> שלדעתכם הוא ה-SC (רמז: הנפח הגבוה ביותר בסוף הירידה).</p><div id="cs-click" class="cs-res"></div></div>` ,
        after: () => `<div class="cs-box ok"><h4>✅ נכון! זה ה-SC · ${HT(sc[0])}</h4><ul><li>נפח <b>${N1(S.sc_vol_ratio)}×</b> מהממוצע (50 נרות): מאמץ עצום.</li><li>שפל: <b>${N0(lowSC)}$</b>. הנר נסגר ב-<b>${scPos}%</b> מהגובה שלו: ${scPos > 50 ? "רחוק מהשפל, כלומר המכירות נבלעו" : "קרוב לשפל, אבל הירידה נעצרת בנרות הבאים"}.</li><li><b>למה זה חשוב?</b> מאמץ גדול + תוצאה קטנה בהמשך = מישהו סופג מכירות.</li></ul><p><b>מה עושים עכשיו? כלום.</b> רק מסמנים ומחכים ל-AR. אף פעם לא קונים "סכין נופלת".</p></div>` },
      { t: "3 · AR: הקפיצה שמגדירה את הטווח", upto: ev.ar + 3, ev: ["sc"], window: 110,
        lines: () => [[K[ev.ar][2], "#ffa028", "ראש הטווח (AR)", true], [lowSC, "#ffa028", "תחתית הטווח (SC)", true]],
        body: () => `<h4>🔍 מה רואים על הגרף</h4><p>אחרי ה-SC המחיר קופץ בחדות: <b>Automatic Rally (AR)</b> של <b>${N1(S.ar_bounce_pct)}%</b> ל-${N0(rh)}$. הקפיצה הזו קורית כי המוכרים נגמרו ומי שמכר בשורט סוגר.</p>
          <p>עכשיו יש לנו <b>טווח</b>: תחתית = שפל ה-SC (<b>${N0(rl)}</b>), ראש = ה-AR (<b>${N0(rh)}</b>), גובה <b>${N1(S.range_height_pct)}%</b>. כל ההמשך קורה בתוך הגבולות האלה ומולם.</p>
          <div class="cs-box q"><h4>❓ שאלה 2</h4><div id="cs-quiz"></div></div>`,
        quiz: yesno("q2", "מה קובע את תחתית הטווח?", ["הסגירה של נר ה-AR", "השפל של נר ה-SC", "הממוצע הנע של 50 נרות"], 1, "התחתית היא שפל ה-SC, והראש הוא השיא של ה-AR. מי שמעל/מתחת לגבולות האלה משנה את התמונה: פריצה למעלה = חוזק, שבירה מתחת = בחינה של Spring או כישלון.") },
      { t: "4 · שלב B: ה-ST והסבלנות", upto: ev.st + 8, ev: ["sc", "ar"], window: 110,
        lines: () => [[rh, "#ffa028", "ראש הטווח", true], [rl, "#ffa028", "תחתית הטווח", true]],
        body: () => `<h4>🔍 מה רואים על הגרף</h4><p>המחיר חוזר לבדוק את אזור השפל: <b>Secondary Test (ST)</b> ב-${HT(K[ev.st][0])}. הוא נעצר <b>${N2(S.st_low_vs_sc_pct)}%</b> מעל שפל ה-SC, והנפח רק <b>${Math.round(S.st_vol_vs_sc * 100)}%</b> מנפח ה-SC.</p>
          <p><b>הקריאה:</b> באותו אזור, פחות מוכרים והפעם המחיר לא ממשיך למטה. המוכרים התעייפו.</p>
          <div class="cs-box tip"><h4>🧘 מה עושים בשלב B?</h4><p>מחכים. בתוך הטווח עוברים "צ'ופ" (עליות וירידות בלי כיוון). <b>לא סוחרים את אמצע הטווח</b>: אין שם יתרון. מסמנים את ה-ST וממשיכים לעקוב אחרי הנפח.</p></div>
          <div class="cs-box q"><h4>❓ שאלה 3</h4><div id="cs-quiz"></div></div>`,
        quiz: yesno("q3", "איזה ST הוא סימן חיובי ל-Accumulation?", ["שובר את שפל ה-SC בנפח גבוה", "חוזר לאזור השפל בנפח נמוך ומחזיק מעליו", "עולה מעל ראש הטווח מיד"], 1, "בדיקה חוזרת בנפח נמוך שמחזיקה מעל שפל ה-SC אומרת שהיצע התייבש. שבירה בנפח גבוה היתה אומרת שהמכירות ממשיכות.") },
      { t: "5 · שלב C: ה-Spring, הנפילה שמפחידה", upto: ev.spring, ev: ["sc", "ar", "st"], window: 90,
        lines: () => [[rh, "#ffa028", "ראש הטווח", true], [rl, "#ffa028", "תחתית הטווח", true]],
        body: () => `<h4>🔍 מה רואים על הגרף</h4><p>${HT(K[ev.spring][0])}: המחיר <b>שובר מתחת לתחתית הטווח</b>, לשפל של <b>${N0(K[ev.spring][3])}</b> (${N2(S.spring_depth_pct)}% מתחת), ונדחף בחזרה. זה <b>Spring</b>: ניסיון אחרון "לנער" את מי שנשאר, ולתפוס עצירות סטופ מתחת לשפל.</p>
          <ul><li>נפח בנר ה-Spring: <b>${N1(S.spring_vol_ratio)}×</b> מהממוצע, כלומר <b>יבש</b>. אין מוכרים אמיתיים מתחת לטווח.</li><li>השבירה קטנה וקצרה. שבירה עמוקה ומתמשכת בנפח גבוה היא כישלון של הטווח, לא Spring.</li></ul>
          <div class="cs-box warn"><h4>⚠ שימו לב</h4><p>עדיין <b>אין אישור</b>. רוב הסוחרים רואים "שבירה" ומוכרים בשורט או מפחדים. הכסף החכם קונה את הפאניקה, אבל אנחנו לא בטוחים שזה Spring עד שהמחיר מוכיח.</p></div>
          <div class="cs-box q"><h4>❓ שאלה 4: עכשיו נכנסים?</h4><div id="cs-quiz"></div></div>`,
        quiz: yesno("q4", "המחיר שבר מתחת לתחתית הטווח בנפח יבש. מה עושים?", ["קונים מיד, זה הכי זול שיש", "מוכרים בשורט: זו שבירה", "מחכים לנר שנסגר חזרה בתוך הטווח, ואז מחליטים"], 2, "הכניסה מגיעה אחרי <b>אישור</b>: סגירה של נר חזרה מעל תחתית הטווח. כך נמנעים מלקנות שבירה אמיתית, והסטופ נשאר קטן וברור: מתחת לשפל ה-Spring.") },
      { t: "6 · הכניסה: מתי, איפה ובכמה", upto: D.back, ev: ["sc", "ar", "st", "spring"], window: 80, entry: true,
        lines: () => [[rh, "#2be07e", "יעד: ראש הטווח", false], [D.entry, "#5aa9ff", "כניסה", false], [D.stop, "#ff4a4a", "סטופ", false], [rl, "#ffa028", "תחתית הטווח", true]],
        body: () => `<h4>✅ הרגע הנכון: ${S.spring_close_back_bars} נרות (12 שעות) אחרי ה-Spring</h4><p>נר <b>נסגר חזרה בתוך הטווח</b>, מעל ${N0(rl)}. זה האישור. תוכנית העסקה:</p>
          <table class="cs-tbl"><tr><td>כניסה (סגירת הנר)</td><td><b>${N0(D.entry)}$</b></td></tr><tr><td>סטופ (מתחת לשפל ה-Spring)</td><td><b class="dn">${N0(D.stop)}$</b> · סיכון ${N2(S.risk_pct)}%</td></tr><tr><td>יעד ראשון (ראש הטווח)</td><td><b class="up">${N0(D.target)}$</b></td></tr><tr><td>יחס סיכון/סיכוי</td><td><b>1 : ${N2(D.rr)}</b></td></tr></table>
          <div class="cs-box q"><h4>🧮 מחשבון גודל פוזיציה</h4><div class="cs-calc"><label>גודל החשבון ($)<input id="cs-acc" type="number" value="10000"/></label><label>סיכון לעסקה (%)<input id="cs-risk" type="number" value="1" step="0.25"/></label></div><div id="cs-calcout" class="cs-res"></div></div>
          <div class="cs-box"><h4>☑ צ'קליסט לפני כניסה</h4><ul class="cs-check" id="cs-check">${[["הקשר: ירידה של " + N1(S.decline_pct) + "% שנעצרה", true], ["SC: נפח " + N1(S.sc_vol_ratio) + "× ושפל ברור", true], ["ST: חזרה לשפל בנפח נמוך שהחזיק", true], ["Spring: שבירה קטנה בנפח יבש (" + N1(S.spring_vol_ratio) + "×)", true], ["אישור: נר נסגר חזרה בתוך הטווח", true], ["סטופ ברור מתחת לשפל ה-Spring וסיכון עד 2%", true]].map((r) => `<li class="on"><i>✓</i>${r[0]}</li>`).join("")}</ul></div>
          <button class="cs-btn" id="cs-enter">▶ בצע כניסה (סימולציה)</button><div id="cs-enterres" class="cs-res"></div>` },
      { t: "7 · ניהול העסקה: מתי לזוז ומתי לצאת", upto: D.back, ev: ["sc", "ar", "st", "spring"], window: 80, replay: true,
        lines: () => [[rh, "#2be07e", "יעד", false], [D.entry, "#5aa9ff", "כניסה", false], [D.stop, "#ff4a4a", "סטופ", false]],
        body: () => `<h4>🎬 הפעילו את העסקה נר אחרי נר</h4><p>לחצו <b>נגן</b> או <b>נר הבא</b> ועקבו אחרי הרווח ב-R (1R = הסיכון ההתחלתי, ${N0(D.entry - D.stop)}$). העסקה תיעצר כשהמחיר יגיע ל-+1R וגם תישאלו מה עושים.</p>
          <div class="cs-ctl"><button class="cs-btn" id="rp-play">▶ נגן</button><button class="cs-btn ghost" id="rp-step">נר הבא</button><button class="cs-btn ghost" id="rp-reset">↺ מההתחלה</button></div>
          <div class="cs-pnl" id="rp-pnl">ממתין לפתיחה…</div><div id="cs-quiz" class="cs-box q" style="display:none"></div>
          <div class="cs-box"><h4>🚪 כללי יציאה</h4><table class="cs-tbl"><tr><td><b>יעד</b></td><td>ראש הטווח (${N0(D.target)}$): מימוש מלא או חלקי.</td></tr><tr><td><b>סטופ</b></td><td>סגירה מתחת לשפל ה-Spring: הרעיון שגוי, יוצאים בלי ויכוחים.</td></tr><tr><td><b>+1R</b></td><td>מעבירים את הסטופ לנקודת הכניסה: עסקה חינמית.</td></tr><tr><td><b>זמן</b></td><td>10 נרות בלי המשך? חושדים ב-Spring כושל וסוגרים.</td></tr><tr><td><b>SOS</b></td><td>פריצה מעלה בנפח = חיזוק. אפשר להשאיר חלק לריצה.</td></tr></table></div>`,
        quiz: yesno("q5", "העסקה עומדת על +1R. מה עושים?", ["סוגרים הכל, רווח הוא רווח", "מעבירים את הסטופ לכניסה ומשאירים את העסקה לרוץ ליעד", "מגדילים את הפוזיציה כי זה הולך טוב"], 1, "מעבירים סטופ לאיזון: הסיכון הופך לאפס והסיכוי לרוץ ליעד נשאר. הגדלה בשיא היא הוספת סיכון בלי הצדקה, וסגירה מוקדמת גורמת לך לוותר על יחס הסיכוי שסביבו בנית את העסקה.") },
      { t: "8 · אחרי היציאה: SOS ו-LPS", upto: K.length - 1, ev: ["sc", "ar", "st", "spring", "sos", "lps"], window: 160,
        lines: () => [[rh, "#ffa028", "ראש הטווח", true], [rl, "#ffa028", "תחתית הטווח", true]],
        marks: () => [{ i: D.back, text: "כניסה", color: "#5aa9ff", shape: "arrowUp", pos: "belowBar" }, { i: D.exit_i, text: `יציאה ${N1(D.rr)}R`, color: "#2be07e", shape: "arrowDown", pos: "aboveBar" }],
        body: () => `<h4>🔍 מה קרה בהמשך</h4><ul><li><b>SOS (Sign of Strength)</b> ב-${HT(K[ev.sos][0])}: עלייה חזקה <b>בנפח ${N1(S.sos_vol_ratio)}×</b> ליד ראש הטווח. הקונים בשליטה. כבר היינו בפנים.</li><li>היציאה: היעד הגיע אחרי <b>${S.bars_to_target} נרות</b> (כ-3 ימים) ב-<b>+${N2(D.rr)}R</b>. הירידה המקסימלית נגד העסקה: רק ${N2(Math.abs(S.mae_r))}R.</li><li><b>LPS (Last Point of Support)</b> ב-${HT(K[ev.lps][0])}: תיקון רדוד בנפח נמוך, <b>${N2(S.lps_above_low_pct)}%</b> מעל תחתית הטווח. זו נקודת <b>כניסה שנייה</b> בטוחה יותר לפי Wyckoff, אחרי ההוכחה.</li></ul>
          <div class="cs-box tip"><h4>שלב D ו-E</h4><p>D: המחיר עובר את הטווח ונשאר בחלקו העליון. E: מגמה חדשה (Markup). אם רוצים להשתתף במהלך גדול, נכנסים ב-LPS ולא רודפים.</p></div>
          <div class="cs-box q"><h4>❓ שאלה 6</h4><div id="cs-quiz"></div></div>`,
        quiz: yesno("q6", "פספסתם את ה-Spring. איפה הכניסה הבטוחה ביותר?", ["ב-LPS: תיקון רדוד בנפח נמוך אחרי ה-SOS", "בשיא הפריצה, מהר לפני שבורח", "בשורט, כי הוא הגיע לראש הטווח"], 0, "אחרי SOS, ה-LPS הוא נקודה שבה כבר יש הוכחה (פריצה בנפח) והסיכון נמדד מתחת לתיקון. רודפים אחרי נר פריצה מגדילים סטופ ומקטינים את היחס.") },
      { t: "9 · מעבדה, סיכום וטעויות נפוצות", upto: K.length - 1, ev: ["sc", "ar", "st", "spring", "sos", "lps"], window: 190, lab: true,
        lines: () => [[rh, "#ffa028", "ראש הטווח", true], [rl, "#ffa028", "תחתית הטווח", true], [D.stop, "#ff4a4a", "הסטופ של הסטאפ", true]],
        body: () => `<div class="cs-box q"><h4>🧪 מעבדה: מה אם נכנסתי במקום אחר?</h4><p>לחצו על <b>כל נר</b> בגרף (מה-SC ואילך). המערכת מדמה כניסה בסגירה שלו, <b>אותו סטופ ואותו יעד</b> של הסטאפ, ומראה מה היה קורה.</p><div id="cs-lab" class="cs-res">לחצו על נר…</div></div>
          <div class="cs-box"><h4>📊 המספרים האמיתיים</h4><div id="cs-stats" class="cs-res">טוען…</div></div>
          <div class="cs-box warn"><h4>🚫 טעויות נפוצות</h4><ul><li><b>לקנות את ה-SC</b> בלי אישור: סכין נופלת.</li><li><b>לקנות בתוך השבירה</b> לפני סגירה חזרה בטווח.</li><li>סטופ <b>בתוך</b> הטווח: רעש רגיל יוציא אתכם.</li><li>לוותר על יחס סיכון/סיכוי: Spring מצליח לעיתים רחוקות, חייב לשלם טוב.</li><li>להתעלם מהנפח: Spring בנפח כבד הוא לרוב שבירה אמיתית.</li><li>לסחור כל טווח: צריך הקשר של ירידה קודם.</li></ul></div>
          <div class="cs-box tip"><h4>🔄 המראה: Distribution</h4><p>אחרי עלייה ארוכה: BC (Buying Climax) → AR למטה → ST → <b>UT/UTAD</b> (עלייה מעל הטווח שנכשלת) → שורט אחרי סגירה חזרה מתחת לראש הטווח. הכל הפוך.</p></div>
          <div id="cs-final"></div>` },
    ];
  }

  // ------------------------------------------------------------------ rendering
  const state = { list: [] };
  function score() { const q = ["q1", "sc", "q2", "q3", "q4", "q5", "q6"]; return { ok: q.filter((k) => C.answers[k] === 1).length, tot: q.length }; }
  function renderStepper() {
    $("#cs-stepper").innerHTML = state.list.map((c, i) => `<button class="cs-pill ${i === C.ch ? "on" : i < C.ch ? "done" : ""}" data-i="${i}"><b>${i}</b><span>${esc(c.t.replace(/^\d+ · /, ""))}</span></button>`).join("");
    $("#cs-stepper").querySelectorAll(".cs-pill").forEach((b) => b.addEventListener("click", () => go(Number(b.dataset.i))));
    const s = score(); $("#cs-score").textContent = `${s.ok}/${s.tot}`;
    $("#cs-prog").style.width = `${(C.ch / (state.list.length - 1)) * 100}%`;
  }
  function quizHtml(q) {
    const done = C.answers[q.id];
    return `<p class="cs-q">${q.q}</p><div class="cs-opts">${q.options.map((o, i) => `<button class="cs-opt" data-i="${i}">${esc(o)}</button>`).join("")}</div><div id="cs-why" class="cs-why"></div>`;
  }
  function wireQuiz(q, after) {
    const box = $("#cs-quiz"); if (!box) return;
    box.innerHTML = quizHtml(q);
    box.querySelectorAll(".cs-opt").forEach((b) => b.addEventListener("click", () => {
      const i = Number(b.dataset.i), ok = i === q.correct;
      box.querySelectorAll(".cs-opt").forEach((x) => { x.disabled = true; x.classList.toggle("right", Number(x.dataset.i) === q.correct); });
      if (!ok) b.classList.add("wrong");
      if (C.answers[q.id] == null) { C.answers[q.id] = ok ? 1 : 0; lset(CK, C.answers); }
      $("#cs-why").innerHTML = `<b>${ok ? "✅ נכון." : "❌ לא בדיוק."}</b> ${q.why}`; $("#cs-why").className = "cs-why " + (ok ? "ok" : "bad"); renderStepper();
      if (after) after(ok);
    }));
  }
  function go(i) {
    clearInterval(C.replay.timer); C.replay = { i: 0, timer: null, done: false }; clickCb = null; C.entered = false; C.lab = null;
    i = Math.max(0, Math.min(state.list.length - 1, i)); C.ch = i; const c = state.list[i];
    $("#cs-title").textContent = c.t;
    $("#cs-body").innerHTML = c.body() + `<div class="cs-nav"><button class="cs-btn ghost" id="cs-prev" ${i === 0 ? "disabled" : ""}>◀ הקודם</button><button class="cs-btn" id="cs-next" ${i === state.list.length - 1 ? "disabled" : ""}>הבא ▶</button></div>`;
    $("#cs-prev").addEventListener("click", () => go(i - 1)); $("#cs-next").addEventListener("click", () => go(i + 1));
    $("#cs-story").scrollTop = 0;
    const base = { lines: c.lines ? c.lines() : [], events: c.ev, marks: c.marks ? c.marks() : [], window: c.window };
    // click-the-chart chapter (SC): the climax marker only appears after the student finds it
    if (c.click === "sc") {
      show(c.upto, { ...base, events: [] });
      clickCb = (idx) => {
        const target = C.data.ev.sc, box = $("#cs-click"), ok = Math.abs(idx - target) <= 4;
        if (C.answers.sc == null) { C.answers.sc = ok ? 1 : 0; lset(CK, C.answers); }
        if (ok) { show(c.upto, { ...base, events: ["sc"] }); box.className = "cs-res okk"; box.innerHTML = c.after(); clickCb = null; } else { box.className = "cs-res bad"; box.innerHTML = `❌ זה לא הנר. חפשו את הנר עם <b>הנפח הגבוה ביותר</b> בסוף הירידה, בערך ליד השפל. נסו שוב.`; }
        renderStepper();
      };
      if (C.answers.sc === 1) { show(c.upto, { ...base, events: ["sc"] }); $("#cs-click").className = "cs-res okk"; $("#cs-click").innerHTML = c.after(); clickCb = null; }
    } else show(c.upto, base);
    if (c.quiz && !c.replay) wireQuiz(c.quiz);
    if (c.entry) chapterEntry(c, base); if (c.replay) chapterReplay(c, base); if (c.lab) chapterLab(c, base);
    renderStepper();
  }
  function chapterEntry(c, base) {
    const D = C.data, calc = () => {
      const acc = Number($("#cs-acc").value) || 0, r = Number($("#cs-risk").value) || 0, risk$ = (acc * r) / 100, dist = D.entry - D.stop, units = risk$ / dist, notional = units * D.entry, win = units * (D.target - D.entry);
      $("#cs-calcout").innerHTML = `סיכון: <b class="dn">$${N0(risk$)}</b> · גודל פוזיציה: <b>${units.toFixed(4)} BTC</b> (כ-$${N0(notional)}) · הרווח אם מגיע ליעד: <b class="up">+$${N0(win)}</b> (${N2(D.rr)}R)${r > 2 ? ' <span class="dn">⚠ יותר מ-2% זה מסוכן: רצף סטופים יכול להכביד.</span>' : ""}`;
    };
    $("#cs-acc").addEventListener("input", calc); $("#cs-risk").addEventListener("input", calc); calc();
    $("#cs-enter").addEventListener("click", () => {
      C.entered = true; show(c.upto, { ...base, marks: [{ i: D.back, text: "כניסה ▲", color: "#5aa9ff", shape: "arrowUp", pos: "belowBar" }] });
      $("#cs-enterres").className = "cs-res okk"; $("#cs-enterres").innerHTML = `✅ נכנסתם ב-<b>${N0(D.entry)}$</b>. עכשיו הכלל: <b>לא נוגעים</b> עד שקורה אחד משלושה: יעד ${N0(D.target)}$, סטופ ${N0(D.stop)}$, או +1R (אז מזיזים סטופ). המשיכו לפרק הבא ונראה מה קרה.`;
    });
  }
  function chapterReplay(c, base) {
    const D = C.data, K = D.candles, risk = D.entry - D.stop, R = C.replay, pnl = $("#rp-pnl");
    const draw = () => {
      const i = D.back + R.i, held = i >= D.be_i && R.i > 0, lines = [[D.target, "#2be07e", "יעד", false], [D.entry, "#5aa9ff", "כניסה", false], [held ? D.entry : D.stop, held ? "#e8d44d" : "#ff4a4a", held ? "סטופ באיזון" : "סטופ", false]];
      const marks = [{ i: D.back, text: "כניסה", color: "#5aa9ff", shape: "arrowUp", pos: "belowBar" }]; if (held) marks.push({ i: D.be_i, text: "סטופ לאיזון", color: "#e8d44d", shape: "circle", pos: "aboveBar" }); if (R.done) marks.push({ i: D.exit_i, text: `יציאה +${N2(D.rr)}R`, color: "#2be07e", shape: "arrowDown", pos: "aboveBar" });
      show(i, { lines, events: c.ev, marks, window: 80 });
      const r = (K[i][4] - D.entry) / risk;
      pnl.innerHTML = `נר ${R.i}/${D.exit_i - D.back} · סגירה <b>${N0(K[i][4])}$</b> · רווח פתוח: <b class="${r >= 0 ? "up" : "dn"}">${r >= 0 ? "+" : ""}${N2(r)}R</b>${held ? " · <b class='amb'>הסטופ הועבר לאיזון</b>" : ""}${R.done ? ` · <b class="up">הגענו ליעד: +${N2(D.rr)}R ✅</b>` : ""}`;
    };
    const step = () => {
      if (R.done) return;
      R.i++; const i = D.back + R.i;
      if (i >= D.exit_i) { R.done = true; clearInterval(R.timer); $("#rp-play").textContent = "▶ נגן"; draw(); return; }
      draw();
      if (i === D.be_i && !C.answers.q5_seen) { clearInterval(R.timer); $("#rp-play").textContent = "▶ המשך"; const q = c.quiz, box = $("#cs-quiz"); box.style.display = "block"; wireQuiz(q, () => { C.answers.q5_seen = 1; }); box.scrollIntoView({ behavior: "smooth", block: "nearest" }); }
    };
    draw();
    $("#rp-play").addEventListener("click", (e) => { if (R.done) return; if (R.timer) { clearInterval(R.timer); R.timer = null; e.target.textContent = "▶ נגן"; return; } e.target.textContent = "■ עצור"; R.timer = setInterval(step, 420); });
    $("#rp-step").addEventListener("click", step);
    $("#rp-reset").addEventListener("click", () => { clearInterval(R.timer); C.replay = { i: 0, timer: null, done: false }; go(C.ch); });
  }
  async function chapterLab(c, base) {
    const D = C.data, K = D.candles; clickCb = (idx) => {
      if (idx < D.ev.sc || idx > K.length - 6) { $("#cs-lab").innerHTML = "בחרו נר מה-SC ואילך (ולא בשוליים האחרונים)."; return; }
      const entry = K[idx][4], stop = D.stop, target = D.target;
      let res = null;
      if (entry <= stop) res = { t: "invalid", txt: `הכניסה ב-${N0(entry)}$ נמצאת <b>מתחת לסטופ</b> של הסטאפ. במחיר הזה המבנה כבר נכשל: אין עסקה.` };
      else if (entry >= target) res = { t: "invalid", txt: `הכניסה ב-${N0(entry)}$ כבר <b>מעל היעד</b> (ראש הטווח). אין סיכוי מעבר: רודפים.` };
      else {
        const risk = entry - stop, rr = (target - entry) / risk; let exit = null, why = "פתוחה";
        for (let m = idx + 1; m < K.length; m++) { if (K[m][3] <= stop) { exit = stop; why = "סטופ"; break; } if (K[m][2] >= target) { exit = target; why = "יעד"; break; } }
        const r = exit == null ? (K[K.length - 1][4] - entry) / risk : (exit - entry) / risk;
        const tag = idx < D.ev.spring ? "לפני ה-Spring: כניסה <b>מוקדמת</b>, בלי אישור" : idx < D.back ? "בתוך ה-Spring: עדיין לפני האישור" : idx <= D.back + 1 ? "<b>הכניסה הנכונה</b> (סגירה חזרה בטווח)" : idx < D.ev.sos ? "אחרי האישור: מאוחרת אך סבירה" : "אחרי ה-SOS: <b>רודפים</b>, היחס נשחק";
        res = { t: why, txt: `כניסה ב-<b>${N0(entry)}$</b> (${tag}).<br>סיכון: ${N2((risk / entry) * 100)}% · יחס סיכון/סיכוי: <b>1:${N2(rr)}</b><br>תוצאה: <b class="${r >= 0 ? "up" : "dn"}">${r >= 0 ? "+" : ""}${N2(r)}R</b> (${why})` };
      }
      $("#cs-lab").className = "cs-res " + (res.t === "סטופ" ? "bad" : res.t === "invalid" ? "" : "okk"); $("#cs-lab").innerHTML = res.txt;
      show(C.upto, { lines: base.lines, events: base.events, marks: [{ i: idx, text: "כאן", color: "#ffffff", shape: "arrowUp", pos: "belowBar" }], window: 190 });
    };
    try {
      const L = await api("/academy/library"), a = (L["BTCUSDT:4h"] || {}).accumulation;
      $("#cs-stats").innerHTML = a ? `בספרייה האמיתית: <b>${a.n}</b> סטאפים כאלה ב-BTC 4 שעות. <b class="dn">${a.stopped}%</b> נעצרו בסטופ, <b>${a.hit_2r}%</b> הגיעו ל-2R, ממוצע <b>${a.avg_r}R</b> אם מחזיקים עד ראש הטווח.<br>המקרה שראיתם הוא <b>מהמוצלחים</b>. רוב הניסיונות נכשלים, ולכן: <b>סטופ קטן, יעד של 2R ומעלה, וסיכון של 1–2%</b>. היתרון הוא בלטעות בזול.` : "הנתונים אינם זמינים כרגע.";
    } catch (e) { $("#cs-stats").innerHTML = "בספרייה הכללית: רוב הניסיונות (כ-70% ומעלה) נעצרים בסטופ, ומעט מהם מגיעים ל-2R. לכן סטופ קטן ויעד גדול הם חובה."; }
    const s = score();
    $("#cs-final").innerHTML = `<div class="cs-final"><div class="cs-big">${s.ok}/${s.tot}</div><div>הציון שלכם בשאלות המקרה הבוחן</div><div class="cs-cta"><button class="cs-btn" id="cs-drill">🎯 תרגול על מקרים אמיתיים (Wyckoff Drill)</button><button class="cs-btn ghost" id="cs-again">↺ התחילו מחדש</button></div></div>`;
    $("#cs-drill").addEventListener("click", () => { const chip = document.querySelector('#ac-modes .bb-chip[data-f="drill"]'); if (chip) chip.click(); });
    $("#cs-again").addEventListener("click", () => { C.answers = {}; lset(CK, C.answers); go(0); });
  }

  // ------------------------------------------------------------------ mount
  async function mount(el) {
    if (C.mounted) { setTimeout(() => C.chart && C.chart.applyOptions({}), 30); return; }
    el.innerHTML = `<div class="cs-loading">טוען את המקרה הבוחן…</div>`;
    try { C.data = await (await fetch("case_btc_2023.json?v=1")).json(); } catch (e) { el.innerHTML = `<div class="bb-empty"><b>לא ניתן לטעון את המקרה הבוחן</b></div>`; return; }
    C.times = C.data.candles.map((r) => r[0]); state.list = chapters();
    el.innerHTML = `<div class="cs-shell"><div class="cs-top" dir="rtl"><div class="cs-brand"><b>מקרה בוחן</b><span>Wyckoff Accumulation · BTC/USDT 4H · נתונים אמיתיים</span></div><div class="cs-stepper" id="cs-stepper"></div><div class="cs-sc">ציון <b id="cs-score">0/0</b></div><div class="cs-progress"><i id="cs-prog"></i></div></div>
      <div class="cs-cols"><div class="cs-chartcol" dir="ltr"><div class="cs-tb ac-draw" id="cs-draw">${window.NXDraw.toolbarHtml()}</div><div class="cs-chartwrap"><div id="cs-chart"></div></div>
        <div class="cs-legend"><span style="--c:#ff4a4a">SC · שיא מכירות</span><span style="--c:#5aa9ff">AR · קפיצה</span><span style="--c:#e8d44d">ST · בדיקה חוזרת</span><span style="--c:#2be07e">SPRING · שבירת שווא</span><span style="--c:#ffa028">SOS · סימן חוזק</span><span style="--c:#c58bff">LPS · תמיכה אחרונה</span></div></div>
        <div class="cs-story" id="cs-story" dir="rtl"><h3 id="cs-title"></h3><div id="cs-body"></div></div></div></div>`;
    initChart($("#cs-chart"));
    C.mounted = true; go(Number(lget(CK + "_ch", 0)) || 0);
    window.addEventListener("beforeunload", () => lset(CK + "_ch", C.ch));
  }
  window.NXCase = { mount, C };
})();
