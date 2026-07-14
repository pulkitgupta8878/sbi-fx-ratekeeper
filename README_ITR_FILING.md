# Foreign Assets, Capital Gains & Cash Processing

This repository contains tools and workflows to process foreign asset data, capital gains, and cash components for Indian Income Tax Returns (ITR). 

---

## General Prerequisites
Before running the scripts, ensure your reference exchange rates are up to date:
* **INTC Rates:** Update `.\INTC_RATES\FA_INTC_RATE.csv` using Google Finance. Ensure data is filled up to March 31st, covering the duration since your join date. (=GOOGLEFINANCE("INTC", "all", "01-01-2019", TODAY(), "DAILY"))
* **SBI TT Buy Rates:** Ensure `.\csv_files\SBI_REFERENCE_RATES_USD.csv` is updated. You can do this by running the main program or pulling the latest data from the repository.

---

## 1. Capital Gains Calculation
> **⚠️ Important:** This section is strictly for calculating Capital Gains (April to March). It is **not** for the Declaration of Foreign Assets (Schedule FA).

### Execution Steps
1. **Download Data:** Export the **Gain and Loss Expanded** report from your trading platform using a Custom Timeframe of **April to March**.
2. **Format File:** Convert the downloaded Excel file to a `.csv` format.
3. **Run Script:** Execute the following command in your terminal:
   ```bash
   python getSBITTrates_CG_FA.py <yourfilename.csv>
   ```
4. **Output:** The program will generate a processed file named `<yourfilename_PROCESSED_CG.csv>`.
5. **Filing:** Use this processed file to fill out your Capital Gains for foreign assets in your ITR.

---

## 2. Declaring Foreign Assets (Schedule FA - A3)
> **📅 Note the Timeline:** Foreign Asset declaration uses the calendar year (**January to December**). 

### Data Collection
Gather the following documents before starting:
1. **Trade Report:** Gain and Loss Expanded report for **January to December**.
2. **Year-End Holdings:** Asset/Holdings statement exactly as of **December 31st**. (Include only vested assets—this gives you data for unsold assets at the end of the year).
3. **Previous Data:** Your completed file from last year and the template `.\SAMPLE_FA_ITR_FY-25-26.csv`.

### Step-by-Step Data Entry
1. **Clean Last Year's Data:** Open your file from last year. Delete any sections/rows for assets that were sold last year (they do not carry over to this year).
2. **Update Retained Assets:** Compare your leftover assets (from Step 1) with your new Jan-Dec Trade Report:
   * **Unsold:** If they are still held, leave them as is.
   * **Fully Sold:** Update the status to `Sold`, input the total proceeds, and add the sold date.
   * **Partially Sold:** Split the row into two distinct entries (one for the sold portion, one for the retained unsold portion).
3. **Add New Transactions (Jan-Dec):**
   * **Bought & Sold this year:** Create new entries in the `Sold` category.
   * **Bought & Retained this year:** Use your Dec 31st Holdings statement to log new assets purchased this year that haven't been sold. Mark these as `Unsold`.
   
### Execution Steps
Once your CSV is completely updated manually using the steps above, run the processing script. *(Note: This utilizes the same INTC and SBI rate files updated in Section 1).*

1. **Run Script:**
   ```bash
   python getFA_ClosingAndPeak.py <yourUpdatedFile.csv>
   ```
2. **Output:** The script will update your file in-place with the correct closing and peak balances. 
3. **Filing:** Use this finalized file to complete section **A3 in Foreign Assets** in your ITR.

---

## 3. Cash Component (Manual Process without Code)

### Data Collection
1. **Download Statements:** Download your monthly account statements from **January to December**.
2. **Template Setup:** Open the provided sample file `SAMPLE_ITR_TY-26-27_CashEtrade.xlsx` and use it to create your own working file.

### Step-by-Step Data Entry
1. **Locate Cash Flows:** Open each monthly statement and navigate to the **"CASH FLOW ACTIVITY BY DATE"** section. 
2. **Extract Data:** Look for the **"Credits/(Debits)"** amounts.
3. **Determine the Date:** Check both the *Activity Date* and the *Settlement Date*. 
   * **Rule:** If a Settlement Date is present, use it. Otherwise, use the Activity Date.
4. **Compile:** Fill this data accurately into your working Excel file. 
   > *Note: Getting this correct is crucial. This file, combined with your output from Section 2, will be required to file section A2 (detailed in Section 4).*

---

## 4. Filing Section A2
> 🚧 **Work in Progress:** *This section will be populated later once the automation code for this step is complete.*