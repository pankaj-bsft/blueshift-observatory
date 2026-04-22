from pathlib import Path

path = Path("/home/ec2-user/pani/blueshift_observatory/backend/app.py")
text = path.read_text()
old = "    get_pulsation_summary
)
"
new = "    get_pulsation_summary,
    get_daily_summary
)
"
if old in text:
    text = text.replace(old, new, 1)

marker = "@app.get("/api/pulsation/available-dates")
"
idx = text.find(marker)
if idx == -1:
    raise SystemExit('marker not found')
next_app = text.find("
@app", idx + 1)
if next_app == -1:
    raise SystemExit('next app not found')

if "@app.get("/api/pulsation/daily-summary")" not in text:
    endpoint = Path('/tmp/DAILY_SUMMARY_ENDPOINT.txt').read_text()
    text = text[:next_app] + endpoint + text[next_app:]

path.write_text(text)
print('updated')
