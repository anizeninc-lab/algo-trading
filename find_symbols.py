import requests, gzip, io, csv, re

url = 'https://assets.upstox.com/market-quote/instruments/exchange/NSE.csv.gz'
r = requests.get(url, timeout=30)
content = gzip.decompress(r.content).decode('utf-8')
reader = csv.DictReader(io.StringIO(content))

results = []
for row in reader:
    name = row.get('tradingsymbol', '')
    ikey = row.get('instrument_key', '')
    exp  = row.get('expiry', '')
    if 'NIFTY26APR' in name and 'BANK' not in name and 'NXT' not in name and 'FIN' not in name and 'MID' not in name:
        # Parse strike from name e.g. NIFTY26APR24550CE
        m = re.search(r'NIFTY26APR(\d+)(CE|PE)', name)
        if m:
            strike = int(m.group(1))
            opt    = m.group(2)
            if 23000 <= strike <= 26000:
                results.append((ikey, name, exp, strike, opt))

results.sort(key=lambda x: (x[3], x[4]))
print(f'Found {len(results)} ATM contracts for April 28')
for ikey, name, exp, strike, opt in results:
    print(f'{ikey} | {name} | strike: {strike} | {opt}')
