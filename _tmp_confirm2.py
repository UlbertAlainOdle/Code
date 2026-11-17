import io, re

path = 'static/js/app.js'
with io.open(path, 'r', encoding='utf-8') as f:
    src = f.read()

def match_paren(text, start):
    depth = 0
    i = start
    in_str = None
    while i < len(text):
        ch = text[i]
        if in_str:
            if ch == '\\':
                i += 2
                continue
            if ch == in_str:
                in_str = None
            elif in_str == '`' and text[i+1:i+2] == '{':
                j = match_brace(text, i+1)
                i = j
                continue
            i += 1
            continue
        if ch in ('"', "'", '`'):
            in_str = ch
            i += 1
            continue
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise ValueError('unbalanced parens')

def match_brace(text, start):
    depth = 0
    i = start
    in_str = None
    while i < len(text):
        ch = text[i]
        if in_str:
            if ch == '\\':
                i += 2
                continue
            if ch == in_str:
                in_str = None
            elif in_str == '`' and text[i+1:i+2] == '{':
                j = match_brace(text, i+1)
                i = j
                continue
            i += 1
            continue
        if ch in ('"', "'", '`'):
            in_str = ch
            i += 1
            continue
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise ValueError('unbalanced braces')

def find_enclosing_function(src, pos):
    best = None
    for m in re.finditer(r'\n    (?:async )?function [A-Za-z0-9_]+\s*\(', src):
        if m.start() >= pos:
            break
        open_idx = src.find('{', m.end())
        if open_idx == -1:
            continue
        try:
            close_idx = match_brace(src, open_idx)
        except ValueError:
            continue
        if open_idx < pos < close_idx:
            best = (open_idx, close_idx)
    return best

def indent_block(text, spaces):
    pad = ' ' * spaces
    return '\n'.join(pad + line if line.strip() else line for line in text.split('\n'))

converted = 0
skipped = []

# Process guard-style confirms bottom-up
while True:
    matches = list(re.finditer(r'if *\((!?)(confirm)\(', src))
    if not matches:
        break
    m = matches[-1]  # bottom-most
    neg = m.group(1) == '!'
    stmt_start = m.start()
    open_paren = src.find('(', m.start())  # the if's paren
    try:
        if_close = match_paren(src, open_paren)
    except ValueError:
        skipped.append(stmt_start)
        break
    # after if(...) expect: ' return;' or ' {'
    after = src[if_close:if_close+20].lstrip()
    if neg and after.startswith('return'):
        ret_end = src.find('return', if_close)
        ret_end = src.index(';', ret_end) + 1
        fn = find_enclosing_function(src, stmt_start)
        if not fn:
            skipped.append(src[stmt_start:stmt_start+60])
            # neutralize to avoid infinite loop
            src = src[:stmt_start] + '/*SKIPPED_CONFIRM*/' + src[stmt_start:]
            continue
        body_open, body_close = fn
        cond_expr = src[open_paren+1:if_close-1]  # '!confirm(X)'
        inner = cond_expr[len('!confirm('):-1] if cond_expr.startswith('!') else cond_expr
        rest = src[ret_end:body_close-1].rstrip()
        if not rest.strip():
            skipped.append('empty rest @' + str(stmt_start))
            src = src[:stmt_start] + '/*SKIPPED_CONFIRM*/' + src[stmt_start:]
            continue
        indent = 8
        new_block = ('confirmAction(' + inner + ', async () => {\n'
                     + indent_block(rest, 4)
                     + '\n    });')
        src = src[:stmt_start] + new_block + src[body_close-1:]
        converted += 1
    elif (not neg) and after.startswith('{'):
        b_open = src.find('{', if_close)
        b_close = match_brace(src, b_open)
        cond_expr = src[open_paren+1:if_close-1]
        inner = cond_expr[len('confirm('):-1]
        body = src[b_open+1:b_close-1]
        new_block = ('confirmAction(' + inner + ', () => {'
                     + body +
                     '});')
        src = src[:stmt_start] + new_block + src[b_close:]
        converted += 1
    else:
        # unsupported shape: report and stop
        skipped.append(src[stmt_start:stmt_start+100].replace('\n', ' '))
        break

print('converted:', converted)
print('skipped:')
for s in skipped:
    print('  ', s[:100])

with io.open(path, 'w', encoding='utf-8', newline='') as f:
    f.write(src)
