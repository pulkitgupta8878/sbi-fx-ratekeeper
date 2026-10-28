import os
import re
import sys
import csv
import pandas as pd
from pathlib import Path

# --- CRITICAL FIX FOR EXECUTABLES ---
if getattr(sys, 'frozen', False):
    application_path = os.path.dirname(sys.executable)
else:
    application_path = os.path.dirname(os.path.abspath(__file__))
os.chdir(application_path)
# ------------------------------------

try:
    from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout,
                                 QLabel, QLineEdit, QPushButton, QCheckBox,
                                 QFileDialog, QMessageBox, QGroupBox)
    from PySide6.QtCore import Qt
except ImportError:
    print("CRITICAL ERROR: PySide6 is not installed.")
    print("Please run: pip install PySide6")
    sys.exit(1)

# =====================================================================
# CORE UTILITY FUNCTIONS
# =====================================================================
def find_column_flexibly(df, target_name):
    target_clean = target_name.strip().upper()
    for col in df.columns:
        if str(col).strip().upper() == target_clean:
            return col
    return None

def parse_date_ultra_flexible(val, day_first=False):
    s = str(val).strip()
    if not s or s.lower() in ['nan', 'null', 'none', '', 'n/a']:
        return pd.NaT
    digits = re.findall(r'\d+', s)
    if len(digits) < 3: return pd.NaT
    try:
        if len(digits[0]) == 4:
            year, month, day = int(digits[0]), int(digits[1]), int(digits[2])
        elif len(digits[2]) == 4:
            year = int(digits[2])
            if day_first:
                day, month = int(digits[0]), int(digits[1])
            else:
                month, day = int(digits[0]), int(digits[1])
        else:
            return pd.NaT
        return pd.Timestamp(year=year, month=month, day=day)
    except Exception:
        return pd.NaT

def get_value_on_or_before(df, date_col, value_col, target_date):
    valid = df[(df[date_col].notna()) & (df[date_col] <= target_date) & (df[value_col] > 0)]
    if valid.empty: return None, None
    idx = valid[date_col].idxmax()
    return valid.loc[idx, value_col], valid.loc[idx, date_col]

# =====================================================================
# CONTINUOUS DAILY TIMELINE FOR PEAK LOGIC (INTC * SBI)
# =====================================================================
def get_peak_in_range_daily(df_intc, intc_date_col, intc_val_col,
                            df_sbi, sbi_date_col, sbi_rate_col,
                            start_date, end_date):
    if pd.isna(start_date) or pd.isna(end_date) or start_date > end_date:
        return None, None, None

    timeline = pd.DataFrame({'Date': pd.date_range(start=start_date, end=end_date, freq='D')})

    df_intc_sorted = df_intc.sort_values(intc_date_col).drop_duplicates(subset=[intc_date_col]).dropna(subset=[intc_date_col])
    df_sbi_sorted = df_sbi.sort_values(sbi_date_col).drop_duplicates(subset=[sbi_date_col]).dropna(subset=[sbi_date_col])

    timeline = pd.merge_asof(timeline, df_intc_sorted[[intc_date_col, intc_val_col]],
                             left_on='Date', right_on=intc_date_col, direction='backward')
    timeline = pd.merge_asof(timeline, df_sbi_sorted[[sbi_date_col, sbi_rate_col]],
                             left_on='Date', right_on=sbi_date_col, direction='backward')

    timeline['Product'] = timeline[intc_val_col] * timeline[sbi_rate_col]
    timeline = timeline.dropna(subset=['Product'])

    if timeline.empty:
        return None, None, None

    idx_max = timeline['Product'].idxmax()
    peak_row = timeline.loc[idx_max]

    return peak_row['Date'], peak_row[intc_val_col], peak_row[sbi_rate_col]

def get_cg_sbi_rate(df_sbi_clean, date_val):
    if pd.isna(date_val): return None, None
    if date_val.month == 1:
        target_year, target_month = date_val.year - 1, 12
    else:
        target_year, target_month = date_val.year, date_val.month - 1
    subset = df_sbi_clean[(df_sbi_clean['_DATE_parsed'].dt.year == target_year) & 
                          (df_sbi_clean['_DATE_parsed'].dt.month == target_month)]
    if not subset.empty:
        max_dt = subset['_DATE_parsed'].max()
        val = subset[subset['_DATE_parsed'] == max_dt]['SBI_RATE'].iloc[0]
        return val, max_dt
    return None, None

def parse_date_for_df(val):
    if pd.isna(val) or val == 'N/A' or str(val).strip() == '': return pd.NaT
    if isinstance(val, pd.Timestamp): return val
    try: return pd.to_datetime(val)
    except: return pd.NaT

def format_date_str(val):
    if pd.isna(val): return 'N/A'
    return val.strftime('%m/%d/%Y')

def format_usd(val):
    if pd.isna(val) or val == 'N/A': return 'N/A'
    return f"${float(val):.2f}"

def round_qty(val):
    try: return int(round(float(val)))
    except (ValueError, TypeError): return val

# =====================================================================
# A2 CASH FILE PARSER (CSV Plain Text Support)
# =====================================================================
def load_cash_file(cash_path):
    if str(cash_path).strip().lower().endswith('.csv'):
        df = pd.read_csv(cash_path)
    else:
        df = pd.read_excel(cash_path)
    
    date_col = next((c for c in df.columns if 'date' in c.lower()), None)
    cash_col = next((c for c in df.columns if 'usd cash' in c.lower()), None)
    if not date_col or not cash_col: raise ValueError(f"Could not find 'Date' or 'USD Cash' column in {cash_path}")
    
    df['_Date'] = df[date_col].apply(lambda x: parse_date_ultra_flexible(x, day_first=False))
    df['_Cash'] = pd.to_numeric(df[cash_col], errors='coerce').fillna(0)
    
    return df[['_Date', '_Cash']].dropna(subset=['_Date']).sort_values('_Date')

# =====================================================================
# DATA PROCESSORS
# =====================================================================
def process_itr_data(year, pnl_path, hold_path, sbi_path, intc_path, out_dir):
    fa_start_date = pd.Timestamp(year=year, month=1, day=1)
    fa_target_date = pd.Timestamp(year=year, month=12, day=31)
    cg_start_date = pd.Timestamp(year=year, month=4, day=1)
    cg_end_date = pd.Timestamp(year=year+1, month=3, day=31)
    
    y_short = str(year)[-2:]
    next_y_short = str(year + 1)[-2:]
    
    df_sbi = pd.read_csv(sbi_path)
    df_intc = pd.read_csv(intc_path)
    sbi_date_col = find_column_flexibly(df_sbi, "DATE")
    sbi_rate_col = find_column_flexibly(df_sbi, "TT BUY")
    intc_date_col = find_column_flexibly(df_intc, "DATE")
    intc_close_col = find_column_flexibly(df_intc, "CLOSE")

    df_sbi['_DATE_parsed'] = df_sbi[sbi_date_col].apply(lambda x: parse_date_ultra_flexible(x, day_first=True))
    df_sbi['SBI_RATE'] = pd.to_numeric(df_sbi[sbi_rate_col], errors='coerce').fillna(0)
    df_sbi_clean = df_sbi.dropna(subset=['_DATE_parsed']).copy()
    df_sbi_clean = df_sbi_clean[df_sbi_clean['SBI_RATE'] > 0]
    
    df_intc['_DATE_parsed'] = df_intc[intc_date_col].apply(lambda x: parse_date_ultra_flexible(x, day_first=False))
    df_intc['INTC_CLOSE'] = pd.to_numeric(df_intc[intc_close_col], errors='coerce').fillna(0)

    raw_pnl_df = pd.read_excel(pnl_path)

    # --- Capital Gains ---
    cg_out_path = None
    if 'Quantity' in raw_pnl_df.columns:
        cg_pnl = raw_pnl_df.dropna(subset=['Quantity', 'Date Acquired', 'Date Sold']).copy()
        cg_pnl['Date Sold Parsed'] = cg_pnl['Date Sold'].apply(parse_date_for_df)
        cg_pnl['Date Acquired Parsed'] = cg_pnl['Date Acquired'].apply(parse_date_for_df)
        cg_pnl = cg_pnl[(cg_pnl['Date Sold Parsed'] >= cg_start_date) & (cg_pnl['Date Sold Parsed'] <= cg_end_date)]
        
        cg_output_rows = []
        for _, row in cg_pnl.iterrows():
            q = round_qty(row['Quantity'])
            acq = row['Date Acquired Parsed']
            sld = row['Date Sold Parsed']
            raw_acb = str(row.get('Adjusted Cost Basis', '0')).replace('$', '').replace(',', '')
            raw_proc = str(row.get('Total Proceeds', '0')).replace('$', '').replace(',', '')
            acb_num = pd.to_numeric(raw_acb, errors='coerce')
            proc_num = pd.to_numeric(raw_proc, errors='coerce')
            
            acq_rate, acq_dt = get_cg_sbi_rate(df_sbi_clean, acq)
            sld_rate, sld_dt = get_cg_sbi_rate(df_sbi_clean, sld)
            acb_inr = (acb_num * acq_rate) if pd.notna(acb_num) and acq_rate is not None else None
            proc_inr = (proc_num * sld_rate) if pd.notna(proc_num) and sld_rate is not None else None
            gain_loss = (proc_inr - acb_inr) if (acb_inr is not None and proc_inr is not None) else None
            
            cg_output_rows.append({
                'Plan Type': row.get('Plan Type', ''),
                'Capital Gains Status': row.get('Capital Gains Status', ''),
                'Quantity': q,
                'Date Acquired': acq.strftime('%d/%m/%Y') if pd.notna(acq) else 'N/A',
                'SBI TT DATE INR - Date Acquired': acq_dt.strftime('%d/%m/%Y') if acq_dt is not None else 'N/A',
                'SBI TT RATE INR - Date Acquired': acq_rate if acq_rate is not None else 'N/A',
                'Adjusted Cost Basis': format_usd(acb_num),
                'Adjusted Cost Basis - INR': f"{acb_inr:.2f}" if acb_inr is not None else 'N/A',
                'Date Sold': sld.strftime('%d/%m/%Y') if pd.notna(sld) else 'N/A',
                'SBI TT DATE INR - Date Sold': sld_dt.strftime('%d/%m/%Y') if sld_dt is not None else 'N/A',
                'SBI TT RATE INR - Date Sold': sld_rate if sld_rate is not None else 'N/A',
                'Total Proceeds': format_usd(proc_num),
                'Total Proceeds - INR': f"{proc_inr:.2f}" if proc_inr is not None else 'N/A',
                'Gain/Loss - INR Per Sale': f"{gain_loss:.2f}" if gain_loss is not None else 'N/A'
            })
        cg_df = pd.DataFrame(cg_output_rows)
        cg_out_path = os.path.join(out_dir, f"CG_ITR_TY_{y_short}_{next_y_short}.csv")
        cg_df.to_csv(cg_out_path, index=False, encoding='utf-8-sig')

    # --- Foreign Assets ---
    template_cols = [
        "Quantity",
        "Date Acquired (mm/dd/yyyy)",
        "Date Acquired (dd/mm/yyyy)",
        "SBI TT DATE INR - Date Acquired",
        "SBI TT RATE INR - Date Acquired",
        "Adjusted Cost Basis",
        "Adjusted Cost Basis - INR",
        "Date Sold (mm/dd/yyyy)",
        "Date Sold (dd/mm/yyyy)",
        "SBI TT DATE INR - Date Sold",
        "SBI TT RATE INR - Date Sold",
        "Total Proceeds",
        "Total Proceeds - INR",
        "Sold/UnSold",
        "31-Dec SBI TT BUY RATE",
        "31-Dec INTC VALUE",
        "Closing Value",
        "Peak Date",
        "Peak Date SBI TT BUY RATE",
        "Peak Date INTC Value",
        "Peak Value"
    ]

    top_rows = [
        ["Year", str(year)],
        []
    ]

    fa_df = pd.DataFrame(columns=template_cols)
    
    # 1. Sold Items
    if 'Quantity' in raw_pnl_df.columns:
        fa_pnl = raw_pnl_df.dropna(subset=['Quantity', 'Date Acquired', 'Date Sold']).copy()
        fa_pnl['Date Sold Parsed'] = fa_pnl['Date Sold'].apply(parse_date_for_df)
        fa_pnl = fa_pnl[(fa_pnl['Date Sold Parsed'] >= fa_start_date) & (fa_pnl['Date Sold Parsed'] <= fa_target_date)]
        
        rows_to_add = []
        for _, row in fa_pnl.iterrows():
            new_row = {col: '' for col in template_cols}
            q = round_qty(row['Quantity'])
            acq = parse_date_for_df(row['Date Acquired'])
            sld = row['Date Sold Parsed'] 
            acb = row['Adjusted Cost Basis']
            proc = row['Total Proceeds']
            if 'Quantity' in new_row: new_row['Quantity'] = q
            if 'Date Acquired (mm/dd/yyyy)' in new_row: new_row['Date Acquired (mm/dd/yyyy)'] = format_date_str(acq)
            if 'Adjusted Cost Basis' in new_row: new_row['Adjusted Cost Basis'] = format_usd(acb)
            if 'Date Sold (mm/dd/yyyy)' in new_row: new_row['Date Sold (mm/dd/yyyy)'] = format_date_str(sld)
            if 'Total Proceeds' in new_row: new_row['Total Proceeds'] = format_usd(proc)
            if 'Sold/UnSold' in new_row: new_row['Sold/UnSold'] = 'Sold'
            rows_to_add.append(new_row)
        if rows_to_add: fa_df = pd.concat([fa_df, pd.DataFrame(rows_to_add)], ignore_index=True)

    # 2. Unsold Items
    try:
        hold_df = pd.read_excel(hold_path, sheet_name='Sellable')
        if 'Sellable Qty.' in hold_df.columns:
            hold_df = hold_df.dropna(subset=['Sellable Qty.', 'Date Acquired']).copy()
            hold_df['Date Acquired'] = hold_df['Date Acquired'].apply(parse_date_for_df)
            hold_df = hold_df[hold_df['Date Acquired'] <= fa_target_date]
            rows_to_add = []
            for _, row in hold_df.iterrows():
                new_row = {col: '' for col in template_cols}
                q = round_qty(row['Sellable Qty.'])
                acq = row['Date Acquired']
                cps = pd.to_numeric(row.get('Est. Cost Basis (per share):', 0), errors='coerce')
                if pd.isna(cps): cps = 0
                acb = float(q) * float(cps) 
                if 'Quantity' in new_row: new_row['Quantity'] = q
                if 'Date Acquired (mm/dd/yyyy)' in new_row: new_row['Date Acquired (mm/dd/yyyy)'] = format_date_str(acq)
                if 'Adjusted Cost Basis' in new_row: new_row['Adjusted Cost Basis'] = format_usd(acb)
                if 'Date Sold (mm/dd/yyyy)' in new_row: new_row['Date Sold (mm/dd/yyyy)'] = 'N/A'
                if 'Total Proceeds' in new_row: new_row['Total Proceeds'] = 'N/A'
                if 'Sold/UnSold' in new_row: new_row['Sold/UnSold'] = 'UnSold'
                rows_to_add.append(new_row)
            if rows_to_add: fa_df = pd.concat([fa_df, pd.DataFrame(rows_to_add)], ignore_index=True)
    except Exception as e:
        print(f"Warning: Could not process Holding statement properly. Error: {e}")

    # 3. RECONSTRUCT YEAR-END UNSOLD HOLDINGS FROM EXTENDED P&L (BLIND ADD)
    if 'Quantity' in raw_pnl_df.columns:
        ext_pnl = raw_pnl_df.dropna(subset=['Quantity', 'Date Acquired', 'Date Sold']).copy()
        ext_pnl['Date Acq Parsed'] = ext_pnl['Date Acquired'].apply(parse_date_for_df)
        ext_pnl['Date Sold Parsed'] = ext_pnl['Date Sold'].apply(parse_date_for_df)
        
        mask_unsold_ext = (ext_pnl['Date Acq Parsed'] <= fa_target_date) & (ext_pnl['Date Sold Parsed'] > fa_target_date)
        ext_unsold_df = ext_pnl[mask_unsold_ext]
        
        rows_to_add = []
        for _, row in ext_unsold_df.iterrows():
            new_row = {col: '' for col in template_cols}
            q = round_qty(row['Quantity'])
            acq = row['Date Acq Parsed']
            acb = row['Adjusted Cost Basis']
            
            if 'Quantity' in new_row: new_row['Quantity'] = q
            if 'Date Acquired (mm/dd/yyyy)' in new_row: new_row['Date Acquired (mm/dd/yyyy)'] = format_date_str(acq)
            if 'Adjusted Cost Basis' in new_row: new_row['Adjusted Cost Basis'] = format_usd(acb)
            if 'Date Sold (mm/dd/yyyy)' in new_row: new_row['Date Sold (mm/dd/yyyy)'] = 'N/A'
            if 'Total Proceeds' in new_row: new_row['Total Proceeds'] = 'N/A'
            if 'Sold/UnSold' in new_row: new_row['Sold/UnSold'] = 'UnSold'
            rows_to_add.append(new_row)
        if rows_to_add:
            fa_df = pd.concat([fa_df, pd.DataFrame(rows_to_add)], ignore_index=True)

    sbi_rate_val, _ = get_value_on_or_before(df_sbi, '_DATE_parsed', 'SBI_RATE', fa_target_date)
    intc_close_val, _ = get_value_on_or_before(df_intc, '_DATE_parsed', 'INTC_CLOSE', fa_target_date)
    if sbi_rate_val is None or intc_close_val is None: raise ValueError(f"No valid SBI or INTC rate found on/before 31-Dec-{year}.")

    status_col = "Sold/UnSold"
    qty_col = "Quantity"
    date_acq_col = "Date Acquired (mm/dd/yyyy)"
    date_sold_col = "Date Sold (mm/dd/yyyy)"
    acb_usd_col = "Adjusted Cost Basis"
    acb_inr_col = "Adjusted Cost Basis - INR"
    proceeds_usd_col = "Total Proceeds"
    proceeds_inr_col = "Total Proceeds - INR"
    sbi_acq_date_col = "SBI TT DATE INR - Date Acquired"
    sbi_acq_rate_col = "SBI TT RATE INR - Date Acquired"
    sbi_sold_date_col = "SBI TT DATE INR - Date Sold"
    sbi_sold_rate_col = "SBI TT RATE INR - Date Sold"
    out_rate_col = "31-Dec SBI TT BUY RATE"
    out_intc_col = "31-Dec INTC VALUE"
    out_closing_col = "Closing Value"
    out_peak_date_col = "Peak Date"
    out_peak_sbi_col = "Peak Date SBI TT BUY RATE"
    out_peak_intc_col = "Peak Date INTC Value"
    out_peak_value_col = "Peak Value"
    date_acq_ddmm_col = "Date Acquired (dd/mm/yyyy)"
    date_sold_ddmm_col = "Date Sold (dd/mm/yyyy)"

    def _mmddyyyy_to_ddmmyyyy(val):
        d = parse_date_ultra_flexible(val, day_first=False)
        if pd.isna(d): return val
        return d.strftime('%d/%m/%Y')

    fa_df[date_acq_ddmm_col] = fa_df[date_acq_col].apply(_mmddyyyy_to_ddmmyyyy)
    fa_df[date_sold_ddmm_col] = fa_df[date_sold_col].apply(_mmddyyyy_to_ddmmyyyy)

    for col in (sbi_acq_date_col, sbi_acq_rate_col, sbi_sold_date_col, sbi_sold_rate_col, acb_inr_col, proceeds_inr_col,
                out_rate_col, out_intc_col, out_closing_col, out_peak_date_col, out_peak_sbi_col, out_peak_intc_col, out_peak_value_col):
        fa_df[col] = fa_df[col].astype(object)
        fa_df[col] = ''

    for idx, row in fa_df.iterrows():
        status = str(row[status_col]).strip().lower()
        qty = pd.to_numeric(str(row[qty_col]).replace(',', ''), errors='coerce')

        if status == 'unsold':
            fa_df.at[idx, out_rate_col] = sbi_rate_val
            fa_df.at[idx, out_intc_col] = intc_close_val
            if pd.notna(qty):
                fa_df.at[idx, out_closing_col] = round(qty * sbi_rate_val * intc_close_val, 2)
            else:
                fa_df.at[idx, out_closing_col] = None
        else:
            fa_df.at[idx, out_rate_col] = 'N/A'
            fa_df.at[idx, out_intc_col] = 'N/A'
            fa_df.at[idx, out_closing_col] = 0

        acq_date = parse_date_ultra_flexible(row[date_acq_col], day_first=False)
        if pd.notna(acq_date):
            acq_rate, acq_matched = get_value_on_or_before(df_sbi, '_DATE_parsed', 'SBI_RATE', acq_date)
            if acq_rate is not None:
                fa_df.at[idx, sbi_acq_rate_col] = acq_rate
                fa_df.at[idx, sbi_acq_date_col] = acq_matched.strftime('%d/%m/%Y')
                acb_usd = pd.to_numeric(str(row[acb_usd_col]).replace('$', '').replace(',', ''), errors='coerce')
                if pd.notna(acb_usd):
                    fa_df.at[idx, acb_inr_col] = round(acb_usd * acq_rate, 2)

        if status == 'sold':
            sold_date = parse_date_ultra_flexible(row[date_sold_col], day_first=False)
            if pd.notna(sold_date):
                sold_rate, sold_matched = get_value_on_or_before(df_sbi, '_DATE_parsed', 'SBI_RATE', sold_date)
                if sold_rate is not None:
                    fa_df.at[idx, sbi_sold_rate_col] = sold_rate
                    fa_df.at[idx, sbi_sold_date_col] = sold_matched.strftime('%d/%m/%Y')
                    proceeds_usd = pd.to_numeric(str(row[proceeds_usd_col]).replace('$', '').replace(',', ''), errors='coerce')
                    if pd.notna(proceeds_usd):
                        fa_df.at[idx, proceeds_inr_col] = round(proceeds_usd * sold_rate, 2)
        else:
            fa_df.at[idx, sbi_sold_date_col] = 'N/A'
            fa_df.at[idx, sbi_sold_rate_col] = 'N/A'
            fa_df.at[idx, proceeds_inr_col] = 'N/A'

        start_date = parse_date_ultra_flexible(row[date_acq_col], day_first=False)
        if pd.notna(start_date) and start_date < fa_start_date:
            start_date = fa_start_date

        if status == 'sold':
            end_date = parse_date_ultra_flexible(row[date_sold_col], day_first=False)
        else:
            end_date = fa_target_date

        if pd.notna(start_date) and pd.notna(end_date):
            peak_date, peak_intc, peak_sbi = get_peak_in_range_daily(
                df_intc, '_DATE_parsed', 'INTC_CLOSE',
                df_sbi, '_DATE_parsed', 'SBI_RATE',
                start_date, end_date)
            if peak_date is not None:
                fa_df.at[idx, out_peak_date_col] = peak_date.strftime('%d/%m/%Y')
                fa_df.at[idx, out_peak_intc_col] = peak_intc
                fa_df.at[idx, out_peak_sbi_col] = peak_sbi
                if pd.notna(qty) and peak_sbi is not None and peak_intc is not None:
                    fa_df.at[idx, out_peak_value_col] = round(qty * peak_sbi * peak_intc, 2)

    # Force strict output formatting
    fa_df = fa_df[template_cols]

    fa_out_path = os.path.join(out_dir, f"FA_ITR_TY_{y_short}_{next_y_short}.csv")
    with open(fa_out_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        for r in top_rows: writer.writerow(r)
        writer.writerow(template_cols)
    fa_df.to_csv(fa_out_path, mode='a', index=False, header=False)
    
    return cg_out_path, fa_out_path

def process_a2_data(year, fa_path, cash_path, sbi_path, intc_path, out_dir):
    df_sbi = pd.read_csv(sbi_path)
    sbi_date_col = find_column_flexibly(df_sbi, "DATE")
    sbi_rate_col = find_column_flexibly(df_sbi, "TT BUY")
    df_sbi['_DATE_parsed'] = df_sbi[sbi_date_col].apply(lambda x: parse_date_ultra_flexible(x, day_first=True))
    df_sbi['SBI_RATE'] = pd.to_numeric(df_sbi[sbi_rate_col], errors='coerce').fillna(0)
    df_sbi = df_sbi[df_sbi['SBI_RATE'] > 0].dropna(subset=['_DATE_parsed']).sort_values('_DATE_parsed')

    df_intc = pd.read_csv(intc_path)
    intc_date_col = find_column_flexibly(df_intc, "DATE")
    intc_close_col = find_column_flexibly(df_intc, "CLOSE")
    df_intc['_DATE_parsed'] = df_intc[intc_date_col].apply(lambda x: parse_date_ultra_flexible(x, day_first=False))
    df_intc['INTC_CLOSE'] = pd.to_numeric(df_intc[intc_close_col], errors='coerce').fillna(0)
    df_intc = df_intc[df_intc['INTC_CLOSE'] > 0].dropna(subset=['_DATE_parsed']).sort_values('_DATE_parsed')

    fa_df = pd.read_csv(fa_path, skiprows=2, keep_default_na=False)
    cash_df = load_cash_file(cash_path)
    
    acq_col = find_column_flexibly(fa_df, "Date Acquired (mm/dd/yyyy)")
    sold_col = find_column_flexibly(fa_df, "Date Sold (mm/dd/yyyy)")
    qty_col = find_column_flexibly(fa_df, "Quantity")
    proceeds_col = find_column_flexibly(fa_df, "Total Proceeds - INR")
    
    fa_df['Acq_Date'] = fa_df[acq_col].apply(lambda x: parse_date_ultra_flexible(x, day_first=False))
    fa_df['Sold_Date'] = fa_df[sold_col].apply(lambda x: parse_date_ultra_flexible(x, day_first=False))
    fa_df['Qty'] = pd.to_numeric(fa_df[qty_col].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
    fa_df['Proceeds'] = pd.to_numeric(fa_df[proceeds_col].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
    sum_proceeds = fa_df['Proceeds'].sum()

    dates = pd.date_range(start=f'{year}-01-01', end=f'{year}-12-31', freq='D')
    out = pd.DataFrame({'Date': dates})

    out = pd.merge_asof(out, cash_df, left_on='Date', right_on='_Date', direction='backward')
    out['_Cash'] = out['_Cash'].fillna(0)
    out = pd.merge_asof(out, df_intc[['_DATE_parsed', 'INTC_CLOSE']], left_on='Date', right_on='_DATE_parsed', direction='backward')
    out = pd.merge_asof(out, df_sbi[['_DATE_parsed', 'SBI_RATE']], left_on='Date', right_on='_DATE_parsed', direction='backward')

    def get_qty(d):
        mask = (fa_df['Acq_Date'] <= d) & ((fa_df['Sold_Date'] > d) | (fa_df['Sold_Date'].isna()))
        return fa_df.loc[mask, 'Qty'].sum()
        
    out['Daily_Qty'] = out['Date'].apply(get_qty)
    out['Stocks_Value_USD'] = out['Daily_Qty'] * out['INTC_CLOSE']
    out['Total_Acc_USD'] = out['Stocks_Value_USD'] + out['_Cash']
    out['Total_INR'] = out['Total_Acc_USD'] * out['SBI_RATE']

    peak_inr = out['Total_INR'].max()
    dec31_inr = out.loc[out['Date'] == f'{year}-12-31', 'Total_INR'].values[0]

    # Embedded A2 Template
    template_lines = [
        f"Year,{year},,,,\n",
        f"Peak INR (Max of Col. F),{peak_inr:.2f},,,,\n",
        f"31-Dec INR,{dec31_inr:.2f},,,,\n",
        f"Amount (from Other Sheet : Sum of all Sales Proceedings in INR Value),{sum_proceeds:.2f},,,,\n",
        "\n",
        "Date,Stocks Value (USD),Cash Value (USD),Total Account Value (USD),SBI TT BUY RATE,Total Value (INR)\n"
    ]

    y_short = str(year)[-2:]
    next_y_short = str(year + 1)[-2:]
    
    a2_out_path = os.path.join(out_dir, f"A2_ITR_TY_{y_short}_{next_y_short}.csv")

    with open(a2_out_path, 'w', encoding='utf-8-sig') as f:
        for line in template_lines: f.write(line)
        for _, row in out.iterrows():
            d_str = row['Date'].strftime('%d/%m/%Y')
            stk_val = f"{row['Stocks_Value_USD']:.2f}"
            csh_val = f"{row['_Cash']:.2f}"
            tot_usd = f"{row['Total_Acc_USD']:.2f}"
            sbi_rate = f"{row['SBI_RATE']}"
            tot_inr = f"{row['Total_INR']:.2f}"
            f.write(f"{d_str},{stk_val},{csh_val},{tot_usd},{sbi_rate},{tot_inr}\n")
            
    return a2_out_path

# =====================================================================
# PYSIDE6 NATIVE GUI 
# =====================================================================
class ITRApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ITR Master Processor - CG & FA")
        self.setMinimumWidth(800)
        self.initUI()

    def initUI(self):
        main_layout = QVBoxLayout()
        main_layout.setSpacing(15)

        # --- IMPORTANT DISCLAIMER ---
        warning_lbl = QLabel("IMPORTANT: To prevent duplicate Unsold entries, please ensure your "
                             "E-Trade P&L and Holdings statements were downloaded on the SAME DAY.")
        warning_lbl.setStyleSheet("color: red; font-weight: bold; font-size: 13px; padding: 5px;")
        warning_lbl.setWordWrap(True)
        main_layout.addWidget(warning_lbl)

        # --- Group 1: Core Inputs ---
        g1 = QGroupBox("1. Mandatory Base Files")
        g1.setStyleSheet("QGroupBox { font-weight: bold; }")
        l1 = QVBoxLayout()
        
        row = QHBoxLayout()
        lbl = QLabel("Target Year (YYYY-YYYY):")
        lbl.setMinimumWidth(200) 
        self.year_entry = QLineEdit()
        self.year_entry.setPlaceholderText("e.g. 2026-2027")
        row.addWidget(lbl)
        row.addWidget(self.year_entry)
        l1.addLayout(row)

        self.pnl_entry, _ = self.add_file_row(l1, "P&L Excel File:")
        self.hold_entry, _ = self.add_file_row(l1, "Holdings Excel File:")
        g1.setLayout(l1)
        main_layout.addWidget(g1)

        # --- Group 2: Rate Files ---
        g2 = QGroupBox("2. Mandatory Rate Files")
        g2.setStyleSheet("QGroupBox { font-weight: bold; }")
        l2 = QVBoxLayout()
        self.intc_entry, _ = self.add_file_row(l2, "INTC Rates CSV:")
        self.sbi_entry, _ = self.add_file_row(l2, "SBI Rates CSV:")
        g2.setLayout(l2)
        main_layout.addWidget(g2)

        # --- Group 3: Optional A2 ---
        g3 = QGroupBox("3. Optional A2 (Cash) Report")
        g3.setStyleSheet("QGroupBox { font-weight: bold; }")
        l3 = QVBoxLayout()
        
        self.a2_checkbox = QCheckBox("Generate Optional A2 (Cash) Report")
        self.a2_checkbox.stateChanged.connect(self.toggle_a2)
        l3.addWidget(self.a2_checkbox)

        self.cash_entry, self.cash_btn = self.add_file_row(l3, "Cash CSV/Excel File:")
        g3.setLayout(l3)
        main_layout.addWidget(g3)

        # --- Group 4: Output Destination ---
        g4 = QGroupBox("4. Output Destination")
        g4.setStyleSheet("QGroupBox { font-weight: bold; }")
        l4 = QVBoxLayout()
        self.out_dir_entry = self.add_dir_row(l4, "Output Folder:\n(Leave blank for app dir)")
        g4.setLayout(l4)
        main_layout.addWidget(g4)

        # --- Action Buttons ---
        btn_layout = QHBoxLayout()
        
        self.reset_btn = QPushButton("Reset Fields")
        self.reset_btn.setMinimumHeight(45)
        self.reset_btn.setStyleSheet("QPushButton { font-weight: bold; font-size: 14px; }")
        self.reset_btn.clicked.connect(self.reset_fields)
        
        self.run_btn = QPushButton("Run Processor")
        self.run_btn.setMinimumHeight(45)
        self.run_btn.setStyleSheet("QPushButton { font-weight: bold; font-size: 14px; }")
        self.run_btn.clicked.connect(self.run_process)
        
        btn_layout.addWidget(self.reset_btn)
        btn_layout.addWidget(self.run_btn)
        
        main_layout.addLayout(btn_layout)

        self.setLayout(main_layout)
        self.toggle_a2()

    def add_file_row(self, layout, label_text, default_text=""):
        row = QHBoxLayout()
        lbl = QLabel(label_text)
        lbl.setMinimumWidth(200) 
        entry = QLineEdit(default_text)
        btn = QPushButton("Browse")
        btn.clicked.connect(lambda *args, e=entry: self.browse_file(e))
        row.addWidget(lbl)
        row.addWidget(entry)
        row.addWidget(btn)
        layout.addLayout(row)
        return entry, btn

    def add_dir_row(self, layout, label_text):
        row = QHBoxLayout()
        lbl = QLabel(label_text)
        lbl.setMinimumWidth(200)
        entry = QLineEdit()
        btn = QPushButton("Browse Folder")
        btn.clicked.connect(lambda *args, e=entry: self.browse_dir(e))
        row.addWidget(lbl)
        row.addWidget(entry)
        row.addWidget(btn)
        layout.addLayout(row)
        return entry

    def browse_file(self, entry_widget):
        fname, _ = QFileDialog.getOpenFileName(self, "Select File")
        if fname:
            entry_widget.setText(fname)

    def browse_dir(self, entry_widget):
        dname = QFileDialog.getExistingDirectory(self, "Select Directory")
        if dname:
            entry_widget.setText(dname)

    def toggle_a2(self):
        is_checked = self.a2_checkbox.isChecked()
        self.cash_entry.setEnabled(is_checked)
        self.cash_btn.setEnabled(is_checked)
        
    def reset_fields(self):
        self.year_entry.clear()
        self.pnl_entry.clear()
        self.hold_entry.clear()
        self.intc_entry.clear()
        self.sbi_entry.clear()
        self.cash_entry.clear()
        self.out_dir_entry.clear()
        self.a2_checkbox.setChecked(False)

    def run_process(self):
        year_str = self.year_entry.text().strip()
        pnl = self.pnl_entry.text().strip()
        hold = self.hold_entry.text().strip()
        intc = self.intc_entry.text().strip()
        sbi = self.sbi_entry.text().strip()
        out_dir = self.out_dir_entry.text().strip() or os.getcwd()

        if not re.match(r'^\d{4}-\d{4}$', year_str):
            QMessageBox.warning(self, "Invalid Year Format", "Please enter the Target Year in the exact format YYYY-YYYY (e.g., 2026-2027).")
            return

        if not all([pnl, hold, intc, sbi]):
            QMessageBox.warning(self, "Missing Information", "Please ensure all mandatory base files and rate files are selected.")
            return

        if self.a2_checkbox.isChecked():
            if not self.cash_entry.text().strip():
                QMessageBox.warning(self, "Missing Information", "You checked A2 Generation but left the Cash CSV/Excel file blank.")
                return

        try:
            target_year = int(year_str.split('-')[0])
            
            cg_path, fa_path = process_itr_data(target_year, pnl, hold, sbi, intc, out_dir)
            
            generated_files = []
            if cg_path: generated_files.append(cg_path)
            if fa_path: generated_files.append(fa_path)

            if self.a2_checkbox.isChecked():
                a2_path = process_a2_data(target_year, fa_path, self.cash_entry.text().strip(), sbi, intc, out_dir)
                if a2_path: generated_files.append(a2_path)

            msg = "Data processed successfully!\n\nFiles generated in:\n" + out_dir + "\n\n"
            for f in generated_files:
                msg += f"• {os.path.basename(f)}\n"
            QMessageBox.information(self, "Success", msg)

        except Exception as e:
            QMessageBox.critical(self, "Execution Error", f"An error occurred during processing:\n\n{str(e)}")

if __name__ == "__main__":
    import signal
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    
    app = QApplication(sys.argv)
    window = ITRApp()
    window.show()
    sys.exit(app.exec())