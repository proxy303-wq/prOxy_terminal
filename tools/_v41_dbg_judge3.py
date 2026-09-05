import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
df = pd.read_csv("reports/v41/die_judgment_scored.csv")
print("total", len(df))
print("flags value_counts top8:")
print(df["flags"].value_counts().head(8).to_string())
print("empty flags:", int((df["flags"]=="").sum()))
print("\nWAIT rows sample (score/thermostat/day pnl proxy cols not stored) - show n by thermostat:")
print(df[df["band"]=="WAIT"]["thermostat"].value_counts().to_string())
