from __future__ import print_function
from __future__ import absolute_import
from __future__ import division
#####################################################################
from builtins import str
from builtins import range
from past.utils import old_div
from builtins import object
from SILVER_VER_18.config_variables import *
from SILVER_VER_18.visualization import *
import SILVER_VER_18.internal_vars as internal_vars
from .minpower.solve import solve_problem
from SILVER_VER_18.minpower.commonscripts import remove_leading_zeros_time_formatting

#####################################################################
#########################
# IMPORT PACKAGES
#########################
import pandas as pd
import numpy as np
from datetime import datetime
from numpy import poly1d, sqrt, exp
from scipy import stats
import sys
import shutil
import os
import openpyxl

''' Class to make print statements appear in both the command prompt as well as write to a text file '''


class Tee(object):
    def __init__(self, *files):
        self.files = files

    def write(self, obj):
        for f in self.files:
            f.write(obj)
            f.flush()

    def flush(self):
        for f in self.files:
            f.flush()


f = open(SCENARIO_NUMBER + '_print_statements.txt', 'a')  # 'a' instead of 'w' to append to existing file
original = sys.stdout
sys.stdout = Tee(sys.stdout, f)


def format_UC_results(load_sched_2, demand_response, uc_commit):
    load_sched_2 = load_sched_2.astype('object')
    demand_response = demand_response.astype('object')
    uc_commit = uc_commit.astype('object')
    UC_results = pd.merge(uc_commit, load_sched_2, how='left', right_on='date', left_index=True, right_index=False)
    UC_results = UC_results.set_index(['date'])
    demand_response = demand_response.reset_index()
    demand_response = demand_response.set_index(['date'])
    UC_results = UC_results.astype('object')
    demand_response.index = demand_response.index.astype('object')
    UC_results = pd.merge(UC_results, demand_response, how='left', on='date')
    UC_results['Total_Generation'] = UC_results['demand_fc'] - UC_results['dr']
    return UC_results


def create_uc_generators_csv(all_plants: pd.DataFrame, folder: Path):
    if not UC_NETWORKED:
        all_plants.to_csv(folder.joinpath('generators.csv'), index=True)
    else:
        all_plants.to_csv(folder.joinpath('generators.csv'), index=False)


def create_uc_loads_csv():
    # FIX loads = pd.DataFrame({'name': ['load'], 'schedule filename': ['load_schedule_fc.csv']})
    loads = pd.DataFrame({'name': ['load'], 'schedule filename': ['load_schedule_real.csv']})
    loads.to_csv(FOLDER_UC.joinpath('loads.csv'), index=False)


def create_uc_temp_load(df_to_parse: pd.DataFrame, col_name: str, hour: int, filename: str):
    ''' create load_schedule.csv for the current hours_commitment file and dataframe for each day in UC '''

    # For testing: over-ride load_schedule to test with known wind and load schedule
    starthour = df_to_parse.index.values[0]
    date = df_to_parse.ix[(starthour + hour):(starthour + hour + HOURS_COMMITMENT - 1), 'date']

    temp_sched = df_to_parse.ix[(starthour + hour):(starthour + hour + HOURS_COMMITMENT - 1), col_name]
    temp_sched_2 = pd.concat([date, temp_sched], axis=1)
    if not UC_NETWORKED:
        temp_sched_2.to_csv(FOLDER_UC.joinpath(filename), index=False, date_format='%Y-%m-%d %H:00')  # FIX
    return temp_sched_2


def create_uc_temp_vre_csv(df_to_parse: pd.DataFrame, hour: int, start_of_period_index: int, folder: Path):
    ''' create 24-hour vre_schedule.csv files - required for UC run'''

    starthour = df_to_parse.index.values[0]

    for column in df_to_parse:
        if column != 'date':
            filename = 'vre_schedule_' + column + '.csv'

            temp_sched = df_to_parse.ix[:, column]

            date = df_to_parse.ix[:, 'date']
            temp_sched_2 = pd.concat([date, temp_sched], axis=1)
            temp_sched_2.to_csv(folder.joinpath(filename), index=False)


def read_uc_results(folder: Path):
    ''' read in results for one day of UC '''
    commit_pwr = pd.read_csv(folder / 'commitment-power.csv', index_col=0, parse_dates=True)
    commit_stat = pd.read_csv(folder / 'commitment-status.csv', index_col=0, parse_dates=True)
    commit_dr = pd.read_csv(folder / 'commitment-dr.csv', index_col=0, parse_dates=True)
    user_names = (pd.read_csv(folder / 'generators.csv', usecols=['name'], index_col=0))
    commit_stored = (pd.read_csv(folder / 'storage_ev-content.csv', index_col=0, parse_dates=True))
    commit_cascadestorage = (pd.read_csv(folder / 'commitment-cascades_storagelevel.csv', index_col=0, parse_dates=True))
    if UC_NETWORKED:
        commit_linesflows = (pd.read_csv(folder / 'commitment-linesflows.csv', index_col=0, parse_dates=True))
    else:
        commit_linesflows = pd.DataFrame
    return commit_pwr, commit_stat, commit_dr, user_names, commit_stored, commit_cascadestorage, commit_linesflows


def create_opf_lines_csv():
    ''' Create lines.csv file here since uc won't change them'''
    df_trans = pd.read_excel(PATH_MODEL_INPUTS, 'existing transmission')
    lines = pd.concat([df_trans], ignore_index=True)[['name', 'from bus', 'to bus', 'pmax', 'reactance']]
    lines.to_csv(FOLDER_OPF.joinpath('lines.csv'), index=False)
    lines.to_csv(FOLDER_PRICE_OPF.joinpath('lines.csv'), index=False)
    if UC_NETWORKED:
        lines.to_csv(FOLDER_UC.joinpath('lines.csv'), index=False)
    return lines


def create_opf_loads_df(df_regions: pd.DataFrame):
    ''' Create loads dataframe, but don't create loads.csv file since it will change'''
    return pd.DataFrame({'name': df_regions['city'], 'bus': df_regions['bus'], 'dr_potential': df_regions['dr_potential']})


def loads_opf_csv(df_regions: pd.DataFrame,
                  loads: pd.DataFrame,
                  folder: Path,
                  hour: int,
                  demand_schedule: pd.DataFrame):
    ''' create loads.csv file by splitting full demand by population fraction '''
    # Demand data is read from demand_schedule_real_by_region, which is split into subregions
    # based on the frac_pop column of df_regions. This data is then appended to the
    # loads data frame under a new 'power' column. This is written to a csv, and then
    # returned.
    loads_copy = loads.copy()

    # Copy regional demands for given hour
    demands = demand_schedule.loc[hour].copy()

    # Copy population fraction data
    fractions = df_regions[['region', 'city', 'frac pop']].set_index(['region', 'city']).copy()

    # Filter to only include demand data and match fractions df
    demands = demands.reindex(fractions.index, level='region')

    # Split regional demands by population fractions
    fractions['power'] = (demands * fractions['frac pop']).astype('float')

    fractions.reset_index(inplace=True)

    # Merge split demand data into loads
    new_loads = pd.merge(loads_copy, fractions[['city', 'power']], left_on='name', right_on='city').drop('city', axis=1)

    new_loads[['name', 'bus', 'power', 'dr_potential']].to_csv(folder / 'loads.csv', index=False)

    return new_loads


def loads_uc_csv(df_regions, loads, folder, demand_schedule_real_by_region):
    ''' create loads.csv file by splitting full demand by population fraction '''
    load_region_2, load_region_3 = [], []
    loads_3 = loads.copy()

    city_load = pd.DataFrame()
    for index in range(len(df_regions.index)):
        city_region = df_regions.ix[index]['region']
        frac_pop = df_regions.ix[index]['frac pop']
        regional_demand = demand_schedule_real_by_region[city_region]
        city_demand = regional_demand * frac_pop
        load_region_3.append(city_demand)
        city_load.ix[:, df_regions.ix[index]['city']] = city_demand
    loads_3['power'] = load_region_3

    load_region_2.extend(
        demand_schedule_real_by_region[d_center]
        for d_center in demand_schedule_real_by_region.columns.values
        if d_center != 'date')

    index_list = []
    for H in range(HOURS_COMMITMENT + 1):  # FIX for fc need +1
        if H < 10:
            index_list.insert(H, f't0{str(H)}')
        else:
            index_list.insert(H, f't{str(H)}')
    city_load.index = index_list
    loads_3[['name', 'bus', 'dr_potential']].to_csv(folder.joinpath('loads.csv'), index=False)
    city_load.to_csv(folder.joinpath('load_schedule_real.csv'), index=True)  # FIX
    return


def opf_csv(all_plants, plants_bus, vre_plants):
    ''' augment all_plants with OPF variables (generator bus) that are NOT over-written for each day '''
    all_plants['name'] = all_plants.index         # add name column back in
    plants_bus = pd.DataFrame(plants_bus)

    # Add bus informaton to all_plants, but don't create generators.csv since it will change:
    if not vre_plants.empty:
        vre_buses_lst = [{
            'bus': vre_plants.ix['bus'][plant],
            'key': vre_plants.ix['name'][plant]}
            for plant in range(len(vre_plants.columns))]

        vre_buses = pd.DataFrame.from_dict(vre_buses_lst)
        vre_buses.index = vre_buses['key']
        vre_buses = vre_buses.drop('key', axis=1)

        buses = pd.concat([plants_bus, vre_buses])
    else:
        buses = plants_bus

    all_plants['bus'] = buses

    return all_plants


################################################################################################################
# CALL MINPOWER OPF and UC and OPF
################################################################################################################


def save_generators_csv_for_second_opf(attempt_number, hour, timestamp, uc_commit, commit_stat, generators_opf,
                                       vre_real, input_for_next_opf):
    '''Create generators.csv file using UC to set pmin and pmax in opf'''
    # uc determins the status of generators, this means whether the generators are on or off, secondly how much the generators can ramp up or down
    # determins max and min ramp based on uc outputs
    for plant in range(len(uc_commit.columns)):
        col_name = uc_commit.columns[plant]               # col_name: g0, g1, g2...
        name_mm = generators_opf.ix[col_name]['name']     # name_mm: Niagara, Wild horse...

        rr_max = uc_commit[col_name]['ramp rate max']
        addToRR = uc_commit[col_name][timestamp] if hour == 0 else input_for_next_opf.ix[name_mm, 'P']

        if uc_commit[col_name]['kind'] in ['Wind', 'Solar']:
            generators_opf.loc[col_name, 'pmax'] = (vre_real[name_mm]).values
            generators_opf.loc[col_name, 'pmin'] = 0
        else:

            set_min = min(
                uc_commit.loc['pmax', col_name],
                addToRR + (rr_max))

            set_max = max(
                uc_commit.loc['pmin', col_name],
                addToRR - (rr_max))

            if commit_stat[col_name][timestamp] == 0 and uc_commit[col_name]['kind'] not in ['PHS', 'LB', 'EV']:
                generators_opf.loc[col_name, 'pmax'] = 0
                generators_opf.loc[col_name, 'pmin'] = 0

            else:
                generators_opf.loc[col_name, 'pmax'] = set_min
                generators_opf.loc[col_name, 'pmin'] = set_max

    try:

        generators_opf = generators_opf.drop(['ramp rate max', 'ramp rate min', 'start up ramp limit',
                                              'shut down ramp limit', 'start up cost', 'shut down cost',
                                              'min up time', 'min down time'], axis=1)
    except:
        pass

    generators_opf.to_csv(FOLDER_OPF / 'generators.csv', index=False)


def check_datetime_format(df: pd.DataFrame, filename: str):
    try:
        valid_format = datetime.strptime(df.iloc[0]['date'], '%m/%d/%Y %H:%M')

        if len(df) > 23:
            check_second = datetime.strptime(df.iloc[24]['date'], '%m/%d/%Y %H:%M')
            if check_second.day - valid_format.day != 1:
                print("Check that day is properly iterating after 24 hours")
                raise ValueError
    except ValueError:
        print(f"{filename} does not follow the proper m/d/yyyy h:mm format")
        sys.exit()


def modify_hourly_df(startdate: str, enddate: str, df: pd.DataFrame):
    index_startdate = df[df['date'] == str(startdate)].index.tolist()
    index_startdate = index_startdate[0]
    index_enddate = df[df['date'] == str(enddate)].index.tolist()
    index_enddate = index_enddate[0]

    df = df.iloc[index_startdate: index_enddate]

    index_list = []
    for H in range(HOURS_COMMITMENT):
        if H < 10:
            index_list.insert(H, f't0{H}')
        else:
            index_list.insert(H, f't{H}')
    df.index = index_list
    return df


def add_hydro_monthly_energy_capacity(all_plants: pd.DataFrame, current_period_run: int):

    try:
        Hydro_monthly_df = pd.read_csv(PATH_HYDRO / 'hydro_monthly.csv', index_col=0,
                                                    header=0)
    except FileNotFoundError:
        path_hydro_loc = f"{{repoDirectory}}\{PATH_HYDRO.relative_to(REPO_ROOT)}"
        print("Case study does not have hydro_monthly, "
              "if this is incorrect the file is expected to be in " + path_hydro_loc)
        return

    try:
        # Calculating which month we are currently in since period is variable
        # When this function is called, period length should be equal to one month
        index_name = f'month_{current_period_run}'

        Hydro_monthly = pd.DataFrame()

        for plant in all_plants.index:
            if all_plants['kind'][plant] == 'hydro_monthly':
                Hydro_monthly.ix[index_name, plant] = Hydro_monthly_df.ix[index_name, plant]

        if Hydro_monthly.empty:
            del Hydro_monthly
            print("WARNING: hydro_monthly.csv given but no hydro_monthly data found in model inputs")
        else:
            Hydro_monthly.to_csv(FOLDER_UC / 'hydro_monthly.csv', index=True)

    except Exception as e:
        print("Could not process hydro_monthly, CSV not created")
        print(e)
        sys.exit()


def add_hydro_daily_energy_capacity(all_plants: pd.DataFrame, current_period_run: int):

    try:
        Hydro_daily_df = pd.read_csv(PATH_HYDRO / 'hydro_daily.csv', header=0)
    except FileNotFoundError:
        path_hydro_loc = f"{{repoDirectory}}\{PATH_HYDRO.relative_to(REPO_ROOT)}"
        print("Case study does not have hydro_daily, "
              "if this is incorrect the file is expected to be in " + path_hydro_loc)
        return

    try:
        Number_of_days_per_commit = NUMBER_OF_DAYS
        startdate = f'day_{(current_period_run - 1) * Number_of_days_per_commit + 1}'
        enddate = f'day_{(current_period_run - 1) * Number_of_days_per_commit + Number_of_days_per_commit}'

        index_startdate = Hydro_daily_df[Hydro_daily_df['date'] == str(startdate)].index.tolist()
        index_startdate = index_startdate[0]
        index_enddate = Hydro_daily_df[Hydro_daily_df['date'] == str(enddate)].index.tolist()
        index_enddate = index_enddate[0]

        Hydro_daily_df = Hydro_daily_df.iloc[index_startdate: index_enddate + 1]

        index_list = []
        for H in (range(1, NUMBER_OF_DAYS + 1)):
            index_list.insert(H, f'day_{H}')
        Hydro_daily_df.index = index_list

        Hydro_daily = pd.DataFrame()

        for plant in all_plants.index:
            if all_plants['kind'][plant] == 'hydro_daily':
                Hydro_daily.ix[:, plant] = Hydro_daily_df.ix[:, plant]

        if Hydro_daily.empty:
            del Hydro_daily
            print("WARNING: hydro_daily.csv given but no hydro_daily data found in model inputs")
        else:
            Hydro_daily.to_csv(FOLDER_UC / 'hydro_daily.csv', index=True)

    except Exception as e:
        print("Could not process hydro_daily, CSV not created")
        print(e)
        sys.exit()


def add_hydro_hourly_energy_capacity(all_plants, startdate, enddate):
    try:
        Hydro_hourly_df = pd.read_csv(PATH_HYDRO / 'hydro_hourly.csv',
                                      header=0)
    except FileNotFoundError:
        path_hydro_loc = f"{{repoDirectory}}\{PATH_HYDRO.relative_to(REPO_ROOT)}"
        print("Case study does not have hydro_hourly, "
              "if this is incorrect the file is expected to be in " + path_hydro_loc)
        return

    check_datetime_format(Hydro_hourly_df, "hydro_hourly.csv")

    try:
        startdate, enddate = remove_leading_zeros_time_formatting(startdate, enddate)
        Hydro_hourly_df = modify_hourly_df(startdate, enddate, Hydro_hourly_df)

        Hydro_hourly = pd.DataFrame()

        for plant in all_plants.index:
            if all_plants['kind'][plant] == 'hydro_hourly':
                Hydro_hourly.ix[:, plant] = Hydro_hourly_df.ix[:, plant]

        if Hydro_hourly.empty:
            del Hydro_hourly
            print("WARNING: hydro_hourly.csv given but no hydro_hourly data found in model inputs")
        else:
            Hydro_hourly.to_csv(FOLDER_UC / 'hydro_hourly.csv', index=True)

    except Exception as e:
        print("Could not process hydro_hourly, CSV not created")
        print(e)
        sys.exit()


def add_hydro_cascade_energy_capacity(all_plants, startdate, enddate):
    try:
        Hydro_cascade_df = pd.read_csv(PATH_HYDRO / 'hydro_cascade.csv',
                                       header=0)
    except FileNotFoundError:
        path_hydro_loc = f"{{repoDirectory}}\{PATH_HYDRO.relative_to(REPO_ROOT)}"
        # print("Case study does not have hydro_cascade, " \
        #     "if this is incorrect the file is expected to be in " + path_hydro_loc)
        return

    check_datetime_format(Hydro_cascade_df, "hydro_cascade.csv")

    try:
        startdate, enddate = remove_leading_zeros_time_formatting(startdate, enddate)
        Hydro_cascade_df = modify_hourly_df(startdate, enddate, Hydro_cascade_df)

        Hydro_cascade = pd.DataFrame()

        for plant in all_plants.index:
            if all_plants['kind'][plant] == 'cascade':
                Hydro_cascade.ix[:, plant] = Hydro_cascade_df.ix[:, plant]

        if Hydro_cascade.empty:
            del Hydro_cascade
            print("WARNING: hydro_cascade.csv given but no hydro_cascade data found in model inputs")
        else:
            Hydro_cascade.to_csv(FOLDER_UC / 'hydro_cascade.csv', index=True)

    except Exception as e:
        print("Could not process hydro_cascade, CSV not created")
        print(e)
        sys.exit()


def add_importexport_energy(all_plants, startdate, enddate):

    try:
        importexport_hourly_df = pd.read_csv(PATH_IMPORTEXPORT / 'importexport_hourly.csv',
                                             header=0)
    except FileNotFoundError:
        path_importexport_loc = f"{{repoDirectory}}\{PATH_IMPORTEXPORT.relative_to(REPO_ROOT)}"
        print("Case study does not have importexport_hourly, "
              "if this is incorrect the file is expected to be in " + path_importexport_loc)
        return

    check_datetime_format(importexport_hourly_df, "importexport_hourly.csv")

    try:
        startdate, enddate = remove_leading_zeros_time_formatting(startdate, enddate)
        importexport_hourly_df = modify_hourly_df(startdate, enddate, importexport_hourly_df)

        importexport_hourly = pd.DataFrame()

        for plant in all_plants.index:
            if all_plants['kind'][plant] == 'importexport':
                importexport_hourly.ix[:, plant] = importexport_hourly_df.ix[:, plant]

        if importexport_hourly.empty:
            del importexport_hourly
            print("WARNING: importexport_hourly.csv given but no importexport data found in model inputs")
        else:
            importexport_hourly.to_csv(FOLDER_UC / 'importexport_hourly.csv', index=True)

    except Exception as e:
        print("Could not process importexport_hourly, CSV not created")
        print(e)
        sys.exit()


def call_mp(all_plants, df_regions, loads, lines, demand_schedule_real,
            vre_timeseries_final, vre_timeseries_fc, startdate, enddate, start_of_period_index,
            list_of_failed_uc_days, demand_schedule_fc, demand_schedule_fc_by_region, demand_schedule_real_by_region):
    
    current_period_run = start_of_period_index + 1
    # CALL 1: establish dataframes to concatenate minpower outputs over multiple runs
    finalcommit = pd.DataFrame({'g0': []})
    energy_storage = pd.DataFrame()
    cascade_storage = pd.DataFrame()
    all_plants_T = all_plants.T

    load_sched = pd.DataFrame()
    demand_response = pd.DataFrame()

    runtime_hours = HOURS_COMMITMENT
    schedule_gen_frames_price, schedule_load_frames_price, schedule_lines_frames_price = [], [], []
    schedule_gen_frames_final, schedule_load_frames_final, schedule_lines_frames_final = [], [], []
    schedule_load_real = pd.DataFrame()
    all_plants_uc = all_plants.copy()
    opf_cost_total = pd.DataFrame()

    all_plants_4 = all_plants_uc.drop('name', 1)
    all_plants_4 = all_plants_4.drop('bus', 1)
    add_hydro_monthly_energy_capacity(all_plants_4, current_period_run)
    add_hydro_daily_energy_capacity(all_plants_4, current_period_run)
    add_hydro_hourly_energy_capacity(all_plants_4, startdate, enddate)
    # add_hydro_cascade_energy_capacity(all_plants_4, startdate, enddate)
    add_importexport_energy(all_plants_4, startdate, enddate)

    # Entire scenario loop: starting at hour 0
    # how long to run - ultimately however long is specified by start/end years
    hourIteration = 0

    while hourIteration < runtime_hours:

        # Cuts schedule to include date and total demand for province
        temp_load_sched_real = create_uc_temp_load(demand_schedule_real, 'demand', hourIteration, 'load_schedule_real.csv')
        temp_load_sched_fc = create_uc_temp_load(demand_schedule_fc, 'demand_fc', hourIteration, 'load_schedule_fc.csv')
        create_uc_temp_vre_csv(vre_timeseries_fc, hourIteration, start_of_period_index, FOLDER_UC)
        if UC_NETWORKED:
            loads_uc_csv(df_regions, loads, FOLDER_UC, demand_schedule_fc_by_region)

        if RUN_OPF:

            create_uc_temp_vre_csv(vre_timeseries_fc, hourIteration, start_of_period_index, FOLDER_PRICE_OPF)
            internal_vars.UC_LMP = False
            internal_vars.opf_variable = 'price'
            internal_vars.OPF_Price = True

            Price_OPF(all_plants, df_regions, loads, lines, vre_timeseries_fc,
                      demand_schedule_fc_by_region, schedule_gen_frames_price,
                      schedule_load_frames_price, schedule_lines_frames_price, temp_load_sched_fc)

        print('################ Day-ahead UC PROBLEM ####################')

        internal_vars.UC_LMP = True
        internal_vars.OPF_Price = False

        # calls solve for the UC problem, problem is specified by the contents of the folder alone currently
        solve_problem(FOLDER_UC)

        ''' in this implementation, the UC solution for the previous day is used if the UC fails to complete in the aloted time'''
        commit_pwr, commit_stat, commit_dr, user_names, commit_stored, commit_cascadestorage, commit_linesflows = read_uc_results(FOLDER_UC)

        uc_failure = commit_pwr.sum(axis=1)
        if uc_failure.sum() == 0:
            print('\t', '\t', "UC FAILED sum of generation over optimizaion period:", uc_failure.sum())

        commit_pwr.index = temp_load_sched_real['date']
        commit_stat.index = temp_load_sched_real['date']
        commit_dr.index = temp_load_sched_real['date']
        column_order = commit_pwr.columns
        commit_stored.index = temp_load_sched_real['date']
        commit_cascadestorage.index = temp_load_sched_real['date']

        ''' add to accumulating dataframes of: uc power, load schedule, demand response, electricity price'''
        finalcommit = pd.concat([finalcommit, commit_pwr])      # running total of uc commitment for multiple days
        finalcommit = finalcommit[column_order]
        load_sched = pd.concat([load_sched, temp_load_sched_fc])
        demand_response = pd.concat([demand_response, commit_dr])
        energy_storage = pd.concat([energy_storage, commit_stored])
        cascade_storage = pd.concat([energy_storage, commit_cascadestorage])
        ''' create dataframe to build OPF from'''

        all_plants_T.columns = finalcommit.columns
        uc_commit = all_plants_T.append(finalcommit)              # all attributes and UC power values

        ''' create initial.csv for next UC iteration using last row of data from commit_pwr and commit_stored'''
        # NOTE: initial.csv for first UC run is created user inputs on model inputs.xlsx sheet
        if hourIteration >= 0:
            name_list, kind_list, power_end_list, status_end_list, hours_in_status_list, storage_content_list = [], [], [], [], [], []
            uc_commit_temp_2 = uc_commit
            uc_commit_temp = uc_commit_temp_2.transpose()
            plants_w_initial_cond_2 = uc_commit_temp[(uc_commit_temp['kind'] == 'nuclear')          # new dataframe of just non-renewable plants
                                                     | (uc_commit_temp['kind'] == 'NG simple')        # (nuclear, NG simple, NG combined, coal)
                                                     | (uc_commit_temp['kind'] == 'NG combined')
                                                     | (uc_commit_temp['kind'] == 'NG_CC')
                                                     | (uc_commit_temp['kind'] == 'NG_CG')
                                                     | (uc_commit_temp['kind'] == 'NG_CT')       # for which initial conditions matter
                                                     | (uc_commit_temp['kind'] == 'coal')
                                                     | (uc_commit_temp['kind'] == 'biomass')
                                                     | (uc_commit_temp['kind'] == 'biogas')
                                                     | (uc_commit_temp['kind'] == 'PHS')
                                                     | (uc_commit_temp['kind'] == 'LB')
                                                     | (uc_commit_temp['kind'] == 'EV')
                                                     | (uc_commit_temp['kind'] == 'Hydrogen')
                                                     | (uc_commit_temp['kind'] == 'hydro')
                                                     | (uc_commit_temp['kind'] == 'hydro_hourly')
                                                     | (uc_commit_temp['kind'] == 'hydro_daily')
                                                     | (uc_commit_temp['kind'] == 'hydro_monthly')
                                                     | (uc_commit_temp['kind'] == 'cascade')
                                                     ]
            plants_w_initial_cond = plants_w_initial_cond_2.transpose()
            for plant in plants_w_initial_cond.columns:
                name = plants_w_initial_cond.ix['name', plant]   # plant: g1, name: Bruce
                kind = plants_w_initial_cond.ix['kind', plant]
                power_end = plants_w_initial_cond.ix[len(plants_w_initial_cond) - 1, plant]
                status_end = commit_stat.ix[HOURS_COMMITMENT - 1, plant]

                hours_in_status = ((commit_stat[plant] == status_end)).sum()
                name_list.append(name)
                kind_list.append(kind)
                power_end_list.append(power_end)
                status_end_list.append(status_end)
                hours_in_status_list.append(hours_in_status)
                if (plants_w_initial_cond.ix['kind', plant] in ['EV', 'PHS', 'LB', 'Hydrogen']):
                    storage_content_list.append(commit_stored.ix[HOURS_COMMITMENT - 1, plant])

                elif (plants_w_initial_cond.ix['kind', plant] == 'Cascade'):
                    storage_content_list.append(commit_cascadestorage.ix[HOURS_COMMITMENT - 1, plant])

                else:
                    storage_content_list.append(0)

            uc_initial_conditions = pd.concat([pd.Series(name_list), pd.Series(kind_list), pd.Series(power_end_list),
                                               pd.Series(status_end_list), pd.Series(hours_in_status_list),
                                               pd.Series(storage_content_list)],
                                              axis=1)
            uc_initial_conditions.columns = ['name', 'kind', 'power', 'status', 'hours in status', 'storage content']
            if uc_failure.sum() == 0:
                print("\nERROR: last month failed, exiting")
                sys.exit()
            else:
                uc_initial_conditions.to_csv(FOLDER_UC / 'initial.csv')

            ''' Set up dataframe that will become generators.csv file for OPF (updated for each hour below in OPF)'''

        generators_opf = all_plants.drop(set(['pmax', 'schedule filename', 'shedding_allowed']), axis=1)  # , 'schedule filename', 'shedding_allowed']), axis=1)
        generators_opf['pmax'] = np.NaN
        generators_opf['pmin'] = np.NaN
        minpower_names = finalcommit.columns.tolist()  # minpower_names: ['g0', 'g1', 'g2', ...]
        generators_opf.index = minpower_names

        internal_vars.UC_LMP = False

        ####################### OPF REALTIME START ############################

        if RUN_OPF:
            (opf_cost_total,
             opf_gen_total,
             opf_dr_total,
             finalload,
             opf_lines_total) = realtime_opf(temp_load_sched_real,
                                             finalcommit,
                                             vre_timeseries_final,
                                             df_regions,
                                             loads,
                                             demand_schedule_real_by_region,
                                             commit_dr,
                                             uc_commit,
                                             commit_stat,
                                             generators_opf,
                                             lines,
                                             hourIteration)

    ####################### OPF REALTIME END ############################
        hourIteration += HOURS_COMMITMENT

    ####################### End of While loop ###########################

    if RUN_OPF:
        ''' create daily opf results and append to previsou days' schedules '''
        schedule_load = create_load_schedule(finalload, opf_dr_total)
        info_gen, info_lines, info_loads = create_opf_result_csv_header(all_plants, loads, lines, schedule_load)
        schedule_gen = create_gen_schedule(opf_gen_total, info_gen)
        schedule_gen_frames_final.append(schedule_gen)
        schedule_load_frames_final.append(schedule_load)
        schedule_load_real = schedule_load_real.append(temp_load_sched_real)
        schedule_lines = create_lines_schedule(opf_lines_total)
        schedule_lines_frames_final.append(schedule_lines)

        '''concatenate full schedule with info for both opfs (price and final) '''
        tgen = pd.concat([info_gen, pd.concat(schedule_gen_frames_final)], keys=['info', 'schedule'])
        tloads = pd.concat([info_loads, pd.concat(schedule_load_frames_final)], keys=['info', 'schedule'])
        tlines = pd.concat([info_lines, pd.concat(schedule_lines_frames_final)], keys=['info', 'schedule'])
        tgen_price = pd.concat([info_gen, pd.concat(schedule_gen_frames_price)], keys=['info', 'schedule'])
        tloads_price = pd.concat([info_loads, pd.concat(schedule_load_frames_price)], keys=['info', 'schedule'])
        tlines_price = pd.concat([info_lines, pd.concat(schedule_lines_frames_price)], keys=['info', 'schedule'])

        ''' send results to csv files'''
        result = pd.concat([tgen, tloads, tlines], axis=1, keys=['generator', 'load', 'line'])
        result.to_csv(FOLDER_OPF / 'opf_result_final.csv')
        result_price = pd.concat([tgen_price, tloads_price, tlines_price], axis=1, keys=['generator', 'load', 'line'])
        result_price.to_csv(FOLDER_PRICE_OPF / 'opf_result_price.csv')
        # export opf results to CSV and visualize
        result.to_csv(FOLDER_TO_SAVE_FINAL_RESULTS / (f'OPF_Results_{startdate.date()}_{enddate.date()}.csv'))
        if USER_STARTDATE == startdate:
            result_ESMIA = pd.concat([tgen, tloads, tlines], axis=1, keys=['generator', 'load', 'line'])
            result_ESMIA.to_csv(FOLDER_TO_SAVE_FINAL_RESULTS / '0_OPF_Results_ESMIA.csv')
        else:
            result_ESMIA = pd.concat([tgen, tloads, tlines], axis=1, keys=['generator', 'load', 'line'])
            result_ESMIA.iloc[6:30].to_csv(FOLDER_TO_SAVE_FINAL_RESULTS / '0_OPF_Results_ESMIA.csv' , mode = 'a' , header=False)
            
        # export price setting opf results
        result_price.to_csv(FOLDER_TO_SAVE_FINAL_RESULTS / (f'First_OPF_Results_{startdate.date()}_{enddate.date()}.csv'))

        # export results for realtime cost of generation
        opf_cost_total.to_csv(FOLDER_TO_SAVE_FINAL_RESULTS / (f'OPF_Costs_{startdate.date()}_{enddate.date()}.csv'))

    row_index = pd.DataFrame(index=[all_plants.columns])
    load_sched.index = demand_response.index  # commit_pwr.index
    load_sched_2 = row_index.append(load_sched)

    UC_results = format_UC_results(load_sched_2, demand_response, uc_commit)

    # Save all results to csv files and visualize
    uc_commit.to_csv(f'{FOLDER_UC}/final_commitment.csv')

    # export UC results and visualize UC results
    UC_results.to_csv(f'{FOLDER_TO_SAVE_FINAL_RESULTS}/UC_Results_{str(startdate.date())}_{str(enddate.date())}.csv')
    #visualize_UC(f'{FOLDER_TO_SAVE_FINAL_RESULTS}/UC_Results_{str(startdate.date())}_{str(enddate.date())}.csv')

    # export line flow
    if UC_NETWORKED:
        commit_linesflows.to_csv(f'{FOLDER_TO_SAVE_FINAL_RESULTS}/UC_Line_Flow_{str(startdate.date())}_{str(enddate.date())}.csv')

    energy_storage.to_csv(FOLDER_TO_SAVE_FINAL_RESULTS / (f'Energy_Storage_{startdate.date()}_{enddate.date()}.csv'))

    new_col_names = uc_commit.ix['name'].tolist()
    new_col_names_2 = [x for x in new_col_names if str(x) != 'nan']
    finalcommit.columns = new_col_names_2

    return finalcommit, list_of_failed_uc_days

################################################################################################################
# FUNCTIONS THAT PERTAIN TO OPF RUNS (both)
#                   > make blank dataframes, clear folders,
#                   > save lines df and csv, make loads df, make loads csv
#                   > read opf results
################################################################################################################


def create_blank_df_OPF():
    '''create blank df for OPF runs'''
    opf_gen_total = pd.DataFrame({'hour': [], 'generator name': [], 'u': [], 'P': [], 'IC': []})
    opf_lines_total = pd.DataFrame({'hour': [], 'from': [], 'to': [], 'power': [], 'congestion shadow price': [], 'name': []})
    finalload = pd.DataFrame({'bus': [], 'name': [], 'power': [], 'hour': []})
    opf_dr_total = pd.DataFrame()
    opf_LMP_total = pd.DataFrame({'hour': []})
    return opf_gen_total, opf_lines_total, finalload, opf_dr_total, opf_LMP_total


def clear_folder(folder: Path):
    '''clear folders for OPF runs'''
    try:
        os.remove(folder / 'generators.csv')
        os.remove(folder / 'loads.csv')
    except:
        pass


def read_opf_results(folder: Path, price: bool = False):
    opf_gen = pd.read_csv(folder / 'powerflow-generators.csv')
    opf_dr = pd.read_csv(folder / 'powerflow-dr.csv')
    opf_lines = pd.read_csv(folder / 'powerflow-lines.csv')
    if price:
        opf_LMP = pd.read_csv(folder / 'powerflow-LMP.csv')
        return opf_gen, opf_dr, opf_lines, opf_LMP

    return opf_gen, opf_dr, opf_lines


def total_gen_check(opf_gen, timestamp):
    if opf_gen['P'].sum() == 0:
        print('\t', "OPF: Error in hour:", timestamp, "sum of generation:", opf_gen['P'].sum())
    return opf_gen['P'].sum()


def dr_check(opf_dr, opf_loads):
    total_load = opf_loads['power'].sum()
    total_dr = opf_dr['dr_2'].sum()
    if abs(total_dr) > total_load * 0.30001:
        print("dr is:", total_dr, "load is:", total_load, "dr exceeds 20 percent of load")


def concatenate_opf_results(timestamp, opf_gen, opf_gen_total, opf_dr, opf_dr_total,
                            loads, finalload, lines, opf_lines_total, opf_lines, opf_LMP_total=None, opf_LMP=None):
    ''' concatenate current (hourly) opf result with previous opf results'''
    opf_gen.index = opf_gen['generator name']
    opf_gen['hour'] = timestamp
    opf_gen_total = pd.concat([opf_gen_total, opf_gen], ignore_index=True)

    opf_dr_2 = opf_dr.transpose()
    opf_dr_2.columns = opf_dr_2.ix['bus']
    opf_dr_3 = opf_dr_2.drop('bus')
    opf_dr_total = pd.concat([opf_dr_total, opf_dr_3], ignore_index=True)

    loads['hour'] = timestamp
    finalload = pd.concat([finalload, loads], ignore_index=True)
    loads.drop('hour', axis=1, inplace=True)

    opf_lines['hour'] = timestamp
    opf_lines['name'] = lines.index  # lines['name']
    opf_lines_total = pd.concat([opf_lines_total, opf_lines], ignore_index=True)

    if opf_LMP is not None:
        opf_LMP['hour'] = timestamp
        opf_LMP_total = pd.concat([opf_LMP_total, opf_LMP], ignore_index=True)
        return opf_gen_total, opf_dr_total, finalload, opf_lines_total, opf_LMP_total

    return opf_gen_total, opf_dr_total, finalload, opf_lines_total


def create_opf_result_csv_header(all_plants, loads, lines, schedule_load):
    info_gen = all_plants[['bus', 'kind']].rename(columns={'bus': 'bus', 'kind': 'kind'}).transpose()
    info_lines = lines[['from bus', 'to bus']].transpose()

    list_names, load_names, bus_names = [], [], []
    for row in range(len(loads.index)):
        loads.loc[row, 'name dr'] = str(loads.ix[row]['name'] + str('_dr'))
        list_names.append(str(loads.ix[row]['name'] + str('_dr')))
    loads['name dr'] = list_names

    for column in schedule_load.columns.values:
        if column in loads['name'].unique():
            load_names.append(column)
            bus_name_2 = str(loads[loads.name.isin([column])]['bus'].values)
            bus_names.append(bus_name_2)
        elif column in loads['bus'].unique():
            bus_names.append(column)
            load_names_2 = str(loads[loads.bus.isin([column])]['name dr'].values)
            load_names.append(load_names_2)

    bus_names_df = pd.DataFrame([bus_names], index=['bus_2'])
    load_names_df = pd.DataFrame([load_names], index=['b_name'])
    bus_names_df.columns = schedule_load.columns
    load_names_df.columns = schedule_load.columns
    info_loads = pd.concat([bus_names_df, load_names_df], ignore_index=False)
    return info_gen, info_lines, info_loads


def create_gen_schedule(opf_gen_total, info_gen):
    schedule_gen = opf_gen_total[['hour', 'generator name', 'P']].pivot(index='hour', columns='generator name', values='P')
    cols = info_gen.columns
    schedule_gen = schedule_gen[cols]
    return schedule_gen


def create_load_schedule(finalload, opf_dr_total):
    schedule_load = finalload[['hour', 'name', 'power']].pivot(index='hour', columns='name', values='power')
    opf_dr_total.index = schedule_load.index
    schedule_load = pd.concat([schedule_load, opf_dr_total], axis=1)
    return schedule_load


def create_lines_schedule(opf_lines_total):
    schedule_lines = opf_lines_total[['hour', 'name', 'power']].pivot(index='hour', columns='name', values='power')
    return schedule_lines


def create_LMP_schedule(opf_LMP_total):
    schedule_LMP = opf_LMP_total[['hour', 'bus', 'LMP']].pivot(index='hour', columns='bus', values='LMP')
    return schedule_LMP


def Price_OPF(all_plants: pd.DataFrame,
              df_regions: pd.DataFrame,
              loads: pd.DataFrame,
              lines: pd.DataFrame,
              vre_timeseries_fc: pd.DataFrame,
              demand_schedule_fc_by_region: pd.DataFrame,
              schedule_gen_frames_price: pd.DataFrame,
              schedule_load_frames_price: pd.DataFrame,
              schedule_lines_frames_price: pd.DataFrame,
              temp_load_sched_fc: pd.DataFrame):

    opf_gen_total, opf_lines_total, finalload, opf_dr_total, opf_LMP_total = create_blank_df_OPF()  # re-set pd dataframe creation
    for hour in range(HOURS_COMMITMENT):
        hour_2 = temp_load_sched_fc.index.values[0] + hour
        timestamp = temp_load_sched_fc.ix[hour_2, 'date']
        clear_folder(FOLDER_PRICE_OPF)

        '''create hourly opf loads.csv '''
        opf_loads = loads_opf_csv(df_regions, loads, FOLDER_PRICE_OPF, hour_2,
                                  demand_schedule_fc_by_region)

        ''' create hourly opf generators.csv: set vre, storave and EV pmax, remove non-opf columns '''
        for plant in range(len(all_plants.index)):
            name_2 = all_plants.index[plant]
            vre_timeseries_fc_2 = vre_timeseries_fc.drop('date', 1)

            vre_timeseries_fc_2.index = range(HOURS_COMMITMENT)

            if (all_plants['kind'][name_2] == 'Wind') or (all_plants['kind'][name_2] == 'Solar'):
                all_plants.loc[name_2, 'pmax'] = (vre_timeseries_fc_2[name_2][hour])

            if (all_plants['kind'][name_2] in ['PHS', 'LB', 'EV']):
                all_plants.loc[name_2, 'pmax'] = 0
        all_plants_2 = all_plants.drop('schedule filename', 1)
        all_plants_2 = all_plants_2.drop('name', 1)
        all_plants_2 = all_plants_2.drop('shedding_allowed', 1)
        all_plants_2.to_csv(FOLDER_PRICE_OPF / 'generators.csv', index=True)

        ''' solve opf and record results'''
        solve_problem(FOLDER_PRICE_OPF)
        # NOTE LMP most likely refers to price
        opf_gen, opf_dr, opf_lines, opf_LMP = read_opf_results(FOLDER_PRICE_OPF, price=True)

        ''' check for potential errors in scenario '''
        # NOTE this may be a good place to add additional input checks made by Nathan and Jake
        opf_gen_sum = total_gen_check(opf_gen, timestamp)
        if opf_gen_sum == 0:
            print('\t', "Price OPF in hour", hour, "FAILED")
            dr_check(opf_dr, opf_loads)

        ''' concatenate hourly opf results'''
        opf_gen_total, opf_dr_total, finalload, opf_lines_total, opf_LMP_total = concatenate_opf_results(
            timestamp, opf_gen, opf_gen_total, opf_dr, opf_dr_total, opf_loads, finalload, lines, opf_lines_total, opf_lines, opf_LMP_total, opf_LMP)

    internal_vars.OPF_Price = False

    ''' create daily opf results and append to previous days' schedule'''
    schedule_load_price = create_load_schedule(finalload, opf_dr_total)
    info_gen, info_lines, info_loads, = create_opf_result_csv_header(all_plants, loads, lines, schedule_load_price)
    schedule_gen_price = create_gen_schedule(opf_gen_total, info_gen)
    schedule_lines_price = create_lines_schedule(opf_lines_total)
    schedule_LMP_price = create_LMP_schedule(opf_LMP_total)

    schedule_gen_frames_price.append(schedule_gen_price)
    schedule_load_frames_price.append(schedule_load_price)
    schedule_lines_frames_price.append(schedule_lines_price)

    Bus_name = pd.read_csv(FOLDER_PRICE_OPF / 'Bus_name.csv')
    schedule_LMP_price.columns = Bus_name.ix[:, 'bus_name']

    '''determing hourly price from OPF runs'''
    timeseries_result = schedule_gen_price

    index_list = []
    for H in (range(HOURS_COMMITMENT)):
        if H < 10:
            index_list.insert(H, f't0{H}')
        else:
            index_list.insert(H, f't{H}')

    schedule_LMP_price.index = index_list
    gen_index = []
    for gen in range((len(all_plants_2.index))):
        gen_index.append(str(gen))

    LMP_price = []
    LMP_price = pd.DataFrame(index=index_list, columns=gen_index)

    for t in schedule_LMP_price.index:
        for gen in range((len(all_plants_2.index))):
            gen_bus = all_plants_2.ix[gen, 'bus']
            time_bus = schedule_LMP_price.ix[t, gen_bus]
            LMP_price.ix[t, gen] = time_bus

    LMP_price.to_csv(FOLDER_UC / 'storage_cost_opf.csv', index=True, header=True)


def realtime_opf(temp_load_sched_real: pd.DataFrame,
                 finalcommit: pd.DataFrame,
                 vre_timeseries_final: pd.DataFrame,
                 df_regions: pd.DataFrame,
                 loads: pd.DataFrame,
                 demand_schedule_real_by_region: pd.DataFrame,
                 commit_dr: pd.DataFrame,
                 uc_commit: pd.DataFrame,
                 commit_stat: pd.DataFrame,
                 generators_opf: pd.DataFrame,
                 lines: pd.DataFrame,
                 current_hour: int):

    opf_gen_total, opf_lines_total, finalload, opf_dr_total, _ = create_blank_df_OPF()

    internal_vars.opf_variable = 'final'

    print("############  Real-time OPF ###########")

    print_df = pd.DataFrame()   # Testing to figure out why second OPF is failing
    opf_cost_total = pd.DataFrame()
    for hour in range(HOURS_COMMITMENT):

        # index for intermediary files
        index_of_current_hour = temp_load_sched_real.index.values[0] + hour  # get current hour

        timestamp = (finalcommit.index[hour + current_hour])

        timestamp = timestamp.strftime('%m/%d/%Y %H:%M')
        timestamp = timestamp.split()
        sec1 = timestamp[0]
        sec2 = timestamp[1]
        counter = 3
        if sec1[0] == '0':
            sec1 = sec1.replace('0', '', 1)
            counter = 2
        if sec1[counter] == '0':
            ax = sec1[0:counter - 1]
            ay = sec1[counter - 1:].replace('0', '', 1)
            sec1 = ax + ay
        if sec2[0] == '0':
            sec2 = sec2.replace('0', '', 1)
        timestamp = sec1 + ' ' + sec2

        clear_folder(FOLDER_OPF)  # gets row of vre data

        vre_real = vre_timeseries_final[(vre_timeseries_final['date'] == timestamp)]  # timestamp format needed = '1/1/2018 0:00'

        timestamp = datetime.strptime(timestamp, '%m/%d/%Y %H:%M')  # datetime object with given format

        ''' create loads.csv file by splitting full demand by population fraction '''

        opf_loads = loads_opf_csv(df_regions, loads, FOLDER_OPF, index_of_current_hour,
                                  demand_schedule_real_by_region)

        ''' create commitment-dr file for OPF folder - to be used in powersystems.py'''
        dr_value = commit_dr.ix[timestamp]
        dr_value.to_csv(FOLDER_OPF / 'commitment-dr.csv')

        ''' create generators.csv file '''
        attempt_number = 'first'

        if hour == 0:
            input_for_next_opf = 0

        save_generators_csv_for_second_opf(attempt_number, hour, timestamp, uc_commit, commit_stat, generators_opf, vre_real, input_for_next_opf)

        ''' call minpower '''

        solve_problem(FOLDER_OPF)

        ''' read in results from second opf '''
        opf_gen, opf_dr, opf_lines = read_opf_results(FOLDER_OPF)
        input_for_next_opf = opf_gen
        input_for_next_opf = input_for_next_opf.drop(['u'], axis=1)
        input_for_next_opf.index = input_for_next_opf['generator name']

        opf_cost = pd.read_csv(FOLDER_OPF / 'totalcost_generation.csv')

        ''' check for potential errors in scenario '''
        opf_gen_sum = total_gen_check(opf_gen, timestamp)
        if opf_gen_sum == 0:
            print_df = pd.concat([print_df, generators_opf['pmin'], generators_opf['pmax']], axis=1)
            print('\t', "    Re-calculating:", timestamp)
            attempt_number = 'second'
            save_generators_csv_for_second_opf(attempt_number, hour, timestamp, uc_commit, commit_stat, generators_opf, vre_real)
            solve_problem(FOLDER_OPF)
            opf_gen, opf_dr, opf_lines = read_opf_results(FOLDER_OPF)
            opf_gen_sum = total_gen_check(opf_gen, timestamp)

            if opf_gen_sum == 0:
                print('\t', "      Re-calculating a second time:", timestamp)
                attempt_number = 'third'
                save_generators_csv_for_second_opf(attempt_number, hour, timestamp, uc_commit, commit_stat, generators_opf, vre_real)
                solve_problem(FOLDER_OPF)
                opf_gen, opf_dr, opf_lines = read_opf_results(FOLDER_OPF)
                opf_gen_sum = total_gen_check(opf_gen, timestamp)

        dr_check(opf_dr, opf_loads)

        ''' concatenante hourly opf results'''
        opf_gen_total, opf_dr_total, finalload, opf_lines_total = concatenate_opf_results(
            timestamp, opf_gen, opf_gen_total, opf_dr, opf_dr_total,
            opf_loads, finalload, lines, opf_lines_total, opf_lines)

        opf_cost['time'] = str(timestamp)
        opf_cost_total = pd.concat([opf_cost_total, opf_cost])

    return opf_cost_total, opf_gen_total, opf_dr_total, finalload, opf_lines_total

################################################################################################################
# CREATE OUPUT CSV FILES: opf_result.csv
################################################################################################################


if __name__ == "__main__":
    startdate = datetime.strptime('2018-01-01', '%Y-%m-%d')
    enddate = datetime.strptime('2018-01-01', '%Y-%m-%d')
