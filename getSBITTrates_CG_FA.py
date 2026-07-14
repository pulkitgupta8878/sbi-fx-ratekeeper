import os
import re
import sys
import pandas as pd
import numpy as np
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

def update_sbi_rates(data_path=None, master_path=None,date_col=[],typ='CG'):
    if not os.path.exists(data_path) or not os.path.exists(master_path):
        print(f"Error: Ensure '{data_path}' and '{master_path}' are in this folder.")
        return
    if date_col is None:
        print(f"Error: Ensure you have passed Date columns to check in your Document")
        return

    df_data = pd.read_csv(data_path)
    df_master = pd.read_csv(master_path)
    
    data_date_col=[]
    
    master_date_col = find_column_flexibly(df_master, "DATE")

    total_err=0

    for dt in date_col:
        x=find_column_flexibly(df_data, dt)
        if x is None:
            total_err+=1
        data_date_col.append(x)
    tt_buy_col = find_column_flexibly(df_master, "TT BUY")


    if not master_date_col or total_err!=0 or not tt_buy_col:
        print("CRITICAL ERROR: Verification failed. Confirm column names match up.")
        return

    date_col_parsed =list(map(lambda x: f"{x} _parsed", date_col))

    # 1. Parse date tracking
    df_master['DATE_parsed'] = df_master[master_date_col].apply(lambda x: parse_date_ultra_flexible(x, day_first=True))
    
    for i in range(len(date_col_parsed)):
        df_data[date_col_parsed[i]] = df_data[data_date_col[i]].apply(lambda x: parse_date_ultra_flexible(x, day_first=False))

    # 2. Convert TT BUY column to floating numbers to perform numeric validation
    df_master[tt_buy_col] = pd.to_numeric(df_master[tt_buy_col], errors='coerce').fillna(0)

    # 3. CRITICAL UPDATE: Clean master data keeping ONLY valid, non-zero records
    df_master_clean = df_master.dropna(subset=['DATE_parsed']).copy()
    df_master_clean = df_master_clean[df_master_clean[tt_buy_col] > 0]

    columns_req=["Plan Type","Capital Gains Status","Quantity"]

    print("Matching rows against previous month's max date containing non-zero records...")
    for i in range(len(date_col_parsed)):

        final_rates, final_dates = [], []

        for idx, row in df_data.iterrows():
            data_date = row[date_col_parsed[i]]
            if pd.isna(data_date):
                final_rates.append(None)
                final_dates.append(None)
                continue
            if typ=='CG':
                # Target Month Partitioning
                if data_date.month == 1:
                    target_year, target_month = data_date.year - 1, 12
                else:
                    target_year, target_month = data_date.year, data_date.month - 1

                # This subset now automatically excludes rows that were 0 or NaN
                month_subset = df_master_clean[
                    (df_master_clean['DATE_parsed'].dt.year == target_year) & 
                    (df_master_clean['DATE_parsed'].dt.month == target_month)
                ]

                if not month_subset.empty:
                    # Grabs the max date that contains a valid value
                    max_date_found = month_subset['DATE_parsed'].max()
                    day_subset = month_subset[month_subset['DATE_parsed'] == max_date_found]
            
                    # Pull clean single value scalars using explicit positional index extraction (.iloc[0])
                    tt_buy_val = day_subset[tt_buy_col].iloc[0]
                    matched_date_str = max_date_found.strftime('%m/%d/%Y')
                
                    final_rates.append(tt_buy_val)
                    final_dates.append(matched_date_str)
                else:
                    final_rates.append(None)
                    final_dates.append(None)
            
            elif typ=='FA':
                
                df_master_clean['DATE_parsed']=pd.to_datetime(df_master_clean['DATE_parsed'], format="%m/%d/%Y")

                data_date = pd.to_datetime(data_date, format="%m/%d/%Y")

                masterdata_subset = df_master_clean[
                    (df_master_clean['DATE_parsed'] <= data_date) 
                ]

                if not masterdata_subset.empty:
                    # Grabs the max date that contains a valid value
                    max_date_found = masterdata_subset['DATE_parsed'].max()
                    day_subset = masterdata_subset[masterdata_subset['DATE_parsed'] == max_date_found]
            
                    # Pull clean single value scalars using explicit positional index extraction (.iloc[0])
                    tt_buy_val = day_subset[tt_buy_col].iloc[0]
                    matched_date_str = max_date_found.strftime('%m/%d/%Y')
                
                    final_rates.append(tt_buy_val)
                    final_dates.append(matched_date_str)
                else:
                    final_rates.append(None)
                    final_dates.append(None)
            else:
                print("Type Should be either CG or FA")
                return

        # Map arrays back into target blank fields
        #"SBI TT DATE INR - "+date_col
        #"SBI TT RATE INR - "+ date_col
        df_data["SBI TT DATE INR - "+ date_col[i]] = final_dates
        df_data["SBI TT RATE INR - "+ date_col[i]] = final_rates

        #print(df_data["Adjusted Cost Basis"])
        if date_col_parsed[i] in df_data.columns:
            df_data = df_data.drop(columns=[date_col_parsed[i]])

        columns_req.append(date_col[i])
        columns_req.append("SBI TT DATE INR - "+ date_col[i])
        columns_req.append("SBI TT RATE INR - "+ date_col[i])
        if "Acquired" in date_col[i]:
            columns_req.append("Adjusted Cost Basis")
            columns_req.append("Adjusted Cost Basis - INR")

            clean_acq= (df_data["Adjusted Cost Basis"].astype(str).str.replace("$", "", regex=False).str.replace(",", "", regex=False))
            clean_acq = pd.to_numeric(clean_acq, errors="coerce")

            final_rates_series = pd.Series(final_rates)
            raw_product = final_rates_series * clean_acq

            df_data["Adjusted Cost Basis - INR"]= [f"{val:.2f}" if pd.notna(val) else None for val in raw_product]


        if "Sold" in date_col[i]:
            columns_req.append("Total Proceeds")
            columns_req.append("Total Proceeds - INR")

            clean_sold= (df_data["Total Proceeds"].astype(str).str.replace("$", "", regex=False).str.replace(",", "", regex=False))
            clean_sold = pd.to_numeric(clean_sold, errors="coerce")

            final_rates_series = pd.Series(final_rates)
            raw_product = final_rates_series * clean_sold

            df_data["Total Proceeds - INR"]= [f"{val:.2f}" if pd.notna(val) else None for val in raw_product]

    if "Adjusted Cost Basis - INR" in df_data and "Total Proceeds - INR" in df_data:
        columns_req.append("Gain/Loss - INR Per Sale")
        df_data["Gain/Loss - INR Per Sale"]= pd.to_numeric(df_data['Total Proceeds - INR'], errors='coerce') - pd.to_numeric(df_data['Adjusted Cost Basis - INR'], errors='coerce')

    for i in range (len(date_col)):

        # 1. Convert the string column to a true datetime object
        df_data[date_col[i]] = pd.to_datetime(df_data[date_col[i]], format='%m/%d/%Y')
        df_data["SBI TT DATE INR - "+ date_col[i]] = pd.to_datetime(df_data["SBI TT DATE INR - "+ date_col[i]], format='%m/%d/%Y')

        # 2. Format the datetime object to the new DD/MM/YYYY string format
        df_data[date_col[i]] = df_data[date_col[i]].dt.strftime('%d/%m/%Y')
        df_data["SBI TT DATE INR - "+ date_col[i]] = df_data["SBI TT DATE INR - "+ date_col[i]].dt.strftime('%d/%m/%Y')

    df_data[columns_req].to_csv(f"{Path(data_path).stem}_PROCESSED_{typ}{Path(data_path).suffix}", index=False)
    print(f"Success! Correctly updated columns are completely populated inside '{data_path}'.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python getSBITTrates.py <your_data_file.csv>")
        print("Using default data.csv if it is present")
        data_path="data.csv"
    else:
        data_path = sys.argv[1]

    master_path='./csv_files/SBI_REFERENCE_RATES_USD.csv'

    update_sbi_rates(data_path= data_path, master_path= master_path,date_col=["Date Acquired","Date Sold"],typ='FA')
    update_sbi_rates(data_path= data_path, master_path= master_path,date_col=["Date Acquired","Date Sold"],typ='CG')