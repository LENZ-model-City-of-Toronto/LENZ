from __future__ import print_function
from __future__ import division

#####################################################################
from builtins import range
from past.utils import old_div
# from SILVER_VER_18.scenario_analysis_module import scenario_metrics
from SILVER_VER_18.config_variables import *
from SILVER_VER_18.setup_call_for_week import setup_call_for_week
from SILVER_VER_18.visualization import *
#####################################################################
from time import perf_counter
import datetime
import pandas as pd
import openpyxl
############################


list_of_failed_uc_days = []
##############
# MAIN - calls all other functions
##############
starttime = perf_counter()


def main():

    print("Beginning analysis from:", USER_STARTDATE.date(), "to", USER_ENDDATE.date(), USER_ENDDATE - USER_STARTDATE, 'hours')
    num_commitment_periods = old_div((USER_ENDDATE - USER_STARTDATE).days, NUMBER_OF_DAYS)
    startdate = USER_STARTDATE

    enddate = startdate + datetime.timedelta(days=NUMBER_OF_DAYS)
    for start_of_period_run in range(num_commitment_periods):

        period_starttime = perf_counter()
        list_of_failed_uc_days_2 = setup_call_for_week(startdate, enddate, start_of_period_run, list_of_failed_uc_days)
        startdate = startdate + datetime.timedelta(days=NUMBER_OF_DAYS)
        enddate = enddate + datetime.timedelta(days=NUMBER_OF_DAYS)

        print("month time: %f seconds " % (perf_counter() - period_starttime))

print("total time: %f seconds " % (perf_counter() - starttime))


if __name__ == '__main__':

    main()

if not os.path.exists(FOLDER_TO_SAVE_FINAL_RESULTS / '0_OPF_Results_ESMIA.csv'):
    open(FOLDER_TO_SAVE_FINAL_RESULTS / '0_OPF_Results_ESMIA.csv', 'w').close()
df_ESMIA = pd.read_csv(FOLDER_TO_SAVE_FINAL_RESULTS / '0_OPF_Results_ESMIA.csv')
cols = df_ESMIA.columns.to_list()
cols.remove('Unnamed: 0')
cols.remove('Unnamed: 1')
for col in cols:
    df_ESMIA[col].loc[df_ESMIA['Unnamed: 0'] == 'schedule'] = df_ESMIA[col].loc[df_ESMIA['Unnamed: 0'] == 'schedule'].astype(float)
book = openpyxl.load_workbook(DATA_PATH / 'SILVER-Toronto_Results.xlsx')
writer = pd.ExcelWriter(DATA_PATH / 'SILVER-Toronto_Results.xlsx' , mode = 'a' , engine = 'openpyxl' , if_sheet_exists = 'replace')
writer.book = book
writer.sheets = dict((ws.title, ws) for ws in book.worksheets)
df_ESMIA.to_excel(writer , sheet_name='ofp_result_final' , index = False)
writer.save()

print("total time csv to excel : %f seconds " % (perf_counter() - starttime))

