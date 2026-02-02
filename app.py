"""
DigiGold Reconciliation Web Application
Reconciles transactions across Finfinity, Cashfree, and Augmont systems.
"""

from flask import Flask, request, render_template, send_file, jsonify
import pandas as pd
from io import BytesIO
import re

app = Flask(__name__)

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def read_file(file_obj):
    """
    Detect and read CSV or XLSX based on file extension.
    Returns a pandas DataFrame.
    """
    filename = file_obj.filename.lower()
    
    if filename.endswith('.csv'):
        return pd.read_csv(file_obj)
    elif filename.endswith('.xlsx') or filename.endswith('.xls'):
        return pd.read_excel(file_obj, engine='openpyxl')
    else:
        raise ValueError(f"Unsupported file format: {filename}. Please upload CSV or XLSX files.")


def clean_key(value):
    """
    Normalize keys for matching: strip whitespace, convert to lowercase, handle NaN.
    """
    if pd.isna(value):
        return ''
    return str(value).strip().lower()


def classify_by_decision_table(fin_status, cf_status, aug_status):
    """
    Apply the master decision table to determine category, action, and priority.
    
    Returns: (decision_category, action_required, priority)
    """
    # Normalize statuses for comparison
    fin = clean_key(fin_status)
    cf = clean_key(cf_status)
    aug = clean_key(aug_status)
    
    # Check for missing statuses
    cf_missing = cf == '' or cf == 'missing'
    aug_missing = aug == '' or aug == 'missing'
    
    # Determine if Augmont status indicates "cancelled"
    aug_cancelled = 'cancelled' in aug or 'canceled' in aug
    aug_not_cancelled = not aug_missing and not aug_cancelled
    
    # ========================================================================
    # MASTER DECISION TABLE (14 scenarios)
    # ========================================================================
    
    # Rule 5: Cashfree FAILED → PAYMENT_FAILED, IGNORE (Priority 1)
    if cf == 'failed':
        # Rule 8: Finfinity ACTIVE + Cashfree FAILED → ORDER_ACTIVE_PAYMENT_FAILED, CANCEL ORDER (Priority 3)
        if fin == 'active':
            return ('ORDER_ACTIVE_PAYMENT_FAILED', 'CANCEL ORDER', 3)
        # Rule 9: Finfinity PAID + Cashfree FAILED → INCONSISTENT_STATE, INVESTIGATE (Priority 4)
        if fin == 'paid':
            return ('INCONSISTENT_STATE', 'INVESTIGATE', 4)
        return ('PAYMENT_FAILED', 'IGNORE', 1)
    
    # Rule 6: Cashfree USER_DROPPED → USER_DROPPED, IGNORE (Priority 1)
    if cf == 'user_dropped':
        return ('USER_DROPPED', 'IGNORE', 1)
    
    # Rule 11: Cashfree PENDING → PAYMENT_NOT_CONFIRMED, WAIT / RETRY (Priority 2)
    if cf == 'pending':
        # Rule 7: Finfinity PENDING + Cashfree PENDING → PAYMENT_IN_PROGRESS, WAIT / RETRY (Priority 2)
        if fin == 'pending':
            return ('PAYMENT_IN_PROGRESS', 'WAIT / RETRY', 2)
        return ('PAYMENT_NOT_CONFIRMED', 'WAIT / RETRY', 2)
    
    # Rule 12: Finfinity FAILED → INTERNAL_FAILURE, INVESTIGATE (Priority 3)
    if fin == 'failed':
        # Rule 4: Finfinity FAILED + Cashfree SUCCESS + Augmont "not cancelled"
        if cf == 'success' and aug_not_cancelled:
            return ('GATEWAY_SUCCESS_INTERNAL_FAIL', 'INVESTIGATE', 3)
        return ('INTERNAL_FAILURE', 'INVESTIGATE', 3)
    
    # Rule 10: Cashfree SUCCESS + Augmont missing → PAYMENT_SUCCESS_ORDER_MISSING, INVESTIGATE / CREATE ORDER (Priority 4)
    if cf == 'success' and aug_missing:
        return ('PAYMENT_SUCCESS_ORDER_MISSING', 'INVESTIGATE / CREATE ORDER', 4)
    
    # Rule 1: Finfinity PAID/ACTIVE + Cashfree SUCCESS + Augmont "not cancelled" → FULLY_RECONCILED (Priority 1)
    if (fin == 'paid' or fin == 'active') and cf == 'success' and aug_not_cancelled:
        return ('FULLY_RECONCILED', 'NO ACTION', 1)
    
    # Rule 2: Finfinity PAID/ACTIVE + Cashfree SUCCESS + Augmont "cancelled" → REFUND_REQUIRED (Priority 4)
    if (fin == 'paid' or fin == 'active') and cf == 'success' and aug_cancelled:
        return ('REFUND_REQUIRED', 'REFUND REQUIRED', 4)
    
    # Rule 3: Finfinity PENDING + Cashfree SUCCESS + Augmont "not cancelled" → SYNC_PENDING (Priority 2)
    if fin == 'pending' and cf == 'success' and aug_not_cancelled:
        return ('SYNC_PENDING', 'SYNC / MONITOR', 2)
    
    # Rule 13: Default - Missing in Cashfree or Augmont → UNCATEGORIZED, INVESTIGATE (Priority 3)
    return ('UNCATEGORIZED', 'INVESTIGATE', 3)


def validate_columns(df, required_columns, file_name):
    """
    Validate that required columns exist in the DataFrame.
    Shows available columns if there's a mismatch.
    """
    # Normalize column names for comparison
    df_columns_lower = [col.strip().lower() for col in df.columns]
    
    missing = []
    for col in required_columns:
        if col.strip().lower() not in df_columns_lower:
            missing.append(col)
    
    if missing:
        available = ', '.join(df.columns.tolist()[:10])  # Show first 10 columns
        if len(df.columns) > 10:
            available += f'... and {len(df.columns) - 10} more'
        raise ValueError(f"Missing required columns in {file_name}: {', '.join(missing)}. Available columns: {available}")
    
    return True


def get_column_case_insensitive(df, column_name):
    """
    Get the actual column name from DataFrame matching case-insensitively.
    """
    for col in df.columns:
        if col.strip().lower() == column_name.strip().lower():
            return col
    return None


def sanitize_sheet_name(name):
    """
    Sanitize sheet name to be Excel-compatible.
    - Max 31 characters
    - No special characters: [ ] : * ? / \
    """
    # Remove invalid characters
    invalid_chars = ['[', ']', ':', '*', '?', '/', '\\']
    for char in invalid_chars:
        name = name.replace(char, '_')
    
    # Truncate to 31 characters
    if len(name) > 31:
        name = name[:31]
    
    return name


def reconcile_files(finfinity_file, cashfree_file, augmont_file):
    """
    Main reconciliation logic.
    Returns a BytesIO object containing the Excel workbook.
    """
    # Read files
    df_finfinity = read_file(finfinity_file)
    df_cashfree = read_file(cashfree_file)
    df_augmont = read_file(augmont_file)
    
    # Store raw data for output
    raw_finfinity = df_finfinity.copy()
    raw_cashfree = df_cashfree.copy()
    raw_augmont = df_augmont.copy()
    
    # Validate required columns
    validate_columns(df_finfinity, ['Order Id', 'Merchant Transaction ID', 'Order Status'], 'Finfinity')
    validate_columns(df_cashfree, ['Order Id', 'Transaction Status'], 'Cashfree')
    validate_columns(df_augmont, ['Merchant Transaction Id', 'Transaction Status'], 'Augmont')
    
    # Get actual column names (case-insensitive)
    fin_order_id_col = get_column_case_insensitive(df_finfinity, 'Order Id')
    fin_merchant_txn_col = get_column_case_insensitive(df_finfinity, 'Merchant Transaction ID')
    fin_status_col = get_column_case_insensitive(df_finfinity, 'Order Status')
    
    cf_order_id_col = get_column_case_insensitive(df_cashfree, 'Order Id')
    cf_status_col = get_column_case_insensitive(df_cashfree, 'Transaction Status')
    
    aug_merchant_txn_col = get_column_case_insensitive(df_augmont, 'Merchant Transaction Id')
    aug_status_col = get_column_case_insensitive(df_augmont, 'Transaction Status')
    
    # Create normalized keys for matching
    df_finfinity['_fin_order_key'] = df_finfinity[fin_order_id_col].apply(clean_key)
    df_finfinity['_fin_merchant_key'] = df_finfinity[fin_merchant_txn_col].apply(clean_key)
    
    df_cashfree['_cf_order_key'] = df_cashfree[cf_order_id_col].apply(clean_key)
    
    df_augmont['_aug_merchant_key'] = df_augmont[aug_merchant_txn_col].apply(clean_key)
    
    # Create lookup dictionaries for Cashfree and Augmont
    cf_lookup = df_cashfree.set_index('_cf_order_key')[cf_status_col].to_dict()
    aug_lookup = df_augmont.set_index('_aug_merchant_key')[aug_status_col].to_dict()
    
    # Process each Finfinity record
    results = []
    
    for idx, row in df_finfinity.iterrows():
        fin_order_key = row['_fin_order_key']
        fin_merchant_key = row['_fin_merchant_key']
        fin_status = row[fin_status_col]
        
        # Match with Cashfree
        in_cashfree = fin_order_key in cf_lookup and fin_order_key != ''
        cf_status = cf_lookup.get(fin_order_key, 'MISSING') if in_cashfree else 'MISSING'
        
        # Match with Augmont
        in_augmont = fin_merchant_key in aug_lookup and fin_merchant_key != ''
        aug_status = aug_lookup.get(fin_merchant_key, 'MISSING') if in_augmont else 'MISSING'
        
        # Classify based on decision table
        decision_category, action_required, priority = classify_by_decision_table(
            fin_status, cf_status, aug_status
        )
        
        # Create status combination string
        fin_status_clean = str(fin_status).replace(' ', '_')[:10] if pd.notna(fin_status) else 'NA'
        cf_status_clean = str(cf_status).replace(' ', '_')[:10] if cf_status != 'MISSING' else 'MISSING'
        aug_status_clean = str(aug_status).replace(' ', '_')[:10] if aug_status != 'MISSING' else 'MISSING'
        
        status_combination = f"FIN_{fin_status_clean}_CF_{cf_status_clean}_AUG_{aug_status_clean}"
        
        result = {
            **row.to_dict(),
            'In Cashfree?': 'YES' if in_cashfree else 'NO',
            'In Augmont?': 'YES' if in_augmont else 'NO',
            'Cashfree_Status': cf_status,
            'Augmont_Status': aug_status,
            'Decision_Category': decision_category,
            'Action_Required': action_required,
            'Priority': priority,
            'Status_Combination': status_combination
        }
        results.append(result)
    
    # Create results DataFrame
    df_results = pd.DataFrame(results)
    
    # Remove internal keys from output
    internal_cols = ['_fin_order_key', '_fin_merchant_key']
    df_results = df_results.drop(columns=[col for col in internal_cols if col in df_results.columns])
    
    # Create filtered DataFrames
    df_missing_cashfree = df_results[df_results['In Cashfree?'] == 'NO'].copy()
    df_missing_augmont = df_results[df_results['In Augmont?'] == 'NO'].copy()
    df_missing_both = df_results[
        (df_results['In Cashfree?'] == 'NO') & 
        (df_results['In Augmont?'] == 'NO')
    ].copy()
    
    # Calculate match statistics
    matched_in_cashfree = len(df_results[df_results['In Cashfree?'] == 'YES'])
    not_matched_in_cashfree = len(df_results[df_results['In Cashfree?'] == 'NO'])
    
    matched_in_augmont = len(df_results[df_results['In Augmont?'] == 'YES'])
    not_matched_in_augmont = len(df_results[df_results['In Augmont?'] == 'NO'])
    
    matched_in_both = len(df_results[(df_results['In Cashfree?'] == 'YES') & (df_results['In Augmont?'] == 'YES')])
    not_matched_in_both = len(df_results[(df_results['In Cashfree?'] == 'NO') | (df_results['In Augmont?'] == 'NO')])
    
    # Create summary statistics in the requested format
    summary_data = {
        'Metric': [
            'Total Finfinity Records',
            'Total Cashfree Records',
            'Total Augmont Records',
            '',  # Empty row for spacing
            'Finfinity Records in Cashfree',
            'Finfinity Records in Augmont',
            'Finfinity Records in Cashfree & Augmont'
        ],
        'Not Matched': [
            '',
            '',
            '',
            '',
            not_matched_in_cashfree,
            not_matched_in_augmont,
            not_matched_in_both
        ],
        'Matched': [
            '',
            '',
            '',
            '',
            matched_in_cashfree,
            matched_in_augmont,
            matched_in_both
        ]
    }
    
    # For total counts, put them in 'Not Matched' column (first data column)
    summary_data['Not Matched'][0] = len(df_finfinity)
    summary_data['Not Matched'][1] = len(df_cashfree)
    summary_data['Not Matched'][2] = len(df_augmont)
    
    # Rename first column to 'Count' for the totals section
    df_summary = pd.DataFrame(summary_data)
    df_summary.columns = ['Metric', 'Count', 'Matched']
    
    # Fix column header - Count should only show for totals, then Not Matched/Matched for comparison
    # Create a cleaner format
    summary_rows = [
        {'Metric': 'Total Finfinity Records', 'Count': len(df_finfinity)},
        {'Metric': 'Total Cashfree Records', 'Count': len(df_cashfree)},
        {'Metric': 'Total Augmont Records', 'Count': len(df_augmont)},
        {'Metric': '', 'Count': ''},
        {'Metric': '', 'Count': 'Not Matched', 'Matched': 'Matched'},
        {'Metric': 'Finfinity Records in Cashfree', 'Count': not_matched_in_cashfree, 'Matched': matched_in_cashfree},
        {'Metric': 'Finfinity Records in Augmont', 'Count': not_matched_in_augmont, 'Matched': matched_in_augmont},
        {'Metric': 'Finfinity Records in Cashfree & Augmont', 'Count': not_matched_in_both, 'Matched': matched_in_both},
    ]
    df_summary = pd.DataFrame(summary_rows)
    
    # Decision category descriptions
    category_descriptions = {
        'UNCATEGORIZED': 'Records missing in Cashfree or Augmont - needs manual review to identify root cause',
        'USER_DROPPED': 'User abandoned payment flow before completing - normal customer behavior, no action needed',
        'INTERNAL_FAILURE': 'Finfinity shows FAILED status - internal system error occurred, check logs',
        'ORDER_ACTIVE_PAYMENT_FAILED': 'Order is ACTIVE but payment FAILED - order should be cancelled immediately',
        'REFUND_REQUIRED': 'Payment SUCCESS but Augmont order CANCELLED - customer paid but order failed, refund needed',
        'INCONSISTENT_STATE': 'Finfinity shows PAID but Cashfree shows FAILED - critical data mismatch, urgent investigation',
        'FULLY_RECONCILED': 'All systems aligned - payment successful, order completed, no action needed',
        'PAYMENT_FAILED': 'Payment failed in Cashfree - no money collected, safe to ignore',
        'PAYMENT_IN_PROGRESS': 'Both Finfinity and Cashfree show PENDING - payment still processing, wait and retry',
        'PAYMENT_NOT_CONFIRMED': 'Cashfree shows PENDING - payment not yet confirmed, monitor and retry',
        'SYNC_PENDING': 'Finfinity PENDING but Cashfree SUCCESS - internal sync delay, monitor for auto-resolution',
        'GATEWAY_SUCCESS_INTERNAL_FAIL': 'Cashfree SUCCESS but Finfinity FAILED - payment received but internal error, investigate',
        'PAYMENT_SUCCESS_ORDER_MISSING': 'Cashfree SUCCESS but no Augmont order - payment collected but order not created, create order or refund'
    }
    
    # Create action summary with descriptions, sorted by count descending (funnel style)
    action_summary = df_results.groupby(['Action_Required', 'Decision_Category', 'Priority']).size().reset_index(name='Count')
    action_summary = action_summary.sort_values('Count', ascending=False)  # Funnel: highest count first
    
    # Add description column
    action_summary['Description'] = action_summary['Decision_Category'].map(category_descriptions)
    action_summary['Description'] = action_summary['Description'].fillna('No description available')
    
    # Reorder columns for better readability
    action_summary = action_summary[['Count', 'Action_Required', 'Decision_Category', 'Priority', 'Description']]
    
    # Create status combinations summary
    status_combinations = df_results.groupby('Status_Combination').size().reset_index(name='Count')
    status_combinations = status_combinations.sort_values('Count', ascending=False)
    
    # Create Excel output
    output = BytesIO()
    
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        # Summary sheets
        df_summary.to_excel(writer, sheet_name='SUMMARY', index=False)
        action_summary.to_excel(writer, sheet_name='ACTION_SUMMARY', index=False)
        status_combinations.to_excel(writer, sheet_name='STATUS_COMBINATIONS', index=False)
        
        # Complete Finfinity with all appended columns
        df_results.to_excel(writer, sheet_name='COMPLETE_FINFINITY', index=False)
        
        # Missing records sheets
        if len(df_missing_cashfree) > 0:
            df_missing_cashfree.to_excel(writer, sheet_name='MISSING_IN_CASHFREE', index=False)
        else:
            pd.DataFrame({'Note': ['No records missing in Cashfree']}).to_excel(
                writer, sheet_name='MISSING_IN_CASHFREE', index=False
            )
        
        if len(df_missing_augmont) > 0:
            df_missing_augmont.to_excel(writer, sheet_name='MISSING_IN_AUGMONT', index=False)
        else:
            pd.DataFrame({'Note': ['No records missing in Augmont']}).to_excel(
                writer, sheet_name='MISSING_IN_AUGMONT', index=False
            )
        
        if len(df_missing_both) > 0:
            df_missing_both.to_excel(writer, sheet_name='MISSING_IN_BOTH', index=False)
        else:
            pd.DataFrame({'Note': ['No records missing in both systems']}).to_excel(
                writer, sheet_name='MISSING_IN_BOTH', index=False
            )
        
        # Dynamic status-combination sheets
        unique_combinations = df_results['Status_Combination'].unique()
        for combo in unique_combinations:
            df_combo = df_results[df_results['Status_Combination'] == combo].copy()
            sheet_name = sanitize_sheet_name(combo)
            df_combo.to_excel(writer, sheet_name=sheet_name, index=False)
        
        # Raw data sheets
        raw_finfinity.to_excel(writer, sheet_name='RAW_FINFINITY', index=False)
        raw_cashfree.to_excel(writer, sheet_name='RAW_CASHFREE', index=False)
        raw_augmont.to_excel(writer, sheet_name='RAW_AUGMONT', index=False)
    
    output.seek(0)
    return output


# ============================================================================
# FLASK ROUTES
# ============================================================================

@app.route('/')
def index():
    """Render the upload interface."""
    return render_template('index.html')


@app.route('/reconcile', methods=['POST'])
def reconcile():
    """Process uploaded files and return Excel reconciliation report."""
    try:
        # Check if all files are uploaded
        if 'finfinity' not in request.files:
            return jsonify({'error': 'Finfinity file is required'}), 400
        if 'cashfree' not in request.files:
            return jsonify({'error': 'Cashfree file is required'}), 400
        if 'augmont' not in request.files:
            return jsonify({'error': 'Augmont file is required'}), 400
        
        finfinity_file = request.files['finfinity']
        cashfree_file = request.files['cashfree']
        augmont_file = request.files['augmont']
        
        # Validate files are not empty
        if finfinity_file.filename == '':
            return jsonify({'error': 'Finfinity file is required'}), 400
        if cashfree_file.filename == '':
            return jsonify({'error': 'Cashfree file is required'}), 400
        if augmont_file.filename == '':
            return jsonify({'error': 'Augmont file is required'}), 400
        
        # Validate file extensions
        allowed_extensions = ['.csv', '.xlsx', '.xls']
        for file, name in [(finfinity_file, 'Finfinity'), 
                           (cashfree_file, 'Cashfree'), 
                           (augmont_file, 'Augmont')]:
            ext = '.' + file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
            if ext not in allowed_extensions:
                return jsonify({
                    'error': f'{name} file must be CSV or XLSX format. Got: {file.filename}'
                }), 400
        
        # Perform reconciliation
        output = reconcile_files(finfinity_file, cashfree_file, augmont_file)
        
        # Return Excel file as download
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name='reconciliation_output.xlsx'
        )
    
    except ValueError as e:
        app.logger.error(f'ValueError: {str(e)}')
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        import traceback
        app.logger.error(f'Exception: {traceback.format_exc()}')
        return jsonify({'error': f'An error occurred: {str(e)}'}), 500


@app.route('/health')
def health():
    """Health check endpoint."""
    return jsonify({
        'status': 'healthy',
        'service': 'DigiGold Reconciliation',
        'version': '1.0.0'
    })


# ============================================================================
# MAIN
# ============================================================================

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
