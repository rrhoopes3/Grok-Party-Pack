import re, os, glob

total = 0
descriptions = []
base = os.path.join(os.getcwd(), 'forge', 'tools')

for pyfile in sorted(glob.glob(os.path.join(base, '*.py'))):
    if 'registry' in pyfile:
        continue
    with open(pyfile, encoding='utf-8') as f:
        content = f.read()
    # Find description= blocks
    # Match description=( "..." "..." ... )
    idx = 0
    while True:
        pos = content.find('description=(\n', idx)
        if pos == -1:
            pos = content.find('description=( ', idx)
            if pos == -1:
                break
        # Find the closing )
        start = pos + len('description=(')
        depth = 1
        i = start
        while i < len(content) and depth > 0:
            if content[i] == '(':
                depth += 1
            elif content[i] == ')':
                depth -= 1
            i += 1
        if depth == 0:
            raw = content[start:i-1].strip()
            # Extract string content
            text = re.sub(r'["\\]', '', raw)
            text = re.sub(r'\s+', ' ', text).strip()
            descriptions.append((os.path.basename(pyfile), len(text), text[:100]))
            total += len(text)
            idx = i
        else:
            break

descriptions.sort(key=lambda x: -x[1])
print(f'Total description chars: {total}')
print(f'Tool count: {len(descriptions)}')
for name, size, preview in descriptions[:20]:
    print(f'  {name}: {size} chars - {preview}')
