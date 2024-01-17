import pandas as pd
import os
import glob
import datetime

def main():
    df_main = 0
    print('hi')
    
    #os.chdir(r'C:\Users\jgmon\Dropbox\silver-most-recent\SILVER\SILVER')
    
    ## Change the hydro data dates
    for filepath in glob.iglob(r'C:\Users\jgmon\Dropbox\silver-most-recent\SILVER\SILVER\SILVER_Data\user_inputs\Hydro_Data\hydro_hourly.csv', recursive=True):
        print('hi2')
        print(filepath)
        hydro_df = pd.read_csv(filepath)
        if "date" not in hydro_df.columns:
            continue
        
        for index,row in hydro_df.iterrows():
            date = str(row.get(0))
            if "2/29" in date:
                hydro_df = hydro_df.drop(index)
                continue
            hydro_df.at[index, 'date'] = date.replace('2012', '2018')
        hydro_df.to_csv(filepath, index=False)
    
    ## Change the VRE data
    for filepath in glob.iglob(r'C:\Users\jgmon\Dropbox\silver-most-recent\SILVER\SILVER\SILVER_Data\user_inputs\VRE_Resource_Analysis\*_Generation_Data\**\*.csv', recursive=True):
        print('hi3')
        print(filepath)
        vre_df = pd.read_csv(filepath)
        
        for index,row in vre_df.iterrows():
            date = str(row.get(0))
            if "2/29" in date:
                vre_df = vre_df.drop(index)
                continue
            vre_df.at[index, vre_df.columns[0]] = date.replace('2012', '2018')
        
        vre_df.to_csv(filepath, index=False)
    
if __name__ == '__main__':
    main()

