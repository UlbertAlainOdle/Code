/* Prompt personas and a plain-text @ composer; saved IDs travel with each turn. */
(() => {
    const input = document.getElementById('user-input');
    const menu = document.getElementById('agent-mention-menu');
    const hint = document.getElementById('agent-reply-hint');
    const list = document.getElementById('chat-agent-list');
    const form = document.getElementById('chat-agent-form');
    const feedback = document.getElementById('chat-agent-feedback');
    let catalog = [{id: 'saya', name: 'Saya', builtin: true}];
    let editingId = null;
    let matches = [];
    let active = 0;
    let loadError = false;
    const boundary = /[\s，,。！？!?:：;；(（]/u;

    async function api(path = '', options = {}) {
        const response = await fetch('/api/chat-agents' + path, options);
        const data = await response.json();
        if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '智能体设置操作失败');
        return data;
    }

    function resetEditor() {
        editingId = null;
        form.reset();
        document.getElementById('chat-agent-editor-title').textContent = '新增智能体';
    }

    function action(label, callback) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'agent-action';
        button.textContent = label;
        button.addEventListener('click', callback);
        return button;
    }

    function renderSettings() {
        list.replaceChildren();
        catalog.forEach(agent => {
            const card = document.createElement('div');
            card.className = 'chat-agent-card';
            const name = document.createElement('strong');
            name.textContent = '@' + agent.name + (agent.builtin ? ' · 默认' : '');
            card.append(name);
            if (agent.builtin) {
                card.append(action('设置 Saya', () => openSettingsWorkspace('prompt')));
            } else {
                card.append(action('编辑', () => {
                    editingId = agent.id;
                    document.getElementById('chat-agent-name').value = agent.name;
                    document.getElementById('chat-agent-prompt').value = agent.prompt;
                    document.getElementById('chat-agent-editor-title').textContent = '编辑 ' + agent.name;
                    feedback.textContent = '';
                }));
                card.append(action('删除', () => {
                    confirmAction(`删除智能体 ${agent.name}？已有聊天记录会保留。`, async () => {
                        try {
                            await api('/' + encodeURIComponent(agent.id), {method: 'DELETE'});
                            if (editingId === agent.id) resetEditor();
                            await refresh();
                            feedback.textContent = '智能体已删除';
                        } catch (error) { feedback.textContent = error.message; }
                    }, {title: '删除智能体', confirmLabel: '删除'});
                }));
            }
            list.append(card);
        });
    }

    // Longest-name match prevents @助手 from consuming the start of @助手二号.
    function selection(text) {
        const ids = [];
        const agents = [];
        let clean = '';
        const ordered = [...catalog].sort((a, b) => b.name.length - a.name.length);
        for (let i = 0; i < text.length;) {
            const mention = text[i] === '@' && (i === 0 || boundary.test(text[i - 1]));
            const agent = mention && ordered.find(a => {
                const candidate = text.slice(i + 1, i + 1 + a.name.length);
                const next = text[i + 1 + a.name.length];
                return candidate.toLocaleLowerCase() === a.name.toLocaleLowerCase() &&
                    (!next || !/[\p{L}\p{N}_-]/u.test(next));
            });
            if (agent) {
                if (!ids.includes(agent.id)) { ids.push(agent.id); agents.push(agent); }
                i += agent.name.length + 1;
            } else { clean += text[i++]; }
        }
        return {ids, agents, text: clean.trim()};
    }

    function tokenAtCursor() {
        const before = input.value.slice(0, input.selectionStart);
        const found = before.match(/(^|[\s，,。！？!?:：;；(（])@([\p{L}\p{N}_-]*)$/u);
        return found ? {start: before.length - found[2].length - 1, end: before.length, query: found[2]} : null;
    }

    function closeMenu() {
        menu.hidden = true;
        input.setAttribute('aria-expanded', 'false');
        input.removeAttribute('aria-activedescendant');
    }

    function choose(agent) {
        const token = tokenAtCursor();
        if (!token) return;
        input.setRangeText('@' + agent.name + ' ', token.start, token.end, 'end');
        input.focus();
        input.dispatchEvent(new Event('input', {bubbles: true}));
        closeMenu();
    }

    function renderMenu() {
        menu.replaceChildren();
        matches.forEach((agent, index) => {
            const button = document.createElement('button');
            button.type = 'button';
            button.id = 'agent-mention-option-' + index;
            button.className = 'agent-mention-option';
            button.setAttribute('role', 'option');
            button.setAttribute('aria-selected', String(index === active));
            button.textContent = '@' + agent.name;
            button.addEventListener('mousedown', e => e.preventDefault());
            button.addEventListener('click', () => choose(agent));
            menu.append(button);
        });
        menu.hidden = !matches.length;
        input.setAttribute('aria-expanded', String(matches.length > 0));
        if (matches.length) input.setAttribute('aria-activedescendant', 'agent-mention-option-' + active);
        else input.removeAttribute('aria-activedescendant');
    }

    function update() {
        const selected = selection(input.value);
        hint.textContent = selected.ids.length
            ? `将收到 ${selected.ids.length} 条回复：${selected.agents.map(a => a.name).join('、')}`
            : '未 @ 时由 Saya 回复；@ 几位就收到几条回复。';
        const token = tokenAtCursor();
        if (!token) { closeMenu(); return; }
        matches = catalog.filter(a => a.name.toLocaleLowerCase().includes(token.query.toLocaleLowerCase()));
        active = 0;
        renderMenu();
    }

    async function refresh() {
        try {
            const data = await api();
            catalog = data.agents;
            loadError = false;
            renderSettings();
            update();
        } catch (error) { loadError = true; feedback.textContent = error.message; }
    }

    form.addEventListener('submit', async event => {
        event.preventDefault();
        const button = form.querySelector('[type="submit"]');
        button.disabled = true;
        try {
            await api(editingId ? '/' + encodeURIComponent(editingId) : '', {
                method: editingId ? 'PUT' : 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({name: document.getElementById('chat-agent-name').value.trim(),
                                      prompt: document.getElementById('chat-agent-prompt').value.trim()})
            });
            resetEditor();
            await refresh();
            feedback.textContent = '已保存，可在聊天框 @ 这位智能体';
        } catch (error) { feedback.textContent = error.message; }
        finally { button.disabled = false; }
    });
    document.getElementById('chat-agent-new').addEventListener('click', resetEditor);
    input.setAttribute('aria-controls', menu.id);
    input.setAttribute('aria-autocomplete', 'list');
    input.setAttribute('aria-expanded', 'false');
    input.addEventListener('input', update);
    input.addEventListener('click', update);
    input.addEventListener('keydown', event => {
        if (menu.hidden || event.isComposing) return;
        if (['ArrowDown', 'ArrowUp', 'Enter', 'Tab', 'Escape'].includes(event.key)) {
            event.preventDefault();
            event.stopImmediatePropagation();
            if (event.key === 'Escape') { closeMenu(); return; }
            if (event.key === 'Enter' || event.key === 'Tab') { choose(matches[active]); return; }
            active = (active + (event.key === 'ArrowDown' ? 1 : -1) + matches.length) % matches.length;
            renderMenu();
            menu.children[active]?.scrollIntoView({block: 'nearest'});
        }
    }, true);
    document.getElementById('agent-mention-button').addEventListener('click', () => {
        input.focus();
        if (!tokenAtCursor()) {
            const start = input.selectionStart;
            const prefix = start && !boundary.test(input.value[start - 1]) ? ' @' : '@';
            input.setRangeText(prefix, start, input.selectionEnd, 'end');
        }
        update();
    });
    document.addEventListener('click', e => {
        if (e.target !== input && !menu.contains(e.target) && !e.target.closest('#agent-mention-button')) closeMenu();
    });
    window.SayaAgents = {selection, update, refresh, get loadError() { return loadError; }};
    window.SayaAgents.ready = refresh();
})();
