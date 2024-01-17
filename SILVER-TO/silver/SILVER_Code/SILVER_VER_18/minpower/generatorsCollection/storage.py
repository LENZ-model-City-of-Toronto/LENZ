from __future__ import print_function
from __future__ import absolute_import
from __future__ import division
from builtins import str
from past.utils import old_div
import pandas as pd
import numpy as np
import logging
from ..config import user_config
from ..commonscripts import update_attributes, bool_to_int
from SILVER_VER_18.config_variables import *
from ..optimization import value
from ..schedule import is_init
from .. import bidding
from ..silver_variables import FOLDER_UC, PATH_MODEL_INPUTS
from .baseGenerator import BaseGenerator


class Storage(BaseGenerator):
    '''
    A generator model.

    :param pmin: minimum real power
    :param pmax: maximum real power
    :param minuptime: min. time after commitment in on status (hours)
    :param mindowntime: min. time after de-commitment in off status (hours)
    :param rampratemax: max. positive change in real power over 1hr (MW/hr)
    :param rampratemin: max. negative change in real power over 1hr (MW/hr)
    :param startupramplimit: max. positive change in real power over the
        first hour after startup (MW/hr)
    :param shutdownramplimit: max. negative change in real power over the
        last hour before shutdown (MW/hr)
    :param costcurveequation: text describing a polynomial cost curve ($/MWh)
      see :meth:`~bidding.parsePolynomial` for more.
    :param heatratestring: text describing a polynomial heat rate curve (MBTU/MW).
        converts to cost curve when multiplied by fuelcost.
    :param fuelcost: cost of fuel ($/MBTU)
    :param startupcost: cost to commit ($)
    :param shutdowncost: cost to de-commit ($)
    :param mustrun: flag that forces commimtent to be on

    :param name: name of the generator
    :param index: numbering of the generator
    :param bus: bus name that the generator is connected to
    '''

    def __init__(self, kind='generic',
                 pmin=0, pmax=500,
                 minuptime=0, mindowntime=0,
                 rampratemax=None, rampratemin=None,
                 costcurveequation='20P',
                 heatrateequation=None, fuelcost=1,
                 bid_points=None,
                 noloadcost=0,
                 startupcost=0, shutdowncost=0,
                 startupramplimit=None,
                 shutdownramplimit=None,
                 faststart=False,
                 mustrun=False,
                 pumprampmax=None,
                 storagecapacitymax=None,
                 name='', index=None, bus=None,
                 generators=[]
                 ):

        update_attributes(self, locals())

        # The formulation below requires that startup ramp limits are set.
        # The defaults are the normal ramp rate limits,
        # unless Pmin so high and Rmin/max small that startup is infeasible.
        # In that case the default is Pmin.

        if (self.startupramplimit is None) and (self.rampratemax is not None):
            self.startupramplimit = max(self.pmin, self.rampratemax)

        if (self.shutdownramplimit is None) and (self.rampratemin is not None):
            self.shutdownramplimit = min(-1 * self.pmin, self.rampratemin)

        self.fuelcost = float(fuelcost)
        self.is_controllable = True
        self.is_stochastic = False
        self.commitment_problem = True
        self.build_cost_model()
        self.init_optimization()
        self.generators = generators

        self.site_independent = pd.read_excel(PATH_MODEL_INPUTS, sheet_name='site independent', index_col=0)
        # Assumes efficiency of batteries to be .85 based on site independent variables for PHS
        self.n_pumping = np.sqrt(.85)
        self.n_generating = np.sqrt(.85)

    ####################################
    # OBJECTIVE FUNCTION
    ####################################
    def create_objective(self, times):

        return sum(self.cost(time) for time in times)

    def cost(self, time, scenario=None, evaluate=False):
        '''total cost at time (operating + startup + shutdown)'''
        return self.operatingcost(time, scenario, evaluate) + \
            self.cost_startup(time, scenario, evaluate) + \
            self.cost_shutdown(time, scenario, evaluate)

    def operatingcost(self, time=None, scenario=None, evaluate=False):

        if self.commitment_problem is True:
            ''' import cost from csv file created in 1st (price-setting) OPF, will only be used in UC '''

            if RUN_OPF:
                self.storage_cost_list = pd.read_csv(FOLDER_UC / 'storage_cost_opf.csv', index_col=0)
                cost = self.storage_cost_list.loc[time][self.index]
            else:
                cost = 1
            return cost * self.power(time)

        if self.commitment_problem is False:
            ''' Return the storage_cost(time)*storage.power(time) '''
            # The OPF status of all generators is always 1, therefore this always just returns the price of the most expensive generator'''
            return self.bids.output(time, scenario=scenario, evaluate=evaluate)

    def cost_startup(self, time, scenario=None, evaluate=False):
        if self.startupcost == 0 or not self.commitment_problem:
            return 0
        c = self.get_variable('startupcost', time, scenario=scenario, indexed=True)
        return value(c) if evaluate else c

    def cost_shutdown(self, time, scenario=None, evaluate=False):
        if self.shutdowncost == 0 or not self.commitment_problem:
            return 0
        c = self.get_variable('shutdowncost', time, scenario=scenario, indexed=True)
        return value(c) if evaluate else c

    def truecost(self, time, scenario=None):
        '''exact cost of real power production at time (based on exact bid polynomial).'''
        return value(self.status(time, scenario)) * self.bids.output_true(self.power(time, scenario))

    def incrementalcost(self, time, scenario=None):
        '''change in cost with change in power at time (based on exact bid polynomial).'''
        return self.bids.output_incremental(self.power(time, scenario)) if value(self.status(time, scenario)) else None

    def cost_first_stage(self, times):
        return sum(self.cost_startup(time) + self.cost_shutdown(time) for time in times)

    def cost_second_stage(self, times):
        return sum(self.operatingcost(time) for time in times)

    def build_cost_model(self):
        '''
        parse the coefficients for the polynomial bid curve
        or custom bid points definition
        '''

        bid_params = dict(
            owner=self,
            input_variable=self.power,
            min_input=self.pmin,
            max_input=self.pmax,
            status_variable=self.status
        )

        if self.bid_points is None:
            self.cost_breakpoints = user_config.breakpoints  # polynomial specification
            if getattr(self, 'heatrateequation', None):
                self.cost_coeffs = [self.fuelcost * coef
                                    for coef in bidding.parse_polynomial(self.heatrateequation)]
            else:
                self.cost_coeffs = bidding.parse_polynomial(
                    self.costcurveequation)

            bid_params['polynomial'] = self.cost_coeffs
            bid_params['constant_term'] = self.cost_coeffs[0]
            bid_params['num_breakpoints'] = self.cost_breakpoints

            if self.noloadcost != 0:
                raise ValueError('no load cost should be defined as part of the polynomial.')

        else:
            # do some simple validation and delay construction to bidding object
            min_power_bid = self.bid_points.power.min()
            max_power_bid = self.bid_points.power.max()

            if min_power_bid > self.pmin:  # pragma: no cover
                self.pmin = min_power_bid
                logging.warning('{g} should have a min. power bid ({mpb}) <= to its min. power limit ({mpl})'.format(g=str(self), mpb=min_power_bid, mpl=self.pmin))

            if max_power_bid < self.pmax:  # pragma: no cover
                self.pmax = max_power_bid
                logging.warning('{g} should have a max. power bid ({mpb}) >= to its max. power limit ({mpl})'.format(g=str(self), mpb=max_power_bid, mpl=self.pmax))

            bid_params['polynomial'] = None
            bid_params['bid_points'] = self.bid_points
            bid_params['constant_term'] = self.noloadcost

        self.bid_params = bid_params

    ####################################
    # OPTIMIZATION VARIABLES
    ####################################
    def power(self, time=None, scenario=None):
        '''real power output (generation) at time'''
        if time is not None and is_init(time):
            return self.initial_power
        else:
            return self.get_variable('power', time, scenario=scenario, indexed=True)

    def power_in(self, time=None, scenario=None):
        ''' power input (pumping) at time '''
        if time is not None and is_init(time):
            return self.initial_power_in
        else:
            return self.get_variable('power_in', time, scenario=scenario, indexed=True)

    def power_available(self, time=None, scenario=None):
        ''' available generating power at time; power_available exists for easier reserve requirement '''
        if time is not None and is_init(time):
            return self.initial_power
        var_name = 'power_available' if self.commitment_problem \
            and self.reserve_required else 'power'
        return self.get_variable(var_name, time, scenario=scenario, indexed=True)

    def power_in_available(self, time=None, scenario=None):
        ''' available pumping power (constrained by pmax, ramprate, ...) at time '''
        if time is not None and is_init(time):
            return self.initial_power
        var_name = 'power_in_available' if self.commitment_problem \
            and self.reserve_required else 'power_in'
        return self.get_variable(var_name, time, scenario=scenario, indexed=True)

    def status(self, time=None, scenario=None):
        ''' generating on/off status at time '''
        if self.commitment_problem or user_config.dispatch_decommit_allowed:
            if time is not None and is_init(time):
                return self.initial_status
            else:
                return self.get_variable('status', time, scenario=scenario, indexed=True)
        else:
            return 1

    def status_in(self, time=None, scenario=None):
        ''' pumping on/off at time '''
        if self.commitment_problem or user_config.dispatch_decommit_allowed:
            if time is not None and is_init(time):
                return self.initial_status
            else:
                return self.get_variable('status_in', time, scenario=scenario, indexed=True)
        else:
            return 1

    def absolute_power(self, time=None, scenario=None):
        ''' the absolute value of generating (positive value) and pumping (negative value) '''
        absolute_power = self.get_variable('absolute power', time, scenario=scenario, indexed=True)
        return absolute_power

    def storage_content(self, time=None, scenario=None):
        ''' the amount of energy in storage: reduced by (+ve) power (generation) and increased by (-ve) power_in (pumping) '''
        return self.get_variable('storage_content', time, scenario=scenario, indexed=True)

    def create_variables(self, times):
        '''
        Create the optimization variables for a generator over all times.
        Also create the :class:`bidding.Bid` objects and their variables.
        '''
        self.commitment_problem = len(times) > 1
        self.add_variable('power', index=times.set, low=0, high=self.pmax)
        self.add_variable('power_in', index=times.set, low=(-1) * self.pmax, high=0)
        self.add_variable('storage_content', index=times.set, low=0, high=self.storagecapacitymax)
        self.add_variable('absolute_power', index=times.set, low=0)

        '''if self.commitment_problem or user_config.dispatch_decommit_allowed:
            self.add_variable('status', index=times.set, kind='Binary',
                              fixed_value=1 if self.mustrun else None)
            self.add_variable('status_in', index=times.set, kind='Binary',
                                fixed_value = 1 if self.mustrun else None)'''
        self.add_variable('status', index=times.set, kind='Binary',
                          fixed_value=1 if self.mustrun else None)
        self.add_variable('status_in', index=times.set, kind='Binary',
                          fixed_value=1 if self.mustrun else None)

        if self.commitment_problem:
            # power_available exists for easier reserve requirement
            self.reserve_required = self._parent_problem().reserve_required
            if self.reserve_required:
                self.add_variable(
                    'power_available', index=times.set, low=0, high=self.pmax)
                self.add_variable(
                    'power_in_available', index=times.set, low=(-1) * self.pmax, high=0)
            if self.startupcost > 0:
                self.add_variable('startupcost', index=times.set,
                                  low=0, high=self.startupcost)
            if self.shutdowncost > 0:
                self.add_variable('shutdowncost', index=times.set,
                                  low=0, high=self.shutdowncost)

        self.bids = bidding.Bid(times=times, **self.bid_params)
        return

    ####################################
    # CONSTRAINTS (and related functions)
    ####################################

    def status_change(self, t, times):
        ''' change in generating or pumping status between t and t-1 '''
        if t > 0:
            previous_status = self.status(times[t - 1])
            previous_status_in = self.status_in(times[t - 1])
        else:
            previous_status = self.initial_status
            previous_status_in = self.initial_status_in
        return (self.status(times[t]) - previous_status) + (self.status_in(times[t]) - previous_status_in)

    def dPrev_storage_content(self, t):
        try:
            storage_plants = pd.read_csv(FOLDER_UC / 'initial.csv', index_col=1)
            self.storage_content_previous = storage_plants['storage content'][self.name]
        except:
            print(" COULD NOT READ STORAGE CONTENT FROM PREVIOUS DAY - restting value to zero")
            self.storage_content_previous = 0

        return float(self.storage_content_previous)

    def pumping(self, time=None, scenario=None):
        pumping = self.power(time) + self.power_in(time)
        return pumping

    def generating(self, time=None, scenario=None):
        generating = self.power(time) + (-1) * (self.power_in(time))
        return generating

    def create_constraints(self, times):
        '''create the optimization constraints for a generator over all times'''
        # Note that the order of constraints does change the solution

        ''' generic constraints that pertain to all generators '''

        if self.commitment_problem:
            # set initial and final time constraints
            tInitial = times.initialTimestr
            tEnd = len(times)

            ''' power generation and pumping must be between pmin and pmax '''
            if self.pmin > 0:
                def min_power(model, t):
                    return self.power(t) >= self.status(t) * self.pmin
                self.add_constraint_set('min gen power', times.set, min_power)

                def min_power_in(model, t):
                    return self.power_in(t) <= self.status_in(t) * self.pmin * (-1)
                self.add_constraint_set('min pumping power', times.set, min_power_in)

            def max_power(model, t):
                return self.power_available(t) <= self.status(t) * self.pmax
            self.add_constraint_set('max gen power', times.set, max_power)

            def max_power_in(model, t):
                return self.power_in_available(t) >= self.status_in(t) * self.pmax * (-1)
            self.add_constraint_set('max pumpming power', times.set, max_power_in)

            self.min_up_down_time_constraints(times, tInitial, tEnd)

            self.rampRateLimitConstraints(times, tInitial)

            '''maximum change in pumping from one hour to the next: max and min pramping limits'''
            if self.rampratemax is not None:
                def ramp_max_pumping(model, t):
                    tPrev = self.get_tPrev(t, model, times)
                    ramp_limit_pump = self.rampratemax * self.status_in(tPrev)
                    if self.startupramplimit is not None:
                        ramp_limit_pump += self.startupramplimit * (self.status_in(t) - self.status_in(tPrev))
                    return (-1) * self.power_in_available(t) - (-1) * self.power_in(tPrev) <= ramp_limit_pump
                self.add_constraint_set('ramp limit high pumping', times.set, ramp_max_pumping)

            if self.rampratemin is not None:
                def ramp_min_pumping(model, t):
                    tPrev = self.get_tPrev(t, model, times)
                    ramp_limit_pump = self.rampratemin * self.status_in(t)
                    if self.shutdownramplimit is not None:
                        ramp_limit_pump += self.shutdownramplimit * (-1 * (self.status_in(t) - self.status_in(tPrev)))
                    return (-1) * self.power_in_available(t) - (-1) * self.power_in(tPrev) >= ramp_limit_pump
                self.add_constraint_set('ramp limit low pumping ', times.set, ramp_min_pumping)

            ''' assume that pumped hydro does not have shutdown or startup costs '''

            ''' reserve constraints '''
            if self.reserve_required:
                def reserve_req(model, t):
                    return self.power(t) <= self.power_available(t)
                self.add_constraint_set('max gen power avail', times.set, reserve_req)

        ''' constraints that pertain to storage facilities specifically '''
        if self.commitment_problem:
            '''Inventory balance: track the resevoir level between subsequent time periods given generating/pumping '''
            def storage_content_capacity(model, t):

                tPrev = self.get_tPrev(t, model, times)

                if t != 't00':  # and t !='t24':
                    return self.storage_content(t) == self.storage_content(tPrev) - \
                        self.n_pumping * self.power_in(t) - old_div(self.power(t), self.n_generating)

                dPrev_storage_content_t23 = self.dPrev_storage_content(t)
                return self.storage_content(t) == dPrev_storage_content_t23 - \
                    self.n_pumping * self.power_in(t) - old_div(self.power(t), self.n_generating)
            self.add_constraint_set('kWh_in_storage', times.set, storage_content_capacity)

            ''' Limit resevoir level to the storage capacity of the resevoir '''
            def max_storage_capacity(model, t):

                tPrev = self.get_tPrev(t, model, times)
                if t != 't00':  # and t!= 't24':
                    return self.storage_content(tPrev) + self.power_in(t) * self.n_pumping <= self.storagecapacitymax
                else:
                    return self.dPrev_storage_content(t) + self.power_in(t) * self.n_pumping <= self.storagecapacitymax
            self.add_constraint_set('max_kWh_in_storage', times.set, max_storage_capacity)

            '''storage unit can EITHER be pumping OR generation, but not both '''
            ''' create 'absolute_power' - a variable that always takes the absolute value of power and power_in '''
            for t in times:
                self.add_constraint('absolute power 1', t, self.pumping(t) <= self.absolute_power(t))
                self.add_constraint('absolute power 2', t, self.generating(t) <= self.absolute_power(t))

            def generating_or_pumping_OPF(model, t):
                # Note: can't use summation of status and status_in <=1 -Error: constraint expression resolved to a trivial Boolean instead of a Pyomo object
                return self.power(t) + (-1) * self.power_in(t) <= self.absolute_power(t)
            self.add_constraint_set('generating or pumping OPF', times.set, generating_or_pumping_OPF)

            def generating_or_pumping_UC(model, t):
                # this works for UC, but NOT OPF b/c the status in an OPF problem always =1
                return self.status(t) + self.status_in(t) <= 1
            self.add_constraint_set('generating or pumping UC', times.set, generating_or_pumping_UC)

            ''' amount of storage_content (energy) in the seasonal storage asset (Hydrogen) must equal pre-determined value '''
            # Note: this constraint is added in the PowerSystems class, rather than the Generator class
            # b/c the PowerSystems add_constraint does not have a time component, but Load class does
            # as such, you can add constraints that apply to the entire optimization period, rather than
            # just the current hour. Since the amount of energy stored in the seasonal storage asset
            # at the end of the period is an accumulation of pumping/generting at every hour during the
            # optimization period (one week), we need to apply the constraint to the entire period,
            # not hour by hour.
            # constraint name and function is 'seasonal_storage'

        else:   # for OPF problem, need to bound power to be within what was calculated in UC
            # note pmin the opf generators.csv is recorded as the power (+ve) or power_in (-ve) value from the UC
            # the pmax in the opf generators.csv is recorded as the abs(power or power_in)
            if self.pmin > 0:
                def max_power(model, t):
                    return self.power_available(t) <= self.pmax

                def min_power(model, t):
                    return self.power(t) >= self.pmin

                def pumping_or_generating(model, t):
                    return self.power_in_available(t) == 0      # if generating (pmin>0), then can't pump
                self.add_constraint_set('max power', times.set, max_power)
                self.add_constraint_set('min power', times.set, min_power)
                self.add_constraint_set('pumping or generating opf', times.set, pumping_or_generating)
            elif self.pmin < 0:
                def max_power_in_2(model, t):
                    return self.power_in_available(t) <= self.pmin

                def min_power_in(model, t):
                    return self.power_in(t) == (-1) * self.pmax

                def set_power_to_zero(model, t):
                    return self.power(t) == 0                   # if pumping (pmin<0), then can't generate
                self.add_constraint_set('max power 2', times.set, max_power_in_2)
                self.add_constraint_set('min power in', times.set, min_power_in)
                self.add_constraint_set('max power 3', times.set, set_power_to_zero)

        return

    ####################################
    # ADDITIONAL FUNCTIONS
    ####################################
    def __str__(self):
        return 'g{ind}'.format(ind=self.index)

    def getstatus(self, tend, times, status):
        return dict(
            status=value(self.status(tend)),
            power=value(self.power(tend)),
            hoursinstatus=self.gethrsinstatus(times, status))

    def gethrsinstatus(self, times, stat):
        if not self.is_controllable:
            return 0
        end_status = stat.ix[stat.index[-1]]
        if (stat == end_status).all():
            intervals = len(stat)
            hrs = intervals * times.intervalhrs
            if self.initial_status == end_status:
                hrs += self.initial_status_hours
        else:
            noneq = stat[stat != end_status]
            if len(noneq) == 0:
                intervals = 0
            else:
                intervals = len(stat.ix[noneq.index[-1]:]) - 1
            hrs = intervals * times.intervalhrs
        return hrs

    def set_initial_condition(self, power=None, power_in=None,
                              status=True, status_in=True, hoursinstatus=1):
        if power is None:
            power = old_div((self.pmax - self.pmin), 2)  # set default power as mean output
        if pd.isnull(power):
            raise ValueError('inital power cannot be null')
        self.initial_status = bool_to_int(status)
        self.initial_status_in = bool_to_int(status_in)
        self.initial_power = float(power * self.initial_status)  # note: this eliminates ambiguity of off status with power non-zero output'
        self.initial_power_in = float(power * self.initial_status_in)
        self.initial_status_hours = hoursinstatus
