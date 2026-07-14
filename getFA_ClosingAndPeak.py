import os
import re
import sys
import csv
import pandas as pd
from pathlib import Path


def find_column_flexibly(df, target_name):
    """Finds a column name ignoring casing and whitespace."""
    target_clean = target_name.strip().upper()
    for col in df.columns:
        if str(col).strip().upper() == target_clean:
            return col
    return None


def parse_date_ultra_flexible(val, day_first=True):
    """Extracts date numbers securely regardless of layout variations."""
    s = str(val).strip()
    if not s or s.lower() in ['nan', 'null', 'none', '']:
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


def extract_year_from_header(data_path, scan_rows=2):
    """Scans the first few raw rows for a 'Year' label and returns the adjacent 4-digit year."""
    with open(data_path, newline='', encoding='utf-8-sig') as f:
        reader = csv.reader(f)
        for _ in range(scan_rows):
            row = next(reader, None)
            if row is None:
                break
            for i, cell in enumerate(row):
                if str(cell).strip().lower() == 'year':
                    for c in row[i + 1:]:
                        m = re.search(r'\d{4}', str(c))
                        if m:
                            return int(m.group())
    return None


def get_value_on_or_before(df, date_col, value_col, target_date):
    """Returns (value, matched_date) for the latest row on/before target_date with value > 0."""
    valid = df[
        (df[date_col].notna())
        & (df[date_col] <= target_date)
        & (df[value_col] > 0)
    ]
    if valid.empty:
        return None, None
    idx = valid[date_col].idxmax()
    return valid.loc[idx, value_col], valid.loc[idx, date_col]


def get_peak_in_range(df_intc, intc_date_col, intc_close_col,
                      df_sbi, sbi_date_col, sbi_rate_col,
                      start_date, end_date):
    """Finds the peak INTC close within [start_date, end_date] inclusive.

    Returns (peak_date, peak_intc_value, peak_sbi_rate). If multiple dates share
    the same maximum close, the date with the minimum SBI TT BUY RATE is chosen.
    The SBI rate for a candidate date is the latest rate on/before it with TT BUY > 0.
    """
    in_range = df_intc[
        (df_intc[intc_date_col].notna())
        & (df_intc[intc_date_col] >= start_date)
        & (df_intc[intc_date_col] <= end_date)
        & (df_intc[intc_close_col] > 0)
    ]
    if in_range.empty:
        return None, None, None

    max_close = in_range[intc_close_col].max()
    candidates = in_range[in_range[intc_close_col] == max_close]

    best_date, best_sbi = None, None
    for _, crow in candidates.iterrows():
        cand_date = crow[intc_date_col]
        sbi_rate, _ = get_value_on_or_before(df_sbi, sbi_date_col, sbi_rate_col, cand_date)
        if sbi_rate is None:
            continue
        if best_sbi is None or sbi_rate < best_sbi:
            best_sbi = sbi_rate
            best_date = cand_date

    if best_date is None:
        # No SBI rate found for any candidate; fall back to the earliest candidate date
        best_date = candidates[intc_date_col].min()

    return best_date, max_close, best_sbi


def update_fa_closing_and_peak(data_path=None, sbi_path=None, intc_path=None, header_rows=2):
    if not os.path.exists(data_path) or not os.path.exists(sbi_path) or not os.path.exists(intc_path):
        print(f"Error: Ensure '{data_path}', '{sbi_path}' and '{intc_path}' are present.")
        return

    # --- Reporting year comes from the 'Year' cell in the top header rows ---
    year = extract_year_from_header(data_path, scan_rows=header_rows)
    if year is None:
        print("CRITICAL ERROR: Could not find the reporting 'Year' in the top rows of the data file.")
        return
    target_date = pd.Timestamp(year=year, month=12, day=31)

    # --- Load data (skip the 'Year' row + blank spacer so real header is used) ---
    df = pd.read_csv(data_path, skiprows=header_rows, keep_default_na=False)
    df_sbi = pd.read_csv(sbi_path)
    df_intc = pd.read_csv(intc_path)

    # --- Resolve columns flexibly ---
    sbi_date_col = find_column_flexibly(df_sbi, "DATE")
    sbi_rate_col = find_column_flexibly(df_sbi, "TT BUY")
    intc_date_col = find_column_flexibly(df_intc, "DATE")
    intc_close_col = find_column_flexibly(df_intc, "CLOSE")

    status_col = find_column_flexibly(df, "Sold/UnSold")
    qty_col = find_column_flexibly(df, "Quantity")
    date_acq_col = find_column_flexibly(df, "Date Acquired (mm/dd/yyyy)")
    date_sold_col = find_column_flexibly(df, "Date Sold (mm/dd/yyyy)")
    acb_usd_col = find_column_flexibly(df, "Adjusted Cost Basis")
    acb_inr_col = find_column_flexibly(df, "Adjusted Cost Basis - INR")
    proceeds_usd_col = find_column_flexibly(df, "Total Proceeds")
    proceeds_inr_col = find_column_flexibly(df, "Total Proceeds - INR")
    sbi_acq_date_col = find_column_flexibly(df, "SBI TT DATE INR - Date Acquired")
    sbi_acq_rate_col = find_column_flexibly(df, "SBI TT RATE INR - Date Acquired")
    sbi_sold_date_col = find_column_flexibly(df, "SBI TT DATE INR - Date Sold")
    sbi_sold_rate_col = find_column_flexibly(df, "SBI TT RATE INR - Date Sold")
    out_rate_col = find_column_flexibly(df, "31-Dec SBI TT BUY RATE")
    out_intc_col = find_column_flexibly(df, "31-Dec INTC VALUE")
    out_closing_col = find_column_flexibly(df, "Closing Value")
    out_peak_date_col = find_column_flexibly(df, "Peak Date")
    out_peak_sbi_col = find_column_flexibly(df, "Peak Date SBI TT BUY RATE")
    out_peak_intc_col = find_column_flexibly(df, "Peak Date INTC Value")
    out_peak_value_col = find_column_flexibly(df, "Peak Value")

    missing = [name for name, col in {
        "SBI DATE": sbi_date_col, "SBI TT BUY": sbi_rate_col,
        "INTC DATE": intc_date_col, "INTC CLOSE": intc_close_col,
        "Sold/UnSold": status_col, "Quantity": qty_col,
        "Date Acquired (mm/dd/yyyy)": date_acq_col, "Date Sold (mm/dd/yyyy)": date_sold_col,
        "Adjusted Cost Basis": acb_usd_col, "Adjusted Cost Basis - INR": acb_inr_col,
        "Total Proceeds": proceeds_usd_col, "Total Proceeds - INR": proceeds_inr_col,
        "SBI TT DATE INR - Date Acquired": sbi_acq_date_col,
        "SBI TT RATE INR - Date Acquired": sbi_acq_rate_col,
        "SBI TT DATE INR - Date Sold": sbi_sold_date_col,
        "SBI TT RATE INR - Date Sold": sbi_sold_rate_col,
        "31-Dec SBI TT BUY RATE": out_rate_col, "31-Dec INTC VALUE": out_intc_col,
        "Closing Value": out_closing_col,
        "Peak Date": out_peak_date_col, "Peak Date SBI TT BUY RATE": out_peak_sbi_col,
        "Peak Date INTC Value": out_peak_intc_col, "Peak Value": out_peak_value_col,
    }.items() if col is None]
    if missing:
        print(f"CRITICAL ERROR: Missing required columns: {', '.join(missing)}")
        return

    # --- Add dd/mm/yyyy display columns immediately to the right of the mm/dd/yyyy source columns ---
    # The source 'Date Acquired'/'Date Sold' (mm/dd/yyyy) are never modified; a sibling
    # dd/mm/yyyy column is (re)generated on every run so reruns stay idempotent.
    date_acq_ddmm_col = "Date Acquired (dd/mm/yyyy)"
    date_sold_ddmm_col = "Date Sold (dd/mm/yyyy)"

    def _mmddyyyy_to_ddmmyyyy(val):
        d = parse_date_ultra_flexible(val, day_first=False)  # source is mm/dd/yyyy
        if pd.isna(d):
            return val  # leave 'N/A' / blanks untouched
        return d.strftime('%d/%m/%Y')

    def _ensure_col_right_of(frame, new_name, ref_name):
        if new_name not in frame.columns:
            frame.insert(frame.columns.get_loc(ref_name) + 1, new_name, '')

    _ensure_col_right_of(df, date_acq_ddmm_col, date_acq_col)
    _ensure_col_right_of(df, date_sold_ddmm_col, date_sold_col)
    df[date_acq_ddmm_col] = df[date_acq_col].apply(_mmddyyyy_to_ddmmyyyy)
    df[date_sold_ddmm_col] = df[date_sold_col].apply(_mmddyyyy_to_ddmmyyyy)

    # --- Parse reference dates / numeric rates ---
    # SBI dates are DD-MM-YYYY (day_first=True); INTC dates are MM/DD/YYYY (day_first=False).
    df_sbi['_DATE_parsed'] = df_sbi[sbi_date_col].apply(lambda x: parse_date_ultra_flexible(x, day_first=True))
    df_sbi[sbi_rate_col] = pd.to_numeric(df_sbi[sbi_rate_col], errors='coerce').fillna(0)

    df_intc['_DATE_parsed'] = df_intc[intc_date_col].apply(lambda x: parse_date_ultra_flexible(x, day_first=False))
    df_intc[intc_close_col] = pd.to_numeric(df_intc[intc_close_col], errors='coerce').fillna(0)

    # --- 31-Dec (or last valid trading day before it) values for the reporting year ---
    sbi_rate_val, sbi_matched = get_value_on_or_before(df_sbi, '_DATE_parsed', sbi_rate_col, target_date)
    intc_close_val, intc_matched = get_value_on_or_before(df_intc, '_DATE_parsed', intc_close_col, target_date)

    if sbi_rate_val is None or intc_close_val is None:
        print(f"CRITICAL ERROR: No valid SBI rate / INTC close found on or before 31-Dec-{year}.")
        return

    print(f"Reporting year: {year}")

    # Ensure all computed columns are object dtype so mixed numbers/'N/A' are allowed
    for col in (sbi_acq_date_col, sbi_acq_rate_col,
                sbi_sold_date_col, sbi_sold_rate_col,
                acb_inr_col, proceeds_inr_col,
                out_rate_col, out_intc_col, out_closing_col,
                out_peak_date_col, out_peak_sbi_col, out_peak_intc_col, out_peak_value_col):
        df[col] = df[col].astype(object)

    # Reset computed columns so every run fully recalculates and overwrites them.
    # The Date-Sold outputs are intentionally excluded here and handled per-row below
    # so UnSold rows keep their 'N/A' instead of being blanked.
    for col in (sbi_acq_date_col, sbi_acq_rate_col,
                acb_inr_col,
                out_rate_col, out_intc_col, out_closing_col,
                out_peak_date_col, out_peak_sbi_col, out_peak_intc_col, out_peak_value_col):
        df[col] = ''
        df[col] = df[col].astype(object)

    # --- Fill calculated columns row by row ---
    for idx, row in df.iterrows():
        status = str(row[status_col]).strip().lower()
        qty = pd.to_numeric(str(row[qty_col]).replace(',', ''), errors='coerce')

        # --- Closing columns ---
        if status == 'unsold':
            df.at[idx, out_rate_col] = sbi_rate_val
            df.at[idx, out_intc_col] = intc_close_val
            if pd.notna(qty):
                df.at[idx, out_closing_col] = round(qty * sbi_rate_val * intc_close_val, 2)
            else:
                df.at[idx, out_closing_col] = None
        else:
            # Sold (or any non-UnSold status): closing value 0, other two columns N/A
            df.at[idx, out_rate_col] = 'N/A'
            df.at[idx, out_intc_col] = 'N/A'
            df.at[idx, out_closing_col] = 0

        # --- SBI TT rate/date on (or nearest past) the Date Acquired ---
        acq_date = parse_date_ultra_flexible(row[date_acq_col], day_first=False)
        if pd.notna(acq_date):
            acq_rate, acq_matched = get_value_on_or_before(df_sbi, '_DATE_parsed', sbi_rate_col, acq_date)
            if acq_rate is not None:
                df.at[idx, sbi_acq_rate_col] = acq_rate
                df.at[idx, sbi_acq_date_col] = acq_matched.strftime('%d/%m/%Y')
                # Adjusted Cost Basis - INR = Adjusted Cost Basis (USD) * acquired rate
                acb_usd = pd.to_numeric(str(row[acb_usd_col]).replace('$', '').replace(',', ''), errors='coerce')
                if pd.notna(acb_usd):
                    df.at[idx, acb_inr_col] = round(acb_usd * acq_rate, 2)

        # --- SBI TT rate/date on (or nearest past) the Date Sold (Sold rows only) ---
        if status == 'sold':
            # Recompute from scratch for Sold rows
            df.at[idx, sbi_sold_date_col] = ''
            df.at[idx, sbi_sold_rate_col] = ''
            df.at[idx, proceeds_inr_col] = ''
            sold_date = parse_date_ultra_flexible(row[date_sold_col], day_first=False)
            if pd.notna(sold_date):
                sold_rate, sold_matched = get_value_on_or_before(df_sbi, '_DATE_parsed', sbi_rate_col, sold_date)
                if sold_rate is not None:
                    df.at[idx, sbi_sold_rate_col] = sold_rate
                    df.at[idx, sbi_sold_date_col] = sold_matched.strftime('%d/%m/%Y')
                    # Total Proceeds - INR = Total Proceeds (USD) * sold rate
                    proceeds_usd = pd.to_numeric(str(row[proceeds_usd_col]).replace('$', '').replace(',', ''), errors='coerce')
                    if pd.notna(proceeds_usd):
                        df.at[idx, proceeds_inr_col] = round(proceeds_usd * sold_rate, 2)
        else:
            # UnSold: keep the Date-Sold outputs as 'N/A' (do not blank/overwrite)
            df.at[idx, sbi_sold_date_col] = 'N/A'
            df.at[idx, sbi_sold_rate_col] = 'N/A'
            df.at[idx, proceeds_inr_col] = 'N/A'

        # --- Peak columns ---
        # Range: max(Date Acquired, 1-Jan of reporting year) .. (Date Sold if Sold, else 31-Dec of reporting year), inclusive.
        
        start_date = parse_date_ultra_flexible(row[date_acq_col], day_first=False)
        year_start = pd.Timestamp(year=year, month=1, day=1)
        
        if pd.notna(start_date) and start_date < year_start:
            start_date = year_start

        if status == 'sold':
            end_date = parse_date_ultra_flexible(row[date_sold_col], day_first=False)
        else:
            end_date = target_date

        if pd.notna(start_date) and pd.notna(end_date):
            peak_date, peak_intc, peak_sbi = get_peak_in_range(
                df_intc, '_DATE_parsed', intc_close_col,
                df_sbi, '_DATE_parsed', sbi_rate_col,
                start_date, end_date)
            if peak_date is not None:
                df.at[idx, out_peak_date_col] = peak_date.strftime('%d/%m/%Y')
                df.at[idx, out_peak_intc_col] = peak_intc
                df.at[idx, out_peak_sbi_col] = peak_sbi
                if pd.notna(qty) and peak_sbi is not None:
                    df.at[idx, out_peak_value_col] = round(qty * peak_sbi * peak_intc, 2)

    # --- Write output in place, preserving the original top header rows (Year / blank spacer) ---
    with open(data_path, newline='', encoding='utf-8-sig') as f:
        reader = csv.reader(f)
        top_rows = [next(reader, []) for _ in range(header_rows)]

    with open(data_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        for r in top_rows:
            writer.writerow(r)
    df.to_csv(data_path, mode='a', index=False)

    print(f"Success! Date, SBI TT rate, INR value, Closing, and Peak columns populated for {year}. Updated '{data_path}' in place.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python getFA_ClosingAndPeak.py <FA_ITR_file.csv>")
        print("Using default 'FA_ITR_FY-25-26.csv' if present.")
        data_path = "FA_ITR_FY-25-26.csv"
    else:
        data_path = sys.argv[1]

    sbi_path = './csv_files/SBI_REFERENCE_RATES_USD.csv'
    intc_path = './INTC_RATES/FA_INTC_RATE.csv'

    update_fa_closing_and_peak(data_path=data_path, sbi_path=sbi_path, intc_path=intc_path, header_rows=2)
