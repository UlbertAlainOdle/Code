/* Public task progress, continuous mood color, and one-owner speech controls. */
(() => {
    'use strict';
    const sayaSettings = window.SayaSettingsStore?.storage || localStorage;
    const pageReady = window.SayaWebWindow?.whenReady || ((fn,options)=>document.addEventListener('DOMContentLoaded',fn,options));
    let owner = null, speechRun = 0, syncThinking = () => {};
    const number = value => Math.max(0, Math.min(100, Number(value) || 0));
    const dimensions = [
        ['happiness','快乐',50], ['excitement','活力',50], ['stress','压力',0], ['sadness','低落',10],
        ['anger','愤怒',0], ['curiosity','好奇',40], ['affection','亲近',50], ['confidence','从容',65]
    ];
    function placeTip(chip, tip) {
        const anchor = chip.getBoundingClientRect(), box = tip.getBoundingClientRect();
        const boundary = chip.closest('#chat-content')?.getBoundingClientRect() || {top:0, bottom:window.innerHeight};
        if (anchor.bottom < boundary.top || anchor.top > boundary.bottom) { tip.hidePopover(); return; }
        tip.style.left = Math.max(8, Math.min(window.innerWidth - box.width - 8, anchor.right - box.width)) + 'px';
        tip.style.top = Math.max(8, anchor.top - box.height - 10) + 'px';
    }
    function paintMood(chip, state, estimated = false) {
        if (!chip) return;
        chip.removeAttribute('title');
        const signature = JSON.stringify([state, estimated]);
        // Polling the same snapshot must not dismiss a tooltip or rebuild the
        // focused control's contents.
        if (chip._moodPaintSignature === signature) return;
        chip._moodPaintSignature = signature;
        const wasOpen = chip.querySelector('.mood-histogram')?.matches?.(':popover-open');
        chip.replaceChildren();
        chip.classList.add('mood-orb');
        if (!state) { chip.hidden = true; return; }
        chip.hidden = false;
        const values = dimensions.map(([key,,fallback]) => number(state[key] ?? fallback));
        estimated = estimated || state.estimated_dimensions?.length === dimensions.length;
        const labels = dimensions.map(([,label]) => label);
        const [joy,energy,stress,sadness,anger,curiosity,affection,confidence] = values;
        chip.style.setProperty('--mood-color', `hsl(${268 - joy * 2.2} ${45 + energy * .25}% ${58 + confidence * .1}%)`);
        chip.style.setProperty('--mood-accent', `hsl(${240 - curiosity * .65} ${44 + curiosity * .24}% 72%)`);
        chip.style.setProperty('--mood-warm', `hsl(${335 + affection * .3 - Math.max(stress,anger) * 2.7} 64% 75%)`);
        chip.style.setProperty('--mood-depth', `hsl(${245 + sadness * .25} 36% ${23 + confidence * .12}%)`);
        chip.style.setProperty('--mood-turn', `${joy * 1.3 + curiosity * .8 - sadness * .4}deg`);
        chip.setAttribute('aria-label', labels.map((x, i) => `${x} ${values[i]}`).join('，') + (estimated ? '，历史估计' : state.estimated_dimensions?.length ? '，新增维度为历史估计' : ''));
        chip.setAttribute('aria-pressed', String(!!(state.is_locked || state.locked)));
        chip.removeAttribute('title');
        const dot = document.createElement('span'); dot.className = 'mood-orb-dot'; dot.setAttribute('aria-hidden', 'true');
        const tip = document.createElement('span'); tip.className = 'mood-histogram'; tip.setAttribute('aria-hidden', 'true');
        const heading = document.createElement('span'); heading.className = 'mood-histogram-title';
        heading.textContent = '情绪光谱'; tip.append(heading);
        const subtitle = document.createElement('span'); subtitle.className = 'mood-histogram-subtitle';
        subtitle.textContent = estimated ? '历史记录估计 · 0–100' : state.estimated_dimensions?.length ? '新增维度为历史估计 · 0–100' : '本轮快照 · 0–100'; tip.append(subtitle);
        const plot = document.createElement('span'); plot.className = 'mood-histogram-plot';
        values.forEach((value, i) => {
            const column = document.createElement('span'); column.className = 'mood-histogram-column';
            const count = document.createElement('span'); count.textContent = String(Math.round(value));
            const track = document.createElement('span'); track.className = 'mood-histogram-track';
            const bar = document.createElement('span'); bar.className = 'mood-histogram-bar'; bar.style.height = value + '%'; bar.dataset.dimension = String(i);
            const label = document.createElement('span'); label.textContent = labels[i];
            track.append(bar); column.append(count, track, label); plot.append(column);
        });
        tip.append(plot); chip.append(dot, tip);
        // Top-layer popovers stay readable over the floating character canvas.
        if (typeof tip.showPopover === 'function') {
            tip.setAttribute('popover', 'manual');
            const show = () => {
                if (!tip.matches(':popover-open')) tip.showPopover();
                placeTip(chip, tip);
            };
            const hide = () => { if (tip.matches(':popover-open')) tip.hidePopover(); };
            chip.onmouseenter = show;
            chip.onmouseleave = () => { if (!chip.matches(':focus-visible')) hide(); };
            chip.onfocus = () => window.requestAnimationFrame(() => { if (document.activeElement === chip) show(); });
            chip.onblur = () => { if (!chip.matches(':hover')) hide(); };
            if (wasOpen || chip.matches(':focus-visible') || chip.matches(':hover')) window.requestAnimationFrame(show);
        }
    }
    function attachMood(row, state, estimated = false) {
        row._moodState = state ? structuredClone(state) : null;
        const actions = row.querySelector('.msg-actions');
        if (!actions) return;
        let chip = actions.querySelector('.message-mood');
        if (!chip) { chip = document.createElement('button'); chip.type = 'button'; chip.className = 'btn-action message-mood'; actions.append(chip); }
        paintMood(chip, state, estimated);
    }
    function phase(row, phase, message) {
        const bubble = row?.querySelector('.message-bubble.ai');
        if (!bubble) return;
        let details = bubble.querySelector('.response-progress');
        if (!details) {
            details = document.createElement('details'); details.className = 'response-progress'; details.open = true;
            const summary = document.createElement('summary'); summary.setAttribute('role', 'status'); summary.setAttribute('aria-live', 'polite');
            const list = document.createElement('ol'); details.append(summary, list); bubble.insertBefore(details, bubble.firstChild);
        }
        const summary = details.querySelector('summary');
        if (phase === 'completed' || phase === 'stopped' || phase === 'error') {
            summary.textContent = message || (phase === 'completed' ? '处理完成' : '已停止'); details.dataset.finished = 'true'; details.open = false; return;
        }
        const list = details.querySelector('ol');
        if (list.lastChild?.textContent !== message) { const item = document.createElement('li'); item.textContent = message || phase; list.append(item); }
        if (details.dataset.finished !== 'true') summary.textContent = message || phase;
    }
    function buttonState(button, playing) {
        if (!button) return;
        button.classList.toggle('is-playing', playing);
        button.setAttribute('aria-pressed', String(playing));
        button.setAttribute('aria-label', playing ? '停止播放此回复' : '播放此回复');
        button.title = playing ? '停止播放' : '播放回复';
    }
    function stopSpeech() {
        ++speechRun; window.SayaSpeech?.stop(); buttonState(owner, false); owner = null;
    }
    async function speak(button, text, context) {
        if (owner === button && button) { stopSpeech(); return; }
        stopSpeech(); const run = speechRun; owner = button; buttonState(button, true);
        try { await window.SayaSpeech.speak(text, context); }
        catch (error) { if (run === speechRun && error.name !== 'AbortError') window.showToast?.(error.message || '语音播放失败', {type: 'error'}); }
        finally { if (run === speechRun) { buttonState(button, false); owner = null; } }
    }
    function busy(value) {
        const send = document.getElementById('send-btn') || document.querySelector('.input-bar .btn-send');
        const stop = document.getElementById('chat-stop-btn');
        if (send) { send.hidden = value; send.disabled = value; }
        if (stop) stop.hidden = !value;
    }
    function initThinking() {
        const select = document.getElementById('thinking-effort'); if (!select) return;
        select.value = sayaSettings.getItem('saya_thinking_effort') || 'auto';
        select.onchange = () => sayaSettings.setItem('saya_thinking_effort', select.value);
        let followedGlobal = false;
        const sync = actualModel => {
            const mode = document.getElementById('model-select')?.value;
            const shared = window.SayaQQSettings?.thinkingConfiguration?.();
            if (mode === 'api' && shared?.follow_global) {
                followedGlobal = true;
                select.value = shared.thinking_effort;
                select.disabled = true;
                select.title = '跟随共享默认；请在 Settings → QQ 接入 → 共享默认中修改思考档位';
                return;
            }
            if (followedGlobal) {
                select.value = sayaSettings.getItem('saya_thinking_effort') || 'auto';
                followedGlobal = false;
            }
            const model = (typeof actualModel === 'string' ? actualModel : window.getSayaChatContext?.().thinkingModel || (window.getSayaChatContext?.().strongLogic ? document.getElementById('api-strong-logic-model-input')?.value : document.getElementById('api-model-input')?.value)) || '';
            let host = ''; try { host = new URL(document.getElementById('api-url-input')?.value || '').hostname; } catch (_) {}
            const localProfile = window.SayaLocalProfile?.status();
            const usesServer = (localProfile?.prefer_local && localProfile.configured.api_key) || document.getElementById('api-use-shared-credentials')?.checked;
            const supported = mode === 'api' && (usesServer ? shared?.thinking_supported === true : host === 'api.deepseek.com' && /^(deepseek-v4|deepseek-flash$)/i.test(model.split('/').pop()));
            select.disabled = !supported;
            select.title = supported ? 'DeepSeek：自动、关闭、轻度、深度或最高思考' : '当前接口/模型未验证思考档位；请求使用自动';
        };
        syncThinking = sync;
        ['model-select','api-model-input','api-url-input','api-strong-logic-model-input','api-memory-strong-model-input','api-multimodal-model-input','combined-mode','api-use-shared-credentials'].forEach(id => document.getElementById(id)?.addEventListener('change', sync));
        window.addEventListener?.('saya-web-configuration', sync);
        document.addEventListener('focusin', sync);
        document.querySelector('.thinking-picker')?.addEventListener('pointerenter', sync);
        sync();
    }
    window.SayaChatUI = { paintMood, attachMood, phase, speak, stopSpeech, busy,
        thinkingEffort: actualModel => { syncThinking(actualModel); const el = document.getElementById('thinking-effort'); const shared = window.SayaQQSettings?.thinkingConfiguration?.(); return document.getElementById('model-select')?.value === 'api' && shared?.follow_global ? shared.thinking_effort : el && !el.disabled ? el.value : 'auto'; } };
    pageReady( () => { busy(false); initThinking(); });
    document.addEventListener('scroll', () => document.querySelectorAll('.mood-histogram[popover]').forEach(tip => {
        const chip = tip.closest('.mood-orb');
        if (!tip.matches(':popover-open') && chip.matches(':focus-visible')) tip.showPopover();
        if (tip.matches(':popover-open')) placeTip(chip, tip);
    }), true);
})();
