import sqlite3
import json
import os
import pandas as pd
from stock_trader.config import DB_PATH

def export_to_csv(output_path="trading_dataset.csv"):
    if not os.path.exists(DB_PATH):
        print(f"❌ DB file not found: {DB_PATH}")
        return
        
    print(f"📂 Loading data from {DB_PATH}...")
    conn = sqlite3.connect(DB_PATH)
    
    # Read trade_history
    query = "SELECT * FROM trade_history"
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    if df.empty:
        print("⚠️ No trade history found to export.")
        return
        
    print(f"📊 Total records found: {len(df)}")
    
    # Parse and flatten features JSON
    flat_features = []
    for idx, row in df.iterrows():
        feat_str = row.get('features')
        flat_row = {}
        if feat_str:
            try:
                feat_dict = json.loads(feat_str)
                # Flatten technical
                tech = feat_dict.get("technical", {})
                for k, v in tech.items():
                    flat_row[f"feat_tech_{k}"] = v
                # Flatten fundamental
                fund = feat_dict.get("fundamental", {})
                for k, v in fund.items():
                    flat_row[f"feat_fund_{k}"] = v
                # Flatten top level keys
                for k, v in feat_dict.items():
                    if k not in ["technical", "fundamental"]:
                        flat_row[f"feat_{k}"] = v
            except Exception as e:
                pass
        flat_features.append(flat_row)
        
    # Create DataFrame of features
    df_feat = pd.DataFrame(flat_features)
    
    # Concatenate trade_history columns with parsed features
    df_out = pd.concat([df.drop(columns=['features']), df_feat], axis=1)
    
    # Save to CSV
    df_out.to_csv(output_path, index=False, encoding='utf-8-sig')
    print(f"✅ Export completed successfully: {output_path} ({len(df_out)} rows)")
    
if __name__ == "__main__":
    export_to_csv()
