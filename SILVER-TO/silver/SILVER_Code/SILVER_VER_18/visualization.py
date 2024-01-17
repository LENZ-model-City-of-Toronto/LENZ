from bokeh.models import ColumnDataSource, HoverTool, Label, Panel, Tabs, Legend
from bokeh.palettes import *
from bokeh.plotting import figure, show, save
from bokeh.transform import cumsum
from matplotlib.style import available
from bokeh.layouts import gridplot
from bokeh.io import output_file, show, save
import pathlib as pl
import glob
import os
import csv
from dateutil.parser import parse
from datetime import datetime
import pandas as pd


def is_date(string, fuzzy=False):
    """
    Return whether the string can be interpreted as a date.

    :param string: str, string to check for date
    :param fuzzy: bool, ignore unknown tokens in string if True
    """
    try:
        parse(string, fuzzy=fuzzy)
        return True

    except ValueError:
        return False


def import_UC_results(UC_results_file):
    '''given a file path to a folder containing one silver UC results file, imports UC results and returns a pandas dataframe'''
    header = []
    timeseries_rows = []
    generator_stats = []
    with open(UC_results_file, mode='r') as infile:
        csv_reader = csv.reader(infile)
        # get the header g0 - g25 values
        header = next(csv_reader)

        data_flag = 1
        # gathers generator data section of the csv file
        # this is done by reading the file line by line (after the header) until the timeseries data is reached
        while (data_flag == 1):
            test_row = next(csv_reader)
            if (is_date(test_row[0])):
                data_flag = 0
            else:
                generator_stats.append(test_row)

        # Isolates data section and removes bottom values and totals?
        for row in csv_reader:
            if (row[0].isnumeric()):
                continue
            elif (is_date(row[0])):
                timeseries_rows.append(row)
            else:
                continue

    # creates dataframe from first section of rows containing generator data then pivots table on generator ID to later join with time series data
    header[0] = 'Date'
    UC_results = pd.DataFrame(timeseries_rows, columns=header)
    header[0] = 'G_ID'
    UC_data = pd.DataFrame(generator_stats, columns=header)
    UC_data.drop(columns=['demand_fc', 'dr'], inplace=True)
    UC_data.set_index('G_ID', inplace=True)
    UC_data = UC_data.transpose()
    UC_data.rename_axis('G_ID', inplace=True)

    # creates dataframe from second section of rows containing time series data
    UC_results['Date'] = pd.to_datetime(UC_results['Date'])

    UC_results.drop(columns=['demand_fc', 'dr'], inplace=True)

    UC_results = UC_results.melt(id_vars=['Date'], value_vars=header[1:-3], var_name='G_ID', value_name='UC_MWh')
    UC_results['UC_MWh'] = pd.to_numeric(UC_results['UC_MWh'])
    # merges the two dataframes into one collumn datasource with each row having a datetime and generator ID + all generator info per row
    # each row has all generator information so each data point in the visualization will have access to generator info

    data = pd.merge(UC_results, UC_data, how="left", on='G_ID')

    return data


def create_stacked_area_plot(UC_data):
    '''given UC_data and output_folder, creates a plot of the UC results by fuel type UC per hour, RETURNS a bokeh plot'''
    # get unique categories of fuel to assign color values to each category
    fuel_types = list(UC_data.kind.unique())
    N = len(fuel_types)
    # DYNAMICALLY assign a color to each fuel type FLEXIBLE
    colors = d3['Category20'][N]
    if (N > 20):
        print("ERROR: Explected less than 21 fuel types as max color palette count is 20, found {}".format(N))

    # pivot data to be grouped UC data and fuel types
    time = UC_data.groupby(['Date', 'kind']).sum().reset_index()
    time = time.pivot(index='Date', columns='kind', values='UC_MWh')
    time['sum'] = time.sum(axis=1)
    negative = time.copy(deep=True)
    negative[negative>0] = 0
    time[time<0] = 0

    
    source = ColumnDataSource(time)

    # CREATES PLOTS
    # you can adjust plot_width and plot_height to change the dimensions of each plot
    p = figure(x_axis_type='datetime', plot_width=1100, plot_height=550, tools='xpan,box_zoom,reset,save,xwheel_zoom', active_scroll='xwheel_zoom')
    p.add_layout(Legend(), 'right')
    p.grid.minor_grid_line_color = '#eeeeee'
    # adds stacked area graph
    p.varea_stack(stackers=fuel_types, x='Date', color=colors, legend_label=fuel_types, source=source)
    p.varea_stack(stackers=fuel_types, x='Date', color=colors, legend_label=fuel_types, source=ColumnDataSource(negative))
    # adds sum line for tooltip, stacked area doesnt work with tooltip
    p.line(y='sum', x='Date', line_width=100, line_alpha=0.0, source=source)
    p.title.text = "Unit Commitment Results: Generation per hour"
    # Dynamically assigns tooltip labels to each fuel type
    tips = [("Date", "@Date{%F}"), ("Total UC", "@sum")]
    for i in range(len(fuel_types)):
        tips.append((fuel_types[i], "@" + str(fuel_types[i])))
    TOOLTIPS = tips
    p.add_tools(HoverTool(tooltips=TOOLTIPS, formatters={"@Date": "datetime"}, attachment='below', line_policy='nearest'))
    return p


def visualize_UC(UC_results_file):
    data = import_UC_results(UC_results_file)
    stacked_area_plot = create_stacked_area_plot(data)
    # set output to static HTML file
    output_file(filename=UC_results_file[:-4] + "_Dispatch_Stack.html", title="UC Dispatch Stack")
    show(stacked_area_plot)

    return


def main():
    # these two lines will gather data and visualize UC results

    # Option 1 pass filepath variable
    # filepath = Insert_filepath_variable
    # Scenario_list = get_silver_data(filepath)

    # Option 2 pass hardcoded filepath
    # Scenario_list = get_silver_data("C:/Users/evand/Documents/__WORK/Sydney Hoffman/SILVER_Results_SH")

    # Option 3 pass no variable and ask for user input

    visualize_UC('C:/SILVER/SILVER/SILVER_Data/Model Results/SS/UC_Results_2018-01-01_2018-01-03.csv')
    # show_OPF(Scenario_list)
    # line_OPF(Scenario_list)
    # show_aggregate_OPF(Scenario_list)
    # show_composition_OPF(Scenario_list)

    # VRE_data = import_available_vre_generation("C:/Users/evand/Documents/__WORK/Tamara Visualization/BC/")
    # show(create_VRE(VRE_data))


if __name__ == "__main__":
    main()
