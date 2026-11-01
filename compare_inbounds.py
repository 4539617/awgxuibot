import json

d = json.load(open('/tmp/ru_inbounds.json'))
inbounds = d['obj']

working = None
ours = None
for i in inbounds:
    if i['port'] == 49606:
        working = i
    if i['remark'] in ['yunpr', 'yunprofin', 'yunprofin2', 'yunpro']:
        ours = i
    # найти последний созданный (наш) - не рабочие
    if i['port'] not in [443, 49606, 45202] and 'rus_' not in i.get('remark',''):
        ours = i

def show(label, inb):
    print(f"\n{'='*60}")
    print(f"{label}: {inb['remark']} port={inb['port']}")
    print(f"{'='*60}")
    ss = inb.get('streamSettings', {})
    rs = ss.get('realitySettings', {})
    sett = inb.get('settings', {})
    clients = sett.get('clients', [])
    print(f"protocol: {inb.get('protocol')}")
    print(f"tag: {inb.get('tag')}")
    print(f"enable: {inb.get('enable')}")
    print(f"network: {ss.get('network')}")
    print(f"security: {ss.get('security')}")
    print(f"reality.target: {rs.get('target')}")
    print(f"reality.serverNames: {rs.get('serverNames')}")
    print(f"reality.privateKey: {rs.get('privateKey','')[:20]}...")
    print(f"reality.shortIds: {rs.get('shortIds')}")
    inner = rs.get('settings', {})
    print(f"reality.settings.publicKey: {inner.get('publicKey','')[:20]}...")
    print(f"reality.settings.fingerprint: {inner.get('fingerprint')}")
    print(f"reality.settings.spiderX: {inner.get('spiderX')}")
    print(f"clients count: {len(clients)}")
    if clients:
        c = clients[0]
        print(f"client[0].flow: {c.get('flow')}")
        print(f"client[0].email: {c.get('email')}")
    print(f"sniffing.enabled: {inb.get('sniffing',{}).get('enabled')}")
    print(f"decryption: {sett.get('decryption')}")
    testseed = sett.get('testseed')
    print(f"settings.testseed: {testseed}")

if working:
    show("РАБОЧИЙ (websrvinfo)", working)
if ours:
    show("НАШ", ours)
else:
    print("Наш inbound не найден")
    print("Все inbounds:", [(i['remark'], i['port']) for i in inbounds])
