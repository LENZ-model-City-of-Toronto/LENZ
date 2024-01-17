from __future__ import print_function
from __future__ import absolute_import
from .main_model import *
from SILVER_VER_18.config_variables import *
import os.path
from pathlib import Path
from SILVER_VER_18.minpower.commonscripts import remove_leading_zeros_time_formatting


class Error(Exception):
    """Base class for other exceptions"""
    pass


class DemandGreaterThanGenerationError(Exception):
    """peak forcasted demand is greater than generation supply"""

    def __init__(self, max_demand, max_supply, message="The Peak Demand from the forecasted schedule in the Demand_real_forecasted excel sheet is greater than the sum of all generators [MW] value in the model inputs workbook"):
        self.max_demand = max_demand
        self.max_supply = max_supply
        self.message = message
        super().__init__(self.message)

    def __str__(self):
        return f'Peak demand {self.max_demand} > max generation {self.max_supply} -> {self.message}'


################################################################################################################
# FORECASTING ANALYSIS - creating the forecast error for demand and VRE
#                  value for gaussian distribution comes from NREL report
################################################################################################################

def forecasting_analysis(sched_real, col_names, plant_types):
    '''create forecasted schedule for vre and demand for range (startdate to enddate), later parsed to 24 hour schedule'''

    ''' Updated hyperbolic distribution implementation -- From Hodge (NREL) '''
    def _hermorm(N):
        plist = [None] * N
        plist[0] = poly1d(1)
        for n in range(1, N):
            plist[n] = plist[n - 1].deriv() - poly1d([1, 0]) * plist[n - 1]
        return plist

    # Moments of Distribution
    # Mean, Variance, Skewness, Kurtosis
    def pdf_mvsk(mvsk):
        N = len(mvsk)
        if N < 4:
            raise ValueError("Four moments must be given")
        mu, mc2, skew, kurt = mvsk
        totp = poly1d(1)
        sig = sqrt(mc2)

        if N > 2:
            Dvals = _hermorm(N + 1)
            C3 = skew / 6.0
            C4 = kurt / 24.0
            totp = totp - C3 * Dvals[3] + C4 * Dvals[4]

        def pdffunc(x):
            xn = (x - mu) / sig

            return totp(xn) * np.exp(-xn * xn / 2.0) / np.sqrt(2 * np.pi) / sig

        return pdffunc

    # NREL Paper: A comparison of wind power and load forecasting error distributions:
        # Link to paper: https://www.nrel.gov/docs/fy12osti/54384.pdf
        # Figure 11: Histogram of the distribution of day-ahead wind power forecasting errors for the ERCOT System, normalized by the installed wind capacity
        # mu: -0.012     >> multiplied by 100 - mean
        # sigma: 0.119   >> multiplied by 100 and squared - standard deviation
        # gamma: -0.062  >> ?? - skewdness (symmetry of the distrubiton)
        # kappa: 1.030   >> kurtosis _ relative weighting of peak and tails of distrubtion
    mvsk_wind = [-1.2, 141.6, -.2, 1.03]
    pdffunc_wind = pdf_mvsk(mvsk_wind)
    range_1 = np.arange(-100, 100, 1)
    SS = pdffunc_wind(range_1)
    total = sum(SS)
    SS_norm = tuple(p / total for p in SS)
    custm_wind = stats.rv_discrete(name='custm', values=(range_1, SS_norm))
    R_wind = custm_wind.rvs(size=8784)

    # NREL Paper: Metrics for Evaluating the Accuracy of Solar Power forecasting
    # link to paper https://www.nrel.gov/docs/fy14osti/60142.pdf
    # Table 1: Metrics values estimated by using an entire year of data - column: One plant- Day-ahead - normalized by plant capacity
    # MAE: 0.1481
    # standard dev: 0.2165
    # skewdness: -0.19
    # kurtosis: 2.04

    mvsk_solar = [14.81, 468.7, -0.19, 2.04]
    pdffunc_solar = pdf_mvsk(mvsk_solar)
    range_1 = np.arange(-100, 100, 1)
    SS = pdffunc_solar(range_1)
    total = sum(SS)
    SS_norm = tuple(p / total for p in SS)
    custm_solar = stats.rv_discrete(name='custm', values=(range_1, SS_norm))
    R_solar = custm_solar.rvs(size=8784)  # this can run a full year of data

    frames = pd.DataFrame()
    col_names_fc = ['date']

    i = 0
    for col_name in col_names:
        sched_fc = []
        col_names_fc.append(col_name)
        if plant_types[i] == 'Wind':
            R = R_wind
        elif plant_types[i] == 'Solar':
            R = R_solar

        i += 1

        for hour in range(sched_real.index.values[0], (len(sched_real.index.values) + sched_real.index.values[0])):
            error = int(R[hour])
            normalized_forecast_error = float(error / 100.00)
            sched_real_hour_2 = sched_real.ix[hour][col_name]
            sched_fc_hour = sched_real_hour_2 * (1 - normalized_forecast_error)
            sched_fc.append(sched_fc_hour)
        sched_fc_df_2 = pd.DataFrame(sched_fc, index=sched_real.index)
        frames = pd.concat([frames, sched_fc_df_2], axis=1)
    sched_fc_df = pd.concat([sched_real['date'], frames], axis=1)
    sched_fc_df.columns = col_names_fc
    return sched_fc_df


################################################################################################################
# FUNCTIONS THAT PERTAIN TO MINPOWER UC - create & save: csv files in  FOLDER_UC:
#                                               > generators.csv, load_schedule.csv, loads.csv
#                                       - create relevant dataframes:
#                                               > temporary load and csv files, hourly marginal cost
#                                       - read in uc results
################################################################################################################
def append_site_ind(plants, site_ind, column, excel_column):
    ''' add site independent characteristics for each generator in non-vre plants '''

    attribute = []
    for i in range(len(plants)):
        if column in ['start up cost', 'shut down cost',
                      'ramp rate max', 'ramp rate min',
                      'start up ramp limit', 'shut down ramp limit']:

            # attribute.append((site_ind.ix[plants.ix[i, 'kind'], excel_column]) * plants.ix[i, 'pmax'])
            attribute.append((site_ind[site_ind.columns[excel_column]].loc[plants['kind'].iloc[i]]) * plants['pmax'].iloc[i])
        elif column in ['cost curve equation', 'min up time', 'min down time']:
            # attribute.append((site_ind.ix[plants.ix[i, 'kind'], excel_column]))
            attribute.append((site_ind[site_ind.columns[excel_column]].loc[plants['kind'].iloc[i]]))
        elif column in ['schedule filename', 'shedding_allowed']:
            attribute.append('')
        elif (column == 'mustrun'):
            if MUSTRUN_NGCC_BIO is True:
                # plants.ix[i, 'kind']
                if plants['kind'].iloc[i] in ['NG_CC', 'NG_CT', 'NG_CG', 'biomass', 'nuclear']:
                    attribute.append('True')
                else:
                    attribute.append('')
            else:
                attribute.append('')

    # FutureWarning: The default dtype for empty Series will be 'object' instead of 'float64' in a future version. Specify a dtype explicitly to silence this warning.
    # Setting dtype to float did not work as type is sometimes string
    plants[column] = pd.Series(attribute, index=plants.index)


def append_vre(all_plants, vre_plants):
    ''' add wind and solar plants to all_plants dataframe '''

    # for plant in range(len(vre_plants.columns)):
    for cnt, plant in enumerate((vre_plants.columns), start=1):
        all_plants.loc[f'vre{cnt}'] = ''
        # all_plants.loc[f'vre{cnt}']['name'] = vre_plants.ix['name', plant]
        all_plants.loc[f'vre{cnt}']['name'] = vre_plants.loc['name'].iloc[plant]
        # all_plants.loc[f'vre{cnt}']['kind'] = vre_plants.ix['plant ID', plant]
        all_plants.loc[f'vre{cnt}']['kind'] = vre_plants.loc['plant ID'].iloc[plant]
        # all_plants.loc[f'vre{cnt}']['pmax'] = vre_plants.ix['[MW]', plant]
        all_plants.loc[f'vre{cnt}']['pmax'] = vre_plants.loc['[MW]'].iloc[plant]
        all_plants.loc[f'vre{cnt}']['pmin'] = 0
        all_plants.loc[f'vre{cnt}']['schedule filename'] = 'vre_schedule_' + vre_plants.loc['name'].iloc[plant] + '.csv'  # FIX
        all_plants.loc[f'vre{cnt}']['shedding_allowed'] = 1     # minpower cannot solve if this is set to 1
        all_plants.loc[f'vre{cnt}']['start up cost'] = 0.0
        all_plants.loc[f'vre{cnt}']['cost curve equation'] = '0.000001P'
        all_plants.loc[f'vre{cnt}']['start up ramp limit'] = ''
        all_plants.loc[f'vre{cnt}']['bus'] = vre_plants.loc['bus'].iloc[plant]
    return all_plants


def create_all_plants_df(vre_plants, non_vre_plants, storage_plants):
    ''' combine all generator types into final all_plants dataframe
        this will be used by minpower for UC: generators.csv '''
    site_ind = read_site_ind_inputs()
    attributes_df_site_ind = pd.DataFrame([[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]],
                                          index=['excel columns'],
                                          columns=['start up cost', 'shut down cost',
                                                   'min up time', 'min down time', 'ramp rate max', 'ramp rate min',
                                                   'start up ramp limit', 'shut down ramp limit',
                                                   'schedule filename', 'shedding_allowed', 'mustrun'])

    for column in attributes_df_site_ind:
        excel_column = attributes_df_site_ind[column].iloc[0]
        append_site_ind(non_vre_plants, site_ind, column, excel_column)
        append_site_ind(storage_plants, site_ind, column, excel_column)
    #     append_site_ind(ev_plants, site_ind, column, excel_column)
    # ev_plants['shedding_allowed'] = 1

    all_plants = append_vre(non_vre_plants, vre_plants)
    all_plants = pd.concat([all_plants, storage_plants], sort=True)
    all_plants.index = all_plants['name']
    all_plants = all_plants.drop('name', axis=1)

    cols_2 = ['kind', 'cost curve equation', 'pmax', 'pmin', 'storagecapacitymax', 'pumprampmax',
               'schedule filename', 'shedding_allowed',
              'start up cost', 'shut down cost', 'min up time', 'min down time',
              'ramp rate max', 'ramp rate min', 'start up ramp limit', 'shut down ramp limit', 'mustrun']
    all_plants = all_plants[cols_2]

    return all_plants

################################################################################################################
# READ IN AND FORMAT INFORMATION FROM model inputs.xlsx:
#                          1) site independent metrics > read_site_ind_inputs
#                          2) non-vre plants           > read_non_vre_inputs including storage and EV plants
#                          3) vre-plants               > read_vre_plants
################################################################################################################


def read_site_ind_inputs():
    ''' Create dataframes for site independent information from model inputs excel, modeled attributes sheet'''
    site_ind = pd.read_excel(PATH_MODEL_INPUTS, 'modeled attributes', index_col=0)
    site_ind.columns = site_ind.loc['col.']
    site_ind = site_ind['coal':'Imports']
    return site_ind


def read_vre_plants(valid_buses=None):
    ''' create dataframe of vre plants from model inputs sheet 'vre plants' '''
    vre_plants = pd.read_excel(PATH_MODEL_INPUTS, sheet_name='vre plants')
    vre_plants = vre_plants[vre_plants['[MW]'].notnull()]
    vre_plants_t = vre_plants.transpose()

    if valid_buses is None:  # To keep funtionality in main_model.py as a standalone script (__name__ == "__main__")
        return vre_plants_t

    power_output = vre_plants['[MW]'].sum()
    vre_buses = set(vre_plants['bus'].unique())

    difference = vre_buses - valid_buses

    if len(difference) > 0:
        print(f'\nERROR: The following buses are not in demand_centres of model inputs: {difference}')
        sys.exit()

    return vre_plants_t, power_output


def read_non_vre_plants(sheet_name, attributes_list, valid_buses):
    '''create dataframe of non-vre plants from model inputs excel workbook'''
    non_vre_plants = pd.read_excel(PATH_MODEL_INPUTS, sheet_name)

    non_vre_plants = non_vre_plants[non_vre_plants['bus'].notnull()]

    if non_vre_plants["kind"].str.contains("hydro_monthly").any() and HOURS_COMMITMENT is not 720:
        print(f"\nERROR: Hydro_monthly plant found but hours_commitment={HOURS_COMMITMENT} is less than minimum of 720 needed for month")
        sys.exit()

    # Make sure all plants connected to a bus in demand centres
    non_vre_buses = set(non_vre_plants['bus'].unique())

    difference = non_vre_buses - valid_buses

    if len(difference) > 0:
        print(f'\nERROR: The following buses are not in demand_centres of model inputs: {difference}')
        sys.exit()

    power_output = non_vre_plants['[MW]'].sum()

    non_vre_plants = non_vre_plants[non_vre_plants['[MW]'].notnull()]
    non_vre_plants.index = non_vre_plants['plant ID']

    non_vre_plants_bus = non_vre_plants['bus']
    non_vre_plants_bus.index = non_vre_plants['name']

    non_vre_plants_initial_conditions = pd.concat([non_vre_plants['name'],
                                                   non_vre_plants['kind'],
                                                   non_vre_plants['initial power'],
                                                   non_vre_plants['initial status'],
                                                   non_vre_plants['hours in status'],
                                                   non_vre_plants['storage content']], axis=1)
    non_vre_plants_initial_conditions = non_vre_plants_initial_conditions.rename(
        columns={'initial power': 'power', 'initial status': 'status'})
    non_vre_plants = non_vre_plants[non_vre_plants['[MW]'].notnull()][attributes_list]
    non_vre_plants = non_vre_plants.rename(columns={'[MW]': 'pmax'})

    return non_vre_plants, non_vre_plants_bus, non_vre_plants_initial_conditions, power_output


# configure opf and uc folders
def setup_folders():
    '''checks that folder_uc, _opf, and _price_opf exist, if they dont, the folder is created,
        then each folder is cleared of all files except for initial.csv'''
    for folder in [FOLDER_UC, FOLDER_OPF, FOLDER_PRICE_OPF]:
        Path(f"{folder}").mkdir(parents=True, exist_ok=True)
        for file in os.listdir(folder):
            if file != 'initial.csv':
                os.remove(folder.joinpath(file))
    for folder in [FOLDER_TO_SAVE_FINAL_RESULTS]:
        Path(f"{folder}").mkdir(parents=True, exist_ok=True)
    return None


def create_vre_time_series_final_and_fc(vre_timeseries_final, date_df, vre_plants, startdate, enddate):
    '''Checks for plants in input scenario, if they exist creates a real and forcasted schedule for vre plants'''
    if vre_timeseries_final.empty:
        print("no vre plants in this scneario")
        return pd.DataFrame(), pd.DataFrame()

    else:
        # solar_timeseries_final] # concatenate full year wind and solar dataframe to create full vre timeseries dataframe
        frames_3 = [date_df, vre_timeseries_final]
        vre_timeseries_final = pd.concat(frames_3, axis=1)
        # if startdate == user_startdate:       # only save this csv file once
        # should be returned vre_timeseries_final is used later
        Path(f"{FOLDER_TO_SAVE_FINAL_RESULTS}").mkdir(parents=True, exist_ok=True)
        vre_timeseries_final.to_csv(f"{FOLDER_TO_SAVE_FINAL_RESULTS}/Available_VRE_generation-{str(startdate.date())}_{str(enddate.date())}.csv")
        col_names = list(vre_timeseries_final.columns.values)
        col_names.remove('date')
        plant_types = list(vre_plants.ix['plant ID'])
        # should be returned vre_timeseries is used later
        vre_timeseries_fc = forecasting_analysis(vre_timeseries_final, col_names, plant_types)
        return vre_timeseries_final, vre_timeseries_fc

################################################################################################################
# VRE PRODUCTION ANALYSIS
#       > checks to see if production file already exsists, if not, it calls vre_analysis
################################################################################################################


def production_ts(plant_dataframe, startdate, enddate):    # Generation_path, vre_type):
    '''create vre generation timeseries from resource timeseries requires schedules for each vre generator. Generation schedule files must be in file location format:

     **/user_inputs/VRE_Resource_Analysis-{Scenario number}/{VRE type wind or solar}_Generation_Data/{merra_lat}-{merra_lng}>/{vre_type}_Generation_Data_{merra_lat}-{merra_lng}_{MERRA_DATA_YEAR}.csv

         example:
        **/user_inputs/VRE_Resource_Analysis-AB/Wind_Generation_Data/272-173/Wind_Generation_Data_272-173_2018.csv '''

    if len(plant_dataframe.columns) == 0:
        print('No VRE plants read in by read_vre_plants()')
        return pd.DataFrame(), pd.DataFrame()

    startdate, enddate = remove_leading_zeros_time_formatting(startdate, enddate)

    frames = []
    name_list = []
    # can pivot table and use iterrows, currently collumns are just indexs
    for plant in range(len(plant_dataframe.columns)):
        # gets the type of vre generation from the plant_dataframe
        vre_type = plant_dataframe.ix['plant ID', plant]
        # creates path to example 'C:/SILVER/SILVER_UC/SILVER_Data//user_inputs/VRE_Resource_Analysis-AB/Solar_Generation_Data/'
        Generation_path = PATH_GENERATION.joinpath(f'{vre_type}_Generation_Data')

        try:
            os.path.isdir(Generation_path)
        except:
            print(f'\nERROR: Generation path does not exist for {vre_type}, expected to be in {Generation_path}')
            sys.exit()

        # gets merra long and lat from dataframe for referencing directory named after mira coordinates
        merra_lat = str(int(plant_dataframe.loc['latitude_MERRA', plant]))
        merra_lng = str(int(plant_dataframe.loc['longitude_MERRA', plant]))
        plant_name = plant_dataframe.loc['name', plant]

        generation_filename = Generation_path.joinpath(f'{merra_lat}-{merra_lng}', f'{vre_type}_Generation_Data_{merra_lat}-{merra_lng}_{MERRA_DATA_YEAR}.csv')
        try:
            generation_ts = pd.read_csv(generation_filename)
        except FileNotFoundError:
            print(f"\nERROR: Generation data could not be found for specific generator, expected to be in {generation_filename}")
            sys.exit()
        index_startdate = generation_ts[generation_ts['GMT'] == str(startdate)].index.tolist()
        index_startdate = index_startdate[0]
        index_enddate = generation_ts[generation_ts['GMT'] == str(enddate)].index.tolist()
        index_enddate = index_enddate[0]
        generation_ts = generation_ts.iloc[index_startdate:index_enddate]

        # converts "time run" values from generation_ts and multiplies it by MW values stored in plant_dataframe
        installed_capacity = plant_dataframe.ix['[MW]', plant]
        frames.append(generation_ts['CapacityFactor'] * installed_capacity)
        name_list.append(plant_name)

    date_df = pd.DataFrame(generation_ts['GMT'])
    date_df.columns = ['date']
    vre_timeseries_final = pd.concat(frames, axis=1)
    vre_timeseries_final.columns = name_list

    return vre_timeseries_final, date_df


def setup_call_for_week(startdate, enddate, start_of_period_run, list_of_failed_uc_days):
    '''calls functions that read in data from user input sheets for a given time period startdate-enddate
       once data is read in and formatted, these values are passed to call_mp() and the model begins'''
    if startdate.date() == USER_STARTDATE.date():
        print('\t', SCENARIO_NUMBER, '\t', HOURS_COMMITMENT,
              'hours commitment', '\t')
    print("START MONTH ANALYSIS:", '\t', startdate.date(), '\t', enddate.date())

    # configure opf and uc folders
    setup_folders()

    centres = pd.read_excel(PATH_MODEL_INPUTS, sheet_name='demand centres')
    buses = set(centres['bus'].unique())

    # Create dataframes of wind plants and solar plants
    vre_plants, total_output = read_vre_plants(buses)

    # Create a dataframe with full year time series for all wind plants and all solar plants
    vre_timeseries_final, date_df = production_ts(
        vre_plants, startdate, enddate)  # FIX change date

    vre_timeseries_final, vre_timeseries_fc = create_vre_time_series_final_and_fc(
        vre_timeseries_final, date_df, vre_plants, startdate, enddate)

    # reads demand centres sheet from model inputs excel file

    df_regions = pd.read_excel(PATH_MODEL_INPUTS, sheet_name='demand centres')

    ''' create real and forecasted demand timeseries from user defined folder location '''

    # reads _Demand_Real_Forecasted.xlsx sheet Zonal_Demand_Real
    demand_schedule_real = pd.read_excel(DEMAND_SCHEDULE_FILENAME, parse_dates=[
                                         'date'], sheet_name='Total_Real')
    demand_schedule_real = demand_schedule_real[(demand_schedule_real['date'] >= startdate)
                                                & (demand_schedule_real['date'] < enddate)]

    col_names_2 = list(demand_schedule_real.columns.values)
    col_names_2.remove('date')

    ''' if running OPF: create demand_schedule_real_by_region and demand_schedule_fc and demand_schedule_fc_by_region: '''

    demand_schedule_real_by_region = pd.read_excel(
        DEMAND_SCHEDULE_FILENAME, sheet_name='Zonal_Demand_Real')
    demand_schedule_real_by_region = demand_schedule_real_by_region[(demand_schedule_real_by_region['date'] >= startdate)
                                                                    & (demand_schedule_real_by_region['date'] < enddate)]

    demand_schedule_fc = pd.read_excel(DEMAND_SCHEDULE_FILENAME, parse_dates=[
                                       'date'], sheet_name='Total_Forecasted')

    demand_schedule_fc = demand_schedule_fc[(demand_schedule_fc['date'] >= startdate)
                                            & (demand_schedule_fc['date'] < enddate)]

    demand_schedule_fc_by_region = pd.read_excel(DEMAND_SCHEDULE_FILENAME, parse_dates=[
                                                 'date'], sheet_name='Zonal_Demand_Forecasted')
    demand_schedule_fc_by_region = demand_schedule_fc_by_region[(demand_schedule_fc_by_region['date'] >= startdate)
                                                                & (demand_schedule_fc_by_region['date'] <= enddate)]

    max_demand = demand_schedule_fc['demand_fc'].max()

    # CREATE dataframe of non-vre plants from model inputs, non-vre plants speadsheet
    # read non-vre plants from model inputs excel file
    non_vre_attributes_list = ['name', '[MW]', 'kind', 'pmin', 'cost curve equation']
    non_vre_plants, non_vre_plants_bus, non_vre_plants_initial_conditions, non_vre_output = \
        read_non_vre_plants(sheet_name='non-vre plants',
                            attributes_list=non_vre_attributes_list,
                            valid_buses=buses)
    total_output += non_vre_output

    # read storage plants from model inputs excel file
    storage_attributes_list = ['name', '[MW]', 'kind',
                               'pmin', 'storagecapacitymax', 'pumprampmax']

    storage_plants, storage_plants_bus, storage_pants_initial_conditions, storage_output = \
        read_non_vre_plants(sheet_name='storage',
                            attributes_list=storage_attributes_list,
                            valid_buses=buses)
    total_output += storage_output

    # # read ev_plants from model inputs excel file
    # ev_attributes_list = ['name', '[MW]', 'kind', 'pmin',
    #                       'storagecapacitymax', 'unpluggedhours', 'tripenergy']

    # ev_plants, ev_plants_bus, ev_plants_initial_conditions, ev_output = \
    #     read_non_vre_plants(sheet_name='EV_aggregator',
    #                         attributes_list=ev_attributes_list,
    #                         valid_buses=buses)
    # total_output += ev_output

    # check if the demand is greater than supply and error if this the case
    if max_demand > total_output:
        raise DemandGreaterThanGenerationError(max_demand, total_output)
    # combine non_vre, vre, storage, and ev_plants initial conditions into one dataframe then write to initial.csv
    if start_of_period_run == 0:
        print('\t', '\t', '\t', "writing initial csv for the first time")
        initial_conditions = pd.concat(
            [non_vre_plants_initial_conditions, storage_pants_initial_conditions])
        initial_conditions.to_csv(FOLDER_UC.joinpath('initial.csv'))

    plants_bus = pd.concat(
        [non_vre_plants_bus, storage_plants_bus])

    ###########################
    all_plants = create_all_plants_df(
        vre_plants, non_vre_plants, storage_plants)

    # CREATE [CONSTANT] CSV FILES FOR OPF - generators minus power, lines, loads minus power
    if not UC_NETWORKED:
        create_uc_loads_csv()
        create_uc_generators_csv(all_plants, FOLDER_UC)

    all_plants = opf_csv(all_plants, plants_bus, vre_plants)
    if UC_NETWORKED:
        create_uc_generators_csv(all_plants, FOLDER_UC)

    lines = create_opf_lines_csv()
    loads = create_opf_loads_df(df_regions)

    # CALL MINPOWER - for every 24h, run UC for 36 and OPF for each hour in 24
    finalcommit, list_of_failed_uc_days = call_mp(all_plants, df_regions, loads, lines, demand_schedule_real,
                                                  vre_timeseries_final, vre_timeseries_fc, startdate, enddate, start_of_period_run,
                                                  list_of_failed_uc_days, demand_schedule_fc, demand_schedule_fc_by_region, demand_schedule_real_by_region)

    ''' copy UC results from last day in case you need it if the first day of the next week fails '''
    ''' note that if the UC fails on the FIRST run of the scenario, then it will fail '''

    list_of_failed_uc_days_2 = list_of_failed_uc_days.append(
        list_of_failed_uc_days)

    return list_of_failed_uc_days_2
