// === BUILD MARKER v20260806-g11 === (if you see this in console, the latest code is loaded)
    console.log('%c[app.js] BUILD v20260806-g11 loaded', 'color: #4a9eff; font-weight: bold;');

// Inline icon helper (ChatGPT/Grok UI refresh): renders the CSS-mask icon set
// defined in style.css. Usage: ic('trash') / ic('plus', 'icn-sm').
    function ic(name, cls) {
        return `<span class="icn icn-${name}${cls ? ' ' + cls : ''}" aria-hidden="true"></span>`;
    }
    window.ic = ic;

// Error handler
    window.onerror = function(msg, url, line, col, error) {
        if (msg.includes("ResizeObserver")) return;
        console.error("Global Error:", msg, "Line:", line, error);
    };

    function escapeHtml(value) {
        return String(value ?? '').replace(/[&<>"']/g, char => ({
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#039;'
        })[char]);
    }

    function escapeJsString(value) {
        return String(value ?? '')
            .replace(/\\/g, '\\\\')
            .replace(/'/g, "\\'")
            .replace(/\r/g, '\\r')
            .replace(/\n/g, '\\n')
            .replace(/&/g, '\\x26')
            .replace(/"/g, '\\x22')
            .replace(/</g, '\\x3C')
            .replace(/>/g, '\\x3E');
    }

    function sanitizeHtml(html) {
        const parser = new DOMParser();
        const doc = parser.parseFromString(`<div>${html}</div>`, 'text/html');
        const blocked = new Set(['SCRIPT', 'STYLE', 'IFRAME', 'OBJECT', 'EMBED', 'LINK', 'META', 'FORM']);
        doc.body.querySelectorAll('*').forEach(element => {
            if (blocked.has(element.tagName)) {
                element.remove();
                return;
            }
            Array.from(element.attributes).forEach(attribute => {
                const name = attribute.name.toLowerCase();
                const value = attribute.value.trim();
                if (name.startsWith('on') || name === 'srcdoc') {
                    element.removeAttribute(attribute.name);
                } else if ((name === 'href' || name === 'src') &&
                           /^(?:javascript|vbscript|data):/i.test(value)) {
                    element.removeAttribute(attribute.name);
                }
            });
            if (element.tagName === 'A') {
                element.setAttribute('rel', 'noopener noreferrer');
            }
        });
        return doc.body.firstElementChild?.innerHTML || '';
    }

    function renderMarkdownSafe(text) {
        return sanitizeHtml(marked.parse(String(text ?? '')));
    }

// Workspace gradient customization was removed from the UI (2026-09-14):
// saved gradients stay untouched in localStorage under the original key
// and are simply no longer read or applied.

// ======================================================================
// UNIFIED FEEDBACK + CONTROL COMPONENTS (secondary interface system)
// Toast / ConfirmDialog / PromptDialog replace every native dialog;
// seg controls and switches bind to the original hidden form fields so
// all existing save/load logic keeps working untouched.
// ======================================================================

    function ensureToastWrap() {
        let wrap = document.querySelector('.st-toast-wrap');
        if (!wrap) {
            wrap = document.createElement('div');
            wrap.className = 'st-toast-wrap';
            document.body.appendChild(wrap);
        }
        return wrap;
    }

    function showToast(message, { type = 'success', duration = 2600 } = {}) {
        const wrap = ensureToastWrap();
        const toast = document.createElement('div');
        toast.className = `st-toast ${type}`;
        const icon = type === 'error' ? 'alert' : (type === 'success' ? 'check-circle' : 'note');
        toast.innerHTML = `${ic(icon, 'icn-sm')}<span>${escapeHtml(message)}</span>`;
        wrap.appendChild(toast);
        setTimeout(() => {
            toast.classList.add('leaving');
            setTimeout(() => toast.remove(), 240);
        }, duration);
    }
    window.showToast = showToast;

    // Shared state renderer for every secondary surface.  Keeping loading,
    // empty and error states in one component prevents individual workspaces
    // from falling back to raw text or browser-native error presentation.
    function renderSurfaceState(target, {
        kind = 'error',
        title = kind === 'error' ? 'Something went wrong' : 'Nothing here yet',
        message = '',
        retry = null,
        retryLabel = 'Retry',
        details = null,
        icon = kind === 'error' ? 'alert' : 'layers'
    } = {}) {
        const element = typeof target === 'string' ? document.querySelector(target) : target;
        if (!element) return null;
        const state = document.createElement('div');
        state.className = kind === 'error' ? 'st-page-error' : (kind === 'loading' ? 'st-empty st-loading-state' : 'st-empty');
        state.setAttribute('role', kind === 'error' ? 'alert' : 'status');
        state.setAttribute('aria-live', kind === 'error' ? 'assertive' : 'polite');
        state.appendChild(document.createRange().createContextualFragment(ic(icon)));
        const heading = document.createElement('strong');
        heading.textContent = title;
        state.appendChild(heading);
        if (message) {
            const copy = document.createElement('span');
            copy.textContent = message;
            state.appendChild(copy);
        }
        if (typeof retry === 'function') {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'workspace-secondary-action';
            button.innerHTML = `${ic('refresh', 'icn-sm')}<span>${escapeHtml(retryLabel)}</span>`;
            button.addEventListener('click', retry);
            state.appendChild(button);
        }
        if (details) {
            const wrapper = document.createElement('div');
            wrapper.innerHTML = technicalDetailsHtml(details);
            const detail = wrapper.firstElementChild;
            if (detail) state.appendChild(detail);
        }
        element.replaceChildren(state);
        return state;
    }
    window.renderSurfaceState = renderSurfaceState;

    function technicalDetailsHtml(error) {
        const text = String(error?.message || error || '').slice(0, 1200);
        if (!text) return '';
        return `<details class="st-dialog-details"><summary>Technical details</summary><pre>${escapeHtml(text)}</pre></details>`;
    }

    function buildStDialog({ title, bodyHtml, confirmLabel = 'Confirm', cancelLabel = 'Cancel', danger = false, onConfirm, onCancel }) {
        const overlay = document.createElement('div');
        overlay.className = 'st-dialog-overlay';
        overlay.innerHTML = `
            <div class="st-dialog" role="dialog" aria-modal="true" aria-label="${escapeHtml(title)}">
                <div class="st-dialog-title">${escapeHtml(title)}</div>
                <div class="st-dialog-body">${bodyHtml}</div>
                <div class="st-dialog-footer">
                    <button type="button" class="st-btn-cancel workspace-secondary-action"><span class="btn-label-ic">${ic('close', 'icn-sm')}${escapeHtml(cancelLabel)}</span></button>
                    <button type="button" class="st-btn-confirm ${danger ? 'workspace-secondary-action' : 'workspace-primary-action'}" ${danger ? 'style="color:var(--color-danger);border-color:rgba(248,113,113,.4);"' : ''}><span class="btn-label-ic">${danger ? ic('alert', 'icn-sm') : ic('check', 'icn-sm')}${escapeHtml(confirmLabel)}</span></button>
                </div>
            </div>`;
        let settled = false;
        const finish = cancelled => {
            if (settled) return;
            settled = true;
            overlay.remove();
            if (cancelled) onCancel?.();
        };
        const closeConfirmed = () => { if (!settled) { settled = true; overlay.remove(); } };
        overlay.querySelector('.st-btn-cancel').addEventListener('click', () => finish(true));
        overlay.addEventListener('mousedown', event => { if (event.target === overlay) finish(true); });
        overlay.querySelector('.st-btn-confirm').addEventListener('click', async () => {
            try { await onConfirm?.(closeConfirmed); } catch (error) { console.error(error); }
        });
        overlay.addEventListener('saya:esc-close', () => finish(true));
        document.body.appendChild(overlay);
        const focusTarget = overlay.querySelector('input, textarea') || overlay.querySelector('.st-btn-confirm');
        focusTarget?.focus();
        return { overlay, close: () => finish(true) };
    }

    function showConfirmDialog({ title, message, impact = [], details = null, confirmLabel = 'Confirm', cancelLabel = 'Cancel', danger = false, onConfirm, onCancel }) {
        const list = impact.length ? `<ul>${impact.map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul>` : '';
        const body = `<p>${escapeHtml(message)}</p>${list}${details ? technicalDetailsHtml(details) : ''}`;
        return buildStDialog({ title, bodyHtml: body, confirmLabel, cancelLabel, danger, onConfirm, onCancel });
    }
    window.showConfirmDialog = showConfirmDialog;

    // confirmAction: drop-in replacement for native confirm() guards.
    // Derives title/impact/danger from the message text.
    function confirmAction(message, run, { title, confirmLabel, danger, onCancel } = {}) {
        const lines = String(message).split('\n').map(line => line.trim()).filter(Boolean);
        const rawTitle = lines[0] || 'Please confirm';
        const derivedTitle = rawTitle.length > 90 ? rawTitle.slice(0, 87) + '…' : rawTitle;
        const rest = lines.slice(1);
        const impact = rest.filter(line => line.startsWith('-')).map(line => line.slice(1).trim());
        const messageText = rest.filter(line => !line.startsWith('-')).join(' ');
        const derivedDanger = danger !== undefined ? danger : /delete|permanent|cannot be undone|clear|remove|overwrite|rebuild|compress|switch/i.test(String(message));
        showConfirmDialog({
            title: title || derivedTitle,
            message: messageText || 'Please confirm to continue.',
            impact,
            confirmLabel: confirmLabel || 'Confirm',
            danger: derivedDanger,
            onConfirm: close => { close(); run(); },
            onCancel
        });
    }
    window.confirmAction = confirmAction;

    function showPromptDialog({ title, label, placeholder = '', value = '', multiline = false, confirmLabel = 'Save', onConfirm }) {
        const field = multiline
            ? `<textarea class="st-prompt-field" placeholder="${escapeHtml(placeholder)}">${escapeHtml(value)}</textarea>`
            : `<input type="text" class="st-prompt-field" placeholder="${escapeHtml(placeholder)}" value="${escapeHtml(value)}">`;
        const body = `<label class="form-label" style="display:block;">${escapeHtml(label)}</label>${field}`;
        const dialog = buildStDialog({
            title, bodyHtml: body, confirmLabel,
            onConfirm: close => {
                const fieldEl = dialog.overlay.querySelector('.st-prompt-field');
                const val = (fieldEl?.value ?? '').trim();
                if (!val) { fieldEl?.focus(); return; }
                onConfirm?.(val, close);
            }
        });
        return dialog;
    }
    window.showPromptDialog = showPromptDialog;

    // --- Settings Workspace: one shell, seven category panes ---------
    const SETTINGS_PANE_TITLES = {
        prompt: 'Settings · System Prompt',
        style: 'Settings · Saya Style',
        llm: 'Settings · LLM API',
        other: 'Settings · Other API',
        limits: 'Settings · Token Limits',
        mode: 'Settings · Mode Setting',
        backup: 'Settings · Backup & Restore'
    };
    let activeSettingsPane = null;

    function showSettingsPane(pane) {
        const panesRoot = document.getElementById('settings-panes');
        if (!panesRoot) return;
        activeSettingsPane = pane;
        panesRoot.querySelectorAll('.settings-pane').forEach(section => {
            section.hidden = section.dataset.pane !== pane;
        });
        document.querySelectorAll('#settings-nav .settings-nav-item').forEach(item => {
            const active = item.dataset.pane === pane;
            item.classList.toggle('active', active);
            item.setAttribute('aria-current', active ? 'true' : 'false');
        });
        document.getElementById('settings-workspace-title').textContent = SETTINGS_PANE_TITLES[pane] || 'Settings';
        // The settings workspace keeps its category navigation visible on every
        // viewport, so the header only needs the close action (no Back button).
        const backBtn = document.getElementById('settings-back-btn');
        if (backBtn) {
            backBtn.hidden = true;
            backBtn.setAttribute('aria-hidden', 'true');
        }
        const paneEl = panesRoot.querySelector(`.settings-pane[data-pane="${pane}"]`);
        if (paneEl) {
            initSegControls(paneEl);
            initSwitchControls(paneEl);
            initPasswordToggles(paneEl);
            paneEl.scrollTop = 0;
        }
    }

    function openSettingsWorkspace(pane = 'prompt') {
        const menu = document.getElementById('settings-menu');
        if (menu) menu.style.display = 'none';
        const modal = document.getElementById('settings-workspace-modal');
        if (!modal) return;
        modal.style.display = 'flex';
        showSettingsPane(pane);
    }

    function closeSettingsWorkspace() {
        const modal = document.getElementById('settings-workspace-modal');
        if (modal) modal.style.display = 'none';
    }

    function settingsNavigateBack() {
        const nav = document.getElementById('settings-nav');
        if (nav) nav.classList.remove('pane-open');
        const backBtn = document.getElementById('settings-back-btn');
        if (backBtn) {
            backBtn.hidden = true;
            backBtn.setAttribute('aria-hidden', 'true');
        }
        const title = document.getElementById('settings-workspace-title');
        if (title) title.textContent = 'Settings';
    }

    document.addEventListener('DOMContentLoaded', () => {
        document.getElementById('settings-nav')?.addEventListener('click', event => {
            const item = event.target.closest('.settings-nav-item');
            if (item) showSettingsPane(item.dataset.pane);
        });
    });
    window.openSettingsWorkspace = openSettingsWorkspace;
    window.closeSettingsWorkspace = closeSettingsWorkspace;
    window.showSettingsPane = showSettingsPane;
    window.settingsNavigateBack = settingsNavigateBack;

    // LLM connection test — only runs when the user clicks the button.
    async function testLlmConnection() {
        const statusEl = document.getElementById('llm-connection-status');
        const baseUrl = (document.getElementById('api-url-input')?.value || '').trim().replace(/\/+$/, '');
        const apiKey = document.getElementById('api-key-input')?.value || '';
        if (!baseUrl) {
            if (statusEl) { statusEl.textContent = 'Base URL required'; statusEl.className = 'st-badge danger'; }
            return;
        }
        if (statusEl) { statusEl.textContent = 'Testing…'; statusEl.className = 'st-badge running'; }
        try {
            const response = await fetch(`${baseUrl}/models`, {
                headers: apiKey ? { 'Authorization': `Bearer ${apiKey}` } : {}
            });
            if (response.ok) {
                if (statusEl) { statusEl.textContent = 'Connected'; statusEl.className = 'st-badge success'; }
            } else {
                if (statusEl) { statusEl.textContent = `Failed · ${response.status}`; statusEl.className = 'st-badge danger'; }
            }
        } catch (error) {
            if (statusEl) { statusEl.textContent = 'Unreachable'; statusEl.className = 'st-badge danger'; }
            console.warn('LLM connection test failed:', error);
        }
    }
    window.testLlmConnection = testLlmConnection;

    // Token limit presets
    const TOKEN_LIMIT_PRESETS = {
        conservative: { api: 4096, qwen3_30b_q5: 2048, mistral_22b_q6: 2048, mistral_22b_q5: 2048, saya_r1_q5: 2048 },
        balanced: { api: 8192, qwen3_30b_q5: 4096, mistral_22b_q6: 4096, mistral_22b_q5: 4096, saya_r1_q5: 4096 },
        long: { api: 16384, qwen3_30b_q5: 8192, mistral_22b_q6: 8192, mistral_22b_q5: 8192, saya_r1_q5: 8192 }
    };

    function applyTokenPreset(name) {
        const preset = TOKEN_LIMIT_PRESETS[name];
        if (!preset) return;
        Object.entries(preset).forEach(([key, value]) => {
            const input = document.getElementById(`output-limit-${key}`);
            if (input) input.value = String(value);
        });
        if (typeof updateOutputCostPreview === 'function') updateOutputCostPreview();
    }
    window.applyTokenPreset = applyTokenPreset;

    document.addEventListener('DOMContentLoaded', () => {
        document.getElementById('token-preset-seg')?.addEventListener('click', event => {
            const btn = event.target.closest('.seg-btn[data-preset]');
            if (!btn) return;
            document.querySelectorAll('#token-preset-seg .seg-btn').forEach(other => other.classList.toggle('active', other === btn));
            applyTokenPreset(btn.dataset.preset);
        });
    });

    // Other API provider badges reflect configuration state
    function refreshProviderStatusBadges() {
        const translationConfigured = !!(document.getElementById('translation-secret-id-input')?.value && document.getElementById('translation-secret-key-input')?.value);
        const ttsConfigured = !!(document.getElementById('tts-api-key-input')?.value);
        const setBadge = (key, configured, label) => {
            const badge = document.querySelector(`.provider-status[data-status="${key}"]`);
            if (!badge) return;
            badge.textContent = configured ? 'Configured' : (label || 'Not configured');
            badge.className = `provider-status st-badge${configured ? ' success' : ''}`;
        };
        setBadge('translation', translationConfigured);
        setBadge('tts', ttsConfigured);
    }
    document.addEventListener('input', event => {
        if (event.target.closest?.('.settings-pane[data-pane="other"]')) refreshProviderStatusBadges();
    });

    // --- Esc + focus management for the modal layer stack -------------
    function visibleLayers() {
        return Array.from(document.querySelectorAll('.st-dialog-overlay, .uni-popover:not([hidden]), .modal-overlay'))
            .filter(el => el.style.display !== 'none' && el.getClientRects().length > 0);
    }

    function closeLayerElement(layer) {
        if (layer.classList.contains('st-dialog-overlay')) {
            if (layer.dataset.persistent === 'true') { layer.hidden = true; }
            else {
                layer.dispatchEvent(new CustomEvent('saya:esc-close'));
                if (layer.isConnected) layer.remove();
            }
            return;
        }
        if (layer.classList.contains('uni-popover')) { layer.remove(); return; }
        const closeBtn = layer.querySelector('.modal-close-btn[onclick]');
        const handler = closeBtn?.getAttribute('onclick');
        if (handler) { try { new Function(handler)(); } catch (e) { console.error(e); } }
        else layer.style.display = 'none';
    }

    // --- anchored context menu (row "…" buttons, export actions) -------
    function showMenuAt(anchor, items, { align = 'start' } = {}) {
        document.querySelectorAll('.uni-popover').forEach(p => p.remove());
        const rect = anchor.getBoundingClientRect();
        const popover = document.createElement('div');
        popover.className = 'uni-popover';
        popover.setAttribute('role', 'menu');
        items.forEach(item => {
            if (item === '-') {
                const sep = document.createElement('div');
                sep.className = 'uni-menu-sep';
                popover.appendChild(sep);
                return;
            }
            const button = document.createElement('button');
            button.type = 'button';
            button.className = `uni-menu-item${item.danger ? ' danger' : ''}`;
            button.setAttribute('role', 'menuitem');
            button.innerHTML = `${ic(item.icon || 'chevron-right', 'icn-sm')}<span>${escapeHtml(item.label)}</span>`;
            button.addEventListener('click', () => {
                popover.remove();
                item.onClick?.();
            });
            popover.appendChild(button);
        });
        document.body.appendChild(popover);
        // viewport collision handling
        const box = popover.getBoundingClientRect();
        let left = align === 'end' ? rect.right - box.width : rect.left;
        left = Math.max(8, Math.min(left, window.innerWidth - box.width - 8));
        let top = rect.bottom + 6;
        if (top + box.height > window.innerHeight - 8) top = Math.max(8, rect.top - box.height - 6);
        popover.style.left = `${left}px`;
        popover.style.top = `${top}px`;
        const dismiss = event => {
            if (!popover.contains(event.target)) { popover.remove(); document.removeEventListener('mousedown', dismiss); }
        };
        setTimeout(() => document.addEventListener('mousedown', dismiss), 0);
        return popover;
    }
    window.showMenuAt = showMenuAt;

    document.addEventListener('keydown', event => {
        const layers = visibleLayers();
        if (!layers.length) return;
        const top = layers[layers.length - 1];
        if (event.key === 'Escape') {
            event.preventDefault();
            closeLayerElement(top);
        } else if (event.key === 'Tab') {
            const focusables = top.querySelectorAll('button, [href], input:not([type="hidden"]), select, textarea, [tabindex]:not([tabindex="-1"])');
            const visible = Array.from(focusables).filter(el => el.offsetParent !== null || el === document.activeElement);
            if (!visible.length) return;
            const first = visible[0], last = visible[visible.length - 1];
            if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
            else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
            else if (!top.contains(document.activeElement)) { event.preventDefault(); first.focus(); }
        }
    });

    // --- segmented controls / switches bound to original fields -------
    function syncSegFor(selectEl) {
        const seg = document.querySelector(`.seg[data-seg-for="${selectEl.id}"]`);
        if (!seg) return;
        seg.querySelectorAll('.seg-btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.value === selectEl.value);
        });
    }

    function initSegControls(root = document) {
        root.querySelectorAll?.('.seg[data-seg-for]').forEach(seg => {
            const selectEl = document.getElementById(seg.dataset.segFor);
            if (!selectEl) return;
            if (seg.dataset.segBound) { syncSegFor(selectEl); return; }
            seg.dataset.segBound = '1';
            seg.addEventListener('click', event => {
                const btn = event.target.closest('.seg-btn');
                if (!btn) return;
                selectEl.value = btn.dataset.value;
                selectEl.dispatchEvent(new Event('change', { bubbles: true }));
                selectEl.dispatchEvent(new Event('input', { bubbles: true }));
                syncSegFor(selectEl);
            });
            if (selectEl.value) syncSegFor(selectEl);
        });
    }

    function initSwitchControls(root = document) {
        root.querySelectorAll?.('.switch input[data-switch-for]').forEach(input => {
            const target = document.getElementById(input.dataset.switchFor);
            if (!target) return;
            const sync = () => {
                const on = target.type === 'checkbox' ? target.checked : target.value === input.dataset.onValue;
                input.checked = on;
            };
            if (input.dataset.switchBound) { sync(); return; }
            input.dataset.switchBound = '1';
            input.addEventListener('change', () => {
                if (target.type === 'checkbox') {
                    target.checked = input.checked;
                } else {
                    target.value = input.checked ? (input.dataset.onValue || 'on') : (input.dataset.offValue || 'off');
                }
                target.dispatchEvent(new Event('change', { bubbles: true }));
            });
            target.addEventListener('change', sync);
            if (target.value !== '' || target.type === 'checkbox') sync();
        });
    }

    function initPasswordToggles(root = document) {
        root.querySelectorAll?.('.pw-toggle').forEach(btn => {
            if (btn.dataset.pwBound) return;
            btn.dataset.pwBound = '1';
            btn.addEventListener('click', () => {
                const input = btn.parentElement?.querySelector('input');
                if (!input) return;
                const show = input.type === 'password';
                input.type = show ? 'text' : 'password';
                btn.innerHTML = ic(show ? 'close' : 'eye', 'icn-sm');
                btn.title = show ? 'Hide' : 'Show';
            });
        });
    }

    document.addEventListener('DOMContentLoaded', () => {
        initSegControls(document);
        initSwitchControls(document);
        initPasswordToggles(document);
    });
    // re-bind controls after any modal body is (re)rendered
    const _segObserver = new MutationObserver(mutations => {
        mutations.forEach(mutation => mutation.addedNodes.forEach(node => {
            if (node.nodeType !== 1) return;
            initSegControls(node);
            initSwitchControls(node);
            initPasswordToggles(node);
        }));
    });
    document.addEventListener('DOMContentLoaded', () => {
        _segObserver.observe(document.body, { childList: true, subtree: true });
    });

    // --- API FIX FOR SEPARATE FRONTEND HOSTING ---
    // Redirects API calls to port 8000 if frontend is running on a different port (e.g. VSCode Live Server)
    const originalFetch = window.fetch;
    window.fetch = function(url, options) {
        if (typeof url === 'string' && url.startsWith('/')) {
            const API_BASE = window.location.port === '8000' ? '' : 'http://127.0.0.1:8000';
            url = API_BASE + url;
        }
        return originalFetch(url, options);
    };

    // --- SIDEBAR TOGGLE ---
    function updateRoleNavigationButton() {
        const panelButton = document.getElementById('btn-toggle-sidebar-panel');
        const chatButton = document.getElementById('btn-toggle-sidebar-chat');
        const panel = document.querySelector('.model-panel');
        if (!panel) return;
        const assistantBack = currentRoleMode === 'assistant' && panel.classList.contains('collapsed');
        if (panelButton) {
            panelButton.innerHTML = ic('sidebar-collapse', 'icn-sm');
            panelButton.title = 'Hide Sidebar';
            panelButton.setAttribute('aria-label', 'Hide Sidebar');
        }
        if (chatButton) {
            chatButton.innerHTML = ic(assistantBack ? 'arrow-left' : 'sidebar-expand', 'icn-sm');
            chatButton.title = assistantBack ? 'Back to Assistant' : 'Show Sidebar';
            chatButton.setAttribute('aria-label', chatButton.title);
        }
    }

    function setCurrentRoleMode(mode, options = {}) {
        const previousRoleMode = currentRoleMode || '';
        const nextRoleMode = mode || '';
        if (nextRoleMode === 'assistant' && previousRoleMode !== 'assistant') {
            const alreadyChanged = assistantMemoryChangedSinceEntry === true;
            assistantRoleEntryMemoryStamp = assistantRoleState?.last_read_at || '';
            assistantMemoryChangedSinceEntry = alreadyChanged;
            localStorage.setItem('saya_assistant_role_entry_memory_stamp', assistantRoleEntryMemoryStamp);
            localStorage.setItem('saya_assistant_role_memory_changed', alreadyChanged ? 'true' : 'false');
        }
        currentRoleMode = nextRoleMode;
        if (options.persist !== false) {
            localStorage.setItem('saya_current_role_mode', currentRoleMode);
        }
        if (previousRoleMode === 'assistant' && currentRoleMode !== 'assistant') {
            assistantRoleEntryMemoryStamp = '';
            assistantMemoryChangedSinceEntry = false;
            localStorage.removeItem('saya_assistant_role_entry_memory_stamp');
            localStorage.removeItem('saya_assistant_role_memory_changed');
        }
        document.body.classList.toggle('assistant-role-active', currentRoleMode === 'assistant');
        syncAssistantStrongLogicToggle();
        updateRoleNavigationButton();
    }

    function collapseSidebarForAssistantChat() {
        const panel = document.querySelector('.model-panel');
        const menu = document.getElementById('settings-menu');
        if (panel && !panel.classList.contains('collapsed')) {
            panel.classList.add('collapsed');
        }
        if (menu) menu.style.display = 'none';
        document.body.classList.add('sidebar-collapsed');
        updateRoleNavigationButton();
        setTimeout(() => {
            window.dispatchEvent(new Event('resize'));
            onWindowResize();
        }, 300);
    }

    function toggleSidebar() {
        const panel = document.querySelector('.model-panel');
        if (currentRoleMode === 'assistant' && panel?.classList.contains('collapsed')) {
            openAssistantRoleModal({fromChat: true});
            return;
        }
        panel.classList.toggle('collapsed');
        const menu = document.getElementById('settings-menu');
        if (panel.classList.contains('collapsed') && menu) {
            menu.style.display = 'none';
        }
        
        // Resize three.js renderer
        setTimeout(() => {
            window.dispatchEvent(new Event('resize'));
            onWindowResize();
            // Center chat content if collapsed
            const chatContent = document.getElementById('chat-content');
            if (panel.classList.contains('collapsed')) {
                 // We might want to add a class to chat content to limit width
                 // But chat content structure is simple divs.
                 // Let's just let it expand for now, or add a class to body
                 document.body.classList.add('sidebar-collapsed');
            } else {
                 document.body.classList.remove('sidebar-collapsed');
            }
            updateRoleNavigationButton();
        }, 300); // Wait for transition
    }

    // --- SETTINGS LOGIC ---
    const API_SETTINGS_STORAGE_KEY = 'saya_api_settings_v1';
    const DAILY_BRIEF_CARD_SETTINGS_KEY = 'saya_daily_brief_card_settings_v1';
    const DAILY_BRIEF_DEFAULT_BORDER_COLORS = Object.freeze({
        completed: '#4f9a68',
        partial: '#6f87b8',
        active: '#6aa5ff',
        pending: '#5d5d5d',
        missed: '#d65d6e',
        no_tasks: '#343434'
    });
    const DAILY_BRIEF_CARD_COLORS = Object.freeze([
        '#e6e6e6', '#a3a3a3', '#5f6368', '#ef4444', '#f97316', '#f59e0b',
        '#eab308', '#84cc16', '#22c55e', '#10b981', '#14b8a6', '#06b6d4',
        '#0ea5e9', '#3b82f6', '#6366f1', '#8b5cf6', '#a855f7', '#d946ef',
        '#ec4899', '#f43f5e', '#fb7185', '#fca5a5', '#fde68a', '#bbf7d0',
        '#99f6e4', '#bae6fd', '#c4b5fd', '#f0abfc'
    ]);

    function getSavedApiSettings() {
        try {
            const saved = JSON.parse(localStorage.getItem(API_SETTINGS_STORAGE_KEY) || '{}');
            return saved && typeof saved === 'object' ? saved : {};
        } catch (e) {
            console.warn("Saved API settings are invalid; using defaults.", e);
            return {};
        }
    }

    function saveApiSettings(updates = {}) {
        const settings = {...getSavedApiSettings(), ...updates};
        delete settings.memory_fast_model_name;
        localStorage.setItem(API_SETTINGS_STORAGE_KEY, JSON.stringify(settings));
        return settings;
    }

    function normalizeApiBaseUrlForChat(value) {
        const raw = String(value || '').trim();
        if (!raw) return '';
        try {
            const url = new URL(/^https?:\/\//i.test(raw) ? raw : `https://${raw}`);
            const host = url.hostname.toLowerCase();
            const path = url.pathname.toLowerCase();
            if (host === 'api.deepseek.com' && path.includes('/anthropic')) {
                url.pathname = '';
                url.search = '';
                url.hash = '';
                return url.toString().replace(/\/$/, '');
            }
            return raw;
        } catch (e) {
            return raw;
        }
    }

    function saveCurrentApiSettings() {
        const apiUrlInput = document.getElementById('api-url-input');
        const normalizedApiUrl = normalizeApiBaseUrlForChat(apiUrlInput?.value || '');
        if (apiUrlInput && normalizedApiUrl && normalizedApiUrl !== apiUrlInput.value.trim()) {
            apiUrlInput.value = normalizedApiUrl;
        }
        saveApiSettings({
            api_url: normalizedApiUrl,
            api_key: document.getElementById('api-key-input')?.value.trim() || '',
            api_model_name: document.getElementById('api-model-input')?.value.trim() || '',
            multimodal_model_name: document.getElementById('api-multimodal-model-input')?.value.trim() || '',
            strong_logic_model_name: document.getElementById('api-strong-logic-model-input')?.value.trim() || '',
            autonomous_memory_model_name: document.getElementById('api-autonomous-memory-model-input')?.value.trim() || '',
            memory_strong_model_name: document.getElementById('api-memory-strong-model-input')?.value.trim() || '',
            brave_search_api_key: document.getElementById('brave-search-key-input')?.value.trim() || ''
        });
    }

    function loadApiSettings() {
        const settings = getSavedApiSettings();

        const apiUrlInput = document.getElementById('api-url-input');
        const apiKeyInput = document.getElementById('api-key-input');
        const apiModelInput = document.getElementById('api-model-input');
        const multimodalModelInput = document.getElementById('api-multimodal-model-input');
        const strongLogicModelInput = document.getElementById('api-strong-logic-model-input');
        const autonomousMemoryModelInput = document.getElementById('api-autonomous-memory-model-input');
        const memoryStrongModelInput = document.getElementById('api-memory-strong-model-input');
        const braveSearchKeyInput = document.getElementById('brave-search-key-input');

        if (apiUrlInput && settings.api_url) {
            apiUrlInput.value = normalizeApiBaseUrlForChat(settings.api_url);
            if (apiUrlInput.value !== settings.api_url) {
                saveApiSettings({api_url: apiUrlInput.value});
            }
        }
        if (apiKeyInput && settings.api_key) apiKeyInput.value = settings.api_key;
        if (apiModelInput && settings.api_model_name) apiModelInput.value = settings.api_model_name;
        if (multimodalModelInput && settings.multimodal_model_name) multimodalModelInput.value = settings.multimodal_model_name;
        if (strongLogicModelInput && settings.strong_logic_model_name) strongLogicModelInput.value = settings.strong_logic_model_name;
        if (autonomousMemoryModelInput && settings.autonomous_memory_model_name) autonomousMemoryModelInput.value = settings.autonomous_memory_model_name;
        if (memoryStrongModelInput && settings.memory_strong_model_name) memoryStrongModelInput.value = settings.memory_strong_model_name;
        if (braveSearchKeyInput && settings.brave_search_api_key) braveSearchKeyInput.value = settings.brave_search_api_key;

        [apiUrlInput, apiKeyInput, apiModelInput, multimodalModelInput, strongLogicModelInput, autonomousMemoryModelInput, memoryStrongModelInput, braveSearchKeyInput]
            .filter(Boolean)
            .forEach(input => { input.oninput = saveCurrentApiSettings; });
    }

    function clearSavedApiSettings() {
        showConfirmDialog({
            title: 'Forget saved API settings?',
            message: 'All saved API URLs, model names and API keys will be removed from this browser.',
            impact: ['Chat stops using the saved API until configured again', 'Backend defaults apply afterwards'],
            confirmLabel: 'Forget',
            danger: true,
            onConfirm: close => {
                close();
                doClearSavedApiSettings();
            }
        });
    }

    function doClearSavedApiSettings() {
        localStorage.removeItem(API_SETTINGS_STORAGE_KEY);

        const apiUrlInput = document.getElementById('api-url-input');
        const apiKeyInput = document.getElementById('api-key-input');
        const apiModelInput = document.getElementById('api-model-input');
        const multimodalModelInput = document.getElementById('api-multimodal-model-input');
        const strongLogicModelInput = document.getElementById('api-strong-logic-model-input');
        const autonomousMemoryModelInput = document.getElementById('api-autonomous-memory-model-input');
        const memoryStrongModelInput = document.getElementById('api-memory-strong-model-input');
        if (apiUrlInput) apiUrlInput.value = 'https://newapi.xiaoqianran.xyz/v1';
        if (apiKeyInput) apiKeyInput.value = '';
        if (apiModelInput) apiModelInput.value = 'openai/gpt-oss-120b';
        if (multimodalModelInput) multimodalModelInput.value = '';
        if (strongLogicModelInput) strongLogicModelInput.value = '';
        if (autonomousMemoryModelInput) autonomousMemoryModelInput.value = '';
        if (memoryStrongModelInput) memoryStrongModelInput.value = '';
        const braveSearchKeyInput = document.getElementById('brave-search-key-input');
        if (braveSearchKeyInput) braveSearchKeyInput.value = '';
        localStorage.setItem(ASSISTANT_STRONG_LOGIC_STORAGE_KEY, 'false');
        syncAssistantStrongLogicToggle();
        showToast("Saved API information has been cleared.");
    }

    // --- Mode persistence (legacy request compatibility; no chat-bar selector) ---
    const RECALL_MODE_STORAGE_KEY = 'saya_recall_mode_v1';
    const SEARCH_MODE_STORAGE_KEY = 'saya_search_mode_v1';
    let _currentRecallMode = 'auto';
    let _currentSearchMode = 'off';
    let temporaryChatEnabled = false;
    let agentModeEnabled = false;

    function syncAgentPrivacyModeToggles() {
        document.querySelectorAll('[data-mode-toggle="temporary"]').forEach(button => {
            button.textContent = temporaryChatEnabled ? 'Temporary Chat · on' : 'Temporary Chat · off';
            button.classList.toggle('active', temporaryChatEnabled);
            button.setAttribute('aria-pressed', String(temporaryChatEnabled));
        });
        document.querySelectorAll('[data-mode-toggle="agent"]').forEach(button => {
            button.textContent = agentModeEnabled ? 'Agent Tools · on' : 'Agent Tools · off';
            button.classList.toggle('active', agentModeEnabled);
            button.setAttribute('aria-pressed', String(agentModeEnabled));
        });
    }

    function toggleTemporaryChat() {
        temporaryChatEnabled = !temporaryChatEnabled;
        if (temporaryChatEnabled && agentModeEnabled) {
            agentModeEnabled = false;
        }
        syncAgentPrivacyModeToggles();
        showToast(temporaryChatEnabled ? 'Temporary Chat enabled: personal memory is isolated.' : 'Temporary Chat disabled.');
    }

    function toggleAgentMode() {
        if (!agentModeEnabled && temporaryChatEnabled) {
            showToast('Agent Tools are unavailable during Temporary Chat.', { type: 'warning' });
            return;
        }
        agentModeEnabled = !agentModeEnabled;
        syncAgentPrivacyModeToggles();
        showToast(agentModeEnabled ? 'Agent tools enabled for this browser session.' : 'Agent tools disabled.');
    }

    function _modeDefaultFallback(part) {
        try {
            const ms = JSON.parse(localStorage.getItem(MODE_SETTING_STORAGE_KEY) || '{}');
            if (part === 'recall') return ms.default_recall_mode || 'auto';
            return ms.default_search_mode || 'off';
        } catch { return part === 'recall' ? 'auto' : 'off'; }
    }

    function getSavedRecallMode() {
        const v = (localStorage.getItem(RECALL_MODE_STORAGE_KEY) || '').trim().toLowerCase();
        if (v === 'off' || v === 'auto' || v === 'on') return v;
        return _modeDefaultFallback('recall');
    }

    function getSavedSearchMode() {
        const v = (localStorage.getItem(SEARCH_MODE_STORAGE_KEY) || '').trim().toLowerCase();
        if (v === 'force') return 'on';
        if (v === 'off' || v === 'auto' || v === 'on') return v;
        return _modeDefaultFallback('search');
    }

    function saveRecallMode(mode) { localStorage.setItem(RECALL_MODE_STORAGE_KEY, mode); }
    function saveSearchMode(mode) { localStorage.setItem(SEARCH_MODE_STORAGE_KEY, mode); }

    function _updateModeDropdownUI() {
        const trigger = document.getElementById('mode-dropdown-trigger');
        if (trigger) trigger.textContent = `${_currentRecallMode} · ${_currentSearchMode}`;
        // Highlight selected items.
        document.querySelectorAll('.mode-dropdown-item').forEach(el => {
            const part = el.dataset.part;
            const val = el.dataset.val;
            const active = (part === 'recall') ? (_currentRecallMode === val) : (_currentSearchMode === val);
            el.classList.toggle('selected', active);
        });
    }

    function loadModesIntoSelects() {
        _currentRecallMode = getSavedRecallMode();
        _currentSearchMode = getSavedSearchMode();
        _updateModeDropdownUI();
    }

    function initModeDropdown() {
        const trigger = document.getElementById('mode-dropdown-trigger');
        const panel = document.getElementById('mode-dropdown-panel');
        if (!trigger || !panel) return;
        trigger.addEventListener('click', (e) => {
            e.stopPropagation();
            panel.classList.toggle('open');
        });
        panel.querySelectorAll('.mode-dropdown-item').forEach(el => {
            el.addEventListener('click', () => {
                const part = el.dataset.part;
                const val = el.dataset.val;
                if (part === 'recall') { _currentRecallMode = val; saveRecallMode(val); }
                else { _currentSearchMode = val; saveSearchMode(val); }
                _updateModeDropdownUI();
                // Keep panel open so user can also pick the other section.
            });
        });
        // Close on outside click.
        document.addEventListener('click', () => panel.classList.remove('open'));
    }

    function parseCombinedMode() {
        return { recallMode: _currentRecallMode, searchMode: _currentSearchMode };
    }

    // --- Progress ring ---
    function updateModeProgressRing() {
        const ring = document.getElementById('mode-progress-circle');
        const pctEl = document.getElementById('mode-ring-pct');
        if (!ring || !pctEl) return;
        const maxConvs = getInputLimits().max_conversations;
        const turns = Math.floor((currentHistoryLength || 0) / 2);
        const pct = Math.min(100, Math.round((turns / maxConvs) * 100));
        const circumference = 56.55; // 2 * π * 9
        ring.style.strokeDashoffset = String(circumference * (1 - pct / 100));
        pctEl.textContent = pct + '%';
    }

    // Preset → (candidate search count, final cited sources) mapping.
    // Candidate pools are deliberately wider than the final evidence set so
    // the backend can prefer primary/official sources without bloating prompt context.
    const WEB_SEARCH_PRESETS = {
        quick:     { count: 6, sources: 2 },
        standard:  { count: 10, sources: 3 },
        deep:      { count: 16, sources: 5 },
    };

    // Recall depth presets: how many memories to retrieve.
    const RECALL_PRESETS = {
        quick:     3,
        standard:  8,
        deep:      15,
    };

    // --- Mode Setting storage (independent from LLM API settings) ---
    const MODE_SETTING_STORAGE_KEY = 'saya_mode_setting_v1';
    const MODE_SETTING_DEFAULTS = {
        web_search_preset: 'standard',
        web_source_strategy: 'official_first',
        recall_preset: 'standard',
        graph_recall_mode: 'auto',
        default_search_mode: 'auto',
        default_recall_mode: 'auto',
        safe_search: 'moderate',
        ai_memory_compression_enabled: false,
    };

    // --- Input Limits storage ---
    const INPUT_LIMITS_STORAGE_KEY = 'saya_input_limits_v1';
    const INPUT_LIMITS_DEFAULTS = {
        history_turns: 10,
        max_conversations: 50,
    };

    function getInputLimits() {
        try {
            const saved = JSON.parse(localStorage.getItem(INPUT_LIMITS_STORAGE_KEY) || '{}');
            return {...INPUT_LIMITS_DEFAULTS, ...saved};
        } catch { return {...INPUT_LIMITS_DEFAULTS}; }
    }

    function saveInputLimits(updates = {}) {
        const settings = {...getInputLimits(), ...updates};
        localStorage.setItem(INPUT_LIMITS_STORAGE_KEY, JSON.stringify(settings));
        updateModeProgressRing();
        return settings;
    }

    function getModeSettings() {
        try {
            const saved = JSON.parse(localStorage.getItem(MODE_SETTING_STORAGE_KEY) || '{}');
            return {...MODE_SETTING_DEFAULTS, ...saved};
        } catch { return {...MODE_SETTING_DEFAULTS}; }
    }

    function saveModeSettings(updates = {}) {
        const settings = {...getModeSettings(), ...updates};
        localStorage.setItem(MODE_SETTING_STORAGE_KEY, JSON.stringify(settings));
        return settings;
    }

    function getWebSearchPresetConfig() {
        return WEB_SEARCH_PRESETS[getModeSettings().web_search_preset] || WEB_SEARCH_PRESETS.standard;
    }

    function getRecallPresetConfig() {
        return RECALL_PRESETS[getModeSettings().recall_preset] || RECALL_PRESETS.standard;
    }


    function organizeModeSettingSections() {
        // Keep the mode workspace deterministic for keyboard users and for
        // compact layouts: WEB SEARCH always precedes MEMORY RECALL.
        const searchIds = ['default-search-mode-select', 'web-search-preset-select', 'web-source-strategy-select', 'safe-search-select'];
        const recallIds = ['default-recall-mode-select', 'recall-preset-select', 'graph-recall-mode-select'];
        const pane = document.querySelector('.settings-pane[data-pane="mode"]');
        if (!pane) return;
        const sections = Array.from(pane.querySelectorAll(':scope > .form-section'));
        const searchSection = sections.find(section => searchIds.some(id => section.querySelector(`#${id}`)));
        const recallSection = sections.find(section => recallIds.some(id => section.querySelector(`#${id}`)));
        if (searchSection && recallSection && searchSection !== recallSection) {
            pane.insertBefore(searchSection, recallSection);
        }
    }

    function openModeSettingModal() {
        organizeModeSettingSections();
        syncAgentPrivacyModeToggles();
        const s = getModeSettings();
        const setVal = (id, val) => { const el = document.getElementById(id); if (el) el.value = val; };
        setVal('web-search-preset-select', s.web_search_preset);
        setVal('web-source-strategy-select', s.web_source_strategy);
        setVal('recall-preset-select', s.recall_preset);
        setVal('graph-recall-mode-select', s.graph_recall_mode);
        setVal('default-search-mode-select', s.default_search_mode);
        setVal('default-recall-mode-select', s.default_recall_mode);
        setVal('safe-search-select', s.safe_search);
        const aiCompressionToggle = document.getElementById('ai-memory-compression-toggle');
        if (aiCompressionToggle) aiCompressionToggle.checked = s.ai_memory_compression_enabled === true;
        openSettingsWorkspace('mode');
    }

    function closeModeSettingModal() {
        closeSettingsWorkspace();
    }

    async function saveModeSettingsFromModal() {
        const nextSettings = {
            web_search_preset: document.getElementById('web-search-preset-select')?.value || 'standard',
            web_source_strategy: document.getElementById('web-source-strategy-select')?.value || 'official_first',
            recall_preset: document.getElementById('recall-preset-select')?.value || 'standard',
            graph_recall_mode: document.getElementById('graph-recall-mode-select')?.value || 'auto',
            default_search_mode: document.getElementById('default-search-mode-select')?.value || 'auto',
            default_recall_mode: document.getElementById('default-recall-mode-select')?.value || 'auto',
            safe_search: document.getElementById('safe-search-select')?.value || 'moderate',
            ai_memory_compression_enabled: document.getElementById('ai-memory-compression-toggle')?.checked === true,
        };
        try {
            const response = await fetch('/api/kb/settings', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    memory_ai_compression_enabled: nextSettings.ai_memory_compression_enabled
                })
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || 'Unable to save AI compression mode');
            saveModeSettings(nextSettings);
            surfaceMemoryMaintenanceResult(data);
            closeModeSettingModal();
        } catch (error) {
            showToast(`Mode Setting could not be saved: ${error.message || error}`, { type: 'error', duration: 4200 });
        }
    }

    async function resetModeSettings() {
        localStorage.removeItem(MODE_SETTING_STORAGE_KEY);
        try {
            await fetch('/api/kb/settings', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({memory_ai_compression_enabled: false})
            });
        } catch (error) {
            console.warn('Unable to reset AI compression backend setting', error);
        }
        openModeSettingModal(); // reload defaults into UI
    }

    function getSelectedBackupScopes() {
        return Array.from(document.querySelectorAll('#backup-scope-grid input[type="checkbox"]:checked'))
            .map(input => input.value);
    }

    function setBackupStatus(message, isError = false) {
        const status = document.getElementById('backup-restore-status');
        if (!status) return;
        status.textContent = message || '';
        status.classList.toggle('error', isError);
    }

    function formatBackupSize(bytes) {
        const value = Number(bytes || 0);
        if (value < 1024) return `${value} B`;
        if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
        return `${(value / (1024 * 1024)).toFixed(1)} MB`;
    }

    async function openBackupRestoreModal() {
        openSettingsWorkspace('backup');
        document.getElementById('backup-preview')?.setAttribute('hidden', '');
        await loadSayaBackups();
    }

    function closeBackupRestoreModal() {
        closeSettingsWorkspace();
    }

    async function loadSayaBackups() {
        const list = document.getElementById('backup-list');
        if (!list) return;
        list.innerHTML = `<div class="st-skeleton st-skeleton-row"><span class="st-skeleton st-skeleton-avatar"></span><span class="st-skeleton st-skeleton-body"><span class="st-skeleton st-skeleton-text" style="width:60%"></span></span></div>`;
        setBackupStatus('');
        try {
            const response = await fetch('/api/backups');
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || 'Unable to load backups');
            list.innerHTML = '';
            const backups = Array.isArray(data.backups) ? data.backups : [];
            if (!backups.length) {
                list.innerHTML = `<div class="st-empty">${ic('shield')}<span>No backups yet.</span><button type="button" class="workspace-secondary-action" onclick="createSayaBackup()">${ic('plus', 'icn-sm')}Create the first backup</button></div>`;
                return;
            }
            const table = document.createElement('div');
            table.className = 'limits-table backup-table';
            table.innerHTML = '<div class="limits-table-head"><span>Date</span><span>Scopes</span><span class="backup-col-num">Files</span><span class="backup-col-num">Size</span><span>Status</span><span></span></div>';
            backups.forEach(backup => {
                const row = document.createElement('div');
                row.className = 'limits-table-row backup-table-row';
                const valid = backup.valid !== false;
                const scopesText = (backup.scopes || []).join(', ') || '—';
                row.innerHTML = `
                    <span class="backup-col-date" title="${escapeHtml(backup.backup_id || '')}">${escapeHtml(backup.created_at || backup.backup_id || 'Backup')}</span>
                    <span class="backup-col-scopes">${escapeHtml(scopesText)}</span>
                    <span class="backup-col-num">${valid ? Number(backup.file_count || 0).toLocaleString() : '—'}</span>
                    <span class="backup-col-num">${valid ? formatBackupSize(backup.archive_size) : '—'}</span>
                    <span>${valid ? '<span class="st-badge success">Valid</span>' : `<span class="st-badge danger" title="${escapeHtml(backup.error || '')}">Invalid</span>`}</span>
                    <span class="backup-col-actions">${valid ? `<button type="button" class="workspace-secondary-action" onclick="previewSayaRestore('${escapeJsString(backup.backup_id)}')">Preview restore</button>` : ''}</span>`;
                table.appendChild(row);
            });
            list.appendChild(table);
        } catch (error) {
            list.innerHTML = `
                <div class="st-page-error">
                    ${ic('alert')}
                    <strong>Unable to read the backup list.</strong>
                    <span>Your data and existing backups are safe. Check that the backend is running, then try again.</span>
                    <button type="button" class="workspace-secondary-action" onclick="loadSayaBackups()">${ic('refresh', 'icn-sm')}Retry</button>
                </div>`;
            console.warn('Backup list failed:', error);
        }
    }

    async function createSayaBackup() {
        const scopes = getSelectedBackupScopes();
        if (!scopes.length) {
            setBackupStatus('Select at least one category.', true);
            return;
        }
        setBackupStatus('Creating backup…');
        try {
            const response = await fetch('/api/backups', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({scopes})
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || 'Backup failed');
            await loadSayaBackups();
            setBackupStatus(`Backup created: ${data.backup_id}`);
        } catch (error) {
            setBackupStatus(error.message || String(error), true);
        }
    }

    async function previewSayaRestore(backupId) {
        const scopes = getSelectedBackupScopes();
        if (!scopes.length) {
            setBackupStatus('Select at least one category to restore.', true);
            return;
        }
        setBackupStatus('Preparing restore preview…');
        try {
            const response = await fetch(`/api/backups/${encodeURIComponent(backupId)}/preview`, {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({scopes})
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || 'Preview failed');
            const panel = document.getElementById('backup-preview');
            panel.hidden = false;
            panel.innerHTML = '';
            const heading = document.createElement('strong');
            heading.textContent = `Restore preview · ${data.summary?.replace || 0} replace · ${data.summary?.add || 0} add · ${data.summary?.unchanged || 0} unchanged`;
            const note = document.createElement('p');
            note.textContent = 'A safety backup of the current data will be created automatically before restore. Extra current files are not deleted.';
            const changes = document.createElement('div');
            changes.className = 'backup-change-list';
            (data.changes || []).filter(item => item.action !== 'unchanged').slice(0, 30).forEach(item => {
                const line = document.createElement('div');
                line.textContent = `${item.action.toUpperCase()} · ${item.path}`;
                changes.appendChild(line);
            });
            const restoreButton = document.createElement('button');
            restoreButton.type = 'button';
            restoreButton.className = 'workspace-primary-action danger';
            restoreButton.textContent = 'Restore selected data';
            restoreButton.addEventListener('click', () => restoreSayaBackup(
                backupId, data.selected_scopes || scopes, data.confirmation_token
            ));
            panel.appendChild(heading);
            panel.appendChild(note);
            panel.appendChild(changes);
            panel.appendChild(restoreButton);
            setBackupStatus('Review the changes before restoring.');
        } catch (error) {
            setBackupStatus(error.message || String(error), true);
        }
    }

    async function restoreSayaBackup(backupId, scopes, confirmationToken) {
        showConfirmDialog({
            title: 'Restore this backup?',
            message: 'The selected data will be overwritten with the backup contents. A safety backup of the current state is created first.',
            impact: ['Selected scopes are replaced by the backup version', 'Anything added after the backup is kept only in the safety backup'],
            confirmLabel: 'Restore',
            danger: true,
            onConfirm: close => {
                close();
                doRestoreSayaBackup(backupId, scopes, confirmationToken);
            }
        });
    }

    async function doRestoreSayaBackup(backupId, scopes, confirmationToken) {
        setBackupStatus('Restoring data…');
        try {
            const response = await fetch(`/api/backups/${encodeURIComponent(backupId)}/restore`, {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({scopes, confirmation_token: confirmationToken})
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || 'Restore failed');
            await loadSayaBackups();
            const reloadWarning = data.runtime_reload_warning ? ` Runtime refresh warning: ${data.runtime_reload_warning}.` : '';
            setBackupStatus(`Restore complete. Safety backup: ${data.safety_backup_id}.${reloadWarning} Restart Saya before continuing.`);
        } catch (error) {
            setBackupStatus(error.message || String(error), true);
        }
    }

    // --- Input Limits modal ---
    function openInputLimitsModal() { openTokenLimitsModal(); }
    function closeInputLimitsModal() { closeTokenLimitsModal(); }
    function saveInputLimitsFromModal() { saveTokenLimitsFromModal(); }
    function resetInputLimits() { resetTokenLimits(); }

    const OTHER_API_SETTINGS_STORAGE_KEY = 'saya_other_api_settings_v1';
    const DEFAULT_OTHER_API_SETTINGS = Object.freeze({
        translation_secret_id: '',
        translation_secret_key: '',
        translation_region: 'ap-guangzhou',
        translation_endpoint: 'tmt.tencentcloudapi.com',
        tts_api_url: 'https://api.elevenlabs.io/v1',
        tts_api_key: '',
        tts_voice_id: '',
        tts_model_id: 'eleven_multilingual_v2'
    });

    function getSavedOtherApiSettings() {
        try {
            const saved = JSON.parse(localStorage.getItem(OTHER_API_SETTINGS_STORAGE_KEY) || '{}');
            return {...DEFAULT_OTHER_API_SETTINGS, ...(saved && typeof saved === 'object' ? saved : {})};
        } catch (e) {
            console.warn('Saved Other API settings are invalid; using defaults.', e);
            return {...DEFAULT_OTHER_API_SETTINGS};
        }
    }

    function collectOtherApiSettings() {
        return {
            translation_secret_id: document.getElementById('translation-secret-id-input')?.value.trim() || '',
            translation_secret_key: document.getElementById('translation-secret-key-input')?.value.trim() || '',
            translation_region: document.getElementById('translation-region-input')?.value.trim() || DEFAULT_OTHER_API_SETTINGS.translation_region,
            translation_endpoint: document.getElementById('translation-endpoint-input')?.value.trim() || DEFAULT_OTHER_API_SETTINGS.translation_endpoint,
            tts_api_url: document.getElementById('tts-api-url-input')?.value.trim() || DEFAULT_OTHER_API_SETTINGS.tts_api_url,
            tts_api_key: document.getElementById('tts-api-key-input')?.value.trim() || '',
            tts_voice_id: document.getElementById('tts-voice-id-input')?.value.trim() || '',
            tts_model_id: document.getElementById('tts-model-id-input')?.value.trim() || DEFAULT_OTHER_API_SETTINGS.tts_model_id
        };
    }

    function saveOtherApiSettings(settings = collectOtherApiSettings()) {
        const clean = {...DEFAULT_OTHER_API_SETTINGS, ...(settings || {})};
        localStorage.setItem(OTHER_API_SETTINGS_STORAGE_KEY, JSON.stringify(clean));
        return clean;
    }

    function applyOtherApiSettings(settings) {
        const fieldMap = {
            'translation-secret-id-input': 'translation_secret_id',
            'translation-secret-key-input': 'translation_secret_key',
            'translation-region-input': 'translation_region',
            'translation-endpoint-input': 'translation_endpoint',
            'tts-api-url-input': 'tts_api_url',
            'tts-api-key-input': 'tts_api_key',
            'tts-voice-id-input': 'tts_voice_id',
            'tts-model-id-input': 'tts_model_id'
        };
        Object.entries(fieldMap).forEach(([id, key]) => {
            const input = document.getElementById(id);
            if (!input) return;
            input.value = settings[key] || '';
            input.oninput = () => saveOtherApiSettings(collectOtherApiSettings());
        });
    }

    function openOtherApiSettingsModal() {
        const menu = document.getElementById('settings-menu');
        if (menu && menu.style.display === 'flex') menu.style.display = 'none';
        applyOtherApiSettings(getSavedOtherApiSettings());
        openSettingsWorkspace('other');
        refreshProviderStatusBadges();
    }

    function closeOtherApiSettingsModal() {
        closeSettingsWorkspace();
    }

    function saveOtherApiSettingsFromModal() {
        saveOtherApiSettings();
        showToast('Other API settings saved.');
        closeOtherApiSettingsModal();
    }

    function clearOtherApiSettings() {
        showConfirmDialog({
            title: 'Reset Translation and TTS settings?',
            message: 'Saved credentials for both providers will be cleared from this browser.',
            impact: ['Falls back to backend environment variables', 'Fields return to their default values'],
            confirmLabel: 'Reset',
            danger: true,
            onConfirm: close => {
                close();
                localStorage.removeItem(OTHER_API_SETTINGS_STORAGE_KEY);
                applyOtherApiSettings({...DEFAULT_OTHER_API_SETTINGS});
                refreshProviderStatusBadges();
                showToast('Translation and TTS settings were reset.');
            }
        });
    }

    const OUTPUT_LIMIT_STORAGE_KEY = 'saya_output_limits_v1';
    const DEFAULT_OUTPUT_LIMITS = Object.freeze({
        api: 8192,
        qwen3_30b_q5: 4096,
        mistral_22b_q6: 4096,
        mistral_22b_q5: 4096,
        saya_r1_q5: 4096
    });

    function normalizeOutputLimit(value, model = 'api') {
        const parsed = Number.parseInt(value, 10);
        const fallback = DEFAULT_OUTPUT_LIMITS[model] || DEFAULT_OUTPUT_LIMITS.api;
        if (!Number.isFinite(parsed)) return fallback;
        const maximum = model === 'api' ? 65536 : 8192;
        return Math.min(maximum, Math.max(256, parsed));
    }

    function getSavedOutputLimits() {
        let saved = {};
        try {
            saved = JSON.parse(localStorage.getItem(OUTPUT_LIMIT_STORAGE_KEY) || '{}');
        } catch (e) {
            console.warn('Saved output limits are invalid; using defaults.', e);
        }
        return Object.fromEntries(Object.keys(DEFAULT_OUTPUT_LIMITS).map(model => [
            model,
            normalizeOutputLimit(saved?.[model], model)
        ]));
    }

    function loadOutputLimits() {
        const limits = getSavedOutputLimits();
        Object.entries(limits).forEach(([model, value]) => {
            const input = document.getElementById(`output-limit-${model}`);
            if (input) input.value = String(value);
        });
        updateOutputCostPreview();
    }

    function updateOutputCostPreview() {
        const apiLimit = normalizeOutputLimit(document.getElementById('output-limit-api')?.value, 'api');
        const tier = document.getElementById('output-cost-tier');
        const guidance = document.getElementById('output-cost-guidance');
        let label = 'Balanced';
        let message = 'Enough room for detailed answers while keeping routine API use predictable.';
        let level = 'balanced';
        if (apiLimit <= 4096) {
            label = 'Efficient';
            message = 'Best for concise daily conversations and lower completion cost.';
            level = 'efficient';
        } else if (apiLimit > 16384) {
            label = 'High cost';
            message = 'Long completions can become noticeably slower and more expensive; use this only when the extra length is useful.';
            level = 'high';
        } else if (apiLimit > 8192) {
            label = 'Extended';
            message = 'Useful for large analyses, with higher latency and completion-token cost.';
            level = 'extended';
        }
        if (tier) {
            tier.textContent = label;
            tier.dataset.level = level;
        }
        if (guidance) guidance.textContent = message;
    }

    function saveOutputLimitsFromModal() {
        const limits = {};
        Object.keys(DEFAULT_OUTPUT_LIMITS).forEach(model => {
            limits[model] = normalizeOutputLimit(document.getElementById(`output-limit-${model}`)?.value, model);
        });
        localStorage.setItem(OUTPUT_LIMIT_STORAGE_KEY, JSON.stringify(limits));
        loadOutputLimits();
    }

    function resetOutputLimits() {
        localStorage.setItem(OUTPUT_LIMIT_STORAGE_KEY, JSON.stringify(DEFAULT_OUTPUT_LIMITS));
        loadOutputLimits();
    }

    function openOutputLimitsModal() {
        // Legacy redirect — now opens the merged Token Limits modal.
        openTokenLimitsModal();
    }

    function closeOutputLimitsModal() {
        // Legacy redirect.
        closeTokenLimitsModal();
    }

    // --- Token Limits modal (merged Output + Input) ---
    function openTokenLimitsModal() {
        const menu = document.getElementById('settings-menu');
        if (menu) menu.style.display = 'none';
        loadOutputLimits();
        const s = getInputLimits();
        const hs = document.getElementById('input-history-turns-select');
        const mc = document.getElementById('input-max-conversations-select');
        if (hs) hs.value = String(s.history_turns);
        if (mc) mc.value = String(s.max_conversations);
        openSettingsWorkspace('limits');
    }

    function closeTokenLimitsModal() {
        closeSettingsWorkspace();
    }

    function saveTokenLimitsFromModal() {
        // Save output limits.
        saveOutputLimitsFromModal();
        // Save input limits.
        saveInputLimits({
            history_turns: parseInt(document.getElementById('input-history-turns-select')?.value || '10', 10),
            max_conversations: parseInt(document.getElementById('input-max-conversations-select')?.value || '50', 10),
        });
        closeTokenLimitsModal();
    }

    function resetTokenLimits() {
        resetOutputLimits();
        localStorage.removeItem(INPUT_LIMITS_STORAGE_KEY);
        updateModeProgressRing();
        // Reload input UI values.
        const s = getInputLimits();
        const hs = document.getElementById('input-history-turns-select');
        const mc = document.getElementById('input-max-conversations-select');
        if (hs) hs.value = String(s.history_turns);
        if (mc) mc.value = String(s.max_conversations);
    }

    function getOutputLimitForModel(model) {
        const key = Object.prototype.hasOwnProperty.call(DEFAULT_OUTPUT_LIMITS, model) ? model : 'api';
        return getSavedOutputLimits()[key];
    }

    const ASSISTANT_STRONG_LOGIC_STORAGE_KEY = 'saya_assistant_strong_logic_enabled_v3';

    function isAssistantStrongLogicEnabled() {
        return localStorage.getItem(ASSISTANT_STRONG_LOGIC_STORAGE_KEY) === 'true';
    }

    function syncAssistantStrongLogicToggle() {
        const toggle = document.getElementById('assistant-strong-logic-toggle');
        const enabled = isAssistantStrongLogicEnabled();
        if (toggle) {
            toggle.classList.toggle('on', enabled);
            toggle.setAttribute('aria-checked', enabled ? 'true' : 'false');
            toggle.setAttribute('aria-label', enabled ? 'Strong Logic on' : 'Strong Logic off');
            toggle.title = enabled ? 'Strong Logic: On' : 'Strong Logic: Off';
        }
        const stateLabel = document.getElementById('assistant-strong-logic-state');
        if (stateLabel) stateLabel.textContent = enabled ? 'ON' : 'OFF';
    }

    function toggleAssistantStrongLogic() {
        setAssistantStrongLogicEnabled(!isAssistantStrongLogicEnabled());
    }

    function setAssistantStrongLogicEnabled(enabled) {
        if (enabled) {
            const settings = getSavedApiSettings();
            const modelName = document.getElementById('api-strong-logic-model-input')?.value.trim() || settings.strong_logic_model_name || '';
            const apiKey = document.getElementById('api-key-input')?.value.trim() || settings.api_key || '';
            if (!modelName || !apiKey) {
                localStorage.setItem(ASSISTANT_STRONG_LOGIC_STORAGE_KEY, 'false');
                syncAssistantStrongLogicToggle();
                showToast('Configure both API Key and Strong Logic Model in LLM API first.', { type: 'error', duration: 3600 });
                closeAssistantRoleModal();
                openApiSettingsModal();
                return;
            }
        }
        localStorage.setItem(ASSISTANT_STRONG_LOGIC_STORAGE_KEY, enabled ? 'true' : 'false');
        syncAssistantStrongLogicToggle();
    }

    // --- USER PROFILE CARD ---
    const USER_PROFILE_STORAGE_KEY = 'saya_user_profile_v1';
    const USER_PROFILE_MIGRATION_KEY = 'saya_user_profile_server_migrated_v1';
    const USER_PROFILE_API_URL = '/api/user-profile';
    const DEFAULT_USER_PROFILE = {
        name: 'Doctor',
        avatar: '',
        avatars: []
    };
    let currentUserProfile = null;

    function normalizeUserProfile(profile) {
        return {
            name: String(profile?.name || DEFAULT_USER_PROFILE.name).trim().slice(0, 40) || DEFAULT_USER_PROFILE.name,
            avatar: String(profile?.avatar || '').trim(),
            avatars: Array.isArray(profile?.avatars)
                ? profile.avatars.filter(item => item && item.url).slice(0, 3)
                : []
        };
    }

    function getSavedUserProfile() {
        if (currentUserProfile) return normalizeUserProfile(currentUserProfile);
        try {
            const saved = JSON.parse(localStorage.getItem(USER_PROFILE_STORAGE_KEY) || '{}');
            return normalizeUserProfile({
                ...DEFAULT_USER_PROFILE,
                ...(saved && typeof saved === 'object' ? saved : {})
            });
        } catch (e) {
            console.warn("Saved user profile is invalid; using defaults.", e);
            return {...DEFAULT_USER_PROFILE};
        }
    }

    function saveUserProfile(profile) {
        const clean = normalizeUserProfile(profile);
        currentUserProfile = clean;
        localStorage.setItem(USER_PROFILE_STORAGE_KEY, JSON.stringify(clean));
        return clean;
    }

    async function requestUserProfileUpdate(profile) {
        const response = await fetch(USER_PROFILE_API_URL, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                name: profile?.name,
                avatar: profile?.avatar
            })
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || 'Unable to save profile');
        return saveUserProfile(data.profile || profile);
    }

    async function requestUserAvatarUpload(file) {
        const formData = new FormData();
        formData.append('file', file);
        const response = await fetch(`${USER_PROFILE_API_URL}/avatar`, {
            method: 'POST',
            body: formData
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || 'Avatar upload failed');
        return saveUserProfile(data.profile || {});
    }

    async function initializeUserProfile() {
        const localProfile = getSavedUserProfile();
        renderUserProfile();
        try {
            const response = await fetch(USER_PROFILE_API_URL, {cache: 'no-store'});
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || 'Unable to load profile');
            let serverProfile = normalizeUserProfile(data.profile || {});

            const shouldMigrateLocalProfile = (
                localStorage.getItem(USER_PROFILE_MIGRATION_KEY) !== 'true' &&
                serverProfile.name === DEFAULT_USER_PROFILE.name &&
                !serverProfile.avatar &&
                serverProfile.avatars.length === 0 &&
                (
                    localProfile.name !== DEFAULT_USER_PROFILE.name ||
                    Boolean(localProfile.avatar)
                )
            );
            if (shouldMigrateLocalProfile) {
                let migratedAvatar = '';
                if (localProfile.avatar) {
                    try {
                        const avatarResponse = await fetch(localProfile.avatar);
                        const avatarBlob = await avatarResponse.blob();
                        if (avatarBlob.type.startsWith('image/')) {
                            const extension = avatarBlob.type.split('/')[1]?.replace('jpeg', 'jpg') || 'png';
                            const migrated = await requestUserAvatarUpload(
                                new File([avatarBlob], `legacy-avatar.${extension}`, {type: avatarBlob.type})
                            );
                            migratedAvatar = migrated.avatar;
                        }
                    } catch (migrationError) {
                        console.warn("Legacy avatar could not be migrated.", migrationError);
                    }
                }
                serverProfile = await requestUserProfileUpdate({
                    name: localProfile.name,
                    avatar: migratedAvatar
                });
            }
            localStorage.setItem(USER_PROFILE_MIGRATION_KEY, 'true');
            saveUserProfile(serverProfile);
            renderUserProfile();
        } catch (e) {
            console.warn("Unable to sync user profile; using the local cache.", e);
        }
    }

    function profileInitials(name) {
        const clean = String(name || DEFAULT_USER_PROFILE.name).trim();
        const parts = clean.split(/\s+/).filter(Boolean);
        if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
        return clean.slice(0, 2).toUpperCase() || 'D';
    }

    function setProfileAvatar(imgEl, fallbackEl, avatar, name) {
        if (!imgEl || !fallbackEl) return;
        fallbackEl.textContent = profileInitials(name);
        imgEl.onload = () => {
            imgEl.style.display = 'block';
            fallbackEl.style.display = 'none';
        };
        imgEl.onerror = () => {
            imgEl.style.display = 'none';
            fallbackEl.style.display = 'inline';
            imgEl.removeAttribute('src');
        };
        if (avatar) {
            imgEl.src = avatar;
        } else {
            imgEl.style.display = 'none';
            fallbackEl.style.display = 'inline';
            imgEl.removeAttribute('src');
        }
    }

    function renderUserProfile() {
        const profile = getSavedUserProfile();
        const nameEl = document.getElementById('user-profile-name');
        if (nameEl) nameEl.textContent = profile.name;
        setProfileAvatar(
            document.getElementById('user-profile-avatar-img'),
            document.getElementById('user-profile-avatar-fallback'),
            profile.avatar,
            profile.name
        );
    }

    function setUserProfileStatus(message, isError = false) {
        const status = document.getElementById('user-profile-status');
        if (!status) return;
        status.textContent = message || '';
        status.classList.toggle('error', Boolean(isError));
    }

    function selectUserProfileAvatar(avatar) {
        const avatarInput = document.getElementById('user-profile-avatar-input');
        if (avatarInput) avatarInput.value = String(avatar || '');
        previewUserAvatarUrl(avatar);
        renderUserAvatarHistory({
            ...getSavedUserProfile(),
            avatar: String(avatar || '')
        });
    }

    function renderUserAvatarHistory(profile = getSavedUserProfile()) {
        const container = document.getElementById('user-profile-avatar-history');
        const count = document.getElementById('user-profile-avatar-history-count');
        if (!container) return;
        container.innerHTML = '';
        const avatars = Array.isArray(profile.avatars) ? profile.avatars.slice(0, 3) : [];
        if (count) count.textContent = `${avatars.length} / 3`;
        if (!avatars.length) {
            const empty = document.createElement('div');
            empty.className = 'user-profile-avatar-history-empty';
            empty.innerHTML = `
                <span class="user-profile-avatar-history-empty-mark">+</span>
                <span>Choose an image to create your synced avatar archive.</span>
            `;
            container.appendChild(empty);
            return;
        }
        avatars.forEach(item => {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = `user-profile-avatar-history-item${item.url === profile.avatar ? ' active' : ''}`;
            button.title = item.uploaded_at ? `Uploaded ${item.uploaded_at}` : 'Use this avatar';
            button.onclick = () => selectUserProfileAvatar(item.url);
            button.setAttribute('aria-pressed', item.url === profile.avatar ? 'true' : 'false');
            const frame = document.createElement('span');
            frame.className = 'user-profile-avatar-history-frame';
            const image = document.createElement('img');
            image.src = item.url;
            image.alt = 'Previous avatar';
            frame.appendChild(image);
            const caption = document.createElement('span');
            caption.className = 'user-profile-avatar-history-caption';
            const uploadDate = String(item.uploaded_at || '').split(' ')[0];
            caption.textContent = item.url === profile.avatar
                ? 'Current'
                : (uploadDate ? uploadDate.slice(5).replace('-', '.') : 'Saved');
            button.appendChild(frame);
            button.appendChild(caption);
            container.appendChild(button);
        });
    }

    function loadUserProfileIntoModal() {
        const profile = getSavedUserProfile();
        const nameInput = document.getElementById('user-profile-name-input');
        const avatarInput = document.getElementById('user-profile-avatar-input');
        if (nameInput) nameInput.value = profile.name;
        if (avatarInput) avatarInput.value = profile.avatar;
        setProfileAvatar(
            document.getElementById('user-profile-preview-img'),
            document.getElementById('user-profile-preview-fallback'),
            profile.avatar,
            profile.name
        );
        renderUserAvatarHistory(profile);
        setUserProfileStatus('');
    }

    function openUserProfileModal(event) {
        if (event?.target?.closest('.user-settings-button')) return;
        const menu = document.getElementById('settings-menu');
        if (menu) menu.style.display = 'none';
        loadUserProfileIntoModal();
        document.getElementById('user-profile-modal').style.display = 'flex';
        fetch(USER_PROFILE_API_URL, {cache: 'no-store'})
            .then(async response => {
                const data = await response.json();
                if (!response.ok) throw new Error(data.detail || 'Unable to refresh profile');
                saveUserProfile(data.profile || {});
                loadUserProfileIntoModal();
                renderUserProfile();
            })
            .catch(error => setUserProfileStatus(error.message || String(error), true));
    }

    function closeUserProfileModal() {
        document.getElementById('user-profile-modal').style.display = 'none';
    }

    function previewUserAvatarUrl(value) {
        const name = document.getElementById('user-profile-name-input')?.value || DEFAULT_USER_PROFILE.name;
        setProfileAvatar(
            document.getElementById('user-profile-preview-img'),
            document.getElementById('user-profile-preview-fallback'),
            String(value || '').trim(),
            name
        );
    }

    function clearUserAvatarInput() {
        const input = document.getElementById('user-profile-avatar-input');
        const fileInput = document.getElementById('user-profile-avatar-file');
        if (input) input.value = '';
        if (fileInput) fileInput.value = '';
        previewUserAvatarUrl('');
        renderUserAvatarHistory({
            ...getSavedUserProfile(),
            avatar: ''
        });
    }

    async function handleUserAvatarFile(input) {
        const file = input?.files?.[0];
        if (!file) return;
        setUserProfileStatus('Uploading avatar...');
        try {
            const profile = await requestUserAvatarUpload(file);
            const avatarInput = document.getElementById('user-profile-avatar-input');
            if (avatarInput) avatarInput.value = profile.avatar;
            previewUserAvatarUrl(profile.avatar);
            renderUserAvatarHistory(profile);
            renderUserProfile();
            setUserProfileStatus('Avatar uploaded and synced.');
        } catch (e) {
            setUserProfileStatus(e.message || String(e), true);
        } finally {
            input.value = '';
        }
    }

    async function saveUserProfileFromModal() {
        setUserProfileStatus('Saving profile...');
        try {
            const profile = await requestUserProfileUpdate({
                name: document.getElementById('user-profile-name-input')?.value,
                avatar: document.getElementById('user-profile-avatar-input')?.value
            });
            renderUserProfile();
            closeUserProfileModal();
            return profile;
        } catch (e) {
            setUserProfileStatus(e.message || String(e), true);
            return null;
        }
    }

    // --- SAYA STYLE LOGIC ---
    const SAYA_STYLE_STORAGE_KEY = 'saya_style_settings_v1';
    const SAYA_STYLE_API_URL = '/api/saya-style/settings';
    const DEFAULT_SAYA_STYLE = {
        base_style: 'balanced',
        response_language: 'auto',
        attachment_level: 'medium',
        sociality: 'normal',
        memory_imagination: 'normal',
        tag_mode: 'normal',
        allow_emoji: false,
        readable: false
    };

    function getSayaStyleSettings() {
        try {
            const saved = JSON.parse(localStorage.getItem(SAYA_STYLE_STORAGE_KEY) || '{}');
            return {...DEFAULT_SAYA_STYLE, ...(saved && typeof saved === 'object' ? saved : {})};
        } catch (e) {
            console.warn("Saya style settings are invalid; using defaults.", e);
            return {...DEFAULT_SAYA_STYLE};
        }
    }

    function saveSayaStyleSettings(settings) {
        const clean = {
            base_style: String(settings?.base_style || DEFAULT_SAYA_STYLE.base_style),
            response_language: ['auto', 'zh-cn', 'zh-tw', 'en', 'ja', 'ko', 'es', 'fr', 'de', 'ru', 'pt', 'ar', 'hi', 'it'].includes(String(settings?.response_language || '').toLowerCase())
                ? String(settings.response_language).toLowerCase()
                : DEFAULT_SAYA_STYLE.response_language,
            attachment_level: ['low', 'medium', 'high', 'extreme'].includes(String(settings?.attachment_level || '').toLowerCase())
                ? String(settings.attachment_level).toLowerCase()
                : DEFAULT_SAYA_STYLE.attachment_level,
            sociality: ['expectant', 'normal', 'aloof', 'averse'].includes(String(settings?.sociality || '').toLowerCase())
                ? String(settings.sociality).toLowerCase()
                : DEFAULT_SAYA_STYLE.sociality,
            memory_imagination: ['off', 'normal', 'on'].includes(String(settings?.memory_imagination || '').toLowerCase())
                ? String(settings.memory_imagination).toLowerCase()
                : DEFAULT_SAYA_STYLE.memory_imagination,
            tag_mode: String(settings?.tag_mode || DEFAULT_SAYA_STYLE.tag_mode),
            allow_emoji: Boolean(settings?.allow_emoji),
            readable: Boolean(settings?.readable)
        };
        localStorage.setItem(SAYA_STYLE_STORAGE_KEY, JSON.stringify(clean));
        return clean;
    }

    function applySayaStyleToModal(settings) {
        const base = document.getElementById('saya-style-base');
        const language = document.getElementById('saya-style-language');
        const attachment = document.getElementById('saya-style-attachment');
        const sociality = document.getElementById('saya-style-sociality');
        const memoryImagination = document.getElementById('saya-style-memory-imagination');
        const tags = document.getElementById('saya-style-tags');
        const emoji = document.getElementById('saya-style-emoji');
        const readable = document.getElementById('saya-style-readable');
        if (base) base.value = settings.base_style;
        if (language) language.value = settings.response_language;
        if (attachment) attachment.value = settings.attachment_level;
        if (sociality) sociality.value = settings.sociality;
        if (memoryImagination) memoryImagination.value = settings.memory_imagination;
        if (tags) tags.value = settings.tag_mode;
        if (emoji) emoji.value = settings.allow_emoji ? 'on' : 'off';
        if (readable) readable.value = settings.readable ? 'on' : 'off';
    }

    async function persistSayaStyleSettings(settings) {
        const response = await fetch(SAYA_STYLE_API_URL, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({settings})
        });
        const data = await response.json();
        if (!response.ok || data.status !== 'success') {
            throw new Error(data.detail || data.message || 'Unable to save Saya style settings');
        }
        return saveSayaStyleSettings(data.settings || settings);
    }

    async function loadSayaStyleIntoModal() {
        let settings = getSayaStyleSettings();
        try {
            const response = await fetch(SAYA_STYLE_API_URL);
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || 'Unable to load Saya style settings');
            if (data.exists) {
                settings = saveSayaStyleSettings(data.settings || settings);
            } else {
                settings = await persistSayaStyleSettings(settings);
            }
        } catch (e) {
            console.warn('Using local Saya style settings because server sync failed.', e);
        }
        applySayaStyleToModal(settings);
    }

    function collectSayaStyleFromModal() {
        return {
            base_style: document.getElementById('saya-style-base')?.value || DEFAULT_SAYA_STYLE.base_style,
            response_language: document.getElementById('saya-style-language')?.value || DEFAULT_SAYA_STYLE.response_language,
            attachment_level: document.getElementById('saya-style-attachment')?.value || DEFAULT_SAYA_STYLE.attachment_level,
            sociality: document.getElementById('saya-style-sociality')?.value || DEFAULT_SAYA_STYLE.sociality,
            memory_imagination: document.getElementById('saya-style-memory-imagination')?.value || DEFAULT_SAYA_STYLE.memory_imagination,
            tag_mode: document.getElementById('saya-style-tags')?.value || DEFAULT_SAYA_STYLE.tag_mode,
            allow_emoji: document.getElementById('saya-style-emoji')?.value === 'on',
            readable: document.getElementById('saya-style-readable')?.value === 'on'
        };
    }

    async function openSayaStyleModal() {
        const menu = document.getElementById('settings-menu');
        if (menu && menu.style.display === 'flex') menu.style.display = 'none';
        await loadSayaStyleIntoModal();
        openSettingsWorkspace('style');
    }

    function closeSayaStyleModal() {
        closeSettingsWorkspace();
    }

    async function saveSayaStyleSettingsFromModal() {
        const settings = saveSayaStyleSettings(collectSayaStyleFromModal());
        try {
            await persistSayaStyleSettings(settings);
            showToast("Saya style saved.");
            closeSayaStyleModal();
        } catch (e) {
            showToast("Saved in this browser, but could not be synced.", { type: 'error', duration: 4200 });
            console.warn('Saya style sync failed:', e);
        }
    }

    async function resetSayaStyleSettings() {
        const settings = saveSayaStyleSettings(DEFAULT_SAYA_STYLE);
        try {
            await persistSayaStyleSettings(settings);
        } catch (e) {
            showToast("Reset locally, but could not be synced.", { type: 'error', duration: 4200 });
            console.warn('Saya style sync failed:', e);
        }
        applySayaStyleToModal(settings);
        showToast("Saya style was reset to defaults.");
    }

    function openApiSettingsModal() {
        const menu = document.getElementById('settings-menu');
        if (menu && menu.style.display === 'flex') menu.style.display = 'none';
        loadApiSettings();
        openSettingsWorkspace('llm');
        const statusEl = document.getElementById('llm-connection-status');
        if (statusEl) { statusEl.textContent = 'Not tested'; statusEl.className = 'st-badge'; }
    }

    function closeApiSettingsModal() {
        closeSettingsWorkspace();
    }

    function saveApiSettingsFromModal() {
        saveCurrentApiSettings();
        showToast("LLM API settings saved.");
        closeApiSettingsModal();
    }

    // Initialize on load
    document.addEventListener('DOMContentLoaded', () => {
        loadApiSettings();
        loadModesIntoSelects();
        initModeDropdown();
        updateModeProgressRing();
        loadPendingMemoryCandidates();
        initializeUserProfile();
        setCurrentRoleMode(currentRoleMode || '', {persist: false});
        const chatContent = document.getElementById('chat-content');
        if (chatContent) {
            chatContent.addEventListener('scroll', updateQuestionIndexActiveState, {passive: true});
            new MutationObserver(refreshQuestionIndex).observe(chatContent, {childList: true});
        }
        refreshQuestionIndex();
    });

    function positionSettingsMenu() {
        const menu = document.getElementById('settings-menu');
        if (!menu) return false;
        const panel = document.querySelector('.model-panel');
        if (!panel || panel.classList.contains('collapsed')) {
            menu.style.display = 'none';
            return false;
        }
        const profileCard = document.getElementById('user-profile-card');
        const panelRect = panel?.getBoundingClientRect();
        const profileRect = profileCard?.getBoundingClientRect();
        const computedMenuWidth = parseFloat(window.getComputedStyle(menu).width) || 200;
        const menuWidth = menu.getBoundingClientRect().width || computedMenuWidth;
        const viewportWidth = document.documentElement.clientWidth || window.innerWidth;
        const viewportHeight = document.documentElement.clientHeight || window.innerHeight;
        const anchorRight = panelRect && panelRect.width > 1 ? panelRect.right : 0;
        const maxLeft = Math.max(0, viewportWidth - menuWidth - 8);
        const left = Math.min(maxLeft, Math.max(0, anchorRight));
        const bottom = profileRect
            ? Math.max(8, viewportHeight - profileRect.bottom)
            : 18;
        menu.style.left = `${left}px`;
        menu.style.right = 'auto';
        menu.style.bottom = `${bottom}px`;
        return true;
    }

    function toggleSettings(event) {
        if (event) event.stopPropagation();
        const menu = document.getElementById('settings-menu');
        if (menu.style.display === 'flex') {
            menu.style.display = 'none';
        } else {
            if (positionSettingsMenu()) {
                menu.style.display = 'flex';
            }
        }
    }

    // Close settings when clicking outside
    document.addEventListener('click', function(event) {
        const menu = document.getElementById('settings-menu');
        const settingsTrigger = event.target.closest('.btn-settings');
        if (menu && menu.style.display === 'flex' && !menu.contains(event.target) && !settingsTrigger) {
            menu.style.display = 'none';
        }
    });

    window.addEventListener('resize', () => {
        const menu = document.getElementById('settings-menu');
        if (menu && menu.style.display === 'flex') {
            positionSettingsMenu();
        }
    });

    // --- PROMPT LOGIC ---
    function openPromptModal() {
        toggleSettings();
        openSettingsWorkspace('prompt');
        loadPrompt();
    }

    function closePromptModal() {
        closeSettingsWorkspace();
        // Reset state
        const editor = document.getElementById('system-prompt-editor');
        if (editor) {
            editor.disabled = true;
            editor.style.borderColor = '';
        }
        const unlockBtn = document.getElementById('btn-unlock-prompt');
        const saveBtn = document.getElementById('btn-save-prompt');
        if (unlockBtn) unlockBtn.style.display = '';
        if (saveBtn) saveBtn.style.display = 'none';
        const badge = document.getElementById('prompt-state-badge');
        if (badge) { badge.textContent = 'Locked'; badge.className = 'st-badge'; }
        const dirty = document.getElementById('prompt-dirty-hint');
        if (dirty) dirty.hidden = true;
    }

    document.addEventListener('DOMContentLoaded', () => {
        const editor = document.getElementById('system-prompt-editor');
        editor?.addEventListener('input', () => {
            if (editor.disabled) return;
            const dirty = document.getElementById('prompt-dirty-hint');
            if (dirty) dirty.hidden = false;
        });
    });

    function setPromptEditingState(editing) {
        const editor = document.getElementById('system-prompt-editor');
        const badge = document.getElementById('prompt-state-badge');
        if (editor) {
            editor.disabled = !editing;
            editor.style.borderColor = editing ? 'rgba(251,191,36,.55)' : '';
        }
        const unlockBtn = document.getElementById('btn-unlock-prompt');
        const saveBtn = document.getElementById('btn-save-prompt');
        if (unlockBtn) unlockBtn.style.display = editing ? 'none' : '';
        if (saveBtn) saveBtn.style.display = editing ? '' : 'none';
        if (badge) {
            badge.textContent = editing ? 'Editing' : 'Locked';
            badge.className = `st-badge${editing ? ' warning' : ''}`;
        }
        if (!editing) {
            const dirty = document.getElementById('prompt-dirty-hint');
            if (dirty) dirty.hidden = true;
        }
    }

    async function loadPrompt() {
        const editor = document.getElementById('system-prompt-editor');
        const errorBox = document.getElementById('prompt-load-error');
        editor.value = '';
        if (errorBox) errorBox.hidden = true;
        try {
            const res = await fetch('/api/system_prompt');
            const data = await res.json();
            editor.value = data.prompt;
        } catch(e) {
            editor.value = '';
            if (errorBox) {
                errorBox.hidden = false;
                errorBox.innerHTML = `${ic('alert', 'icn-sm')}<span>The system prompt could not be loaded. Your current prompt is unchanged and safe.</span><button type="button" onclick="loadPrompt()">Retry</button>${technicalDetailsHtml(e)}`;
            }
        }
    }

    function unlockPrompt() {
        showConfirmDialog({
            title: 'Unlock the system prompt?',
            message: 'Changing the system prompt can fundamentally alter Saya\'s behavior and personality.',
            impact: ['Applies to every future reply once saved', 'The previous prompt is not versioned — keep your own copy if it matters'],
            confirmLabel: 'Unlock to Edit',
            danger: true,
            onConfirm: close => {
                setPromptEditingState(true);
                close();
            }
        });
    }

    async function savePrompt() {
        showConfirmDialog({
            title: 'Apply system prompt changes?',
            message: 'This will immediately affect all subsequent responses.',
            confirmLabel: 'Apply',
            onConfirm: async close => {
                close();
                const editor = document.getElementById('system-prompt-editor');
                const newPrompt = editor.value;
                try {
                    const res = await fetch('/api/system_prompt/update', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({new_prompt: newPrompt})
                    });
                    const data = await res.json();
                    if(data.status === 'success') {
                        showToast('System prompt updated.');
                        setPromptEditingState(false);
                        closePromptModal();
                    } else {
                        showToast('Update failed: ' + (data.message || 'unknown error'), { type: 'error' });
                    }
                } catch(e) {
                    showToast('Could not save the prompt. It is unchanged.', { type: 'error' });
                    console.error('Error saving prompt:', e);
                }
            }
        });
    }

    // --- MEMORY SYSTEM UI ---
    let memoryAutoSave = true;
    let memoryAutoMergeEnabled = false;
    let memoryAutoMergeDays = 7;
    let memory2Enabled = true;
    let memoryRelevantCount = 8;
    let memoryIncludeMetadata = true;
    let memoryAutonomousEnabled = false;
    let memoryLayeredEnabled = true;
    let memoryApiContextTokens = 1000000;
    let memoryLocalContextTokens = 8192;
    let memoryPromptBudgetTokens = 12000;
    let memoryPromptBudgetRatio = 0.15;
    let memoryItemBudgetTokens = 700;
    let memoryMaxCharsCore = 1200;
    let memoryMaxCharsSemantic = 2400;
    let memoryMaxCharsEpisodic = 3600;
    let memoryMaxCharsKnowledgeSimple = 1200;
    let memoryMaxCharsKnowledgeComplex = 3600;
    let memoryCoreLimit = 10;
    let memoryCandidatePoolSize = 80;
    let memoryActiveSoftLimit = 5000;
    let memoryArchiveSoftLimit = 50000;
    let memoryActiveHardLimit = 6000;
    let memoryArchiveHardLimit = 60000;
    let memoryCapacityWarningRatio = 0.80;
    let memoryCapacityAutoCompactEnabled = true;
    let memoryColdArchiveEnabled = true;
    let memoryColdArchiveBatchSize = 200;
    let memoryRetrievalThreshold = 0.08;
    let memoryAccessFlushSeconds = 120;
    let memoryCompressionEnabled = false;
    let memoryCompressionAgeDays = 30;
    let memoryCompressionMinGroup = 3;
    let memoryCompressionGroupSize = 20;
    let memoryCompressionSummaryChars = 1800;
    let memoryCompressionMaxGenerations = 2;
    let memorySqliteEnabled = true;
    let memorySqliteFtsEnabled = true;
    let memoryEmbeddingEnabled = false;
    let memoryEmbeddingProvider = 'sentence_transformers';
    let memoryEmbeddingModel = 'BAAI/bge-m3';
    let memoryEmbeddingDevice = 'cpu';
    let memoryEmbeddingApiUrl = '';
    let memoryEmbeddingApiKeyEnv = 'EMBEDDING_API_KEY';
    let memoryEmbeddingBatchSize = 8;
    let memoryEmbeddingCandidateCount = 80;
    let memoryEmbeddingMinSimilarity = 0.30;
    let memoryEmbeddingIndexBatchLimit = 500;
    let memoryEmbeddingApiTimeout = 120;
    let memoryEmbeddingMaxChars = 12000;
    let memoryEmbeddingAutoIndexNew = false;
    let memoryViewerIncludeArchived = false;
    let memoryViewerCategory = '';
    let memoryViewerLayer = '';
    const selectedMemoryIds = new Set();
    const renderedMemoryCandidateIds = new Set();
    const renderedMemoryMaintenanceEventIds = new Set();
    let checkinState = null;
    let checkinSelectedDate = null;
    let checkinViewYear = null;
    let checkinViewMonth = null;
    let pendingCheckinContext = "";
    let currentProjectId = "";
    let projectSidebarViewId = "";
    let projectsCache = [];

    function showMemoryMaintenanceNotice(event) {
        // This engine uses no LLM tokens for deterministic capacity maintenance.
        if (!event || !event.id || renderedMemoryMaintenanceEventIds.has(String(event.id))) return;
        renderedMemoryMaintenanceEventIds.add(String(event.id));
        document.querySelector('.memory-maintenance-toast')?.remove();
        const toast = document.createElement('aside');
        const level = ['critical', 'warning'].includes(String(event.level)) ? String(event.level) : 'info';
        const aiRecommended = event.details?.ai_recommended === true;
        const compressionLabel = event.compression_engine === 'ai_deepseek'
            ? 'Confirmed DeepSeek compression'
            : 'Local deterministic compression';
        toast.className = `memory-maintenance-toast ${level}`;
        toast.setAttribute('role', 'status');
        toast.innerHTML = `
            <span class="memory-maintenance-toast-kicker">MEMORY CAPACITY</span>
            <strong>${level === 'critical' ? 'Forced capacity protection' : 'Capacity maintenance'}</strong>
            <p>${escapeHtml(event.message || 'Memory capacity maintenance completed.')}</p>
            <small>${compressionLabel} · ${Number(event.token_usage || 0)} tokens</small>
            <div>
                ${aiRecommended
                    ? `<button type="button" onclick="openAiMemoryCompressionPlanner('${escapeJsString(event.id)}')">${ic('zap', 'icn-sm')}Review AI compression</button>`
                    : `<button type="button" onclick="openMemorySettings(); acknowledgeMemoryMaintenance('${escapeJsString(event.id)}')">${ic('settings', 'icn-sm')}Review settings</button>`}
                <button type="button" onclick="acknowledgeMemoryMaintenance('${escapeJsString(event.id)}')">${ic('close', 'icn-sm')}Dismiss</button>
            </div>`;
        document.body.appendChild(toast);
    }

    function surfaceMemoryMaintenanceResult(result) {
        const maintenance = result?.maintenance || result;
        if (maintenance?.notify_user && maintenance.event) {
            showMemoryMaintenanceNotice(maintenance.event);
        }
    }

    async function acknowledgeMemoryMaintenance(eventId) {
        document.querySelector('.memory-maintenance-toast')?.remove();
        try {
            await fetch('/api/kb/maintenance/acknowledge', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({event_id: eventId})
            });
        } catch (error) {
            console.warn('Unable to acknowledge memory maintenance notice', error);
        }
    }

    async function checkMemoryMaintenanceNotices() {
        try {
            const response = await fetch('/api/kb/maintenance/status');
            if (!response.ok) return;
            const data = await response.json();
            const pending = Array.isArray(data.pending_events) ? data.pending_events : [];
            if (pending.length) showMemoryMaintenanceNotice(pending[pending.length - 1]);
        } catch (error) {
            console.warn('Unable to check memory capacity notices', error);
        }
    }

    document.addEventListener('DOMContentLoaded', () => {
        window.setTimeout(checkMemoryMaintenanceNotices, 1200);
    });

    let aiMemoryCompressionPlan = null;
    let aiMemoryCompressionEventId = '';

    function closeAiMemoryCompressionPlanner() {
        const modal = document.getElementById('ai-memory-compression-modal');
        if (modal) modal.style.display = 'none';
    }

    async function abandonAiMemoryCompression() {
        if (aiMemoryCompressionEventId) {
            await acknowledgeMemoryMaintenance(aiMemoryCompressionEventId);
        }
        closeAiMemoryCompressionPlanner();
    }

    async function enableAiMemoryCompressionFromPlanner() {
        try {
            const response = await fetch('/api/kb/settings', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({memory_ai_compression_enabled: true})
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || 'Unable to enable AI compression');
            saveModeSettings({ai_memory_compression_enabled: true});
            await loadAiMemoryCompressionPreview('pressure', 50);
        } catch (error) {
            showToast(`AI compression could not be enabled: ${error.message || error}`, { type: 'error', duration: 3600 });
        }
    }

    async function openAiMemoryCompressionPlanner(eventId = '') {
        aiMemoryCompressionEventId = String(eventId || '');
        aiMemoryCompressionPlan = null;
        document.querySelector('.memory-maintenance-toast')?.remove();
        const modal = document.getElementById('ai-memory-compression-modal');
        const body = document.getElementById('ai-memory-compression-body');
        if (!modal || !body) return;
        modal.style.display = 'flex';
        if (getModeSettings().ai_memory_compression_enabled !== true) {
            body.innerHTML = `
                <div class="ai-compression-disabled-state">
                    <h3>AI compression is currently off</h3>
                    <p>Enable it to build a scoped DeepSeek plan and see the estimated tokens. Enabling the switch does not start compression.</p>
                    <div class="workspace-form-actions">
                        <button class="workspace-secondary-action" onclick="abandonAiMemoryCompression()">${ic('close', 'icn-sm')}Skip AI compression</button>
                        <button class="workspace-primary-action" onclick="enableAiMemoryCompressionFromPlanner()">${ic('zap', 'icn-sm')}Enable &amp; estimate</button>
                    </div>
                </div>`;
            return;
        }
        await loadAiMemoryCompressionPreview('pressure', 50);
    }

    async function loadAiMemoryCompressionPreview(scope = 'pressure', limit = 50) {
        const body = document.getElementById('ai-memory-compression-body');
        if (!body) return;
        body.innerHTML = '<div class="ai-compression-loading">Building a safe compression range and DeepSeek token estimate...</div>';
        try {
            const response = await fetch('/api/kb/ai-compression/preview', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    scope,
                    limit: Number(limit),
                    project_id: currentProjectId || ''
                })
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || 'Unable to estimate AI compression');
            aiMemoryCompressionPlan = data;
            renderAiMemoryCompressionPreview(data, scope, Number(limit));
        } catch (error) {
            body.innerHTML = `
                <div class="ai-compression-error">
                    <h3>Estimate unavailable</h3>
                    <p>${escapeHtml(error.message || error)}</p>
                    <div class="workspace-form-actions">
                        <button class="workspace-secondary-action" onclick="abandonAiMemoryCompression()">${ic('close', 'icn-sm')}Skip AI compression</button>
                        <button class="workspace-primary-action" onclick="openAiMemoryCompressionPlanner('${escapeJsString(aiMemoryCompressionEventId)}')">${ic('refresh', 'icn-sm')}Retry</button>
                    </div>
                </div>`;
        }
    }

    function renderAiMemoryCompressionPreview(data, scope, limit) {
        const body = document.getElementById('ai-memory-compression-body');
        if (!body) return;
        const estimate = data.token_estimate || {};
        const apiSettings = getSavedApiSettings();
        const selectedModel = apiSettings.memory_strong_model_name || apiSettings.api_model_name || 'configured DeepSeek model';
        const noSafeGroups = data.status !== 'ready' || !data.plan_id;
        body.innerHTML = `
            <div class="ai-compression-workspace">
                <section class="page-intro">
                    <div class="page-intro-copy"><h3 class="page-intro-title">Scoped semantic compression</h3><p class="page-intro-sub">Local compression has stopped at its safe generation limit. Full source memories remain archived after a successful AI run.</p></div>
                    <span class="workspace-status-pill">${noSafeGroups ? 'No safe group' : `${Number(data.candidate_count || 0)} memories`}</span>
                </section>
                <div class="ai-compression-controls">
                    <label>Compression range
                        <select id="ai-compression-scope">
                            <option value="pressure" ${scope === 'pressure' ? 'selected' : ''}>Capacity relief (recommended)</option>
                            <option value="repeated" ${scope === 'repeated' ? 'selected' : ''}>Generation-limited summaries only</option>
                            <option value="project" ${scope === 'project' ? 'selected' : ''} ${currentProjectId ? '' : 'disabled'}>Current project only</option>
                            <option value="oldest" ${scope === 'oldest' ? 'selected' : ''}>Oldest safe memories</option>
                        </select>
                    </label>
                    <label>Maximum source memories
                        <input id="ai-compression-limit" type="number" min="2" max="200" value="${Math.max(2, Math.min(200, Number(limit || 50)))}">
                    </label>
                    <button class="workspace-secondary-action" onclick="refreshAiMemoryCompressionEstimate()">${ic('refresh', 'icn-sm')}Update estimate</button>
                </div>
                ${noSafeGroups ? `
                    <div class="ai-compression-empty">
                        <strong>No semantically safe group can reduce the selected range.</strong>
                        <p>Saya will not mix unrelated topics merely to reduce the count. You can choose another range or skip AI; hard-limit cold storage remains recoverable.</p>
                    </div>` : `
                    <div class="ai-compression-token-grid">
                        <span><small>Estimated input</small><strong>${Number(estimate.input_tokens || 0).toLocaleString()}</strong></span>
                        <span><small>Maximum output</small><strong>${Number(estimate.max_output_tokens || 0).toLocaleString()}</strong></span>
                        <span><small>Estimated total</small><strong>${Number(estimate.estimated_total_tokens || 0).toLocaleString()}</strong></span>
                        <span><small>Likely range</small><strong>${Number(estimate.likely_min_tokens || 0).toLocaleString()}–${Number(estimate.likely_max_tokens || 0).toLocaleString()}</strong></span>
                    </div>
                    <div class="ai-compression-estimate-note">DeepSeek-compatible CJK estimate for <b>${escapeHtml(selectedModel)}</b>. The provider's billed count is authoritative and will be shown after completion. Plan expires at ${escapeHtml(data.expires_at || '')}.</div>
                    <div class="ai-compression-group-list">${(data.groups || []).map(group => `
                        <span><b>${escapeHtml(group.topic || group.category || 'memory')}</b><small>${Number(group.memory_count || 0)} sources</small></span>
                    `).join('')}</div>
                    <label class="ai-compression-consent"><input id="ai-compression-consent" type="checkbox" onchange="updateAiCompressionExecuteState()"> I confirm this range and estimated DeepSeek token usage.</label>
                `}
                <div id="ai-compression-run-status" class="ai-compression-run-status"></div>
                <div class="workspace-form-actions">
                    <button class="workspace-secondary-action" onclick="abandonAiMemoryCompression()">${ic('close', 'icn-sm')}Skip AI compression</button>
                    ${noSafeGroups ? '' : `<button id="ai-compression-execute" class="workspace-primary-action" onclick="executeAiMemoryCompression()" disabled>${ic('zap', 'icn-sm')}Compress with DeepSeek</button>`}
                </div>
            </div>`;
    }

    function refreshAiMemoryCompressionEstimate() {
        const scope = document.getElementById('ai-compression-scope')?.value || 'pressure';
        const limit = Number(document.getElementById('ai-compression-limit')?.value || 50);
        loadAiMemoryCompressionPreview(scope, limit);
    }

    function updateAiCompressionExecuteState() {
        const button = document.getElementById('ai-compression-execute');
        if (button) button.disabled = document.getElementById('ai-compression-consent')?.checked !== true;
    }

    async function executeAiMemoryCompression() {
        if (!aiMemoryCompressionPlan?.plan_id || document.getElementById('ai-compression-consent')?.checked !== true) return;
        const settings = getSavedApiSettings();
        if (!settings.api_key) {
            confirmAction('A DeepSeek API key is required. Open LLM API settings now?', () => {
                closeAiMemoryCompressionPlanner();
                openApiSettingsModal();
            });
            return;
        }
        const estimate = aiMemoryCompressionPlan.token_estimate || {};
        confirmAction(`Compress ${Number(aiMemoryCompressionPlan.candidate_count || 0)} memories now? Estimated total: ${Number(estimate.estimated_total_tokens || 0).toLocaleString()} DeepSeek tokens. Full sources will be archived.`, async () => {

            const button = document.getElementById('ai-compression-execute');
            const status = document.getElementById('ai-compression-run-status');
            if (button) button.disabled = true;
            if (status) status.textContent = 'DeepSeek is compressing the confirmed range...';
            try {
                const response = await fetch('/api/kb/ai-compression/execute', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        plan_id: aiMemoryCompressionPlan.plan_id,
                        api_key: settings.api_key || '',
                        api_url: settings.api_url || '',
                        api_model_name: settings.api_model_name || '',
                        memory_strong_model_name: settings.memory_strong_model_name || ''
                    })
                });
                const data = await response.json();
                if (!response.ok) throw new Error(data.detail || 'AI compression failed');
                if (aiMemoryCompressionEventId) await acknowledgeMemoryMaintenance(aiMemoryCompressionEventId);
                const usage = data.usage || {};
                if (status) status.innerHTML = `Completed: ${Number(data.archived_count || 0)} sources → ${Number(data.summary_count || 0)} summaries. Actual usage: <b>${Number(usage.total_tokens || 0).toLocaleString()} tokens</b>.`;
                surfaceMemoryMaintenanceResult(data);
                if (button) button.remove();
            } catch (error) {
                if (status) status.textContent = `No memories were changed: ${error.message || error}`;
                if (button) button.disabled = false;
            }
    });    }
    let currentQuestTab = 'active';
    let currentRoleMode = localStorage.getItem('saya_current_role_mode') || "";
    let learnerState = {tasks: [], runs: [], counts: {}, creators: []};
    let learnerPollTimer = null;
    let learnerSchedulerTimer = null;
    let learnerHeartbeatBusy = false;
    let learnerEditingTaskId = null;
    let ankiState = {status: 'not_configured', settings: {}, profiles: [], summary: null, decks: [], daily_activity: []};
    let ankiBusy = false;
    let ankiDraftPath = '';
    let assistantRoleState = null;
    let assistantRoleEntryMemoryStamp = localStorage.getItem('saya_assistant_role_entry_memory_stamp') || "";
    let assistantMemoryChangedSinceEntry = localStorage.getItem('saya_assistant_role_memory_changed') === 'true';
    currentProjectId = localStorage.getItem('saya_current_project_id') || "";

    // --- DAILY CHECK-IN LOGIC ---
    function checkinStatusClass(status) {
        return ['completed', 'partial', 'missed', 'pending', 'future', 'no_tasks'].includes(status)
            ? status
            : 'pending';
    }

    function checkinDayStatusClass(day) {
        if (day?.date && checkinState?.today && day.date > checkinState.today) {
            return 'future';
        }
        return checkinStatusClass(day?.status);
    }

    function formatCheckinRate(value) {
        return `${Math.round(Number(value || 0) * 100)}%`;
    }

    function createCheckinLocalId(prefix = 'daily') {
        if (window.crypto && typeof window.crypto.randomUUID === 'function') {
            return `${prefix}-${window.crypto.randomUUID()}`;
        }
        return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    }

    function checkinMonthKey(year, month) {
        return `${String(year).padStart(4, '0')}-${String(month).padStart(2, '0')}`;
    }

    function isCheckinMonthAllowed(year, month) {
        const windowInfo = checkinState?.date_window;
        if (!windowInfo) return true;
        const key = checkinMonthKey(year, month);
        return key >= windowInfo.min_month && key <= windowInfo.max_month;
    }

    function isCheckinDateAllowed(date) {
        const windowInfo = checkinState?.date_window;
        if (!windowInfo || !date) return true;
        return date >= windowInfo.min_date && date <= windowInfo.max_date;
    }

    function checkinWindowText() {
        const windowInfo = checkinState?.date_window;
        if (!windowInfo) return '';
        return `${windowInfo.min_month} to ${windowInfo.max_month}`;
    }

    function heatColorForStatus(status) {
        const palette = {
            completed: '#0b3d78',
            active: '#0b3d78',
            partial: '#3d155f',
            no_tasks: '#3d155f',
            inactive: '#3d155f',
            missed: '#65151f',
            pending: '#111111',
            future: '#050505'
        };
        return palette[status] || '#111111';
    }

    function renderContinuousHeatStrip(container, statuses) {
        if (!container) return;
        const clean = (statuses || []).map(status => heatColorForStatus(status));
        if (!clean.length) {
            container.style.setProperty('--heat-strip', '#050505');
            return;
        }
        if (clean.length === 1) {
            container.style.setProperty('--heat-strip', clean[0]);
            return;
        }
        const stops = clean.map((color, index) => {
            const position = (index / (clean.length - 1)) * 100;
            return `${color} ${position.toFixed(2)}%`;
        });
        container.style.setProperty('--heat-strip', `linear-gradient(90deg, ${stops.join(', ')})`);
    }

    async function loadCheckinState(options = {}) {
        const now = new Date();
        if (!checkinViewYear) checkinViewYear = now.getFullYear();
        if (!checkinViewMonth) checkinViewMonth = now.getMonth() + 1;
        if (!checkinSelectedDate) {
            const yyyy = now.getFullYear();
            const mm = String(now.getMonth() + 1).padStart(2, '0');
            const dd = String(now.getDate()).padStart(2, '0');
            checkinSelectedDate = `${yyyy}-${mm}-${dd}`;
        }

        const params = new URLSearchParams({
            year: String(checkinViewYear),
            month: String(checkinViewMonth),
            selected_date: checkinSelectedDate
        });
        const controller = new AbortController();
        const timeoutId = window.setTimeout(() => controller.abort(), 8000);
        let response;
        try {
            response = await fetch(`/api/checkins/status?${params.toString()}`, {
                signal: controller.signal
            });
            if (!response.ok) throw new Error(`Check-in status failed: ${response.status}`);
            checkinState = await response.json();
        } catch (error) {
            if (error?.name === 'AbortError') {
                throw new Error('Daily status timed out after 8 seconds');
            }
            throw error;
        } finally {
            window.clearTimeout(timeoutId);
        }
        if (checkinState.year) checkinViewYear = checkinState.year;
        if (checkinState.month) checkinViewMonth = checkinState.month;
        if (checkinState.selected_date) checkinSelectedDate = checkinState.selected_date;
        if (checkinState.auto_report?.generated?.length) {
            console.info("Auto-saved check-in report:", checkinState.auto_report.generated);
        }
        renderCheckinPeek();
        if (options.renderModal) renderCheckinModal();
        return checkinState;
    }

    async function initializeCheckinPeek() {
        try {
            await loadCheckinState();
        } catch (e) {
            console.warn("Unable to load check-in state", e);
            renderCheckinLoadError();
        }
    }

    async function loadSessionStats() {
        try {
            const response = await fetch('/api/sessions/stats');
            const stats = await response.json();
            renderSearchPeek(stats);
        } catch (e) {
            console.warn("Unable to load chat activity stats", e);
        }
    }

    function renderSearchPeek(stats) {
        const chatCountEl = document.getElementById('search-peek-chat-count');
        const dayCountEl = document.getElementById('search-peek-day-count');
        const dotsEl = document.getElementById('search-peek-dots');
        if (!stats || !chatCountEl || !dayCountEl || !dotsEl) return;

        chatCountEl.innerText = `Chats: ${Number(stats.total_sessions || 0)}`;
        dayCountEl.innerText = `Active days: ${Number(stats.active_days_total || 0)}`;

        dotsEl.innerHTML = '';
        const heatStatuses = [];
        (stats.month_days || []).forEach(day => {
            const dot = document.createElement('span');
            const status = ['active', 'inactive', 'future'].includes(day.status) ? day.status : 'inactive';
            heatStatuses.push(status);
            dot.className = `chat-dot ${status}`;
            dot.title = `${day.date}: ${status === 'active' ? 'chatted' : status === 'future' ? 'future' : 'no chat'}`;
            dotsEl.appendChild(dot);
        });
        renderContinuousHeatStrip(dotsEl, heatStatuses);
    }

    // --- INPUT SUGGESTIONS ---
    const suggestionLanguageAliases = {
        'zh': 'zh-cn',
        'zh-hans': 'zh-cn',
        'zh-cn': 'zh-cn',
        'zh-sg': 'zh-cn',
        'zh-hant': 'zh-tw',
        'zh-tw': 'zh-tw',
        'zh-hk': 'zh-tw',
        'zh-mo': 'zh-tw',
        'en': 'en',
        'ja': 'ja',
        'ko': 'ko',
        'es': 'es',
        'fr': 'fr',
        'de': 'de',
        'ru': 'ru',
        'pt': 'pt',
        'ar': 'ar',
        'hi': 'hi',
        'it': 'it'
    };

    const localizedSuggestionTexts = {
        tone_smiling: {
            'zh-cn': '笑着说：',
            'zh-tw': '笑著說：',
            en: 'says with a smile:',
            ja: '笑顔で言う：',
            ko: '웃으며 말한다:',
            es: 'dice sonriendo:',
            fr: 'dit avec un sourire :',
            de: 'sagt lächelnd:',
            ru: 'говорит с улыбкой:',
            pt: 'diz sorrindo:',
            ar: 'تقول بابتسامة:',
            hi: 'मुस्कुराकर कहती है:',
            it: 'dice sorridendo:'
        },
        tone_sad: {
            'zh-cn': '有点悲伤地说：',
            'zh-tw': '有點悲傷地說：',
            en: 'says a little sadly:',
            ja: '少し悲しそうに言う：',
            ko: '조금 슬프게 말한다:',
            es: 'dice con un poco de tristeza:',
            fr: 'dit avec un peu de tristesse :',
            de: 'sagt etwas traurig:',
            ru: 'говорит немного грустно:',
            pt: 'diz um pouco triste:',
            ar: 'تقول بحزن خفيف:',
            hi: 'थोड़ा उदास होकर कहती है:',
            it: "dice con un po' di tristezza:"
        },
        tone_annoyed: {
            'zh-cn': '有点懊恼，但还是想好好说：',
            'zh-tw': '有點懊惱，但還是想好好說：',
            en: 'says, a little annoyed but still trying to be kind:',
            ja: '少し苛立ちながらも、ちゃんと伝えようとして言う：',
            ko: '조금 짜증이 나지만 그래도 다정하게 말하려고 한다:',
            es: 'dice, un poco molesto pero intentando hablar con calma:',
            fr: 'dit, un peu agacé mais en essayant de rester doux :',
            de: 'sagt, etwas genervt, aber weiterhin freundlich:',
            ru: 'говорит немного раздраженно, но стараясь быть мягко:',
            pt: 'diz, um pouco irritado mas tentando falar com carinho:',
            ar: 'تقول بانزعاج خفيف لكنها تحاول الكلام بلطف:',
            hi: 'थोड़ी झुंझलाहट के साथ, फिर भी नरमी से कहती है:',
            it: "dice, un po' infastidita ma cercando di essere gentile:"
        },
        tone_soft: {
            'zh-cn': '轻声说：',
            'zh-tw': '輕聲說：',
            en: 'says softly:',
            ja: 'やさしく言う：',
            ko: '부드럽게 말한다:',
            es: 'dice suavemente:',
            fr: 'dit doucement :',
            de: 'sagt leise:',
            ru: 'говорит мягко:',
            pt: 'diz suavemente:',
            ar: 'تقول بلطف:',
            hi: 'धीरे से कहती है:',
            it: 'dice piano:'
        },
        tone_shy: {
            'zh-cn': '有点害羞地说：',
            'zh-tw': '有點害羞地說：',
            en: 'says a little shyly:',
            ja: '少し恥ずかしそうに言う：',
            ko: '조금 부끄러워하며 말한다:',
            es: 'dice con un poco de timidez:',
            fr: 'dit avec un peu de timidité :',
            de: 'sagt etwas schüchtern:',
            ru: 'говорит немного смущенно:',
            pt: 'diz um pouco timidamente:',
            ar: 'تقول بخجل قليل:',
            hi: 'थोड़ा शर्माकर कहती है:',
            it: "dice un po' timidamente:"
        },
        action_pat_head: {
            'zh-cn': '（轻轻摸摸 Saya 的头）',
            'zh-tw': '（輕輕摸摸 Saya 的頭）',
            en: " *gently pats Saya's head* ",
            ja: '（Sayaの頭をそっと撫でる）',
            ko: '（Saya의 머리를 살짝 쓰다듬는다）',
            es: '(acaricia suavemente la cabeza de Saya)',
            fr: '(caresse doucement la tête de Saya)',
            de: '(streichelt Saya sanft über den Kopf)',
            ru: '(нежно гладит Saya по голове)',
            pt: '(acaricia suavemente a cabeça de Saya)',
            ar: '(تربت برفق على رأس Saya)',
            hi: '(धीरे से Saya के सिर पर हाथ फेरता है)',
            it: '(accarezza dolcemente la testa di Saya)'
        },
        action_hold_hand: {
            'zh-cn': '（牵住 Saya 的手）',
            'zh-tw': '（牽住 Saya 的手）',
            en: " *holds Saya's hand for a moment* ",
            ja: '（Sayaの手をそっと握る）',
            ko: '（Saya의 손을 살며시 잡는다）',
            es: '(toma suavemente la mano de Saya)',
            fr: '(prend doucement la main de Saya)',
            de: '(hält sanft Sayas Hand)',
            ru: '(нежно держит Saya за руку)',
            pt: '(segura a mão de Saya com suavidade)',
            ar: '(تمسك يد Saya بلطف)',
            hi: '(धीरे से Saya का हाथ पकड़ता है)',
            it: '(prende dolcemente la mano di Saya)'
        },
        action_eye_contact: {
            'zh-cn': '（认真地看着 Saya）',
            'zh-tw': '（認真地看著 Saya）',
            en: " *looks into Saya's eyes* ",
            ja: '（Sayaの目をまっすぐ見る）',
            ko: '（Saya의 눈을 진지하게 바라본다）',
            es: '(mira a Saya a los ojos)',
            fr: '(regarde Saya dans les yeux)',
            de: '(sieht Saya in die Augen)',
            ru: '(внимательно смотрит Saya в глаза)',
            pt: '(olha nos olhos de Saya)',
            ar: '(تنظر إلى عيني Saya بتركيز)',
            hi: '(ध्यान से Saya की आंखों में देखता है)',
            it: '(guarda Saya negli occhi)'
        },
        action_hug: {
            'zh-cn': '（轻轻抱一下 Saya）',
            'zh-tw': '（輕輕抱一下 Saya）',
            en: ' *gently hugs Saya* ',
            ja: '（Sayaをそっと抱きしめる）',
            ko: '（Saya를 살짝 안아 준다）',
            es: '(abraza suavemente a Saya)',
            fr: '(serre doucement Saya dans ses bras)',
            de: '(umarmt Saya sanft)',
            ru: '(нежно обнимает Saya)',
            pt: '(abraca Saya suavemente)',
            ar: '(تعانق Saya بلطف)',
            hi: '(धीरे से Saya को गले लगाता है)',
            it: '(abbraccia dolcemente Saya)'
        },
        action_brush_hair: {
            'zh-cn': '（小心地帮 Saya 拨开脸侧的头发）',
            'zh-tw': '（小心地幫 Saya 撥開臉側的頭髮）',
            en: " *carefully brushes Saya's hair aside* ",
            ja: '（Sayaの顔にかかった髪をそっと払う）',
            ko: '（Saya의 얼굴 옆 머리카락을 조심히 넘겨 준다）',
            es: '(aparta con cuidado el cabello de Saya)',
            fr: '(écarte doucement les cheveux du visage de Saya)',
            de: '(streicht Saya vorsichtig die Haare aus dem Gesicht)',
            ru: '(осторожно убирает волосы с лица Saya)',
            pt: '(afasta com cuidado o cabelo do rosto de Saya)',
            ar: '(تزيح شعر Saya عن وجهها بحذر)',
            hi: '(सावधानी से Saya के चेहरे से बाल हटाता है)',
            it: '(scosta con cura i capelli dal viso di Saya)'
        }
    };

    const moodSuggestionChips = [
        {label: 'Tone: smiling', key: 'tone_smiling'},
        {label: 'Tone: sad', key: 'tone_sad'},
        {label: 'Tone: annoyed', key: 'tone_annoyed'},
        {label: 'Tone: soft', key: 'tone_soft'},
        {label: 'Tone: shy', key: 'tone_shy'}
    ];

    const actionSuggestionChips = [
        {label: 'Pat Saya’s head', text: ' *gently pats Saya’s head* '},
        {label: 'Hold Saya’s hand', text: ' *holds Saya’s hand for a moment* '},
        {label: 'Look at Saya', text: ' *looks into Saya’s eyes* '},
        {label: 'Give Saya a hug', text: ' *gently hugs Saya* '},
        {label: 'Brush her hair aside', text: ' *carefully brushes Saya’s hair aside* '}
    ];

    const safeActionSuggestionChips = [
        {label: 'Action: pat head', key: 'action_pat_head'},
        {label: 'Action: hold hand', key: 'action_hold_hand'},
        {label: 'Action: eye contact', key: 'action_eye_contact'},
        {label: 'Action: hug', key: 'action_hug'},
        {label: 'Action: brush hair', key: 'action_brush_hair'}
    ];

    function normalizeSuggestionLanguage(language) {
        const raw = String(language || '').trim().toLowerCase();
        if (!raw) return '';
        if (suggestionLanguageAliases[raw]) return suggestionLanguageAliases[raw];
        const primary = raw.split('-')[0];
        return suggestionLanguageAliases[primary] || '';
    }

    function detectSuggestionLanguageFromText(text) {
        const value = String(text || '');
        if (/[\u3040-\u30ff]/.test(value)) return 'ja';
        if (/[\uac00-\ud7af]/.test(value)) return 'ko';
        if (/[\u0600-\u06ff]/.test(value)) return 'ar';
        if (/[\u0900-\u097f]/.test(value)) return 'hi';
        if (/[\u0400-\u04ff]/.test(value)) return 'ru';
        if (/[\u4e00-\u9fff]/.test(value)) return 'zh-cn';
        if (/[A-Za-z]/.test(value)) return 'en';
        return '';
    }

    function getSuggestionLanguage() {
        const configured = String(getSayaStyleSettings().response_language || 'auto').toLowerCase();
        if (configured && configured !== 'auto') {
            return normalizeSuggestionLanguage(configured) || 'zh-cn';
        }
        const inputLanguage = detectSuggestionLanguageFromText(document.getElementById('user-input')?.value || '');
        if (inputLanguage) return inputLanguage;
        return normalizeSuggestionLanguage(navigator.language || '') || 'zh-cn';
    }

    function getSuggestionText(item) {
        if (!item) return '';
        if (typeof item.text === 'string') return item.text;
        const translations = localizedSuggestionTexts[item.key] || {};
        const language = getSuggestionLanguage();
        return translations[language] || translations.en || translations['zh-cn'] || '';
    }

    function insertSuggestionText(text) {
        const input = document.getElementById('user-input');
        if (!input) return;
        const start = input.selectionStart ?? input.value.length;
        const end = input.selectionEnd ?? input.value.length;
        const before = input.value.slice(0, start);
        const after = input.value.slice(end);
        const needsSpace = before && !/\s$/.test(before) && text && !/^\s/.test(text);
        const insertText = `${needsSpace ? ' ' : ''}${text}`;
        input.value = `${before}${insertText}${after}`;
        const nextPos = before.length + insertText.length;
        input.focus();
        input.setSelectionRange(nextPos, nextPos);
        updateInputSuggestions();
        autoResizeInput();
    }

    function updateInputSuggestions() {
        const strip = document.getElementById('input-suggestion-strip');
        const input = document.getElementById('user-input');
        if (!strip || !input) return;

        if (document.activeElement !== input && !strip.matches(':hover')) {
            strip.classList.remove('visible');
            return;
        }

        const source = input.value.trim() ? safeActionSuggestionChips : moodSuggestionChips;
        strip.innerHTML = '';
        source.forEach(item => {
            const chip = document.createElement('button');
            chip.type = 'button';
            chip.className = 'suggestion-chip';
            chip.innerText = item.label;
            chip.onmousedown = event => event.preventDefault();
            chip.onclick = () => insertSuggestionText(getSuggestionText(item));
            strip.appendChild(chip);
        });
        strip.classList.add('visible');
    }

    function initInputSuggestions() {
        const input = document.getElementById('user-input');
        const strip = document.getElementById('input-suggestion-strip');
        if (!input || !strip) return;
        input.addEventListener('focus', updateInputSuggestions);
        input.addEventListener('input', updateInputSuggestions);
        input.addEventListener('blur', () => {
            setTimeout(() => {
                if (!strip.matches(':hover') && document.activeElement !== input) {
                    strip.classList.remove('visible');
                }
            }, 120);
        });
    }

    function renderCheckinPeek() {
        const timeEl = document.getElementById('checkin-peek-time');
        const statusEl = document.getElementById('checkin-peek-status');
        const dotsEl = document.getElementById('checkin-peek-dots');
        if (!checkinState || !timeEl || !statusEl || !dotsEl) return;

        timeEl.innerText = checkinState.current_time || '';
        const todayRecord = checkinState.records?.[checkinState.today] || checkinState.selected_record || {};
        statusEl.innerText = `Today: ${todayRecord.done || 0}/${todayRecord.total || 0}`;

        dotsEl.innerHTML = '';
        const monthDays = checkinState.month_days || [];
        const heatStatuses = [];
        monthDays.forEach(day => {
            const dot = document.createElement('span');
            const status = checkinDayStatusClass(day);
            heatStatuses.push(status);
            dot.className = `checkin-dot ${status}`;
            dot.title = `${day.date}: ${day.done}/${day.total}`;
            dotsEl.appendChild(dot);
        });
        renderContinuousHeatStrip(dotsEl, heatStatuses);
    }

    function renderCheckinLoadError() {
        const timeEl = document.getElementById('checkin-peek-time');
        const statusEl = document.getElementById('checkin-peek-status');
        const dotsEl = document.getElementById('checkin-peek-dots');
        if (timeEl) timeEl.innerText = 'Unavailable';
        if (statusEl) statusEl.innerText = 'Open Daily to retry';
        if (dotsEl) {
            dotsEl.innerHTML = '';
            dotsEl.style.setProperty('--heat-strip', '#050505');
        }
    }

    async function openCheckinModal() {
        document.getElementById('checkin-modal').style.display = 'flex';
        try {
            await loadCheckinState({renderModal: true});
        } catch (error) {
            console.warn('Unable to open Daily', error);
            renderCheckinLoadError();
        }
    }

    function closeCheckinModal() {
        document.getElementById('checkin-modal').style.display = 'none';
    }

    // --- unified Daily add-task dialog ---------------------------------
    function openCheckinAddTaskDialog(mode = 'fixed') {
        const overlay = document.getElementById('checkin-add-task-overlay');
        if (!overlay) return;
        overlay.hidden = false;
        setCheckinAddTaskMode(mode);
        populateCheckinGoalSelector('new-checkin-task-goal', '', true);
        populateCheckinGoalSelector('new-checkin-daily-task-goal', '', true);
    }

    function closeCheckinAddTaskDialog() {
        const overlay = document.getElementById('checkin-add-task-overlay');
        if (overlay) overlay.hidden = true;
    }

    function setCheckinAddTaskMode(mode) {
        const overlay = document.getElementById('checkin-add-task-overlay');
        if (!overlay) return;
        overlay.querySelectorAll('#checkin-add-task-mode .seg-btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.mode === mode);
        });
        overlay.querySelectorAll('[data-mode-body]').forEach(body => {
            body.hidden = body.dataset.modeBody !== mode;
        });
    }

    document.addEventListener('DOMContentLoaded', () => {
        document.getElementById('checkin-add-task-mode')?.addEventListener('click', event => {
            const btn = event.target.closest('.seg-btn[data-mode]');
            if (btn) setCheckinAddTaskMode(btn.dataset.mode);
        });
    });

    async function submitCheckinAddTaskDialog() {
        const overlay = document.getElementById('checkin-add-task-overlay');
        const mode = overlay?.querySelector('#checkin-add-task-mode .seg-btn.active')?.dataset.mode || 'fixed';
        if (mode === 'fixed') await addCheckinTask();
        else if (mode === 'today') await addCheckinDailyTask();
        else await addCheckinPresetTask();
    }

    function openCheckinExportMenu(anchor) {
        showMenuAt(anchor, [
            { label: 'Export month report', icon: 'download', onClick: () => exportCheckinReport('month') },
            { label: 'Export year report', icon: 'download', onClick: () => exportCheckinReport('year') }
        ], { align: 'end' });
    }
    window.openCheckinAddTaskDialog = openCheckinAddTaskDialog;
    window.closeCheckinAddTaskDialog = closeCheckinAddTaskDialog;
    window.submitCheckinAddTaskDialog = submitCheckinAddTaskDialog;
    window.openCheckinExportMenu = openCheckinExportMenu;

    function updateCheckinProgressBadge() {
        const badge = document.getElementById('checkin-progress-badge');
        const record = checkinState?.selected_record || {};
        const done = Number(record.done || 0);
        const total = Number(record.total || 0);
        if (badge) {
            badge.textContent = total ? `${done}/${total} done` : 'No tasks';
            badge.className = `st-badge${total && done >= total ? ' success' : ''}`;
        }
    }

    async function changeCheckinMonth(delta) {
        const date = new Date(checkinViewYear, checkinViewMonth - 1 + delta, 1);
        if (!isCheckinMonthAllowed(date.getFullYear(), date.getMonth() + 1)) {
            showToast(`Check-in browsing is limited to ${checkinWindowText()}.`, { type: 'error' });
            return;
        }
        await saveSelectedCheckinDay({silent: true, skipReload: true});
        checkinViewYear = date.getFullYear();
        checkinViewMonth = date.getMonth() + 1;
        await loadCheckinState({renderModal: true});
    }

    function renderCheckinModal() {
        if (!checkinState) return;
        const currentTime = document.getElementById('checkin-current-time');
        const monthLabel = document.getElementById('checkin-month-label');
        const monthSummary = document.getElementById('checkin-month-summary');
        const yearSummary = document.getElementById('checkin-year-summary');

        if (currentTime) currentTime.innerText = `Now: ${checkinState.current_time}`;
        if (monthLabel) monthLabel.innerText = `${checkinState.year}-${String(checkinState.month).padStart(2, '0')}`;

        const prevBtn = document.getElementById('checkin-prev-month');
        const nextBtn = document.getElementById('checkin-next-month');
        if (prevBtn) {
            const prevDate = new Date(checkinViewYear, checkinViewMonth - 2, 1);
            prevBtn.disabled = !isCheckinMonthAllowed(prevDate.getFullYear(), prevDate.getMonth() + 1);
            prevBtn.title = checkinWindowText();
        }
        if (nextBtn) {
            const nextDate = new Date(checkinViewYear, checkinViewMonth, 1);
            nextBtn.disabled = !isCheckinMonthAllowed(nextDate.getFullYear(), nextDate.getMonth() + 1);
            nextBtn.title = checkinWindowText();
        }

        const m = checkinState.month_summary || {};
        const y = checkinState.year_summary || {};
        if (monthSummary) {
            monthSummary.innerHTML = `
                <div class="checkin-summary-title">This Month</div>
                <div>Completion rate: ${formatCheckinRate(m.completion_rate)}</div>
                <div>Days counted: ${m.days_counted || 0} · Complete: ${m.completed_days || 0} · Partial: ${m.partial_days || 0} · Missed: ${m.missed_days || 0}</div>
                <div>Current streak: ${m.current_streak || 0} · One-day tasks: ${m.one_off_done || 0}/${m.one_off_total || 0}</div>
            `;
        }
        if (yearSummary) {
            yearSummary.innerHTML = `
                <div class="checkin-summary-title">This Year</div>
                <div>Completion rate: ${formatCheckinRate(y.completion_rate)}</div>
                <div>Days counted: ${y.days_counted || 0} · Complete: ${y.completed_days || 0} · Partial: ${y.partial_days || 0} · Missed: ${y.missed_days || 0}</div>
                <div>Best streak: ${y.longest_streak || 0} · One-day tasks: ${y.one_off_done || 0}/${y.one_off_total || 0}</div>
            `;
        }

        populateCheckinGoalSelector('new-checkin-task-goal', '', true);
        populateCheckinGoalSelector('new-checkin-daily-task-goal', '', true);
        renderCheckinCalendar();
        renderSelectedCheckinDay();
    }

    function checkinGoalOptions(selectedId = '', activeOnly = false) {
        const goals = Array.isArray(checkinState?.goals) ? checkinState.goals : [];
        const visible = goals.filter(goal => !activeOnly || goal.status === 'active' || String(goal.id) === String(selectedId));
        return `<option value="">No related quest</option>` + visible.map(goal => {
            const suffix = goal.status && goal.status !== 'active' ? ` (${goal.status})` : '';
            return `<option value="${escapeHtml(goal.id)}" ${String(goal.id) === String(selectedId || '') ? 'selected' : ''}>${escapeHtml(goal.title || 'Untitled quest')}${escapeHtml(suffix)}</option>`;
        }).join('');
    }

    function populateCheckinGoalSelector(id, selectedId = '', activeOnly = false) {
        const select = document.getElementById(id);
        if (select) select.innerHTML = checkinGoalOptions(selectedId, activeOnly);
    }

    function renderCheckinCalendar() {
        const grid = document.getElementById('checkin-calendar-grid');
        if (!grid || !checkinState) return;
        grid.innerHTML = '';

        const days = checkinState.month_days || [];
        if (days.length === 0) return;

        const first = new Date(`${days[0].date}T00:00:00`);
        const mondayOffset = (first.getDay() + 6) % 7;
        for (let i = 0; i < mondayOffset; i++) {
            const empty = document.createElement('div');
            empty.className = 'checkin-day empty';
            grid.appendChild(empty);
        }

        days.forEach(day => {
            const btn = document.createElement('button');
            btn.className = `checkin-day ${checkinDayStatusClass(day)}`;
            if (day.date === checkinSelectedDate) btn.classList.add('selected');
            if (day.date === checkinState.today) btn.classList.add('today');
            btn.innerText = day.day;
            btn.title = `${day.date}: ${day.done}/${day.total}`;
            btn.onclick = () => selectCheckinDate(day.date);
            grid.appendChild(btn);
        });
    }

    async function selectCheckinDate(date) {
        if (!isCheckinDateAllowed(date)) {
            showToast(`Check-in editing is limited to ${checkinWindowText()}.`, { type: 'error' });
            return;
        }
        await saveSelectedCheckinDay({silent: true, skipReload: true});
        checkinSelectedDate = date;
        await loadCheckinState({renderModal: true});
    }

    function renderSelectedCheckinDay() {
        const title = document.getElementById('checkin-selected-title');
        const list = document.getElementById('checkin-task-list');
        const note = document.getElementById('checkin-note');
        if (!checkinState || !list || !note) return;

        const record = checkinState.selected_record || {};
        if (title) {
            title.innerText = `${checkinSelectedDate}${checkinSelectedDate === checkinState.today ? ' · Today' : ''} · ${record.done || 0}/${record.total || 0}`;
        }

        note.value = record.note || '';
        const mood = document.getElementById('checkin-mood');
        const energy = document.getElementById('checkin-energy');
        const difficulty = document.getElementById('checkin-difficulty');
        if (mood) mood.value = record.mood || '';
        if (energy) energy.value = record.energy || '';
        if (difficulty) difficulty.value = record.difficulty || '';
        [mood, energy, difficulty].forEach(el => { if (el) syncSegFor(el); });
        updateCheckinProgressBadge();
        const dayMeta = document.getElementById('checkin-day-meta');
        if (dayMeta) dayMeta.textContent = `Tasks and notes for ${checkinSelectedDate}`;
        list.innerHTML = '';

        const fixedTasks = checkinState.selected_tasks || checkinState.active_tasks || [];
        const dailyTasks = Array.isArray(record.daily_tasks) ? record.daily_tasks : [];

        if (fixedTasks.length === 0 && dailyTasks.length === 0) {
            list.innerHTML = `<div class="st-empty">${ic('check-circle')}<span>No tasks for this day yet.</span><button type="button" class="workspace-secondary-action" onclick="openCheckinAddTaskDialog()">${ic('plus', 'icn-sm')}Add task</button></div>`;
            return;
        }

        if (fixedTasks.length > 0) {
            const fixedTitle = document.createElement('div');
            fixedTitle.className = 'checkin-task-section-title';
            fixedTitle.innerText = 'Fixed daily tasks';
            list.appendChild(fixedTitle);
        }

        fixedTasks.forEach(task => {
            const row = document.createElement('div');
            const automatic = task.completion_mode === 'automatic';
            row.className = `checkin-task-row${automatic ? ' automatic' : ''}`;
            const safeId = escapeJsString(task.id);
            const checked = record.completions && record.completions[task.id] ? 'checked' : '';
            const automaticDetail = record.automatic_completions?.[task.id] || {};
            const completedDecks = Array.isArray(automaticDetail.completed_decks)
                ? automaticDetail.completed_decks.filter(Boolean)
                : [];
            const automaticText = automatic
                ? automaticDetail.completed
                    ? `Anki confirmed${completedDecks.length ? ` · ${completedDecks.join(' / ')}` : ''}`
                    : automaticDetail.source
                        ? `${Number(automaticDetail.today_reviews || 0)} reviews · no completed decks yet`
                        : `Waiting for today's Anki status`
                : '';
            row.innerHTML = `
                <input type="checkbox" data-task-id="${escapeHtml(task.id)}" ${checked} ${automatic ? 'disabled aria-label="Automatically verified by Anki"' : 'onchange="saveSelectedCheckinDay({silent:true})"'}>
                <span class="checkin-task-title">${escapeHtml(task.title)}${automatic ? `<small class="checkin-auto-status">${escapeHtml(automaticText)}</small>` : ''}</span>
                <span class="checkin-task-badge">${automatic ? 'Anki · auto' : escapeHtml(task.frequency || 'daily')}</span>
                <select class="checkin-goal-link" title="Related Life Quest" onchange="updateCheckinTaskGoal('${safeId}', this.value)">${checkinGoalOptions(task.goal_id || '')}</select>
                ${automatic ? '' : `<button type="button" onclick="editCheckinTask('${safeId}')" title="Edit task">${ic('edit', 'icn-sm')}</button>`}
                <button type="button" onclick="moveCheckinTask('${safeId}', -1)" title="Move up">${ic('arrow-up-sm', 'icn-sm')}</button>
                <button type="button" onclick="moveCheckinTask('${safeId}', 1)" title="Move down">${ic('arrow-down', 'icn-sm')}</button>
                <button type="button" onclick="deleteCheckinTask('${safeId}')" title="Delete task">${ic('trash', 'icn-sm')}</button>
            `;
            list.appendChild(row);
        });

        if (dailyTasks.length > 0) {
            const dailyTitle = document.createElement('div');
            dailyTitle.className = 'checkin-task-section-title';
            dailyTitle.innerText = 'Tasks only for this day';
            list.appendChild(dailyTitle);
        }

        dailyTasks.forEach(task => {
            appendCheckinDailyTaskRow(task);
        });
    }

    function collectCheckinCompletions() {
        const completions = {};
        document.querySelectorAll('#checkin-task-list input[type="checkbox"][data-task-id]').forEach(box => {
            completions[box.dataset.taskId] = box.checked;
        });
        return completions;
    }

    function appendCheckinDailyTaskRow(task) {
        const list = document.getElementById('checkin-task-list');
        if (!list) return;
        const row = document.createElement('div');
        row.className = 'checkin-task-row daily';
        const id = task.id || createCheckinLocalId();
        const safeId = escapeJsString(id);
        const title = String(task.title || '').trim();
        row.dataset.dailyRow = 'true';
        row.dataset.dailyId = id;
        row.dataset.dailyTitle = title;
        row.dataset.goalId = String(task.goal_id || '');
        row.innerHTML = `
            <input type="checkbox" data-daily-id="${escapeHtml(id)}" ${task.done ? 'checked' : ''} onchange="saveSelectedCheckinDay({silent:true})">
            <span class="checkin-task-title">${escapeHtml(title)}</span>
            <span class="checkin-task-badge">today</span>
            <select class="checkin-goal-link" title="Related Life Quest" onchange="updateCheckinDailyTaskGoal('${safeId}', this.value)">${checkinGoalOptions(task.goal_id || '')}</select>
            <button type="button" onclick="deleteCheckinDailyTask('${safeId}')" title="Delete today's task">${ic('trash', 'icn-sm')}</button>
        `;
        list.appendChild(row);
    }

    function collectCheckinDailyTasks() {
        return Array.from(document.querySelectorAll('#checkin-task-list [data-daily-row="true"]')).map(row => {
            const checkbox = row.querySelector('input[type="checkbox"][data-daily-id]');
            return {
                id: row.dataset.dailyId,
                title: row.dataset.dailyTitle || row.querySelector('span')?.innerText || '',
                done: Boolean(checkbox?.checked),
                goal_id: row.dataset.goalId || null
            };
        }).filter(task => task.title.trim());
    }

    async function saveSelectedCheckinDay(options = {}) {
        if (!checkinSelectedDate) return null;
        if (!isCheckinDateAllowed(checkinSelectedDate)) {
            if (!options.silent) showToast(`Check-in editing is limited to ${checkinWindowText()}.`, { type: 'error' });
            return null;
        }
        const list = document.getElementById('checkin-task-list');
        if (!list) return null;
        const response = await fetch('/api/checkins/day', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                date: checkinSelectedDate,
                completions: collectCheckinCompletions(),
                daily_tasks: collectCheckinDailyTasks(),
                note: document.getElementById('checkin-note')?.value || '',
                mood: document.getElementById('checkin-mood')?.value || null,
                energy: document.getElementById('checkin-energy')?.value || null,
                difficulty: document.getElementById('checkin-difficulty')?.value || null
            })
        });
        if (!response.ok) {
            const error = await response.json().catch(() => ({}));
            throw new Error(error.detail || `Save failed: ${response.status}`);
        }
        const data = await response.json();
        if (!options.skipReload) await loadCheckinState({renderModal: true});
        if (!options.silent) showToast('Check-in saved.');
        return data.record;
    }

    async function addCheckinTask() {
        const input = document.getElementById('new-checkin-task-input');
        const title = input?.value.trim();
        if (!title) return;
        const response = await fetch('/api/checkins/tasks/add', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                title,
                goal_id: document.getElementById('new-checkin-task-goal')?.value || null
            })
        });
        const data = await response.json();
        const frequency = document.getElementById('new-checkin-task-frequency')?.value || 'daily';
        if (data.task?.id) {
            await fetch('/api/checkins/tasks/update', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({id: data.task.id, updates: {frequency}})
            });
        }
        input.value = '';
        await loadCheckinState({renderModal: true});
    }

    async function addCheckinPresetTask() {
        const select = document.getElementById('new-checkin-task-preset');
        const preset = String(select?.value || '').trim();
        if (!preset) return;
        const response = await fetch('/api/checkins/tasks/add', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({preset})
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            showToast(data.detail || 'Unable to add the preset task.', { type: 'error' });
            return;
        }
        if (select) select.value = '';
        await loadCheckinState({renderModal: true});
    }

    async function updateCheckinTaskGoal(id, goalId) {
        await fetch('/api/checkins/tasks/update', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({id, updates: {goal_id: goalId || null}})
        });
        await loadCheckinState({renderModal: true});
    }

    async function editCheckinTask(id) {
        const task = (checkinState?.tasks || []).find(t => t.id === id);
        if (!task) return;
        const currentFrequency = String(task.frequency || 'daily');
        buildStDialog({
            title: 'Edit fixed task',
            bodyHtml: `
                <label class="form-label" style="display:block; margin-bottom:6px;" for="edit-checkin-title">Task title</label>
                <input type="text" class="st-prompt-field" id="edit-checkin-title" value="${escapeHtml(task.title || '')}">
                <label class="form-label" style="display:block; margin:12px 0 6px;" for="edit-checkin-frequency">Frequency</label>
                <select id="edit-checkin-frequency" style="width:100%; min-height:40px; padding:8px 11px; border-radius:6px; background:var(--surface-input); color:var(--text-primary); border:1px solid var(--border-default);">
                    ${['daily', 'weekdays', 'weekends', 'weekly', 'monthly'].map(freq => `<option value="${freq}" ${freq === currentFrequency ? 'selected' : ''}>${freq.charAt(0).toUpperCase() + freq.slice(1)}</option>`).join('')}
                </select>`,
            confirmLabel: 'Save',
            onConfirm: async close => {
                const title = document.getElementById('edit-checkin-title')?.value.trim();
                const frequency = document.getElementById('edit-checkin-frequency')?.value || currentFrequency;
                if (!title) return;
                close();
                await fetch('/api/checkins/tasks/update', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({id, updates: {title, frequency: String(frequency).trim().toLowerCase()}})
                });
                await loadCheckinState({renderModal: true});
            }
        });
    }

    async function moveCheckinTask(id, delta) {
        const tasks = [...(checkinState?.tasks || [])];
        const index = tasks.findIndex(t => t.id === id);
        const next = index + delta;
        if (index < 0 || next < 0 || next >= tasks.length) return;
        [tasks[index], tasks[next]] = [tasks[next], tasks[index]];
        await fetch('/api/checkins/tasks/reorder', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ordered_ids: tasks.map(t => t.id)})
        });
        await loadCheckinState({renderModal: true});
    }

    async function addCheckinDailyTask() {
        const input = document.getElementById('new-checkin-daily-task-input');
        const title = input?.value.trim();
        if (!title) return;

        const list = document.getElementById('checkin-task-list');
        if (list && list.innerText.includes('No tasks for this day yet')) {
            list.innerHTML = '';
            const dailyTitle = document.createElement('div');
            dailyTitle.className = 'checkin-task-section-title';
            dailyTitle.innerText = 'Tasks only for this day';
            list.appendChild(dailyTitle);
        } else if (list && !list.querySelector('.checkin-task-row.daily')) {
            const dailyTitle = document.createElement('div');
            dailyTitle.className = 'checkin-task-section-title';
            dailyTitle.innerText = 'Tasks only for this day';
            list.appendChild(dailyTitle);
        }

        appendCheckinDailyTaskRow({
            id: createCheckinLocalId(),
            title,
            done: false,
            goal_id: document.getElementById('new-checkin-daily-task-goal')?.value || null
        });
        input.value = '';
        await saveSelectedCheckinDay({silent: true});
    }

    async function updateCheckinDailyTaskGoal(id, goalId) {
        const row = Array.from(document.querySelectorAll('#checkin-task-list [data-daily-row="true"]'))
            .find(item => item.dataset.dailyId === id);
        if (!row) return;
        row.dataset.goalId = goalId || '';
        await saveSelectedCheckinDay({silent: true});
    }

    async function deleteCheckinDailyTask(id) {
        const row = Array.from(document.querySelectorAll('#checkin-task-list [data-daily-row="true"]'))
            .find(item => item.dataset.dailyId === id);
        if (row) row.remove();
        await saveSelectedCheckinDay({silent: true});
    }

    async function deleteCheckinTask(id) {
        showConfirmDialog({
            title: 'Delete this fixed task?',
            message: 'Past records stay in the data file; the task simply stops appearing.',
            impact: ['Existing history and statistics are kept'],
            confirmLabel: 'Delete task',
            danger: true,
            onConfirm: close => { close(); doDeleteCheckinTask(id); }
        });
    }

    async function doDeleteCheckinTask(id) {
        await fetch('/api/checkins/tasks/delete', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({id})
        });
        await loadCheckinState({renderModal: true});
    }

    async function talkWithSayaAboutCheckin() {
        await saveSelectedCheckinDay({silent: true});
        const sessionResponse = await fetch('/api/checkins/chat_session', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({date: checkinSelectedDate})
        });
        const sessionData = await sessionResponse.json();
        const response = await fetch('/api/checkins/context', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({date: checkinSelectedDate})
        });
        const data = await response.json();
        pendingCheckinContext = data.context || '';
        closeCheckinModal();
        if (sessionData.session_id) {
            await loadSession(sessionData.session_id);
        }
        const input = document.getElementById('user-input');
        const defaultMessage = document.getElementById('checkin-talk-message')?.value.trim() ||
            "Saya, I want to talk with you about today's check-in progress.";
        input.value = defaultMessage;
        input.focus();
    }

    async function exportCheckinReport(scope) {
        await saveSelectedCheckinDay({silent: true});
        const response = await fetch('/api/checkins/export', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                scope,
                year: checkinViewYear,
                month: checkinViewMonth
            })
        });
        const data = await response.json();
        if (!response.ok) {
            showToast(data.detail || 'The report could not be exported.', { type: 'error' });
            return;
        }
        const scopeLabel = scope === 'year' ? 'year' : 'month';
        showConfirmDialog({
            title: `${scopeLabel.charAt(0).toUpperCase() + scopeLabel.slice(1)} report exported`,
            message: `The ${scopeLabel} report and a safety JSON were written to the reports folder.`,
            impact: [
                `Markdown: ${data.markdown_path || 'reports folder'}`,
                `Backup JSON: ${data.json_path || 'reports folder'}`
            ],
            confirmLabel: 'Done',
            cancelLabel: 'Close'
        });
    }

    // --- QUEST LOGIC ---
    function getQuestStatusDate(goal) {
        if (goal.status === 'active') return String(goal.created_at || '');
        if (goal.status === 'completed') return String(goal.achieved_at || goal.completed_at || '');
        if (goal.status === 'failed') return String(goal.failed_at || goal.completed_at || '');
        return '';
    }

    function getQuestDateLabel(status) {
        if (status === 'active') return 'Started';
        if (status === 'completed') return 'Achieved';
        return 'Failed';
    }

    function formatQuestDate(value) {
        return String(value || '').split(' ')[0];
    }

    function isValidQuestDate(value) {
        const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value || '').trim());
        if (!match) return false;
        const date = new Date(`${match[1]}-${match[2]}-${match[3]}T00:00:00`);
        return date.getFullYear() === Number(match[1]) &&
            date.getMonth() + 1 === Number(match[2]) &&
            date.getDate() === Number(match[3]);
    }

    function openQuestModal() {
        const settingsMenu = document.getElementById('settings-menu');
        if (settingsMenu) settingsMenu.style.display = 'none';
        const memoryModal = document.getElementById('memory-modal');
        if (memoryModal) memoryModal.style.display = 'none';
        document.getElementById('quest-modal').style.display = 'flex';
        syncQuestTabState();
        loadQuests();
    }
    function closeQuestModal() {
        document.getElementById('quest-modal').style.display = 'none';
    }
    function switchQuestTab(tab) {
        currentQuestTab = ['active', 'completed', 'failed'].includes(tab) ? tab : 'active';
        syncQuestTabState();
        loadQuests();
    }

    function syncQuestTabState() {
        ['active', 'completed', 'failed'].forEach(status => {
            const tab = document.getElementById(`tab-${status}`);
            const selected = currentQuestTab === status;
            tab?.classList.toggle('active', selected);
            tab?.setAttribute('aria-selected', selected ? 'true' : 'false');
        });
        const inputArea = document.getElementById('quest-input-area');
        if (inputArea) inputArea.style.display = currentQuestTab === 'active' ? 'flex' : 'none';
    }

    function renderQuestDailyProgress(progress) {
        const state = progress && typeof progress === 'object' ? progress : {};
        const events = Array.isArray(state.events) ? state.events : [];
        if (!events.length) {
            return '<div class="quest-daily-progress empty"><span>No Daily task linked yet</span></div>';
        }
        const boxes = events.map(event => {
            const title = `${event.date || ''} · ${event.title || 'Daily task'} · ${event.done ? 'completed' : 'not completed'}`;
            return `<span class="quest-progress-box ${event.done ? 'done' : 'pending'}" title="${escapeHtml(title)}"></span>`;
        }).join('');
        const prefix = state.truncated ? 'Recent · ' : '';
        return `
            <div class="quest-daily-progress">
                <div class="quest-progress-summary"><span>Daily progress</span><strong>${Number(state.done || 0)}/${Number(state.total || 0)}</strong></div>
                <div class="quest-progress-boxes" aria-label="${escapeHtml(prefix)}${Number(state.done || 0)} of ${Number(state.total || 0)} linked Daily tasks completed">${boxes}</div>
            </div>
        `;
    }

    async function loadQuests() {
        try {
            const res = await fetch('/api/goals');
            const goals = await res.json();
            const list = document.getElementById('quest-list');
            list.innerHTML = '';

            const filtered = goals.filter(g => {
                if (currentQuestTab === 'active') return g.status === 'active';
                if (currentQuestTab === 'completed') return g.status === 'completed';
                if (currentQuestTab === 'failed') return g.status === 'failed';
                return false;
            });
            
            // Sort: Active by created (newest first), Completed/Failed by completed_at/updated (newest first)
            filtered.sort((a, b) => {
                const da = currentQuestTab === 'active' ? a.created_at : (a.completed_at || a.created_at);
                const db = currentQuestTab === 'active' ? b.created_at : (b.completed_at || b.created_at);
                return da < db ? 1 : -1;
            });

            if (filtered.length === 0) {
                list.innerHTML = '<div style="text-align:center; color:#666; padding:20px;">No quests found.</div>';
                return;
            }

            filtered.forEach(g => {
                const div = document.createElement('div');
                div.className = `quest-item ${g.status}`;
                const safeId = escapeJsString(g.id);
                const safeTitleJs = escapeJsString(g.title);
                const safeTitle = escapeHtml(g.title);
                const dateValue = formatQuestDate(getQuestStatusDate(g));
                const safeDateJs = escapeJsString(dateValue);
                const dateAction = `<button class="btn-quest-action btn-edit" onclick="editQuestDate('${safeId}', '${g.status}', '${safeDateJs}')" title="Edit ${getQuestDateLabel(g.status)} date">${ic('calendar', 'icn-sm')}</button>`;

                let actions = '';
                if (g.status === 'active') {
                    actions = dateAction + `
                        <button class="btn-quest-action btn-link-daily" onclick="createQuestDailyTask('${safeId}', '${safeTitleJs}')" title="Add a Daily task linked to this quest">${ic('plus', 'icn-sm')}Daily</button>
                        <button class="btn-quest-action btn-complete" onclick="completeQuest('${safeId}', '${safeTitleJs}')" title="Mark Complete">${ic('check-circle', 'icn-sm')}</button>
                        <button class="btn-quest-action btn-fail" onclick="failQuest('${safeId}', '${safeTitleJs}')" title="Mark Failed">${ic('alert', 'icn-sm')}</button>
                        <button class="btn-quest-action btn-edit" onclick="editQuest('${safeId}', '${safeTitleJs}')" title="Edit">${ic('edit', 'icn-sm')}</button>
                        <button class="btn-quest-action btn-delete" onclick="deleteQuest('${safeId}')" title="Delete">${ic('trash', 'icn-sm')}</button>
                    `;
                } else if (g.status === 'completed') {
                    actions = dateAction + `
                        <button class="btn-quest-action btn-share" onclick="shareQuest('${safeTitleJs}')" title="Share with Saya">${ic('chat', 'icn-sm')}</button>
                        <button class="btn-quest-action btn-edit" onclick="editQuest('${safeId}', '${safeTitleJs}')" title="Edit">${ic('edit', 'icn-sm')}</button>
                        <button class="btn-quest-action btn-delete" onclick="deleteQuest('${safeId}')" title="Delete">${ic('trash', 'icn-sm')}</button>
                    `;
                } else if (g.status === 'failed') {
                    actions = dateAction + `
                        <button class="btn-quest-action btn-edit" onclick="editQuest('${safeId}', '${safeTitleJs}')" title="Edit">${ic('edit', 'icn-sm')}</button>
                        <button class="btn-quest-action btn-delete" onclick="deleteQuest('${safeId}')" title="Delete">${ic('trash', 'icn-sm')}</button>
                    `;
                }

                const dateDisplay = `${getQuestDateLabel(g.status)}: ${dateValue || 'Not set'}`;

                let failureReason = '';
                if (g.status === 'failed' && g.description) {
                    failureReason = `<div style="font-size:0.75rem; color:#e74c3c; margin-top:2px;">Reason: ${escapeHtml(g.description)}</div>`;
                }
                const dailyProgress = renderQuestDailyProgress(g.daily_progress);

                div.innerHTML = `
                    <div class="quest-item-copy">
                        <div class="quest-title">${safeTitle}</div>
                        <div class="quest-date">${escapeHtml(dateDisplay)}</div>
                        ${failureReason}
                        ${dailyProgress}
                    </div>
                    <div class="quest-item-actions">${actions}</div>
                `;
                list.appendChild(div);
            });
        } catch(e) { console.error(e); }
    }

    async function createQuestDailyTask(goalId, goalTitle) {
        buildStDialog({
            title: `Add a Daily task for “${goalTitle}”`,
            bodyHtml: `
                <label class="form-label" style="display:block; margin-bottom:6px;" for="quest-daily-title">Task title</label>
                <input type="text" class="st-prompt-field" id="quest-daily-title" placeholder="Describe the recurring task">
                <label class="form-label" style="display:block; margin:12px 0 6px;" for="quest-daily-frequency">Frequency</label>
                <select id="quest-daily-frequency" style="width:100%; min-height:40px; padding:8px 11px; border-radius:6px; background:var(--surface-input); color:var(--text-primary); border:1px solid var(--border-default);">
                    <option value="daily">Daily</option>
                    <option value="weekdays">Weekdays</option>
                    <option value="weekends">Weekends</option>
                    <option value="weekly">Weekly</option>
                    <option value="monthly">Monthly</option>
                </select>`,
            confirmLabel: 'Add task',
            onConfirm: async close => {
                const taskTitle = document.getElementById('quest-daily-title')?.value.trim();
                if (!taskTitle) return;
                close();
                await doCreateQuestDailyTask(goalId, taskTitle, document.getElementById('quest-daily-frequency')?.value || 'daily');
            }
        });
    }

    async function doCreateQuestDailyTask(goalId, taskTitle, frequency) {
        const response = await fetch('/api/checkins/tasks/add', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({title: taskTitle.trim(), goal_id: goalId})
        });
        if (!response.ok) {
            showToast('Unable to create the linked Daily task.', { type: 'error', duration: 3600 });
            return;
        }
        const data = await response.json();
        if (data.task?.id && frequency !== 'daily') {
            await fetch('/api/checkins/tasks/update', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({id: data.task.id, updates: {frequency}})
            });
        }
        await loadQuests();
    }

    async function failQuest(id, title) {
        showPromptDialog({
            title: 'Mark quest as failed',
            label: 'Reason for failure or secondary achievement?',
            value: '',
            multiline: true,
            confirmLabel: 'Mark failed',
            onConfirm: (reason, close) => {
                close();
                doFailQuest(id, reason);
            }
        });
    }

    async function doFailQuest(id, reason) {
        {
            try {
                await fetch('/api/goals/update', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        id: id, 
                        updates: {
                            status: 'failed', 
                            description: reason
                        }
                    })
                });
                loadQuests();
            } catch(e) { showToast("Failed to update status", { type: 'error', duration: 3600 }); }
        }
    }

    async function editQuest(id, currentTitle) {
        showPromptDialog({
            title: 'Edit goal title',
            label: 'Goal title',
            value: currentTitle,
            confirmLabel: 'Save',
            onConfirm: (newTitle, close) => {
                close();
                if (newTitle === currentTitle) return;
                doEditQuest(id, newTitle);
            }
        });
    }

    async function doEditQuest(id, newTitle) {
        {
            try {
                await fetch('/api/goals/update', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({id: id, updates: {title: newTitle.trim()}})
                });
                loadQuests();
            } catch(e) { showToast("Failed to update", { type: 'error', duration: 3600 }); }
        }
    }

    async function editQuestDate(id, status, currentDate) {
        const label = getQuestDateLabel(status);
        showPromptDialog({
            title: `Set ${label.toLowerCase()} date`,
            label: 'Date (YYYY-MM-DD)',
            value: currentDate || '',
            placeholder: todayIsoDate(),
            confirmLabel: 'Save',
            onConfirm: (nextDate, close) => {
                const date = nextDate.trim();
                if (!isValidQuestDate(date)) {
                    showToast('Please enter a valid date in YYYY-MM-DD format.', { type: 'info', duration: 3600 });
                    return;
                }
                close();
                doEditQuestDate(id, status, date);
            }
        });
    }

    async function doEditQuestDate(id, status, date) {
        const dateField = status === 'active'
            ? 'created_at'
            : (status === 'completed' ? 'achieved_at' : 'failed_at');
        try {
            const response = await fetch('/api/goals/update', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({id, updates: {[dateField]: date}})
            });
            if (!response.ok) throw new Error(`Update failed: ${response.status}`);
            await loadQuests();
        } catch (e) {
            showToast('Failed to update quest date: ' + (e.message || e), { type: 'error', duration: 3600 });
        }
    }

    async function addQuest() {
        const input = document.getElementById('new-quest-input');
        const title = input.value.trim();
        if (!title) return;
        
        try {
            await fetch('/api/goals/add', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({title: title})
            });
            input.value = '';
            loadQuests();
        } catch(e) { showToast("Failed to add quest", { type: 'error', duration: 3600 }); }
    }

    async function completeQuest(id, title) {
        confirmAction("Mark this quest as completed?", async () => {

            try {
                await fetch('/api/goals/update', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({id: id, updates: {status: 'completed'}})
                });
                loadQuests();
            
                // Prompt to share
                confirmAction("Great job! Tell Saya about this achievement now?", () => {
                    shareQuest(title);
                });
            } catch(e) { showToast("Failed to update", { type: 'error', duration: 3600 }); }
    });    }

    async function deleteQuest(id) {
        confirmAction("Delete this quest?", async () => {

            try {
                await fetch('/api/goals/delete', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({id: id})
                });
                loadQuests();
            } catch(e) { showToast("Failed to delete", { type: 'error', duration: 3600 }); }
    });    }

    function shareQuest(title) {
        closeQuestModal();
        const input = document.getElementById('user-input');
        input.value = `I just completed my goal: "${title}"!`;
        input.focus();
        // Optional: Auto-send? Let's let user edit/send.
    }

    function closeModal() {
        stopMemoryGraphAnimation();
        disposeMemoryGraph3D();
        document.getElementById('memory-modal').style.display = 'none';
    }

    function stopMemoryGraphAnimation() {
        if (memoryGraphState?.animationFrame) {
            cancelAnimationFrame(memoryGraphState.animationFrame);
            memoryGraphState.animationFrame = null;
        }
    }

    function setMemoryModalHeader(title, backHandler = "") {
        const titleEl = document.getElementById('modal-title');
        const backEl = document.getElementById('memory-modal-back');
        if (titleEl) titleEl.innerText = title || "";
        if (!backEl) return;
        if (backHandler) {
            backEl.hidden = false;
            backEl.setAttribute('aria-hidden', 'false');
            backEl.setAttribute('onclick', backHandler);
        } else {
            backEl.hidden = true;
            backEl.setAttribute('aria-hidden', 'true');
            backEl.setAttribute('onclick', '');
        }
    }

    async function openMemoryViewer() {
        stopMemoryGraphAnimation();
        const settingsMenu = document.getElementById('settings-menu');
        if (settingsMenu) settingsMenu.style.display = 'none';
        const modal = document.getElementById('memory-modal');
        const body = document.getElementById('modal-body');
        setMemoryModalHeader("Memories", "openAboutYouModal()");
        body.innerHTML = `
            <section class="page-intro">
                <div class="page-intro-copy">
                    <h3 class="page-intro-title">Memories</h3>
                    <p class="page-intro-sub">Search, curate and inspect what Saya can recall. Archived originals stay preserved but outside normal prompts.</p>
                </div>
                <div class="page-intro-actions">
                    <span class="st-badge"><strong id="memory-hero-active">--</strong>&nbsp;active</span>
                    <span class="st-badge"><strong id="memory-hero-archived">--</strong>&nbsp;archived</span>
                    <span class="st-badge" id="memory-hero-store">--</span>
                </div>
            </section>
            <div class="memory-toolbar">
                <input id="memory-search-input" type="text" placeholder="Search memories..." oninput="loadMemoryList()" />
                <select id="memory-category-filter" onchange="memoryViewerCategory = this.value; loadMemoryList()" title="Category">
                    <option value="">All categories</option>
                    <option value="identity">Identity</option>
                    <option value="preference">Preference</option>
                    <option value="relationship">Relationship</option>
                    <option value="project">Project</option>
                    <option value="goal">Goal</option>
                    <option value="schedule">Schedule</option>
                    <option value="health">Health</option>
                    <option value="emotion">Emotion</option>
                    <option value="conversation_summary">Summary</option>
                    <option value="learning_map">Learning map</option>
                    <option value="learning_note">Learning note</option>
                    <option value="general">General</option>
                </select>
                <select id="memory-layer-filter" onchange="setMemoryLayerFilter(this.value)" hidden>
                    <option value="">All layers</option>
                    <option value="core">Core</option>
                    <option value="semantic">Semantic</option>
                    <option value="episodic">Episodic</option>
                    <option value="archive">Archive</option>
                </select>
                <button class="workspace-secondary-action" onclick="openMemoryGraphViewer()" title="Explore and manage relationships between memories">${ic('share-net', 'icn-sm')}Network map</button>
                <button class="row-menu-btn" onclick="showMemoryToolsMenu(this)" title="More actions" aria-label="More actions" aria-haspopup="menu">${ic('more', 'icn-sm')}</button>
                <button class="workspace-primary-action" onclick="toggleAddMemoryPanel()" title="Add a manual memory">${ic('plus', 'icn-sm')}Add memory</button>
            </div>
            <div class="ws-tabs" id="memory-layer-overview" role="tablist">
                <button type="button" class="ws-tab" data-memory-layer-tab="" role="tab">All<em class="memory-tab-count" data-count-layer="all"></em></button>
                <button type="button" class="ws-tab" data-memory-layer-tab="core" role="tab">Core<em class="memory-tab-count" data-count-layer="core"></em></button>
                <button type="button" class="ws-tab" data-memory-layer-tab="semantic" role="tab">Semantic<em class="memory-tab-count" data-count-layer="semantic"></em></button>
                <button type="button" class="ws-tab" data-memory-layer-tab="episodic" role="tab">Episodic<em class="memory-tab-count" data-count-layer="episodic"></em></button>
                <button type="button" class="ws-tab" data-memory-layer-tab="archive" role="tab">Archive<em class="memory-tab-count" data-count-layer="archive"></em></button>
            </div>
            <p class="form-help memory-layer-help">Prompt order is Core → Semantic → Episodic. Managed records are generated by their owning feature and cannot be edited or deleted here.</p>
            <div id="memory-list"></div>
            <section class="memory-v4-panel" aria-labelledby="memory-v4-panel-title">
                <div class="memory-v4-panel-heading">
                    <div><strong id="memory-v4-panel-title">V4 memory controls</strong><span>Audited facts, versions and retrieval policy</span></div>
                    <div><span id="memory-v4-status" class="st-badge">Checking…</span><button type="button" class="workspace-secondary-action" onclick="loadMemoryV4Panel()">Refresh</button></div>
                </div>
                <div id="memory-v4-list"><div class="st-empty">Loading V4 memory state…</div></div>
            </section>
        `;
        ensureMemoryAddDialog();
        const categoryFilter = document.getElementById('memory-category-filter');
        if (categoryFilter) categoryFilter.value = memoryViewerCategory;
        const layerFilter = document.getElementById('memory-layer-filter');
        if (layerFilter) layerFilter.value = memoryViewerLayer;
        syncMemoryLayerTabs(memoryViewerLayer);
        modal.style.display = 'flex';

        await Promise.all([loadMemoryLayerOverview(), loadMemoryList(), loadMemoryV4Panel()]);
    }

    async function loadMemoryV4Panel() {
        const list = document.getElementById('memory-v4-list');
        const badge = document.getElementById('memory-v4-status');
        if (!list) return;
        try {
            const statusResponse = await fetch('/api/kb/v4/status');
            const status = await statusResponse.json();
            if (badge) {
                badge.textContent = status.status === 'ready' ? (status.read_enabled ? 'Ready · read' : 'Ready · controls') : (status.status || 'Unavailable');
                badge.className = `st-badge ${status.status === 'ready' ? 'success' : 'running'}`;
            }
            if (!statusResponse.ok || status.status !== 'ready') {
                list.innerHTML = `<div class="st-empty">V4 is not enabled. Existing memories remain unchanged.</div>`;
                return;
            }
            const response = await fetch('/api/kb/v4/controls/summary?limit=100');
            const data = await response.json();
            if (!response.ok || !Array.isArray(data.memories)) throw new Error(data.reason || 'Unable to load V4 memories');
            renderMemoryV4Panel(data.memories);
        } catch (error) {
            if (badge) { badge.textContent = 'Unavailable'; badge.className = 'st-badge danger'; }
            list.innerHTML = `<div class="st-empty">V4 controls are unavailable right now. ${escapeHtml(error.message || '')}</div>`;
        }
    }

    function renderMemoryV4Panel(memories) {
        const list = document.getElementById('memory-v4-list');
        if (!list) return;
        if (!memories.length) {
            list.innerHTML = '<div class="st-empty">No V4 facts have been committed yet.</div>';
            return;
        }
        list.innerHTML = memories.map(memory => {
            const id = escapeJsString(memory.id);
            const meta = memory.metadata && typeof memory.metadata === 'object' ? memory.metadata : {};
            const noMention = Boolean(meta.do_not_mention);
            const flags = [memory.pinned ? 'pinned' : '', memory.locked ? 'locked' : '', noMention ? 'do not mention' : ''].filter(Boolean).join(' · ');
            return `<article class="memory-v4-card" data-memory-v4-id="${escapeHtml(memory.id)}">
                <div class="memory-v4-card-copy"><strong>${escapeHtml(memory.canonical_text || 'Untitled fact')}</strong><small>${escapeHtml(memory.kind || 'fact')} · rev ${Number(memory.revision || 0)} · ${escapeHtml(memory.lifecycle || 'active')}${flags ? ` · ${escapeHtml(flags)}` : ''}</small></div>
                <div class="memory-v4-card-actions">
                    <button type="button" onclick="correctMemoryV4('${id}')">Correct</button>
                    <button type="button" onclick="toggleMemoryV4Pin('${id}', ${memory.pinned ? 'false' : 'true'})">${memory.pinned ? 'Unpin' : 'Pin'}</button>
                    <button type="button" onclick="toggleMemoryV4NoMention('${id}', ${noMention ? 'false' : 'true'})">${noMention ? 'Allow mention' : 'Do not mention'}</button>
                    <button type="button" class="danger" onclick="forgetMemoryV4('${id}')">Delete</button>
                </div>
            </article>`;
        }).join('');
    }

    async function applyMemoryV4Control(operation, memoryId, extra = {}) {
        const response = await fetch('/api/kb/v4/controls', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({operation, memory_id: memoryId, ...extra, actor: 'memory_ui'})
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || data.reason || 'V4 control failed');
        await loadMemoryV4Panel();
        return data;
    }

    function toggleMemoryV4Pin(memoryId, enabled) {
        applyMemoryV4Control(enabled ? 'pin' : 'unpin', memoryId).catch(error => showToast(error.message, {type: 'error'}));
    }

    function toggleMemoryV4NoMention(memoryId, enabled) {
        applyMemoryV4Control('do_not_mention', memoryId, {enabled}).catch(error => showToast(error.message, {type: 'error'}));
    }

    function correctMemoryV4(memoryId) {
        showPromptDialog({title: 'Correct V4 memory', label: 'New fact text', multiline: true, placeholder: 'Write the corrected fact', onConfirm: (value, close) => {
            applyMemoryV4Control('correct', memoryId, {updates: {canonical_text: value}}).then(() => { close(); showToast('V4 memory corrected.'); }).catch(error => showToast(error.message, {type: 'error'}));
        }});
    }

    function forgetMemoryV4(memoryId) {
        showConfirmDialog({title: 'Delete this V4 memory?', message: 'The active fact will be tombstoned and excluded from future V4 recall.', confirmLabel: 'Delete', danger: true, onConfirm: close => {
            applyMemoryV4Control('forget', memoryId).then(() => { close(); showToast('V4 memory deleted.'); }).catch(error => showToast(error.message, {type: 'error'}));
        }});
    }
    window.loadMemoryV4Panel = loadMemoryV4Panel;
    window.toggleMemoryV4Pin = toggleMemoryV4Pin;
    window.toggleMemoryV4NoMention = toggleMemoryV4NoMention;
    window.correctMemoryV4 = correctMemoryV4;
    window.forgetMemoryV4 = forgetMemoryV4;

    const MEMORY_GRAPH_DISPLAY_STORAGE_KEY = 'saya_memory_graph_display_scales_v1';

    function normalizeMemoryGraphDisplayScale(value, minimum, maximum, fallback = 1) {
        const number = Number(value);
        return Number.isFinite(number) ? Math.max(minimum, Math.min(maximum, number)) : fallback;
    }

    function normalizeMemoryGraphDisplayScales(value) {
        const normalizeMode = mode => {
            const source = value?.[mode] || {};
            return {
                text: normalizeMemoryGraphDisplayScale(source.text, 0, 2.4),
                line: normalizeMemoryGraphDisplayScale(source.line, 0.5, 3),
                node: normalizeMemoryGraphDisplayScale(source.node, 0.6, 2.4)
            };
        };
        return {'2d': normalizeMode('2d'), '3d': normalizeMode('3d')};
    }

    function loadMemoryGraphDisplayScales() {
        try {
            const saved = JSON.parse(localStorage.getItem(MEMORY_GRAPH_DISPLAY_STORAGE_KEY) || 'null');
            return normalizeMemoryGraphDisplayScales(saved);
        } catch (_) {
            return normalizeMemoryGraphDisplayScales(null);
        }
    }

    function saveMemoryGraphDisplayScales() {
        try {
            localStorage.setItem(MEMORY_GRAPH_DISPLAY_STORAGE_KEY, JSON.stringify(memoryGraphState.displayScales));
        } catch (_) {
            // Display settings remain usable for this page even when browser
            // storage is unavailable or disabled.
        }
    }

    const memoryGraphState = {
        data: null,
        viewMode: '2d',
        selectedNodeId: '',
        centerMemoryId: '',
        linkSourceId: '',
        linkRelation: 'related_to',
        animationFrame: null,
        loadTimer: null,
        displayScales: loadMemoryGraphDisplayScales(),
        transform: {x: 0, y: 0, scale: 1},
        positions: new Map(),
        positions3d: new Map(),
        three: null
    };

    function memoryGraphRelationLabel(relation) {
        const labels = {
            belongs_to_project: 'belongs to project', from_session: 'from session',
            occurred_on: 'occurred on', tagged_with: 'tagged with', mentions: 'mentions',
            conflicts_with: 'conflicts with', supersedes: 'supersedes', related_to: 'related to',
            supports: 'supports', caused_by: 'caused by', part_of: 'part of', follows: 'follows'
        };
        return labels[relation] || String(relation || '').replaceAll('_', ' ');
    }

    async function openMemoryGraphViewer(options = {}) {
        const modal = document.getElementById('memory-modal');
        const body = document.getElementById('modal-body');
        if (!modal || !body) return;
        setMemoryModalHeader('Memory network', 'openMemoryViewer()');
        memoryGraphState.centerMemoryId = options.centerMemoryId || '';
        memoryGraphState.selectedNodeId = '';
        memoryGraphState.linkSourceId = '';
        memoryGraphState.transform = {x: 0, y: 0, scale: 1};
        const displaySettings = getMemoryGraphDisplaySettings();
        body.innerHTML = `
            <section class="page-intro">
                <div class="page-intro-copy">
                    <h3 class="page-intro-title">Memory network</h3>
                    <p class="page-intro-sub">Inspect why memories are connected, focus on one neighborhood, and correct user-defined relationships.</p>
                </div>
                <div class="workspace-mini-metrics">
                    <span><strong id="memory-graph-memory-count">--</strong>memories</span>
                    <span><strong id="memory-graph-edge-count">--</strong>links</span>
                    <span><strong id="memory-graph-orphan-count">--</strong>isolated</span>
                </div>
            </section>
            <div class="memory-graph-toolbar">
                <input id="memory-graph-search" type="search" placeholder="Find memory, project or topic" oninput="scheduleMemoryGraphLoad()" onkeydown="if(event.key==='Enter'){event.preventDefault(); loadMemoryGraphView();}" />
                <select id="memory-graph-layer" onchange="loadMemoryGraphView({clearCenter:true})" aria-label="Memory layer">
                    <option value="">All layers</option>
                    <option value="core">Core</option>
                    <option value="semantic">Semantic</option>
                    <option value="episodic">Episodic</option>
                    <option value="archive">Archive</option>
                </select>
                <select id="memory-graph-relation" onchange="loadMemoryGraphView({clearCenter:true})" aria-label="Relationship type">
                    <option value="">All relationships</option>
                    <option value="belongs_to_project">Project</option>
                    <option value="from_session">Session</option>
                    <option value="tagged_with">Topic / tag</option>
                    <option value="mentions">Entity mention</option>
                    <option value="occurred_on">Date</option>
                    <option value="conflicts_with">Conflict</option>
                    <option value="supersedes">Supersedes</option>
                    <option value="related_to">User: related to</option>
                    <option value="supports">User: supports</option>
                    <option value="caused_by">User: caused by</option>
                    <option value="part_of">User: part of</option>
                    <option value="follows">User: follows</option>
                </select>
                <select id="memory-graph-limit" onchange="loadMemoryGraphView({clearCenter:true})" aria-label="Maximum memories">
                    <option value="40">40 memories</option>
                    <option value="80" selected>80 memories</option>
                    <option value="150">150 memories</option>
                </select>
                <label class="memory-graph-archive-toggle"><input id="memory-graph-archived" type="checkbox" onchange="loadMemoryGraphView({clearCenter:true})"> Archive</label>
                <div class="seg memory-graph-view-toggle" role="group" aria-label="Network dimension">
                    <button id="memory-graph-view-2d" class="seg-btn active" type="button" aria-pressed="true" onclick="setMemoryGraphViewMode('2d')">2D</button>
                    <button id="memory-graph-view-3d" class="seg-btn" type="button" aria-pressed="false" onclick="setMemoryGraphViewMode('3d')">3D</button>
                </div>
                <button onclick="resetMemoryGraphOverview()">${ic('eye', 'icn-sm')}Overview</button>
                <button onclick="rebuildMemoryGraphView()">${ic('refresh', 'icn-sm')}Rebuild</button>
            </div>
            <div id="memory-graph-focus" class="memory-graph-focus" hidden></div>
            <div class="memory-graph-layout">
                <section class="memory-graph-stage-wrap" aria-label="Interactive memory relationship graph">
                    <div id="memory-graph-loading" class="memory-graph-loading">Building relationship view...</div>
                    <svg id="memory-graph-svg" role="img" aria-label="Memory relationship network" preserveAspectRatio="xMidYMid meet"></svg>
                    <div id="memory-graph-3d" class="memory-graph-3d" role="application" aria-label="3D memory relationship network. Drag to orbit, scroll to zoom, and select a node for details." tabindex="0" hidden></div>
                    <div id="memory-graph-3d-tooltip" class="memory-graph-3d-tooltip" hidden></div>
                    <div class="memory-graph-legend" aria-label="Node type legend">
                        <span data-type="memory">Memory</span><span data-type="project">Project</span><span data-type="session">Session</span><span data-type="entity">Entity</span><span data-type="tag">Topic</span><span data-type="date">Date</span>
                    </div>
                    <div class="memory-graph-viewport-actions">
                        <button onclick="resetMemoryGraphViewport()">${ic('refresh', 'icn-sm')}Reset view</button>
                    </div>
                </section>
                <aside id="memory-graph-detail" class="memory-graph-detail">
                    <details class="memory-graph-display-controls" aria-label="Network display settings">
                        <summary class="memory-graph-display-heading"><strong>Display settings</strong><span id="memory-graph-display-mode">${memoryGraphState.viewMode.toUpperCase()}</span></summary>
                        <label for="memory-graph-text-scale">
                            <span>Text size</span>
                            <output id="memory-graph-text-scale-value" for="memory-graph-text-scale">${formatMemoryGraphDisplayScale('text', displaySettings.text)}</output>
                            <input id="memory-graph-text-scale" type="range" min="0" max="2.4" step="0.1" value="${displaySettings.text}" oninput="setMemoryGraphDisplayScale('text', this.value)" aria-label="Network text size" />
                        </label>
                        <label for="memory-graph-line-scale">
                            <span>Line thickness</span>
                            <output id="memory-graph-line-scale-value" for="memory-graph-line-scale">${displaySettings.line.toFixed(1)}×</output>
                            <input id="memory-graph-line-scale" type="range" min="0.5" max="3" step="0.1" value="${displaySettings.line}" oninput="setMemoryGraphDisplayScale('line', this.value)" aria-label="Connection line thickness" />
                        </label>
                        <label for="memory-graph-node-scale">
                            <span>Node size</span>
                            <output id="memory-graph-node-scale-value" for="memory-graph-node-scale">${displaySettings.node.toFixed(1)}×</output>
                            <input id="memory-graph-node-scale" type="range" min="0.6" max="2.4" step="0.1" value="${displaySettings.node}" oninput="setMemoryGraphDisplayScale('node', this.value)" aria-label="Network node size" />
                        </label>
                    </details>
                    <div id="memory-graph-detail-content">
                        <div class="memory-graph-detail-empty"><strong>Select a node</strong><span>View its memory, links, source conversation, or create a confirmed relationship.</span></div>
                    </div>
                </aside>
            </div>
        `;
        setMemoryGraphViewMode(memoryGraphState.viewMode, {render: false});
        modal.style.display = 'flex';
        await loadMemoryGraphView();
    }

    function setMemoryGraphSurfaceHidden(element, hidden) {
        if (!element) return;
        // SVGElement.hidden is not consistently reflected as the HTML hidden
        // attribute. Set the attribute explicitly so the 2D graph cannot stay
        // in the layout and push the 3D canvas below the visible stage.
        if (hidden) element.setAttribute('hidden', '');
        else element.removeAttribute('hidden');
    }

    function getMemoryGraphDisplaySettings(mode = memoryGraphState.viewMode) {
        return memoryGraphState.displayScales[mode === '3d' ? '3d' : '2d'];
    }

    function formatMemoryGraphDisplayScale(kind, value) {
        return kind === 'text' && value === 0 ? 'Off' : `${value.toFixed(1)}×`;
    }

    function syncMemoryGraphDisplayControls() {
        const settings = getMemoryGraphDisplaySettings();
        const controls = [
            ['text', settings.text],
            ['line', settings.line],
            ['node', settings.node]
        ];
        controls.forEach(([kind, value]) => {
            const input = document.getElementById(`memory-graph-${kind}-scale`);
            const output = document.getElementById(`memory-graph-${kind}-scale-value`);
            if (input) input.value = String(value);
            if (output) output.textContent = formatMemoryGraphDisplayScale(kind, value);
        });
        const modeLabel = document.getElementById('memory-graph-display-mode');
        if (modeLabel) modeLabel.textContent = `${memoryGraphState.viewMode.toUpperCase()} settings`;
    }

    function applyMemoryGraphDisplaySettings() {
        const settings2d = getMemoryGraphDisplaySettings('2d');
        const settings3d = getMemoryGraphDisplaySettings('3d');
        document.querySelectorAll('#memory-graph-svg .memory-graph-edge').forEach(line => {
            const baseWidth = Number(line.dataset.baseWidth || (line.classList.contains('manual') ? 1.8 : 1.1));
            line.style.strokeWidth = (baseWidth * settings2d.line).toFixed(2);
        });
        document.querySelectorAll('#memory-graph-svg .memory-graph-node circle').forEach(circle => {
            const baseRadius = Number(circle.dataset.baseRadius || 8);
            circle.setAttribute('r', (baseRadius * settings2d.node).toFixed(2));
        });
        document.querySelectorAll('#memory-graph-svg .memory-graph-node text').forEach(label => {
            const baseOffset = Number(label.dataset.baseOffset || 11);
            label.setAttribute('x', (baseOffset * settings2d.node).toFixed(2));
            label.style.display = settings2d.text === 0 ? 'none' : '';
            label.style.fontSize = `${(10 * settings2d.text).toFixed(2)}px`;
        });
        memoryGraphState.three?.edgeMeshes?.forEach(mesh => {
            const radius = Number(mesh.userData.baseRadius || 0.34) * settings3d.line;
            mesh.scale.x = radius;
            mesh.scale.z = radius;
        });
        memoryGraphState.three?.labels?.forEach(label => {
            const node = label.userData.graphNode;
            if (node) label.position.y = node.y + Number(label.userData.baseOffset || 8) * settings3d.node;
            label.visible = settings3d.text > 0;
            label.scale.set(52 * settings3d.text, 9.75 * settings3d.text, 1);
        });
        updateMemoryGraphNodeSelection();
    }

    function setMemoryGraphDisplayScale(kind, rawValue) {
        const value = Number(rawValue);
        if (!Number.isFinite(value)) return;
        const settings = getMemoryGraphDisplaySettings();
        if (kind === 'text') settings.text = Math.max(0, Math.min(2.4, value));
        else if (kind === 'line') settings.line = Math.max(0.5, Math.min(3, value));
        else if (kind === 'node') settings.node = Math.max(0.6, Math.min(2.4, value));
        else return;
        saveMemoryGraphDisplayScales();
        syncMemoryGraphDisplayControls();
        applyMemoryGraphDisplaySettings();
    }

    function setMemoryGraphViewMode(mode, options = {}) {
        const nextMode = mode === '3d' ? '3d' : '2d';
        memoryGraphState.viewMode = nextMode;
        const svg = document.getElementById('memory-graph-svg');
        const host = document.getElementById('memory-graph-3d');
        setMemoryGraphSurfaceHidden(svg, nextMode !== '2d');
        setMemoryGraphSurfaceHidden(host, nextMode !== '3d');
        ['2d', '3d'].forEach(value => {
            const button = document.getElementById(`memory-graph-view-${value}`);
            if (!button) return;
            const active = value === nextMode;
            button.classList.toggle('active', active);
            button.setAttribute('aria-pressed', String(active));
        });
        syncMemoryGraphDisplayControls();
        if (nextMode === '2d') disposeMemoryGraph3D();
        else stopMemoryGraphAnimation();
        if (options.render !== false && memoryGraphState.data) renderMemoryGraph(memoryGraphState.data);
    }

    function scheduleMemoryGraphLoad() {
        clearTimeout(memoryGraphState.loadTimer);
        memoryGraphState.loadTimer = setTimeout(() => loadMemoryGraphView({clearCenter: true}), 280);
    }

    async function loadMemoryGraphView(options = {}) {
        const svg = document.getElementById('memory-graph-svg');
        if (!svg) return;
        if (options.clearCenter) memoryGraphState.centerMemoryId = '';
        if (memoryGraphState.animationFrame) cancelAnimationFrame(memoryGraphState.animationFrame);
        const loading = document.getElementById('memory-graph-loading');
        if (loading) {
            loading.hidden = false;
            loading.textContent = 'Building relationship view...';
        }
        const params = new URLSearchParams({
            search: document.getElementById('memory-graph-search')?.value.trim() || '',
            memory_layer: document.getElementById('memory-graph-layer')?.value || '',
            relation: document.getElementById('memory-graph-relation')?.value || '',
            limit: document.getElementById('memory-graph-limit')?.value || '80',
            include_archived: document.getElementById('memory-graph-archived')?.checked ? 'true' : 'false',
            center_memory_id: memoryGraphState.centerMemoryId || ''
        });
        try {
            const response = await fetch(`/api/kb/graph/view?${params.toString()}`);
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || 'Unable to load relationship graph');
            memoryGraphState.data = data;
            const summary = data.summary || {};
            document.getElementById('memory-graph-memory-count').textContent = String(summary.memory_nodes || 0);
            document.getElementById('memory-graph-edge-count').textContent = String(summary.edges || 0);
            document.getElementById('memory-graph-orphan-count').textContent = String(summary.orphan_memories || 0);
            const focus = document.getElementById('memory-graph-focus');
            if (focus) {
                focus.hidden = !memoryGraphState.centerMemoryId;
                focus.textContent = memoryGraphState.centerMemoryId ? 'Focused neighborhood · choose Overview to return to the full selection.' : '';
            }
            renderMemoryGraph(data);
            if (loading) loading.hidden = true;
            if (memoryGraphState.selectedNodeId) {
                const selected = (data.nodes || []).find(node => node.id === memoryGraphState.selectedNodeId);
                if (selected) renderMemoryGraphDetail(selected);
                else memoryGraphState.selectedNodeId = '';
            }
        } catch (error) {
            if (loading) {
                loading.hidden = false;
                loading.innerHTML = `
                    <div class="st-page-error">
                        ${ic('alert')}
                        <strong>The memory network could not be loaded.</strong>
                        <span>Nothing was changed. Check that the backend is running, then try again.</span>
                        <button type="button" class="workspace-secondary-action" onclick="loadMemoryGraphView()">${ic('refresh', 'icn-sm')}Retry</button>
                    </div>`;
            }
            svg.innerHTML = '';
            console.warn('Memory graph load failed:', error);
        }
    }

    function memoryGraphHash(text) {
        let value = 2166136261;
        for (const char of String(text || '')) {
            value ^= char.charCodeAt(0);
            value = Math.imul(value, 16777619);
        }
        return value >>> 0;
    }

    function renderMemoryGraph(data) {
        const stageWrap = document.querySelector('.memory-graph-stage-wrap');
        const nodes = Array.isArray(data?.nodes) ? data.nodes : [];
        const memoryNodes = nodes.filter(node => !node.kind || node.kind === 'memory');
        if (!nodes.length || !memoryNodes.length) {
            if (stageWrap && !stageWrap.querySelector('.memory-graph-empty')) {
                const empty = document.createElement('div');
                empty.className = 'memory-graph-empty';
                empty.innerHTML = `
                    <div class="st-empty">
                        ${ic('share-net')}
                        <span>No connected memories match the current filters. Widen the layer or relationship filters, or add memories first.</span>
                    </div>`;
                stageWrap.appendChild(empty);
            }
        } else {
            stageWrap?.querySelector('.memory-graph-empty')?.remove();
        }
        if (memoryGraphState.viewMode === '3d') {
            renderMemoryGraph3D(data);
            return;
        }
        renderMemoryGraph2D(data);
    }

    function renderMemoryGraph2D(data) {
        disposeMemoryGraph3D();
        const svg = document.getElementById('memory-graph-svg');
        if (!svg) return;
        svg.innerHTML = '';
        const width = Math.max(620, svg.parentElement?.clientWidth || 760);
        const height = 560;
        svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
        const namespace = 'http://www.w3.org/2000/svg';
        const viewport = document.createElementNS(namespace, 'g');
        viewport.classList.add('memory-graph-viewport');
        svg.appendChild(viewport);

        const nodes = (data.nodes || []).map((raw, index) => {
            const previous = memoryGraphState.positions.get(raw.id);
            const hash = memoryGraphHash(raw.id);
            const angle = ((hash % 360) / 180) * Math.PI;
            const isConnector = raw.type !== 'memory';
            const radius = isConnector ? Math.min(width, height) * 0.22 : Math.min(width, height) * 0.39;
            return {
                ...raw,
                x: previous?.x ?? width / 2 + Math.cos(angle) * radius + ((index % 7) - 3) * 4,
                y: previous?.y ?? height / 2 + Math.sin(angle) * radius + ((index % 5) - 2) * 4,
                vx: 0,
                vy: 0,
                fixed: false
            };
        });
        const byId = new Map(nodes.map(node => [node.id, node]));
        const edges = (data.edges || []).map(raw => ({...raw, sourceNode: byId.get(raw.source), targetNode: byId.get(raw.target)}))
            .filter(edge => edge.sourceNode && edge.targetNode);
        if (!nodes.length) {
            const empty = document.createElementNS(namespace, 'text');
            empty.setAttribute('x', String(width / 2));
            empty.setAttribute('y', String(height / 2));
            empty.setAttribute('text-anchor', 'middle');
            empty.classList.add('memory-graph-empty-label');
            empty.textContent = 'No memories match these filters.';
            viewport.appendChild(empty);
            return;
        }

        const edgeLayer = document.createElementNS(namespace, 'g');
        edgeLayer.classList.add('memory-graph-edges');
        const nodeLayer = document.createElementNS(namespace, 'g');
        nodeLayer.classList.add('memory-graph-nodes');
        viewport.appendChild(edgeLayer);
        viewport.appendChild(nodeLayer);

        edges.forEach(edge => {
            const line = document.createElementNS(namespace, 'line');
            line.classList.add('memory-graph-edge');
            line.dataset.relation = edge.relation || '';
            if (edge.source_kind === 'manual') line.classList.add('manual');
            line.dataset.baseWidth = edge.source_kind === 'manual' ? '1.8' : '1.1';
            const title = document.createElementNS(namespace, 'title');
            title.textContent = `${memoryGraphRelationLabel(edge.relation)} · confidence ${Number(edge.confidence || 0).toFixed(2)}`;
            line.appendChild(title);
            edge.element = line;
            edgeLayer.appendChild(line);
        });

        const showMemoryLabels = nodes.filter(node => node.type === 'memory').length <= 35;
        nodes.forEach(node => {
            const group = document.createElementNS(namespace, 'g');
            group.classList.add('memory-graph-node', `type-${node.type || 'unknown'}`);
            group.dataset.nodeId = node.id;
            group.setAttribute('role', 'button');
            group.setAttribute('tabindex', '0');
            group.setAttribute('aria-label', `${node.type}: ${node.label || 'Untitled'}`);
            const circle = document.createElementNS(namespace, 'circle');
            const baseRadius = node.type === 'memory' ? (node.layer === 'core' ? 10 : 8) : (node.type === 'project' ? 12 : 7);
            circle.dataset.baseRadius = String(baseRadius);
            circle.setAttribute('r', String(baseRadius * getMemoryGraphDisplaySettings('2d').node));
            const title = document.createElementNS(namespace, 'title');
            title.textContent = `${node.label || 'Untitled'} · ${node.type || 'node'}`;
            circle.appendChild(title);
            group.appendChild(circle);
            const label = document.createElementNS(namespace, 'text');
            const baseLabelOffset = node.type === 'project' ? 15 : 11;
            label.dataset.baseOffset = String(baseLabelOffset);
            label.setAttribute('x', String(baseLabelOffset * getMemoryGraphDisplaySettings('2d').node));
            label.setAttribute('y', '4');
            label.textContent = String(node.label || '').slice(0, 32);
            if (node.type === 'memory' && !showMemoryLabels) label.classList.add('memory-label-collapsed');
            group.appendChild(label);
            group.addEventListener('click', event => {
                event.stopPropagation();
                handleMemoryGraphNodeSelection(node);
            });
            group.addEventListener('keydown', event => {
                if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    handleMemoryGraphNodeSelection(node);
                }
            });
            group.addEventListener('pointerdown', event => {
                event.stopPropagation();
                node.fixed = true;
                node.dragging = true;
                group.setPointerCapture?.(event.pointerId);
            });
            group.addEventListener('pointermove', event => {
                if (!node.dragging) return;
                const point = memoryGraphPointer(svg, event);
                node.x = (point.x - memoryGraphState.transform.x) / memoryGraphState.transform.scale;
                node.y = (point.y - memoryGraphState.transform.y) / memoryGraphState.transform.scale;
                group.setAttribute('transform', `translate(${node.x.toFixed(2)} ${node.y.toFixed(2)})`);
                edges.forEach(edge => {
                    if (edge.sourceNode !== node && edge.targetNode !== node) return;
                    edge.element.setAttribute('x1', edge.sourceNode.x.toFixed(2));
                    edge.element.setAttribute('y1', edge.sourceNode.y.toFixed(2));
                    edge.element.setAttribute('x2', edge.targetNode.x.toFixed(2));
                    edge.element.setAttribute('y2', edge.targetNode.y.toFixed(2));
                });
            });
            group.addEventListener('pointerup', event => {
                node.dragging = false;
                node.fixed = false;
                group.releasePointerCapture?.(event.pointerId);
            });
            node.element = group;
            nodeLayer.appendChild(group);
        });

        installMemoryGraphViewportControls(svg, viewport);
        applyMemoryGraphTransform(viewport);
        let alpha = 1;
        const tick = () => {
            const repulsion = nodes.length > 120 ? 850 : 1350;
            for (let i = 0; i < nodes.length; i += 1) {
                for (let j = i + 1; j < nodes.length; j += 1) {
                    const left = nodes[i];
                    const right = nodes[j];
                    let dx = right.x - left.x;
                    let dy = right.y - left.y;
                    let distanceSquared = dx * dx + dy * dy;
                    if (distanceSquared < 4) {
                        dx = ((memoryGraphHash(left.id + right.id) % 11) - 5) * 0.2 || 0.2;
                        dy = ((memoryGraphHash(right.id + left.id) % 11) - 5) * 0.2 || -0.2;
                        distanceSquared = dx * dx + dy * dy;
                    }
                    const force = (repulsion * alpha) / Math.max(36, distanceSquared);
                    const distance = Math.sqrt(distanceSquared);
                    const fx = (dx / distance) * force;
                    const fy = (dy / distance) * force;
                    if (!left.fixed) { left.vx -= fx; left.vy -= fy; }
                    if (!right.fixed) { right.vx += fx; right.vy += fy; }
                }
            }
            edges.forEach(edge => {
                const dx = edge.targetNode.x - edge.sourceNode.x;
                const dy = edge.targetNode.y - edge.sourceNode.y;
                const distance = Math.max(1, Math.sqrt(dx * dx + dy * dy));
                const desired = edge.sourceNode.type === 'memory' && edge.targetNode.type === 'memory' ? 115 : 82;
                const spring = (distance - desired) * 0.006 * alpha;
                const fx = (dx / distance) * spring;
                const fy = (dy / distance) * spring;
                if (!edge.sourceNode.fixed) { edge.sourceNode.vx += fx; edge.sourceNode.vy += fy; }
                if (!edge.targetNode.fixed) { edge.targetNode.vx -= fx; edge.targetNode.vy -= fy; }
            });
            nodes.forEach(node => {
                if (!node.fixed) {
                    node.vx += (width / 2 - node.x) * 0.0009 * alpha;
                    node.vy += (height / 2 - node.y) * 0.0009 * alpha;
                    node.vx *= 0.84;
                    node.vy *= 0.84;
                    node.x = Math.max(22, Math.min(width - 22, node.x + node.vx));
                    node.y = Math.max(22, Math.min(height - 22, node.y + node.vy));
                }
                node.element.setAttribute('transform', `translate(${node.x.toFixed(2)} ${node.y.toFixed(2)})`);
                memoryGraphState.positions.set(node.id, {x: node.x, y: node.y});
            });
            edges.forEach(edge => {
                edge.element.setAttribute('x1', edge.sourceNode.x.toFixed(2));
                edge.element.setAttribute('y1', edge.sourceNode.y.toFixed(2));
                edge.element.setAttribute('x2', edge.targetNode.x.toFixed(2));
                edge.element.setAttribute('y2', edge.targetNode.y.toFixed(2));
            });
            alpha *= 0.968;
            if (alpha > 0.025 || nodes.some(node => node.dragging)) {
                memoryGraphState.animationFrame = requestAnimationFrame(tick);
            }
        };
        tick();
        applyMemoryGraphDisplaySettings();
    }

    function memoryGraph3DNodeColor(node) {
        const colors = {
            memory: 0x8fa6d9,
            project: 0xb69dff,
            session: 0x7dcdef,
            entity: 0xe9a2bf,
            tag: 0xd9ca7e,
            date: 0x86cba9
        };
        return colors[node?.type] || 0x8993a8;
    }

    function memoryGraph3DInitialPosition(node, index, count) {
        const previous = memoryGraphState.positions3d.get(node.id);
        if (previous) return {...previous};
        const hash = memoryGraphHash(node.id);
        const offset = ((hash % 997) / 997) * Math.PI * 2;
        const goldenAngle = Math.PI * (3 - Math.sqrt(5));
        const ratio = count <= 1 ? 0.5 : (index + 0.5) / count;
        const yUnit = 1 - ratio * 2;
        const ring = Math.sqrt(Math.max(0, 1 - yUnit * yUnit));
        const angle = index * goldenAngle + offset;
        const connector = node.type !== 'memory';
        const radius = connector ? 72 + (hash % 17) : 142 + (hash % 31);
        return {
            x: Math.cos(angle) * ring * radius,
            y: yUnit * radius,
            z: Math.sin(angle) * ring * radius,
            vx: 0,
            vy: 0,
            vz: 0
        };
    }

    function settleMemoryGraph3DLayout(nodes, edges) {
        const pairwise = nodes.length <= 170;
        const iterations = nodes.length > 260 ? 42 : 64;
        for (let iteration = 0; iteration < iterations; iteration += 1) {
            const alpha = 1 - iteration / iterations;
            if (pairwise) {
                for (let i = 0; i < nodes.length; i += 1) {
                    for (let j = i + 1; j < nodes.length; j += 1) {
                        const left = nodes[i];
                        const right = nodes[j];
                        let dx = right.x - left.x;
                        let dy = right.y - left.y;
                        let dz = right.z - left.z;
                        let distanceSquared = dx * dx + dy * dy + dz * dz;
                        if (distanceSquared < 9) {
                            dx = ((memoryGraphHash(left.id + right.id) % 17) - 8) * 0.12 || 0.12;
                            dy = ((memoryGraphHash(right.id + left.id) % 17) - 8) * 0.12 || -0.12;
                            dz = ((memoryGraphHash(left.id) % 13) - 6) * 0.12 || 0.08;
                            distanceSquared = dx * dx + dy * dy + dz * dz;
                        }
                        const distance = Math.sqrt(distanceSquared);
                        const force = (95 * alpha) / Math.max(64, distanceSquared);
                        const fx = dx / distance * force;
                        const fy = dy / distance * force;
                        const fz = dz / distance * force;
                        left.vx -= fx; left.vy -= fy; left.vz -= fz;
                        right.vx += fx; right.vy += fy; right.vz += fz;
                    }
                }
            }
            edges.forEach(edge => {
                const source = edge.sourceNode;
                const target = edge.targetNode;
                const dx = target.x - source.x;
                const dy = target.y - source.y;
                const dz = target.z - source.z;
                const distance = Math.max(1, Math.sqrt(dx * dx + dy * dy + dz * dz));
                const directMemoryLink = source.type === 'memory' && target.type === 'memory';
                const desired = directMemoryLink ? 78 : 54;
                const spring = (distance - desired) * 0.0045 * alpha;
                const fx = dx / distance * spring;
                const fy = dy / distance * spring;
                const fz = dz / distance * spring;
                source.vx += fx; source.vy += fy; source.vz += fz;
                target.vx -= fx; target.vy -= fy; target.vz -= fz;
            });
            nodes.forEach(node => {
                const gravity = node.type === 'memory' ? 0.00035 : 0.0011;
                node.vx -= node.x * gravity * alpha;
                node.vy -= node.y * gravity * alpha;
                node.vz -= node.z * gravity * alpha;
                node.vx *= 0.82; node.vy *= 0.82; node.vz *= 0.82;
                node.x += node.vx; node.y += node.vy; node.z += node.vz;
            });
        }
        nodes.forEach(node => {
            memoryGraphState.positions3d.set(node.id, {x: node.x, y: node.y, z: node.z});
        });
    }

    function createMemoryGraph3DLabel(node) {
        const canvas = document.createElement('canvas');
        canvas.width = 384;
        canvas.height = 72;
        const context = canvas.getContext('2d');
        if (!context) return null;
        context.font = '600 24px system-ui, sans-serif';
        context.textAlign = 'center';
        context.textBaseline = 'middle';
        context.fillStyle = 'rgba(8, 10, 14, 0.78)';
        if (typeof context.roundRect === 'function') {
            context.beginPath();
            context.roundRect(4, 8, 376, 56, 14);
            context.fill();
        } else {
            context.fillRect(4, 8, 376, 56);
        }
        context.fillStyle = '#eef2fb';
        const label = String(node.label || 'Untitled');
        context.fillText(label.length > 28 ? `${label.slice(0, 27)}…` : label, 192, 36);
        const texture = new THREE.CanvasTexture(canvas);
        texture.minFilter = THREE.LinearFilter;
        const material = new THREE.SpriteMaterial({map: texture, transparent: true, depthWrite: false});
        const sprite = new THREE.Sprite(material);
        sprite.scale.set(52, 9.75, 1);
        sprite.position.set(node.x, node.y + (node.type === 'project' ? 11 : 8), node.z);
        sprite.userData.memoryGraphLabel = true;
        sprite.userData.graphNode = node;
        sprite.userData.baseOffset = node.type === 'project' ? 11 : 8;
        return sprite;
    }

    function renderMemoryGraph3D(data) {
        const host = document.getElementById('memory-graph-3d');
        const svg = document.getElementById('memory-graph-svg');
        if (!host) return;
        setMemoryGraphSurfaceHidden(svg, true);
        setMemoryGraphSurfaceHidden(host, false);
        disposeMemoryGraph3D();
        setMemoryGraphSurfaceHidden(host, false);
        if (typeof THREE === 'undefined' || typeof THREE.WebGLRenderer !== 'function') {
            memoryGraphState.viewMode = '2d';
            setMemoryGraphViewMode('2d', {render: false});
            renderMemoryGraph2D(data);
            const loading = document.getElementById('memory-graph-loading');
            if (loading) {
                loading.hidden = false;
                loading.textContent = '3D rendering is unavailable, so the network was opened in 2D.';
            }
            return;
        }

        const width = Math.max(320, host.clientWidth || host.parentElement?.clientWidth || 760);
        const height = Math.max(360, host.clientHeight || 560);
        const renderer = new THREE.WebGLRenderer({antialias: true, alpha: true, powerPreference: 'high-performance'});
        renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
        renderer.setSize(width, height, false);
        renderer.setClearColor(0x0d0f13, 0);
        renderer.domElement.setAttribute('aria-hidden', 'true');
        host.replaceChildren(renderer.domElement);

        const scene = new THREE.Scene();
        scene.fog = new THREE.FogExp2(0x0d0f13, 0.0026);
        const camera = new THREE.PerspectiveCamera(48, width / height, 0.1, 1800);
        camera.position.set(0, 35, 390);
        scene.add(new THREE.AmbientLight(0xffffff, 0.68));
        const keyLight = new THREE.PointLight(0xb9c9ff, 1.15, 900);
        keyLight.position.set(140, 180, 240);
        scene.add(keyLight);
        const fillLight = new THREE.PointLight(0x9de4d2, 0.62, 700);
        fillLight.position.set(-190, -80, -120);
        scene.add(fillLight);

        const controls = typeof THREE.OrbitControls === 'function'
            ? new THREE.OrbitControls(camera, renderer.domElement)
            : null;
        if (controls) {
            controls.enableDamping = true;
            controls.dampingFactor = 0.075;
            controls.rotateSpeed = 0.58;
            controls.zoomSpeed = 0.78;
            controls.panSpeed = 0.55;
            controls.minDistance = 45;
            controls.maxDistance = 980;
        }

        const rawNodes = data.nodes || [];
        const nodes = rawNodes.map((raw, index) => ({
            ...raw,
            ...memoryGraph3DInitialPosition(raw, index, rawNodes.length)
        }));
        const byId = new Map(nodes.map(node => [node.id, node]));
        const edges = (data.edges || []).map(raw => ({
            ...raw,
            sourceNode: byId.get(raw.source),
            targetNode: byId.get(raw.target)
        })).filter(edge => edge.sourceNode && edge.targetNode);
        settleMemoryGraph3DLayout(nodes, edges);

        // WebGL lineWidth is fixed to one pixel on most Windows drivers. Use
        // lightweight hexagonal tubes so the thickness slider is visible and
        // behaves consistently in both dimensions.
        const edgeMeshes = [];
        const edgeGeometry = new THREE.CylinderGeometry(1, 1, 1, 6, 1, false);
        const edgeMaterials = {
            derived: new THREE.MeshBasicMaterial({color: 0x74819d, transparent: true, opacity: 0.36, depthWrite: false}),
            manual: new THREE.MeshBasicMaterial({color: 0x9bb4ff, transparent: true, opacity: 0.86, depthWrite: false})
        };
        const up = new THREE.Vector3(0, 1, 0);
        edges.forEach(edge => {
            const source = new THREE.Vector3(edge.sourceNode.x, edge.sourceNode.y, edge.sourceNode.z);
            const target = new THREE.Vector3(edge.targetNode.x, edge.targetNode.y, edge.targetNode.z);
            const direction = new THREE.Vector3().subVectors(target, source);
            const length = direction.length();
            if (length < 0.001) return;
            const manual = edge.source_kind === 'manual';
            const baseRadius = manual ? 0.52 : 0.34;
            const mesh = new THREE.Mesh(edgeGeometry, manual ? edgeMaterials.manual : edgeMaterials.derived);
            mesh.position.copy(source).add(target).multiplyScalar(0.5);
            mesh.quaternion.setFromUnitVectors(up, direction.normalize());
            const lineScale = getMemoryGraphDisplaySettings('3d').line;
            mesh.scale.set(baseRadius * lineScale, length, baseRadius * lineScale);
            mesh.userData.baseRadius = baseRadius;
            scene.add(mesh);
            edgeMeshes.push(mesh);
        });

        const geometries = {
            memory: new THREE.SphereGeometry(3.7, 16, 12),
            core: new THREE.SphereGeometry(5.2, 18, 14),
            connector: new THREE.SphereGeometry(4.5, 16, 12),
            project: new THREE.SphereGeometry(6.2, 20, 16)
        };
        const meshes = [];
        const meshByNodeId = new Map();
        nodes.forEach(node => {
            const color = memoryGraph3DNodeColor(node);
            const geometry = node.type === 'memory'
                ? (node.layer === 'core' ? geometries.core : geometries.memory)
                : (node.type === 'project' ? geometries.project : geometries.connector);
            const material = new THREE.MeshPhongMaterial({
                color,
                emissive: color,
                emissiveIntensity: node.type === 'memory' ? 0.13 : 0.2,
                shininess: 46,
                transparent: true,
                opacity: node.archived ? 0.48 : 0.96
            });
            const mesh = new THREE.Mesh(geometry, material);
            mesh.position.set(node.x, node.y, node.z);
            mesh.userData.graphNode = node;
            mesh.userData.baseColor = color;
            scene.add(mesh);
            meshes.push(mesh);
            meshByNodeId.set(node.id, mesh);
        });

        const labeledMemories = nodes.filter(node => node.type === 'memory' && (node.layer === 'core' || node.locked || node.starred)).slice(0, 24);
        const labelNodes = nodes.filter(node => node.type !== 'memory').concat(labeledMemories);
        const labels = [];
        labelNodes.slice(0, 90).forEach(node => {
            const label = createMemoryGraph3DLabel(node);
            if (label) {
                scene.add(label);
                labels.push(label);
            }
        });

        if (!nodes.length) {
            const empty = document.createElement('div');
            empty.className = 'memory-graph-3d-empty';
            empty.textContent = 'No memories match these filters.';
            host.appendChild(empty);
        }

        const raycaster = new THREE.Raycaster();
        const pointer = new THREE.Vector2();
        const tooltip = document.getElementById('memory-graph-3d-tooltip');
        const pickNode = event => {
            const rect = renderer.domElement.getBoundingClientRect();
            pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
            pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
            raycaster.setFromCamera(pointer, camera);
            return raycaster.intersectObjects(meshes, false)[0]?.object?.userData?.graphNode || null;
        };
        let pointerStart = null;
        renderer.domElement.addEventListener('pointerdown', event => {
            pointerStart = {x: event.clientX, y: event.clientY};
        });
        renderer.domElement.addEventListener('pointermove', event => {
            const node = pickNode(event);
            renderer.domElement.style.cursor = node ? 'pointer' : 'grab';
            if (!tooltip) return;
            if (!node) {
                tooltip.hidden = true;
                return;
            }
            const stageRect = host.parentElement.getBoundingClientRect();
            tooltip.hidden = false;
            tooltip.textContent = `${node.label || 'Untitled'} · ${node.type || 'node'}`;
            tooltip.style.left = `${Math.min(stageRect.width - 210, Math.max(10, event.clientX - stageRect.left + 12))}px`;
            tooltip.style.top = `${Math.min(stageRect.height - 48, Math.max(10, event.clientY - stageRect.top + 12))}px`;
        });
        renderer.domElement.addEventListener('pointerleave', () => {
            renderer.domElement.style.cursor = 'grab';
            if (tooltip) tooltip.hidden = true;
        });
        renderer.domElement.addEventListener('click', event => {
            if (pointerStart && Math.hypot(event.clientX - pointerStart.x, event.clientY - pointerStart.y) > 5) return;
            const node = pickNode(event);
            if (node) handleMemoryGraphNodeSelection(node);
            else {
                memoryGraphState.selectedNodeId = '';
                updateMemoryGraphNodeSelection();
                renderMemoryGraphDetail(null);
            }
        });
        renderer.domElement.addEventListener('dblclick', event => {
            const node = pickNode(event);
            if (node?.type === 'memory') centerMemoryGraphOn(node.ref_id);
        });
        host.onkeydown = event => {
            if (event.key.toLowerCase() === 'r') resetMemoryGraphViewport();
        };

        const resize = () => {
            if (!memoryGraphState.three || memoryGraphState.three.renderer !== renderer) return;
            const nextWidth = Math.max(320, host.clientWidth || width);
            const nextHeight = Math.max(360, host.clientHeight || height);
            camera.aspect = nextWidth / nextHeight;
            camera.updateProjectionMatrix();
            renderer.setSize(nextWidth, nextHeight, false);
        };
        const resizeObserver = typeof ResizeObserver === 'function' ? new ResizeObserver(resize) : null;
        resizeObserver?.observe(host);
        memoryGraphState.three = {renderer, scene, camera, controls, meshes, meshByNodeId, edgeMeshes, labels, resizeObserver, host};
        const animate = () => {
            if (!memoryGraphState.three || memoryGraphState.three.renderer !== renderer || memoryGraphState.viewMode !== '3d') return;
            controls?.update();
            renderer.render(scene, camera);
            memoryGraphState.animationFrame = requestAnimationFrame(animate);
        };
        animate();
        applyMemoryGraphDisplaySettings();
    }

    function disposeMemoryGraph3D() {
        const state = memoryGraphState?.three;
        if (!state) return;
        if (memoryGraphState.animationFrame) {
            cancelAnimationFrame(memoryGraphState.animationFrame);
            memoryGraphState.animationFrame = null;
        }
        state.resizeObserver?.disconnect();
        state.controls?.dispose?.();
        state.scene?.traverse?.(object => {
            object.geometry?.dispose?.();
            if (Array.isArray(object.material)) object.material.forEach(material => material?.dispose?.());
            else object.material?.dispose?.();
            object.material?.map?.dispose?.();
        });
        state.renderer?.dispose?.();
        if (state.host) state.host.replaceChildren();
        memoryGraphState.three = null;
        const tooltip = document.getElementById('memory-graph-3d-tooltip');
        if (tooltip) tooltip.hidden = true;
    }

    function memoryGraphPointer(svg, event) {
        const rect = svg.getBoundingClientRect();
        const viewBox = svg.viewBox.baseVal;
        return {
            x: ((event.clientX - rect.left) / Math.max(1, rect.width)) * viewBox.width,
            y: ((event.clientY - rect.top) / Math.max(1, rect.height)) * viewBox.height
        };
    }

    function installMemoryGraphViewportControls(svg, viewport) {
        let pan = null;
        svg.addEventListener('pointerdown', event => {
            if (event.target.closest?.('.memory-graph-node')) return;
            const point = memoryGraphPointer(svg, event);
            pan = {point, x: memoryGraphState.transform.x, y: memoryGraphState.transform.y};
            svg.setPointerCapture?.(event.pointerId);
        });
        svg.addEventListener('pointermove', event => {
            if (!pan) return;
            const point = memoryGraphPointer(svg, event);
            memoryGraphState.transform.x = pan.x + point.x - pan.point.x;
            memoryGraphState.transform.y = pan.y + point.y - pan.point.y;
            applyMemoryGraphTransform(viewport);
        });
        const stopPan = event => {
            pan = null;
            svg.releasePointerCapture?.(event.pointerId);
        };
        svg.addEventListener('pointerup', stopPan);
        svg.addEventListener('pointercancel', stopPan);
        svg.addEventListener('wheel', event => {
            event.preventDefault();
            const point = memoryGraphPointer(svg, event);
            const oldScale = memoryGraphState.transform.scale;
            const nextScale = Math.max(0.45, Math.min(3.2, oldScale * (event.deltaY < 0 ? 1.12 : 0.89)));
            const worldX = (point.x - memoryGraphState.transform.x) / oldScale;
            const worldY = (point.y - memoryGraphState.transform.y) / oldScale;
            memoryGraphState.transform.scale = nextScale;
            memoryGraphState.transform.x = point.x - worldX * nextScale;
            memoryGraphState.transform.y = point.y - worldY * nextScale;
            applyMemoryGraphTransform(viewport);
        }, {passive: false});
        svg.addEventListener('click', event => {
            if (event.target === svg || event.target === viewport) {
                memoryGraphState.selectedNodeId = '';
                updateMemoryGraphNodeSelection();
                renderMemoryGraphDetail(null);
            }
        });
    }

    function applyMemoryGraphTransform(viewport = document.querySelector('#memory-graph-svg .memory-graph-viewport')) {
        if (!viewport) return;
        const transform = memoryGraphState.transform;
        viewport.setAttribute('transform', `translate(${transform.x} ${transform.y}) scale(${transform.scale})`);
    }

    function resetMemoryGraphViewport() {
        if (memoryGraphState.viewMode === '3d' && memoryGraphState.three) {
            const {camera, controls} = memoryGraphState.three;
            camera.position.set(0, 35, 390);
            camera.lookAt(0, 0, 0);
            if (controls) {
                controls.target.set(0, 0, 0);
                controls.update();
            }
            return;
        }
        memoryGraphState.transform = {x: 0, y: 0, scale: 1};
        applyMemoryGraphTransform();
    }

    function handleMemoryGraphNodeSelection(node) {
        if (memoryGraphState.linkSourceId) {
            if (node.type === 'memory' && node.ref_id !== memoryGraphState.linkSourceId) {
                completeMemoryGraphLink(node.ref_id);
            }
            return;
        }
        memoryGraphState.selectedNodeId = node.id;
        updateMemoryGraphNodeSelection();
        renderMemoryGraphDetail(node);
    }

    function updateMemoryGraphNodeSelection() {
        document.querySelectorAll('#memory-graph-svg .memory-graph-node').forEach(element => {
            const nodeId = element.dataset.nodeId || '';
            element.classList.toggle('selected', nodeId === memoryGraphState.selectedNodeId);
            element.classList.toggle('link-source', nodeId === `memory:${memoryGraphState.linkSourceId}`);
        });
        const three = memoryGraphState.three;
        three?.meshByNodeId?.forEach((mesh, nodeId) => {
            const selected = nodeId === memoryGraphState.selectedNodeId;
            const linkSource = nodeId === `memory:${memoryGraphState.linkSourceId}`;
            const interactionScale = selected ? 1.65 : (linkSource ? 1.45 : 1);
            mesh.scale.setScalar(getMemoryGraphDisplaySettings('3d').node * interactionScale);
            if (mesh.material?.emissive) {
                mesh.material.emissive.setHex(selected ? 0xffffff : (linkSource ? 0xffb85c : mesh.userData.baseColor));
                mesh.material.emissiveIntensity = selected ? 0.72 : (linkSource ? 0.58 : 0.16);
            }
        });
    }

    function renderMemoryGraphDetail(node) {
        const panel = document.getElementById('memory-graph-detail-content');
        if (!panel) return;
        if (!node) {
            panel.innerHTML = '<div class="memory-graph-detail-empty"><strong>Select a node</strong><span>View its memory, links, source conversation, or create a confirmed relationship.</span></div>';
            return;
        }
        const data = memoryGraphState.data || {nodes: [], edges: []};
        const byId = new Map((data.nodes || []).map(item => [item.id, item]));
        const related = (data.edges || []).filter(edge => edge.source === node.id || edge.target === node.id);
        const relationHtml = related.length ? related.map(edge => {
            const otherId = edge.source === node.id ? edge.target : edge.source;
            const other = byId.get(otherId) || {};
            const remove = edge.source_kind === 'manual'
                ? `<button type="button" onclick="removeMemoryGraphEdge('${escapeJsString(edge.id)}')">${ic('close', 'icn-sm')}Remove</button>`
                : '<span class="memory-graph-derived">derived</span>';
            return `<div class="memory-graph-relation-row"><span><strong>${escapeHtml(memoryGraphRelationLabel(edge.relation))}</strong>${escapeHtml(other.label || otherId)}</span>${remove}</div>`;
        }).join('') : '<div class="memory-graph-detail-empty compact"><span>No visible relationships in this filtered view.</span></div>';

        if (node.type !== 'memory') {
            panel.innerHTML = `
                <div class="memory-graph-detail-heading"><span>${escapeHtml(node.type || 'connector')}</span><h4>${escapeHtml(node.label || 'Untitled')}</h4></div>
                <p class="memory-graph-detail-copy">This connector groups ${related.length} visible relationship${related.length === 1 ? '' : 's'}.</p>
                <div class="memory-graph-relation-list">${relationHtml}</div>
            `;
            return;
        }

        const conflict = node.conflict || {};
        const sessionButton = node.session_id
            ? `<button type="button" onclick="openMemoryGraphSourceSession('${escapeJsString(node.session_id)}')">${ic('chat', 'icn-sm')}Open source conversation</button>`
            : '';
        const conflictLine = conflict.status && conflict.status !== 'clear'
            ? `<div class="memory-graph-detail-warning">Conflict state: ${escapeHtml(conflict.status)}</div>`
            : '';
        const linkActive = memoryGraphState.linkSourceId === node.ref_id;
        panel.innerHTML = `
            <div class="memory-graph-detail-heading"><span>${escapeHtml(node.layer || 'memory')} · ${escapeHtml(node.category || 'general')}</span><h4>${escapeHtml(node.label || 'Untitled')}</h4></div>
            <p class="memory-graph-detail-copy">${escapeHtml(node.preview || 'No preview available.')}</p>
            <div class="memory-graph-detail-meta">${escapeHtml(node.project_name || node.source || 'memory')} ${node.locked ? '· locked' : ''} ${node.starred ? '· starred' : ''}</div>
            ${conflictLine}
            <div class="memory-graph-detail-actions">
                <button type="button" onclick="centerMemoryGraphOn('${escapeJsString(node.ref_id)}')">${ic('share-net', 'icn-sm')}Focus neighborhood</button>
                ${sessionButton}
            </div>
            <div class="memory-graph-link-editor ${linkActive ? 'active' : ''}">
                <select id="memory-graph-link-relation-select" onchange="memoryGraphState.linkRelation=this.value" aria-label="New relationship type">
                    <option value="related_to">related to</option><option value="supports">supports</option><option value="caused_by">caused by</option><option value="part_of">part of</option><option value="follows">follows</option>
                </select>
                <button type="button" onclick="${linkActive ? 'cancelMemoryGraphLink()' : `beginMemoryGraphLink('${escapeJsString(node.ref_id)}')`}">${linkActive ? ic('close', 'icn-sm') + 'Cancel linking' : ic('branch', 'icn-sm') + 'Create relationship'}</button>
                ${linkActive ? '<small>Select another memory node on the map to complete the relationship.</small>' : ''}
            </div>
            <h5>Visible relationships</h5>
            <div class="memory-graph-relation-list">${relationHtml}</div>
        `;
        const relationSelect = document.getElementById('memory-graph-link-relation-select');
        if (relationSelect) relationSelect.value = memoryGraphState.linkRelation;
    }

    function beginMemoryGraphLink(memoryId) {
        memoryGraphState.linkSourceId = memoryId;
        const select = document.getElementById('memory-graph-link-relation-select');
        if (select) memoryGraphState.linkRelation = select.value || 'related_to';
        updateMemoryGraphNodeSelection();
        const node = (memoryGraphState.data?.nodes || []).find(item => item.ref_id === memoryId && item.type === 'memory');
        renderMemoryGraphDetail(node || null);
    }

    function cancelMemoryGraphLink() {
        memoryGraphState.linkSourceId = '';
        updateMemoryGraphNodeSelection();
        const node = (memoryGraphState.data?.nodes || []).find(item => item.id === memoryGraphState.selectedNodeId);
        renderMemoryGraphDetail(node || null);
    }

    async function completeMemoryGraphLink(targetMemoryId) {
        const sourceMemoryId = memoryGraphState.linkSourceId;
        if (!sourceMemoryId || sourceMemoryId === targetMemoryId) return;
        const nodes = memoryGraphState.data?.nodes || [];
        const sourceNode = nodes.find(item => item.type === 'memory' && item.ref_id === sourceMemoryId);
        const targetNode = nodes.find(item => item.type === 'memory' && item.ref_id === targetMemoryId);
        const relationLabel = memoryGraphRelationLabel(memoryGraphState.linkRelation);
        showConfirmDialog({
            title: 'Create this relationship?',
            message: `${sourceNode?.label || 'Source memory'} ${relationLabel} ${targetNode?.label || 'Target memory'}`,
            impact: ['The link becomes visible in recall and in this network map'],
            confirmLabel: 'Create link',
            onConfirm: close => { close(); doCreateMemoryGraphRelationship(); }
        });
        return;
    }

    async function doCreateMemoryGraphRelationship() {
        try {
            const response = await fetch('/api/kb/graph/edges', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    source_memory_id: sourceMemoryId,
                    target_memory_id: targetMemoryId,
                    relation: memoryGraphState.linkRelation,
                    confidence: 1
                })
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || 'Unable to create relationship');
            memoryGraphState.linkSourceId = '';
            memoryGraphState.centerMemoryId = sourceMemoryId;
            memoryGraphState.selectedNodeId = `memory:${sourceMemoryId}`;
            await loadMemoryGraphView();
        } catch (error) {
            showToast(`Unable to create relationship: ${error.message || error}`, { type: 'error', duration: 4200 });
        }
    }

    async function removeMemoryGraphEdge(edgeId) {
        showConfirmDialog({
            title: 'Remove this relationship?',
            message: 'Only user-created links can be removed; derived links stay.',
            confirmLabel: 'Remove link',
            danger: true,
            onConfirm: close => { close(); doRemoveMemoryGraphRelationship(nodeId); }
        });
        return;
    }

    async function doRemoveMemoryGraphRelationship(nodeId) {
        try {
            const response = await fetch('/api/kb/graph/edges/remove', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({edge_id: edgeId})
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || 'Unable to remove relationship');
            await loadMemoryGraphView();
        } catch (error) {
            showToast(`Unable to remove relationship: ${error.message || error}`, { type: 'error', duration: 4200 });
        }
    }

    function centerMemoryGraphOn(memoryId) {
        memoryGraphState.centerMemoryId = memoryId;
        memoryGraphState.transform = {x: 0, y: 0, scale: 1};
        loadMemoryGraphView();
    }

    function resetMemoryGraphOverview() {
        memoryGraphState.centerMemoryId = '';
        memoryGraphState.selectedNodeId = '';
        memoryGraphState.linkSourceId = '';
        memoryGraphState.transform = {x: 0, y: 0, scale: 1};
        loadMemoryGraphView();
    }

    async function rebuildMemoryGraphView() {
        showConfirmDialog({
            title: 'Rebuild the memory network?',
            message: 'Relationships are recomputed from current memories and sessions. Existing memories are not modified.',
            confirmLabel: 'Rebuild',
            onConfirm: async close => {
                close();
                try {
                    const response = await fetch('/api/kb/graph/rebuild', {method: 'POST'});
                    const data = await response.json();
                    if (!response.ok) throw new Error(data.detail || 'Rebuild failed');
                    memoryGraphState.positions.clear();
                    memoryGraphState.positions3d.clear();
                    await loadMemoryGraphView();
                    showToast('Memory network rebuilt.');
                } catch (error) {
                    showToast('Memory network rebuild failed.', { type: 'error', duration: 4200 });
                    console.warn('Memory graph rebuild failed:', error);
                }
            }
        });
    }

    async function openMemoryGraphSourceSession(sessionId) {
        const loaded = await loadSession(sessionId);
        if (loaded) closeModal();
    }

    function setMemoryLayerFilter(layer) {
        memoryViewerLayer = layer || '';
        memoryViewerIncludeArchived = memoryViewerLayer === 'archive';
        const select = document.getElementById('memory-layer-filter');
        if (select) select.value = memoryViewerLayer;
        loadMemoryList();
    }

    async function loadMemoryLayerOverview() {
        try {
            const response = await fetch('/api/kb/stats');
            if (!response.ok) return;
            const stats = await response.json();
            const layers = stats.layers || {};
            const active = document.getElementById('memory-hero-active');
            const archived = document.getElementById('memory-hero-archived');
            const store = document.getElementById('memory-hero-store');
            if (active) active.textContent = String(stats.active || 0);
            if (archived) archived.textContent = String(stats.archived || 0);
            if (store) store.textContent = stats.sqlite?.available ? 'SQLite + JSON' : 'JSON';
            const tabsRoot = document.getElementById('memory-layer-overview');
            const allCount = ['core', 'semantic', 'episodic'].reduce((sum, key) => sum + Number(layers[key] || 0), 0);
            tabsRoot?.querySelectorAll('.memory-tab-count').forEach(counter => {
                const key = counter.dataset.countLayer;
                counter.textContent = key === 'all' ? String(allCount) : String(layers[key] || 0);
            });
        } catch (error) {
            console.warn('Unable to load memory layer counts', error);
        }
    }

    async function loadMemoryList() {
        const list = document.getElementById('memory-list');
        if (!list) return;
        const query = document.getElementById('memory-search-input')?.value.trim() || '';
        const category = document.getElementById('memory-category-filter')?.value || memoryViewerCategory || '';
        const memoryLayer = document.getElementById('memory-layer-filter')?.value || memoryViewerLayer || '';
        const includeArchived = memoryLayer === 'archive';
        memoryViewerIncludeArchived = includeArchived;
        renderSurfaceState(list, { kind: 'loading', title: 'Loading memories', message: 'Applying the selected filters…', icon: 'layers' });
        try {
            const res = await fetch('/api/kb/search', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    query,
                    category,
                    memory_layer: memoryLayer,
                    include_archived: includeArchived,
                    limit: 200
                })
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || data.message || `Memory search failed (${res.status})`);
            renderMemoryList(data.memories || []);
        } catch(e) {
            renderSurfaceState(list, {
                title: 'Memories could not be loaded',
                message: 'Your saved memories are unchanged. Check the connection and try again.',
                retry: () => loadMemoryList(),
                details: e
            });
            console.error(e);
        }
    }

    function renderMemoryList(memories) {
        const list = document.getElementById('memory-list');
        if (!list) return;
        list.innerHTML = '';
        selectedMemoryIds.clear();

        if (memories.length === 0) {
            list.innerHTML = `<div class="st-empty">${ic('layers')}<span>No memories match the current filters.</span><button type="button" class="workspace-secondary-action" onclick="toggleAddMemoryPanel()">${ic('plus', 'icn-sm')}Add memory</button></div>`;
            return;
        }

        const layerInfo = {
            core: {label: 'Core', description: 'Stable identity, boundaries and essential preferences'},
            semantic: {label: 'Semantic', description: 'Facts, projects, concepts and Obsidian knowledge'},
            episodic: {label: 'Episodic', description: 'Active events and conversation summaries used when relevant'},
            archive: {label: 'Archive', description: 'Retired or manually archived records excluded from normal prompt recall'}
        };
        const grouped = {core: [], semantic: [], episodic: [], archive: []};
        memories.forEach(memory => {
            const layer = memory.is_archived ? 'archive' : (memory.memory_layer || 'semantic');
            (grouped[layer] || grouped.semantic).push(memory);
        });

        const canDeleteMemoryRecord = memory => {
            const managedSlot = String(memory?.managed_slot || '');
            const retiredAssistantManaged = Boolean(memory?.is_archived && [
                'assistant_role:learning_area:',
                'assistant_role:learning_note:',
                'assistant_role:curated:'
            ].some(prefix => managedSlot.startsWith(prefix)));
            return retiredAssistantManaged || (!managedSlot && !memory?.is_locked);
        };

        ['core', 'semantic', 'episodic', 'archive'].forEach(layer => {
            const layerMemories = grouped[layer];
            if (!layerMemories.length) return;
            const deletableCount = layerMemories.filter(canDeleteMemoryRecord).length;
            const section = document.createElement('section');
            section.className = `memory-layer-section layer-${layer}`;
            section.dataset.memoryLayer = layer;
            section.innerHTML = `
                <header class="memory-layer-section-header">
                    <div class="memory-layer-section-copy"><strong>${layerInfo[layer].label}</strong><span>${layerInfo[layer].description}</span></div>
                    <div class="memory-layer-bulk-actions">
                        <em>${layerMemories.length}</em>
                        ${deletableCount ? `<button type="button" data-memory-select-layer="${layer}" onclick="toggleMemoryLayerSelection('${layer}')">${ic('check', 'icn-sm')}Select deletable</button><button type="button" class="memory-bulk-delete" data-memory-delete-layer="${layer}" onclick="deleteSelectedMemories('${layer}')" disabled>${ic('trash', 'icn-sm')}Delete selected (0)</button>` : '<span class="memory-layer-protected">Protected</span>'}
                    </div>
                </header>
                <div class="memory-layer-items"></div>
            `;
            const sectionBody = section.querySelector('.memory-layer-items');
            layerMemories.forEach(mem => {
            const div = document.createElement('div');
            div.className = `memory-item layer-${layer} ${mem.is_archived ? 'archived' : ''}`;
            const safeId = escapeJsString(mem.id);
            const tags = Array.isArray(mem.tags) ? mem.tags : [];
            const tagHtml = tags.length
                ? `<div class="memory-tags">${tags.map(tag => `<span>${escapeHtml(tag)}</span>`).join('')}</div>`
                : '';
            const confidence = Number(mem.confidence ?? 0.75);
            const score = mem.relevance_score !== undefined
                ? ` · relevance ${Number(mem.relevance_score).toFixed(2)}`
                : '';
            const priority = mem.priority_score !== undefined || mem.base_priority_score !== undefined
                ? ` · ${escapeHtml(mem.memory_tier || 'auto')} ${Number(mem.priority_score ?? mem.base_priority_score).toFixed(2)}`
                : '';
            const archivedBadge = mem.is_archived ? '<span class="memory-badge archived">archived</span>' : '';
            const conflictBadge = mem.conflicts_with && mem.conflicts_with.length
                ? '<span class="memory-badge conflict">conflict</span>'
                : '';
            const updateBadge = mem.supersedes && mem.supersedes.length
                ? '<span class="memory-badge update">updates old</span>'
                : '';
            const managedBadge = mem.managed_slot
                ? '<span class="memory-badge managed">managed</span>'
                : '';
            const kindBadge = mem.memory_kind
                ? `<span class="memory-badge kind">${escapeHtml(mem.memory_kind)}</span>`
                : '';
            const childBadge = mem.parent_memory_id
                ? '<span class="memory-badge child">child</span>'
                : '';
            const retentionBadge = mem.retention_policy
                ? `<span class="memory-badge retention ${escapeHtml(mem.retention_policy)}">${escapeHtml(mem.retention_policy)}</span>`
                : '';
            const layerBadge = `<span class="memory-badge layer ${layer}">${escapeHtml(layer)}</span>`;
            const restoreButton = mem.is_archived && !mem.managed_slot
                ? `<button class="btn-mem-action" onclick="restoreMemory('${safeId}', this)" title="Restore archived memory">${ic('archive', 'icn-sm')}</button>`
                : '';
            const canDelete = canDeleteMemoryRecord(mem);
            const editableAttr = mem.managed_slot ? 'false' : 'true';
            const blurHandler = mem.managed_slot ? '' : ` onblur="updateMemoryContent('${safeId}', this.innerText)"`;
            const deleteButton = canDelete
                ? `<button class="btn-mem-action" onclick="deleteMemory('${safeId}', this)" title="Delete memory permanently">${ic('trash', 'icn-sm')}</button>`
                : '';
            const lockButton = mem.managed_slot
                ? `<button class="btn-mem-action lock active" title="Managed memory is protected">${ic('lock', 'icn-sm')}</button>`
                : `<button class="btn-mem-action lock ${mem.is_locked ? 'active' : ''}" onclick="toggleMemoryLock('${safeId}', this)" title="Lock memory">${ic('lock', 'icn-sm')}</button>`;

            div.innerHTML = `
                <div style="flex:1">
                    <div class="memory-title-row">
                        <span>${escapeHtml(mem.title || 'Untitled')}</span>
                        ${layerBadge}${kindBadge}${retentionBadge}${childBadge}${archivedBadge}${conflictBadge}${updateBadge}${managedBadge}
                    </div>
                    <div class="memory-meta">
                        ${escapeHtml(mem.creation_time || mem.date || '')}
                        · ${escapeHtml(mem.source || 'manual')}
                        · ${escapeHtml(mem.category || 'general')}
                        · confidence ${confidence.toFixed(2)}
                        · accessed ${Number(mem.access_count || 0)}
                        ${priority}
                        ${score}
                    </div>
                    ${tagHtml}
                    <div class="memory-content" contenteditable="${editableAttr}"${blurHandler}>${escapeHtml(mem.content || '')}</div>
                </div>
                <div class="memory-actions">
                    <button class="btn-mem-action star ${mem.is_important ? 'active' : ''}" onclick="toggleMemoryStar('${safeId}', this)" title="Star important memory">${mem.is_important ? ic('star-fill', 'icn-sm') : ic('star', 'icn-sm')}</button>
                    ${lockButton}
                    <button type="button" class="row-menu-btn" onclick="showMemoryRowMenu(this, '${safeId}', ${mem.is_archived && !mem.managed_slot ? 'true' : 'false'}, ${canDelete ? 'true' : 'false'})" title="More actions" aria-label="More actions" aria-haspopup="menu">${ic('more', 'icn-sm')}</button>
                </div>
            `;
                div.dataset.memoryId = String(mem.id || '');
                div.dataset.memoryLayer = layer;
                if (canDelete) {
                    const selector = document.createElement('label');
                    selector.className = 'memory-bulk-check';
                    selector.title = 'Select for permanent deletion';
                    const checkbox = document.createElement('input');
                    checkbox.type = 'checkbox';
                    checkbox.setAttribute('aria-label', `Select ${mem.title || 'memory'} for deletion`);
                    checkbox.addEventListener('change', () => toggleMemoryBulkSelection(String(mem.id || ''), checkbox.checked, layer));
                    selector.appendChild(checkbox);
                    div.prepend(selector);
                }
                sectionBody.appendChild(div);
            });
            list.appendChild(section);
        });
    }

    function showMemoryRowMenu(anchor, id, canRestore, canDelete) {
        const items = [];
        if (canRestore) items.push({ label: 'Restore from archive', icon: 'archive', onClick: () => restoreMemory(id, null) });
        if (canDelete) items.push({ label: 'Delete permanently', icon: 'trash', danger: true, onClick: () => deleteMemory(id, null) });
        if (!items.length) items.push({ label: 'Protected record', icon: 'lock', onClick: () => {} });
        showMenuAt(anchor, items, { align: 'end' });
    }

    function toggleMemoryBulkSelection(memoryId, selected, layer) {
        if (selected) selectedMemoryIds.add(memoryId);
        else selectedMemoryIds.delete(memoryId);
        syncMemoryBulkControls(layer);
    }

    function syncMemoryBulkControls(layer) {
        const section = document.querySelector(`.memory-layer-section[data-memory-layer="${layer}"]`);
        if (!section) return;
        const checkboxes = Array.from(section.querySelectorAll('.memory-bulk-check input[type="checkbox"]'));
        const selectedCount = checkboxes.filter(checkbox => checkbox.checked).length;
        const selectButton = section.querySelector(`[data-memory-select-layer="${layer}"]`);
        const deleteButton = section.querySelector(`[data-memory-delete-layer="${layer}"]`);
        if (selectButton) selectButton.innerHTML = `${ic('check', 'icn-sm')}${selectedCount === checkboxes.length && checkboxes.length ? 'Clear selection' : 'Select deletable'}`;
        if (deleteButton) {
            deleteButton.disabled = selectedCount === 0;
            deleteButton.innerHTML = `${ic('trash', 'icn-sm')}Delete selected (${selectedCount})`;
        }
    }

    function toggleMemoryLayerSelection(layer) {
        const section = document.querySelector(`.memory-layer-section[data-memory-layer="${layer}"]`);
        if (!section) return;
        const checkboxes = Array.from(section.querySelectorAll('.memory-bulk-check input[type="checkbox"]'));
        const shouldSelect = checkboxes.some(checkbox => !checkbox.checked);
        checkboxes.forEach(checkbox => {
            checkbox.checked = shouldSelect;
            const memoryId = checkbox.closest('.memory-item')?.dataset.memoryId || '';
            if (!memoryId) return;
            if (shouldSelect) selectedMemoryIds.add(memoryId);
            else selectedMemoryIds.delete(memoryId);
        });
        syncMemoryBulkControls(layer);
    }

    async function deleteSelectedMemories(layer) {
        const section = document.querySelector(`.memory-layer-section[data-memory-layer="${layer}"]`);
        if (!section) return;
        const ids = Array.from(section.querySelectorAll('.memory-bulk-check input[type="checkbox"]:checked'))
            .map(checkbox => checkbox.closest('.memory-item')?.dataset.memoryId || '')
            .filter(Boolean);
        if (!ids.length) return;
        try {
            const previewResponse = await fetch('/api/kb/delete/bulk', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ids})
            });
            const preview = await previewResponse.json();
            if (!previewResponse.ok) throw new Error(preview.detail || preview.message || 'Unable to review deletion');
            const impact = memoryDeletionImpactText(preview);
            confirmAction(`Permanently delete ${preview.delete_count || ids.length} selected ${layer} memories?\n\n${impact}\n\nThis cannot be undone.`, async () => {

                const response = await fetch('/api/kb/delete/bulk', {
                    method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ids, confirmation_token: preview.confirmation_token})
                });
                const data = await response.json();
                if (!response.ok || data.status !== 'success') throw new Error(data.detail || data.message || 'Bulk deletion failed');
                ids.forEach(id => selectedMemoryIds.delete(id));
                await Promise.all([loadMemoryList(), loadMemoryLayerOverview()]);
                const blockedCount = Array.isArray(data.blocked) ? data.blocked.length : 0;
                if (blockedCount) showToast(`${data.deleted_count || 0} memories deleted. ${blockedCount} protected memories were skipped.`, { type: 'error', duration: 3600 });
        });        } catch (error) {
            showToast('Failed to delete selected memories: ' + (error.message || error), { type: 'error', duration: 3600 });
        }
    }

    function memoryCandidateCategoryOptions(selected) {
        const categories = [
            'identity', 'preference', 'project', 'goal', 'schedule',
            'health', 'relationship', 'emotion', 'general'
        ];
        return categories.map(category => {
            const isSelected = category === selected ? 'selected' : '';
            return `<option value="${category}" ${isSelected}>${category}</option>`;
        }).join('');
    }

    function renderAutonomousMemoryCandidates(candidates) {
        const stack = document.getElementById('memory-candidate-stack');
        if (!stack || !Array.isArray(candidates) || !candidates.length) return;

        candidates.forEach(candidate => {
            const id = String(candidate?.id || '');
            if (!id || renderedMemoryCandidateIds.has(id)) return;
            renderedMemoryCandidateIds.add(id);

            const tags = Array.isArray(candidate.tags) ? candidate.tags.join(', ') : '';
            const confidence = Number(candidate.confidence ?? 0.7);
            const card = document.createElement('section');
            card.className = 'memory-candidate-card';
            card.dataset.candidateId = id;
            card.innerHTML = `
                <div class="memory-candidate-topline">
                    <div>
                        <div class="memory-candidate-kicker">Saya noticed a memory candidate</div>
                        <input class="memory-candidate-title" value="${escapeHtml(candidate.title || 'Autonomous memory')}">
                    </div>
                    <button class="memory-candidate-close" onclick="ignoreAutonomousMemoryCandidate('${escapeJsString(id)}')" title="Ignore">${ic('close', 'icn-sm')}</button>
                </div>
                <textarea class="memory-candidate-content" rows="4">${escapeHtml(candidate.content || '')}</textarea>
                <div class="memory-candidate-grid">
                    <label>
                        <span>Category</span>
                        <select class="memory-candidate-category">
                            ${memoryCandidateCategoryOptions(candidate.category || 'general')}
                        </select>
                    </label>
                    <label>
                        <span>Tags</span>
                        <input class="memory-candidate-tags" value="${escapeHtml(tags)}">
                    </label>
                </div>
                <div class="memory-candidate-footer">
                    <div class="memory-candidate-options">
                        <label><input type="checkbox" class="memory-candidate-star"> Star</label>
                        <label><input type="checkbox" class="memory-candidate-lock"> Lock</label>
                        <span>confidence ${confidence.toFixed(2)}</span>
                    </div>
                    <div class="memory-candidate-actions">
                        <button onclick="ignoreAutonomousMemoryCandidate('${escapeJsString(id)}')">${ic('close', 'icn-sm')}Ignore</button>
                        <button class="primary" onclick="confirmAutonomousMemoryCandidate('${escapeJsString(id)}')">${ic('save', 'icn-sm')}Save</button>
                    </div>
                </div>
            `;
            stack.appendChild(card);
        });
    }

    async function loadPendingMemoryCandidates() {
        try {
            const response = await fetch('/api/kb/candidates?status=pending&limit=10');
            if (!response.ok) return;
            const data = await response.json();
            renderAutonomousMemoryCandidates(data.candidates || []);
        } catch (e) {
            console.warn("Unable to load memory candidates", e);
        }
    }

    function cssEscapeValue(value) {
        if (window.CSS && typeof window.CSS.escape === 'function') {
            return window.CSS.escape(String(value));
        }
        return String(value).replace(/["\\]/g, '\\$&');
    }

    function removeMemoryCandidateCard(id) {
        const card = document.querySelector(`.memory-candidate-card[data-candidate-id="${cssEscapeValue(id)}"]`);
        if (card) card.remove();
        renderedMemoryCandidateIds.delete(String(id));
    }

    function readMemoryCandidateUpdates(id) {
        const card = document.querySelector(`.memory-candidate-card[data-candidate-id="${cssEscapeValue(id)}"]`);
        if (!card) return null;
        const tags = card.querySelector('.memory-candidate-tags')?.value || '';
        return {
            title: card.querySelector('.memory-candidate-title')?.value.trim() || 'Autonomous memory',
            content: card.querySelector('.memory-candidate-content')?.value.trim() || '',
            category: card.querySelector('.memory-candidate-category')?.value || 'general',
            tags: tags.split(',').map(tag => tag.trim()).filter(Boolean),
            is_important: card.querySelector('.memory-candidate-star')?.checked === true,
            is_locked: card.querySelector('.memory-candidate-lock')?.checked === true
        };
    }

    async function confirmAutonomousMemoryCandidate(id) {
        const updates = readMemoryCandidateUpdates(id);
        if (!updates || !updates.content) {
            showToast("Memory content is required.", { type: 'error', duration: 3600 });
            return;
        }
        try {
            const response = await fetch('/api/kb/candidates/confirm', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({id, updates})
            });
            if (!response.ok) throw new Error("Save candidate failed");
            const data = await response.json();
            surfaceMemoryMaintenanceResult(data);
            removeMemoryCandidateCard(id);
            if (data.status === 'duplicate') {
                showToast("That memory already exists.", { type: 'info', duration: 3600 });
            }
            loadMemoryList();
        } catch (e) {
            showToast("Failed to save memory candidate: " + e, { type: 'error', duration: 3600 });
        }
    }

    async function ignoreAutonomousMemoryCandidate(id) {
        try {
            await fetch('/api/kb/candidates/ignore', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({id})
            });
        } catch (e) {
            console.warn("Unable to ignore memory candidate", e);
        }
        removeMemoryCandidateCard(id);
    }

    function ensureMemoryAddDialog() {
        if (document.getElementById('memory-add-panel')) return;
        const overlay = document.createElement('div');
        overlay.className = 'st-dialog-overlay';
        overlay.id = 'memory-add-panel';
        overlay.dataset.persistent = 'true';
        overlay.hidden = true;
        overlay.innerHTML = `
            <div class="st-dialog st-dialog-wide" role="dialog" aria-modal="true" aria-label="Add memory">
                <div class="st-dialog-title">Add memory</div>
                <div class="st-dialog-body">
                    <div class="form-row stacked" style="margin-bottom:12px;">
                        <label class="form-label" for="memory-add-title">Title <span class="form-help" style="display:inline;">(optional)</span></label>
                        <input id="memory-add-title" type="text" placeholder="Title (optional)" />
                    </div>
                    <div style="display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-bottom:12px;">
                        <div>
                            <label class="form-label" for="memory-add-category">Category</label>
                            <select id="memory-add-category">
                                <option value="">Auto category</option>
                                <option value="identity">Identity</option>
                                <option value="preference">Preference</option>
                                <option value="relationship">Relationship</option>
                                <option value="project">Project</option>
                                <option value="goal">Goal</option>
                                <option value="schedule">Schedule</option>
                                <option value="health">Health</option>
                                <option value="emotion">Emotion</option>
                                <option value="general">General</option>
                            </select>
                        </div>
                        <div>
                            <label class="form-label" for="memory-add-layer">Layer</label>
                            <select id="memory-add-layer">
                                <option value="">Auto layer</option>
                                <option value="core">Core</option>
                                <option value="semantic">Semantic</option>
                                <option value="episodic">Episodic</option>
                            </select>
                        </div>
                    </div>
                    <label class="form-label" for="memory-add-content">Content</label>
                    <textarea id="memory-add-content" rows="4" placeholder="Write the memory to save..."></textarea>
                    <div class="memory-add-actions">
                        <label class="switch"><input id="memory-add-important" type="checkbox"><span class="switch-track"></span><span class="switch-label">Star important</span></label>
                        <label class="switch"><input id="memory-add-locked" type="checkbox"><span class="switch-track"></span><span class="switch-label">Lock</span></label>
                        <label id="memory-add-project-label" class="switch" style="display:none;"><input id="memory-add-project" type="checkbox"><span class="switch-track"></span><span class="switch-label">Current project only</span></label>
                    </div>
                </div>
                <div class="st-dialog-footer">
                    <button type="button" class="workspace-secondary-action" onclick="toggleAddMemoryPanel()">${ic('close', 'icn-sm')}Cancel</button>
                    <button type="button" class="workspace-primary-action" onclick="saveManualMemory()">${ic('save', 'icn-sm')}Save memory</button>
                </div>
            </div>`;
        document.body.appendChild(overlay);
        const projectLabel = overlay.querySelector('#memory-add-project-label');
        if (projectLabel && currentProjectId) projectLabel.style.display = 'inline-flex';
    }

    function toggleAddMemoryPanel() {
        ensureMemoryAddDialog();
        const panel = document.getElementById('memory-add-panel');
        if (!panel) return;
        panel.hidden = !panel.hidden;
    }

    function showMemoryToolsMenu(anchor) {
        showMenuAt(anchor, [
            { label: 'Rebuild metadata', icon: 'refresh', onClick: () => rebuildMemoryMetadata() }
        ], { align: 'end' });
    }

    function syncMemoryLayerTabs(layer) {
        document.querySelectorAll('#memory-layer-overview .ws-tab').forEach(tab => {
            const active = (tab.dataset.memoryLayerTab || '') === (layer || '');
            tab.classList.toggle('active', active);
            tab.setAttribute('aria-selected', active ? 'true' : 'false');
        });
    }

    document.addEventListener('DOMContentLoaded', () => {
        document.getElementById('memory-layer-overview')?.addEventListener?.('click', () => {});
    });
    document.addEventListener('click', event => {
        const tab = event.target.closest('#memory-layer-overview .ws-tab');
        if (tab) {
            const layer = tab.dataset.memoryLayerTab || '';
            const select = document.getElementById('memory-layer-filter');
            if (select) select.value = layer;
            setMemoryLayerFilter(layer);
        }
    });

    async function saveManualMemory() {
        const contentEl = document.getElementById('memory-add-content');
        const content = contentEl?.value.trim() || '';
        if (!content) {
            showToast('Memory content is required.', { type: 'error' });
            return;
        }

        const metadata = {};
        const selectedLayer = document.getElementById('memory-add-layer')?.value || '';
        if (selectedLayer) metadata.memory_layer = selectedLayer;
        if (document.getElementById('memory-add-locked')?.checked) {
            metadata.is_locked = true;
        }
        if (currentProjectId && document.getElementById('memory-add-project')?.checked) {
            const project = projectsCache.find(item => item.id === currentProjectId);
            metadata.project_id = currentProjectId;
            metadata.project_name = project?.name || "Untitled Project";
            metadata.project_scoped = true;
        }

        try {
            const response = await fetch('/api/kb/add', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    title: document.getElementById('memory-add-title')?.value.trim() || null,
                    content,
                    source: 'manual',
                    is_important: document.getElementById('memory-add-important')?.checked === true,
                    category: document.getElementById('memory-add-category')?.value || null,
                    metadata
                })
            });
            if (!response.ok) throw new Error("Add memory failed");
            const data = await response.json();
            surfaceMemoryMaintenanceResult(data);
            document.getElementById('memory-add-title').value = '';
            contentEl.value = '';
            document.getElementById('memory-add-category').value = '';
            document.getElementById('memory-add-layer').value = '';
            toggleAddMemoryPanel();
            showToast('Memory saved.');
            loadMemoryList();
        } catch(e) {
            showToast('The memory could not be saved.', { type: 'error' });
            console.warn(e);
        }
    }

    async function openMemorySettings() {
        const settingsMenu = document.getElementById('settings-menu');
        if (settingsMenu) settingsMenu.style.display = 'none';
        const modal = document.getElementById('memory-modal');
        const body = document.getElementById('modal-body');
        setMemoryModalHeader("Settings", "openAboutYouModal()");
        modal.style.display = 'flex';

        let memoryStats = null;
        try {
            const [settingsResponse, statsResponse] = await Promise.all([
                fetch('/api/kb/settings'),
                fetch('/api/kb/stats')
            ]);
            const settings = await settingsResponse.json();
            memoryStats = statsResponse.ok ? await statsResponse.json() : null;
            memoryAutoSave = settings.auto_save_memories !== false;
            memoryAutoMergeEnabled = settings.auto_merge_enabled === true;
            memoryAutoMergeDays = Number(settings.auto_merge_days || 7);
            memory2Enabled = settings.memory_2_enabled !== false;
            memoryRelevantCount = Number(settings.relevant_memory_count || 8);
            memoryIncludeMetadata = settings.include_memory_metadata !== false;
            memoryAutonomousEnabled = settings.autonomous_memory_enabled === true;
            memoryLayeredEnabled = settings.layered_memory_enabled !== false;
            memoryApiContextTokens = Number(settings.api_context_window_tokens || 1000000);
            memoryLocalContextTokens = Number(settings.local_context_window_tokens || 8192);
            memoryPromptBudgetTokens = Number(settings.memory_prompt_budget_tokens || 12000);
            memoryPromptBudgetRatio = Number(settings.memory_prompt_budget_ratio || 0.15);
            memoryItemBudgetTokens = Number(settings.memory_item_budget_tokens || 700);
            memoryMaxCharsCore = Number(settings.memory_max_chars_core || 1200);
            memoryMaxCharsSemantic = Number(settings.memory_max_chars_semantic || 2400);
            memoryMaxCharsEpisodic = Number(settings.memory_max_chars_episodic || 3600);
            memoryMaxCharsKnowledgeSimple = Number(settings.memory_max_chars_knowledge_simple || 1200);
            memoryMaxCharsKnowledgeComplex = Number(settings.memory_max_chars_knowledge_complex || 3600);
            memoryCoreLimit = Number(settings.memory_core_limit ?? 10);
            memoryCandidatePoolSize = Number(settings.memory_candidate_pool_size || 80);
            memoryActiveSoftLimit = Number(settings.memory_active_soft_limit || 5000);
            memoryArchiveSoftLimit = Number(settings.memory_archive_soft_limit || 50000);
            memoryActiveHardLimit = Number(settings.memory_active_hard_limit || 6000);
            memoryArchiveHardLimit = Number(settings.memory_archive_hard_limit || 60000);
            memoryCapacityWarningRatio = Number(settings.memory_capacity_warning_ratio ?? 0.80);
            memoryCapacityAutoCompactEnabled = settings.memory_capacity_auto_compact_enabled !== false;
            memoryColdArchiveEnabled = settings.memory_cold_archive_enabled !== false;
            memoryColdArchiveBatchSize = Number(settings.memory_cold_archive_batch_size || 200);
            memoryRetrievalThreshold = Number(settings.memory_retrieval_threshold ?? 0.08);
            memoryAccessFlushSeconds = Number(settings.memory_access_flush_seconds || 120);
            memoryCompressionEnabled = settings.memory_compression_enabled === true || settings.auto_merge_enabled === true;
            memoryCompressionAgeDays = Number(settings.memory_compression_age_days || 30);
            memoryCompressionMinGroup = Number(settings.memory_compression_min_group || 3);
            memoryCompressionGroupSize = Number(settings.memory_compression_group_size || 20);
            memoryCompressionSummaryChars = Number(settings.memory_compression_summary_chars || 1800);
            memoryCompressionMaxGenerations = Number(settings.memory_compression_max_generations || 2);
            memorySqliteEnabled = settings.sqlite_memory_enabled !== false;
            memorySqliteFtsEnabled = settings.sqlite_fts_enabled !== false;
            memoryEmbeddingEnabled = settings.embedding_enabled === true;
            memoryEmbeddingProvider = settings.embedding_provider || 'sentence_transformers';
            memoryEmbeddingModel = settings.embedding_model || 'BAAI/bge-m3';
            memoryEmbeddingDevice = settings.embedding_device || 'cpu';
            memoryEmbeddingApiUrl = settings.embedding_api_url || '';
            memoryEmbeddingApiKeyEnv = settings.embedding_api_key_env || 'EMBEDDING_API_KEY';
            memoryEmbeddingBatchSize = Number(settings.embedding_batch_size || 8);
            memoryEmbeddingCandidateCount = Number(settings.embedding_candidate_count || 80);
            memoryEmbeddingMinSimilarity = Number(settings.embedding_min_similarity ?? 0.30);
            memoryEmbeddingIndexBatchLimit = Number(settings.embedding_index_batch_limit || 500);
            memoryEmbeddingApiTimeout = Number(settings.embedding_api_timeout_seconds || 120);
            memoryEmbeddingMaxChars = Number(settings.embedding_max_chars || 12000);
            memoryEmbeddingAutoIndexNew = settings.embedding_auto_index_new === true;
        } catch (e) {
            console.warn("Unable to load memory settings", e);
        }

        const layerStats = memoryStats?.layers || {};
        const capacitySummary = memoryStats
            ? `${memoryStats.active} active / ${memoryStats.archived} archived · core ${layerStats.core || 0}, semantic ${layerStats.semantic || 0}, episodic ${layerStats.episodic || 0}`
            : 'Capacity statistics unavailable';
        const sqliteSummary = memoryStats?.sqlite?.available
            ? `SQLite ready · ${memoryStats.sqlite.memory_count || 0} records · ${memoryStats.sqlite.indexed_count || 0} indexed · FTS ${memoryStats.sqlite.fts_tokenizer || 'enabled'}`
            : `JSON fallback${memoryStats?.sqlite?.message ? ` · ${memoryStats.sqlite.message}` : ''}`;
        const embeddingState = memoryStats?.embedding || {};
        const embeddingSummary = embeddingState.ready
            ? `Semantic index ready · ${embeddingState.index?.count || 0} vectors · ${embeddingState.missing_count || 0} missing · ${embeddingState.stale_count || 0} stale`
            : `${embeddingState.enabled ? 'Semantic provider unavailable' : 'Semantic embedding disabled'}${embeddingState.last_error ? ` · ${embeddingState.last_error}` : ''}`;
        const maintenanceState = memoryStats?.maintenance || {};
        const maintenanceCapacity = maintenanceState.capacity || {};
        const maintenanceEvent = (maintenanceState.pending_events || []).slice(-1)[0] || null;
        const activeCapacityCount = Number(memoryStats?.active || 0);
        const archiveCapacityCount = Number(memoryStats?.archived || 0);
        const effectiveMaintenanceStatus = (
            activeCapacityCount >= Number(memoryActiveHardLimit)
            || archiveCapacityCount >= Number(memoryArchiveHardLimit)
        ) ? 'hard' : (
            activeCapacityCount >= Number(maintenanceCapacity.active_warning_at || memoryActiveSoftLimit)
            || archiveCapacityCount >= Number(maintenanceCapacity.archive_warning_at || memoryArchiveSoftLimit)
        ) ? 'warning' : 'normal';
        const maintenanceTone = effectiveMaintenanceStatus === 'hard'
            ? 'critical'
            : effectiveMaintenanceStatus === 'normal' ? 'healthy' : 'warning';
        const maintenanceBanner = `
            <section class="memory-capacity-banner ${maintenanceTone}">
                <div>
                    <span>CAPACITY GOVERNANCE</span>
                    <strong>${effectiveMaintenanceStatus === 'normal' ? 'Capacity is healthy' : effectiveMaintenanceStatus === 'hard' ? 'Hard-limit protection is active' : 'Capacity maintenance is approaching'}</strong>
                    <small>${escapeHtml(maintenanceEvent?.message || `Saya warns at ${Math.round(Number(maintenanceCapacity.warning_ratio || memoryCapacityWarningRatio) * 100)}%, compacts at the soft threshold, and forces recoverable compaction only at the hard limit.`)}</small>
                </div>
                <div class="memory-capacity-banner-metrics">
                    <span><b>${Number(memoryStats?.active || 0).toLocaleString()}</b> / ${Number(memoryActiveHardLimit).toLocaleString()} active</span>
                    <span><b>${Number(memoryStats?.archived || 0).toLocaleString()}</b> / ${Number(memoryArchiveHardLimit).toLocaleString()} archive</span>
                    <span><b>${Number(maintenanceState.cold_archive_count || 0).toLocaleString()}</b> cold</span>
                </div>
            </section>`;

        const activeRatio = Math.min(1, activeCapacityCount / Math.max(1, Number(memoryActiveHardLimit)));
        body.innerHTML = `
            <div class="memory-settings-workspace">
                <section class="page-intro">
                    <div class="page-intro-copy">
                        <h3 class="page-intro-title">Memory settings</h3>
                        <p class="page-intro-sub">${escapeHtml(maintenanceEvent?.message || 'Capacity compaction preserves originals in Archive or recoverable cold storage.')}</p>
                    </div>
                    <span class="st-badge ${maintenanceTone === 'healthy' ? 'success' : maintenanceTone === 'critical' ? 'danger' : 'warning'}">${effectiveMaintenanceStatus === 'normal' ? 'Capacity healthy' : effectiveMaintenanceStatus === 'hard' ? 'Hard limit active' : 'Approaching limit'}</span>
                </section>
                <div class="memory-capacity-line">
                    <div class="memory-capacity-bar"><span style="width:${Math.round(activeRatio * 100)}%"></span></div>
                    <div class="memory-capacity-meta">
                        <span>${Number(memoryStats?.active || 0).toLocaleString()} / ${Number(memoryActiveHardLimit).toLocaleString()} active</span>
                        <span>${Number(memoryStats?.archived || 0).toLocaleString()} / ${Number(memoryArchiveHardLimit).toLocaleString()} archived</span>
                        <span>${Number(maintenanceState.cold_archive_count || 0).toLocaleString()} cold</span>
                        <span class="memory-store-summary">${escapeHtml(sqliteSummary)}</span>
                    </div>
                </div>
                <div class="ws-tabs" id="memory-settings-tabs" role="tablist">
                    <button type="button" class="ws-tab active" data-ms-pane="basic" role="tab" aria-selected="true">Basic</button>
                    <button type="button" class="ws-tab" data-ms-pane="advanced" role="tab" aria-selected="false">Advanced</button>
                </div>
                <section data-ms-pane="basic" role="tabpanel">
                    <div class="form-row">
                        <div><div class="form-label">Auto-save memories</div><p class="form-help">Automatically saves explicit "remember this" requests; it does not guess personal facts.</p></div>
                        <label class="switch"><input type="checkbox" id="auto-save-toggle" ${memoryAutoSave ? 'checked' : ''} onchange="memoryAutoSave = this.checked"><span class="switch-track"></span></label>
                    </div>
                    <div class="form-row">
                        <div><div class="form-label">Autonomous suggestions</div><p class="form-help">Suggests durable memories after chats using the Autonomous Memory Model. Saved only after your confirmation.</p></div>
                        <label class="switch"><input type="checkbox" id="autonomous-memory-toggle" ${memoryAutonomousEnabled ? 'checked' : ''} onchange="memoryAutonomousEnabled = this.checked"><span class="switch-track"></span></label>
                    </div>
                    <div class="form-row">
                        <div><div class="form-label">Smart retrieval</div><p class="form-help">Uses category, confidence, tags, recency and relevance instead of dumping raw memories into the prompt.</p></div>
                        <label class="switch"><input type="checkbox" id="memory-2-toggle" ${memory2Enabled ? 'checked' : ''} onchange="memory2Enabled = this.checked"><span class="switch-track"></span></label>
                    </div>
                    <div class="form-row">
                        <div><div class="form-label">Layered memory</div><p class="form-help">Orders prompt memory as core → semantic facts → episodic summaries.</p></div>
                        <label class="switch"><input type="checkbox" id="layered-memory-toggle" ${memoryLayeredEnabled ? 'checked' : ''} onchange="memoryLayeredEnabled = this.checked"><span class="switch-track"></span></label>
                    </div>
                    <div class="form-row">
                        <div><div class="form-label">Relevant memories per prompt</div><p class="form-help">How many matched memories may enter a single prompt.</p></div>
                        <input class="form-control" style="max-width:140px;" type="number" value="${memoryRelevantCount}" min="1" max="50" onchange="memoryRelevantCount = this.value">
                    </div>
                </section>
                <section data-ms-pane="advanced" role="tabpanel" hidden>
                    <div class="form-section">
                        <div class="form-section-title">Capacity</div>
                        <div class="limits-table">
                            <div class="limits-table-row"><span>Active soft limit</span><input type="number" value="${memoryActiveSoftLimit}" min="100" max="1000000" onchange="memoryActiveSoftLimit = this.value"></div>
                            <div class="limits-table-row"><span>Active hard limit</span><input type="number" value="${memoryActiveHardLimit}" min="100" max="1200000" onchange="memoryActiveHardLimit = this.value"></div>
                            <div class="limits-table-row"><span>Archive soft limit</span><input type="number" value="${memoryArchiveSoftLimit}" min="100" max="5000000" onchange="memoryArchiveSoftLimit = this.value"></div>
                            <div class="limits-table-row"><span>Archive hard limit</span><input type="number" value="${memoryArchiveHardLimit}" min="100" max="6000000" onchange="memoryArchiveHardLimit = this.value"></div>
                            <div class="limits-table-row"><span>Early warning ratio</span><input type="number" value="${memoryCapacityWarningRatio}" min="0.5" max="0.95" step="0.05" onchange="memoryCapacityWarningRatio = this.value"></div>
                            <div class="limits-table-row"><span>Cold archive batch size</span><input type="number" value="${memoryColdArchiveBatchSize}" min="2" max="2000" onchange="memoryColdArchiveBatchSize = this.value"></div>
                        </div>
                        <div class="form-row" style="margin-top:10px;">
                            <div><div class="form-label">Automatic capacity compaction</div><p class="form-help">Runs safe deterministic compaction before the hard limit. No LLM tokens are used.</p></div>
                            <label class="switch"><input type="checkbox" ${memoryCapacityAutoCompactEnabled ? 'checked' : ''} onchange="memoryCapacityAutoCompactEnabled = this.checked"><span class="switch-track"></span></label>
                        </div>
                        <div class="form-row">
                            <div><div class="form-label">Allow cold storage</div><p class="form-help">Moves full originals out of the active JSON working set before the hard limit.</p></div>
                            <label class="switch"><input type="checkbox" ${memoryColdArchiveEnabled ? 'checked' : ''} onchange="memoryColdArchiveEnabled = this.checked"><span class="switch-track"></span></label>
                        </div>
                    </div>
                    <div class="form-section">
                        <div class="form-section-title">Prompt budget</div>
                        <div class="limits-table">
                            <div class="limits-table-row"><span>API context tokens</span><input type="number" value="${memoryApiContextTokens}" min="8192" max="2000000" onchange="memoryApiContextTokens = this.value"></div>
                            <div class="limits-table-row"><span>Local context tokens</span><input type="number" value="${memoryLocalContextTokens}" min="2048" max="262144" onchange="memoryLocalContextTokens = this.value"></div>
                            <div class="limits-table-row"><span>Memory prompt max tokens</span><input type="number" value="${memoryPromptBudgetTokens}" min="512" max="200000" onchange="memoryPromptBudgetTokens = this.value"></div>
                            <div class="limits-table-row"><span>Context budget ratio</span><input type="number" value="${memoryPromptBudgetRatio}" min="0.001" max="0.5" step="0.01" onchange="memoryPromptBudgetRatio = this.value"></div>
                            <div class="limits-table-row"><span>Per-memory max tokens</span><input type="number" value="${memoryItemBudgetTokens}" min="64" max="12000" onchange="memoryItemBudgetTokens = this.value"></div>
                            <div class="limits-table-row"><span>Core content max chars</span><input type="number" value="${memoryMaxCharsCore}" min="200" max="12000" onchange="memoryMaxCharsCore = this.value"></div>
                            <div class="limits-table-row"><span>Semantic content max chars</span><input type="number" value="${memoryMaxCharsSemantic}" min="400" max="30000" onchange="memoryMaxCharsSemantic = this.value"></div>
                            <div class="limits-table-row"><span>Episodic content max chars</span><input type="number" value="${memoryMaxCharsEpisodic}" min="600" max="50000" onchange="memoryMaxCharsEpisodic = this.value"></div>
                            <div class="limits-table-row"><span>Simple note max chars</span><input type="number" value="${memoryMaxCharsKnowledgeSimple}" min="400" max="12000" onchange="memoryMaxCharsKnowledgeSimple = this.value"></div>
                            <div class="limits-table-row"><span>Complex note max chars</span><input type="number" value="${memoryMaxCharsKnowledgeComplex}" min="800" max="30000" onchange="memoryMaxCharsKnowledgeComplex = this.value"></div>
                        </div>
                    </div>
                    <div class="form-section">
                        <div class="form-section-title">Retrieval</div>
                        <div class="limits-table">
                            <div class="limits-table-row"><span>Core layer limit</span><input type="number" value="${memoryCoreLimit}" min="0" max="100" onchange="memoryCoreLimit = this.value"></div>
                            <div class="limits-table-row"><span>Candidate pool size</span><input type="number" value="${memoryCandidatePoolSize}" min="10" max="1000" onchange="memoryCandidatePoolSize = this.value"></div>
                            <div class="limits-table-row"><span>Retrieval threshold</span><input type="number" value="${memoryRetrievalThreshold}" min="0" max="1" step="0.01" onchange="memoryRetrievalThreshold = this.value"></div>
                            <div class="limits-table-row"><span>Access metadata flush (sec)</span><input type="number" value="${memoryAccessFlushSeconds}" min="10" max="3600" onchange="memoryAccessFlushSeconds = this.value"></div>
                        </div>
                        <div class="form-row" style="margin-top:10px;">
                            <div><div class="form-label">Include metadata in prompt</div></div>
                            <label class="switch"><input type="checkbox" id="memory-metadata-toggle" ${memoryIncludeMetadata ? 'checked' : ''} onchange="memoryIncludeMetadata = this.checked"><span class="switch-track"></span></label>
                        </div>
                        <div class="form-row">
                            <div><div class="form-label">Layered compression</div><p class="form-help">Local summaries stop after ${memoryCompressionMaxGenerations} generations. Dates, entities, decisions and negations are preserved; originals are archived and linked.</p></div>
                            <label class="switch"><input type="checkbox" id="auto-merge-toggle" ${memoryCompressionEnabled ? 'checked' : ''} onchange="memoryCompressionEnabled = this.checked; memoryAutoMergeEnabled = this.checked"><span class="switch-track"></span></label>
                        </div>
                        <div class="limits-table">
                            <div class="limits-table-row"><span>Compression age (days)</span><input type="number" value="${memoryCompressionAgeDays}" min="1" max="3650" onchange="memoryCompressionAgeDays = this.value; memoryAutoMergeDays = this.value"></div>
                            <div class="limits-table-row"><span>Minimum group size</span><input type="number" value="${memoryCompressionMinGroup}" min="2" max="100" onchange="memoryCompressionMinGroup = this.value"></div>
                            <div class="limits-table-row"><span>Maximum group size</span><input type="number" value="${memoryCompressionGroupSize}" min="2" max="500" onchange="memoryCompressionGroupSize = this.value"></div>
                            <div class="limits-table-row"><span>Summary max characters</span><input type="number" value="${memoryCompressionSummaryChars}" min="300" max="20000" onchange="memoryCompressionSummaryChars = this.value"></div>
                        </div>
                    </div>
                    <div class="form-section">
                        <div class="form-section-title">Indexing</div>
                        <div class="form-row">
                            <div><div class="form-label">SQLite mirror and recovery store</div><p class="form-help">knowledge.json remains a complete compatibility export. SQLite failures automatically fall back to JSON retrieval.</p></div>
                            <label class="switch"><input type="checkbox" ${memorySqliteEnabled ? 'checked' : ''} onchange="memorySqliteEnabled = this.checked"><span class="switch-track"></span></label>
                        </div>
                        <div class="form-row">
                            <div><div class="form-label">FTS5 candidate retrieval</div></div>
                            <label class="switch"><input type="checkbox" ${memorySqliteFtsEnabled ? 'checked' : ''} onchange="memorySqliteFtsEnabled = this.checked"><span class="switch-track"></span></label>
                        </div>
                        <div class="form-row">
                            <div class="form-label" style="flex:1;">Rebuild the SQLite index from JSON.</div>
                            <button type="button" class="workspace-secondary-action" onclick="rebuildMemoryStore()">${ic('database', 'icn-sm')}Rebuild SQLite index</button>
                        </div>
                    </div>
                    <div class="form-section">
                        <div class="form-section-title">Semantic embeddings</div>
                        <p class="form-help" style="margin-bottom:10px;">${escapeHtml(embeddingSummary)}</p>
                        <div class="form-row">
                            <div><div class="form-label">Enable semantic retrieval</div></div>
                            <label class="switch"><input type="checkbox" ${memoryEmbeddingEnabled ? 'checked' : ''} onchange="memoryEmbeddingEnabled = this.checked"><span class="switch-track"></span></label>
                        </div>
                        <div class="form-row">
                            <div><div class="form-label">Auto-index new/edited memories</div></div>
                            <label class="switch"><input type="checkbox" ${memoryEmbeddingAutoIndexNew ? 'checked' : ''} onchange="memoryEmbeddingAutoIndexNew = this.checked"><span class="switch-track"></span></label>
                        </div>
                        <div class="limits-table">
                            <div class="limits-table-row"><span>Provider</span><select onchange="memoryEmbeddingProvider = this.value">
                                <option value="sentence_transformers" ${memoryEmbeddingProvider === 'sentence_transformers' ? 'selected' : ''}>Local sentence-transformers</option>
                                <option value="openai_compatible" ${memoryEmbeddingProvider === 'openai_compatible' ? 'selected' : ''}>OpenAI-compatible API</option>
                            </select></div>
                            <div class="limits-table-row"><span>Model</span><input type="text" value="${escapeHtml(memoryEmbeddingModel)}" onchange="memoryEmbeddingModel = this.value"></div>
                            <div class="limits-table-row"><span>Local device</span><input type="text" value="${escapeHtml(memoryEmbeddingDevice)}" onchange="memoryEmbeddingDevice = this.value"></div>
                            <div class="limits-table-row"><span>Batch size</span><input type="number" value="${memoryEmbeddingBatchSize}" min="1" max="256" onchange="memoryEmbeddingBatchSize = this.value"></div>
                            <div class="limits-table-row"><span>Semantic candidates</span><input type="number" value="${memoryEmbeddingCandidateCount}" min="5" max="1000" onchange="memoryEmbeddingCandidateCount = this.value"></div>
                            <div class="limits-table-row"><span>Minimum cosine similarity</span><input type="number" value="${memoryEmbeddingMinSimilarity}" min="-1" max="1" step="0.01" onchange="memoryEmbeddingMinSimilarity = this.value"></div>
                            <div class="limits-table-row"><span>Index batch limit</span><input type="number" value="${memoryEmbeddingIndexBatchLimit}" min="1" max="1000000" onchange="memoryEmbeddingIndexBatchLimit = this.value"></div>
                            <div class="limits-table-row"><span>Memory text max chars</span><input type="number" value="${memoryEmbeddingMaxChars}" min="200" max="100000" onchange="memoryEmbeddingMaxChars = this.value"></div>
                            <div class="limits-table-row"><span>Embedding API URL</span><input type="text" value="${escapeHtml(memoryEmbeddingApiUrl)}" placeholder="https://provider.example/v1" onchange="memoryEmbeddingApiUrl = this.value"></div>
                            <div class="limits-table-row"><span>API key environment variable</span><input type="text" value="${escapeHtml(memoryEmbeddingApiKeyEnv)}" onchange="memoryEmbeddingApiKeyEnv = this.value"></div>
                            <div class="limits-table-row"><span>API timeout seconds</span><input type="number" value="${memoryEmbeddingApiTimeout}" min="10" max="600" onchange="memoryEmbeddingApiTimeout = this.value"></div>
                        </div>
                        <p class="form-help" style="margin-top:8px;">Local BGE-M3 requires the optional sentence-transformers dependency and a model download. API keys are read only from the named environment variable and are never saved in memory settings.</p>
                        <div style="display:flex; gap:8px; margin-top:10px; flex-wrap:wrap;">
                            <button type="button" class="workspace-secondary-action" onclick="buildEmbeddingIndex(false)">${ic('database', 'icn-sm')}Index missing/stale memories</button>
                            <button type="button" class="workspace-secondary-action" onclick="buildEmbeddingIndex(true)">${ic('refresh', 'icn-sm')}Force re-index batch</button>
                        </div>
                    </div>
                </section>
                <div class="form-section memory-maintenance-zone">
                    <div class="form-section-title">Maintenance</div>
                    <div style="display:flex; gap:8px; flex-wrap:wrap;">
                        <button type="button" class="workspace-secondary-action" onclick="runMemoryCapacityMaintenanceNow()">${ic('zap', 'icn-sm')}Check capacity</button>
                        <button type="button" class="workspace-secondary-action" onclick="openColdArchiveViewer()">${ic('archive', 'icn-sm')}Cold archive (${Number(maintenanceState.cold_archive_count || 0)})</button>
                        <button type="button" class="workspace-secondary-action" onclick="openAiMemoryCompressionPlanner()">${ic('zap', 'icn-sm')}AI compression</button>
                        <button type="button" class="workspace-secondary-action" onclick="mergeMemoriesNow()">${ic('branch', 'icn-sm')}Merge now</button>
                    </div>
                </div>
                <div class="ws-footer memory-settings-footer">
                    <span class="ws-footer-meta">Maintenance actions run immediately; everything else saves together.</span>
                    <button type="button" class="workspace-primary-action" onclick="saveMemorySettings()">${ic('save', 'icn-sm')}Save &amp; Close</button>
                </div>
            </div>
        `;
    }

    document.addEventListener('click', event => {
        const tab = event.target.closest('#memory-settings-tabs .ws-tab');
        if (!tab) return;
        const root = tab.closest('.memory-settings-workspace');
        if (!root) return;
        root.querySelectorAll('#memory-settings-tabs .ws-tab').forEach(item => {
            const active = item === tab;
            item.classList.toggle('active', active);
            item.setAttribute('aria-selected', active ? 'true' : 'false');
        });
        root.querySelectorAll('[data-ms-pane]').forEach(pane => {
            pane.hidden = pane.dataset.msPane !== tab.dataset.msPane;
        });
    });

    async function saveMemorySettings() {
        try {
            const response = await fetch('/api/kb/settings', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    auto_save_memories: memoryAutoSave,
                    auto_merge_enabled: memoryAutoMergeEnabled,
                    auto_merge_days: Number(memoryAutoMergeDays),
                    memory_2_enabled: memory2Enabled,
                    relevant_memory_count: Number(memoryRelevantCount),
                    include_memory_metadata: memoryIncludeMetadata,
                    autonomous_memory_enabled: memoryAutonomousEnabled,
                    layered_memory_enabled: memoryLayeredEnabled,
                    api_context_window_tokens: Number(memoryApiContextTokens),
                    local_context_window_tokens: Number(memoryLocalContextTokens),
                    memory_prompt_budget_tokens: Number(memoryPromptBudgetTokens),
                    memory_prompt_budget_ratio: Number(memoryPromptBudgetRatio),
                    memory_item_budget_tokens: Number(memoryItemBudgetTokens),
                    memory_max_chars_core: Number(memoryMaxCharsCore),
                    memory_max_chars_semantic: Number(memoryMaxCharsSemantic),
                    memory_max_chars_episodic: Number(memoryMaxCharsEpisodic),
                    memory_max_chars_knowledge_simple: Number(memoryMaxCharsKnowledgeSimple),
                    memory_max_chars_knowledge_complex: Number(memoryMaxCharsKnowledgeComplex),
                    memory_core_limit: Number(memoryCoreLimit),
                    memory_candidate_pool_size: Number(memoryCandidatePoolSize),
                    memory_active_soft_limit: Number(memoryActiveSoftLimit),
                    memory_archive_soft_limit: Number(memoryArchiveSoftLimit),
                    memory_active_hard_limit: Number(memoryActiveHardLimit),
                    memory_archive_hard_limit: Number(memoryArchiveHardLimit),
                    memory_capacity_warning_ratio: Number(memoryCapacityWarningRatio),
                    memory_capacity_auto_compact_enabled: memoryCapacityAutoCompactEnabled,
                    memory_cold_archive_enabled: memoryColdArchiveEnabled,
                    memory_cold_archive_batch_size: Number(memoryColdArchiveBatchSize),
                    memory_retrieval_threshold: Number(memoryRetrievalThreshold),
                    memory_access_flush_seconds: Number(memoryAccessFlushSeconds),
                    memory_compression_enabled: memoryCompressionEnabled,
                    memory_compression_age_days: Number(memoryCompressionAgeDays),
                    memory_compression_min_group: Number(memoryCompressionMinGroup),
                    memory_compression_group_size: Number(memoryCompressionGroupSize),
                    memory_compression_summary_chars: Number(memoryCompressionSummaryChars),
                    sqlite_memory_enabled: memorySqliteEnabled,
                    sqlite_fts_enabled: memorySqliteFtsEnabled,
                    embedding_enabled: memoryEmbeddingEnabled,
                    embedding_provider: memoryEmbeddingProvider,
                    embedding_model: memoryEmbeddingModel,
                    embedding_device: memoryEmbeddingDevice,
                    embedding_api_url: memoryEmbeddingApiUrl,
                    embedding_api_key_env: memoryEmbeddingApiKeyEnv,
                    embedding_batch_size: Number(memoryEmbeddingBatchSize),
                    embedding_candidate_count: Number(memoryEmbeddingCandidateCount),
                    embedding_min_similarity: Number(memoryEmbeddingMinSimilarity),
                    embedding_index_batch_limit: Number(memoryEmbeddingIndexBatchLimit),
                    embedding_api_timeout_seconds: Number(memoryEmbeddingApiTimeout),
                    embedding_max_chars: Number(memoryEmbeddingMaxChars),
                    embedding_auto_index_new: memoryEmbeddingAutoIndexNew
                })
            });
            if (!response.ok) throw new Error("Settings request failed");
            const data = await response.json();
            closeModal();
            surfaceMemoryMaintenanceResult(data);
        } catch (e) {
            showToast("Failed to save memory settings: " + e, { type: 'error', duration: 3600 });
        }
    }

    async function mergeMemoriesNow() {
        confirmAction("Compact eligible old memories now? Originals will remain archived in knowledge.json.", async () => {

            try {
                const response = await fetch('/api/kb/merge', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({days: Number(memoryCompressionAgeDays || memoryAutoMergeDays)})
                });
                const data = await response.json();
                const aiRecommended = data.all_candidates_at_generation_limit === true
                    || data.maintenance?.ai_compression_recommended === true;
                if (aiRecommended) {
                    const eventId = data.maintenance?.event?.id || '';
                    confirmAction('The remaining safe memories have reached the local compression-generation limit. Review a scoped DeepSeek estimate now? Cancel skips AI compression.', () => {
                        openAiMemoryCompressionPlanner(eventId);
                    }, { onCancel: () => { if (eventId) acknowledgeMemoryMaintenance(eventId); } });
                } else {
                    showToast(data.status === 'success'
                        ? `Compressed ${data.merged_count} memories into ${data.summary_count || 1} bounded summaries. Token usage: ${Number(data.token_usage || 0)}.`
                        : "No eligible memories needed merging.", { type: 'info', duration: 3600 });
                }
            } catch (e) {
                showToast("Memory merge failed: " + e, { type: 'error', duration: 3600 });
            }
    });    }

    async function runMemoryCapacityMaintenanceNow() {
        try {
            const response = await fetch('/api/kb/maintenance/run', {method: 'POST'});
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || 'Capacity check failed');
            const summary = data.status === 'healthy'
                ? 'Memory capacity is healthy. No compaction was needed.'
                : data.event?.message || `Maintenance status: ${data.status}.`;
            if (data.ai_compression_recommended && data.event?.details?.ai_recommended) {
                const eventId = data.event.id || '';
                confirmAction(`${summary}\n\nReview a scoped DeepSeek token estimate now? Cancel skips AI compression.`, () => {
                    openAiMemoryCompressionPlanner(eventId);
                }, { onCancel: () => { if (eventId) acknowledgeMemoryMaintenance(eventId); } });
            } else {
                showToast(`${summary}\n\nToken usage: ${Number(data.token_usage || 0)}.`, { type: 'info', duration: 3600 });
                openMemorySettings();
            }
        } catch (error) {
            showToast(`Memory capacity check failed: ${error.message || error}`, { type: 'error', duration: 3600 });
        }
    }

    async function openColdArchiveViewer() {
        const modal = document.getElementById('memory-modal');
        const body = document.getElementById('modal-body');
        setMemoryModalHeader('Cold archive', 'openMemorySettings()');
        modal.style.display = 'flex';
        renderSurfaceState(body, { kind: 'loading', title: 'Loading cold archive', message: 'Checking recoverable originals…', icon: 'archive' });
        try {
            const response = await fetch('/api/kb/cold-archive?limit=200');
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || 'Unable to load cold archive');
            const memories = Array.isArray(data.memories) ? data.memories : [];
            body.innerHTML = `
                <div class="memory-cold-archive-workspace">
                    <section class="page-intro">
                        <div class="page-intro-copy"><h3 class="page-intro-title">Cold archive</h3><p class="page-intro-sub">Full originals moved out of the active JSON working set by capacity maintenance. Restore only when you need to inspect or reuse one.</p></div>
                        <span class="workspace-status-pill">${Number(data.count || 0).toLocaleString()}</span>
                    </section>
                    <div class="memory-cold-archive-list">
                        ${memories.length ? memories.map(memory => `
                            <article class="memory-cold-archive-item">
                                <div><strong>${escapeHtml(memory.title || 'Untitled')}</strong><span>${escapeHtml(memory.date || '')} · ${escapeHtml(memory.layer || 'memory')} · ${escapeHtml(memory.reason || '')}</span></div>
                                <button type="button" onclick="restoreColdArchiveMemory('${escapeJsString(memory.id)}')">${ic('archive', 'icn-sm')}Restore to Archive</button>
                            </article>`).join('') : '<div class="memory-cold-archive-empty">No memories are currently in cold storage.</div>'}
                    </div>
                </div>`;
        } catch (error) {
            renderSurfaceState(body, {
                title: 'Cold archive could not be opened',
                message: 'The archived originals are safe. Try again when the backend is available.',
                retry: () => openColdArchiveViewer(),
                details: error,
                icon: 'archive'
            });
        }
    }

    async function restoreColdArchiveMemory(memoryId) {
        confirmAction('Restore this full original to the searchable Archive layer? Capacity governance may move another old record to cold storage if the hard limit is still full.', async () => {

            try {
                const response = await fetch('/api/kb/cold-archive/restore', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({id: memoryId})
                });
                const data = await response.json();
                if (!response.ok) throw new Error(data.detail || 'Restore failed');
                surfaceMemoryMaintenanceResult(data);
                await openColdArchiveViewer();
            } catch (error) {
                showToast(`Cold archive restore failed: ${error.message || error}`, { type: 'error', duration: 3600 });
            }
    });    }

    async function rebuildMemoryStore() {
        confirmAction("Rebuild the SQLite search index from current memories? No memories will be deleted.", async () => {

            try {
                const response = await fetch('/api/kb/store/rebuild', {method: 'POST'});
                const data = await response.json();
                if (!response.ok || !['success', 'disabled'].includes(data.status)) {
                    throw new Error(data.error || data.message || 'Rebuild failed');
                }
                showToast(data.status === 'success'
                    ? `SQLite index rebuilt from ${data.count || 0} memories. knowledge.json was preserved.`
                    : 'SQLite memory storage is disabled.', { type: 'info', duration: 3600 });
                openMemorySettings();
            } catch (e) {
                showToast("SQLite index rebuild failed; JSON memory remains available. " + e, { type: 'error', duration: 3600 });
            }
    });    }

    async function buildEmbeddingIndex(force = false) {
        const localWarning = memoryEmbeddingProvider === 'sentence_transformers'
            ? ' The local provider may download and load a large model if it is not already cached.'
            : ' The configured embedding API may incur usage charges.';
        confirmAction(`Build semantic vectors for up to ${Number(memoryEmbeddingIndexBatchLimit)} memories?${localWarning} Existing memories and knowledge.json will not be deleted.`, async () => {

            try {
                const settingsResponse = await fetch('/api/kb/settings', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        sqlite_memory_enabled: true,
                        embedding_enabled: true,
                        embedding_provider: memoryEmbeddingProvider,
                        embedding_model: memoryEmbeddingModel,
                        embedding_device: memoryEmbeddingDevice,
                        embedding_api_url: memoryEmbeddingApiUrl,
                        embedding_api_key_env: memoryEmbeddingApiKeyEnv,
                        embedding_batch_size: Number(memoryEmbeddingBatchSize),
                        embedding_candidate_count: Number(memoryEmbeddingCandidateCount),
                        embedding_min_similarity: Number(memoryEmbeddingMinSimilarity),
                        embedding_index_batch_limit: Number(memoryEmbeddingIndexBatchLimit),
                        embedding_api_timeout_seconds: Number(memoryEmbeddingApiTimeout),
                        embedding_max_chars: Number(memoryEmbeddingMaxChars),
                        embedding_auto_index_new: memoryEmbeddingAutoIndexNew
                    })
                });
                if (!settingsResponse.ok) throw new Error('Unable to save embedding settings');
                memoryEmbeddingEnabled = true;
                const response = await fetch('/api/kb/embeddings/index', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({force, limit: Number(memoryEmbeddingIndexBatchLimit)})
                });
                const data = await response.json();
                if (!response.ok || data.status !== 'success') {
                    throw new Error(data.error || data.last_error || data.status || 'Embedding index failed');
                }
                showToast(`Semantic index updated: ${data.indexed_count || 0} vectors, ${data.remaining_count || 0} remaining.`, { type: 'info', duration: 3600 });
                openMemorySettings();
            } catch (e) {
                showToast('Semantic indexing was not completed. FTS and JSON retrieval remain active. ' + e, { type: 'error', duration: 3600 });
            }
    });    }

    async function toggleMemoryStar(id, btn) {
        const isActive = btn.classList.contains('active');
        try {
            await fetch('/api/kb/update', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({id: id, updates: {is_important: !isActive}})
            });
            btn.classList.toggle('active');
        } catch(e) { showToast("Failed to update", { type: 'error', duration: 3600 }); }
    }

    async function toggleMemoryLock(id, btn) {
        const isActive = btn.classList.contains('active');
        try {
            const response = await fetch('/api/kb/update', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({id: id, updates: {is_locked: !isActive}})
            });
            if (!response.ok) throw new Error('Update rejected');
            await loadMemoryList();
        } catch(e) { showToast("Failed to update: " + (e.message || e), { type: 'error', duration: 3600 }); }
    }

    async function updateMemoryContent(id, newContent) {
        try {
            await fetch('/api/kb/update', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({id: id, updates: {content: newContent}})
            });
        } catch(e) { console.error("Update failed", e); }
    }

    async function deleteMemory(id, btn) {
        try {
            const previewResponse = await fetch('/api/kb/delete', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({id: id})
            });
            const preview = await previewResponse.json();
            if (!previewResponse.ok) throw new Error(preview.detail || preview.message || 'Unable to review deletion');
            const proceed = await new Promise(resolve => {
                showConfirmDialog({
                    title: 'Delete this memory permanently?',
                    message: 'The memory and its child links will be removed from every layer.',
                    impact: memoryDeletionImpactText(preview).split('\n').filter(Boolean),
                    confirmLabel: 'Delete',
                    danger: true,
                    onConfirm: close => { close(); resolve(true); },
                    onCancel: () => resolve(false)
                });
            });
            if (!proceed) return;
            const response = await fetch('/api/kb/delete', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({id, confirmation_token: preview.confirmation_token})
            });
            const data = await response.json();
            if (!response.ok || data.status !== 'success') {
                throw new Error(data.message || data.detail || 'Memory is protected');
            }
            await Promise.all([loadMemoryList(), loadMemoryLayerOverview()]);
        } catch(e) { showToast("Failed to delete: " + (e.message || e), { type: 'error', duration: 3600 }); }
    }

    function memoryDeletionImpactText(preview = {}) {
        const lines = [`${Number(preview.delete_count || preview.memory_count || 0)} linked memory record(s) will be deleted.`];
        const rewired = preview.rewired_links || preview.memory_preview?.rewired_links || [];
        const cleared = Number(preview.cleared_reference_count ?? preview.memory_preview?.cleared_reference_count ?? 0);
        const manual = Number(preview.removed_manual_edge_count ?? preview.memory_preview?.removed_manual_edge_count ?? 0);
        const warnings = preview.warnings || preview.memory_preview?.warnings || [];
        const blocked = preview.blocked || preview.memory_preview?.blocked || [];
        if (rewired.length) lines.push(`${rewired.length} supersession link(s) will be safely reconnected.`);
        if (cleared) lines.push(`${cleared} obsolete relationship reference(s) will be removed.`);
        if (manual) lines.push(`${manual} user-created relationship(s) attached to the deleted memory will be removed.`);
        if (warnings.length) lines.push(`${warnings.length} ambiguous relationship(s) cannot be guessed and will be cleared.`);
        if (blocked.length) lines.push(`${blocked.length} protected memory record(s) will be kept.`);
        if (!rewired.length && !cleared && !manual && !warnings.length) lines.push('No other memory relationship needs to be changed.');
        return lines.join('\n');
    }

    async function restoreMemory(id, btn) {
        try {
            await fetch('/api/kb/update', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({id: id, updates: {is_archived: false}})
            });
            loadMemoryList();
            showToast('Memory restored from archive.');
        } catch(e) {
            showToast('The memory could not be restored.', { type: 'error' });
            console.warn(e);
        }
    }

    async function rebuildMemoryMetadata() {
        showConfirmDialog({
            title: 'Rebuild memory metadata?',
            message: 'Existing content is preserved; layers and retrieval metadata are recomputed.',
            confirmLabel: 'Rebuild',
            onConfirm: async close => {
                close();
                try {
                    const response = await fetch('/api/kb/rebuild_metadata', {method: 'POST'});
                    const data = await response.json();
                    showToast(`Metadata rebuilt for ${data.count || 0} memories.`);
                    loadMemoryList();
                } catch(e) {
                    showToast('Metadata rebuild failed.', { type: 'error' });
                    console.warn(e);
                }
            }
        });
    }

    function collectArchiveGenerationSettings() {
        const apiModelName = document.getElementById('api-model-input')?.value.trim() || '';
        const memoryStrongModelInput = document.getElementById('api-memory-strong-model-input')?.value.trim() || '';
        return {
            model: document.getElementById('model-select')?.value || '',
            api_key: document.getElementById('api-key-input')?.value || '',
            api_url: normalizeApiBaseUrlForChat(document.getElementById('api-url-input')?.value || ''),
            api_model_name: apiModelName,
            memory_strong_model_name: memoryStrongModelInput || apiModelName
        };
    }

    const archiveSelectionState = {
        sessionId: null,
        exchanges: [],
        selectedIds: new Set()
    };

    function closeArchiveSelectionModal() {
        const modal = document.getElementById('archive-selection-modal');
        if (modal) modal.style.display = 'none';
    }

    async function openArchiveSelection(sessionId = null) {
        archiveSelectionState.sessionId = sessionId || null;
        archiveSelectionState.exchanges = [];
        archiveSelectionState.selectedIds = new Set();
        const modal = document.getElementById('archive-selection-modal');
        const list = document.getElementById('archive-selection-list');
        const status = document.getElementById('archive-selection-status');
        if (modal) modal.style.display = 'flex';
        if (list) renderSurfaceState(list, { kind: 'loading', title: 'Loading conversation', message: 'Preparing completed exchanges…', icon: 'archive' });
        if (status) status.textContent = '';
        updateArchiveSelectionCount();
        try {
            const endpoint = sessionId ? '/api/sessions/archive-preview' : '/api/history/archive-preview';
            const response = await fetch(endpoint, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(sessionId ? {session_id: sessionId} : {})
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || data.message || 'Unable to read conversation');
            archiveSelectionState.exchanges = Array.isArray(data.exchanges) ? data.exchanges : [];
            archiveSelectionState.selectedIds = new Set(archiveSelectionState.exchanges.map(item => String(item.id)));
            renderArchiveSelection();
        } catch (error) {
            renderSurfaceState(list, {
                title: 'Conversation could not be loaded',
                message: 'No data was changed. Try loading the completed exchanges again.',
                retry: () => openArchiveSelection(archiveSelectionState.sessionId),
                details: error,
                icon: 'archive'
            });
            if (status) status.textContent = 'Unable to load the conversation. Use Retry to try again.';
        }
    }

    function renderArchiveSelection() {
        const list = document.getElementById('archive-selection-list');
        if (!list) return;
        if (!archiveSelectionState.exchanges.length) {
            list.innerHTML = '<div class="about-you-empty">This conversation has no completed question-and-answer pairs.</div>';
            updateArchiveSelectionCount();
            return;
        }
        list.innerHTML = archiveSelectionState.exchanges.map((exchange, index) => `
            <label class="archive-exchange-card">
                <input type="checkbox" value="${escapeHtml(exchange.id)}" checked onchange="toggleArchiveExchange('${escapeJsString(exchange.id)}', this.checked)">
                <span class="archive-exchange-index">${index + 1}</span>
                <span class="archive-exchange-copy">
                    <span class="archive-exchange-question"><em>Doctor</em>${escapeHtml(exchange.question || '')}</span>
                    <span class="archive-exchange-answer"><em>Saya</em>${escapeHtml(exchange.answer || '')}</span>
                </span>
            </label>
        `).join('');
        updateArchiveSelectionCount();
    }

    function toggleArchiveExchange(exchangeId, checked) {
        if (checked) archiveSelectionState.selectedIds.add(String(exchangeId));
        else archiveSelectionState.selectedIds.delete(String(exchangeId));
        updateArchiveSelectionCount();
    }

    function setAllArchiveExchanges(selected) {
        archiveSelectionState.selectedIds = selected
            ? new Set(archiveSelectionState.exchanges.map(item => String(item.id)))
            : new Set();
        document.querySelectorAll('#archive-selection-list input[type="checkbox"]').forEach(input => {
            input.checked = Boolean(selected);
        });
        updateArchiveSelectionCount();
    }

    function updateArchiveSelectionCount() {
        const count = archiveSelectionState.selectedIds.size;
        const total = archiveSelectionState.exchanges.length;
        const label = document.getElementById('archive-selection-count');
        const submit = document.getElementById('archive-selection-submit');
        if (label) label.textContent = `${count} of ${total} selected`;
        if (submit) submit.disabled = count === 0;
    }

    async function submitArchiveSelection() {
        const selectedIds = archiveSelectionState.exchanges
            .map(item => String(item.id))
            .filter(id => archiveSelectionState.selectedIds.has(id));
        const status = document.getElementById('archive-selection-status');
        const submit = document.getElementById('archive-selection-submit');
        if (!selectedIds.length) {
            if (status) status.textContent = 'Select at least one exchange.';
            return;
        }
        if (submit) submit.disabled = true;
        if (status) status.textContent = `Summarizing ${selectedIds.length} selected exchange${selectedIds.length === 1 ? '' : 's'}...`;
        try {
            saveCurrentApiSettings();
            const isSavedSession = Boolean(archiveSelectionState.sessionId);
            const endpoint = isSavedSession ? '/api/sessions/archive' : '/api/history/archive';
            const response = await fetch(endpoint, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    ...collectArchiveGenerationSettings(),
                    ...(isSavedSession ? {session_id: archiveSelectionState.sessionId} : {}),
                    selected_exchange_ids: selectedIds
                })
            });
            const data = await response.json();
            if (!response.ok || data.status !== 'success') {
                throw new Error(data.detail || data.message || data.status || 'Conversation summary failed');
            }
            closeArchiveSelectionModal();
            showToast(`Saved ${Number(data.selected_exchange_count || selectedIds.length)} selected exchange(s) as an active Episodic memory.\n\n${data.summary || data.title || ''}`, { type: 'info', duration: 3600 });
        } catch (error) {
            if (status) status.textContent = error.message || String(error);
        } finally {
            updateArchiveSelectionCount();
        }
    }

    function archiveCurrentSession() {
        openArchiveSelection(null);
    }

    function triggerUpload() {
        const input = document.getElementById('file-upload');
        if (!input) return;
        input.value = '';
        input.click();
    }

    function clearAttachment() {
        pendingAttachment = null;
        document.getElementById('attachment-preview').style.display = 'none';
        document.getElementById('preview-img').src = '';
        document.getElementById('preview-text').innerText = '';
        document.getElementById('file-upload').value = '';
    }

    async function uploadFile(input) {
        const file = input instanceof File ? input : input?.files?.[0];
        if (file) {
            const formData = new FormData();
            formData.append("file", file);
            formData.append("model", document.getElementById('model-select')?.value || '');

            // const chatContent = document.getElementById('chat-content');
            // const loadingMsg = document.createElement('div');
            // loadingMsg.className = 'message user-message';
            // loadingMsg.innerText = "[Uploading...]";
            // chatContent.appendChild(loadingMsg);
            // chatContent.scrollTop = chatContent.scrollHeight;
            
            // Show uploading state in preview area
            document.getElementById('attachment-preview').style.display = 'flex';
            document.getElementById('preview-text').innerText = `Uploading ${file.name}...`;

            try {
                const response = await fetch('/api/upload_file', {
                    method: 'POST',
                    body: formData
                });
                const data = await response.json();
                
                // loadingMsg.remove();
                
                if (data.status === 'success') {
                    pendingAttachment = data;
                    
                    // Show preview
                    const previewArea = document.getElementById('attachment-preview');
                    const previewImg = document.getElementById('preview-img');
                    const previewText = document.getElementById('preview-text');
                    
                    previewArea.style.display = 'flex';
                    
                    if (data.type === 'image') {
                        previewImg.src = data.url;
                        previewImg.style.display = 'block';
                        previewText.innerText = 'Image attached';
                    } else {
                        previewImg.style.display = 'none';
                        previewText.innerText = `File attached: ${data.filename}`;
                    }
                    
                    // Focus input for user to type text
                    document.getElementById('user-input').focus();
                    
                } else {
                    showToast("Upload failed: " + (data.message || data.detail || "Unknown error"), { type: 'error', duration: 3600 });
                    clearAttachment();
                }
            } catch (e) {
                // loadingMsg.innerText = "Error uploading file.";
                console.error(e);
                showToast("Error uploading file", { type: 'error', duration: 3600 });
                clearAttachment();
            }
        }
    }

    function initializeFileDropUpload() {
        const overlay = document.getElementById('file-drop-overlay');
        if (!overlay || document.body.dataset.fileDropReady === 'true') return;
        document.body.dataset.fileDropReady = 'true';
        let dragDepth = 0;

        const hasFiles = event => Array.from(event.dataTransfer?.types || []).includes('Files');
        const hideOverlay = () => {
            dragDepth = 0;
            overlay.classList.remove('visible');
            overlay.setAttribute('aria-hidden', 'true');
        };

        document.addEventListener('dragenter', event => {
            if (!hasFiles(event)) return;
            event.preventDefault();
            dragDepth += 1;
            overlay.classList.add('visible');
            overlay.setAttribute('aria-hidden', 'false');
        });
        document.addEventListener('dragover', event => {
            if (!hasFiles(event)) return;
            event.preventDefault();
            event.dataTransfer.dropEffect = 'copy';
        });
        document.addEventListener('dragleave', event => {
            if (!hasFiles(event)) return;
            dragDepth = Math.max(0, dragDepth - 1);
            if (dragDepth === 0) hideOverlay();
        });
        document.addEventListener('drop', event => {
            if (!hasFiles(event)) return;
            event.preventDefault();
            const files = Array.from(event.dataTransfer?.files || []);
            hideOverlay();
            if (!files.length) return;
            if (files.length > 1) {
                showToast('One attachment can be sent per message. The first dropped file will be attached.', { type: 'info', duration: 3600 });
            }
            uploadFile(files[0]);
        });
        window.addEventListener('blur', hideOverlay);
    }

    // Global variables
    let pendingAttachment = null;
    let motionFiles = [];
    let motionEntries = [];
    let currentMotionIndex = 0;
    let isLooping = true;
    let currentMesh = null;
    let modelLoadGeneration = 0;
    let animationHelper = null;
    let animationBindingPose = null;
    let activeShoeDiagnostics = null;
    let lastShoeDiagnosticSampleAt = 0;
    let genogeTieMode = 'stable';
    let mmdPhysicsStepper = null;
    let activeTiePhysicsDiagnostics = null;
    let scene, camera, renderer, controls, loader;
    let mixer = null;
    let initialPosition = null;
    let initialFootPosition = null;
    
    // Physics Reset Variables
    let lastPhysicsResetTime = Date.now();
    let physicsEnableTimer = null;
    let lastPhysicsVisualSample = null;
    // [MODIFIED] Physics tunables are now per-model (let, not const) so a single
    // misbehaving model can be patched without affecting the others. Defaults below
    // mirror the original global values; applyCurrentModelPhysics() overrides them.
    let PHYSICS_RESET_INTERVAL = 30000; // Reset physics every 30 seconds to prevent vibration accumulation
    let lastPhysicsPanicTime = 0;
    let abnormalPhysicsFrames = 0;
    let PHYSICS_PANIC_COOLDOWN = 1200;
    let PHYSICS_MAX_ROOT_DRIFT = 8.0;
    let PHYSICS_MAX_FOOT_DRIFT = 14.0;
    let PHYSICS_MAX_FRAME_Y_DELTA = 3.5;
    let PHYSICS_MAX_VISUAL_CENTER_JUMP = 5.0;
    let PHYSICS_MAX_VISUAL_SIZE_JUMP = 8.0;
    let PHYSICS_RECOVERY_MOTION_DELAY_MS = 120;
    let PHYSICS_RECOVERY_CROSSFADE_SECONDS = 0.25;
    
    const vmdCache = new Map();
    
    let motionSpeed = 1.0;
    const clock = new THREE.Clock();

    let motionCategories = {};
    let currentMotionState = 'normal_idle';
    let isWaitingForResponse = false;
    let lastInteractionTime = Date.now();
    let motionTimer = null;
    let motionPlayToken = 0;
    let motionLoadToken = 0;
    let motionReturnTimer = null;
    let activeMotionFinishedHandler = null;
    let activeMotionIndex = -1;
    let activeMotionEntry = null;
    let activeMotionStartedAt = 0;
    let activeMotionDurationSeconds = 0;
    let activeMotionAction = null;
    let loadingMotionIndex = -1;
    let loadingMotionEntry = null;
    let pendingMotionIndex = null;
    let pendingMotionPriority = -Infinity;
    let pendingMotionOptions = null;
    let openingMotionPlayedGeneration = -1;
    let lastNightMotionAttemptAt = 0;
    let lastModelInteractionAt = 0;
    let lastModelInteractionMotionKey = '';
    let modelInteractionLockActive = false;
    let modelInteractionLockTimer = null;
    let modelInteractionReturnTimer = null;
    let modelInteractionReturningToIdle = false;
    let modelInteractionMotionIndex = -1;
    const modelInteractionMotionTimes = new Map();
    const NIGHT_MOTION_BEFORE_MIDNIGHT_INTERVAL_MS = 8 * 60 * 1000;
    const NIGHT_MOTION_AFTER_MIDNIGHT_INTERVAL_MS = 5 * 60 * 1000;
    const NIGHT_MOTION_PROBABILITY = 0.3;
    const SLEEPY_TOUCH_SURPRISED_PROBABILITY = 0.1;
    const MODEL_INTERACTION_DEBOUNCE_MS = 800;
    const MODEL_INTERACTION_LOCK_MS = 3000;
    const MODEL_INTERACTION_RETURN_FADE_SECONDS = 0.65;
    const MODEL_INTERACTION_MORPH_SETTLE_SECONDS = 0.4;
    const SOFT_Z_CORRECTION_MOTION_INTERVAL = 10;
    const MOTION_CROSSFADE_SECONDS = 0.5;
    const MAX_BONE_Y_OFFSET = 5;
    let softPositionMotionCount = 0;
    let correctSoftZThisMotion = false;
    let currentHistoryLength = 0; // Track backend history length locally
    let currentSessionId = null;
    let dailyBriefState = null;
    let videoRecommendationState = null;
    let videoRecommendationAutoOpened = false;
    // directTranslateEnabled removed

    // [ADDED] Lighting Global Variables for Dynamic Adjustment
    let mainAmbientLight, mainDirLight, mainTopLight;
    // [ADDED] Advanced lights
    let rimLight, fillLight, hemiLight, pointLight;
    let initialCameraDistance = 0;
    const BASE_AMBIENT = 0.7;
    const BASE_DIR = 0.8;
    const BASE_TOP = 0.5;
    const BASE_RIM = 0.6;
    const BASE_FILL = 0.3;
    const BASE_HEMI = 0.4;

    // [ADDED] Selectable 3D Models (served from /MMD/ ...)
    // Each model carries its OWN placement + physics block (built via
    // defaultModelConfig()) so future per-model fixes never overwrite the others.
    function defaultModelConfig() {
        return {
            scale: 1.2,
            posY: -13,
            physics: {
                resetInterval: 30000,
                panicCooldown: 1200,
                maxRootDrift: 8.0,
                maxFootDrift: 14.0,
                maxFrameYDelta: 3.5,
                maxVisualCenterJump: 5.0,
                maxVisualSizeJump: 8.0,
                recoveryMotionDelayMs: 120,
                recoveryCrossfadeSeconds: 0.25,
                detectVisualJitter: false,
            },
            // null keeps the original, known-good TDA path byte-for-byte equivalent.
            // Only models that need a repair opt into a targeted override below.
            rigidBodyTuning: null,
            ikTuning: null,
            geometryTuning: null,
            renderTuning: null,
            // Default lighting per model (overridden by user "Default" button)
            defaultLight: {
                ambient: 1, dir: 1, top: 1, ambientColor: '#aaaaaa', dirColor: '#ffffff', topColor: '#ffffff', bg: 'transparent',
                advEnabled: true,
                rimColor: '#4488ff', rimIntensity: 0.6, fillColor: '#fff5e0', fillIntensity: 0.3,
                hemiSky: '#88aaff', hemiGround: '#504030', hemiIntensity: 0.4,
                pointColor: '#ffcc88', pointIntensity: 0,
                fogColor: '#000000', fogDensity: 0.035,
            },
        };
    }

    const MMD_MODELS = [
        { id: 'tda_miku', name: 'TDA Default Miku (Xoriu Ver.)', url: '/MMD/TDA Default Miku (Xoriu Ver.)/TDA Default Miku (Xoriu Ver).pmx', ...defaultModelConfig() },
        {
            id: 'yyb_miku', name: 'YYB Hatsune Miku RE-MiX 10th',
            url: '/MMD/YYB Hatsune Miku RE-MiX 10th - Stylized Edit (1.0 Release)/YYB Hatsune Miku RE-MiX 10th.pmx',
            ...defaultModelConfig(),
            physics: { ...defaultModelConfig().physics, resetInterval: 0 },
            // Keep the skirt joint chains, but remove all collision impulses for YYB only.
            rigidBodyTuning: {
                profile: 'yyb-collision-free-skirt',
                preserveAuthoredParameters: true,
                skirt: {
                    positionDamping: 0.98,
                    rotationDamping: 0.98,
                    disableAllCollisions: true,
                },
            },
        },
        {
            id: 'genoge_miku', name: 'げのげ式初音ミク',
            url: '/MMD/げのげ式初音ミク/げのげ式初音ミク.pmx',
            ...defaultModelConfig(),
            physics: { ...defaultModelConfig().physics, resetInterval: 0 },
            // Keep the author's high damping. Only the unstable areas are isolated.
            rigidBodyTuning: {
                profile: 'genoge-stable-cloth',
                preserveAuthoredParameters: true,
                skirt: { disableAllCollisions: true },
                breast: { forceKinematic: true, disableAllCollisions: true },
                tie: { enabled: true },
            },
            // A subset of shoe-tip vertices is bound to the normal toe chain while
            // the rest of this model's legs use its D/deformation chain. Once those
            // chains diverge, the mixed skinning pins part of the shoe in place and
            // stretches the remaining vertices. Keep the shoe on one chain.
            geometryTuning: {
                skinBoneRemap: [
                    { source: '左つま先', target: '左足先EX', side: 'left' },
                    { source: '右つま先', target: '右足先EX', side: 'right' },
                ],
            },
            renderTuning: { disableFrustumCulling: true },
        },
    ];
    let currentModelId = MMD_MODELS[0].id;
    let currentModelUrl = MMD_MODELS[0].url;
    let currentModelConfig = MMD_MODELS[0];

    // [ADDED] User-adjustable lighting state (persisted)
    const MMD_SETTINGS_KEY = 'saya_mmd_3d_settings';
    const MMD_LIGHT_DEFAULTS_KEY = 'saya_mmd_light_defaults';
    let mmdLight = {
        ambient: 1, dir: 1, top: 1, ambientColor: '#aaaaaa', dirColor: '#ffffff', topColor: '#ffffff', bg: 'transparent',
        // Advanced lighting (coexists with the 3 basic lights above)
        advEnabled: true,
        rimColor: '#4488ff', rimIntensity: 0.6,
        fillColor: '#fff5e0', fillIntensity: 0.3,
        hemiSky: '#88aaff', hemiGround: '#504030', hemiIntensity: 0.4,
        pointColor: '#ffcc88', pointIntensity: 0,
        fogColor: '#000000', fogDensity: 0.035,
        skyboxEnabled: false, skyboxUrl: '', skyboxRotation: 0, skyboxEnv: false,
    };

    document.addEventListener('DOMContentLoaded', () => {
        // [ADDED] Initialize Draggable & Resizable Logic
        initDraggableModel();
        initializeFileDropUpload();
    });

    // --- DRAGGABLE & RESIZABLE LOGIC ---
    function initDraggableModel() {
        const container = document.getElementById('draggable-model');
        if (!container) return;

        function chatBounds() {
            const panel = document.querySelector('.model-panel');
            const header = document.querySelector('.panel-header');
            const sidebarWidth = panel && !panel.classList.contains('collapsed')
                ? Math.round(panel.getBoundingClientRect().width)
                : 0;
            const headerBottom = header ? Math.round(header.getBoundingClientRect().bottom) : 52;
            const width = container.offsetWidth || 300;
            const height = container.offsetHeight || 350;
            return {
                minLeft: sidebarWidth,
                minTop: headerBottom,
                maxLeft: Math.max(sidebarWidth, window.innerWidth - width - 8),
                maxTop: Math.max(headerBottom, window.innerHeight - height - 8),
            };
        }

        function clampMmdPosition() {
            const bounds = chatBounds();
            const currentLeft = Number.parseFloat(container.style.left) || container.offsetLeft || bounds.minLeft;
            const currentTop = Number.parseFloat(container.style.top) || container.offsetTop || bounds.minTop;
            container.style.left = `${Math.min(bounds.maxLeft, Math.max(bounds.minLeft, currentLeft))}px`;
            container.style.top = `${Math.min(bounds.maxTop, Math.max(bounds.minTop, currentTop))}px`;
        }

        // Dock the initial frame to the chat boundary and keep it there if the
        // sidebar is toggled or the window is resized.
        clampMmdPosition();
        window.addEventListener('resize', clampMmdPosition);
        
        // [ADDED] Right-click to Reload Model
        container.addEventListener('contextmenu', (e) => {
            // Ignore right-clicks that originate from the settings panel or trigger
            if (e.target.closest('.mmd-settings-panel') || e.target.closest('.mmd-settings-trigger')) return;
            e.preventDefault();
            confirmAction("Reload model?", () => {
                reloadModel();
            });
        });

        const dragBar = container.querySelector('.drag-bar');
        const handles = container.querySelectorAll('.resize-handle');
        
        // --- DRAGGING ---
        let isDragging = false;
        let dragOffsetX = 0;
        let dragOffsetY = 0;

        dragBar.addEventListener('mousedown', (e) => {
            isDragging = true;
            dragOffsetX = e.clientX - container.offsetLeft;
            dragOffsetY = e.clientY - container.offsetTop;
            document.body.style.userSelect = 'none'; // Prevent selection
            document.addEventListener('mousemove', onDrag);
            document.addEventListener('mouseup', stopDrag);
        });

        function onDrag(e) {
            if (!isDragging) return;
            container.style.left = (e.clientX - dragOffsetX) + 'px';
            container.style.top = (e.clientY - dragOffsetY) + 'px';
            clampMmdPosition();
        }

        function stopDrag() {
            isDragging = false;
            document.body.style.userSelect = '';
            document.removeEventListener('mousemove', onDrag);
            document.removeEventListener('mouseup', stopDrag);
        }

        // --- RESIZING ---
        let isResizing = false;
        let startX, startY, startWidth, startHeight, startTop, startLeft;
        let aspectRatio = 300 / 350; // Initial aspect ratio
        let currentHandle = null;

        handles.forEach(handle => {
            handle.addEventListener('mousedown', (e) => {
                e.stopPropagation(); // Prevent drag
                isResizing = true;
                currentHandle = handle;
                startX = e.clientX;
                startY = e.clientY;
                startWidth = container.offsetWidth;
                startHeight = container.offsetHeight;
                startTop = container.offsetTop;
                startLeft = container.offsetLeft;
                aspectRatio = startWidth / startHeight; // Lock current ratio

                document.body.style.userSelect = 'none';
                document.addEventListener('mousemove', onResize);
                document.addEventListener('mouseup', stopResize);
            });
        });

        function onResize(e) {
            if (!isResizing) return;
            
            const dx = e.clientX - startX;
            // Proportional scaling: maintain aspect ratio
            // We'll base scaling on width change primarily, or largest delta
            
            let newWidth = startWidth;
            let newHeight = startHeight;
            let newTop = startTop;
            let newLeft = startLeft;

            // Simple corner logic with aspect ratio constraint
            // For simplicity, we assume dragging mostly changes width, height follows
            
            if (currentHandle.classList.contains('se')) {
                // Bottom-Right
                newWidth = startWidth + dx;
                newHeight = newWidth / aspectRatio;
            } else if (currentHandle.classList.contains('sw')) {
                // Bottom-Left
                newWidth = startWidth - dx;
                newHeight = newWidth / aspectRatio;
                newLeft = startLeft + (startWidth - newWidth);
            } else if (currentHandle.classList.contains('ne')) {
                // Top-Right
                newWidth = startWidth + dx;
                newHeight = newWidth / aspectRatio;
                newTop = startTop + (startHeight - newHeight);
            } else if (currentHandle.classList.contains('nw')) {
                // Top-Left
                newWidth = startWidth - dx;
                newHeight = newWidth / aspectRatio;
                newLeft = startLeft + (startWidth - newWidth);
                newTop = startTop + (startHeight - newHeight);
            }

            // Keep the frame inside the chat region and prevent it from
            // expanding back over the sidebar or beyond the viewport.
            const bounds = chatBounds();
            const minWidth = 160;
            const maxWidth = Math.max(minWidth, window.innerWidth - bounds.minLeft - 8);
            newWidth = Math.min(maxWidth, Math.max(minWidth, newWidth));
            newHeight = newWidth / aspectRatio;

            if (newWidth > 100) {
                container.style.width = newWidth + 'px';
                container.style.height = newHeight + 'px';
                if (newLeft !== startLeft) container.style.left = newLeft + 'px';
                if (newTop !== startTop) container.style.top = newTop + 'px';
                clampMmdPosition();
                
                // Update 3D Renderer
                if (renderer && camera) {
                    camera.aspect = newWidth / newHeight;
                    camera.updateProjectionMatrix();
                    renderer.setSize(newWidth, newHeight);
                }
            }
        }

        function stopResize() {
            isResizing = false;
            currentHandle = null;
            document.body.style.userSelect = '';
            document.removeEventListener('mousemove', onResize);
            document.removeEventListener('mouseup', stopResize);
        }
    }

    function isNightTime(date = new Date()) {
        const hour = date.getHours();
        return hour >= 21 || hour < 6;
    }

    function getNightMotionSettings(date = new Date()) {
        const beforeMidnight = date.getHours() >= 21;
        return {
            attemptIntervalMs: beforeMidnight
                ? NIGHT_MOTION_BEFORE_MIDNIGHT_INTERVAL_MS
                : NIGHT_MOTION_AFTER_MIDNIGHT_INTERVAL_MS,
            sleepyProbability: beforeMidnight ? 1 / 15 : 1 / 10
        };
    }

    function selectSleepyEyesLoopRepetitions() {
        const randomValue = Math.random();
        if (randomValue < 4 / 5) return 4;
        if (randomValue < 9 / 10) return 8;
        return Infinity;
    }

    async function fetchMotionConfig() {
        try {
            const response = await fetch('/get_motion_config');
            if (!response.ok) throw new Error("Failed to fetch motion config");
            motionCategories = await response.json();
            console.log("Motion categories loaded:", motionCategories);
        } catch (e) {
            console.error("Error loading motion config:", e);
        }
    }

    function selectMotionFromCategory(category, options = {}) {
        let motions = motionCategories[category];
        if (!motions || motions.length === 0) return null;
        if (options.allowReaction === false) {
            motions = motions.filter(motion => {
                const kind = String(motion.kind || '').toLowerCase();
                const playback = String(motion.playback || 'loop').toLowerCase();
                return kind !== 'reaction' && playback !== 'once' && playback !== 'once_then_idle';
            });
            if (motions.length === 0) return null;
        }

        const totalWeight = motions.reduce((sum, motion) => sum + Number(motion.weight || 0), 0);
        if (totalWeight <= 0) return null;
        let random = Math.random() * totalWeight;

        for (const motion of motions) {
            random -= Number(motion.weight || 0);
            if (random <= 0) {
                return motion;
            }
        }
        return motions[0];
    }

    const ANGRY_MODEL_INTERACTION_POOL = [
        { id: 'coquettish_you_are_bad', weight: 1 },
        { id: 'jealous_pout', weight: 1 },
        { id: 'pout_turn_head_hmph', weight: 1 },
        { id: 'teasing_are_you_picking_a_fight', weight: 1 }
    ];
    const MODEL_INTERACTION_MOTION_POOLS = {
        head: [
            { id: 'shy_blush', weight: 45 },
            { id: 'hold_hands_119', weight: 45 },
            { id: 'Fairy_idle_362', weight: 10 }
        ],
        face: [
            { id: 'shy_blush', weight: 35 },
            { id: 'confused_cheek_scratch', weight: 35 },
            { id: 'hold_hands_119', weight: 20 },
            { id: 'Fairy_idle_362', weight: 10 }
        ],
        upper_body: [
            { motions: ANGRY_MODEL_INTERACTION_POOL, weight: 60, label: 'angry' },
            { id: 'shy_blush', weight: 20 },
            { id: 'Fairy_idle_362', weight: 20 }
        ],
        lower_body: [
            { motions: ANGRY_MODEL_INTERACTION_POOL, weight: 40, label: 'angry' },
            { id: 'shy_blush', weight: 20 },
            { id: 'Fairy_idle_362', weight: 10 },
            { id: 'cautious_smile', weight: 30 }
        ]
    };
    const NATURAL_COMPLETION_MODEL_INTERACTION_IDS = new Set([
        'fairy_idle_362',
        'pout_turn_head_hmph'
    ]);
    const MOTION_END_HOLD_SECONDS_BY_ID = new Map([
        ['pout_turn_head_hmph', 1]
    ]);

    function motionEntryKey(entry) {
        return String(entry?.id || entry?.file || entry?.name || entry?.url || '');
    }

    function modelInteractionUsesNaturalCompletion(entry, index = -1) {
        const entryId = normalizeMotionLookupValue(entry?.id || entry?.name || '')
            .split('/')
            .pop()
            .replace(/\.vmd$/, '');
        const fileId = getMotionEntryFileName(entry, index).replace(/\.vmd$/, '');
        return (
            NATURAL_COMPLETION_MODEL_INTERACTION_IDS.has(entryId) ||
            NATURAL_COMPLETION_MODEL_INTERACTION_IDS.has(fileId)
        );
    }

    function getMotionEndHoldSeconds(entry, index = -1) {
        const entryId = normalizeMotionLookupValue(entry?.id || entry?.name || '')
            .split('/')
            .pop()
            .replace(/\.vmd$/, '');
        const fileId = getMotionEntryFileName(entry, index).replace(/\.vmd$/, '');
        const builtInHoldSeconds = Math.max(
            Number(MOTION_END_HOLD_SECONDS_BY_ID.get(entryId) || 0),
            Number(MOTION_END_HOLD_SECONDS_BY_ID.get(fileId) || 0)
        );
        const configuredHoldSeconds = Number(entry?.end_hold_seconds);
        return Math.max(
            builtInHoldSeconds,
            Number.isFinite(configuredHoldSeconds) ? configuredHoldSeconds : 0
        );
    }

    function selectWeightedModelInteractionCandidate(candidates) {
        if (!candidates.length) return null;
        const totalWeight = candidates.reduce(
            (sum, candidate) => sum + Number(candidate.weight || 0),
            0
        );
        if (totalWeight <= 0) return null;

        let randomValue = Math.random() * totalWeight;
        for (const candidate of candidates) {
            randomValue -= Number(candidate.weight || 0);
            if (randomValue <= 0) return candidate;
        }
        return candidates[0];
    }

    function resolveModelInteractionCandidate(candidate) {
        if (!candidate?.id) return null;
        const index = findMotionByPayload({
            motion_id: candidate.id,
            motion_file: `${candidate.id}.vmd`,
            motion_name: candidate.id
        });
        if (index === -1) return null;
        const sourceEntry = motionEntries[index] || {};
        const entry = {
            ...sourceEntry,
            id: sourceEntry.id || candidate.id,
            file: sourceEntry.file || `${candidate.id}.vmd`,
            kind: sourceEntry.kind || 'reaction',
            playback: sourceEntry.playback || 'once_then_idle',
            return_category: sourceEntry.return_category || 'normal_idle'
        };
        motionEntries[index] = entry;
        return { index, entry };
    }

    function selectModelInteractionMotion(zone) {
        const pool = MODEL_INTERACTION_MOTION_POOLS[zone] || MODEL_INTERACTION_MOTION_POOLS.upper_body;
        const availableChoices = pool.map(candidate => {
            if (Array.isArray(candidate.motions)) {
                const motions = candidate.motions
                    .map(inner => ({ ...inner, resolved: resolveModelInteractionCandidate(inner) }))
                    .filter(inner => inner.resolved);
                return motions.length ? { ...candidate, motions } : null;
            }
            const resolved = resolveModelInteractionCandidate(candidate);
            return resolved ? { ...candidate, resolved } : null;
        }).filter(Boolean);

        const selectedChoice = selectWeightedModelInteractionCandidate(availableChoices);
        if (!selectedChoice) return null;
        if (Array.isArray(selectedChoice.motions)) {
            const selectedMotion = selectWeightedModelInteractionCandidate(selectedChoice.motions);
            return selectedMotion
                ? { ...selectedMotion.resolved, tone: selectedChoice.label || 'pool' }
                : null;
        }
        return { ...selectedChoice.resolved, tone: selectedChoice.label || 'direct' };
    }

    function isSleepyEyesClosedMotionActive() {
        return (
            activeMotionIndex !== -1 &&
            getMotionEntryFileName(activeMotionEntry, activeMotionIndex) === 'sleepy_eyes_closed.vmd'
        );
    }

    function selectSleepyTouchSurprisedMotion() {
        const availableMotions = (motionCategories.surprised || []).map(motion => {
            const index = findMotionByPayload(motion);
            return index === -1 ? null : { ...motion, index };
        }).filter(Boolean);
        const selectedMotion = selectWeightedModelInteractionCandidate(availableMotions);
        if (!selectedMotion) return null;

        const index = selectedMotion.index;
        const sourceEntry = motionEntries[index] || {};
        const entry = {
            ...sourceEntry,
            id: sourceEntry.id || selectedMotion.id || selectedMotion.motion_id,
            file: sourceEntry.file || selectedMotion.file || selectedMotion.motion_file,
            kind: 'reaction',
            playback: 'once_then_idle',
            return_category: sourceEntry.return_category || 'normal_idle'
        };
        motionEntries[index] = entry;
        return { index, entry, tone: 'sleepy_surprised' };
    }

    function classifyModelInteraction(intersectionPoint) {
        const bounds = new THREE.Box3().setFromObject(currentMesh);
        const size = bounds.getSize(new THREE.Vector3());
        if (!Number.isFinite(size.y) || size.y <= 0.001) return 'upper_body';

        const normalizedY = (intersectionPoint.y - bounds.min.y) / size.y;

        if (normalizedY >= 0.9) return 'head';
        if (normalizedY >= 0.78) return 'face';
        if (normalizedY >= 0.38) return 'upper_body';
        return 'lower_body';
    }

    function triggerModelInteraction(zone) {
        const now = Date.now();
        if (modelInteractionLockActive) {
            console.log("Model interaction ignored: the current touch reaction is locked.");
            return false;
        }
        if (!currentMesh || now - lastModelInteractionAt < MODEL_INTERACTION_DEBOUNCE_MS) return false;

        const sleepySurprised = (
            isSleepyEyesClosedMotionActive() &&
            Math.random() < SLEEPY_TOUCH_SURPRISED_PROBABILITY
        );
        const selected = (
            sleepySurprised
                ? selectSleepyTouchSurprisedMotion()
                : null
        ) || selectModelInteractionMotion(zone);
        if (!selected) return false;

        const key = motionEntryKey(selected.entry);
        lastModelInteractionAt = now;
        lastInteractionTime = now;
        lastModelInteractionMotionKey = key;
        modelInteractionMotionTimes.set(key, now);
        currentMotionState = 'model_interaction';
        currentMotionIndex = selected.index;
        beginModelInteractionLock(selected.index);
        const waitForNaturalCompletion = modelInteractionUsesNaturalCompletion(
            selected.entry,
            selected.index
        );
        loadAndPlayMotion(currentMesh, {
            forceRestart: true,
            interruptOneShot: true,
            isModelInteraction: true,
            waitForNaturalCompletion,
            playbackOverride: waitForNaturalCompletion ? 'once_then_idle' : '',
            priority: 100,
            resetPosition: false
        });
        console.log(
            `Model interaction (${zone}, ${selected.tone}): ${selected.entry.file || selected.entry.id}`
        );
        return true;
    }

    function initModelPartInteractions() {
        const canvas = renderer?.domElement;
        if (!canvas || canvas.dataset.modelInteractionReady === 'true') return;
        canvas.dataset.modelInteractionReady = 'true';

        const raycaster = new THREE.Raycaster();
        const pointer = new THREE.Vector2();
        let pointerStart = null;

        canvas.addEventListener('pointerdown', event => {
            if (event.button !== 0) return;
            pointerStart = {
                id: event.pointerId,
                x: event.clientX,
                y: event.clientY,
                at: Date.now()
            };
        });

        canvas.addEventListener('pointercancel', () => {
            pointerStart = null;
        });

        canvas.addEventListener('pointerup', event => {
            const start = pointerStart;
            pointerStart = null;
            if (!start || start.id !== event.pointerId || event.button !== 0 || !currentMesh) return;

            const distance = Math.hypot(event.clientX - start.x, event.clientY - start.y);
            if (distance > 7 || Date.now() - start.at > 650) return;

            const rect = canvas.getBoundingClientRect();
            if (rect.width <= 0 || rect.height <= 0) return;
            pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
            pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
            raycaster.setFromCamera(pointer, camera);

            const intersections = raycaster.intersectObject(currentMesh, true);
            if (!intersections.length) return;
            triggerModelInteraction(classifyModelInteraction(intersections[0].point));
        });
    }

    function normalizeMotionLookupValue(value) {
        try {
            return decodeURIComponent(String(value || '').replace(/\\/g, '/')).toLowerCase();
        } catch (e) {
            return String(value || '').replace(/\\/g, '/').toLowerCase();
        }
    }

    function findMotionByPayload(payload) {
        const motionUrl = normalizeMotionLookupValue(payload?.motion_url || payload?.url || '');
        const motionFile = normalizeMotionLookupValue(payload?.motion_file || payload?.file || '');
        const motionId = normalizeMotionLookupValue(payload?.motion_id || payload?.id || '');
        const motionName = normalizeMotionLookupValue(payload?.motion_name || payload?.name || payload || '');

        return motionEntries.findIndex((entry, index) => {
            const entryUrl = normalizeMotionLookupValue(entry?.url || motionFiles[index]);
            const entryFile = normalizeMotionLookupValue(entry?.file || '');
            const entryId = normalizeMotionLookupValue(entry?.id || '');
            const entryName = normalizeMotionLookupValue(entry?.name || '');

            if (motionUrl && entryUrl === motionUrl) return true;
            if (motionFile && entryFile === motionFile) return true;
            if (motionFile && entryUrl.endsWith('/' + motionFile)) return true;
            if (motionId && entryId === motionId) return true;
            if (motionName && entryName === motionName) return true;
            if (motionName && entryUrl.includes(motionName)) return true;
            return false;
        });
    }

    function findMotionByName(motionName) {
        return findMotionByPayload({ motion_name: motionName });
    }

    function getMotionEntryFileName(entry, index = -1) {
        const rawValue = entry?.file || entry?.name || motionFiles[index] || entry?.url || '';
        const normalized = normalizeMotionLookupValue(rawValue);
        return normalized.split('/').pop() || '';
    }

    function findClassicIdleMotionIndex() {
        const preferred = findMotionByPayload({
            motion_id: 'Idle_normal_187',
            motion_file: 'Idle_normal_187.vmd',
            motion_name: 'Idle_normal_187'
        });
        if (preferred !== -1) return preferred;

        const neutral = findMotionByPayload({
            motion_id: 'a_idle_neutral_loop_88',
            motion_file: 'a_idle_neutral_loop_88.vmd',
            motion_name: 'a_idle_neutral_loop_88'
        });
        if (neutral !== -1) return neutral;

        const selectedMotion = selectMotionFromCategory('normal_idle', { allowReaction: false });
        const selectedIndex = selectedMotion ? findMotionByPayload(selectedMotion) : -1;
        if (selectedIndex !== -1) return selectedIndex;

        return motionEntries.findIndex(entry => {
            const kind = String(entry?.kind || '').toLowerCase();
            const playback = String(entry?.playback || 'loop').toLowerCase();
            return kind !== 'reaction' && playback === 'loop';
        });
    }

    function playClassicIdleMotion(resetPosition = false) {
        const idleIndex = findClassicIdleMotionIndex();
        if (idleIndex === -1 || !motionFiles[idleIndex]) {
            currentMotionState = 'normal_idle';
            playIntelligentMotion({
                forceNormal: true,
                allowReaction: false,
                resetPosition
            });
            return false;
        }

        currentMotionState = 'normal_idle';
        currentMotionIndex = idleIndex;
        loadAndPlayMotion(currentMesh, {
            forceRestart: true,
            resetPosition
        });
        return true;
    }

    function getMorphTargetIndexFromTrackName(trackName) {
        const match = String(trackName || '').match(/\.morphTargetInfluences\[(.+?)\]/);
        if (!match) return null;
        const rawTarget = match[1].replace(/^['"]|['"]$/g, '');
        const numericIndex = Number(rawTarget);
        if (Number.isInteger(numericIndex) && numericIndex >= 0) return numericIndex;

        const dictionary = currentMesh?.morphTargetDictionary || {};
        if (!Object.prototype.hasOwnProperty.call(dictionary, rawTarget)) return null;
        const dictionaryIndex = Number(dictionary[rawTarget]);
        return Number.isInteger(dictionaryIndex) && dictionaryIndex >= 0 ? dictionaryIndex : null;
    }

    function getClipMorphTargetIndexes(clip) {
        const indexes = new Set();
        (clip?.tracks || []).forEach(track => {
            const index = getMorphTargetIndexFromTrackName(track?.name);
            if (index !== null) indexes.add(index);
        });
        return indexes;
    }

    function settleModelInteractionMorphsToIdle(idleClip, expectedAction) {
        if (!currentMesh || activeMotionAction !== expectedAction) return;
        const mesh = currentMesh;
        const morphs = mesh.morphTargetInfluences || [];
        if (!morphs.length) return;

        const idleMorphIndexes = getClipMorphTargetIndexes(idleClip);
        const bindingMorphs = animationBindingPose?.morphTargetInfluences || [];
        const targets = [];

        for (let index = 0; index < morphs.length; index += 1) {
            if (idleMorphIndexes.has(index)) continue;
            const startValue = Number(morphs[index]);
            const bindingValue = Number(bindingMorphs[index]);
            const targetValue = Number.isFinite(bindingValue) ? bindingValue : 0;
            if (!Number.isFinite(startValue) || Math.abs(startValue - targetValue) <= 0.015) continue;
            targets.push({ index, startValue, targetValue });
        }

        if (!targets.length) {
            console.log('Model interaction return: facial morphs already match the classic idle state.');
            return;
        }

        const startedAt = performance.now();
        const durationMs = Math.max(80, MODEL_INTERACTION_MORPH_SETTLE_SECONDS * 1000);
        const tick = now => {
            if (currentMesh !== mesh || activeMotionAction !== expectedAction) return;
            const progress = Math.min(1, Math.max(0, (now - startedAt) / durationMs));
            const eased = 1 - Math.pow(1 - progress, 3);
            targets.forEach(target => {
                morphs[target.index] = target.startValue + (target.targetValue - target.startValue) * eased;
            });
            if (progress < 1) {
                requestAnimationFrame(tick);
            } else {
                console.log(`Model interaction return: settled ${targets.length} leftover facial morph(s).`);
            }
        };
        requestAnimationFrame(tick);
    }

    function findOpeningMotionIndex() {
        const openingPool = [
            { file: 'apologizing.vmd', weight: 40 },
            { file: 'door_knock.vmd', weight: 40 },
            { file: 'looking_96.vmd', weight: 20 }
        ].map(candidate => {
            const index = motionEntries.findIndex((entry, entryIndex) => (
                getMotionEntryFileName(entry, entryIndex) === candidate.file
            ));
            return index >= 0 ? { ...candidate, index } : null;
        }).filter(Boolean);
        if (!openingPool.length) return -1;

        const totalWeight = openingPool.reduce((sum, candidate) => sum + candidate.weight, 0);
        let randomValue = Math.random() * totalWeight;
        for (const candidate of openingPool) {
            randomValue -= candidate.weight;
            if (randomValue <= 0) return candidate.index;
        }
        return openingPool[0].index;
    }

    function playOpeningMotionOnce(generation) {
        if (openingMotionPlayedGeneration === generation) return false;
        const openingIndex = findOpeningMotionIndex();
        if (openingIndex === -1 || !currentMesh) return false;

        openingMotionPlayedGeneration = generation;
        currentMotionState = 'normal_idle';
        currentMotionIndex = openingIndex;
        loadAndPlayMotion(currentMesh);
        return true;
    }

    function selectIntelligentMotion(context = {}) {
        let targetCategory = currentMotionState;

        if (context.categoryOverride) {
            targetCategory = context.categoryOverride;
        } else if (context.isResponding) {
            targetCategory = 'giving_answer';
        } else if (context.isListening) {
            targetCategory = context.questionType === 'emotional' ? 'listening_emotional' : 'searching_task';
        } else if (context.emotion === 'happy') {
            targetCategory = 'happy_mood';
        } else if (context.emotion === 'sad') {
            targetCategory = 'sad_emotion';
        } else if (context.isPraised) {
            targetCategory = 'praised';
        } else if (context.isEasterEgg) {
            targetCategory = 'easter_egg';
        } else if (context.forceNight) {
            targetCategory = 'night_sleepy';
        } else if (context.forceNormal) {
            targetCategory = 'normal_idle';
        }

        const selectedMotion = selectMotionFromCategory(targetCategory, {
            allowReaction: context.allowReaction
        });
        if (!selectedMotion) return null;

        const motionIndex = findMotionByPayload(selectedMotion);
        if (motionIndex === -1) {
            console.warn(`Motion ${selectedMotion.name || selectedMotion.id || selectedMotion.file} not found in files`);
            return null;
        }

        currentMotionState = targetCategory;
        return motionIndex;
    }

    function playIntelligentMotion(context = {}) {
        if (!currentMesh) return;

        const selectedIndex = selectIntelligentMotion(context);
        if (selectedIndex === null) return;

        const shouldResetPosition = context.resetPosition !== false;
        if (context.playOnce === true) {
            if (shouldResetPosition) {
                forceResetPosition();
            }
            currentMotionIndex = selectedIndex;
            loadAndPlayMotion(currentMesh, {
                forceRestart: true,
                interruptOneShot: true,
                playbackOverride: 'once_then_idle',
                returnToClassicIdle: true,
                resetPosition: shouldResetPosition
            });
            console.log(
                `Playing one-shot intelligent motion: ${motionFiles[selectedIndex]} ` +
                `(Category: ${currentMotionState})`
            );
            return;
        }
        if (shouldResetPosition) {
            forceResetPosition();
        }
        currentMotionIndex = selectedIndex;
        loadAndPlayMotion(currentMesh, { resetPosition: shouldResetPosition });

        console.log(`Playing intelligent motion: ${motionFiles[selectedIndex]} (Category: ${currentMotionState})`);
    }

    function playSleepyEyesMotion(sleepyIndex, shouldResetPosition = true) {
        const loopRepetitions = selectSleepyEyesLoopRepetitions();
        currentMotionState = 'night_sleepy';
        currentMotionIndex = sleepyIndex;
        loadAndPlayMotion(currentMesh, {
            forceRestart: true,
            playbackOverride: Number.isFinite(loopRepetitions) ? 'finite_loop' : 'loop',
            loopRepetitions,
            resetPosition: shouldResetPosition
        });
        console.log(
            `Night sleepy eyes: ${
                Number.isFinite(loopRepetitions) ? `${loopRepetitions} repetitions` : 'continuous'
            }.`
        );
    }

    function playNightRegularMotion(date = new Date()) {
        const settings = getNightMotionSettings(date);
        if (Math.random() >= settings.sleepyProbability) {
            playIntelligentMotion({ forceNormal: true, allowReaction: false });
            return;
        }

        const sleepyIndex = findMotionByPayload({
            motion_id: 'sleepy_eyes_closed',
            motion_file: 'sleepy_eyes_closed.vmd',
            motion_name: 'sleepy_eyes_closed'
        });
        if (sleepyIndex === -1) {
            playIntelligentMotion({ forceNormal: true, allowReaction: false });
            return;
        }

        playSleepyEyesMotion(sleepyIndex);
    }

    function startAutoMotionSystem() {
        if (motionTimer) clearInterval(motionTimer);

        motionTimer = setInterval(() => {
            const now = Date.now();
            const timeSinceLastInteraction = now - lastInteractionTime;

            if (timeSinceLastInteraction > 10000 && !isWaitingForResponse) {
                const currentDate = new Date(now);
                if (isNightTime(currentDate)) {
                    if (currentMotionState === 'night_sleepy') return;
                    const nightSettings = getNightMotionSettings(currentDate);
                    if (now - lastNightMotionAttemptAt >= nightSettings.attemptIntervalMs) {
                        lastNightMotionAttemptAt = now;
                        if (Math.random() < NIGHT_MOTION_PROBABILITY) {
                            playIntelligentMotion({
                                forceNight: true,
                                playOnce: true
                            });
                            return;
                        }
                    }
                    playNightRegularMotion(currentDate);
                    return;
                } else if (currentMotionState === 'night_sleepy') {
                    lastNightMotionAttemptAt = 0;
                }
                playIntelligentMotion({ forceNormal: true, allowReaction: false });
            }
        }, 8000);
    }

    function stopAutoMotionSystem() {
        if (motionTimer) {
            clearInterval(motionTimer);
            motionTimer = null;
        }
    }

    async function recordDailyAppEntry() {
        try {
            await fetch('/api/checkins/activity/enter', {method: 'POST'});
        } catch (error) {
            console.warn('Unable to update daily activity memory', error);
        }
    }

    window.addEventListener('load', function() {
        recordDailyAppEntry();
        fetchMotionConfig();
        fetchSessionList(); // Initial session list
        fetchCurrentModel(); // Sync model selection
        initializeCheckinPeek();
        loadSessionStats();
        initInputSuggestions();
        initializeVideoRecommendations();
        initializeLearner();
        initializeAnki();
        setTimeout(() => {
            if (isNightTime()) {
                console.log('Night mode detected - sleepy motions available');
            }
            startAutoMotionSystem();
        }, 3000);
    });

    // [ADDED] Model Switching Logic
    let previousModelValue = "";

    function initModelSelect() {
        const select = document.getElementById('model-select');
        if (select) {
            previousModelValue = select.value;
        }
    }
    window.addEventListener('load', initModelSelect);

    async function handleModelChange(selectElement) {
        const newValue = selectElement.value;
        if (newValue === 'api' && !getSavedApiSettings().api_key) {
            showToast("Please configure LLM API first.", { type: 'info', duration: 3600 });
            openApiSettingsModal();
            selectElement.value = previousModelValue;
            return;
        }

        // Confirm switch
        const confirmMsg = newValue === 'api'
            ? "Switch to API mode?"
            : "Switching local models requires reloading the backend. \n\n" +
                           "- Sending messages will be disabled during the switch.\n" + 
                           "- This may take a few moments.\n\n" +
                           "Continue?";
                           
        confirmAction(confirmMsg, async () => {
            // 1. Disable UI
            setChatDisabled(true);
        
        try {
            // 2. Call Backend
            const res = await fetch('/api/model/switch', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({model: newValue})
            });
            const data = await res.json();
            
            if (data.status === 'success') {
                showToast("Model switched to: " + (data.model || newValue), { type: 'info', duration: 3600 });
                previousModelValue = newValue;
                saveApiSettings({selected_model: newValue});
                toggleApiInput(); 
            } else {
                throw new Error(data.detail || data.message || "Unknown error");
            }
        } catch (e) {
            showToast("Model switch failed: " + e.message, { type: 'error', duration: 3600 });
            selectElement.value = previousModelValue; // Revert
        } finally {
            // 3. Re-enable UI
            setChatDisabled(false);
        }
        }, { onCancel: () => { selectElement.value = previousModelValue; } });
    }

    function setChatDisabled(disabled) {
        const sendBtn = document.querySelector('.btn-send'); 
        const input = document.getElementById('user-input');
        // Select all action buttons (Regenerate, Edit, Translate)
        const actionBtns = document.querySelectorAll('.btn-action');
        
        if (disabled) {
            if(sendBtn) {
                sendBtn.style.filter = "grayscale(100%)";
                sendBtn.style.pointerEvents = "none";
                sendBtn.style.opacity = "0.5";
            }
            if(input) {
                input.disabled = true;
                input.placeholder = "Switching model... please wait...";
            }
            // Disable message actions
            actionBtns.forEach(btn => {
                // Check if it is specifically a regenerate button if we want to be strict, 
                // but user said "options" (plural) and Edit/Regen both send requests.
                // Let's disable all for safety and consistency.
                btn.style.filter = "grayscale(100%)";
                btn.style.pointerEvents = "none";
                btn.style.opacity = "0.5";
                btn.disabled = true;
            });
        } else {
            if(sendBtn) {
                sendBtn.style.filter = "none";
                sendBtn.style.pointerEvents = "auto";
                sendBtn.style.opacity = "1";
            }
            if(input) {
                input.disabled = false;
                input.placeholder = "Type a message... (Shift+Enter for new line)";
                input.focus();
            }
            // Enable message actions
            actionBtns.forEach(btn => {
                btn.style.filter = "none";
                btn.style.pointerEvents = "auto";
                btn.style.opacity = "1";
                btn.disabled = false;
            });
        }
    }

    async function fetchCurrentModel() {
        try {
            const savedSettings = getSavedApiSettings();
            const savedApiMode =
                savedSettings.selected_model === 'api' &&
                Boolean(savedSettings.api_key);
            if (savedApiMode) {
                const select = document.getElementById('model-select');
                if (select) {
                    select.value = 'api';
                    previousModelValue = 'api';
                    toggleApiInput();
                    await fetch('/api/model/switch', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({model: 'api'})
                    });
                }
                return;
            }

            const res = await fetch('/api/current_model');
            const data = await res.json();
            if (data.model) {
                const select = document.getElementById('model-select');
                if (select) {
                    select.value = data.model;
                    previousModelValue = data.model;
                    toggleApiInput(); // Ensure UI state matches
                }
            }
        } catch (e) {
            console.error("Failed to fetch current model:", e);
        }
    }

    // --- SESSION MANAGEMENT ---
    function sessionLastQaTime(session) {
        return String(session?.last_qa_time || session?.time || '').trim();
    }

    function sortSessionsByLastQaTime(sessions) {
        return [...(sessions || [])].sort((a, b) => {
            const byTime = sessionLastQaTime(b).localeCompare(sessionLastQaTime(a));
            if (byTime !== 0) return byTime;
            return String(b?.id || '').localeCompare(String(a?.id || ''));
        });
    }

    async function fetchSessionList() {
        try {
            const res = await fetch('/api/sessions');
            const data = await res.json();
            const allSessions = sortSessionsByLastQaTime(data.sessions || []);
            const list = document.getElementById('session-list');
            list.innerHTML = '';

            list.appendChild(createSidebarActionButton('New project', createProject));
            list.appendChild(await createProjectSection(allSessions));
            list.appendChild(createSidebarActionButton('New chat', createNewSession));

            const globalSessions = allSessions.filter(s => !s.project_id && s.role_mode !== 'assistant');
            const pinnedSessions = globalSessions.filter(s => s.pinned);
            const historySessions = globalSessions.filter(s => !s.pinned);

            // PINNED Section
            if (pinnedSessions.length > 0) {
                const pinnedSection = createSessionSection('Pinned', pinnedSessions, true);
                list.appendChild(pinnedSection);
            }

            // HISTORY Section
            if (historySessions.length > 0) {
                const historySection = createSessionSection('Your chats', historySessions, false);
                list.appendChild(historySection);
            }
            
            // Also fetch status
            fetchStatus();
            loadSessionStats();
        } catch(e) { console.error("Fetch sessions failed", e); }
    }

    function createSidebarActionButton(label, handler) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'btn-new-chat sidebar-action-btn';
        button.innerHTML = `<span class="sidebar-action-plus" aria-hidden="true">+</span><span>${escapeHtml(label)}</span>`;
        button.onclick = handler;
        button.onkeydown = event => {
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                handler(event);
            }
        };
        return button;
    }

    async function createProjectSection(allSessions = []) {
        await loadProjects();
        const validProjectIds = new Set(projectsCache.map(project => project.id));
        if (currentProjectId && !validProjectIds.has(currentProjectId)) {
            setCurrentProject("", {refresh: false});
        }
        if (projectSidebarViewId && !validProjectIds.has(projectSidebarViewId)) {
            projectSidebarViewId = "";
        }
        const section = document.createElement('div');
        section.className = 'session-section project-section';
        const title = 'Projects';
        const projectSessionsById = new Map();
        allSessions.forEach(session => {
            if (!session.project_id) return;
            if (!projectSessionsById.has(session.project_id)) {
                projectSessionsById.set(session.project_id, []);
            }
            projectSessionsById.get(session.project_id).push(session);
        });

        const header = document.createElement('div');
        header.className = 'session-section-header';
        header.setAttribute('role', 'button');
        header.setAttribute('tabindex', '0');
        header.setAttribute('aria-expanded', 'true');
        header.innerHTML = `<span class="icon session-section-chevron icn icn-chevron-down icn-sm" aria-hidden="true"></span><span>${title}</span>`;

        const container = document.createElement('div');
        container.className = 'session-list-container';
        const viewedProject = projectsCache.find(project => project.id === projectSidebarViewId);
        if (viewedProject) {
            container.appendChild(createProjectBackItem(viewedProject));
            const projectSessions = projectSessionsById.get(viewedProject.id) || [];
            if (projectSessions.length) {
                projectSessions.forEach(session => container.appendChild(createProjectSessionItem(session)));
            } else {
                container.appendChild(createDisabledProjectItem('No conversations yet'));
            }
        } else if (projectsCache.length) {
            projectsCache.forEach(project => container.appendChild(createProjectItem(project)));
        } else {
            // Keep the empty project rail visually minimal: the ellipsis is
            // enough; the old empty-state label added noise to the sidebar.
            container.appendChild(createDisabledProjectItem(''));
        }
        section.appendChild(container);

        header.onclick = () => {
            const collapsed = header.classList.toggle('collapsed');
            container.classList.toggle('collapsed', collapsed);
            header.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
        };
        header.onkeydown = event => {
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                header.click();
            }
        };
        section.insertBefore(header, container);

        return section;
    }

    function createProjectItem(project) {
        const projectId = project.id || '';
        const div = document.createElement('div');
        div.className = `session-item project-session-item${projectId === currentProjectId ? ' active' : ''}`;
        div.onclick = (e) => {
            if (e.target.closest('.session-menu-btn') || e.target.closest('.session-menu-dropdown')) return;
            openProjectView(projectId);
        };

        const content = document.createElement('div');
        content.className = 'session-content';
        content.innerHTML = `
            <div class="session-preview">${escapeHtml(project.name || 'Untitled Project')}</div>
            <div class="session-date">${projectId === currentProjectId ? 'Current' : 'Project'}</div>
        `;
        div.appendChild(content);
        div.appendChild(createProjectMenuButton(project));
        return div;
    }

    function createProjectMenuButton(project) {
        const menuBtn = document.createElement('button');
        menuBtn.className = 'session-menu-btn';
        menuBtn.innerHTML = ic('more', 'icn-sm');
        menuBtn.title = 'Project actions';
        menuBtn.setAttribute('aria-label', 'Project actions');
        menuBtn.onclick = (e) => {
            e.stopPropagation();
            toggleProjectMenu(e, project);
        };
        return menuBtn;
    }

    function toggleProjectMenu(e, project) {
        document.querySelectorAll('.session-menu-dropdown').forEach(el => el.remove());
        document.querySelectorAll('.session-menu-btn').forEach(el => el.classList.remove('active'));

        const btn = e.currentTarget;
        btn.classList.add('active');

        const menu = document.createElement('div');
        menu.className = 'session-menu-dropdown show';

        const delOption = document.createElement('div');
        delOption.className = 'menu-option delete';
        delOption.innerHTML = '<span>Delete</span>';
        delOption.onclick = (ev) => {
            ev.stopPropagation();
            menu.remove();
            btn.classList.remove('active');
            deleteProject(project.id);
        };
        menu.appendChild(delOption);

        document.body.appendChild(menu);

        const rect = btn.getBoundingClientRect();
        menu.style.top = rect.bottom + 5 + 'px';
        menu.style.left = (rect.right - 120) + 'px';

        setTimeout(() => {
            const closeHandler = (ev) => {
                if (!menu.contains(ev.target) && ev.target !== btn) {
                    menu.remove();
                    btn.classList.remove('active');
                    document.removeEventListener('click', closeHandler);
                }
            };
            document.addEventListener('click', closeHandler);
        }, 0);
    }

    async function deleteProject(projectId) {
        if (!projectId) return;
        const project = projectsCache.find(p => p.id === projectId);
        const projectName = project?.name || 'this project';
        const sessionCount = project?.session_ids?.length || 0;
        try {
            const previewResponse = await fetch('/api/projects/delete-preview', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({id: projectId})
            });
            const preview = await previewResponse.json();
            if (!previewResponse.ok) throw new Error(preview.detail || 'Unable to review project deletion');
            if ((preview.memory_preview?.blocked || []).length) {
                throw new Error('This project has protected automatic memories. Unlock them before deleting the project.');
            }
            const sessionNote = preview.session_count
                ? `\n\nThis will permanently delete the project and its ${preview.session_count} conversation${preview.session_count === 1 ? '' : 's'}.`
                : '\n\nThis will permanently delete the project.';
            confirmAction(`Delete "${projectName}"?${sessionNote}\n${preview.auto_memory_count || 0} linked automatic memories will also be deleted.\n${memoryDeletionImpactText(preview.memory_preview || {})}\n\nThis cannot be undone.`, async () => {

                const response = await fetch('/api/projects/delete', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        id: projectId,
                        project_confirmation_token: preview.project_confirmation_token,
                        memory_confirmation_token: preview.memory_confirmation_token
                    })
                });
                if (!response.ok) throw new Error(`Delete failed: ${response.status}`);
                const data = await response.json();
                if (data.status !== 'success') throw new Error('Server returned failure');

                if (currentProjectId === projectId) setCurrentProject("", {refresh: false});
                if (projectSidebarViewId === projectId) projectSidebarViewId = "";

                if (data.was_active_session && currentRoleMode !== 'assistant') {
                    const chatContent = document.getElementById('chat-content');
                    if (chatContent) chatContent.innerHTML = '';
                    currentHistoryLength = 0;
                    currentSessionId = null;
                    await startNewSessionSilently("");
                } else {
                    fetchSessionList();
                }
        });        } catch (e) {
            console.error("Delete project failed", e);
            showToast("Failed to delete project: " + e.message, { type: 'error', duration: 3600 });
        }
    }

    function createProjectBackItem(project) {
        const div = document.createElement('div');
        div.className = 'session-item project-back-item';
        div.onclick = (e) => {
            if (e.target.closest('.session-menu-btn') || e.target.closest('.session-menu-dropdown')) return;
            closeProjectView();
        };

        const back = document.createElement('span');
        back.className = 'project-back-symbol icn icn-arrow-left icn-sm';
        back.setAttribute('aria-hidden', 'true');
        div.appendChild(back);

        const content = document.createElement('div');
        content.className = 'session-content';
        content.innerHTML = `
            <div class="session-preview">${escapeHtml(project.name || 'Untitled Project')}</div>
            <div class="session-date">Back to projects</div>
        `;
        div.appendChild(content);
        div.appendChild(createProjectMenuButton(project));
        return div;
    }

    function createDisabledProjectItem(label) {
        const div = document.createElement('div');
        div.className = 'session-item project-session-item project-empty-item';
        div.setAttribute('aria-disabled', 'true');

        const content = document.createElement('div');
        content.className = 'session-content';
        const safeLabel = String(label || '').trim();
        div.setAttribute('aria-label', safeLabel || 'Projects');
        content.innerHTML = safeLabel
            ? `
                <div class="session-preview project-empty-label"><span class="project-empty-ellipsis icn icn-more icn-sm" aria-hidden="true"></span><span>${escapeHtml(safeLabel)}</span></div>
                <div class="session-date">Project</div>
            `
            : '<div class="session-preview project-empty-label"><span class="project-empty-ellipsis icn icn-more icn-sm" aria-hidden="true"></span></div>';
        div.appendChild(content);
        return div;
    }

    function createProjectSessionItem(s) {
        const div = createSessionItem(s);
        div.classList.add('project-child-session');
        return div;
    }

    async function loadProjects() {
        try {
            const res = await fetch('/api/projects');
            const data = await res.json();
            projectsCache = data.projects || [];
        } catch (e) {
            projectsCache = [];
        }
    }

    function setCurrentProject(projectId, options = {}) {
        currentProjectId = projectId || "";
        localStorage.setItem('saya_current_project_id', currentProjectId);
        if (options.refresh) fetchSessionList();
    }

    function openProjectView(projectId) {
        if (!projectId) return;
        projectSidebarViewId = projectId;
        fetchSessionList();
    }

    function closeProjectView() {
        projectSidebarViewId = "";
        fetchSessionList();
    }

    async function createProject() {
        showPromptDialog({
            title: 'New project',
            label: 'Project name',
            placeholder: 'e.g. Saya refactor',
            confirmLabel: 'Create',
            onConfirm: async (name, close) => {
                close();
                await doCreateProject(name);
            }
        });
    }

    async function doCreateProject(name) {
        const response = await fetch('/api/projects/add', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({name: name.trim()})
        });
        const data = await response.json();
        if (data.project?.id) {
            currentProjectId = data.project.id;
            projectSidebarViewId = data.project.id;
            localStorage.setItem('saya_current_project_id', currentProjectId);
            await startNewSessionSilently(data.project.id);
        }
        fetchSessionList();
    }

    function showUnderDevelopment(name) {
        showToast(`${name} is still under development.`, { type: 'info', duration: 3600 });
    }

    // --- ROOMS (local conversation spaces) ---
    const ROOMS_STORAGE_KEY = 'saya_rooms_v1';
    const CURRENT_ROOM_STORAGE_KEY = 'saya_current_room_v1';

    function getRooms() {
        try {
            const saved = JSON.parse(localStorage.getItem(ROOMS_STORAGE_KEY) || '[]');
            const rooms = Array.isArray(saved) ? saved.map(value => String(value || '').trim()).filter(Boolean) : [];
            return rooms.length ? Array.from(new Set(['General', ...rooms])) : ['General'];
        } catch (error) {
            return ['General'];
        }
    }

    function getCurrentRoom() {
        const rooms = getRooms();
        const saved = String(localStorage.getItem(CURRENT_ROOM_STORAGE_KEY) || 'General').trim();
        return rooms.includes(saved) ? saved : 'General';
    }

    function saveRooms(rooms) {
        localStorage.setItem(ROOMS_STORAGE_KEY, JSON.stringify(Array.from(new Set(rooms))));
    }

    function syncCurrentRoomLabel() {
        const label = document.getElementById('rooms-current-label');
        if (label) label.textContent = getCurrentRoom();
    }

    function renderRooms() {
        const list = document.getElementById('rooms-list');
        if (!list) return;
        const current = getCurrentRoom();
        syncCurrentRoomLabel();
        list.innerHTML = '';
        getRooms().forEach(room => {
            const item = document.createElement('div');
            item.className = `room-item${room === current ? ' active' : ''}`;
            item.innerHTML = `
                <button type="button" class="room-item-open" onclick="selectRoom('${escapeJsString(room)}')">
                    <span class="room-item-icon"><span class="icn icn-home icn-sm" aria-hidden="true"></span></span>
                    <span class="room-item-copy"><strong>${escapeHtml(room)}</strong><small>${room === current ? 'Current room' : 'Conversation space'}</small></span>
                    ${room === current ? '<span class="icn icn-check icn-sm room-item-check" aria-hidden="true"></span>' : ''}
                </button>
                <button type="button" class="row-menu-btn" onclick="showRoomMenu(this, '${escapeJsString(room)}')" title="More actions" aria-label="More actions" aria-haspopup="menu"><span class="icn icn-more icn-sm" aria-hidden="true"></span></button>`;
            list.appendChild(item);
        });
    }

    function showRoomMenu(anchor, room) {
        const current = getCurrentRoom();
        const items = [
            { label: 'Rename room', icon: 'edit', onClick: () => renameRoom(room) },
        ];
        if (room !== 'General') {
            items.push({ label: 'Delete room', icon: 'trash', danger: true, onClick: () => deleteRoom(room) });
        }
        showMenuAt(anchor, items, { align: 'end' });
    }
    window.showRoomMenu = showRoomMenu;

    function renameRoom(room) {
        showPromptDialog({
            title: 'Rename room',
            label: 'Room name',
            value: room,
            confirmLabel: 'Rename',
            onConfirm: (name, close) => {
                close();
                const rooms = getRooms().filter(item => item !== room);
                if (!rooms.includes(name)) rooms.push(name);
                saveRooms(rooms);
                if (getCurrentRoom() === room) selectRoom(name);
                renderRooms();
                showToast('Room renamed.');
            }
        });
    }

    function deleteRoom(room) {
        showConfirmDialog({
            title: 'Delete this room?',
            message: `The room "${room}" is removed from the list. Conversations are not deleted.`,
            impact: ['If it was the current room, you return to General'],
            confirmLabel: 'Delete room',
            danger: true,
            onConfirm: close => {
                close();
                const rooms = getRooms().filter(item => item !== room);
                saveRooms(rooms);
                if (getCurrentRoom() === room) selectRoom(rooms[0] || 'General');
                renderRooms();
                showToast('Room deleted.');
            }
        });
    }

    function createRoom() {
        showPromptDialog({
            title: 'New room',
            label: 'Room name',
            placeholder: 'e.g. Study notes',
            confirmLabel: 'Create',
            onConfirm: (name, close) => {
                close();
                const rooms = getRooms();
                if (!rooms.includes(name)) rooms.push(name);
                saveRooms(rooms);
                selectRoom(name);
                showToast('Room created.');
            }
        });
    }

    function openRoomsModal() {
        const modal = document.getElementById('rooms-modal');
        if (!modal) return;
        renderRooms();
        modal.style.display = 'flex';
    }

    function closeRoomsModal() {
        const modal = document.getElementById('rooms-modal');
        if (modal) modal.style.display = 'none';
    }

    function selectRoom(room) {
        const name = String(room || '').trim();
        if (!name) return;
        localStorage.setItem(CURRENT_ROOM_STORAGE_KEY, name);
        syncCurrentRoomLabel();
        renderRooms();
    }

    function openToolsModal() {
        const modal = document.getElementById('tools-modal');
        if (modal) modal.style.display = 'flex';
    }

    function closeToolsModal() {
        const modal = document.getElementById('tools-modal');
        if (modal) modal.style.display = 'none';
    }

    function openMoreModal(anchor) {
        const button = anchor instanceof Element ? anchor : document.querySelector('[onclick*="openMoreModal"]');
        if (!button) return;
        showMenuAt(button, [
            { label: 'Search', icon: 'search', onClick: () => openChatSearch() },
            { label: 'Backup & Restore', icon: 'save', onClick: () => openBackupRestoreModal() },
            { label: 'Mode Settings', icon: 'sliders', onClick: () => openModeSettingModal() },
            { label: 'Token Limits', icon: 'note', onClick: () => openTokenLimitsModal() }
        ]);
    }

    function closeMoreModal() {
        document.querySelectorAll('.uni-popover').forEach(p => p.remove());
    }

    function openRolesModal() {
        const modal = document.getElementById('roles-modal');
        if (modal) modal.style.display = 'flex';
    }

    function closeRolesModal() {
        const modal = document.getElementById('roles-modal');
        if (modal) modal.style.display = 'none';
    }

    function learnerCredentials() {
        const settings = getSavedApiSettings();
        const style = getSayaStyleSettings();
        return {
            model: document.getElementById('model-select')?.value || '',
            api_url: settings.api_url || '',
            api_key: settings.api_key || '',
            // A Learner report is a normal long-form response, not a memory
            // maintenance job. Some reasoning-only memory models can consume
            // their output budget without returning visible report content.
            api_model_name: settings.api_model_name || settings.memory_strong_model_name || '',
            brave_search_api_key: settings.brave_search_api_key || '',
            response_language: style.response_language || 'auto'
        };
    }

    async function fetchLearnerState() {
        const response = await fetch('/api/learner/state', {cache: 'no-store'});
        if (!response.ok) throw new Error(`Learner state failed: ${response.status}`);
        learnerState = await response.json();
        renderLearnerActivityBadge();
        if (document.getElementById('learner-role-modal')?.style.display === 'flex') renderLearnerRole();
        return learnerState;
    }

    async function initializeLearner() {
        try {
            const response = await fetch('/api/learner/start', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(learnerCredentials())
            });
            if (!response.ok) throw new Error(`Learner start failed: ${response.status}`);
            learnerState = await response.json();
            renderLearnerActivityBadge();
        } catch (error) {
            console.warn('Learner startup failed', error);
            try { await fetchLearnerState(); } catch (_) {}
        }
        scheduleLearnerHeartbeat(Number(learnerState.counts?.running || 0) ? 3000 : 60000);
    }

    function scheduleLearnerHeartbeat(delay = null) {
        if (learnerPollTimer) clearTimeout(learnerPollTimer);
        if (learnerSchedulerTimer) clearInterval(learnerSchedulerTimer);
        learnerSchedulerTimer = null;
        const nextDelay = delay ?? (Number(learnerState.counts?.running || 0) ? 3000 : 60000);
        learnerPollTimer = setTimeout(runLearnerHeartbeat, nextDelay);
    }

    async function runLearnerHeartbeat() {
        if (learnerHeartbeatBusy) {
            scheduleLearnerHeartbeat(3000);
            return;
        }
        learnerHeartbeatBusy = true;
        try {
            if (Number(learnerState.counts?.running || 0)) await fetchLearnerState();
            else await startLearnerDueTasks(false);
        } finally {
            learnerHeartbeatBusy = false;
            scheduleLearnerHeartbeat();
        }
    }

    function renderLearnerActivityBadge() {
        const counts = learnerState.counts || {};
        const running = Number(counts.running || 0);
        const unread = Number(counts.unread || 0);
        const visibleCount = running + unread;
        const count = document.getElementById('learner-activity-count');
        const label = document.getElementById('learner-activity-label');
        const subtitle = document.getElementById('learner-activity-subtitle');
        const dot = document.getElementById('learner-activity-dot');
        const button = document.getElementById('learner-activity-button');
        const hasCaution = !running && (learnerState.runs || []).some(run => run.status === 'completed_caution' && !run.is_read);
        if (count) {
            count.textContent = String(visibleCount);
            count.hidden = visibleCount === 0;
        }
        if (label) label.textContent = running ? 'Learning' : hasCaution ? 'Check result' : unread ? 'New learning' : 'Learner';
        if (subtitle) subtitle.textContent = running ? `${running} in progress` : hasCaution ? 'Completed with caution' : unread ? `${unread} unread` : 'All caught up';
        if (dot) dot.className = running ? 'running' : hasCaution ? 'caution' : unread ? 'unread' : '';
        if (button) {
            button.classList.toggle('has-activity', Boolean(visibleCount));
            button.classList.toggle('is-running', Boolean(running));
            button.classList.toggle('has-unread', Boolean(unread && !running));
            button.classList.toggle('has-caution', Boolean(hasCaution));
            button.title = running ? `${running} learning task(s) in progress` : unread ? `${unread} unread learning result(s)` : 'Learner activity';
        }
    }

    async function openLearnerRoleModal() {
        closeRolesModal();
        const modal = document.getElementById('learner-role-modal');
        if (modal) modal.style.display = 'flex';
        setLearnerDefaultRunAt();
        try { await fetchLearnerState(); }
        catch (error) {
            console.warn('Learner state unavailable:', error);
            const taskList = document.getElementById('learner-task-list');
            if (taskList) {
                taskList.innerHTML = `
                    <div class="st-page-error">
                        ${ic('alert')}
                        <strong>Learner could not be loaded.</strong>
                        <span>Your tasks and memory are safe. Check that the backend is running, then try again.</span>
                        <button type="button" class="workspace-secondary-action" onclick="retryLearnerState()">${ic('refresh', 'icn-sm')}Retry</button>
                    </div>`;
            }
        }
        renderLearnerSourceFields();
        renderLearnerScheduleFields();
        renderLearnerRole();
    }

    function closeLearnerRoleModal() {
        const modal = document.getElementById('learner-role-modal');
        if (modal) modal.style.display = 'none';
    }

    async function retryLearnerState() {
        try {
            await fetchLearnerState();
            renderLearnerRole();
            showToast('Learner reconnected.');
        } catch (error) {
            showToast('Learner is still unavailable.', { type: 'error' });
            console.warn('Learner retry failed:', error);
        }
    }
    window.retryLearnerState = retryLearnerState;

    function backToRolesFromLearner() {
        closeLearnerRoleModal();
        openRolesModal();
    }

    function setLearnerDefaultRunAt() {
        const input = document.getElementById('learner-run-at');
        if (!input || input.value) return;
        const date = new Date(Date.now() + 60000);
        date.setSeconds(0, 0);
        const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
        input.value = local;
    }

    function syncLearnerSegmented(groupId, value) {
        document.querySelectorAll(`#${groupId} button[data-value]`).forEach(button => {
            const active = button.dataset.value === value;
            button.classList.toggle('active', active);
            button.setAttribute('aria-pressed', String(active));
        });
    }

    function selectLearnerSource(value) {
        const select = document.getElementById('learner-source-type');
        if (select) select.value = value;
        renderLearnerSourceFields();
    }

    function selectLearnerSchedule(value) {
        const select = document.getElementById('learner-schedule-type');
        if (select) select.value = value;
        renderLearnerScheduleFields();
    }

    function selectLearnerRunWhen(value) {
        const select = document.getElementById('learner-run-when');
        if (select) select.value = value;
        renderLearnerScheduleFields();
    }

    function selectLearnerVideoSelection(value) {
        const select = document.getElementById('learner-video-selection-mode');
        if (select) select.value = value;
        renderLearnerVideoFields();
    }

    function renderLearnerSourceFields() {
        const source = document.getElementById('learner-source-type')?.value || 'web';
        const topic = document.getElementById('learner-topic-field');
        const creators = document.getElementById('learner-creator-field');
        const webSettings = document.getElementById('learner-web-settings');
        if (topic) topic.hidden = source !== 'web';
        if (creators) creators.hidden = source !== 'video';
        if (webSettings) webSettings.hidden = source !== 'web';
        syncLearnerSegmented('learner-source-choice', source);
        renderLearnerCreatorOptions();
        renderLearnerVideoFields();
    }

    function renderLearnerScheduleFields() {
        const schedule = document.getElementById('learner-schedule-type')?.value || 'once';
        const runWhen = document.getElementById('learner-run-when')?.value || 'now';
        const runAt = document.getElementById('learner-run-at-field');
        const time = document.getElementById('learner-time-field');
        const weekdays = document.getElementById('learner-weekday-field');
        const runWhenField = document.getElementById('learner-run-when-field');
        if (runWhenField) runWhenField.hidden = schedule !== 'once';
        if (runAt) runAt.hidden = schedule !== 'once' || runWhen !== 'scheduled';
        if (time) time.hidden = schedule === 'once';
        if (weekdays) weekdays.hidden = schedule !== 'weekly';
        syncLearnerSegmented('learner-schedule-choice', schedule);
        syncLearnerSegmented('learner-run-when-choice', runWhen);
        renderLearnerVideoFields();
    }

    function renderLearnerCreatorOptions() {
        const container = document.getElementById('learner-creator-options');
        if (!container) return;
        const creators = (learnerState.creators || []).filter(item => item.enabled !== false);
        container.innerHTML = creators.length ? creators.map(item => `
            <label><input type="checkbox" value="${escapeHtml(item.id)}" onchange="renderLearnerVideoFields()"><span>${escapeHtml(item.name || 'Creator')}</span><small>${escapeHtml(item.platform || '')}</small></label>
        `).join('') : '<div class="learner-empty">Add creators under Tools → Video Recommendations first.</div>';
    }

    function renderLearnerVideoFields() {
        const field = document.getElementById('learner-video-field');
        const source = document.getElementById('learner-source-type')?.value || 'web';
        const schedule = document.getElementById('learner-schedule-type')?.value || 'once';
        if (!field) return;
        field.hidden = source !== 'video';
        if (source !== 'video') return;
        const modeSelect = document.getElementById('learner-video-selection-mode');
        const allowed = schedule === 'once' ? ['selected'] : ['new', 'latest', 'most_viewed'];
        if (modeSelect && !allowed.includes(modeSelect.value)) modeSelect.value = allowed[0];
        document.querySelectorAll('#learner-video-selection-choice button').forEach(button => {
            button.hidden = !allowed.includes(button.dataset.value);
        });
        const mode = modeSelect?.value || allowed[0];
        syncLearnerSegmented('learner-video-selection-choice', mode);
        const help = document.getElementById('learner-video-selection-help');
        if (help) help.textContent = schedule === 'once'
            ? 'One time: choose one or more videos from the current recommendation list. Learner will read their available YouTube captions.'
            : mode === 'new'
                ? 'Recurring: each run reads every new candidate video published after this task was created.'
                : mode === 'latest'
                    ? 'Recurring: each run reads the newest unprocessed candidate from each selected creator.'
                    : 'Recurring: each run reads the most-viewed unprocessed candidate from each selected creator; if YouTube view data is unavailable, it falls back to newest.';
        const options = document.getElementById('learner-video-options');
        if (!options) return;
        if (schedule !== 'once' || mode !== 'selected') {
            options.innerHTML = '<div class="learner-video-selection-note">No fixed videos are needed for this recurring strategy.</div>';
            return;
        }
        const selectedCreators = new Set(Array.from(document.querySelectorAll('#learner-creator-options input:checked')).map(input => input.value));
        const videos = (learnerState.video_options || []).filter(video => selectedCreators.has(video.creator_id));
        if (!selectedCreators.size) {
            options.innerHTML = '<div class="learner-video-selection-note">Select at least one creator first.</div>';
            return;
        }
        if (!videos.length) {
            options.innerHTML = '<div class="learner-video-selection-note">No cached recommendations. Refresh Video Recommendations, then return here.</div>';
            return;
        }
        const checked = new Set(Array.from(options.querySelectorAll('input:checked')).map(input => input.value));
        options.innerHTML = videos.map(video => `
            <label class="learner-video-option"><input type="checkbox" value="${escapeHtml(video.id)}" ${checked.has(video.id) ? 'checked' : ''}><span><strong>${escapeHtml(video.title || 'Untitled')}</strong><small>${escapeHtml(video.creator_name || '')} · ${escapeHtml(formatVideoDate(video.published_at))}${video.view_count != null ? ` · ${Number(video.view_count).toLocaleString()} views` : ''}${video.watched ? ' · watched' : ''}</small></span></label>
        `).join('');
    }

    async function refreshLearnerVideoOptions() {
        setLearnerEditorStatus('Refreshing video recommendations...');
        try {
            const response = await fetch('/api/video_recommendations/recommendations?refresh=true');
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || 'Unable to refresh recommendations');
            videoRecommendationState = data;
            await fetchLearnerState();
            renderLearnerVideoFields();
            setLearnerEditorStatus('Recommendations refreshed.');
        } catch (error) {
            setLearnerEditorStatus(error.message || 'Unable to refresh recommendations', true);
        }
    }

    function learnerScheduleText(task) {
        if (task.schedule_type === 'once') {
            if (task.run_when === 'next_launch') return 'Once · next launch';
            if (task.run_when === 'now') return 'Once · now';
            return task.run_at ? `Once · ${formatVideoDate(task.run_at)}` : 'One time';
        }
        if (task.schedule_type === 'daily') return `Daily · ${task.time || '09:00'}`;
        const names = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
        return `Weekly ${((task.weekdays || []).map(day => names[day]).filter(Boolean).join(', '))} · ${task.time || '09:00'}`;
    }

    function learnerVideoStrategyText(task) {
        if (task.source_type !== 'video') return '';
        const labels = {
            selected: `${(task.video_ids || []).length} selected video(s)`,
            new: 'every new video',
            latest: 'latest per creator',
            most_viewed: 'most viewed per creator',
            legacy_creator_latest: 'latest per creator'
        };
        return labels[task.video_selection_mode] || 'video selection';
    }

    function learnerRunLabel(run) {
        const labels = {queued: 'Queued', running: 'Running', completed: 'Completed', completed_caution: 'Completed with caution', failed: 'Failed', interrupted: 'Interrupted'};
        return labels[run.status] || run.status || 'Unknown';
    }

    function learnerRunCard(run, compact = false) {
        const terminal = !['queued', 'running'].includes(run.status);
        const readClass = run.is_read ? 'is-read' : terminal ? 'is-unread' : '';
        const time = run.status === 'running'
            ? `${run.stage || 'working'} · ${run.progress || 0}%`
            : `${formatVideoDate(run.finished_at || run.created_at)}${run.status === 'completed_caution' ? ' · wider source tier used' : ''}`;
        const badgeClass = {queued: '', running: 'running', completed: 'success', completed_caution: 'warning', failed: 'danger', interrupted: 'danger'}[run.status] || '';
        return `<article class="learner-run-card ${escapeHtml(run.status || '')} ${readClass}">
            <button class="learner-run-open" onclick="openLearnerRun('${escapeJsString(run.id)}')">
                <span class="st-badge ${badgeClass}">${terminal && !run.is_read ? '<i aria-hidden="true" class="learner-unread-dot"></i>' : ''}${escapeHtml(learnerRunLabel(run))}</span>
                <strong>${escapeHtml(run.task_title || 'Learning run')}</strong><small>${escapeHtml(time)}</small>
            </button>
            ${terminal ? `<div class="learner-run-actions">
                <button onclick="setLearnerRunRead('${escapeJsString(run.id)}', ${run.is_read ? 'false' : 'true'})">${run.is_read ? ic('eye', 'icn-sm') + 'Mark unread' : ic('check', 'icn-sm') + 'Mark read'}</button>
                <button class="danger" onclick="deleteLearnerRun('${escapeJsString(run.id)}')">${ic('trash', 'icn-sm')}Delete</button>
            </div>` : ''}
        </article>`;
    }

    function renderLearnerRole() {
        const tasks = learnerState.tasks || [];
        const runs = learnerState.runs || [];
        const taskCount = document.getElementById('learner-task-count');
        const completeCount = document.getElementById('learner-complete-count');
        if (taskCount) taskCount.textContent = String(tasks.length);
        if (completeCount) completeCount.textContent = String(learnerState.counts?.completed || 0);
        const orphanNotice = document.getElementById('learner-orphan-notice');
        const orphanCount = Number(learnerState.counts?.orphaned_memories || 0);
        if (orphanNotice) {
            orphanNotice.hidden = orphanCount === 0;
            orphanNotice.innerHTML = orphanCount ? `<div><strong>Memory cleanup available</strong><span>${orphanCount} older Learner memor${orphanCount === 1 ? 'y no longer has' : 'ies no longer have'} a matching task or activity record. Review before removing.</span></div><button onclick="cleanupOrphanedLearnerMemories()">${ic('trash', 'icn-sm')}Review cleanup</button>` : '';
        }
        renderLearnerCreatorOptions();
        const taskList = document.getElementById('learner-task-list');
        if (taskList) taskList.innerHTML = tasks.length ? tasks.map(task => {
            const taskBadge = task.enabled
                ? '<span class="st-badge running">Active</span>'
                : '<span class="st-badge">Paused</span>';
            return `
            <article class="learner-task-card ${task.enabled ? '' : 'disabled'}">
                <div class="learner-card-main"><div class="learner-card-title-row"><strong>${escapeHtml(task.title)}</strong>${taskBadge}</div><small>${escapeHtml(task.source_type === 'web' ? task.topic : `${(task.creator_ids || []).length} selected creator(s) · ${learnerVideoStrategyText(task)}`)}</small><div class="learner-card-meta"><span>${escapeHtml(learnerScheduleText(task))}</span>${task.read_memory ? '<span>reads memory</span>' : ''}${task.manage_memory ? '<span>manages memory</span>' : ''}</div></div>
                <div class="learner-card-actions"><button onclick="editLearnerTask('${escapeJsString(task.id)}')">${ic('edit', 'icn-sm')}Edit</button><button onclick="runLearnerTask('${escapeJsString(task.id)}')">${ic('play', 'icn-sm')}Run now</button><button onclick="toggleLearnerTask('${escapeJsString(task.id)}', ${task.enabled ? 'false' : 'true'})">${task.enabled ? ic('stop', 'icn-sm') + 'Pause' : ic('play', 'icn-sm') + 'Enable'}</button><button class="danger" onclick="deleteLearnerTask('${escapeJsString(task.id)}')">${ic('trash', 'icn-sm')}Delete</button></div>
            </article>`;
        }).join('') : '<div class="learner-empty">No learning tasks yet.</div>';
        const runList = document.getElementById('learner-run-list');
        if (runList) runList.innerHTML = runs.length ? runs.slice(0, 20).map(run => learnerRunCard(run, true)).join('') : '<div class="learner-empty">No activity yet.</div>';
    }

    async function cleanupOrphanedLearnerMemories() {
        const previewResponse = await fetch('/api/learner/memories/orphans/delete-preview', {method: 'POST'});
        const preview = await previewResponse.json();
        if (!previewResponse.ok) { showToast(preview.detail || 'Unable to review memory cleanup.', { type: 'error' }); return; }
        if (!preview.memory_count) { await fetchLearnerState(); return; }
        if ((preview.memory_preview?.blocked || []).length) {
            showToast('Some orphaned memories are protected. Unlock them before cleanup.', { type: 'error' });
            return;
        }
        showConfirmDialog({
            title: `Delete ${preview.memory_count} orphaned Learner memor${preview.memory_count === 1 ? 'y' : 'ies'}?`,
            message: 'These memories no longer belong to any task or activity record.',
            impact: memoryDeletionImpactText(preview.memory_preview || {}).split('\n').filter(Boolean),
            confirmLabel: 'Delete',
            danger: true,
            onConfirm: async close => {
                close();
                const response = await fetch(`/api/learner/memories/orphans?confirmation_token=${encodeURIComponent(preview.confirmation_token || '')}`, {method: 'DELETE'});
                const data = await response.json();
                if (!response.ok || data.status !== 'success') { showToast(data.detail || data.message || 'Unable to clean up memories.', { type: 'error' }); return; }
                showToast('Orphaned Learner memories deleted.');
                await fetchLearnerState();
            }
        });
    }

    function setLearnerEditorStatus(message, error = false) {
        const status = document.getElementById('learner-editor-status');
        if (!status) return;
        status.textContent = message || '';
        status.classList.toggle('error', Boolean(error));
    }

    async function createLearnerTask() {
        const editingTask = learnerEditingTaskId
            ? (learnerState.tasks || []).find(item => item.id === learnerEditingTaskId)
            : null;
        const wasEditing = Boolean(editingTask);
        const sourceType = document.getElementById('learner-source-type')?.value || 'web';
        const scheduleType = document.getElementById('learner-schedule-type')?.value || 'once';
        const creatorIds = Array.from(document.querySelectorAll('#learner-creator-options input:checked')).map(input => input.value);
        const videoIds = Array.from(document.querySelectorAll('#learner-video-options input:checked')).map(input => input.value);
        const weekdays = Array.from(document.querySelectorAll('#learner-weekday-field input:checked')).map(input => Number(input.value));
        const payload = {
            title: document.getElementById('learner-task-title')?.value.trim() || '',
            source_type: sourceType,
            topic: document.getElementById('learner-task-topic')?.value.trim() || '',
            creator_ids: creatorIds,
            video_ids: videoIds,
            video_selection_mode: document.getElementById('learner-video-selection-mode')?.value || (scheduleType === 'once' ? 'selected' : 'new'),
            schedule_type: scheduleType,
            run_when: document.getElementById('learner-run-when')?.value || 'now',
            run_at: document.getElementById('learner-run-at')?.value || null,
            time: document.getElementById('learner-time')?.value || '09:00',
            weekdays,
            read_memory: Boolean(document.getElementById('learner-read-memory')?.checked),
            manage_memory: Boolean(document.getElementById('learner-manage-memory')?.checked),
            web_max_pages: Number(document.getElementById('learner-web-max-pages')?.value || 15),
            web_reference_count: Number(document.getElementById('learner-web-reference-count')?.value || 6),
            web_min_references: Number(document.getElementById('learner-web-min-references')?.value || 1),
            web_source_policy: document.getElementById('learner-web-source-policy')?.value || 'balanced',
            enabled: editingTask ? editingTask.enabled !== false : true
        };
        setLearnerEditorStatus(learnerEditingTaskId ? 'Saving...' : 'Creating...');
        try {
            const endpoint = learnerEditingTaskId ? `/api/learner/tasks/${encodeURIComponent(learnerEditingTaskId)}` : '/api/learner/tasks';
            const response = await fetch(endpoint, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || `Unable to ${learnerEditingTaskId ? 'save' : 'create'} task`);
            setLearnerEditorStatus(learnerEditingTaskId ? 'Task updated.' : 'Task created. Due work will begin while the application is open.');
            resetLearnerTaskEditor();
            await fetchLearnerState();
            if (!wasEditing && payload.schedule_type === 'once' && payload.run_when === 'now') {
                await startLearnerDueTasks(false);
            }
        } catch (error) { setLearnerEditorStatus(error.message || error, true); }
    }

    function editLearnerTask(taskId) {
        const task = (learnerState.tasks || []).find(item => item.id === taskId);
        if (!task) return;
        learnerEditingTaskId = taskId;
        document.getElementById('learner-task-title').value = task.title || '';
        document.getElementById('learner-source-type').value = task.source_type || 'web';
        document.getElementById('learner-task-topic').value = task.topic || '';
        document.getElementById('learner-schedule-type').value = task.schedule_type || 'once';
        document.getElementById('learner-video-selection-mode').value = task.video_selection_mode || (task.schedule_type === 'once' ? 'selected' : 'new');
        document.getElementById('learner-run-when').value = task.run_when || 'scheduled';
        document.getElementById('learner-run-at').value = task.run_at ? String(task.run_at).slice(0, 16) : '';
        document.getElementById('learner-time').value = task.time || '09:00';
        document.getElementById('learner-read-memory').checked = Boolean(task.read_memory);
        document.getElementById('learner-manage-memory').checked = Boolean(task.manage_memory);
        document.getElementById('learner-web-max-pages').value = task.web_max_pages || 15;
        document.getElementById('learner-web-reference-count').value = task.web_reference_count || 6;
        document.getElementById('learner-web-min-references').value = task.web_min_references || 1;
        document.getElementById('learner-web-source-policy').value = task.web_source_policy || 'balanced';
        renderLearnerSourceFields(); renderLearnerScheduleFields();
        const creators = new Set(task.creator_ids || []);
        document.querySelectorAll('#learner-creator-options input').forEach(input => { input.checked = creators.has(input.value); });
        renderLearnerVideoFields();
        const selectedVideos = new Set(task.video_ids || []);
        document.querySelectorAll('#learner-video-options input').forEach(input => { input.checked = selectedVideos.has(input.value); });
        const weekdays = new Set(task.weekdays || []);
        document.querySelectorAll('#learner-weekday-field input').forEach(input => { input.checked = weekdays.has(Number(input.value)); });
        const title = document.getElementById('learner-editor-title');
        const submit = document.getElementById('learner-editor-submit');
        const cancel = document.getElementById('learner-editor-cancel');
        if (title) title.textContent = 'Edit task';
        if (submit) submit.textContent = 'Save changes';
        if (cancel) cancel.hidden = false;
        setLearnerEditorStatus('Editing this task.');
        document.querySelector('.learner-task-editor')?.scrollIntoView({behavior: 'smooth', block: 'start'});
    }

    function resetLearnerTaskEditor() {
        learnerEditingTaskId = null;
        const titleInput = document.getElementById('learner-task-title');
        const topicInput = document.getElementById('learner-task-topic');
        if (titleInput) titleInput.value = '';
        if (topicInput) topicInput.value = '';
        const source = document.getElementById('learner-source-type');
        const schedule = document.getElementById('learner-schedule-type');
        const runAt = document.getElementById('learner-run-at');
        const runWhen = document.getElementById('learner-run-when');
        const time = document.getElementById('learner-time');
        const readMemory = document.getElementById('learner-read-memory');
        const manageMemory = document.getElementById('learner-manage-memory');
        if (source) source.value = 'web';
        if (schedule) schedule.value = 'once';
        const videoSelection = document.getElementById('learner-video-selection-mode');
        if (videoSelection) videoSelection.value = 'selected';
        if (runWhen) runWhen.value = 'now';
        if (runAt) runAt.value = '';
        if (time) time.value = '09:00';
        if (readMemory) readMemory.checked = false;
        if (manageMemory) manageMemory.checked = false;
        const webMaxPages = document.getElementById('learner-web-max-pages');
        const webReferenceCount = document.getElementById('learner-web-reference-count');
        const webMinReferences = document.getElementById('learner-web-min-references');
        const webSourcePolicy = document.getElementById('learner-web-source-policy');
        if (webMaxPages) webMaxPages.value = '15';
        if (webReferenceCount) webReferenceCount.value = '6';
        if (webMinReferences) webMinReferences.value = '1';
        if (webSourcePolicy) webSourcePolicy.value = 'balanced';
        document.querySelectorAll('#learner-creator-options input, #learner-weekday-field input, #learner-video-options input').forEach(input => { input.checked = false; });
        const title = document.getElementById('learner-editor-title');
        const submit = document.getElementById('learner-editor-submit');
        const cancel = document.getElementById('learner-editor-cancel');
        if (title) title.textContent = 'Create task';
        if (submit) submit.textContent = 'Create task';
        if (cancel) cancel.hidden = true;
        renderLearnerSourceFields(); renderLearnerScheduleFields();
        setLearnerDefaultRunAt();
    }

    function cancelLearnerTaskEdit() {
        resetLearnerTaskEditor();
        setLearnerEditorStatus('');
    }

    async function runLearnerTask(taskId) {
        try {
            const response = await fetch(`/api/learner/tasks/${encodeURIComponent(taskId)}/run`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(learnerCredentials())});
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || 'Unable to start task');
            await fetchLearnerState();
            scheduleLearnerHeartbeat(3000);
        } catch (error) { showToast(error.message || error, { type: 'error', duration: 3600 }); }
    }

    async function startLearnerDueTasks(showNotice = false) {
        try {
            const response = await fetch('/api/learner/start', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(learnerCredentials())});
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || 'Unable to start due tasks');
            learnerState = data;
            renderLearnerRole(); renderLearnerActivityBadge();
            if (showNotice && !(data.started || []).length) showToast('No due learning task is waiting.', { type: 'info', duration: 3600 });
        } catch (error) { if (showNotice) showToast(error.message || error, { type: 'error', duration: 3600 }); }
    }

    async function toggleLearnerTask(taskId, enabled) {
        await fetch(`/api/learner/tasks/${encodeURIComponent(taskId)}`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({enabled})});
        await fetchLearnerState();
    }

    async function deleteLearnerTask(taskId) {
        const previewResponse = await fetch(`/api/learner/tasks/${encodeURIComponent(taskId)}/delete-preview`, {method: 'POST'});
        const preview = await previewResponse.json();
        if (!previewResponse.ok) { showToast(preview.detail || 'Unable to review task deletion', { type: 'error', duration: 3600 }); return; }
        if ((preview.memory_preview?.blocked || []).length) {
            showToast('This task has protected memories. Unlock them before deleting the task and its history.', { type: 'error', duration: 3600 });
            return;
        }
        confirmAction(`Delete this learning task?\n\n${preview.run_count || 0} activity record(s) and ${preview.memory_count || 0} memories created by this task will also be permanently deleted.\n${memoryDeletionImpactText(preview.memory_preview || {})}\n\nThis cannot be undone.`, async () => {

            const response = await fetch(`/api/learner/tasks/${encodeURIComponent(taskId)}?confirmation_token=${encodeURIComponent(preview.confirmation_token || '')}`, {method: 'DELETE'});
            const data = await response.json();
            if (!response.ok || data.status !== 'success') { showToast(data.detail || data.memory_result?.message || 'Unable to delete task', { type: 'error', duration: 3600 }); return; }
            await fetchLearnerState();
    });    }

    async function openLearnerActivityModal() {
        const modal = document.getElementById('learner-activity-modal');
        if (modal) modal.style.display = 'flex';
        try { await fetchLearnerState(); } catch (_) {}
        renderLearnerActivityList();
    }

    function closeLearnerActivityModal() {
        const modal = document.getElementById('learner-activity-modal');
        if (modal) modal.style.display = 'none';
    }

    function renderLearnerActivityList() {
        const body = document.getElementById('learner-activity-body');
        if (!body) return;
        const runs = learnerState.runs || [];
        const unread = Number(learnerState.counts?.unread || 0);
        const readTerminal = runs.some(run => !['queued', 'running'].includes(run.status) && run.is_read);
        body.innerHTML = `<div class="learner-activity-toolbar">
            <div class="learner-activity-counts">
                <span class="st-badge running">${Number(learnerState.counts?.running || 0)} running</span>
                <span class="st-badge ${unread ? 'warning' : ''}">${unread} unread</span>
                <span class="st-badge">${runs.length} retained</span>
            </div>
            <div class="learner-activity-toolbar-actions">
                <button type="button" class="row-menu-btn" onclick="showLearnerActivityMenu(this, ${unread ? 'true' : 'false'}, ${readTerminal ? 'true' : 'false'})" title="More actions" aria-label="More actions" aria-haspopup="menu">${ic('more', 'icn-sm')}</button>
                <button class="workspace-primary-action" onclick="closeLearnerActivityModal(); openLearnerRoleModal();">${ic('book-open', 'icn-sm')}Open Learner</button>
            </div>
        </div><div class="learner-list">${runs.length ? runs.map(run => learnerRunCard(run)).join('') : `<div class="st-empty">${ic('book-open')}<span>No learning activity yet.</span></div>`}</div>`;
    }

    document.addEventListener('click', event => {
        const tab = event.target.closest('.learner-mobile-tabs .ws-tab');
        if (!tab) return;
        const root = tab.closest('.learner-role-body');
        if (!root) return;
        root.querySelectorAll('.learner-mobile-tabs .ws-tab').forEach(item => {
            const active = item === tab;
            item.classList.toggle('active', active);
            item.setAttribute('aria-selected', active ? 'true' : 'false');
        });
        root.querySelectorAll('[data-learner-col]').forEach(col => {
            col.hidden = col.dataset.learnerCol !== tab.dataset.learnerTab;
        });
    });

    function showLearnerActivityMenu(anchor, canMarkAll, canClear) {
        showMenuAt(anchor, [
            { label: 'Mark all read', icon: 'check', onClick: () => { if (canMarkAll) markAllLearnerRunsRead(); } },
            { label: 'Clear read activities', icon: 'trash', danger: true, onClick: () => { if (canClear) clearReadLearnerRuns(); } }
        ], { align: 'end' });
    }
    window.showLearnerActivityMenu = showLearnerActivityMenu;

    async function setLearnerRunRead(runId, isRead, rerender = true) {
        const response = await fetch(`/api/learner/runs/${encodeURIComponent(runId)}/read`, {
            method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({is_read: isRead})
        });
        const data = await response.json();
        if (!response.ok) { showToast(data.detail || 'Unable to update activity.', { type: 'error' }); return; }
        const run = (learnerState.runs || []).find(item => item.id === runId);
        if (run) Object.assign(run, data.run || {is_read: isRead});
        await fetchLearnerState();
        if (rerender) renderLearnerActivityList();
    }

    async function markAllLearnerRunsRead() {
        const response = await fetch('/api/learner/runs/read-all', {method: 'POST'});
        const data = await response.json();
        if (!response.ok) { showToast(data.detail || 'Unable to mark activity as read.', { type: 'error' }); return; }
        await fetchLearnerState(); renderLearnerActivityList();
    }

    async function deleteLearnerRun(runId, fromDetail = false) {
        const previewResponse = await fetch(`/api/learner/runs/${encodeURIComponent(runId)}/delete-preview`, {method: 'POST'});
        const preview = await previewResponse.json();
        if (!previewResponse.ok) { showToast(preview.detail || 'Unable to review activity deletion.', { type: 'error' }); return; }
        if ((preview.memory_preview?.blocked || []).length) {
            showToast('This activity has protected memories. Unlock them before deleting the activity.', { type: 'error' });
            return;
        }
        showConfirmDialog({
            title: 'Delete this activity record?',
            message: `${preview.memory_count || 0} memories created by this run will also be permanently deleted.`,
            impact: memoryDeletionImpactText(preview.memory_preview || {}).split('\n').filter(Boolean),
            confirmLabel: 'Delete activity',
            danger: true,
            onConfirm: async close => {
                close();
                const response = await fetch(`/api/learner/runs/${encodeURIComponent(runId)}?confirmation_token=${encodeURIComponent(preview.confirmation_token || '')}`, {method: 'DELETE'});
                const data = await response.json();
                if (!response.ok) { showToast(data.detail || 'Unable to delete activity.', { type: 'error' }); return; }
                showToast('Activity deleted.');
                await fetchLearnerState();
                renderLearnerActivityList();
            }
        });
    }

    async function clearReadLearnerRuns() {
        const previewResponse = await fetch('/api/learner/runs/delete-preview', {
            method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({scope: 'read'})
        });
        const preview = await previewResponse.json();
        if (!previewResponse.ok) { showToast(preview.detail || 'Unable to review activity cleanup.', { type: 'error' }); return; }
        if (!preview.run_count) return;
        if ((preview.memory_preview?.blocked || []).length) {
            showToast('Some selected activities have protected memories. Unlock them before clearing the history.', { type: 'error' });
            return;
        }
        showConfirmDialog({
            title: `Clear ${preview.run_count} read activities?`,
            message: `${preview.memory_count || 0} memories created by those runs will also be permanently deleted.`,
            impact: memoryDeletionImpactText(preview.memory_preview || {}).split('\n').filter(Boolean),
            confirmLabel: 'Clear read',
            danger: true,
            onConfirm: async close => {
                close();
                const response = await fetch(`/api/learner/runs?scope=read&confirmation_token=${encodeURIComponent(preview.confirmation_token || '')}`, {method: 'DELETE'});
                const data = await response.json();
                if (!response.ok) { showToast(data.detail || 'Unable to clear activity.', { type: 'error' }); return; }
                showToast('Read activities cleared.');
                await fetchLearnerState(); renderLearnerActivityList();
            }
        });
    }

    async function openLearnerRun(runId) {
        const modal = document.getElementById('learner-activity-modal');
        const body = document.getElementById('learner-activity-body');
        if (modal) modal.style.display = 'flex';
        if (body) body.innerHTML = '<div class="learner-empty">Loading report...</div>';
        try {
            const response = await fetch(`/api/learner/runs/${encodeURIComponent(runId)}`, {cache: 'no-store'});
            const run = await response.json();
            if (!response.ok) throw new Error(run.detail || 'Unable to load report');
            if (!run.is_read && !['queued', 'running'].includes(run.status)) {
                await setLearnerRunRead(run.id, true, false);
                run.is_read = true;
            }
            const sources = (run.sources || []).map(source => `<a href="${escapeHtml(source.url || '#')}" target="_blank" rel="noopener noreferrer"><strong>${escapeHtml(source.title || source.creator || 'Source')}</strong><small>${escapeHtml([source.domain || source.creator, source.source_type || source.transcript_status, source.acceptance === 'cross_checked' ? 'independently cross-checked' : source.acceptance === 'flexible_readable' ? 'broader trusted source' : source.acceptance === 'trusted_snippet' ? 'trusted search snippet' : '', source.published_at ? formatVideoDate(source.published_at) : ''].filter(Boolean).join(' · '))}</small></a>`).join('');
            const memory = run.memory_result || {};
            const terminal = !['queued', 'running'].includes(run.status);
            body.innerHTML = `<div class="learner-result-head ${escapeHtml(run.status || '')}"><button onclick="renderLearnerActivityList()">${ic('arrow-left', 'icn-sm')}Back</button><div><span>${escapeHtml(learnerRunLabel(run))}</span><h3>${escapeHtml(run.task_title || 'Learning report')}</h3><small>${escapeHtml(run.finished_at ? formatVideoDate(run.finished_at) : `${run.stage || ''} · ${run.progress || 0}%`)}</small></div>${terminal ? `<div class="learner-result-actions"><button onclick="setLearnerRunRead('${escapeJsString(run.id)}', false)">${ic('eye', 'icn-sm')}Mark unread</button><button class="danger" onclick="deleteLearnerRun('${escapeJsString(run.id)}', true)">${ic('trash', 'icn-sm')}Delete</button></div>` : ''}</div>${run.quality_notice ? `<div class="learner-caution-box"><strong>Source quality notice</strong><span>${escapeHtml(run.quality_notice)}</span></div>` : ''}${run.error ? `<div class="learner-error-box">${escapeHtml(run.error)}</div>` : ''}${run.summary ? `<article class="learner-report">${renderMarkdownSafe(run.summary)}</article>` : '<div class="learner-empty">This run has not produced a report yet.</div>'}<section class="learner-result-section"><strong>Sources</strong><div class="learner-source-list">${sources || '<div class="learner-empty">No source metadata.</div>'}</div></section>${run.memory_result ? `<section class="learner-result-section"><strong>Memory management</strong><p>${memory.skipped ? escapeHtml(memory.reason || 'Skipped') : `${(memory.created_ids || []).length} created · ${(memory.merged_ids || []).length} merged · ${(memory.archived_duplicate_ids || []).length} duplicates archived · ${(memory.preserved_important_ids || []).length} important memories preserved · graph ${memory.graph_rebuilt ? 'rebuilt' : 'unchanged'}`}</p></section>` : ''}`;
        } catch (error) { if (body) body.innerHTML = `<div class="learner-error-box">${escapeHtml(error.message || error)}</div>`; }
    }

    function formatAnkiDuration(milliseconds) {
        const seconds = Math.max(0, Math.round(Number(milliseconds || 0) / 1000));
        const hours = Math.floor(seconds / 3600);
        const minutes = Math.floor((seconds % 3600) / 60);
        if (hours) return `${hours}h ${minutes}m`;
        if (minutes) return `${minutes}m`;
        return `${seconds}s`;
    }

    function formatAnkiTimestamp(value) {
        if (!value) return '';
        const parsed = new Date(value);
        return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString();
    }

    function ankiFriendlyError(response, data) {
        const detail = data?.detail || '';
        if (response.status === 404) return { message: 'The Anki service is not available on the backend.', detail };
        if (response.status >= 500) return { message: 'The backend could not read the Anki data.', detail };
        return { message: 'Anki could not be reached.', detail: detail || `HTTP ${response.status}` };
    }

    async function ankiResponse(response) {
        let data = {};
        try { data = await response.json(); } catch (_) {}
        if (!response.ok) {
            const friendly = ankiFriendlyError(response, data);
            const error = new Error(friendly.message);
            error.detail = friendly.detail;
            throw error;
        }
        return data;
    }

    async function fetchAnkiState() {
        const response = await fetch('/api/anki/state', {cache: 'no-store'});
        ankiState = await ankiResponse(response);
        if (!ankiDraftPath) ankiDraftPath = ankiState.settings?.data_path || '';
        return ankiState;
    }

    async function initializeAnki() {
        try {
            await fetchAnkiState();
            if (ankiState.configured) await refreshAnkiState(true);
        } catch (error) {
            console.warn('Anki startup refresh failed', error);
        }
    }

    async function openAnkiRoleModal() {
        closeRolesModal();
        const modal = document.getElementById('anki-role-modal');
        if (modal) modal.style.display = 'flex';
        const body = document.getElementById('anki-role-body');
        if (body) body.innerHTML = '<div class="anki-empty-state">Loading Anki study status...</div>';
        try {
            await fetchAnkiState();
            renderAnkiRole();
            if (ankiState.configured) await refreshAnkiState(true);
        } catch (error) {
            if (body) body.innerHTML = `<div class="anki-error-box">${escapeHtml(error.message || error)}</div>`;
        }
    }

    function closeAnkiRoleModal() {
        const modal = document.getElementById('anki-role-modal');
        if (modal) modal.style.display = 'none';
    }

    function backToRolesFromAnki() {
        closeAnkiRoleModal();
        openRolesModal();
    }

    function renderAnkiRole() {
        const body = document.getElementById('anki-role-body');
        if (!body) return;
        const state = ankiState || {};
        const settings = state.settings || {};
        const summary = state.summary || null;
        const profiles = Array.isArray(state.profiles) ? state.profiles : [];
        const selectedProfile = state.profile || settings.profile || profiles[0]?.name || '';
        const pathValue = ankiDraftPath || settings.data_path || '';
        const profileOptions = profiles.length
            ? profiles.map(profile => `<option value="${escapeHtml(profile.name || '')}"${profile.name === selectedProfile ? ' selected' : ''}>${escapeHtml(profile.name || 'Profile')}</option>`).join('')
            : '<option value="">Detect profiles first</option>';
        const sourceMeta = state.connected
            ? `Profile ${escapeHtml(state.profile || '')} · refreshed ${escapeHtml(formatAnkiTimestamp(state.last_refreshed_at))}`
            : state.stale
                ? `Showing the last successful result · ${escapeHtml(formatAnkiTimestamp(state.last_refreshed_at))}`
                : 'Connect an Anki profile to begin.';
        const statusClass = state.status === 'error' ? 'error' : state.stale ? 'stale' : '';
        const statusText = state.error
            ? escapeHtml(state.error)
            : state.connected
                ? `Read-only connection active · source updated ${escapeHtml(formatAnkiTimestamp(state.source_modified_at))}`
                : 'The source database is never modified. Saya reads a temporary SQLite snapshot and removes it after calculating statistics.';

        let dashboard = '';
        if (summary) {
            const daily = Array.isArray(state.daily_activity) ? state.daily_activity : [];
            const maxReviews = Math.max(1, ...daily.map(item => Number(item.reviews || 0)));
            const chart = daily.map((item, index) => {
                const reviews = Number(item.reviews || 0);
                const height = reviews ? Math.max(4, Math.round(reviews * 100 / maxReviews)) : 2;
                const label = (index % 5 === 0 || index === daily.length - 1) ? String(item.date || '').slice(5) : '';
                return `<div class="anki-trend-day${index === daily.length - 1 ? ' today' : ''}" title="${escapeHtml(item.date || '')}: ${reviews} reviews · ${formatAnkiDuration(item.time_ms)}"><div class="anki-trend-bar" style="height:${height}%"></div>${label ? `<span class="anki-trend-label">${escapeHtml(label)}</span>` : ''}</div>`;
            }).join('');
            const decks = Array.isArray(state.decks) ? state.decks : [];
            const deckRows = decks.length ? decks.map(deck => `
                <div class="anki-deck-row" style="--anki-depth:${Math.max(0, Number(deck.depth || 0))}">
                    <div class="anki-deck-name" title="${escapeHtml(deck.name || '')}">${escapeHtml(String(deck.name || '').replaceAll('::', ' / '))}</div>
                    <div class="anki-deck-stat">${Number(deck.total_cards || 0).toLocaleString()}</div>
                    <div class="anki-deck-stat due">${Number(deck.due_cards || 0).toLocaleString()}</div>
                    <div class="anki-deck-stat">${Number(deck.new_cards || 0).toLocaleString()}</div>
                    <div class="anki-deck-stat">${Number(deck.today_reviews || 0).toLocaleString()}</div>
                </div>`).join('') : '<div class="anki-empty-state">No regular decks were found in this profile.</div>';
            dashboard = `
                <section class="anki-metric-grid">
                    <div class="anki-metric-card accent"><span>Today reviews</span><strong>${Number(summary.today_reviews || 0).toLocaleString()}</strong><small>${Number(summary.today_cards || 0).toLocaleString()} unique cards</small></div>
                    <div class="anki-metric-card"><span>Study time</span><strong>${formatAnkiDuration(summary.today_time_ms)}</strong><small>Answer time recorded by Anki</small></div>
                    <div class="anki-metric-card"><span>Accuracy</span><strong>${Number(summary.today_accuracy || 0).toFixed(1)}%</strong><small>${Number(summary.today_again || 0).toLocaleString()} Again answer(s)</small></div>
                    <div class="anki-metric-card"><span>Current streak</span><strong>${Number(summary.current_streak || 0)} days</strong><small>Longest ${Number(summary.longest_streak || 0)} days</small></div>
                </section>
                <section class="anki-panel">
                    <div class="anki-panel-head"><div><strong>Collection overview</strong><small>Current profile totals and cards available to study.</small></div></div>
                    <div class="anki-metric-grid">
                        <div class="anki-metric-card"><span>Total cards</span><strong>${Number(summary.total_cards || 0).toLocaleString()}</strong><small>${Number(summary.total_notes || 0).toLocaleString()} notes</small></div>
                        <div class="anki-metric-card"><span>Due now</span><strong>${Number(summary.due_cards || 0).toLocaleString()}</strong><small>Review and learning queues</small></div>
                        <div class="anki-metric-card"><span>New cards</span><strong>${Number(summary.new_cards || 0).toLocaleString()}</strong><small>Not introduced yet</small></div>
                        <div class="anki-metric-card"><span>All-time reviews</span><strong>${Number(summary.all_time_reviews || 0).toLocaleString()}</strong><small>${formatAnkiDuration(summary.all_time_time_ms)} recorded</small></div>
                    </div>
                </section>
                <section class="anki-panel">
                    <div class="anki-panel-head"><div><strong>Last 30 days</strong><small>Every answered card counts toward the daily check-in.</small></div></div>
                    <div class="anki-trend-chart" aria-label="Anki reviews over the last 30 days">${chart}</div>
                </section>
                <section class="anki-panel">
                    <div class="anki-panel-head"><div><strong>Decks</strong><small>Parent rows include their subdecks.</small></div></div>
                    <div class="anki-deck-list">
                        <div class="anki-deck-row header"><div>Deck</div><div class="anki-deck-stat">Cards</div><div class="anki-deck-stat">Due</div><div class="anki-deck-stat">New</div><div class="anki-deck-stat">Today</div></div>
                        ${deckRows}
                    </div>
                </section>`;
        } else if (state.configured && state.error) {
            dashboard = `
                <div class="st-page-error">
                    ${ic('alert')}
                    <strong>No usable Anki statistics are available yet.</strong>
                    <span>${escapeHtml(String(state.error).replace(/Anki request failed: \d+/g, 'Anki could not be reached.'))}</span>
                    <button type="button" class="workspace-secondary-action" onclick="refreshAnkiState()">${ic('refresh', 'icn-sm')}Retry</button>
                </div>`;
        } else {
            dashboard = '<div class="anki-empty-state">Set the folder that contains your Anki profiles. On Windows, the recommended location is usually <strong>%APPDATA%\\Anki2</strong>.</div>';
        }

        body.innerHTML = `
            <section class="page-intro">
                <div class="page-intro-copy"><h3 class="page-intro-title">Review rhythm</h3><p class="page-intro-sub">Read deck and review history directly from your Anki profile without changing the collection.</p><div class="anki-role-hero-meta">${sourceMeta}</div></div>
                <div class="anki-checkin-badge${summary?.checked_in_today ? ' checked' : ''}"><strong>${summary?.checked_in_today ? 'Checked in today' : 'Not checked in'}</strong><span>${summary ? `${Number(summary.today_reviews || 0)} review(s)` : 'No data yet'}</span></div>
            </section>
            <section class="anki-panel">
                <div class="anki-panel-head"><div><strong>Anki data source</strong><small>Select the Anki2 root, a profile folder, or collection.anki2 itself.</small></div><div class="anki-panel-actions">${state.configured ? `<button onclick="refreshAnkiState()">${ic('refresh', 'icn-sm')}Refresh</button>` : ''}</div></div>
                <div class="anki-settings-grid">
                    <label><span>Data folder or collection file</span><input id="anki-data-path" type="text" value="${escapeHtml(pathValue)}" placeholder="${escapeHtml(settings.default_data_path || '%APPDATA%\\Anki2')}" oninput="ankiDraftPath=this.value"></label>
                    <label><span>Profile</span><select id="anki-profile-select"${profiles.length ? '' : ' disabled'}>${profileOptions}</select></label>
                </div>
                <div class="anki-settings-actions"><div><button onclick="useDefaultAnkiPath()">${ic('check', 'icn-sm')}Use recommended</button><button onclick="discoverAnkiProfiles()">${ic('search', 'icn-sm')}Detect profiles</button></div><button class="primary" onclick="saveAnkiSettings()">${ic('key', 'icn-sm')}Connect and refresh</button></div>
                <div id="anki-connection-status" class="anki-connection-status ${statusClass}">${statusText}</div>
                <p class="anki-safe-note">Only aggregate study statistics are kept in Saya's running state. Card contents and the temporary database snapshot are not retained.</p>
            </section>
            ${state.configured ? `
            <div class="danger-zone">
                <div class="danger-zone-title">${ic('alert', 'icn-sm')}Danger zone</div>
                <p class="danger-zone-desc">Disconnecting removes the saved Anki folder and profile from Saya. Your Anki collection itself is never changed.</p>
                <button type="button" class="workspace-secondary-action" style="color:var(--color-danger); border-color:rgba(248,113,113,.4);" onclick="disconnectAnki()">${ic('close', 'icn-sm')}Disconnect Anki</button>
            </div>` : ''}
            ${dashboard}`;
    }

    function setAnkiStatus(message, kind = '') {
        const status = document.getElementById('anki-connection-status');
        if (!status) return;
        status.textContent = message || '';
        status.className = `anki-connection-status ${kind}`.trim();
    }

    async function discoverAnkiProfiles() {
        if (ankiBusy) return;
        const input = document.getElementById('anki-data-path');
        const dataPath = String(input?.value || '').trim();
        ankiDraftPath = dataPath;
        if (!dataPath) { setAnkiStatus('Choose an Anki data folder first.', 'error'); return; }
        ankiBusy = true;
        setAnkiStatus('Looking for Anki profiles...');
        try {
            const response = await fetch('/api/anki/discover', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({data_path: dataPath})
            });
            const discovered = await ankiResponse(response);
            ankiDraftPath = discovered.data_path || dataPath;
            ankiState = {
                ...ankiState,
                profiles: discovered.profiles || [],
                settings: {...(ankiState.settings || {}), data_path: ankiDraftPath}
            };
            renderAnkiRole();
            setAnkiStatus(`${(discovered.profiles || []).length} profile(s) found. Choose one, then connect.`);
        } catch (error) {
            setAnkiStatus(error.message || error, 'error');
        } finally { ankiBusy = false; }
    }

    async function useDefaultAnkiPath() {
        const defaultPath = ankiState.settings?.default_data_path || '%APPDATA%\\Anki2';
        ankiDraftPath = defaultPath;
        const input = document.getElementById('anki-data-path');
        if (input) input.value = defaultPath;
        await discoverAnkiProfiles();
    }

    async function saveAnkiSettings() {
        if (ankiBusy) return;
        const dataPath = String(document.getElementById('anki-data-path')?.value || '').trim();
        const profile = String(document.getElementById('anki-profile-select')?.value || '').trim();
        if (!dataPath) { setAnkiStatus('Choose an Anki data folder first.', 'error'); return; }
        ankiBusy = true;
        setAnkiStatus('Creating a safe read-only snapshot and calculating study status...');
        try {
            const response = await fetch('/api/anki/settings', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({data_path: dataPath, profile})
            });
            ankiState = await ankiResponse(response);
            ankiDraftPath = ankiState.settings?.data_path || dataPath;
            renderAnkiRole();
        } catch (error) {
            setAnkiStatus(error.message || error, 'error');
        } finally { ankiBusy = false; }
    }

    async function refreshAnkiState(silent = false) {
        if (ankiBusy) return;
        ankiBusy = true;
        if (!silent) setAnkiStatus('Reading the latest committed Anki reviews...');
        try {
            const response = await fetch('/api/anki/refresh', {method: 'POST'});
            ankiState = await ankiResponse(response);
            ankiDraftPath = ankiState.settings?.data_path || ankiDraftPath;
            if (document.getElementById('anki-role-modal')?.style.display === 'flex') renderAnkiRole();
        } catch (error) {
            if (!silent) setAnkiStatus(error.message || error, 'error');
            else console.warn('Anki refresh failed', error);
        } finally { ankiBusy = false; }
    }

    async function disconnectAnki() {
        if (ankiBusy) return;
        confirmAction('Disconnect this Anki profile? The Anki collection itself will not be changed.', async () => {
        ankiBusy = true;
        try {
            const response = await fetch('/api/anki/settings', {method: 'DELETE'});
            ankiState = await ankiResponse(response);
            ankiDraftPath = '';
            renderAnkiRole();
        } catch (error) {
            setAnkiStatus(error.message || error, 'error');
        } finally { ankiBusy = false; }
        });
    }

    function closeAssistantRoleModal() {
        const modal = document.getElementById('assistant-role-modal');
        if (modal) modal.style.display = 'none';
    }

    async function backToRolesFromAssistant() {
        if (currentRoleMode === 'assistant') {
            await exitAssistantRoleMode({openRoles: true, clearChat: true});
            return;
        }
        closeAssistantRoleModal();
        openRolesModal();
    }

    async function fetchAssistantRoleState() {
        const response = await fetch('/api/kb/roles/assistant/state');
        if (!response.ok) throw new Error(`Assistant state failed: ${response.status}`);
        assistantRoleState = await response.json();
        return assistantRoleState;
    }

    function markAssistantMemoryChangedThisEntry() {
        assistantMemoryChangedSinceEntry = true;
        localStorage.setItem('saya_assistant_role_memory_changed', 'true');
    }

    async function assistantMemoryChangedThisEntry() {
        try {
            await fetchAssistantRoleState();
        } catch (e) {
            console.warn("Unable to check Assistant memory state before exit", e);
        }
        const latestStamp = assistantRoleState?.last_read_at || '';
        const stampChanged = Boolean(
            assistantRoleEntryMemoryStamp &&
            latestStamp &&
            latestStamp !== assistantRoleEntryMemoryStamp
        );
        return assistantMemoryChangedSinceEntry || stampChanged;
    }

    async function openAssistantRoleModal() {
        closeRolesModal();
        const modal = document.getElementById('assistant-role-modal');
        const body = document.getElementById('assistant-role-body');
        if (!modal || !body) return;
        modal.style.display = 'flex';
        syncAssistantStrongLogicToggle();
        body.innerHTML = `
            <div class="assistant-role-loading">
                <div class="st-skeleton st-skeleton-title"></div>
                <div class="st-skeleton st-skeleton-text" style="width:88%"></div>
                <div class="st-skeleton st-skeleton-text" style="width:64%"></div>
                <div class="st-skeleton st-skeleton-row"><span class="st-skeleton st-skeleton-avatar"></span><span class="st-skeleton st-skeleton-body"><span class="st-skeleton st-skeleton-text" style="width:70%"></span></span></div>
                <div class="st-skeleton st-skeleton-row"><span class="st-skeleton st-skeleton-avatar"></span><span class="st-skeleton st-skeleton-body"><span class="st-skeleton st-skeleton-text" style="width:52%"></span></span></div>
            </div>`;
        try {
            await fetchAssistantRoleState();
            renderAssistantRoleModal();
        } catch (e) {
            console.warn('Assistant role unavailable:', e);
            body.innerHTML = `
                <div class="st-page-error">
                    ${ic('alert')}
                    <strong>Assistant could not be loaded.</strong>
                    <span>Nothing was changed. Check that the backend is running, then try again.</span>
                    <button type="button" class="workspace-secondary-action" onclick="openAssistantRoleModal()">${ic('refresh', 'icn-sm')}Retry</button>
                </div>`;
        }
    }

    function renderAssistantRoleModal() {
        const body = document.getElementById('assistant-role-body');
        if (!body) return;
        const state = assistantRoleState || {};
        const sessions = state.sessions || [];
        const curatedExtractions = state.curated_extractions || [];
        const pathMemories = state.path_memories || [];
        const vaultPath = state.vault_path || state.default_vault_path || '';
        const lastRead = state.last_read_at ? `Last read: ${escapeHtml(state.last_read_at)}` : 'No Obsidian read yet.';
        const curatedHtml = curatedExtractions.length
            ? curatedExtractions.map(item => `
                <article class="assistant-memory-item curated">
                    <div class="assistant-memory-item-copy">
                        <div class="assistant-memory-item-title"><span class="assistant-important-dot"></span><strong>${escapeHtml(item.title || item.path || 'Curated note')}</strong></div>
                        <small>${escapeHtml(item.path || '')}${item.chapter ? ` · ${escapeHtml(item.chapter)}` : ''}</small>
                        <small>${escapeHtml(item.mode || 'simple')} extraction · ${Number(item.content_chars || 0).toLocaleString()} characters</small>
                    </div>
                    <button class="assistant-memory-remove" onclick="removeAssistantExtractedMemory('${escapeJsString(item.memory_id)}')">${ic('trash', 'icn-sm')}Remove</button>
                </article>
            `).join('')
            : `<div class="st-empty">${ic('star')}<span>No note has been marked important yet.</span></div>`;
        const pathMemoryHtml = pathMemories.length
            ? pathMemories.map(item => `
                <div class="assistant-memory-item description">
                    <div class="assistant-memory-item-copy"><strong>${escapeHtml(item.path || 'Vault path')}</strong><small>${escapeHtml(item.description || '')}</small></div>
                </div>
            `).join('')
            : '';
        const chatHtml = sessions.length
            ? sessions.map(session => `
                <div class="assistant-chat-item">
                    <div>
                        <strong>${escapeHtml(session.preview || session.name || session.id)}</strong>
                        <small>${escapeHtml(sessionLastQaTime(session) || 'No completed exchange')}</small>
                    </div>
                    <button onclick="openAssistantRoleSession('${escapeJsString(session.id)}')">${ic('chat', 'icn-sm')}Open</button>
                </div>
            `).join('')
            : `<div class="st-empty">${ic('chat')}<span>No Assistant chats yet.</span></div>`;
        const activeHtml = currentRoleMode === 'assistant'
            ? '<div class="about-you-warning">Assistant role is active. Ordinary long-term memory is disabled for Assistant chats.</div>'
            : '<div class="assistant-role-hint">Start or open an Assistant chat to use this role. Assistant chats use only Assistant role memory plus the current conversation.</div>';

        body.innerHTML = `
            <section class="page-intro">
                <div class="page-intro-copy"><h3 class="page-intro-title">Assistant</h3><p class="page-intro-sub">Browse the vault broadly, then promote only valuable notes or chapters into long-term memory.</p></div>
                <div class="assistant-role-metrics"><span><strong>${Number(state.file_count || 0)}</strong>notes scanned</span><span><strong>${curatedExtractions.length}</strong>important memories</span></div>
            </section>
            ${activeHtml}
            <section class="assistant-role-panel">
                <div class="assistant-role-section-title">Obsidian library</div>
                <div class="assistant-role-row">
                    <input id="assistant-vault-path" type="text" value="${escapeHtml(vaultPath)}" placeholder="Obsidian vault path">
                </div>
                <div class="assistant-role-hint">Batch reading builds the Assistant-only outline and one short Learning Map. It does not automatically turn every note into long-term memory.</div>
                <div class="assistant-role-actions">
                    <button onclick="readAssistantVault('rough')">${ic('book-open', 'icn-sm')}Rough batch read</button>
                    <button onclick="readAssistantVault('detailed')">${ic('book-open', 'icn-sm')}Detailed batch read</button>
                </div>
                <div id="assistant-role-status" class="assistant-role-hint">${lastRead}${state.file_count ? ` · ${Number(state.file_count)} notes` : ''}</div>
            </section>
            <section class="assistant-role-panel">
                <div class="assistant-dedicated-memory-header">
                    <div><div class="assistant-role-section-title">Important Obsidian memory</div><div class="assistant-role-hint">Select a note or chapter and extract one bounded memory into the curated high-priority pool.</div></div>
                    <button class="primary" onclick="openAssistantMemoryPicker()">${ic('folder', 'icn-sm')}Curate note</button>
                </div>
                <div class="assistant-role-list">${curatedHtml}</div>
            </section>
            ${pathMemories.length ? `
            <section class="assistant-role-panel assistant-path-notes-panel">
                <div class="assistant-role-section-title">Path descriptions</div>
                <div class="assistant-role-hint">Your own guidance for how Assistant should interpret a folder or file. These are not model-generated note memories.</div>
                <div class="assistant-role-list">${pathMemoryHtml}</div>
            </section>` : ''}
            <section class="assistant-role-panel">
                <div class="assistant-role-section-title">Dedicated chats</div>
                <div class="assistant-role-actions">
                    <button class="primary" onclick="startAssistantRoleSession()">${ic('message-plus', 'icn-sm')}New Assistant chat</button>
                </div>
                <div class="assistant-role-list">${chatHtml}</div>
            </section>
        `;
    }

    const assistantMemoryPickerState = {
        vaultPath: '',
        currentPath: '',
        parentPath: null,
        selectedPath: null,
        selectedType: null,
        noteOutline: null,
        extractionMode: 'simple'
    };

    function closeAssistantMemoryPicker() {
        const modal = document.getElementById('assistant-memory-picker-modal');
        if (modal) modal.style.display = 'none';
    }

    async function openAssistantMemoryPicker() {
        const vaultPath = document.getElementById('assistant-vault-path')?.value.trim()
            || assistantRoleState?.vault_path
            || assistantRoleState?.default_vault_path
            || '';
        assistantMemoryPickerState.vaultPath = vaultPath;
        assistantMemoryPickerState.currentPath = '';
        assistantMemoryPickerState.parentPath = null;
        assistantMemoryPickerState.selectedPath = null;
        assistantMemoryPickerState.selectedType = null;
        assistantMemoryPickerState.noteOutline = null;
        assistantMemoryPickerState.extractionMode = 'simple';
        document.querySelectorAll('input[name="assistant-extraction-mode"]').forEach(radio => {
            radio.checked = radio.value === 'simple';
        });
        syncAssistantExtractionMode();
        const description = document.getElementById('assistant-memory-description');
        if (description) description.value = '';
        const selected = document.getElementById('assistant-memory-picker-selected');
        if (selected) selected.textContent = 'Nothing selected';
        const extractionOptions = document.getElementById('assistant-note-extraction-options');
        if (extractionOptions) extractionOptions.hidden = true;
        const descriptionField = document.getElementById('assistant-memory-description-field');
        if (descriptionField) descriptionField.style.display = 'flex';
        const saveDescription = document.getElementById('assistant-save-description-button');
        if (saveDescription) saveDescription.style.display = '';
        const modal = document.getElementById('assistant-memory-picker-modal');
        if (modal) modal.style.display = 'flex';
        await Promise.all([browseAssistantMemoryFolder(''), loadAssistantExtractionLimits()]);
    }

    async function browseAssistantMemoryFolder(relativePath) {
        const list = document.getElementById('assistant-memory-picker-list');
        const status = document.getElementById('assistant-memory-picker-status');
        if (list) renderSurfaceState(list, { kind: 'loading', title: 'Loading library', message: 'Reading available folders and notes…', icon: 'folder' });
        if (status) status.textContent = '';
        try {
            const response = await fetch('/api/kb/roles/assistant/browse', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    vault_path: assistantMemoryPickerState.vaultPath,
                    relative_path: relativePath || ''
                })
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || data.message || 'Unable to browse library');
            assistantMemoryPickerState.vaultPath = data.vault_path || assistantMemoryPickerState.vaultPath;
            assistantMemoryPickerState.currentPath = data.current_path || '';
            assistantMemoryPickerState.parentPath = data.parent_path;
            renderAssistantMemoryPickerEntries(data.entries || []);
            const vault = document.getElementById('assistant-memory-picker-vault');
            if (vault) vault.textContent = assistantMemoryPickerState.vaultPath;
            const current = document.getElementById('assistant-memory-picker-current');
            if (current) current.textContent = assistantMemoryPickerState.currentPath || 'Vault root';
            const up = document.getElementById('assistant-memory-picker-up');
            if (up) up.disabled = data.parent_path === null || data.parent_path === undefined;
        } catch (e) {
            renderSurfaceState(list, {
                title: 'Library could not be opened',
                message: 'Check the Obsidian connection and try again.',
                retry: () => browseAssistantMemoryFolder(assistantMemoryPickerState.currentPath || ''),
                details: e,
                icon: 'folder'
            });
            if (status) status.textContent = 'Library request failed. Use Retry to try again.';
        }
    }

    function renderAssistantMemoryPickerEntries(entries) {
        const list = document.getElementById('assistant-memory-picker-list');
        if (!list) return;
        list.innerHTML = '';
        if (!entries.length) {
            list.innerHTML = '<div class="about-you-empty">No folders or Markdown files here.</div>';
            return;
        }
        entries.forEach(entry => {
            const row = document.createElement('div');
            row.className = 'assistant-memory-entry';
            const main = document.createElement('div');
            main.className = 'assistant-memory-entry-main';
            main.tabIndex = 0;
            main.setAttribute('role', 'button');
            main.innerHTML = `<span aria-hidden="true">${entry.type === 'folder' ? '▸' : '·'}</span>` +
                `<span class="assistant-memory-entry-name">${escapeHtml(entry.name || entry.path)}</span>`;
            main.onclick = () => selectAssistantMemoryPath(entry.path, entry.type);
            main.onkeydown = event => {
                if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    selectAssistantMemoryPath(entry.path, entry.type);
                }
            };
            row.appendChild(main);
            if (entry.type === 'folder') {
                const open = document.createElement('button');
                open.type = 'button';
                open.textContent = 'Open';
                open.onclick = () => browseAssistantMemoryFolder(entry.path);
                row.appendChild(open);
            } else {
                const mark = document.createElement('button');
                mark.type = 'button';
                mark.className = 'assistant-memory-mark';
                mark.textContent = '★ Mark important';
                mark.onclick = () => selectAssistantMemoryPath(entry.path, entry.type);
                row.appendChild(mark);
            }
            list.appendChild(row);
        });
    }

    function browseAssistantMemoryParent() {
        if (assistantMemoryPickerState.parentPath === null || assistantMemoryPickerState.parentPath === undefined) return;
        browseAssistantMemoryFolder(assistantMemoryPickerState.parentPath);
    }

    function selectAssistantMemoryCurrentFolder() {
        selectAssistantMemoryPath(assistantMemoryPickerState.currentPath || '', 'folder');
    }

    async function selectAssistantMemoryPath(path, type) {
        assistantMemoryPickerState.selectedPath = path || '';
        assistantMemoryPickerState.selectedType = type;
        const displayPath = path || 'Vault root';
        const selected = document.getElementById('assistant-memory-picker-selected');
        if (selected) selected.textContent = `${type}: ${displayPath}`;
        const existing = (assistantRoleState?.path_memories || []).find(item =>
            String(item.path || '').toLowerCase() === displayPath.toLowerCase()
        );
        const description = document.getElementById('assistant-memory-description');
        if (description) description.value = existing?.description || '';
        const extractionOptions = document.getElementById('assistant-note-extraction-options');
        const descriptionField = document.getElementById('assistant-memory-description-field');
        const saveDescription = document.getElementById('assistant-save-description-button');
        if (type === 'file') {
            if (extractionOptions) extractionOptions.hidden = false;
            if (descriptionField) descriptionField.style.display = 'none';
            if (saveDescription) saveDescription.style.display = 'none';
            await loadAssistantNoteOutline(path || '');
        } else {
            assistantMemoryPickerState.noteOutline = null;
            if (extractionOptions) extractionOptions.style.display = 'none';
            if (descriptionField) descriptionField.style.display = 'flex';
            if (saveDescription) saveDescription.style.display = '';
        }
    }

    async function loadAssistantExtractionLimits() {
        try {
            const response = await fetch('/api/kb/stats');
            if (!response.ok) return;
            const stats = await response.json();
            const limits = stats.content_limits || {};
            const simple = document.getElementById('assistant-simple-limit');
            const complex = document.getElementById('assistant-complex-limit');
            if (simple) simple.textContent = `Up to ${Number(limits.knowledge_simple || 1200).toLocaleString()} characters`;
            if (complex) complex.textContent = `Up to ${Number(limits.knowledge_complex || 3600).toLocaleString()} characters`;
        } catch (error) {
            console.warn('Unable to load extraction limits', error);
        }
    }

    async function loadAssistantNoteOutline(relativePath) {
        const status = document.getElementById('assistant-memory-picker-status');
        const select = document.getElementById('assistant-note-chapter-select');
        const sourceInfo = document.getElementById('assistant-note-source-info');
        if (status) status.textContent = 'Reading note structure...';
        if (select) select.innerHTML = '<option value="">Entire note</option>';
        try {
            const response = await fetch('/api/kb/roles/assistant/note-outline', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({vault_path: assistantMemoryPickerState.vaultPath, relative_path: relativePath})
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || 'Unable to read note outline');
            assistantMemoryPickerState.noteOutline = data;
            if (select) {
                (data.chapters || []).forEach(chapter => {
                    const option = document.createElement('option');
                    option.value = chapter.selector;
                    option.textContent = chapter.label || chapter.title;
                    select.appendChild(option);
                });
            }
            if (sourceInfo) {
                sourceInfo.textContent = `${Number(data.char_count || 0).toLocaleString()} source characters · ${(data.chapters || []).length} chapters` +
                    (data.source_truncated ? ' · source exceeds read ceiling' : '');
            }
            if (status) status.textContent = '';
        } catch (error) {
            assistantMemoryPickerState.noteOutline = null;
            if (status) status.textContent = error.message || String(error);
        }
    }

    function syncAssistantExtractionMode() {
        const selected = document.querySelector('input[name="assistant-extraction-mode"]:checked');
        assistantMemoryPickerState.extractionMode = selected?.value === 'complex' ? 'complex' : 'simple';
        document.querySelectorAll('.assistant-extraction-mode').forEach(card => {
            card.classList.toggle('selected', card.querySelector('input')?.checked === true);
        });
    }

    async function extractAssistantNoteMemory() {
        const status = document.getElementById('assistant-memory-picker-status');
        const button = document.getElementById('assistant-extract-memory-button');
        if (assistantMemoryPickerState.selectedType !== 'file' || !assistantMemoryPickerState.noteOutline) {
            if (status) status.textContent = 'Select a readable Markdown note first.';
            return;
        }
        syncAssistantExtractionMode();
        const mode = assistantMemoryPickerState.extractionMode;
        const chapterSelector = document.getElementById('assistant-note-chapter-select')?.value || '';
        const scope = chapterSelector ? 'selected chapter' : 'entire note';
        confirmAction(`Use the configured ${mode === 'complex' ? 'strong memory' : 'memory'} model to read the ${scope} and save one important memory?`, async () => {

            if (button) button.disabled = true;
            if (status) status.textContent = `Reading the ${scope} and generating a ${mode} memory...`;
            try {
                saveCurrentApiSettings();
                const response = await fetch('/api/kb/roles/assistant/extract', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        ...collectArchiveGenerationSettings(),
                        vault_path: assistantMemoryPickerState.vaultPath,
                        relative_path: assistantMemoryPickerState.selectedPath,
                        chapter_selector: chapterSelector,
                        mode
                    })
                });
                const data = await response.json();
                if (!response.ok) throw new Error(data.detail || data.message || 'Extraction failed');
                assistantRoleState = data.assistant_state || assistantRoleState;
                markAssistantMemoryChangedThisEntry();
                closeAssistantMemoryPicker();
                renderAssistantRoleModal();
            } catch (error) {
                if (status) status.textContent = error.message || String(error);
            } finally {
                if (button) button.disabled = false;
            }
    });    }

    async function removeAssistantExtractedMemory(memoryId) {
        confirmAction('Remove this important Obsidian memory from active recall? It will be archived, not destroyed.', async () => {

            try {
                const response = await fetch('/api/kb/roles/assistant/extraction/remove', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({memory_id: memoryId})
                });
                const data = await response.json();
                if (!response.ok) throw new Error(data.detail || 'Unable to remove memory');
                assistantRoleState = data;
                markAssistantMemoryChangedThisEntry();
                renderAssistantRoleModal();
            } catch (error) {
                showToast(error.message || String(error), { type: 'error', duration: 3600 });
            }
    });    }

    async function saveAssistantPathMemory() {
        const status = document.getElementById('assistant-memory-picker-status');
        const description = document.getElementById('assistant-memory-description')?.value.trim() || '';
        if (!assistantMemoryPickerState.selectedType) {
            if (status) status.textContent = 'Select a Markdown file or folder first.';
            return;
        }
        if (!description) {
            if (status) status.textContent = 'Add a description first.';
            return;
        }
        if (status) status.textContent = 'Saving memory...';
        try {
            const response = await fetch('/api/kb/roles/assistant/path-memory', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    vault_path: assistantMemoryPickerState.vaultPath,
                    relative_path: assistantMemoryPickerState.selectedPath,
                    description
                })
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || data.message || 'Unable to save memory');
            assistantRoleState = data;
            markAssistantMemoryChangedThisEntry();
            closeAssistantMemoryPicker();
            renderAssistantRoleModal();
        } catch (e) {
            if (status) status.textContent = e.message || String(e);
        }
    }

    async function readAssistantVault(detail) {
        const label = detail === 'detailed' ? 'Detailed batch read' : 'Rough batch read';
        confirmAction(`${label} will refresh the Assistant outline and short Learning Map. Your curated important-note memories will remain. Continue?`, async () => {

            const status = document.getElementById('assistant-role-status');
            if (status) status.innerText = detail === 'detailed' ? 'Reading vault in detailed mode...' : 'Reading vault in rough mode...';
            try {
                const response = await fetch('/api/kb/roles/assistant/read', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        vault_path: document.getElementById('assistant-vault-path')?.value || '',
                        detail
                    })
                });
                const data = await response.json();
                if (!response.ok) throw new Error(data.detail || data.message || "Read failed");
                assistantRoleState = data;
                markAssistantMemoryChangedThisEntry();
                renderAssistantRoleModal();
            } catch (e) {
                if (status) status.innerText = `Read failed: ${e.message || e}`;
            }
    });    }

    async function startAssistantRoleSession() {
        try {
            setCurrentRoleMode('assistant');
            await startNewSessionSilently("", {roleMode: 'assistant'});
            closeAssistantRoleModal();
            collapseSidebarForAssistantChat();
        } catch (e) {
            showToast("Failed to start Assistant chat: " + (e.message || e), { type: 'error', duration: 3600 });
        }
    }

    async function openAssistantRoleSession(sessionId) {
        if (!sessionId) return;
        setCurrentRoleMode('assistant');
        closeAssistantRoleModal();
        const loaded = await loadSession(sessionId, {skipConfirm: true, skipAlert: true, roleMode: 'assistant'});
        if (loaded) {
            collapseSidebarForAssistantChat();
        } else {
            await openAssistantRoleModal();
        }
    }

    async function exitAssistantRoleMode(options = {}) {
        try {
            const memoryChanged = await assistantMemoryChangedThisEntry();
            let updated = false;
            if (memoryChanged) {
                const syncAssistantExitMemory = async () => {
                    const response = await fetch('/api/kb/roles/assistant/exit', {method: 'POST'});
                    const data = await response.json();
                    if (!response.ok) throw new Error(data.detail || data.message || "Exit failed");
                    return Boolean(data.updated);
                };
                if (options.autoUpdate === true) {
                    updated = await syncAssistantExitMemory();
                } else {
                    confirmAction("Assistant dedicated memory changed during this role session. Update the locked normal-mode learning memory now?", async () => {
                        await syncAssistantExitMemory();
                        showToast('Learning memory updated.');
                    }, { title: 'Update the learning memory?', confirmLabel: 'Update' });
                }
            }
            setCurrentRoleMode('');
            if (options.clearChat !== false) {
                await startNewSessionSilently(projectSidebarViewId || currentProjectId || "", {skipRoleExit: true});
            } else {
                fetchSessionList();
            }
            if (!options.silent && memoryChanged && updated) {
                showToast("Assistant role exited. Normal-mode learning memory updated.", { type: 'info', duration: 3600 });
            }
            if (options.openRoles) {
                closeAssistantRoleModal();
                openRolesModal();
            } else if (!options.keepModalClosed) {
                await openAssistantRoleModal();
            }
            return true;
        } catch (e) {
            if (!options.silent) showToast("Failed to exit Assistant role: " + (e.message || e), { type: 'error', duration: 3600 });
            return false;
        }
    }

    function backToToolsFromAboutYou() {
        const modal = document.getElementById('memory-modal');
        if (modal) modal.style.display = 'none';
        openToolsModal();
    }

    function backToAboutYouFromQuest() {
        closeQuestModal();
        openAboutYouModal();
    }

    function backToToolsFromVideoRecommendations() {
        closeVideoRecommendationsModal();
        openToolsModal();
    }

    function backToToolsFromVideoDigest() {
        closeVideoDigestModal();
        openToolsModal();
    }

    const ABOUT_YOU_PERSONALITY_QUESTIONS = [
        {
            key: 'energy',
            label: 'Energy source',
            options: [
                'I recharge by being alone and thinking quietly.',
                'I recharge by talking, sharing, and being around people.',
                'It depends heavily on context.'
            ]
        },
        {
            key: 'work_style',
            label: 'Work style',
            options: [
                'I like focused execution once the goal is clear.',
                'I like exploring options before committing.',
                'I alternate between exploration and execution.'
            ]
        },
        {
            key: 'information',
            label: 'Information style',
            options: [
                'I trust concrete details and lived experience first.',
                'I like patterns, possibilities, and abstract models first.',
                'I use both depending on the problem.'
            ]
        },
        {
            key: 'decisions',
            label: 'Decision style',
            options: [
                'I prefer logical consistency and direct truth.',
                'I prefer emotional nuance and impact on people.',
                'I want both weighed explicitly.'
            ]
        },
        {
            key: 'decision_speed',
            label: 'Decision speed',
            options: [
                'Help me decide quickly with a clear recommendation.',
                'Give me tradeoffs and let me choose deliberately.',
                'Use quick recommendations for small things, tradeoffs for big things.'
            ]
        },
        {
            key: 'structure',
            label: 'Structure style',
            options: [
                'I like plans, closure, and clear next steps.',
                'I like flexibility, exploration, and options staying open.',
                'I need structure for hard work and freedom for creative work.'
            ]
        },
        {
            key: 'detail',
            label: 'Detail level',
            options: [
                'Start concise and only expand if needed.',
                'Give me rich detail and context.',
                'Give a concise answer first, then useful depth.'
            ]
        },
        {
            key: 'social',
            label: 'Social pace',
            options: [
                'Slow trust, deep bonds.',
                'Warm quickly, then refine trust over time.',
                'Highly situational.'
            ]
        },
        {
            key: 'emotion_tone',
            label: 'Emotional tone',
            options: [
                'Steady and calm.',
                'Warm and emotionally expressive.',
                'Match my mood without overdoing it.'
            ]
        },
        {
            key: 'stress',
            label: 'Stress response',
            options: [
                'I want calm analysis and practical steps.',
                'I want emotional reassurance before solving.',
                'I want both, in that order.'
            ]
        },
        {
            key: 'failure_support',
            label: 'Failure support',
            options: [
                'Analyze what happened and make a recovery plan.',
                'Acknowledge the emotional hit first.',
                'Reassure me briefly, then help me move.'
            ]
        },
        {
            key: 'support',
            label: 'Preferred support',
            options: [
                'Be direct and help me act.',
                'Be gentle and help me feel understood.',
                'Match the situation.'
            ]
        },
        {
            key: 'feedback',
            label: 'Feedback preference',
            options: [
                'Be direct even when it is uncomfortable.',
                'Be gentle and frame critique carefully.',
                'Be direct, but keep the tone kind.'
            ]
        },
        {
            key: 'communication',
            label: 'Communication',
            options: [
                'Concise and precise.',
                'Warm, expressive, and detailed.',
                'Start concise, expand when useful.'
            ]
        },
        {
            key: 'initiative',
            label: 'Agent initiative',
            options: [
                'Take initiative and implement reasonable next steps.',
                'Ask before changing direction or taking action.',
                'Take initiative for low-risk work, ask for high-risk choices.'
            ]
        },
        {
            key: 'memory',
            label: 'Memory preference',
            options: [
                'Only remember explicit important facts.',
                'Propose memory candidates for confirmation.',
                'Remember more proactively when confidence is high.'
            ]
        },
        {
            key: 'boundaries',
            label: 'Boundaries',
            options: [
                'Keep roleplay and practical work clearly separated.',
                'Blend warmth naturally into practical work.',
                'Follow the tone I set in the moment.'
            ]
        }
    ];
    const ABOUT_YOU_PERSONALITY_DETAILED_QUESTIONS = [
        {
            key: 'mbti_energy',
            label: 'Energy orientation',
            options: [
                'More introverted: depth, quiet recovery, selective sharing.',
                'More extraverted: interactive thinking, social energy, spoken processing.',
                'Ambiverted: changes with context and trust.'
            ]
        },
        {
            key: 'mbti_perception',
            label: 'Perception style',
            options: [
                'Sensing-leaning: concrete examples, details, evidence, proven steps.',
                'Intuition-leaning: patterns, meaning, possibilities, systems.',
                'Balanced: concrete grounding plus abstract synthesis.'
            ]
        },
        {
            key: 'mbti_judgment',
            label: 'Judgment style',
            options: [
                'Thinking-leaning: consistency, logic, precision, tradeoffs.',
                'Feeling-leaning: values, relationships, emotional impact, harmony.',
                'Balanced: logic and emotional consequence both matter.'
            ]
        },
        {
            key: 'mbti_lifestyle',
            label: 'Lifestyle style',
            options: [
                'Judging-leaning: plans, closure, deadlines, stable routines.',
                'Perceiving-leaning: flexibility, improvisation, options, discovery.',
                'Hybrid: structure for commitments, openness for creative work.'
            ]
        },
        {
            key: 'trust',
            label: 'Trust building',
            options: [
                'Reliability and factual accuracy build trust fastest.',
                'Emotional attunement and loyalty build trust fastest.',
                'Both matter; trust grows through repeated good judgment.'
            ]
        },
        {
            key: 'attachment',
            label: 'Reassurance needs',
            options: [
                'I need little reassurance unless something is wrong.',
                'I value explicit reassurance and emotional presence.',
                'I prefer reassurance only when stress or uncertainty is high.'
            ]
        },
        {
            key: 'focus',
            label: 'Focus support',
            options: [
                'Help me narrow scope and remove distractions.',
                'Help me stay emotionally engaged with the task.',
                'Help me alternate between clarity and momentum.'
            ]
        },
        {
            key: 'planning',
            label: 'Planning style',
            options: [
                'I like step-by-step plans with clear milestones.',
                'I like lightweight direction and room to adapt.',
                'I like a plan, but it should not feel rigid.'
            ]
        },
        {
            key: 'learning',
            label: 'Learning style',
            options: [
                'Teach through examples and concrete walkthroughs.',
                'Teach through concepts and first principles.',
                'Teach concept first, then examples.'
            ]
        },
        {
            key: 'creativity',
            label: 'Creative collaboration',
            options: [
                'Generate bold options and help me choose.',
                'Iterate gently around my existing taste.',
                'Offer a strong point of view with alternatives.'
            ]
        },
        {
            key: 'motivation',
            label: 'Motivation style',
            options: [
                'Progress, clarity, and competence motivate me.',
                'Meaning, connection, and emotional resonance motivate me.',
                'I need both achievement and meaning.'
            ]
        },
        {
            key: 'conflict',
            label: 'Conflict style',
            options: [
                'Address conflict directly and quickly.',
                'Reduce emotional tension before solving.',
                'Name the issue calmly and solve with care.'
            ]
        },
        {
            key: 'change',
            label: 'Change handling',
            options: [
                'I adapt well when the reason is clear.',
                'I need emotional orientation before changing direction.',
                'I can adapt if the next step is concrete.'
            ]
        },
        {
            key: 'privacy',
            label: 'Privacy preference',
            options: [
                'Be cautious and remember only explicitly approved personal details.',
                'Remember meaningful personal context when it helps continuity.',
                'Ask before saving sensitive or intimate details.'
            ]
        },
        {
            key: 'autonomy',
            label: 'Autonomy preference',
            options: [
                'Act independently when the goal is clear.',
                'Collaborate closely before acting.',
                'Use judgment: act on reversible work, ask on irreversible work.'
            ]
        },
        {
            key: 'humor',
            label: 'Humor preference',
            options: [
                'Keep humor light and rare.',
                'Use playful warmth when the moment allows.',
                'Match my humor level.'
            ]
        },
        {
            key: 'rituals',
            label: 'Continuity rituals',
            options: [
                'Prefer practical continuity without rituals.',
                'Like small recurring emotional references.',
                'Use continuity only when it feels natural.'
            ]
        },
        {
            key: 'communication',
            label: 'Response shape',
            options: [
                'Answer first, brief explanation after.',
                'Explain reasoning and emotional nuance.',
                'Start with the answer, then structured detail.'
            ]
        },
        {
            key: 'support',
            label: 'Support mode',
            options: [
                'Coach me firmly.',
                'Comfort me gently.',
                'Switch between coach and companion.'
            ]
        },
        {
            key: 'memory',
            label: 'Memory behavior',
            options: [
                'Conservative: only explicit saved memories.',
                'Balanced: suggest important memory candidates.',
                'Proactive: save high-confidence stable facts.'
            ]
        }
    ];
    let aboutYouPersonalityEntries = [];
    let aboutYouPersonalityMode = 'simple';
    let aboutYouPersonalityFullEntry = null;
    let aboutYouPossessionState = null;

    function todayIsoDate() {
        return new Date().toISOString().slice(0, 10);
    }

    function openAboutYouModal() {
        closeToolsModal();
        const modal = document.getElementById('memory-modal');
        const body = document.getElementById('modal-body');
        setMemoryModalHeader("About you", "backToToolsFromAboutYou()");
        body.innerHTML = `
            <div class="page-intro">
                <div class="page-intro-copy">
                    <h3 class="page-intro-title">About you</h3>
                    <p class="page-intro-sub">The personal context Saya remembers and how she uses it.</p>
                </div>
            </div>
            <div class="form-section">
                <div class="form-section-title">Memory</div>
                <div class="nav-list">
                    <button class="nav-list-row" onclick="openMemoryViewer()">
                        <span class="nav-row-ic"><span class="icn icn-layers" aria-hidden="true"></span></span>
                        <span class="nav-row-copy"><span class="nav-row-title">Memories</span><span class="nav-row-sub">View, edit, and explore the relationship network</span></span>
                        <span class="icn icn-chevron-right icn-sm nav-row-chevron" aria-hidden="true"></span>
                    </button>
                    <button class="nav-list-row" onclick="openMemorySettings()">
                        <span class="nav-row-ic"><span class="icn icn-sliders" aria-hidden="true"></span></span>
                        <span class="nav-row-copy"><span class="nav-row-title">Memory settings</span><span class="nav-row-sub">Retrieval, metadata, and merge behavior</span></span>
                        <span class="icn icn-chevron-right icn-sm nav-row-chevron" aria-hidden="true"></span>
                    </button>
                </div>
            </div>
            <div class="form-section">
                <div class="form-section-title">Personal profile</div>
                <div class="nav-list">
                    <button class="nav-list-row" onclick="openAboutYouPersonality()">
                        <span class="nav-row-ic"><span class="icn icn-user" aria-hidden="true"></span></span>
                        <span class="nav-row-copy"><span class="nav-row-title">Personality</span><span class="nav-row-sub">Dated self-assessment bound to protected memory</span></span>
                        <span class="icn icn-chevron-right icn-sm nav-row-chevron" aria-hidden="true"></span>
                    </button>
                    <button class="nav-list-row" onclick="openAboutYouPossessions()">
                        <span class="nav-row-ic"><span class="icn icn-star" aria-hidden="true"></span></span>
                        <span class="nav-row-copy"><span class="nav-row-title">Important possessions</span><span class="nav-row-sub">Protected slots for meaningful belongings or objects</span></span>
                        <span class="icn icn-chevron-right icn-sm nav-row-chevron" aria-hidden="true"></span>
                    </button>
                </div>
            </div>
            <div class="form-section">
                <div class="form-section-title">Goals</div>
                <div class="nav-list">
                    <button class="nav-list-row" onclick="openQuestModal()">
                        <span class="nav-row-ic"><span class="icn icn-zap" aria-hidden="true"></span></span>
                        <span class="nav-row-copy"><span class="nav-row-title">Life quests</span><span class="nav-row-sub">Personal goals and completed quest memory</span></span>
                        <span class="icn icn-chevron-right icn-sm nav-row-chevron" aria-hidden="true"></span>
                    </button>
                </div>
            </div>
        `;
        modal.style.display = 'flex';
    }

    async function openAboutYouPersonality() {
        const modal = document.getElementById('memory-modal');
        const body = document.getElementById('modal-body');
        setMemoryModalHeader("About you · User personality", "openAboutYouModal()");
        renderSurfaceState(body, { kind: 'loading', title: 'Loading personality profile', message: 'Reading the protected profile slot…', icon: 'user' });
        modal.style.display = 'flex';
        try {
            const response = await fetch('/api/kb/about_you/personality');
            const data = await response.json();
            aboutYouPersonalityMode = data.mode || 'simple';
            aboutYouPersonalityEntries = data.entries || [];
            aboutYouPersonalityFullEntry = data.full_entry || null;
            renderAboutYouPersonality();
        } catch (e) {
            renderSurfaceState(body, {
                title: 'Personality profile could not be loaded',
                message: 'Your existing profile is unchanged. Try again when the backend is available.',
                retry: () => openAboutYouPersonality(),
                details: e,
                icon: 'user'
            });
        }
    }

    function renderAboutYouPersonality(view = null) {
        setMemoryModalHeader("About you · User personality", "openAboutYouModal()");
        if (!view && aboutYouPersonalityMode === 'full' && aboutYouPersonalityFullEntry) {
            renderAboutYouPersonalityFullSummary();
            return;
        }
        const body = document.getElementById('modal-body');
        const questionHtml = ABOUT_YOU_PERSONALITY_QUESTIONS.map(question => `
            <label class="about-you-field">
                <span>${escapeHtml(question.label)}</span>
                <select id="about-personality-${escapeHtml(question.key)}">
                    ${question.options.map(option => `<option value="${escapeHtml(option)}">${escapeHtml(option)}</option>`).join('')}
                </select>
            </label>
        `).join('');
        const entryHtml = aboutYouPersonalityEntries.length
            ? aboutYouPersonalityEntries.slice().reverse().map(entry => `
                <div class="about-you-entry">
                    <div>
                        <strong>${escapeHtml(entry.date || '')}</strong>
                        <div>${escapeHtml(Object.values(entry.answers || {}).filter(Boolean).slice(0, 2).join(' · ') || 'Saved assessment')}</div>
                    </div>
                    <div class="about-you-entry-actions">
                        <button onclick="editAboutYouPersonality('${escapeJsString(entry.id)}')">Edit</button>
                        <button onclick="deleteAboutYouPersonality('${escapeJsString(entry.id)}')">Delete</button>
                    </div>
                </div>
            `).join('')
            : '<div class="about-you-empty">No personality assessment yet.</div>';
        const fullWarning = aboutYouPersonalityMode === 'full'
            ? '<div class="about-you-warning">Saving this simple questionnaire will replace the existing detailed profile in the protected memory slot.</div>'
            : '';

        body.innerHTML = `
            <input id="about-personality-id" type="hidden" value="">
            <div class="about-you-form">
                ${fullWarning}
                <label class="about-you-field about-you-date-field">
                    <span>Date</span>
                    <input id="about-personality-date" type="date" value="${todayIsoDate()}">
                </label>
                <div class="about-you-survey-grid">${questionHtml}</div>
                <label class="about-you-field">
                    <span>Optional note</span>
                    <textarea id="about-personality-note" rows="3" placeholder="Anything Saya should understand about how you think or feel..."></textarea>
                </label>
                <div class="about-you-hint">Simple personality memory keeps at most two dated versions in one protected memory slot.</div>
                <div class="about-you-actions">
                    <button onclick="renderAboutYouDetailedPersonality()">Detailed MBTI-like questionnaire</button>
                    <button onclick="resetAboutYouPersonalityForm()">New dated entry</button>
                    <button onclick="saveAboutYouPersonality()">Save personality memory</button>
                </div>
            </div>
            <div class="about-you-section-title">Saved assessments</div>
            <div>${entryHtml}</div>
        `;
    }

    function renderAboutYouPersonalityFullSummary() {
        setMemoryModalHeader("About you · User personality", "openAboutYouModal()");
        const body = document.getElementById('modal-body');
        const entry = aboutYouPersonalityFullEntry || {};
        const answers = entry.answers || {};
        const answerRows = ABOUT_YOU_PERSONALITY_DETAILED_QUESTIONS
            .filter(question => answers[question.key])
            .map(question => `
                <div class="about-you-answer-row">
                    <strong>${escapeHtml(question.label)}</strong>
                    <span>${escapeHtml(answers[question.key])}</span>
                </div>
            `).join('');
        body.innerHTML = `
            <div class="about-you-form">
                <div class="about-you-warning">Detailed personality profile is active. It occupies the single protected personality memory slot and replaces dated simple versions.</div>
                <div class="about-you-section-title">Detailed profile · ${escapeHtml(entry.date || '')}</div>
                <div class="about-you-answer-list">${answerRows || '<div class="about-you-empty">No detailed answers saved.</div>'}</div>
                ${entry.note ? `<div class="about-you-note">${escapeHtml(entry.note)}</div>` : ''}
                <div class="about-you-actions">
                    <button onclick="renderAboutYouPersonality('simple')">Replace with simple dated questionnaire</button>
                    <button onclick="renderAboutYouDetailedPersonality()">Retake detailed questionnaire</button>
                    <button onclick="deleteAboutYouPersonality('${escapeJsString(entry.id || '')}')">Clear profile</button>
                </div>
            </div>
        `;
    }

    function renderAboutYouDetailedPersonality() {
        setMemoryModalHeader("About you · Detailed personality", "openAboutYouPersonality()");
        const body = document.getElementById('modal-body');
        const entry = aboutYouPersonalityMode === 'full' ? (aboutYouPersonalityFullEntry || {}) : {};
        const answers = entry.answers || {};
        const questionHtml = ABOUT_YOU_PERSONALITY_DETAILED_QUESTIONS.map(question => `
            <label class="about-you-field">
                <span>${escapeHtml(question.label)}</span>
                <select id="about-personality-full-${escapeHtml(question.key)}">
                    ${question.options.map(option => `<option value="${escapeHtml(option)}" ${answers[question.key] === option ? 'selected' : ''}>${escapeHtml(option)}</option>`).join('')}
                </select>
            </label>
        `).join('');
        const replaceWarning = aboutYouPersonalityMode === 'full'
            ? 'Saving will overwrite the current detailed profile.'
            : 'Saving this detailed questionnaire will overwrite all dated simple versions and become the only personality profile.';
        body.innerHTML = `
            <input id="about-personality-full-id" type="hidden" value="${escapeHtml(entry.id || '')}">
            <div class="about-you-form">
                <div class="about-you-warning">${replaceWarning}</div>
                <label class="about-you-field about-you-date-field">
                    <span>Date</span>
                    <input id="about-personality-full-date" type="date" value="${escapeHtml(entry.date || todayIsoDate())}">
                </label>
                <div class="about-you-survey-grid about-you-detailed-grid">${questionHtml}</div>
                <label class="about-you-field">
                    <span>Optional note</span>
                    <textarea id="about-personality-full-note" rows="4" placeholder="Anything especially important for Saya's long-term understanding...">${escapeHtml(entry.note || '')}</textarea>
                </label>
                <div class="about-you-actions">
                    <button onclick="openAboutYouPersonality()">Cancel</button>
                    <button onclick="saveAboutYouDetailedPersonality()">Save detailed profile</button>
                </div>
            </div>
        `;
    }

    async function saveAboutYouDetailedPersonality() {
        const hasExisting = aboutYouPersonalityMode === 'full' || aboutYouPersonalityEntries.length > 0;
        const doSaveDetailed = async () => {
        const answers = {};
        ABOUT_YOU_PERSONALITY_DETAILED_QUESTIONS.forEach(question => {
            answers[question.key] = document.getElementById(`about-personality-full-${question.key}`)?.value || '';
        });
        try {
            const response = await fetch('/api/kb/about_you/personality', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    mode: 'full',
                    id: document.getElementById('about-personality-full-id')?.value || null,
                    date: document.getElementById('about-personality-full-date')?.value || todayIsoDate(),
                    answers,
                    note: document.getElementById('about-personality-full-note')?.value || ''
                })
            });
            if (!response.ok) throw new Error("Save failed");
            const data = await response.json();
            aboutYouPersonalityMode = data.mode || 'full';
            aboutYouPersonalityEntries = data.entries || [];
            aboutYouPersonalityFullEntry = data.full_entry || null;
            renderAboutYouPersonalityFullSummary();
        } catch (e) {
            showToast("Failed to save detailed personality memory: " + e, { type: 'error', duration: 3600 });
        }
        };
        if (hasExisting) {
            confirmAction("This will rewrite the protected personality memory slot and replace previous personality versions. Continue?", doSaveDetailed, { confirmLabel: 'Save anyway', danger: true });
        } else {
            await doSaveDetailed();
        }
    }

    function resetAboutYouPersonalityForm() {
        const id = document.getElementById('about-personality-id');
        const date = document.getElementById('about-personality-date');
        const note = document.getElementById('about-personality-note');
        if (id) id.value = '';
        if (date) date.value = todayIsoDate();
        if (note) note.value = '';
    }

    function editAboutYouPersonality(id) {
        const entry = aboutYouPersonalityEntries.find(item => String(item.id) === String(id));
        if (!entry) return;
        document.getElementById('about-personality-id').value = entry.id || '';
        document.getElementById('about-personality-date').value = entry.date || todayIsoDate();
        document.getElementById('about-personality-note').value = entry.note || '';
        ABOUT_YOU_PERSONALITY_QUESTIONS.forEach(question => {
            const select = document.getElementById(`about-personality-${question.key}`);
            if (select && entry.answers && entry.answers[question.key]) {
                select.value = entry.answers[question.key];
            }
        });
    }

    async function saveAboutYouPersonality() {
        const activeId = document.getElementById('about-personality-id')?.value || '';
        const personalityWarning = aboutYouPersonalityMode === 'full'
            ? 'This will replace the existing detailed personality profile with a simple dated version.'
            : (!activeId && aboutYouPersonalityEntries.length >= 2)
                ? 'Only two dated simple personality versions are kept. Saving will overwrite the oldest version.'
                : '';
        const doSaveAboutYouPersonality = async () => {
        const answers = {};
        ABOUT_YOU_PERSONALITY_QUESTIONS.forEach(question => {
            answers[question.key] = document.getElementById(`about-personality-${question.key}`)?.value || '';
        });
        try {
            const response = await fetch('/api/kb/about_you/personality', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    mode: 'simple',
                    id: activeId || null,
                    date: document.getElementById('about-personality-date')?.value || todayIsoDate(),
                    answers,
                    note: document.getElementById('about-personality-note')?.value || ''
                })
            });
            if (!response.ok) throw new Error("Save failed");
            const data = await response.json();
            aboutYouPersonalityMode = data.mode || 'simple';
            aboutYouPersonalityEntries = data.entries || [];
            aboutYouPersonalityFullEntry = data.full_entry || null;
            renderAboutYouPersonality();
        } catch (e) {
            showToast("Failed to save personality memory: " + e, { type: 'error', duration: 3600 });
        }
        };
        if (personalityWarning) {
            confirmAction(personalityWarning + ' Continue?', doSaveAboutYouPersonality, { confirmLabel: 'Save anyway', danger: true });
        } else {
            await doSaveAboutYouPersonality();
        }
    }

    async function deleteAboutYouPersonality(id) {
        confirmAction("Delete this dated assessment? The protected memory slot will be rewritten.", async () => {

            try {
                const response = await fetch('/api/kb/about_you/personality/delete', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({id})
                });
                if (!response.ok) throw new Error("Delete failed");
                const data = await response.json();
                aboutYouPersonalityMode = data.mode || 'simple';
                aboutYouPersonalityEntries = data.entries || [];
                aboutYouPersonalityFullEntry = data.full_entry || null;
                renderAboutYouPersonality();
            } catch (e) {
                showToast("Failed to delete personality entry: " + e, { type: 'error', duration: 3600 });
            }
    });    }

    async function openAboutYouPossessions() {
        const modal = document.getElementById('memory-modal');
        const body = document.getElementById('modal-body');
        setMemoryModalHeader("About you · Important possessions", "openAboutYouModal()");
        renderSurfaceState(body, { kind: 'loading', title: 'Loading important possessions', message: 'Reading protected slots…', icon: 'star' });
        modal.style.display = 'flex';
        try {
            const response = await fetch('/api/kb/about_you/possessions');
            aboutYouPossessionState = await response.json();
            renderAboutYouPossessions();
        } catch (e) {
            renderSurfaceState(body, {
                title: 'Important possessions could not be loaded',
                message: 'Your protected slots are unchanged. Try again to refresh this view.',
                retry: () => openAboutYouPossessions(),
                details: e,
                icon: 'star'
            });
        }
    }

    function renderAboutYouPossessions() {
        const body = document.getElementById('modal-body');
        const slots = aboutYouPossessionState?.slots || [];
        const suggested = Number(aboutYouPossessionState?.suggested_length || 600);
        const activeSlots = slots.filter(slot => slot.active);
        const listHtml = activeSlots.length
            ? activeSlots.map(slot => `
                <div class="about-you-entry">
                    <div>
                        <strong>Slot ${escapeHtml(slot.slot)} · ${escapeHtml(slot.title || 'Untitled')}</strong>
                        <div>${escapeHtml((slot.content || '').slice(0, 140))}${(slot.content || '').length > 140 ? '...' : ''}</div>
                        <small>${Number(slot.parts || 1)} protected memory slot${Number(slot.parts || 1) > 1 ? 's' : ''}</small>
                    </div>
                    <div class="about-you-entry-actions">
                        <button onclick="loadAboutYouPossessionSlot('${escapeJsString(slot.slot)}')">Edit</button>
                        <button onclick="deleteAboutYouPossession('${escapeJsString(slot.slot)}')">Delete</button>
                    </div>
                </div>
            `).join('')
            : '<div class="about-you-empty">No important possessions saved yet.</div>';

        body.innerHTML = `
            <div class="about-you-form">
                <div class="about-you-possession-row">
                    <label class="about-you-field">
                        <span>Slot</span>
                        <select id="about-possession-slot" onchange="loadAboutYouPossessionSlot(this.value)">
                            ${Array.from({length: 8}, (_, index) => {
                                const slot = String(index + 1);
                                return `<option value="${slot}">Slot ${slot}</option>`;
                            }).join('')}
                        </select>
                    </label>
                    <label class="about-you-field">
                        <span>Title</span>
                        <input id="about-possession-title" type="text" placeholder="A concise title">
                    </label>
                </div>
                <label class="about-you-field">
                    <span>Details</span>
                    <textarea id="about-possession-content" rows="6" maxlength="2400" placeholder="Keep it concise when possible." oninput="updateAboutYouPossessionCount()"></textarea>
                </label>
                <div class="about-you-hint">
                    Suggested length: ${suggested} chars. Longer notes are split into additional protected memory slots.
                    <span id="about-possession-count">0</span> chars.
                </div>
                <div class="about-you-actions">
                    <button onclick="clearAboutYouPossessionForm()">Clear form</button>
                    <button onclick="saveAboutYouPossession()">Save possession memory</button>
                </div>
            </div>
            <div class="about-you-section-title">Saved possessions</div>
            <div>${listHtml}</div>
        `;
        updateAboutYouPossessionCount();
    }

    function getAboutYouPossession(slotId) {
        return (aboutYouPossessionState?.slots || []).find(slot => String(slot.slot) === String(slotId));
    }

    function loadAboutYouPossessionSlot(slotId) {
        const slot = getAboutYouPossession(slotId);
        const slotSelect = document.getElementById('about-possession-slot');
        if (slotSelect) slotSelect.value = String(slotId || '1');
        document.getElementById('about-possession-title').value = slot?.title || '';
        document.getElementById('about-possession-content').value = slot?.content || '';
        updateAboutYouPossessionCount();
    }

    function clearAboutYouPossessionForm() {
        document.getElementById('about-possession-title').value = '';
        document.getElementById('about-possession-content').value = '';
        updateAboutYouPossessionCount();
    }

    function updateAboutYouPossessionCount() {
        const count = document.getElementById('about-possession-content')?.value.length || 0;
        const target = document.getElementById('about-possession-count');
        if (target) target.innerText = String(count);
    }

    async function saveAboutYouPossession() {
        const slot = document.getElementById('about-possession-slot')?.value || '1';
        const content = document.getElementById('about-possession-content')?.value.trim() || '';
        if (!content) {
            showToast("Details are required. Use Delete to clear an existing slot.", { type: 'error', duration: 3600 });
            return;
        }
        try {
            const response = await fetch('/api/kb/about_you/possessions', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    slot,
                    title: document.getElementById('about-possession-title')?.value || '',
                    content
                })
            });
            if (!response.ok) throw new Error("Save failed");
            aboutYouPossessionState = await response.json();
            renderAboutYouPossessions();
            loadAboutYouPossessionSlot(slot);
        } catch (e) {
            showToast("Failed to save possession memory: " + e, { type: 'error', duration: 3600 });
        }
    }

    async function deleteAboutYouPossession(slot) {
        confirmAction("Clear this possession slot? Its protected memory content will be rewritten and archived.", async () => {

            try {
                const response = await fetch('/api/kb/about_you/possessions/delete', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({slot})
                });
                if (!response.ok) throw new Error("Delete failed");
                aboutYouPossessionState = await response.json();
                renderAboutYouPossessions();
            } catch (e) {
                showToast("Failed to delete possession slot: " + e, { type: 'error', duration: 3600 });
            }
    });    }

    async function initializeVideoRecommendations() {
        try {
            const brief = await fetchDailyBrief(false);
            if (brief?.settings?.auto_open && !videoRecommendationAutoOpened) {
                videoRecommendationAutoOpened = true;
                await openDailyBriefModal({prefetched: true});
            }
        } catch (e) {
            console.warn("Daily brief init failed", e);
        }
    }

    async function fetchDailyBrief(refreshVideos = false) {
        const endpoint = `/api/daily_brief?refresh_videos=${refreshVideos ? 'true' : 'false'}`;
        const response = await fetch(endpoint);
        if (!response.ok) throw new Error(`Daily brief failed: ${response.status}`);
        dailyBriefState = await response.json();
        if (dailyBriefState.video_state) {
            videoRecommendationState = dailyBriefState.video_state;
        }
        return dailyBriefState;
    }

    async function fetchVideoRecommendationState(refresh = false) {
        const endpoint = refresh
            ? '/api/video_recommendations/recommendations?refresh=true'
            : '/api/video_recommendations/state';
        const response = await fetch(endpoint);
        if (!response.ok) throw new Error(`Video recommendations failed: ${response.status}`);
        videoRecommendationState = await response.json();
        return videoRecommendationState;
    }

    async function openVideoRecommendationsModal(options = {}) {
        closeToolsModal();
        closeVideoDigestModal();
        const modal = document.getElementById('video-recommendations-modal');
        if (modal) modal.style.display = 'flex';
        setVideoRecommendationStatus(options.refresh ? 'Refreshing...' : 'Loading...');
        try {
            await fetchVideoRecommendationState(Boolean(options.refresh));
            renderVideoRecommendations();
        } catch (e) {
            console.error(e);
            setVideoRecommendationStatus("Unable to load recommendations.");
            renderVideoRecommendations();
        }
    }

    function closeVideoRecommendationsModal() {
        const modal = document.getElementById('video-recommendations-modal');
        if (modal) modal.style.display = 'none';
    }

    async function openDailyBriefModal(options = {}) {
        closeToolsModal();
        const modal = document.getElementById('video-digest-modal');
        if (modal) modal.style.display = 'flex';
        setVideoDigestStatus(options.refresh ? 'Refreshing brief...' : 'Loading brief...');
        try {
            if (!options.prefetched) {
                await fetchDailyBrief(Boolean(options.refresh));
            }
            renderDailyBrief();
        } catch (e) {
            console.error(e);
            setVideoDigestStatus("Unable to load Daily Brief.");
            renderDailyBrief();
        }
    }

    async function openVideoDigestModal(options = {}) {
        await openDailyBriefModal(options);
    }

    function closeVideoDigestModal() {
        const modal = document.getElementById('video-digest-modal');
        if (modal) modal.style.display = 'none';
    }

    function setVideoRecommendationStatus(text) {
        const status = document.getElementById('video-recommendation-status');
        if (status) status.innerText = text || '';
    }

    function setVideoDigestStatus(text) {
        const status = document.getElementById('video-digest-status');
        if (status) status.innerText = text || '';
    }

    function videoCredentialStatus(state) {
        const parts = [];
        if (state.youtube_api_key_configured) {
            parts.push(`YouTube API key: ${state.youtube_api_key_source || 'configured'}`);
        } else if (state.youtube_cookie_configured) {
            parts.push(`YouTube cookie: ${state.youtube_cookie_source || 'configured'}`);
        } else {
            parts.push('YouTube API key: not configured');
        }
        return parts.join(' / ');
    }

    function renderVideoRecommendations() {
        const state = videoRecommendationState || {};
        const autoOpen = document.getElementById('video-auto-open-checkbox');
        if (autoOpen) autoOpen.checked = Boolean(state.settings?.auto_open);
        renderVideoCreatorList(state.creators || []);
        renderVideoRecommendationList(state.management_videos || []);
        const resultCount = document.getElementById('video-workspace-result-count');
        if (resultCount) resultCount.textContent = String((state.management_videos || []).length);
        const checked = state.last_checked ? formatVideoDate(state.last_checked) : 'Not checked';
        const windowDays = state.window_days || state.settings?.recent_days || 3;
        setVideoRecommendationStatus(`Last check: ${checked} / Managing all updates from ${windowDays} days / ${videoCredentialStatus(state)}`);
    }

    function getDailyBriefCardSettings() {
        try {
            const saved = JSON.parse(localStorage.getItem(DAILY_BRIEF_CARD_SETTINGS_KEY) || '{}');
            return {
                order: Array.isArray(saved.order) ? saved.order.map(String) : [],
                edits: saved.edits && typeof saved.edits === 'object' ? saved.edits : {},
                custom: Array.isArray(saved.custom) ? saved.custom.filter(item => item && typeof item === 'object') : []
            };
        } catch (e) {
            console.warn("Saved Daily Brief card settings are invalid; using defaults.", e);
            return {order: [], edits: {}, custom: []};
        }
    }

    function saveDailyBriefCardSettings(settings) {
        const clean = {
            order: Array.isArray(settings.order) ? settings.order.map(String) : [],
            edits: settings.edits && typeof settings.edits === 'object' ? settings.edits : {},
            custom: Array.isArray(settings.custom) ? settings.custom : []
        };
        localStorage.setItem(DAILY_BRIEF_CARD_SETTINGS_KEY, JSON.stringify(clean));
        return clean;
    }

    function dailyBriefCardSlug(value) {
        return String(value || 'card')
            .trim()
            .toLowerCase()
            .replace(/[^a-z0-9]+/g, '_')
            .replace(/^_+|_+$/g, '') || 'card';
    }

    function dailyBriefCardId(card) {
        const known = {
            daily: 'daily',
            month: 'month',
            'life quests': 'life_quests',
            memory: 'memory',
            'memory review': 'memory',
            videos: 'videos',
            'video updates': 'videos',
            chats: 'chats',
            'chat days': 'chats',
            projects: 'projects',
            year: 'year',
            'total memory': 'total_memory',
            'starred memory': 'important_memory',
            'today tasks': 'today_tasks',
            'video sources': 'video_sources'
        };
        const label = String(card?.label || '').trim().toLowerCase();
        return String(card?.id || known[label] || dailyBriefCardSlug(label));
    }

    function normalizeDailyBriefBorderColor(value, fallback = '#5d5d5d') {
        const raw = String(value || '').trim();
        if (/^#[0-9a-f]{6}$/i.test(raw)) return raw.toLowerCase();
        if (/^#[0-9a-f]{3}$/i.test(raw)) {
            return `#${raw[1]}${raw[1]}${raw[2]}${raw[2]}${raw[3]}${raw[3]}`.toLowerCase();
        }
        return fallback;
    }

    function dailyBriefDefaultBorderColor(card) {
        const tone = dailyBriefToneClass(card?.tone || 'pending');
        return DAILY_BRIEF_DEFAULT_BORDER_COLORS[tone] || DAILY_BRIEF_DEFAULT_BORDER_COLORS.pending;
    }

    function dailyBriefCardBorderColor(card) {
        return normalizeDailyBriefBorderColor(card?.borderColor, dailyBriefDefaultBorderColor(card));
    }

    function normalizeDailyBriefCard(card, source = 'builtin') {
        const tone = dailyBriefToneClass(card?.tone || 'pending');
        return {
            id: dailyBriefCardId(card),
            source,
            label: String(card?.label || 'Card'),
            value: String(card?.value ?? ''),
            detail: String(card?.detail || ''),
            tone,
            borderColor: normalizeDailyBriefBorderColor(card?.borderColor, DAILY_BRIEF_DEFAULT_BORDER_COLORS[tone] || DAILY_BRIEF_DEFAULT_BORDER_COLORS.pending),
            hidden: Boolean(card?.hidden)
        };
    }

    function getDailyBriefCardCatalog(includeHidden = false) {
        const settings = getDailyBriefCardSettings();
        const builtins = (dailyBriefState?.available_cards || dailyBriefState?.cards || [])
            .map(card => normalizeDailyBriefCard(card, 'preset'));
        const custom = settings.custom.map(card => normalizeDailyBriefCard(card, 'custom'));
        const byId = new Map();

        [...builtins, ...custom].forEach(card => {
            const edit = settings.edits[card.id] || {};
            const resolved = {...card};
            ['label', 'value', 'detail', 'tone', 'borderColor'].forEach(field => {
                if (Object.prototype.hasOwnProperty.call(edit, field)) {
                    resolved[field] = String(edit[field] ?? '');
                }
            });
            if (Object.prototype.hasOwnProperty.call(edit, 'hidden')) {
                resolved.hidden = Boolean(edit.hidden);
            }
            resolved.tone = dailyBriefToneClass(resolved.tone);
            resolved.borderColor = normalizeDailyBriefBorderColor(resolved.borderColor, dailyBriefDefaultBorderColor(resolved));
            byId.set(resolved.id, resolved);
        });

        const orderedIds = [];
        settings.order.forEach(id => {
            if (byId.has(id) && !orderedIds.includes(id)) orderedIds.push(id);
        });
        byId.forEach((_, id) => {
            if (!orderedIds.includes(id)) orderedIds.push(id);
        });

        return orderedIds
            .map(id => byId.get(id))
            .filter(Boolean)
            .filter(card => includeHidden || !card.hidden);
    }

    function dailyBriefColorSwatches(id, selectedColor) {
        const safeId = escapeJsString(id);
        const selected = normalizeDailyBriefBorderColor(selectedColor);
        return DAILY_BRIEF_CARD_COLORS.map(color => {
            const isSelected = color.toLowerCase() === selected;
            return `
                <button type="button"
                    class="daily-brief-color-swatch ${isSelected ? 'is-selected' : ''}"
                    style="--swatch-color:${escapeHtml(color)}"
                    aria-label="Set border color ${escapeHtml(color)}"
                    onclick="setDailyBriefCardField('${safeId}', 'borderColor', '${escapeJsString(color)}', true)">
                </button>
            `;
        }).join('');
    }

    function splitDailyBriefOpening(text) {
        const fallback = 'Daily Brief is not available yet.';
        const raw = String(text || fallback).trim() || fallback;
        const pieces = raw.match(/[^.!?。！？]+[.!?。！？]+|[^.!?。！？]+$/g);
        const sentences = (pieces || [raw]).map(item => item.trim()).filter(Boolean);
        if (sentences.length <= 1) {
            const value = sentences[0] || fallback;
            if (value.length < 110) return [value];
            const midpoint = Math.floor(value.length / 2);
            const splitAt = value.indexOf(' ', midpoint) > 0 ? value.indexOf(' ', midpoint) : midpoint;
            return [value.slice(0, splitAt).trim(), value.slice(splitAt).trim()].filter(Boolean);
        }
        const firstCount = sentences.length <= 3 ? 1 : Math.ceil(sentences.length / 2);
        return [
            sentences.slice(0, firstCount).join(' '),
            sentences.slice(firstCount).join(' ')
        ].filter(Boolean);
    }

    function toggleDailyBriefCardSettings(force) {
        const panel = document.getElementById('daily-brief-card-settings');
        if (!panel) return;
        const show = typeof force === 'boolean' ? force : panel.hidden;
        panel.hidden = !show;
        if (show) renderDailyBriefCardSettings();
    }

    function renderDailyBriefCardSettings() {
        const list = document.getElementById('daily-brief-card-settings-list');
        if (!list) return;
        const cards = getDailyBriefCardCatalog(true);
        if (!cards.length) {
            list.innerHTML = '<div class="video-empty">No cards yet.</div>';
            return;
        }
        list.innerHTML = cards.map((card, index) => {
            const id = escapeJsString(card.id);
            const removable = card.source === 'custom';
            const sourceLabel = card.source === 'custom' ? 'Custom' : 'Preset';
            const borderColor = dailyBriefCardBorderColor(card);
            const colorControls = card.hidden ? '' : `
                <details class="daily-brief-color-details">
                    <summary>
                        <span>Border</span>
                        <span class="daily-brief-current-color" style="--current-border-color:${escapeHtml(borderColor)}"></span>
                    </summary>
                    <div class="daily-brief-color-row">
                        <div class="daily-brief-color-picker">
                            <span>Custom color</span>
                            <input type="color" value="${escapeHtml(borderColor)}" aria-label="Border color" oninput="setDailyBriefCardField('${id}', 'borderColor', this.value)">
                        </div>
                        <div class="daily-brief-color-swatches">
                            ${dailyBriefColorSwatches(card.id, borderColor)}
                        </div>
                    </div>
                </details>
            `;
            return `
                <article class="daily-brief-settings-card">
                    <div class="daily-brief-settings-card-top">
                        <label class="daily-brief-show-toggle">
                            <input type="checkbox" ${card.hidden ? '' : 'checked'} onchange="setDailyBriefCardField('${id}', 'hidden', !this.checked, true)">
                            <span>Show</span>
                        </label>
                        <span class="daily-brief-card-source">${sourceLabel}</span>
                        <div class="daily-brief-card-move-actions">
                            <button onclick="moveDailyBriefCard('${id}', -1)" title="Move up" ${index === 0 ? 'disabled' : ''}>Up</button>
                            <button onclick="moveDailyBriefCard('${id}', 1)" title="Move down" ${index === cards.length - 1 ? 'disabled' : ''}>Down</button>
                            ${removable ? `<button onclick="deleteDailyBriefCustomCard('${id}')" title="Delete">Delete</button>` : ''}
                        </div>
                    </div>
                    <div class="daily-brief-settings-fields">
                        <input type="text" value="${escapeHtml(card.label)}" placeholder="Label" oninput="setDailyBriefCardField('${id}', 'label', this.value)">
                        <input type="text" value="${escapeHtml(card.value)}" placeholder="Value" oninput="setDailyBriefCardField('${id}', 'value', this.value)">
                        <input type="text" value="${escapeHtml(card.detail)}" placeholder="Detail" oninput="setDailyBriefCardField('${id}', 'detail', this.value)">
                    </div>
                    ${colorControls}
                </article>
            `;
        }).join('');
    }

    function setDailyBriefCardField(id, field, value, refreshSettings = false) {
        const settings = getDailyBriefCardSettings();
        if (!settings.edits[id]) settings.edits[id] = {};
        if (field === 'hidden') {
            settings.edits[id][field] = Boolean(value);
        } else if (field === 'borderColor') {
            settings.edits[id][field] = normalizeDailyBriefBorderColor(value);
        } else {
            settings.edits[id][field] = String(value ?? '');
        }
        saveDailyBriefCardSettings(settings);
        renderDailyBrief();
        if (refreshSettings) renderDailyBriefCardSettings();
    }

    function moveDailyBriefCard(id, direction) {
        const settings = getDailyBriefCardSettings();
        const order = getDailyBriefCardCatalog(true).map(card => card.id);
        const index = order.indexOf(id);
        const target = index + Number(direction || 0);
        if (index < 0 || target < 0 || target >= order.length) return;
        [order[index], order[target]] = [order[target], order[index]];
        settings.order = order;
        saveDailyBriefCardSettings(settings);
        renderDailyBrief();
        renderDailyBriefCardSettings();
    }

    function addDailyBriefCustomCard() {
        const labelInput = document.getElementById('daily-brief-new-card-label');
        const valueInput = document.getElementById('daily-brief-new-card-value');
        const detailInput = document.getElementById('daily-brief-new-card-detail');
        const label = labelInput?.value.trim() || 'Custom';
        const settings = getDailyBriefCardSettings();
        const card = {
            id: `custom_${Date.now().toString(36)}`,
            label,
            value: valueInput?.value.trim() || '0',
            detail: detailInput?.value.trim() || '',
            borderColor: '#e6e6e6',
            tone: 'pending'
        };
        settings.custom.push(card);
        settings.order = getDailyBriefCardCatalog(true).map(item => item.id).concat(card.id);
        saveDailyBriefCardSettings(settings);
        [labelInput, valueInput, detailInput].filter(Boolean).forEach(input => { input.value = ''; });
        renderDailyBrief();
        renderDailyBriefCardSettings();
    }

    function deleteDailyBriefCustomCard(id) {
        const settings = getDailyBriefCardSettings();
        settings.custom = settings.custom.filter(card => String(card.id) !== String(id));
        delete settings.edits[id];
        settings.order = settings.order.filter(item => String(item) !== String(id));
        saveDailyBriefCardSettings(settings);
        renderDailyBrief();
        renderDailyBriefCardSettings();
    }

    function resetDailyBriefCardSettings() {
        confirmAction("Reset Daily Brief card layout?", async () => {

            localStorage.removeItem(DAILY_BRIEF_CARD_SETTINGS_KEY);
            renderDailyBrief();
            renderDailyBriefCardSettings();
    });    }

    function renderDailyBrief() {
        const brief = dailyBriefState || {};
        const opening = document.getElementById('daily-brief-opening');
        const grid = document.getElementById('daily-brief-grid');
        const title = document.getElementById('daily-brief-title');

        if (title) title.innerText = brief.today?.date ? `Today with Saya / ${brief.today.date}` : 'Today with Saya';
        if (opening) {
            opening.innerHTML = splitDailyBriefOpening(brief.opening_message)
                .map(paragraph => `<p>${escapeHtml(paragraph)}</p>`)
                .join('');
        }
        if (grid) {
            grid.innerHTML = '';
            getDailyBriefCardCatalog(false).forEach(card => {
                const div = document.createElement('article');
                div.className = `daily-brief-card ${dailyBriefToneClass(card.tone || 'pending')}`;
                div.style.borderColor = dailyBriefCardBorderColor(card);
                div.innerHTML = `
                    <div class="daily-brief-card-label">${escapeHtml(card.label || '')}</div>
                    <div class="daily-brief-card-value">${escapeHtml(card.value || '0')}</div>
                    <div class="daily-brief-card-detail">${escapeHtml(card.detail || '')}</div>
                `;
                grid.appendChild(div);
            });
        }

        const generated = brief.generated_at || 'Not generated';
        const videoError = brief.video_error ? ` / Video warning: ${brief.video_error}` : '';
        setVideoDigestStatus(`Generated: ${generated}${videoError}`);
        renderDailyBriefVideos();
    }

    function dailyBriefToneClass(tone) {
        const value = String(tone || '').trim();
        return ['completed', 'partial', 'missed', 'pending', 'future', 'no_tasks', 'active'].includes(value)
            ? value
            : 'pending';
    }

    function renderVideoDigest() {
        renderDailyBriefVideos();
    }

    function renderDailyBriefVideos() {
        const state = videoRecommendationState || dailyBriefState?.video_state || {};
        const list = document.getElementById('video-digest-list');
        if (!list) return;
        const creators = state.creators || [];
        const videos = (state.recommendations || dailyBriefState?.videos || []).slice(0, 8);
        const activeCreators = creators.filter(creator => creator.enabled !== false);
        const checked = state.last_checked ? formatVideoDate(state.last_checked) : 'Not checked';
        const windowDays = state.window_days || state.settings?.recent_days || 3;

        list.innerHTML = '';
        if (!activeCreators.length) {
            list.innerHTML = '<div class="video-empty">No video creators configured.</div>';
            return;
        }
        if (!videos.length) {
            list.innerHTML = `<div class="video-empty">No unwatched videos from the last ${escapeHtml(windowDays)} days. Last check: ${escapeHtml(checked)}.</div>`;
            return;
        }
        videos.forEach(video => {
            const div = document.createElement('article');
            div.className = 'video-digest-card';
            const safeId = escapeJsString(video.id || '');
            const reasons = Array.isArray(video.reasons) ? video.reasons.slice(0, 3).join(' / ') : '';
            const score = Number(video.score || 0).toFixed(1);
            const url = /^(?:https?:\/\/)/i.test(video.url || '') ? video.url : '#';
            const thumbnail = /^(?:https?:\/\/)/i.test(video.thumbnail || '')
                ? `<img src="${escapeHtml(video.thumbnail)}" alt="">`
                : '<div class="video-digest-thumb-fallback"></div>';
            div.innerHTML = `
                <a class="video-digest-thumb" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">
                    ${thumbnail}
                </a>
                <div class="video-digest-card-body">
                    <div class="video-digest-meta">
                        <span class="video-platform-badge">${escapeHtml(video.platform || '')}</span>
                        ${escapeHtml(video.creator_name || '')}
                    </div>
                    <a class="video-digest-link" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">
                        ${escapeHtml(video.title || 'Untitled')}
                    </a>
                    <div class="video-digest-subline">
                        ${escapeHtml(formatVideoDate(video.published_at))} / Score ${escapeHtml(score)}
                    </div>
                    <div class="video-digest-reason">${escapeHtml(reasons)}</div>
                    <div class="video-card-actions">
                        <button class="video-discuss-btn" onclick="discussVideo('${safeId}', 'digest')">${ic('chat', 'icn-sm')}Discuss</button>
                        <button class="video-caption-discuss-btn" onclick="discussVideoWithCaptions('${safeId}', 'digest')">${ic('note', 'icn-sm')}Discuss with captions</button>
                        <button class="video-watch-btn" onclick="markVideoWatched('${safeId}', true, 'digest')">${ic('check', 'icn-sm')}Mark watched</button>
                    </div>
                </div>
            `;
            list.appendChild(div);
        });
    }

    async function talkWithSayaAboutDailyBrief() {
        try {
            if (!dailyBriefState) {
                await fetchDailyBrief(false);
            }
            pendingCheckinContext = dailyBriefState?.chat_context || '';
            closeVideoDigestModal();
            await startNewSessionSilently();
            await sendMessage({
                userText: dailyBriefState?.talk_prompt || "Saya, give me today's brief and help me choose the next small step.",
                displayText: "Saya, tell me today's brief."
            });
        } catch (e) {
            console.error(e);
            setVideoDigestStatus("Unable to start Daily Brief chat.");
        }
    }

    function renderVideoCreatorList(creators) {
        const list = document.getElementById('video-creator-list');
        if (!list) return;
        const creatorCount = document.getElementById('video-workspace-creator-count');
        if (creatorCount) creatorCount.textContent = String(creators.length);
        list.innerHTML = '';
        if (!creators.length) {
            list.innerHTML = `<div class="st-empty">${ic('video')}<span>No creators yet.</span></div>`;
            return;
        }
        creators.forEach(creator => {
            const div = document.createElement('div');
            div.className = 'video-creator-item';
            const safeId = escapeJsString(creator.id);
            const status = creator.status || 'pending';
            const errorLine = creator.last_error ? `<div class="video-meta">${escapeHtml(creator.last_error)}</div>` : '';
            div.innerHTML = `
                <div class="video-creator-copy">
                    <div class="video-creator-title">
                        <span class="video-platform-badge">${escapeHtml(creator.platform || '')}</span>
                        ${escapeHtml(creator.display_name || creator.name || '')}
                    </div>
                    <div class="video-meta">${escapeHtml(status)}${creator.source_id ? ' · ' + escapeHtml(creator.source_id) : ''}</div>
                    ${errorLine}
                </div>
                <button type="button" class="row-menu-btn" onclick="showVideoCreatorMenu(this, '${safeId}')" title="More actions" aria-label="More actions" aria-haspopup="menu">${ic('more', 'icn-sm')}</button>
            `;
            list.appendChild(div);
        });
    }

    function showVideoCreatorMenu(anchor, creatorId) {
        showMenuAt(anchor, [
            { label: 'Refresh now', icon: 'refresh', onClick: () => refreshVideoRecommendations() },
            { label: 'Remove creator', icon: 'trash', danger: true, onClick: () => deleteVideoCreator(creatorId) }
        ], { align: 'end' });
    }
    window.showVideoCreatorMenu = showVideoCreatorMenu;

    function renderVideoRecommendationList(videos) {
        const list = document.getElementById('video-recommendation-list');
        if (!list) return;
        list.innerHTML = '';
        if (!videos.length) {
            list.innerHTML = `<div class="st-empty">${ic('film')}<span>No videos from the last 3 days.</span></div>`;
            return;
        }
        videos.forEach(video => {
            const div = document.createElement('div');
            div.className = 'video-rec-card';
            const watchedBadge = video.watched ? '<span class="video-watched-badge">Watched</span>' : '';
            const watchLabel = video.watched ? 'Unwatch' : 'Mark watched';
            const watchTarget = video.watched ? 'false' : 'true';
            const safeId = escapeJsString(video.id || '');
            const reasons = Array.isArray(video.reasons) ? video.reasons.join(' · ') : '';
            const score = Number(video.score || 0).toFixed(1);
            const url = /^(?:https?:\/\/)/i.test(video.url || '') ? video.url : '#';
            div.innerHTML = `
                <div class="video-rec-title">
                    <a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(video.title || 'Untitled')}</a>
                    ${watchedBadge}
                </div>
                <div class="video-meta">
                    <span class="video-platform-badge">${escapeHtml(video.platform || '')}</span>
                    ${escapeHtml(video.creator_name || '')} · ${escapeHtml(formatVideoDate(video.published_at))} · Score ${escapeHtml(score)}
                </div>
                <div class="video-reason">${escapeHtml(reasons)}</div>
                <div class="video-card-actions">
                    <button class="video-discuss-btn" onclick="discussVideo('${safeId}', 'recommendations')">${ic('chat', 'icn-sm')}Discuss</button>
                    <button class="video-caption-discuss-btn" onclick="discussVideoWithCaptions('${safeId}', 'recommendations')">${ic('note', 'icn-sm')}Discuss with captions</button>
                    <button class="video-watch-btn ${video.watched ? 'is-watched' : ''}" onclick="markVideoWatched('${safeId}', ${watchTarget}, 'recommendations')">${video.watched ? ic('close', 'icn-sm') : ic('check', 'icn-sm')}${watchLabel}</button>
                </div>
            `;
            list.appendChild(div);
        });
    }

    async function markVideoWatched(id, watched = true, source = 'recommendations') {
        if (!id) return;
        if (source === 'digest') {
            setVideoDigestStatus("Saving watched status...");
        } else {
            setVideoRecommendationStatus("Saving watched status...");
        }
        try {
            const response = await fetch('/api/video_recommendations/videos/watched', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({id, watched})
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || `Save failed: ${response.status}`);
            videoRecommendationState = data.state || videoRecommendationState;
            renderVideoRecommendations();
            renderVideoDigest();
            const savedMessage = watched ? "Marked watched." : "Marked unwatched.";
            if (source === 'digest') {
                setVideoDigestStatus(savedMessage);
            } else {
                setVideoRecommendationStatus(savedMessage);
            }
        } catch (e) {
            console.error(e);
            if (source === 'digest') {
                setVideoDigestStatus("Failed to save watched status.");
            } else {
                setVideoRecommendationStatus("Failed to save watched status.");
            }
        }
    }

    async function discussVideo(id, source = 'recommendations') {
        if (!id) return;
        const setStatus = source === 'digest' ? setVideoDigestStatus : setVideoRecommendationStatus;
        setStatus("Preparing discussion...");
        try {
            const response = await fetch('/api/video_recommendations/videos/discuss', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({id})
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || `Discussion failed: ${response.status}`);
            videoRecommendationState = data.state || videoRecommendationState;
            renderVideoRecommendations();
            renderVideoDigest();
            closeVideoDigestModal();
            closeVideoRecommendationsModal();

            const displayText = data.display_text || `Doctor给你分享了一个视频：${data.video?.title || 'Untitled'}`;
            setStatus("Starting new chat...");
            await startNewSessionSilently();
            setStatus("Starting discussion...");
            await sendMessage({
                userText: data.prompt || displayText,
                displayText
            });
        } catch (e) {
            console.error(e);
            setStatus(e.message || "Failed to prepare discussion.");
        }
    }

    async function discussVideoWithCaptions(id, source = 'recommendations') {
        if (!id) return;
        const setStatus = source === 'digest' ? setVideoDigestStatus : setVideoRecommendationStatus;
        setStatus("Reading full captions...");
        try {
            const response = await fetch('/api/video_recommendations/videos/discuss-captions', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({id})
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || `Caption discussion failed: ${response.status}`);
            videoRecommendationState = data.state || videoRecommendationState;
            renderVideoRecommendations();
            renderVideoDigest();
            closeVideoDigestModal();
            closeVideoRecommendationsModal();

            const displayText = data.display_text || `Doctor给你分享了一个视频：${data.video?.title || 'Untitled'}`;
            setStatus("Starting new chat...");
            await startNewSessionSilently();
            setStatus("Starting caption discussion...");
            await sendMessage({
                // The visible bubble stays short; userText contains the full
                // caption prompt returned by the backend.
                userText: data.prompt || displayText,
                displayText
            });
        } catch (e) {
            console.error(e);
            setStatus(e.message || "Failed to read full captions.");
        }
    }

    async function saveVideoRecommendationSettings() {
        const checkbox = document.getElementById('video-auto-open-checkbox');
        try {
            const response = await fetch('/api/video_recommendations/settings', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({auto_open: Boolean(checkbox?.checked)})
            });
            if (!response.ok) throw new Error(`Save failed: ${response.status}`);
            const data = await response.json();
            videoRecommendationState = {
                ...(videoRecommendationState || {}),
                settings: data.settings || {}
            };
            setVideoRecommendationStatus("Settings saved.");
        } catch (e) {
            console.error(e);
            setVideoRecommendationStatus("Settings save failed.");
        }
    }

    async function addVideoCreator() {
        const platform = 'youtube';
        const input = document.getElementById('video-creator-input');
        const name = input?.value.trim();
        if (!name) return;
        setVideoRecommendationStatus("Adding creator...");
        try {
            const response = await fetch('/api/video_recommendations/creators/add', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({platform, name})
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || `Add failed: ${response.status}`);
            if (input) input.value = '';
            await fetchVideoRecommendationState(true);
            renderVideoRecommendations();
        } catch (e) {
            console.error(e);
            setVideoRecommendationStatus(e.message || "Add failed.");
        }
    }

    async function deleteVideoCreator(id) {
        confirmAction("Delete this creator?", async () => {

            setVideoRecommendationStatus("Deleting creator...");
            try {
                const response = await fetch('/api/video_recommendations/creators/delete', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({id})
                });
                if (!response.ok) throw new Error(`Delete failed: ${response.status}`);
                await fetchVideoRecommendationState(true);
                renderVideoRecommendations();
            } catch (e) {
                console.error(e);
                setVideoRecommendationStatus("Delete failed.");
            }
    });    }

    async function refreshVideoRecommendations() {
        setVideoRecommendationStatus("Refreshing...");
        try {
            await fetchVideoRecommendationState(true);
            renderVideoRecommendations();
        } catch (e) {
            console.error(e);
            setVideoRecommendationStatus("Refresh failed.");
        }
    }

    async function refreshVideoDigest() {
        await refreshDailyBrief();
    }

    async function refreshDailyBrief() {
        setVideoDigestStatus("Refreshing brief...");
        try {
            await fetchDailyBrief(true);
            renderDailyBrief();
        } catch (e) {
            console.error(e);
            setVideoDigestStatus("Daily Brief refresh failed.");
        }
    }

    function formatVideoDate(value) {
        if (!value) return 'Unknown date';
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return String(value).slice(0, 16);
        return date.toLocaleString([], {
            year: 'numeric',
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit'
        });
    }

    async function openChatSearch() {
        showPromptDialog({
            title: 'Search conversations',
            label: 'Find a conversation or message',
            placeholder: 'Search conversation history',
            confirmLabel: 'Search',
            onConfirm: async (query, close) => {
                close();
                await doChatSearch(query);
            }
        });
    }

    async function doChatSearch(query) {
        const modal = document.getElementById('memory-modal');
        const body = document.getElementById('modal-body');
        setMemoryModalHeader("Search Results");
        body.innerHTML = '';
        modal.style.display = 'flex';
        renderSurfaceState(body, { kind: 'loading', title: 'Searching conversations', message: 'Looking through your local history…', icon: 'search' });
        try {
            const response = await fetch('/api/sessions/search', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({query: query.trim(), limit: 50})
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || data.message || `Search failed (${response.status})`);
        const results = data.results || [];
        if (!results.length) {
            renderSurfaceState(body, {
                kind: 'empty',
                title: 'No matching conversations',
                message: 'Try a shorter phrase or search for a distinctive word.',
                icon: 'search'
            });
            return;
        }
        results.forEach(result => {
            const div = document.createElement('div');
            div.className = 'memory-item';
            const safeId = escapeJsString(result.id);
            div.innerHTML = `
                <div style="flex:1">
                    <div class="memory-title-row">${escapeHtml(result.name || result.id)} · ${Number(result.match_count || 0)} matches</div>
                    <div class="memory-meta">${escapeHtml(sessionLastQaTime(result) || 'Unknown')}</div>
                    ${(result.matches || []).map(match => `<div class="memory-content">${escapeHtml(match.snippet)}</div>`).join('')}
                </div>
                <div class="memory-actions">
                    <button class="btn-mem-action" onclick="closeModal(); loadSession('${safeId}')">Open</button>
                </div>
            `;
            body.appendChild(div);
        });
        } catch (error) {
            renderSurfaceState(body, {
                title: 'Search could not be completed',
                message: 'Your conversation history is unchanged. Try again in a moment.',
                retry: () => doChatSearch(query),
                details: error,
                icon: 'search'
            });
        }
    }

    function createSessionSection(title, sessions, isPinned) {
        const section = document.createElement('div');
        section.className = 'session-section';

        const header = document.createElement('div');
        header.className = 'session-section-header';
        header.setAttribute('role', 'button');
        header.setAttribute('tabindex', '0');
        header.setAttribute('aria-expanded', 'true');
        header.innerHTML = `<span class="icon session-section-chevron icn icn-chevron-down icn-sm" aria-hidden="true"></span><span>${escapeHtml(title)}</span>`;
        section.appendChild(header);

        const container = document.createElement('div');
        container.className = 'session-list-container';
        
        sessions.forEach(s => {
            container.appendChild(createSessionItem(s));
        });
        section.appendChild(container);

        header.onclick = () => {
            const collapsed = header.classList.toggle('collapsed');
            container.classList.toggle('collapsed', collapsed);
            header.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
        };
        header.onkeydown = event => {
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                header.click();
            }
        };

        return section;
    }

    function createSessionItem(s) {
        const div = document.createElement('div');
        div.className = 'session-item';
        div.onclick = (e) => {
            // Ignore if clicked on menu button or menu itself
            if(e.target.closest('.session-menu-btn') || e.target.closest('.session-menu-dropdown')) return;
            if (s.project_id) {
                setCurrentProject(s.project_id, {refresh: false});
                projectSidebarViewId = s.project_id;
            } else {
                setCurrentProject("", {refresh: false});
            }
            loadSession(s.id);
        };

        const content = document.createElement('div');
        content.className = 'session-content';
        const lastQaTime = sessionLastQaTime(s);
        content.innerHTML = `
            <div class="session-preview">${escapeHtml(s.preview)}</div>
            <div class="session-date">${escapeHtml(lastQaTime || 'No completed exchange')}${s.project_name ? ' · ' + escapeHtml(s.project_name) : ''}${s.legacy ? ' · Legacy (read-only)' : ''}</div>
        `;
        div.appendChild(content);

        // Menu Button (Vertical Ellipsis)
        const menuBtn = document.createElement('button');
        menuBtn.className = 'session-menu-btn';
        menuBtn.title = 'Conversation options';
        menuBtn.setAttribute('aria-label', 'Conversation options');
        menuBtn.innerHTML = ic('more', 'icn-sm');
        menuBtn.onclick = (e) => {
            e.stopPropagation();
            toggleSessionMenu(e, s, div);
        };
        div.appendChild(menuBtn);

        return div;
    }

    function toggleSessionMenu(e, s, parentItem) {
        // Close any existing menus
        document.querySelectorAll('.session-menu-dropdown').forEach(el => el.remove());
        document.querySelectorAll('.session-menu-btn').forEach(el => el.classList.remove('active'));

        const btn = e.currentTarget;
        btn.classList.add('active');

        const menu = document.createElement('div');
        menu.className = 'session-menu-dropdown show';
        
        if (!s.legacy) {
            // Pin/Unpin Option
            const pinOption = document.createElement('div');
            pinOption.className = 'menu-option';
            pinOption.innerHTML = s.pinned ? `<span>${ic('pin', 'icn-sm')}Unpin</span>` : `<span>${ic('pin', 'icn-sm')}Pin</span>`;
            pinOption.onclick = (ev) => {
                ev.stopPropagation();
                s.pinned ? unpinSession(s.id) : pinSession(s.id);
                menu.remove();
            };
            menu.appendChild(pinOption);
        }

        // Summarize selected exchanges into active Episodic memory.
        const archiveOption = document.createElement('div');
        archiveOption.className = 'menu-option';
        archiveOption.innerHTML = `<span>${ic('archive', 'icn-sm')}Summarize to Episodic</span>`;
        archiveOption.onclick = (ev) => {
            ev.stopPropagation();
            archiveSession(s.id);
            menu.remove();
        };
        menu.appendChild(archiveOption);

        // Move to global: detach this conversation from its project so it
        // shows up in the global conversation list. Only shown for sessions
        // that currently belong to a project.
        if (s.project_id && !s.legacy) {
            const moveOption = document.createElement('div');
            moveOption.className = 'menu-option';
            moveOption.innerHTML = `<span>${ic('external', 'icn-sm')}Detach</span>`;
            moveOption.onclick = (ev) => {
                ev.stopPropagation();
                moveSessionToGlobal(s.id, s.preview);
                menu.remove();
            };
            menu.appendChild(moveOption);
        }

        if (!s.legacy) {
            // Delete Option
            const delOption = document.createElement('div');
            delOption.className = 'menu-option delete';
            delOption.innerHTML = `<span>${ic('trash', 'icn-sm')}Delete</span>`;
            delOption.onclick = (ev) => {
                ev.stopPropagation();
                deleteSession(s.id);
                menu.remove();
            };
            menu.appendChild(delOption);
        }

        document.body.appendChild(menu);

        // Positioning
        const rect = btn.getBoundingClientRect();
        menu.style.top = rect.bottom + 5 + 'px';
        menu.style.left = (rect.right - 120) + 'px'; // Align right edge

        // Close on click outside
        setTimeout(() => {
            const closeHandler = (ev) => {
                if (!menu.contains(ev.target) && ev.target !== btn) {
                    menu.remove();
                    btn.classList.remove('active');
                    document.removeEventListener('click', closeHandler);
                }
            };
            document.addEventListener('click', closeHandler);
        }, 0);
    }

    async function pinSession(id) {
        try {
            const res = await fetch('/api/sessions/pin', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({session_id: id})
            });
            if (!res.ok) throw new Error("API request failed");
            const data = await res.json();
            if (!data.success) throw new Error("Server returned failure");
            fetchSessionList();
        } catch(e) { 
            console.error("Pin failed", e);
            showToast("Failed to pin session. Please ensure server is restarted to apply new changes.", { type: 'error', duration: 3600 });
        }
    }

    async function unpinSession(id) {
        try {
            const res = await fetch('/api/sessions/unpin', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({session_id: id})
            });
            if (!res.ok) throw new Error("API request failed");
            fetchSessionList();
        } catch(e) { 
            console.error("Unpin failed", e);
            showToast("Failed to unpin session. Please ensure server is restarted.", { type: 'error', duration: 3600 });
        }
    }

    async function fetchStatus() {
        // [MODIFIED] Emotion system is now autonomous via model tags. 
        // We no longer fetch numeric status from backend for UI bars.
        try {
            // Keep fetching status if there are other things (like server health), 
            // but for now, we just ignore the old emotion bars.
            // const res = await fetch('/api/status');
            // const data = await res.json();
            // ...
        } catch(e) {}
    }

    // [REMOVED] toggleEmotionLock (Emotion system is autonomous)
    /*
    async function toggleEmotionLock() {
       ...
    }
    */

    async function showOfflineReport() {
        try {
            const res = await fetch('/api/emotion/offline_report');
            const data = await res.json();
            let msg = data.message;
            if (data.details && data.details.length > 0) {
                msg += "\n\nChanges:\n" + data.details.join("\n");
            }
            showToast(msg, { type: 'info', duration: 3600 });
        } catch(e) { showToast("Failed to fetch report", { type: 'error', duration: 3600 }); }
    }

    async function loadSession(id, options = {}) {
        if (!options.skipConfirm) {
            confirmAction("Load this session? Current unsaved chat will be lost.", async () => {
                await loadSession(id, {...options, skipConfirm: true});
                document.getElementById('memory-modal').style.display = 'none';
            }, { title: 'Load this session?', confirmLabel: 'Load' });
            return false;
        }
        try {
            const res = await fetch('/api/sessions/load_full', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({session_id: id})
            });
            const data = await res.json();
            const loadedRoleMode = options.roleMode || data.role_mode || '';
            if (loadedRoleMode === 'assistant') {
                setCurrentRoleMode('assistant');
            } else if (currentRoleMode === 'assistant') {
                await exitAssistantRoleMode({silent: true, keepModalClosed: true, clearChat: false});
            } else {
                setCurrentRoleMode('');
            }
            const chatContent = document.getElementById('chat-content');
            if (chatContent) chatContent.innerHTML = '';
            
            (data.items || []).forEach((item, historyIndex) => {
                if (item.role === "user") {
                    appendMessage('user', item.content || '', {
                        historyIndex,
                        attachments: item.attachments || []
                    });
                } else if (item.role === "ai" || item.role === "assistant") {
                    const webResearch = item.web_research || {};
                    appendMessage('ai', item.content || '', {
                        historyIndex,
                        translation: item.translation || '',
                        readableGenerated: item.readable_generated === true,
                        webSources: (webResearch.sources || []),
                        webQuery: webResearch.query || '',
                        webCitationCheck: webResearch.citation_check || null,
                        memoryRecall: item.memory_recall || null
                    });
                }
            });
            currentHistoryLength = (data.items || []).length;
            updateModeProgressRing();
            currentSessionId = data.session_id || id;
            void enhanceListenerSessionOnLoad();
            if (data.migrated_from) fetchSessionList();
            if (!options.skipAlert) showToast("Session loaded.", { type: 'info', duration: 3600 });
            return true;
        } catch(e) {
            showToast("Load failed: " + e, { type: 'error', duration: 3600 });
            return false;
        }
    }

    function archiveSession(sessionId) {
        openArchiveSelection(sessionId);
    }

    async function startNewSessionSilently(projectIdOverride = undefined, options = {}) {
        const roleMode = options.roleMode || '';
        if (currentRoleMode === 'assistant' && roleMode !== 'assistant' && !options.skipRoleExit) {
            await exitAssistantRoleMode({silent: true, keepModalClosed: true, clearChat: false});
        }
        const sessionProjectId = projectIdOverride !== undefined
            ? (projectIdOverride || "")
            : (projectSidebarViewId || currentProjectId || "");
        const response = await fetch('/api/sessions/new', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                project_id: roleMode === 'assistant' ? null : (sessionProjectId || null),
                role_mode: roleMode || null
            })
        });
        if (!response.ok) throw new Error(`New session failed: ${response.status}`);
        const data = await response.json();
        const chatContent = document.getElementById('chat-content');
        if (chatContent) chatContent.innerHTML = '';
        // [MODIFIED] Removed trans-content clear
        currentHistoryLength = 0; // Reset
        updateModeProgressRing();
        currentSessionId = data.session_id || null;
        if (roleMode === 'assistant') {
            setCurrentRoleMode('assistant');
        } else {
            setCurrentRoleMode('');
            if (data.project_id) {
                setCurrentProject(data.project_id, {refresh: false});
                projectSidebarViewId = data.project_id;
            } else {
                setCurrentProject("", {refresh: false});
            }
        }
        fetchSessionList();
        return data;
    }

    async function createNewSession() {
        confirmAction("Start new session?", async () => {

            await startNewSessionSilently(projectSidebarViewId || "");
    });    }

    async function deleteSession(id) {
        try {
            const deletingCurrentSession = currentSessionId && String(currentSessionId) === String(id);
            const previewResponse = await fetch('/api/sessions/delete-preview', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({session_id: id})
            });
            if (!previewResponse.ok) throw new Error(`Delete preview failed: ${previewResponse.status}`);
            const preview = await previewResponse.json();
            const autoMemories = Array.isArray(preview.auto_memories) ? preview.auto_memories : [];
            const blockedMemories = preview.memory_preview?.blocked || [];
            if (blockedMemories.length) {
                throw new Error('This session has protected automatic memories. Unlock them before deleting the session.');
            }

            const runDelete = async () => {
                const response = await fetch('/api/sessions/delete', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        session_id: id,
                        session_confirmation_token: preview.session_confirmation_token,
                        memory_confirmation_token: preview.memory_confirmation_token
                    })
                });
                if (!response.ok) throw new Error(`Delete failed: ${response.status}`);
                const data = await response.json();
                if (!data.success) throw new Error(data.message || "Server returned failure");

                const deletingDisplayedNormalSession = currentRoleMode !== 'assistant' &&
                    (deletingCurrentSession || data.was_active_session === true);
                if (deletingDisplayedNormalSession) {
                    const chatContent = document.getElementById('chat-content');
                    if (chatContent) chatContent.innerHTML = '';
                    currentHistoryLength = 0;
                    currentSessionId = null;
                    await startNewSessionSilently(projectSidebarViewId || currentProjectId || "");
                } else if (deletingCurrentSession) {
                    await startNewSessionSilently(projectSidebarViewId || currentProjectId || "");
                } else {
                    fetchSessionList();
                }
            };

            if (autoMemories.length) {
                const memoryList = autoMemories
                    .map((memory, index) => {
                        const title = String(memory.title || 'Untitled automatic memory').trim();
                        const content = String(memory.content || '').trim();
                        return `${index + 1}. ${title}${content ? `\n   ${content}` : ''}`;
                    })
                    .join('\n\n');
                const message = [
                    `This session created ${autoMemories.length} automatic memor${autoMemories.length === 1 ? 'y' : 'ies'}.`,
                    'Deleting the session will also permanently delete the following automatic memories:',
                    '',
                    memoryList,
                    '',
                    memoryDeletionImpactText(preview.memory_preview || {}),
                    '',
                    'Continue and delete both the session and these memories?'
                ].join('\n');
                confirmAction(message, runDelete, { title: 'Delete this session and its memories?', confirmLabel: 'Delete session', danger: true });
            } else {
                confirmAction('Delete this session permanently?\n\nNo linked automatic memory will be removed. This cannot be undone.', runDelete, { title: 'Delete this session?', confirmLabel: 'Delete session', danger: true });
            }
        } catch (e) {
            console.error("Delete session failed", e);
            showToast("Failed to delete session: " + e.message, { type: 'error', duration: 3600 });
        }
    }

    async function moveSessionToGlobal(sessionId, previewText) {
        if (!sessionId) return;
        const label = previewText ? `"${String(previewText).slice(0, 40)}"` : 'this conversation';
        confirmAction(`Move ${label} out of this project and into the global conversation list?\n\nThe conversation itself is preserved; it just no longer belongs to the project.`, async () => {

            try {
                const response = await fetch('/api/projects/remove_session', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({session_id: sessionId})
                });
                if (!response.ok) throw new Error(`Move failed: ${response.status}`);
                const data = await response.json();
                if (data.status !== 'success') throw new Error('Server returned failure');
                // If the moved session is currently displayed, keep it loaded but
                // clear the project context so the UI no longer treats it as part
                // of the open project.
                if (currentSessionId && String(currentSessionId) === String(sessionId)) {
                    setCurrentProject("", {refresh: false});
                    projectSidebarViewId = "";
                }
                fetchSessionList();
            } catch (e) {
                console.error("Move session to global failed", e);
                showToast("Failed to move conversation: " + e.message, { type: 'error', duration: 3600 });
            }
    });}

    function toggleApiInput() {
        const select = document.getElementById('model-select');
        if (select && select.value === 'api' && !getSavedApiSettings().api_key) {
            console.info("API mode selected. Configure API details in Settings → LLM API.");
        }
    }

    let questionAnchorSequence = 0;

    function getQuestionIndexText(rawText) {
        const text = String(rawText || '')
            .replace(/\[FILE_CONTEXT\][\s\S]*?\[\/FILE_CONTEXT\]/gi, ' ')
            .replace(/\[(?:IMAGE|FILE):[^\]]*\]/gi, ' ')
            .replace(/\(System Note:[\s\S]*?\)\s*/gi, ' ')
            .replace(/^Doctor:\s*/i, '')
            .replace(/\s+/g, ' ')
            .trim();
        return text || 'Attachment';
    }

    function refreshQuestionIndex() {
        const chatContent = document.getElementById('chat-content');
        const index = document.getElementById('question-index');
        const list = document.getElementById('question-index-list');
        if (!chatContent || !index || !list) return;

        const userRows = Array.from(chatContent.querySelectorAll(':scope > .message-row.user-row'));
        list.innerHTML = '';
        index.hidden = userRows.length === 0;

        userRows.forEach(row => {
            if (!row.id) row.id = `chat-question-${++questionAnchorSequence}`;
            const fullLabel = row.dataset.questionText || getQuestionIndexText(row.dataset.rawContent);
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'question-index-item';
            button.dataset.anchorId = row.id;
            button.setAttribute('aria-label', `Go to question: ${fullLabel}`);
            button.innerHTML = `<span class="question-index-label">${escapeHtml(fullLabel)}</span>`;
            button.addEventListener('click', () => {
                row.scrollIntoView({behavior: 'smooth', block: 'center'});
            });
            list.appendChild(button);
        });

        updateQuestionIndexActiveState();
    }

    function updateQuestionIndexActiveState() {
        const chatContent = document.getElementById('chat-content');
        const list = document.getElementById('question-index-list');
        if (!chatContent || !list) return;
        const rows = Array.from(chatContent.querySelectorAll(':scope > .message-row.user-row'));
        if (!rows.length) return;

        const focusLine = chatContent.scrollTop + (chatContent.clientHeight * 0.42);
        let nearest = rows[0];
        let nearestDistance = Infinity;
        rows.forEach(row => {
            const distance = Math.abs(row.offsetTop - focusLine);
            if (distance < nearestDistance) {
                nearest = row;
                nearestDistance = distance;
            }
        });
        list.querySelectorAll('.question-index-item').forEach(button => {
            button.classList.toggle('active', button.dataset.anchorId === nearest.id);
        });
    }

    function appendMessage(type, text, options = {}) {
        const chatContent = document.getElementById('chat-content');
        
        // Create Row Container
        const rowDiv = document.createElement('div');
        rowDiv.className = `message-row ${type}-row`;
        rowDiv.dataset.rawContent = text; // Store raw content for edit/regenerate
        rowDiv._attachments = cloneAttachmentMetadata(options.attachments);
        if (type === 'user') rowDiv.dataset.questionText = getQuestionIndexText(text);
        if (Number.isInteger(options.historyIndex)) {
            rowDiv.dataset.historyIndex = String(options.historyIndex);
        }
        if (type === 'ai' && options.readableGenerated) {
            rowDiv.dataset.readableGenerated = 'true';
        }
        
        // Create Bubble
        const bubbleDiv = document.createElement('div');
        bubbleDiv.className = `message-bubble ${type}`; // 'user' or 'ai' class for legacy selector compatibility
        
        // Content
        const contentDiv = document.createElement('div');
        let imageElement = null; // 图片缩略图，最终渲染在气泡上方（不在气泡内）
        if (type === 'ai') {
            contentDiv.innerHTML = `Saya: ` + renderMarkdownSafe(text);
        } else {
            // Check for image tag [IMAGE:url]
            const imgMatch = text.match(/\[IMAGE:(.*?)\]/);
            const fileMatch = text.match(/\[FILE:(.*?)\]/);
            
            // Extract System Note if present (User side)
            let displayNote = "";
            let remainingText = text;
            
            // Logic: The message structure is [IMAGE:...] (System Note: ...)\n\nUser Text
            // We first extract/remove the image tag
            if (imgMatch) {
                const imgUrl = imgMatch[1];
                let textAfterImage = text.replace(imgMatch[0], "").trim();
                
                // Check if it starts with System Note
                if (textAfterImage.startsWith("(System Note:")) {
                    // We look for the separator we added in sendMessage (\n\n)
                    // If not found, maybe user text is empty, so the whole thing is note
                    const splitIndex = textAfterImage.indexOf("\n\n");
                    
                    let notePart = "";
                    if (splitIndex !== -1) {
                        notePart = textAfterImage.substring(0, splitIndex);
                        remainingText = textAfterImage.substring(splitIndex).trim();
                    } else {
                        // No user text following
                        notePart = textAfterImage;
                        remainingText = "";
                    }
                    
                    // Extract content from notePart: "(System Note: ... Content: ...)"
                    const contentMarker = "Content:";
                    const cIdx = notePart.indexOf(contentMarker);
                    if (cIdx !== -1) {
                        let rawContent = notePart.substring(cIdx + contentMarker.length).trim();
                        // Remove trailing ')'
                        if (rawContent.endsWith(')')) {
                            rawContent = rawContent.substring(0, rawContent.length - 1);
                        }
                        displayNote = rawContent;
                    }
                } else {
                    remainingText = textAfterImage;
                }

                const img = document.createElement('img');
                img.src = /^(?:\/|https?:\/\/)/i.test(imgUrl) ? imgUrl : '';
                img.style.width = '80px';
                img.style.height = '80px';
                img.style.objectFit = 'cover'; // 非方形图片取正中部分
                img.style.borderRadius = '8px';
                img.style.display = 'block';
                img.style.marginBottom = '8px';
                img.title = '点击查看大图';
                img.onclick = () => window.open(imgUrl, '_blank');
                img.style.cursor = 'pointer';
                imageElement = img;
                
                // Show Description Toggle
                if (displayNote) {
                    const details = document.createElement('details');
                    details.style.marginBottom = '10px';
                    details.style.fontSize = '0.75rem';
                    details.style.color = '#888';
                    
                    const summary = document.createElement('summary');
                    summary.innerText = "Show Description";
                    summary.style.cursor = 'pointer';
                    summary.style.outline = 'none';
                    
                    const p = document.createElement('p');
                    p.innerText = displayNote;
                    p.style.marginTop = '5px';
                    p.style.background = 'rgba(0,0,0,0.2)';
                    p.style.padding = '5px';
                    p.style.borderRadius = '4px';
                    
                    details.appendChild(summary);
                    details.appendChild(p);
                    contentDiv.appendChild(details);
                }

                if (remainingText.length > 0) {
                    contentDiv.appendChild(document.createTextNode(remainingText));
                }
            } else if (fileMatch) {
                const filename = fileMatch[1];
                const fileContextPattern = /\[FILE_CONTEXT\][\s\S]*?\[\/FILE_CONTEXT\]/;
                const cleanText = text
                    .replace(fileMatch[0], "")
                    .replace(fileContextPattern, "")
                    .trim();
                
                const fileDiv = document.createElement('div');
                fileDiv.style.background = 'rgba(255,255,255,0.1)';
                fileDiv.style.padding = '8px 12px';
                fileDiv.style.borderRadius = '6px';
                fileDiv.style.display = 'flex';
                fileDiv.style.alignItems = 'center';
                fileDiv.style.gap = '10px';
                fileDiv.style.marginBottom = '5px';
                fileDiv.style.border = '1px solid rgba(255,255,255,0.2)';
                
                fileDiv.innerHTML = `
                    <span style="font-size:1.5rem; display:inline-flex;">${ic('note', 'icn-lg')}</span>
                    <div style="display:flex; flex-direction:column;">
                        <span style="font-weight:bold; font-size:0.9rem;">${escapeHtml(filename)}</span>
                        <span style="font-size:0.75rem; opacity:0.7;">File Content Uploaded</span>
                    </div>
                `;
                
                contentDiv.appendChild(fileDiv);
                if (cleanText.length > 0) {
                    contentDiv.appendChild(document.createTextNode(`Doctor: ${cleanText}`));
                }
            } else {
                contentDiv.textContent = text;
            }
        }
        bubbleDiv.appendChild(contentDiv);

        // Actions
        const actionsDiv = document.createElement('div');
        actionsDiv.className = 'msg-actions';
        
        if (type === 'user') {
            const editBtn = document.createElement('button');
            editBtn.className = 'btn-action';
            editBtn.title = 'Edit message';
            editBtn.setAttribute('aria-label', 'Edit message');
            editBtn.innerHTML = ic('edit', 'icn-sm');
            editBtn.onclick = () => editMessage(editBtn);
            actionsDiv.appendChild(editBtn);
        } else {
            const regenBtn = document.createElement('button');
            regenBtn.className = 'btn-action';
            regenBtn.title = 'Regenerate response';
            regenBtn.setAttribute('aria-label', 'Regenerate response');
            regenBtn.innerHTML = ic('refresh', 'icn-sm');
            regenBtn.onclick = () => regenerateMessage(regenBtn);
            actionsDiv.appendChild(regenBtn);

            // [MODIFIED] New Collapsible Translation
            const transBtn = document.createElement('button');
            transBtn.className = 'btn-action';
            transBtn.title = 'Translate';
            transBtn.setAttribute('aria-label', 'Translate');
            transBtn.innerHTML = ic('translate', 'icn-sm');
            transBtn.onclick = () => {
                const allRows = Array.from(document.getElementById('chat-content').children);
                const index = rowDiv.dataset.historyIndex !== undefined
                    ? Number(rowDiv.dataset.historyIndex)
                    : allRows.indexOf(rowDiv);
                const raw = contentDiv.innerText.replace(/^Saya:\s*/, '').trim();
                toggleTranslation(bubbleDiv, raw, index);
            };
            actionsDiv.appendChild(transBtn);

            // Branch: copy this conversation up to this message into a new session.
            const branchBtn = document.createElement('button');
            branchBtn.className = 'btn-action';
            branchBtn.title = 'Branch conversation here';
            branchBtn.setAttribute('aria-label', 'Branch conversation here');
            branchBtn.innerHTML = ic('branch', 'icn-sm');
            branchBtn.onclick = () => branchFromMessage(branchBtn);
            actionsDiv.appendChild(branchBtn);

            if (options.readableGenerated) {
                const readBtn = document.createElement('button');
                readBtn.className = 'btn-action read-aloud-inline';
                readBtn.title = 'Read aloud';
                readBtn.setAttribute('aria-label', 'Read aloud');
                readBtn.innerHTML = ic('volume', 'icn-sm');
                readBtn.onclick = () => speakWithElevenLabs(getAiMessageText(rowDiv));
                actionsDiv.appendChild(readBtn);
            }
        }
        bubbleDiv.appendChild(actionsDiv);
        if (imageElement) {
            // 图片渲染在气泡上方：用纵向 flex 包装，图片 + 气泡上下排列
            const wrapper = document.createElement('div');
            wrapper.style.display = 'flex';
            wrapper.style.flexDirection = 'column';
            wrapper.style.width = '100%'; // 占满整行，避免气泡按收缩宽度计算 max-width 导致过早换行
            wrapper.style.alignItems = type === 'user' ? 'flex-end' : 'flex-start';
            wrapper.appendChild(imageElement);
            wrapper.appendChild(bubbleDiv);
            rowDiv.appendChild(wrapper);
        } else {
            rowDiv.appendChild(bubbleDiv);
        }

        chatContent.appendChild(rowDiv);
        chatContent.scrollTop = chatContent.scrollHeight;

        // Restore web research sources panel from history (plan section 19.6).
        if (type === 'ai' && Array.isArray(options.webSources) && options.webSources.length) {
            renderWebSourcesPanel(rowDiv, options.webQuery || '', options.webSources);
        }
        if (type === 'ai' && options.webCitationCheck) {
            renderWebCitationCheck(rowDiv, options.webCitationCheck);
        }
        if (type === 'ai' && options.memoryRecall) {
            renderMemoryRecallAudit(rowDiv, options.memoryRecall);
        }

        if (type === 'ai' && options.translation) {
            showStoredTranslation(bubbleDiv, options.translation);
        }
        
        // Update status after message
        if(type === 'ai') fetchStatus();
        
        return rowDiv; 
    }

    function getAiMessageText(rowDiv) {
        const bubble = rowDiv?.querySelector('.message-bubble.ai');
        if (!bubble) return "";
        const clone = bubble.cloneNode(true);
        clone.querySelectorAll('.msg-actions, .read-aloud-inline, .trans-box, .web-sources-panel, .web-citation-check, .memory-recall-audit, .web-status-line, .web-warning-line').forEach(node => node.remove());
        return clone.innerText.replace(/^Saya:\s*/, '').trim();
    }

    function showStoredTranslation(bubbleDiv, translatedText) {
        let transBox = bubbleDiv.querySelector('.trans-box');
        if (!transBox) {
            transBox = document.createElement('div');
            transBox.className = 'trans-box';
            transBox.style.marginTop = '12px';
            transBox.style.padding = '12px';
            transBox.style.background = 'rgba(0, 0, 0, 0.3)';
            transBox.style.borderTop = '1px solid #333';
            transBox.style.borderLeft = '2px solid var(--accent-blue)';
            transBox.style.fontFamily = "'Courier New', monospace";
            transBox.style.fontSize = '0.9rem';
            transBox.style.color = '#ccc';
            bubbleDiv.appendChild(transBox);
        }
        transBox.textContent = translatedText;
    }

    // [ADDED] Collapsible Translation Logic
    async function toggleTranslation(bubbleDiv, text, index) {
        let transBox = bubbleDiv.querySelector('.trans-box');
        
        if (transBox) {
            // Toggle
            transBox.style.display = transBox.style.display === 'none' ? 'block' : 'none';
            return;
        }

        // Create Container
        transBox = document.createElement('div');
        transBox.className = 'trans-box';
        transBox.style.marginTop = '12px';
        transBox.style.padding = '12px';
        transBox.style.background = 'rgba(0, 0, 0, 0.3)';
        transBox.style.borderTop = '1px solid #333';
        transBox.style.borderLeft = '2px solid var(--accent-blue)';
        transBox.style.fontFamily = "'Courier New', monospace";
        transBox.style.fontSize = '0.9rem';
        transBox.style.color = '#ccc';
        transBox.style.display = 'block';
        transBox.style.animation = 'fadeIn 0.3s ease';
        
        // Loading State
        transBox.innerHTML = '<span class="blink">_Analyzing...</span>';
        bubbleDiv.appendChild(transBox);

        try {
            const translationSettings = getSavedOtherApiSettings();
            const trRes = await fetch('/translate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    text: text,
                    source: 'en',
                    target: 'zh',
                    session_id: currentSessionId,
                    message_index: index,
                    secret_id: translationSettings.translation_secret_id,
                    secret_key: translationSettings.translation_secret_key,
                    region: translationSettings.translation_region,
                    endpoint: translationSettings.translation_endpoint
                })
            });
            const trData = await trRes.json();
            if (!trRes.ok) {
                throw new Error(trData.detail || `Translation failed (${trRes.status})`);
            }
            if (trData && trData.translatedText) {
                transBox.innerHTML = '';
                const p = document.createElement('p');
                p.style.margin = '0';
                p.style.lineHeight = '1.5';
                p.innerText = trData.translatedText;
                transBox.appendChild(p);
            } else {
                transBox.innerText = "Translation failed.";
            }
        } catch (e) {
            transBox.innerText = "Error: " + e;
        }
    }

    function splitAttachmentContent(rawContent) {
        const fileContextMatch = rawContent.match(/\[FILE_CONTEXT\][\s\S]*?\[\/FILE_CONTEXT\]/);
        if (fileContextMatch) {
            const end = rawContent.indexOf(fileContextMatch[0]) + fileContextMatch[0].length;
            const prefix = rawContent.substring(0, end).trimEnd() + "\n\n";
            return {prefix, userPart: rawContent.substring(end).trim()};
        }

        const hasAttachment = rawContent.match(/\[IMAGE:(.*?)\]/) || rawContent.match(/\[FILE:(.*?)\]/);
        const splitIndex = rawContent.indexOf("\n\n");
        if (hasAttachment && splitIndex !== -1) {
            return {
                prefix: rawContent.substring(0, splitIndex + 2),
                userPart: rawContent.substring(splitIndex + 2)
            };
        }
        if (hasAttachment && rawContent.startsWith(hasAttachment[0])) {
            return {
                prefix: hasAttachment[0] + "\n",
                userPart: rawContent.substring(hasAttachment[0].length).trim()
            };
        }
        return {prefix: "", userPart: rawContent};
    }

    function cloneAttachmentMetadata(attachments) {
        if (!Array.isArray(attachments)) return [];
        return attachments
            .filter(item => item && typeof item === 'object' && ['image', 'file'].includes(String(item.type || '').toLowerCase()))
            .map(item => ({
                type: String(item.type || '').toLowerCase(),
                url: String(item.url || ''),
                data_url: String(item.data_url || ''),
                filename: String(item.filename || ''),
                description: String(item.description || '')
            }));
    }

    function getMessageAttachments(rowDiv) {
        const stored = cloneAttachmentMetadata(rowDiv?._attachments);
        if (stored.length) return stored;
        const rawContent = String(rowDiv?.dataset?.rawContent || '');
        const imageMatch = rawContent.match(/\[IMAGE:(.*?)\]/);
        if (imageMatch?.[1]) {
            return [{type: 'image', url: imageMatch[1], data_url: '', filename: '', description: ''}];
        }
        const fileMatch = rawContent.match(/\[FILE:(.*?)\]/);
        if (fileMatch?.[1]) {
            return [{type: 'file', url: '', data_url: '', filename: fileMatch[1], description: ''}];
        }
        return [];
    }

    async function editMessage(btn) {
        const rowDiv = btn.closest('.message-row');
        
        // Calculate index
        const allRows = Array.from(document.getElementById('chat-content').children);
        const domIndex = allRows.indexOf(rowDiv);
        const historyIndex = rowDiv.dataset.historyIndex !== undefined
            ? Number(rowDiv.dataset.historyIndex)
            : domIndex;
        
        if (domIndex === -1) {
             showToast("Error identifying message.", { type: 'error', duration: 3600 });
             return;
        }

        const rawContent = rowDiv.dataset.rawContent || "";
        const {prefix, userPart} = splitAttachmentContent(rawContent);
        const attachments = getMessageAttachments(rowDiv);

        // const bubbleDiv = rowDiv.querySelector('.message-bubble');
        // const contentDiv = bubbleDiv.firstElementChild;
        // const currentText = contentDiv.textContent.replace("Doctor:", "").trim();
        
        showPromptDialog({
            title: 'Edit your message',
            label: 'Message text',
            value: userPart.trim(),
            multiline: true,
            confirmLabel: 'Continue',
            onConfirm: (newText, close) => {
                close();
                if (newText === userPart.trim()) return;
                confirmAction("Resend edited message? This will remove all subsequent conversation history.", async () => {


                // 1. Reset backend history to this index
                try {
                    const res = await fetch('/api/history/reset_to', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({index: historyIndex, session_id: currentSessionId})
                    });
                    const data = await res.json();
                    if(data.status !== 'success') throw new Error(data.message);
                
                    // 2. Update local state
                    currentHistoryLength = data.remaining_count;

                    // 3. Remove subsequent DOM elements
                    const container = document.getElementById('chat-content');
                    while (container.children.length > domIndex) {
                        container.removeChild(container.lastElementChild);
                    }

                    // 4. Send new message
                    const editedContent = prefix + newText;
                    sendMessage({
                        userText: editedContent,
                        displayText: editedContent,
                        attachments
                    });

                } catch(e) {
                    showToast("Edit failed: " + e, { type: 'error', duration: 3600 });
                }
                });
            }
        });
    }

    async function regenerateMessage(btn) {
        const rowDiv = btn.closest('.message-row');
        const allRows = Array.from(document.getElementById('chat-content').children);
        const domIndex = allRows.indexOf(rowDiv);

        if (domIndex === -1) return;
        
        // We need to find the preceding User message to re-send.
        // Usually index - 1.
        if (domIndex === 0) {
            showToast("Cannot regenerate without context.", { type: 'error', duration: 3600 });
            return;
        }
        
        const userRowDiv = allRows[domIndex - 1];
        if (!userRowDiv.classList.contains('user-row')) {
             showToast("Preceding message is not from user. Cannot regenerate.", { type: 'error', duration: 3600 });
             return;
        }
        
        // Retrieve raw content from dataset
        const rawContent = userRowDiv.dataset.rawContent || "";
        const attachments = getMessageAttachments(userRowDiv);
        
        confirmAction("Regenerate response?", async () => {


            try {
                // 1. Reset history to User message index (removing User + AI)
                // Wait, we want to re-send the User message.
                // So we need to remove the User message from backend history too,
                // because sendMessage() will append it again.
                const resetIndex = userRowDiv.dataset.historyIndex !== undefined
                    ? Number(userRowDiv.dataset.historyIndex)
                    : domIndex - 1;
            
                const res = await fetch('/api/history/reset_to', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({index: resetIndex, session_id: currentSessionId})
                });
                const data = await res.json();
                if(data.status !== 'success') throw new Error(data.message);
            
                currentHistoryLength = data.remaining_count;

                // 2. Remove DOM elements (remove all subsequent messages including the ones being regenerated)
                const container = document.getElementById('chat-content');
                while (container.children.length > domIndex - 1) {
                    container.removeChild(container.lastElementChild);
                }

                // 3. Re-send User message using raw content
                sendMessage({
                    userText: rawContent,
                    displayText: rawContent,
                    attachments
                });

            } catch(e) {
                showToast("Regenerate failed: " + e, { type: 'error', duration: 3600 });
            }
    });}

    // Branch: copy conversation up to this AI message into a new session.
    async function branchFromMessage(btn) {
        const rowDiv = btn.closest('.message-row');
        if (!rowDiv) return;
        const allRows = Array.from(document.getElementById('chat-content').children);
        const domIndex = allRows.indexOf(rowDiv);
        if (domIndex < 0) return;
        // Inclusive index of this message.
        const upToIndex = rowDiv.dataset.historyIndex !== undefined
            ? Number(rowDiv.dataset.historyIndex)
            : domIndex;

        confirmAction(`Branch a new session from here?\n将复制到此条为止的所有对话到新会话。`, async () => {


            try {
                const res = await fetch('/api/sessions/branch', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ up_to_index: upToIndex, session_id: currentSessionId })
                });
                if (!res.ok) throw new Error(`Branch failed: ${res.status}`);
                const data = await res.json();
                // Switch to the new session.
                currentSessionId = data.session_id;
                currentHistoryLength = data.copied_count || (upToIndex + 1);
                updateModeProgressRing();
                fetchSessionList();
                // Reload the chat view from the new session.
                await loadSession(data.session_id, {silent: true});
            } catch (e) {
                showToast("Branch failed: " + e, { type: 'error', duration: 3600 });
            }
    });}

    const physicsParams = {
        friction: 0.2,
        restitution: 0.05,
        damping: 0.95
    };

    function createAnimationHelper() {
        return new THREE.MMDAnimationHelper({
            afterglow: 2.0,
        });
    }

    function recreateAnimationHelper() {
        if (physicsEnableTimer) {
            clearTimeout(physicsEnableTimer);
            physicsEnableTimer = null;
        }
        if (animationHelper) {
            try {
                animationHelper.enable('physics', false);
            } catch (e) {
                console.warn("Physics disable before helper reset failed", e);
            }
        }
        animationHelper = createAnimationHelper();
        lastPhysicsResetTime = Date.now();
        abnormalPhysicsFrames = 0;
        lastPhysicsVisualSample = null;
        mmdPhysicsStepper?.reset();
        clock.getDelta();
    }

    function getRigidBodySearchName(rigidBody) {
        return `${rigidBody?.name || ''} ${rigidBody?.englishName || ''}`;
    }

    function isSkirtRigidBody(rigidBody) {
        return /スカート|skirt/i.test(getRigidBodySearchName(rigidBody));
    }

    function isHairRigidBody(rigidBody) {
        return /髪|hair/i.test(getRigidBodySearchName(rigidBody));
    }

    function applyOriginalMmdRigidBodyStability(rigidBodies) {
        // This is the pre-existing path. TDA gets no model-specific collision,
        // IK, or joint changes.
        rigidBodies.forEach((rigidBody) => {
            rigidBody.friction = physicsParams.friction;
            rigidBody.restitution = physicsParams.restitution;
            rigidBody.damping = physicsParams.damping;
            rigidBody.positionDamping = physicsParams.damping;
            rigidBody.rotationDamping = physicsParams.damping;

            if (isHairRigidBody(rigidBody)) {
                rigidBody.damping = 0.98;
                rigidBody.rotationDamping = 0.98;
                rigidBody.friction = 0.9;
                rigidBody.mass = Math.max(0.01, Number(rigidBody.mass || 0.01) * 2.0);
            } else if (isSkirtRigidBody(rigidBody)) {
                rigidBody.damping = 0.9;
                rigidBody.rotationDamping = 0.9;
            }
        });
    }

    function applyMmdRigidBodyStability(mesh) {
        const rigidBodies = mesh?.geometry?.userData?.MMD?.rigidBodies;
        if (!Array.isArray(rigidBodies)) return;

        const tuning = currentModelConfig?.rigidBodyTuning;
        if (!tuning || !tuning.preserveAuthoredParameters) {
            applyOriginalMmdRigidBodyStability(rigidBodies);
        }
        if (!tuning) {
            console.log('Applied original rigid body stability path for model:', currentModelId);
            return;
        }

        const skirtBodies = rigidBodies.filter(isSkirtRigidBody);
        if (tuning.profile === 'yyb-collision-free-skirt') {
            skirtBodies.forEach((body) => {
                body.positionDamping = tuning.skirt.positionDamping;
                body.rotationDamping = tuning.skirt.rotationDamping;
                if (tuning.skirt.disableAllCollisions) body.groupTarget = 0;
            });
            console.log('Applied YYB-only collision-free skirt:', {
                skirtBodies: skirtBodies.length,
                collisionTargetsRemaining: skirtBodies.filter(body => body.groupTarget !== 0).length,
            });
            return;
        }

        if (tuning.profile === 'genoge-stable-cloth') {
            if (tuning.skirt?.disableAllCollisions) {
                skirtBodies.forEach(body => { body.groupTarget = 0; });
            }

            const breastBodies = rigidBodies.filter(body =>
                /breast|bust|胸/i.test(getRigidBodySearchName(body))
            );
            breastBodies.forEach((body) => {
                if (tuning.breast?.forceKinematic) body.type = 0;
                if (tuning.breast?.disableAllCollisions) body.groupTarget = 0;
            });

            if (tuning.tie?.enabled) {
                activeTiePhysicsDiagnostics = MmdTiePhysics.tuneRigidBodies(rigidBodies, genogeTieMode);
            }

            console.log('Applied げのげ-only cloth/upper-body stability:', {
                skirtBodies: skirtBodies.length,
                stabilizedBreastBodies: breastBodies.length,
                tie: activeTiePhysicsDiagnostics,
            });
        }
    }

    function publishMmdModelDiagnostics() {
        const groups = activeShoeDiagnostics?.groups || [];
        const payload = {
            modelId: currentModelId,
            tiePhysics: activeTiePhysicsDiagnostics ? {
                ...activeTiePhysicsDiagnostics,
                timing: mmdPhysicsStepper ? { ...mmdPhysicsStepper.stats } : null,
            } : null,
            shoeSkinRemap: groups.map(group => ({
                side: group.side,
                sourceBone: group.sourceBone,
                targetBone: group.targetBone,
                remappedInfluences: group.remappedInfluences,
                affectedVertices: group.vertexIndices.length,
                remainingSourceInfluences: group.remainingSourceInfluences,
                baselineRadius: group.baselineRadius,
                latestRadius: group.latestRadius,
                maxRadiusRatio: group.maxRadiusRatio,
                anomalyCount: group.anomalyCount,
            })),
        };
        window.__MMD_MODEL_DIAGNOSTICS__ = payload;

        // Mirror the passive diagnostic payload into the DOM so automated tests can
        // read it even when the browser runs page scripts in an isolated world.
        let output = document.getElementById('mmd-model-diagnostics');
        if (!output) {
            output = document.createElement('output');
            output.id = 'mmd-model-diagnostics';
            output.hidden = true;
            (document.body || document.documentElement).appendChild(output);
        }
        output.textContent = JSON.stringify(payload);
    }

    function clearMmdModelDiagnostics() {
        activeShoeDiagnostics = null;
        activeTiePhysicsDiagnostics = null;
        mmdPhysicsStepper = null;
        lastShoeDiagnosticSampleAt = 0;
        publishMmdModelDiagnostics();
    }

    function applyMmdGeometryStability(mesh) {
        clearMmdModelDiagnostics();
        const remaps = currentModelConfig?.geometryTuning?.skinBoneRemap;
        const bones = mesh?.skeleton?.bones;
        const skinIndex = mesh?.geometry?.attributes?.skinIndex;
        if (!Array.isArray(remaps) || !Array.isArray(bones) || !skinIndex?.array) return;

        const boneIndexByName = new Map();
        bones.forEach((bone, index) => {
            if (!boneIndexByName.has(bone.name)) boneIndexByName.set(bone.name, index);
        });
        const itemSize = Number(skinIndex.itemSize || 4);
        const groups = [];

        remaps.forEach((remap) => {
            const sourceIndex = boneIndexByName.get(remap.source);
            const targetIndex = boneIndexByName.get(remap.target);
            if (!Number.isInteger(sourceIndex) || !Number.isInteger(targetIndex)) {
                console.warn('MMD shoe skin remap skipped because a bone was not found:', remap);
                return;
            }

            let remappedInfluences = 0;
            const vertexIndices = new Set();
            for (let offset = 0; offset < skinIndex.array.length; offset += 1) {
                if (skinIndex.array[offset] !== sourceIndex) continue;
                skinIndex.array[offset] = targetIndex;
                remappedInfluences += 1;
                vertexIndices.add(Math.floor(offset / itemSize));
            }
            let remainingSourceInfluences = 0;
            for (let offset = 0; offset < skinIndex.array.length; offset += 1) {
                if (skinIndex.array[offset] === sourceIndex) remainingSourceInfluences += 1;
            }
            groups.push({
                side: remap.side || remap.source,
                sourceBone: remap.source,
                targetBone: remap.target,
                remappedInfluences,
                remainingSourceInfluences,
                vertexIndices: Array.from(vertexIndices),
                baselineRadius: null,
                latestRadius: null,
                maxRadiusRatio: 1,
                anomalyCount: 0,
            });
        });

        skinIndex.needsUpdate = true;
        activeShoeDiagnostics = { mesh, groups };
        publishMmdModelDiagnostics();
        console.log('Applied げのげ-only shoe skin-chain repair:', window.__MMD_MODEL_DIAGNOSTICS__);
    }

    function getCpuSkinnedVertexPosition(mesh, vertexIndex, target, scratch) {
        const geometry = mesh?.geometry;
        const position = geometry?.attributes?.position;
        const skinIndex = geometry?.attributes?.skinIndex;
        const skinWeight = geometry?.attributes?.skinWeight;
        const boneMatrices = mesh?.skeleton?.boneMatrices;
        if (!position || !skinIndex || !skinWeight || !boneMatrices) return null;

        const base = scratch.base.fromBufferAttribute(position, vertexIndex).applyMatrix4(mesh.bindMatrix);
        target.set(0, 0, 0);
        for (let slot = 0; slot < skinIndex.itemSize; slot += 1) {
            const weight = skinWeight.array[vertexIndex * skinWeight.itemSize + slot];
            if (!Number.isFinite(weight) || weight <= 0) continue;
            const boneIndex = skinIndex.array[vertexIndex * skinIndex.itemSize + slot];
            scratch.matrix.fromArray(boneMatrices, boneIndex * 16);
            scratch.weighted.copy(base).applyMatrix4(scratch.matrix).multiplyScalar(weight);
            target.add(scratch.weighted);
        }
        return target.applyMatrix4(mesh.bindMatrixInverse);
    }

    function sampleMmdShoeDiagnostics(now) {
        const diagnostics = activeShoeDiagnostics;
        if (!diagnostics || diagnostics.mesh !== currentMesh || now - lastShoeDiagnosticSampleAt < 750) return;
        lastShoeDiagnosticSampleAt = now;
        const mesh = diagnostics.mesh;
        if (!mesh?.skeleton || !Array.isArray(diagnostics.groups)) return;
        mesh.skeleton.update();

        const scratch = {
            base: new THREE.Vector3(),
            weighted: new THREE.Vector3(),
            matrix: new THREE.Matrix4(),
            point: new THREE.Vector3(),
            center: new THREE.Vector3(),
        };
        diagnostics.groups.forEach((group) => {
            if (!group.vertexIndices.length) return;
            const points = [];
            scratch.center.set(0, 0, 0);
            group.vertexIndices.forEach((vertexIndex) => {
                const point = getCpuSkinnedVertexPosition(mesh, vertexIndex, scratch.point, scratch);
                if (!point || !isFiniteVector3(point)) return;
                points.push(point.clone());
                scratch.center.add(point);
            });
            if (!points.length) return;
            scratch.center.divideScalar(points.length);
            let radius = 0;
            points.forEach(point => { radius = Math.max(radius, point.distanceTo(scratch.center)); });
            if (!Number.isFinite(radius) || radius <= 0) return;

            if (!Number.isFinite(group.baselineRadius)) group.baselineRadius = radius;
            group.latestRadius = radius;
            const ratio = radius / group.baselineRadius;
            group.maxRadiusRatio = Math.max(group.maxRadiusRatio, ratio);
            if (ratio > 1.75) {
                group.anomalyCount += 1;
                console.warn('MMD shoe stretch diagnostic:', {
                    model: currentModelId,
                    side: group.side,
                    radiusRatio: Number(ratio.toFixed(3)),
                    radius: Number(radius.toFixed(3)),
                    baselineRadius: Number(group.baselineRadius.toFixed(3)),
                });
            }
        });
        publishMmdModelDiagnostics();
    }

    function attachMeshToAnimationHelper(mesh) {
        if (!animationHelper || !mesh) return;
        applyMmdRigidBodyStability(mesh);
        const tieProfile = currentModelConfig?.rigidBodyTuning?.tie?.enabled
            ? MmdTiePhysics.getProfile(genogeTieMode) : null;
        // Per-mesh physics options belong to add(), not the helper constructor.
        // Other models keep the r106 defaults they have actually been using.
        animationHelper.add(mesh, {
            physics: true,
            unitStep: tieProfile?.unitStep ?? 1 / 65,
            maxStepNum: tieProfile?.maxStepNum ?? 3,
            gravity: new THREE.Vector3(...(tieProfile?.gravity ?? [0, -98, 0])),
            warmup: tieProfile ? 0 : 60,
        });
        if (tieProfile) {
            const physics = animationHelper.objects.get(mesh).physics;
            const runtime = MmdTiePhysics.configurePhysics(physics, genogeTieMode);
            if (tieProfile.hz) {
                for (let i = 0; i < tieProfile.hz; i++) physics.update(tieProfile.unitStep);
            } else {
                physics.warmup(60);
            }
            activeTiePhysicsDiagnostics.runtime = runtime;
            mmdPhysicsStepper = MmdTiePhysics.createStepper(genogeTieMode);
            publishMmdModelDiagnostics();
        }
    }

    function getFootPosition() {
        if (!currentMesh) return null;
        currentMesh.updateMatrixWorld(true);

        const footBones = currentMesh.skeleton.bones.filter(bone =>
            bone.name.toLowerCase().includes('leg') ||
            bone.name.toLowerCase().includes('foot') ||
            bone.name.toLowerCase().includes('足') ||
            bone.name.toLowerCase().includes('脚')
        );

        if (footBones.length === 0) {
            const box = new THREE.Box3().setFromObject(currentMesh);
            return new THREE.Vector3(
                currentMesh.position.x,
                box.min.y,
                currentMesh.position.z
            );
        }

        const footPosition = new THREE.Vector3();
        footBones.forEach(bone => {
            const worldPos = bone.getWorldPosition(new THREE.Vector3());
            footPosition.add(worldPos);
        });
        footPosition.divideScalar(footBones.length);

        return footPosition;
    }

    function forceResetPosition(options = {}) {
        if (currentMesh && initialPosition) {
            const hard = Boolean(options.hard);
            if (hard) {
                currentMesh.position.copy(initialPosition.position);
                currentMesh.rotation.copy(initialPosition.rotation);
                currentMesh.updateMatrixWorld(true);
                return;
            }

            const currentFootPos = getFootPosition();
            if (currentFootPos && initialFootPosition) {
                const offset = new THREE.Vector3().subVectors(initialFootPosition, currentFootPos);
                const correctionSpeed = Number.isFinite(options.correctionSpeed) ? options.correctionSpeed : 0.16;
                currentMesh.position.x += offset.x * correctionSpeed;
                if (correctSoftZThisMotion) {
                    currentMesh.position.z += offset.z * correctionSpeed;
                }
                currentMesh.rotation.copy(initialPosition.rotation);
            }
        }
    }

    function isFiniteVector3(vector) {
        return vector && Number.isFinite(vector.x) && Number.isFinite(vector.y) && Number.isFinite(vector.z);
    }

    function resetPhysicsVisualSample() {
        lastPhysicsVisualSample = null;
    }

    function detectPhysicsVisualJitter() {
        if (!currentModelConfig?.physics?.detectVisualJitter) {
            resetPhysicsVisualSample();
            return null;
        }
        if (!currentMesh) return null;
        const bounds = new THREE.Box3().setFromObject(currentMesh);
        if (
            !bounds ||
            !Number.isFinite(bounds.min.x) ||
            !Number.isFinite(bounds.max.x)
        ) {
            resetPhysicsVisualSample();
            return null;
        }

        const center = bounds.getCenter(new THREE.Vector3());
        const size = bounds.getSize(new THREE.Vector3());
        if (!isFiniteVector3(center) || !isFiniteVector3(size)) {
            resetPhysicsVisualSample();
            return null;
        }

        const previous = lastPhysicsVisualSample;
        lastPhysicsVisualSample = {
            center: center.clone(),
            size: size.clone()
        };
        if (!previous) return null;

        const centerJump = center.distanceTo(previous.center);
        const sizeJump = size.distanceTo(previous.size);
        const severe = centerJump > PHYSICS_MAX_VISUAL_CENTER_JUMP * 1.6
            || sizeJump > PHYSICS_MAX_VISUAL_SIZE_JUMP * 1.6;
        const abnormal = severe
            || centerJump > PHYSICS_MAX_VISUAL_CENTER_JUMP
            || sizeJump > PHYSICS_MAX_VISUAL_SIZE_JUMP;

        if (!abnormal) return null;
        return {
            severe,
            centerJump,
            sizeJump
        };
    }

    function detectPhysicsInstability(beforeY) {
        if (!currentMesh || !initialPosition) {
            abnormalPhysicsFrames = 0;
            resetPhysicsVisualSample();
            return null;
        }

        const position = currentMesh.position;
        if (!isFiniteVector3(position) || !isFiniteVector3(initialPosition.position)) {
            abnormalPhysicsFrames += 1;
            return "non-finite model position";
        }

        const rootDrift = position.distanceTo(initialPosition.position);
        const frameYDelta = Math.abs(position.y - beforeY);
        let footDrift = 0;
        const footPosition = getFootPosition();
        if (footPosition && initialFootPosition && isFiniteVector3(footPosition) && isFiniteVector3(initialFootPosition)) {
            footDrift = footPosition.distanceTo(initialFootPosition);
        }
        const visualJitter = detectPhysicsVisualJitter();

        const severe = rootDrift > PHYSICS_MAX_ROOT_DRIFT * 1.6
            || footDrift > PHYSICS_MAX_FOOT_DRIFT * 1.6
            || frameYDelta > PHYSICS_MAX_FRAME_Y_DELTA * 2
            || Boolean(visualJitter?.severe);
        const abnormal = severe
            || rootDrift > PHYSICS_MAX_ROOT_DRIFT
            || footDrift > PHYSICS_MAX_FOOT_DRIFT
            || frameYDelta > PHYSICS_MAX_FRAME_Y_DELTA
            || Boolean(visualJitter);

        if (!abnormal) {
            abnormalPhysicsFrames = 0;
            return null;
        }

        abnormalPhysicsFrames += 1;
        if (severe || abnormalPhysicsFrames >= 2) {
            const visualSummary = visualJitter
                ? `, visualCenter=${visualJitter.centerJump.toFixed(2)}, visualSize=${visualJitter.sizeJump.toFixed(2)}`
                : "";
            return `root=${rootDrift.toFixed(2)}, foot=${footDrift.toFixed(2)}, yJump=${frameYDelta.toFixed(2)}${visualSummary}`;
        }
        return null;
    }

    function restartInitialStableLoopMotion(reason = 'physics recovery') {
        if (!currentMesh || !Array.isArray(motionFiles) || motionFiles.length === 0) return false;
        const idleIndex = findClassicIdleMotionIndex();
        if (idleIndex === -1 || !motionFiles[idleIndex]) {
            console.warn(`Stable idle motion not found after ${reason}.`);
            return false;
        }

        clearModelInteractionLockState();
        clearPendingMotionRequest();
        clearMotionPlaybackCallbacks();
        currentMotionState = 'normal_idle';
        currentMotionIndex = idleIndex;
        loadAndPlayMotion(currentMesh, {
            bypassInteractionLock: true,
            interruptOneShot: true,
            forceRestart: true,
            resetPosition: false,
            crossfadeSeconds: PHYSICS_RECOVERY_CROSSFADE_SECONDS
        });
        console.warn(`Restarted stable idle motion after ${reason}: ${motionFiles[idleIndex]}`);
        return true;
    }

    function resetMmdPhysics(reason, options = {}) {
        if (!currentMesh || !animationHelper) return false;
        const now = Date.now();
        if (!options.force && now - lastPhysicsPanicTime < PHYSICS_PANIC_COOLDOWN) {
            return false;
        }

        lastPhysicsPanicTime = now;
        abnormalPhysicsFrames = 0;
        console.warn(`Resetting MMD physics: ${reason}`);

        if (physicsEnableTimer) {
            clearTimeout(physicsEnableTimer);
            physicsEnableTimer = null;
        }

        try {
            animationHelper.enable('physics', false);
        } catch (e) {
            console.warn("Physics disable during reset failed", e);
        }
        forceResetPosition({ hard: true });
        resetPhysicsVisualSample();

        const meshSnapshot = currentMesh;
        physicsEnableTimer = setTimeout(() => {
            if (!meshSnapshot || currentMesh !== meshSnapshot) return;
            let helperRecovered = false;
            try {
                recreateAnimationHelper();
                attachMeshToAnimationHelper(meshSnapshot);
                forceResetPosition({ hard: true });
                animationHelper.enable('physics', true);
                lastPhysicsResetTime = Date.now();
                helperRecovered = true;
            } catch (e) {
                console.warn("Physics rebuild failed", e);
            } finally {
                physicsEnableTimer = null;
            }

            const shouldRestartStableMotion = options.restartStableMotion === true
                || currentModelConfig?.physics?.restartMotionAfterReset === true;
            if (helperRecovered && shouldRestartStableMotion) {
                setTimeout(() => {
                    if (currentMesh !== meshSnapshot) return;
                    restartInitialStableLoopMotion(`physics reset (${reason})`);
                }, PHYSICS_RECOVERY_MOTION_DELAY_MS);
            }
        }, options.delayMs ?? 80);

        return true;
    }

    if (typeof Ammo === 'function') {
        Ammo().then(function() {
            console.log('Ammo.js initialized');
            init();
        }).catch(function(err) {
            console.error('Ammo.js initialization failed:', err);
            showToast('Failed to load Ammo.js. The model cannot be loaded. Please check your internet connection.', { type: 'error', duration: 3600 });
        });
    } else {
        showToast('Failed to load Ammo.js. The model cannot be loaded. Please check your internet connection.', { type: 'error', duration: 3600 });
    }

    function createAnimationBindingPose(mesh) {
        if (!mesh || !mesh.skeleton || !Array.isArray(mesh.skeleton.bones)) {
            return null;
        }

        const boneByName = new Map();
        const bonePositions = new Map();
        const boneTransforms = mesh.skeleton.bones.map((bone) => ({
            position: bone.position.clone(),
            quaternion: bone.quaternion.clone(),
            scale: bone.scale.clone()
        }));
        const bones = mesh.skeleton.bones.map((bone) => {
            const position = bone.position.clone();
            if (!boneByName.has(bone.name)) {
                const bindingBone = { name: bone.name, position };
                boneByName.set(bone.name, bindingBone);
                bonePositions.set(bone.name, position.clone());
                return bindingBone;
            }
            return { name: bone.name, position };
        });

        return {
            mesh,
            bonePositions,
            boneTransforms,
            morphTargetInfluences: Array.from(mesh.morphTargetInfluences || []),
            morphTargetDictionary: Object.assign({}, mesh.morphTargetDictionary || {}),
            skeleton: {
                bones,
                getBoneByName(name) {
                    return boneByName.get(name);
                }
            }
        };
    }

    function getPositionTrackBoneName(trackName) {
        const prefix = '.bones[';
        const suffix = '].position';
        if (
            typeof trackName !== 'string' ||
            !trackName.startsWith(prefix) ||
            !trackName.endsWith(suffix)
        ) {
            return null;
        }
        return trackName.slice(prefix.length, -suffix.length);
    }

    function clampBoneYOffsetsToBindingPose(track) {
        const boneName = getPositionTrackBoneName(track.name);
        const bindingY = animationBindingPose?.bonePositions.get(boneName)?.y;
        if (!Number.isFinite(bindingY) || !track.values) return;

        for (let i = 1; i < track.values.length; i += 3) {
            const offsetY = track.values[i] - bindingY;
            if (!Number.isFinite(offsetY)) continue;
            const clampedOffsetY = Math.max(Math.min(offsetY, MAX_BONE_Y_OFFSET), -MAX_BONE_Y_OFFSET);
            track.values[i] = bindingY + clampedOffsetY;
        }
    }

    function loadAnimationFromBindingPose(vmdFilePath, mesh, onLoad, onProgress, onError) {
        const bindingPose = animationBindingPose;
        if (
            !bindingPose ||
            bindingPose.mesh !== mesh ||
            !loader ||
            typeof loader.loadVMD !== 'function' ||
            !loader.animationBuilder
        ) {
            onError(new Error('The model binding pose is unavailable for VMD loading.'));
            return;
        }

        loader.loadVMD(
            vmdFilePath,
            function(vmdData) {
                if (bindingPose !== animationBindingPose || mesh !== currentMesh) return;
                try {
                    onLoad(loader.animationBuilder.build(vmdData, bindingPose));
                } catch (error) {
                    onError(error);
                }
            },
            onProgress,
            onError
        );
    }

    function resetModelColdStartState() {
        motionPlayToken += 1;
        motionLoadToken += 1;
        clearMotionPlaybackCallbacks();
        clearModelInteractionLockState();
        if (physicsEnableTimer) {
            clearTimeout(physicsEnableTimer);
            physicsEnableTimer = null;
        }
        vmdCache.clear();
        motionFiles = [];
        motionEntries = [];
        currentMotionIndex = 0;
        currentMotionState = 'normal_idle';
        activeMotionIndex = -1;
        activeMotionEntry = null;
        activeMotionStartedAt = 0;
        activeMotionDurationSeconds = 0;
        activeMotionAction = null;
        loadingMotionIndex = -1;
        loadingMotionEntry = null;
        animationBindingPose = null;
        softPositionMotionCount = 0;
        correctSoftZThisMotion = false;
        pendingMotionIndex = null;
        pendingMotionPriority = -Infinity;
        pendingMotionOptions = null;
        openingMotionPlayedGeneration = -1;
        lastInteractionTime = Date.now();
        lastModelInteractionAt = 0;
        lastModelInteractionMotionKey = '';
        modelInteractionMotionTimes.clear();
        lastPhysicsPanicTime = 0;
        abnormalPhysicsFrames = 0;
        lastPhysicsVisualSample = null;
        lastPhysicsResetTime = Date.now();
        mmdPhysicsStepper?.reset();
        clock.getDelta();

        if (camera && controls) {
            controls.target.set(0, 0, 0);
            camera.position.set(0, 10, 30);
            controls.update();
            initialCameraDistance = camera.position.distanceTo(new THREE.Vector3(0, 0, 0));
        }
    }

    function reloadModel() {
        if (!loader) return;
        console.log("Reloading model...");
        resetModelColdStartState();
        loadModel(false, { coldStart: true });
    }

    function loadModel(isFirstLoad, options = {}) {
        const coldStart = Boolean(options.coldStart);
        const loadGeneration = ++modelLoadGeneration;
        if (currentMesh) {
            scene.remove(currentMesh);
            if (animationHelper) {
                try {
                    animationHelper.remove(currentMesh);
                } catch (e) { console.warn("Helper remove error", e); }
            }
            if (mixer) {
                motionPlayToken += 1;
                clearMotionPlaybackCallbacks();
                mixer.stopAllAction();
                mixer = null;
            }
            
            if (currentMesh.geometry) currentMesh.geometry.dispose();
            if (currentMesh.material) {
                if (Array.isArray(currentMesh.material)) {
                    currentMesh.material.forEach(m => m.dispose());
                } else {
                    currentMesh.material.dispose();
                }
            }
            currentMesh = null;
            animationBindingPose = null;
            clearMmdModelDiagnostics();
        }

        if (!isFirstLoad) {
            recreateAnimationHelper();
        }

        // [FIX] Show loading overlay and hide canvas content until model is fully ready.
        // This prevents the user from seeing partially-loaded body parts.
        const mmdOverlay = document.getElementById('mmd-loading-overlay');
        if (mmdOverlay) mmdOverlay.hidden = false;

        loader.load(
            currentModelUrl,
            function(mesh) {
                if (loadGeneration !== modelLoadGeneration) {
                    if (mesh.geometry) mesh.geometry.dispose();
                    if (mesh.material) {
                        if (Array.isArray(mesh.material)) {
                            mesh.material.forEach(m => m.dispose());
                        } else {
                            mesh.material.dispose();
                        }
                    }
                    return;
                }

                // [FIX] Keep mesh invisible until we explicitly reveal it
                mesh.visible = false;

                currentMesh = mesh;
                applyMmdGeometryStability(mesh);
                animationBindingPose = createAnimationBindingPose(mesh);
                mesh.scale.set(currentModelConfig.scale, currentModelConfig.scale, currentModelConfig.scale);
                mesh.position.y = currentModelConfig.posY;

                // The げのげ mesh has animated shoe vertices outside its authored bounds.
                // Disable whole-mesh frustum culling for this model only; other models
                // retain their original renderer behavior.
                if (currentModelConfig?.renderTuning?.disableFrustumCulling) {
                    mesh.frustumCulled = false;
                    mesh.traverse(child => { child.frustumCulled = false; });
                }
                
                initialPosition = {
                    position: mesh.position.clone(),
                    rotation: mesh.rotation.clone()
                };
                
                if (mesh.material) {
                    if (Array.isArray(mesh.material)) {
                        mesh.material.forEach(setupMaterial);
                    } else {
                        setupMaterial(mesh.material);
                    }
                } else if (mesh.materials) {
                    mesh.materials.forEach(setupMaterial);
                }

                scene.add(mesh);
                mesh.updateMatrixWorld(true);
                initialFootPosition = getFootPosition();
                mixer = new THREE.AnimationMixer(mesh);
                attachMeshToAnimationHelper(mesh);

                // [ADDED] Restore saved view state (camera position/target) after model load
                applyViewState();

                // [FIX] Wait for all textures to finish loading, then reveal.
                // MMD models reference many texture files (skin, clothes, hair, etc.)
                // that load asynchronously. We check THREE.Cache and material maps
                // before revealing the mesh and hiding the overlay.
                function revealModel() {
                    mesh.visible = true;
                    if (mmdOverlay) {
                        // Fade out overlay
                        mmdOverlay.style.transition = 'opacity 0.3s ease';
                        mmdOverlay.style.opacity = '0';
                        setTimeout(() => {
                            mmdOverlay.hidden = true;
                            mmdOverlay.style.opacity = '';
                            mmdOverlay.style.transition = '';
                        }, 300);
                    }
                }

                // Check if all textures used by this mesh are loaded
                function checkTexturesReady() {
                    let pending = 0;
                    mesh.traverse(child => {
                        if (child.material) {
                            const mats = Array.isArray(child.material) ? child.material : [child.material];
                            mats.forEach(mat => {
                                ['map','envMap','bumpMap','normalMap','specularMap','emissiveMap','gradientMap'].forEach(key => {
                                    const tex = mat[key];
                                    if (tex && tex.image === undefined) pending++;
                                });
                            });
                        }
                    });
                    return pending === 0;
                }

                if (checkTexturesReady()) {
                    // Textures already cached/loaded — reveal after one frame
                    requestAnimationFrame(() => requestAnimationFrame(revealModel));
                } else {
                    // Wait for textures: poll a few times, then force-reveal as fallback
                    let attempts = 0;
                    const maxAttempts = 30; // ~500ms at 60fps
                    const poll = () => {
                        attempts++;
                        if (checkTexturesReady() || attempts >= maxAttempts) {
                            requestAnimationFrame(() => requestAnimationFrame(revealModel));
                        } else {
                            requestAnimationFrame(poll);
                        }
                    };
                    requestAnimationFrame(poll);
                }

                if (isFirstLoad) {
                    fetchMotionFiles({ resetIndex: true });
                    animate();
                } else if (coldStart) {
                    forceResetPosition({ hard: true });
                    fetchMotionFiles({ resetIndex: true });
                } else {
                    if (animationHelper) {
                        animationHelper.enable('physics', false);
                    }
                    setTimeout(() => {
                        forceResetPosition();
                        playIntelligentMotion({ forceNormal: true });
                    }, 180);
                }
            },
            function(xhr) {
                console.log(`Model loading: ${(xhr.loaded / xhr.total * 100).toFixed(1)}%`);
            },
            function(err) {
                console.error('MMD Model load error:', err);
                if (mmdOverlay) mmdOverlay.hidden = true;
                showToast("Failed to load model. Please try again.", { type: 'error', duration: 3600 });
            }
        );
    }

    // [ADDED] 3D Settings Panel controller (hidden model + lighting controls)
    function initMmdSettingsPanel() {
        const trigger = document.getElementById('mmd-settings-trigger');
        const panel = document.getElementById('mmd-settings-panel');
        const closeBtn = document.getElementById('mmd-settings-close');
        const modelSelect = document.getElementById('mmd-model-select');
        const ambientIn = document.getElementById('mmd-light-ambient');
        const dirIn = document.getElementById('mmd-light-dir');
        const topIn = document.getElementById('mmd-light-top');
        const ambientColorIn = document.getElementById('mmd-ambient-color');
        const dirColorIn = document.getElementById('mmd-dir-color');
        const topColorIn = document.getElementById('mmd-top-color');
        const bgIn = document.getElementById('mmd-bg-color');
        const resetBtn = document.getElementById('mmd-light-reset');
        if (!trigger || !panel || !modelSelect) return;

        // Populate model dropdown
        modelSelect.innerHTML = '';
        MMD_MODELS.forEach(m => {
            const opt = document.createElement('option');
            opt.value = m.id;
            opt.textContent = m.name;
            modelSelect.appendChild(opt);
        });
        modelSelect.value = currentModelId;
        syncMmdTieControls();
        const tieSelect = document.getElementById('mmd-tie-mode');
        tieSelect?.addEventListener('change', () => {
            genogeTieMode = MmdTiePhysics.normalizeMode(tieSelect.value);
            syncMmdTieControls();
            saveMmdSettings();
            reloadModel();
        });

        // Sync sliders to current state
        syncLightControlsToState();
        applyMmdLightVisuals();

        trigger.addEventListener('click', (e) => {
            e.stopPropagation();
            panel.hidden = !panel.hidden;
        });
        closeBtn.addEventListener('click', () => { panel.hidden = true; });
        // Prevent canvas interactions when using the panel
        panel.addEventListener('mousedown', (e) => e.stopPropagation());

        // Apply model immediately when user picks a different one (no Load Model button)
        modelSelect.addEventListener('change', () => {
            const id = modelSelect.value;
            if (!selectCurrentModel(id)) return;
            // Switch to the new model's own default lighting
            applyModelDefaultLight();
            saveMmdSettings();
            reloadModel();
        });

        const onLightChange = () => {
            mmdLight.ambient = parseFloat(ambientIn.value);
            mmdLight.dir = parseFloat(dirIn.value);
            mmdLight.top = parseFloat(topIn.value);
            if (ambientColorIn) mmdLight.ambientColor = ambientColorIn.value;
            if (dirColorIn) mmdLight.dirColor = dirColorIn.value;
            if (topColorIn) mmdLight.topColor = topColorIn.value;
            mmdLight.bg = bgIn.value;
            // Advanced lighting
            const advGet = (id) => document.getElementById(id);
            const rimInt = advGet('mmd-rim-intensity');
            const fillInt = advGet('mmd-fill-intensity');
            const hemiInt = advGet('mmd-hemi-intensity');
            const pointInt = advGet('mmd-point-intensity');
            const fogDens = advGet('mmd-fog-density');
            if (rimInt) mmdLight.rimIntensity = parseFloat(rimInt.value);
            if (advGet('mmd-rim-color')) mmdLight.rimColor = advGet('mmd-rim-color').value;
            if (fillInt) mmdLight.fillIntensity = parseFloat(fillInt.value);
            if (advGet('mmd-fill-color')) mmdLight.fillColor = advGet('mmd-fill-color').value;
            if (hemiInt) mmdLight.hemiIntensity = parseFloat(hemiInt.value);
            if (advGet('mmd-hemi-sky')) mmdLight.hemiSky = advGet('mmd-hemi-sky').value;
            if (advGet('mmd-hemi-ground')) mmdLight.hemiGround = advGet('mmd-hemi-ground').value;
            if (pointInt) mmdLight.pointIntensity = parseFloat(pointInt.value);
            if (advGet('mmd-point-color')) mmdLight.pointColor = advGet('mmd-point-color').value;
            if (fogDens) mmdLight.fogDensity = parseFloat(fogDens.value);
            if (advGet('mmd-fog-color')) mmdLight.fogColor = advGet('mmd-fog-color').value;
            // Skybox
            const skyEn = advGet('mmd-skybox-enabled');
            const skyRot = advGet('mmd-skybox-rotation');
            const skyEnv = advGet('mmd-skybox-env');
            const skyUrl = advGet('mmd-skybox-url');
            if (skyEn) mmdLight.skyboxEnabled = skyEn.checked;
            if (skyRot) mmdLight.skyboxRotation = parseFloat(skyRot.value);
            if (skyEnv) mmdLight.skyboxEnv = skyEnv.checked;
            if (skyUrl) mmdLight.skyboxUrl = skyUrl.value;
            applyMmdLightVisuals();
            saveMmdSettings();
        };
        [ambientIn, dirIn, topIn].forEach(el => el.addEventListener('input', onLightChange));
        [ambientColorIn, dirColorIn, topColorIn, bgIn].forEach(el => el && el.addEventListener('change', onLightChange));

        // Advanced lighting input listeners
        const advIds = ['mmd-rim-color','mmd-rim-intensity','mmd-fill-color','mmd-fill-intensity',
            'mmd-hemi-sky','mmd-hemi-ground','mmd-hemi-intensity','mmd-point-color','mmd-point-intensity',
            'mmd-fog-color','mmd-fog-density',
            'mmd-skybox-enabled','mmd-skybox-rotation','mmd-skybox-env'];
        advIds.forEach(id => {
            const el = document.getElementById(id);
            if (el) el.addEventListener('input', onLightChange);
        });
        // Skybox URL is bound to 'change' (blur/enter) to avoid reloading on every keystroke
        const skyboxUrlIn = document.getElementById('mmd-skybox-url');
        if (skyboxUrlIn) skyboxUrlIn.addEventListener('change', onLightChange);

        // More toggle: show/hide advanced lighting section
        const moreToggle = document.getElementById('mmd-more-toggle');
        const advSection = document.getElementById('mmd-advanced-lighting');
        if (moreToggle && advSection) {
            moreToggle.addEventListener('click', () => {
                advSection.hidden = !advSection.hidden;
                moreToggle.classList.toggle('active', !advSection.hidden);
                // innerHTML 保留图标 span（textContent 会把图标抹掉）
                moreToggle.innerHTML = ic('chevron-down', 'icn-sm') + (advSection.hidden ? 'More' : 'Less');
            });
        }

        const saveDefaultBtn = document.getElementById('mmd-light-save-default');
        resetBtn.addEventListener('click', () => {
            // Reset to the current model's default lighting
            applyModelDefaultLight();
            saveMmdSettings();
        });
        if (saveDefaultBtn) {
            saveDefaultBtn.addEventListener('click', () => {
                saveCurrentLightAsModelDefault();
                // innerHTML（而非 textContent），textContent 会把图标 span 抹掉
                saveDefaultBtn.innerHTML = ic('save', 'icn-sm') + 'Saved!';
                setTimeout(() => { saveDefaultBtn.innerHTML = ic('save', 'icn-sm') + 'Default'; }, 1200);
            });
        }
    }

    let skyboxTexture = null, skyboxMesh = null, skyboxLoadedUrl = '';

    function disposeSkybox() {
        // 清理 scene 级引用（始终清除，避免残留）
        scene.background = null;
        scene.environment = null;
        // 清理等距矩形纹理（非 HDR 路径用 scene.background）
        if (skyboxTexture) {
            skyboxTexture.dispose();
            skyboxTexture = null;
        }
        // 清理 mesh（立方体天空盒 / HDR 球体）
        if (skyboxMesh) {
            scene.remove(skyboxMesh);
            skyboxMesh.geometry.dispose();
            if (Array.isArray(skyboxMesh.material)) {
                skyboxMesh.material.forEach(m => { if (m.map) m.map.dispose(); m.dispose(); });
            } else {
                if (skyboxMesh.material.map) skyboxMesh.material.map.dispose();
                skyboxMesh.material.dispose();
            }
            skyboxMesh = null;
        }
    }

    function applySkybox() {
        if (!scene) return;
        const enabled = mmdLight.skyboxEnabled === true;
        // 容错：Windows 本地路径 (D:\...\static\skybox\xxx) → URL (/static/skybox/xxx)
        const normalizeSkyboxUrl = (raw) => {
            const u = (raw || '').trim().replace(/\\/g, '/');
            const m = u.match(/(?:^|\/)static\/(.+)/i);
            return m ? ('/static/' + m[1]) : u;
        };
        const url = normalizeSkyboxUrl(mmdLight.skyboxUrl);

        console.log('[Skybox] applySkybox called — enabled:', enabled, 'url:', url);

        const fallbackBg = () => {
            scene.environment = null;
            if (mmdLight.bg && mmdLight.bg !== 'transparent') {
                scene.background = new THREE.Color(mmdLight.bg);
            } else {
                scene.background = null;
            }
        };

        if (!enabled || !url) {
            console.log('[Skybox] Disabled or no URL — falling back to plain background');
            disposeSkybox();
            skyboxLoadedUrl = '';
            fallbackBg();
            return;
        }

        // 自动判断模式：以图片扩展名结尾 = equirect 单张全景图；否则 = cube 立方体前缀
        const isEquirect = /\.(jpg|jpeg|png|bmp|tga|webp|hdr|exr)$/i.test(url);
        // HDR 类格式（线性空间 DataTexture）：.hdr 和 .exr 处理逻辑一致
        const isHdr = /\.(hdr|exr)$/i.test(url);
        const isExr = /\.exr$/i.test(url);
        const rotDeg = (mmdLight.skyboxRotation || 0);

        // —— 旋转更新（URL 不变时）——
        if (isEquirect && skyboxTexture && !isHdr) {
            skyboxTexture.wrapS = THREE.RepeatWrapping;
            skyboxTexture.offset.x = (rotDeg % 360) / 360;
            skyboxTexture.needsUpdate = true;
        }
        if (skyboxMesh) {
            skyboxMesh.rotation.y = rotDeg * Math.PI / 180;
        }

        if (url !== skyboxLoadedUrl) {
            disposeSkybox();
            skyboxLoadedUrl = url;

            if (isEquirect) {
                // 根据格式选择 loader：.hdr 用 RGBELoader，.exr 用 EXRLoader，.tga 用 TGALoader，其他用 TextureLoader
                const loader = isExr ? new THREE.EXRLoader()
                    : (isHdr ? new THREE.RGBELoader()
                    : (/\.tga$/i.test(url) ? new THREE.TGALoader() : new THREE.TextureLoader()));
                console.log('[Skybox] Loading equirect texture:', url, isHdr ? '(HDR/EXR → 3D sphere mesh)' : '');

                loader.load(url, (tex) => {
                    tex.needsUpdate = true;

                    if (isHdr) {
                        // —— HDR: 用球体 mesh 渲染 3D 天空球（和立方体天空盒同一架构）——
                        // RGBELoader 返回的 DataTexture 是线性空间，不覆盖 encoding
                        // 设置 wrapS=RepeatWrapping 消除左右接缝
                        tex.wrapS = THREE.RepeatWrapping;
                        tex.generateMipmaps = false;
                        tex.minFilter = THREE.LinearFilter;
                        tex.magFilter = THREE.LinearFilter;
                        tex.needsUpdate = true;
                        const geo = new THREE.SphereGeometry(800, 64, 32);
                        const mat = new THREE.MeshBasicMaterial({
                            map: tex, side: THREE.BackSide, depthWrite: false, fog: false
                        });
                        skyboxMesh = new THREE.Mesh(geo, mat);
                        skyboxMesh.renderOrder = -1;
                        skyboxMesh.rotation.y = rotDeg * Math.PI / 180;
                        scene.add(skyboxMesh);
                        // 仅当 Reflection 开关启用时设环境反射（和普通全景图逻辑一致）
                        if (mmdLight.skyboxEnv === true) scene.environment = tex;
                        console.log('[Skybox] HDR/EXR sphere mesh built, env:', mmdLight.skyboxEnv === true);
                    } else {
                        // —— JPG/PNG/TGA: 用 scene.background 渲染（r106 原生支持）——
                        if (THREE.sRGBEncoding !== undefined) tex.encoding = THREE.sRGBEncoding;
                        tex.mapping = THREE.EquirectangularReflectionMapping;
                        tex.wrapS = THREE.RepeatWrapping;
                        tex.offset.x = (rotDeg % 360) / 360;
                        skyboxTexture = tex;
                        scene.background = tex;
                        console.log('[Skybox] Equirect applied to scene.background');
                        if (mmdLight.skyboxEnv === true) scene.environment = tex;
                    }
                }, undefined, (err) => {
                    console.warn('[Skybox] Equirect load FAILED:', err);
                    skyboxLoadedUrl = '';
                    fallbackBg();
                });
            } else {
                // —— 立方体天空盒（6 面）—— 用 BoxGeometry + BackSide mesh 渲染
                //   统一用每面一个 MeshBasicMaterial，TGA/JPG/PNG 全支持
                // 后缀方案（按优先级探测，覆盖常见命名变体）：
                //   单字母:     _R  _L  _U  _D  _F  _B
                //   双字母 v1:  _RT _LF _UP _DN _FT _BK
                //   双字母 v2:  _RT _LF _UP _DN _FR _BK   (Front=FR 变体)
                //   双字母 v3:  _RT _LF _UP _DN _F  _B    (混合)
                const suffixSchemes = [
                    ['_R', '_L', '_U', '_D', '_F', '_B'],
                    ['_RT', '_LF', '_UP', '_DN', '_FT', '_BK'],
                    ['_RT', '_LF', '_UP', '_DN', '_FR', '_BK'],
                    ['_RT', '_LF', '_UP', '_DN', '_F', '_B'],
                ];
                const exts = ['.jpg', '.png', '.tga'];

                const tryLoadCube = (schemeIdx, extIdx) => {
                    if (schemeIdx >= suffixSchemes.length) {
                        console.warn('[Skybox] Cube load FAILED: 未找到匹配的6面贴图（尝试了所有命名方案与格式）');
                        skyboxLoadedUrl = '';
                        fallbackBg();
                        return;
                    }
                    if (extIdx >= exts.length) {
                        return tryLoadCube(schemeIdx + 1, 0);
                    }
                    const suffixes = suffixSchemes[schemeIdx];
                    const ext = exts[extIdx];
                    const urls = suffixes.map(s => url + s + ext);

                    // 并行探测全部 6 面，只有全部存在才算匹配
                    console.log('[Skybox] Probing scheme', schemeIdx, 'ext', ext, '— checking all 6 faces:', urls);
                    Promise.all(urls.map(u => fetch(u, { method: 'HEAD' }).then(r => r.ok).catch(() => false))
                    ).then(results => {
                        if (results.every(ok => ok)) {
                            console.log('[Skybox] All 6 faces matched! Loading:', urls);
                            loadCubeFaces(urls, ext);
                        } else {
                            const missing = urls.filter((_, i) => !results[i]);
                            console.log('[Skybox] Scheme', schemeIdx, ext, 'missing:', missing);
                            tryLoadCube(schemeIdx, extIdx + 1);
                        }
                    });
                };

                // 用 BoxGeometry 渲染：每面一个材质，side=BackSide，fog=false
                // BoxGeometry 面顺序: [px,right] [nx,left] [py,top] [ny,bottom] [pz,front] [nz,back]
                // 对应后缀顺序:       _R            _L          _U        _D         _F          _B
                const loadCubeFaces = (urls, ext) => {
                    const isTga = (ext === '.tga');
                    const loader = isTga ? new THREE.TGALoader() : new THREE.TextureLoader();
                    const materials = [null, null, null, null, null, null];
                    let loaded = 0, failed = false;

                    urls.forEach((u, i) => {
                        loader.load(u, (tex) => {
                            if (failed) return;

                            // 诊断：打印 texture.image 信息
                            const img = tex.image;
                            const w = (img && (img.width || img.naturalWidth)) || '?';
                            const h = (img && (img.height || img.naturalHeight)) || '?';
                            console.log('[Skybox] Face', i, '— image type:', img && img.constructor && img.constructor.name,
                                'size:', w + 'x' + h);

                            // TGA 返回的 Canvas/ImageBitmap 可能是非2次幂尺寸，
                            // THREE.Texture 默认 generateMipmaps=true 会导致 WebGL 上传失败（黑/透明）
                            if (isTga) {
                                tex.generateMipmaps = false;
                                tex.minFilter = THREE.LinearFilter;
                                tex.magFilter = THREE.LinearFilter;
                                tex.wrapS = THREE.ClampToEdgeWrapping;
                                tex.wrapT = THREE.ClampToEdgeWrapping;
                            }
                            if (THREE.sRGBEncoding !== undefined) tex.encoding = THREE.sRGBEncoding;
                            tex.needsUpdate = true;

                            materials[i] = new THREE.MeshBasicMaterial({
                                map: tex, side: THREE.BackSide, depthWrite: false, fog: false
                            });
                            loaded++;
                            console.log('[Skybox] Face', i, 'loaded (' + loaded + '/6)');
                            if (loaded === 6) {
                                const geo = new THREE.BoxGeometry(800, 800, 800);
                                skyboxMesh = new THREE.Mesh(geo, materials);
                                skyboxMesh.renderOrder = -1;
                                skyboxMesh.rotation.y = rotDeg * Math.PI / 180;
                                scene.background = null;
                                scene.add(skyboxMesh);
                                console.log('[Skybox] Cube mesh built and added to scene');
                            }
                        }, undefined, (err) => {
                            if (failed) return;
                            failed = true;
                            console.warn('[Skybox] Cube face load FAILED:', u, err);
                            skyboxLoadedUrl = '';
                            fallbackBg();
                        });
                    });
                };

                tryLoadCube(0, 0);
            }
        } else {
            console.log('[Skybox] URL unchanged, rotation updated');
        }
    }

    function applyMmdLightVisuals() {
        // Basic lights (always active)
        if (mainDirLight) mainDirLight.color.set(mmdLight.dirColor || '#ffffff');
        if (mainTopLight) mainTopLight.color.set(mmdLight.topColor || '#ffffff');
        if (mainAmbientLight) mainAmbientLight.color.set(mmdLight.ambientColor || '#aaaaaa');

        // Advanced lights — gated by advEnabled master switch
        const adv = mmdLight.advEnabled !== false;

        if (rimLight) {
            rimLight.color.set(mmdLight.rimColor || '#4488ff');
            rimLight.intensity = adv ? BASE_RIM * (mmdLight.rimIntensity ?? 0.6) : 0;
        }
        if (fillLight) {
            fillLight.color.set(mmdLight.fillColor || '#fff5e0');
            fillLight.intensity = adv ? BASE_FILL * (mmdLight.fillIntensity ?? 0.3) : 0;
        }
        if (hemiLight) {
            hemiLight.color.set(mmdLight.hemiSky || '#88aaff');
            hemiLight.groundColor.set(mmdLight.hemiGround || '#504030');
            hemiLight.intensity = adv ? BASE_HEMI * (mmdLight.hemiIntensity ?? 0.4) : 0;
        }
        if (pointLight) {
            pointLight.color.set(mmdLight.pointColor || '#ffcc88');
            pointLight.intensity = adv ? (mmdLight.pointIntensity ?? 0) : 0;
        }

        // Background / Skybox — delegated to applySkybox()
        if (scene) {
            applySkybox();
        }

        // Fog — also gated by advEnabled
        if (scene) {
            if (adv) {
                const density = mmdLight.fogDensity ?? 0.035;
                if (density > 0) {
                    if (!scene.fog) {
                        scene.fog = new THREE.FogExp2(mmdLight.fogColor || '#000000', density);
                    } else {
                        scene.fog.color.set(mmdLight.fogColor || '#000000');
                        scene.fog.density = density;
                    }
                } else {
                    scene.fog = null;
                }
            } else {
                scene.fog = null;
            }
        }
    }

    function loadMmdSettings() {
        try {
            const raw = localStorage.getItem(MMD_SETTINGS_KEY);
            if (raw) {
                const data = JSON.parse(raw);
                if (data && typeof data === 'object') {
                    genogeTieMode = MmdTiePhysics.normalizeMode(data.genogeTieMode);
                    if (typeof data.ambient === 'number') mmdLight.ambient = data.ambient;
                    if (typeof data.dir === 'number') mmdLight.dir = data.dir;
                    if (typeof data.top === 'number') mmdLight.top = data.top;
                    // Backward compatibility: single color → all 3 colors
                    if (typeof data.dirColor === 'string') mmdLight.dirColor = data.dirColor;
                    else if (typeof data.color === 'string') mmdLight.dirColor = data.color;
                    if (typeof data.topColor === 'string') mmdLight.topColor = data.topColor;
                    else if (typeof data.color === 'string') mmdLight.topColor = data.color;
                    if (typeof data.ambientColor === 'string') mmdLight.ambientColor = data.ambientColor;
                    else if (typeof data.color === 'string') mmdLight.ambientColor = data.color;
                    if (typeof data.bg === 'string') mmdLight.bg = data.bg;
                    // Advanced lighting fields (backward compatible: missing = keep defaults)
                    const advKeys = ['advEnabled','rimColor','rimIntensity','fillColor','fillIntensity',
                        'hemiSky','hemiGround','hemiIntensity','pointColor','pointIntensity',
                        'fogColor','fogDensity',
                        'skyboxEnabled','skyboxUrl','skyboxRotation','skyboxEnv'];
                    advKeys.forEach(k => {
                        if (data[k] !== undefined && data[k] !== null) mmdLight[k] = data[k];
                    });
                    // 读取保存的视角参数（相机位置/target）
                    if (data._view && typeof data._view === 'object') {
                        mmdLight._view = data._view;
                    }
                    if (typeof data.modelId === 'string') {
                        selectCurrentModel(data.modelId);
                    }
                    return true;
                }
            }
        } catch (e) { console.warn('Failed to load 3D settings', e); }
        return false;
    }

    // [ADDED] Resolve a model by id, updating url + config + physics tunables.
    // Returns true when the model was found.
    function selectCurrentModel(id) {
        const m = MMD_MODELS.find(x => x.id === id);
        if (!m) return false;
        currentModelId = m.id;
        currentModelUrl = m.url;
        currentModelConfig = m;
        applyCurrentModelPhysics();
        syncMmdTieControls();
        return true;
    }

    function syncMmdTieControls() {
        const section = document.getElementById('mmd-tie-settings');
        const select = document.getElementById('mmd-tie-mode');
        const hint = document.getElementById('mmd-tie-hint');
        if (!section || !select || !hint) return;
        section.hidden = currentModelId !== 'genoge_miku';
        if (!select.options.length) {
            Object.entries(MmdTiePhysics.profiles).forEach(([value, profile]) => {
                select.add(new Option(profile.label, value));
            });
        }
        select.value = genogeTieMode;
        hint.textContent = `${MmdTiePhysics.getProfile(genogeTieMode).description} Reload the model after switching.`;
    }

    // [ADDED] Load per-model light defaults: user-saved overrides take priority,
    // otherwise fall back to the model's built-in defaultLight.
    function getModelDefaultLight(modelId) {
        const model = MMD_MODELS.find(m => m.id === modelId);
        const baseDefaults = {
            ambient: 1, dir: 1, top: 1, ambientColor: '#aaaaaa', dirColor: '#ffffff', topColor: '#ffffff', bg: 'transparent',
            advEnabled: true,
            rimColor: '#4488ff', rimIntensity: 0.6, fillColor: '#fff5e0', fillIntensity: 0.3,
            hemiSky: '#88aaff', hemiGround: '#504030', hemiIntensity: 0.4,
            pointColor: '#ffcc88', pointIntensity: 0,
            fogColor: '#000000', fogDensity: 0.035,
            skyboxEnabled: false, skyboxUrl: '', skyboxRotation: 0, skyboxEnv: false,
        };
        let builtin = (model && model.defaultLight) ? { ...baseDefaults, ...model.defaultLight } : { ...baseDefaults };
        // Backward compatibility: if an old default only had a single 'color', use it for all directional colors
        if (!builtin.dirColor && builtin.color) builtin.dirColor = builtin.color;
        if (!builtin.topColor && builtin.color) builtin.topColor = builtin.color;
        if (!builtin.ambientColor && builtin.color) builtin.ambientColor = builtin.color;
        try {
            const raw = localStorage.getItem(MMD_LIGHT_DEFAULTS_KEY);
            if (raw) {
                const all = JSON.parse(raw);
                if (all && all[modelId] && typeof all[modelId] === 'object') {
                    const saved = all[modelId];
                    if (!saved.dirColor && saved.color) saved.dirColor = saved.color;
                    if (!saved.topColor && saved.color) saved.topColor = saved.color;
                    if (!saved.ambientColor && saved.color) saved.ambientColor = saved.color;
                    return { ...builtin, ...saved };
                }
            }
        } catch (e) { /* ignore */ }
        return { ...builtin };
    }

    // [ADDED] Save current mmdLight as the default for the active model.
    function saveCurrentLightAsModelDefault() {
        try {
            const raw = localStorage.getItem(MMD_LIGHT_DEFAULTS_KEY);
            let all = {};
            if (raw) all = JSON.parse(raw) || {};
            // 保存灯光配置 + 当前视角（相机位置、target、模型缩放/位置）+ 模型框大小
            const viewState = {};
            if (camera) {
                viewState.camPos = [camera.position.x, camera.position.y, camera.position.z];
            }
            if (controls) {
                viewState.camTarget = [controls.target.x, controls.target.y, controls.target.z];
            }
            if (currentModelConfig) {
                viewState.modelScale = currentModelConfig.scale;
                viewState.modelPosY = currentModelConfig.posY;
            }
            const frame = document.getElementById('draggable-model');
            if (frame) {
                viewState.frameW = Math.round(frame.offsetWidth);
                viewState.frameH = Math.round(frame.offsetHeight);
            }
            all[currentModelId] = { ...mmdLight, _view: viewState };
            localStorage.setItem(MMD_LIGHT_DEFAULTS_KEY, JSON.stringify(all));
            // 让本次保存的视角/框大小立即成为会话内的恢复基准，
            // 防止后续 saveMmdSettings 把旧视角快照写回去。
            mmdLight._view = viewState;
        } catch (e) { /* ignore */ }
    }

    // [ADDED] Apply the active model's default lighting to mmdLight + visuals.
    function applyModelDefaultLight() {
        mmdLight = getModelDefaultLight(currentModelId);
        applyMmdLightVisuals();
        syncLightControlsToState();
    }

    // [ADDED] Apply a saved size to the draggable model frame, keeping the
    // camera aspect + renderer in sync (same updates the resize handles do).
    function applyModelFrameSize(w, h) {
        const frame = document.getElementById('draggable-model');
        if (!frame || !Number.isFinite(w) || !Number.isFinite(h)) return;
        // Same bounds logic as the resize handles: keep the frame inside the
        // chat region and never narrower than the handle minimum.
        const panel = document.querySelector('.model-panel');
        const sidebarWidth = panel && !panel.classList.contains('collapsed')
            ? Math.round(panel.getBoundingClientRect().width) : 0;
        const minWidth = 160;
        const maxWidth = Math.max(minWidth, window.innerWidth - sidebarWidth - 8);
        const newW = Math.min(maxWidth, Math.max(minWidth, Math.round(w)));
        const newH = Math.max(120, Math.round(h));
        frame.style.width = newW + 'px';
        frame.style.height = newH + 'px';
        if (renderer && camera) {
            camera.aspect = newW / newH;
            camera.updateProjectionMatrix();
            renderer.setSize(newW, newH);
        }
    }

    // [ADDED] Apply saved view state (camera position/target) from mmdLight._view.
    // Called after model load completes. Falls back silently if no saved view.
    function applyViewState() {
        const v = mmdLight._view;
        if (!v || !camera || !controls) return;
        if (Array.isArray(v.camPos) && v.camPos.length === 3) {
            camera.position.set(v.camPos[0], v.camPos[1], v.camPos[2]);
        }
        if (Array.isArray(v.camTarget) && v.camTarget.length === 3) {
            controls.target.set(v.camTarget[0], v.camTarget[1], v.camTarget[2]);
        }
        if (Number.isFinite(v.frameW) && Number.isFinite(v.frameH) && v.frameW > 0 && v.frameH > 0) {
            applyModelFrameSize(v.frameW, v.frameH);
        }
        controls.update();
        if (typeof initialCameraDistance !== 'undefined') {
            initialCameraDistance = camera.position.distanceTo(controls.target);
        }
        console.log('[View] Restored view state:', v);
    }

    // [ADDED] Sync the panel's slider/color inputs to mmdLight.
    function syncLightControlsToState() {
        const ambientIn = document.getElementById('mmd-light-ambient');
        const dirIn = document.getElementById('mmd-light-dir');
        const topIn = document.getElementById('mmd-light-top');
        const ambientColorIn = document.getElementById('mmd-ambient-color');
        const dirColorIn = document.getElementById('mmd-dir-color');
        const topColorIn = document.getElementById('mmd-top-color');
        const bgIn = document.getElementById('mmd-bg-color');
        if (!ambientIn) return;
        ambientIn.value = mmdLight.ambient;
        dirIn.value = mmdLight.dir;
        topIn.value = mmdLight.top;
        if (ambientColorIn) ambientColorIn.value = mmdLight.ambientColor || '#aaaaaa';
        if (dirColorIn) dirColorIn.value = mmdLight.dirColor || '#ffffff';
        if (topColorIn) topColorIn.value = mmdLight.topColor || '#ffffff';
        bgIn.value = (mmdLight.bg && mmdLight.bg !== 'transparent') ? mmdLight.bg : '#000000';

        // Advanced lighting controls
        const setVal = (id, val) => { const el = document.getElementById(id); if (el) el.value = val; };
        setVal('mmd-rim-color', mmdLight.rimColor || '#4488ff');
        setVal('mmd-rim-intensity', mmdLight.rimIntensity ?? 0.6);
        setVal('mmd-fill-color', mmdLight.fillColor || '#fff5e0');
        setVal('mmd-fill-intensity', mmdLight.fillIntensity ?? 0.3);
        setVal('mmd-hemi-sky', mmdLight.hemiSky || '#88aaff');
        setVal('mmd-hemi-ground', mmdLight.hemiGround || '#504030');
        setVal('mmd-hemi-intensity', mmdLight.hemiIntensity ?? 0.4);
        setVal('mmd-point-color', mmdLight.pointColor || '#ffcc88');
        setVal('mmd-point-intensity', mmdLight.pointIntensity ?? 0);
        setVal('mmd-fog-color', mmdLight.fogColor || '#000000');
        setVal('mmd-fog-density', mmdLight.fogDensity ?? 0.035);
        const skyEn = document.getElementById('mmd-skybox-enabled');
        const skyUrl = document.getElementById('mmd-skybox-url');
        const skyRot = document.getElementById('mmd-skybox-rotation');
        const skyEnv = document.getElementById('mmd-skybox-env');
        if (skyEn) skyEn.checked = mmdLight.skyboxEnabled === true;
        if (skyUrl) skyUrl.value = mmdLight.skyboxUrl || '';
        if (skyRot) skyRot.value = mmdLight.skyboxRotation ?? 0;
        if (skyEnv) skyEnv.checked = mmdLight.skyboxEnv === true;
    }

    // [ADDED] Sync the global physics tunables from the active model's own block,
    // so per-model overrides take effect without touching the others.
    function applyCurrentModelPhysics() {
        const p = (currentModelConfig && currentModelConfig.physics) || defaultModelConfig().physics;
        PHYSICS_RESET_INTERVAL = p.resetInterval;
        PHYSICS_PANIC_COOLDOWN = p.panicCooldown;
        PHYSICS_MAX_ROOT_DRIFT = p.maxRootDrift;
        PHYSICS_MAX_FOOT_DRIFT = p.maxFootDrift;
        PHYSICS_MAX_FRAME_Y_DELTA = p.maxFrameYDelta;
        PHYSICS_MAX_VISUAL_CENTER_JUMP = p.maxVisualCenterJump;
        PHYSICS_MAX_VISUAL_SIZE_JUMP = p.maxVisualSizeJump;
        PHYSICS_RECOVERY_MOTION_DELAY_MS = p.recoveryMotionDelayMs;
        PHYSICS_RECOVERY_CROSSFADE_SECONDS = p.recoveryCrossfadeSeconds;
    }

    function saveMmdSettings() {
        try {
            // 每次保存时同步当前视角到 _view，使常规保存也记录最新相机位置
            const viewState = {};
            if (camera) viewState.camPos = [camera.position.x, camera.position.y, camera.position.z];
            if (controls) viewState.camTarget = [controls.target.x, controls.target.y, controls.target.z];
            localStorage.setItem(MMD_SETTINGS_KEY, JSON.stringify({
                ...mmdLight, _view: viewState, modelId: currentModelId, genogeTieMode
            }));
        } catch (e) { /* ignore */ }
    }

    function init() {
        const container = document.querySelector('.canvas-container');
        if (!container) {
            console.error("Canvas container not found!");
            return;
        }
        const width = container.clientWidth;
        const height = container.clientHeight;

        scene = new THREE.Scene();
        camera = new THREE.PerspectiveCamera(45, width / height, 1, 2000);
        camera.position.set(0, 10, 30);

        renderer = new THREE.WebGLRenderer({
            canvas: document.getElementById('mmd-canvas'),
            alpha: true,
            antialias: true,
            logarithmicDepthBuffer: true,
            precision: 'highp'
        });
        renderer.setSize(width, height);
        renderer.setPixelRatio(window.devicePixelRatio);
        // 设置输出编码为 sRGB，确保 HDR/线性空间贴图颜色正确显示
        if (THREE.sRGBEncoding !== undefined) renderer.outputEncoding = THREE.sRGBEncoding;
        
        // [MODIFIED] Store lights in global variables
        mainAmbientLight = new THREE.AmbientLight(0xaaaaaa, BASE_AMBIENT);
        scene.add(mainAmbientLight);
        
        mainDirLight = new THREE.DirectionalLight(0xffffff, BASE_DIR);
        mainDirLight.position.set(0, 20, 10);
        scene.add(mainDirLight);

        // [MODIFIED] Advanced Rendering: Fog only (Grid removed)
        scene.fog = new THREE.FogExp2(0x000000, 0.035); // Slightly denser fog for darker surroundings

        // Additional overhead light for realism
        mainTopLight = new THREE.DirectionalLight(0xffffff, BASE_TOP);
        mainTopLight.position.set(0, 50, 0);
        scene.add(mainTopLight);

        // [ADDED] Advanced lights — created here, configured by applyMmdLightVisuals()
        // Rim light: behind the model, creates edge glow
        rimLight = new THREE.DirectionalLight(0x4488ff, BASE_RIM);
        rimLight.position.set(0, 15, -20);
        scene.add(rimLight);

        // Fill light: opposite side of key light, softens shadows
        fillLight = new THREE.DirectionalLight(0xfff5e0, BASE_FILL);
        fillLight.position.set(-15, 10, 5);
        scene.add(fillLight);

        // Hemisphere light: sky/ground gradient for natural ambient
        hemiLight = new THREE.HemisphereLight(0x88aaff, 0x504030, BASE_HEMI);
        scene.add(hemiLight);

        // Point light: local accent light (off by default)
        pointLight = new THREE.PointLight(0xffcc88, 0, 50, 2);
        pointLight.position.set(10, 20, 10);
        scene.add(pointLight);

        controls = new THREE.OrbitControls(camera, renderer.domElement);
        controls.enableKeys = false; // Disable keyboard panning so arrow keys work normally in all input fields
        loader = new THREE.MMDLoader();
        initModelPartInteractions();

        // [ADDED] Calculate initial camera distance (approx to target 0,0,0 or just length)
        // Since controls.target defaults to 0,0,0 and camera is at 0,10,30
        initialCameraDistance = camera.position.distanceTo(new THREE.Vector3(0, 0, 0));
        
        animationHelper = createAnimationHelper();

        // [ADDED] Restore persisted 3D settings (model + lighting) before first load
        const hadSavedLighting = loadMmdSettings();
        applyCurrentModelPhysics(); // ensure tunables match the resolved model
        if (!hadSavedLighting) {
            // First visit: use this model's built-in default lighting
            applyModelDefaultLight();
        } else {
            applyMmdLightVisuals();
        }
        // Default 按钮保存的视角（按模型，含模型框大小）优先于
        // saya_mmd_3d_settings 里的 _view —— 后者只在调灯光时更新，
        // 是旧快照，会把 Default 保存的镜头视角顶掉。
        const savedDefaultView = getModelDefaultLight(currentModelId)._view;
        if (savedDefaultView) mmdLight._view = savedDefaultView;

        loadModel(true);

        // [ADDED] Wire up the hidden 3D settings panel
        initMmdSettingsPanel();
        
        // Handle window resize
        window.addEventListener('resize', onWindowResize, false);
    }

    function onWindowResize() {
        const container = document.querySelector('.canvas-container');
        if (!container || !camera || !renderer) return;
        
        const width = container.clientWidth;
        const height = container.clientHeight;
        
        camera.aspect = width / height;
        camera.updateProjectionMatrix();
        renderer.setSize(width, height);
    }

    function setupMaterial(material) {
        material.side = THREE.FrontSide;
        material.transparent = true;
        material.opacity = 1.0;
        material.depthWrite = true;
        material.depthTest = true;
        material.flatShading = false;
        material.skinning = true;
        material.morphTargets = true;
        material.morphNormals = false; // Disable morph normals to prevent lighting issues/discoloration
        material.needsUpdate = true;
    }

    function animate() {
        requestAnimationFrame(animate);
        
        // [ADDED] Dynamic Lighting Adjustment based on Camera Distance
        if (controls && mainAmbientLight && mainDirLight && mainTopLight && initialCameraDistance > 0) {
            const currentDist = camera.position.distanceTo(controls.target);
            // Calculate ratio: if closer (ratio < 1), darker; if farther (ratio > 1), brighter
            // We use a power curve to make it less aggressive or more aggressive if needed.
            // Linear is a good start. 
            // Clamp ratio to avoid extreme values (e.g. 0.2 to 3.0)
            let ratio = currentDist / initialCameraDistance;
            ratio = Math.max(0.2, Math.min(ratio, 3.0));

            // Apply to lights (including user-adjustable multipliers)
            mainAmbientLight.intensity = BASE_AMBIENT * mmdLight.ambient * ratio;
            mainDirLight.intensity = BASE_DIR * mmdLight.dir * ratio;
            mainTopLight.intensity = BASE_TOP * mmdLight.top * ratio;
            // Advanced lights follow the same camera-distance ratio
            if (rimLight) rimLight.intensity = BASE_RIM * (mmdLight.rimIntensity ?? 0.6) * ratio;
            if (fillLight) fillLight.intensity = BASE_FILL * (mmdLight.fillIntensity ?? 0.3) * ratio;
            if (hemiLight) hemiLight.intensity = BASE_HEMI * (mmdLight.hemiIntensity ?? 0.4) * ratio;
            if (pointLight) pointLight.intensity = (mmdLight.pointIntensity ?? 0) * ratio;
        }

        const delta = clock.getDelta() * motionSpeed;
        if (mmdPhysicsStepper && genogeTieMode !== 'legacy' && document.hidden) {
            mmdPhysicsStepper.reset();
        } else if (mmdPhysicsStepper) {
            mmdPhysicsStepper.advance(delta, updateMmdSimulationStep);
        } else {
            updateMmdSimulationStep(delta);
        }

        sampleMmdShoeDiagnostics(Date.now());
        controls.update();
        renderer.render(scene, camera);
    }

    function updateMmdSimulationStep(delta) {
        const beforeY = currentMesh ? currentMesh.position.y : 0;
        // Preserve the old correction rate when animation and physics run at 120/240 Hz.
        const correctionOptions = mmdPhysicsStepper && genogeTieMode !== 'legacy'
            ? { correctionSpeed: 1 - Math.pow(1 - 0.16, delta * 60) } : {};

        if (mixer && currentMesh) {
            mixer.update(delta);
        }

        forceResetPosition(correctionOptions);
        const prePhysicsInstability = detectPhysicsInstability(beforeY);
        const physicsResetStarted = prePhysicsInstability
            ? resetMmdPhysics(`pre-update instability (${prePhysicsInstability})`)
            : false;
        
        // Periodic Physics Hard Reset to prevent vibration accumulation
        const now = Date.now();
        if (
            !physicsResetStarted &&
            animationHelper &&
            Number.isFinite(PHYSICS_RESET_INTERVAL) &&
            PHYSICS_RESET_INTERVAL > 0 &&
            now - lastPhysicsResetTime > PHYSICS_RESET_INTERVAL
        ) {
            // Disable and re-enable physics to reset accumulated forces
            animationHelper.enable('physics', false);
            forceResetPosition({ hard: true });
            resetPhysicsVisualSample();
            const helperSnapshot = animationHelper;
            if (physicsEnableTimer) clearTimeout(physicsEnableTimer);
            physicsEnableTimer = setTimeout(() => {
                if (animationHelper && animationHelper === helperSnapshot) {
                    forceResetPosition({ hard: true });
                    animationHelper.enable('physics', true);
                    resetPhysicsVisualSample();
                }
                physicsEnableTimer = null;
            }, 50); // Short delay to let the mesh settle
            lastPhysicsResetTime = now;
        }
        
        if (animationHelper && !physicsResetStarted) {
            try {
                animationHelper.update(delta);
                forceResetPosition(correctionOptions);
                const postPhysicsInstability = detectPhysicsInstability(beforeY);
                if (postPhysicsInstability) {
                    resetMmdPhysics(`post-update instability (${postPhysicsInstability})`);
                }
            } catch (error) {
                console.warn("Physics error", error);
                resetMmdPhysics(`physics exception: ${error.message || error}`, { force: true });
            }
        }

    }

    async function fetchMotionFiles(options = {}) {
        const expectedGeneration = modelLoadGeneration;
        try {
            const response = await fetch('/get_vmd_files');
            if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
            const data = await response.json();
            if (expectedGeneration !== modelLoadGeneration) return;
            
            const entries = Array.isArray(data.motions)
                ? data.motions
                : (Array.isArray(data.vmd_files) ? data.vmd_files.map(url => ({ url })) : []);

            if (entries.length > 0) {
                motionEntries = entries.filter(entry => entry && entry.url);
                motionFiles = motionEntries.map(entry => entry.url);
                if (options.resetIndex) {
                    currentMotionIndex = 0;
                    currentMotionState = 'normal_idle';
                }
                if (currentMesh) {
                    if (options.resetIndex && playOpeningMotionOnce(expectedGeneration)) {
                        return;
                    }
                    loadAndPlayMotion(currentMesh);
                }
            }
        } catch (error) {
            console.error('Fetch motion files failed:', error);
        }
    }

    function detachActiveMotionFinishedHandler() {
        if (mixer && activeMotionFinishedHandler) {
            try {
                mixer.removeEventListener('finished', activeMotionFinishedHandler);
            } catch (e) {
                console.warn("Motion finished handler cleanup failed", e);
            }
        }
        activeMotionFinishedHandler = null;
    }

    function clearMotionPlaybackCallbacks() {
        if (motionReturnTimer) {
            clearTimeout(motionReturnTimer);
            motionReturnTimer = null;
        }
        detachActiveMotionFinishedHandler();
    }

    function completeAfterMotionEndHold(
        motionEntry,
        motionIndex,
        onComplete,
        motionLabel = ''
    ) {
        if (motionReturnTimer) {
            clearTimeout(motionReturnTimer);
            motionReturnTimer = null;
        }
        detachActiveMotionFinishedHandler();

        const holdSeconds = getMotionEndHoldSeconds(motionEntry, motionIndex);
        if (holdSeconds <= 0) {
            onComplete();
            return;
        }

        console.log(
            `${motionLabel || getMotionEntryFileName(motionEntry, motionIndex)} ` +
            `reached its final frame; holding the pose for ${holdSeconds.toFixed(2)}s.`
        );
        motionReturnTimer = setTimeout(() => {
            motionReturnTimer = null;
            onComplete();
        }, Math.round(holdSeconds * 1000));
    }

    function clearPendingMotionRequest() {
        pendingMotionIndex = null;
        pendingMotionPriority = -Infinity;
        pendingMotionOptions = null;
    }

    function queuePendingMotionRequest(index, options = {}) {
        if (!Number.isInteger(index) || !motionFiles[index]) return false;
        const explicitPriority = Number(options.priority);
        const priority = Number.isFinite(explicitPriority)
            ? explicitPriority
            : (options.interruptOneShot === true ? 20 : 0);
        if (pendingMotionIndex === null || priority >= pendingMotionPriority) {
            pendingMotionIndex = index;
            pendingMotionPriority = priority;
            pendingMotionOptions = {
                ...options,
                isModelInteraction: false,
                resetPosition: false
            };
            return true;
        }
        return false;
    }

    function takePendingMotionRequest() {
        if (pendingMotionIndex === null) return null;
        const request = {
            index: pendingMotionIndex,
            options: pendingMotionOptions || { resetPosition: false }
        };
        clearPendingMotionRequest();
        return request;
    }

    function clearModelInteractionLockState() {
        if (modelInteractionLockTimer) {
            clearTimeout(modelInteractionLockTimer);
            modelInteractionLockTimer = null;
        }
        if (modelInteractionReturnTimer) {
            clearTimeout(modelInteractionReturnTimer);
            modelInteractionReturnTimer = null;
        }
        modelInteractionLockActive = false;
        modelInteractionReturningToIdle = false;
        modelInteractionMotionIndex = -1;
    }

    function beginModelInteractionLock(motionIndex) {
        clearModelInteractionLockState();
        clearPendingMotionRequest();
        modelInteractionLockActive = true;
        modelInteractionReturningToIdle = false;
        modelInteractionMotionIndex = motionIndex;
    }

    function restoreModelAnimationBindingState() {
        if (!currentMesh) return;
        if (animationHelper) {
            try {
                animationHelper.enable('physics', false);
            } catch (e) {
                console.warn("Physics disable during interaction reset failed", e);
            }
        }
        if (mixer) {
            try {
                mixer.stopAllAction();
                mixer.update(0);
            } catch (e) {
                console.warn("Animation mixer reset failed", e);
            }
        }

        const boneTransforms = animationBindingPose?.boneTransforms || [];
        const bones = currentMesh.skeleton?.bones || [];
        bones.forEach((bone, index) => {
            const initialTransform = boneTransforms[index];
            if (!initialTransform) return;
            bone.position.copy(initialTransform.position);
            bone.quaternion.copy(initialTransform.quaternion);
            bone.scale.copy(initialTransform.scale);
        });

        const initialMorphs = animationBindingPose?.morphTargetInfluences || [];
        const morphs = currentMesh.morphTargetInfluences || [];
        for (let index = 0; index < morphs.length; index += 1) {
            const initialValue = Number(initialMorphs[index]);
            morphs[index] = Number.isFinite(initialValue) ? initialValue : 0;
        }

        forceResetPosition({ hard: true });
        currentMesh.updateMatrixWorld(true);
    }

    function completeModelInteractionReturn(reason = 'classic idle transition') {
        if (!modelInteractionLockActive && !modelInteractionReturningToIdle) return;
        clearModelInteractionLockState();

        const queuedRequest = takePendingMotionRequest();
        console.log(`Model interaction idle return settled after ${reason}.`);
        if (queuedRequest && motionFiles[queuedRequest.index]) {
            currentMotionIndex = queuedRequest.index;
            loadAndPlayMotion(currentMesh, queuedRequest.options);
        }
    }

    function finishModelInteractionWindow(reason = 'three-second window') {
        if (!modelInteractionLockActive || modelInteractionReturningToIdle) return;
        if (modelInteractionLockTimer) {
            clearTimeout(modelInteractionLockTimer);
            modelInteractionLockTimer = null;
        }
        modelInteractionReturningToIdle = true;
        clearMotionPlaybackCallbacks();
        loadingMotionIndex = -1;
        loadingMotionEntry = null;

        const idleIndex = findClassicIdleMotionIndex();
        if (idleIndex === -1 || !motionFiles[idleIndex]) {
            console.warn('Classic idle motion not found; releasing model interaction lock without a smooth idle bridge.');
            clearModelInteractionLockState();
            currentMotionState = 'normal_idle';
            playIntelligentMotion({
                forceNormal: true,
                allowReaction: false,
                resetPosition: false
            });
            return;
        }

        currentMotionState = 'normal_idle';
        currentMotionIndex = idleIndex;
        console.log(`Model interaction released after ${reason}; fading into classic idle: ${motionFiles[idleIndex]}`);
        loadAndPlayMotion(currentMesh, {
            bypassInteractionLock: true,
            interruptOneShot: true,
            preservePendingQueue: true,
            forceRestart: true,
            resetPosition: false,
            crossfadeSeconds: MODEL_INTERACTION_RETURN_FADE_SECONDS,
            onMotionStarted: ({ action, clip }) => {
                const morphSettleDelayMs = Math.round((MODEL_INTERACTION_RETURN_FADE_SECONDS + 0.08) * 1000);
                setTimeout(() => {
                    settleModelInteractionMorphsToIdle(clip, action);
                }, morphSettleDelayMs);

                if (modelInteractionReturnTimer) {
                    clearTimeout(modelInteractionReturnTimer);
                }
                modelInteractionReturnTimer = setTimeout(() => {
                    modelInteractionReturnTimer = null;
                    completeModelInteractionReturn('classic idle fade');
                }, Math.round((MODEL_INTERACTION_RETURN_FADE_SECONDS + MODEL_INTERACTION_MORPH_SETTLE_SECONDS + 0.18) * 1000));
            }
        });
    }

    function scheduleModelInteractionTransition(action, motionEntry = {}, options = {}) {
        if (!modelInteractionLockActive) return;
        if (modelInteractionLockTimer) {
            clearTimeout(modelInteractionLockTimer);
            modelInteractionLockTimer = null;
        }

        const interactionFile = getMotionEntryFileName(
            motionEntry,
            modelInteractionMotionIndex
        );
        const waitForNaturalCompletion = (
            options.waitForNaturalCompletion === true ||
            modelInteractionUsesNaturalCompletion(
                motionEntry,
                modelInteractionMotionIndex
            )
        );
        if (waitForNaturalCompletion && action && mixer) {
            activeMotionFinishedHandler = function(event) {
                if (event.action !== action) return;
                completeAfterMotionEndHold(
                    motionEntry,
                    modelInteractionMotionIndex,
                    () => finishModelInteractionWindow(
                        `${interactionFile} natural completion and final-pose hold`
                    ),
                    interactionFile
                );
            };
            mixer.addEventListener('finished', activeMotionFinishedHandler);
            console.log(
                `Model interaction has no three-second timer; waiting for ${interactionFile} ` +
                `to finish naturally (${Number(options.durationSeconds || 0).toFixed(2)}s).`
            );
            return;
        }

        modelInteractionLockTimer = setTimeout(() => {
            finishModelInteractionWindow('three-second window');
        }, MODEL_INTERACTION_LOCK_MS);
    }

    function scheduleReturnToIdle(
        action,
        durationSeconds,
        playToken,
        playbackMode,
        motionEntry = {},
        loopRepetitions = 1
    ) {
        if (!['once_then_idle', 'finite_loop'].includes(playbackMode) || !action) return;

        const returnToStableMotion = () => {
            if (motionEntry.return_to_classic_idle === true) {
                playClassicIdleMotion(false);
                return;
            }
            if (playbackMode === 'finite_loop') {
                currentMotionState = 'normal_idle';
                playIntelligentMotion({
                    forceNormal: true,
                    allowReaction: false,
                    resetPosition: false
                });
                return;
            }
            const returnCategory = String(motionEntry.return_category || '').trim();
            if (
                returnCategory === 'night_sleepy' &&
                currentMotionState === 'night_sleepy' &&
                isNightTime()
            ) {
                playIntelligentMotion({
                    categoryOverride: 'night_sleepy',
                    allowReaction: false,
                    resetPosition: false
                });
                return;
            }
            if (isWaitingForResponse && returnCategory) {
                playIntelligentMotion({
                    categoryOverride: returnCategory,
                    allowReaction: false,
                    resetPosition: false
                });
                return;
            }
            playIntelligentMotion({ context: { emotion: 'normal' }, resetPosition: false });
        };

        let transitioned = false;
        const finishOneShot = () => {
            if (transitioned || playToken !== motionPlayToken) return;
            transitioned = true;
            clearMotionPlaybackCallbacks();

            const queuedRequest = takePendingMotionRequest();
            activeMotionIndex = -1;
            activeMotionEntry = null;
            activeMotionStartedAt = 0;
            activeMotionDurationSeconds = 0;
            activeMotionAction = null;

            if (
                queuedRequest &&
                motionFiles[queuedRequest.index]
            ) {
                currentMotionIndex = queuedRequest.index;
                loadAndPlayMotion(currentMesh, queuedRequest.options);
                return;
            }
            returnToStableMotion();
        };
        activeMotionFinishedHandler = function(event) {
            if (event.action !== action) return;
            completeAfterMotionEndHold(
                motionEntry,
                currentMotionIndex,
                finishOneShot
            );
        };
        mixer.addEventListener('finished', activeMotionFinishedHandler);

        motionReturnTimer = setTimeout(() => {
            motionReturnTimer = null;
            completeAfterMotionEndHold(
                motionEntry,
                currentMotionIndex,
                finishOneShot
            );
        }, Math.max(0.2, durationSeconds) * Math.max(1, loopRepetitions) * 1000 + 250);
    }

    function loadAndPlayMotion(mesh, options = {}) {
        if (!motionFiles || !Array.isArray(motionFiles) || motionFiles.length === 0) return;
        const shouldResetPosition = options.resetPosition !== false;
        const interruptOneShot = options.interruptOneShot === true;
        const isModelInteractionRequest = options.isModelInteraction === true;
        const bypassInteractionLock = options.bypassInteractionLock === true;
        const forceRestart = options.forceRestart === true;
        const requestedMotionIndex = currentMotionIndex;
        if (modelInteractionLockActive && !isModelInteractionRequest && !bypassInteractionLock) {
            queuePendingMotionRequest(requestedMotionIndex, options);
            return;
        }
        if (
            !forceRestart &&
            !isModelInteractionRequest &&
            activeMotionIndex === requestedMotionIndex &&
            activeMotionEntry
        ) return;
        if (
            !forceRestart &&
            !isModelInteractionRequest &&
            loadingMotionIndex === requestedMotionIndex &&
            loadingMotionEntry
        ) return;

        const activePlayback = String(activeMotionEntry?.playback || '').toLowerCase();
        const loadingPlayback = String(loadingMotionEntry?.playback || '').toLowerCase();
        const activeIsOneShot = activePlayback === 'once' || activePlayback === 'once_then_idle';
        const loadingIsOneShot = loadingPlayback === 'once' || loadingPlayback === 'once_then_idle';
        if (
            (activeIsOneShot && requestedMotionIndex !== activeMotionIndex) ||
            (loadingIsOneShot && requestedMotionIndex !== loadingMotionIndex)
        ) {
            if (interruptOneShot) {
                if (options.preservePendingQueue !== true) {
                    clearPendingMotionRequest();
                }
                motionPlayToken += 1;
                clearMotionPlaybackCallbacks();
            } else {
                queuePendingMotionRequest(requestedMotionIndex, options);
                return;
            }
        }

        const expectedGeneration = modelLoadGeneration;
        const expectedMotionLoadToken = ++motionLoadToken;
        const vmdFilePath = motionFiles[requestedMotionIndex];
        const baseMotionEntry = motionEntries[requestedMotionIndex] || {};
        const requestedPlaybackOverride = String(options.playbackOverride || '').toLowerCase();
        const requestedReturnCategory = String(options.returnCategoryOverride || '').trim();
        const requestedReturnToClassicIdle = options.returnToClassicIdle === true;
        const playbackMode = ['once', 'once_then_idle', 'finite_loop', 'loop'].includes(requestedPlaybackOverride)
            ? requestedPlaybackOverride
            : String(baseMotionEntry.playback || 'loop').toLowerCase();
        const requestedLoopRepetitions = Number(options.loopRepetitions);
        const loopRepetitions = playbackMode === 'finite_loop' && Number.isFinite(requestedLoopRepetitions)
            ? Math.max(1, Math.floor(requestedLoopRepetitions))
            : 1;
        const basePlaybackMode = String(baseMotionEntry.playback || 'loop').toLowerCase();
        const motionEntry = (
            playbackMode === basePlaybackMode &&
            !requestedReturnCategory &&
            !requestedReturnToClassicIdle
        )
            ? baseMotionEntry
            : {
                ...baseMotionEntry,
                playback: playbackMode,
                loop_repetitions: loopRepetitions,
                return_category: requestedReturnCategory || baseMotionEntry.return_category,
                return_to_classic_idle: (
                    requestedReturnToClassicIdle ||
                    baseMotionEntry.return_to_classic_idle === true
                )
            };
        const requestedCrossfadeSeconds = Number(options.crossfadeSeconds);
        const crossfadeSeconds = Number.isFinite(requestedCrossfadeSeconds)
            ? Math.max(0, requestedCrossfadeSeconds)
            : MOTION_CROSSFADE_SECONDS;
        loadingMotionIndex = requestedMotionIndex;
        loadingMotionEntry = motionEntry;
        console.log("Loading motion:", vmdFilePath);

        const processAndPlayVmd = (vmd) => {
             if (
                 expectedGeneration !== modelLoadGeneration ||
                 expectedMotionLoadToken !== motionLoadToken ||
                 mesh !== currentMesh
             ) return;
             loadingMotionIndex = -1;
             loadingMotionEntry = null;
             let motionStarted = false;
             if (mixer && vmd) {
                 try {
                      clearMotionPlaybackCallbacks();
                     const previousAction = activeMotionAction;
                     if (!previousAction) {
                         mixer.stopAllAction();
                     }

                     if (!currentMesh.geometry.animations) {
                         currentMesh.geometry.animations = [];
                     }

                     if (vmd.tracks && vmd.tracks.length > 0) {
                         if (!vmd.isProcessed) {
                             vmd.tracks = vmd.tracks.filter(track => {
                                 if (track.name.includes('.bones[')) {
                                     if (getPositionTrackBoneName(track.name)) {
                                         clampBoneYOffsetsToBindingPose(track);
                                     }
                                     return true;
                                 }
                                 return !track.name.includes('position') && !track.name.includes('quaternion') && !track.name.includes('.material');
                             });

                             let maxTime = 0;
                             vmd.tracks.forEach(track => {
                                 if (track.times && track.times.length > 0) {
                                     maxTime = Math.max(maxTime, track.times[track.times.length - 1]);
                                 }
                             });
                             vmd.duration = maxTime > 0 ? maxTime : 5.0;
                             vmd.isProcessed = true;
                         }

                         if (animationHelper && !(mmdPhysicsStepper && genogeTieMode !== 'legacy')) {
                             animationHelper.enable('physics', false);
                         }

                         currentMesh.geometry.animations = [vmd];
                         const action = mixer.clipAction(vmd);
                         if (action) {
                             const playToken = ++motionPlayToken;
                             const durationSeconds = Number(vmd.duration || action.getClip().duration || 5);
                             const fadedOutAction = previousAction && previousAction !== action
                                 ? previousAction
                                 : null;
                             if (previousAction === action) {
                                 action.stop();
                             }
                             action.reset();
                             action.enabled = true;
                             action.paused = false;
                             action.setEffectiveTimeScale(1);
                             action.setEffectiveWeight(1);
                             if (playbackMode === 'once' || playbackMode === 'once_then_idle') {
                                 action.setLoop(THREE.LoopOnce, 1);
                                 action.clampWhenFinished = true;
                             } else if (playbackMode === 'finite_loop') {
                                 action.setLoop(THREE.LoopRepeat, loopRepetitions);
                                 action.clampWhenFinished = true;
                             } else {
                                 action.setLoop(THREE.LoopRepeat);
                                 action.clampWhenFinished = false;
                             }
                             softPositionMotionCount += 1;
                             correctSoftZThisMotion = softPositionMotionCount % SOFT_Z_CORRECTION_MOTION_INTERVAL === 0;
                              if (shouldResetPosition) {
                                  forceResetPosition();
                              }
                              currentMotionIndex = requestedMotionIndex;
                              action.play();
                              motionStarted = true;
                              activeMotionIndex = requestedMotionIndex;
                              activeMotionEntry = motionEntry;
                             activeMotionStartedAt = Date.now();
                             activeMotionDurationSeconds = durationSeconds;
                             activeMotionAction = action;
                             if (fadedOutAction) {
                                 fadedOutAction.fadeOut(crossfadeSeconds);
                                 action.fadeIn(crossfadeSeconds);
                                 setTimeout(() => {
                                     if (activeMotionAction === fadedOutAction) return;
                                     try {
                                         fadedOutAction.stop();
                                     } catch (e) {
                                         console.warn("Motion fade cleanup failed", e);
                                     }
                                 }, (crossfadeSeconds + 0.1) * 1000);
                             } else if (!previousAction) {
                                 action.fadeIn(crossfadeSeconds);
                             }
                             if (typeof options.onMotionStarted === 'function') {
                                 try {
                                     options.onMotionStarted({
                                         action,
                                         clip: vmd,
                                         motionEntry,
                                         motionIndex: requestedMotionIndex,
                                         durationSeconds,
                                         playToken
                                     });
                                 } catch (e) {
                                     console.warn("Motion start callback failed", e);
                                 }
                             }
                             if (isModelInteractionRequest) {
                                 scheduleModelInteractionTransition(action, motionEntry, {
                                     waitForNaturalCompletion: options.waitForNaturalCompletion === true,
                                     durationSeconds
                                 });
                             } else {
                                 scheduleReturnToIdle(
                                     action,
                                     durationSeconds,
                                     playToken,
                                     playbackMode,
                                     motionEntry,
                                     loopRepetitions
                                 );
                             }

                             const helperSnapshot = animationHelper;
                             if (physicsEnableTimer) clearTimeout(physicsEnableTimer);
                             if (mmdPhysicsStepper && genogeTieMode !== 'legacy') {
                                 // Keep contact solving during animation crossfades; freezing it for
                                 // 100 ms lets the animated torso jump into a stationary tie.
                                 animationHelper.enable('physics', true);
                                 physicsEnableTimer = null;
                             } else {
                                 physicsEnableTimer = setTimeout(() => {
                                     if (animationHelper && animationHelper === helperSnapshot) {
                                        animationHelper.enable('physics', true);
                                     }
                                     physicsEnableTimer = null;
                                 }, 100);
                             }
                         }
                     }
                 } catch (error) {
                      console.error("Error playing motion:", error);
                       if (expectedMotionLoadToken === motionLoadToken) {
                           loadingMotionIndex = -1;
                           loadingMotionEntry = null;
                       }
                   }
               }
               if (isModelInteractionRequest && !motionStarted) {
                   finishModelInteractionWindow('interaction motion start failure');
               }
          };

        if (vmdCache.has(vmdFilePath)) {
            processAndPlayVmd(vmdCache.get(vmdFilePath));
            return;
        }

        try {
            loadAnimationFromBindingPose(
                vmdFilePath,
                 mesh,
                 function(vmd) {
                     if (
                         expectedGeneration !== modelLoadGeneration ||
                         expectedMotionLoadToken !== motionLoadToken ||
                         mesh !== currentMesh
                     ) return;
                     vmdCache.set(vmdFilePath, vmd);
                     processAndPlayVmd(vmd);
                 },
                 undefined,
                 function(error) {
                     if (
                         expectedGeneration !== modelLoadGeneration ||
                         expectedMotionLoadToken !== motionLoadToken ||
                         mesh !== currentMesh
                     ) return;
                     loadingMotionIndex = -1;
                     loadingMotionEntry = null;
                     console.error("VMD load failed:", error);
                     if (isModelInteractionRequest) {
                         finishModelInteractionWindow('interaction motion load failure');
                         return;
                     }
                     if (isLooping) {
                         currentMotionIndex = (requestedMotionIndex + 1) % motionFiles.length;
                         setTimeout(() => loadAndPlayMotion(currentMesh), 1000);
                     }
                 }
             );
         } catch (error) {
             if (expectedMotionLoadToken === motionLoadToken) {
                 loadingMotionIndex = -1;
                 loadingMotionEntry = null;
             }
             console.error("Load animation error:", error);
             if (isModelInteractionRequest) {
                 finishModelInteractionWindow('interaction motion load failure');
             }
         }
     }

    async function resetConversation() {
        confirmAction("Are you sure you want to clear the conversation history?", async () => {

            try {
                await fetch('/reset', { method: 'POST' });
                const chatContent = document.getElementById('chat-content');
                if (chatContent) chatContent.innerHTML = '';

                playIntelligentMotion({ context: { emotion: 'normal' } });
                showToast("Conversation reset.", { type: 'info', duration: 3600 });
            } catch (e) {
                console.error("Reset failed", e);
                showToast("Failed to reset conversation.", { type: 'error', duration: 3600 });
            }
    });}

    async function sendMessage(options = {}) {
        const input = document.getElementById('user-input');
        const directUserText = options && Object.prototype.hasOwnProperty.call(options, 'userText');
        let userText = directUserText ? String(options.userText || '').trim() : input.value.trim();
        let displayText = options?.displayText ? String(options.displayText).trim() : '';
        const outgoingAttachments = cloneAttachmentMetadata(options?.attachments);
        
        // Combine attachment if present
        if (pendingAttachment && !directUserText) {
            if (pendingAttachment.type === 'image') {
                const imgTag = `[IMAGE:${pendingAttachment.url}]`;
                outgoingAttachments.push({
                    type: 'image',
                    url: pendingAttachment.url || '',
                    data_url: pendingAttachment.data_url || '',
                    filename: pendingAttachment.filename || '',
                    description: pendingAttachment.description || ''
                });
                const attachmentModel = document.getElementById('model-select')?.value || '';
                const apiImageAttached = attachmentModel === 'api' && pendingAttachment.data_url;
                const sysNote = apiImageAttached
                    ? `(System Note: Doctor sent a picture. The image is attached for vision-capable API models. Content note: ${pendingAttachment.description})`
                    : `(System Note: Doctor sent a picture. Content: ${pendingAttachment.description})`;
                
                // If user text is empty, provide a default
                if (!userText) userText = "Sent a picture.";
                
                // Combine: Image + System Note + User Text
                userText = `${imgTag}\n${sysNote}\n\n${userText}`;
                
            } else if (pendingAttachment.type === 'file') {
                outgoingAttachments.push({
                    type: 'file',
                    filename: pendingAttachment.filename || ''
                });
                const safeContent = String(pendingAttachment.content || '')
                    .replace(/\[FILE_CONTEXT\]/gi, '[FILE CONTEXT]')
                    .replace(/\[\/FILE_CONTEXT\]/gi, '[/FILE CONTEXT]');
                if (!userText) userText = "Please review this file.";
                userText = `[FILE:${pendingAttachment.filename}]\n` +
                    `[FILE_CONTEXT]\n${safeContent}\n[/FILE_CONTEXT]\n\n${userText}`;
            }
            clearAttachment();
        }

        if (!userText) return;
        if (!displayText) displayText = userText;

        // Listener role: one conversation records exactly one memory; the
        // flow has its own endpoint (no /chat_stream, no persona prompt).
        if (currentRoleMode === 'listener') {
            appendUserMessageForListener(displayText || userText);
            await sendListenerTurn(userText);
            return;
        }

        const model = document.getElementById('model-select').value;
        const apiKey = document.getElementById('api-key-input').value;
        const apiUrlInputForSend = document.getElementById('api-url-input');
        const apiUrl = normalizeApiBaseUrlForChat(apiUrlInputForSend?.value || '');
        if (apiUrlInputForSend && apiUrl && apiUrl !== apiUrlInputForSend.value.trim()) {
            apiUrlInputForSend.value = apiUrl;
        }
        const apiModelName = document.getElementById('api-model-input').value.trim();
        const multimodalModelName = document.getElementById('api-multimodal-model-input')?.value.trim() || '';
        const strongLogicModelName = document.getElementById('api-strong-logic-model-input')?.value.trim() || '';
        const autonomousMemoryModelName = document.getElementById('api-autonomous-memory-model-input')?.value.trim() || '';
        const useAssistantStrongLogic = currentRoleMode === 'assistant' && isAssistantStrongLogicEnabled();
        const maxOutputTokens = getOutputLimitForModel(useAssistantStrongLogic ? 'api' : model);
        const memoryStrongModelInput = document.getElementById('api-memory-strong-model-input')?.value.trim() || '';
        const memoryStrongModelName = memoryStrongModelInput || apiModelName;
        const sayaStyleSnapshot = getSayaStyleSettings();
        // Combined Recall · Search mode from #combined-mode.
        const { recallMode, searchMode: rawSearchMode } = parseCombinedMode();
        const recallPreset = getRecallPresetConfig();
        // UI "on" maps to internal "force" for the backend.
        const webSearchMode = rawSearchMode === 'on' ? 'force' : rawSearchMode;
        const webSearchApiKey = getSavedApiSettings().brave_search_api_key || '';
        const webSearchPreset = getWebSearchPresetConfig();
        const inputLimits = getInputLimits();
        saveCurrentApiSettings();

        lastInteractionTime = Date.now();
        isWaitingForResponse = true;
        
        // addLog(`User: ${userText}`, 'user'); // Removed
        
        // Use appendMessage for consistent UI
        appendMessage('user', displayText, {
            historyIndex: currentHistoryLength,
            attachments: outgoingAttachments
        });
        input.value = '';
        input.style.height = '48px'; // Reset height
        input.style.overflowY = 'hidden'; // Reset overflow
        updateInputSuggestions();

        let aiDiv = null;
        let fullResponseText = "";
        try {
            const requestContext = {};
            if (pendingCheckinContext) {
                requestContext.checkin_context = pendingCheckinContext;
                pendingCheckinContext = "";
            }
            if (currentRoleMode === 'assistant') {
                requestContext.role_mode = 'assistant';
            } else if (currentProjectId) {
                requestContext.project_id = currentProjectId;
            }
            const response = await fetch('/chat_stream', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ 
                    user_input: userText,
                    display_input: displayText,
                    session_id: currentSessionId || '',
                    context: requestContext,
                    model: model,
                    api_key: apiKey,
                    api_url: apiUrl,
                    api_model_name: apiModelName,
                    multimodal_model_name: multimodalModelName,
                    max_output_tokens: maxOutputTokens,
                    strong_logic_model_name: strongLogicModelName,
                    use_strong_logic_model: useAssistantStrongLogic,
                    memory_strong_model_name: memoryStrongModelName,
                    autonomous_memory_model_name: autonomousMemoryModelName,
                    attachments: outgoingAttachments,
                    // Agent mode owns memory/search selection. Send neutral
                    // compatibility values so stale localStorage presets do
                    // not compete with the Agent policy on the server.
                    mode: agentModeEnabled ? 'normal' : (recallMode === 'on' ? 'deep' : 'normal'),
                    recall_mode: agentModeEnabled ? 'auto' : recallMode,
                    recall_memory_count: agentModeEnabled ? 0 : recallPreset,
                    graph_recall_mode: getModeSettings().graph_recall_mode || 'auto',
                    history_turns: inputLimits.history_turns,
                    style_settings: sayaStyleSnapshot,
                    web_search_mode: agentModeEnabled ? 'auto' : webSearchMode,
                    temporary_chat: temporaryChatEnabled,
                    agent_mode: agentModeEnabled,
                    web_search_api_key: webSearchApiKey,
                    web_search_count: agentModeEnabled ? 0 : webSearchPreset.count,
                    web_final_sources: agentModeEnabled ? 0 : webSearchPreset.sources,
                    web_source_strategy: getModeSettings().web_source_strategy || 'official_first',
                    web_safe_search: getModeSettings().safe_search || 'moderate'
                })
            });

            if (!response.ok) throw new Error("Network response was not ok");

            aiDiv = appendMessage('ai', '...', {
                historyIndex: currentHistoryLength + 1,
                readableGenerated: sayaStyleSnapshot.readable === true
            });
            const textSpan = aiDiv.firstElementChild.firstElementChild; // The content div inside bubble
            textSpan.innerHTML = `Saya: `;

            if (!response.body) throw new Error('Streaming response body is unavailable.');
            const reader = response.body.getReader();
            const decoder = new TextDecoder("utf-8");
            let buffer = "";
            let streamNotice = null;
            let chatMotionHandled = false;
            let agentFallbackText = "";

            const handleStreamEvent = (eventType, eventData) => {
                if (!eventType || !eventData) return;
                if (eventType === 'motion_start' || eventType === 'motion_end') {
                    if (chatMotionHandled) return;
                    if (eventData.motion_name || eventData.motion_url || eventData.motion_file || eventData.motion_id) {
                        const motionIndex = findMotionByPayload(eventData);
                        if (motionIndex !== -1) {
                            chatMotionHandled = true;
                            if (eventData.category) {
                                currentMotionState = String(eventData.category);
                            }
                            const motionEntry = motionEntries[motionIndex] || {};
                            const motionKind = String(
                                eventData.motion_kind || eventData.kind || motionEntry.kind || ''
                            ).toLowerCase();
                            const requestedPlayback = 'once_then_idle';
                            const isPriorityReaction = (
                                motionKind === 'reaction' ||
                                Boolean(eventData.reaction_intent) ||
                                requestedPlayback === 'once' ||
                                requestedPlayback === 'once_then_idle'
                            );
                            currentMotionIndex = motionIndex;
                            loadAndPlayMotion(currentMesh, {
                                forceRestart: requestedPlayback === 'once' || requestedPlayback === 'once_then_idle',
                                interruptOneShot: isPriorityReaction,
                                playbackOverride: requestedPlayback,
                                returnToClassicIdle: true,
                                resetPosition: false
                            });
                        }
                    }
                } else if (eventType === 'text') {
                    fullResponseText += String(eventData.text || '');
                    textSpan.innerHTML = `Saya: ` + renderMarkdownSafe(fullResponseText);
                    const chatContent = document.getElementById('chat-content');
                    chatContent.scrollTop = chatContent.scrollHeight;
                } else if (eventType === 'stream_status' && eventData.incomplete) {
                    streamNotice = {message: eventData.message || 'The response reached its output limit.', isError: false};
                } else if (eventType === 'stream_error') {
                    streamNotice = {message: eventData.message || 'The response stream was interrupted.', isError: true};
                } else if (eventType === 'assistant_status') {
                    renderAgentStatus(aiDiv, eventData.phase, eventData.message);
                } else if (eventType === 'agent_event') {
                    renderAgentEvent(aiDiv, eventData);
                } else if (eventType === 'agent_result') {
                    // The canonical visible reply arrives through the normal
                    // `text` event. Keep the Agent answer only as an end-of-
                    // stream fallback; rendering it here caused the later
                    // text event to append the same answer a second time.
                    if (eventData.answer) agentFallbackText = String(eventData.answer);
                    if (eventData.status === 'needs_approval') {
                        streamNotice = {message: 'This action needs your confirmation before it can continue.', isError: false};
                    }
                } else if (eventType === 'web_status') {
                    // Live phase indicator (searching / reading / preparing).
                    renderWebStatus(aiDiv, eventData.phase, eventData.message);
                } else if (eventType === 'web_warning') {
                    renderWebWarning(aiDiv, eventData.code, eventData.message);
                } else if (eventType === 'web_sources') {
                    renderWebSourcesPanel(aiDiv, eventData.query, eventData.sources || []);
                } else if (eventType === 'web_citation_check') {
                    renderWebCitationCheck(aiDiv, eventData);
                } else if (eventType === 'chat_runtime') {
                    if (eventData.session_id) currentSessionId = eventData.session_id;
                } else if (eventType === 'memory_recall') {
                    renderMemoryRecallAudit(aiDiv, eventData);
                } else if (eventType === 'memory_usage') {
                    renderAgentStatus(aiDiv, 'memory_used', `${Number((eventData.receipts || []).length)} 条记忆已记录使用反馈`);
                } else if (eventType === 'memory_candidates') {
                    renderAutonomousMemoryCandidates(eventData.candidates || []);
                } else if (eventType === 'memory_maintenance') {
                    if (!renderedMemoryMaintenanceEventIds.has(String(eventData.id || ''))) {
                        showStreamNotice(aiDiv, eventData.message || 'Memory capacity maintenance completed.', eventData.level === 'critical');
                        showMemoryMaintenanceNotice(eventData);
                    }
                }
            };

            const handleSseBlock = (part) => {
                if (!part.trim()) return;
                let eventType = 'message';
                const dataLines = [];
                part.split(/\r?\n/).forEach(line => {
                    if (line.startsWith('event:')) {
                        eventType = line.substring(6).trim();
                    } else if (line.startsWith('data:')) {
                        dataLines.push(line.substring(5).replace(/^ /, ''));
                    }
                });
                if (!dataLines.length) return;
                try {
                    handleStreamEvent(eventType, JSON.parse(dataLines.join('\n')));
                } catch (parseError) {
                    console.error('SSE JSON parse error:', parseError, dataLines.join('\n'));
                }
            };

            const drainSseBuffer = () => {
                const parts = buffer.split(/\r?\n\r?\n/);
                buffer = parts.pop() || '';
                parts.forEach(handleSseBlock);
            };

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                
                buffer += decoder.decode(value, { stream: true });
                drainSseBuffer();
            }

            // Flush both an incomplete UTF-8 code point and an SSE event that did
            // not happen to end on the same network chunk as its blank separator.
            buffer += decoder.decode();
            const finalParts = buffer.split(/\r?\n\r?\n/);
            buffer = '';
            finalParts.forEach(handleSseBlock);

            if (!fullResponseText && agentFallbackText) {
                fullResponseText = agentFallbackText
                    .replace(/\[MOTION[^\]\r\n]{0,160}\]/gi, '')
                    .replace(/\[MC[^\]\r\n]{0,220}\]/gi, '')
                    .trim();
                textSpan.innerHTML = `Saya: ` + renderMarkdownSafe(fullResponseText);
            }
            if (!fullResponseText && !streamNotice) {
                streamNotice = {
                    message: 'The response stream ended without reply text. Please retry this message.',
                    isError: true
                };
            }

            if (streamNotice) showStreamNotice(aiDiv, streamNotice.message, streamNotice.isError);
            
            fetchSessionList(); // Update session list preview
            
            // Sync length: Assuming one User msg and one AI msg added
            // BUT, if stream failed, it might be different.
            // Safe way: fetch status from backend?
            // Or just increment by 2
            if (!temporaryChatEnabled) currentHistoryLength += 2;
            updateModeProgressRing();

            finalizeResponse(aiDiv, fullResponseText);
            isWaitingForResponse = false;
            lastInteractionTime = Date.now();

        } catch (error) {
            isWaitingForResponse = false;
            lastInteractionTime = Date.now();
            console.error('Error:', error);
            if (aiDiv) {
                finalizeResponse(aiDiv, fullResponseText);
                showStreamNotice(aiDiv, `Response stream interrupted: ${error.message || error}`, true);
            } else {
                appendMessage('ai', 'Sorry, I encountered an error.');
            }
        }
    }

    async function finalizeResponse(aiDiv, fullText) {
        if (aiDiv) aiDiv.dataset.rawContent = fullText;
    }

    function showStreamNotice(rowDiv, message, isError = false) {
        const bubble = rowDiv?.querySelector('.message-bubble.ai');
        if (!bubble) return;
        let notice = bubble.querySelector('.stream-notice');
        if (!notice) {
            notice = document.createElement('div');
            const actions = bubble.querySelector('.msg-actions');
            bubble.insertBefore(notice, actions || null);
        }
        notice.className = `stream-notice${isError ? ' error' : ''}`;
        notice.textContent = message;
    }

    function renderAgentStatus(aiDiv, phase, message) {
        if (!aiDiv) return;
        const bubble = aiDiv.querySelector('.message-bubble.ai');
        if (!bubble) return;
        let line = bubble.querySelector('.agent-status-line');
        if (!line) {
            line = document.createElement('div');
            line.className = 'agent-status-line';
            const actions = bubble.querySelector('.msg-actions');
            bubble.insertBefore(line, actions || null);
        }
        line.dataset.phase = String(phase || '');
        line.textContent = message || phase || '';
        line.hidden = phase === 'completed' || phase === 'memory_used';
        if (phase === 'completed') setTimeout(() => { if (line) line.textContent = ''; }, 500);
    }

    function renderAgentEvent(aiDiv, event) {
        if (!aiDiv || !event || typeof event !== 'object') return;
        const bubble = aiDiv.querySelector('.message-bubble.ai');
        if (!bubble) return;
        const eventId = String(event.id || '');
        if (eventId && bubble.querySelector(`[data-agent-event-id="${cssEscapeValue(eventId)}"]`)) return;
        const payload = event.public_payload && typeof event.public_payload === 'object' ? event.public_payload : {};
        if (event.type === 'assistant.message.delta') return;
        const card = document.createElement('div');
        card.className = 'agent-event-card';
        if (eventId) card.dataset.agentEventId = eventId;
        const label = event.type === 'tool.call.started' ? 'Tool running' : event.type === 'tool.call.completed' ? 'Tool complete' : event.type === 'approval.required' ? 'Confirmation needed' : event.type;
        card.innerHTML = `<strong>${escapeHtml(label)}</strong><span>${escapeHtml(payload.name || payload.message || payload.summary || payload.reason || '')}</span>`;
        const actions = bubble.querySelector('.msg-actions');
        bubble.insertBefore(card, actions || null);
    }

    function renderMemoryRecallAudit(aiDiv, audit) {
        if (!aiDiv || !audit || typeof audit !== 'object') return;
        const bubble = aiDiv.querySelector('.message-bubble.ai');
        if (!bubble) return;
        bubble.querySelector('.memory-recall-audit')?.remove();

        const selected = Array.isArray(audit.selected) ? audit.selected : [];
        const conflictCount = Number(audit.conflict_count || 0);
        const statusLabels = {
            selected: 'Recall complete', daily_only: 'Daily snapshot only',
            no_match: 'No matching memory', budget_limited: 'Limited by context budget',
            disabled: 'Recall disabled', agent_selected: 'Agent-selected memory', assistant_scope: 'Assistant memory isolated',
            no_daily_snapshot: 'No daily snapshot'
        };
        const reasonLabels = {
            core_memory: 'Core memory', protected_priority: 'Locked / starred',
            embedding_match: 'Vector match', semantic_match: 'Semantic match',
            text_relevance: 'Text match', graph_related: 'Relationship expansion'
        };
        const details = document.createElement('details');
        details.className = `memory-recall-audit${conflictCount ? ' has-conflict' : ''}`;
        const summary = document.createElement('summary');
        const statusText = statusLabels[audit.status] || String(audit.status || 'Recall audit');
        summary.textContent = `${statusText} · ${selected.length} selected${conflictCount ? ` · ${conflictCount} conflict flag(s)` : ''}`;
        details.appendChild(summary);

        const body = document.createElement('div');
        body.className = 'memory-recall-body';
        const meta = document.createElement('div');
        meta.className = 'memory-recall-meta';
        const candidates = audit.candidate_summary || {};
        const graph = audit.graph || {};
        const graphText = graph.mode
            ? ` · Graph: ${graph.mode}/${graph.status || 'idle'} (+${Number(graph.included_count || 0)})`
            : '';
        meta.textContent = `Mode: ${audit.mode || 'normal'} · Active pool: ${Number(candidates.active || 0)} · Project filtered: ${Number(candidates.project_filtered || 0)}${graphText}`;
        body.appendChild(meta);

        const visibleMemoryCount = 3;
        const memoryCards = [];
        selected.forEach((item, index) => {
            const card = document.createElement('div');
            card.className = 'memory-recall-item';
            if (index >= visibleMemoryCount) {
                card.hidden = true;
                card.classList.add('memory-recall-item-hidden');
            }
            if (item.memory_id) card.dataset.memoryId = item.memory_id;
            const heading = document.createElement('div');
            heading.className = 'memory-recall-item-heading';
            const title = document.createElement('strong');
            title.textContent = item.title || 'Untitled';
            const badges = document.createElement('span');
            const score = Number(item.scores?.relevance || 0);
            badges.textContent = `${item.layer || 'semantic'} · ${reasonLabels[item.reason] || item.reason || 'relevant'} · score ${score.toFixed(2)}`;
            heading.appendChild(title);
            heading.appendChild(badges);
            card.appendChild(heading);

            const contentPreview = String(
                item.content_preview || item.content || item.prompt_excerpt || ''
            ).trim();
            if (contentPreview) {
                const content = document.createElement('div');
                content.className = 'memory-recall-content';
                content.textContent = contentPreview;
                card.appendChild(content);
            }

            if (item.graph) {
                const graphLine = document.createElement('div');
                graphLine.className = 'memory-recall-graph-path';
                const relations = (item.graph.relations || []).filter(Boolean).join(' → ');
                const via = (item.graph.via || []).filter(Boolean).join(', ');
                graphLine.textContent = `Linked${relations ? ` by ${relations}` : ''}${via ? ` via ${via}` : ''}`;
                card.appendChild(graphLine);
            }

            const conflict = item.conflict || {};
            if (conflict.status && conflict.status !== 'clear') {
                const warning = document.createElement('div');
                warning.className = 'memory-recall-conflict';
                const related = (conflict.relations || []).map(rel => rel.title).filter(Boolean).slice(0, 3);
                warning.textContent = `Conflict check: ${conflict.status}${related.length ? ` · ${related.join(', ')}` : ''}`;
                card.appendChild(warning);
            }
            body.appendChild(card);
            memoryCards.push(card);
        });

        if (selected.length > visibleMemoryCount) {
            const moreButton = document.createElement('button');
            moreButton.type = 'button';
            moreButton.className = 'memory-recall-more';
            moreButton.textContent = `More (${selected.length - visibleMemoryCount})`;
            moreButton.setAttribute('aria-expanded', 'false');
            moreButton.addEventListener('click', () => {
                const expanded = moreButton.getAttribute('aria-expanded') === 'true';
                memoryCards.slice(visibleMemoryCount).forEach((card) => {
                    card.hidden = expanded;
                    card.classList.toggle('memory-recall-item-hidden', expanded);
                });
                moreButton.setAttribute('aria-expanded', String(!expanded));
                moreButton.textContent = expanded ? 'More' : 'Less';
            });
            body.appendChild(moreButton);
        }
        details.appendChild(body);
        const actions = bubble.querySelector('.msg-actions');
        bubble.insertBefore(details, actions || null);
    }

    // --- Web research rendering (plan section 19.3-19.5) ---
    // Sources remain public metadata only. Citation checks may include one
    // bounded supporting excerpt, never a full page body.
    // Reuses the existing global escapeHtml() helper.

    function renderWebStatus(aiDiv, phase, message) {
        if (!aiDiv) return;
        const bubble = aiDiv.querySelector('.message-bubble.ai');
        if (!bubble) return;
        let line = bubble.querySelector('.web-status-line');
        if (!line) {
            line = document.createElement('div');
            line.className = 'web-status-line';
            const actions = bubble.querySelector('.msg-actions');
            bubble.insertBefore(line, actions || null);
        }
        line.textContent = message || phase || '';
        // Clear the live status once preparation is done (sources take over).
        if (phase === 'preparing') {
            setTimeout(() => { if (line) line.textContent = ''; }, 800);
        }
    }

    function renderWebWarning(aiDiv, code, message) {
        if (!aiDiv) return;
        const bubble = aiDiv.querySelector('.message-bubble.ai');
        if (!bubble) return;
        const line = document.createElement('div');
        line.className = 'web-warning-line';
        line.textContent = message || code || '';
        const actions = bubble.querySelector('.msg-actions');
        bubble.insertBefore(line, actions || null);
    }

    function buildSourcesPanelNode(query, sources) {
        const panel = document.createElement('div');
        panel.className = 'web-sources-panel';
        const toggle = document.createElement('button');
        toggle.type = 'button';
        toggle.className = 'web-sources-toggle';
        toggle.innerHTML = `<span>参考来源 (${sources.length})</span>${ic('chevron-down', 'icn-sm')}`;
        toggle.setAttribute('aria-expanded', 'false');
        const list = document.createElement('div');
        list.className = 'web-sources-list';
        sources.forEach((src) => {
            const id = Number(src.id) || 0;
            const title = escapeHtml(src.title || src.domain || src.url || '');
            const url = escapeHtml(src.url || '');
            const domain = escapeHtml(src.domain || '');
            const status = src.status || '';
            const statusTag = status === 'snippet_only' ? ' (摘要)' : (status === 'failed' ? ' (失败)' : '');
            const sourceTypeLabels = {
                government: '政府一手来源', standards: '标准组织', official: '官方',
                documentation: '官方文档候选', research: '原始研究', repository: '源码仓库',
                reputable_media: '可靠媒体', community: '社区观点', aggregator: '聚合内容',
                user_provided: '用户提供', web: '网页'
            };
            const typeTag = sourceTypeLabels[src.source_type] || '';
            const item = document.createElement('div');
            item.className = 'src-item';
            item.innerHTML = `[${id}] <a href="${url}" target="_blank" rel="noopener noreferrer">${title || domain || url}</a>` +
                (domain ? ` <span style="color:#666">— ${domain}${statusTag}${typeTag ? ' · ' + escapeHtml(typeTag) : ''}</span>` : '');
            list.appendChild(item);
        });
        toggle.addEventListener('click', () => {
            const open = list.classList.toggle('open');
            toggle.classList.toggle('open', open);
            toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
        });
        panel.appendChild(toggle);
        panel.appendChild(list);
        return panel;
    }

    function renderWebSourcesPanel(aiDiv, query, sources) {
        if (!aiDiv || !Array.isArray(sources) || !sources.length) return;
        const bubble = aiDiv.querySelector('.message-bubble.ai');
        if (!bubble) return;
        // Avoid duplicate panels on the same bubble.
        const existing = bubble.querySelector('.web-sources-panel');
        if (existing) existing.remove();
        bubble.appendChild(buildSourcesPanelNode(query || '', sources));
    }

    function renderWebCitationCheck(aiDiv, report) {
        if (!aiDiv || !report || typeof report !== 'object') return;
        const bubble = aiDiv.querySelector('.message-bubble.ai');
        if (!bubble) return;
        bubble.querySelector('.web-citation-check')?.remove();

        const summary = report.summary || {};
        const status = String(report.status || 'needs_review');
        const details = document.createElement('details');
        details.className = `web-citation-check ${status}`;
        details.open = status === 'needs_review' || status === 'no_citations';
        const label = document.createElement('summary');
        const labelByStatus = {
            pass: '引用证据校验通过',
            partial: '引用证据部分通过',
            needs_review: '引用证据需要复核',
            no_citations: '回答中没有编号引用'
        };
        label.textContent = `${labelByStatus[status] || '引用证据校验'} · ${Number(summary.supported || 0)} 条匹配，${Number(summary.partial || 0) + Number(summary.snippet_only || 0)} 条受限，${Number(summary.unsupported || 0) + Number(summary.invalid_references || 0)} 条有问题`;
        details.appendChild(label);

        const body = document.createElement('div');
        body.className = 'web-citation-check-body';
        const claims = Array.isArray(report.claims) ? report.claims : [];
        if (!claims.length) {
            const empty = document.createElement('div');
            empty.className = 'web-citation-empty';
            empty.textContent = '回答使用了网页资料，但没有把编号引用附在对应论断后。';
            body.appendChild(empty);
        }
        claims.forEach(item => {
            const claim = document.createElement('div');
            claim.className = 'web-citation-claim';
            const claimText = document.createElement('div');
            claimText.className = 'web-citation-claim-text';
            claimText.textContent = item.claim || '（引用附近没有可识别的论断）';
            claim.appendChild(claimText);
            (Array.isArray(item.citations) ? item.citations : []).forEach(citation => {
                const evidence = document.createElement('div');
                evidence.className = `web-citation-evidence ${citation.support || 'unavailable'}`;
                const heading = document.createElement('div');
                heading.className = 'web-citation-evidence-heading';
                const supportLabels = {
                    supported: '原文支持',
                    partial: '部分匹配',
                    snippet_only: '仅搜索摘要',
                    unsupported: '未找到支持',
                    invalid_reference: '引用编号不存在',
                    unavailable: '无可用正文'
                };
                heading.textContent = `[${Number(citation.source_id) || '?'}] ${citation.title || ''} · ${supportLabels[citation.support] || '无法判断'}`;
                evidence.appendChild(heading);
                if (citation.passage) {
                    const passage = document.createElement('blockquote');
                    passage.textContent = citation.passage;
                    evidence.appendChild(passage);
                }
                claim.appendChild(evidence);
            });
            body.appendChild(claim);
        });
        if (Number(summary.uncited_specific_claims || 0) > 0) {
            const notice = document.createElement('div');
            notice.className = 'web-citation-empty';
            notice.textContent = `另有 ${Number(summary.uncited_specific_claims)} 条包含数字、版本或归因的具体陈述可能缺少引用。`;
            body.appendChild(notice);
        }
        details.appendChild(body);
        const actions = bubble.querySelector('.msg-actions');
        bubble.insertBefore(details, actions || null);
    }


    // [REMOVED] Old Translation Functions: renderTranslationItem, renderTranslations, performTranslation
    
    // Speech pipeline ported from airi-main's speech-pipeline + tts-chunker
    // design: long text is split on sentence boundaries so the first chunk
    // starts speaking quickly, the next chunk is fetched while the current
    // one plays, and a new speak request interrupts what is playing
    // (intent: interrupt) instead of stacking audio on top.
    const SayaSpeech = {
        token: 0,
        currentAudio: null,
        MAX_CHUNK_CHARS: 220,

        chunkText(text) {
            const clean = String(text || '').trim();
            if (!clean) return [];
            const parts = clean.match(/[^.!?\n。！？]+[.!?。！？]*\s*/g) || [clean];
            const chunks = [];
            let buffer = '';
            for (const part of parts) {
                if ((buffer + part).length > this.MAX_CHUNK_CHARS && buffer) {
                    chunks.push(buffer.trim());
                    buffer = part;
                } else {
                    buffer += part;
                }
            }
            if (buffer.trim()) chunks.push(buffer.trim());
            return chunks.filter(Boolean).slice(0, 40);
        },

        stop() {
            this.token += 1;
            if (this.currentAudio) {
                this.currentAudio.pause();
                this.currentAudio = null;
            }
        },

        async fetchChunkAudio(text) {
            const ttsSettings = getSavedOtherApiSettings();
            const res = await fetch("/tts", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    text,
                    api_url: ttsSettings.tts_api_url,
                    api_key: ttsSettings.tts_api_key,
                    voice_id: ttsSettings.tts_voice_id,
                    model_id: ttsSettings.tts_model_id
                })
            });
            if (!res.ok) {
                let detail = `TTS failed (${res.status})`;
                try { const data = await res.json(); detail = data.detail || detail; } catch (_) {}
                throw new Error(detail);
            }
            if (!res.headers.get("Content-Type")?.includes("audio")) throw new Error('TTS API did not return audio.');
            const blob = new Blob([await res.arrayBuffer()], { type: "audio/mpeg" });
            return URL.createObjectURL(blob);
        },

        playUrl(url) {
            return new Promise(resolve => {
                const audio = new Audio(url);
                this.currentAudio = audio;
                audio.onended = () => { if (this.currentAudio === audio) this.currentAudio = null; resolve(); };
                audio.onerror = () => { if (this.currentAudio === audio) this.currentAudio = null; resolve(); };
                audio.play().catch(() => resolve());
            });
        },

        async speak(text) {
            const chunks = this.chunkText(text);
            if (!chunks.length) return;
            this.stop();               // intent: interrupt
            const myToken = this.token;
            let nextUrl = null;
            for (let index = 0; index < chunks.length; index++) {
                if (myToken !== this.token) return; // superseded by a newer speak/stop
                try {
                    const url = nextUrl || await this.fetchChunkAudio(chunks[index]);
                    if (myToken !== this.token) return;
                    // Prefetch the following chunk while this one plays.
                    const prefetch = (index + 1 < chunks.length)
                        ? this.fetchChunkAudio(chunks[index + 1]).catch(() => null)
                        : null;
                    await this.playUrl(url);
                    URL.revokeObjectURL(url);
                    nextUrl = (await prefetch) || null;
                } catch (e) {
                    console.warn("TTS chunk failed:", e);
                    nextUrl = null;
                }
            }
        }
    };

    async function speakWithElevenLabs(text) {
        try {
            await SayaSpeech.speak(text);
        } catch (e) {
            showToast(e.message || 'TTS playback failed.', { type: 'error', duration: 3600 });
        }
    }

    const inputField = document.getElementById('user-input');

    function autoResizeInput() {
        inputField.style.height = 'auto';
        inputField.style.height = (inputField.scrollHeight) + 'px';
        if (inputField.scrollHeight > 150) {
            inputField.style.overflowY = 'auto';
        } else {
            inputField.style.overflowY = 'hidden';
        }
    }
    
    inputField.addEventListener('input', autoResizeInput);

    inputField.addEventListener('keydown', function (e) {
        // Prevent event from bubbling to global listeners (like OrbitControls/Model Movement)
        e.stopPropagation();

        if (e.key === 'Enter') {
            if (!e.shiftKey) {
                e.preventDefault();
                sendMessage();
                return;
            }
            // Allow Shift+Enter for newline, then resize
            setTimeout(autoResizeInput, 0);
        }

        // Custom cursor navigation for < and > keys
        if (e.key === '<') {
            e.preventDefault();
            const start = this.selectionStart;
            if (start > 0) {
                this.setSelectionRange(start - 1, start - 1);
            }
        } else if (e.key === '>') {
            e.preventDefault();
            const start = this.selectionStart;
            if (start < this.value.length) {
                this.setSelectionRange(start + 1, start + 1);
            }
        }
    });

    // Start periodic status updates
    setInterval(fetchStatus, 5000);
    fetchStatus(); // Initial fetch

    // ========================================================================
    // ========================================================================
    // Listener role (memory intake): one conversation = one memory.
    // Flow: user msg -> Saya asks ONE batched clarification OR proposes ->
    // editable proposal card (content + layer core/semantic/episodic) ->
    // confirm stores the memory and LOCKS the conversation.  Editing an
    // earlier message truncates the session server-side, which auto-unlocks
    // and makes the next confirm REPLACE the same linked memory.
    // ========================================================================
    const LISTENER_LAYERS = [
        {value: 'core', label: 'core · 核心身份（永久）'},
        {value: 'semantic', label: 'semantic · 稳定知识/偏好'},
        {value: 'episodic', label: 'episodic · 具体事件经历'},
    ];

    async function startListenerSession() {
        try {
            closeToolsModal();
            const response = await fetch('/api/listener/open', {method: 'POST'});
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const data = await response.json();
            setCurrentRoleMode('listener');
            currentSessionId = data.session_id;
            currentHistoryLength = 0;
            document.getElementById('chat-content').innerHTML = '';
            const input = document.getElementById('user-input');
            if (input) { input.value = ''; input.disabled = false; }
            appendListenerNotice('🎙 Listener 模式：这条对话用来记录一条记忆。告诉我内容，我最多追问一次细节，然后给你一份可修改的记忆提案。');
        } catch (e) {
            showToast(`无法开启 Listener 对话：${e.message || e}`, { type: 'info', duration: 3600 });
        }
    }
    window.startListenerSession = startListenerSession;

    function setListenerInputLocked(locked) {
        const input = document.getElementById('user-input');
        const sendBtn = document.getElementById('send-btn');
        if (input) input.disabled = locked;
        if (sendBtn) sendBtn.disabled = locked;
    }

    function appendListenerNotice(text) {
        const chat = document.getElementById('chat-content');
        const div = document.createElement('div');
        div.className = 'message-row';
        div.innerHTML = `<div class="message-bubble listener-card" style="border-style:dashed;">
            <div class="lc-hint" style="font-size:13px;color:#9db8ff;">${text}</div></div>`;
        chat.appendChild(div);
        chat.scrollTop = chat.scrollHeight;
    }

    function appendUserMessageForListener(text) {
        appendMessage('user', text, {});
    }

    async function sendListenerTurn(text) {
        setListenerInputLocked(true);
        try {
            const response = await fetch('/api/listener/send', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({session_id: currentSessionId, text})
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
            if (payload.type === 'locked') {
                appendListenerNotice(`🔒 ${payload.message}`);
                return;
            }
            if (payload.type === 'question') {
                appendMessage('ai', `🎙 ${payload.text}`, {});
                setListenerInputLocked(false);
                return;
            }
            if (payload.type === 'proposal') {
                renderListenerProposalCard(payload.proposal, false);
                // no input unlock: the card owns the next action
                return;
            }
            throw new Error('未知响应');
        } catch (e) {
            appendListenerNotice(`⚠️ ${e.message || e}（可以重发或修改你的话）`);
            setListenerInputLocked(false);
        }
    }

    function renderListenerProposalCard(proposal, locked) {
        const chat = document.getElementById('chat-content');
        const card = document.createElement('div');
        card.className = 'message-row';
        const layerOptions = LISTENER_LAYERS.map(l =>
            `<option value="${l.value}" ${l.value === proposal.layer ? 'selected' : ''}>${l.label}</option>`).join('');
        card.innerHTML = `
        <div class="message-bubble listener-card ${locked ? 'lc-locked' : ''}">
            <div class="lc-title">${ic('note', 'icn-sm')} 记忆提案（可修改后保存）</div>
            <input class="lc-title-input" value="${(proposal.title || '').replace(/"/g, '&quot;')}" />
            <textarea class="lc-content">${proposal.content || ''}</textarea>
            <div class="listener-row">
                <span class="lc-row-label">记忆分级：</span>
                <select class="lc-layer">${layerOptions}</select>
                <span class="lc-row-label">类别：</span>
                <input class="lc-category" value="${(proposal.category || 'general')}" style="width:120px;" />
                ${locked
                    ? `<button class="lc-update">${ic('save', 'icn-sm')}更新这条记忆</button><span class="lc-badge">✔ 已保存</span>`
                    : `<button class="lc-confirm">${ic('lock', 'icn-sm')}确认保存并锁定对话</button>`}
            </div>
            <div class="lc-hint">${locked
                ? '对话已锁定（保存新记忆请开启新的 Listener 对话）。这里仍可直接修改已保存的记忆内容或分级。'
                : '保存后这条对话会锁定；如需重来，可以编辑/返回上面对话，重新确认时会替换同一条记忆而不是新增。'}</div>
        </div>`;
        chat.appendChild(card);
        chat.scrollTop = chat.scrollHeight;
        const btn = card.querySelector(locked ? '.lc-update' : '.lc-confirm');
        btn.onclick = async () => {
            btn.disabled = true;
            btn.innerHTML = `${ic('refresh', 'icn-sm')}保存中…`;
            try {
                const body = {
                    session_id: currentSessionId,
                    title: card.querySelector('.lc-title-input').value.trim(),
                    content: card.querySelector('.lc-content').value.trim(),
                    layer: card.querySelector('.lc-layer').value,
                    category: card.querySelector('.lc-category').value.trim() || 'general',
                };
                const endpoint = locked ? '/api/listener/update' : '/api/listener/confirm';
                const response = await fetch(endpoint, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(body)
                });
                const payload = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
                if (!locked) {
                    appendListenerNotice(`🔒 记忆已保存（${payload.proposal?.layer || ''}）。此对话已锁定，开启新的 Listener 对话记录下一条。`);
                    setListenerInputLocked(true);
                } else {
                    appendListenerNotice('✔ 已更新这条记忆。');
                }
                btn.innerHTML = locked ? `${ic('save', 'icn-sm')}更新这条记忆` : `${ic('check', 'icn-sm')}已保存`;
            } catch (e) {
                btn.disabled = false;
                btn.innerHTML = locked ? `${ic('save', 'icn-sm')}更新这条记忆` : `${ic('lock', 'icn-sm')}确认保存并锁定对话`;
                showToast(`保存失败：${e.message || e}`, { type: 'info', duration: 3600 });
            }
        };
    }

    async function enhanceListenerSessionOnLoad() {
        // After loading a listener session, rebuild the interactive tail
        // (pending proposal or saved card) from the derived server state.
        if (currentRoleMode !== 'listener' || !currentSessionId) return;
        try {
            const response = await fetch(`/api/listener/state?session_id=${encodeURIComponent(currentSessionId)}`);
            if (!response.ok) return;
            const state = await response.json();
            setListenerInputLocked(!!state.locked);
            if (state.proposal) {
                renderListenerProposalCard(state.proposal, !!state.locked);
            } else if (!state.locked && !state.question_used) {
                appendListenerNotice('🎙 继续这条 Listener 对话：告诉我这条记忆的内容。');
            }
        } catch (e) { /* best-effort enhancement */ }
    }

    // ========================================================================
    // Connect-more-tools row: visible only while the input is EMPTY and not
    // focused (idle state). Typing or focusing the composer hides the chips.
    // ========================================================================
    function updateConnectToolsVisibility() {
        const row = document.getElementById('connect-tools-row');
        const input = document.getElementById('user-input');
        if (!row || !input) return;
        const idle = input.value.trim() === '' && document.activeElement !== input;
        row.classList.toggle('ctr-hidden', !idle);
        row.setAttribute('aria-hidden', String(!idle));
    }
    window.updateConnectToolsVisibility = updateConnectToolsVisibility;

    (function initConnectTools() {
        const input = document.getElementById('user-input');
        if (!input) return;
        input.addEventListener('input', updateConnectToolsVisibility);
        input.addEventListener('focus', updateConnectToolsVisibility);
        input.addEventListener('blur', () => setTimeout(updateConnectToolsVisibility, 120));
        updateConnectToolsVisibility();
    })();

    // Weather panel (feature ported from airi-main's get_weather widget; the
    // backend proxies Open-Meteo so no API key is needed).
    // ========================================================================
    function openWeatherModal() {
        const modal = document.getElementById('weather-modal');
        if (modal) modal.style.display = 'flex';
        const input = document.getElementById('weather-location-input');
        if (input && !input.value) input.value = localStorage.getItem('saya_weather_city') || '';
        if (input && input.value) fetchWeather();
    }

    function closeWeatherModal() {
        const modal = document.getElementById('weather-modal');
        if (modal) modal.style.display = 'none';
    }

    function backToToolsFromWeather() {
        closeWeatherModal();
        openToolsModal();
    }

    async function fetchWeather() {
        const input = document.getElementById('weather-location-input');
        const resultBox = document.getElementById('weather-result');
        const location = (input?.value || '').trim();
        if (!location || !resultBox) return;
        localStorage.setItem('saya_weather_city', location);
        resultBox.innerHTML = '<div class="weather-loading">Checking...</div>';
        try {
            const response = await fetch('/api/weather', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({location})
            });
            const payload = await response.json();
            if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
            const current = payload.current || {};
            const days = payload.daily || [];
            resultBox.innerHTML = `
                <div class="weather-now">
                    <div class="weather-temp">${current.temperature ?? '?'}°C</div>
                    <div class="weather-desc">
                        <div class="weather-place">${escapeHtml(payload.location || location)}</div>
                        <div>${escapeHtml(current.text_en || '')} · feels ${current.apparent_temperature ?? '?'}°C · humidity ${current.humidity ?? '?'}%</div>
                    </div>
                </div>
                <div class="weather-days">
                    ${days.map(day => `
                        <div class="weather-day">
                            <div class="weather-day-date">${escapeHtml(String(day.date || '').slice(5))}</div>
                            <div class="weather-day-text">${escapeHtml(day.text_cn || day.text_en || '')}</div>
                            <div class="weather-day-range">${day.temp_min ?? '?'}~${day.temp_max ?? '?'}°C</div>
                            <div class="weather-day-rain">${day.precipitation_probability ?? 0}%</div>
                        </div>`).join('')}
                </div>
                <div class="weather-summary">${escapeHtml(payload.summary_cn || '')}</div>`;
        } catch (error) {
            renderSurfaceState(resultBox, {
                title: 'Weather could not be loaded',
                message: 'Check the city name or connection, then try again.',
                retry: () => fetchWeather(),
                details: error,
                icon: 'cloud'
            });
        }
    }

    // ========================================================================
    // Diary panel (feature ported from airi-main's text_journal tool and the
    // character notebook diary; recent entries also join the chat prompt).
    // ========================================================================
    function openJournalModal() {
        const modal = document.getElementById('journal-modal');
        if (modal) modal.style.display = 'flex';
        fetchJournalEntries();
    }

    function closeJournalModal() {
        const modal = document.getElementById('journal-modal');
        if (modal) modal.style.display = 'none';
    }

    function backToToolsFromJournal() {
        closeJournalModal();
        openToolsModal();
    }

    async function fetchJournalEntries() {
        const box = document.getElementById('journal-entries');
        if (!box) return;
        try {
            const response = await fetch('/api/journal/entries?limit=30', {cache: 'no-store'});
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const payload = await response.json();
            box.innerHTML = (payload.entries || []).length
                ? payload.entries.map(entry => `
                    <div class="journal-entry">
                        <div class="journal-entry-head">
                            <span class="journal-entry-title">${escapeHtml(entry.title || '')}</span>
                            <span class="journal-entry-meta">${escapeHtml(entry.date || '')} · ${entry.source === 'saya' ? 'Saya' : 'Doctor'}${entry.mood ? ' · ' + escapeHtml(entry.mood) : ''}</span>
                            <button class="journal-entry-delete" onclick="journalDeleteEntry('${entry.id}')">x</button>
                        </div>
                        <div class="journal-entry-body">${escapeHtml(entry.content || '')}</div>
                    </div>`).join('')
                : '<div class="journal-empty">No diary entries yet. Ask Saya to write about today.</div>';
        } catch (error) {
            renderSurfaceState(box, {
                title: 'Diary could not be loaded',
                message: 'Your existing entries are safe. Try again to refresh this view.',
                retry: () => fetchJournalEntries(),
                details: error,
                icon: 'book'
            });
        }
    }

    function journalCredentials() {
        const settings = getSavedApiSettings();
        const style = getSayaStyleSettings();
        return {
            api_url: settings.api_url || '',
            api_key: settings.api_key || '',
            api_model_name: settings.api_model_name || '',
            response_language: style.response_language || 'auto'
        };
    }

    async function journalWriteToday() {
        const button = document.getElementById('journal-write-btn');
        if (button) { button.disabled = true; button.textContent = 'Saya is writing...'; }
        try {
            const response = await fetch('/api/journal/write', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(journalCredentials())
            });
            const payload = await response.json();
            if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
            fetchJournalEntries();
        } catch (error) {
            showToast(`Could not write diary: ${error.message}`, { type: 'error', duration: 3600 });
        } finally {
            if (button) { button.disabled = false; button.textContent = 'Ask Saya to write today'; }
        }
    }

    async function journalAddManual() {
        const textarea = document.getElementById('journal-new-entry');
        const content = (textarea?.value || '').trim();
        if (!content) return;
        try {
            const response = await fetch('/api/journal/add', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({content})
            });
            if (!response.ok) throw new Error((await response.json()).detail || `HTTP ${response.status}`);
            if (textarea) textarea.value = '';
            fetchJournalEntries();
        } catch (error) {
            showToast(`Could not add note: ${error.message}`, { type: 'error', duration: 3600 });
        }
    }

    async function journalDeleteEntry(entryId) {
        confirmAction('Delete this diary entry?', async () => {

            try {
                const response = await fetch(`/api/journal/${entryId}/delete`, {method: 'POST'});
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                fetchJournalEntries();
            } catch (error) {
                showToast(`Could not delete: ${error.message}`, { type: 'error', duration: 3600 });
            }
    });}

    // ===================== Game bridge module =====================
    // Ported from airi-main's minecraft-bot integration: external bots push
    // status, Saya answers with [MC:] commands that queue for the bot to poll.
    let bridgeRefreshTimer = null;

    function openBridgeModal() {
        const modal = document.getElementById('bridge-modal');
        if (modal) modal.style.display = 'flex';
        bridgeBotDirty = false;
        refreshBridgeState();
        refreshBridgeBot();
        if (bridgeRefreshTimer) clearInterval(bridgeRefreshTimer);
        bridgeRefreshTimer = setInterval(() => { refreshBridgeState(); refreshBridgeBot(); }, 5000);
    }

    function closeBridgeModal() {
        const modal = document.getElementById('bridge-modal');
        if (modal) modal.style.display = 'none';
        if (bridgeRefreshTimer) { clearInterval(bridgeRefreshTimer); bridgeRefreshTimer = null; }
    }

    function backToToolsFromBridge() {
        closeBridgeModal();
        openToolsModal();
    }

    async function refreshBridgeState() {
        const box = document.getElementById('bridge-sources');
        if (!box) return;
        try {
            const response = await fetch('/api/bridge/state');
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const data = await response.json();
            if (!data.sources.length) {
                box.innerHTML = '<div class="bridge-empty">No bots connected yet. Push a status below to register one.</div>';
                return;
            }
            box.innerHTML = data.sources.map(source => `
                <div class="bridge-source">
                    <div class="bridge-head">
                        <span class="bridge-dot ${source.online ? 'on' : ''}"></span>
                        <span class="bridge-name">${escapeHtml(source.label)}</span>
                        <span class="bridge-badge">${source.online ? 'online' : 'offline'}</span>
                        <span class="bridge-pending">${source.pending} pending</span>
                    </div>
                    <div class="bridge-latest">${escapeHtml(source.latest_text || 'No status pushed yet.')}</div>
                    <div class="bridge-cmd-row">
                        <input type="text" id="bridge-cmd-${escapeHtml(source.source)}" placeholder="Send a command to this bot...">
                        <button onclick="bridgeSendCommand('${escapeHtml(source.source)}')">${ic('arrow-up-sm', 'icn-sm')}Send</button>
                    </div>
                    ${source.commands.length ? `<div class="bridge-commands">${source.commands.slice(-5).reverse().map(command => `
                        <div class="bridge-command ${command.status}">
                            <span class="bridge-command-origin">${command.origin === 'saya' ? 'Saya' : 'Doctor'}</span>
                            <span class="bridge-command-text">${escapeHtml(command.text)}</span>
                            <span class="bridge-command-status">${command.status}</span>
                        </div>`).join('')}</div>` : ''}
                    ${source.events.length ? `<div class="bridge-events">${source.events.slice(-4).reverse().map(event => `
                        <div class="bridge-event"><span class="bridge-event-lane">${escapeHtml(event.lane)}</span>${escapeHtml(event.text)}</div>`).join('')}</div>` : ''}
                </div>
            `).join('');
        } catch (error) {
            renderSurfaceState(box, {
                title: 'Bridge status unavailable',
                message: 'The bot connection was not changed. Try refreshing the status.',
                retry: () => refreshBridgeState(),
                details: error,
                icon: 'monitor'
            });
        }
    }

    async function bridgeSendCommand(source) {
        const input = document.getElementById(`bridge-cmd-${source}`);
        const text = input && input.value.trim();
        if (!text) return;
        try {
            const response = await fetch(`/api/bridge/${source}/send`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({text}),
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            input.value = '';
            refreshBridgeState();
        } catch (error) {
            showToast(`Could not send: ${error.message}`, { type: 'error', duration: 3600 });
        }
    }

    // --- Minecraft bot control (Node.js Mineflayer worker) ---
    // Once the user edits a field, stop overwriting the form from the saved
    // config until Start saves it or the modal is reopened.
    let bridgeBotDirty = false;

    function bridgeBotMarkDirty() {
        bridgeBotDirty = true;
    }

    async function refreshBridgeBot() {
        const badge = document.getElementById('bridge-bot-badge');
        const dot = document.getElementById('bridge-bot-dot');
        const errorBox = document.getElementById('bridge-bot-error');
        const logBox = document.getElementById('bridge-bot-log');
        const activityBox = document.getElementById('bridge-bot-activity');
        if (!badge) return;
        try {
            const response = await fetch('/api/bridge/minecraft/bot');
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const bot = await response.json();
            const engine = bot.engine === 'mineflayer' ? 'Mineflayer' : (bot.engine || 'Minecraft');
            badge.textContent = bot.running ? (bot.connected ? `in game · ${engine}` : `connecting · ${engine}`) : 'idle';
            if (bot.running && bot.connected && bot.pending_chats) {
                badge.textContent = `in game · ${bot.pending_chats} chat${bot.pending_chats > 1 ? 's' : ''}`;
            }
            dot.className = 'bridge-dot' + (bot.connected ? ' on' : '');
            errorBox.textContent = bot.last_error ? ` ${bot.last_error}` : '';
            if (activityBox) {
                const activity = bot.activity || {};
                const progress = Math.max(0, Math.min(100, Number(activity.progress || 0)));
                const follow = bot.follow_enabled
                    ? `Follow: ${escapeHtml(bot.follow_target || '?')} · ${escapeHtml(bot.follow_mode || 'on')}`
                    : 'Follow: off';
                const vitals = bot.runtime && bot.runtime.vitals
                    ? `HP ${escapeHtml(String(bot.runtime.vitals.health ?? '?'))} · Food ${escapeHtml(String(bot.runtime.vitals.food ?? '?'))}`
                    : '';
                const inventoryCount = bot.runtime && Array.isArray(bot.runtime.inventory)
                    ? bot.runtime.inventory.reduce((sum, item) => sum + Number(item.count || 0), 0)
                    : null;
                const inventory = inventoryCount === null ? '' : `Inventory ${inventoryCount}`;
                const title = activity.command
                    ? `${escapeHtml(activity.command)} · ${escapeHtml(activity.state || 'idle')}`
                    : 'No active task';
                const message = activity.message ? escapeHtml(activity.message) : 'Waiting for a command';
                activityBox.innerHTML = `
                    <div class="bridge-bot-activity-title"><span>${title}</span><span>${Math.round(progress)}%</span></div>
                    <div class="bridge-bot-progress"><span style="width:${progress}%"></span></div>
                    <div class="bridge-bot-activity-message">${message}</div>
                    <div class="bridge-bot-activity-meta">${follow}${vitals ? ` · ${vitals}` : ''}${inventory ? ` · ${inventory}` : ''}</div>`;
            }
            if (logBox) {
                logBox.innerHTML = bot.log.length
                    ? bot.log.map(line => `<div class="bridge-bot-log-line">${escapeHtml(line.text)}</div>`).join('')
                    : '<div class="bridge-bot-log-line dim">No bot activity yet.</div>';
            }
            if (bridgeBotDirty) return;
            const fields = {host: 'mc-host', port: 'mc-port', username: 'mc-username',
                            version: 'mc-version', account: 'mc-account'};
            for (const [key, id] of Object.entries(fields)) {
                const input = document.getElementById(id);
                if (input && bot.config && bot.config[key] !== undefined && document.activeElement !== input) {
                    input.value = bot.config[key];
                }
            }
            bridgeMaybeAutoReply(bot);
        } catch (error) {
            badge.textContent = 'error';
            errorBox.textContent = ` ${error.message}`;
        }
    }

    // Keep the unified Minecraft/desktop conversation credentials in sync
    // while the panel is open. In-game chat is handled internally now, so this
    // must run even when there are no legacy pending chat lines.
    let bridgeLastAutoReplyAt = 0;

    async function bridgeMaybeAutoReply(bot) {
        if (!bot || !bot.running || !bot.connected) return;
        const now = Date.now();
        if (now - bridgeLastAutoReplyAt < 10000) return;
        bridgeLastAutoReplyAt = now;
        try {
            const response = await fetch('/api/bridge/minecraft/bot/auto-reply', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(journalCredentials())
            });
            if (!response.ok) return;  // no key / not running: silent, log shows state
        } catch (error) { /* transient: next tick retries */ }
    }

    async function bridgeBotSaveConfig() {
        const payload = {
            host: document.getElementById('mc-host').value.trim(),
            port: parseInt(document.getElementById('mc-port').value, 10) || 25565,
            username: document.getElementById('mc-username').value.trim() || 'Saya',
            version: document.getElementById('mc-version').value.trim() || '1.21.5',
            account: document.getElementById('mc-account').value.trim(),
        };
        const response = await fetch('/api/bridge/minecraft/bot', {
            method: 'PUT',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload),
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
    }

    async function bridgeBotStart() {
        try {
            await bridgeBotSaveConfig();
            bridgeBotDirty = false;
            const response = await fetch('/api/bridge/minecraft/bot/start', {method: 'POST'});
            if (!response.ok) {
                const detail = await response.json().catch(() => ({}));
                throw new Error(detail.detail || `HTTP ${response.status}`);
            }
            refreshBridgeBot();
        } catch (error) {
            showToast(`Could not start bot: ${error.message}`, { type: 'error', duration: 3600 });
        }
    }

    async function bridgeBotStop() {
        try {
            const response = await fetch('/api/bridge/minecraft/bot/stop', {method: 'POST'});
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            setTimeout(refreshBridgeBot, 800);
        } catch (error) {
            showToast(`Could not stop bot: ${error.message}`, { type: 'error', duration: 3600 });
        }
    }

    // Desktop companion console (PySide6): the backend spawns it detached, so
    // the window survives independently and repeated clicks are idempotent.
    async function bridgeOpenConsole() {
        try {
            const response = await fetch('/api/bridge/minecraft/console', {method: 'POST'});
            const data = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
            const badge = document.getElementById('bridge-bot-error');
            if (badge && !data.already_running) {
                badge.textContent = ' desktop console launched';
                setTimeout(() => { if (badge) badge.textContent = badge.textContent.replace(' desktop console launched', ''); }, 4000);
            }
        } catch (error) {
            showToast(`Could not open desktop console: ${error.message}`, { type: 'error', duration: 3600 });
        }
    }
