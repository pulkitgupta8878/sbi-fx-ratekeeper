import os
import re
import sys
import csv
import pandas as pd
from pathlib import Path


def find_column_flexibly(df, target_name):
    target_clean = target_name.strip().upper()
    for col in df.columns:
        if str(col).strip().upper() == target_clean:
            return col
    return None


def parse_date_ultra_flexible(val, day_first=False):
    """Extracts date numbers securely, defaulting to MM/DD/YYYY (day_first=False) for E-Trade files."""
    s = str(val).strip()
    if not s or s.lower() in ['nan', 'null', 'none', '', 'n/a']:
        return pd.NaT

    digits = re.findall(r'\d+', s)
    if len(digits) < 3:
        return pd.NaT

    try:
        if len(digits[0]) == 4:  # YYYY/MM/DD
            year, month, day = int(digits[0]), int(digits[1]), int(digits[2])
        elif len(digits[2]) == 4:  # DD/MM/YYYY or MM/DD/YYYY
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
    valid = df[
        (df[date_col].notna())
        & (df[date_col] <= target_date)
        & (df[value_col] > 0)
    ]
    if valid.empty:
        return None, None
    idx = valid[date_col].idxmax()
    return valid.loc[idx, value_col], valid.loc[idx, date_col]


# =====================================================================
# CONTINUOUS DAILY TIMELINE FOR PEAK LOGIC (INTC * SBI)
# =====================================================================
def get_peak_in_range_daily(df_intc, intc_date_col, intc_val_col,
                            df_sbi, sbi_date_col, sbi_rate_col,
                            start_date, end_date):
    """
    Finds the absolute maximum of (INTC * SBI) by creating a continuous, 
    unbroken daily timeline from start_date to end_date, forward-filling 
    prices for weekends and holidays using the last available rates.
    """
    if pd.isna(start_date) or pd.isna(end_date) or start_date > end_date:
        return None, None, None

    # Create unbroken daily timeline
    timeline = pd.DataFrame({'Date': pd.date_range(start=start_date, end=end_date, freq='D')})

    # Ensure source dataframes are sorted and unique for asof merge
    df_intc_sorted = df_intc.sort_values(intc_date_col).drop_duplicates(subset=[intc_date_col]).dropna(subset=[intc_date_col])
    df_sbi_sorted = df_sbi.sort_values(sbi_date_col).drop_duplicates(subset=[sbi_date_col]).dropna(subset=[sbi_date_col])

    # Backward merge to pull the latest available rate for every single calendar day
    timeline = pd.merge_asof(timeline, df_intc_sorted[[intc_date_col, intc_val_col]],
                             left_on='Date', right_on=intc_date_col, direction='backward')
    timeline = pd.merge_asof(timeline, df_sbi_sorted[[sbi_date_col, sbi_rate_col]],
                             left_on='Date', right_on=sbi_date_col, direction='backward')

    # Calculate combined product (INR value per share)
    timeline['Product'] = timeline[intc_val_col] * timeline[sbi_rate_col]
    
    # Drop any days before we have valid rates
    timeline = timeline.dropna(subset=['Product'])

    if timeline.empty:
        return None, None, None

    # Find the exact date representing the absolute peak product
    idx_max = timeline['Product'].idxmax()
    peak_row = timeline.loc[idx_max]

    return peak_row['Date'], peak_row[intc_val_col], peak_row[sbi_rate_col]


def get_cg_sbi_rate(df_sbi_clean, date_val):
    if pd.isna(date_val):
        return None, None
    
    if date_val.month == 1:
        target_year, target_month = date_val.year - 1, 12
    else:
        target_year, target_month = date_val.year, date_val.month - 1
        
    subset = df_sbi_clean[
        (df_sbi_clean['_DATE_parsed'].dt.year == target_year) & 
        (df_sbi_clean['_DATE_parsed'].dt.month == target_month)
    ]
    
    if not subset.empty:
        max_dt = subset['_DATE_parsed'].max()
        val = subset[subset['_DATE_parsed'] == max_dt]['SBI_RATE'].iloc[0]
        return val, max_dt
    return None, None


# Reverted to reliable parser for P&L to prevent blank CG/FA rows
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
    try:
        return int(round(float(val)))
    except (ValueError, TypeError):
        return val


# =====================================================================
# A2 CASH FILE PARSER (Cleaned up for CSV Plain Text)
# =====================================================================
def load_cash_file(cash_path):
    """Loads cash file, supporting CSVs to cleanly enforce MM/DD/YYYY without Excel corruption."""
    if str(cash_path).strip().lower().endswith('.csv'):
        df = pd.read_csv(cash_path)
    else:
        df = pd.read_excel(cash_path)
    
    date_col = next((c for c in df.columns if 'date' in c.lower()), None)
    cash_col = next((c for c in df.columns if 'usd cash' in c.lower()), None)
    
    if not date_col or not cash_col:
        raise ValueError(f"Could not find 'Date' or 'USD Cash' column in {cash_path}")
        
    # Since we are using CSV, we bypass Excel serial dates entirely and apply the strict regex 
    df['_Date'] = df[date_col].apply(lambda x: parse_date_ultra_flexible(x, day_first=False))
        
    df['_Cash'] = pd.to_numeric(df[cash_col], errors='coerce').fillna(0)
    
    return df[['_Date', '_Cash']].dropna(subset=['_Date']).sort_values('_Date')


# =====================================================================
# A2 PROCESSOR
# =====================================================================
def process_a2_data(year, fa_path, cash_path, sbi_path, intc_path):
    print(f"\nProcessing A2 (Cash & Active Balances) for {year}...")
    
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
    out_file = f"A2_ITR_TY_{y_short}_{next_y_short}.csv"

    with open(out_file, 'w', encoding='utf-8-sig') as f:
        for line in template_lines:
            f.write(line)
        for _, row in out.iterrows():
            d_str = row['Date'].strftime('%d/%m/%Y')
            stk_val = f"{row['Stocks_Value_USD']:.2f}"
            csh_val = f"{row['_Cash']:.2f}"
            tot_usd = f"{row['Total_Acc_USD']:.2f}"
            sbi_rate = f"{row['SBI_RATE']}"
            tot_inr = f"{row['Total_INR']:.2f}"
            f.write(f"{d_str},{stk_val},{csh_val},{tot_usd},{sbi_rate},{tot_inr}\n")
            
    print(f"-> Generated {out_file}")


# =====================================================================
# MAIN FA AND CG PROCESSOR
# =====================================================================
def process_itr_data(year, pnl_path, hold_path, sbi_path, intc_path):
    for path in [pnl_path, hold_path, sbi_path, intc_path]:
        if not os.path.exists(path):
            print(f"Error: Ensure '{path}' is present.")
            return None

    fa_start_date = pd.Timestamp(year=year, month=1, day=1)
    fa_target_date = pd.Timestamp(year=year, month=12, day=31)
    
    cg_start_date = pd.Timestamp(year=year, month=4, day=1)
    cg_end_date = pd.Timestamp(year=year+1, month=3, day=31)
    
    print(f"\nExecuting Unified Processing for Target Year: {year}")

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

    # -----------------------------------------------------------
    # SECTION A: CAPITAL GAINS
    # -----------------------------------------------------------
    if 'Quantity' in raw_pnl_df.columns:
        print("Processing Capital Gains (CG)...")
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
        cg_out_file = f"CG_ITR_TY_{y_short}_{next_y_short}.csv"
        cg_df.to_csv(cg_out_file, index=False, encoding='utf-8-sig')
        print(f"-> Generated {cg_out_file}")

    # -----------------------------------------------------------
    # SECTION B: FOREIGN ASSETS (A3)
    # -----------------------------------------------------------
    print("Processing Foreign Assets (FA A3)...")
    
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
    
    # 1. Processing SOLD items during the target year
    if 'Quantity' in raw_pnl_df.columns:
        fa_pnl = raw_pnl_df.dropna(subset=['Quantity', 'Date Acquired', 'Date Sold']).copy()
        fa_pnl['Date Sold Parsed'] = fa_pnl['Date Sold'].apply(parse_date_for_df)
        
        fa_pnl = fa_pnl[
            (fa_pnl['Date Sold Parsed'] >= fa_start_date) & 
            (fa_pnl['Date Sold Parsed'] <= fa_target_date)
        ]
        
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
        if rows_to_add:
            fa_df = pd.concat([fa_df, pd.DataFrame(rows_to_add)], ignore_index=True)

    # 2. Processing UNSOLD items from Holdings statement
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
                cost_per_share = pd.to_numeric(row.get('Est. Cost Basis (per share):', 0), errors='coerce')
                if pd.isna(cost_per_share): cost_per_share = 0
                acb = float(q) * float(cost_per_share) 
                
                if 'Quantity' in new_row: new_row['Quantity'] = q
                if 'Date Acquired (mm/dd/yyyy)' in new_row: new_row['Date Acquired (mm/dd/yyyy)'] = format_date_str(acq)
                if 'Adjusted Cost Basis' in new_row: new_row['Adjusted Cost Basis'] = format_usd(acb)
                if 'Date Sold (mm/dd/yyyy)' in new_row: new_row['Date Sold (mm/dd/yyyy)'] = 'N/A'
                if 'Total Proceeds' in new_row: new_row['Total Proceeds'] = 'N/A'
                if 'Sold/UnSold' in new_row: new_row['Sold/UnSold'] = 'UnSold'
                
                rows_to_add.append(new_row)
                
            if rows_to_add:
                fa_df = pd.concat([fa_df, pd.DataFrame(rows_to_add)], ignore_index=True)
    except Exception as e:
        print(f"Warning: Could not process Holding statement properly. Error: {e}")

    # =====================================================================
    # RECONSTRUCT YEAR-END UNSOLD HOLDINGS FROM EXTENDED P&L (BLIND ADD)
    # =====================================================================
    if 'Quantity' in raw_pnl_df.columns:
        ext_pnl = raw_pnl_df.dropna(subset=['Quantity', 'Date Acquired', 'Date Sold']).copy()
        ext_pnl['Date Acq Parsed'] = ext_pnl['Date Acquired'].apply(parse_date_for_df)
        ext_pnl['Date Sold Parsed'] = ext_pnl['Date Sold'].apply(parse_date_for_df)
        
        # Lots acquired on or before Dec 31, but sold AFTER Dec 31
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
            print(f"-> Notice: Appended {len(rows_to_add)} 'UnSold' lot(s) from extended P&L (held on Dec 31, sold post-year-end).")

    # Final calculations for populated fa_df
    sbi_rate_val, sbi_matched = get_value_on_or_before(df_sbi, '_DATE_parsed', 'SBI_RATE', fa_target_date)
    intc_close_val, intc_matched = get_value_on_or_before(df_intc, '_DATE_parsed', 'INTC_CLOSE', fa_target_date)

    if sbi_rate_val is None or intc_close_val is None:
        print(f"CRITICAL ERROR: No valid SBI rate / INTC close found on or before 31-Dec-{year}.")
        return None

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
            # Using the robust daily continuous peak function
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

    fa_df = fa_df[template_cols]
    fa_out_file = f"FA_ITR_TY_{y_short}_{next_y_short}.csv"

    with open(fa_out_file, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        for r in top_rows:
            writer.writerow(r)
        writer.writerow(template_cols)
    
    fa_df.to_csv(fa_out_file, mode='a', index=False, header=False)
    print(f"-> Generated {fa_out_file}")
    
    return fa_out_file


if __name__ == "__main__":
    sbi_path = './SBI_REFERENCE_RATES_USD.csv'
    intc_path = './FA_INTC_RATE.csv'

    # =====================================================================
    # START-UP DISCLAIMER
    # =====================================================================
    print("=========================================")
    print("      ITR FA & CG Master Processor       ")
    print("=========================================\n")
    print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
    print(" IMPORTANT: To prevent duplicate Unsold entries, please ensure your")
    print(" E-Trade P&L and Holdings statements were downloaded on the SAME DAY.")
    print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n")

    year_input = input("1. Enter Target Year (Format: YYYY-YYYY, e.g., 2026-2027): ").strip()
    if not re.match(r'^\d{4}-\d{4}$', year_input):
        print("\nError: Invalid format. You must enter the exact format YYYY-YYYY (e.g., 2026-2027).")
        sys.exit(1)
    
    target_year = int(year_input.split('-')[0])

    pnl_path = input("2. Enter path to P&L Excel file: ").strip().strip("'\"")
    hold_path = input("3. Enter path to Holdings Excel file: ").strip().strip("'\"")

    if not pnl_path or not hold_path:
        print("\nError: P&L and Holdings files are required to proceed.")
        sys.exit(1)

    generated_fa_file = process_itr_data(target_year, pnl_path, hold_path, sbi_path, intc_path)

    if generated_fa_file:
        print("\n=========================================")
        print("         Optional A2 Generation          ")
        print("=========================================")
        print("To generate the A2 report, provide the required Cash file.")
        print("(Press Enter to leave blank and skip this step)")
        
        cash_path = input("\n4. Enter path to Cash CSV/Excel file (or press Enter to skip): ").strip().strip("'\"")
        
        if not cash_path:
            print("\nSkipping A2 processing. Script execution finished!")
        else:
            process_a2_data(target_year, generated_fa_file, cash_path, sbi_path, intc_path)
            print("\nScript execution fully completed!")