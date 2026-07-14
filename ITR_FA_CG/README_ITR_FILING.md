# Unified ITR Master Processor (CG, FA & A2)

This tool automates the processing of foreign asset data, capital gains, and cash components for Indian Income Tax Returns (ITR). 

The workflow has been unified into a single Graphical User Interface (GUI). You can run this tool natively as a standalone executable application (no Python required) or via the source Python script. All templates are seamlessly injected and generated automatically by the tool.

---

## General Prerequisites
Before running the application, ensure your reference exchange rates are up to date and downloaded to your machine:
* **INTC Rates:** Update your INTC rate file (e.g., `FA_INTC_RATE.csv`) using Google Finance. Ensure data is filled up to March 31st, covering the duration since your join date. `(=GOOGLEFINANCE("INTC", "all", "01-01-2019", TODAY(), "DAILY"))`
* **SBI TT Buy Rates:** Ensure your `SBI_REFERENCE_RATES_USD.csv` is updated with the latest monthly rates.

---

## Step 1: Downloading E-Trade Reports
To satisfy both Capital Gains (Financial Year) and Schedule FA (Calendar Year) requirements, the tool uses an advanced lot-reconciliation engine. 

⚠️ **CRITICAL WARNING:** To prevent duplicate asset entries, you **MUST** download both your P&L Report and your Holdings Report on the **EXACT SAME DAY**. Do not download historical snapshots.

### 1. P&L / Gain & Loss Report
This file is used to calculate your sold assets and capital gains, and to reconstruct missing year-end assets.
1. Log in to E-Trade and navigate to **Stock Plan** -> **My Account** -> **Gains & Losses**.
2. Select **Custom Date** for the date range.
3. **CRITICAL DATES:** Set the Start Date to **January 1st, `<Target Year>`** and the End Date to **March 31st, `<Target Year + 1>`** (or up to today's date). *(For example: Jan 1, 2026 to Mar 31, 2027. This ensures the file captures both the Calendar Year for FA and the Financial Year for CG).*
4. Click **Download** and select the **Expanded Excel** format. Save this file.

### 2. Current Holdings Report
This file is used to declare your active, unsold assets. The script will automatically calculate their value as of December 31st.
1. Navigate to **Portfolios** or **Holdings**.
2. Ensure the view is set to your current active holdings.
3. Click **Download / Export to Excel** and save this file. 

---

## Step 2: Cash Component Preparation (Manual Pre-requisite for A2)
> **Note:** Only complete this step if you intend to generate the optional **Schedule FA - A2** report.

1. **Download Statements:** Download your monthly account statements from **January to December** (Calendar Year).
2. **Template Setup:** Open the provided sample file `SAMPLE_ITR_TY-26-27_CashEtrade.csv` and use it to create your own working file.
3. **Extract Data:** Open each monthly statement, navigate to the **"CASH FLOW ACTIVITY BY DATE"** section, and look for the **"Credits/(Debits)"** amounts.
4. **Determine the Date:** Check both the *Activity Date* and the *Settlement Date*. 
   * **Rule:** If a Settlement Date is present, use it. Otherwise, use the Activity Date.
5. **Compile & Save:** Fill this data accurately into your working file. **You must save this file as a CSV (`.csv`).** Do not save it as an Excel spreadsheet, as regional Excel settings can silently corrupt the US date formatting (`MM/DD/YYYY`) and ruin the timeline.

---

## Step 3: Automated Processing via GUI

> **📅 Note the Timeline Differences Programmed into the Tool:** 
> *   **Capital Gains:** Calculated strictly on the Financial Year (April 1 to March 31).
> *   **Foreign Assets (FA - A3 & A2):** Calculated strictly on the Calendar Year (January 1 to December 31).

### Running the Application
* **If using the Standalone Executable:** Simply double-click the `process_ITR_GUI` application file. (No Python or libraries required).
* **If using the Python Script:** Activate your virtual environment and run `python process_ITR_GUI.py`.

### Using the Interface
1. **Target Year:**
   * Enter the exact reporting period in `YYYY-YYYY` format (e.g., `2026-2027`). The tool will automatically use this to set date boundaries and format the headers in your generated reports.
2. **Group 1 (Mandatory Base Files):** 
   * Click **Browse** to select your E-Trade **P&L Excel file**.
   * Click **Browse** to select your **Holdings Excel file** (downloaded on the same day as your P&L).
3. **Group 2 (Mandatory Rate Files):** 
   * Click **Browse** to upload your up-to-date **INTC Rates CSV**.
   * Click **Browse** to upload your up-to-date **SBI Rates CSV**.
4. **Group 3 (Optional A2 Report):** 
   * If you need the A2 Cash report, check the box.
   * Upload the manual **Cash CSV file** you created in Step 2.
5. **Group 4 (Output Destination):** 
   * Select a specific folder to save the generated files. If left blank, the tool saves them in the same directory as the application.
6. Click **Run Processor**.

### Outputs Generated
Upon success, the tool will output the following finalized files directly to your chosen folder:
1. `CG_ITR_TY_YY_YY+1.csv` (Your completed Capital Gains statement)
2. `FA_ITR_TY_YY_YY+1.csv` (Your completed Schedule FA A3 statement)
3. `A2_ITR_TY_YY_YY+1.csv` *(If the optional A2 box was checked)*

Use these exact figures to file your respective Capital Gains and Foreign Asset sections in your ITR.