"""
Defines models for power systems components, including
:class:`~powersystems.PowerSystem`, :class:`~powersystems.Bus`,
:class:`~powersystems.Load` and  :class:`~powersystems.Line`.
:class:`~powersystems.Generator` components can be found
in the :module:`~generators`. Each of these objects inherits an
optimization framework from :class:`~optimization.OptimizationObject`.
"""
from __future__ import print_function
from __future__ import absolute_import
from __future__ import division

from builtins import str
from builtins import range
from past.utils import old_div
import logging

from .commonscripts import update_attributes, getattrL, flatten
from .config import user_config
from .optimization import value, OptimizationObject, OptimizationProblem, OptimizationError
from . import stochastic

from pyomo.environ import Block
import numpy as np
import pandas as pd
from .silver_variables import FOLDER_OPF, FOLDER_PRICE_OPF, PATH, FOLDER_UC, PATH_MODEL_INPUTS
import SILVER_VER_18.internal_vars as internal_vars


class Load(OptimizationObject):

    """
    Describes a power system load (demand).
    Currently only real power is considered.
    For OPF problems, the name of the bus can.
    For UC problems, schedules (pandas.Series objects) are used.
    By setting `sheddingallowed`, the amount of power can become a variable,
        (bounded to be at most the scheduled amount).
    """

    def __init__(self,
                 name='', index=None, bus=None, schedule=None,
                 sheddingallowed=True,
                 cost_shedding=None,
                 drpotential=None,
                 ):
        update_attributes(self, locals())  # load in inputs
        if cost_shedding is None:
            self.cost_shedding = user_config.cost_load_shedding
        self.init_optimization()
        self.shedding_mode = True
        self.daily_demand = 0
        self.commitment_problem = True
        self.generators = []

    def read_system_wide_dr(self):
        demand_response_inputs = pd.read_excel(PATH_MODEL_INPUTS, sheet_name='demand response', index_col=0)

        max_hourly_dr_p = demand_response_inputs.ix['maximum hourly dr limit (% of schedule)']
        max_hourly_dr_a = demand_response_inputs.ix['absolute hourly dr limit (MW)']
        max_daily_dr = demand_response_inputs.ix['maximum daily dr (MW)']
        ramp_limits = demand_response_inputs.ix['ramping limiatations (MW)']
        min_up_down_times = demand_response_inputs.ix['minimum up/down times (hours)']
        initial_values = demand_response_inputs.ix['initial values']
        return max_hourly_dr_p, max_hourly_dr_a, max_daily_dr, ramp_limits, min_up_down_times, initial_values

    def read_dPrev_dr(self):
        try:
            self.dPrev_dr = pd.read_csv(FOLDER_UC / 'commitment-dr.csv')
            return self.dPrev_dr
        except:
            pass

    ####################################
    # OBJECTIVE FUNCTION
    ####################################

    def create_objective(self, times):
        return sum([self.cost(time) for time in times])

    def cost(self, time, scenario=None):

        shedding_cost = self.cost_shedding * self.shed_variable(time)
        return shedding_cost

    def cost_first_stage(self, times):
        return 0

    def cost_second_stage(self, times):

        self.summation = 0
        for time in times:
            self.shed_value = abs(self.up_shed(time))
            cost_value = self.shed_value * self.cost_shedding
            self.summation = self.summation + cost_value
        return sum(self.cost(time) for time in times)

    ####################################
    # OPTIMIZATION VARIABLES
    ####################################
    def power(self, time, scenario=None, evaluate=False):

        if self.shedding_mode:
            power = self.get_variable('power', time,
                                      scenario=scenario, indexed=True)
            if evaluate:
                power = value(power)

            return power
        else:
            # NOTE: IN self.shedding_mode is FALSE for OPF, therefore GETTING HERE!
            return self.get_scheduled_output(time)

    def shed_variable(self, time, scenario=None, evaluate=False):
        ''' create a new optimization variable 'shed_variable'; use constraints to force it to be positive '''
        # used to make the shedding value that gets passed into the cost calculation postive
        # For further documentation, refer to Philip Mar's emails
        if self.shedding_mode:
            shed_variable = self.get_variable('shed_variable', time, scenario=scenario, indexed=True)
            if evaluate:
                shed_variable = value(shed_variable)
            return shed_variable
        else:
            return 0

    def dr_status(self, time=None, scenario=None):
        ''' on/off (1/0) status of demand response depending on if shedding (up or down) is happening '''
        # if self.commitment_problem:
        if self.commitment_problem and self.shedding_mode:
            return self.get_variable('dr_status', time, scenario=scenario, indexed=True)
        else:
            return 1

    def dr_per_load_node(self, time, scenario=None, evaluate=False):
        dr_per_load_node = self.get_variable('dr_per_load_node', time, scenario=None, indexed=True)
        if evaluate:
            dr_per_load_node = value(dr_per_load_node)
        return dr_per_load_node

    def create_variables(self, times):
        if self.shedding_mode:
            self.add_variable('power', index=times.set, low=0)
            self.add_variable('shed_variable', index=times.set, low=0)
            self.add_variable('dr_status', index=times.set, kind='Binary')
            self.add_variable('dr_per_load_node', index=times.set)
            '''self.add_variable('z', index=times.set, low=0)'''

    ####################################
    # ACCOUNTING VARIABLES
    ####################################
    def get_scheduled_output(self, time):
        return float(self.schedule.ix[time])

    def shed(self, time, scenario=None, evaluate=False):
        return self.get_scheduled_output(time) - self.power(time, scenario, evaluate)

    def up_shed(self, time, scenario=None, evaluate=False):
        up_shed = self.get_scheduled_output(time) - self.power(time, scenario, evaluate)
        return up_shed

    def down_shed(self, time, scenario=None, evaluate=False):
        down_shed = -1 * (self.get_scheduled_output(time) - self.power(time, scenario, evaluate))
        return down_shed

    def daily_demand_Load(self, times):
        return sum(self.schedule.ix[time] for time in times)

    def change_in_shed(self, time, times, scenario=None, evaluate=False):
        time_index = [i for i, j in enumerate(times) if j == time][0]
        previous_time = times[time_index - 1]
        if time_index >= 1:
            return self.shed(time) - self.shed(previous_time)

    def dr_status_change(self, t, times):
        ''' is the unit changing status between t and t-1'''
        if t > 0:
            previous_status = self.dr_status(times[t - 1])
        else:
            previous_status = self.initial_status
        return self.dr_status(times[t]) - previous_status

    ####################################
    # CONSTRAINTS (and related functions)
    ####################################
    def create_constraints(self, times):
        if self.shedding_mode:

            if len(times) > 1:
                ''' UC constraints: like storage, dr gets optimized over 24-hour period (UC) not 1-hour period (OPF) '''

                max_hourly_dr_p, max_hourly_dr_a, max_daily_dr, ramp_limits, min_up_down_times, initial_values \
                    = self.read_system_wide_dr()
                self.total_daily_shed = 0

                ''' the sum of power generated over 24 hour period equals scheduled power demand over 24 hour period '''
                # Note: this constraint is added in the PowerSystems class, rather than the Load class
                # b/c the PowerSystems add_constraint does not have a time component, but Load class does
                # constraint name and function is 'daily_balance'

                for time in times:

                    ''' assign positive costs associated with up and down shifting demand'''
                    # Note: 'shed_variable' that is the abs(shed) - +ve for both up-shedding and down-shedding
                    self.add_constraint('shed_limit_1', time, self.up_shed(time) <= self.shed_variable(time))
                    self.add_constraint('shed_limit_2', time, self.down_shed(time) <= self.shed_variable(time))

                    ''' limit the total allowable demand response (abs of under/over production) over the course of a day '''
                    self.total_daily_shed = self.total_daily_shed + self.shed_variable(time)
                    self.add_constraint('total daily shed', time, self.total_daily_shed <= max_daily_dr.ix[1])

                    ''' Maximum / maximum hourly under/ over-production as a percentage of scheduled load '''

                    self.add_constraint('limit_up_shifting', time,
                                        self.power(time) <= max_hourly_dr_p[1] * self.get_scheduled_output(time))
                    self.add_constraint('limit_down_shifting', time,
                                        self.power(time) >= max_hourly_dr_p[2] * self.get_scheduled_output(time))

                    ''' Maximum & minimum hourly curtialment in absolute terms rather than as a % of scheduled load '''

                    self.add_constraint('max absolute dr', time,
                                        self.shed_variable(time) <= self.dr_status(time) * max_hourly_dr_a[1])
                    self.add_constraint('min absolute dr', time,
                                        self.shed_variable(time) >= self.dr_status(time) * max_hourly_dr_a[2])

                    ''' Limit the ramping capability for changes in dr from one hour to the next '''

                    if time != 't00':  # and time != 't24':

                        self.add_constraint('z_tPrev_limit_1', time,
                                            self.change_in_shed(time, times) <= ramp_limits[1])
                        self.add_constraint('z_tPrev_limit_2', time,
                                            self.change_in_shed(time, times) >= ramp_limits[2])
                    else:

                        self.read_dPrev_dr()
                        if hasattr(self, 'dPrev_dr'):
                            t23_dPrev_dr = self.dPrev_dr['dr'].iloc[-1]
                            self.add_constraint('z_tPrev_limit_1', time, self.shed(time) - t23_dPrev_dr <= ramp_limits[1])
                            self.add_constraint('z_tPrev_limit_2', time, self.shed(time) - t23_dPrev_dr >= ramp_limits[2])

                ''' Minimum number of consecutive hours that dr is on or off '''

                def roundoff(n):
                    m = int(n)

                    if n != m:
                        raise ValueError('min up/down times must be integer number of intervals not, {}'.format(n))

                    return m
                tInitial = times.initialTimestr
                tEnd = len(times)

                self.minuptime = min_up_down_times[1]
                self.mindowntime = min_up_down_times[2]
                self.initial_status_hour = initial_values[2]
                self.initial_status = initial_values[1]
                # Refer to set_initial_condition function in Generator Class

                if self.minuptime > 0:
                    up_intervals_remaining = roundoff(old_div((self.minuptime - self.initial_status_hour), times.intervalhrs))
                    min_up_intervals_remaining_init = int(min(tEnd, up_intervals_remaining * self.initial_status))
                else:
                    min_up_intervals_remaining_init = 0

                if self.mindowntime > 0:
                    down_intervals_remaining = roundoff(old_div((self.mindowntime - self.initial_status_hour), times.intervalhrs))
                    min_down_intervals_remaining_init = int(min(tEnd, down_intervals_remaining * (self.initial_status == 0)))

                else:
                    min_down_intervals_remaining_init = 0

                if min_up_intervals_remaining_init > 0:
                    self.add_constraint('minuptime_load', tInitial,
                                        0 >= sum([(1 - self.dr_status(times[t]))
                                                 for t in range(min_up_intervals_remaining_init)]))

                if min_down_intervals_remaining_init > 0:
                    self.add_constraint('mindowntime_load', tInitial, 0 == sum([self.dr_status(times[t])
                                                                               for t in range(min_down_intervals_remaining_init)]))

                # calculate up down intervals
                min_up_intervals = roundoff(old_div(self.minuptime, times.intervalhrs))
                min_down_intervals = roundoff(old_div(self.mindowntime, times.intervalhrs))

                for t, time in enumerate(times):
                    # min up time
                    if t >= min_up_intervals_remaining_init and self.minuptime > 0:
                        no_shut_down = list(range(t, min(tEnd, t + min_up_intervals)))
                        min_up_intervals_remaining = min(tEnd - t, min_up_intervals)
                        E = sum([self.dr_status(times[s]) for s in no_shut_down]) >= min_up_intervals_remaining * self.dr_status_change(t, times)
                        self.add_constraint('min up time load', time, E)

                    # min down time
                    if t >= min_down_intervals_remaining_init and self.mindowntime > 0:
                        no_start_up = list(range(t, min(tEnd, t + min_down_intervals)))
                        min_down_intervals_remaining = min(tEnd - t, min_down_intervals)
                        E = sum([1 - self.dr_status(times[s]) for s in no_start_up]) \
                            >= min_down_intervals_remaining * -1 * self.dr_status_change(t, times)
                        self.add_constraint('min down time load', time, E)

            else:
                ''' OPF related constraints: set DR to be the same and the previous UC run '''
                opf_status = internal_vars.opf_variable  # either 'price' or 'final'
                if opf_status == 'final':
                    '''in second OPF run, set the dr value to the calculated UC value '''

                    try:

                        dr_2 = pd.read_csv(FOLDER_OPF / 'commitment-dr.csv', index_col=0, header=None)
                        dr_3 = float(dr_2.ix['dr'])

                    except:
                        print("could not read commitment-dr.csv, and so overwrote dr_3 - starting timer")

                        dr_testing = pd.read_csv(FOLDER_UC / 'commitment-dr.csv', index_col=0, header=None)
                        dr_2 = pd.read_csv(FOLDER_OPF / 'commitment-dr.csv', index_col=0, header=None)
                        dr_3 = float(dr_2.ix['dr'])

                    for time in times:

                        if dr_3 >= 0:
                            self.add_constraint('opf shed limit 3', time,
                                                self.get_scheduled_output(time) - self.power(time) >= dr_3 * self.drpotential)

                        elif dr_3 < 0:
                            self.add_constraint('opf shed limit 4', time,
                                                self.get_scheduled_output(time) - self.power(time) <= dr_3 * self.drpotential)
                else:
                    '''' in the first OPF run, set the dr value to zero '''
                    for time in times:
                        self.add_constraint('opf shed limit 4', time,
                                            self.get_scheduled_output(time) == self.power(time))
            ''' assign dr to specific nodes: called in results.py to assign dr values to specific load centres'''
            for time in times:
                self.add_constraint('dr_per_load_node', time, self.get_scheduled_output(time) - self.power(time) == self.dr_per_load_node(time))

    def __str__(self):
        return 'd{ind}'.format(ind=self.index)


class Line(OptimizationObject):

    """
    A tranmission line. Currently the model
    only considers real power flow under normal conditions.
    """

    def __init__(self, name='', index=None, frombus=None, tobus=None,
                 reactance=0.05, pmin=None, pmax=9999, **kwargs):
        update_attributes(self, locals())  # load in inputs
        if self.pmin is None:
            self.pmin = -1 * self.pmax   # default is -1*pmax
        self.init_optimization()

    def power(self, time):
        return self.get_variable('power', time, indexed=True)

    def price(self, time):
        '''congestion price on line'''
        return self.get_dual('line flow', time)

    def create_variables(self, times):
        self.add_variable('power', index=times.set)

    def create_constraints(self, times, buses):
        '''create the constraints for a line over all times'''
        busNames = getattrL(buses, 'name')
        iFrom, iTo = busNames.index(self.frombus), busNames.index(self.tobus)
        for t in times:
            line_flow_ij = self.power(t) == \
                1000 * ((1 / self.reactance) * (buses[iFrom].angle(t)
                                                - buses[iTo].angle(t)))

            self.add_constraint('line flow', t, line_flow_ij)
            self.add_constraint(
                'line limit high', t, self.power(t) <= self.pmax)
            self.add_constraint(
                'line limit low', t, self.pmin <= self.power(t))

        return

    def __str__(self):
        return 'k{ind}'.format(ind=self.index)

    def __int__(self):
        return self.index


class Bus(OptimizationObject):

    """
    A transmission bus bus (usually a substation where one or more
    tranmission lines start/end).

    :param isSwing: flag if the bus is the swing bus
      (sets the reference angle for the system)
    """

    def __init__(self, name=None, index=None, isSwing=False):

        update_attributes(self, locals())  # load in inputs
        self.generators, self.loads = [], []
        self.init_optimization()

    def angle(self, time):

        return self.get_variable('angle', time, indexed=True)

    def price(self, time):

        return self.get_dual('power balance', time)

    def get_storage_power_in(self, time, evaluate=False):
        storage_power_in = 0
        for gen in self.generators:
            if type(gen).__name__ == 'Storage':
                if evaluate:
                    storage_power_in += value(gen.power_in(time))
                else:
                    storage_power_in += gen.power_in(time)
        return storage_power_in

    def get_EV_power_in(self, time, evaluate=False):
        ev_power_in = 0
        for gen in self.generators:
            if type(gen).__name__ == 'EV_aggregator':
                if evaluate:
                    ev_power_in += value(gen.power_in(time))
                else:
                    ev_power_in += gen.power_in(time)
        return ev_power_in

    def Pgen(self, t, evaluate=False):
        '''gets called for each bus-making this Class a good place to balance load and demand at each bus'''
        if evaluate:
            return sum(value(gen.power(t)) for gen in self.generators)
        else:
            return sum(gen.power(t) for gen in self.generators)

    def Pload(self, t, evaluate=False):
        if evaluate:
            return_value = sum(value(ld.power(t) for ld in self.loads))
        else:
            return_value = sum(ld.power(t) for ld in self.loads)
        return return_value

    def power_balance(self, t, Bmatrix, allBuses):
        if len(allBuses) == 1:
            lineFlowsFromBus = 0
        else:
            lineFlowsFromBus = 1000 * sum([Bmatrix[self.index][otherBus.index] * otherBus.angle(t) for otherBus in allBuses])  # P_{ij}=sum_{i} B_{ij}*theta_j ???

        return sum([-lineFlowsFromBus, -self.Pload(t), self.Pgen(t), self.get_storage_power_in(t), self.get_EV_power_in(t)])

    def create_variables(self, times):
        self.add_children(self.generators, 'generators')
        self.add_children(self.loads, 'loads')
        logging.debug('added bus {} components - generators and loads'.format(
            self.name))
#
        for gen in self.generators:
            gen.create_variables(times)

        logging.debug('created generator variables')
        for load in self.loads:
            load.create_variables(times)
        logging.debug('created load variables')
        self.add_variable('angle', index=times.set, low=-3.14, high=3.14)
        logging.debug('created bus variables ... returning')
        return

    def create_objective(self, times):
        return self.cost_first_stage(times) + self.cost_second_stage(times)

    def cost_first_stage(self, times):
        return sum(gen.cost_first_stage(times) for gen in self.generators) + \
            sum(load.cost_first_stage(times) for load in self.loads)

    def cost_second_stage(self, times):
        return sum(gen.cost_second_stage(times) for gen in self.generators) + \
            sum(load.cost_second_stage(times) for load in self.loads)

    def create_constraints(self, times, Bmatrix, buses, include_children=True):

        if include_children:
            for gen in self.generators:

                gen.create_constraints(times)
            for load in self.loads:
                load.create_constraints(times)
        nBus = len(buses)
        for time in times:
            ######## TRYING TO CREATE EASY WAY TO IDENTIFY WHICH NODE IS FAILING ##########
            try:
                self.add_constraint('power balance 2', time, self.power_balance(
                    time, Bmatrix, buses) == 0)  # power balance must be zer
            except:

                print("THIS BUS IS FAILING")
                print(self.name)

            self.add_constraint('power balance', time, self.power_balance(
                time, Bmatrix, buses) == 0)  # power balance must be zero
            if nBus > 1 and self.isSwing:
                self.add_constraint('swing bus', time, self.angle(
                    time) == 0)  # swing bus has angle=0

        return

    def __str__(self):
        return 'i{ind}'.format(ind=self.index)


class PowerSystem(OptimizationProblem):

    '''
    Power systems object which is the container for all other components.

    :param generators: list of :class:`~powersystem.Generator` objects
    :param loads: list of :class:`~powersystem.Load` objects
    :param lines: list of :class:`~powersystem.Line` objects

    Other settings are inherited from `user_config`.
    '''

    def __init__(self, generators, loads, lines=None):

        update_attributes(self, locals(),
                          exclude=['generators', 'loads', 'lines'])
        self.reserve_fixed = user_config.reserve_fixed
        self.reserve_load_fraction = user_config.reserve_load_fraction
        self.reserve_required = (self.reserve_fixed > 0) or \
            (self.reserve_load_fraction > 0.0)

        if lines is None:  # pragma: no cover
            lines = []

        buses = self.make_buses_list(loads, generators, lines)
        self.create_admittance_matrix(buses, lines)
        self.init_optimization()
        self.add_children(buses, 'buses')
        self.add_children(lines, 'lines')
        self.is_stochastic = len(
            [gen for gen in generators if gen.is_stochastic]) > 0

        self.shedding_mode = True

        self.MM_generators = generators
        self.MM_loads = loads

    def make_buses_list(self, loads, generators, lines):
        """
        Create list of :class:`powersystems.Bus` objects
        from the load and generator bus names. Otherwise
        (as in ED,UC) create just one (system)
        :class:`powersystems.Bus` instance.

        :param loads: a list of :class:`powersystems.Load` objects
        :param generators: a list of :class:`powersystems.Generator` objects
        :returns: a list of :class:`powersystems.Bus` objects
        """
        busNameL = []
        busNameL.extend(getattrL(generators, 'bus'))
        busNameL.extend(getattrL(loads, 'bus'))
        busNameL.extend(getattrL(lines, 'frombus'))
        busNameL.extend(getattrL(lines, 'tobus'))
        busNameL = pd.Series(pd.unique(busNameL)).dropna().tolist()

        if len(busNameL) == 0:
            busNameL = [None]

        buses = []
        swingHasBeenSet = False

        for b, busNm in enumerate(busNameL):
            newBus = Bus(name=busNm, index=b)
            for gen in generators:
                if gen.bus == newBus.name:
                    newBus.generators.append(gen)
                if not swingHasBeenSet:
                    newBus.isSwing = swingHasBeenSet = True
            for ld in loads:
                if ld.bus == newBus.name:
                    newBus.loads.append(ld)
            buses.append(newBus)

        return buses

    def create_admittance_matrix(self, buses, lines):
        """
        Creates the admittance matrix (B),
        with elements = total admittance of line from bus i to j.
        Used in calculating the power balance for OPF problems.

        :param buses: list of :class:`~powersystems.Line` objects
        :param lines: list of :class:`~powersystems.Bus` objects
        """
        nB = len(buses)
        self.Bmatrix = np.zeros((nB, nB))
        namesL = [bus.name for bus in buses]

        d = {'bus_name': namesL}
        df = pd.DataFrame(d)
        df.to_csv(FOLDER_PRICE_OPF / 'Bus_name.csv')
        df.to_csv(FOLDER_UC / 'Bus_name.csv')

        for line in lines:
            busFrom = buses[namesL.index(line.frombus)]
            busTo = buses[namesL.index(line.tobus)]
            self.Bmatrix[busFrom.index, busTo.index] += old_div(-1, line.reactance)
            self.Bmatrix[busTo.index, busFrom.index] += old_div(-1, line.reactance)

        for i in range(0, nB):
            self.Bmatrix[i, i] = -1 * sum(self.Bmatrix[i, :])

    def loads(self):
        return flatten(bus.loads for bus in self.buses)

    def generators(self):
        return flatten(bus.generators for bus in self.buses)

    ####################################
    # OBJECTIVE FUNCTION
    ####################################

    def create_objective(self, times):
        self.add_objective(self.cost_first_stage() + self.cost_second_stage())

    def cost_first_stage(self, scenario=None):
        return self.get_component('cost_first_stage', scenario=scenario)

    def cost_second_stage(self, scenario=None):
        return self.get_component('cost_second_stage', scenario=scenario)

    def create_variables(self, times):
        self.add_variable('cost_first_stage')
        self.add_variable('cost_second_stage')
        self.add_set('times', times._set, ordered=True)
        times.set = self._model.times
        for bus in self.buses:
            bus.create_variables(times)
        for line in self.lines:
            line.create_variables(times)
        logging.debug('... created power system vars... returning')

    ####################################
    # CONSTRAINTS (and related functions)
    ####################################

    def overall_dr_equals_uc(self, time, total_load, total_gen, dr_3):
        ''' force the dr in the UC formulation to be spread out among nodes in the OPF formulation '''
        # Note: cant use [return total_load - total_gen == dr_3] b/c doesn't account for power_in in Storage and EV
        return sum([load.power(time) for load in self.loads()]) == total_load - dr_3

    def get_daily_demand(self, times, load_class):
        '''calculate the daily electricity consumption - both demand and power_in to Storage and EV '''

        daily_demand = sum([load.daily_demand_Load(times) for load in self.loads()])
        self.power_in = 0
        for gen in self.MM_generators:
            if (type(gen).__name__ == 'Storage') or (type(gen).__name__ == 'EV_aggregator'):
                self.power_in += sum(gen.power_in(time) for time in times)
        return daily_demand + (-1) * self.power_in

    def Pgen(self, time, evaluate=False):

        daily_generation_mm = 0
        for t in time:
            daily_generation_mm += sum(gen.power(t) for gen in self.MM_generators)
        return daily_generation_mm

    def daily_balance(self, times):
        self.daily_demand = self.get_daily_demand(times, Load)
        self.daily_generation = self.Pgen(times)
        return self.daily_generation >= self.daily_demand

    def create_constraints(self, times, include_children=True):
        if include_children:
            if user_config.duals:
                self.add_suffix('dual')
            for bus in self.buses:
                bus.create_constraints(times, self.Bmatrix, self.buses)
            for line in self.lines:
                line.create_constraints(times, self.buses)

        # system reserve constraint
        self._has_reserve = not self.shedding_mode and \
            (self.reserve_fixed > 0 or self.reserve_load_fraction > 0)
        if self._has_reserve:
            print("has reserve?")
            for time in times:

                required_generation_availability = self.reserve_fixed + (1.0 + self.reserve_load_fraction) * sum(load.power(time) for load in self.loads())
                generation_availability = sum(
                    gen.power_available(time) for gen in self.generators())
                self.add_constraint('reserve', generation_availability >= required_generation_availability, time=time)

        self.add_constraint('system_cost_first_stage',
                            self.cost_first_stage()
                            == sum(bus.cost_first_stage(times) for bus in self.buses))
        self.add_constraint('system_cost_second_stage',
                            self.cost_second_stage()
                            == sum(bus.cost_second_stage(times) for bus in self.buses))

        ''' for UC problem (but not OPF problem): force the total generation to equalt the total load in a given day '''
        if len(times) != 1:
            if self.shedding_mode:
                self.add_constraint('daily_balance', self.daily_balance(times))

        # Constraint: that total (over all buses) dr in entire system equals the total dr (over all buses) scheduled in the UC
        # Needs to be here, becuase it is for the entire system, rather than one bus
        if len(times) == 1:

            opf_status = internal_vars.opf_variable
            if opf_status == 'final':
                # in second OPF run, set the dr value to the calculated UC value
                try:
                    dr_2 = pd.read_csv(FOLDER_OPF / 'commitment-dr.csv', index_col=0, header=None)
                    dr_3 = float(dr_2.ix['dr'])
                except:

                    print("Commitment-dr.csv file could not be read- overwrote dr with zero")
                    dr_3 = 0
                for time in times:
                    total_load = sum(load.get_scheduled_output(time) for load in self.loads())
                    total_gen = sum(gen.power(time) for gen in self.generators())
                    self.add_constraint('overall dr equals uc',
                                        self.overall_dr_equals_uc(time, total_load, total_gen, dr_3))

        ''' currently working here: force seasonal storage cumulative pumping/generation for week to required value'''
        ''' Note: this restriction on the cumulative Hydrogen storage activity applies only to a seasonal (52-hour)
            run, not an hourly annual (24 - hour) run '''
        ''' the constraint below sets the difference between generation and pumping of the Hydrogen asset,
            not the amount left in the Hydrogen asset (storage content); as such, it represents the change in
            storage content over the designated period; e.g. a cumulative_weekly_activity of -10 would represnt a
            change in storage content from 100 at the beginning of the analysis period to 90 at the end'''
        if len(times) != 1:

            for asset in self.MM_generators:
                if asset.kind == 'Hydrogen':
                    cumulative_weekly_activity_df = pd.read_csv(FOLDER_UC / 'seasonal_activity.csv')
                    cumulative_weekly_activity = cumulative_weekly_activity_df.ix[0, 'Hydrogen']
                    test_all_gen = self.Pgen(times)
                    weekly_power = 0

                    for t in times:
                        # Note: using PHS efficiency (85%) instead of hydrogen effieicny (50%), b/c
                        # using hydrogen efficiencies makes the cumulative weekly activity an incorrect value
                        self.site_independent = pd.read_excel(PATH_MODEL_INPUTS, sheet_name='site independent', index_col=0)
                        self.n_generating = np.sqrt(self.site_independent[3]['PHS'])
                        hourly_power = (old_div(asset.power(t), self.n_generating)) + (asset.power_in(t) * self.n_generating)
                        weekly_power += hourly_power
                    self.add_constraint('seasonal_storage', weekly_power == -cumulative_weekly_activity)

            #######  Cascade constraints  ###########

            groups_name = []
            self.cascade_gen = []
            for gen in self.MM_generators:

                try:
                    groups_name.append(gen.cascadegroupname)
                    self.cascade_gen.append(gen)
                except:

                    pass

            groups_name = np.unique(groups_name)
            self.groups = {}
            for group in groups_name:

                gens_in_group = []
                for gen in self.cascade_gen:

                    if gen.cascadegroupname == group:

                        gens_in_group.append(gen)
                self.groups[group] = gens_in_group

            for i in range(len(self.cascade_gen)):

                def cascade_storage_level(gen, t):

                    tPrev = get_tPrev(t, gen, times)

                    if self.cascade_gen[i].number == 1:

                        if t != 't00':

                            return self.cascade_gen[i].storage_level(tPrev) + self.cascade_gen[i].nhinput[t] - \
                                self.cascade_gen[i].discharge(t) - self.cascade_gen[i].spill(t) == self.cascade_gen[i].storage_level(t)
                        else:

                            return self.cascade_gen[i].initial_storagecontent + self.cascade_gen[i].nhinput[t] - \
                                self.cascade_gen[i].discharge(t) - self.cascade_gen[i].spill(t) == self.cascade_gen[i].storage_level(t)
                    else:

                        if t != 't00':
                            for gen_in_group in self.groups[self.cascade_gen[i].cascadegroupname]:
                                if gen_in_group.number == self.cascade_gen[i].number - 1:
                                    return self.cascade_gen[i].storage_level(tPrev) + self.cascade_gen[i].nhinput[t] + gen_in_group.discharge(t) + gen_in_group.spill(t) - \
                                        self.cascade_gen[i].discharge(t) - self.cascade_gen[i].spill(t) == self.cascade_gen[i].storage_level(t)

                        else:

                            for gen_in_group in self.groups[self.cascade_gen[i].cascadegroupname]:

                                if gen_in_group.number == self.cascade_gen[i].number - 1:
                                    return self.cascade_gen[i].initial_storagecontent + self.cascade_gen[i].nhinput[t] + gen_in_group.discharge(t) + gen_in_group.spill(t) - \
                                        self.cascade_gen[i].discharge(t) - self.cascade_gen[i].spill(t) == self.cascade_gen[i].storage_level(t)

                self.cascade_gen[i].add_constraint_set('cascade_storage_level', times.set, cascade_storage_level)

    def iden(self, time=None):
        name = 'system'
        if time is not None:
            name += '_' + str(time)
        return name

    def total_scheduled_load(self):
        ''' called by debug '''

        return sum([load.schedule for load in self.loads()])

    def total_scheduled_generation(self):
        ''' called by debug '''
        # This only includes wildHorse, the non_controllable generator, but not the other generators
        return sum(gen.schedule for gen in self.generators() if not gen.is_controllable)

    def get_generators_controllable(self):
        return [gen for gen in self.generators() if gen.is_controllable]

    def get_generators_noncontrollable(self):
        return [gen for gen in self.generators() if not gen.is_controllable]

    def get_generators_without_scenarios(self):
        return [gen for gen in self.generators() if getattr(gen, 'is_stochastic', False) is False]

    def get_generator_with_scenarios(self):
        gens = [gen for gen in self.generators() if getattr(gen, 'is_stochastic',
                                                            False)]
        if len(gens) > 1:  # pragma: no cover
            raise NotImplementedError(
                'Dont handle the case of multiple stochastic generators')
        elif len(gens) == 0:  # pragma: no cover
            return []
        else:
            return gens[0]

    def get_generator_with_observed(self):
        return [gen for gen in self.generators() if getattr(gen, 'observed_values', None) is not None][0]

    def get_finalconditions(self, sln):
        times = sln.times

        tEnd = times.last_non_overlap()  # like 2011-01-01 23:00:00
        tEndstr = times.non_overlap().last()  # like t99

        status = sln.generators_status

        for gen in self.generators():
            g = str(gen)
            stat = status[g]

            if sln.is_stochastic:
                gen.finalstatus = dict(
                    power=sln.generators_power[g][tEnd],
                    status=sln.generators_status[g][tEnd],
                    hoursinstatus=gen.gethrsinstatus(times.non_overlap(), stat)
                )
            else:
                gen.finalstatus = gen.getstatus(tEndstr,
                                                times.non_overlap(), stat)
        return

    def set_initialconditions(self, initTime):
        for gen in self.generators():
            finalstatus = getattr(gen, 'finalstatus', {})
            if finalstatus:
                gen.set_initial_condition(**finalstatus)
                del gen.finalstatus
        return

    def solve_problem(self, times):
        try:

            instance = self.solve()

        except OptimizationError:
            # re-do stage, with load shedding allowed
            logging.critical('stage infeasible, re-run with shedding.')
            self.allow_shedding(times)
            try:
                instance = self.solve()
            except OptimizationError:
                scheduled, committed = self.debug_infeasible(times)
                raise OptimizationError('failed to solve with shedding.')
        return instance

    def resolve_stochastic_with_observed(self, instance, sln):
        s = sln.scenarios[0]
        self._model = instance.active_components(Block)[s]
        self.is_stochastic = False
        self.stochastic_formulation = False

        self._resolve_problem(sln)

        # re-store the generator outputs and costs
        sln._resolved = True
        sln._get_outputs(resolve=True)
        sln._get_costs(resolve=True)

        self.is_stochastic = True
        self.disallow_shedding()
        return

    def resolve_determinisitc_with_observed(self, sln):
        # store the useful expected value solution information
        sln.expected_status = sln.generators_status.copy()
        sln.expected_power = sln.generators_power.copy()
        sln.expected_fuelcost = sln.fuelcost.copy()
        sln.expected_totalcost = sln.totalcost_generation.copy()
        sln.expected_load_shed = float(sln.load_shed)

        # resolve the problem
        self._resolve_problem(sln)

        # re-calc the generator outputs and costs
        sln._resolved = True
        sln._get_outputs()
        sln._get_costs()

        sln.observed_fuelcost = sln.fuelcost
        sln.observed_totalcost = sln.totalcost_generation
        self.disallow_shedding()
        return

    def _set_load_shedding(self, to_mode):
        '''set system mode for load shedding'''
        for load in [ld for ld in self.loads() if ld.sheddingallowed]:
            load.shedding_mode = to_mode

    def _set_gen_shedding(self, to_mode):
        for gen in [g for g in self.generators() if not g.is_controllable and g.sheddingallowed]:
            gen.shedding_mode = to_mode

    def allow_shedding(self, times, resolve=False):
        self.shedding_mode = True
        self._set_load_shedding(True)

        if not user_config.economic_wind_shed:
            logging.debug('allowing non-controllable generation shedding')
            self._set_gen_shedding(True)

        const_times = times.non_overlap() if resolve else times

        # make load power into a variable instead of a param
        for load in self.loads():

            try:
                load.create_variables(times)  # need all for the .set attrib
                load.create_constraints(const_times)
            except RuntimeError:
                # load already has a power variable and shedding constraint
                pass

        if not user_config.economic_wind_shed:
            for gen in [gen for gen in self.get_generators_noncontrollable() if gen.shedding_mode]:
                # create only the power_used var, don't reset the power param
                gen.create_variables_shedding(times)
                gen.create_constraints(const_times)

        # recalc the power balance constraint
        for bus in self.buses:
            for time in const_times:
                bus._remove_component('power balance', time)
            bus.create_constraints(const_times,
                                   self.Bmatrix, self.buses, include_children=False)

        # reset objective
        self.reset_objective()
        self.create_objective(const_times)
        # re-create system cost constraints
        self._remove_component('system_cost_first_stage')
        self._remove_component('system_cost_second_stage')
        if self._has_reserve:
            self._remove_component('reserve')
        self.create_constraints(const_times, include_children=False)

        # recreating all constraints would be simpler
        # but would take a bit longer
        # self.create_constraints(const_times, include_children=True)
        if self.is_stochastic:
            # need to recreate the scenario tree variable links
            stochastic.define_stage_variables(self, times)
            # and the stochastic instance
            stochastic.create_problem_with_scenarios(self, times)

    def disallow_shedding(self):
        # change shedding allowed flags for the next stage
        self.shedding_mode = True
        self._set_load_shedding(True)
        if user_config.economic_wind_shed is False:
            self._set_gen_shedding(False)

    def _resolve_problem(self, sln):
        times = sln.times_non_overlap
        self._remove_component('times')
        self.add_set('times', times._set, ordered=True)
        times.set = self._model.times

        # reset the constraints
        self._remove_all_constraints()
        # dont create reserve constraints
        self.reserve_fixed = 0
        self.reserve_load_fraction = 0

        # set wind to observed power
        gen = self.get_generator_with_observed()
        gen.set_power_to_observed(times)

        # reset objective to only the non-overlap times
        self.reset_objective()
        self.create_objective(times)

        # recreate constraints only for the non-overlap times
        self.create_constraints(times)

        # fix statuses for all units
        self.fix_binary_variables()

        # store original problem solve time
        self.full_sln_time = self.solution_time
        full_mipgap = self.mipgap

        logging.info('resolving with observed values')
        try:
            self.solve()
        except OptimizationError:
            faststarts = [str(gen) for gen in [gen for gen in self.generators() if gen.faststart]]
            # at least one faststarting unit must be available (off)
            if user_config.faststart_resolve and \
                    (sln.expected_status[faststarts] == 0).any().any():
                self._resolve_with_faststarts(sln)
            else:
                # just shed the un-meetable load and calculate cost later
                self.allow_shedding(sln.times, resolve=True)
                try:
                    self.solve()
                except OptimizationError:
                    scheduled, committed = self.debug_infeasible(
                        sln.times, resolve_sln=sln)
                    raise

        self.resolve_solution_time = float(self.solution_time)
        self.solution_time = float(self.full_sln_time)

        if self.mipgap:
            self.mipgap_resolve = float(self.mipgap)
        if full_mipgap:
            self.mipgap = float(full_mipgap)

        logging.info('resolved instance with observed values (in {}s)'.format(
            self.resolve_solution_time))

    def _resolve_with_faststarts(self, sln):
        '''allow faststart units to be started up to meet the load'''
        self._unfix_variables()
        self._fix_non_faststarts(sln.times)
        logging.warning('allowing fast-starting units')

        try:
            self.solve()
        except OptimizationError:
            self._unfix_variables()
            self._fix_non_faststarts(sln.times, fix_power=False)
            logging.warning(
                'allowing non fast-starters to change power output')
            try:
                self.solve()
            except OptimizationError:
                logging.warning('allowing load shedding')
                self.allow_shedding(sln.times, resolve=True)
                try:
                    self.solve()
                except OptimizationError:
                    scheduled, committed = self.debug_infeasible(
                        sln.times, resolve_sln=sln)
                    raise

    def _fix_non_faststarts(self, times, fix_power=True):
        '''
        fix non-faststart units - both power and status
        (unless this is infeasible, then only fix status)
        the idea is that fast-starts should be contributing power
        only for system security, not economics
        '''
        names = []
        for gen in [gen for gen in self.generators() if (not gen.faststart) and gen.is_controllable]:
            names.append(gen.status().name)
            if fix_power:
                names.append(gen.power().name)
        self._fix_variables(names)

    def debug_infeasible(self, times, resolve_sln=None):  # pragma: no cover
        print("getting into debug infeasible:")
        generators = self.generators()
        if resolve_sln:
            windgen = self.get_generator_with_observed()

            scheduled = pd.DataFrame({
                'expected_power': resolve_sln.generators_power.sum(axis=1).values,
                'expected_wind': windgen.schedule.ix[times.non_overlap()],
                'observed_wind': windgen.observed_values.ix[times.non_overlap()],
            })
            scheduled['net_required'] = \
                scheduled.expected_wind - scheduled.observed_wind
        else:
            scheduled = pd.DataFrame({
                'load': self.total_scheduled_load().ix[times.strings.values]})

            if self.is_stochastic:
                gen = self.get_generator_with_scenarios()
                scenarios = gen.scenario_values[times.Start.date()].drop('probability', axis=1).T
                scenarios.index = scheduled.index

                scheduled['net_load'] = scheduled['load'] - sum(
                    [gen.schedule for gen in [gen for gen in self.get_generators_noncontrollable() if not gen.is_stochastic]])

                gen_required = (-1 * scenarios).add(scheduled.net_load, axis=0)

                print('generation required')
                print(gen_required)
                print(gen_required.describe())

            else:
                if any([hasattr(gen, 'schedule') for gen in self.generators()]):
                    scheduled['generation'] = self.total_scheduled_generation().ix[times.strings.values]
                else:
                    scheduled['generation'] = 0

                scheduled['net_required'] = scheduled['load'] - \
                    scheduled.generation

        print('total scheduled\n', scheduled)

        if resolve_sln:
            committed = pd.DataFrame(dict(
                Pmin=[gen.pmin for gen in generators],
                Pmax=[gen.pmax for gen in generators],
                rampratemin=[getattr(gen, 'rampratemin',
                                     None) for gen in generators],
                rampratemax=[getattr(gen, 'rampratemax',
                                     None) for gen in generators],
            )).T
            print('generator limits\n', committed)
        else:
            gens = [gen for gen in self.generators() if gen.is_controllable and gen.initial_status == 1]
            committed = pd.Series(dict(
                Pmin=sum(gen.pmin for gen in gens),
                Pmax=sum(gen.pmax for gen in gens),
                rampratemin=pd.Series(gen.rampratemin for gen in gens).sum(),
                rampratemax=pd.Series(gen.rampratemax for gen in gens).sum(),
            ))
            print('total committed\n', committed)

        if resolve_sln:
            print('expected status')
            if len(resolve_sln.generators_status.columns) < 5:
                print(resolve_sln.generators_status)
            else:
                print(resolve_sln.generators_status.sum(axis=1))
            ep = resolve_sln.generators_power
            ep['net_required'] = scheduled.net_required.values
            print('expected power')
            if len(ep.columns) < 5:
                print(ep)
            else:
                print(ep.sum(axis=1))
        else:
            print('initial_status\n')
            print(pd.Series([gen.initial_status for gen in self.generators()]))

        return scheduled, committed


def get_tPrev(t, model, times):
    return model.times.prev(t) if t != model.times.first() else times.initialTime
