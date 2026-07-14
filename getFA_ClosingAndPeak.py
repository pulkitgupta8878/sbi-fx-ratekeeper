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
    date_acq_col = find_column_flexibly(df, "Date Acquired")
    date_sold_col = find_column_flexibly(df, "Date Sold")
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
        "Date Acquired": date_acq_col, "Date Sold": date_sold_col,
        "31-Dec SBI TT BUY RATE": out_rate_col, "31-Dec INTC VALUE": out_intc_col,
        "Closing Value": out_closing_col,
        "Peak Date": out_peak_date_col, "Peak Date SBI TT BUY RATE": out_peak_sbi_col,
        "Peak Date INTC Value": out_peak_intc_col, "Peak Value": out_peak_value_col,
    }.items() if col is None]
    if missing:
        print(f"CRITICAL ERROR: Missing required columns: {', '.join(missing)}")
        return

    # --- Parse reference dates / numeric rates ---
    # SBI dates are YYYY-MM-DD; INTC dates are MM/DD/YYYY (day_first=False for both is safe).
    df_sbi['_DATE_parsed'] = df_sbi[sbi_date_col].apply(lambda x: parse_date_ultra_flexible(x, day_first=False))
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
    print(f"  31-Dec SBI TT BUY RATE = {sbi_rate_val}  (matched {sbi_matched.date()})")
    print(f"  31-Dec INTC VALUE      = {intc_close_val}  (matched {intc_matched.date()})")

    # Allow both numbers and the 'N/A' string in these output columns
    for col in (out_rate_col, out_intc_col, out_closing_col,
                out_peak_date_col, out_peak_sbi_col, out_peak_intc_col, out_peak_value_col):
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
                df.at[idx, out_closing_col] = round(qty * sbi_rate_val * intc_close_val, 4)
            else:
                df.at[idx, out_closing_col] = None
        else:
            # Sold (or any non-UnSold status): closing value 0, other two columns N/A
            df.at[idx, out_rate_col] = 'N/A'
            df.at[idx, out_intc_col] = 'N/A'
            df.at[idx, out_closing_col] = 0

        # --- Peak columns ---
        # Range: Date Acquired .. (Date Sold if Sold, else 31-Dec of reporting year), inclusive.
        start_date = parse_date_ultra_flexible(row[date_acq_col], day_first=True)
        if status == 'sold':
            end_date = parse_date_ultra_flexible(row[date_sold_col], day_first=True)
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
                    df.at[idx, out_peak_value_col] = round(qty * peak_sbi * peak_intc, 4)

    # --- Write output in place, preserving the original top header rows (Year / blank spacer) ---
    with open(data_path, newline='', encoding='utf-8-sig') as f:
        reader = csv.reader(f)
        top_rows = [next(reader, []) for _ in range(header_rows)]

    with open(data_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        for r in top_rows:
            writer.writerow(r)
    df.to_csv(data_path, mode='a', index=False)

    print(f"Success! Closing + Peak columns populated for {year}. Updated '{data_path}' in place.")


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
