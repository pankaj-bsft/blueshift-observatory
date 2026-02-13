# Account Mappings Management System

## Overview
This feature allows you to map sending domains to customer accounts and view consolidated account-level reporting in the MBR Dashboard.

## Features Implemented

### 1. Database-Backed Management
- **SQLite Database**: All mappings stored in `/Users/pankaj/pani/data/account_mappings.db`
- **Auto-initialization**: Database and tables created automatically on startup
- **CSV Integration**: Auto-imports from CSV file if database is empty
- **Indexes**: Fast lookups on both domain and account names

### 2. Management UI (Browser-Based)
- **View All Mappings**: Browse all domain-account mappings
- **Search**: Real-time search across domains and accounts
- **Add Mapping**: Create new domain-account associations
- **Edit Mapping**: Update account name and notes for existing mappings
- **Delete Mapping**: Remove individual mappings with confirmation
- **Statistics Dashboard**: View total mappings, accounts, and top accounts

### 3. Import/Export
- **Import CSV**: Bulk import from CSV file (upserts existing mappings)
- **Export CSV**: Backup current mappings to CSV file
- **CSV Format**:
  ```csv
  sending_domain,account_name
  example.com,Acme Corporation
  mail.example.com,Acme Corporation
  ```

### 4. Account-Level Reporting (API Ready)
The backend is fully prepared for account-level aggregation:
- Top 10 accounts by ESP
- Top 10 accounts overall
- Account summary with domain breakdown
- All metrics aggregated at account level

## File Structure

```
backend/
├── account_mapping_service.py       # CRUD operations for mappings
├── account_aggregation_service.py   # Account-level data aggregation
└── app.py                          # API endpoints

frontend/
├── index.html                      # Account Mappings UI
├── js/app.js                       # Vue.js logic
└── css/main.css                    # Modal styles

data/
├── account_mappings.db             # SQLite database (auto-created)
├── domain_account_mapping.csv      # Your CSV file
└── domain_account_mapping_sample.csv  # Sample format
```

## API Endpoints

### Mapping Management
- `GET /api/account-mappings` - List all mappings (with search)
- `GET /api/account-mappings/{id}` - Get single mapping
- `POST /api/account-mappings` - Create new mapping
- `PUT /api/account-mappings/{id}` - Update mapping
- `DELETE /api/account-mappings/{id}` - Delete mapping
- `POST /api/account-mappings/bulk-delete` - Delete multiple mappings
- `POST /api/account-mappings/import-csv` - Import from CSV
- `GET /api/account-mappings/export-csv` - Export to CSV
- `GET /api/account-mappings/statistics` - Get mapping statistics

### Account Reporting
- `POST /api/fetch-data-by-account` - Get account-level aggregated data
- `GET /api/account-summary/{account_name}` - Detailed account summary

## How to Use

### Initial Setup

1. **Prepare your CSV file** at `/Users/pankaj/pani/data/domain_account_mapping.csv`:
   ```csv
   sending_domain,account_name
   example.com,Acme Corporation
   widget.com,Widget Industries
   ```

2. **Start the backend** (database auto-initializes and imports CSV):
   ```bash
   cd /Users/pankaj/pani/mbr_dashboard/backend
   python app.py
   ```

3. **Open the frontend** and navigate to "Account Mappings" tab

### Managing Mappings in Browser

#### Add New Mapping
1. Click "Add Mapping" button
2. Enter sending domain (e.g., `example.com`)
3. Enter account name (e.g., `Acme Corporation`)
4. Optionally add notes
5. Click "Create"

#### Edit Mapping
1. Find the mapping in the table
2. Click "Edit" button
3. Update account name or notes
4. Click "Update"

#### Delete Mapping
1. Find the mapping in the table
2. Click "Delete" button
3. Confirm deletion

#### Search Mappings
- Type in the search box to filter by domain or account name
- Results update automatically

#### Import from CSV
1. Update your CSV file at `/Users/pankaj/pani/data/domain_account_mapping.csv`
2. Click "Import CSV" button
3. Confirm import
4. Existing mappings will be updated, new ones added

#### Export to CSV
1. Click "Export CSV" button
2. File saved to `/Users/pankaj/pani/data/domain_account_mapping.csv`

### Viewing Account-Level Reports

The backend supports account-level reporting. To integrate with UI:

**API Call Example:**
```javascript
// Fetch top 10 accounts by ESP
const response = await fetch('http://localhost:8000/api/fetch-data-by-account', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    from_date: '2025-01-01',
    to_date: '2025-01-31'
  })
});

const result = await response.json();
console.log(result.top_accounts_by_esp);      // Top 10 per ESP
console.log(result.top_accounts_overall);     // Top 10 overall
```

**Response Format:**
```json
{
  "status": "success",
  "top_accounts_by_esp": {
    "SparkPost": [
      {
        "Account": "Acme Corporation",
        "ESP": "SparkPost",
        "Sent": 1000000,
        "Delivered": 985000,
        "Delivery_Rate_%": 98.5,
        "Bounce_Rate_%": 0.8,
        "Spam_Rate_%": 0.1,
        "Rank": 1
      }
    ],
    "SendGrid": [...],
    "Mailgun": [...]
  },
  "top_accounts_overall": [
    {
      "Account": "Acme Corporation",
      "Sent": 3500000,
      "Delivered": 3450000,
      "Delivery_Rate_%": 98.6,
      "Rank": 1
    }
  ],
  "total_accounts": 45,
  "unmapped_domains": 12
}
```

## Data Flow

```
┌─────────────────┐
│   CSV File      │
│  (Initial Load) │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ SQLite Database │ ◄──── Browser UI (CRUD Operations)
│   (Source of    │
│     Truth)      │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Aggregation     │ ──► Account-Level Reports
│    Service      │
└─────────────────┘
```

## Database Schema

```sql
CREATE TABLE domain_account_mapping (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sending_domain TEXT UNIQUE NOT NULL,
    account_name TEXT NOT NULL,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for fast lookups
CREATE INDEX idx_sending_domain ON domain_account_mapping(sending_domain);
CREATE INDEX idx_account_name ON domain_account_mapping(account_name);
```

## Maintenance

### Backup Mappings
```bash
# Option 1: Export via API
curl http://localhost:8000/api/account-mappings/export-csv

# Option 2: Copy database file
cp /Users/pankaj/pani/data/account_mappings.db /path/to/backup/
```

### Restore from CSV
1. Place CSV file at `/Users/pankaj/pani/data/domain_account_mapping.csv`
2. Click "Import CSV" in the UI
3. Or restart backend to auto-import

### Update Mappings for New Client
**Option 1: Via UI**
- Click "Add Mapping"
- Enter domain and account name

**Option 2: Via CSV**
- Add new row to CSV file
- Click "Import CSV"

**Option 3: Via API**
```bash
curl -X POST http://localhost:8000/api/account-mappings \
  -H "Content-Type: application/json" \
  -d '{
    "sending_domain": "newclient.com",
    "account_name": "New Client Inc"
  }'
```

### Remove Terminated Account
**Option 1: Via UI**
- Find all mappings for that account (use search)
- Click "Delete" for each mapping

**Option 2: Via Database**
```sql
DELETE FROM domain_account_mapping
WHERE account_name = 'Terminated Account';
```

## Next Steps (Optional Enhancements)

1. **Add Account View to MBR Tab**
   - Toggle between "By Domain" and "By Account" views
   - Show top 10 accounts in MBR Deliverability

2. **Bulk Operations**
   - Checkbox selection for bulk delete
   - Bulk edit account names

3. **History/Audit Trail**
   - Track who changed what and when
   - Revert capability

4. **Advanced Search**
   - Filter by account
   - Filter by date range
   - Export filtered results

5. **Validation Rules**
   - Domain format validation
   - Duplicate detection warnings
   - Account name standardization

## Troubleshooting

### Issue: Mappings not showing
- Check backend is running
- Open browser console for errors
- Verify database exists: `ls /Users/pankaj/pani/data/account_mappings.db`

### Issue: Import CSV fails
- Verify CSV format matches: `sending_domain,account_name`
- Check file path is correct
- Ensure no special characters in domains

### Issue: Search not working
- Clear search box and try again
- Refresh the page
- Check backend logs for errors

### Issue: Database locked
- Only one process can write at a time
- Close other connections to the database
- Restart backend if needed

## Support

For questions or issues, check:
1. Backend logs: Look for errors in terminal where `app.py` is running
2. Browser console: Press F12 and check Console tab
3. Database: Use SQLite browser to inspect data
