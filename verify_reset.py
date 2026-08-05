from google.cloud import firestore
db = firestore.Client.from_service_account_json('/home/mtoro/projects/markettool/trading-firestore.json')
mon_ref = db.collection('monitoreos')
docs = list(mon_ref.limit(5).stream())
print('=== VERIFICACAO POST-RESET ===')
for doc in docs:
    data = doc.to_dict()
    symbol = data.get('symbol', 'unknown')
    exec_id = str(data.get('exec_id', 'unknown'))[:8]
    running = data.get('running', [])
    estado = data.get('estado', 'N/A')
    print(f'{symbol} ({exec_id}): running={running}, estado={estado}')
print('OK - Temporalidades INACTIVAS')
