# NovaRouter Health Check & Model Management System

ব্যক্তিগতভাবে তৈরি করা auto health check এবং model management system যেটি:

## Features
✅ **Automatic Health Check**: সব enabled model-এর health check করে (প্রথম 24টিতে থামে না)
✅ **Auto Disable**: Dead/unreachable model গুলো automatically disable করে  
✅ **Re-check Option**: Disabled model গুলো আবার check করে re-enable করার option  
✅ **Scheduled runner**: Existing scheduled invocation থেকে automatic cleanup
✅ **Interactive Menu**: User-friendly interface  
✅ **Logging**: Detailed logs with timestamps  

## Available Tools

### 1. Interactive Health Check Script (`health_check_and_toggle.py`)
```
python3 /root/Novarouter2/Novarouter/health_check_and_toggle.py
```

**Options:**
1. **Run health check on all enabled models** - সব enabled model check করে
2. **Auto-disable dead/unreachable models** - Dead model গুলো automatically disable করবে
3. **List and re-check disabled models** - Disabled model গুলো দেখাবে, selected গুলো আবার check করে re-enable করবে
4. **Full automatic cleanup** - Health check + auto-disable একসাথে
5. **Exit**

### 2. Cron Job Script (`cron_health_check.py`)
Scheduled job জন্য তৈরি:
```
python3 /root/Novarouter2/Novarouter/cron_health_check.py
```

এটা automatically:
- সব enabled model check করে
- Dead model disable করে
- Log file-এ summary save করে (`/root/Novarouter2/Novarouter/health_check.log`)

## Scheduling and coverage

`python3 cron_health_check.py` প্রতিবার **সব enabled model** probe করে, একসঙ্গে সর্বোচ্চ 3টি request পাঠায়, এবং শুধু সেই run-এ নিশ্চিত dead হওয়া model disable করে। Cooling/unknown বা ব্যর্থ probe-এর model disable হয় না; partial run non-zero exit দেয়। Dashboard-এর **Models → Health check**-ও পুরো catalogue-এর সব enabled model check করে, শুধু দৃশ্যমান page/filter নয়। Disabled model আবার চালু করতে আলাদাভাবে re-enable বা Ping করুন।

এই repository কোনো daily scheduler install করে না। আপনার deployment-এ যে scheduler ইতিমধ্যে runner চালায়, সেটি **প্রতিদিন একবার** invoke হচ্ছে কি না যাচাই করুন। নিচের পুরনো 12-hour example-কে daily schedule ধরে নেবেন না।

## Legacy examples (not installed automatically)

### Option A: Crontab (Every 12 hours)
```bash
# Edit crontab
crontab -e

# Add this line (every 12 hours at 00:00 and 12:00)
0 */12 * * * cd /root/Novarouter2/Novarouter && python3 cron_health_check.py
```

### Option B: Systemd Timer (Recommended)
```bash
# Create service file
sudo nano /etc/systemd/system/novarouter-health.service
```
```ini
[Unit]
Description=NovaRouter Health Check Service
After=network.target

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /root/Novarouter2/Novarouter/cron_health_check.py
WorkingDirectory=/root/Novarouter2/Novarouter
User=root

[Install]
WantedBy=multi-user.target
```

```bash
# Create timer file
sudo nano /etc/systemd/system/novarouter-health.timer
```
```ini
[Unit]
Description=Run NovaRouter health check every 12 hours

[Timer]
OnCalendar=*-*-* 00/12:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
# Enable and start
sudo systemctl enable novarouter-health.timer
sudo systemctl start novarouter-health.timer
```

## API Endpoints (Already Available)

NovaRouter already has these endpoints:

1. **`POST /api/admin/models/disable-unreachable`** - Disable all dead models
2. **`POST /api/admin/models/{id}/ping`** - Health check single model
3. **`PATCH /api/admin/models/{id}`** - Toggle model enabled/disabled

## How It Works

### Health Check Process:
1. Random question send করে model-কে (একটি word-এর answer দিতে হবে)
2. যদি 200-299 HTTP status পায় এবং reply আসে → **Healthy**
3. যদি 429 status পায় → **Cooling** (rate limited)
4. যদি অন্য error পায় → **Dead**
5. যদি network error হয় → **Dead**

### Auto-Disable:
- যেসব model "dead" status-এ আছে এবং enabled আছে → automatically disable হয়ে যাবে

### Re-check Feature:
- Disabled model গুলো select করে আবার health check করতে পারবে
- যদি healthy হয় → automatically re-enable হবে

## Troubleshooting

### Logs দেখতে:
```bash
# Interactive script logs (console-এ থাকে)
tail -f /root/Novarouter2/Novarouter/health_check.log

# Database status দেখতে
sqlite3 /root/Novarouter2/Novarouter/db/sqlite.db "SELECT exposedId, enabled, status FROM Model LIMIT 10;"
```

### Manual Check:
```bash
# Quick manual health check
cd /root/Novarouter2/Novarouter
python3 -c "
from nova.database import SessionLocal
from nova.models import Model
from sqlalchemy import select
db = SessionLocal()
models = db.scalars(select(Model).where(Model.enabled.is_(True))).all()
print(f'Enabled models: {len(models)}')
for m in models[:5]:
    print(f'  - {m.exposedId}: {m.status}')
"
```

## Configuration Options

Script গুলোতে customize করতে পারবে:

1. **Check interval**: `cron_health_check.py` - crontab/systemd timer দিয়ে control করবে
2. **Timeout**: `PING_TIMEOUT_S = 15.0` (seconds)
3. **Questions**: `PROBE_QUESTIONS` list modify করতে পারবে
4. **Logging**: Log level এবং format change করতে পারবে

## Recommendations

1. **Daily Check**: প্রতিদিন 1 বার auto health check রাখা ভালো
2. **Notification**: যদি আরো advanced করতে চাও, email/slack notification add করতে পারবে
3. **Dashboard**: Current status real-time দেখার জন্য web dashboard করতে পারবে
4. **Auto-re-enable**: Dead model গুলো after few days আবার check করে re-enable করার logic add করতে পারবে

## Need Help?

কোন সমস্যা হলে:
1. Logs check করো: `cat /root/Novarouter2/Novarouter/health_check.log`
2. Database connection verify করো
3. NovaRouter server running আছে কিনা check করো

**এখন তুমি এক command দিয়ে সব model check করতে পারবে এবং unreachable model গুলো automatically disable হবে!** 🚀
