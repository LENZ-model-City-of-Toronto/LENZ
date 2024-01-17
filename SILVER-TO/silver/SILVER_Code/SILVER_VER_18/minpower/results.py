"""
    Solution models, output, and display for power system
    optimization problems. Matplotlib and networkx are used for
    visualization.
"""

from __future__ import print_function
from __future__ import absolute_import
from __future__ import division
from builtins import str
from builtins import zip
from builtins import range
from builtins import object
from past.utils import old_div
import logging
import pandas as pd
import numpy as np
from SILVER_VER_18.config_variables import *
from .commonscripts import (update_attributes, gen_time_dataframe, joindir,
                            replace_all, getattrL, writeCSV, transpose, within, correct_status,
                            set_trace)
from .schedule import TimeIndex
from .optimization import value
from .config import user_config
try:
    import matplotlib
    import matplotlib.pyplot as plot
    do_plotting = True
except ImportError:
    logging.warning("Can't import matplotlib -- skipping plotting.")
    do_plotting = False
from .silver_variables import FOLDER_OPF, PATH, FOLDER_UC, PATH_MODEL_INPUTS
import SILVER_VER_18.internal_vars as internal_vars


def prettify_plots(for_publication=True):
    plot.rc("xtick", direction="out")
    plot.rc("ytick", direction="out")
    plot.rc("ytick", labelsize='small')
    plot.rc("xtick", labelsize='small')
    plot.rc("legend", fancybox=True)
    plot.rc("font", family="serif")
    if for_publication:
        plot.rc("font", size=16)


def full_filename(filename):
    if user_config.standalone:
        filename = '{}-{}'.format(user_config.pid, filename)
    return joindir(user_config.directory, filename)


def classify_problem(times, power_system):
    '''
    Classify the type of problem: ED, OPF, UC, or SCUC.
    and if it is stochastic or not.
    '''
    if not power_system.lines and len(times) == 1:
        kind = 'ED'
    elif len(times) == 1:
        kind = 'OPF'
    elif not power_system.lines:
        kind = 'UC'
    else:
        kind = 'SCUC'

    if power_system.is_stochastic:
        kind = 'stochastic_' + kind
    return kind


def make_solution(power_system, times, **kwargs):
    '''
    Create a :class:`solution.Solution` object for a power system over times.
    '''
    problem_type = dict(
        ED=Solution_ED,
        OPF=Solution_OPF,
        UC=Solution_UC,
        SCUC=Solution_SCUC,
        stochastic_UC=Solution_Stochastic_UC
    )

    if do_plotting:
        prettify_plots()

    kind = classify_problem(times, power_system)

    return problem_type[kind](power_system, times, **kwargs)


def make_multistage_solution(power_system, stage_times, stage_solutions):
    '''Create a multi stage solution'''
    if do_plotting:
        prettify_plots()
    if power_system.lines:
        logging.warning('no visualization for multistage SCUC yet')
    klass = MultistageStandalone if getattr(
        stage_solutions, 'path', False) else Solution_UC_multistage
    return klass(power_system, stage_times, stage_solutions)


class Solution(object):

    '''
    Solution information template for a power system over times.
    Each problem type has its own class for visualization and
    spreadsheet output, e.g. :class:`~solution.Solution_ED`.
    '''

    def __init__(self, power_system, times, datadir='.', is_stochastic=False):
        update_attributes(self, locals())
        self._resolved = False
        self._setup_powersystem()
        self._get_problem_info()
        self._get_outputs()
        self._get_costs()
        self._get_prices()

    def _setup_powersystem(self):
        '''shortcuts for power system attributes'''
        self.buses = self.power_system.buses
        self.lines = self.power_system.lines
        self.generators = self.power_system.generators()
        self.loads = self.power_system.loads()
        self.times_non_overlap = self.times.non_overlap()

    def get_values(self, items, method='power', time=None, evaluate=False):
        '''Get the attributes of all objects of a certain kind at a given time.'''
        # items are each generator (including storage and ev)
        phs_ev_df = pd.DataFrame(columns=['name', 'time', 'power', 'power in', 'storage content'])

        if time:

            if evaluate:

                out = [value(getattr(
                    obj, method)(time, evaluate=True)) for obj in items]

            else:
                if method == 'power':
                    mm_out = []
                    for obj in items:

                        if (type(obj).__name__ == 'Storage') or (type(obj).__name__ == 'EV_aggregator'):
                            power_mm = value(getattr(obj, method)(time))
                            power_in_list = self.get_values_power_in(items=obj, method='power_in', time=time)
                            power_in_value = power_in_list[0]
                            power_final = power_in_value + power_mm
                            mm_out.append(power_final)

                            if (type(obj).__name__ == 'Storage'):
                                storage_content_list = self.get_values_storage_content(items=obj, method='storage_content', time=time)
                                storage_content_value = storage_content_list[0]

                                absolute_power_list = self.get_values_absolute_power(items=obj, method='absolute_power', time=time)
                                absolute_power_value = absolute_power_list[0]

                            else:
                                EV_content_list = self.get_values_EV_content(items=obj, method='EV_content', time=time)
                                EV_content_value = EV_content_list[0]

                        elif (type(obj).__name__ != 'Storage') or (type(obj).__name__ != 'EV_aggregator'):

                            if (type(obj).__name__ == 'Cascade'):

                                power_mm = value(getattr(obj, 'discharge')(time)) * 997 * 9.81 * obj.hfactor * obj.efficiency * (2.77e-10)
                                mm_out.append(power_mm)

                            else:

                                power_mm = value(getattr(obj, method)(time))
                                mm_out.append(power_mm)
                    out = mm_out

                elif method == 'discharge':

                    out = [value(getattr(obj, method)(time)) for obj in items if (type(obj).__name__ == 'Cascade')]

                elif method == 'storage_level':

                    out = [value(getattr(obj, method)(time)) for obj in items if (type(obj).__name__ == 'Cascade')]

                elif method == 'spill':

                    out = [value(getattr(obj, method)(time)) for obj in items if (type(obj).__name__ == 'Cascade')]

                else:

                    out = [value(getattr(obj, method)(time)) for obj in items]
        else:
            out = [value(getattr(obj, method)) for obj in items]

        return out

    def gen_time_df(self, method, non_overlap=True, evaluate=False,
                    generators=None):
        times = self.times_non_overlap if non_overlap else self.times

        if generators is None:
            generators = self.generators

        # generators is the full list of ALL generators (Generator, Storage, and EV class)

        return gen_time_dataframe(generators, times, [self.get_values(generators, method, t, evaluate) for t in times])

    def get_values_power_in(self, items, method='power_in', time=None, evaluate=False):
        if time:
            if evaluate:
                print("power gets called in B loop")
            else:
                out = [value(getattr(items, method)(time))]
        return out

    def get_values_absolute_power(self, items, method='absolute_power', time=None, evaluate=False):
        if time:
            if evaluate:
                print("power gets called in B loop")
            else:
                out = [value(getattr(items, method)(time))]
        return out

    def get_values_EV_content(self, items, method='EV_content', time=None, evaluate=False):
        if time:
            if evaluate:
                print("not sure what to do here")
            else:
                out = [value(getattr(items, method)(time))]
        if time == 't23':
            closing_EV_content = open(FOLDER_UC / 'closing_EV_content.txt', 'w')
            closing_EV_content.write(str(out[0]))
            closing_EV_content.close()
        return out

    def get_values_storage_content(self, items, method='storage_content', time=None, evaluate=False):

        if time:
            if evaluate:
                print("not sure what to do here")
            else:
                out = [value(getattr(items, method)(time))]
        return out

    def power_in_time_df(self, method, non_overlap=True, evaluate=False, generators=None):
        times = self.times_non_overlap if non_overlap else self.times
        if generators is None:
            generators = self.generators
        for item in generators:
            if type(item).__name__ == 'Storage':
                for t in times:
                    power_in_value = self.get_values_power_in(item, method, t, evaluate)

    def cascade_time_df(self, method, non_overlap=True, evaluate=False, generators=None):
        times = self.times_non_overlap if non_overlap else self.times
        if generators is None:
            generators = self.generators

        # instead of passing the full list of generators in, just pass cascade generators
        cascade_generators = []
        for item in generators:
            if type(item).__name__ == 'Cascade':
                cascade_generators.append(item)

        return gen_time_dataframe(cascade_generators, times,
                                  [self.get_values(cascade_generators, method, t, evaluate) for t in times])

    def storage_content_df(self, method, non_overlap=True, evaluate=False, generators=None):
        times = self.times_non_overlap if non_overlap else self.times
        if generators is None:
            generators = self.generators

        # instead of passing the full list of generators in, just pass storage generators
        storage_generators = []
        for item in generators:
            if type(item).__name__ == 'Storage':
                storage_generators.append(item)

        return gen_time_dataframe(storage_generators, times,
                                  [self.get_values(storage_generators, method, t, evaluate) for t in times])

    def ev_content_df(self, method, non_overlap=True, evaluate=False, generators=None):
        times = self.times_non_overlap if non_overlap else self.times
        if generators is None:
            generators = self.generators

        # instead of passing the full list of generators in, just pass EV generators
        ev_generators = []
        for item in generators:
            if type(item).__name__ == 'EV_aggregator':
                ev_generators.append(item)

        return gen_time_dataframe(ev_generators, times,
                                  [self.get_values(ev_generators, method, t, evaluate) for t in times])

    def _get_problem_info(self):
        self.solve_time = self.power_system.solution_time

        # testing to see if objective can be set to None
        try:
            self.objective = float(value(self.power_system.objective))
        except TypeError:
            self.objective = self.power_system.objective

        try:
            self.mipgap = self.power_system.mipgap
        except AttributeError:
            self.mipgap = None

    def _get_outputs(self):
        self.generators_power = self.gen_time_df('power')
        self.cascades_storagelevel = self.cascade_time_df('storage_level')
        self.cascades_discharge = self.cascade_time_df('discharge')
        self.cascades_spill = self.cascade_time_df('spill')
        self.generators_power_in = self.power_in_time_df('power_in')
        self.generators_status = correct_status(self.gen_time_df('status'))

        self.storage_content = self.storage_content_df('storage_content')
        self.ev_content = self.ev_content_df('EV_content')

    def _get_costs(self):

        self.totalcost_generation = self.gen_time_df('cost', evaluate=True)
        self.fuelcost = self.gen_time_df('operatingcost', evaluate=True)
        self.fuelcost_true = self.gen_time_df('truecost').sum().sum()
        self.incremental_cost = self.gen_time_df('incrementalcost')

        times = self.times_non_overlap
        self.load_shed_timeseries = pd.Series(
            [sum(self.get_values(
                self.loads, 'shed', t, evaluate=True)) for t in times],
            index=times.strings.index)
        self.gen_shed_timeseries = self.gen_time_df('shed', evaluate=True,
                                                    generators=self.power_system.get_generators_noncontrollable()
                                                    ).sum(axis=1)

        self.load_shed = self.load_shed_timeseries.sum()
        self.gen_shed = self.gen_shed_timeseries.sum()
#
        if self.gen_shed > 0.01:
            logging.debug('generation shed: {}MW'.format(self.gen_shed))
        if self.load_shed > 0.01:
            logging.debug('load shed: {}MW'.format(self.load_shed))
        self._get_cost_error()

        try:
            self.load_shed_status_timeseries = pd.Series([sum(self.get_values(self.loads, 'dr_status', t)) for t in times],
                                                         index=times.strings.index)
        except TypeError:   # if shedding_mode is false, then dr_status is [None], so you cannot sum it

            pass

        problem_type = classify_problem(times, self)

        opf_status = internal_vars.opf_variable  # either 'price' or 'final'
        if (opf_status == 'final') and (problem_type == 'OPF'):
            '''in second OPF run, set the dr value to the calculated UC value '''

            all_costs = pd.concat([self.totalcost_generation, self.fuelcost, self.incremental_cost])

            all_costs.to_csv(FOLDER_OPF / 'all_costs.csv')

            self.totalcost_generation.to_csv(FOLDER_OPF / 'totalcost_generation.csv')

    def get_load_shed_status_timeseries(self, times):
        return pd.Series([sum(self.get_values(self.loads, 'dr_status', t)) for t in times],
                         index=times.strings.index)

    def _get_cost_error(self):
        try:
            self.costerror = old_div(abs(self.fuelcost.sum(
            ).sum() - self.fuelcost_true), self.fuelcost_true)

        except ZeroDivisionError:
            self.costerror = 0

    def _get_prices(self):
        self.lmps = {}
        self.line_prices = {}
        for t in self.times:
            self.lmps[str(t)] = [bus.price(t) for bus in self.buses]
            self.line_prices[str(t)] = [line.price(t) for line in self.lines]

    def savevisualization(self, filename=None):
        '''Save the visualization to a file'''
        if filename is None:
            plot.show()
        else:
            plot.savefig(filename, bbox_inches='tight')
        plot.close()

    def show(self):
        '''Display the solution information to the terminal'''
        out = ['']
        out.extend(['Solution information', '-' * 10, ''])
        if len(self.times) < 5:
            out.append('Status')
            out.append(self.generators_status)
            out.append('Power')
            out.append(self.generators_power)
            out.append('IC')
            out.append(self.incremental_cost)
            out.append('')
            for t in self.times:
                if len(self.times) > 1:
                    out.append(t)
                out.extend(self.info_price(t))
                out.extend(self.info_loads(t))
                out.extend(self.info_buses(t))
                out.extend(self.info_lines(t))
                out.append('')

        out.extend(self.info_cost())
        # Un-comment the following line to print out the opf solution again

    def info_status(self):
        return ['solved in {time:0.4f} sec'.format(time=self.solve_time)]

    def info_price(self, t):
        return ['price={}'.format(self.lmps[str(t)])]

    def info_loads(self, t):
        out = ['load info:']
        if len(self.buses) > 1:
            out.append('bus={}'.format(self.get_values(self.loads, 'bus')))
        out.append('name={}'.format(self.get_values(self.loads, 'name')))
        out.append('Pd={}'.format(self.get_values(self.loads, 'power', t)))
        return out

    def info_buses(self, t):
        buses = self.buses
        out = ['bus info:',
               'name={}'.format(getattrL(buses, 'name')),
               'Pinj={}'.format([bus.Pgen(t, evaluate=True)
                                - bus.Pload(
                                    t, evaluate=True) for bus in buses]),
               'angle={}'.format(self.get_values(
                                 buses, 'angle', t) if len(buses) > 1 else []),
               'LMP={}'.format(self.lmps[str(t)])]

        return out

    def info_lines(self, t):
        lines = self.lines

        return ['line info:',
                'connecting={}'.format(
                    list(zip(getattrL(lines, 'frombus'), getattrL(lines, 'tobus')))),
                'Pk={}'.format(self.get_values(lines, 'power', t)),
                'price={}'.format(self.line_prices[str(t)])]

    def info_cost(self):

        return ['objective cost={}'.format(self.objective),
                'total cost of generation={}'.format(
                    self.totalcost_generation.sum().sum()),
                'linearized fuel cost of generation={}'.format(
                    self.fuelcost.sum().sum()),
                'polynomial fuel cost of generation={}'.format(
                    self.fuelcost_true),
                'percentage difference\t\t={}'.format(
                    self.costerror),
                ]


class Solution_ED(Solution):

    def info_lines(self, t):
        return []

    def info_buses(self, t):
        return []

    def visualization(self, show_cost_also=False):
        ''' economic dispatch visualization of linearized incremental cost'''
        print("GETTING HERE 2")
        if not do_plotting:
            return
        print("GETTING HERE")
        t = self.times[0]
        price = self.lmps[str(t)][0]
        generators = self.generators
        loads = self.loads

        plotted_gens, names_gens, plotted_loads, names_loads = [], [], [], []
        minGen = min(getattrL(generators, 'pmin'))
        maxGen = max(getattrL(generators, 'pmax'))

        # save a plot of the price space - illustrating equal IC
        ax = plot.axes()
        for gen in generators:
            if gen.status(t):
                in_range, out_range = gen.bids.output_incremental_range()
                if gen.bids.is_pwl:
                    line, = plot.step(
                        in_range, out_range, where='pre', linestyle='-')
                else:
                    line, = plot.plot(in_range, out_range, linestyle='-', )
                plotted_gens.append(line)
                P = value(gen.power(t))
                IC = gen.incrementalcost(t)
                plot.plot(P, IC, '.', c=line.get_color(
                ), markersize=8, linewidth=2, alpha=0.7)
                names_gens.append(gen.name)
        if price is not None:
            grayColor = '.75'
            plot.plot([minGen, maxGen], [price, price], '--k', color=grayColor)
            plot.text(maxGen, price, '{p:0.2f} $/MWh'.format(
                p=price), color=grayColor, horizontalalignment='right')

        plot.xlabel('P [MWh]')
        if plotted_loads:
            plot.ylabel('Marginal Cost-Benifit [$/MWh]')
        else:
            plot.ylabel('Marginal Cost [$/MWh]')
        prettify_axes(ax)

        ymin, _ = plot.ylim()
        if ymin < 0:
            plot.ylim(ymin=0)

        legendGens = plot.legend(plotted_gens, names_gens, fancybox=True,
                                 title='Generators:', loc='best')
        if plotted_loads:
            plot.legend(plotted_loads, names_loads,
                        fancybox=True, title='Loads:', loc='best')
            # add first legend to the axes manually bcs multiple legends get overwritten
            plot.gca().add_artist(legendGens)

        self.savevisualization(filename=full_filename('dispatch.png'))

        if show_cost_also:
            # show a plot of the cost space, illustrating the linearization
            plot.figure()
            gensPlotted_price = plotted_gens
            plotted_gens, names_gens, plotted_loads, names_loads = [
            ], [], [], []
            for g, gen in enumerate(generators):
                if gen.status(t):
                    plotted_gens.append(gen.cost_model.plot(
                        P=value(gen.power(t)),
                        linestyle='-',
                        color=gensPlotted_price[g].get_color()
                    ))
                    names_gens.append(gen.name)
            for load in loads:
                if load.kind == 'bidding':
                    plotted_loads.append(load.bid(
                        t).plot(P=value(load.power(t)), linestyle=':'))
                    names_loads.append(load.name)
            plot.xlabel('P [MWh]')
            if plotted_loads:
                plot.ylabel('Cost-Benifit [$/h]')
            else:
                plot.ylabel('Cost [$/h]')
            legendGens = plot.legend(plotted_gens, names_gens, fancybox=True,
                                     title='Generators:', loc='best')
            if plotted_loads:
                plot.legend(plotted_loads, names_loads,
                            fancybox=True, title='Loads:', loc='best')
                plot.gca().add_artist(legendGens)  # add first legend to the axes manually bcs multiple legends get overwritten

            self.savevisualization(filename=full_filename('dispatch-cost.png'))

    def saveCSV(self):
        '''economic dispatch generator solution values in spreadsheet form'''
        def niceTF(value):
            return 0 if value == 0 else 1

        def nice_zeros(value):
            return 0 if value == 0 else value

        t = self.times.strings.index[0]

        out = pd.DataFrame(columns=['power', 'IC'],
                           index=self.generators_power.columns)
        out['power'] = self.generators_power.ix[t]
        out['IC'] = [nice_zeros(g.incrementalcost(self.times[0]))
                     for g in self.generators]

        if user_config.dispatch_decommit_allowed:
            # add status, but label all units at Pmin as OFF
            out['status'] = self.generators_status.ix[t]
            out = out.drop('IC', axis=1)

        out.index = [gen.name for gen in self.generators]
        out.index.name = 'name'

        out.to_csv(full_filename('dispatch.csv'))


class Solution_OPF(Solution):

    def visualization(self, filename=None):
        '''power flow visualization'''
        print("GETTING HERE 3")
        if not do_plotting:
            return

        try:
            import networkx as nx
        except ImportError:
            logging.warning("Could'nt import networkx -- skipping plotting.")
            return

        buses, lines, t = self.buses, self.lines, self.times[0]

        G = nx.DiGraph()
        for bus in buses:
            Pinj = value(bus.Pgen(t)) - value(bus.Pload(t))
            print("Pinj", Pinj)
            G.add_node(bus.name, Pinj=Pinj)
        for line in lines:
            P = value(line.power(t))
            if P >= 0:
                G.add_edge(line.frombus, line.tobus, P=P, Plim=line.pmax)
            else:
                G.add_edge(line.tobus, line.frombus, P=-P, Plim=-line.pmin)

        pos = nx.spectral_layout(G)
        Pinj = [ndata['Pinj'] for bus, ndata in G.nodes(data=True)
                if 'Pinj' in ndata]
        nx.draw(G, node_color=Pinj, pos=pos, node_size=1500,
                alpha=.7, cmap=plot.cm.get_cmap('RdYlBu'), fontsize=30)

        Plines = [edata['P'] for _, t, edata in G.edges(data=True)
                  if 'P' in edata]
        atLimLines = [(f, t) for f, t, edata in G.edges(
            data=True) if within(edata['P'], val=edata['Plim'], eps=1e-3)]
        nx.draw_networkx_edges(
            G, edge_color='0.6', pos=pos, width=Plines, alpha=0.5)
        nx.draw_networkx_edges(G, edgelist=atLimLines, edge_color='r',
                               pos=pos, width=Plines, alpha=0.5)
        self.savevisualization(full_filename('powerflow.png'))

    def saveCSV(self):
        '''OPF generator and line power values in spreadsheet form'''

        t = self.times[0]
        gens = self.generators
        lines = self.lines

        fields, data = [], []
        fields.append('generator name')
        data.append(self.get_values(gens, 'name'))
        fields.append('u')
        data.append(self.get_values(gens, 'status', t))
        fields.append('P')
        data.append(self.get_values(gens, 'power', t))

        writeCSV(fields, transpose(
            data), filename=full_filename('powerflow-generators.csv'))

        fields, data = [], []
        fields.append('from')
        data.append(self.get_values(lines, 'frombus'))
        fields.append('to')
        data.append(self.get_values(lines, 'tobus'))
        fields.append('power')
        data.append(self.get_values(lines, 'power', t))
        fields.append('congestion shadow price')
        data.append(self.line_prices[str(t)])
        writeCSV(fields, transpose(data), filename=full_filename('powerflow-lines.csv'))

        # Create a new csv file that reports dr output
        fields, data = [], []

        fields.append('bus')
        data.append(getattrL(self.loads, 'bus'))
        fields.append('dr_2')
        data.append(self.get_values(self.loads, 'dr_per_load_node', t))

        writeCSV(fields, transpose(data), filename=full_filename('powerflow-dr.csv'))

    def info_price(self, t):
        return []  # built into bus info


class Solution_UC(Solution):

    def info_lines(self, t):
        return []

    def info_buses(self, t):
        return []

    def saveCSV(self):
        '''generator power values and statuses for unit commitment'''

        # mutlistage UC
        # for stochastic: output is the generators' power under observed wind
        self.load_shed_timeseries.name = 'dr'
        self.load_shed_timeseries.to_csv(full_filename('commitment-dr.csv'), header=True)

        # Original implementaton:

        columns_list = []
        for H in range(len(self.generators_power.columns)):
            columns_list.insert(H, 'g' + str(H))

        AB = pd.DataFrame(columns=columns_list)
        for H in range(len(self.generators_power.columns)):
            AB.ix[:, 'g' + str(H)] = self.generators_power.ix[:, 'g' + str(H)]

        AB.to_csv(full_filename('commitment-power.csv'))

        self.generators_status.to_csv(full_filename('commitment-status.csv'))

        if self.cascades_storagelevel is not None:
            self.cascades_storagelevel.to_csv(full_filename('commitment-cascades_storagelevel.csv'))

        if self.cascades_discharge is not None:
            self.cascades_discharge.to_csv(full_filename('commitment-cascade_discharge.csv'))

        if self.cascades_spill is not None:
            self.cascades_spill.to_csv(full_filename('commitment-cascade_spill.csv'))

        self.fuelcost.to_csv(full_filename('operating-cost.csv'))

        if UC_NETWORKED:

            lines = self.lines
            fields, data = [], []
            fields.append('from')
            data.append(self.get_values(lines, 'frombus'))
            fields.append('to')
            data.append(self.get_values(lines, 'tobus'))
            for t in self.times:
                fields.append(t)
                data.append(self.get_values(lines, 'power', t))
            writeCSV(fields, transpose(data), filename=full_filename('commitment-linesflows.csv'))

        if self.storage_content is not None and self.ev_content is not None:
            storage_ev_content = pd.concat([self.storage_content, self.ev_content], axis=1)

            storage_ev_content.to_csv(full_filename('storage_ev-content.csv'))

        self.load_shed_status_timeseries = Solution.get_load_shed_status_timeseries

        self.gen_and_dr_status = self.generators_status

        self.gen_and_dr_status['dr'] = pd.Series(self.load_shed_status_timeseries, index=self.gen_and_dr_status.index)

    def visualization(self, withPrices=True):
        '''generator output visualization for unit commitment'''
        if not do_plotting:
            return

        prices = [self.lmps[str(t)][0] for t in self.times]
        stack_plot_UC(
            self, self.power_system.generators(), self.times, prices, withPrices=withPrices)
        self.savevisualization(full_filename('commitment.png'))


class Solution_SCUC(Solution_UC):

    def visualization(self):
        logging.warning('no visualization for SCUC. Spreadsheet output is valid, except for the price column is the price on first bus only.')


class Solution_UC_multistage(Solution_UC):

    '''
    Muti-stage unit commitment. Each stage represents one optimization problem.
    Each element of the list :param:stage_solutions is a :class:`~results.Solution_UC` object.

    '''

    def __init__(self, power_system, stage_times, stage_solutions):
        update_attributes(self, locals(),
                          exclude=['stage_solutions', 'stage_times'])
        self._resolved = power_system.is_stochastic \
            or user_config.deterministic_solve or user_config.perfect_solve

        self.is_stochastic = any(sln.is_stochastic for sln in stage_solutions)

        times = pd.concat(
            [times.non_overlap().strings for times in stage_times]).index
        self.times = TimeIndex(times)
        self.times.set_initial(stage_times[0].initialTime)

        self.objective = self._sum_over('objective', stage_solutions)
        self.solve_time = self._sum_over('solve_time', stage_solutions)
        self.mipgaps = pd.Series([sln.mipgap for sln in stage_solutions])

        self._get_outputs(stage_solutions)
        self._get_costs(stage_solutions)
        self._get_prices(stage_solutions)

        print("You are modelling a multistage UC problem")

    def _sum_over(self, attrib, stage_solutions):
        return sum(getattr(sln, attrib) for sln in stage_solutions)

    def _concat(self, attrib, slns):
        return pd.concat([getattr(sln, attrib) for sln in slns])

    def _get_outputs(self, slns):
        '''the outputs under observed wind'''

        self.generators_power = self._concat('generators_power', slns)
        self.generators_status = self._concat('generators_status', slns)
        if self._resolved:
            self.expected_power = self._concat('expected_power', slns)
            self.expected_status = self._concat('expected_status', slns)

    def _get_costs(self, slns):
        self.expected_cost = self.totalcost_generation = \
            self._concat('expected_totalcost'
                         if self._resolved else 'totalcost_generation', slns)

        self.load_shed_timeseries = self._concat('load_shed_timeseries', slns)
        self.gen_shed_timeseries = self._concat('gen_shed_timeseries', slns)
        self.load_shed = self.load_shed_timeseries.sum()
        self.gen_shed = self.gen_shed_timeseries.sum()

        if self._resolved:
            self.observed_cost = self.totalcost_generation = \
                self._concat('observed_totalcost', slns)

    def info_cost(self):
        resolved = self._resolved
        expected = 'expected ' if resolved else ''
        observed = 'observed ' if resolved else ''
        out = []
        print("commented out line 929 of results.py- put back in later")
        '''out.append('total {}generation cost = {}'.format(
            expected, self.expected_cost.sum().sum()))'''
        if resolved:
            out.append('total {}generation cost = {}'.format(
                observed, self.observed_cost.sum().sum()))

        print("objective cost :", self.objective)
        return out

    def _get_prices(self, stage_solutions):
        self.lmps = {}
        try:
            for stage in stage_solutions:
                self.lmps.update(stage.lmps)
        except:
            # no lmps in stochastic solutions right now
            pass

    def show(self):
        out = []
        out.extend(self.info_status())
        out.extend(self.info_cost())
        out.extend(self.info_shedding())
        print("Shedding : ", self.info_shedding())
        print('\n'.join(out))
        print("info cost: ", self.info_cost())

    def info_generators(self):
        return []

    def info_loads(self):
        return []

    def info_status(self):
        return ['solved multistage problem in a total solver time '
                + 'of {time:0.4f} sec'.format(time=self.solve_time)]

    def info_shedding(self):
        return [
            'total load shed={}MW'.format(self.load_shed)
            if self.load_shed > 0.01 else '',
            'total gen shed={}MW'.format(self.gen_shed)
            if self.gen_shed > 0.01 else '',
        ]


class MultistageStandalone(Solution_UC_multistage):

    def __init__(self, power_system, stage_times, store):
        self.power_system = power_system
        self.is_stochastic = power_system.is_stochastic
        self._resolved = self.is_stochastic or \
            user_config.deterministic_solve or user_config.perfect_solve
        times = pd.concat(
            [times.non_overlap().strings for times in stage_times]).index
        self.times = TimeIndex(times)
        self.times.set_initial(stage_times[0].initialTime)

        self.expected_cost = self.totalcost_generation = store['expected_cost']

        if self._resolved:
            self.observed_cost = self.totalcost_generation = \
                store['observed_cost']

        self.generators_power = store['power']
        self.generators_status = store['status']
        self.load_shed_timeseries = store['load_shed']
        self.gen_shed_timeseries = store['gen_shed']

        self.load_shed = store['load_shed'].sum()
        self.gen_shed = store['gen_shed'].sum()

        self.solve_time = store['solve_time'].sum()


class Solution_Stochastic(Solution):

    def __init__(self, power_system, times, datadir='.', is_stochastic=True):
        update_attributes(self, locals())
        self._setup_powersystem()

        self.scenarios = sorted(self.power_system._scenario_instances.keys())
        self.stage_start = times.strings.index[0]

        gen = self.power_system.get_generator_with_scenarios()

        try:  # single stage setup
            self.probability = gen.scenario_values.probability.dropna()
        except AttributeError:  # staged setup
            self.probability = gen._get_scenario_probabilities(times)

        self.probability.index = self.scenarios

        self._get_problem_info()
        self._get_outputs()
        self._get_costs()
        self._get_prices()

    def stg_panel(self, method,
                  generators=None,
                  no_overlap=True,
                  evaluate=False):
        if generators is None:
            generators = self.generators

        times = self.times_non_overlap if no_overlap else self.times
        if evaluate:
            getval = lambda s, gen, t: value(
                getattr(gen, method)(t, s, evaluate=True))
        else:
            getval = lambda s, gen, t: value(getattr(gen, method)(t, s))
        return pd.Panel(
            [[[getval(s, gen, t) for gen in generators]
                for t in times] for s in self.scenarios],
            items=[s for s in self.scenarios],
            major_axis=times.strings.index,
            minor_axis=[str(g) for g in generators])

    def gen_time_df(self, method, scenario, non_overlap=True, evaluate=False,
                    generators=None):
        if generators is None:
            generators = self.generators
        if evaluate:

            getval = lambda gen, t: value(
                getattr(gen, method)(t, scenario, evaluate=True))
        else:
            getval = lambda gen, t: value(getattr(gen, method)(t, scenario))

        times = self.times_non_overlap if non_overlap else self.times
        return gen_time_dataframe(generators, times,
                                  [[getval(gen, t) for t in times] for gen in generators])

    def _get_outputs(self, resolve=False):
        if resolve:

            self.generators_power = self.gen_time_df('power', None)
            self.generators_status = self.gen_time_df('status', None)

        else:
            self.generators_power_scenarios = self.stg_panel('power')
            self.generators_status_scenarios = \
                correct_status(self.stg_panel('status'))
            self.expected_status = \
                self.generators_status_scenarios[self.scenarios[0]]
            self.generators_status = self.expected_status.copy()
            self.expected_power = self.generators_power = self._calc_expected(
                self.generators_power_scenarios)
        return

    def _calc_expected(self, panel):
        out = panel.copy()
        for s, pr in list(self.probability.items()):
            out[s] *= pr
        return out.sum(axis=0)

    def _calc_expected_cost(self, method):
        '''
        calculate expected cost by getting costs over all scenarios,
        multiplying each scenario cost by its probability,
        and summing over the scenarios
        '''
        return self._calc_expected(self.stg_panel(method, evaluate=True))

    def _get_costs(self, resolve=False):
        if resolve:
            # resolved on the first scenario instance
            # -- no more scenario labeling is needed
            s = None
            self.observed_totalcost = self.totalcost_generation = \
                self.gen_time_df('cost', s, evaluate=True)
            self.observed_fuelcost = self.fuelcost = \
                self.gen_time_df('operatingcost', s, evaluate=True)

            # calc observed load shed
            if len(self.loads) > 1:
                raise NotImplementedError
            self.load_shed_timeseries = pd.Series(
                [self.power_system.loads()[0].shed(time, evaluate=True)
                    for time in self.times_non_overlap],
                index=self.generators_power.index)

            self.gen_shed_timeseries = self.gen_time_df('shed', s,
                                                        evaluate=True,
                                                        generators=self.power_system.get_generators_noncontrollable()
                                                        ).sum(axis=1)

            self.load_shed = self.load_shed_timeseries.sum()
            self.gen_shed = self.gen_shed_timeseries.sum()

            if self.gen_shed > 0.01:
                logging.debug('generation shed: {}MW'.format(self.gen_shed))
            if self.load_shed > 0.01:
                logging.debug('load shed: {}MW'.format(self.load_shed))

        else:
            # get expected cost of non_overlap times
            self.expected_totalcost = self._calc_expected_cost('cost')
            self.expected_fuelcost = self._calc_expected_cost('operatingcost')

            # calculate expected load, gen shed
            self.expected_gen_shed_timeseries = self._calc_expected(self.stg_panel('shed', evaluate=True,
                                                                                   generators=self.power_system.get_generators_noncontrollable()))
            self.expected_gen_shed = self.expected_gen_shed_timeseries.sum().sum()

            self.expected_load_shed_timeseries = self._calc_expected(
                self.stg_panel('shed', evaluate=True,
                               generators=self.power_system.loads()))
            self.expected_load_shed = self.expected_load_shed_timeseries.sum()

            if self.expected_gen_shed > 0.01:
                logging.debug('expected generation shed: {}MW'.format(
                    self.expected_gen_shed))
            if self.expected_load_shed > 0.01:
                logging.debug('expected load shed: {}MW'.format(
                    self.expected_load_shed))

    def _get_cost_error(self):
        pass

    def _get_prices(self):
        pass

    def info_cost(self):
        return ['expected cost= {}'.format(self.expected_totalcost.sum().sum())]

    def show(self):
        '''Display the solution information to the terminal'''
        out = ['']
        out.extend(['Solution information', '-' * 20])
        out.extend(self.info_cost())


class Solution_Stochastic_UC(Solution_Stochastic):

    def saveCSV(self):
        '''generator power and status values for stochastic unit commitment'''
        try:
            self.generators_power.to_csv(full_filename('commitment-power.csv'))
        except AttributeError:
            logging.warn('''didnt resolve - no power
                 values are available for csv format''')
        print("Results, generators_status: ", generators_status)
        self.generators_status.to_csv(full_filename('commitment-status.csv'))


def _colormap(numcolors, colormapName='gist_rainbow', mincolor=1):
    cm = matplotlib.cm.get_cmap(colormapName)
    return [cm(1. * i / numcolors) for i in range(mincolor, numcolors + mincolor)]


def stack_plot_UC(solution, generators, times, prices,
                  withPrices=True,
                  seperate_legend=False,
                  hours_tick_interval=None
                  ):
    withPrices = withPrices and any(prices)
    font_big = {'fontsize': 15}
    figWidth = .85
    figLeft = old_div((1 - figWidth), 2)
    yLabel_pos = {'x': -0.12, 'y': 0.5}

    plot.figure(figsize=(10, 4), dpi=180)
    ax = plot.axes([figLeft, .1, figWidth, .6])
    ax.set_ylabel('energy [MWh]', ha='center', **font_big)
    ax.yaxis.set_label_coords(**yLabel_pos)
    prettify_axes(ax)

    T = times.times.values
    bar_width = times.intervalhrs / 24.0  # maplotlib dates have base of 1day

    convert_to_GW = solution.generators_power.max().sum() > 20e3

    power = solution.generators_power.copy()
    if convert_to_GW:
        power = old_div(power, 1e3)

    colors = _colormap(len(generators), colormapName='Blues')

    power.plot(ax=ax, kind='bar', legend=True, stacked=True, color=colors, edgecolor='none')

    # show prices
    if withPrices:
        prices = pd.Series(prices, index=times.times)
        prices[prices == user_config.cost_load_shedding] = None
        prices = prices.dropna()
        if len(prices):
            axes_price = plot.axes([figLeft, .75, figWidth, .2], sharex=ax)

            # HACK - pandas uses this index for the bar plots
            # and since they share an x axis, use the same x values here
            pd.Series(prices.values, index=np.arange(len(prices)) + 0.25).plot(
                ax=axes_price, drawstyle='steps')

            # axes_price.step(times.times.tolist() + [times.End], prices.values.tolist() + [prices[prices.index[-1]]],
            #                 where='post')  # start from 1 past initial time
            axes_price.set_ylabel('price\n[$/MWh]', ha='center', **font_big)
            axes_price.yaxis.set_label_coords(**yLabel_pos)
            plot.setp(axes_price.get_xticklabels(), visible=False)
            # format the price axis nicely
            axes_price.set_ylim(.9 * prices.min(), 1.1 * prices.max())
            axes_price.yaxis.set_major_locator(
                matplotlib.ticker.MaxNLocator(5))
            prettify_axes(axes_price)

    ax.xaxis_date()

    for label in ax.get_xticklabels():
        label.set_ha('right')
        label.set_rotation(30)

    # format the time axis nicely
    if hours_tick_interval is None:
        if len(times) < 10:
            pass
        else:
            if 24 * 10 > times.spanhrs > 48:
                ax.xaxis.set_major_locator(matplotlib.dates.DayLocator())
                ax.xaxis.set_major_formatter(
                    matplotlib.dates.DateFormatter('%Y-%m-%d'))
                ax.xaxis.set_minor_locator(matplotlib.dates.HourLocator())
            elif times.spanhrs < 48:
                ax.xaxis.set_major_locator(matplotlib.dates.HourLocator())
                ax.xaxis.set_major_formatter(
                    matplotlib.dates.DateFormatter('%H:%M'))
            # otherwise use defaults
    else:
        ax.xaxis.set_major_locator(
            matplotlib.dates.HourLocator(interval=hours_tick_interval))
        ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter('%H:%M'))

    # format the power axis nicely
    if convert_to_GW:
        ax.set_ylabel('energy [GWh]', ha='center', **font_big)
    ax.autoscale_view()


def prettify_axes(ax):
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.get_xaxis().tick_bottom()
    ax.get_yaxis().tick_left()


def shrink_axis(ax, percent_horizontal=0.20, percent_vertical=0):
    box = ax.get_position()
    ax.set_position([box.x0, box.y0, box.width * (1
                                                  - percent_horizontal), box.height * (1 - percent_vertical)])
