# DigiGold Reconciliation Web Application

A Flask web application that reconciles transactions across three systems: **Finfinity** (internal system), **Cashfree** (payment gateway), and **Augmont** (gold order provider).

## Features

- **Multi-format Support**: Accepts both CSV and XLSX file formats
- **Comprehensive Reconciliation**: Matches transactions across three systems
- **Master Decision Table**: 14 predefined scenarios with priority levels
- **Detailed Excel Output**: Multi-sheet workbook with complete analysis
- **Modern UI**: Dark teal/green themed responsive interface
- **Vercel Ready**: Configured for easy deployment

## Technical Stack

- Python 3.12
- Flask 3.1.1
- Pandas 2.3.3
- openpyxl 3.1.5

## Installation

### Local Development

1. Clone the repository:
```bash
git clone <repository-url>
cd dgr
```

2. Create a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Run the application:
```bash
python app.py
```

5. Open your browser and navigate to `http://localhost:5000`

### Vercel Deployment

1. Install Vercel CLI:
```bash
npm install -g vercel
```

2. Deploy:
```bash
vercel
```

## File Requirements

### Finfinity File
Required columns:
- `Order Id`
- `Merchant Transaction ID`
- `Order Status`

### Cashfree File
Required columns:
- `Order Id`
- `Transaction Status`

### Augmont File
Required columns:
- `Merchant Transaction Id`
- `Transaction Status`

## Reconciliation Logic

### Matching Rules
- **Finfinity ↔ Cashfree**: Matched using `Order Id`
- **Finfinity ↔ Augmont**: Matched using `Merchant Transaction ID`
- All matching is case-insensitive with trimmed whitespace

### Master Decision Table (14 Scenarios)

| # | Finfinity Status | Cashfree Status | Augmont Status | Decision Category | Action Required | Priority |
|---|------------------|-----------------|----------------|-------------------|-----------------|----------|
| 1 | PAID/ACTIVE | SUCCESS | Not Cancelled | FULLY_RECONCILED | NO ACTION | 1 |
| 2 | PAID/ACTIVE | SUCCESS | Cancelled | REFUND_REQUIRED | REFUND REQUIRED | 4 |
| 3 | PENDING | SUCCESS | Not Cancelled | SYNC_PENDING | SYNC / MONITOR | 2 |
| 4 | FAILED | SUCCESS | Not Cancelled | GATEWAY_SUCCESS_INTERNAL_FAIL | INVESTIGATE | 3 |
| 5 | Any | FAILED | Any | PAYMENT_FAILED | IGNORE | 1 |
| 6 | Any | USER_DROPPED | Any | USER_DROPPED | IGNORE | 1 |
| 7 | PENDING | PENDING | Any | PAYMENT_IN_PROGRESS | WAIT / RETRY | 2 |
| 8 | ACTIVE | FAILED | Any | ORDER_ACTIVE_PAYMENT_FAILED | CANCEL ORDER | 3 |
| 9 | PAID | FAILED | Any | INCONSISTENT_STATE | INVESTIGATE | 4 |
| 10 | Any | SUCCESS | Missing | PAYMENT_SUCCESS_ORDER_MISSING | INVESTIGATE / CREATE ORDER | 4 |
| 11 | Any | PENDING | Any | PAYMENT_NOT_CONFIRMED | WAIT / RETRY | 2 |
| 12 | FAILED | Any | Any | INTERNAL_FAILURE | INVESTIGATE | 3 |
| 13 | Default | - | - | UNCATEGORIZED | INVESTIGATE | 3 |

### Priority Levels
- **Priority 1**: No action needed or can be ignored
- **Priority 2**: Monitoring/waiting required
- **Priority 3**: Investigation or cancellation needed
- **Priority 4**: Critical - requires immediate action

## Output Excel Structure

The generated `reconciliation_output.xlsx` contains the following sheets:

### Summary Sheets
1. **SUMMARY**: Total counts from each system
2. **ACTION_SUMMARY**: Grouped by action required with counts
3. **STATUS_COMBINATIONS**: All unique status combinations found

### Main Data Sheets
4. **COMPLETE_FINFINITY**: All Finfinity records with appended columns:
   - In Cashfree? (YES/NO)
   - In Augmont? (YES/NO)
   - Cashfree_Status
   - Augmont_Status
   - Decision_Category
   - Action_Required
   - Priority
   - Status_Combination

### Missing Records Sheets
5. **MISSING_IN_CASHFREE**: Finfinity records not found in Cashfree
6. **MISSING_IN_AUGMONT**: Finfinity records not found in Augmont
7. **MISSING_IN_BOTH**: Finfinity records missing from both systems

### Dynamic Sheets
8. **Status Combination Sheets**: Separate sheet for each unique status combination (e.g., `FIN_PAID_CF_SUCCESS_AUG_Not Can`)

### Raw Data Sheets
9. **RAW_FINFINITY**: Complete original Finfinity data
10. **RAW_CASHFREE**: Complete original Cashfree data
11. **RAW_AUGMONT**: Complete original Augmont data

## API Endpoints

### GET /
Renders the upload interface

### POST /reconcile
Processes uploaded files and returns Excel reconciliation report

**Request**: `multipart/form-data` with files:
- `finfinity`: Finfinity data file (CSV/XLSX)
- `cashfree`: Cashfree data file (CSV/XLSX)
- `augmont`: Augmont data file (CSV/XLSX)

**Response**: Excel file download (`reconciliation_output.xlsx`)

### GET /health
Health check endpoint

**Response**:
```json
{
    "status": "healthy",
    "service": "DigiGold Reconciliation",
    "version": "1.0.0"
}
```

## Project Structure

```
dgr/
├── app.py                 # Main Flask application
├── templates/
│   └── index.html         # Upload interface
├── requirements.txt       # Python dependencies
├── vercel.json           # Vercel deployment config
└── README.md             # Documentation
```

## Error Handling

The application validates:
- All three files must be uploaded
- File extensions must be `.csv` or `.xlsx`
- Required columns must exist in each file
- Graceful error messages for any processing failures

## License

MIT License

## Support

For issues or questions, please open an issue in the repository.
